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

Cooling time is the bead's own heat-loss half-time, not a fitted plume
timescale. Zhang's 2008 law then uses that half-time as tau. The sodium
surface flux is the catalog NaO0.5 equilibrium pressure at a stated
ambient fO2 and fNa, converted with the Hertz–Knudsen–Langmuir owner.

This module does not read the paper's fitted tau, a0 or b0.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

from simulator.physical_constants import STEFAN_BOLTZMANN

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


def _non_negative_number(value: float, reason: str) -> float:
    number = _finite_number(value, reason)
    if number < 0.0:
        raise BeadDiffusionRefusal(reason, f"{reason}: {value!r}")
    return number


def sphere_heat_loss_w_m2(
    temperature_K: float,
    *,
    emissivity: float,
    environment_temperature_K: float,
    convection_coefficient_w_m2_k: float,
) -> float:
    """Outward heat flux from a lumped sphere, W/m^2.

    Premise: the bead is isothermal (the same Bi << 1 premise Su and Zhang
    cite) and loses heat by radiation to an environment at ``T_env`` plus
    Newtonian convection to that same environment.
    Algebra: ``q = ε σ (T^4 - T_env^4) + h (T - T_env)``.
    Units: σ is W/(m^2 K^4), so both terms are W/m^2. σ is
    :data:`simulator.physical_constants.STEFAN_BOLTZMANN`.
    Sanity: ``q`` is zero when ``T = T_env`` and positive when the bead is
    hotter than the environment. ``h = 0`` is an explicit input, not a
    default. Emissivity outside ``[0, 1]`` refuses.
    """

    temperature = _positive_temperature_K(temperature_K)
    emissivity_value = _finite_number(emissivity, "emissivity_out_of_range")
    if not 0.0 <= emissivity_value <= 1.0:
        raise BeadDiffusionRefusal(
            "emissivity_out_of_range",
            f"emissivity must lie in [0, 1], got {emissivity!r}",
        )
    environment = _non_negative_number(
        environment_temperature_K, "environment_temperature_negative"
    )
    convection = _non_negative_number(
        convection_coefficient_w_m2_k, "convection_coefficient_negative"
    )
    radiative = emissivity_value * STEFAN_BOLTZMANN * (
        temperature**4 - environment**4
    )
    convective = convection * (temperature - environment)
    flux = radiative + convective
    if not math.isfinite(flux):
        raise BeadDiffusionRefusal(
            "heat_loss_not_finite",
            f"sphere heat loss was {flux!r}",
        )
    return flux


