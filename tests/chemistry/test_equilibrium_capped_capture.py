"""Physical limits of stage capture, independent of output golden pins."""

import math

import pytest

from simulator import condensation
from simulator.state import EvaporationFlux, MeltState
from tests.test_condensation_input_refusals import _configured_model


def _fe_stage_wall_K(model):
    """Production Stage-1 wall: band midpoint, not a pasted temperature."""
    return condensation._stage_midpoint_temperature_K(model.train.stages[1])


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
    psat, refused = condensation._try_antoine_psat_pa("Fe", _fe_stage_wall_K(model))
    assert not refused
    assert efficiency(model, psat / 2) == 0.0
    assert efficiency(model, psat) == 0.0


def test_large_capture_rate_stops_at_equilibrium():
    model = _configured_model()
    model.stage_area_m2_by_stage = {"1": 1e12}
    psat, refused = condensation._try_antoine_psat_pa("Fe", _fe_stage_wall_K(model))
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
    psat, refused = condensation._try_antoine_psat_pa("Fe", _fe_stage_wall_K(model))
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
    psat, refused = condensation._try_antoine_psat_pa("Fe", _fe_stage_wall_K(model))
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

    # Use a smaller collector so the rate-doubling check is in the linear,
    # small-exposure regime; its expected value still comes from the ODE above.
    small_area = 1e-9
    model.stage_area_m2_by_stage = {"1": small_area}
    expected_at_small_area = _integrated_pressure_driven_capture(
        pressure=27.18, psat=psat, temperature=gas_K, coefficient=coefficient,
        area=small_area, inventory=inventory, residence=5,
    )
    expected_at_double_flow = _integrated_pressure_driven_capture(
        pressure=27.18, psat=psat, temperature=gas_K, coefficient=coefficient,
        area=small_area, inventory=2 * inventory, residence=5,
    )
    actual_at_small_area = efficiency(model, 27.18, flow=1.0)
    actual_at_double_flow = efficiency(model, 27.18, flow=2.0)
    assert actual_at_small_area == pytest.approx(expected_at_small_area, rel=1e-8)
    assert actual_at_double_flow == pytest.approx(expected_at_double_flow, rel=1e-8)
    # At small exposure, doubling the hourly supply rate doubles n_dot and
    # halves eta to first order, with area, pressure, alpha and residence fixed.
    assert actual_at_double_flow == pytest.approx(expected_at_small_area / 2, rel=1e-5)


def test_missing_inlet_pressure_is_unavailable():
    model = _configured_model()
    model.wall_species_partial_pressures_pa = {}
    model.species_partial_pressures_mbar = {}
    with pytest.raises(condensation.DepositionInputRefusal):
        efficiency(model, None)


def test_sio_above_reactive_product_cutoff_is_physical_pass_through():
    model = _configured_model()
    stage = model.train.stages[3]
    stage.temp_range_C = (1100.0, 1200.0)
    outcomes = []

    eta = model._condensation_efficiency(
        stage=stage, species="SiO", T_cond_C=1050.0, residence_s=4.0,
        available_kg=1.0, alpha_s_value=0.5,
        inlet_partial_pressure_pa=100.0, efficiency_outcomes=outcomes,
    )

    assert eta == 0.0
    assert outcomes[-1]["status"] == "pass_through"
    assert outcomes[-1]["reason"] == "above_reactive_product_condensation_temperature"


