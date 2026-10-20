from __future__ import annotations

from engines.alphamelts.parser import _is_finite as alphamelts_parser_is_finite
from engines.alphamelts.provider import _is_finite as alphamelts_provider_is_finite
from engines.builtin.melt_effect_adjustment import (
    _finite_float as melt_effect_finite_float,
)
from engines.magemin.domain import _is_finite as magemin_domain_is_finite
from engines.magemin.provider import _is_finite as magemin_provider_is_finite
from engines.vaporock.provider import _is_finite as vaporock_provider_is_finite
from scripts.classify_epoch2_liquid_fraction_failures import (
    _finite_float as classify_epoch2_finite_float,
)
from scripts.grid_pregrind_triage import _finite_float as grid_pregrind_finite_float
from simulator.diagnostic_helpers.binary_pot_battery import (
    _finite_float as binary_pot_finite_float,
)
from simulator.diagnostics import _finite_float as diagnostics_finite_float

IS_FINITE_HELPERS = (
    ("engines/magemin/provider.py", magemin_provider_is_finite),
    ("engines/vaporock/provider.py", vaporock_provider_is_finite),
    ("engines/alphamelts/provider.py", alphamelts_provider_is_finite),
    ("engines/alphamelts/parser.py", alphamelts_parser_is_finite),
    ("engines/magemin/domain.py", magemin_domain_is_finite),
)

FINITE_FLOAT_HELPERS = (
    ("simulator/diagnostics.py", diagnostics_finite_float),
    ("simulator/diagnostic_helpers/binary_pot_battery.py", binary_pot_finite_float),
    ("engines/builtin/melt_effect_adjustment.py", melt_effect_finite_float),
    ("scripts/grid_pregrind_triage.py", grid_pregrind_finite_float),
    (
        "scripts/classify_epoch2_liquid_fraction_failures.py",
        classify_epoch2_finite_float,
    ),
)

# Columns pin is_finite and finite_float results from the existing production copies.
OUTPUT_PIN_GRID = (
    ("zero", 0.0, ("value", "True"), ("value", "0x0.0p+0")),
    (
        "negative_finite",
        -2.25,
        ("value", "True"),
        ("value", "-0x1.2000000000000p+1"),
    ),
    (
        "numeric_string",
        "3.5",
        ("value", "True"),
        ("value", "0x1.c000000000000p+1"),
    ),
    ("nan", float("nan"), ("value", "False"), ("value", "None")),
    ("positive_inf", float("inf"), ("value", "False"), ("value", "None")),
    ("negative_inf", float("-inf"), ("value", "False"), ("value", "None")),
    ("bad_string", "not-a-number", ("value", "False"), ("value", "None")),
    ("none", None, ("value", "False"), ("value", "None")),
    (
        "bool",
        True,
        ("value", "True"),
        ("value", "0x1.0000000000000p+0"),
    ),
    (
        "huge_int",
        10**400,
        ("raises", "OverflowError"),
        ("raises", "OverflowError"),
    ),
)


def _outcome(helper, value):
    try:
        result = helper(value)
    except Exception as exc:
        return ("raises", type(exc).__name__)
    if isinstance(result, float):
        return ("value", result.hex())
    return ("value", repr(result))


def test_is_finite_pin_grid():
    for label, value, expected, _ in OUTPUT_PIN_GRID:
        for source, helper in IS_FINITE_HELPERS:
            assert _outcome(helper, value) == expected, f"{source} {label}"


def test_finite_float_pin_grid():
    for label, value, _, expected in OUTPUT_PIN_GRID:
        for source, helper in FINITE_FLOAT_HELPERS:
            assert _outcome(helper, value) == expected, f"{source} {label}"
