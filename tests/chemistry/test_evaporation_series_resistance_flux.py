"""Source-side series-resistance evaporation flux tests."""

from __future__ import annotations

import math

import pytest

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    EvaporationFluxConfigurationError,
    _series_resistance_evaporation_flux_kg_m2_s,
)
from simulator.accounting.formulas import resolve_species_formula
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import (
    DEFAULT_BINARY_DIFFUSION_M2_S,
    GAS_CONSTANT_J_MOL_K,
    _chapman_enskog_d_ab_m2_s,
    _series_resistance_deposition_flux_mol_m2_s,
)
import simulator.evaporation as evaporation_module
from simulator.state import (
    Atmosphere,
    CampaignPhase,
    MAX_STIR_FACTOR,
    EvaporationFlux,
    MeltState,
)
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN
from simulator.overhead import (
    OverheadGasModel,
    _solve_same_hour_headspace_closure,
)
from tests.chemistry.conftest import _build_sim


_K_BASE = {
    "species": "K",
    "P_eq_pa": 80.0,
    "P_bulk_pa": 5.0,
    "T_surface_K": 1800.0,
    "molar_mass_kg_mol": 0.0390983,
    "alpha_i": 0.13,
    "knudsen_number": 1.0,
    "pipe_diameter_m": 0.12,
    "overhead_pressure_pa": 1000.0,
    "axial_stir_factor": 1.0,
    "radial_stir_factor": 1.0,
    "cold_skull_envelope": {"frozen_skull_stir_ceiling": MAX_STIR_FACTOR},
    "carrier_gas": "N2",
    "T_gas_K": 1800.0,
    "melt_resistance_enabled": False,
}


def _evap(**overrides):
    kwargs = dict(_K_BASE)
    kwargs.update(overrides)
    return _series_resistance_evaporation_flux_kg_m2_s(**kwargs)


def test_free_molecular_limit_recovers_intrinsic_alpha_hk():
    result = _evap(
        knudsen_number=FREE_MOLECULAR_KNUDSEN_MIN * 1000.0,
        gas_resistance_enabled=False,
        melt_resistance_enabled=False,
    )

    delta_p = _K_BASE["P_eq_pa"] - _K_BASE["P_bulk_pa"]
    k_hk = math.sqrt(
        _K_BASE["molar_mass_kg_mol"]
        / (2.0 * math.pi * GAS_CONSTANT_J_MOL_K * _K_BASE["T_surface_K"])
    )
    expected = _K_BASE["alpha_i"] * delta_p * k_hk

    assert result.r_gas == 0.0
    assert result.r_melt == 0.0
    assert result.flux_kg_s_m2 == pytest.approx(expected, rel=1e-12)
    assert result.alpha_effective == pytest.approx(_K_BASE["alpha_i"], rel=1e-12)


@pytest.mark.parametrize(
    ("species", "p_eq_pa", "molar_mass", "alpha", "expected_kg_hr"),
    [
        ("Fe", 78.0238, 0.055845, 0.02, 0.816697),
        ("SiO", 21.7234, 0.044084, 0.52 * math.exp(-3685.0 / 2023.15), 0.849869),
    ],
)
def test_1750c_true_vacuum_recovers_rederived_hkl_upper_bound(
    species, p_eq_pa, molar_mass, alpha, expected_kg_hr
):
    result = _evap(
        species=species,
        P_eq_pa=p_eq_pa,
        P_bulk_pa=0.0,
        T_surface_K=2023.15,
        T_gas_K=2023.15,
        molar_mass_kg_mol=molar_mass,
        alpha_i=alpha,
        knudsen_number=None,
        overhead_pressure_pa=0.0,
        axial_stir_factor=6.0,
        gas_resistance_enabled=True,
    )

    assert result.r_gas == 0.0
    assert result.r_melt == 0.0
    assert result.flux_kg_s_m2 * 0.2 * 3600.0 == pytest.approx(
        expected_kg_hr, rel=5e-5
    )
    diag = result.as_diagnostic()
    assert diag["authority_class"] == "upper-bound"
    assert diag["authority_reason"] == (
        "missing-species-state-dependent-melt-transfer-inputs"
    )


