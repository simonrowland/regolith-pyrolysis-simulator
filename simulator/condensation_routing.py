"""Canonical condenser-stage and extraction-product routing."""

from __future__ import annotations

import math
from collections.abc import MutableMapping


STAGE_KEY_BY_NUMBER: dict[int, str] = {
    1: 'stage_1_fe_condenser',
    2: 'stage_2_cr_oxide_harvest',
    3: 'stage_3_sio_zone',
    4: 'stage_4_alkali_mg_cyclone',
}

# Stage 4 keeps its key. The K trap is a sub-stage of 4, not a new
# public stage number. sub_order 0 is every existing stage.
NA_COLLECTOR_STAGE_KEY = 'stage_4_alkali_mg_cyclone'
K_TRAP_STAGE_KEY = 'stage_4b_k_trap'
K_TRAP_STAGE_NUMBER = 4
K_TRAP_SUB_ORDER = 1
STAGE_KEY_BY_IDENTITY: dict[tuple[int, int], str] = {
    (number, 0): key for number, key in STAGE_KEY_BY_NUMBER.items()
}
STAGE_KEY_BY_IDENTITY[(K_TRAP_STAGE_NUMBER, K_TRAP_SUB_ORDER)] = K_TRAP_STAGE_KEY
# Collector bands the integer registry cannot name. Completeness checks
# require these keys; they do not renumber stages 5-7.
SUBSTAGE_COLLECTOR_KEYS: tuple[str, ...] = (K_TRAP_STAGE_KEY,)

# Config includes ducts and downstream monitors as well as product collectors.
# Keep the product/report registry above stable; reuse its canonical keys here.
COLLECTOR_STAGE_KEY_BY_NUMBER: dict[int, str] = {
    0: 'stage_0_hot_duct',
    **STAGE_KEY_BY_NUMBER,
    5: 'stage_5_vortex_dust_filter',
    6: 'stage_6_turbine_compressor',
    7: 'stage_7_accumulator',
}


def stage_key_for_stage(
    stage: object,
    *,
    fallback_stage_number: int | None = None,
    falsey_sub_order_is_zero: bool = False,
    include_collector_fallback: bool = False,
) -> str:
    """Resolve a stage key from its explicit key, then its identity tuple.

    Callers select their own missing-number and unknown-collector behavior:
    report projections leave unknown identities unrouted, while the runner
    assigns them a collector-number key or ``stage_N``.
    """

    try:
        stage_number = int(getattr(stage, 'stage_number'))
    except AttributeError:
        if fallback_stage_number is None:
            raise
        stage_number = int(fallback_stage_number)

    sub_order = getattr(stage, 'sub_order', 0)
    if falsey_sub_order_is_zero:
        sub_order = sub_order or 0
    identity_key = STAGE_KEY_BY_IDENTITY.get(
        (stage_number, int(sub_order))
    )

    explicit_key = getattr(stage, 'stage_key', '') or ''
    if explicit_key:
        return explicit_key
    if identity_key:
        return identity_key
    if include_collector_fallback:
        return COLLECTOR_STAGE_KEY_BY_NUMBER.get(
            stage_number, f'stage_{stage_number}'
        )
    return ''


STAGE_NUMBER_BY_KEY: dict[str, int] = {
    key: number for number, key in STAGE_KEY_BY_NUMBER.items()
}

# Backward/short names used by prior reviews and handoff prompts.
STAGE_NUMBER_BY_KEY.update({
    'stage_2_cr_harvester': 2,
    'stage_3_silica_baffle': 3,
    'stage_4_alkali_cyclone': 4,
    K_TRAP_STAGE_KEY: K_TRAP_STAGE_NUMBER,
})

STAGE_TARGET_SPECIES: dict[int, tuple[str, ...]] = {
    1: ('Fe',),
    2: ('Cr', 'CrO2', 'Mn'),
    3: ('SiO',),
    4: ('Na', 'Mg'),
}
# The integer map cannot hold two target tuples for stage 4.
STAGE_TARGET_SPECIES_BY_KEY: dict[str, tuple[str, ...]] = {
    K_TRAP_STAGE_KEY: ('K',),
}

# Per CLAUDE.md sections 2/5 and Review F routing table. Product aliases
# such as SiO2/Cr2O3 are accepted only as condensate products of SiO/CrO2.
DESIGNATED_STAGE: dict[str, str] = {
    'Fe': 'stage_1_fe_condenser',
    'Cr': 'stage_2_cr_oxide_harvest',
    'CrO2': 'stage_2_cr_oxide_harvest',
    'Cr2O3': 'stage_2_cr_oxide_harvest',
    'Mn': 'stage_2_cr_oxide_harvest',
    'SiO': 'stage_3_sio_zone',
    'SiO2': 'stage_3_sio_zone',
    'Na': NA_COLLECTOR_STAGE_KEY,
    'K': K_TRAP_STAGE_KEY,
    'Mg': NA_COLLECTOR_STAGE_KEY,
}

