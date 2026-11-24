"""Drift gate and row-truth tests for the tracked empirical-corpus index."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import re
import yaml
from tests.helpers.corpus_paths import regolith_corpus_root, require_corpus_files
from tests.helpers.named_manifests import assert_named_rows, load_named_manifest
from simulator.reference_data.janaf import load_manifest as load_janaf_manifest

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


def test_index_extract_row_counts_match_source_extracts() -> None:
    """Every indexed row count must agree with its owning extract file."""
    index = yaml.safe_load(COMMITTED_YAML.read_text())
    for source in index["sources"]:
        for extract in source.get("extracts") or []:
            doc = yaml.safe_load((REPO_ROOT / extract["path"]).read_text())
            rows = sum(
                len(body.get("observations") or [])
                for body in (doc.get("species") or {}).values()
            )
            assert extract["rows"] == rows, extract["path"]


def test_robie_hemingway_compilation_is_citable_with_published_doi() -> None:
    index = yaml.safe_load(COMMITTED_YAML.read_text())
    source = next(
        row for row in index["sources"]
        if row["source_id"] == "robie-hemingway-1995-usgs-b2131"
    )
    assert source["citation"] == (
        "Robie & Hemingway (1995), Thermodynamic properties of minerals and related substances "
        "at 298.15 K and 1 bar pressure and at higher temperatures, U.S. Geological Survey "
        "Bulletin 2131. https://pubs.usgs.gov/bul/2131/report.pdf"
    )
    assert source["doi"] == "10.3133/b2131"
    assert source["report_number"] == "USGS Bulletin 2131"


def test_aithala_index_keeps_article_primary_and_supplementary_locators() -> None:
    index = yaml.safe_load(COMMITTED_YAML.read_text(encoding="utf-8"))
    source = next(
        row for row in index["sources"]
        if row["source_id"] == "aithala-2026-magmatic-iron-redox-to-2100c"
    )
    assert source["corpus"]["raw"]["path"] == (
        "raw/aithala-2026-magmatic-iron-redox-to-2100c/"
        "aithala-2026-magmatic-iron-redox-to-2100c.pdf"
    )
    assert source["corpus"]["raw"]["sha256"] == (
        "4a3a85dcb94b935de7b3f30af872d2bb7b56d4b0e5c177179f33b4a876694261"
    )


def test_robie_hemingway_compilation_work_retains_index_doi() -> None:
    from simulator.battery.migrate import Migrator

    source_id = "robie-hemingway-1995-usgs-b2131"
    index = yaml.safe_load(COMMITTED_YAML.read_text())
    row = next(item for item in index["sources"] if item["source_id"] == source_id)
    manifest = REPO_ROOT / "data/literature/compilations" / source_id / "manifest.yaml"
    migrator = Migrator(root=REPO_ROOT, index={source_id: row}, aliases={})
    migrator.migrate_index_only_sources()
    migrator._migrate_compilation_file(manifest)
    migrator.finalize()

    works = [work for work in migrator.result.works.values() if source_id in work.source_ids]
    assert len(works) == 1
    assert works[0].doi == "10.3133/b2131"


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
    works_dir = tmp_path / "data/literature/works"
    works_dir.mkdir(parents=True)
    (works_dir / "fixture-work.yaml").write_text(
        "schema_version: battery_work.v2.1\n"
        "work:\n"
        "  work_id: fixture-work\n"
        "  citation: Work metadata citation\n"
        "  doi: 10.9999/work-metadata\n"
        "  source_ids: [paper]\n"
        "  source_files:\n"
        "    corpus_repo: regolith-corpus\n"
        "    corpus_commit: {tag: unknown, reason: fixture}\n"
        "    files:\n"
        "    - asset_id: unknown:paper\n"
        "      role: pdftotext\n"
        "      path: unknown\n"
        "      sha256: {tag: unknown, reason: fixture}\n"
        "experiments: []\n"
        "context: []\n"
    )
    row = builder.build_index(tmp_path)["sources"][0]
    assert row["doi"] == "10.9999/test"
    assert row["citation"].strip("`") == "Author (2020), Paper"
    assert row["sidecar_missing_fields"] == []
    assert row["pdf_status"] == "ABSENT"
    assert row["corpus_status"] == "unavailable"
    assert all(value is None for value in row["corpus"].values())


def test_builder_uses_extract_citation_doi_before_work_doi(monkeypatch, tmp_path):
    monkeypatch.setattr(builder, "load_extracts", lambda root: {
        "extract-doi": {"doc": {"source": {"citation": "Paper DOI: 10.9999/extract"}}, "files": []}
    })
    monkeypatch.setattr(builder, "load_pdfs", lambda root: {})
    monkeypatch.setattr(builder, "load_work_metadata", lambda root: {
        "extract-doi": {"citation": "Fallback citation", "doi": "10.9999/work"}
    })
    monkeypatch.setattr(builder, "load_consumers", lambda root: ([], []))
    monkeypatch.setattr(builder, "git_tracked_set", lambda root: ("not-git", None))
    monkeypatch.setattr(builder, "walk_private_roots", lambda root, paths: ([], [], {}))
    monkeypatch.setattr(builder, "build_alias_map", lambda *args: ({}, []))

    row = builder.build_index(tmp_path)["sources"][0]
    assert row["doi"] == "10.9999/extract"


def test_extract_citation_doi_mutation_is_detected(monkeypatch, tmp_path):
    """Mutation probe: a later work DOI must not replace the resolved extract DOI."""
    monkeypatch.setattr(builder, "load_extracts", lambda root: {
        "extract-doi": {"doc": {"source": {"citation": "Paper DOI: 10.9999/extract"}}, "files": []}
    })
    monkeypatch.setattr(builder, "load_pdfs", lambda root: {})
    monkeypatch.setattr(builder, "load_work_metadata", lambda root: {
        "extract-doi": {"citation": "Fallback citation", "doi": "10.9999/work"}
    })
    monkeypatch.setattr(builder, "load_consumers", lambda root: ([], []))
    monkeypatch.setattr(builder, "git_tracked_set", lambda root: ("not-git", None))
    monkeypatch.setattr(builder, "walk_private_roots", lambda root, paths: ([], [], {}))
    monkeypatch.setattr(builder, "build_alias_map", lambda *args: ({}, []))
    original = builder.build_index

    def mutated(root, **kwargs):
        rows = original(root, **kwargs)
        rows["sources"][0]["doi"] = "10.9999/work"
        return rows

    monkeypatch.setattr(builder, "build_index", mutated)
    with pytest.raises(AssertionError):
        assert builder.build_index(tmp_path)["sources"][0]["doi"] == "10.9999/extract"


def test_work_metadata_rejects_empty_assets_and_accepts_typed_placeholder(tmp_path):
    works = tmp_path / "data/literature/works"
    works.mkdir(parents=True)
    empty = {
        "schema_version": "battery_work.v2.1",
        "work": {"source_ids": ["empty"], "citation": "Empty", "source_files": {"files": []}},
        "experiments": [], "context": [],
    }
    placeholder = {
        "schema_version": "battery_work.v2.1",
        "work": {"work_id": "placeholder", "source_ids": ["placeholder"], "citation": "Placeholder", "source_files": {
            "files": [{
                "asset_id": "unknown:placeholder", "role": "pdftotext", "path": "unknown",
                "sha256": {"tag": "unknown", "reason": "INDEX does not give an unmatched-layer asset"},
            }]
        }},
        "experiments": [], "context": [],
    }
    missing_sha256 = {
        "schema_version": "battery_work.v2.1",
        "work": {"work_id": "missing-sha256", "source_ids": ["missing-sha256"], "citation": "Missing sha256", "source_files": {
            "files": [{"asset_id": "unknown:missing-sha256", "role": "pdftotext", "path": "unknown"}]
        }},
        "experiments": [], "context": [],
    }
    (works / "empty.yaml").write_text(yaml.safe_dump(empty))
    (works / "placeholder.yaml").write_text(yaml.safe_dump(placeholder))
    (works / "missing-sha256.yaml").write_text(yaml.safe_dump(missing_sha256))

    metadata = builder.load_work_metadata(tmp_path)
    assert "empty" not in metadata
    assert "missing-sha256" not in metadata
    assert metadata["placeholder"]["citation"] == "Placeholder"


def test_empty_asset_mutation_is_detected(monkeypatch, tmp_path):
    """Mutation probe: admitting an empty asset list violates the loader contract."""
    original = builder.load_work_metadata
    works = tmp_path / "data/literature/works"
    works.mkdir(parents=True)
    (works / "empty.yaml").write_text(yaml.safe_dump({
        "schema_version": "battery_work.v2.1",
        "work": {"source_ids": ["empty"], "citation": "Empty", "source_files": {"files": []}},
        "experiments": [], "context": [],
    }))
    assert "empty" not in original(tmp_path)
    monkeypatch.setattr(builder, "load_work_metadata", lambda root: {"empty": {"citation": "Empty"}})
    with pytest.raises(AssertionError):
        assert "empty" not in builder.load_work_metadata(tmp_path)


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


@pytest.mark.parametrize("path_kind", ["raw", "corpus_raw", "absolute"])
def test_corpus_pointers_use_stated_provenance_path(tmp_path, path_kind):
    corpus = tmp_path / "corpus"
    raw_rel = "raw/ammin-73-470/ammin-73-470.pdf"
    raw = corpus / raw_rel
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"pdf")
    mirror = tmp_path / "paper.yaml"
    mirror.write_text("extract: true\n")
    if path_kind == "raw":
        provenance_path = raw_rel
    elif path_kind == "corpus_raw":
        provenance_path = f"corpus/{raw_rel}"
    else:
        provenance_path = str(raw)

    pointers = builder.corpus_pointers(
        corpus,
        "paper",
        hashlib.sha256(b"pdf").hexdigest(),
        mirror,
        "abc",
        extract_doc={"extraction": {"provenance_path": provenance_path}},
    )
    assert pointers["raw"]["path"] == raw_rel
    assert pointers["raw"]["present"] is True
    assert pointers["raw"]["matches_simulator"] is True


def test_corpus_pointers_use_unique_raw_locator_when_provenance_is_private(tmp_path):
    corpus = tmp_path / "corpus"
    raw_rel = "raw/ammin-75-781/ammin-75-781.pdf"
    raw = corpus / raw_rel
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"pdf")
    mirror = tmp_path / "paper.yaml"
    mirror.write_text("extract: true\n")

    pointers = builder.corpus_pointers(
        corpus,
        "paper",
        hashlib.sha256(b"pdf").hexdigest(),
        mirror,
        "abc",
        extract_doc={
            "extraction": {"provenance_path": "docs-private/paper.pdf"},
            "species": {
                "NiO": {
                    "observations": [
                        {"locator": {"source_path": str(raw)}}
                    ]
                }
            },
        },
    )
    assert pointers["raw"]["path"] == raw_rel
    assert pointers["raw"]["present"] is True


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
    for source_id in ("nasa-cea-thermo",):
        row = next(row for row in index["sources"] if row["source_id"] == source_id)
        assert row["extracts"][0]["locator_status"] == "private_path"
        assert source_id in index["gaps"]["extracts_with_private_locators"]


# ---------------------------------------------------------------------------
# Generated SOURCE_STATUS.yaml — tags derived from artefacts, never hand-set.
# ---------------------------------------------------------------------------

COMMITTED_STATUS = REPO_ROOT / "data" / "literature" / "SOURCE_STATUS.yaml"


def test_repo_source_status_counts_match_rows() -> None:
    status = yaml.safe_load(COMMITTED_STATUS.read_text(encoding="utf-8"))
    # build_index.py omits a stage whose row count is zero.
    by_stage = {
        stage: sum(row["stage"] == stage for row in status["sources"])
        for stage in builder.STATUS_STAGES
    }
    by_stage = {name: count for name, count in by_stage.items() if count}
    assert status["counts"] == {
        "sources": len(status["sources"]),
        "by_stage": by_stage,
    }


def test_repo_hybrid_index_count_pins_match_green_plus_stack_deltas() -> None:
    index = yaml.safe_load(COMMITTED_YAML.read_text(encoding="utf-8"))
    counts = index["counts"]
    extract_census = load_named_manifest(
        REPO_ROOT / "tests" / "manifests" / "literature_extract_source_ids.jsonl"
    )
    excluded_extract = "bencze-yazhenskikh-2016-table-s1-k.yaml"
    assert excluded_extract in extract_census
    # The supplementary table uses literature_extract_table.v1, so build_index
    # excludes it from the literature_extract.v1 index census.
    expected_extracts = {
        path: source_id
        for path, source_id in extract_census.items()
        if path != excluded_extract
    }
    indexed_extracts = {
        Path(extract["path"]).name: source["source_id"]
        for source in index["sources"]
        for extract in source["extracts"]
    }
    assert_named_rows(
        indexed_extracts, expected_extracts, label="literature index extracts"
    )
    # Pin the generated index for the approved combined-offer merge stack.
    # The named comparison runs first so an added or dropped extract is
    # reported by filename before this aggregate pin can fail on a bare count.
    indexed_source_rows = {source["source_id"]: source for source in index["sources"]}
    t1880_source_ids = {
        "barin-1989",
        "ivtan-nio-thermo",
        "ivtan-zno-thermo",
        "robie-huebner-hemingway-1995-braunite-rhodonite-thermodynamics",
    }
    assert t1880_source_ids <= indexed_source_rows.keys()
    assert indexed_source_rows[
        "robie-huebner-hemingway-1995-braunite-rhodonite-thermodynamics"
    ]["doi"] == "10.2138/am-1995-5-615"
    indexed_sidecar_gaps = {
        gap["source_id"]: gap for gap in index["gaps"]["sidecars_missing_fields"]
    }
    assert t1880_source_ids <= indexed_sidecar_gaps.keys()
    expected_sidecar_gaps = {
        "barin-1989": ["doi", "license_or_oa_basis", "sha256", "retrieved_date"],
        "ivtan-nio-thermo": ["doi", "license_or_oa_basis", "sha256", "retrieved_date"],
        "ivtan-zno-thermo": ["doi", "license_or_oa_basis", "sha256", "retrieved_date"],
        "robie-huebner-hemingway-1995-braunite-rhodonite-thermodynamics": [
            "license_or_oa_basis",
            "sha256",
            "retrieved_date",
        ],
    }
    assert {
        source_id: indexed_sidecar_gaps[source_id]["missing_fields"]
        for source_id in t1880_source_ids
    } == expected_sidecar_gaps
    assert counts["sources"] == 879
    # PDF status of every source with extracts, compared by source id against
    # a checked-in manifest so a changed status is named, and the
    # extracts_without_pdf count is pinned to that independent expectation
    # rather than to a recount of the same index (green pinned it as 848).
    indexed_pdf_status = {
        source["source_id"]: source["pdf_status"]
        for source in index["sources"]
        if source["extracts"]
    }
    expected_pdf_status = load_named_manifest(
        REPO_ROOT / "tests" / "manifests" / "literature_source_pdf_status.jsonl"
    )
    assert_named_rows(
        indexed_pdf_status,
        expected_pdf_status,
        label="literature index source PDF status",
    )
    assert counts["extracts"] == len(indexed_extracts)
    assert counts["extracts_without_pdf"] == sum(
        status == "ABSENT" for status in expected_pdf_status.values()
    )
    assert counts["alias_groups_needing_resolution"] == 94

    status = yaml.safe_load(COMMITTED_STATUS.read_text(encoding="utf-8"))
    # SOURCE_STATUS is restricted to green plus extracts in this merge stack.
    assert status["counts"]["sources"] == 2338
    assert status["counts"]["by_stage"] == {
        "located": 206,
        "inbox": 1244,
        "in_progress": 198,
        "ingested_partial": 183,
        "ingested_complete": 149,
        "unknown": 358,
    }


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


def test_source_status_covered_by_alias_exclusion_and_missing_target_refusal(tmp_path, monkeypatch):
    root, corpus = _status_tree(tmp_path, monkeypatch)
    source_id = "fixture-appendix"
    target_id = "fixture-main-paper"
    raw = corpus / "raw" / source_id
    raw.mkdir(parents=True)
    (raw / f"{source_id}.pdf").write_bytes(b"%PDF-1.4 fixture\n")
    _write_yaml(root / "data/literature/extracts" / f"{target_id}.yaml", _extract_v1(target_id))
    declaration = {
        source_id: {
            "covered_by": target_id,
            "note": "The appendix adds no distinct numeric rows (189/189 rows match Tables AI-AXX).",
        }
    }
    _write_yaml(root / "data/literature/source_aliases.yaml", declaration)

    rows, status = _status_rows(root)
    assert rows[source_id]["stage"] == "unknown"
    assert rows[source_id]["inbox_exclusion_reason"] == (
        "covered_by: source is covered by fixture-main-paper; "
        "The appendix adds no distinct numeric rows (189/189 rows match Tables AI-AXX)."
    )
    assert source_id not in [row["source_id"] for row in status["sources"] if row["stage"] == "inbox"]

    declaration[source_id]["covered_by"] = "missing-target"
    _write_yaml(root / "data/literature/source_aliases.yaml", declaration)
    with pytest.raises(ValueError, match="not a known source with an extract"):
        builder.build_source_status(root)


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
    assert "wired" not in status["counts"]["by_stage"]
    assert rows[sid]["stage"] != "wired"

    _write_yaml(root / "data/literature/battery_residuals.yaml", {
        "schema_version": "battery_residuals.v2.1",
        "residuals": [{"source_id": sid, "status": "mismatch", "numeric": {"value": 1.5, "unit": "kJ/mol"}}],
    })
    rows, status = _status_rows(root)
    assert status["counts"]["by_stage"]["wired"] == 1
    assert rows[sid]["stage"] == "wired"


def test_source_status_anti_loss_nonempty_cases(tmp_path, monkeypatch):
    """Anti-loss checks report tracked changes and concrete parity/staleness cases."""
    root, corpus = _status_tree(tmp_path, monkeypatch, git_corpus=True)
    now = datetime(2026, 9, 18, tzinfo=timezone.utc)

    (corpus / "tracked.txt").write_text("tracked")
    subprocess.run(["git", "-C", str(corpus), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(corpus), "commit", "-m", "base"], check=True, capture_output=True)
    (corpus / "tracked.txt").write_text("tracked edit")
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

    assert anti["corpus_uncommitted"] == {"count": 1, "ids": ["tracked.txt"]}

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


def test_source_status_untracked_corpus_scratch_is_excluded(tmp_path, monkeypatch, capsys):
    root, corpus = _status_tree(tmp_path, monkeypatch, git_corpus=True)
    tracked = corpus / "tracked.txt"
    tracked.write_text("base")
    subprocess.run(["git", "-C", str(corpus), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(corpus), "commit", "-m", "base"], check=True, capture_output=True)
    tracked.write_text("modified")

    scratch = corpus / ".agent-scratch" / "nested"
    scratch.mkdir(parents=True)
    private_names = [f"private-note-{i:02}.txt" for i in range(12)]
    for name in private_names:
        (scratch / name).write_text("untracked")

    _, status = _status_rows(root)
    out_dir = tmp_path / "out"
    builder.write_source_status(status, out_dir)
    output = (out_dir / "SOURCE_STATUS.yaml").read_text()

    assert status["anti_loss"]["corpus_uncommitted"] == {"count": 1, "ids": ["tracked.txt"]}
    assert "tracked.txt" in output
    assert all(name not in output for name in private_names)

    cli_out = tmp_path / "cli-out"
    assert builder.main(["--root", str(root), "--out-dir", str(cli_out)]) == 0
    stderr = capsys.readouterr().err.splitlines()
    assert stderr == ["corpus untracked files (not in SOURCE_STATUS): 1"]
    assert all(name not in "\n".join(stderr) for name in private_names)


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
    on_disk = builder.iter_observation_store_paths(obs_dir)
    included = {
        builder.posix(path.relative_to(obs_dir))
        for path in on_disk
        if builder.is_compilation_observation_shard(path)
    }
    excluded = {
        builder.posix(path.relative_to(obs_dir))
        for path in on_disk
        if not builder.is_compilation_observation_shard(path)
    }
    missing = sorted(included - set(committed["shards"]))
    extra = sorted(set(committed["shards"]) - included)
    assert not missing and not extra, (
        "compilation-shard summary scope drifted: "
        f"missing={missing} extra={extra}"
    )
    leaked = sorted(excluded & set(committed["shards"]))
    assert not leaked, (
        f"{len(excluded)} non-compilation store files must be absent from the summary; "
        f"leaked {leaked}"
    )
    assert len(excluded) == 7, (
        "non-compilation store files excluded from the summary: "
        f"expected 7, got {len(excluded)} {sorted(excluded)}"
    )


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


def test_source_files_do_not_register_absent_layers():
    from simulator.battery.migrate import source_files_for

    files = source_files_for(
        "fixture-source",
        {
            "corpus": {
                "raw": {"path": "raw/fixture.pdf", "present": False},
                "text": {"path": "text/fixture", "exists": False},
                "tables": {"path": "tables/fixture", "exists": False},
                "ocr": {"path": "ocr/fixture.md", "exists": False},
            }
        },
    )

    assert {asset.asset_id for asset in files.files} == {"unknown:fixture-source"}


def test_registered_citation_only_works_load_and_feed_index_metadata(tmp_path: Path) -> None:
    import shutil

    from simulator.battery import migrate
    from simulator.battery.migrate import load_migrated_store

    source_work_ids = {
        "bell-roseboom-1969-jadeite-albite-melting": "f71f1546815d059a2957da7b9fb0bf4c2b21e5d9f3bc4561c8edde06b92949c0",
        "blander-pelton-1983-anl-fe-83-19": "78648bc6f6f54256098f7a3b7759de3541ca2c88acfee01bf08d7d9dc6dc4964",
        "chan-alcock-jacob-1973-oxygen-potential": "10.1179/cmq.1973.12.4.439",
        "glushko-1978-1982-thermodynamic-properties": "b81da04104c1d52d2d9ed013ac28045c05bb0dd3f65c6ec66657bc50288bf74c",
        "pankratz-kelley-1964-ri6555": "3d82819f9f3147a4a45a6cc246d02b855fdcd99b02d90acb43f757e12694e966",
        "proks-eliasova-kosa-1977-heat-fusion-akermanite": "9580e89efeab1ab64dce87d028bb8e06de14be5cfea560f7dcafb95f75a9c14f",
        "rein-chipman-1965-trans-aime": "c0c411028f74688fa14e4daa552004540acc370ce6385682af9edaa2c8106e4e",
        "richet-bottinga-1984-epsl": "10.1016/0012-821x(84)90179-1",
        "schairer-bowen-1955-kas-system": "10.2475/ajs.253.12.681",
        "stout-piwinskii-1982-silicate-melts": "2bbf78ec242637310d3d76fd589b6293a41edebd4ba5e98d4f688037fc325e39",
    }
    works_dir = tmp_path / "data/literature/works"
    works_dir.mkdir(parents=True)
    for work_id in source_work_ids.values():
        shutil.copyfile(
            REPO_ROOT / "data/literature/works" / migrate.work_filename(work_id),
            works_dir / migrate.work_filename(work_id),
        )

    loaded, experiments, observations = load_migrated_store(tmp_path)
    assert set(loaded) == set(source_work_ids.values())
    assert experiments == {}
    assert observations == {}
    for source_id, work_id in source_work_ids.items():
        work = loaded[work_id]
        assets = work.source_files.files
        assert len(assets) == 1
        assert assets[0].asset_id == f"unknown:{source_id}"
        assert assets[0].path == "unknown"
        assert migrate.work_id_for(
            citation=work.citation, doi=work.doi, source_id=source_id, aliases={},
        )[0] == work_id

    indexed = builder.load_work_metadata(REPO_ROOT)
    assert set(source_work_ids) <= indexed.keys()
    for source_id, doi in {
        "chan-alcock-jacob-1973-oxygen-potential": "10.1179/cmq.1973.12.4.439",
        "richet-bottinga-1984-epsl": "10.1016/0012-821x(84)90179-1",
        "schairer-bowen-1955-kas-system": "10.2475/ajs.253.12.681",
    }.items():
        assert indexed[source_id]["doi"] == doi
        assert migrate.work_id_for(
            citation=loaded[doi].citation,
            doi=indexed[source_id]["doi"], source_id=source_id, aliases={},
        ) == (doi, doi)


def test_publisher_doi_identity_pin_fails_if_hash_fallback_returns(monkeypatch, tmp_path: Path) -> None:
    from simulator.battery import migrate

    monkeypatch.setattr(
        migrate,
        "work_id_for",
        lambda *, citation, doi, source_id, aliases: (migrate.citation_hash(citation), None),
    )
    with pytest.raises(AssertionError):
        test_registered_citation_only_works_load_and_feed_index_metadata(tmp_path)


def _require_nist_janaf_source_files(corpus_root: Path, table_ids: set[str]) -> None:
    txt_dir = corpus_root / "raw" / "janaf-nist-txt"
    require_corpus_files(txt_dir / f"{table_id}.txt" for table_id in sorted(table_ids))


def test_partial_nist_janaf_corpus_skips_at_missing_source_file(tmp_path: Path) -> None:
    corpus_root = tmp_path / "regolith-corpus"
    corpus_root.mkdir()
    missing = corpus_root / "raw" / "janaf-nist-txt" / "K-001.txt"

    with pytest.raises(pytest.skip.Exception, match=re.escape(str(missing))):
        _require_nist_janaf_source_files(corpus_root, {"K-001"})


def test_nist_janaf_index_asset_is_the_corpus_txt_download() -> None:
    """The compilation's INDEX tables asset is raw/janaf-nist-txt, not an empty convention path.

    Null hypothesis: the row is attached to tables/nist-janaf-4th or to the
    absent monograph PDF. Those paths are not the files the tables were parsed
    from. choose_read_from would still accept them for a tables/ locator.
    """
    import hashlib

    from simulator.battery.migrate import choose_read_from, source_files_for, unmatched_read_from_reason
    from simulator.battery.records import Locator, Work

    expected_table_ids = {
        entry["table_id"] for entry in load_janaf_manifest()["entries"]
    }
    configured_corpus_root = regolith_corpus_root()
    _require_nist_janaf_source_files(configured_corpus_root, expected_table_ids)
    index = builder.build_index(REPO_ROOT)
    rows = {row["source_id"]: row for row in index["sources"]}
    row = rows["nist-janaf-4th"]
    tables = row["corpus"]["tables"]
    corpus_root = Path(index["scan"]["corpus_root"])
    txt_dir = corpus_root / "raw" / "janaf-nist-txt"
    actual_table_ids = {path.stem for path in txt_dir.glob("*.txt")}
    assert_named_rows(
        {table_id: None for table_id in actual_table_ids},
        {table_id: None for table_id in expected_table_ids},
        label="NIST-JANAF raw table file paths",
    )

    assert tables["path"] == "raw/janaf-nist-txt"
    assert tables["exists"] is True
    assert row["pdf_status"] == "ABSENT"
    assert row["pdf_path"] is None
    assert row["extracts"] == []
    assert row["aliases"] == []
    assert row["doi"] == "10.18434/T42S31"
    legacy = rows["janaf-4th"]
    assert legacy["corpus"]["tables"]["path"] != "raw/janaf-nist-txt"
    assert "nist-janaf-4th" not in legacy["aliases"]
    assert tables["file_count"] == len(expected_table_ids)
    txt = txt_dir / "K-001.txt"
    header = (REPO_ROOT / "data/literature/compilations/janaf/tables/K-001.yaml").read_text(
        encoding="utf-8", errors="replace"
    )[:2000]
    recorded = None
    for line in header.splitlines():
        if "source_sha256" in line:
            recorded = line.split(":", 1)[-1].strip().strip(",").strip().strip('"')
            break
    assert recorded
    assert hashlib.sha256(txt.read_bytes()).hexdigest() == recorded

    files = source_files_for("nist-janaf-4th", row)
    asset = next(item for item in files.files if item.asset_id == "tables:nist-janaf-4th")
    assert asset.path == "raw/janaf-nist-txt"
    work = Work(
        work_id="nist-janaf-4th",
        citation=row["citation"],
        source_ids=("nist-janaf-4th",),
        source_files=files,
        doi=row["doi"],
    )
    locator = Locator(
        table="K-001",
        source_path="data/literature/compilations/janaf/tables/K-001.yaml",
        record="K-001",
    )
    read_from = choose_read_from(work, locator)
    assert read_from == "tables:nist-janaf-4th"
    assert unmatched_read_from_reason(locator, read_from) is None


def test_nist_janaf_txt_tables_refuses_stem_mismatch_and_content_drift(tmp_path):
    """Stem equality alone must not advertise a directory whose bytes were not read.

    Null hypothesis: same-named .txt with different bytes still registers the asset
    and choose_read_from attaches every tables/ locator to it.
    """
    import hashlib

    root = tmp_path / "root"
    corpus = tmp_path / "corpus"
    tables = root / "data/literature/compilations/janaf/tables"
    txt_dir = corpus / "raw" / "janaf-nist-txt"
    tables.mkdir(parents=True)
    txt_dir.mkdir(parents=True)
    body = b"NIST tab-delimited fixture\n"
    digest = hashlib.sha256(body).hexdigest()
    (tables / "A-001.yaml").write_text(
        f"source_id: nist-janaf-4th\nextraction:\n  source_sha256: {digest}\n",
        encoding="utf-8",
    )
    (txt_dir / "A-001.txt").write_bytes(body)
    assert builder.nist_janaf_txt_tables(root, corpus)["path"] == "raw/janaf-nist-txt"

    (txt_dir / "FOREIGN.txt").write_bytes(b"x")
    assert builder.nist_janaf_txt_tables(root, corpus)["exists"] is False
    (txt_dir / "FOREIGN.txt").unlink()

    (txt_dir / "A-001.txt").write_bytes(b"ATTACKER_BYTES")
    drifted = builder.nist_janaf_txt_tables(root, corpus)
    assert drifted["path"] == ""
    assert drifted["exists"] is False


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