def test_continuum_limit_is_transport_limited_by_gas_resistance():
    result = _evap(
        alpha_i=1.0,
        knudsen_number=1.0e-7,
        radial_stir_factor=1.0,
        melt_resistance_enabled=False,
    )

    delta_p = _K_BASE["P_eq_pa"] - _K_BASE["P_bulk_pa"]
    assert result.r_gas > result.r_interface * 50.0
    assert result.gas_resistance_weight == pytest.approx(1.0, rel=1e-5)
    assert result.flux_kg_s_m2 == pytest.approx(delta_p / result.r_gas, rel=0.02)


def test_helium_carrier_changes_chapman_enskog_gas_resistance():
    nitrogen = _evap(
        carrier_gas="N2",
        knudsen_number=1.0e-7,
        melt_resistance_enabled=False,
    )
    helium = _evap(
        carrier_gas="He",
        knudsen_number=1.0e-7,
        melt_resistance_enabled=False,
    )

    assert helium.d_ab_m2_s > nitrogen.d_ab_m2_s
    assert helium.r_gas < nitrogen.r_gas


def test_alpha_effective_never_exceeds_intrinsic_alpha_across_kn_and_stir():
    kn_values = [0.0, 1.0e-7, 0.01, 0.1, 1.0, 10.0, math.inf]
    stir_values = [0.0, 1.0, MAX_STIR_FACTOR, 1000.0]

    for alpha in (0.02, 0.13, 1.0):
        for kn in kn_values:
            for axial in stir_values:
                for radial in stir_values:
                    result = _evap(
                        alpha_i=alpha,
                        knudsen_number=kn,
                        axial_stir_factor=axial,
                        radial_stir_factor=radial,
                    )
                    assert 0.0 <= result.alpha_effective <= alpha + 1e-15


def test_resistances_move_monotonically_with_kn_and_stir_axes():
    gas_by_kn = [
        _evap(
            knudsen_number=kn,
            radial_stir_factor=1.0,
            melt_resistance_enabled=False,
        ).r_gas
        for kn in (1.0e-7, 1.0e-4, 0.01, 0.1, 1.0, 10.0, math.inf)
    ]
    assert gas_by_kn == sorted(gas_by_kn, reverse=True)

    melt_by_axial = [
        _evap(
            axial_stir_factor=axial,
            gas_resistance_enabled=False,
        ).r_melt
        for axial in (0.0, 1.0, 4.0, MAX_STIR_FACTOR, 1000.0)
    ]
    assert melt_by_axial == [melt_by_axial[0]] * len(melt_by_axial)
    assert melt_by_axial == [0.0] * 5

    gas_by_radial = [
        _evap(
            knudsen_number=1.0e-7,
            radial_stir_factor=radial,
            melt_resistance_enabled=False,
        ).r_gas
        for radial in (0.0, 1.0, 4.0, MAX_STIR_FACTOR, 1000.0)
    ]
    assert gas_by_radial == sorted(gas_by_radial, reverse=True)


def test_axial_stir_does_not_multiply_hkl_or_add_unphysical_melt_resistance():
    static = _evap(axial_stir_factor=0.0, gas_resistance_enabled=False)
    stirred = _evap(axial_stir_factor=MAX_STIR_FACTOR, gas_resistance_enabled=False)

    assert static.r_melt == 0.0
    assert stirred.r_melt == 0.0
    assert static.flux_kg_s_m2 == pytest.approx(stirred.flux_kg_s_m2, rel=1e-12)
    assert static.alpha_effective == pytest.approx(_K_BASE["alpha_i"], rel=1e-12)


