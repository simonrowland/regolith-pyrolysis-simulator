"""Pin the distinct Meteoritical Society 2015 abstract as context-only."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import corpus_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2015-cai-mineral-thermodynamics-metsoc.yaml"
SOURCE_ID = "shornikov-2015-cai-mineral-thermodynamics-metsoc"
PDF = corpus_root() / "raw" / SOURCE_ID / f"{SOURCE_ID}.pdf"


def test_metsoc_abstract_is_distinct_and_has_no_numeric_observations():
    doc = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert doc["source_id"] == SOURCE_ID
    assert "78th Annual Meeting of the Meteoritical Society" in doc["source"]["citation"]
    assert "abstract 5017" in doc["source"]["citation"]
    assert "Lunar and Planetary Science Conference" not in doc["source"]["citation"]
    assert doc["source"]["corpus_sha256"] == "61593c040c6ac2f989e3553487e8fc0646d513e28e9baf974e393b0aed58f4e2"
    assert doc["species"]["Al2O3"]["observations"] == []
    context = doc["species"]["Al2O3"]["context"]
    assert len(context) == 1
    record = context[0]
    assert record["type"] == "model_derived_context"
    assert record["locator"]["page"] == 1
    assert record["locator"]["pdf_page_index"] == 0
    assert record["locator"]["abstract"] == "5017"
    values = record["values"]
    assert values["method_class"] == "model_derived"
    assert values["model_as_printed"] == "ideal associated solutions"
    assert values["input_data_as_printed"] == (
        "experimental results obtained by the Knudsen effusion mass spectrometric method [4, 5]"
    )
    assert values["measured_table"] == "none printed"
    assert values["numeric_target_species_results"] == "none printed"
    assert "not printed" in values["reason"]


def test_metsoc_source_text_identifies_abstract_and_model():
    if not PDF.is_file():
        pytest.skip(f"source PDF not available: {PDF}")
    text = subprocess.run(
        ["pdftotext", "-f", "1", "-l", "1", "-layout", str(PDF), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "78th Annual Meeting of the Meteoritical Society (2015)" in text
    assert "5017.pdf" in text
    assert "S. I. Shornikov." in text
    normalized = re.sub(r"-\s*\n\s*", "", text)
    normalized = " ".join(normalized.split())
    assert "ideal associated solutions" in normalized
    assert re.search(r"thermodynamic information was obtained by calculation", normalized, re.IGNORECASE)
    assert not re.search(r"^\s*Table\s+\d+", text, re.MULTILINE | re.IGNORECASE)


def test_context_only_extract_migrates_without_hard_issues():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.observations == {}