# Homonuclear metal dimers. d-378: M2(g) has no distinct condensate.
# M2(g) -> 2 M(l/s) on the monomer's stage when that monomer already
# has a collection contract. The gas-phase M2 = 2 M equilibrium is one
# element saturation condition, so the stage and the wall saturation are
# the monomer's. Sticking uses the monomer alpha as a flagged proxy.
METAL_DIMER_SPECIES: frozenset[str] = frozenset({
    'Al2',
    'Ca2',
    'K2',
    'Mg2',
    'Na2',
})
DIMER_ALPHA_FROM_MONOMER_PROXY = 'dimer_alpha_from_monomer_proxy'
# d-378 amendment 1. No dissociation constant: compare the element
# partial with P_sat,M and say so. This is an upper bound on capture,
# not a measured equilibrium partial.
DIMER_SATURATION_UPPER_BOUND_NO_KP = 'dimer_saturation_upper_bound_no_Kp'


def dimer_collection_monomer(species: str) -> str | None:
    """Monomer collection account for a metal dimer, or None.

    None means the monomer itself has no condenser contract. Those
    dimers keep the typed missing-collection-product refusal.
    """

    if species not in METAL_DIMER_SPECIES:
        return None
    monomer = species[:-1]
    if monomer not in DESIGNATED_STAGE:
        return None
    return monomer


def monomer_condensate_credit_kg(
    dimer: str,
    monomer: str,
    dimer_kg: float,
) -> float:
    """kg of M credited for ``dimer_kg`` of M2.

    d-378: M2(g) -> n M(l/s), where n is the dimer formula's count of the
    monomer element. Credited mass is n * (dimer_kg / M_M2) * M_M.
    A homonuclear dimer has n = 2 and M_M2 = 2 M_M, so the credited
    mass equals the dimer mass and the atom count is 2 mol M per mol M2.
    """

    from simulator.accounting.formulas import resolve_species_formula

    dimer_formula = resolve_species_formula(dimer)
    monomer_formula = resolve_species_formula(monomer)
    atom_count = float(dimer_formula.elements.get(monomer, 0.0))
    if atom_count <= 0.0:
        raise ValueError(f'{dimer} has no {monomer} atoms to credit')
    dimer_molar_mass = float(dimer_formula.molar_mass_g_per_mol())
    monomer_molar_mass = float(monomer_formula.molar_mass_g_per_mol())
    return float(dimer_kg) * atom_count * monomer_molar_mass / dimer_molar_mass


def element_partial_pa(
    monomer_partial_pa: float,
    dimer_partial_pa: float,
) -> float:
    """Conserved element partial p_elem = p_M + 2 p_M2, in pascals.

    A missing monomer partial is zero coexistence, not a refusal of the
    dimer. Negative or non-finite inputs are not a partial pressure.
    """

    monomer_partial = float(monomer_partial_pa)
    dimer_partial = float(dimer_partial_pa)
    if not math.isfinite(monomer_partial) or monomer_partial < 0.0:
        monomer_partial = 0.0
    if not math.isfinite(dimer_partial) or dimer_partial < 0.0:
        dimer_partial = 0.0
    return monomer_partial + 2.0 * dimer_partial


def respeciated_monomer_partial_pa(
    element_partial_pa_value: float,
    kp_pa: float,
) -> float:
    """Equilibrium monomer partial at the wall for one element total.

    d-378 amendment 1, option (b). The condensed phase is M, so the wall
    condenses iff p_M(T_wall) > P_sat,M(T_wall).

    Premise. The gas re-speciates at the wall through M2 <=> 2 M:

        K_p(T) = p_M^2 / p_M2

    with the element partial conserved:

        p_elem = p_M + 2 p_M2.

    Quadratic. Substitute p_M2 = p_M^2 / K_p:

        p_M + 2 p_M^2 / K_p = p_elem
        2 p_M^2 + K_p p_M - K_p p_elem = 0.

    Closed form. The non-negative root is

        p_M = (K_p / 4) * (sqrt(1 + 8 p_elem / K_p) - 1).

    Evaluation uses the conjugate identity, which does not cancel when
    K_p is large:

        p_M = 2 p_elem / (sqrt(1 + 8 p_elem / K_p) + 1).

    When 8 p_elem / K_p exceeds 1e16 the square root overflows, and the
    K_p -> 0 asymptotic is used instead: p_M = sqrt(K_p * p_elem / 2).

    Units. p_elem, p_M, p_M2, and K_p are pascals. K_p = P° exp(ΔG_assoc/RT)
    with P° = 1e5 Pa, so p_elem / K_p is dimensionless.

    Limits. K_p -> inf (fully dissociated) gives p_M = p_elem.
    K_p -> 0 (fully associated) gives p_M -> 0.
    """

    p_elem = float(element_partial_pa_value)
    if not math.isfinite(p_elem) or p_elem <= 0.0:
        return 0.0
    kp = float(kp_pa)
    if math.isinf(kp) and kp > 0.0:
        return p_elem
    if not math.isfinite(kp) or kp <= 0.0:
        return 0.0
    ratio = p_elem / kp
    if ratio > 1.0e16:
        return math.sqrt(kp * p_elem / 2.0)
    return (2.0 * p_elem) / (math.sqrt(1.0 + 8.0 * ratio) + 1.0)