def test_universal_melt_renewal_conductance_is_uncertified_and_refused():
    with pytest.raises(
        EvaporationFluxConfigurationError,
        match="requires species- and state-specific",
    ):
        _evap(
            melt_resistance_enabled=True,
            melt_surface_renewal_base_kg_s_m2_pa=1.0e-4,
            gas_resistance_enabled=False,
        )


def test_transition_and_free_molecular_regimes_do_not_apply_continuum_gas_film():
    transitional = _evap(knudsen_number=1.0)
    free_molecular = _evap(knudsen_number=FREE_MOLECULAR_KNUDSEN_MIN)

    assert transitional.r_gas == 0.0
    assert free_molecular.r_gas == 0.0
    assert transitional.gas_resistance_weight == 0.0
    assert free_molecular.gas_resistance_weight == 0.0


def test_missing_chapman_enskog_parameters_do_not_fall_back_to_constant():
    with pytest.raises(
        EvaporationFluxConfigurationError,
        match="missing Chapman-Enskog transport parameters.*species='H2O'",
    ):
        _evap(
            species="H2O",
            molar_mass_kg_mol=0.01801528,
            knudsen_number=1.0e-7,
        )

    free_molecular = _evap(
        species="H2O",
        molar_mass_kg_mol=0.01801528,
        knudsen_number=math.inf,
        melt_resistance_enabled=False,
    )
    assert free_molecular.flux_kg_s_m2 > 0.0
    assert free_molecular.r_gas == 0.0


def test_cro2_class_proxy_preserves_chapman_enskog_scaling():
    base = _evap(
        species="CrO2",
        molar_mass_kg_mol=0.0839941,
        knudsen_number=1.0e-7,
        melt_resistance_enabled=False,
    )
    double_pressure = _evap(
        species="CrO2",
        molar_mass_kg_mol=0.0839941,
        knudsen_number=1.0e-7,
        overhead_pressure_pa=2000.0,
        melt_resistance_enabled=False,
    )
    hotter = _evap(
        species="CrO2",
        molar_mass_kg_mol=0.0839941,
        knudsen_number=1.0e-7,
        T_surface_K=2400.0,
        T_gas_K=2400.0,
        melt_resistance_enabled=False,
    )

    assert base.d_ab_m2_s > 0.0
    assert double_pressure.d_ab_m2_s == pytest.approx(
        base.d_ab_m2_s / 2.0, rel=1e-12
    )
    assert hotter.d_ab_m2_s > base.d_ab_m2_s


def test_cro2_evaporation_proxy_absolute_diffusivity_pin():
    result = _evap(
        species="CrO2",
        molar_mass_kg_mol=0.0839941,
        knudsen_number=1.0e-7,
        T_surface_K=1973.0,
        T_gas_K=1973.0,
        melt_resistance_enabled=False,
    )

    # sigma_AB=(3.374+3.798)/2=3.586 A; M_AB=42.0150 g/mol;
    # T*=1973/71.4=27.6331; Omega_D=0.631606. The BSL atmosphere
    # prefactor (0.0018583*sqrt(2)) gives 443.270 cm2/s = 0.0443270 m2/s
    # at 1973 K and 1000 Pa. The previous 0.0448662 m2/s pin used the
    # bar-form 0.00266 constant with P in atm (1.216% high).
    assert result.d_ab_m2_s == pytest.approx(0.044327038311009044, rel=1e-12)


def test_cro2_evaporation_proxy_uses_shared_sio_collision_parameters():
    from simulator.condensation import _LENNARD_JONES_PARAMS

    sio_sigma, sio_epsilon, _sio_molar_mass = _LENNARD_JONES_PARAMS["SiO"]
    expected = _chapman_enskog_d_ab_m2_s(
        "CrO2",
        1973.0,
        1000.0,
        carrier="N2",
        species_params=(sio_sigma, sio_epsilon, 83.9941),
    )
    result = _evap(
        species="CrO2",
        molar_mass_kg_mol=0.0839941,
        knudsen_number=1.0e-7,
        T_surface_K=1973.0,
        T_gas_K=1973.0,
        melt_resistance_enabled=False,
    )

    assert result.d_ab_m2_s == pytest.approx(expected, rel=1e-12)


