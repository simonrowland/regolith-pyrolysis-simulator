from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import Quantity, RefusalReason
from simulator.battery.records import State
from simulator.battery.score import _fe_redox_pressure, total_pressure_bar_for_score
from tests.battery import factories as F


def test_unknown_score_pressure_default_numeric_pin_before_policy_change() -> None:
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.unknown("not printed"),
    )

    pressure_bar, notice, invalid = total_pressure_bar_for_score(
        identity, Quantity.ACTIVITY
    )

    assert pressure_bar.hex() == "0x1.0c6f7a0b5ed8dp-20"
    assert notice is not None
    assert invalid is None


def test_redox_pressure_numeric_pin_and_missing_pressure_refusal_before_move() -> None:
    identity = replace(
        F.pref_identity(),
        total_pressure_Pa=State.of(Decimal("25000")),
    )
    observation = F.observation(
        "pressure-pin",
        "pressure-pin-experiment",
        identity,
        Decimal("1"),
        source_id="pressure-pin-source",
    )

    pressure = _fe_redox_pressure(observation, identity)

    assert pressure == (0.25, identity.total_pressure_Pa)
    assert pressure[0].hex() == "0x1.0000000000000p-2"

    missing_identity = replace(
        identity,
        total_pressure_Pa=State.unknown("not printed"),
    )
    missing_observation = replace(observation, identity=missing_identity)

    missing = _fe_redox_pressure(missing_observation, missing_identity)

    assert missing.reason is RefusalReason.IDENTITY_INCOMPLETE
    assert missing.detail == {
        "reason": "missing_input",
        "input": "total_pressure_Pa",
    }
