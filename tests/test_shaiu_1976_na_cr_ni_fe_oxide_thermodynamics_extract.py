from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import yaml

from simulator.battery.enums import ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shaiu-1976-na-cr-ni-fe-oxide-thermodynamics.yaml"
SOURCE_ID = "shaiu-1976-na-cr-ni-fe-oxide-thermodynamics"

RENDERED_TABLE_8 = {
    "Na2FeO2": "-183060 + 51.34T",
    "NaFeO2": "-173410 + 43.66T",
    "Na4FeO3": "-289180 + 82.69T",
}


def _document() -> dict[str, object]:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_fe_table_rows_and_rendered_cells_detect_mutation() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SOURCE_ID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=EXTRACT.stem) == []

    table7_rows = [
        row for species in document["species"].values()
        for row in species["observations"]
        if row["locator"].get("table") == "7"
    ]
    table8_rows = [
        row for species in document["species"].values()
        for row in species["observations"]
        if row["locator"].get("table") == "8"
    ]
    assert len(table7_rows) == 2
    assert len(table8_rows) == 3

    stored = {
        species: next(
            row for row in observations["observations"]
            if row["observation_id"] == f"shaiu_1976_table8_{species.lower()}"
        )["values"]["equation_as_printed"]
        for species, observations in document["species"].items()
        if species in RENDERED_TABLE_8
    }
    assert stored == RENDERED_TABLE_8

    na2feo2_rows = next(
        row for row in document["species"]["Na2FeO2"]["observations"]
        if row["observation_id"] == "shaiu_1976_table_a4_cell_v"
    )["values"]["points"]
    nafeo2_rows = next(
        row for row in document["species"]["NaFeO2"]["observations"]
        if row["observation_id"] == "shaiu_1976_table_a4_cell_vi"
    )["values"]["points"]
    assert len(na2feo2_rows) == 6
    assert len(nafeo2_rows) == 20
    assert (nafeo2_rows[10]["T_K"], nafeo2_rows[10]["value"]) == (948, 1.2743)
    assert (nafeo2_rows[11]["T_K"], nafeo2_rows[11]["value"]) == (970, 1.2754)
    assert (nafeo2_rows[-1]["T_K"], nafeo2_rows[-1]["value"]) == (1048, 1.2743)

    mutated = deepcopy(document)
    mutated["species"]["NaFeO2"]["observations"][0]["values"][
        "equation_as_printed"
    ] = "-173411 + 43.66T"
    assert check_all_fidelity_samples_match(mutated, label="mutated Table 8")


def test_isolated_migration_keeps_known_schema_gaps_and_no_invented_conditions() -> None:
    document = _document()
    source_rows = [
        row
        for species in document["species"].values()
        for row in species["observations"]
    ]
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert result.registry_issues == []
    assert len(result.observations) == 33
    assert len(source_rows) == 9
    assert document["species"]["NaFeO2"]["observations"][0]["locator"] == {
        "page": 47,
        "table": "8",
        "row": "NaFeO2",
    }
    assert document["species"]["NaFeO2"]["observations"][2]["locator"] == {
        "page": 76,
        "table": "A4",
        "table_caption_page": 74,
        "table_continued": True,
        "row": "Cell VI",
    }

    migrated = result.observations
    unknown_quantity_ids = {
        observation_id.split("::", 1)[1].split("::", 1)[0]
        for observation_id, row in migrated.items()
        if row.identity.quantity is None or row.identity.quantity.is_unknown
    }
    assert unknown_quantity_ids == {
        "shaiu_1976_table7_cell_v_emf_fit",
        "shaiu_1976_table7_cell_vi_emf_fit",
        "shaiu_1976_table_a4_cell_v",
        "shaiu_1976_table_a4_cell_vi",
        "shaiu_1976_table9_na4feo3_critical_oxygen",
        "shaiu_1976_eq62_gross_wilson_enthalpy",
    }
    assert sum(
        row.identity.quantity is None or row.identity.quantity.is_unknown
        for row in migrated.values()
    ) == 30
    assert all(row.identity.species.phase is not None for row in migrated.values())
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 33

    emf_points = [
        row for observation_id, row in migrated.items()
        if "shaiu_1976_table_a4_cell_" in observation_id
    ]
    assert len(emf_points) == 26
    assert all(row.identity.temperature_K.is_value for row in emf_points)
    assert all(
        (row.identity.total_pressure_Pa is None or not row.identity.total_pressure_Pa.is_value)
        and (row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value)
        for row in emf_points
    )

    for observation_id in (
        "shaiu_1976_table8_na2feo2",
        "shaiu_1976_table8_nafeo2",
        "shaiu_1976_table8_na4feo3",
    ):
        row = migrated[f"{SOURCE_ID}::{observation_id}"]
        assert row.identity.temperature_K.is_unknown
        assert not row.identity.total_pressure_Pa.is_value
        assert not row.identity.fO2_Pa.is_value
        assert not row.identity.composition.is_value
        assert row.point_conditions is None

    assert result.validation.hard_issues == ()
