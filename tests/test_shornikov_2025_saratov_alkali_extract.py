"""Migration pins for the Saratov alkali evaporation report."""

from decimal import Decimal
from pathlib import Path

from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/shornikov-2025-saratov-alkali-evaporation.yaml"


def test_printed_pressure_ranges_migrate_without_inventing_temperature() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()

    observations = tuple(migrator.result.observations.values())
    assert len(observations) == 2
    assert not migrator.result.validation.hard_issues
    for observation in observations:
        assert observation.value.interval_low == Decimal("1.01325e-3")
        assert observation.value.interval_high == Decimal("1.01325e-2")
        assert observation.identity.temperature_K.reason == (
            "source T_range_K [1450.0, 1500.0]; no midpoint invented"
        )
    assert [entry.why for entry in migrator.result.queue] == [
        "source T_range_K [1450.0, 1500.0]; no midpoint invented",
        "source T_range_K [1450.0, 1500.0]; no midpoint invented",
    ]