def test_chapman_enskog_prefactor_matches_bsl_atmosphere_form():
    """Default condensation D_AB uses the BSL atm prefactor, not 0.00266.

    SiO/N2 at the documented 10 mbar, 1700 C anchor. Reverting the
    prefactor to 0.00266 restores 0.04969275549781849 m2/s.
    """
    sio_n2 = _chapman_enskog_d_ab_m2_s("SiO", 1973.15, 1000.0, carrier="N2")
    assert sio_n2 == pytest.approx(0.049095565578101914, rel=1e-12)

    # O2 self-diffusion at 1 atm, 1000 K against NIST TN 2279
    # (Hellmann O2-O2 fit, valid 55-2000 K). Chapman-Enskog with the
    # BSL Table E.1 Lennard-Jones row is several percent low; 10% is
    # the method band, not a coefficient to retune.
    o2 = _chapman_enskog_d_ab_m2_s("O2", 1000.0, 101325.0, carrier="O2")
    nist_m2_s = math.exp(-10.787 - 44.220 / 1000.0 + 1.649 * math.log(1000.0)) * 1.0e-4
    assert o2 == pytest.approx(nist_m2_s, rel=0.10)
    assert o2 < nist_m2_s


@pytest.mark.xfail(
    strict=True,
    reason=(
        "missing authority: WallSaturationPressureRefusal "
        "reason=antoine_data_unavailable; CrO2 has no executable "
        "Antoine/Chapman-Enskog wall-saturation input, so deposition must "
        "refuse rather than invent DEFAULT_BINARY_DIFFUSION_M2_S"
    ),
)
def test_cro2_condensation_transport_uses_documented_default_fallback():
    helper_result = _chapman_enskog_d_ab_m2_s("CrO2", 1973.0, 1000.0)
    deposition_inputs = {
        "species": "CrO2",
        "P_local_pa": 1.0e5,
        "T_surface_K": 1973.0,
        "alpha_s": 1.0,
        "regime_factor": 0.0,
        "T_gas_K": 1973.0,
        "reactive_product_backstop": False,
    }
    implicit_fallback = _series_resistance_deposition_flux_mol_m2_s(
        **deposition_inputs
    )
    failed_ce_fallback = _series_resistance_deposition_flux_mol_m2_s(
        **deposition_inputs,
        overhead_pressure_pa=1000.0,
    )

    assert helper_result == 0.0
    assert DEFAULT_BINARY_DIFFUSION_M2_S == pytest.approx(1.0e-2)
    assert failed_ce_fallback == pytest.approx(implicit_fallback, rel=1e-12)


def test_anti_exploit_stir_bounds_and_defensive_clamps():
    max_axial = _evap(
        axial_stir_factor=MAX_STIR_FACTOR,
        gas_resistance_enabled=False,
    )
    for axial in (11.0, 1000.0):
        assert _evap(
            axial_stir_factor=axial,
            gas_resistance_enabled=False,
        ).flux_kg_s_m2 == pytest.approx(max_axial.flux_kg_s_m2, rel=1e-12)

    max_radial = _evap(
        knudsen_number=1.0e-7,
        radial_stir_factor=MAX_STIR_FACTOR,
        melt_resistance_enabled=False,
    )
    for radial in (11.0, 1000.0):
        assert _evap(
            knudsen_number=1.0e-7,
            radial_stir_factor=radial,
            melt_resistance_enabled=False,
        ).flux_kg_s_m2 == pytest.approx(max_radial.flux_kg_s_m2, rel=1e-12)

    for bad in (-1.0, float("nan"), float("inf"), True):
        with pytest.raises(
            EvaporationFluxConfigurationError,
            match="axial_stir_factor must be finite and non-negative",
        ):
            _evap(axial_stir_factor=bad, gas_resistance_enabled=False)
        with pytest.raises(
            EvaporationFluxConfigurationError,
            match="radial_stir_factor must be finite and non-negative",
        ):
            _evap(
                radial_stir_factor=bad,
                knudsen_number=1.0e-7,
                melt_resistance_enabled=False,
            )


