"""Spherical bead diffusion: mass, analytic decay, and the frozen-bead limit.

Expected numbers are the sphere volume, the no-flux Bessel root, and the
printed Lowry / Ni and Zhang Arrhenius expressions. They are not copied
from the solver's running output.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from simulator.bead_diffusion import (
    LOWRY_1982_NA_LN_D_INTERCEPT,
    LOWRY_1982_NA_LN_D_SLOPE_K,
    NI_ZHANG_2016_CU_LN_D_INTERCEPT,
    NI_ZHANG_2016_CU_LN_D_SLOPE_K,
    BeadDiffusionRefusal,
    BeadProfilePrediction,
    compare_profile_point,
    cooling_half_time_s,
    diffusivity_along_zhang_cooling,
    integrate_spherical_diffusion,
    lowry_1982_na_diffusivity_m2_s,
    na2o_relative_moles,
    na2o_surface_mass_flux,
    ni_zhang_2016_cu_diffusivity_m2_s,
    oxide_mass_kg,
    potassium_tracer_diffusivity_m2_s,
    predict_bead_profile,
    radius_from_surface_traverse,
    spherical_bead_grid,
    wt_percent_at_radius,
    zhang_2008_duration_until_s,
    zhang_2008_temperature_K,
)
from simulator.accounting.oxide_mass import oxide_molar_mass_g_mol
from simulator.chemistry.langmuir_knudsen import (
    langmuir_molar_flux,
    species_molar_mass_kg_mol,
)
from simulator.chemistry.melt_activity import melt_oxide_activity
from simulator.physical_constants import STEFAN_BOLTZMANN

_DENSITY = 3000.0
_RADIUS = 1.0e-4


def _zero_flux(_t_s: float, _mass_fraction: float) -> float:
    return 0.0


def _no_flux_eigenvalue() -> float:
    """First positive root of tan(μ) = μ, the no-flux sphere condition."""

    mu = 4.5
    for _ in range(12):
        denominator = mu * math.sin(mu)
        mu -= (math.sin(mu) - mu * math.cos(mu)) / denominator
    return mu


def test_shell_volumes_sum_to_the_sphere() -> None:
    grid = spherical_bead_grid(1.25e-4, 17)
    sphere = (4.0 / 3.0) * math.pi * grid.radius_m**3
    assert sum(grid.volume_m3) == pytest.approx(sphere, rel=1e-12)
    assert grid.face_radius_m[0] == 0.0
    assert grid.face_radius_m[-1] == pytest.approx(grid.radius_m)
    assert grid.centroid_radius_m[0] == pytest.approx(0.75 * grid.face_radius_m[1])


def test_zero_flux_conserves_oxide_mass_and_a_uniform_profile() -> None:
    initial = 0.0035
    result = integrate_spherical_diffusion(
        radius_m=_RADIUS,
        density_kg_m3=_DENSITY,
        initial_mass_fraction=initial,
        diffusivity_m2_s=1.0e-10,
        surface_mass_flux_kg_m2_s=_zero_flux,
        t_end_s=2.0,
        n_steps=40,
        n_cells=12,
        theta=0.5,
    )
    assert result.boundary_mass_out_kg == pytest.approx(0.0, abs=1e-18)
    assert result.oxide_mass_kg(_DENSITY) == pytest.approx(
        oxide_mass_kg([initial] * 12, result.grid, _DENSITY),
        rel=1e-12,
    )
    assert result.mass_fraction == pytest.approx([initial] * 12, rel=0.0, abs=1e-15)


def _positive_eigenmode(grid_radii: list[float] | tuple[float, ...], lam: float) -> list[float]:
    """Offset the signed no-flux mode so every shell stays positive.

    The diffusion equation is linear, so a uniform offset does not decay
    and the mode still decays as exp(-D λ^2 t).
    """

    mode = [math.sin(lam * radius_c) / radius_c for radius_c in grid_radii]
    return [2.0 + 0.2 * value for value in mode]


def _eigenmode_expected(initial: list[float], decay: float) -> list[float]:
    return [2.0 + (value - 2.0) * decay for value in initial]


def test_no_flux_eigenmode_decays_at_the_bessel_rate() -> None:
    mu = _no_flux_eigenvalue()
    assert math.tan(mu) == pytest.approx(mu, rel=1e-12)
    radius = 1.0
    diffusivity = 0.02
    duration = 0.15
    cells = 80
    grid = spherical_bead_grid(radius, cells)
    lam = mu / radius
    initial = _positive_eigenmode(grid.centroid_radius_m, lam)
    result = integrate_spherical_diffusion(
        radius_m=radius,
        density_kg_m3=1.0,
        initial_mass_fraction=initial,
        diffusivity_m2_s=diffusivity,
        surface_mass_flux_kg_m2_s=_zero_flux,
        t_end_s=duration,
        n_steps=80,
        n_cells=cells,
        theta=0.5,
    )
    decay = math.exp(-diffusivity * lam * lam * duration)
    expected = _eigenmode_expected(initial, decay)
    assert result.mass_fraction == pytest.approx(expected, rel=2e-3, abs=1e-5)
    assert result.oxide_mass_kg(1.0) == pytest.approx(
        oxide_mass_kg(initial, grid, 1.0),
        rel=1e-9,
    )


def _shell_average_of_sinc_mode(
    face_radius_m: tuple[float, ...],
    lam: float,
) -> list[float]:
    """Volume average of sin(λr)/r on each shell.

    ∫ r sin(λr) dr = -r cos(λr)/λ + sin(λr)/λ², so the shell mean is
    exact. The finite-volume unknown is that mean, not the centroid sample.
    """

    def antiderivative(radius: float) -> float:
        if radius == 0.0:
            return 0.0
        return (
            -radius * math.cos(lam * radius) / lam
            + math.sin(lam * radius) / lam**2
        )

    averages = []
    for inner, outer in zip(face_radius_m, face_radius_m[1:]):
        volume_factor = outer**3 - inner**3
        averages.append(
            3.0 * (antiderivative(outer) - antiderivative(inner)) / volume_factor
        )
    return averages


def test_eigenmode_error_falls_when_the_grid_is_refined() -> None:
    """Spatial error of the cell-average eigenmode.

    At 24 shells the scheme is still pre-asymptotic. From 48 to 96 shells
    the max error must fall by more than a factor of two. The comparison
    is cell average against cell average: the initial profile is the exact
    shell integral of the mode, and the analytic answer is that integral
    times exp(-D λ² t).
    """

    mu = _no_flux_eigenvalue()
    radius = 1.0
    diffusivity = 0.01
    duration = 0.05

    def error_at(cells: int) -> float:
        grid = spherical_bead_grid(radius, cells)
        lam = mu / radius
        mode = _shell_average_of_sinc_mode(grid.face_radius_m, lam)
        initial = [2.0 + 0.2 * value for value in mode]
        result = integrate_spherical_diffusion(
            radius_m=radius,
            density_kg_m3=1.0,
            initial_mass_fraction=initial,
            diffusivity_m2_s=diffusivity,
            surface_mass_flux_kg_m2_s=_zero_flux,
            t_end_s=duration,
            n_steps=400,
            n_cells=cells,
            theta=0.5,
        )
        decay = math.exp(-diffusivity * lam * lam * duration)
        expected = _eigenmode_expected(initial, decay)
        return max(
            abs(got - want) for got, want in zip(result.mass_fraction, expected)
        )

    coarse_24 = error_at(24)
    coarse_48 = error_at(48)
    fine_96 = error_at(96)
    assert coarse_24 > coarse_48 > fine_96
    assert fine_96 < coarse_48 / 2.0


def test_short_time_freezes_a_nonuniform_profile() -> None:
    """A duration far below the shell diffusion time leaves the profile.

    The Fourier number is ``D t / h^2`` with ``h = R / n_cells``. The
    profile may move by a fraction of its contrast of that order. Five
    times that product is the bound: an integrator that ignored ``t_end``
    and relaxed the profile would fail it.
    """

    cells = 8
    grid = spherical_bead_grid(_RADIUS, cells)
    initial = [0.01 * (1.0 - (radius / _RADIUS) ** 2) for radius in grid.centroid_radius_m]
    diffusivity = lowry_1982_na_diffusivity_m2_s(1640.0)
    duration = 1.0e-6
    result = integrate_spherical_diffusion(
        radius_m=_RADIUS,
        density_kg_m3=_DENSITY,
        initial_mass_fraction=initial,
        diffusivity_m2_s=diffusivity,
        surface_mass_flux_kg_m2_s=_zero_flux,
        t_end_s=duration,
        n_steps=4,
        n_cells=cells,
        theta=1.0,
    )
    contrast = max(initial) - min(initial)
    thickness = _RADIUS / cells
    fourier = diffusivity * duration / thickness**2
    bound = 5.0 * fourier * contrast
    assert bound < 0.01 * contrast
    moved = max(abs(got - start) for got, start in zip(result.mass_fraction, initial))
    assert moved < bound
    assert result.oxide_mass_kg(_DENSITY) == pytest.approx(
        oxide_mass_kg(initial, grid, _DENSITY),
        rel=1e-12,
    )


def test_fast_diffusion_and_slow_loss_stays_uniform_and_matches_the_mixed_limit() -> None:
    """Well-mixed bead: dc/dt = -3 J / (ρ R) with J = k c.

    The exponential is the integral of that ODE, not a solver output.
    """

    radius = 1.0e-4
    density = 3000.0
    coefficient = 1.0e-6
    initial = 0.004
    duration = 2000.0
    cells = 16

    def flux(_t_s: float, mass_fraction: float) -> float:
        return coefficient * mass_fraction

    result = integrate_spherical_diffusion(
        radius_m=radius,
        density_kg_m3=density,
        initial_mass_fraction=initial,
        diffusivity_m2_s=1.0e-8,
        surface_mass_flux_kg_m2_s=flux,
        t_end_s=duration,
        n_steps=80,
        n_cells=cells,
        theta=1.0,
    )
    expected = initial * math.exp(-3.0 * coefficient * duration / (density * radius))
    assert result.mass_fraction == pytest.approx([expected] * cells, rel=5e-3)
    spread = max(result.mass_fraction) - min(result.mass_fraction)
    assert spread < 0.01 * expected
    initial_mass = oxide_mass_kg([initial] * cells, result.grid, density)
    assert initial_mass - result.oxide_mass_kg(density) == pytest.approx(
        result.boundary_mass_out_kg,
        rel=1e-9,
    )


def test_surface_loss_is_the_only_oxide_mass_change() -> None:
    def flux(_t_s: float, mass_fraction: float) -> float:
        return 2.0e-7 * mass_fraction

    initial = 0.002
    result = integrate_spherical_diffusion(
        radius_m=_RADIUS,
        density_kg_m3=_DENSITY,
        initial_mass_fraction=initial,
        diffusivity_m2_s=1.0e-11,
        surface_mass_flux_kg_m2_s=flux,
        t_end_s=5.0,
        n_steps=25,
        n_cells=10,
        theta=1.0,
    )
    initial_mass = oxide_mass_kg([initial] * 10, result.grid, _DENSITY)
    assert initial_mass - result.oxide_mass_kg(_DENSITY) == pytest.approx(
        result.boundary_mass_out_kg,
        rel=1e-9,
    )


def test_missing_surface_flux_refuses() -> None:
    with pytest.raises(BeadDiffusionRefusal) as caught:
        integrate_spherical_diffusion(
            radius_m=_RADIUS,
            density_kg_m3=_DENSITY,
            initial_mass_fraction=0.001,
            diffusivity_m2_s=1.0e-12,
            surface_mass_flux_kg_m2_s=None,
            t_end_s=1.0,
            n_steps=2,
            n_cells=4,
        )
    assert caught.value.reason == "surface_flux_missing"


def test_bad_radius_and_negative_diffusivity_refuse() -> None:
    with pytest.raises(BeadDiffusionRefusal) as caught:
        spherical_bead_grid(0.0, 4)
    assert caught.value.reason == "radius_not_positive"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        integrate_spherical_diffusion(
            radius_m=_RADIUS,
            density_kg_m3=_DENSITY,
            initial_mass_fraction=0.001,
            diffusivity_m2_s=-1.0,
            surface_mass_flux_kg_m2_s=_zero_flux,
            t_end_s=1.0,
            n_steps=2,
            n_cells=4,
        )
    assert caught.value.reason == "diffusivity_not_finite"


def test_printed_na_and_cu_diffusivities_and_the_potassium_refusal() -> None:
    temperature = 1500.0
    assert lowry_1982_na_diffusivity_m2_s(temperature) == pytest.approx(
        math.exp(
            LOWRY_1982_NA_LN_D_INTERCEPT
            - LOWRY_1982_NA_LN_D_SLOPE_K / temperature
        )
    )
    assert ni_zhang_2016_cu_diffusivity_m2_s(temperature) == pytest.approx(
        math.exp(
            NI_ZHANG_2016_CU_LN_D_INTERCEPT
            - NI_ZHANG_2016_CU_LN_D_SLOPE_K / temperature
        )
    )
    # Supplement code: D = exp(Da - Db/T). These are the printed Da, Db.
    assert LOWRY_1982_NA_LN_D_INTERCEPT == -9.251
    assert LOWRY_1982_NA_LN_D_SLOPE_K == 19628.0
    assert NI_ZHANG_2016_CU_LN_D_INTERCEPT == -13.59
    assert NI_ZHANG_2016_CU_LN_D_SLOPE_K == 12153.0
    hotter = lowry_1982_na_diffusivity_m2_s(1700.0)
    colder = lowry_1982_na_diffusivity_m2_s(1200.0)
    assert hotter > colder > 0.0
    with pytest.raises(BeadDiffusionRefusal) as caught:
        potassium_tracer_diffusivity_m2_s(1640.0)
    assert caught.value.reason == "tracer_diffusivity_absent"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        lowry_1982_na_diffusivity_m2_s(0.0)
    assert caught.value.reason == "temperature_not_positive"


def _cold_space_radiative_half_time_s(
    *,
    radius_m: float,
    density_kg_m3: float,
    heat_capacity_j_kg_k: float,
    emissivity: float,
    initial_temperature_K: float,
) -> float:
    """Integral of dT/dt = -β T^4 from T0 to T0/2.

    β = 3 ε σ / (ρ cp R). The antiderivative gives
    t = 7 / (3 β T0^3). This is not the RK4 used by the solver.
    """

    beta = (
        3.0
        * emissivity
        * STEFAN_BOLTZMANN
        / (density_kg_m3 * heat_capacity_j_kg_k * radius_m)
    )
    return 7.0 / (3.0 * beta * initial_temperature_K**3)


def test_radiative_half_time_matches_the_cold_space_integral() -> None:
    radius = 1.16e-4
    density = 2800.0
    heat_capacity = 1400.0
    emissivity = 0.8
    initial = 1600.0
    got = cooling_half_time_s(
        radius_m=radius,
        density_kg_m3=density,
        heat_capacity_j_kg_k=heat_capacity,
        emissivity=emissivity,
        environment_temperature_K=0.0,
        convection_coefficient_w_m2_k=0.0,
        initial_temperature_K=initial,
    )
    expected = _cold_space_radiative_half_time_s(
        radius_m=radius,
        density_kg_m3=density,
        heat_capacity_j_kg_k=heat_capacity,
        emissivity=emissivity,
        initial_temperature_K=initial,
    )
    assert got == pytest.approx(expected, rel=1e-6)
    doubled = cooling_half_time_s(
        radius_m=2.0 * radius,
        density_kg_m3=density,
        heat_capacity_j_kg_k=heat_capacity,
        emissivity=emissivity,
        environment_temperature_K=0.0,
        convection_coefficient_w_m2_k=0.0,
        initial_temperature_K=initial,
    )
    assert doubled == pytest.approx(2.0 * got, rel=1e-6)


def test_convective_half_time_matches_the_newton_integral() -> None:
    """ε = 0 leaves dT/dt = -γ (T - T_env), γ = 3 h / (ρ cp R)."""

    radius = 2.0e-4
    density = 3000.0
    heat_capacity = 1200.0
    convection = 40.0
    initial = 1500.0
    environment = 300.0
    got = cooling_half_time_s(
        radius_m=radius,
        density_kg_m3=density,
        heat_capacity_j_kg_k=heat_capacity,
        emissivity=0.0,
        environment_temperature_K=environment,
        convection_coefficient_w_m2_k=convection,
        initial_temperature_K=initial,
    )
    gamma = 3.0 * convection / (density * heat_capacity * radius)
    expected = math.log((initial - environment) / (0.5 * initial - environment)) / gamma
    assert got == pytest.approx(expected, rel=1e-6)


def test_a_bead_that_is_not_cooling_refuses() -> None:
    with pytest.raises(BeadDiffusionRefusal) as caught:
        cooling_half_time_s(
            radius_m=1.0e-4,
            density_kg_m3=3000.0,
            heat_capacity_j_kg_k=1000.0,
            emissivity=1.0,
            environment_temperature_K=1640.0,
            convection_coefficient_w_m2_k=0.0,
            initial_temperature_K=1640.0,
        )
    assert caught.value.reason == "bead_not_cooling"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        cooling_half_time_s(
            radius_m=1.0e-4,
            density_kg_m3=3000.0,
            heat_capacity_j_kg_k=1000.0,
            emissivity=1.5,
            environment_temperature_K=0.0,
            convection_coefficient_w_m2_k=0.0,
            initial_temperature_K=1640.0,
        )
    assert caught.value.reason == "emissivity_out_of_range"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        cooling_half_time_s(
            radius_m=1.0e-4,
            density_kg_m3=3000.0,
            heat_capacity_j_kg_k=1000.0,
            emissivity=1.0,
            environment_temperature_K=0.0,
            convection_coefficient_w_m2_k=-1.0,
            initial_temperature_K=1640.0,
        )
    assert caught.value.reason == "convection_coefficient_negative"
    with pytest.raises(TypeError):
        cooling_half_time_s(  # type: ignore[call-arg]
            radius_m=1.0e-4,
            density_kg_m3=3000.0,
            heat_capacity_j_kg_k=1000.0,
            emissivity=1.0,
            environment_temperature_K=0.0,
            initial_temperature_K=1640.0,
        )


def test_zhang_law_passes_through_the_half_temperature() -> None:
    initial = 1640.0
    tau = 12.5
    assert zhang_2008_temperature_K(
        0.0,
        initial_temperature_K=initial,
        cooling_half_time_s=tau,
    ) == pytest.approx(initial)
    assert zhang_2008_temperature_K(
        tau,
        initial_temperature_K=initial,
        cooling_half_time_s=tau,
    ) == pytest.approx(initial / 2.0)
    end = 940.0
    duration = zhang_2008_duration_until_s(
        end,
        initial_temperature_K=initial,
        cooling_half_time_s=tau,
    )
    assert duration == pytest.approx(tau * (initial / end - 1.0))
    assert zhang_2008_temperature_K(
        duration,
        initial_temperature_K=initial,
        cooling_half_time_s=tau,
    ) == pytest.approx(end)


def test_fast_zhang_cooling_freezes_a_nonuniform_profile() -> None:
    cells = 8
    radius = 1.0e-4
    grid = spherical_bead_grid(radius, cells)
    initial = [0.01 * (1.0 - (r / radius) ** 2) for r in grid.centroid_radius_m]
    tau = 1.0e-12
    start = 1640.0
    end = 940.0
    duration = zhang_2008_duration_until_s(
        end,
        initial_temperature_K=start,
        cooling_half_time_s=tau,
    )
    diffusivity = diffusivity_along_zhang_cooling(
        lowry_1982_na_diffusivity_m2_s,
        initial_temperature_K=start,
        cooling_half_time_s=tau,
    )
    result = integrate_spherical_diffusion(
        radius_m=radius,
        density_kg_m3=_DENSITY,
        initial_mass_fraction=initial,
        diffusivity_m2_s=diffusivity,
        surface_mass_flux_kg_m2_s=_zero_flux,
        t_end_s=duration,
        n_steps=4,
        n_cells=cells,
        theta=1.0,
    )
    contrast = max(initial) - min(initial)
    thickness = radius / cells
    fourier = lowry_1982_na_diffusivity_m2_s(start) * duration / thickness**2
    moved = max(abs(got - start_c) for got, start_c in zip(result.mass_fraction, initial))
    assert moved < 5.0 * fourier * contrast


def test_slow_cooling_relaxes_a_profile_toward_its_conserved_mean() -> None:
    cells = 12
    radius = 1.0
    grid = spherical_bead_grid(radius, cells)
    initial = [0.2 + 0.1 * (index / (cells - 1)) for index in range(cells)]
    diffusivity = diffusivity_along_zhang_cooling(
        lambda _temperature: 0.2,
        initial_temperature_K=1000.0,
        cooling_half_time_s=1.0e6,
    )
    result = integrate_spherical_diffusion(
        radius_m=radius,
        density_kg_m3=1.0,
        initial_mass_fraction=initial,
        diffusivity_m2_s=diffusivity,
        surface_mass_flux_kg_m2_s=_zero_flux,
        t_end_s=3.0,
        n_steps=30,
        n_cells=cells,
        theta=1.0,
    )
    mean = oxide_mass_kg(initial, grid, 1.0) / sum(grid.volume_m3)
    assert result.oxide_mass_kg(1.0) == pytest.approx(
        oxide_mass_kg(initial, grid, 1.0),
        rel=1e-12,
    )
    assert max(result.mass_fraction) - min(result.mass_fraction) < 0.05 * (
        max(initial) - min(initial)
    )
    assert result.mass_fraction == pytest.approx([mean] * cells, rel=0.0, abs=0.01)


_HOST = {
    "SiO2": 45.0,
    "TiO2": 8.0,
    "Al2O3": 7.0,
    "FeO": 22.0,
    "MgO": 12.0,
    "CaO": 6.0,
}


def _predict_sodium(**overrides: object):
    arguments = dict(
        component="Na2O",
        diameter_m=2.0e-4,
        host_oxide_wt_percent=_HOST,
        initial_wt_percent=0.35,
        initial_temperature_K=1640.0,
        end_temperature_K=940.0,
        density_kg_m3=2862.0,
        heat_capacity_j_kg_k=1522.0,
        emissivity=1.0,
        environment_temperature_K=0.0,
        convection_coefficient_w_m2_k=0.0,
        ambient_p_na_pa=0.0,
        ambient_p_o2_bar=1.0e-10,
        n_cells=6,
        n_steps=8,
        property_flags=("density_sfb_room_temperature_glass_proxy",),
    )
    arguments.update(overrides)
    return predict_bead_profile(**arguments)


def test_sodium_flux_matches_catalog_pressure_and_langmuir_difference() -> None:
    """The expected flux is the catalog pressure through the Langmuir owner.

    The factor 1/2 is the NaO0.5 stoichiometry: one mole of Na(g) removes
    half a mole of Na2O. The test does not re-type the Antoine coefficients.
    """

    from simulator.vapour_rail.catalog import compile_vapour_rail_catalog
    from simulator.yaml_cache import load_cached_safe_yaml

    temperature = 1500.0
    fraction = 0.0035
    oxygen = 1.0e-10
    vacuum = na2o_surface_mass_flux(
        temperature,
        fraction,
        host_oxide_wt_percent=_HOST,
        ambient_p_na_pa=0.0,
        ambient_p_o2_bar=oxygen,
    )
    account = na2o_relative_moles(fraction, _HOST)
    activity = melt_oxide_activity("Na2O", account, temperature_K=temperature)
    assert activity is not None
    payload = load_cached_safe_yaml(
        (Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml").read_text(
            encoding="utf-8"
        )
    )
    catalog = compile_vapour_rail_catalog(payload)
    evaluation = catalog.species["Na"].evaluator.evaluate(
        temperature,
        source_activity=activity.activity,
        pO2_bar=oxygen,
    )
    assert vacuum.equilibrium_p_na_pa == pytest.approx(evaluation.pressure_pa)
    assert vacuum.vapour_pressure_out_of_range is False
    molar_mass = species_molar_mass_kg_mol("Na")
    outward = langmuir_molar_flux(
        temperature,
        evaluation.pressure_pa,
        0.0,
        vacuum.evaporation_alpha,
        molar_mass_kg_mol=molar_mass,
    )
    inward = langmuir_molar_flux(
        temperature,
        0.0,
        evaluation.pressure_pa,
        vacuum.evaporation_alpha,
        molar_mass_kg_mol=molar_mass,
    )
    expected = (outward - inward) * (oxide_molar_mass_g_mol("Na2O") / 1000.0) / 2.0
    assert vacuum.na2o_mass_flux_kg_m2_s == pytest.approx(expected)
    assert vacuum.na2o_mass_flux_kg_m2_s > 0.0
    rich = na2o_surface_mass_flux(
        temperature,
        fraction,
        host_oxide_wt_percent=_HOST,
        ambient_p_na_pa=evaluation.pressure_pa * 10.0,
        ambient_p_o2_bar=oxygen,
    )
    assert rich.na2o_mass_flux_kg_m2_s < 0.0
    dry = na2o_surface_mass_flux(
        temperature,
        0.0,
        host_oxide_wt_percent=_HOST,
        ambient_p_na_pa=0.0,
        ambient_p_o2_bar=oxygen,
    )
    assert dry.equilibrium_p_na_pa == 0.0
    assert dry.na2o_mass_flux_kg_m2_s == 0.0
    hot = na2o_surface_mass_flux(
        1640.0,
        fraction,
        host_oxide_wt_percent=_HOST,
        ambient_p_na_pa=0.0,
        ambient_p_o2_bar=oxygen,
    )
    assert hot.vapour_pressure_out_of_range is True
    assert hot.evaporation_alpha_outside_stated_band is True
    assert "out_of_gamma_domain" in hot.gamma_authority_status


def test_missing_ambient_and_potassium_and_copper_refuse() -> None:
    with pytest.raises(BeadDiffusionRefusal) as caught:
        _predict_sodium(ambient_p_na_pa=None)
    assert caught.value.reason == "ambient_p_na_missing"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        _predict_sodium(ambient_p_o2_bar=0.0)
    assert caught.value.reason == "ambient_p_o2_invalid"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        _predict_sodium(component="K2O")
    assert caught.value.reason == "tracer_diffusivity_absent"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        _predict_sodium(component="Cu")
    assert caught.value.reason == "cu_melt_volatility_not_a_silicate_carrier"
    with pytest.raises(BeadDiffusionRefusal) as caught:
        _predict_sodium(host_oxide_wt_percent={**_HOST, "Na2O": 0.35})
    assert caught.value.reason == "host_na2o_not_separated"
    with pytest.raises(TypeError):
        predict_bead_profile(  # type: ignore[call-arg]
            component="Na2O",
            diameter_m=2.0e-4,
            host_oxide_wt_percent=_HOST,
            initial_wt_percent=0.35,
            initial_temperature_K=1640.0,
            end_temperature_K=940.0,
            density_kg_m3=2862.0,
            heat_capacity_j_kg_k=1522.0,
            emissivity=1.0,
            environment_temperature_K=0.0,
            ambient_p_na_pa=0.0,
            ambient_p_o2_bar=1.0e-10,
            n_cells=4,
            n_steps=4,
            property_flags=(),
        )


def test_prediction_flags_ambient_and_loses_more_sodium_at_lower_fO2() -> None:
    low = _predict_sodium(ambient_p_o2_bar=1.0e-12)
    high = _predict_sodium(ambient_p_o2_bar=1.0e-8)
    assert "not_fitted_to_su_zhang_profiles" in low.flags
    assert "thermal_history:zhang_2008_with_heat_loss_half_time" in low.flags
    assert "ambient_p_na_pa=0" in low.flags
    assert "ambient_p_o2_bar=1e-12" in low.flags
    assert "alpha_authority_status:analytical_upper_bound" in low.flags
    assert "vapour_pressure_out_of_range" in low.flags
    assert "density_sfb_room_temperature_glass_proxy" in low.flags
    assert any(flag.startswith("gamma_authority_status:") for flag in low.flags)
    assert low.tau_s > 0.0
    assert low.oxide_mass_initial_kg - low.oxide_mass_final_kg == pytest.approx(
        low.boundary_mass_out_kg,
        rel=1e-9,
    )
    assert low.boundary_mass_out_kg > high.boundary_mass_out_kg > 0.0
    source = (
        Path(__file__).resolve().parents[1] / "simulator" / "bead_diffusion.py"
    ).read_text(encoding="utf-8")
    for token in ("0.145", "1183", "6001"):
        assert token not in source


def test_traverse_coordinate_is_distance_from_the_centre() -> None:
    """Printed distance is measured from one surface. r = |x - D/2|."""

    located = radius_from_surface_traverse(
        1.2,
        232.0,
        distance_uncertainty_um=1.0,
    )
    assert located.radius_m == pytest.approx(114.8e-6)
    assert located.clipped_to_surface is False
    centre = radius_from_surface_traverse(
        116.0,
        232.0,
        distance_uncertainty_um=1.0,
    )
    assert centre.radius_m == pytest.approx(0.0)
    clipped = radius_from_surface_traverse(
        -0.4,
        232.0,
        distance_uncertainty_um=1.0,
    )
    assert clipped.clipped_to_surface is True
    assert clipped.radius_m == pytest.approx(116.0e-6)
    with pytest.raises(BeadDiffusionRefusal) as caught:
        radius_from_surface_traverse(
            300.0,
            232.0,
            distance_uncertainty_um=1.0,
        )
    assert caught.value.reason == "radial_position_outside_bead"


def test_profile_interpolation_holds_the_end_cells() -> None:
    grid = spherical_bead_grid(1.0, 4)
    values = list(grid.centroid_radius_m)
    midpoint = 0.5 * (values[1] + values[2])
    assert wt_percent_at_radius(grid, values, midpoint) == pytest.approx(midpoint)
    assert wt_percent_at_radius(grid, values, 0.0) == pytest.approx(values[0])
    assert wt_percent_at_radius(grid, values, grid.radius_m) == pytest.approx(values[-1])


def test_profile_point_residual_uses_the_bead_diameter() -> None:
    grid = spherical_bead_grid(116.0e-6, 4)
    built = BeadProfilePrediction(
        component="Na2O",
        grid=grid,
        mass_fraction=(0.0035, 0.0035, 0.0035, 0.0035),
        wt_percent=(0.35, 0.35, 0.35, 0.35),
        tau_s=1.0,
        t_end_s=1.0,
        initial_temperature_K=1640.0,
        end_temperature_K=940.0,
        flags=("not_fitted_to_su_zhang_profiles", "ambient_p_na_pa=0"),
        oxide_mass_initial_kg=1.0,
        oxide_mass_final_kg=1.0,
        boundary_mass_out_kg=0.0,
        step_count=1,
    )
    comparison = compare_profile_point(
        built,
        radial_distance_um=1.2,
        sample_diameter_um=232.0,
        printed_wt_percent=0.381,
        distance_uncertainty_um=1.0,
    )
    assert comparison.radius_m == pytest.approx(114.8e-6)
    assert comparison.predicted_wt_percent == pytest.approx(0.35)
    assert comparison.residual_wt_percent == pytest.approx(0.35 - 0.381)
    with pytest.raises(BeadDiffusionRefusal) as caught:
        compare_profile_point(
            built,
            radial_distance_um=1.2,
            sample_diameter_um=210.0,
            printed_wt_percent=0.381,
            distance_uncertainty_um=1.0,
        )
    assert caught.value.reason == "bead_diameter_mismatch"


_EXTRACT = (
    Path(__file__).resolve().parents[1]
    / "data/literature/extracts/su-zhang-2023-lunar-orange-glass-na-k-cu-degassing.yaml"
)
_EMPA_HOST_OXIDES = (
    "SiO2",
    "TiO2",
    "Al2O3",
    "Cr2O3",
    "FeO",
    "MnO",
    "MgO",
    "CaO",
    "K2O",
)
_DRY_RUN_BEADS = ("GB-B", "GB-G1", "GB-S6")
_DRY_RUN_FLAGS = (
    "heat_capacity_sfb_basalt_proxy",
    "density_sfb_room_temperature_glass_proxy",
    "emissivity_blackbody_upper_bound",
    "environment_temperature_cold_space",
    "convection_absent",
    "end_temperature_hui_2018_average_tg",
)


def _extract_document() -> dict:
    return yaml.safe_load(_EXTRACT.read_text(encoding="utf-8"))


def _table_s2_series(document: dict, oxide: str) -> list[dict]:
    observation_id = f"su_zhang_2023_table_s2_empa_{oxide.lower()}_profile"
    observations = document["species"][oxide]["observations"]
    row = next(item for item in observations if item["observation_id"] == observation_id)
    return row["values"]["series"]


def table_s2_bead(document: dict, sample: str) -> dict:
    """Interior EMPA host and the printed Na2O traverse for one bead.

    The interior point is the analysis closest to the traverse centre.
    Measured Na2O is not copied into the host. The initial sodium used by
    the predictor stays a separate argument.
    """

    series = {
        oxide: [
            point
            for point in _table_s2_series(document, oxide)
            if point["sample"] == sample
        ]
        for oxide in _EMPA_HOST_OXIDES
    }
    na_points = [
        point
        for point in _table_s2_series(document, "Na2O")
        if point["sample"] == sample
    ]
    if not na_points or any(not points for points in series.values()):
        raise BeadDiffusionRefusal(
            "profile_points_missing",
            f"Table S2 is missing an EMPA series for {sample}",
        )
    diameter = float(na_points[0]["sample_diameter_um"])
    if any(float(point["sample_diameter_um"]) != diameter for point in na_points):
        raise BeadDiffusionRefusal(
            "bead_diameter_mismatch",
            f"{sample} Na2O points do not share one diameter",
        )
    host: dict[str, float] = {}
    unmeasured: list[str] = []
    for oxide, points in series.items():
        interior = min(
            points,
            key=lambda point: (
                abs(float(point["radial_distance_um"]) - diameter / 2.0),
                float(point["radial_distance_um"]),
            ),
        )
        raw = interior[f"{oxide}_wt_pct"]
        # A blank EMPA cell is an unmeasured oxide. It is reported and left
        # out of the host. It is not stored as zero.
        if raw is None:
            unmeasured.append(oxide)
            continue
        host[oxide] = float(raw)
    printed = []
    for point in na_points:
        raw_na = point["Na2O_wt_pct"]
        if raw_na is None:
            raise BeadDiffusionRefusal(
                "printed_wt_percent_invalid",
                f"{sample} Na2O at {point['radial_distance_um']} µm is blank",
            )
        printed.append(
            {
                "radial_distance_um": float(point["radial_distance_um"]),
                "na2o_wt_pct": float(raw_na),
                "sample_diameter_um": float(point["sample_diameter_um"]),
            }
        )
    return {
        "sample": sample,
        "diameter_um": diameter,
        "host": host,
        "unmeasured_oxides": unmeasured,
        "points": printed,
    }


def test_three_bead_dry_run_residuals_are_finite_and_flagged() -> None:
    """GB-B, GB-G1 and GB-S6 at one stated ambient. Residuals are not a score.

    The grid here is coarser than the reported dry run. It checks that every
    printed Table S2 point gets a finite residual and that the ambient and
    property flags are on the prediction.
    """

    document = _extract_document()
    expected_counts = {"GB-B": 20, "GB-G1": 36, "GB-S6": 10}
    expected_diameters = {"GB-B": 210.0, "GB-G1": 232.0, "GB-S6": 127.0}
    g1 = table_s2_bead(document, "GB-G1")
    first = g1["points"][0]
    assert first["radial_distance_um"] == pytest.approx(1.2)
    assert first["na2o_wt_pct"] == pytest.approx(0.381)
    for sample in _DRY_RUN_BEADS:
        bead = table_s2_bead(document, sample)
        assert len(bead["points"]) == expected_counts[sample]
        assert bead["diameter_um"] == pytest.approx(expected_diameters[sample])
        notices = tuple(
            f"host_oxide_unmeasured:{oxide}" for oxide in bead["unmeasured_oxides"]
        )
        prediction = predict_bead_profile(
            component="Na2O",
            diameter_m=bead["diameter_um"] * 1.0e-6,
            host_oxide_wt_percent=bead["host"],
            initial_wt_percent=0.35,
            initial_temperature_K=1640.0,
            end_temperature_K=940.0,
            density_kg_m3=2862.0,
            heat_capacity_j_kg_k=1522.0,
            emissivity=1.0,
            environment_temperature_K=0.0,
            convection_coefficient_w_m2_k=0.0,
            ambient_p_na_pa=0.0,
            ambient_p_o2_bar=1.0e-10,
            n_cells=8,
            n_steps=6,
            property_flags=_DRY_RUN_FLAGS + notices,
        )
        assert "ambient_p_na_pa=0" in prediction.flags
        assert "ambient_p_o2_bar=1e-10" in prediction.flags
        assert "not_fitted_to_su_zhang_profiles" in prediction.flags
        for flag in _DRY_RUN_FLAGS + notices:
            assert flag in prediction.flags
        if sample == "GB-S6":
            assert bead["unmeasured_oxides"] == ["Cr2O3", "MnO"]
        else:
            assert bead["unmeasured_oxides"] == []
        for point in bead["points"]:
            comparison = compare_profile_point(
                prediction,
                radial_distance_um=point["radial_distance_um"],
                sample_diameter_um=point["sample_diameter_um"],
                printed_wt_percent=point["na2o_wt_pct"],
                distance_uncertainty_um=1.0,
            )
            assert math.isfinite(comparison.residual_wt_percent)
            assert comparison.clipped_to_surface is False
