"""Migration pins for Shornikov (2018), Tables 1 and 3."""

from decimal import Decimal
from pathlib import Path

from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2018-spinel-solid-solutions.yaml"


def test_shornikov_2018_table_values_migrate_with_page_coordinates() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 168
    mg_pressure = next(
        observation
        for observation in observations
        if "::shornikov_2018_t1_p_mg::" in observation.observation_id
        and "x_Al2O3=50.00" in observation.locator.note
    )
    mgo_activity = next(
        observation
        for observation in observations
        if "::shornikov_2018_t3_amgo_eq27::" in observation.observation_id
        and "x_Al2O3=50.00" in observation.locator.note
    )

    # Table 1 (p. 1452): 2.12e-5 atm; migrated pressure is Pa.
    assert mg_pressure.value.point == Decimal("2.1480900")
    assert mg_pressure.locator.table == "1"
    assert mg_pressure.locator.published_page == 1452
    # Table 3 (p. 1454): a_MgO by Eq. 27 is 3.50e-1 at x_Al2O3=50.00.
    assert mgo_activity.value.point == Decimal("0.35")
    assert mgo_activity.locator.table == "3"
    assert mgo_activity.locator.published_page == 1454
    assert not migrator.result.validation.hard_issues
