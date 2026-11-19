from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from simulator.battery.records import State
from simulator.battery.score import _temperature_envelope_value


def test_celsius_envelope_key_matches_its_kelvin_temperature() -> None:
    uncertainty = SimpleNamespace(
        verbatim={"log10_a_envelope": {"0_C": "0.125"}}
    )
    observation = SimpleNamespace(
        identity=SimpleNamespace(temperature_K=State.of(Decimal("273.15")))
    )

    assert _temperature_envelope_value(uncertainty, observation) == (
        Decimal("0.125"),
        None,
        True,
    )
