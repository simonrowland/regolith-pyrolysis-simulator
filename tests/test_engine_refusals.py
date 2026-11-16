"""Pin the existing refusal vocabulary before consolidating its owner."""

from simulator.diagnostic_helpers import binary_pot_battery as battery


def test_existing_refusal_wire_codes_are_unchanged():
    assert {
        name: getattr(battery, name) for name in (
            "REFUSAL_OUT_OF_BASIS", "REFUSAL_COMPOSITION_PROJECTED", "REFUSAL_MAJOR_SUM",
            "REFUSAL_NO_LIQUID", "REFUSAL_TIMEOUT", "REFUSAL_ENGINE_CRASH",
            "REFUSAL_GATE_REFUSED_IN_ADAPTER", "REFUSAL_UNAVAILABLE", "REFUSAL_VALUE_IS_FLOOR",
        )
    } == {
        "REFUSAL_OUT_OF_BASIS": "out_of_basis",
        "REFUSAL_COMPOSITION_PROJECTED": "composition_projected",
        "REFUSAL_MAJOR_SUM": "sum_below_95_wt_pct",
        "REFUSAL_NO_LIQUID": "no_liquid",
        "REFUSAL_TIMEOUT": "engine_timeout",
        "REFUSAL_ENGINE_CRASH": "engine_crash",
        "REFUSAL_GATE_REFUSED_IN_ADAPTER": "gate_refused_in_adapter",
        "REFUSAL_UNAVAILABLE": "unavailable",
        "REFUSAL_VALUE_IS_FLOOR": "value_is_floor",
    }
