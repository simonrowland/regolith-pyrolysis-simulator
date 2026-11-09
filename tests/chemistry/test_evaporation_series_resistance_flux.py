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
    MOLAR_MASS,
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
    vacuum_series = _evap(
        species=species,
        P_eq_pa=80.0,
        P_bulk_pa=0.0,
        T_surface_K=1800.0,
        T_gas_K=1800.0,
        molar_mass_kg_mol=molar_mass,
        alpha_i=0.13,
        knudsen_number=math.inf,
        gas_resistance_enabled=False,
    )
    resistance_Pa_m2_s_kg = (
        vacuum_series.r_interface
        + vacuum_series.r_gas
        + vacuum_series.r_melt
    )
    molar_resistance_Pa_hr_mol = (
        resistance_Pa_m2_s_kg * molar_mass / (area_m2 * 3600.0)
    )

    def rates_at_projection_g(g, _pressure_Pa):
        molar_rate = 80.0 / (molar_resistance_Pa_hr_mol + g)
        return {species: molar_rate * molar_mass}

    return (
        model,
        melt,
        area_m2,
        vacuum,
        project_partials,
        flux_at_partials,
        rates_at_projection_g,
        molar_mass,
    )


def _solve_self_pumped_case(species, *, pipe_diameter_m=0.12):
    model, melt, area_m2, vacuum, project, _flux_at, rates_at_g, molar_mass = _self_pumped_case(
        species, pipe_diameter_m=pipe_diameter_m
    )
    result = _solve_same_hour_headspace_closure(
        vacuum,
        rates_at_g,
        project,
        {species: molar_mass},
    )
    return model, melt, area_m2, result