def element_collection_pair(species: str) -> tuple[str, str] | None:
    """(monomer, dimer) when this arrival shares one collection contract.

    Both the monomer and the dimer of Na, K, and Mg are the same
    element. Al2 and Ca2 stay out: their monomers have no condenser
    contract. Other vapors are not an M2 <=> 2 M pair.
    """

    collected_dimer = dimer_collection_monomer(species)
    if collected_dimer is not None:
        return collected_dimer, species
    dimer = f'{species}2'
    if dimer_collection_monomer(dimer) == species:
        return species, dimer
    return None


def saturated_element_partial_pa(
    saturation_pa: float,
    kp_pa: float | None,
) -> float:
    """Element partial at which the wall is no longer supersaturated.

    Derivation. Condensation of M stops when the re-speciated monomer
    partial equals P_sat,M. Substitute p_M = P_sat into the element
    balance p_elem = p_M + 2 p_M^2 / K_p:

        p_elem,sat = P_sat + 2 P_sat^2 / K_p.

    The capture fraction of the conserved element inventory is
    (p_elem - p_elem,sat) / p_elem when that is positive. It is not
    (p_M - P_sat) / p_M. Worked check: p_elem = 6 Pa, K_p = 1 Pa,
    P_sat = 1 Pa gives p_M = 1.5 Pa and p_elem,sat = 3 Pa, so the
    fraction is 1/2, not the linear 1/3.

    Limits. K_p -> inf (no dimer) gives p_elem,sat = P_sat, the
    monomer-only rule. K_p -> 0 (all dimer, p_M -> 0) gives
    p_elem,sat -> inf, so a finite element partial does not condense.
    No K_p: the flagged upper bound compares p_elem with P_sat, so
    p_elem,sat = P_sat.
    """

    saturation = float(saturation_pa)
    if not math.isfinite(saturation) or saturation <= 0.0:
        return 0.0
    if kp_pa is None:
        return saturation
    kp = float(kp_pa)
    if math.isinf(kp) and kp > 0.0:
        return saturation
    if not math.isfinite(kp) or kp <= 0.0:
        return math.inf
    return saturation + 2.0 * saturation * saturation / kp


def element_equilibrium_comparison_pa(
    *,
    monomer_partial_pa: float,
    dimer_partial_pa: float,
    kp_pa: float | None,
) -> tuple[float, float, str | None]:
    """(p_elem, pressure compared with P_sat,M, no-K_p flag).

    One criterion for a monomer arrival and a dimer arrival of the
    same element. K_p known: the compared pressure is the re-speciated
    p_M. K_p missing: the compared pressure is p_elem and the flag is
    dimer_saturation_upper_bound_no_Kp.
    """

    p_elem = element_partial_pa(monomer_partial_pa, dimer_partial_pa)
    if kp_pa is None:
        return p_elem, p_elem, DIMER_SATURATION_UPPER_BOUND_NO_KP
    return p_elem, respeciated_monomer_partial_pa(p_elem, kp_pa), None


def element_limiting_capture_fraction(
    element_partial_pa_value: float,
    saturation_pa: float,
    kp_pa: float | None,
) -> float:
    """(p_elem - p_elem,sat) / p_elem when positive, else 0."""

    p_elem = float(element_partial_pa_value)
    if not math.isfinite(p_elem) or p_elem <= 0.0:
        return 0.0
    saturated = saturated_element_partial_pa(saturation_pa, kp_pa)
    if math.isinf(saturated):
        return 0.0
    return max(0.0, (p_elem - saturated) / p_elem)


