from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.migrate import Migrator


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/borisov-2009-alkali-activity-coefficients.yaml"
)


def _observations(document):
    return document["species"]["Na2O"]["observations"]


def _assert_render_pins(document):
    rows = _observations(document)
    assert len(rows) == 19  # Table 4: 15; Table 5: 4.
    pins = {
        "borisov_2009_table_4_darnk_36_na2o_relative_gamma": (
            "0.868",
            "4",
            "6",
            "0.477",
        ),
        "borisov_2009_table_4_das200rnk_39_na2o_relative_gamma": (
            "0.580",
            "4",
            "6",
            "0.747",
        ),
        "borisov_2009_table_5_csa25_na2o_relative_gamma": (
            "0.881",
            "5",
            "8",
            "0.164",
        ),
    }
    by_id = {row["observation_id"]: row for row in rows}
    for obs_id, (gamma, table, page, composition_value) in pins.items():
        row = by_id[obs_id]
        assert row["values"]["relative_gamma_Na2O_as_published"] == gamma
        assert row["locator"]["table"] == table
        assert row["locator"]["page"] == int(page)
        assert row["point_conditions"]["composition"]["state"]["value"]["components"]
        components = dict(
            row["point_conditions"]["composition"]["state"]["value"]["components"]
        )
        assert components["SiO2" if table == "4" else "Al2O3"] == composition_value


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
        == "borisov_2009_table_4_darnk_36_na2o_relative_gamma"
    )
    row["values"]["relative_gamma_Na2O_as_published"] = "0.869"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_borisov_2009_isolated_migration_preserves_conditions_and_refuses_absolute_activity():
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    result = migrator.result
    assert len(result.observations) == 19
    assert result.validation.hard_issues == ()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    for row in migrated.values():
        assert row.value.kind.value == "unavailable"
        assert row.admission.status.value == "rejected"
        assert row.admission.reason == (
            "relative to the printed reference melt; absolute activity not printed"
        )
        assert row.identity.quantity.value.value == "activity"
        assert row.identity.temperature_K.is_value
        assert row.identity.composition.is_value
        assert row.identity.total_pressure_Pa.is_value
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator is not None
        assert row.point_conditions["total_pressure_Pa"].state.is_value
        assert row.point_conditions["total_pressure_Pa"].locator is not None
        assert row.point_conditions["minus_log_fO2_as_published"].state.is_value
        assert row.point_conditions["minus_log_fO2_as_published"].locator is not None
        assert row.point_conditions["composition"].state.is_value
        assert row.point_conditions["composition"].locator is not None

    # The current migrator deliberately leaves fO2 identity unknown on refused
    # activity observations; the printed log fugacity remains on each row.
    first = migrated["borisov_2009_table_4_darnk_36_na2o_relative_gamma"]
    assert first.identity.fO2_Pa.is_unknown
    assert (
        first.point_conditions["minus_log_fO2_as_published"].state.value
        == Decimal("10.74")
    )
