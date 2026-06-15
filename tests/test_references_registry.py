from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

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
    assert 8 <= len(references) <= 12


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


def test_seed_entries_are_not_placeholder_records():
    references = (yaml.safe_load(REGISTRY.read_text(encoding="utf-8")) or {})["references"]

    for ref_id, entry in references.items():
        assert entry["authors"].strip()
        assert entry["title"].strip()
        assert not re.search(r"\b(TBD|TODO|unknown|placeholder)\b", entry["authors"], re.I)
        assert not re.search(r"\b(TBD|TODO|unknown|placeholder)\b", entry["title"], re.I)
        assert entry["doi"] or entry["url"]
        if entry["doi"]:
            assert entry["doi"].startswith("10.")
        if entry["url"]:
            assert entry["url"].startswith(("http://", "https://"))
        if not entry.get("pull_quotes"):
            assert entry.get("needs_quote") is True, ref_id
