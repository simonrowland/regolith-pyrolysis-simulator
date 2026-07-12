"""Mechanism contracts for electrolysis reaction quotients and controls."""

from __future__ import annotations

import math

import pytest

from engines.builtin.electrolysis_step import (
    MRE_INVALID_CONTROL_REFUSAL,
    MRE_INVALID_GAS_FUGACITY_REFUSAL,
    MRE_INVALID_TARGET_REFUSAL,
    BuiltinElectrolysisStepProvider,
)
from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_METAL_PHASE_CONDENSED,
    ELLINGHAM_METAL_PHASE_GAS,
)
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.electrolysis import (
    ELECTRONS_PER_OXIDE,
    ElectrolysisModel,
    mre_selectivity_weight,
)
from simulator.state import (
    FARADAY,
    GAS_CONSTANT,
    OXIDE_TO_METAL,
    MeltState,
)
from tests.chemistry.conftest import _build_sim


def _single_oxide_view(sim, oxide: str, *, oxide_mol: float = 10.0):
    return ProviderAccountView(
        accounts={
            "process.cleaned_melt": {oxide: oxide_mol},
            "process.metal_phase": {},
            "process.overhead_gas": {},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )


def test_parent_oxide_nernst_quotient_exponents_single_cation_activity():
    """Na2O uses a_NaO0.5^2 because E0 is per parent formula."""

    temperature_K = 1873.15
    activity = 1.0e-2
    unit = BuiltinElectrolysisStepProvider._nernst_voltage(
        "Na2O",
        temperature_K,
        1.0,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        electrons_per_oxide={"Na2O": 2},
        oxide_to_metal={"Na2O": ("Na", 2, 1)},
        metal_product_phase=ELLINGHAM_METAL_PHASE_CONDENSED,
        decomp_voltages={"Na2O": 0.5},
    )
    depleted = BuiltinElectrolysisStepProvider._nernst_voltage(
        "Na2O",
        temperature_K,
        activity,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        electrons_per_oxide={"Na2O": 2},
        oxide_to_metal={"Na2O": ("Na", 2, 1)},
        metal_product_phase=ELLINGHAM_METAL_PHASE_CONDENSED,
        decomp_voltages={"Na2O": 0.5},
    )

    # ln(Q) contains -2*ln(a), while the parent formula transfers 2 electrons.
    expected_shift = -(GAS_CONSTANT * temperature_K / FARADAY) * math.log(
        activity
    )
    assert depleted - unit == pytest.approx(expected_shift)

    legacy = ElectrolysisModel()
    legacy_unit = legacy.nernst_voltage(
        "Na2O", temperature_K - 273.15, 1.0, metal_fugacity_bar=1.0
    )
    legacy_depleted = legacy.nernst_voltage(
        "Na2O",
        temperature_K - 273.15,
        activity,
        metal_fugacity_bar=1.0,
    )
    assert legacy_depleted - legacy_unit == pytest.approx(expected_shift)


def test_depleted_activity_nernst_voltage_is_continuous_at_old_shortcut():
    temperature_K = 1873.15
    boundary = 1.0e-10

    def voltage(activity: float) -> float:
        return BuiltinElectrolysisStepProvider._nernst_voltage(
            "K2O",
            temperature_K,
            activity,
            gas_constant=GAS_CONSTANT,
            faraday=FARADAY,
            electrons_per_oxide={"K2O": 2},
            oxide_to_metal={"K2O": ("K", 2, 1)},
            metal_product_phase=ELLINGHAM_METAL_PHASE_CONDENSED,
            decomp_voltages={"K2O": 0.5},
        )

    below = voltage(boundary * (1.0 - 1.0e-6))
    above = voltage(boundary * (1.0 + 1.0e-6))
    assert abs(below - above) < 1.0e-6
    assert voltage(0.0) > below


def test_gas_product_fugacity_enters_nernst_quotient_and_authority_gate(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    temperature_K = 1873.15
    at_standard_state = BuiltinElectrolysisStepProvider._nernst_voltage(
        "MgO",
        temperature_K,
        1.0,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        electrons_per_oxide=ELECTRONS_PER_OXIDE,
        oxide_to_metal=OXIDE_TO_METAL,
        metal_product_phase=ELLINGHAM_METAL_PHASE_GAS,
        metal_fugacity_bar=1.0,
    )
    at_10_mbar = BuiltinElectrolysisStepProvider._nernst_voltage(
        "MgO",
        temperature_K,
        1.0,
        gas_constant=GAS_CONSTANT,
        faraday=FARADAY,
        electrons_per_oxide=ELECTRONS_PER_OXIDE,
        oxide_to_metal=OXIDE_TO_METAL,
        metal_product_phase=ELLINGHAM_METAL_PHASE_GAS,
        metal_fugacity_bar=0.01,
    )
    expected_shift = (
        GAS_CONSTANT * temperature_K / (2.0 * FARADAY) * math.log(0.01)
    )
    assert at_10_mbar - at_standard_state == pytest.approx(expected_shift)

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    view = _single_oxide_view(sim, "MgO")
    voltage_V = (at_standard_state + at_10_mbar) / 2.0

    def dispatch(fugacity=None):
        controls = {
            "voltage_V": voltage_V,
            "current_A": 100.0,
            "dt_hr": 1.0,
            "allowed_oxides": ["MgO"],
        }
        if fugacity is not None:
            controls["gas_product_fugacity_bar"] = {"Mg": fugacity}
        return BuiltinElectrolysisStepProvider().dispatch(
            IntentRequest(
                intent=ChemistryIntent.ELECTROLYSIS_STEP,
                account_view=view,
                temperature_C=temperature_K - 273.15,
                pressure_bar=1.0,
                control_inputs=controls,
            )
        )

    standard_state = dispatch()
    assert standard_state.status == "ok"
    assert standard_state.transition is None
    assert standard_state.diagnostic[
        "mre_gas_product_fugacity_bar_by_oxide"
    ]["MgO"] == pytest.approx(1.0)
    assert standard_state.diagnostic[
        "mre_gas_product_fugacity_source_by_oxide"
    ]["MgO"] == "standard_state_default_1_bar"

    explicit = dispatch(0.01)
    assert explicit.transition is not None
    assert explicit.diagnostic[
        "mre_gas_product_fugacity_bar_by_oxide"
    ]["MgO"] == pytest.approx(0.01)
    assert explicit.diagnostic[
        "mre_gas_product_fugacity_source_by_oxide"
    ]["MgO"] == "control_inputs.gas_product_fugacity_bar"

    live_view = ProviderAccountView(
        accounts={
            "process.cleaned_melt": {"MgO": 10.0},
            "process.metal_phase": {},
            "process.overhead_gas": {"Mg": 1.0, "O2": 9.0},
            "terminal.oxygen_mre_anode_stored": {},
        },
        species_formula_registry=sim.species_formula_registry,
    )
    overhead = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=live_view,
            temperature_C=temperature_K - 273.15,
            pressure_bar=0.1,
            control_inputs={
                "voltage_V": voltage_V,
                "current_A": 100.0,
                "dt_hr": 1.0,
                "pO2_bar": 1.0,
                "allowed_oxides": ["MgO"],
            },
        )
    )
    assert overhead.transition is not None
    assert overhead.diagnostic[
        "mre_gas_product_fugacity_bar_by_oxide"
    ]["MgO"] == pytest.approx(0.01)
    assert overhead.diagnostic[
        "mre_gas_product_fugacity_source_by_oxide"
    ]["MgO"] == "account_view.process.overhead_gas_ideal_partial_pressure"


