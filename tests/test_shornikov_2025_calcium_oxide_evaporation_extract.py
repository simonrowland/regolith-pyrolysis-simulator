"""Migration pins for Shornikov (2025), Tables 1 and 2."""

from decimal import Decimal
from pathlib import Path

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2025-calcium-oxide-evaporation.yaml"


def test_shornikov_2025_table_pressures_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 210
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

    # Tables 1 and 2 (printed pp. 224–225) give pressures in atm; migrated Pa.
    assert ca.value.point == Decimal("0.0066773175")
    assert oxygen.value.point == Decimal("0.000047926725")
    assert ca.locator.published_page == 224 and ca.locator.table == "1"
    assert oxygen.locator.published_page == 225 and oxygen.locator.table == "2"
    assert not migrator.result.validation.hard_issues
