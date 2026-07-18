from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "references" / "references.yaml"
BUILDER = ROOT / "docs" / "references" / "build_references.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("build_references", BUILDER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_references_yaml_parses_and_schema_valid():
    builder = load_builder()
    references = builder.load_registry(REGISTRY)

    errors, _warnings = builder.validate_registry(references)

    assert errors == []
    assert len(references) >= 8


def test_build_references_check_passes():
    result = subprocess.run(
        [sys.executable, str(BUILDER), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_reference_ids_are_unique_and_zero_padded():
    raw = REGISTRY.read_text(encoding="utf-8")
    ids = re.findall(r"^  (REF-\d{3,}):", raw, flags=re.MULTILINE)
    parsed_ids = list((yaml.safe_load(raw) or {})["references"])

    assert ids == parsed_ids
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"REF-\d{3,}", ref_id) for ref_id in ids)


def test_duplicate_ref_ids_are_rejected_by_builder(tmp_path):
    builder = load_builder()
    raw = REGISTRY.read_text(encoding="utf-8")
    duplicate = raw + """
  REF-011:
    authors: "Duplicate"
    title: "Duplicate"
    journal: ""
    year: 2026
    volume: ""
    pages: ""
    doi: ""
    url: ""
    role: DATA
    topic: thermochemistry
    status: current
    found: "duplicate test"
    needs_quote: true
    pull_quotes: []
"""
    registry = tmp_path / "references.yaml"
    registry.write_text(duplicate, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate reference IDs"):
        builder.load_registry(registry)


def test_live_cited_blank_doi_url_requires_unavailable_reason():
    builder = load_builder()
    references = {
        "REF-999": {
            "status": "current",
            "doi": "",
            "url": "",
        }
    }
    cited_by = {
        "REF-999": [
            builder.CitationUse(file="data/example.yaml", line=12, context="source: REF-999")
        ]
    }

    errors, _warnings = builder.validate_citations(references, cited_by)

    assert any("blank doi and url without provenance_unavailable_reason" in error for error in errors)

    references["REF-999"]["provenance_unavailable_reason"] = "No DOI or stable URL exists."
    errors, _warnings = builder.validate_citations(references, cited_by)

    assert not any("blank doi and url without provenance_unavailable_reason" in error for error in errors)


def test_scan_excludes_private_chemistry_provenance_notes(tmp_path):
    builder = load_builder()
    notes = tmp_path / "docs" / "chemistry-provenance.notes.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("Private audit note citing REF-999.\n", encoding="utf-8")
    public = tmp_path / "docs" / "public.md"
    public.write_text("Public model note citing REF-998.\n", encoding="utf-8")

    assert builder.scan_files(tmp_path) == {
        "REF-998": [
            builder.CitationUse(
                file="docs/public.md",
                line=1,
                context="Public model note citing REF-998.",
            )
        ]
    }


def test_doi_manifest_rejects_mislabelled_title_and_fake_doi():
    builder = load_builder()
    references = {ref_id: dict(entry) for ref_id, entry in builder.load_registry(REGISTRY).items()}
    manifest = builder.load_doi_manifest()

    wrong_title = {ref_id: dict(entry) for ref_id, entry in references.items()}
    wrong_title["REF-011"]["title"] = "Carbon-rich dust in comet 67P/Churyumov-Gerasimenko measured by COSIMA/Rosetta"
    errors, _warnings = builder.validate_registry(wrong_title, manifest)
    assert any("REF-011: registry title does not match DOI-resolved title" in error for error in errors)

    fake_doi = {ref_id: dict(entry) for ref_id, entry in references.items()}
    fake_doi["REF-011"]["doi"] = "10.9999/not-a-real-ref"
    errors, _warnings = builder.validate_registry(fake_doi, manifest)
    assert any("REF-011: DOI manifest expects 10.1038/nature19320" in error for error in errors)


def test_rendered_html_diffs_detect_stale_topic(tmp_path):
    builder = load_builder()
    expected = tmp_path / "expected"
    actual = tmp_path / "actual"
    (expected / "topics").mkdir(parents=True)
    (actual / "topics").mkdir(parents=True)
    (expected / "index.html").write_text("same", encoding="utf-8")
    (actual / "index.html").write_text("same", encoding="utf-8")
    (expected / "topics" / "vapor-pressure.html").write_text("old", encoding="utf-8")
    (actual / "topics" / "vapor-pressure.html").write_text("new", encoding="utf-8")

    assert builder.rendered_html_diffs(expected, actual) == [
        "stale generated HTML: topics/vapor-pressure.html"
    ]


def test_seed_entries_are_not_placeholder_records():
    references = (yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {})["references"]

    for ref_id, entry in references.items():
        assert entry["authors"].strip()
        assert entry["title"].strip()
        assert not re.search(r"\b(TBD|TODO|unknown|placeholder)\b", entry["authors"], re.I)
        assert not re.search(r"\b(TBD|TODO|unknown|placeholder)\b", entry["title"], re.I)
        if entry["doi"]:
            assert entry["doi"].startswith("10.")
        if entry["url"]:
            assert entry["url"].startswith(("http://", "https://"))
        if not entry["doi"] and not entry["url"]:
            assert entry.get("needs_quote") is True or entry.get("provenance_unavailable_reason"), ref_id
        if not entry.get("pull_quotes"):
            assert entry.get("needs_quote") is True, ref_id