def _element_drawdown_rate_pa_s(
    element_partial: float,
    strips: tuple[tuple[float, float, float, float], ...],
) -> float:
    """dp_elem/dt from the parallel monomer and dimer Hertz-Knudsen fluxes.

    d-378 amendment 2. The condensed phase is M. Each gas species
    deposits against its own equilibrium pressure over that condensate:

        J_M = k_M * max(p_M - P_sat,M, 0)
        J_M2 = k_M2 * max(p_M2 - P_sat,M^2 / K_p, 0)
        dp_elem/dt = -(J_M + 2 J_M2)

    k_M and k_M2 already include the accommodation coefficient, the
    ideal-gas scale q = RT/V, and the strip area. Amendment 1
    re-speciates the gas as p_elem falls, so p_M2 = p_M^2 / K_p and
    the dimer overpressure is

        (p_M - P_sat) (p_M + P_sat) / K_p.

    Both channels are zero together at
    p_elem,sat = P_sat + 2 P_sat^2 / K_p. The rate is not linear in
    (p_M - P_sat): the dimer channel grows with (p_M + P_sat) / K_p.
    K_p -> inf drops that channel and leaves
    k_M * max(p_elem - P_sat, 0), the monomer exponential.
    """

    rate = 0.0
    for k_monomer, k_dimer, saturation, kp_pa in strips:
        monomer_partial = respeciated_monomer_partial_pa(element_partial, kp_pa)
        monomer_drive = monomer_partial - saturation
        if monomer_drive <= 0.0:
            continue
        if k_monomer > 0.0:
            rate -= k_monomer * monomer_drive
        if k_dimer > 0.0 and math.isfinite(kp_pa) and kp_pa > 0.0:
            dimer_drive = monomer_drive * (monomer_partial + saturation) / kp_pa
            if dimer_drive > 0.0:
                rate -= 2.0 * k_dimer * dimer_drive
    return rate


def _element_saturation_floor_pa(
    strips: tuple[tuple[float, float, float, float], ...],
) -> float:
    """Lowest p_elem,sat of the strips that can still deposit."""

    floor = math.inf
    for k_monomer, k_dimer, saturation, kp_pa in strips:
        if k_monomer <= 0.0 and k_dimer <= 0.0:
            continue
        saturated = saturated_element_partial_pa(saturation, kp_pa)
        if saturated < floor:
            floor = saturated
    return floor


def _strips_are_linear(
    strips: tuple[tuple[float, float, float, float], ...],
) -> bool:
    """True when dp/dt = -sum k_i max(p - P_sat,i, 0).

    Fully dissociated strips (K_p -> inf) have p_M = p_elem and no dimer
    channel, so the drive is linear in the element partial. A finite K_p
    makes p_M nonlinear in p_elem even if the dimer coefficient is zero.
    """

    if not strips:
        return True
    for _k_monomer, _k_dimer, _saturation, kp_pa in strips:
        kp = float(kp_pa)
        if not (math.isinf(kp) and kp > 0.0):
            return False
    return True


def _final_linear_monomer_partial(
    partial: float,
    duration: float,
    strips: tuple[tuple[float, float, float, float], ...],
) -> float:
    """Closed form of dp/dt = -sum k_i max(p - P_i, 0). No step, no snap.

    While the active set (strips with P_i < p) is fixed,

        C = sum k_i
        p_eq = sum (k_i P_i) / C
        p(t) = p_eq + (p0 - p_eq) exp(-C t).

    A strip turns off when p reaches its own P_i. The condenser does not
    evaporate, so a strip at or above p contributes nothing. March the
    saturation levels from hot to cold. p_eq is a convex combination of
    the active levels, so it is never above the hottest active level.
    If p_eq is below that level, the hot strips hit it and turn off, and
    the colder strips keep integrating for the time that remains. An
    oversized coefficient therefore stops on the hot strip's own floor.
    It does not jump to the coldest floor.

    Float64 drops a 1e-6 coefficient next to 1e12, so the two-strip
    p_eq collapses onto the hot level. The hot decay is then a few 1/C
    (40/C), which is negligible beside a 1 s residence, and the cold
    strip continues alone.

    Worked chain. p0 = 200 Pa. Strip A: k = 1e12 /s, P_sat = 100 Pa.
    Strip B: k = 1e-6 /s, P_sat = 1 Pa. One second:

        fast strip reaches 100 Pa (exp(-1e12) underflows to 0)
        slow strip: p = 1 + (100 - 1) exp(-1e-6)
                  = 1 + 99 (1 - 1e-6 + 5e-13 - ...)
                  = 99.999901 Pa

    not 1 Pa.
    """

    rows = [
        (float(k_monomer), float(saturation))
        for k_monomer, _k_dimer, saturation, _kp in strips
        if float(k_monomer) > 0.0
    ]
    pressure = float(partial)
    remaining = float(duration)
    if not rows or remaining <= 0.0:
        return pressure
    levels = sorted({saturation for _k, saturation in rows})

    def _segment(
        pressure_pa: float,
        duration_s: float,
    ) -> tuple[float, float, float] | None:
        active = [
            (coefficient, saturation)
            for coefficient, saturation in rows
            if saturation < pressure_pa
        ]
        total_k = sum(coefficient for coefficient, _saturation in active)
        if total_k <= 0.0:
            return None
        equilibrium = (
            sum(coefficient * saturation for coefficient, saturation in active)
            / total_k
        )
        nxt = equilibrium + (pressure_pa - equilibrium) * math.exp(
            -total_k * duration_s
        )
        return total_k, equilibrium, nxt

    # The coldest level is the last active equilibrium, not a crossing.
    # An inactive floor (P_sat >= p) is skipped. It must not end the
    # march: p0 = 75 Pa with floors (100, 50, 1) Pa is below the 100 Pa
    # strip, crosses 50 Pa at ln(99/49)/2, and finishes on the 1 Pa strip.
    for threshold in reversed(levels[1:]):
        if remaining <= 0.0:
            break
        if pressure <= threshold:
            continue
        solved = _segment(pressure, remaining)
        if solved is None:
            break
        total_k, equilibrium, nxt = solved
        if equilibrium < threshold:
            gap = threshold - equilibrium
            numer = pressure - equilibrium
            if gap <= 0.0 or numer <= gap:
                hit_s = 0.0
            else:
                hit_s = math.log(numer / gap) / total_k
            if hit_s >= remaining:
                return nxt
            pressure = threshold
            remaining -= hit_s
            continue
        # p_eq is on this level at float64 precision. A stiff hot strip
        # arrives in a few 1/C and turns off. A gentle strip approaches
        # the level for the rest of the residence and does not pass it.
        if total_k * remaining >= 40.0:
            pressure = threshold
            remaining -= 40.0 / total_k
            continue
        return threshold + (pressure - threshold) * math.exp(
            -total_k * remaining
        )
    if remaining > 0.0:
        solved = _segment(pressure, remaining)
        if solved is not None:
            pressure = solved[2]
    return pressure