def test_reactive_sio_backstop_keeps_raw_saturated_strip_active():
    model = _configured_model()
    stage = model.train.stages[3]
    stage.temp_range_C = (1200.0, 1200.0)
    model.vapor_pressure_data = {
        "oxide_vapors": {
            "SiO": {
                "pure_component_antoine": {
                    "A": 2.0,
                    "B": 0.0,
                    "C": 0.0,
                    "valid_range_K": [1000.0, 1600.0],
                },
            },
        },
    }
    wall_temperature_K = 1200.0 + condensation.CELSIUS_TO_KELVIN_OFFSET
    raw_psat, refused = condensation._try_antoine_psat_pa(
        "SiO", wall_temperature_K,
        vapor_pressure_data=model.vapor_pressure_data,
    )
    assert not refused
    assert raw_psat is not None
    inlet_pressure_pa = raw_psat / 2.0
    assert raw_psat >= inlet_pressure_pa

    diagnostic = {}
    flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        "SiO", inlet_pressure_pa, wall_temperature_K, 0.5,
        pipe_diameter_m=model.pipe_diameter_m,
        T_gas_K=model.gas_temperature_C + condensation.CELSIUS_TO_KELVIN_OFFSET,
        overhead_pressure_pa=model.overhead_pressure_mbar * 100.0,
        carrier_gas=model.carrier_gas,
        vapor_pressure_data=model.vapor_pressure_data,
        reactive_product_backstop=True,
        diagnostic_out=diagnostic,
    )
    assert flux > 0.0
    assert diagnostic["wall_saturation_pressure_pa"] == 0.0

    eta = model._condensation_efficiency(
        stage=stage, species="SiO", T_cond_C=1200.0, residence_s=4.0,
        available_kg=1.0, alpha_s_value=0.5,
        inlet_partial_pressure_pa=inlet_pressure_pa,
    )
    assert eta > 0.0


def test_sio_missing_saturation_without_reactive_backstop_refuses(monkeypatch):
    model = _configured_model()
    stage = model.train.stages[3]
    stage.temp_range_C = (1000.0, 1000.0)
    monkeypatch.setattr(
        condensation, "_reactive_product_backstop_authorized", lambda _species: False,
    )
    monkeypatch.setattr(
        condensation, "_try_antoine_psat_pa", lambda *_args, **_kwargs: (None, False),
    )

    with pytest.raises(
        condensation.WallSaturationPressureRefusal,
        match="saturation_pressure_unavailable",
    ):
        model._condensation_efficiency(
            stage=stage, species="SiO", T_cond_C=1050.0, residence_s=4.0,
            available_kg=1.0, alpha_s_value=0.5,
            inlet_partial_pressure_pa=100.0,
        )


def test_band_capture_integrates_33_centers_with_inclusive_reactive_cutoff(monkeypatch):
    model = _configured_model()
    stage = model.train.stages[3]
    stage.temp_range_C = (900.0, 1200.0)
    evaluated_C = []

    def constant_flux(_species, _pressure, temperature_K, _alpha, **kwargs):
        evaluated_C.append(temperature_K - 273.15)
        kwargs["diagnostic_out"]["wall_saturation_pressure_pa"] = 0.0
        kwargs["diagnostic_out"]["wall_saturation_pressure_refused"] = False
        return 0.02

    monkeypatch.setattr(
        condensation, "_series_resistance_deposition_flux_mol_m2_s", constant_flux,
    )
    eta = model._condensation_efficiency(
        stage=stage, species="SiO", T_cond_C=1050.0, residence_s=4.0,
        available_kg=1.0, alpha_s_value=0.5,
        inlet_partial_pressure_pa=100.0,
    )

    assert len(evaluated_C) == 17
    assert max(evaluated_C) == pytest.approx(1050.0)
    assert min(evaluated_C) > 900.0
    band_flux = 0.02 * 17 / condensation.HKL_BAND_SAMPLES
    molar_mass = (
        condensation._molecular_mass_kg_per_molecule("SiO")
        * condensation.AVOGADRO_MOL
    )
    inventory = 1.0 / molar_mass / 3600.0 * 4.0
    expected = _integrated_pressure_driven_capture(
        pressure=100.0, psat=0.0,
        temperature=model.gas_temperature_C + 273.15,
        coefficient=band_flux / 100.0,
        area=model._stage_area_m2_for_stage(stage),
        inventory=inventory, residence=4.0,
    )
    assert eta == pytest.approx(expected, rel=1e-8)