@pytest.mark.parametrize("p_bulk", [_K_BASE["P_eq_pa"], _K_BASE["P_eq_pa"] * 2.0])
def test_double_count_guard_zeroes_nonpositive_driving_pressure(p_bulk):
    result = _evap(P_bulk_pa=p_bulk)

    assert result.flux_kg_s_m2 == 0.0


def test_grounded_cr_alpha_uses_series_resistance_path_without_fallback():
    provider = BuiltinEvaporationFluxProvider()
    view = ProviderAccountView(
        accounts={"process.cleaned_melt": {"Cr2O3": 10.0}},
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.EVAPORATION_FLUX,
        account_view=view,
        temperature_C=1700.0,
        pressure_bar=1.0e-6,
        fO2_log=None,
        control_inputs={
            "vapour_batch_flux_pressures_Pa": {"Cr": 100.0},
            "overhead_partials_Pa": {"Cr": 0.0},
            "overhead_pressure_pa": 0.0,
            "molar_mass_kg_mol": {"Cr": 0.052},
            "stoich_by_species": {
                "Cr": {
                    "parent_oxide": "Cr2O3",
                    "oxide_per_product_kg": 1.0,
                    "O2_per_product_kg": 0.0,
                }
            },
            "available_oxide_kg": {"Cr": 10.0},
            "melt_surface_area_m2": 1.0,
            "stir_factor": {"axial": 1000.0, "radial": 1000.0},
            "alpha": {"Cr": 0.9},
        },
    )

    result = provider.dispatch(request)

    assert result.status == "ok"
    assert result.diagnostic["evaporation_flux_kg_hr"]["Cr"] > 0.0
    assert result.diagnostic["alpha_used_by_species"]["Cr"] == pytest.approx(0.9)
    assert "missing_alpha" not in result.diagnostic
    assert "unmeasured_alpha_fallback_species" not in result.diagnostic


def _self_pumped_case(species, *, pipe_diameter_m=0.12):
    molar_mass = resolve_species_formula(species, {}).molar_mass_kg_per_mol()
    area_m2 = 0.2
    model = OverheadGasModel({"enabled": True})
    model.pipe_diameter_m = pipe_diameter_m
    model.pipe_length_m = 1.0
    melt = MeltState()
    melt.temperature_C = 1526.85
    melt.p_total_mbar = 100.0

    def project_partials(rates_kg_hr):
        candidate = EvaporationFlux(species_kg_hr=dict(rates_kg_hr))
        candidate.update_totals()
        transport = model.estimate_transport_state(candidate, melt)
        return model.species_partial_pressures(
            candidate,
            transport["vapor_pressure_mbar"],
            model.species_formula_registry,
        )

    def flux_at_partials(partials_mbar):
        series = _evap(
            species=species,
            P_eq_pa=80.0,
            P_bulk_pa=100.0 * float(partials_mbar.get(species, 0.0)),
            T_surface_K=1800.0,
            T_gas_K=1800.0,
            molar_mass_kg_mol=molar_mass,
            alpha_i=0.13,
            knudsen_number=math.inf,
            gas_resistance_enabled=False,
        )
        return {species: series.flux_kg_s_m2 * area_m2 * 3600.0}

    vacuum = flux_at_partials({})
    return model, melt, area_m2, vacuum, project_partials, flux_at_partials


def _solve_self_pumped_case(species, *, pipe_diameter_m=0.12):
    model, melt, area_m2, vacuum, project, flux_at = _self_pumped_case(
        species, pipe_diameter_m=pipe_diameter_m
    )
    result = _solve_same_hour_headspace_closure(
        vacuum,
        flux_at,
        project,
    )
    return model, melt, area_m2, result


