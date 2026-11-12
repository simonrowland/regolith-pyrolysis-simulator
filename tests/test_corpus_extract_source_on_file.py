"""Source-on-file check for green literature extracts.

Owner ruling d-385 (2026-11-10): ``data/literature/extracts`` is the only
extract database. The corpus keeps raw PDFs, OCR text, tables, sidecars, and
the ledger. This check does not read a corpus ``extracts/`` directory.

``REGOLITH_REQUIRE_CORPUS=1`` opts a gate into the check. An absent checkout
then fails closed. A source_id is on file when ``<corpus>/raw/<source_id>``
exists, or any seated ``<corpus>/raw/*/sidecar.yaml`` lists that id under
``aliases:``. Otherwise the failure reason is ``source_pending_acquisition``
for ids starting with ``pending-``, and ``source_not_on_file`` for every
other id. When the variable is unset, the check skips with
``corpus_checkout_not_required_by_this_gate``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from tests.helpers.corpus_paths import regolith_corpus_root
from tests.helpers.named_manifests import assert_named_rows, load_named_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_DIR = REPO_ROOT / "data" / "literature" / "extracts"
REQUIRE_CORPUS_ENV = "REGOLITH_REQUIRE_CORPUS"
SKIP_REASON = "corpus_checkout_not_required_by_this_gate"

CENSUS_MANIFEST = REPO_ROOT / "tests" / "manifests" / "literature_extract_source_ids.jsonl"

# Root source_id in the preamble, YAML or JSON-style. Checked against a full
# document load of every extract at the census commit.
_SOURCE_ID_LINE = re.compile(
    r'^(?:source_id|"source_id")\s*:\s*("?)([^"\n#]+?)\1\s*,?\s*$'
)
_PREAMBLE_LINES = 40


def _corpus_required() -> bool:
    return os.environ.get(REQUIRE_CORPUS_ENV) == "1"


def _require_corpus_root() -> Path:
    """Return the corpus checkout when this gate opted in.

    Unset ``REGOLITH_REQUIRE_CORPUS`` skips. ``REGOLITH_REQUIRE_CORPUS=1``
    fails closed when the checkout (``REGOLITH_CORPUS_ROOT``, else
    ``~/Repos/regolith-corpus``) is absent.
    """
    if not _corpus_required():
        pytest.skip(SKIP_REASON)
    root = regolith_corpus_root()
    assert root.is_dir(), (
        "regolith-corpus checkout is absent: "
        f"{root}. Set REGOLITH_CORPUS_ROOT to a checkout. "
        "Absence fails closed; it is not a skip."
    )
    return root


def _source_id(path: Path) -> str:
    source_id = None
    with path.open(encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            if index > _PREAMBLE_LINES:
                break
            stripped = line.strip()
            # Root YAML keys sit at column 0. JSON extracts quote the key.
            if not (line.startswith("source_id:") or stripped.startswith('"source_id"')):
                continue
            match = _SOURCE_ID_LINE.match(stripped)
            if match:
                source_id = match.group(2).strip()
                break
    if not source_id or source_id in {".", ".."} or "/" in source_id or "\\" in source_id:
        raise AssertionError(
            f"{path.name} source_id is not a single path segment: {source_id!r}"
        )
    return source_id


def _extract_source_ids(extract_dir: Path) -> list[tuple[str, str]]:
    paths = sorted(
        path
        for path in extract_dir.glob("*.yaml")
        if not path.name.startswith("_")
    )
    return [(path.name, _source_id(path)) for path in paths]


def _assert_extract_census(records: list[tuple[str, str]]) -> None:
    assert_named_rows(
        dict(records),
        load_named_manifest(CENSUS_MANIFEST),
        label="data/literature/extracts source_id census",
    )


def _absence_reason(source_id: str) -> str:
    if source_id.startswith("pending-"):
        return "source_pending_acquisition"
    return "source_not_on_file"


def _seated_alias_ids(raw: Path) -> set[str]:
    """Return ids listed under ``aliases:`` on seated ``raw/*/sidecar.yaml`` files."""
    if not raw.is_dir():
        return set()
    found: set[str] = set()
    for sidecar in sorted(raw.glob("*/sidecar.yaml")):
        try:
            document = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise AssertionError(f"{sidecar} is not valid YAML: {exc}") from exc
        if not isinstance(document, dict):
            continue
        aliases = document.get("aliases")
        if aliases is None:
            continue
        if not isinstance(aliases, list) or any(
            not isinstance(item, str) for item in aliases
        ):
            raise AssertionError(f"{sidecar} aliases: must be a list of strings")
        found.update(aliases)
    return found