def test_mixed_saturation_band_matches_numerical_ode_below_mean_threshold(monkeypatch):
    model = _configured_model()
    stage = model.train.stages[3]
    stage.temp_range_C = (0.0, 2.0)
    monkeypatch.setattr(condensation, "HKL_BAND_SAMPLES", 2)
    strip_conductance = 1.0e-4

    def two_strip_flux(_species, pressure, temperature_K, _alpha, **kwargs):
        psat = 0.0 if temperature_K < 274.15 else 90.0
        kwargs["diagnostic_out"].update(
            wall_saturation_pressure_pa=psat,
            wall_saturation_pressure_refused=False,
        )
        return strip_conductance * max(0.0, pressure - psat)

    monkeypatch.setattr(
        condensation,
        "_series_resistance_deposition_flux_mol_m2_s",
        two_strip_flux,
    )
    pressure_in_pa = 100.0
    residence_s = 4.0
    available_kg = 1.0
    eta = model._condensation_efficiency(
        stage=stage,
        species="Fe",
        T_cond_C=1050.0,
        residence_s=residence_s,
        available_kg=available_kg,
        alpha_s_value=0.5,
        inlet_partial_pressure_pa=pressure_in_pa,
    )
    assert eta.hex() == "0x1.459050b43bb4fp-1"

    molar_mass_kg_mol = condensation.MOLAR_MASS["Fe"] / 1000.0
    inventory_mol = available_kg / molar_mass_kg_mol / 3600.0 * residence_s
    area = model._stage_area_m2_for_stage(stage)
    pressure_scale_pa_per_mol = pressure_in_pa / inventory_mol
    strips = ((0.0, strip_conductance), (90.0, strip_conductance))

    def pressure_derivative(pressure_pa):
        return -pressure_scale_pa_per_mol * sum(
            conductance
            * area
            / len(strips)
            * max(pressure_pa - psat, 0.0)
            for psat, conductance in strips
        )

    pressure_pa = pressure_in_pa
    step_s = residence_s / 10_000
    for _ in range(10_000):
        k1 = pressure_derivative(pressure_pa)
        k2 = pressure_derivative(pressure_pa + step_s * k1 / 2)
        k3 = pressure_derivative(pressure_pa + step_s * k2 / 2)
        k4 = pressure_derivative(pressure_pa + step_s * k3)
        pressure_pa += step_s * (k1 + 2 * k2 + 2 * k3 + k4) / 6
    numerical_eta = (pressure_in_pa - pressure_pa) / pressure_in_pa

    assert pressure_pa < 45.0
    assert eta == pytest.approx(numerical_eta, rel=1e-8)


def test_isothermal_band_matches_midpoint_excess_inventory_capture(monkeypatch):
    model = _configured_model()
    stage = model.train.stages[3]
    stage.temp_range_C = (1000.0, 1000.0)
    evaluated_K = []

    def constant_flux(_species, _pressure, temperature_K, _alpha, **kwargs):
        evaluated_K.append(temperature_K)
        kwargs["diagnostic_out"]["wall_saturation_pressure_pa"] = 25.0
        kwargs["diagnostic_out"]["wall_saturation_pressure_refused"] = False
        return 0.02

    monkeypatch.setattr(
        condensation, "_series_resistance_deposition_flux_mol_m2_s", constant_flux,
    )
    eta = model._condensation_efficiency(
        stage=stage, species="Fe", T_cond_C=1050.0, residence_s=4.0,
        available_kg=1.0, alpha_s_value=0.5,
        inlet_partial_pressure_pa=100.0,
    )

    assert len(evaluated_K) == 1
    assert evaluated_K[0] == pytest.approx(1273.15)
    molar_mass = (
        condensation._molecular_mass_kg_per_molecule("Fe")
        * condensation.AVOGADRO_MOL
    )
    inventory = 1.0 / molar_mass / 3600.0 * 4.0
    expected = _integrated_pressure_driven_capture(
        pressure=100.0, psat=25.0,
        temperature=model.gas_temperature_C + 273.15,
        coefficient=0.02 / 75.0,
        area=model._stage_area_m2_for_stage(stage),
        inventory=inventory, residence=4.0,
    )
    assert eta == pytest.approx(expected, rel=1e-8)


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
    assert result.condensed_by_stage_species == {1: {"Fe": 0.5}}
    assert result.remaining_by_species["Fe"] == pytest.approx(0.5)
    assert seen == [(1, 1.0, 100.0)]


