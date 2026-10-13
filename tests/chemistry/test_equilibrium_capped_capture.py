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
    flux = 0.02 * (27.18 - psat) / math.sqrt(2 * math.pi * mass * 8.314462618 * gas_K)
    expected = -math.expm1(-flux * 1e-5 / (1.0 / mass / 3600))
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
