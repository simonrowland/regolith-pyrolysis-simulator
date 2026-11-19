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
timescale. Zhang's 2008 law then uses that half-time as tau.

This module does not read the paper's fitted tau, a0 or b0.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

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