def test_hot_sio_passes_to_its_reactive_product_stage():
    model = _configured_model()
    model.pipe_segments = []
    model.species_partial_pressures_mbar = {"SiO": 1.0}
    model.wall_species_partial_pressures_pa = {"SiO": 100.0}
    result = model.route(EvaporationFlux(species_kg_hr={"SiO": 1.0}), MeltState(temperature_C=1700.0))
    assert "SiO" not in result.condensed_by_stage_species.get(1, {})
    assert "SiO" not in result.condensed_by_stage_species.get(2, {})
    assert "SiO" not in result.condensed_by_stage_species.get(3, {})


def test_unavailable_wall_pressure_preserves_flow_with_typed_refusal(monkeypatch):
    model = _configured_model()
    model.pipe_segments = []
    model.species_partial_pressures_mbar = {"Na": 1.0}
    def unavailable(species, pressure, temperature, **kwargs):
        raise condensation.WallSaturationPressureRefusal(species, temperature, "saturation_pressure_unavailable")

    monkeypatch.setattr(condensation, "_wall_deposition_driving_pressure_pa", unavailable)
    result = model.route(EvaporationFlux(species_kg_hr={"Na": 1.0}), MeltState(temperature_C=1700.0))
    # Missing capture pressure cannot undo the independently supplied vapor.
    # With no upstream verdict, neither capture nor terminal offgas is certified.
    assert result.retained_in_source_by_species == {}
    assert result.remaining_by_species["Na"] == 1.0
    authority = result.condensation_authority_by_species["Na"]
    assert authority["status"] == "missing"
    assert authority["authoritative_for_condensation"] is False
    assert authority["authoritative_for_terminal_offgas"] is False
    assert authority["condensed_mass_kg_hr"] == 0.0
    assert authority["mass_closure_error_kg_hr"] == 0.0
    refusal = result.condensation_refusals_by_species["Na"]
    assert refusal["status"] == "refused"
    refused_stage = next(item for item in refusal["stage_outcomes"] if item["status"] == "refused")
    assert refused_stage["eta"] is None
    assert refused_stage["refusal_type"] == "WallSaturationPressureRefusal"


def _published_neufeld_omega_d(reduced_temperature: float) -> float:
    """Neufeld, Janzen and Aziz, J. Chem. Phys. 57, 1100 (1972).

    The collision-integral correlation, written from the published
    coefficients. This is not a call to the production helper.
    """
    t_star = reduced_temperature
    return (
        1.06036 / t_star ** 0.1561
        + 0.193 / math.exp(0.47635 * t_star)
        + 1.03587 / math.exp(1.52996 * t_star)
        + 1.76474 / math.exp(3.89411 * t_star)
    )


def _hand_chapman_enskog_temperature_ratio(
    temperature_film_K: float,
    temperature_bulk_K: float,
    *,
    species: str,
    carrier: str,
) -> tuple[float, float]:
    """D(T_film)/D(T_bulk) from T^1.5 and the published Ω_D.

    Lennard-Jones ε/k comes from the condensation table (the data owner).
    ε_AB = sqrt(ε_A * ε_B). D ∝ T^1.5 / Ω_D(T/ε_AB) at fixed pressure.
    """
    species_row = condensation._LENNARD_JONES_PARAMS[species]
    carrier_key = condensation._canonical_carrier_gas_key(carrier)
    carrier_row = condensation._LENNARD_JONES_PARAMS[carrier_key]
    epsilon_ab_K = math.sqrt(species_row[1] * carrier_row[1])
    omega_film = _published_neufeld_omega_d(temperature_film_K / epsilon_ab_K)
    omega_bulk = _published_neufeld_omega_d(temperature_bulk_K / epsilon_ab_K)
    power = (temperature_film_K / temperature_bulk_K) ** 1.5
    return power, power * omega_bulk / omega_film


