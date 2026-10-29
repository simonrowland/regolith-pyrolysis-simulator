"""Migration pins for Shornikov (2014), Table 2 melting temperatures."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2014-sodium-silicate-model.yaml"


def test_shornikov_table_2_printed_temperatures_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = {
        observation.observation_id.split("::")[1]: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == 17
    na2o = observations["shornikov_2014_table2_melting_na2o_01"]
    na4sio4 = observations["shornikov_2014_table2_melting_na4sio4_04"]

    # Table 2 (printed p. 49) gives 1405 K for Na2O and 1393 K for Na4SiO4.
    assert na2o.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    assert na2o.value.point == Decimal("1405.0")
    assert na4sio4.value.point == Decimal("1393.0")
    assert na2o.locator.published_page == na4sio4.locator.published_page == 49
    assert na2o.locator.table == na4sio4.locator.table == "2"
    assert not migrator.result.validation.hard_issues
