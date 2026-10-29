"""Migration pins for Shornikov (2014), Ca–Al inclusion coefficients."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2014-ca-al-evaporation-coefficients.yaml"


def test_shornikov_2014_liquid_coefficients_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = {
        observation.observation_id.split("::")[1]: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == 12
    cao = observations["shornikov_2014_cao_liquid_alpha_estimate"]
    mgo = observations["shornikov_2014_mgo_liquid_alpha_estimate"]

    # The one-page abstract states both liquid-state estimates as alpha_v = 1.
    assert cao.identity.quantity.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert cao.value.point == Decimal("1.0")
    assert mgo.value.point == Decimal("1.0")
    assert cao.locator.paragraph == mgo.locator.paragraph == "Liquid-state estimate"
    assert not migrator.result.validation.hard_issues
