"""Source-on-file check for green literature extracts.

Owner ruling d-385 (2026-11-10): ``data/literature/extracts`` is the only
extract database. The corpus keeps raw PDFs, OCR text, tables, sidecars, and
the ledger. This check does not read a corpus ``extracts/`` directory.

``REGOLITH_REQUIRE_CORPUS=1`` opts a gate into the check. An absent checkout
then fails closed. Every extract ``source_id`` must have
``<corpus>/raw/<source_id>``. When the variable is unset, the check skips
with ``corpus_checkout_not_required_by_this_gate``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from tests.helpers.corpus_paths import regolith_corpus_root

REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT_DIR = REPO_ROOT / "data" / "literature" / "extracts"
REQUIRE_CORPUS_ENV = "REGOLITH_REQUIRE_CORPUS"
SKIP_REASON = "corpus_checkout_not_required_by_this_gate"

# Census of data/literature/extracts/*.yaml at 6badf9e3b. This change does not
# add or remove extracts. Names starting with '_' are store policy, not
# extracts (SCHEMA.md). 781 extracts carry 781 source_id values. 780 are
# distinct: bencze-yazhenskikh-2016.yaml and
# bencze-yazhenskikh-2016-table-s1-k.yaml share one source_id.
EXPECTED_EXTRACT_COUNT = 781
EXPECTED_SOURCE_ID_COUNT = 781
EXPECTED_DISTINCT_SOURCE_IDS = 780

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


def _assert_raw_sources(corpus_root: Path, source_ids: list[str]) -> None:
    raw = corpus_root / "raw"
    missing = sorted(
        {source_id for source_id in source_ids if not (raw / source_id).is_dir()}
    )
    assert not missing, (
        "corpus raw/<source_id> is absent for "
        f"{len(missing)} source_id(s): " + ", ".join(missing)
    )


def test_green_extract_source_id_count() -> None:
    records = _extract_source_ids(EXTRACT_DIR)
    source_ids = [source_id for _, source_id in records]
    assert len(records) == EXPECTED_EXTRACT_COUNT
    assert len(source_ids) == EXPECTED_SOURCE_ID_COUNT
    assert len(set(source_ids)) == EXPECTED_DISTINCT_SOURCE_IDS


def test_green_extract_source_ids_have_corpus_raw_directories() -> None:
    corpus = _require_corpus_root()
    source_ids = [source_id for _, source_id in _extract_source_ids(EXTRACT_DIR)]
    assert len(source_ids) == EXPECTED_SOURCE_ID_COUNT
    assert len(set(source_ids)) == EXPECTED_DISTINCT_SOURCE_IDS
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
    assert "kept-source" not in message
