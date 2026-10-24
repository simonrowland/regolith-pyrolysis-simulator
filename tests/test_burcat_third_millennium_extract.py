from decimal import Decimal

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import REPO_ROOT
from tests.battery.factories import migrate_one_extract


def test_burcat_formation_enthalpies_migrate_without_choosing_competing_methods() -> None:
    migrator = migrate_one_extract(
        REPO_ROOT,
        REPO_ROOT / "data/literature/extracts/burcat-third-millennium.yaml",
    )
    observations = {
        observation.observation_id.split("::", 1)[1]: observation
        for observation in migrator.result.observations.values()
    }

    assert len(observations) == 37
    assert all(
        observation.identity.quantity.is_value
        and observation.identity.quantity.value is Quantity.DELTA_FH
        for observation in observations.values()
    )

    numeric = [
        observation
        for observation in observations.values()
        if observation.value.kind is ValueKind.POINT
    ]
    assert len(numeric) == 32
    assert observations["burcat_2005_table1_C_gas"].value.point == Decimal("717.065")
    assert observations["burcat_2005_table1_O_gas"].value.point == Decimal("249.229")
    assert observations["burcat_2005_table3_OH_radical"].value.point == Decimal("37.34")

    for observation in observations.values():
        if "table2_" in observation.observation_id:
            assert observation.value.kind is ValueKind.UNAVAILABLE
            assert "no mapped delta_fH field" in observation.value.unavailable_reason
            assert observation.locator.table == "2"
            assert observation.point_conditions is None