def test_constant_temperature_multihour_closure_does_not_alternate():
    for species in ("Cr", "CrO", "Fe", "SiO", "Na", "K", "Mg"):
        hourly_fluxes = []
        for hour in range(8):
            # Stale end-of-hour pressures alternate deliberately; each hour
            # supplies the corresponding extreme initial rate, while the
            # fixed point closes against its own evolved-rate projection.
            (
                _model,
                _melt,
                _area_m2,
                vacuum,
                project,
                _flux_at,
                rates_at_g,
                molar_mass,
            ) = _self_pumped_case(
                species
            )
            stale_bulk_mbar = 0.0 if hour % 2 == 0 else 1.6
            result = _solve_same_hour_headspace_closure(
                vacuum if stale_bulk_mbar == 0.0 else _flux_at({species: stale_bulk_mbar}),
                rates_at_g,
                project,
                {species: molar_mass},
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
    _model, _melt, _area_m2, vacuum, _project, _flux_at, _rates, _mass = _self_pumped_case(species)
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
    assert closed_pressure == pytest.approx(80.0 / 100.0, rel=1.0e-12)


def test_closed_line_subboiling_root_is_recognized_without_iteration():
    equilibrium_pressures = {"A": 40.0, "B": 39.0}

    def unexpected_evaluation(*_values):
        raise AssertionError("zero-boundary root must not iterate")

    result = _solve_same_hour_headspace_closure(
        {"A": 1.0, "B": 2.0},
        unexpected_evaluation,
        unexpected_evaluation,
        {"A": 1.0, "B": 1.0},
        closed_line_equilibrium_pressures_Pa=equilibrium_pressures,
        closed_line_total_pressure_Pa=80.0,
    )

    assert result.converged
    assert result.iterations == 0
    assert result.rates_kg_hr == {"A": 0.0, "B": 0.0}


def test_transformed_subboiling_source_returns_no_positive_root_boundary():
    result = _solve_same_hour_headspace_closure(
        {"A": 75.0},
        lambda g, _pressure: {"A": 75.0 / (1.0 + g)},
        lambda _rates: {"A": 1.0},
        {"A": 1.0},
        closed_line_equilibrium_pressures_Pa={"A": 150.0},
        closed_line_total_pressure_Pa=100.0,
    )

    assert result.converged
    assert result.status == "no_positive_root_boundary"
    assert result.rates_kg_hr == {"A": 0.0}
    assert result.zero_flow_criterion["closed_line_total_pressure_Pa"] == 100.0
    assert result.scan_evaluations <= 64


def test_positive_downstream_above_effective_equilibrium_returns_boundary():
    def pressure_at_rates(rates):
        total_flow = math.fsum(rates.values())
        return {"A": math.sqrt(200.0**2 + total_flow) / 100.0}

    result = _solve_same_hour_headspace_closure(
        {"A": 150.0},
        lambda g, _pressure: {"A": 150.0 / (1.0 + g)},
        pressure_at_rates,
        {"A": 1.0},
    )

    assert result.converged
    assert result.status == "no_positive_root_boundary"
    assert result.rates_kg_hr["A"] == 0.0
    assert result.zero_flow_criterion["zero_flow_projected_pressure_Pa"] >= 200.0
    assert result.scan_evaluations <= 64


def test_source_free_nested_projection_returns_boundary_before_g_scan():
    result = _solve_same_hour_headspace_closure(
        {},
        lambda _g, _pressure: {"A": 0.0},
        lambda _rates: {},
        {"A": 1.0},
        pressure_floor_Pa=1000.0,
    )

    assert result.converged
    assert result.status == "no_positive_root_boundary"
    assert result.rates_kg_hr == {"A": 0.0}
    assert result.iterations <= 65
    assert result.scan_evaluations == 1
    assert result.zero_flow_criterion["pressure_floor_Pa"] == 1000.0


def test_scalar_scan_treats_late_species_as_zero_in_earlier_samples():
    def rates_at_g(g, _pressure):
        rates = {"A": 100.0 / (1.0 + g)}
        if 0.0 < g < 1.0:
            rates["CrO3"] = 0.01 / (1.0 + g)
        return rates

    result = _solve_same_hour_headspace_closure(
        {"A": 100.0},
        rates_at_g,
        lambda _rates: {},
        {"A": 1.0, "CrO3": 0.1},
        carrier_feed_mol_s=1.0 / 3600.0,
        closed_line_total_pressure_Pa=100.0,
    )

    assert result.converged
    assert result.status == "converged"
    assert result.rates_kg_hr["A"] > 0.0
    assert result.rates_kg_hr["CrO3"] == 0.0


def test_review_shared_depletion_examples_are_nonmonotone(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    cases = (
        (
            "Al",
            {"Al2": 1000.0, "Al": 1.0},
            ("Al2", "Al"),
            "Al2O3",
            (0.009807809383814018, 0.009808250708756574),
        ),
        (
            "Fe",
            {"Fe": 1000.0, "FeO_association_gas": 1.0},
            ("Fe", "FeO_association_gas"),
            "FeO",
            (1.3918907634597167e-7, 1.3917655058167698e-6),
        ),
    )

    for primary, raw_molar_coefficients, species_names, parent, expected in cases:
        molar_masses = {
            species: resolve_species_formula(
                species, sim.species_formula_registry
            ).molar_mass_kg_per_mol()
            for species in species_names
        }
        if primary == "Fe":
            sim._native_fe_vapor_residual_capacity_mol_this_hr = 0.0
        transformed_totals = []
        for g in (0.0, 10.0):
            raw_rates = {
                species: (
                    raw_molar_coefficients[species]
                    / ((1.0 + g) if species in {"Al2", "Fe"} else (100.0 + g))
                    * molar_masses[species]
                )
                for species in species_names
            }
            transformed = sim._analytic_evaporation_depletion_rates(
                raw_rates,
                dt_hr=1.0,
                phase_scalar=1.0,
                cleaned_melt_kg={parent: 0.001},
                available_o2_kg=1.0,
            )
            if primary == "Fe":
                transformed, _ = sim._apply_native_fe_residual_capacity(
                    transformed, molar_masses
                )
            transformed_totals.append(
                math.fsum(
                    transformed.get(species, 0.0) / molar_masses[species]
                    for species in species_names
                )
            )

        assert transformed_totals == pytest.approx(expected, rel=1e-12)
        assert transformed_totals[1] > transformed_totals[0]


def test_scalar_closure_reports_all_roots_for_nonmonotone_stream():
    pressure_pa = 1.0
    carrier_feed_mol_hr = 1.0e-6

    def total_rate_mol_hr(g):
        # A smooth admitted-stream bump creates two additional crossings in
        # G(N)=P/(F+N). This isolates the solver's all-roots contract.
        log_offset = math.log((g + 1.0e-20) / 100.0)
        return 0.01 / (1.0 + g) + 2.0 * math.exp(
            -0.5 * (log_offset / 1.5) ** 2
        )

    result = _solve_same_hour_headspace_closure(
        {"A": total_rate_mol_hr(0.0)},
        lambda g, _pressure: {"A": total_rate_mol_hr(g)},
        lambda _rates: {},
        {"A": 1.0},
        carrier_feed_mol_s=carrier_feed_mol_hr / 3600.0,
        closed_line_total_pressure_Pa=pressure_pa,
    )

    assert result.converged
    assert result.multiple_closure_roots
    assert len(result.roots_g) == 3
    assert tuple(sorted(result.roots_g)) == result.roots_g
    assert result.iterations < 200
    assert result.rates_kg_hr["A"] == pytest.approx(
        total_rate_mol_hr(result.roots_g[0]), rel=1e-12
    )
    assert all(
        pressure_pa / (carrier_feed_mol_hr + total_rate_mol_hr(g)) - g
        == pytest.approx(0.0, abs=1e-8)
        for g in result.roots_g
    )


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


def _single_species_pressure_held_closure(carrier_feed_mol_s=None):
    species = "K"
    p_eq_pa = 250.0
    p_total_pa = 100.0
    resistance_pa_hr_mol = 5.0
    molar_mass_kg_mol = MOLAR_MASS[species] / 1000.0

    def rates_at_projection_g(g, _pressure_pa):
        molar_rate_mol_hr = p_eq_pa / (resistance_pa_hr_mol + g)
        return {species: molar_rate_mol_hr * molar_mass_kg_mol}

    def partials_at_rates(rates_kg_hr):
        # No-feed lower bracket: evolved gas fills P_total. The feed-enabled
        # closure replaces this projection with n_i*g in its single-species path.
        return {species: p_total_pa / 100.0}

    kwargs = {}
    if carrier_feed_mol_s is not None:
        kwargs["carrier_feed_mol_s"] = carrier_feed_mol_s
    return _solve_same_hour_headspace_closure(
        {species: p_eq_pa / resistance_pa_hr_mol * molar_mass_kg_mol},
        rates_at_projection_g,
        partials_at_rates,
        {species: molar_mass_kg_mol},
        closed_line_equilibrium_pressures_Pa={species: p_eq_pa},
        closed_line_total_pressure_Pa=p_total_pa,
        **kwargs,
    )


def test_carrier_feed_zero_is_closed_line_and_large_feed_approaches_upper():
    lower = _single_species_pressure_held_closure()
    zero_feed = _single_species_pressure_held_closure(0.0)
    large_feed = _single_species_pressure_held_closure(1.0e6)
    mass_kg_mol = MOLAR_MASS["K"] / 1000.0
    upper_mol_hr = 250.0 / 5.0

    assert zero_feed.converged
    assert zero_feed.rates_kg_hr["K"] == pytest.approx(
        lower.rates_kg_hr["K"], rel=1e-10, abs=1e-14
    )
    assert large_feed.converged
    assert large_feed.rates_kg_hr["K"] / mass_kg_mol == pytest.approx(
        upper_mol_hr, rel=1e-8
    )


def test_single_species_feed_closure_uses_total_outflow_in_beta():
    carrier_feed_mol_s = 1.0e-3
    p_eq_pa = 250.0
    p_total_pa = 100.0
    resistance_pa_hr_mol = 5.0
    molar_mass_kg_mol = MOLAR_MASS["K"] / 1000.0
    result = _single_species_pressure_held_closure(carrier_feed_mol_s)
    rate_mol_hr = result.rates_kg_hr["K"] / molar_mass_kg_mol
    # R' is Pa·hr/mol and the setpoint is mol/s, so convert F to mol/hr.
    # The stated F+N outflow makes beta use carrier plus evolved flow:
    # beta=(3600 F + J)R'/P_total. Consequently J=J_max*beta/(1+beta).
    beta = (
        (3600.0 * carrier_feed_mol_s + rate_mol_hr)
        * resistance_pa_hr_mol
        / p_total_pa
    )
    j_max_mol_hr = p_eq_pa / resistance_pa_hr_mol

    assert result.converged
    assert rate_mol_hr == pytest.approx(
        j_max_mol_hr * beta / (1.0 + beta), rel=1e-10
    )


def test_unset_carrier_feed_keeps_the_pressure_held_bracket_authority(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _pressure_held_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.campaign = CampaignPhase.C2A

    flux = sim._calculate_evaporation(sim._get_equilibrium())
    diagnostic = sim._last_evaporation_flux_diagnostic
    bounds = diagnostic["pressure_held_sweep_bounds"]

    assert diagnostic["same_hour_headspace_closure"]["value_authority"] == (
        "sweep_flow_unspecified"
    )
    assert any(
        notice["kind"] == "o2_coproduct_excluded_pending_b1141"
        for notice in diagnostic["notices"]
    )
    assert all(
        flux.species_kg_hr.get(species, 0.0)
        == bounds["upper_rates_kg_hr"].get(species, 0.0)
        for species in set(bounds["upper_rates_kg_hr"])
        | set(bounds["lower_rates_kg_hr"])
    )


def test_carrier_feed_consumption_is_reported_per_hour_and_run(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _pressure_held_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.campaign = CampaignPhase.C2A
    sim.melt.carrier_feed_mol_s = 2.0e-4
    sim.melt.carrier_feed_gas = "Kr"
    sim._update_temperature = lambda: None

    snapshot = sim.step()
    consumption = snapshot.same_hour_headspace_diagnostics[
        "carrier_feed_consumption"
    ]
    expected_mol = 2.0e-4 * 3600.0
    expected_kg = expected_mol * MOLAR_MASS["Kr"] / 1000.0

    assert consumption["species"] == "Kr"
    assert consumption["mol_per_hour"] == pytest.approx(expected_mol)
    assert consumption["kg_per_hour"] == pytest.approx(expected_kg)
    assert consumption["mol_per_run"] == pytest.approx(expected_mol)
    assert consumption["kg_per_run"] == pytest.approx(expected_kg)
    assert sim._last_evaporation_flux_diagnostic[
        "same_hour_headspace_closure"
    ]["value_authority"] == "sweep_flow_from_setpoint"
    assert any(
        notice["kind"] == "o2_coproduct_excluded_pending_b1141"
        for notice in snapshot.same_hour_headspace_diagnostics["notices"]
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

    admitted_flux = sim._calculate_evaporation(sim._get_equilibrium())
    closure = sim._last_evaporation_flux_diagnostic[
        "same_hour_headspace_closure"
    ]
    assert (
        sim._last_evaporation_flux_diagnostic[
            "evaporation_flux_rates_are_admitted"
        ]
        is True
    )
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
    assert closure["iterations"] <= 200
    for species in ("Na", "Fe"):
        assert admitted_flux.species_kg_hr[species] == pytest.approx(
            closure["rates_kg_hr"][species],
            rel=1e-12,
        )
        assert closure["partial_pressures_Pa"][species] == pytest.approx(
            admitted_partials_mbar[species] * 100.0,
            rel=1e-9,
        )


def test_scalar_closure_counts_source_and_projection_evaluations():
    species = tuple(f"S{index:03d}" for index in range(100))
    projection_calls = 0

    def partials_at_rates(rates):
        nonlocal projection_calls
        projection_calls += 1
        total_molar_flow = math.fsum(rates.values())
        pressure_mbar = math.sqrt(total_molar_flow) / 100.0
        return {
            name: pressure_mbar / len(species)
            for name in species
        }

    def rates_at_projection_g(g, _pressure_Pa):
        rate = 1.0 / (1.0 + g)
        return {name: rate for name in species}

    result = _solve_same_hour_headspace_closure(
        {name: 1.0 for name in species},
        rates_at_projection_g,
        partials_at_rates,
        {name: 1.0 for name in species},
    )

    total_molar_flow = math.fsum(result.rates_kg_hr.values())
    projected_g = (
        100.0 * math.fsum(result.partial_pressures_mbar.values())
        / total_molar_flow
    )
    assert result.converged
    assert result.iterations <= 200
    assert projection_calls == result.iterations
    assert projected_g == pytest.approx(
        1.0 / math.sqrt(total_molar_flow), rel=1.0e-12
    )
    assert 1.0 / result.rates_kg_hr[species[0]] - 1.0 == pytest.approx(
        projected_g, rel=1.0e-12
    )


def test_native_fe_closure_uses_pressure_held_zero_boundary(
    vapor_pressure_data, feedstocks_data, setpoints_data
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

    diagnostic = sim._native_fe_partition_diagnostic(1.0)
    closure = diagnostic["same_hour_headspace_closure"]

    assert diagnostic["P_reference_Antoine_Pa"] < sim.melt.p_total_mbar * 100.0
    assert closure["converged"]
    assert closure["iterations"] == 0
    assert closure["status"] == "no_positive_root_boundary"
    assert closure["zero_flow_criterion"]["closed_line_total_pressure_Pa"] == (
        pytest.approx(100.0)
    )
    assert closure["rates_kg_hr"]["Fe"] == 0.0
    assert diagnostic["native_fe_vapor_mol"] == 0.0
    assert diagnostic["native_fe_tap_mol"] == pytest.approx(1.0)
    assert sim._same_hour_headspace_closure_iterations_by_hour[
        sim.melt.hour
    ]["native_fe"] == 0


def test_c7_pressure_scan_finds_positive_branch_above_suppressed_floor(
    vapor_pressure_data, feedstocks_data, setpoints_data
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1750.0
    sim.melt.p_total_mbar = 0.03632
    sim.overhead_model.pipe_diameter_m = 0.12
    sim.overhead.headspace_temperature_K = 2023.15
    cfg = dict(sim._c7_campaign_config())
    cfg.update(
        active_ca_condensation_route=True,
        dedicated_ca_condenser=True,
        hold_temp_C=1750.0,
        p_total_mbar=0.03632,
        ca_route_surface_area_m2=0.2,
        hold_time_h=1.0,
        stir_factor=1.0,
        radial_stir_factor=1.0,
    )

    extent, diagnostic = sim._c7_transport_extent_mol(cfg, ca_per_extent=3.0)
    closure = diagnostic["same_hour_headspace_closure"]

    assert closure["converged"] is True, closure
    assert closure["iterations"] <= 200
    assert closure["multiple_closure_roots"] is False
    assert closure["rates_kg_hr"]["Ca"] == pytest.approx(
        562.3498686097761, rel=1.0e-6
    )
    assert extent > 0.0


@pytest.mark.parametrize("pressure_held", (False, True))
def test_step_persists_the_selected_same_hour_source_map(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    pressure_held,
):
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1670.0
    sim.melt.campaign = CampaignPhase.C2A
    sim._apply_native_fe_saturation_split = lambda **_kwargs: None
    sim._update_temperature = lambda: None
    if pressure_held:
        sim.melt.atmosphere = Atmosphere.CONTROLLED_O2
        sim.melt.p_total_mbar = 1.0
        sim._overhead_headspace_config["downstream_pressure_bar"] = 0.001
    else:
        sim.melt.atmosphere = Atmosphere.HARD_VACUUM
        sim.melt.p_total_mbar = 0.01
        sim._overhead_headspace_config["downstream_pressure_bar"] = 0.0
        sim.overhead_model.pipe_diameter_m = 1.2
        sim.melt.melt_surface_area_m2 = 100.0

    snapshot = sim.step()
    diagnostic = sim._last_evaporation_flux_diagnostic
    closure = diagnostic["same_hour_headspace_closure"]
    expected_source_map_mbar = {
        species: pressure / 100.0
        for species, pressure in closure["partial_pressures_Pa"].items()
    }

    assert snapshot.melt_headspace_composition_mbar == pytest.approx(
        expected_source_map_mbar
    )
    hour_diagnostics = snapshot.same_hour_headspace_diagnostics
    assert hour_diagnostics["closure"] == closure
    assert hour_diagnostics["regime"] == closure["regime"]
    assert hour_diagnostics["iterations_by_consumer"][
        "ordinary_evaporation"
    ] == closure["iterations"]
    if pressure_held:
        assert closure["partial_pressures_Pa"]
        assert all(
            pressure == 0.0
            for pressure in closure["partial_pressures_Pa"].values()
        )
        assert "lower_bound_partial_pressures_Pa" in diagnostic[
            "pressure_held_sweep_bounds"
        ]
        assert hour_diagnostics["pressure_held_sweep_bounds"] == diagnostic[
            "pressure_held_sweep_bounds"
        ]
        assert any(
            notice["kind"] == "sweep_flow_unspecified"
            for notice in hour_diagnostics["notices"]
        )