def test_ferric_nernst_quotient_includes_squared_feo_product_activity():
    temperature_K = 1873.15

    def provider_voltage(feo_activity: float) -> float:
        return BuiltinElectrolysisStepProvider._ferric_to_ferrous_voltage(
            temperature_K,
            0.2,
            gas_constant=GAS_CONSTANT,
            faraday=FARADAY,
            reference_V=0.65,
            electrons=2,
            o2_per_fe2o3=0.5,
            pO2_bar=1.0,
            feo_activity=feo_activity,
        )

    expected_shift = GAS_CONSTANT * temperature_K / FARADAY * math.log(0.1)
    assert provider_voltage(0.1) - provider_voltage(1.0) == pytest.approx(
        expected_shift
    )

    legacy = ElectrolysisModel()
    legacy_at_one = legacy.ferric_to_ferrous_voltage(
        temperature_K - 273.15, 0.2, feo_activity=1.0
    )
    legacy_at_tenth = legacy.ferric_to_ferrous_voltage(
        temperature_K - 273.15, 0.2, feo_activity=0.1
    )
    assert legacy_at_tenth - legacy_at_one == pytest.approx(expected_shift)


@pytest.mark.parametrize(
    "control_updates, request_updates, invalid_name",
    [
        ({"voltage_V": math.nan}, {}, "voltage_V"),
        ({"current_A": math.inf}, {}, "current_A"),
        ({"dt_hr": -1.0}, {}, "dt_hr"),
        ({"voltage_V": True}, {}, "voltage_V"),
        ({}, {"temperature_C": -273.15}, "temperature_C"),
        ({}, {"pressure_bar": math.inf}, "pressure_bar"),
        ({}, {"pressure_bar": -1.0}, "pressure_bar"),
    ],
)
def test_invalid_authoritative_controls_refuse_before_energy_or_transition(
    control_updates,
    request_updates,
    invalid_name,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    controls = {
        "voltage_V": 5.0,
        "current_A": 100.0,
        "dt_hr": 1.0,
        "pO2_bar": 1.0,
        "allowed_oxides": ["SiO2"],
    }
    controls.update(control_updates)
    request_values = {"temperature_C": 1575.0, "pressure_bar": 1.0}
    request_values.update(request_updates)
    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=_single_oxide_view(sim, "SiO2"),
            control_inputs=controls,
            **request_values,
        )
    )

    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == MRE_INVALID_CONTROL_REFUSAL
    assert invalid_name in result.diagnostic["invalid_controls"]
    assert result.diagnostic["energy_kWh"] == 0.0


