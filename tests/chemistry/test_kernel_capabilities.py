"""Regression coverage for kernel capability declarations."""

from __future__ import annotations

from simulator.chemistry.kernel.capabilities import ChemistryIntent


_STALE_2026_05_BINDING_TABLE_VALUES = frozenset(
    {
        "silicate_liquidus",
        "silicate_equilibrium",
        "equilibrium_crystallization",
        "fractional_crystallization",
        "decompression_path",
        "vapor_pressure",
        "evaporation_flux",
        "evaporation_transition",
        "condensation_route",
        "electrolysis_step",
        "metallothermic_step",
        "stage0_pretreatment",
        "overhead_gas_equilibrium",
        "overhead_bleed",
        "sulfur_saturation_gate",
        "t_p_validation",
    }
)

_C8_17_RUNTIME_INTENTS_MISSING_FROM_STALE_TABLE = frozenset(
    {
        "gate_liquid_fraction",
        "metal_phase_stratification",
        "ca_aluminothermic_step",
        "native_fe_saturation",
        "native_fe_metallic_tap",
        "fe_redox_respeciation",
        "oxygen_bubbler",
        "oxygen_reservoir_exchange",
        "backend_equilibrium",
    }
)


def test_binding_table_values_are_derived_from_live_chemistry_intents():
    values = ChemistryIntent.binding_table_values()

    assert values == tuple(intent.value for intent in ChemistryIntent)


def test_binding_table_rows_are_derived_from_live_chemistry_intents():
    rows = ChemistryIntent.binding_table_rows()

    assert rows == tuple((intent.name, intent.value) for intent in ChemistryIntent)


def test_c8_17_live_binding_values_supersede_stale_static_table():
    live_values = frozenset(value for _, value in ChemistryIntent.binding_table_rows())
    missing_from_static_table = live_values - _STALE_2026_05_BINDING_TABLE_VALUES

    assert len(_STALE_2026_05_BINDING_TABLE_VALUES) == 16
    assert _C8_17_RUNTIME_INTENTS_MISSING_FROM_STALE_TABLE <= missing_from_static_table
    assert len(missing_from_static_table) >= 7
