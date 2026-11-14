from __future__ import annotations

import pytest

from simulator.battery.enums import METHOD_TOKEN_ALIASES, MethodToken
from simulator.battery.migrate import map_method


NEW_METHOD_TOKENS = (
    MethodToken.AERODYNAMIC_LEVITATION_LASER_HEATING,
    MethodToken.ADIABATIC_CALORIMETRY,
    MethodToken.DROP_CALORIMETRY,
    MethodToken.SOLUTION_CALORIMETRY,
    MethodToken.PPMS_HEAT_CAPACITY,
    MethodToken.MOSSBAUER_SPECTROSCOPY,
    MethodToken.XANES_SPECTROSCOPY,
    MethodToken.WET_CHEMICAL_ANALYSIS,
    MethodToken.PISTON_CYLINDER,
)


@pytest.mark.parametrize("token", NEW_METHOD_TOKENS)
def test_new_method_tokens_round_trip(token: MethodToken) -> None:
    assert MethodToken(token.value) is token


@pytest.mark.parametrize(
    ("printed", "token"),
    (
        (
            "aerodynamic_levitation_laser_heating",
            MethodToken.AERODYNAMIC_LEVITATION_LASER_HEATING,
        ),
        (
            "aerodynamic_levitation_laser_heating_then_quench",
            MethodToken.AERODYNAMIC_LEVITATION_LASER_HEATING,
        ),
        (
            "adiabatic_calorimetry",
            MethodToken.ADIABATIC_CALORIMETRY,
        ),
        ("drop_calorimetry", MethodToken.DROP_CALORIMETRY),
        (
            "solution_calorimetry_at_298_K",
            MethodToken.SOLUTION_CALORIMETRY,
        ),
        (
            "Physical Properties Measurement System (PPMS) heat-capacity measurement",
            MethodToken.PPMS_HEAT_CAPACITY,
        ),
        (
            "mossbauer_room_temperature",
            MethodToken.MOSSBAUER_SPECTROSCOPY,
        ),
        (
            "Cr K-edge XANES silicate glass redox",
            MethodToken.XANES_SPECTROSCOPY,
        ),
        (
            "Cr K-edge XANES at 1400 °C; high-temperature redox ratio preserved on quench",
            MethodToken.XANES_SPECTROSCOPY,
        ),
        (
            "Modified indirect potentiometric redox titration; numerator is all reduced Ti, reported as Ti3+, and Ti4+ is total Ti minus reduced Ti",
            MethodToken.WET_CHEMICAL_ANALYSIS,
        ),
        (
            "high_pressure_anhydrous_piston_cylinder",
            MethodToken.PISTON_CYLINDER,
        ),
        (
            "piston_cylinder_equilibration_quench",
            MethodToken.PISTON_CYLINDER,
        ),
        (
            "end_loaded_piston_cylinder_CCO_buffered",
            MethodToken.PISTON_CYLINDER,
        ),
    ),
)
def test_reviewed_printed_method_aliases_map_to_closed_tokens(
    printed: str, token: MethodToken
) -> None:
    assert METHOD_TOKEN_ALIASES[printed] is token
    mapped = map_method(printed)
    assert mapped.is_value and mapped.value is token


@pytest.mark.parametrize(
    "printed",
    (
        "compiled_trace_element_concentrations",
        "aerodynamic_levitation",
        "aerodynamic_levitation_evaporation",
        "drop_calorimetry_and_compiled_calorimetry",
        "high_temperature_drop_solution_calorimetry",
        "electron_microprobe_and_wet_chemical_analysis",
        "quoted_wet_chemical_analysis_method",
        "containerless_laser_heating",
    ),
)
def test_ambiguous_method_labels_remain_typed_unknown(printed: str) -> None:
    assert printed not in METHOD_TOKEN_ALIASES
    assert map_method(printed).is_unknown