def test_constant_temperature_multihour_closure_does_not_alternate():
    for species in ("Cr", "CrO", "Fe", "SiO", "Na", "K", "Mg"):
        hourly_fluxes = []
        for hour in range(8):
            # Stale end-of-hour pressures alternate deliberately; each hour
            # supplies the corresponding extreme initial rate, while the
            # fixed point closes against its own evolved-rate projection.
            _model, _melt, _area_m2, vacuum, project, flux_at = _self_pumped_case(
                species
            )
            stale_bulk_mbar = 0.0 if hour % 2 == 0 else 1.6
            result = _solve_same_hour_headspace_closure(
                vacuum if stale_bulk_mbar == 0.0 else flux_at({species: stale_bulk_mbar}),
                flux_at,
                project,
            )
            assert result.converged, species
            hourly_fluxes.append(result.rates_kg_hr[species])

        assert all(value > 0.0 for value in hourly_fluxes), species
        for left, right in zip(hourly_fluxes, hourly_fluxes[1:]):
            assert max(left, right) / min(left, right) <= 1.01, species


def test_projected_pressure_carries_the_converged_evolved_rate():
    model, melt, _area_m2, result = _solve_self_pumped_case("K")

    candidate = EvaporationFlux(species_kg_hr=result.rates_kg_hr)
    candidate.update_totals()
    transport = model.estimate_transport_state(candidate, melt)
    projected = model.species_partial_pressures(
        candidate,
        transport["vapor_pressure_mbar"],
        model.species_formula_registry,
    )

    assert result.converged
    assert result.iterations > 0
    assert result.partial_pressures_mbar["K"] == pytest.approx(
        projected["K"], rel=1e-9
    )
    assert result.partial_pressures_mbar["K"] < 0.8

    # For the same mixture and throughput, Poiseuille inversion gives
    # P ∝ d^-2 (flow ∝ d^4 P^2); doubling pipe diameter quarters pressure.
    wider = OverheadGasModel({"enabled": True})
    wider.pipe_diameter_m = 2.0 * model.pipe_diameter_m
    wider.pipe_length_m = model.pipe_length_m
    wider_transport = wider.estimate_transport_state(candidate, melt)
    assert wider_transport["vapor_pressure_mbar"] == pytest.approx(
        transport["vapor_pressure_mbar"] / 4.0, rel=1e-9
    )


def test_pipe_geometry_limits_recover_vacuum_and_suppress_closed_line_flux():
    species = "K"
    _model, _melt, _area_m2, vacuum, _project, _flux_at = _self_pumped_case(
        species
    )
    _open_model, _open_melt, _open_area, open_result = _solve_self_pumped_case(
        species, pipe_diameter_m=1.2e6
    )
    closed_model, closed_melt, _closed_area, closed_result = _solve_self_pumped_case(
        species, pipe_diameter_m=1.2e-5
    )

    assert open_result.converged
    assert open_result.rates_kg_hr[species] == pytest.approx(
        vacuum[species], rel=1e-9
    )
    assert closed_result.rates_kg_hr[species] < vacuum[species] * 1e-8
    closed_candidate = EvaporationFlux(species_kg_hr=closed_result.rates_kg_hr)
    closed_candidate.update_totals()
    closed_pressure = closed_model.estimate_transport_state(
        closed_candidate, closed_melt
    )["vapor_pressure_mbar"]
    assert closed_pressure >= 80.0 / 100.0


def test_closed_line_subboiling_root_is_recognized_without_iteration():
    equilibrium_pressures = {"A": 40.0, "B": 39.0}

    def unexpected_evaluation(_values):
        raise AssertionError("zero-boundary root must not iterate")

    result = _solve_same_hour_headspace_closure(
        {"A": 1.0, "B": 2.0},
        unexpected_evaluation,
        unexpected_evaluation,
        closed_line_equilibrium_pressures_Pa=equilibrium_pressures,
        closed_line_total_pressure_Pa=80.0,
    )

    assert result.converged
    assert result.iterations == 0
    assert result.rates_kg_hr == {"A": 0.0, "B": 0.0}