def _next_strip_floor_pa(
    partial: float,
    strips: tuple[tuple[float, float, float, float], ...],
) -> float | None:
    """Hottest strip floor strictly below ``partial``, or None."""

    floor = None
    slack = max(1e-15, abs(partial) * 1e-14)
    for k_monomer, k_dimer, saturation, kp_pa in strips:
        if k_monomer <= 0.0 and k_dimer <= 0.0:
            continue
        saturated = saturated_element_partial_pa(saturation, kp_pa)
        if not math.isfinite(saturated) or saturated >= partial - slack:
            continue
        if floor is None or saturated > floor:
            floor = saturated
    return floor


def _local_drawdown_line(
    partial: float,
    strips: tuple[tuple[float, float, float, float], ...],
    next_floor: float | None,
) -> tuple[float, float, float | None]:
    """(rate, λ, p_star) for dp/dt ≈ -λ (p - p_star) at ``partial``.

    λ <= 0 means the finite difference did not see a restoring slope.
    p_star is None in that case. The probe stays above the next floor
    so the slope is not taken across a strip that has already turned off.
    """

    rate = _element_drawdown_rate_pa_s(partial, strips)
    if rate >= 0.0 or not math.isfinite(rate):
        return rate, 0.0, None
    span = (partial - next_floor) if next_floor is not None else abs(partial) * 0.01
    span = max(span, 1e-18)
    probe = partial - span * 1.0e-4
    if next_floor is not None and probe <= next_floor:
        probe = partial - 0.5 * span
    if probe >= partial:
        return rate, 0.0, None
    rate_probe = _element_drawdown_rate_pa_s(probe, strips)
    slope = (rate - rate_probe) / (partial - probe)
    lam = -slope
    if not math.isfinite(lam) or lam <= 0.0:
        return rate, 0.0, None
    return rate, lam, partial + rate / lam


def _exponential_partial(
    partial: float,
    duration_s: float,
    lam: float,
    p_star: float,
) -> float:
    """Unclamped local exponential p_star + (p - p_star) exp(-λ t).

    The next strip floor is not substituted. A Newton step whose
    equilibrium lies below that floor would otherwise become the
    captured partial.
    """

    exponent = -lam * duration_s
    if exponent <= -700.0:
        return p_star
    return p_star + (partial - p_star) * math.exp(exponent)