def cooling_half_time_s(
    *,
    radius_m: float,
    density_kg_m3: float,
    heat_capacity_j_kg_k: float,
    emissivity: float,
    environment_temperature_K: float,
    convection_coefficient_w_m2_k: float,
    initial_temperature_K: float,
) -> float:
    """Time for a bead's heat-loss ODE to fall from ``T0`` to ``T0/2``.

    Premise: a sphere of radius ``R`` has heat capacity
    ``ρ (4/3) π R^3 cp`` and surface area ``4 π R^2``. Every thermal
    property is an argument. Nothing here is fitted to a 74220 bead.
    Algebra: ``ρ cp R dT/dt = -3 q(T)``, with ``q`` from
    :func:`sphere_heat_loss_w_m2`. Zhang 2008 defines τ as the time at
    which ``T = T0/2``, so this function returns that time. It is not the
    initial-slope time ``ρ cp R T0 / (3 q(T0))``. For radiation into cold
    space (``T_env = 0``, ``h = 0``) the ODE is ``dT/dt = -β T^4`` with
    ``β = 3 ε σ / (ρ cp R)``, and the integral from ``T0`` to ``T0/2`` is
    ``7 / (3 β T0^3) = 7 ρ cp R / (9 ε σ T0^3)``, which is ``7/3`` times
    the initial-slope time. The integrator below is RK4, so that closed
    form is a check rather than the implementation.
    Units: ``q`` in W/m^2 and ``ρ cp R`` in J/(m^2 K) give ``dT/dt`` in
    K/s and the result in seconds.
    Sanity: a bead that is not losing heat at ``T0`` refuses
    (``bead_not_cooling``). If the loss shuts off before ``T0/2``, the
    half-temperature is unreachable and that refuses too.
    """

    radius = _finite_number(radius_m, "radius_not_positive")
    if radius <= 0.0:
        raise BeadDiffusionRefusal(
            "radius_not_positive",
            f"radius_m must be finite and positive, got {radius_m!r}",
        )
    density = _finite_number(density_kg_m3, "density_not_positive")
    if density <= 0.0:
        raise BeadDiffusionRefusal(
            "density_not_positive",
            f"density_kg_m3 must be finite and positive, got {density_kg_m3!r}",
        )
    heat_capacity = _finite_number(heat_capacity_j_kg_k, "heat_capacity_not_positive")
    if heat_capacity <= 0.0:
        raise BeadDiffusionRefusal(
            "heat_capacity_not_positive",
            "heat_capacity_j_kg_k must be finite and positive, "
            f"got {heat_capacity_j_kg_k!r}",
        )
    initial = _positive_temperature_K(initial_temperature_K)
    # Reject a bad emissivity or convection coefficient before the ODE.
    opening_flux = sphere_heat_loss_w_m2(
        initial,
        emissivity=emissivity,
        environment_temperature_K=environment_temperature_K,
        convection_coefficient_w_m2_k=convection_coefficient_w_m2_k,
    )
    if opening_flux <= 0.0:
        raise BeadDiffusionRefusal(
            "bead_not_cooling",
            f"heat loss at the initial temperature was {opening_flux} W/m^2",
        )

    capacity_per_area = density * heat_capacity * radius / 3.0

    def rate(temperature_K: float) -> float:
        if temperature_K <= 0.0 or not math.isfinite(temperature_K):
            raise BeadDiffusionRefusal(
                "half_temperature_unreachable",
                "the cooling step left the positive temperature axis",
            )
        flux = sphere_heat_loss_w_m2(
            temperature_K,
            emissivity=emissivity,
            environment_temperature_K=environment_temperature_K,
            convection_coefficient_w_m2_k=convection_coefficient_w_m2_k,
        )
        return -flux / capacity_per_area

    target = 0.5 * initial
    initial_rate = rate(initial)
    if initial_rate >= 0.0:
        raise BeadDiffusionRefusal(
            "bead_not_cooling",
            "temperature is not falling at the initial state",
        )
    # The initial-slope time is T0 / |dT/dt|. A few hundred RK4 steps across
    # that interval resolve the drop to T0/2 (7/3 of the slope time for
    # cold-space radiation; order-1 of it for convection).
    step = (initial / -initial_rate) / 500.0
    temperature = initial
    elapsed = 0.0
    for _ in range(200_000):
        if step <= 0.0 or not math.isfinite(step):
            raise BeadDiffusionRefusal(
                "half_temperature_unreachable",
                "the cooling step collapsed before T0/2",
            )
        accepted = False
        for _attempt in range(40):
            k1 = rate(temperature)
            if k1 >= 0.0:
                raise BeadDiffusionRefusal(
                    "half_temperature_unreachable",
                    "cooling stopped before the bead reached T0/2",
                )
            stage_a = temperature + 0.5 * step * k1
            if stage_a <= 0.0:
                step *= 0.5
                continue
            k2 = rate(stage_a)
            stage_b = temperature + 0.5 * step * k2
            if stage_b <= 0.0:
                step *= 0.5
                continue
            k3 = rate(stage_b)
            stage_c = temperature + step * k3
            if stage_c <= 0.0:
                step *= 0.5
                continue
            k4 = rate(stage_c)
            updated = temperature + (step / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
            if updated <= 0.0 or not math.isfinite(updated):
                step *= 0.5
                continue
            accepted = True
            break
        if not accepted:
            raise BeadDiffusionRefusal(
                "half_temperature_unreachable",
                "cooling stopped before the bead reached T0/2",
            )
        if updated <= target:
            span = temperature - updated
            if span <= 0.0:
                raise BeadDiffusionRefusal(
                    "half_temperature_unreachable",
                    "the cooling step did not decrease temperature",
                )
            fraction = (temperature - target) / span
            half_time = elapsed + fraction * step
            if not math.isfinite(half_time) or half_time <= 0.0:
                raise BeadDiffusionRefusal(
                    "half_temperature_unreachable",
                    f"half time was {half_time!r}",
                )
            return half_time
        temperature = updated
        elapsed += step
    raise BeadDiffusionRefusal(
        "half_temperature_unreachable",
        "RK4 did not reach T0/2 within the step budget",
    )


def zhang_2008_temperature_K(
    t_s: float,
    *,
    initial_temperature_K: float,
    cooling_half_time_s: float,
) -> float:
    """Zhang 2008 asymptotic cooling, ``T = T0 / (1 + t/τ)``.

    Premise: τ is the time at which ``T = T0/2``, supplied by the caller
    (for a bead, :func:`cooling_half_time_s`). This hyperbola is not the
    ``T^4`` heat-loss trajectory. It is the literature cooling law, pinned
    to pass through that half-temperature time.
    Algebra: direct evaluation of the printed formula.
    Units: temperatures in kelvin, time and τ in seconds.
    Sanity: ``t = 0`` returns ``T0`` and ``t = τ`` returns ``T0/2``.
    """

    time = _non_negative_number(t_s, "time_negative")
    initial = _positive_temperature_K(initial_temperature_K)
    tau = _finite_number(cooling_half_time_s, "cooling_half_time_not_positive")
    if tau <= 0.0:
        raise BeadDiffusionRefusal(
            "cooling_half_time_not_positive",
            "cooling_half_time_s must be finite and positive, "
            f"got {cooling_half_time_s!r}",
        )
    temperature = initial / (1.0 + time / tau)
    if not math.isfinite(temperature) or temperature <= 0.0:
        raise BeadDiffusionRefusal(
            "temperature_not_positive",
            f"Zhang temperature was {temperature!r}",
        )
    return temperature


def zhang_2008_duration_until_s(
    end_temperature_K: float,
    *,
    initial_temperature_K: float,
    cooling_half_time_s: float,
) -> float:
    """Time at which the Zhang law reaches ``end_temperature_K``.

    Algebra: ``T = T0 / (1 + t/τ)`` inverts to ``t = τ (T0/T - 1)``.
    A cutoff at or above ``T0`` refuses; the bead has not cooled.
    """

    end = _positive_temperature_K(end_temperature_K)
    initial = _positive_temperature_K(initial_temperature_K)
    tau = _finite_number(cooling_half_time_s, "cooling_half_time_not_positive")
    if tau <= 0.0:
        raise BeadDiffusionRefusal(
            "cooling_half_time_not_positive",
            "cooling_half_time_s must be finite and positive, "
            f"got {cooling_half_time_s!r}",
        )
    if end >= initial:
        raise BeadDiffusionRefusal(
            "end_temperature_not_below_initial",
            f"end temperature {end} K is not below initial {initial} K",
        )
    return tau * (initial / end - 1.0)


def diffusivity_along_zhang_cooling(
    diffusivity_m2_s_at_temperature: Diffusivity,
    *,
    initial_temperature_K: float,
    cooling_half_time_s: float,
) -> Diffusivity:
    """``D(t) = D(T(t))`` on the Zhang 2008 hyperbola."""

    if not callable(diffusivity_m2_s_at_temperature):
        raise BeadDiffusionRefusal(
            "diffusivity_missing",
            "diffusivity must be a function of temperature",
        )

    def at_time(t_s: float) -> float:
        temperature = zhang_2008_temperature_K(
            t_s,
            initial_temperature_K=initial_temperature_K,
            cooling_half_time_s=cooling_half_time_s,
        )
        return diffusivity_m2_s_at_temperature(temperature)

    return at_time


@dataclass(frozen=True)
class SodiumSurfaceFlux:
    """One evaluation of the sodium oxide surface mass flux.

    ``na2o_mass_flux_kg_m2_s`` is positive when Na2O leaves the bead.
    """

    na2o_mass_flux_kg_m2_s: float
    equilibrium_p_na_pa: float
    activity_nao0_5: float
    evaporation_alpha: float
    alpha_authority_status: str
    gamma_authority_status: str
    vapour_pressure_out_of_range: bool
    vapour_pressure_status: str | None
    evaporation_alpha_outside_stated_band: bool


@dataclass(frozen=True)
class BeadProfilePrediction:
    """A radial profile and the assumptions that produced it.

    ``flags`` carries the ambient pressures, the volatility authority, and
    any caller-supplied property labels. Ambient is never a hidden default.
    """

    component: str
    grid: SphericalBeadGrid
    mass_fraction: tuple[float, ...]
    wt_percent: tuple[float, ...]
    tau_s: float
    t_end_s: float
    initial_temperature_K: float
    end_temperature_K: float
    flags: tuple[str, ...]
    oxide_mass_initial_kg: float
    oxide_mass_final_kg: float
    boundary_mass_out_kg: float
    step_count: int


def _absent_or_finite(value: float | None, *, missing: str, invalid: str) -> float:
    if value is None:
        raise BeadDiffusionRefusal(missing, missing)
    return _finite_number(value, invalid)


@lru_cache(maxsize=1)
def _vapour_catalog():
    """Compile the vapour-rail catalog once.

    The pressure evaluator and the evaporation-alpha contract are what the
    bead reads. Request rules are part of that same compile.
    """

    from pathlib import Path

    from simulator.yaml_cache import load_cached_safe_yaml

    path = Path(__file__).resolve().parents[1] / "data" / "vapor_pressures.yaml"
    try:
        payload = load_cached_safe_yaml(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BeadDiffusionRefusal(
            "vapour_catalog_unreadable",
            f"could not read {path}",
        ) from exc
    if not isinstance(payload, dict):
        raise BeadDiffusionRefusal(
            "vapour_catalog_unreadable",
            "vapor_pressures.yaml did not load as a mapping",
        )
    from simulator.vapour_rail.catalog import compile_vapour_rail_catalog

    return compile_vapour_rail_catalog(payload)


def _sodium_alpha(temperature_K: float) -> tuple[float, str, bool]:
    """Return alpha, its authority status, and whether ``T`` is outside the stated band."""

    from simulator.alpha_kinetics import parse_alpha_contract

    try:
        species = _vapour_catalog().species["Na"]
    except KeyError as exc:
        raise BeadDiffusionRefusal(
            "evaporation_alpha_absent",
            "no compiled Na species",
        ) from exc
    contract = species.vaporisation_coefficients.evaporation_alpha
    parsed = parse_alpha_contract(contract)
    if parsed is None:
        raise BeadDiffusionRefusal(
            "evaporation_alpha_absent",
            "Na evaporation_alpha is no_data",
        )
    if isinstance(parsed, bool) or isinstance(parsed, (int, float)):
        if isinstance(parsed, bool):
            raise BeadDiffusionRefusal(
                "evaporation_alpha_form_unsupported",
                "Na evaporation_alpha was a boolean",
            )
        alpha = float(parsed)
        authority = "unspecified"
    elif isinstance(parsed, Mapping):
        form = str(parsed.get("form") or "scalar")
        if form != "scalar":
            raise BeadDiffusionRefusal(
                "evaporation_alpha_form_unsupported",
                f"Na evaporation_alpha form {form!r} is not scalar",
            )
        try:
            alpha = float(parsed["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BeadDiffusionRefusal(
                "evaporation_alpha_absent",
                "Na evaporation_alpha scalar value is missing",
            ) from exc
        authority = str(parsed.get("alpha_authority_status") or "unspecified")
    else:
        raise BeadDiffusionRefusal(
            "evaporation_alpha_form_unsupported",
            f"Na evaporation_alpha parsed as {type(parsed).__name__}",
        )
    if not math.isfinite(alpha) or not 0.0 <= alpha <= 1.0:
        raise BeadDiffusionRefusal(
            "evaporation_alpha_out_of_range",
            f"Na evaporation alpha was {alpha!r}",
        )
    band = contract.get("T_band_K") if isinstance(contract, Mapping) else None
    outside = False
    if isinstance(band, (list, tuple)) and len(band) == 2:
        low = float(band[0])
        high = float(band[1])
        outside = temperature_K < low or temperature_K > high
    return alpha, authority, outside


def na2o_relative_moles(
    na2o_mass_fraction: float,
    host_oxide_wt_percent: Mapping[str, float],
) -> dict[str, float]:
    """Relative moles of host oxides plus Na2O on a 100 mass-unit basis.

    Premise: density is constant and only Na2O mass fraction ``c`` changes.
    The host oxides keep the ratios of the printed weight percentages and
    together occupy mass fraction ``1 - c``. The printed weights are not
    rescaled to a 100% analytical total first.
    Algebra: ``m_Na2O = 100 c``, ``m_i = 100 (1-c) w_i / sum(w)``,
    ``n = m / M`` with ``M`` from :func:`oxide_molar_mass_g_mol`.
    Units: the 100 is a mass-unit choice; only ratios enter the activity.
    Sanity: ``c = 0`` contributes no Na2O moles. An oxide with no cation
    count in the melt-activity table refuses rather than disappearing.
    """

    from simulator.accounting.exceptions import UnparseableCompositionComponent
    from simulator.accounting.oxide_mass import oxide_molar_mass_g_mol
    from simulator.chemistry.melt_activity import MELT_OXIDE_CATIONS_PER_FORMULA

    fraction = _finite_number(na2o_mass_fraction, "mass_fraction_invalid")
    if fraction < 0.0 or fraction > 1.0:
        raise BeadDiffusionRefusal(
            "mass_fraction_invalid",
            f"Na2O mass fraction must lie in [0, 1], got {na2o_mass_fraction!r}",
        )
    if not isinstance(host_oxide_wt_percent, Mapping):
        raise BeadDiffusionRefusal(
            "host_oxides_missing",
            "host_oxide_wt_percent must be a mapping of oxide to wt%",
        )
    weights: dict[str, float] = {}
    for name, raw in host_oxide_wt_percent.items():
        oxide = str(name)
        if oxide in {"Na2O", "NaO0.5"}:
            raise BeadDiffusionRefusal(
                "host_na2o_not_separated",
                "pass initial Na2O separately from the host oxides",
            )
        if oxide not in MELT_OXIDE_CATIONS_PER_FORMULA:
            raise BeadDiffusionRefusal(
                "host_oxide_unsupported",
                f"{oxide} has no melt cation count",
            )
        weight = _finite_number(raw, "host_oxide_wt_percent_invalid")
        if weight < 0.0:
            raise BeadDiffusionRefusal(
                "host_oxide_wt_percent_invalid",
                f"{oxide} wt% was {raw!r}",
            )
        if weight == 0.0:
            continue
        weights[oxide] = weight
    total = sum(weights.values())
    if total <= 0.0:
        raise BeadDiffusionRefusal(
            "host_oxides_missing",
            "host oxides need a positive weight-percent sum",
        )
    remaining = 1.0 - fraction
    moles: dict[str, float] = {}
    for oxide, weight in weights.items():
        grams = 100.0 * remaining * weight / total
        try:
            molar_mass = oxide_molar_mass_g_mol(oxide)
        except UnparseableCompositionComponent as exc:
            raise BeadDiffusionRefusal(
                "host_oxide_unparseable",
                f"{oxide} has no molar mass",
            ) from exc
        moles[oxide] = grams / molar_mass
    if fraction > 0.0:
        na_grams = 100.0 * fraction
        moles["Na2O"] = na_grams / oxide_molar_mass_g_mol("Na2O")
    return moles


def na2o_surface_mass_flux(
    temperature_K: float,
    na2o_mass_fraction: float,
    *,
    host_oxide_wt_percent: Mapping[str, float],
    ambient_p_na_pa: float,
    ambient_p_o2_bar: float,
) -> SodiumSurfaceFlux:
    """Na2O mass flux from the catalogue sodium volatility, kg/(m^2 s).

    Premise: the compiled reaction is NaO0.5(l) = Na(g) + 1/4 O2. The
    equilibrium Na pressure comes from that catalogue row at the surface
    activity and the stated ambient oxygen pressure. Ambient Na pressure
    is an input. The paper's a0 and b0 are not.
    Algebra: the Hertz–Knudsen–Langmuir owner clamps to evaporation, so
    the net molar flux is ``langmuir(T, p_eq, p_amb) - langmuir(T, p_amb, p_eq)``.
    One mole of Na(g) removes one mole of NaO0.5, hence half a mole of
    Na2O: ``J_Na2O = J_Na * M_Na2O / 2``.
    Units: molar flux times kg/mol is kg/(m^2 s), positive outward.
    Sanity: ``p_amb = 0`` does not condense. ``p_amb > p_eq`` returns a
    negative flux. ``c = 0`` does not call the activity-dependent evaluator.
    """

    from simulator.accounting.oxide_mass import oxide_molar_mass_g_mol
    from simulator.chemistry.langmuir_knudsen import (
        langmuir_molar_flux,
        species_molar_mass_kg_mol,
    )
    from simulator.chemistry.melt_activity import melt_oxide_activity
    from simulator.vapour_rail.catalog import CatalogCompileError

    temperature = _positive_temperature_K(temperature_K)
    ambient_na = _absent_or_finite(
        ambient_p_na_pa,
        missing="ambient_p_na_missing",
        invalid="ambient_p_na_invalid",
    )
    if ambient_na < 0.0:
        raise BeadDiffusionRefusal(
            "ambient_p_na_invalid",
            f"ambient_p_na_pa must be >= 0, got {ambient_p_na_pa!r}",
        )
    if ambient_p_o2_bar is None:
        raise BeadDiffusionRefusal("ambient_p_o2_missing", "ambient_p_o2_missing")
    ambient_o2 = _finite_number(ambient_p_o2_bar, "ambient_p_o2_invalid")
    if ambient_o2 <= 0.0:
        raise BeadDiffusionRefusal(
            "ambient_p_o2_invalid",
            f"ambient_p_o2_bar must be positive, got {ambient_p_o2_bar!r}",
        )
    fraction = _finite_number(na2o_mass_fraction, "mass_fraction_invalid")
    # The diffusion step keeps dust at or above -1e-12 instead of clamping
    # it to zero. That dust is an empty shell, not a negative inventory.
    if -1e-12 <= fraction < 0.0:
        fraction = 0.0
    alpha, authority, alpha_outside = _sodium_alpha(temperature)
    account = na2o_relative_moles(fraction, host_oxide_wt_percent)
    activity_row = melt_oxide_activity(
        "Na2O",
        account,
        temperature_K=temperature,
    )
    if activity_row is None:
        raise BeadDiffusionRefusal(
            "melt_activity_unavailable",
            "Na2O melt activity returned no row",
        )
    activity = float(activity_row.activity)
    if not math.isfinite(activity) or activity < 0.0:
        raise BeadDiffusionRefusal(
            "melt_activity_unavailable",
            f"Na2O activity was {activity!r}",
        )
    vapour_status: str | None = None
    out_of_range = False
    if activity == 0.0:
        pressure = 0.0
    else:
        try:
            species = _vapour_catalog().species["Na"]
        except KeyError as exc:
            raise BeadDiffusionRefusal(
                "volatility_absent",
                "no compiled Na vapour species",
            ) from exc
        if species.evaluator is None:
            raise BeadDiffusionRefusal(
                "volatility_absent",
                "Na vapour pressure evaluator is absent",
            )
        try:
            evaluation = species.evaluator.evaluate(
                temperature,
                source_activity=activity,
                pO2_bar=ambient_o2,
            )
        except CatalogCompileError as exc:
            raise BeadDiffusionRefusal(
                "ambient_p_o2_invalid",
                str(exc),
            ) from exc
        pressure = float(evaluation.pressure_pa)
        out_of_range = bool(evaluation.out_of_range)
        vapour_status = evaluation.status
    if not math.isfinite(pressure) or pressure < 0.0:
        raise BeadDiffusionRefusal(
            "equilibrium_pressure_invalid",
            f"Na equilibrium pressure was {pressure!r}",
        )
    molar_mass = species_molar_mass_kg_mol("Na")
    outward = langmuir_molar_flux(
        temperature,
        pressure,
        ambient_na,
        alpha,
        molar_mass_kg_mol=molar_mass,
    )
    inward = langmuir_molar_flux(
        temperature,
        ambient_na,
        pressure,
        alpha,
        molar_mass_kg_mol=molar_mass,
    )
    net_mol = outward - inward
    na2o_molar_mass = oxide_molar_mass_g_mol("Na2O") / 1000.0
    mass_flux = net_mol * na2o_molar_mass / 2.0
    if not math.isfinite(mass_flux):
        raise BeadDiffusionRefusal(
            "surface_flux_not_finite",
            f"Na2O surface flux was {mass_flux!r}",
        )
    return SodiumSurfaceFlux(
        na2o_mass_flux_kg_m2_s=mass_flux,
        equilibrium_p_na_pa=pressure,
        activity_nao0_5=activity,
        evaporation_alpha=alpha,
        alpha_authority_status=authority,
        gamma_authority_status=str(activity_row.authority_status),
        vapour_pressure_out_of_range=out_of_range,
        vapour_pressure_status=vapour_status,
        evaporation_alpha_outside_stated_band=alpha_outside,
    )


def _format_flag(name: str, value: float) -> str:
    return f"{name}={format(value, '.12g')}"


def _property_flags(property_flags: Sequence[str] | None) -> tuple[str, ...]:
    if property_flags is None:
        raise BeadDiffusionRefusal("property_flags_missing", "property_flags_missing")
    if isinstance(property_flags, (str, bytes)) or not isinstance(
        property_flags, Sequence
    ):
        raise BeadDiffusionRefusal(
            "property_flag_invalid",
            "property_flags must be a sequence of strings",
        )
    cleaned: list[str] = []
    for flag in property_flags:
        if not isinstance(flag, str) or not flag.strip():
            raise BeadDiffusionRefusal(
                "property_flag_invalid",
                f"property flag was {flag!r}",
            )
        cleaned.append(flag)
    return tuple(cleaned)


def _require_positive(value: float, reason: str) -> float:
    number = _finite_number(value, reason)
    if number <= 0.0:
        raise BeadDiffusionRefusal(reason, f"{reason}: {value!r}")
    return number


def predict_bead_profile(
    *,
    component: str,
    diameter_m: float,
    host_oxide_wt_percent: Mapping[str, float],
    initial_wt_percent: float,
    initial_temperature_K: float,
    end_temperature_K: float,
    density_kg_m3: float,
    heat_capacity_j_kg_k: float,
    emissivity: float,
    environment_temperature_K: float,
    convection_coefficient_w_m2_k: float,
    ambient_p_na_pa: float,
    ambient_p_o2_bar: float,
    n_cells: int,
    n_steps: int,
    property_flags: Sequence[str],
) -> BeadProfilePrediction:
    """Predict one radial profile. Tau, a0 and b0 are not arguments.

    Sodium uses Lowry diffusivity along Zhang cooling whose tau is
    :func:`cooling_half_time_s`, and :func:`na2o_surface_mass_flux` at the
    stated ambient. Potassium refuses: no tracer diffusivity is printed.
    Copper refuses: the compiled Cu row is not a silicate-melt carrier.
    """

    if not isinstance(component, str) or not component:
        raise BeadDiffusionRefusal(
            "component_unsupported",
            f"component was {component!r}",
        )
    diameter = _require_positive(diameter_m, "diameter_not_positive")
    initial_wt = _finite_number(initial_wt_percent, "initial_wt_percent_invalid")
    if initial_wt < 0.0 or initial_wt > 100.0:
        raise BeadDiffusionRefusal(
            "initial_wt_percent_invalid",
            f"initial_wt_percent was {initial_wt_percent!r}",
        )
    initial_temperature = _positive_temperature_K(initial_temperature_K)
    end_temperature = _positive_temperature_K(end_temperature_K)
    density = _require_positive(density_kg_m3, "density_not_positive")
    heat_capacity = _require_positive(
        heat_capacity_j_kg_k, "heat_capacity_not_positive"
    )
    caller_flags = _property_flags(property_flags)
    # Ambient is validated even for components that then refuse, so a missing
    # atmosphere is not hidden behind the component refusal.
    if ambient_p_na_pa is None:
        raise BeadDiffusionRefusal("ambient_p_na_missing", "ambient_p_na_missing")
    if ambient_p_o2_bar is None:
        raise BeadDiffusionRefusal("ambient_p_o2_missing", "ambient_p_o2_missing")
    _finite_number(ambient_p_na_pa, "ambient_p_na_invalid")
    _finite_number(ambient_p_o2_bar, "ambient_p_o2_invalid")
    if component == "K2O":
        potassium_tracer_diffusivity_m2_s(initial_temperature)
    if component == "Cu":
        try:
            copper = _vapour_catalog().species["Cu"]
        except KeyError as exc:
            raise BeadDiffusionRefusal(
                "cu_melt_volatility_not_a_silicate_carrier",
                "no compiled Cu vapour species",
            ) from exc
        evaluator = copper.evaluator
        if evaluator is None or evaluator.activity_exponent == 0.0:
            raise BeadDiffusionRefusal(
                "cu_melt_volatility_not_a_silicate_carrier",
                "compiled Cu pressure does not depend on a dissolved-oxide activity",
            )
        raise BeadDiffusionRefusal(
            "component_not_wired",
            "a dissolved Cu carrier is compiled and this predictor does not use it",
        )
    if component != "Na2O":
        raise BeadDiffusionRefusal(
            "component_unsupported",
            f"component {component!r} has no bead profile",
        )

    radius = 0.5 * diameter
    tau = cooling_half_time_s(
        radius_m=radius,
        density_kg_m3=density,
        heat_capacity_j_kg_k=heat_capacity,
        emissivity=emissivity,
        environment_temperature_K=environment_temperature_K,
        convection_coefficient_w_m2_k=convection_coefficient_w_m2_k,
        initial_temperature_K=initial_temperature,
    )
    duration = zhang_2008_duration_until_s(
        end_temperature,
        initial_temperature_K=initial_temperature,
        cooling_half_time_s=tau,
    )
    initial_fraction = initial_wt / 100.0
    # Reject a bad host before stepping, including a measured Na2O left in it.
    na2o_relative_moles(initial_fraction, host_oxide_wt_percent)
    host_total = 0.0
    for raw in host_oxide_wt_percent.values():
        host_total += float(raw)
    diffusivity = diffusivity_along_zhang_cooling(
        lowry_1982_na_diffusivity_m2_s,
        initial_temperature_K=initial_temperature,
        cooling_half_time_s=tau,
    )
    gamma_status: list[str] = []
    vapour_status: list[str] = []
    vapour_out = False
    vapour_inside = False
    alpha_outside = False
    alpha_value: float | None = None
    alpha_authority: str | None = None

    def surface_flux(t_s: float, mass_fraction: float) -> float:
        nonlocal alpha_outside, alpha_value, alpha_authority, vapour_out, vapour_inside
        temperature = zhang_2008_temperature_K(
            t_s,
            initial_temperature_K=initial_temperature,
            cooling_half_time_s=tau,
        )
        report = na2o_surface_mass_flux(
            temperature,
            mass_fraction,
            host_oxide_wt_percent=host_oxide_wt_percent,
            ambient_p_na_pa=ambient_p_na_pa,
            ambient_p_o2_bar=ambient_p_o2_bar,
        )
        alpha_value = report.evaporation_alpha
        alpha_authority = report.alpha_authority_status
        if report.evaporation_alpha_outside_stated_band:
            alpha_outside = True
        if report.gamma_authority_status not in gamma_status:
            gamma_status.append(report.gamma_authority_status)
        if report.vapour_pressure_out_of_range:
            vapour_out = True
            if (
                report.vapour_pressure_status
                and report.vapour_pressure_status not in vapour_status
            ):
                vapour_status.append(report.vapour_pressure_status)
        else:
            vapour_inside = True
        return report.na2o_mass_flux_kg_m2_s

    result = integrate_spherical_diffusion(
        radius_m=radius,
        density_kg_m3=density,
        initial_mass_fraction=initial_fraction,
        diffusivity_m2_s=diffusivity,
        surface_mass_flux_kg_m2_s=surface_flux,
        t_end_s=duration,
        n_steps=n_steps,
        n_cells=n_cells,
    )
    if alpha_value is None or alpha_authority is None:
        raise BeadDiffusionRefusal(
            "evaporation_alpha_absent",
            "the cooling path did not evaluate an evaporation alpha",
        )
    flags = [
        "not_fitted_to_su_zhang_profiles",
        "thermal_history:zhang_2008_with_heat_loss_half_time",
        _format_flag("ambient_p_na_pa", float(ambient_p_na_pa)),
        _format_flag("ambient_p_o2_bar", float(ambient_p_o2_bar)),
        _format_flag("diameter_m", diameter),
        _format_flag("density_kg_m3", density),
        _format_flag("heat_capacity_j_kg_k", heat_capacity),
        _format_flag("emissivity", float(emissivity)),
        _format_flag("environment_temperature_K", float(environment_temperature_K)),
        _format_flag(
            "convection_coefficient_w_m2_k",
            float(convection_coefficient_w_m2_k),
        ),
        _format_flag("initial_temperature_K", initial_temperature),
        _format_flag("end_temperature_K", end_temperature),
        _format_flag("tau_s", tau),
        _format_flag("evaporation_alpha", alpha_value),
        f"alpha_authority_status:{alpha_authority}",
        _format_flag("host_analytical_total_wt_percent", host_total + initial_wt),
    ]
    for status in gamma_status:
        flags.append(f"gamma_authority_status:{status}")
    if vapour_out:
        flags.append("vapour_pressure_out_of_range")
    for status in vapour_status:
        flags.append(f"vapour_pressure_status:{status}")
    if vapour_inside:
        flags.append("vapour_pressure_evaluated_inside_domain")
    if alpha_outside:
        flags.append("evaporation_alpha_outside_stated_band")
    flags.extend(caller_flags)
    final_mass = result.oxide_mass_kg(density)
    initial_mass = oxide_mass_kg(
        [initial_fraction] * result.grid.n_cells,
        result.grid,
        density,
    )
    return BeadProfilePrediction(
        component="Na2O",
        grid=result.grid,
        mass_fraction=result.mass_fraction,
        wt_percent=tuple(100.0 * value for value in result.mass_fraction),
        tau_s=tau,
        t_end_s=result.t_end_s,
        initial_temperature_K=initial_temperature,
        end_temperature_K=end_temperature,
        flags=tuple(flags),
        oxide_mass_initial_kg=initial_mass,
        oxide_mass_final_kg=final_mass,
        boundary_mass_out_kg=result.boundary_mass_out_kg,
        step_count=result.step_count,
    )
