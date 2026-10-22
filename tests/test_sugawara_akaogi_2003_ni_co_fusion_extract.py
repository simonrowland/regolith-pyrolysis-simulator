"""Source-grounded migration and transcription guards for Sugawara & Akaogi 2003."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root
from tools.validate_literature_extracts import validate_extract_document


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/sugawara-akaogi-2003-ni-co-fusion.yaml"
CORPUS = regolith_corpus_root()
SOURCE_TEXT = CORPUS / "text/sugawara-akaogi-2003-ni-co-fusion/pdftotext-layout.txt"

pytestmark = pytest.mark.skipif(
    not SOURCE_TEXT.is_file(), reason="source text is absent from REGOLITH_CORPUS_ROOT"
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, observation_id: str) -> dict:
    return next(
        row
        for species in document["species"].values()
        for row in species.get("observations", [])
        if row["observation_id"] == observation_id
    )


def test_only_fusion_rows_migrate_and_unidentified_rows_remain_context() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    observations = migrator.result.observations
    assert len(observations) == 18
    assert not migrator.result.validation.hard_issues
    assert all(
        observation.identity.quantity.value
        in {Quantity.ENTHALPY_OF_FUSION, Quantity.ENTROPY_OF_FUSION}
        for observation in observations.values()
    )

    context = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    moved_heat_rows = [
        row
        for row in context.values()
        if row.get("type") == "calorimetric_heat_content_context"
    ]
    moved_glass_rows = [
        row
        for row in context.values()
        if row.get("values", {}).get("quantity") == "equilibrium_phase_composition"
    ]
    assert len(moved_heat_rows) == 17
    assert len(moved_glass_rows) == 40
    assert context["suga03_t1_co2sio4_01"]["values"]["enthalpy_increment"] == 194.7
    assert context["suga03_nio_heat_content_context"]["values"]["reference_temperature_K"] == 298
    nio_rows = context["suga03_nio_heat_content_context"]["values"]["rows"]
    assert [(row["atmosphere"], row["value"], row["sigma"]) for row in nio_rows] == [
        ("Ar", 100.6, 9.0),
        ("Air", 87.9, 5.2),
    ]

    for species_formula, species in document["species"].items():
        for row in species.get("observations", []):
            observation = observations[f"{EXTRACT.stem}::{row['observation_id']}"]
            assert observation.identity.species.formula == species_formula
            assert observation.value.kind.value == "point"
            assert observation.identity.temperature_K.is_value
            assert observation.locator is not None
            assert observation.locator.page == observation.locator.pdf_page_index + 1
            assert all(
                notice.kind.value != "source_value_outside_physical_range"
                for notice in observation.notices
            )


def test_fidelity_pin_rejects_a_mutated_printed_cell() -> None:
    mutated = deepcopy(_document())
    _observation(mutated, "suga03_t4_co2sio4_2_heat_fusion")["values"]["delta_H_fus_kJ_mol-1"] = 98.0
    errors = validate_extract_document(
        mutated,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    )
    assert any("fidelity" in error.casefold() for error in errors)


def test_moved_rows_are_absent_from_observations() -> None:
    document = _document()
    observation_ids = {
        row["observation_id"]
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    assert not any(row_id.startswith(("suga03_t1_", "suga03_t2_")) for row_id in observation_ids)
    assert not any(row_id.startswith("suga03_t3_") for row_id in observation_ids)


def test_table_4_author_calculations_remain_numeric_context() -> None:
    document = _document()
    context = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    assert context["suga03_t4_co2sio4_1_heat_fusion"]["values"]["value"] == 103
    assert context["suga03_t4_co2sio4_1_entropy_fusion"]["values"]["value"] == 61
    assert context["suga03_t4_ni2sio4_1_heat_fusion"]["values"]["value"] == 221
    assert context["suga03_t4_ni2sio4_1_entropy_fusion"]["values"]["value"] == 115
