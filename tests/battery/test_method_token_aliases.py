from __future__ import annotations

import pytest

from simulator.battery.enums import MethodToken
from simulator.battery.migrate import map_method


@pytest.mark.parametrize(
    ("label", "expected"),
    (
        ("knudsen_effusion", MethodToken.KNUDSEN_EFFUSION),
        ("static_equilibrium", MethodToken.STATIC_EQUILIBRIUM),
        ("langmuir_free_evaporation", MethodToken.LANGMUIR_FREE_EVAPORATION),
        ("transpiration", MethodToken.TRANSPIRATION),
        ("tga", MethodToken.TGA),
        ("dta_dsc", MethodToken.DTA_DSC),
        ("evolved_gas_ms", MethodToken.EVOLVED_GAS_MS),
        ("solar_furnace_pyrolysis", MethodToken.SOLAR_FURNACE_PYROLYSIS),
        ("vacuum_chamber_pyrolysis", MethodToken.VACUUM_CHAMBER_PYROLYSIS),
        ("emf_cell", MethodToken.EMF_CELL),
        ("quench_equilibration", MethodToken.QUENCH_EQUILIBRATION),
        ("tabulation", MethodToken.TABULATION),
        ("engine_evaluation", MethodToken.ENGINE_EVALUATION),
        ("static_capsule_vapor_pressure", MethodToken.STATIC_EQUILIBRIUM),
        ("kems_effusion", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_compiled_experiment", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_method_context", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_review_compilation", MethodToken.KNUDSEN_EFFUSION),
        ("kems_effusion_ion_comparison", MethodToken.KNUDSEN_EFFUSION),
        ("knudsen_effusion_mass_spectrometry", MethodToken.KNUDSEN_EFFUSION),
        (
            "Knudsen-cell mass-spectrometric ion currents converted to partial pressures using the thesis calibration.",
            MethodToken.KNUDSEN_EFFUSION,
        ),
        (
            "KEMS ion-current intensities converted to partial pressures using the thesis calibration (Eq. 2).",
            MethodToken.KNUDSEN_EFFUSION,
        ),
        ("free_evaporation", MethodToken.LANGMUIR_FREE_EVAPORATION),
        ("open_furnace_apparent", MethodToken.LANGMUIR_FREE_EVAPORATION),
        ("solar_furnace", MethodToken.SOLAR_FURNACE_PYROLYSIS),
        ("solar_vacuum_pyrolysis", MethodToken.SOLAR_FURNACE_PYROLYSIS),
        (
            "solar_vacuum_pyrolysis_free_evaporation",
            MethodToken.SOLAR_FURNACE_PYROLYSIS,
        ),
        (
            "solar_fresnel_vacuum_pyrolysis",
            MethodToken.SOLAR_FURNACE_PYROLYSIS,
        ),
        (
            "solar_fresnel_continuously_pumped_vacuum_pyrolysis",
            MethodToken.SOLAR_FURNACE_PYROLYSIS,
        ),
        ("melt_equilibration_quench", MethodToken.QUENCH_EQUILIBRATION),
        ("gas_standard_state_thermo", MethodToken.TABULATION),
    ),
)
def test_previously_accepted_method_labels_keep_their_targets(
    label: str, expected: MethodToken
) -> None:
    mapped = map_method(label)

    assert mapped.is_value
    assert mapped.value is expected


@pytest.mark.parametrize("label", (None, "unrecognized_method_label"))
def test_missing_or_unrecognized_method_labels_remain_typed_unknown(
    label: object,
) -> None:
    assert map_method(label).is_unknown
