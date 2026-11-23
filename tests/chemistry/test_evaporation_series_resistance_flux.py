"""Source-side series-resistance evaporation flux tests."""

from __future__ import annotations

import math

import pytest

from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.condensation import (
    DEFAULT_BINARY_DIFFUSION_M2_S,
    GAS_CONSTANT_J_MOL_K,
    _chapman_enskog_d_ab_m2_s,
    _series_resistance_deposition_flux_mol_m2_s,
    common_film_diffusivity,
)
from simulator.lab_geometry import GAS_FILM_DISK, GAS_FILM_SPHERE
from simulator.state import MAX_STIR_FACTOR
from simulator.transport_constants import FREE_MOLECULAR_KNUDSEN_MIN

from engines.builtin.evaporation_flux import (
    BuiltinEvaporationFluxProvider,
    EvaporationFluxConfigurationError,
    _oxygen_coproduct_moles_per_carrier_mole,
    _solve_equal_d_stefan_maxwell,
    _series_resistance_evaporation_flux_kg_m2_s,
)


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


def test_lab_sphere_and_disk_limits_use_their_stated_sherwood_numbers():
    sphere = _evap(
        knudsen_number=0.0,
        pipe_diameter_m=0.013,
        gas_film_shape=GAS_FILM_SPHERE,
    )
    disk = _evap(
        knudsen_number=0.0,
        pipe_diameter_m=0.013,
        gas_film_shape=GAS_FILM_DISK,
    )

    assert sphere.transport_length_m == pytest.approx(0.013)
    assert disk.transport_length_m == pytest.approx(0.013)
    # k_mt is proportional to Sh for identical T, D, pressure, and length.
    assert disk.k_mt_kg_s_m2_pa / sphere.k_mt_kg_s_m2_pa == pytest.approx(
        4.0 / math.pi
    )


def test_unshaped_furnace_transport_keeps_its_pipe_length():
    furnace = _evap(knudsen_number=0.0, pipe_diameter_m=0.12)

    assert furnace.transport_length_m == pytest.approx(0.12)


def test_dilute_furnace_hour_recovers_the_round_one_linear_flux():
    linear = _evap(
        P_eq_pa=1.0e-9,
        P_bulk_pa=0.0,
        overhead_pressure_pa=1000.0,
        stefan_total_pressure_pa=1000.0,
        _defer_shared_stefan=True,
    )
    stefan = _evap(
        P_eq_pa=1.0e-9,
        P_bulk_pa=0.0,
        overhead_pressure_pa=1000.0,
        stefan_total_pressure_pa=1000.0,
    )

    assert stefan.transport_length_m == pytest.approx(0.12)
    assert stefan.stefan_log_E == pytest.approx(0.0, abs=1.0e-12)
    assert stefan.flux_kg_s_m2 == pytest.approx(linear.flux_kg_s_m2, rel=1.0e-11)


def test_surface_pressure_limit_recovers_interface_plus_melt_series_flux():
    total_pressure = 1300.0
    p_eq = 1.0e6
    result = _evap(
        species="SiO",
        P_eq_pa=p_eq,
        P_bulk_pa=0.0,
        T_surface_K=2200.0,
        T_gas_K=2200.0,
        molar_mass_kg_mol=0.044084,
        alpha_i=0.1,
        knudsen_number=4.0e-4,
        pipe_diameter_m=1.0,
        overhead_pressure_pa=total_pressure,
        stefan_total_pressure_pa=total_pressure,
        carrier_gas="Ar",
        gas_film_shape=GAS_FILM_SPHERE,
    )
    interface_plus_melt = (p_eq - total_pressure) / (
        result.r_interface + result.r_melt
    )

    assert result.surface_partial_pressure_Pa < total_pressure
    assert result.surface_partial_pressure_Pa > total_pressure - 0.02
    assert result.flux_kg_s_m2 == pytest.approx(interface_plus_melt, rel=5.0e-4)


