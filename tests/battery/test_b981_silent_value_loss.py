from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[2]


def _observations(source: str, observation_token: str):
    migrated = migrate_one_extract(
        ROOT, ROOT / "data/literature/extracts" / source
    )
    return [
        observation
        for observation_id, observation in migrated.result.observations.items()
        if observation_token in observation_id
    ]


def test_kambayashi_table7_activity_rows_migrate_with_temperature_and_composition():
    observations = _observations(
        "kems-057-kambayashi-1985.yaml",
        "kambayashi_1985_table7_raoultian_a_p2o5",
    )
    assert len(observations) == 22
    assert all(
        observation.identity.quantity.value.value is Quantity.ACTIVITY
        and observation.value.kind is ValueKind.POINT
        for observation in observations
    )
    assert sorted(observation.value.point for observation in observations) == sorted(
        Decimal(value)
        for value in (
            "1.2000000000000001e-17",
            "1.4e-17",
            "1.5e-17",
            "3.8e-17",
            "4.5000000000000004e-17",
            "5.400000000000001e-17",
            "7.200000000000001e-17",
            "8.7e-17",
            "9.2e-17",
            "1.0000000000000001e-16",
            "1.14e-16",
            "1.4e-16",
            "1.2400000000000002e-16",
            "1.4800000000000001e-16",
            "1.8100000000000003e-16",
            "2.0800000000000003e-16",
            "1.56e-16",
            "1.8000000000000002e-16",
            "2.0000000000000002e-16",
            "2.6300000000000003e-16",
            "3.2299999999999997e-16",
            "4.76e-16",
        )
    )
    for observation in observations:
        conditions = observation.point_conditions or {}
        for key in ("composition", "temperature_K"):
            assert key in conditions
            assert conditions[key].state.is_value
            assert conditions[key].locator is not None
