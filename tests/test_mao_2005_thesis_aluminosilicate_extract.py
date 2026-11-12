"""Pins Mao (2005) KTH kappa as a context-only cover essay."""

from pathlib import Path

import yaml

from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/mao-2005-thesis-aluminosilicate.yaml"


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_mao_2005_kappa_is_context_only_and_names_appended_papers() -> None:
    document = _document()
    assert document["source_id"] == "mao-2005-thesis-aluminosilicate"
    assert document["source"]["year"] == 2005
    assert document["review_status"] == "draft"
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    block = document["species"]["alumino_silicate_systems"]
    assert block["observations"] == []
    records = {row["observation_id"]: row for row in block["context"]}
    assert set(records) == {
        "mao_2005_kappa_scope_and_appended_papers",
        "mao_2005_liquid_model_formulae",
        "mao_2005_no_tabulated_assessment_in_kappa",
    }

    scope = records["mao_2005_kappa_scope_and_appended_papers"]
    assert scope["values"]["appended_papers_present_in_this_pdf"] is False
    assert scope["values"]["isbn_as_printed"] == "91-7178-087-4"
    assert scope["values"]["isrn_as_printed"] == "KTH/MSE--05/52--SE+THERM/AVH"
    papers = {row["id"]: row for row in scope["values"]["papers"]}
    assert set(papers) == {"I", "II", "III", "IV", "V"}
    assert papers["II"]["title_spelling_as_printed"] == "reevalution"
    assert "reevalution" in papers["II"]["title_as_printed"]
    assert papers["II"]["venue_as_printed"].startswith("Calphad")

    formulae = records["mao_2005_liquid_model_formulae"]["values"]
    assert formulae["new_species_as_printed"] == "AlO2-1"
    assert formulae["liquid_alumina_as_printed"] == (
        "the modelling of liquid Al2O3 as (Al+3)1(AlO2-1)3"
    )
    assert "CN=6" in formulae["coordination_as_printed"]
    assert "3Al2O3" in formulae["mullite_as_printed"] or "mullite" in formulae["mullite_as_printed"]
    assert "(Y+3)4(Al+3,Si+4)2(O-2,Va)(O-2)9" in formulae["paper_V_YAM_formula_as_printed"]

    empty = records["mao_2005_no_tabulated_assessment_in_kappa"]["values"]
    assert empty["tables_in_this_pdf"] == 0
    assert empty["figures_in_this_pdf"] == 0
    assert empty["printed_numeric_experimental_or_assessed_values"] == "none"

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert not migrator.result.observations
    assert not migrator.result.validation.hard_issues
