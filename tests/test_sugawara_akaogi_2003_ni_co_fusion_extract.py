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


def test_table_3_starting_compositions_match_printed_rows_after_migration() -> None:
    expected = {
        "suga03_t3_starting_composition_x12.5": {
            "starting_material_mol_percent": {
                "anorthite": 46.67,
                "diopside": 46.67,
                "Ni2SiO4": 6.67,
            },
            "starting_material_join_fraction": {
                "join": "An50Di50-Ni2SiO4",
                "Ni2SiO4": 12.5,
            },
            "starting_composition_wt_percent": {
                "SiO2": 47.44,
                "Al2O3": 19.43,
                "MgO": 7.68,
                "NiO": 4.07,
                "CaO": 21.38,
                "total": 100.00,
            },
        },
        "suga03_t3_starting_composition_x25": {
            "starting_material_mol_percent": {
                "anorthite": 42.86,
                "diopside": 42.86,
                "Ni2SiO4": 14.29,
            },
            "starting_material_join_fraction": {
                "join": "An50Di50-Ni2SiO4",
                "Ni2SiO4": 25.0,
            },
            "starting_composition_wt_percent": {
                "SiO2": 46.12,
                "Al2O3": 18.06,
                "MgO": 7.14,
                "NiO": 8.82,
                "CaO": 19.87,
                "total": 100.00,
            },
        },
        "suga03_t3_starting_composition_x37.5": {
            "starting_material_mol_percent": {
                "anorthite": 38.46,
                "diopside": 38.46,
                "Ni2SiO4": 23.08,
            },
            "starting_material_join_fraction": {
                "join": "An50Di50-Ni2SiO4",
                "Ni2SiO4": 37.5,
            },
            "starting_composition_wt_percent": {
                "SiO2": 44.55,
                "Al2O3": 16.43,
                "MgO": 6.50,
                "NiO": 14.45,
                "CaO": 18.08,
                "total": 100.00,
            },
        },
        "suga03_t3_starting_composition_x50": {
            "starting_material_mol_percent": {
                "anorthite": 33.33,
                "diopside": 33.33,
                "Ni2SiO4": 33.33,
            },
            "starting_material_join_fraction": {
                "join": "An50Di50-Ni2SiO4",
                "Ni2SiO4": 50.0,
            },
            "starting_composition_wt_percent": {
                "SiO2": 42.66,
                "Al2O3": 14.48,
                "MgO": 5.72,
                "NiO": 21.21,
                "CaO": 15.93,
                "total": 100.00,
            },
        },
    }
    document = _document()
    source_rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("context", [])
    }
    for observation_id, values in expected.items():
        row = source_rows[observation_id]
        assert row["type"] == "bulk_charge_analysis_context"
        assert {key: row["values"][key] for key in values} == values

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    assert len(migrator.result.observations) == 18
    assert not migrator.result.validation.hard_issues
    migrated_context = {
        row["observation_id"]: row
        for rows in migrator.result.context_by_work.values()
        for row in rows
        if row["observation_id"] in expected
    }
    assert set(migrated_context) == set(expected)
    for observation_id, values in expected.items():
        row = migrated_context[observation_id]
        assert row["type"] == "bulk_charge_analysis_context"
        assert {key: row["values"][key] for key in values} == values


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