def _adaptive_nonlinear_partial(
    partial: float,
    duration: float,
    strips: tuple[tuple[float, float, float, float], ...],
) -> float:
    """Element partial for the nonlinear dimer drive.

    The dimer term makes dp_elem/dt nonlinear, so there is no closed
    form. Each accepted step is a local exponential dp/dt = -λ (p - p_star),
    halved until a re-linearized half-step agrees to 1e-10 relative.

    p_star below the next strip's p_elem,sat is a Newton overshoot of
    the frozen line, not an arrival. The line reaches that floor in
    finite time; the ODE only approaches it. The unclamped exponential
    is legal only while it stays above the floor. A longer step is
    halved. The floor is written only after an accepted step has
    already landed within tolerance of it and many time constants
    remain, so a stiff strip stops and a colder strip continues. A
    residence of a few time constants stays above the floor. Nothing
    snaps an oversized step onto the coldest floor.
    """

    t = 0.0
    pressure = float(partial)
    rows = strips
    for _guard in range(200000):
        remaining = duration - t
        if remaining <= 1.0e-15 * max(1.0, duration):
            return pressure
        next_floor = _next_strip_floor_pa(pressure, rows)
        rate, lam, p_star = _local_drawdown_line(pressure, rows, next_floor)
        tolerance = 1.0e-10 * max(1.0, abs(pressure)) + 1.0e-12
        if rate >= -1.0e-18 * max(1.0, abs(pressure)):
            return pressure
        if lam <= 0.0 or p_star is None:
            dt = remaining
            if next_floor is not None and rate < 0.0:
                dt = min(dt, 0.2 * (pressure - next_floor) / abs(rate))
            updated = pressure + rate * dt
            if next_floor is not None and updated < next_floor:
                # Euler crossed. Shorten; do not replace the step
                # with the floor.
                room = pressure - next_floor
                dt = 0.5 * room / abs(rate) if room > 0.0 else 0.0
                updated = pressure + rate * dt
            if dt <= 0.0 or updated >= pressure:
                return pressure
            pressure = updated
            t += dt
            continue
        dt_cap = remaining
        if next_floor is not None and p_star < next_floor:
            gap = next_floor - p_star
            numer = pressure - p_star
            if gap > 0.0 and numer > gap:
                # Frozen line would cross here. Stay strictly above.
                hit_s = math.log(numer / gap) / lam
                dt_cap = min(dt_cap, hit_s * (1.0 - 1.0e-12))
            else:
                dt_cap = 0.0
        if dt_cap <= 0.0:
            return pressure
        dt = dt_cap
        accepted = None
        for _halve in range(50):
            full = _exponential_partial(pressure, dt, lam, p_star)
            if not math.isfinite(full):
                dt *= 0.5
                continue
            if next_floor is not None and full < next_floor - tolerance:
                dt *= 0.5
                continue
            mid = _exponential_partial(pressure, 0.5 * dt, lam, p_star)
            if not math.isfinite(mid) or (
                next_floor is not None and mid < next_floor - tolerance
            ):
                dt *= 0.5
                continue
            _mid_rate, mid_lam, mid_star = _local_drawdown_line(
                mid, rows, next_floor,
            )
            if mid_lam <= 0.0 or mid_star is None:
                second = mid + _mid_rate * (0.5 * dt)
            else:
                second = _exponential_partial(mid, 0.5 * dt, mid_lam, mid_star)
            if not math.isfinite(second):
                dt *= 0.5
                continue
            if next_floor is not None and second < next_floor - tolerance:
                dt *= 0.5
                continue
            if abs(second - full) <= tolerance:
                accepted = second
                break
            dt *= 0.5
        else:
            room = (
                pressure - next_floor
                if next_floor is not None else abs(pressure)
            )
            dt = min(dt, 0.25 * room / abs(rate)) if room > 0.0 else 0.0
            if dt <= 0.0:
                return pressure
            accepted = pressure + rate * dt
            if next_floor is not None and accepted < next_floor:
                return pressure
        if accepted is None or accepted > pressure or accepted == pressure:
            return pressure
        # Already on this strip's floor, with a stiff remainder. Land
        # so colder strips see the time that is left. The trajectory
        # that got here was accepted; the floor is not a shortcut.
        if (
            next_floor is not None
            and abs(accepted - next_floor) <= tolerance
            and lam * max(0.0, remaining - dt) >= 40.0
        ):
            accepted = next_floor
        pressure = accepted
        t += dt
    return pressure


def final_element_partial_pa(
    *,
    element_partial_initial_pa: float,
    residence_s: float,
    strips: tuple[tuple[float, float, float, float], ...] | list[
        tuple[float, float, float, float]
    ],
) -> float:
    """Element partial after residence under the parallel fluxes.

    ``strips`` is (k_M, k_M2, P_sat_Pa, K_p_Pa). See
    ``_element_drawdown_rate_pa_s``. This is the only drawdown. Stage
    efficiency and wall capture both call it. A fast strip stops on its
    own p_elem,sat. A slow strip then barely moves. Nothing snaps an
    oversized step to the coldest floor, and nothing caps the removal
    with min(rate, supply).

    Linear strips (K_p -> inf) use the closed form
    p(t) = P_sat + (p0 - P_sat) exp(-k t) on each active set. The dimer
    term is nonlinear and uses the adaptive exponential instead. The
    element never falls below p_elem,sat = P_sat + 2 P_sat^2 / K_p.

    Production strips that apply the monomer sticking coefficient to
    the dimer call ``draw_element_partial_pa``, which records
    ``dimer_alpha_from_monomer_proxy`` and then calls this. A direct
    call here is the integral only and does not publish that notice.
    """

    partial = float(element_partial_initial_pa)
    duration = float(residence_s)
    rows = tuple(strips)
    if not math.isfinite(partial) or partial <= 0.0 or duration <= 0.0 or not rows:
        return max(0.0, partial) if math.isfinite(partial) else 0.0
    floor = _element_saturation_floor_pa(rows)
    if not math.isfinite(floor) or partial <= floor:
        return partial
    if _element_drawdown_rate_pa_s(partial, rows) == 0.0:
        return partial
    if _strips_are_linear(rows):
        updated = _final_linear_monomer_partial(partial, duration, rows)
    else:
        updated = _adaptive_nonlinear_partial(partial, duration, rows)
    if not math.isfinite(updated):
        return partial
    # An ulp under the equilibrium is the floor. A real overshoot is left
    # in place so a heterogeneous step cannot be snapped back up to it.
    if updated < floor and floor - updated <= 1e-9 * max(1.0, abs(floor)):
        return floor
    if updated > partial:
        return partial
    return updated


