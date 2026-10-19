"""Migration pins for Lu et al. (2024), Table 1."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/lu-2024-mgsio3-levitation.yaml"


def test_table_1_m01_residue_values_and_conditions_migrate() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 27
    m01 = [o for o in observations if o.locator.note == "row=M01"]
    assert len(m01) == 3
    assert {o.value.point for o in m01} == {
        Decimal("53.62"), Decimal("44.44"), Decimal("1.64")
    }
    assert all(o.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION for o in m01)
    assert all(o.identity.temperature_K.value == Decimal("2623") for o in m01)
    assert all(o.point_conditions["duration_s"].state.value == Decimal("120") for o in m01)
    assert all(o.locator.published_page == 1195 and o.locator.table == "1" for o in m01)
    assert not migrator.result.validation.hard_issues