def test_stage_diffusivity_uses_film_temperature(monkeypatch):
    model = _configured_model()
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Na": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1450.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    stage = next(
        item for item in model.train.stages
        if getattr(item, "stage_key", "") == condensation.NA_COLLECTOR_STAGE_KEY
    )
    seen: list[tuple[str, float, float, float]] = []
    original = condensation._chapman_enskog_d_ab_m2_s

    def spy(species, temperature_K, pressure_pa, carrier="N2", **kwargs):
        value = original(
            species, temperature_K, pressure_pa, carrier=carrier, **kwargs,
        )
        seen.append((species, float(temperature_K), float(pressure_pa), float(value)))
        return value

    monkeypatch.setattr(condensation, "_chapman_enskog_d_ab_m2_s", spy)
    eta = model._condensation_efficiency(
        stage=stage,
        species="Na",
        T_cond_C=883.0,
        residence_s=3.0,
        available_kg=0.01,
        alpha_s_value=1.0,
        inlet_partial_pressure_pa=10.0,
    )

    assert eta > 0.0
    gas_K = model.gas_temperature_C + condensation.CELSIUS_TO_KELVIN_OFFSET
    wall_K = condensation._stage_midpoint_temperature_K(stage)
    film_K = condensation._stage_film_temperature_K(gas_K, wall_K)
    assert gas_K == pytest.approx(1723.15)
    assert wall_K == pytest.approx(523.15)
    assert film_K == pytest.approx(1123.15)
    assert seen
    assert {row[0] for row in seen} == {"Na"}
    assert all(row[1] == pytest.approx(film_K) for row in seen)
    power, expected_ratio = _hand_chapman_enskog_temperature_ratio(
        film_K, gas_K, species="Na", carrier=model.carrier_gas,
    )
    # Controller sanity, Celsius offset omitted: (1123/1723)^1.5 = 0.526.
    assert (1123.0 / 1723.0) ** 1.5 == pytest.approx(0.526, abs=5e-4)
    assert power == pytest.approx((1123.15 / 1723.15) ** 1.5)
    produced = seen[0][3] / original(
        "Na", gas_K, seen[0][2], carrier=model.carrier_gas,
    )
    # Na–N2 ε/k is 1375 K, so Ω_D is not flat between 1123 K and 1723 K.
    # The published collision integral carries the whole residual off T^1.5.
    # The wall-only power (523.15/1723.15)^1.5 is about 0.167 and is not used.
    assert produced == pytest.approx(expected_ratio, rel=1e-12)
    assert produced / power == pytest.approx(expected_ratio / power, rel=1e-12)
    assert (wall_K / gas_K) ** 1.5 < 0.2
    assert produced > 2.0 * (wall_K / gas_K) ** 1.5


def test_stage_band_uses_each_sample_wall_in_the_film_temperature(monkeypatch):
    model = _configured_model()
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"SiO": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1450.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    stage = model.train.stages[3]
    stage.temp_range_C = (900.0, 1200.0)
    seen: list[float] = []
    original = condensation._chapman_enskog_d_ab_m2_s

    def spy(species, temperature_K, pressure_pa, carrier="N2", **kwargs):
        seen.append(float(temperature_K))
        return original(
            species, temperature_K, pressure_pa, carrier=carrier, **kwargs,
        )

    def saturated_nowhere(_species, pressure_pa, _temperature_K, **kwargs):
        diagnostic = kwargs.get("diagnostic_out")
        if diagnostic is not None:
            diagnostic["wall_saturation_pressure_pa"] = 0.0
            diagnostic["wall_saturation_pressure_refused"] = False
        return float(pressure_pa)

    monkeypatch.setattr(condensation, "_chapman_enskog_d_ab_m2_s", spy)
    monkeypatch.setattr(
        condensation, "_wall_deposition_driving_pressure_pa", saturated_nowhere,
    )
    eta = model._condensation_efficiency(
        stage=stage,
        species="SiO",
        T_cond_C=1200.0,
        residence_s=4.0,
        available_kg=1.0,
        alpha_s_value=0.5,
        inlet_partial_pressure_pa=100.0,
    )

    gas_K = model.gas_temperature_C + condensation.CELSIUS_TO_KELVIN_OFFSET
    lo_C, hi_C = condensation._stage_temp_band_C(stage)
    width_C = hi_C - lo_C
    expected = [
        condensation._stage_film_temperature_K(
            gas_K,
            lo_C + width_C * (sample + 0.5) / condensation.HKL_BAND_SAMPLES
            + condensation.CELSIUS_TO_KELVIN_OFFSET,
        )
        for sample in range(condensation.HKL_BAND_SAMPLES)
    ]
    assert eta > 0.0
    assert len(seen) == condensation.HKL_BAND_SAMPLES
    assert seen == pytest.approx(expected)
    assert min(seen) < max(seen)