def test_robinot_outflow_pressure_is_finite_flagged_and_below_hk_bound():
    total_pressure = 1300.0
    p_eq = 1560.0
    inputs = dict(
        species="SiO",
        P_eq_pa=p_eq,
        P_bulk_pa=0.0,
        T_surface_K=2236.0,
        T_gas_K=2236.0,
        molar_mass_kg_mol=0.044084,
        alpha_i=0.1,
        knudsen_number=4.0e-4,
        pipe_diameter_m=0.013,
        overhead_pressure_pa=total_pressure,
        stefan_total_pressure_pa=total_pressure,
        carrier_gas="Ar",
        gas_film_shape=GAS_FILM_SPHERE,
    )
    result = _evap(**inputs)
    linear_film = _evap(**inputs, _defer_shared_stefan=True)
    diagnostic = result.as_diagnostic()
    hertz_knudsen_bound = (
        result.alpha_intrinsic
        * result.k_hk_kg_s_m2_pa
        * (p_eq - result.P_bulk_Pa)
    )
    molar_flux = result.flux_kg_s_m2 / result.molar_mass_kg_mol
    interface_molar_conductance = 1.0 / (
        (result.r_interface + result.r_melt) * result.molar_mass_kg_mol
    )

    assert math.isfinite(result.flux_kg_s_m2)
    assert result.surface_partial_pressure_Pa < total_pressure
    assert 0.0 < result.flux_kg_s_m2 < hertz_knudsen_bound
    assert result.flux_kg_s_m2 > 1.5 * linear_film.flux_kg_s_m2
    assert molar_flux == pytest.approx(
        interface_molar_conductance
        * (p_eq - result.surface_partial_pressure_Pa),
        rel=1.0e-12,
    )
    assert result.flux_kg_s_m2 * (
        result.r_interface + result.r_gas + result.r_melt
    ) == pytest.approx(p_eq - result.P_bulk_Pa, rel=1.0e-12)
    assert diagnostic["P_eq_over_P_total"] == pytest.approx(1.2)
    assert "bulk_outflow_regime" in diagnostic["typed_flags"]
    assert "gas_film_equal_d_stefan_maxwell" in diagnostic["typed_flags"]
    assert "internal bubble boiling" in diagnostic["typed_flag_details"][
        "bulk_outflow_regime"
    ]


def test_stefan_total_includes_stoichiometric_oxygen_coproduct():
    from simulator.state import MOLAR_MASS

    sio_molar_mass = 0.044084
    o2_per_product_kg = (
        0.5 * (float(MOLAR_MASS["O2"]) / 1000.0) / sio_molar_mass
    )
    assert _oxygen_coproduct_moles_per_carrier_mole(
        {"O2_per_product_kg": o2_per_product_kg},
        sio_molar_mass,
    ) == pytest.approx(0.5)

    result = _evap(
        species="SiO",
        P_eq_pa=900.0,
        P_bulk_pa=0.0,
        T_surface_K=2200.0,
        T_gas_K=2200.0,
        molar_mass_kg_mol=sio_molar_mass,
        alpha_i=0.1,
        knudsen_number=4.0e-4,
        pipe_diameter_m=0.013,
        overhead_pressure_pa=1300.0,
        stefan_total_pressure_pa=1300.0,
        carrier_gas="Ar",
        gas_film_shape=GAS_FILM_SPHERE,
        oxygen_coproduct_moles_per_mole=0.5,
        oxygen_bulk_partial_pressure_pa=0.0,
    )

    assert result.surface_o2_partial_pressure_Pa == pytest.approx(
        0.5 * result.surface_partial_pressure_Pa,
        rel=1.0e-12,
    )
    assert result.shared_surface_evolved_pressure_Pa == pytest.approx(
        result.surface_partial_pressure_Pa
        + result.surface_o2_partial_pressure_Pa
    )


def test_equal_d_closure_is_invariant_to_bulk_oxygen_composition():
    common = dict(
        species="SiO",
        P_eq_pa=900.0,
        P_bulk_pa=0.0,
        T_surface_K=2200.0,
        T_gas_K=2200.0,
        molar_mass_kg_mol=0.044084,
        alpha_i=0.1,
        knudsen_number=4.0e-4,
        pipe_diameter_m=0.013,
        overhead_pressure_pa=1300.0,
        stefan_total_pressure_pa=1300.0,
        carrier_gas="Ar",
        gas_film_shape=GAS_FILM_SPHERE,
        oxygen_coproduct_moles_per_mole=0.5,
    )
    dilute_o2 = _evap(**common, oxygen_bulk_partial_pressure_pa=0.01)
    oxygen_rich = _evap(**common, oxygen_bulk_partial_pressure_pa=1200.0)

    assert oxygen_rich.flux_kg_s_m2 == pytest.approx(
        dilute_o2.flux_kg_s_m2, rel=1.0e-12
    )
    assert oxygen_rich.surface_partial_pressure_Pa == pytest.approx(
        dilute_o2.surface_partial_pressure_Pa, rel=1.0e-12
    )
    assert oxygen_rich.surface_partial_pressure_Pa < 1300.0
    assert math.isfinite(oxygen_rich.stefan_log_E)
    assert oxygen_rich.surface_o2_partial_pressure_Pa > dilute_o2.surface_o2_partial_pressure_Pa


def test_oxygen_consumers_and_melt_buffer_oxidants_add_no_film_o2():
    oxygen_consuming = _oxygen_coproduct_moles_per_carrier_mole(
        {"O2_per_product_kg": -0.13563178355490185},
        0.0589795385,
    )
    melt_buffer = _oxygen_coproduct_moles_per_carrier_mole(
        {
            "O2_per_product_kg": 0.25,
            "oxygen_destination": "reservoir.fo2_buffer",
        },
        0.0839941,
    )

    assert oxygen_consuming == 0.0
    assert melt_buffer == 0.0


