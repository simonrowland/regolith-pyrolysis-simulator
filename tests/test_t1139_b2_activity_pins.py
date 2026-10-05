"""t-1139 Build B2 pins of the trace-parent activity verdict before the ladder.

Taken against the production resolver at the B1 checkout. Temperature is
recorded at both anchors even though this path does not read it. Live
channel pin tables stay the Build A digest.
"""

from __future__ import annotations

import hashlib
import json

from simulator.trace_oxide_parents import ACTIVITY_BASIS, LIQUID_PARENT_OXIDE
from simulator.vapour_rail.activity import (
    ActivityInputDeclaration,
    CondensedPhaseActivityProvider,
    StandardStateIdentity,
)
from tests.test_t1139_b1_ledger_pins import _LIVE_CHANNEL_PIN_DIGEST
from tests.test_t1139_build_a_pins import (
    FIRST_BATCH_EXISTING,
    PRESSURE_PINS,
    STOICH_PINS,
)


PIN_TEMPERATURES_K = (1500.0, 1673.0)
PIN_MOLE_FRACTION = 1e-6
PIN_MOLE_FRACTION_HEX = PIN_MOLE_FRACTION.hex()

# Pre-ladder verdict. No coefficient row, so the Henrian policy asserts an
# ideal solution. gamma is 1 and the activity value is the supplied mole
# fraction. There is no rung and no bound.
_PRE_LADDER_VERDICT = {
    "verdict": "StatusBearingValue",
    "value_hex": PIN_MOLE_FRACTION_HEX,
    "reason": "declared_ideal_solution_activity",
    "provider": "declared_ideal_solution_policy",
    "bound_direction": None,
    "report_label": "status-bearing-not-point",
    "authority": False,
    "evidence_tier": "ASSUMED_IDEAL_SOLUTION",
    "may_certify": False,
    "missing_inputs": ("coefficient_table_row",),
    "rung": None,
    "flag": "declared_ideal_solution_activity",
}


def _trace_parent_formulas() -> tuple[str, ...]:
    return tuple(
        sorted(set(LIQUID_PARENT_OXIDE.values()) | set(ACTIVITY_BASIS.values()))
    )


def _resolve(formula: str, temperature_K: float):
    provider = CondensedPhaseActivityProvider()
    declaration = ActivityInputDeclaration(
        component_id=formula,
        standard_state=StandardStateIdentity(
            convention="raoultian_pure_endmember",
            phase="liquid",
            reference_pressure_bar=1.0,
        ),
        activity_model="source_reaction_activity",
        allow_henrian_upper_bound=True,
    )
    # temperature_K is part of the pin contract. The pre-ladder resolver
    # does not take it; the call still records both anchors.
    del temperature_K
    return provider.resolve_source_reaction_activity(
        declaration,
        magemin=None,
        thermoengine=None,
        activity_exponent=1.0,
        mole_fraction=PIN_MOLE_FRACTION,
    )


def test_trace_parent_activity_verdicts_match_the_pre_ladder_pin() -> None:
    formulas = _trace_parent_formulas()
    assert formulas == (
        "B2O3",
        "Cs2O",
        "Cu2O",
        "CuO0.5",
        "Ga2O3",
        "GaO1.5",
        "GeO2",
        "In2O3",
        "InO1.5",
        "Li2O",
        "PbO",
        "Rb2O",
        "SnO",
        "V2O3",
    )
    for formula in formulas:
        for temperature_K in PIN_TEMPERATURES_K:
            answer = _resolve(formula, temperature_K)
            assert answer.verdict.value == _PRE_LADDER_VERDICT["verdict"], formula
            assert answer.value is not None
            assert answer.value.hex() == _PRE_LADDER_VERDICT["value_hex"], formula
            assert answer.reason == _PRE_LADDER_VERDICT["reason"], formula
            assert answer.provider == _PRE_LADDER_VERDICT["provider"], formula
            assert answer.bound_direction is None, formula
            assert answer.report_label == _PRE_LADDER_VERDICT["report_label"]
            assert answer.authority is False
            assert answer.evidence_tier == _PRE_LADDER_VERDICT["evidence_tier"]
            assert answer.may_certify() is False
            assert answer.derivation["missing_inputs"] == (
                _PRE_LADDER_VERDICT["missing_inputs"]
            )
            assert answer.derivation.get("rung") is None


def test_live_channel_pin_tables_stay_at_the_build_a_digest() -> None:
    payload = {
        "pressure": {key: list(value) for key, value in sorted(PRESSURE_PINS.items())},
        "stoich": {key: list(value) for key, value in sorted(STOICH_PINS.items())},
        "dormant": list(FIRST_BATCH_EXISTING),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    assert hashlib.sha256(encoded).hexdigest() == _LIVE_CHANNEL_PIN_DIGEST
