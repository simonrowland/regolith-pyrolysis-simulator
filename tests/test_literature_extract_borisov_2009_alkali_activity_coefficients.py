from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/borisov-2009-alkali-activity-coefficients.yaml"
)


def _observations(document):
    return [
        row
        for row in document["species"]["Na2O"]["observations"]
        if row["type"] == "activity_coefficient"
        and row["locator"].get("table") in {"4", "5"}
    ]


def _assert_render_pins(document):
    rows = _observations(document)
    assert len(rows) == 15  # Table 4: 12; Table 5: 3 Na2O coefficients.
    table4_reference = next(
        row for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "borisov_2009_table4_reference_melts"
    )
    table5_reference = next(
        row for row in document["species"]["Na2O"]["context"]
        if row["observation_id"] == "borisov_2009_table5_csa6_reference_melt"
    )
    assert table4_reference["locator"]["table"] == "4"
    assert [
        (row["T_C"], row["gamma_Na2O"])
        for row in table4_reference["values"]["reference_rows"]
    ] == [("1300", "1.000"), ("1400", "1.000"), ("1470", "1.000")]
    assert table5_reference["locator"]["table"] == "5"
    assert table5_reference["values"]["sample"] == "CSA6"
    assert table5_reference["values"]["gamma_Na2O"] == "1.000"
    assert len(rows) + len(table4_reference["values"]["reference_rows"]) + 1 == 19
    pins = {
        "borisov_2009_table4_darnk_36_na2o_activity_coefficient": (
            "0.868",
            "4",
            "6",
            "0.477",
            "X_SiO2_as_printed",
        ),
        "borisov_2009_table4_das200rnk_39_na2o_activity_coefficient": (
            "0.580",
            "4",
            "6",
            "0.747",
            "X_SiO2_as_printed",
        ),
        "borisov_2009_table5_csa25_na2o_activity_coefficient": (
            "0.881",
            "5",
            "8",
            "0.164",
            "X_Al2O3_as_printed",
        ),
    }
    by_id = {row["observation_id"]: row for row in rows}
    for obs_id, (gamma, table, page, composition_value, composition_key) in pins.items():
        row = by_id[obs_id]
        assert row["values"]["gamma"] == gamma
        assert row["locator"]["table"] == table
        assert row["locator"]["page"] == int(page)
        assert row["values"]["sample_as_printed"] in {"DARNK-36", "DAS200RNK-39", "CSA25"}
        assert row["values"][composition_key] == composition_value


def test_borisov_2009_render_pins_tables_4_5_and_row_count():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    _assert_render_pins(document)


def test_borisov_2009_render_pin_rejects_mutated_gamma():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    row = next(
        row
        for row in _observations(mutated)
        if row["observation_id"]
        == "borisov_2009_table4_darnk_36_na2o_activity_coefficient"
    )
    row["values"]["gamma"] = "0.869"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_borisov_2009_isolated_migration_preserves_conditions_and_relative_reference():
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 169
    assert result.validation.hard_issues == ()
    target_ids = {
        row["observation_id"]
        for row in _observations(yaml.safe_load(EXTRACT.read_text(encoding="utf-8")))
    }
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
        if row.observation_id.rsplit("::", 1)[-1] in target_ids
    }
    assert len(migrated) == 15
    for row in migrated.values():
        assert row.identity.quantity.value is Quantity.ACTIVITY_COEFFICIENT
        assert row.identity.temperature_K.is_value
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.is_value
        assert row.identity.reference_state.is_unknown
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.point_conditions["total_pressure_Pa"].state.is_value
        assert row.point_conditions["total_pressure_Pa"].locator is not None
        assert row.point_conditions["minus_log_fO2_as_published"].state.is_value
        assert row.point_conditions["minus_log_fO2_as_published"].locator is not None
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["composition"].locator is not None

    first = migrated["borisov_2009_table4_darnk_36_na2o_activity_coefficient"]
    assert (
        first.point_conditions["minus_log_fO2_as_published"].state.value
        == Decimal("10.74")
    )
