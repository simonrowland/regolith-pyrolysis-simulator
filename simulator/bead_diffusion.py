"""Radial diffusion in a cooling glass bead.

The equation is the constant-density spherical diffusion equation

    dc/dt = (1/r^2) d/dr (r^2 D(T) dc/dr)

with c the mass fraction of one dilute oxide. A finite-volume shell
discretisation keeps the oxide mass change equal to the surface integral
of the boundary flux. Crank–Nicolson (theta = 1/2) or backward Euler
(theta = 1) advances the diffusive operator. The surface flux is lagged
over a step so the loss term is a known number and the shell fluxes still
telescope.

D(T) for Na and Cu are the literature expressions Su and Zhang print
(thesis Chapter 3, printed page 61; supplement fit code
``D = exp(Da - Db/T)``). They are not fits to the 74220 profiles. K has
no printed tracer diffusivity, so a potassium call refuses.

This module does not read the paper's fitted tau, a0 or b0.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

# Lowry et al. 1982, as printed by Su and Zhang, thesis Chapter 3 printed
# page 61 (physical PDF page 76). Supplement DOCX: Da = -9.251, Db = 19628,
# D0 = exp(Da - Db/T) m^2/s. Units: Db is kelvin, D is m^2/s.
LOWRY_1982_NA_LN_D_INTERCEPT = -9.251
LOWRY_1982_NA_LN_D_SLOPE_K = 19628.0

# Ni and Zhang 2016, same page and the same supplement form:
# Da = -13.59, Db = 12153, D in m^2/s.
NI_ZHANG_2016_CU_LN_D_INTERCEPT = -13.59
NI_ZHANG_2016_CU_LN_D_SLOPE_K = 12153.0

Diffusivity = Callable[[float], float]
SurfaceMassFlux = Callable[[float, float], float]


class BeadDiffusionRefusal(ValueError):
    """A bead-diffusion input that cannot be given an ordinary value."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = str(reason)
        super().__init__(detail or self.reason)


@dataclass(frozen=True)
class SphericalBeadGrid:
    """Equal-thickness spherical shells from the centre to the surface.

    Face ``j`` lies at ``j * R / n``. Cell ``j`` is the shell between face
    ``j`` and face ``j + 1``. The centroid is the volume centroid,

        r_c = (3/4) (r_out^4 - r_in^4) / (r_out^3 - r_in^3),

    which is ``(3/4) r_out`` for the centre shell. Volumes sum to the
    sphere volume. Face areas are ``4 π r^2``.
    """

    radius_m: float
    n_cells: int
    face_radius_m: tuple[float, ...]
    centroid_radius_m: tuple[float, ...]
    volume_m3: tuple[float, ...]
    face_area_m2: tuple[float, ...]


@dataclass(frozen=True)
class BeadDiffusionResult:
    """One finished radial profile.

    ``boundary_mass_out_kg`` is the oxide mass that crossed the surface
    during the run, positive when the bead lost mass. With a zero surface
    flux it is zero and the oxide mass is unchanged.
    """

    grid: SphericalBeadGrid
    mass_fraction: tuple[float, ...]
    boundary_mass_out_kg: float
    step_count: int
    t_end_s: float

    def oxide_mass_kg(self, density_kg_m3: float) -> float:
        return sum(
            density_kg_m3 * fraction * volume
            for fraction, volume in zip(self.mass_fraction, self.grid.volume_m3)
        )