def _assert_raw_sources(corpus_root: Path, source_ids: list[str]) -> None:
    raw = corpus_root / "raw"
    aliases = _seated_alias_ids(raw)
    unresolved = sorted(
        {
            source_id
            for source_id in source_ids
            if not (raw / source_id).is_dir() and source_id not in aliases
        }
    )
    if not unresolved:
        return
    lines: list[str] = []
    for reason in ("source_pending_acquisition", "source_not_on_file"):
        names = [
            source_id
            for source_id in unresolved
            if _absence_reason(source_id) == reason
        ]
        if names:
            lines.append(f"{reason} ({len(names)}): " + ", ".join(names))
    raise AssertionError(
        "corpus source is not on file for "
        f"{len(unresolved)} source_id(s):\n" + "\n".join(lines)
    )


def test_green_extract_source_id_count() -> None:
    records = _extract_source_ids(EXTRACT_DIR)
    _assert_extract_census(records)


def test_green_extract_source_ids_have_corpus_raw_directories() -> None:
    corpus = _require_corpus_root()
    records = _extract_source_ids(EXTRACT_DIR)
    _assert_extract_census(records)
    source_ids = [source_id for _, source_id in records]
    _assert_raw_sources(corpus, source_ids)


def test_absent_corpus_fails_closed_when_required(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(REQUIRE_CORPUS_ENV, "1")
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(tmp_path / "absent-corpus"))
    with pytest.raises(AssertionError, match="regolith-corpus checkout is absent"):
        _require_corpus_root()


def test_corpus_check_skips_when_require_corpus_is_unset(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(REQUIRE_CORPUS_ENV, raising=False)
    present = tmp_path / "corpus"
    present.mkdir()
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(present))
    with pytest.raises(pytest.skip.Exception, match=SKIP_REASON):
        _require_corpus_root()


def test_missing_raw_directory_names_the_source_id(tmp_path: Path) -> None:
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    (extracts / "kept-source.yaml").write_text("source_id: kept-source\n")
    (extracts / "gone-source.yaml").write_text("source_id: gone-source\n")
    corpus = tmp_path / "corpus"
    (corpus / "raw" / "kept-source").mkdir(parents=True)
    source_ids = [source_id for _, source_id in _extract_source_ids(extracts)]
    with pytest.raises(AssertionError, match=r"\bgone-source\b") as raised:
        _assert_raw_sources(corpus, source_ids)
    message = str(raised.value)
    assert "gone-source" in message
    assert "source_not_on_file" in message
    assert "source_pending_acquisition" not in message
    assert "kept-source" not in message


def test_sidecar_alias_counts_as_source_on_file(tmp_path: Path) -> None:
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    (extracts / "alias-source.yaml").write_text("source_id: alias-source\n")
    corpus = tmp_path / "corpus"
    other = corpus / "raw" / "other-id"
    other.mkdir(parents=True)
    (other / "sidecar.yaml").write_text(
        "source_id: other-id\naliases:\n- alias-source\n"
    )
    source_ids = [source_id for _, source_id in _extract_source_ids(extracts)]
    _assert_raw_sources(corpus, source_ids)


def test_pending_source_id_fails_with_source_pending_acquisition(
    tmp_path: Path,
) -> None:
    extracts = tmp_path / "extracts"
    extracts.mkdir()
    (extracts / "pending-fixture.yaml").write_text("source_id: pending-fixture\n")
    corpus = tmp_path / "corpus"
    (corpus / "raw").mkdir(parents=True)
    source_ids = [source_id for _, source_id in _extract_source_ids(extracts)]
    with pytest.raises(AssertionError, match="source_pending_acquisition") as raised:
        _assert_raw_sources(corpus, source_ids)
    message = str(raised.value)
    assert "pending-fixture" in message
    assert "source_not_on_file" not in message
