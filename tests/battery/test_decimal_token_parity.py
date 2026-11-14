from decimal import Decimal

import pytest

from simulator.battery.compilation_tier import _decimal_token
from simulator.battery.composition import _amount_token
from simulator.battery.migrate import _dec_str
from simulator.battery.score import _dec_token
from simulator.battery.waypoints import _printed_wt_token


_CALL_SITES = {
    "migrate": lambda value: _dec_str(value),
    "score": lambda value: _dec_token(value),
    "compilation_tier": lambda value: _decimal_token(value),
    "waypoints": lambda value: _printed_wt_token(None, value),
    "composition": lambda value: _amount_token(value),
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0"), {"migrate": "0", "score": "0", "compilation_tier": "0", "waypoints": "0", "composition": "0"}),
        (Decimal("-0"), {"migrate": "-0", "score": "-0", "compilation_tier": "0", "waypoints": "-0", "composition": "-0"}),
        (Decimal("-0.0"), {"migrate": "-0", "score": "-0", "compilation_tier": "0", "waypoints": "-0", "composition": "-0"}),
        (Decimal("12.3400"), {"migrate": "12.34", "score": "12.34", "compilation_tier": "12.34", "waypoints": "12.34", "composition": "12.34"}),
        (Decimal("1.2E+3"), {"migrate": "1200", "score": "1200", "compilation_tier": "1200", "waypoints": "1200", "composition": "1200"}),
        (Decimal("1E-7"), {"migrate": "0.0000001", "score": "0.0000001", "compilation_tier": "0.0000001", "waypoints": "0.0000001", "composition": "0.0000001"}),
        (Decimal("1E+100"), {"migrate": "1" + "0" * 100, "score": "1" + "0" * 100, "compilation_tier": "1" + "0" * 100, "waypoints": "1" + "0" * 100, "composition": "1" + "0" * 100}),
        (Decimal("1E-100"), {"migrate": "0." + "0" * 99 + "1", "score": "0." + "0" * 99 + "1", "compilation_tier": "0." + "0" * 99 + "1", "waypoints": "0." + "0" * 99 + "1", "composition": "0." + "0" * 99 + "1"}),
        (Decimal("NaN"), {"migrate": "NaN", "score": "NaN", "compilation_tier": "NaN", "waypoints": "NaN", "composition": "NaN"}),
        (Decimal("Infinity"), {"migrate": "Infinity", "score": "Infinity", "compilation_tier": "Infinity", "waypoints": "Infinity", "composition": "Infinity"}),
        (Decimal("-Infinity"), {"migrate": "-Infinity", "score": "-Infinity", "compilation_tier": "-Infinity", "waypoints": "-Infinity", "composition": "-Infinity"}),
    ],
)
def test_decimal_token_call_site_outputs_are_pinned(value, expected) -> None:
    assert {name: formatter(value) for name, formatter in _CALL_SITES.items()} == expected
