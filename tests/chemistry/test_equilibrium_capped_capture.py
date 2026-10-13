"""Physical limits of stage capture, independent of output golden pins."""

import math

import pytest

from simulator import condensation
from simulator.state import EvaporationFlux, MeltState
from tests.test_condensation_input_refusals import _configured_model


def efficiency(model, pressure, *, alpha=0.02, residence=5.0, flow=1.0):
    return model._condensation_efficiency(
        stage=model.train.stages[1], species="Fe", T_cond_C=1250.0,
        residence_s=residence, available_kg=flow, alpha_s_value=alpha,
        inlet_partial_pressure_pa=pressure,
    )


def _integrated_pressure_driven_capture(
    *, pressure, psat, temperature, coefficient, area, inventory, residence,
):
    """Numerically integrate dN/dt=-K*A*(p(N)-P_sat), independent of the closed form."""
    volume = inventory * condensation.GAS_CONSTANT_J_MOL_K * temperature / pressure

    def derivative(moles):
        local_pressure = moles * condensation.GAS_CONSTANT_J_MOL_K * temperature / volume
        return -coefficient * (local_pressure - psat) * area

    moles = inventory
    dt = residence / 10_000
    for _ in range(10_000):
        k1 = derivative(moles)
        k2 = derivative(moles + dt * k1 / 2)
        k3 = derivative(moles + dt * k2 / 2)
        k4 = derivative(moles + dt * k3)
        moles += dt * (k1 + 2 * k2 + 2 * k3 + k4) / 6
    return 1 - moles / inventory


@pytest.mark.parametrize("alpha,residence", [(0.0, 5.0), (0.02, 0.0)])
def test_zero_surface_uptake_or_residence(alpha, residence):
    assert efficiency(_configured_model(), 27.18, alpha=alpha, residence=residence) == 0.0


def test_undersaturated_and_saturated_stream_passes_through():
    model = _configured_model()
    psat, refused = condensation._try_antoine_psat_pa("Fe", 1523.15)
    assert not refused
    assert efficiency(model, psat / 2) == 0.0
    assert efficiency(model, psat) == 0.0


def test_large_capture_rate_stops_at_equilibrium():
    model = _configured_model()
    model.stage_area_m2_by_stage = {"1": 1e12}
    psat, refused = condensation._try_antoine_psat_pa("Fe", 1523.15)
    assert not refused
    # Same owning saturation resolver as the wall model, not a copied fit.
    assert efficiency(model, 27.18) == pytest.approx(1 - psat / 27.18)


@pytest.mark.parametrize(
    ("sat_ratio", "integrated_capture"),
    [(0.5, 0.3160602794), (0.9, 0.06321205588)],
)
def test_capture_matches_numerical_excess_inventory_ode(sat_ratio, integrated_capture):
    model = _configured_model()
    model.regime_factor = 1.0
    area = 0.045238934
    model.stage_area_m2_by_stage = {"1": area}
    psat, refused = condensation._try_antoine_psat_pa("Fe", 1523.15)
    assert not refused
    pressure = psat / sat_ratio
    molar_mass = condensation.MOLAR_MASS["Fe"] / 1000
    temperature = model.gas_temperature_C + 273.15
    coefficient = 0.02 / math.sqrt(
        2 * math.pi * molar_mass * condensation.GAS_CONSTANT_J_MOL_K * temperature
    )
    flow = coefficient * pressure * area * molar_mass * 3600
    inventory = flow / molar_mass / 3600 * 5
    integrated = _integrated_pressure_driven_capture(
        pressure=pressure, psat=psat, temperature=temperature,
        coefficient=coefficient, area=area, inventory=inventory, residence=5,
    )
    assert integrated == pytest.approx(integrated_capture, rel=1e-8)

    actual = model._condensation_efficiency(
        stage=model.train.stages[1], species="Fe", T_cond_C=1250,
        residence_s=5, available_kg=flow, alpha_s_value=0.02,
        inlet_partial_pressure_pa=pressure,
    )
    assert actual == pytest.approx(integrated, rel=1e-8)


