"""Drift gate and row-truth tests for the tracked empirical-corpus index."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

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
    for row in index["sources"]:
        if row["sidecar_path"]:
            sidecar = builder.parse_sidecar(REPO_ROOT / row["sidecar_path"])
            if sidecar["doi"]:
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
