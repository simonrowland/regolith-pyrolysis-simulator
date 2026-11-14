"""Migration pins for Shornikov (2025), Tables 1 and 2."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2025-calcium-oxide-evaporation.yaml"


def test_shornikov_2025_table_pressures_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    ca = next(
        observation
        for observation in observations
        if "::shornikov_2025_table1_ca_partial_pressure::T=1812:" in observation.observation_id
    )
    oxygen = next(
        observation
        for observation in observations
        if "::shornikov_2025_table2_o_eq19::T=1812:" in observation.observation_id
    )
    molybdenum_oxide = next(
        observation
        for observation in observations
        if (
            "::shornikov_2025_table1_moo3_partial_pressure::T=1812:"
            in observation.observation_id
        )
    )
    table2_atomic_oxygen = next(
        observation
        for observation in observations
        if (
            "::shornikov_2025_table2_o_eq11::T=1812:"
            in observation.observation_id
        )
    )

    # Tables 1 and 2 (printed pp. 224–225) give pressures in atm; migrated Pa.
    assert ca.value.point == Decimal("0.0066773175")
    assert oxygen.value.point == Decimal("0.000047926725")
    assert ca.locator.published_page == 224 and ca.locator.table == "1"
    assert oxygen.locator.published_page == 225 and oxygen.locator.table == "2"
    assert all(
        observation.experiment_id == ca.experiment_id
        for observation in (ca, oxygen, molybdenum_oxide, table2_atomic_oxygen)
    )

    experiment = migrator.result.experiments[ca.experiment_id]
    assert experiment.apparatus is not None
    assert experiment.apparatus.geometry is not None
    diameter = experiment.apparatus.geometry.orifice_diameter_m
    assert diameter is not None and diameter.state.is_value
    assert diameter.state.value.kind is ValueKind.INTERVAL
    assert diameter.state.value.interval_low == Decimal("0.00025")
    assert diameter.state.value.interval_high == Decimal("0.00030")
    assert diameter.locator.published_page == 221
    assert diameter.locator.section == "Experimental"

    for observation in observations:
        assert observation.identity.temperature_K.is_value
        temperature = observation.point_conditions.get("temperature_K")
        assert temperature is not None and temperature.state.is_value
        assert temperature.locator.published_page is not None
    assert not migrator.result.validation.hard_issues
