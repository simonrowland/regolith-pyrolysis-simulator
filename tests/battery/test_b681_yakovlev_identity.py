from decimal import Decimal

from simulator.battery.enums import (
    Phase,
    Quantity,
)
from simulator.battery.migrate import Migrator, REPO_ROOT


def test_yakovlev_murchison_alkali_partial_pressures_have_gas_phase() -> None:
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(
        REPO_ROOT / "data/literature/extracts/kems-028-yakovlev-1984.yaml"
    )
    migrator.finalize()
    result = migrator.result
    expected_pressures = {
        "kems-028-yakovlev-1984::yakovlev_1984_murchison_Na_P_1225C": Decimal(
            "0.03599694"
        ),
        "kems-028-yakovlev-1984::yakovlev_1984_murchison_K_P_1225C": Decimal(
            "0.005599524"
        ),
    }

    for observation_id, expected_pressure in expected_pressures.items():
        observation = result.observations[observation_id]
        identity = observation.identity
        assert identity.quantity.value is Quantity.P_PARTIAL
        assert identity.species.phase.is_value
        assert identity.species.phase.value is Phase.G
        assert observation.value.point == expected_pressure
