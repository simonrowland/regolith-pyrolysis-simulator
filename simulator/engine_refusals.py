"""Shared wire vocabulary for typed engine refusals."""

from collections.abc import Mapping

REFUSAL_OUT_OF_BASIS = "out_of_basis"
REFUSAL_COMPOSITION_PROJECTED = "composition_projected"
REFUSAL_MAJOR_SUM = "sum_below_95_wt_pct"
REFUSAL_NO_LIQUID = "no_liquid"
REFUSAL_TIMEOUT = "engine_timeout"
REFUSAL_ENGINE_CRASH = "engine_crash"
REFUSAL_GATE_REFUSED_IN_ADAPTER = "gate_refused_in_adapter"
REFUSAL_UNAVAILABLE = "unavailable"
REFUSAL_VALUE_IS_FLOOR = "value_is_floor"

CACHEABLE_REFUSAL_CODES = frozenset({
    REFUSAL_OUT_OF_BASIS, REFUSAL_COMPOSITION_PROJECTED, REFUSAL_MAJOR_SUM,
    REFUSAL_NO_LIQUID,
})


def cacheable_refusal(result) -> bool:
    return (
        isinstance(result, Mapping)
        and result.get("status") == "refusal"
        and result.get("refusal_reason") in CACHEABLE_REFUSAL_CODES
        and result.get("engine_status") in {None, "out_of_domain", "input_refusal", "refusal", "no_liquid", "ok"}
        and not result.get("exit_signal")
        and not result.get("exit_code")
    )
