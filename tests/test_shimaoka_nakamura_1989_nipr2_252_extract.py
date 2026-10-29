"""Migration pins for Shimaoka and Nakamura (1989), Table 2."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shimaoka-nakamura-1989-nipr2-252.yaml"


def test_shimaoka_table_2_residue_values_migrate() -> None:
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()

    observations = {
        observation.observation_id.split("::")[1]: observation
        for observation in migrator.result.observations.values()
    }
    assert len(observations) == 12
    run_37a = observations["shimaoka_1989_table2_run_37_a"]
    run_35a = observations["shimaoka_1989_table2_run_35_a"]

    # Table 2 (printed p. 258) reports 450 and 129 ppm Na for these runs.
    assert run_37a.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    assert run_37a.value.point == Decimal("450.0")
    assert run_35a.value.point == Decimal("129.0")
    assert run_37a.locator.published_page == run_35a.locator.published_page == 258
    assert run_37a.locator.table == run_35a.locator.table == "2"
    assert not migrator.result.validation.hard_issues
