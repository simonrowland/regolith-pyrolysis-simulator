"""Drift gate and row-truth tests for the tracked empirical-corpus index."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import re
import yaml
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER = REPO_ROOT / "data" / "literature" / "build_index.py"
COMMITTED_YAML = REPO_ROOT / "data" / "literature" / "INDEX.yaml"
COMMITTED_MD = REPO_ROOT / "data" / "literature" / "INDEX.md"
spec = importlib.util.spec_from_file_location("literature_index", BUILDER)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def _run_builder(root: Path, out_dir: Path, extra: list[str] | None = None) -> None:
    cmd = [sys.executable, str(BUILDER), "--root", str(root), "--out-dir", str(out_dir)]
    if extra:
        cmd.extend(extra)
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_regenerated_index_matches_committed(tmp_path: Path) -> None:
    """Rebuilding INDEX.yaml/md into a temp dir must match the committed files.

    Null hypothesis: the committed index can drift from the builder without CI
    noticing. Regenerating into tmp (not mutating the tree) and comparing bytes
    makes that drift fail this test. CI walks --root <repo> with no private roots.
    """
    assert BUILDER.is_file()
    assert COMMITTED_YAML.is_file()
    assert COMMITTED_MD.is_file()
    _run_builder(REPO_ROOT, tmp_path)
    got_yaml = tmp_path / "INDEX.yaml"
    got_md = tmp_path / "INDEX.md"
    committed = yaml.safe_load(COMMITTED_YAML.read_text())
    regenerated = yaml.safe_load(got_yaml.read_text())
    assert COMMITTED_MD.read_text() == builder.render_md(committed)
    assert got_md.read_text() == builder.render_md(regenerated)
    # Corpus is an external snapshot, not a checked-in test fixture.
    for doc in (committed, regenerated):
        doc["scan"].pop("corpus_root")
        for row in doc["sources"]:
            row.pop("corpus")
            row.pop("corpus_status")
    assert regenerated == committed


def test_builder_temp_corpus_row_truth(tmp_path: Path) -> None:
    """Two fake PDFs + one extract + one preset: exact rows without the corpus."""
    root = tmp_path / "corpus"
    pdf_dir = root / "docs" / "references" / "pdfs" / "99-kems-langmuir"
    extract_dir = root / "data" / "literature" / "extracts"
    preset_dir = root / "data" / "presets" / "kems"
    pdf_dir.mkdir(parents=True)
    extract_dir.mkdir(parents=True)
    preset_dir.mkdir(parents=True)

    alpha_bytes = b"%PDF-1.4 alpha-2020 fixture\n"
    beta_bytes = b"%PDF-1.4 beta-2021 fixture\n"
    (pdf_dir / "alpha-2020.pdf").write_bytes(alpha_bytes)
    (pdf_dir / "beta-2021.pdf").write_bytes(beta_bytes)
    (extract_dir / "alpha-2020.yaml").write_text(
        "\n".join(
            [
                "schema_version: literature_extract.v1",
                "source_id: alpha-2020",
                "source:",
                "  citation: Alpha, A. (2020), Fixture Paper",
                "  doi: 10.9999/alpha-fixture",
                "  year: 2020",
                "extraction:",
                "  method: fixture",
                "  date: '2026-09-06'",
                "  worker: test",
                "review_status: draft",
                "species: {}",
                "",
            ]
        )
    )
    (preset_dir / "alpha_case.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "case_id: alpha_case",
                "source_id: alpha_author_2020",
                "doi: 10.9999/alpha-fixture",
                "observation_sidecar_path: data/literature/vacuum_pyrolysis_measurements.yaml",
                "note: docs/references/pdfs/99-kems-langmuir/beta-2021.pdf is not this source",
                "",
            ]
        )
    )

    out_dir = tmp_path / "out"
    _run_builder(root, out_dir)
    index = yaml.safe_load((out_dir / "INDEX.yaml").read_text())
    rows = {row["source_id"]: row for row in index["sources"]}
    assert set(rows) == {"alpha-2020", "beta-2021"}
    assert "alpha_author_2020" not in rows

    alpha = rows["alpha-2020"]
    beta = rows["beta-2021"]
    alpha_sha = hashlib.sha256(alpha_bytes).hexdigest()
    beta_sha = hashlib.sha256(beta_bytes).hexdigest()

    assert alpha["aliases"] == ["alpha_author_2020"]
    assert alpha["doi"] == "10.9999/alpha-fixture"
    assert alpha["pdf_status"] == "present"
    assert alpha["pdf_path"] == "docs/references/pdfs/99-kems-langmuir/alpha-2020.pdf"
    assert alpha["pdf_sha256"] == alpha_sha
    assert alpha["pdf_tracked"] == "unknown"
    assert alpha["extracts"] == [
        {"path": "data/literature/extracts/alpha-2020.yaml", "review_status": "draft", "rows": 0}
    ]
    assert alpha["battery_datasets"] == ["data/presets/kems/alpha_case.yaml"]
    assert alpha["copies"] == []
    assert alpha["hunt_ids"] == []
    assert alpha["pdf_last_seen"] == []

    assert beta["aliases"] == []
    assert beta["extracts"] == []
    assert beta["pdf_status"] == "present"
    assert beta["pdf_path"] == "docs/references/pdfs/99-kems-langmuir/beta-2021.pdf"
    assert beta["pdf_sha256"] == beta_sha
    assert beta["pdf_tracked"] == "unknown"
    assert beta["battery_datasets"] == []
    assert "data/presets/kems/alpha_case.yaml" not in beta["battery_datasets"]
    assert index["counts"]["sources"] == 2
    assert index["scan"]["private_roots_scanned"] == []
    assert index["scan"]["hunt_json"] is None


def test_doi_bearing_sidecars_reach_index():
    index = yaml.safe_load(COMMITTED_YAML.read_text())
    # Detect DOI-bearing sidecars with a parser-independent regex on the raw
    # text so a parser regression cannot hide itself (review r2 P3): if the
    # production parser dropped every DOI, the parser-based check would pass
    # vacuously while this one fails.
    # Only a DOI *field* counts (doi: / DOI: / **DOI:** ...); free-text
    # mentions of rejected or guessed DOIs (e.g. a recorded resolver miss) do not.
    doi_re = re.compile(r"^\s*[-*#]*\s*\**doi\**\s*[:=]\s*\**\s*`?(10\.\d{4,9}/\S+)", re.I | re.M)
    for row in index["sources"]:
        if row["sidecar_path"]:
            raw = (REPO_ROOT / row["sidecar_path"]).read_text(errors="replace")
            if doi_re.search(raw):
                assert row["doi"], row["source_id"]
    kems = next(row for row in index["sources"] if row["source_id"] == "kems-017-stolyarova-2013")
    assert kems["doi"] == "10.2174/1874396x01307010057"
    assert kems["sidecar_path"].endswith("kems-017-stolyarova-2013.md")
    assert set(kems["corpus"]) == {"raw", "sidecar", "text", "tables", "extract", "ledger", "commit"}
    assert not kems["sidecar_missing_fields"]


@pytest.mark.parametrize("format", ["bullet", "bold", "heading"])
def test_sidecar_formats_and_null_extract_fallback(tmp_path, monkeypatch, format):
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(tmp_path / "absent"))
    pdf_dir = tmp_path / "docs/references/pdfs/topic"
    pdf_dir.mkdir(parents=True)
    fields = {"Citation": "Author (2020), Paper", "DOI": "10.9999/test",
              "Licence": "CC BY", "SHA-256": "a" * 64,
              "Retrieved date": "2026-09-06", "Retrieved URL": "https://example.org/paper.pdf"}
    if format == "heading":
        text = "\n\n".join(f"## {key}\n\n`{value}`" for key, value in fields.items())
    else:
        text = "\n".join(f"- {key}: `{value}`" if format == "bullet" else f"**{key}:** `{value}`"
                         for key, value in fields.items())
    (pdf_dir / "paper.md").write_text(text)
    extract_dir = tmp_path / "data/literature/extracts"
    extract_dir.mkdir(parents=True)
    (extract_dir / "paper.yaml").write_text("schema_version: literature_extract.v1\nsource: {doi: null, citation: null}\n")
    row = builder.build_index(tmp_path)["sources"][0]
    assert row["doi"] == "10.9999/test"
    assert row["citation"].strip("`") == "Author (2020), Paper"
    assert row["sidecar_missing_fields"] == []
    assert row["pdf_status"] == "ABSENT"
    assert row["corpus_status"] == "unavailable"
    assert all(value is None for value in row["corpus"].values())


def test_heading_no_doi_and_primary_file_table(tmp_path):
    path = tmp_path / "paper.md"
    path.write_text("## DOI\n\n`doi: null`\n\nFailed guess https://doi.org/10.9999/wrong\n\n"
                    "## File\n\n| sha256 | `" + "a" * 64 + "` |\n| retrieved_date | 2026-09-06 |\n"
                    "## File (supplement)\n\n| sha256 | `" + "b" * 64 + "` |\n")
    sidecar = builder.parse_sidecar(path)
    assert sidecar["doi"] is None
    assert sidecar["sha256"] == "a" * 64
    assert sidecar["retrieved_date"] == "2026-09-06"


def test_explicit_unknown_access_and_absent_retrieval_override_prose(tmp_path):
    path = tmp_path / "paper.md"
    path.write_text("DOI: 10.9999/test\nhttps://doi.org/10.9999/test\n\n"
                    "## Licence\n\nNo Creative Commons licence found.\n\n"
                    "## Access\n\n`access: unknown`\n`paywalled: unknown`\n\n"
                    "## File\n\n| retrieved_url | *(none succeeded as PDF bytes)* |\n")
    sidecar = builder.parse_sidecar(path)
    assert sidecar["access"] == "unknown"
    assert builder.access_for("paper", {}, sidecar, {}, False) == "unknown"
    assert sidecar["retrieval_url"] is None
    assert "retrieval_url" in sidecar["missing_fields"]


def test_corpus_pointers_compare_bytes_counts_and_ledger(tmp_path):
    corpus = tmp_path / "corpus"
    raw = corpus / "raw/paper/paper.pdf"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"pdf")
    raw.with_name("sidecar.yaml").write_text("doi: 10.9999/test\n")
    for name in ("text", "tables"):
        folder = corpus / name / "paper"
        folder.mkdir(parents=True)
        (folder / "file.txt").write_text("data")
        (folder / "empty-dir").mkdir()
    extract = corpus / "extracts/paper.yaml"
    extract.parent.mkdir()
    extract.write_bytes(b"extract")
    mirror = tmp_path / "paper.yaml"
    mirror.write_bytes(b"extract")
    ledger = corpus / "ledger/paper.yaml"
    ledger.parent.mkdir()
    ledger.write_text("stages:\n  decoded: {date: '2026-09-06'}\n  transcribed: {date: '2026-09-06'}\n")
    pointers = builder.corpus_pointers(corpus, "paper", hashlib.sha256(b"pdf").hexdigest(), mirror, "abc")
    assert pointers["raw"]["matches_simulator"] is True
    assert pointers["extract"]["matches_simulator"] is True
    assert pointers["text"]["file_count"] == pointers["tables"]["file_count"] == 1
    assert pointers["ledger"]["last_stage"] == "transcribed"
    assert pointers["ledger"]["date"] == "2026-09-06"
    assert pointers["commit"] == "abc"
    mirror.write_bytes(b"different")
    pointers = builder.corpus_pointers(corpus, "paper", "different", mirror, "abc")
    assert pointers["raw"]["matches_simulator"] is False
    assert pointers["extract"]["matches_simulator"] is False


@pytest.mark.parametrize("layout", ["empty", "unrelated", "raw", "extracts", "ledger", "git"])
def test_corpus_availability_cli(tmp_path, monkeypatch, layout):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    if layout == "git":
        subprocess.run(["git", "init", str(corpus)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(corpus), "-c", "user.name=Test", "-c",
                        "user.email=test@example.org", "commit", "--allow-empty", "-m", "fixture"],
                       check=True, capture_output=True)
    elif layout != "empty":
        (corpus / layout).mkdir()
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(corpus))
    root = tmp_path / "simulator"
    extracts = root / "data/literature/extracts"
    extracts.mkdir(parents=True)
    (extracts / "paper.yaml").write_text("schema_version: literature_extract.v1\n")
    out = tmp_path / "out"
    _run_builder(root, out)
    row = yaml.safe_load((out / "INDEX.yaml").read_text())["sources"][0]
    available = layout in {"raw", "extracts", "ledger", "git"}
    assert row["corpus_status"] == ("available" if available else "unavailable")
    if not available:
        assert all(value is None for value in row["corpus"].values())
        assert "0 files" not in (out / "INDEX.md").read_text()


def test_stebbins_explicit_retrieval_url():
    sidecar = builder.parse_sidecar(REPO_ROOT / "docs/references/pdfs/02-thermochemistry/stebbins-carmichael-weill-1983.md")
    assert sidecar["retrieval_url"] == "http://www.minsocam.org/ammin/AM68/AM68_717.pdf"


@pytest.mark.parametrize("explicit_first", [True, False])
@pytest.mark.parametrize("format", ["bullet", "heading", "table"])
def test_retrieval_url_wins_over_alias(tmp_path, explicit_first, format):
    fields = [("Official open URL", "https://example.org/bibliography"),
              ("Retrieved URL", "http://example.org/retrieved.pdf")]
    if explicit_first:
        fields.reverse()
    path = tmp_path / "paper.md"
    if format == "heading":
        text = "\n\n".join(f"## {key}\n\n{value}" for key, value in fields)
    elif format == "table":
        text = "\n".join(f"| {key} | {value} |" for key, value in fields)
    else:
        text = "\n".join(f"- {key}: {value}" for key, value in fields)
    path.write_text(text)
    assert builder.parse_sidecar(path)["retrieval_url"] == "http://example.org/retrieved.pdf"


def test_private_locator_reporting(tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(tmp_path / "absent"))
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".gitignore").write_text("ignored/\n")
    (tmp_path / "public.txt").write_text("public evidence")
    subprocess.run(["git", "-C", str(tmp_path), "add", "public.txt", ".gitignore"],
                   check=True, capture_output=True)
    extracts = tmp_path / "data/literature/extracts"
    extracts.mkdir(parents=True)
    locators = {"private": "docs-private/missing.txt", "ignored": "ignored/table.md",
                "untracked": "local/table.md", "external": str(tmp_path.parent / "external.txt"),
                "public": "public.txt", "url": "https://example.org/table.txt"}
    for sid, path in locators.items():
        (extracts / f"{sid}.yaml").write_text(yaml.safe_dump({
            "schema_version": "literature_extract.v1",
            "species": {"Fe": {"observations": [{"locator": {"source_path": path}}]}}}))
    index = builder.build_index(tmp_path)
    for row in index["sources"]:
        assert (row["extracts"][0].get("locator_status") == "private_path") == (
            row["source_id"] in {"private", "ignored", "untracked", "external"})
    assert index["counts"]["extracts_with_private_locators"] == 4
    assert index["gaps"]["extracts_with_private_locators"] == ["external", "ignored", "private", "untracked"]
    assert "Extracts with private/non-public row locators: 4" in builder.render_md(index)


def test_nasa_cea_private_locator_backlog():
    index = yaml.safe_load(COMMITTED_YAML.read_text())
    for source_id in ("nasa-cea-thermo", "ref-016-sio-kems-1700-2000k"):
        row = next(row for row in index["sources"] if row["source_id"] == source_id)
        assert row["extracts"][0]["locator_status"] == "private_path"
        assert source_id in index["gaps"]["extracts_with_private_locators"]


# ---------------------------------------------------------------------------
# Generated SOURCE_STATUS.yaml — tags derived from artefacts, never hand-set.
# ---------------------------------------------------------------------------

COMMITTED_STATUS = REPO_ROOT / "data" / "literature" / "SOURCE_STATUS.yaml"


def _status_tree(tmp_path: Path, monkeypatch, *, git_corpus: bool = False) -> tuple[Path, Path]:
    root = tmp_path / "sim"
    corpus = tmp_path / "corpus"
    for path in (
        root / "data/literature/extracts",
        root / "data/literature/extracts-v2",
        root / "data/literature/compilations",
        root / "data/literature/residuals-v2",
        corpus / "raw",
        corpus / "extracts",
        corpus / "claims",
        corpus / "ledger",
        corpus / "text",
        corpus / "tables",
    ):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("REGOLITH_CORPUS_ROOT", str(corpus))
    if git_corpus:
        subprocess.run(["git", "init", str(corpus)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(corpus), "config", "user.name", "Test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(corpus), "config", "user.email", "test@example.org"],
                       check=True, capture_output=True)
    return root, corpus


def _write_yaml(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True))


def _extract_v1(source_id: str, **extra) -> dict:
    doc = {
        "schema_version": "literature_extract.v1",
        "source_id": source_id,
        "source": {"citation": f"{source_id} fixture"},
        "extraction": {"method": "fixture", "date": "2026-09-01", "worker": "test"},
        "review_status": "draft",
        "species": {},
    }
    doc.update(extra)
    return doc


def _usable_obs(oid: str = "o1") -> dict:
    return {
        "observation_id": oid,
        "identity": {"quantity": {"tag": "value", "value": "activity"}},
        "value": {"kind": "point", "point": "0.5"},
        "evidence": {"class": {"tag": "value", "value": "measured_direct"}},
    }


def _unusable_obs(oid: str = "o2") -> dict:
    return {
        "observation_id": oid,
        "identity": {"quantity": {"tag": "unknown", "reason": "fixture"}},
        "value": {"kind": "unavailable", "unavailable_reason": "fixture"},
        "evidence": {"class": {"tag": "value", "value": "model_derived"}},
    }


def _v21(source_id: str, observations: list[dict]) -> dict:
    return {
        "schema_version": "battery_observations.v2.1",
        "observations": [{**row, "source_id": source_id} for row in observations],
    }


def _status_rows(root: Path, **kwargs) -> dict[str, dict]:
    status = builder.build_source_status(root, **kwargs)
    return {row["source_id"]: row for row in status["sources"]}, status


def test_source_status_stage_moves_when_evidence_mutates(tmp_path, monkeypatch):
    """Mutate artefacts under one source_id; the derived tag must move with them.

    Reading committed YAML cannot prove derivation. Each assertion is against a
    freshly computed registry after a single evidence change.
    """
    root, corpus = _status_tree(tmp_path, monkeypatch)
    sid = "alpha-2020"
    raw = corpus / "raw" / sid
    raw.mkdir(parents=True)
    _write_yaml(raw / "sidecar.yaml", {"citation": "Alpha (2020)", "stage": "wired"})

    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "located"

    (raw / f"{sid}.pdf").write_bytes(b"%PDF-1.4 fixture\n")
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "inbox"

    _write_yaml(root / "data/literature/extracts" / f"{sid}.yaml", _extract_v1(sid, stage="wired"))
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "in_progress"

    _write_yaml(root / "data/literature/extracts-v2" / f"{sid}.yaml",
                _v21(sid, [_usable_obs(), _unusable_obs()]))
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "ingested_partial"

    _write_yaml(root / "data/literature/extracts-v2" / f"{sid}.yaml",
                _v21(sid, [_usable_obs("a"), _usable_obs("b")]))
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "ingested_complete"

    _write_yaml(root / "data/literature/residuals-v2" / f"{sid}.yaml", {
        "schema_version": "battery_residuals.v2.1",
        "residuals": [{"source_id": sid, "status": "match", "numeric": {"value": 0.12, "unit": "log10"}}],
    })
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "wired"


def test_source_status_ignores_handwritten_stage(tmp_path, monkeypatch):
    """A stage field in extract, sidecar, ledger, or v2.1 YAML is not honoured."""
    root, corpus = _status_tree(tmp_path, monkeypatch)
    sid = "hand-set-2020"
    raw = corpus / "raw" / sid
    raw.mkdir(parents=True)
    (raw / f"{sid}.pdf").write_bytes(b"%PDF-1.4 fixture\n")
    _write_yaml(raw / "sidecar.yaml", {"stage": "ingested_complete", "pipeline_stage": "wired"})
    _write_yaml(corpus / "ledger" / f"{sid}.yaml", {"stage": "wired", "stages": {"scored": {"date": "2026-01-01"}}})
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "inbox"
    assert rows[sid]["stage"] != "ingested_complete"
    assert rows[sid]["stage"] != "wired"


def test_source_status_compilation_not_inbox_with_control(tmp_path, monkeypatch):
    """Compilation-path sources are not inbox; a non-compilation control is."""
    root, corpus = _status_tree(tmp_path, monkeypatch)
    compilation = "fixture-compilation"
    control = "fixture-paper"
    for sid in (compilation, control):
        raw = corpus / "raw" / sid
        raw.mkdir(parents=True)
        (raw / f"{sid}.pdf").write_bytes(b"%PDF-1.4 fixture\n")
        _write_yaml(raw / "sidecar.yaml", {"citation": sid, "doi": f"10.9999/{sid}"})
    _write_yaml(root / "data/literature/compilations" / compilation / "manifest.yaml", {
        "schema_version": "literature_compilation_manifest.v1",
        "source_id": compilation,
        "compilation_role": {"engine_reference_input": True, "validation_measurement": False},
        "source": {"doi": f"10.9999/{compilation}"},
    })
    rows, status = _status_rows(root)
    assert rows[control]["stage"] == "inbox", "control must be inbox so the exclusion is not vacuous"
    assert rows[compilation]["stage"] != "inbox"
    assert rows[compilation]["inbox_exclusion_reason"]
    assert "compilation" in rows[compilation]["inbox_exclusion_reason"]
    inbox_ids = [row["source_id"] for row in status["sources"] if row["stage"] == "inbox"]
    assert control in inbox_ids
    assert compilation not in inbox_ids


def test_source_status_partial_vs_complete_boundary(tmp_path, monkeypatch):
    """One non-usable row among otherwise usable rows is partial, not complete."""
    root, corpus = _status_tree(tmp_path, monkeypatch)
    sid = "boundary-2020"
    raw = corpus / "raw" / sid
    raw.mkdir(parents=True)
    (raw / f"{sid}.pdf").write_bytes(b"%PDF-1.4 fixture\n")
    _write_yaml(root / "data/literature/extracts" / f"{sid}.yaml", _extract_v1(sid))
    _write_yaml(root / "data/literature/extracts-v2" / f"{sid}.yaml",
                _v21(sid, [_usable_obs("ok"), _unusable_obs("bad")]))
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "ingested_partial"
    assert rows[sid]["usable_rows"] == 1
    assert rows[sid]["total_rows"] == 2

    _write_yaml(root / "data/literature/extracts-v2" / f"{sid}.yaml",
                _v21(sid, [_usable_obs("ok")]))
    rows, _ = _status_rows(root)
    assert rows[sid]["stage"] == "ingested_complete"
    assert rows[sid]["usable_rows"] == 1
    assert rows[sid]["total_rows"] == 1


def test_source_status_wired_zero_today_and_positive_control(tmp_path, monkeypatch):
    """Wired is 0 on the real tree (chunk-2 scorer absent) and 1 with a synthetic ledger.

    Wired is a residual-ledger fact (load_scored_residuals). Scanning the v2.1
    store does not change it; the synthetic tree is the stage-machine proof.
    """
    assert builder.load_scored_residuals(REPO_ROOT) == {}
    assert not (REPO_ROOT / "data/literature/battery_residuals.yaml").is_file()
    residuals_dir = REPO_ROOT / "data/literature/residuals-v2"
    assert not residuals_dir.is_dir() or not any(
        path.is_file() and not path.name.startswith("_") for path in residuals_dir.glob("*.yaml")
    )

    root, corpus = _status_tree(tmp_path, monkeypatch)
    sid = "wired-control-2020"
    raw = corpus / "raw" / sid
    raw.mkdir(parents=True)
    (raw / f"{sid}.pdf").write_bytes(b"%PDF-1.4 fixture\n")
    _write_yaml(root / "data/literature/extracts" / f"{sid}.yaml", _extract_v1(sid))
    _write_yaml(root / "data/literature/extracts-v2" / f"{sid}.yaml", _v21(sid, [_usable_obs()]))
    rows, status = _status_rows(root)
    assert status["counts"]["by_stage"]["wired"] == 0
    assert rows[sid]["stage"] != "wired"

    _write_yaml(root / "data/literature/battery_residuals.yaml", {
        "schema_version": "battery_residuals.v2.1",
        "residuals": [{"source_id": sid, "status": "mismatch", "numeric": {"value": 1.5, "unit": "kJ/mol"}}],
    })
    rows, status = _status_rows(root)
    assert status["counts"]["by_stage"]["wired"] == 1
    assert rows[sid]["stage"] == "wired"


def test_source_status_anti_loss_nonempty_cases(tmp_path, monkeypatch):
    """Each anti-loss check has a non-empty fixture case; a forever-zero check is not a check."""
    root, corpus = _status_tree(tmp_path, monkeypatch, git_corpus=True)
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)

    uncommitted = corpus / "raw" / "uncommitted-src" / "note.txt"
    uncommitted.parent.mkdir(parents=True)
    uncommitted.write_text("not committed")

    (corpus / "tracked.txt").write_text("tracked")
    subprocess.run(["git", "-C", str(corpus), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(corpus), "commit", "-m", "base"], check=True, capture_output=True)
    bare = tmp_path / "mirror.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(corpus), "remote", "add", "origin", str(bare)],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(corpus), "push", "origin", "HEAD:main"],
                   check=True, capture_output=True)
    (corpus / "pushed-never.txt").write_text("local only")
    subprocess.run(["git", "-C", str(corpus), "add", "pushed-never.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(corpus), "commit", "-m", "not on mirror"],
                   check=True, capture_output=True)
    unpushed_sha = subprocess.run(["git", "-C", str(corpus), "rev-parse", "HEAD"],
                                  check=True, capture_output=True, text=True).stdout.strip()

    sim_only = "sim-only-extract"
    corpus_only = "corpus-only-extract"
    mismatch = "mismatch-extract"
    _write_yaml(root / "data/literature/extracts" / f"{sim_only}.yaml", _extract_v1(sim_only))
    _write_yaml(corpus / "extracts" / f"{corpus_only}.yaml", _extract_v1(corpus_only))
    _write_yaml(root / "data/literature/extracts" / f"{mismatch}.yaml", _extract_v1(mismatch, review_status="draft"))
    _write_yaml(corpus / "extracts" / f"{mismatch}.yaml", _extract_v1(mismatch, review_status="reviewed"))

    stale_sid = "stale-inbox-2010"
    raw = corpus / "raw" / stale_sid
    raw.mkdir(parents=True)
    (raw / f"{stale_sid}.pdf").write_bytes(b"%PDF-1.4 stale\n")
    _write_yaml(raw / "sidecar.yaml", {"retrieved_at": "2026-01-01T00:00:00Z", "citation": "stale"})

    claim_sid = "finished-claim-2020"
    claim_raw = corpus / "raw" / claim_sid
    claim_raw.mkdir(parents=True)
    (claim_raw / f"{claim_sid}.pdf").write_bytes(b"%PDF-1.4 claimed\n")
    (corpus / "claims" / f"{claim_sid}.claim").write_text("dispatch-done 2026-09-01T00:00:00Z decode\n")
    text_dir = corpus / "text" / claim_sid
    text_dir.mkdir(parents=True)
    (text_dir / "pdftotext-layout.txt").write_text("decoded")

    _, status = _status_rows(root, now=now)
    anti = status["anti_loss"]

    assert anti["corpus_uncommitted"]["count"] >= 1
    assert any("uncommitted-src" in item for item in anti["corpus_uncommitted"]["ids"])

    assert anti["corpus_unpushed"]["count"] >= 1
    assert unpushed_sha in anti["corpus_unpushed"]["ids"]

    parity = anti["extract_mirror_parity"]
    assert sim_only in parity["simulator_only"]["ids"]
    assert parity["simulator_only"]["count"] >= 1
    assert corpus_only in parity["corpus_only"]["ids"]
    assert parity["corpus_only"]["count"] >= 1
    assert mismatch in parity["byte_mismatch"]["ids"]
    assert parity["byte_mismatch"]["count"] >= 1

    assert stale_sid in anti["stale_inbox"]["ids"]
    assert anti["stale_inbox"]["count"] >= 1
    assert builder.STALE_INBOX_DAYS == 7

    assert claim_sid in anti["stale_claims"]["ids"]
    assert anti["stale_claims"]["count"] >= 1


def test_observation_store_summary_matches_full_scan():
    """Committed per-shard counts equal a live observation_id marker scan."""
    live = builder.build_observation_store_summary(REPO_ROOT)
    committed_path = builder.observation_store_summary_path(REPO_ROOT)
    assert committed_path.is_file()
    committed = builder.load_yaml(committed_path)
    assert committed["schema_version"] == builder.STORE_SUMMARY_SCHEMA
    assert live["shards"] == committed["shards"]
    obs_dir = REPO_ROOT / "data/literature/observations-v2"
    assert live["shards"], "compilation observation shards must exist to prove the summary"
    for rel, body in live["shards"].items():
        path = obs_dir / rel
        assert path.is_file(), rel
        assert path.stat().st_size == body["size"]
        assert builder.count_observation_ids(path) == body["observation_id_count"]


def test_load_v21_store_uses_summary_without_rereading_payloads(monkeypatch):
    """Matching size in the derived summary must not open compilation payloads."""
    calls: list[Path] = []

    def forbid(path: Path) -> int:
        calls.append(path)
        raise AssertionError(f"compilation payload reread: {path}")

    monkeypatch.setattr(builder, "count_observation_ids", forbid)
    store = builder.load_v21_store(REPO_ROOT)
    assert calls == []
    assert "janaf" in store
    assert "nist-janaf-4th" in store
    assert store["janaf"]["total_rows"] == store["nist-janaf-4th"]["total_rows"]
    assert store["janaf"]["total_rows"] > 0
    assert store["janaf"]["usable_rows"] == 0


def test_compilation_manifest_identity_matches_yaml():
    """Text-scan of each real manifest equals YAML source_id, source.doi, and raw/ paths."""
    compilations = REPO_ROOT / "data/literature/compilations"
    families = list(builder._iter_named_dirs(compilations))
    assert families
    for family in families:
        manifest = family / "manifest.yaml"
        if not manifest.is_file():
            continue
        sid, doi, raw_ids = builder.compilation_manifest_identity(manifest)
        doc = builder.load_yaml(manifest)
        assert isinstance(doc, dict), family.name
        expected_sid = str(doc["source_id"]).strip() if doc.get("source_id") else None
        assert sid == expected_sid, family.name
        src = doc.get("source") if isinstance(doc.get("source"), dict) else {}
        assert doi == builder.doi_of(src or {}), family.name
        text_raw = tuple(dict.fromkeys(re.findall(
            r"raw/([A-Za-z0-9_.-]+)/", manifest.read_text(encoding="utf-8", errors="replace")
        )))
        assert raw_ids == text_raw, family.name


def test_compilation_manifest_identity_header_fixture(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text(
        "\n".join([
            "schema_version: literature_compilation_manifest.v1",
            "source_id: fixture-compilation",
            "source:",
            "  doi: 10.9999/fixture-compilation",
            "records:",
            "- path: raw/fixture-compilation/fixture-compilation.pdf",
            "",
        ])
    )
    sid, doi, raw_ids = builder.compilation_manifest_identity(path)
    assert sid == "fixture-compilation"
    assert doi == "10.9999/fixture-compilation"
    assert raw_ids == ("fixture-compilation",)
    sid_only, doi_only, raw_only = builder.compilation_manifest_identity(path, scan_raw=False)
    assert (sid_only, doi_only, raw_only) == ("fixture-compilation", "10.9999/fixture-compilation", ())


def test_source_status_regenerates_byte_identically(tmp_path, monkeypatch):
    root, corpus = _status_tree(tmp_path, monkeypatch)
    sid = "alpha-2020"
    raw = corpus / "raw" / sid
    raw.mkdir(parents=True)
    (raw / f"{sid}.pdf").write_bytes(b"%PDF-1.4 fixture\n")
    _write_yaml(raw / "sidecar.yaml", {"citation": "Alpha (2020)"})
    _write_yaml(root / "data/literature/extracts" / f"{sid}.yaml", _extract_v1(sid))
    a, b = tmp_path / "out-a", tmp_path / "out-b"
    _run_builder(root, a)
    _run_builder(root, b)
    assert (a / "SOURCE_STATUS.yaml").is_file()
    assert (a / "SOURCE_STATUS.yaml").read_bytes() == (b / "SOURCE_STATUS.yaml").read_bytes()