def test_free_molecular_rate_uses_actual_flow_inventory():
    model = _configured_model()
    model.regime_factor = 1.0
    model.stage_area_m2_by_stage = {"1": 1e-5}
    psat, refused = condensation._try_antoine_psat_pa("Fe", 1523.15)
    assert not refused
    # Safarian & Engh HKL, molar form: J=alpha*dp/sqrt(2*pi*M*R*Tgas).
    # M from the species registry; flow is kg/h, so n_dot=flow/M/3600.
    mass = condensation.MOLAR_MASS["Fe"] / 1000
    gas_K = model.gas_temperature_C + 273.15
    coefficient = 0.02 / math.sqrt(
        2 * math.pi * mass * condensation.GAS_CONSTANT_J_MOL_K * gas_K
    )
    inventory = 1.0 / mass / 3600 * 5
    expected = _integrated_pressure_driven_capture(
        pressure=27.18, psat=psat, temperature=gas_K, coefficient=coefficient,
        area=1e-5, inventory=inventory, residence=5,
    )
    assert efficiency(model, 27.18) == pytest.approx(expected, rel=1e-8)
    # N_stage=n_dot*tau, so tau cancels for fixed flow/area/pressure.
    assert efficiency(model, 27.18, residence=50.0) == pytest.approx(expected, rel=1e-8)


def test_missing_inlet_pressure_is_unavailable():
    model = _configured_model()
    model.wall_species_partial_pressures_pa = {}
    model.species_partial_pressures_mbar = {}
    with pytest.raises(condensation.DepositionInputRefusal):
        efficiency(model, None)


@pytest.mark.parametrize("band", [(math.nan, 600.0), (500.0, math.inf), (600.0, 500.0)])
def test_invalid_stage_temperature_band_remains_a_typed_refusal(band):
    model = _configured_model()
    model.train.stages[1].temp_range_C = band
    with pytest.raises(condensation.DepositionInputRefusal, match="stage_temperature"):
        efficiency(model, 27.18)


def test_route_keeps_capture_at_its_stage_and_depletes_inlet_pressure(monkeypatch):
    model = _configured_model()
    model.pipe_segments = []
    model.train.stages = [model.train.stages[1], model.train.stages[3]]
    model.species_partial_pressures_mbar = {"Fe": 1.0}
    seen = []

    def capture_half(**inputs):
        seen.append((inputs["stage"].stage_number, inputs["available_kg"],
                     inputs["inlet_partial_pressure_pa"]))
        return 0.5

    monkeypatch.setattr(model, "_condensation_efficiency", capture_half)
    result = model.route(EvaporationFlux(species_kg_hr={"Fe": 1.0}), MeltState(temperature_C=1700.0))
    assert result.condensed_by_stage_species == {1: {"Fe": 0.5}, 3: {"Fe": 0.25}}
    assert result.remaining_by_species["Fe"] == pytest.approx(0.25)
    assert seen[0] == (1, 1.0, 100.0)
    # Dalton: 0.1 Fe + 0.9 carrier at inlet; after half Fe removal,
    # y_Fe = 0.05/(0.05+0.9), and p_Fe = y_Fe * 1000 Pa.
    assert seen[1] == pytest.approx((3, 0.5, 1000 * 0.05 / 0.95))


def test_hot_sio_passes_to_its_reactive_product_stage():
    model = _configured_model()
    model.pipe_segments = []
    model.species_partial_pressures_mbar = {"SiO": 1.0}
    model.wall_species_partial_pressures_pa = {"SiO": 100.0}
    result = model.route(EvaporationFlux(species_kg_hr={"SiO": 1.0}), MeltState(temperature_C=1700.0))
    assert "SiO" not in result.condensed_by_stage_species.get(1, {})
    assert "SiO" not in result.condensed_by_stage_species.get(2, {})
    assert result.condensed_by_stage_species[3]["SiO"] > 0.0


def test_unavailable_wall_pressure_retains_source_with_typed_refusal(monkeypatch):
    model = _configured_model()
    model.pipe_segments = []
    model.species_partial_pressures_mbar = {"Na": 1.0}
    def unavailable(species, pressure, temperature, **kwargs):
        raise condensation.WallSaturationPressureRefusal(species, temperature, "saturation_pressure_unavailable")

    monkeypatch.setattr(condensation, "_wall_deposition_driving_pressure_pa", unavailable)
    result = model.route(EvaporationFlux(species_kg_hr={"Na": 1.0}), MeltState(temperature_C=1700.0))
    assert result.retained_in_source_by_species["Na"] == 1.0
    assert result.remaining_by_species["Na"] == 0.0
    assert result.condensation_authority_by_species["Na"]["status"] == "refused"
    refusal = result.condensation_refusals_by_species["Na"]
    assert refusal["status"] == "refused"
    refused_stage = next(item for item in refusal["stage_outcomes"] if item["status"] == "refused")
    assert refused_stage["eta"] is None