def _strip_uses_monomer_alpha_for_dimer(
    strips: tuple[tuple[float, float, float, float], ...],
) -> bool:
    """True when a strip applies a finite-K_p dimer coefficient.

    That coefficient is the monomer accommodation used as alpha_M2.
    K_p -> inf drops the dimer drive, and a zero coefficient never
    applies the proxy. The no-K_p upper bound does not build these strips.
    """

    for _k_monomer, k_dimer, _saturation, kp_pa in strips:
        kp = float(kp_pa)
        if float(k_dimer) > 0.0 and math.isfinite(kp) and kp > 0.0:
            return True
    return False


def draw_element_partial_pa(
    *,
    element_partial_initial_pa: float,
    residence_s: float,
    strips: tuple[tuple[float, float, float, float], ...] | list[
        tuple[float, float, float, float]
    ],
    monomer: str,
    dimer: str,
    proxy_notice: MutableMapping[str, str],
) -> float:
    """The only production integral of the parallel dimer channel.

    Invariant. Whenever a strip uses the monomer sticking coefficient
    as alpha_M2 (k_M2 > 0 and K_p finite), this records
    ``proxy_notice[dimer] = monomer`` and then integrates. Stage
    efficiency and wall capture both call this and do not integrate
    that channel anywhere else. A monomer-only feed is the same
    channel as a dimer feed: the gas re-speciates, and a cold wall
    with zero collector area still deposits K2 with alpha_K. K_p -> inf
    records nothing, because the dimer drive is gone.
    """

    rows = tuple(strips)
    if _strip_uses_monomer_alpha_for_dimer(rows):
        proxy_notice[str(dimer)] = str(monomer)
    return final_element_partial_pa(
        element_partial_initial_pa=element_partial_initial_pa,
        residence_s=residence_s,
        strips=rows,
    )


# Monomer has no condenser contract. The vapor stays in the stream.
MISSING_COLLECTION_PRODUCT_CONTRACT_SPECIES: frozenset[str] = frozenset(
    species
    for species in METAL_DIMER_SPECIES
    if dimer_collection_monomer(species) is None
)
MISSING_COLLECTION_PRODUCT_CONTRACT_REASON = (
    'missing_collection_product_contract'
)

CONDENSATION_COPRODUCT_STAGE: dict[str, str] = {
    'Si': 'stage_3_sio_zone',
}

METAL_PHASE_DESTINATIONS: dict[str, str] = {
    'Al': 'metal_phase_al',
    'Ca': 'metal_phase_ca',
    'Si': 'metal_phase_si',
    'Ti': 'metal_phase_ti',
}

# Recipe-extracted products are not automatically condenser targets. A stage
# key here means the product is physically routed to that train stage; a
# metal_phase_* value means the atom ledger's metal-phase account is the honest
# destination and no condenser-stage projection should be minted.
PRODUCT_DESTINATIONS: dict[str, dict[str, str]] = {
    'C3': {
        'Fe': 'stage_1_fe_condenser',
        'Cr': 'stage_2_cr_oxide_harvest',
        'CrO2': 'stage_2_cr_oxide_harvest',
        'Ti': 'metal_phase_ti',
    },
    'C5': {
        'Fe': 'stage_1_fe_condenser',
        'Cr': 'stage_2_cr_oxide_harvest',
        'Mn': 'stage_2_cr_oxide_harvest',
        'Si': 'metal_phase_si',
        'Ti': 'metal_phase_ti',
    },
    'C6': {
        'Al': 'metal_phase_al',
        'Si': 'metal_phase_si',
    },
    'C7': {
        'Ca': 'stage_4_alkali_mg_cyclone',
    },
    'MRE': {
        'Fe': 'stage_1_fe_condenser',
        'Cr': 'stage_2_cr_oxide_harvest',
        'Mn': 'stage_2_cr_oxide_harvest',
        'Si': 'metal_phase_si',
        'Ti': 'metal_phase_ti',
        'Al': 'metal_phase_al',
        'Mg': NA_COLLECTOR_STAGE_KEY,
        'Ca': 'metal_phase_ca',
        'Na': NA_COLLECTOR_STAGE_KEY,
        'K': K_TRAP_STAGE_KEY,
    },
}


