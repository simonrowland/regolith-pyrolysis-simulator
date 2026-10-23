"""Migration pins for Wetzel et al. (2012), Tables 2 and 3."""

from pathlib import Path
from decimal import Decimal

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/wetzel-pucci-gail-2012-sio-evaporation-coefficient.yaml"
)


def test_wetzel_2012_table2_pins_and_all_rows_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    table2 = {
        observation.observation_id.rsplit("::", 1)[-1]: observation
        for observation in observations
        if "wetzel_2012_table2_r" in observation.observation_id
    }
    assert len(table2) == 30
    assert sum(observation.value.kind.value == "point" for observation in table2.values()) == 30
    assert not migrator.result.validation.hard_issues

    # PDF p. 7 render: first, middle, and final table rows.
    expected = {
        "wetzel_2012_table2_r01": ("0.003", "0.002", "1211.00", 1, "left"),
        "wetzel_2012_table2_r15": ("1.0", "0.3", "1421.00", 5, "center"),
        "wetzel_2012_table2_r30": ("24", "5", "1565.00", 10, "right"),
    }
    for observation_id, (pressure, uncertainty, temperature, row, group) in expected.items():
        observation = table2[observation_id]
        assert observation.value.point == Decimal(pressure)
        assert observation.uncertainty.verbatim == (
            f"±{uncertainty} Pa; confidence level not stated"
        )
        assert observation.locator.page == 7
        assert observation.locator.table == "2"
        condition = observation.point_conditions["temperature_K"]
        assert condition.state.value == Decimal(temperature)
        assert condition.locator.page == 7
        assert condition.locator.table == "2"
        assert f"row={row}" in condition.locator.note
        assert f"{group} T/p pair" in condition.locator.note
        assert observation.identity.quantity.value.value == "p_sat"
        assert observation.identity.temperature_K.value == Decimal(temperature)

    assert all("temperature_K" in observation.point_conditions for observation in table2.values())
    unavailable = sum(
        observation.value.kind.value == "unavailable" for observation in observations
    )
    assert unavailable == 6

    enthalpy = next(
        observation
        for observation in observations
        if observation.observation_id.endswith(
            "::wetzel_2012_table3_this_work_second_law_enthalpy"
        )
    )
    assert len(enthalpy.derived_from) == 30