def test_omitted_diffusivity_temperature_stays_at_bulk_gas(monkeypatch):
    seen: list[float] = []
    original = condensation._chapman_enskog_d_ab_m2_s

    def spy(species, temperature_K, pressure_pa, carrier="N2", **kwargs):
        seen.append(float(temperature_K))
        return original(
            species, temperature_K, pressure_pa, carrier=carrier, **kwargs,
        )

    monkeypatch.setattr(condensation, "_chapman_enskog_d_ab_m2_s", spy)
    bulk_K = 1700.0 + condensation.CELSIUS_TO_KELVIN_OFFSET
    condensation._series_resistance_deposition_flux_mol_m2_s(
        "Na",
        10.0,
        523.15,
        0.4,
        pipe_diameter_m=0.02,
        T_gas_K=bulk_K,
        overhead_pressure_pa=1000.0,
        equilibrium_pressure_pa=0.0,
        regime_factor=0.0,
        carrier_gas="N2",
    )
    assert seen == pytest.approx([bulk_K])


def test_mass_transfer_denominator_stays_at_bulk_temperature():
    bulk_K = 1450.0 + condensation.CELSIUS_TO_KELVIN_OFFSET
    wall_K = 250.0 + condensation.CELSIUS_TO_KELVIN_OFFSET
    film_K = condensation._stage_film_temperature_K(bulk_K, wall_K)
    overhead_pa = 1000.0
    partial_pa = 10.0
    alpha = 0.4
    diameter_m = 0.02
    diffusivity = condensation._chapman_enskog_d_ab_m2_s(
        "Na", film_K, overhead_pa, carrier="N2",
    )
    sherwood = condensation._stirring_enhanced_sherwood(
        1.0, radial_stir_factor=1.0,
    )
    molar_mass = condensation.MOLAR_MASS["Na"] / 1000.0
    k_hkl = alpha / math.sqrt(
        2.0 * math.pi * molar_mass * condensation.GAS_CONSTANT_J_MOL_K * bulk_K
    )
    k_mt_bulk = (
        sherwood * diffusivity
        / (diameter_m * condensation.GAS_CONSTANT_J_MOL_K * bulk_K)
    )
    k_mt_film = (
        sherwood * diffusivity
        / (diameter_m * condensation.GAS_CONSTANT_J_MOL_K * film_K)
    )
    at_bulk_denominator = partial_pa / (1.0 / k_hkl + 1.0 / k_mt_bulk)
    at_film_denominator = partial_pa / (1.0 / k_hkl + 1.0 / k_mt_film)
    flux = condensation._series_resistance_deposition_flux_mol_m2_s(
        "Na",
        partial_pa,
        wall_K,
        alpha,
        pipe_diameter_m=diameter_m,
        stir_factor=1.0,
        radial_stir_factor=1.0,
        regime_factor=0.0,
        T_gas_K=bulk_K,
        overhead_pressure_pa=overhead_pa,
        carrier_gas="N2",
        equilibrium_pressure_pa=0.0,
        diffusivity_temperature_K=film_K,
    )
    assert flux == pytest.approx(at_bulk_denominator, rel=1e-12)
    assert flux != pytest.approx(at_film_denominator, rel=1e-4)


def test_film_temperature_refuses_a_non_positive_temperature():
    with pytest.raises(condensation.DepositionInputRefusal, match="T_gas_K"):
        condensation._stage_film_temperature_K(0.0, 523.15)
    with pytest.raises(condensation.DepositionInputRefusal, match="T_surface_K"):
        condensation._stage_film_temperature_K(1723.15, -1.0)
    with pytest.raises(
        condensation.DepositionInputRefusal, match="diffusivity_temperature_K",
    ):
        condensation._series_resistance_deposition_flux_mol_m2_s(
            "Na",
            10.0,
            523.15,
            0.4,
            pipe_diameter_m=0.02,
            T_gas_K=1723.15,
            overhead_pressure_pa=1000.0,
            equilibrium_pressure_pa=0.0,
            regime_factor=0.0,
            diffusivity_temperature_K=0.0,
        )