def stage_number_for_destination(destination: str | None) -> int | None:
    """Return condenser stage number for a stage destination key."""

    if not destination:
        return None
    return STAGE_NUMBER_BY_KEY.get(destination)


def designated_stage_number(species: str) -> int | None:
    """Return canonical condenser stage for vapor species, if any."""

    return stage_number_for_destination(DESIGNATED_STAGE.get(species))


def accepted_species_for_stage_number(stage_number: int) -> frozenset[str]:
    """Species that may land in a stage without counting as impurity.

    A sub-stage key that shares this number is not accepted here. K is
    designated to the K trap, so stage 4's accepted set is Na and Mg.
    """

    canonical = STAGE_KEY_BY_NUMBER.get(stage_number)
    accepted = {
        species
        for species, destination in DESIGNATED_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
        and (canonical is None or destination == canonical)
    }
    accepted.update(
        species
        for species, destination in CONDENSATION_COPRODUCT_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
        and (canonical is None or destination == canonical)
    )
    return frozenset(accepted)


def accepted_species_for_stage_key(stage_key: str) -> frozenset[str]:
    """Accepted species for one stage key, including a sub-stage key."""

    number = STAGE_NUMBER_BY_KEY.get(stage_key)
    if (
        number is not None
        and STAGE_KEY_BY_NUMBER.get(number) == stage_key
    ):
        return accepted_species_for_stage_number(number)
    accepted = {
        species
        for species, destination in DESIGNATED_STAGE.items()
        if destination == stage_key
    }
    accepted.update(
        species
        for species, destination in CONDENSATION_COPRODUCT_STAGE.items()
        if destination == stage_key
    )
    return frozenset(accepted)


def species_accepted_on_stage(stage: object, species: str) -> bool:
    """True when this stage object, not merely its number, accepts species."""

    key = getattr(stage, 'stage_key', '') or ''
    if key:
        if DESIGNATED_STAGE.get(species) == key:
            return True
        if CONDENSATION_COPRODUCT_STAGE.get(species) == key:
            return True
        if key == STAGE_KEY_BY_NUMBER.get(int(stage.stage_number)):
            return is_designated_for_stage(species, int(stage.stage_number))
        return False
    return is_designated_for_stage(species, int(stage.stage_number))


def coproduct_species_for_stage_number(stage_number: int) -> frozenset[str]:
    """Condensation coproducts accepted in a stage but not primary targets."""

    return frozenset(
        species
        for species, destination in CONDENSATION_COPRODUCT_STAGE.items()
        if stage_number_for_destination(destination) == stage_number
    )


def target_species_for_stage_number(stage_number: int) -> list[str]:
    """Primary target species for public stage definitions."""

    return list(STAGE_TARGET_SPECIES.get(stage_number, ()))


def target_species_for_stage_key(stage_key: str) -> list[str]:
    """Primary targets for a stage key. Sub-stage keys are not the integer map."""

    if stage_key in STAGE_TARGET_SPECIES_BY_KEY:
        return list(STAGE_TARGET_SPECIES_BY_KEY[stage_key])
    number = STAGE_NUMBER_BY_KEY.get(stage_key)
    if number is not None and STAGE_KEY_BY_NUMBER.get(number) == stage_key:
        return target_species_for_stage_number(number)
    return []


def stage_list_index_for_destination(train: object, destination: str | None) -> int | None:
    """List index of the stage object for a destination key.

    Public stage numbers stay 0-7. The K trap shares number 4 and is
    appended after stage 7, so its list index is not its stage number.
    Metal-phase destinations are not condenser stages.
    """

    if not destination or not str(destination).startswith('stage_'):
        return None
    stages = getattr(train, 'stages', ())
    for index, stage in enumerate(stages):
        if stage_key_for_stage(
            stage,
            falsey_sub_order_is_zero=True,
            include_collector_fallback=True,
        ) == destination:
            return index
    number = stage_number_for_destination(destination)
    if number is None:
        return None
    for index, stage in enumerate(stages):
        if (
            int(stage.stage_number) == number
            and int(getattr(stage, 'sub_order', 0) or 0) == 0
        ):
            return index
    return None


def product_destination(recipe: str, species: str) -> str | None:
    """Return the honest destination for a recipe-extracted product."""

    return PRODUCT_DESTINATIONS.get(recipe, {}).get(species)


def product_stage_number(recipe: str, species: str) -> int | None:
    """Return stage number only when product destination is a condenser stage."""

    return stage_number_for_destination(product_destination(recipe, species))


def is_designated_for_stage(species: str, stage_number: int) -> bool:
    """True when species belongs to the stage's accepted-species set."""

    return species in accepted_species_for_stage_number(stage_number)