@pytest.mark.parametrize("zero_control", ["voltage_V", "current_A", "dt_hr"])
def test_zero_controls_remain_valid_no_ops(
    zero_control,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    controls = {
        "voltage_V": 5.0,
        "current_A": 100.0,
        "dt_hr": 1.0,
        "pO2_bar": 1.0,
        "allowed_oxides": ["SiO2"],
    }
    controls[zero_control] = 0.0
    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=_single_oxide_view(sim, "SiO2"),
            temperature_C=1575.0,
            pressure_bar=0.0,
            control_inputs=controls,
        )
    )
    assert result.status == "ok"
    assert result.transition is None
    assert result.diagnostic["energy_kWh"] == 0.0


@pytest.mark.parametrize("pO2_bar", [0.0, -1.0, math.nan, math.inf])
def test_invalid_anode_pressure_refuses_instead_of_creating_extreme_vacuum(
    pO2_bar,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=_single_oxide_view(sim, "SiO2"),
            temperature_C=1575.0,
            pressure_bar=1.0,
            control_inputs={
                "voltage_V": 0.5,
                "current_A": 100.0,
                "dt_hr": 1.0,
                "pO2_bar": pO2_bar,
                "allowed_oxides": ["SiO2"],
            },
        )
    )
    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == MRE_INVALID_CONTROL_REFUSAL
    assert "pO2_bar" in result.diagnostic["invalid_controls"]


@pytest.mark.parametrize(
    "allowed_oxides",
    ["FeO", 7, ["Bogus"], ["FeO", "Bogus"], (item for item in ["FeO"])],
)
def test_invalid_allowed_oxide_filters_refuse_atomically(
    allowed_oxides,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=_single_oxide_view(sim, "FeO"),
            temperature_C=1575.0,
            pressure_bar=1.0,
            control_inputs={
                "voltage_V": 5.0,
                "current_A": 100.0,
                "dt_hr": 1.0,
                "allowed_oxides": allowed_oxides,
            },
        )
    )
    assert result.status == "refused"
    assert result.transition is None
    assert result.diagnostic["reason_refused"] == MRE_INVALID_TARGET_REFUSAL
    assert result.diagnostic["energy_kWh"] == 0.0


@pytest.mark.parametrize("fugacity", [0.0, -1.0, math.nan, math.inf, True])
def test_invalid_gas_product_fugacity_refuses_atomically(
    fugacity,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    result = BuiltinElectrolysisStepProvider().dispatch(
        IntentRequest(
            intent=ChemistryIntent.ELECTROLYSIS_STEP,
            account_view=_single_oxide_view(sim, "MgO"),
            temperature_C=1600.0,
            pressure_bar=1.0,
            control_inputs={
                "voltage_V": 5.0,
                "current_A": 100.0,
                "dt_hr": 1.0,
                "allowed_oxides": ["MgO"],
                "gas_product_fugacity_bar": {"Mg": fugacity},
            },
        )
    )
    assert result.status == "refused"
    assert result.transition is None
    assert (
        result.diagnostic["reason_refused"]
        == MRE_INVALID_GAS_FUGACITY_REFUSAL
    )


def test_legacy_gas_fugacity_inputs_fail_closed():
    model = ElectrolysisModel()
    melt = MeltState(composition_kg={"MgO": 1.0})

    with pytest.raises(ValueError, match="finite and positive"):
        model.step_hour(
            melt,
            voltage_V=5.0,
            current_A=100.0,
            T_C=1600.0,
            gas_product_fugacity_bar={"Mg": -1.0},
        )

    standard_state = model.step_hour(
        MeltState(composition_kg={"MgO": 1.0}),
        voltage_V=5.0,
        current_A=100.0,
        T_C=1600.0,
    )
    assert standard_state["mre_gas_product_fugacity_bar_by_oxide"][
        "MgO"
    ] == pytest.approx(1.0)
    assert standard_state["mre_gas_product_fugacity_source_by_oxide"][
        "MgO"
    ] == "standard_state_default_1_bar"

    with pytest.raises(ValueError, match="pO2_bar"):
        model.get_reduction_sequence(
            MeltState(composition_kg={"MgO": 1.0}),
            T_C=1600.0,
            pO2_bar=0.0,
            gas_product_fugacity_bar={"Mg": 1.0},
        )


def test_selectivity_exponent_uses_thermal_voltage_scale():
    activity = 0.25
    exponent = 0.75
    temperature_1_K = 1500.0
    temperature_2_K = 2000.0
    overvoltage_1_V = exponent * GAS_CONSTANT * temperature_1_K / FARADAY
    overvoltage_2_V = exponent * GAS_CONSTANT * temperature_2_K / FARADAY

    expected = activity * math.exp(exponent)
    assert mre_selectivity_weight(
        activity, overvoltage_1_V, temperature_1_K
    ) == pytest.approx(expected)
    assert mre_selectivity_weight(
        activity, overvoltage_2_V, temperature_2_K
    ) == pytest.approx(expected)