def _pressure_held_sim(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    gate_factor=1.0,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1670.0
    sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
    sim.melt.p_total_mbar = 1.0
    sim._overhead_headspace_config["downstream_pressure_bar"] = 0.001
    sim._freeze_gate_enabled = lambda: gate_factor != 1.0
    sim._freeze_gate_liquid_fraction_factor = lambda **_kwargs: gate_factor
    return sim


def test_pressure_held_liquid_fraction_is_applied_once(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _pressure_held_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        gate_factor=0.5,
    )

    flux = sim._calculate_evaporation(sim._get_equilibrium())
    bounds = sim._last_evaporation_flux_diagnostic[
        "pressure_held_sweep_bounds"
    ]

    for species in ("Al", "AlO"):
        assert flux.species_kg_hr[species] == pytest.approx(
            bounds["upper_rates_kg_hr"][species], rel=1e-12
        )


@pytest.mark.parametrize("selected_bound", ("upper", "lower", "mid"))
def test_pressure_held_bound_selection_matches_value_and_authority(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    selected_bound,
):
    monkeypatch.setattr(
        evaporation_module,
        "PRESSURE_HELD_FLUX_BOUND",
        selected_bound,
    )
    sim = _pressure_held_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )

    flux = sim._calculate_evaporation(sim._get_equilibrium())
    diagnostic = sim._last_evaporation_flux_diagnostic
    bounds = diagnostic["pressure_held_sweep_bounds"]
    notices = [
        notice
        for notice in diagnostic["notices"]
        if notice.get("kind") == "sweep_flow_unspecified"
    ]

    assert notices
    assert all(
        notice["authority"] == f"{selected_bound}_bound"
        and notice["selected_bound"] == selected_bound
        for notice in notices
    )
    species = set(bounds["upper_rates_kg_hr"]) | set(
        bounds["lower_rates_kg_hr"]
    )
    for species_id in species:
        upper = bounds["upper_rates_kg_hr"].get(species_id, 0.0)
        lower = bounds["lower_rates_kg_hr"].get(species_id, 0.0)
        expected = {
            "upper": upper,
            "lower": lower,
            "mid": (upper + lower) / 2.0,
        }[selected_bound]
        assert flux.species_kg_hr.get(species_id, 0.0) == pytest.approx(
            expected, rel=1e-12, abs=1e-15
        )


def test_pumped_closure_prices_the_admitted_depleted_stream(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1670.0
    sim.melt.atmosphere = Atmosphere.HARD_VACUUM
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.p_total_mbar = 0.01
    sim._overhead_headspace_config["downstream_pressure_bar"] = 0.0
    sim.overhead_model.pipe_diameter_m = 1.2
    sim.melt.melt_surface_area_m2 = 100.0

    raw_flux = sim._calculate_evaporation(sim._get_equilibrium())
    closure = sim._last_evaporation_flux_diagnostic[
        "same_hour_headspace_closure"
    ]
    admitted_flux = sim._apply_analytic_evaporation_depletion(raw_flux)
    transport = sim.overhead_model.estimate_transport_state(
        admitted_flux,
        sim.melt,
        p_downstream_bar=sim._headspace_downstream_pressure_bar(None),
    )
    admitted_partials_mbar = sim.overhead_model.species_partial_pressures(
        admitted_flux,
        transport["vapor_pressure_mbar"],
        sim.species_formula_registry,
    )

    assert closure["converged"]
    for species in ("Na", "Fe"):
        assert closure["partial_pressures_Pa"][species] == pytest.approx(
            admitted_partials_mbar[species] * 100.0,
            rel=1e-9,
        )
