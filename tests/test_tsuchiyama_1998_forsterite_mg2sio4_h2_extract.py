"""Migration pins for the printed Tsuchiyama et al. Table 1 values."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    ROOT
    / "data/literature/extracts/tsuchiyama-1998-forsterite-mg2sio4-h2.yaml"
)


def test_tsuchiyama_table_1_values_migrate_from_printed_page() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = {
        observation.locator.note: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == 18
    fo_h1 = observations["Table 1 row for run FoH-1."]
    fo_h14 = observations["Table 1 row for run FoH-14."]

    # Table 1 (printed p. 116) reports 0.00174 and 0.00409 g/cm²;
    # the migrated unit is kg/m², a factor of ten larger.
    assert fo_h1.identity.quantity.value is Quantity.MASS_LOSS_AREAL_DENSITY
    assert fo_h1.value.point == Decimal("0.01740")
    assert fo_h1.locator.published_page == 116
    assert fo_h1.point_conditions["temperature_K"].state.value == Decimal("1473.15")
    assert fo_h14.value.point == Decimal("0.04090")
    assert fo_h14.locator.published_page == 116
    assert not migrator.result.validation.hard_issues
