"""Spherical bead diffusion: mass, analytic decay, and the frozen-bead limit.

Expected numbers are the sphere volume, the no-flux Bessel root, and the
printed Lowry / Ni and Zhang Arrhenius expressions. They are not copied
from the solver's running output.
"""

from __future__ import annotations

import math

import pytest

from simulator.bead_diffusion import (
    LOWRY_1982_NA_LN_D_INTERCEPT,
    LOWRY_1982_NA_LN_D_SLOPE_K,
    NI_ZHANG_2016_CU_LN_D_INTERCEPT,
    NI_ZHANG_2016_CU_LN_D_SLOPE_K,
    BeadDiffusionRefusal,
    integrate_spherical_diffusion,
    lowry_1982_na_diffusivity_m2_s,
    ni_zhang_2016_cu_diffusivity_m2_s,
    oxide_mass_kg,
    potassium_tracer_diffusivity_m2_s,
    spherical_bead_grid,
)

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
