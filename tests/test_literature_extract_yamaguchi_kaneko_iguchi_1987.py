from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import EvidenceClass, Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import validate_extract_file


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/yamaguchi-kaneko-iguchi-1987-na2o-fe2o3-activity.yaml"


RENDER_PINS = {
    "yki1987_activity_f_n3f5": ("-0.0426", "3", "A"),
    "yki1987_activity_n3f5_nf_mid": ("-0.1100", "3", "A"),
    "yki1987_gibbs_table4_na2fe2o4_mid": ("-162.7", "4", "B"),
    "yki1987_gibbs_abstract_na2fe2o4_1047_1278": ("-152.7", None, None),
}


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }


def _assert_render_pins(document: dict) -> None:
    observations = _observations(document)
    assert len(observations) == 12
    assert {
        species: len(rows.get("observations", []))
        for species, rows in document["species"].items()
    } == {"Na2O": 4, "Na3Fe5O9": 2, "NaFeO2": 6}
    table2 = next(
        row for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "yki1987_table2_emf_fits"
    )
    assert len(table2["values"]["printed_rows"]) == 8
    assert table2["locator"]["page"] == 4
    assert table2["locator"]["table"] == "2"
    assert table2["values"]["printed_rows"][0]["A_mV"] == "-747.8"
    assert table2["values"]["printed_rows"][4]["B_mV_per_K"] == "-0.006"
    table3 = [row for row in observations.values() if row["locator"].get("table") == "3"]
    table4 = [row for row in observations.values() if row["locator"].get("table") == "4"]
    assert len(table3) == 4
    assert len(table4) == 4

    for observation_id, (value, table, column) in RENDER_PINS.items():
        row = observations[observation_id]
        values = row["values"]
        if observation_id == "yki1987_activity_f_n3f5":
            actual = values["fit_coefficients_as_printed"]["A"]
        elif observation_id == "yki1987_activity_n3f5_nf_mid":
            actual = values["fit_coefficients_as_printed"]["A"]
        elif observation_id == "yki1987_gibbs_table4_na2fe2o4_mid":
            actual = values["coefficients_as_printed"]["B_kJ_per_mol"]
        else:
            actual = values["coefficients_as_printed"]["B_kJ_per_mol"]
        assert actual == value
        assert row["locator"]["page"] == (1 if table is None else 6)
        if table is not None:
            assert row["locator"]["table"] == table


def test_yamaguchi_1987_fit_cells_are_pinned_to_render_and_row_counts() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    _assert_render_pins(document)

    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in migrator.result.observations.values()
    }
    for observation_id, table in (
        ("yki1987_activity_f_n3f5", "3"),
        ("yki1987_activity_n3f5_nf_mid", "3"),
        ("yki1987_gibbs_table4_na2fe2o4_mid", "4"),
    ):
        assert migrated[observation_id].locator.page == 6
        assert migrated[observation_id].locator.table == table
    assert migrated["yki1987_gibbs_abstract_na2fe2o4_1047_1278"].locator.page == 1

    mutated = deepcopy(document)
    table4_mid = next(
        row for row in mutated["species"]["NaFeO2"]["observations"]
        if row["observation_id"] == "yki1987_gibbs_table4_na2fe2o4_mid"
    )
    table4_mid["values"]["coefficients_as_printed"]["B_kJ_per_mol"] = "-152.7"
    try:
        _assert_render_pins(mutated)
    except AssertionError:
        pass
    else:
        raise AssertionError("a changed render-pinned Table 4 cell did not fail")


def test_yamaguchi_1987_isolated_migration_keeps_fits_typed_and_conditions_uninvented() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_file(EXTRACT) == []

    migrator = migrate_one_extract(ROOT, EXTRACT)
    migrator.finalize()
    observations = list(migrator.result.observations.values())
    assert migrator.result.validation is not None
    assert not migrator.result.validation.hard_issues
    assert len(observations) == 12
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in observations) == 12
    assert sum(row.identity.quantity.is_value and row.identity.quantity.value is Quantity.ACTIVITY for row in observations) == 4
    assert sum(row.identity.quantity.is_value and row.identity.quantity.value is Quantity.DELTA_FG for row in observations) == 8
    assert all(row.identity.species.phase.is_value for row in observations)
    assert all(row.identity.quantity.is_value for row in observations)
    assert all(
        row.evidence.class_.is_value
        and row.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        for row in observations
    )
    assert all(
        not row.point_conditions
        or ("total_pressure_Pa" not in row.point_conditions and "fO2_Pa" not in row.point_conditions)
        for row in observations
    )
    assert all(
        row.point_conditions
        and row.point_conditions["temperature_K"].state.is_value
        and row.point_conditions["temperature_K"].locator.page == row.locator.page
        for row in observations
    )
    assert all(not row.identity.total_pressure_Pa.is_value for row in observations)
    assert all(row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value for row in observations)
    for observation_id in (
        "yki1987_activity_f_n3f5",
        "yki1987_gibbs_table4_na3fe5o9",
        "yki1987_gibbs_table4_na2fe2o4_mid",
    ):
        row = next(
            row for row in observations
            if row.observation_id.rsplit("::", 1)[-1] == observation_id
        )
        assert not row.identity.composition.is_value
        assert not row.identity.total_pressure_Pa.is_value
        assert row.identity.fO2_Pa is None or not row.identity.fO2_Pa.is_value
        assert not row.identity.temperature_K.is_value
    activity_range = next(
        row for row in observations
        if row.observation_id.endswith("::yki1987_activity_f_n3f5")
    ).point_conditions["temperature_K"].state.value
    assert activity_range.bound_operator == "<"
    assert activity_range.bound_value == Decimal("1405")
    gibbs_interval = next(
        row for row in observations
        if row.observation_id.endswith("::yki1987_gibbs_table4_na2fe2o4_mid")
    ).point_conditions["temperature_K"].state.value
    assert gibbs_interval.interval_low == Decimal("1047")
    assert gibbs_interval.interval_high == Decimal("1278")

    activity = next(row for row in observations if row.identity.quantity.value is Quantity.ACTIVITY)
    assert activity.identity.temperature_K.is_unknown