def _finite_number(value: float, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BeadDiffusionRefusal(reason, f"{reason}: {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise BeadDiffusionRefusal(reason, f"{reason}: {value!r}")
    return number


def _positive_temperature_K(temperature_K: float) -> float:
    temperature = _finite_number(temperature_K, "temperature_not_positive")
    if temperature <= 0.0:
        raise BeadDiffusionRefusal(
            "temperature_not_positive",
            f"temperature_K must be finite and positive, got {temperature_K!r}",
        )
    return temperature


def _arrhenius_diffusivity_m2_s(
    temperature_K: float,
    *,
    intercept: float,
    slope_K: float,
) -> float:
    """``D = exp(intercept - slope_K / T)`` in m^2/s.

    Premise: the printed tracer diffusivity is Arrhenius in 1/T.
    Algebra: the supplement evaluates ``exp(Da - Db/T)`` with Da the
    intercept and Db the slope in kelvin. Unit check: the exponent is
    dimensionless and D is m^2/s. Sanity: D increases with T and is
    positive at every finite positive temperature.
    """

    temperature = _positive_temperature_K(temperature_K)
    diffusivity = math.exp(intercept - slope_K / temperature)
    if not math.isfinite(diffusivity) or diffusivity < 0.0:
        raise BeadDiffusionRefusal(
            "diffusivity_not_finite",
            f"Arrhenius diffusivity was {diffusivity!r} at {temperature} K",
        )
    return diffusivity


def lowry_1982_na_diffusivity_m2_s(temperature_K: float) -> float:
    """Na tracer diffusivity, Lowry et al. 1982, m^2/s.

    Printed by Su and Zhang on thesis page 61 and in the supplement as
    ``exp(-9.251 - 19628/T)``.
    """

    return _arrhenius_diffusivity_m2_s(
        temperature_K,
        intercept=LOWRY_1982_NA_LN_D_INTERCEPT,
        slope_K=LOWRY_1982_NA_LN_D_SLOPE_K,
    )


def ni_zhang_2016_cu_diffusivity_m2_s(temperature_K: float) -> float:
    """Cu tracer diffusivity, Ni and Zhang 2016, m^2/s.

    Printed by Su and Zhang on thesis page 61 and in the supplement as
    ``exp(-13.59 - 12153/T)``.
    """

    return _arrhenius_diffusivity_m2_s(
        temperature_K,
        intercept=NI_ZHANG_2016_CU_LN_D_INTERCEPT,
        slope_K=NI_ZHANG_2016_CU_LN_D_SLOPE_K,
    )


def potassium_tracer_diffusivity_m2_s(temperature_K: float) -> float:
    """Refuse. The source prints K profiles and no K tracer diffusivity."""

    _positive_temperature_K(temperature_K)
    raise BeadDiffusionRefusal(
        "tracer_diffusivity_absent",
        "no K tracer diffusivity is printed; Su and Zhang do not fit K",
    )


def spherical_bead_grid(radius_m: float, n_cells: int) -> SphericalBeadGrid:
    """Build equal-thickness shells. ``n_cells`` < 1 refuses."""

    radius = _finite_number(radius_m, "radius_not_positive")
    if radius <= 0.0:
        raise BeadDiffusionRefusal(
            "radius_not_positive",
            f"radius_m must be finite and positive, got {radius_m!r}",
        )
    if isinstance(n_cells, bool) or not isinstance(n_cells, int) or n_cells < 1:
        raise BeadDiffusionRefusal(
            "n_cells_invalid",
            f"n_cells must be an integer >= 1, got {n_cells!r}",
        )
    thickness = radius / n_cells
    face_radius = [index * thickness for index in range(n_cells + 1)]
    face_radius[-1] = radius
    volumes: list[float] = []
    centroids: list[float] = []
    for index in range(n_cells):
        inner = face_radius[index]
        outer = face_radius[index + 1]
        volumes.append((4.0 / 3.0) * math.pi * (outer**3 - inner**3))
        if inner == 0.0:
            centroids.append(0.75 * outer)
        else:
            centroids.append(
                0.75 * (outer**4 - inner**4) / (outer**3 - inner**3)
            )
    areas = [4.0 * math.pi * face * face for face in face_radius]
    return SphericalBeadGrid(
        radius_m=radius,
        n_cells=n_cells,
        face_radius_m=tuple(face_radius),
        centroid_radius_m=tuple(centroids),
        volume_m3=tuple(volumes),
        face_area_m2=tuple(areas),
    )


def _diffusivity_at(diffusivity: Diffusivity | float, t_s: float) -> float:
    if callable(diffusivity):
        value = diffusivity(t_s)
    else:
        value = diffusivity
    number = _finite_number(value, "diffusivity_not_finite")
    if number < 0.0:
        raise BeadDiffusionRefusal(
            "diffusivity_not_finite",
            f"diffusivity must be >= 0, got {value!r}",
        )
    return number


def _conductance(
    diffusivity_m2_s: float,
    density_kg_m3: float,
    grid: SphericalBeadGrid,
) -> list[float]:
    """Mass conductance (kg/s per unit mass-fraction) on each interior face.

    Premise: oxide mass flux is ``-ρ D dc/dr`` for constant density.
    Algebra: between cell centroids the rate from left to right is
    ``G (c_left - c_right)`` with ``G = ρ D A / Δr_centroid``.
    Unit check: kg/m3 * m^2/s * m^2 / m = kg/s. Sanity: ``D = 0`` gives
    no interior exchange.
    """

    conductance = [0.0] * (grid.n_cells + 1)
    for face in range(1, grid.n_cells):
        gap = grid.centroid_radius_m[face] - grid.centroid_radius_m[face - 1]
        if gap <= 0.0:
            raise BeadDiffusionRefusal(
                "centroid_gap_not_positive",
                f"centroid gap at face {face} was {gap}",
            )
        conductance[face] = (
            density_kg_m3
            * diffusivity_m2_s
            * grid.face_area_m2[face]
            / gap
        )
    return conductance


def _thomas(lower: list[float], diag: list[float], upper: list[float], rhs: list[float]) -> list[float]:
    """Solve a tridiagonal system. ``diag`` is overwritten."""

    size = len(diag)
    if size == 0 or len(lower) != size or len(upper) != size or len(rhs) != size:
        raise BeadDiffusionRefusal("linear_system_shape")
    for index in range(1, size):
        if diag[index - 1] == 0.0:
            raise BeadDiffusionRefusal("linear_system_singular")
        factor = lower[index] / diag[index - 1]
        diag[index] -= factor * upper[index - 1]
        rhs[index] -= factor * rhs[index - 1]
    if diag[-1] == 0.0:
        raise BeadDiffusionRefusal("linear_system_singular")
    solution = [0.0] * size
    solution[-1] = rhs[-1] / diag[-1]
    for index in range(size - 2, -1, -1):
        solution[index] = (rhs[index] - upper[index] * solution[index + 1]) / diag[index]
    return solution


def _diffusion_step(
    mass_fraction: list[float],
    *,
    grid: SphericalBeadGrid,
    density_kg_m3: float,
    dt_s: float,
    theta: float,
    conductance_old: list[float],
    conductance_new: list[float],
    surface_mass_out_kg_s: float,
) -> list[float]:
    """One theta-method step. Surface loss is an explicit mass rate.

    The last shell's right-hand side loses ``dt * J * A``. Interior face
    terms cancel when the shells are summed, so the oxide-mass change of
    the step is exactly that surface loss (to roundoff).
    """

    count = grid.n_cells
    lower = [0.0] * count
    diag = [0.0] * count
    upper = [0.0] * count
    rhs = [0.0] * count
    for index in range(count):
        capacity = density_kg_m3 * grid.volume_m3[index]
        left_new = conductance_new[index]
        right_new = conductance_new[index + 1] if index + 1 < count else 0.0
        left_old = conductance_old[index]
        right_old = conductance_old[index + 1] if index + 1 < count else 0.0
        diag[index] = capacity + dt_s * theta * (left_new + right_new)
        if index > 0:
            lower[index] = -dt_s * theta * left_new
        if index + 1 < count:
            upper[index] = -dt_s * theta * right_new
        inflow_old = 0.0
        if index > 0:
            inflow_old += left_old * (
                mass_fraction[index - 1] - mass_fraction[index]
            )
        if index + 1 < count:
            inflow_old -= right_old * (
                mass_fraction[index] - mass_fraction[index + 1]
            )
        rhs[index] = (
            capacity * mass_fraction[index]
            + dt_s * (1.0 - theta) * inflow_old
        )
    rhs[-1] -= dt_s * surface_mass_out_kg_s
    return _thomas(lower, diag, upper, rhs)


def integrate_spherical_diffusion(
    *,
    radius_m: float,
    density_kg_m3: float,
    initial_mass_fraction: float | Sequence[float],
    diffusivity_m2_s: Diffusivity | float,
    surface_mass_flux_kg_m2_s: SurfaceMassFlux | None,
    t_end_s: float,
    n_steps: int,
    n_cells: int,
    theta: float = 0.5,
) -> BeadDiffusionResult:
    """Integrate the spherical diffusion equation out to ``t_end_s``.

    ``surface_mass_flux_kg_m2_s(t, c_outer)`` is kg of oxide per square
    metre per second, positive outward. A missing flux refuses
    (``surface_flux_missing``); the no-evaporation limit is an explicit
    zero function, not a default. The outer-cell mass fraction is the
    surface state. That is first order in the shell thickness and
    converges as ``n_cells`` grows.

    A step that would drive a shell negative is halved and retried.
    ``n_steps`` is the requested uniform count; retries add to
    ``step_count``. Sanity: zero flux conserves oxide mass; a very short
    ``t_end_s`` leaves the initial profile in place; fast interior
    diffusion plus a slow surface loss stays nearly uniform while the
    bead depletes.
    """

    density = _finite_number(density_kg_m3, "density_not_positive")
    if density <= 0.0:
        raise BeadDiffusionRefusal(
            "density_not_positive",
            f"density_kg_m3 must be finite and positive, got {density_kg_m3!r}",
        )
    t_end = _finite_number(t_end_s, "t_end_not_positive")
    if t_end < 0.0:
        raise BeadDiffusionRefusal(
            "t_end_not_positive",
            f"t_end_s must be >= 0, got {t_end_s!r}",
        )
    if isinstance(n_steps, bool) or not isinstance(n_steps, int) or n_steps < 1:
        raise BeadDiffusionRefusal(
            "n_steps_invalid",
            f"n_steps must be an integer >= 1, got {n_steps!r}",
        )
    theta_value = _finite_number(theta, "theta_out_of_range")
    if not 0.0 <= theta_value <= 1.0:
        raise BeadDiffusionRefusal(
            "theta_out_of_range",
            f"theta must lie in [0, 1], got {theta!r}",
        )
    if surface_mass_flux_kg_m2_s is None or not callable(surface_mass_flux_kg_m2_s):
        raise BeadDiffusionRefusal(
            "surface_flux_missing",
            "surface mass flux is required; pass a zero function for no evaporation",
        )
    if not callable(diffusivity_m2_s) and not isinstance(
        diffusivity_m2_s, (int, float)
    ):
        raise BeadDiffusionRefusal(
            "diffusivity_missing",
            "diffusivity must be a number or a function of time",
        )
    grid = spherical_bead_grid(radius_m, n_cells)
    if isinstance(initial_mass_fraction, Sequence) and not isinstance(
        initial_mass_fraction, (str, bytes)
    ):
        if len(initial_mass_fraction) != grid.n_cells:
            raise BeadDiffusionRefusal(
                "initial_profile_length",
                "initial_mass_fraction length must equal n_cells",
            )
        profile = [
            _finite_number(value, "initial_mass_fraction_invalid")
            for value in initial_mass_fraction
        ]
    else:
        uniform = _finite_number(
            initial_mass_fraction, "initial_mass_fraction_invalid"
        )
        profile = [uniform] * grid.n_cells
    if any(value < 0.0 for value in profile):
        raise BeadDiffusionRefusal(
            "initial_mass_fraction_invalid",
            "initial mass fraction must be >= 0",
        )
    if t_end == 0.0:
        return BeadDiffusionResult(
            grid=grid,
            mass_fraction=tuple(profile),
            boundary_mass_out_kg=0.0,
            step_count=0,
            t_end_s=0.0,
        )

    base_dt = t_end / n_steps
    time = 0.0
    mass_out = 0.0
    step_count = 0
    max_steps = n_steps * 10000
    while time < t_end:
        if step_count >= max_steps:
            raise BeadDiffusionRefusal(
                "time_step_collapsed",
                f"stopped after {step_count} steps at t={time}",
            )
        remaining = t_end - time
        dt = min(base_dt, remaining)
        accepted = False
        for _attempt in range(60):
            diffusivity_old = _diffusivity_at(diffusivity_m2_s, time)
            diffusivity_new = _diffusivity_at(diffusivity_m2_s, time + dt)
            flux = surface_mass_flux_kg_m2_s(time, profile[-1])
            flux_value = _finite_number(flux, "surface_flux_not_finite")
            surface_rate = flux_value * grid.face_area_m2[-1]
            outer_mass = density * profile[-1] * grid.volume_m3[-1]
            if surface_rate > 0.0 and outer_mass > 0.0:
                safe_dt = 0.25 * outer_mass / surface_rate
                if dt > safe_dt and safe_dt > 0.0:
                    dt = safe_dt
                    continue
            updated = _diffusion_step(
                profile,
                grid=grid,
                density_kg_m3=density,
                dt_s=dt,
                theta=theta_value,
                conductance_old=_conductance(diffusivity_old, density, grid),
                conductance_new=_conductance(diffusivity_new, density, grid),
                surface_mass_out_kg_s=surface_rate,
            )
            if any(not math.isfinite(value) for value in updated):
                raise BeadDiffusionRefusal(
                    "concentration_not_finite",
                    "a shell mass fraction became non-finite",
                )
            if any(value < -1e-12 for value in updated):
                dt *= 0.5
                if dt < base_dt * 1e-12:
                    raise BeadDiffusionRefusal(
                        "concentration_would_go_negative",
                        "surface loss emptied a shell inside the step floor",
                    )
                continue
            profile = updated
            mass_out += surface_rate * dt
            time += dt
            step_count += 1
            accepted = True
            break
        if not accepted:
            raise BeadDiffusionRefusal(
                "concentration_would_go_negative",
                "surface loss emptied a shell inside the step floor",
            )
    return BeadDiffusionResult(
        grid=grid,
        mass_fraction=tuple(profile),
        boundary_mass_out_kg=mass_out,
        step_count=step_count,
        t_end_s=time,
    )


def oxide_mass_kg(
    mass_fraction: Sequence[float],
    grid: SphericalBeadGrid,
    density_kg_m3: float,
) -> float:
    density = _finite_number(density_kg_m3, "density_not_positive")
    return sum(
        density * fraction * volume
        for fraction, volume in zip(mass_fraction, grid.volume_m3)
    )