def test_bulk_carrier_without_evaporation_still_enters_surface_composition():
    active = _evap(
        species="SiO",
        P_eq_pa=900.0,
        P_bulk_pa=0.0,
        T_surface_K=2200.0,
        T_gas_K=2200.0,
        molar_mass_kg_mol=0.044084,
        alpha_i=0.1,
        knudsen_number=4.0e-4,
        pipe_diameter_m=0.013,
        overhead_pressure_pa=1300.0,
        stefan_total_pressure_pa=1300.0,
        carrier_gas="Ar",
        gas_film_shape=GAS_FILM_SPHERE,
        oxygen_coproduct_moles_per_mole=0.5,
        _defer_shared_stefan=True,
    )
    inactive = _evap(
        species="Na",
        P_eq_pa=50.0,
        P_bulk_pa=100.0,
        T_surface_K=2200.0,
        T_gas_K=2200.0,
        molar_mass_kg_mol=0.02298976928,
        alpha_i=0.1,
        knudsen_number=4.0e-4,
        pipe_diameter_m=0.013,
        overhead_pressure_pa=1300.0,
        stefan_total_pressure_pa=1300.0,
        carrier_gas="Ar",
        gas_film_shape=GAS_FILM_SPHERE,
        _defer_shared_stefan=True,
    )

    solved, surface_evolved, surface_o2, _log_E = (
        _solve_equal_d_stefan_maxwell(
            {"SiO": active, "Na": inactive},
            total_pressure_Pa=1300.0,
            temperature_K=2200.0,
            diffusion_m2_s=active.d_ab_m2_s,
            total_bulk_partial_pressure_Pa=100.0,
            bulk_o2_partial_pressure_Pa=0.0,
        )
    )

    inverse_E = math.exp(-solved["SiO"].stefan_log_E)
    assert solved["Na"].flux_kg_s_m2 == 0.0
    assert solved["Na"].surface_partial_pressure_Pa == pytest.approx(
        100.0 * inverse_E
    )
    assert surface_evolved == pytest.approx(
        solved["SiO"].surface_partial_pressure_Pa
        + solved["Na"].surface_partial_pressure_Pa
        + surface_o2
    )


def test_common_film_diffusivity_is_exact_for_one_carrier_and_reports_spread():
    diffusion, top_species, spread, species_diffusions = common_film_diffusivity(
        {"SiO": 1.0}, 2200.0, 1300.0, "Ar"
    )
    expected = _chapman_enskog_d_ab_m2_s("SiO", 2200.0, 1300.0, carrier="Ar")

    assert diffusion == pytest.approx(expected, rel=1.0e-14)
    assert top_species == "SiO"
    assert spread == pytest.approx(1.0)
    assert species_diffusions == {"SiO": pytest.approx(expected)}


def test_common_film_diffusivity_is_continuous_through_na_sio_weight_crossover():
    from simulator.chemistry.langmuir_knudsen import (
        hertz_knudsen_k_kg_s_m2_pa,
    )

    midpoint_K = 1800.0
    alpha = 0.5
    na_mass = 0.02298976928
    sio_mass = 0.044084
    na_k = alpha * hertz_knudsen_k_kg_s_m2_pa(midpoint_K, na_mass) / na_mass
    sio_k = alpha * hertz_knudsen_k_kg_s_m2_pa(midpoint_K, sio_mass) / sio_mass
    sio_eq_at_crossing = na_k / sio_k
    diffusivities = []
    top_species = set()
    for temperature_K in range(1780, 1821):
        offset = temperature_K - midpoint_K
        na_eq = math.exp(0.01 * offset)
        sio_eq = sio_eq_at_crossing * math.exp(-0.01 * offset)
        na_flux_weight = (
            alpha
            * hertz_knudsen_k_kg_s_m2_pa(temperature_K, na_mass)
            / na_mass
            * na_eq
        )
        sio_flux_weight = (
            alpha
            * hertz_knudsen_k_kg_s_m2_pa(temperature_K, sio_mass)
            / sio_mass
            * sio_eq
        )
        diffusion, top, spread, _species_diffusions = common_film_diffusivity(
            {"Na": na_flux_weight, "SiO": sio_flux_weight},
            float(temperature_K),
            1300.0,
            "Ar",
        )
        assert spread >= 1.0
        top_species.add(top)
        diffusivities.append(diffusion)

    largest_adjacent_relative_change = max(
        abs(right - left) / min(right, left)
        for left, right in zip(diffusivities, diffusivities[1:])
    )
    assert top_species == {"Na", "SiO"}
    assert largest_adjacent_relative_change < 0.01


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
