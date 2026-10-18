"""Pure shadow solve for cold-train/overhead capacity coupling."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    compressible_pressure_capacity_fraction,
    controlled_flow_capacity,
)
from simulator.physical_constants import GAS_CONSTANT
from simulator.thermal_train import FiniteCapacity, NoColdTrain
from simulator.transport_constants import (
    COLLISION_DIAMETERS_M,
    FREE_MOLECULAR_KNUDSEN_MIN,
    N2_COLLISION_DIAMETER_M,
    VISCOUS_KNUDSEN_MAX,
)


OXYGEN_SPECIES = "O2"
DEFAULT_REL_TOL = 1.0e-9
DEFAULT_ABS_TOL_PA = 1.0e-6
DEFAULT_MAX_ITERATIONS = 50
SOURCE_DUCT_MAX_ITERATIONS = 80
SOURCE_DUCT_H_REFINE_ITERATIONS = 80
SOURCE_DUCT_ABS_TOL_PA = 1.0e-12


@dataclass(frozen=True)
class OxygenShadowPartition:
    external_mol: float
    admitted_mol: float
    accumulated_mol: float
    relieved_mol: float
    held_mol: float

    @property
    def debited_mol(self) -> float:
        return (
            self.external_mol
            + self.admitted_mol
            + self.accumulated_mol
            + self.relieved_mol
        )


@dataclass(frozen=True)
class SaturationShadow:
    pipe: float
    oxygen: float | None
    combined: float
    binding_cause: str


@dataclass(frozen=True)
class CapacityShadowResult:
    capacity: NoColdTrain | FiniteCapacity
    partial_pressures_Pa: Mapping[str, float]
    evaporation_flux_kg_hr: Mapping[str, float]
    bled_species_mol: Mapping[str, float]
    terminal_offgas_mol: Mapping[str, float]
    oxygen: OxygenShadowPartition
    saturation: SaturationShadow
    iterations: int
    max_delta_history_Pa: tuple[float, ...]
    mass_closure_error_pct: float
    authoritative: bool = False
    source_duct_residual_rel: float = 0.0
    closure_notices: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "partial_pressures_Pa",
            "evaporation_flux_kg_hr",
            "bled_species_mol",
            "terminal_offgas_mol",
        ):
            object.__setattr__(
                self,
                name,
                MappingProxyType(dict(getattr(self, name))),
            )
        object.__setattr__(
            self,
            "closure_notices",
            tuple(dict(notice) for notice in self.closure_notices),
        )


@dataclass(frozen=True)
class CapacityShadowRefusal:
    reason: str
    iterations: int
    authoritative: bool = False


class CapacityCouplingRefusalError(RuntimeError):
    def __init__(self, refusal: CapacityShadowRefusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.reason)


def partition_melt_oxygen(
    *,
    bled_o2_mol: float,
    overhead_o2_mol: float,
    external_o2_holdup_mol: float,
    capacity: FiniteCapacity,
    dt_hr: float,
    p_o2_Pa: float,
    k_relief_kg_hr_Pa: float,
    p_open_Pa: float,
    molar_mass_kg_mol: float,
    accumulator_enabled: bool = False,
    cistern_fill_kg: float = 0.0,
    cavern_capacity_kg: float = 0.0,
) -> OxygenShadowPartition:
    # Ordinary bleed preserves the HEAD provenance split. Admission acts on
    # its melt-origin share. Relief is an additional debit from the full
    # post-admission melt-origin inventory, independent of ordinary bleed.
    # Unit check: (kg/hr)*hr/(kg/mol) and
    # (kg/(hr Pa))*Pa*hr/(kg/mol) both reduce to mol. Sanity anchors: with no
    # external holdup and ample capacity, all bled O2 is admitted; with zero
    # admission and closed relief, all melt-origin bled O2 remains held.
    external_holdup = min(
        max(0.0, external_o2_holdup_mol), max(0.0, overhead_o2_mol)
    )
    external = (
        min(bled_o2_mol, bled_o2_mol * external_holdup / overhead_o2_mol)
        if bled_o2_mol > 0.0 and overhead_o2_mol > 0.0
        else 0.0
    )
    melt_inventory = max(0.0, overhead_o2_mol - external_holdup)
    melt_bled = min(melt_inventory, max(0.0, bled_o2_mol - external))
    admitted = min(
        melt_bled,
        capacity.value_kg_hr * dt_hr / molar_mass_kg_mol,
    )
    remainder = max(0.0, melt_inventory - admitted)
    accumulated = 0.0
    if accumulator_enabled:
        available_cistern_kg = max(0.0, cavern_capacity_kg - cistern_fill_kg)
        accumulated = min(
            remainder,
            available_cistern_kg / molar_mass_kg_mol,
        )
        remainder = max(0.0, remainder - accumulated)
    relief_law_mol = (
        k_relief_kg_hr_Pa
        * max(0.0, p_o2_Pa - p_open_Pa)
        * dt_hr
        / molar_mass_kg_mol
    )
    relieved = min(remainder, max(0.0, relief_law_mol))
    held = max(0.0, remainder - relieved)
    return OxygenShadowPartition(
        external, admitted, accumulated, relieved, held
    )


def combined_saturation(
    *,
    total_evaporation_kg_hr: float,
    oxygen_evaporation_kg_hr: float,
    pipe_capacity_kg_hr: float,
    capacity: NoColdTrain | FiniteCapacity,
) -> SaturationShadow:
    pipe = (
        total_evaporation_kg_hr / pipe_capacity_kg_hr
        if pipe_capacity_kg_hr > 0.0
        else (math.inf if total_evaporation_kg_hr > 0.0 else 0.0)
    )
    oxygen = (
        oxygen_evaporation_kg_hr / capacity.value_kg_hr
        if isinstance(capacity, FiniteCapacity)
        else None
    )
    if oxygen is None or pipe >= oxygen:
        return SaturationShadow(pipe, oxygen, pipe, "pipe")
    return SaturationShadow(pipe, oxygen, oxygen, "oxygen")


def hertz_knudsen_molar_conductance_mol_s_Pa(
    *,
    alpha: float,
    area_m2: float,
    molar_mass_kg_mol: float,
    temperature_K: float,
) -> float:
    # Premise: equal-temperature Hertz–Knudsen net number rate is
    # Ndot = alpha*A*(P_eq-p)/sqrt(2*pi*m*k_B*T) [molecules/s].
    # Algebra: m=M/N_A and k_B=R/N_A, so dividing by N_A gives the
    # molar source q_source = H*(P_eq-p) [mol/s] with
    # H = alpha*A/sqrt(2*pi*M*R*T) [mol/(s Pa)].
    # Unit check: sqrt(M R T) is kg m /(mol s); Pa divided by that
    # times m^2 is mol/s. Sanity: alpha=0 or A=0 or T<=0 gives H=0.
    alpha_f = max(0.0, float(alpha))
    area_f = max(0.0, float(area_m2))
    mass_f = float(molar_mass_kg_mol)
    temp_f = float(temperature_K)
    if (
        alpha_f <= 0.0
        or area_f <= 0.0
        or mass_f <= 0.0
        or not math.isfinite(temp_f)
        or temp_f <= 0.0
    ):
        return 0.0
    return alpha_f * area_f / math.sqrt(
        2.0 * math.pi * mass_f * GAS_CONSTANT * temp_f
    )


def pipe1_surface_pressure_Pa(
    *,
    P_eq_Pa: float,
    H_mol_s_Pa: float,
    G_mol_s_Pa2: float,
    P_downstream_Pa: float = 0.0,
) -> float:
    # Premise: quasi-steady melt-only source against a PIPE-1 duct.
    # Closure: H*(P_eq-p) = q_duct(p) in the same tick.
    # PIPE-1 mass outflow is F=pi*d^4*M*(p^2-P_d^2)/(256*eta*L*R*T)
    # [kg/s]; divide by M to get q_duct=G*(p^2-P_d^2) with
    # G=pi*d^4/(256*eta*L*R*T) [mol/(s Pa^2)].
    # Vacuum (P_d=0): G*p^2 + H*p - H*P_eq = 0. The positive root,
    # written without subtractive cancellation, is
    # p = 2*P_eq/(1+sqrt(1+4*G*P_eq/H)).
    # Unit check: G*P_eq/H is dimensionless; H*(P_eq-p) and G*p^2
    # are both mol/s.
    # Limits: G→∞ gives p→0 (free HK); G→0 gives p→P_eq (blocked
    # duct); P_eq=0 gives p=0. Nonzero downstream P_d raises the
    # lower bound to P_d and replaces p^2 with (p^2-P_d^2).
    P_eq = max(0.0, float(P_eq_Pa))
    H = max(0.0, float(H_mol_s_Pa))
    G = max(0.0, float(G_mol_s_Pa2))
    P_down = max(0.0, float(P_downstream_Pa))
    if P_eq <= 0.0:
        return P_down
    if H <= 0.0:
        return P_down
    if G <= 0.0:
        return max(P_eq, P_down)
    if P_down <= 0.0:
        return 2.0 * P_eq / (1.0 + math.sqrt(1.0 + 4.0 * G * P_eq / H))
    # Finite downstream: G*p^2 + H*p - (H*P_eq + G*P_d^2) = 0.
    discriminant = H * H + 4.0 * G * (H * P_eq + G * P_down * P_down)
    pressure = (-H + math.sqrt(discriminant)) / (2.0 * G)
    return max(P_down, min(pressure, max(P_eq, P_down)))


def _pipe1_mixture_factor(total_Pa: float, G_mol_s_Pa2: float, P_down_Pa: float) -> float:
    # Well-mixed PIPE-1: q_i = p_i * G * (P - P_d^2/P) = p_i * factor.
    # Vacuum reduces to factor = G*P, so q_i = G*P*p_i.
    if G_mol_s_Pa2 <= 0.0 or total_Pa <= P_down_Pa:
        return 0.0
    if P_down_Pa <= 0.0:
        return G_mol_s_Pa2 * total_Pa
    return G_mol_s_Pa2 * (total_Pa - P_down_Pa * P_down_Pa / total_Pa)


def _duct_viscous_weight(knudsen_number: float) -> float:
    # Existing Kn owner: viscous Kn < 0.01, free-molecular Kn >= 10,
    # transitional in between (transport_constants / classify_knudsen_regime).
    # Weight is 1 on the viscous PIPE-1 law, 0 on the long-tube Knudsen law,
    # log-interpolated across the owner's transitional band so C2A (Kn~3e-4)
    # stays pure PIPE-1.
    from simulator.transport_regime import KnudsenRegime, classify_knudsen_regime

    kn = float(knudsen_number)
    if math.isnan(kn) or kn < 0.0:
        return 1.0
    regime = classify_knudsen_regime(kn)
    if regime is KnudsenRegime.VISCOUS:
        return 1.0
    if regime is KnudsenRegime.FREE_MOLECULAR:
        return 0.0
    return (
        math.log(FREE_MOLECULAR_KNUDSEN_MIN) - math.log(kn)
    ) / (
        math.log(FREE_MOLECULAR_KNUDSEN_MIN) - math.log(VISCOUS_KNUDSEN_MAX)
    )


def _knudsen_number_at_total_Pa(
    total_Pa: float,
    *,
    temperature_K: float,
    diameter_m: float,
    carrier_gas: str,
) -> float:
    from simulator.transport_regime import single_species_mean_free_path_m

    if diameter_m <= 0.0:
        return math.inf
    carrier = str(carrier_gas or "N2")
    sigma = float(COLLISION_DIAMETERS_M.get(carrier, N2_COLLISION_DIAMETER_M))
    mean_free_path_m = single_species_mean_free_path_m(
        max(0.0, float(total_Pa)),
        float(temperature_K),
        sigma,
    )
    return mean_free_path_m / diameter_m


def _long_tube_molar_conductance_mol_s_Pa(
    *,
    diameter_m: float,
    length_m: float,
    temperature_K: float,
    molar_mass_kg_mol: float,
) -> tuple[float, dict[str, object] | None]:
    # Owner: transport_regime.long_tube_molecular_conductance_m3_s.
    # Volume conductance C_v = 0.25 * A * v_bar * (4d/(3L)) [m^3/s].
    # Molar C = C_v / (R T) [mol/(s Pa)]. Same units as Hertz-Knudsen H.
    # C ∝ d^3 / (L sqrt(M T)). Throughput q = C * Δp.
    from simulator.transport_regime import (
        TransportRegimeRefusal,
        long_tube_molecular_conductance_m3_s,
        molecular_tube_conductance_m3_s,
    )

    if (
        diameter_m <= 0.0
        or length_m <= 0.0
        or temperature_K <= 0.0
        or molar_mass_kg_mol <= 0.0
    ):
        return 0.0, None
    notice: dict[str, object] | None = None
    try:
        c_m3_s = long_tube_molecular_conductance_m3_s(
            diameter_m,
            length_m,
            temperature_K,
            molar_mass_kg_mol,
            knudsen_number=math.inf,
        )
    except TransportRegimeRefusal as exc:
        if exc.category != "clausing_long_tube_asymptote_out_of_range":
            raise
        # Same 4d/(3L) Clausing factor; L/D is below the certified floor of 10.
        # Predict-and-flag the extension; do not abort.
        transmission = 4.0 * diameter_m / (3.0 * length_m)
        c_m3_s = molecular_tube_conductance_m3_s(
            diameter_m,
            length_m,
            temperature_K,
            molar_mass_kg_mol,
            transmission_probability=min(1.0, transmission),
            knudsen_number=math.inf,
        )
        notice = {
            "reason": "clausing_long_tube_asymptote_out_of_range",
            "status": "model_limit",
            "length_over_diameter": length_m / diameter_m,
            "detail": (
                "long-tube Clausing 4d/(3L) used below L/D=10; "
                "molecular pipe C is an extension"
            ),
        }
    return c_m3_s / (GAS_CONSTANT * temperature_K), notice


def _bracketed_root(
    residual,
    lo: float,
    hi: float,
    f_lo: float,
    f_hi: float,
    *,
    rel_tol: float,
    abs_tol: float,
    max_iterations: int,
) -> float:
    if f_lo == 0.0:
        return lo
    if f_hi == 0.0:
        return hi
    if f_lo * f_hi > 0.0:
        if abs(f_lo) <= abs_tol:
            return lo
        if abs(f_hi) <= abs_tol:
            return hi
        raise CapacityCouplingRefusalError(
            CapacityShadowRefusal("source_duct_bracket_missed", 0)
        )
    width_tol = max(abs_tol, rel_tol * max(abs(lo), abs(hi), 1.0))
    cap = max(1, int(max_iterations))
    for iteration in range(1, cap + 1):
        mid = 0.5 * (lo + hi)
        width_tol = max(abs_tol, rel_tol * max(abs(lo), abs(hi), 1.0))
        if hi - lo <= width_tol:
            return mid
        f_mid = float(residual(mid))
        if f_mid == 0.0 or abs(f_mid) <= abs_tol:
            return mid
        if f_lo * f_mid <= 0.0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    mid = 0.5 * (lo + hi)
    width_tol = max(abs_tol, rel_tol * max(abs(lo), abs(hi), 1.0))
    if hi - lo <= width_tol or abs(float(residual(mid))) <= abs_tol:
        return mid
    raise CapacityCouplingRefusalError(
        CapacityShadowRefusal("source_duct_root_non_convergence", cap)
    )


def mixture_surface_partials_Pa(
    *,
    P_eq_Pa: Mapping[str, float],
    H_mol_s_Pa: Mapping[str, float],
    G_mol_s_Pa2: float,
    P_downstream_Pa: float = 0.0,
    extra_partials_Pa: Mapping[str, float] | None = None,
    P_carrier_Pa: float = 0.0,
    molecular_C_mol_s_Pa: Mapping[str, float] | None = None,
    knudsen_at_total_Pa: Callable[[float], float] | None = None,
) -> dict[str, float]:
    # Premise: the well-mixed melt-to-train mixture is the actual boundary
    # gas, P = P_carrier + Σ p_i, not a vapour-only pipe. P_carrier is the
    # regulator/carrier fill from the atmosphere owner; extras also hold
    # retained incoming gas that exceeds a newly lowered P_eq.
    # Duct law follows the existing Kn owner (classify_knudsen_regime).
    # Viscous PIPE-1: q_total = G*(P^2 - P_d^2). Well-mixed y_i = p_i/P
    # gives q_i = p_i * G * (P - P_d^2/P). Vacuum P_d=0 reduces to
    # q_i = G*P*p_i. Closing H_i*(P_eq_i - p_i) = q_i yields
    # p_i = H_i*P_eq_i / (H_i + factor(P, P_d)).
    # Free-molecular long-tube Knudsen (transport_regime owner):
    # q_i = C_i*(p_i - p_d,i) with C_i = C_v,i/(R T) [mol/(s Pa)],
    # C_v from long_tube_molecular_conductance_m3_s. Species are
    # independent (no common-P coupling). Closed form
    # p_i = (H_i*P_eq_i + C_i*p_d,i) / (H_i + C_i).
    # Limits: C→∞ gives p→p_d; H→∞ gives p→P_eq. Units: H and C both
    # mol/(s Pa).
    # Transitional: log-weight w_v(Kn) between PIPE-1 and Knudsen at the
    # candidate total P, inside the same bracketed root:
    # p_i = (H_i*P_eq_i + w_m*C_i*p_d) / (H_i + w_v*factor + w_m*C_i).
    # w_v=1 recovers viscous; w_v=0 recovers the molecular closed form.
    # Unit check: G is mol/(s Pa^2); G*P, H_i and C_i are mol/(s Pa).
    extra = {
        str(species): max(0.0, float(pressure))
        for species, pressure in dict(extra_partials_Pa or {}).items()
        if float(pressure) > 0.0
    }
    P_carrier = max(0.0, float(P_carrier_Pa))
    P_extra = sum(extra.values()) + P_carrier
    P_down = max(0.0, float(P_downstream_Pa))
    C_map = {
        str(species): max(0.0, float(conductance))
        for species, conductance in dict(molecular_C_mol_s_Pa or {}).items()
        if float(conductance) > 0.0
    }

    def companion_pressure(species: str) -> float:
        return P_down if float(P_eq_Pa.get(species, 0.0)) <= 0.0 else 0.0

    solved_species = tuple(
        str(species)
        for species, pressure in P_eq_Pa.items()
        if float(pressure) > 0.0
        and float(H_mol_s_Pa.get(species, 0.0)) > 0.0
        and species not in extra
    )
    if not solved_species:
        return {
            **{
                str(species): companion_pressure(str(species))
                for species in P_eq_Pa
                if species not in extra
            },
            **extra,
        }
    if (
        len(solved_species) == 1
        and not extra
        and P_carrier <= 0.0
        and knudsen_at_total_Pa is None
    ):
        species = solved_species[0]
        pressure = pipe1_surface_pressure_Pa(
            P_eq_Pa=float(P_eq_Pa[species]),
            H_mol_s_Pa=float(H_mol_s_Pa.get(species, 0.0)),
            G_mol_s_Pa2=G_mol_s_Pa2,
            P_downstream_Pa=P_down,
        )
        return {
            str(name): (pressure if name == species else companion_pressure(str(name)))
            for name in P_eq_Pa
        }
    P_eq_sum = sum(max(0.0, float(P_eq_Pa[species])) for species in solved_species)
    lo = 0.0
    hi = max(P_eq_sum, 0.0)

    def partials_at(P_vapour: float) -> dict[str, float]:
        total = max(0.0, P_vapour) + P_extra
        factor = _pipe1_mixture_factor(
            total, G_mol_s_Pa2, P_down
        )
        if knudsen_at_total_Pa is None:
            w_v = 1.0
        else:
            w_v = _duct_viscous_weight(float(knudsen_at_total_Pa(total)))
        w_m = 1.0 - w_v
        out = dict(extra)
        for species in P_eq_Pa:
            if species in extra:
                continue
            P_eq = max(0.0, float(P_eq_Pa[species]))
            conductance = max(0.0, float(H_mol_s_Pa.get(species, 0.0)))
            if P_eq <= 0.0 or conductance <= 0.0:
                out[species] = companion_pressure(str(species))
                continue
            C = float(C_map.get(species, 0.0))
            denom = conductance + w_v * factor + w_m * C
            if denom <= 0.0:
                out[species] = P_eq
            else:
                out[species] = (
                    conductance * P_eq + w_m * C * P_down
                ) / denom
        return out

    def residual(P_vapour: float) -> float:
        solved = partials_at(P_vapour)
        return sum(solved[species] for species in solved_species) - P_vapour

    if hi <= lo:
        return partials_at(lo)
    f_lo = residual(lo)
    f_hi = residual(hi)
    if abs(f_lo) <= SOURCE_DUCT_ABS_TOL_PA:
        return partials_at(lo)
    if abs(f_hi) <= SOURCE_DUCT_ABS_TOL_PA:
        return partials_at(hi)
    root = _bracketed_root(
        residual,
        lo,
        hi,
        f_lo,
        f_hi,
        rel_tol=DEFAULT_REL_TOL,
        abs_tol=SOURCE_DUCT_ABS_TOL_PA,
        max_iterations=SOURCE_DUCT_MAX_ITERATIONS,
    )
    return partials_at(root)


def _h_from_free_flux(
    *,
    P_eq_Pa: Mapping[str, float],
    flux_kg_hr: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
) -> dict[str, float]:
    conductance: dict[str, float] = {}
    for species, P_eq in P_eq_Pa.items():
        equilibrium = max(0.0, float(P_eq))
        mass = float(molar_mass_kg_mol.get(species, 0.0))
        if equilibrium <= 0.0 or mass <= 0.0:
            conductance[str(species)] = 0.0
            continue
        q_mol_s = max(0.0, float(flux_kg_hr.get(species, 0.0))) / mass / 3600.0
        conductance[str(species)] = q_mol_s / equilibrium
    return conductance


def _source_duct_residual_rel(
    *,
    partials_Pa: Mapping[str, float],
    flux_kg_hr: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    G_mol_s_Pa2: float,
    P_downstream_Pa: float,
    P_carrier_Pa: float = 0.0,
    molecular_C_mol_s_Pa: Mapping[str, float] | None = None,
    knudsen_at_total_Pa: Callable[[float], float] | None = None,
) -> float:
    total_P = (
        sum(max(0.0, float(pressure)) for pressure in partials_Pa.values())
        + max(0.0, float(P_carrier_Pa))
    )
    factor = _pipe1_mixture_factor(total_P, G_mol_s_Pa2, P_downstream_Pa)
    if knudsen_at_total_Pa is None:
        w_v = 1.0
    else:
        w_v = _duct_viscous_weight(float(knudsen_at_total_Pa(total_P)))
    w_m = 1.0 - w_v
    C_map = dict(molecular_C_mol_s_Pa or {})
    P_down = max(0.0, float(P_downstream_Pa))
    max_scale = 0.0
    max_delta = 0.0
    species = set(partials_Pa) | set(flux_kg_hr)
    for name in species:
        mass = float(molar_mass_kg_mol.get(name, 0.0))
        if mass <= 0.0:
            continue
        q_source = max(0.0, float(flux_kg_hr.get(name, 0.0))) / mass / 3600.0
        if q_source <= 0.0:
            continue
        p_i = max(0.0, float(partials_Pa.get(name, 0.0)))
        C = max(0.0, float(C_map.get(name, 0.0)))
        q_duct = w_v * p_i * factor + w_m * C * (p_i - P_down)
        max_delta = max(max_delta, abs(q_source - q_duct))
        max_scale = max(max_scale, abs(q_source), abs(q_duct))
    if max_scale <= 0.0:
        return 0.0
    return max_delta / max_scale


def solve_source_duct_closure(
    *,
    P_eq_Pa: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    molar_mass_kg_mol: Mapping[str, float],
    G_mol_s_Pa2: float,
    capacity: NoColdTrain | FiniteCapacity,
    P_downstream_Pa: float = 0.0,
    H_mol_s_Pa: Mapping[str, float] | None = None,
    incoming_partials_Pa: Mapping[str, float] | None = None,
    carrier_partials_Pa: Mapping[str, float] | None = None,
    P_carrier_Pa: float = 0.0,
    head_bled_species_mol: Mapping[str, float] | None = None,
    pipe_diameter_m: float | None = None,
    pipe_length_m: float | None = None,
    temperature_K: float | None = None,
    carrier_gas: str = "N2",
) -> CapacityShadowResult | CapacityShadowRefusal:
    from simulator.evaporation import EvaporationFluxRefusal

    equilibrium = {
        str(species): max(0.0, float(pressure))
        for species, pressure in dict(P_eq_Pa).items()
        if math.isfinite(float(pressure))
    }
    notices: list[dict[str, object]] = []
    extra: dict[str, float] = {}
    unnamed_carrier_Pa = max(0.0, float(P_carrier_Pa))
    for species, pressure in dict(carrier_partials_Pa or {}).items():
        fill = max(0.0, float(pressure))
        if fill > 0.0:
            extra[str(species)] = fill
    for species, pressure in dict(incoming_partials_Pa or {}).items():
        incoming = max(0.0, float(pressure))
        P_eq = equilibrium.get(str(species), 0.0)
        if incoming > P_eq and incoming > 0.0:
            extra[str(species)] = max(extra.get(str(species), 0.0), incoming)
            notices.append(
                {
                    "reason": "retained_gas_exceeds_equilibrium",
                    "species": str(species),
                    "incoming_partial_Pa": incoming,
                    "equilibrium_Pa": P_eq,
                    "status": "model_limit",
                    "detail": (
                        "retained real gas exceeds newly lowered P_eq; "
                        "inventory is not clamped or deleted"
                    ),
                }
            )
    zero_partials = {species: 0.0 for species in equilibrium}
    try:
        free_flux = {
            str(species): max(0.0, float(rate))
            for species, rate in flux_kg_hr_at_partials(zero_partials).items()
        }
    except EvaporationFluxRefusal:
        raise
    except Exception as exc:
        return CapacityShadowRefusal(
            f"evaporation_flux_refused:{type(exc).__name__}:{exc}",
            0,
        )
    conductance = dict(H_mol_s_Pa or {})
    if not conductance:
        conductance = _h_from_free_flux(
            P_eq_Pa=equilibrium,
            flux_kg_hr=free_flux,
            molar_mass_kg_mol=molar_mass_kg_mol,
        )
    G = max(0.0, float(G_mol_s_Pa2))
    P_down = max(0.0, float(P_downstream_Pa))
    molecular_C: dict[str, float] = {}
    knudsen_at_total = None
    diameter_m = 0.0 if pipe_diameter_m is None else float(pipe_diameter_m)
    length_m = 0.0 if pipe_length_m is None else float(pipe_length_m)
    T_K = 0.0 if temperature_K is None else float(temperature_K)
    if diameter_m > 0.0 and length_m > 0.0 and T_K > 0.0:
        ld_notice = None
        for species, mass in dict(molar_mass_kg_mol).items():
            C_i, notice = _long_tube_molar_conductance_mol_s_Pa(
                diameter_m=diameter_m,
                length_m=length_m,
                temperature_K=T_K,
                molar_mass_kg_mol=float(mass),
            )
            if C_i > 0.0:
                molecular_C[str(species)] = C_i
            if notice is not None and ld_notice is None:
                ld_notice = notice
        if ld_notice is not None:
            notices.append(ld_notice)
        carrier = str(carrier_gas or "N2")

        def knudsen_at_total(total_Pa: float, _carrier=carrier) -> float:
            return _knudsen_number_at_total_Pa(
                total_Pa,
                temperature_K=T_K,
                diameter_m=diameter_m,
                carrier_gas=_carrier,
            )

    mixture_kwargs = dict(
        extra_partials_Pa=extra,
        P_carrier_Pa=unnamed_carrier_Pa,
        molecular_C_mol_s_Pa=molecular_C or None,
        knudsen_at_total_Pa=knudsen_at_total,
    )
    try:
        partials = mixture_surface_partials_Pa(
            P_eq_Pa=equilibrium,
            H_mol_s_Pa=conductance,
            G_mol_s_Pa2=G,
            P_downstream_Pa=P_down,
            **mixture_kwargs,
        )
    except CapacityCouplingRefusalError as exc:
        notices.append(
            {
                "reason": exc.refusal.reason,
                "status": "model_limit",
                "iterations": exc.refusal.iterations,
                "detail": (
                    "source/duct root did not bracket; last viscous/molecular "
                    "extension is not installed, run continues with free-surface "
                    "partials of 0"
                ),
            }
        )
        partials = {species: 0.0 for species in equilibrium}
    try:
        flux = {
            str(species): max(0.0, float(rate))
            for species, rate in flux_kg_hr_at_partials(partials).items()
        }
    except EvaporationFluxRefusal:
        raise
    except CapacityCouplingRefusalError as exc:
        return exc.refusal
    except Exception as exc:
        return CapacityShadowRefusal(
            f"evaporation_flux_refused:{type(exc).__name__}:{exc}",
            1,
        )
    iterations = 1

    def residual_rel_now() -> float:
        return _source_duct_residual_rel(
            partials_Pa=partials,
            flux_kg_hr=flux,
            molar_mass_kg_mol=molar_mass_kg_mol,
            G_mol_s_Pa2=G,
            P_downstream_Pa=P_down,
            P_carrier_Pa=unnamed_carrier_Pa,
            molecular_C_mol_s_Pa=molecular_C or None,
            knudsen_at_total_Pa=knudsen_at_total,
        )

    residual_rel = residual_rel_now()
    for _refine in range(SOURCE_DUCT_H_REFINE_ITERATIONS):
        if residual_rel <= DEFAULT_REL_TOL:
            break
        updated = dict(conductance)
        changed = 0.0
        for species, P_eq in equilibrium.items():
            if species in extra:
                continue
            p_surface = max(0.0, float(partials.get(species, 0.0)))
            mass = float(molar_mass_kg_mol.get(species, 0.0))
            if P_eq <= p_surface or mass <= 0.0:
                continue
            q_mol_s = max(0.0, float(flux.get(species, 0.0))) / mass / 3600.0
            new_H = q_mol_s / (P_eq - p_surface)
            changed = max(changed, abs(new_H - float(updated.get(species, 0.0))))
            updated[species] = new_H
        h_scale = max(
            (abs(value) for value in updated.values()),
            default=0.0,
        )
        if changed <= DEFAULT_REL_TOL * h_scale + 1.0e-18:
            notices.append(
                {
                    "reason": "source_duct_h_refine_non_convergence",
                    "status": "model_limit",
                    "iterations": iterations,
                    "residual_rel": residual_rel,
                    "detail": (
                        "H-refine froze with residual above DEFAULT_REL_TOL; "
                        "last iterate kept"
                    ),
                }
            )
            break
        conductance = updated
        try:
            partials = mixture_surface_partials_Pa(
                P_eq_Pa=equilibrium,
                H_mol_s_Pa=conductance,
                G_mol_s_Pa2=G,
                P_downstream_Pa=P_down,
                **mixture_kwargs,
            )
        except CapacityCouplingRefusalError as exc:
            notices.append(
                {
                    "reason": exc.refusal.reason,
                    "status": "model_limit",
                    "iterations": iterations,
                    "detail": "source/duct root missed on H-refine; last iterate kept",
                }
            )
            break
        try:
            flux = {
                str(species): max(0.0, float(rate))
                for species, rate in flux_kg_hr_at_partials(partials).items()
            }
        except EvaporationFluxRefusal:
            raise
        except CapacityCouplingRefusalError as exc:
            notices.append(
                {
                    "reason": exc.refusal.reason,
                    "status": "model_limit",
                    "iterations": iterations,
                    "detail": "source/duct root missed on H-refine; last iterate kept",
                }
            )
            break
        except Exception as exc:
            return CapacityShadowRefusal(
                f"evaporation_flux_refused:{type(exc).__name__}:{exc}",
                iterations,
            )
        iterations += 1
        residual_rel = residual_rel_now()
    else:
        residual_rel = residual_rel_now()
        if residual_rel > DEFAULT_REL_TOL:
            notices.append(
                {
                    "reason": "source_duct_h_refine_non_convergence",
                    "status": "model_limit",
                    "iterations": iterations,
                    "residual_rel": residual_rel,
                    "detail": (
                        "H-refine exhausted SOURCE_DUCT_H_REFINE_ITERATIONS; "
                        "last iterate kept"
                    ),
                }
            )
    bled = dict(head_bled_species_mol or {})
    return CapacityShadowResult(
        capacity=capacity,
        partial_pressures_Pa=partials,
        evaporation_flux_kg_hr={
            species: rate
            for species, rate in flux.items()
            if rate > 0.0
        },
        bled_species_mol=bled,
        terminal_offgas_mol={
            species: mol
            for species, mol in bled.items()
            if species != OXYGEN_SPECIES
        },
        oxygen=OxygenShadowPartition(0.0, 0.0, 0.0, 0.0, 0.0),
        saturation=SaturationShadow(0.0, None, 0.0, "pipe"),
        iterations=iterations,
        max_delta_history_Pa=(),
        mass_closure_error_pct=0.0,
        source_duct_residual_rel=residual_rel,
        closure_notices=tuple(notices),
    )


def solve_capacity_shadow(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    capacity: NoColdTrain | FiniteCapacity,
    head_bled_species_mol: Mapping[str, float],
    external_o2_holdup_mol: float,
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    bleed_conductance_kg_s: float,
    downstream_pressure_Pa: float,
    k_relief_kg_hr_Pa: float,
    p_open_Pa: float,
    overhead_source_mol_hr_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
    total_pressure_Pa_at_partials: (
        Callable[[Mapping[str, float]], float] | None
    ) = None,
    vessel_rating_Pa: float | None = None,
    accumulator_enabled: bool = False,
    cistern_fill_kg: float = 0.0,
    cavern_capacity_kg: float = 0.0,
    controlled_flow: bool = False,
    rel_tol: float = DEFAULT_REL_TOL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    equilibrium_pressures_Pa: Mapping[str, float] | None = None,
    hertz_knudsen_conductance_mol_s_Pa: Mapping[str, float] | None = None,
    pipe_molar_G_mol_s_Pa2: float | None = None,
    incoming_partials_Pa: Mapping[str, float] | None = None,
    carrier_partials_Pa: Mapping[str, float] | None = None,
    P_carrier_Pa: float = 0.0,
    pipe_diameter_m: float | None = None,
    pipe_length_m: float | None = None,
    carrier_gas: str = "N2",
) -> CapacityShadowResult | CapacityShadowRefusal:
    controls = {
        "cold_train_capacity": capacity,
        "dt_hr": dt_hr,
        "bleed_conductance_kg_s": bleed_conductance_kg_s,
        "p_total_bar": 0.0,
        "p_downstream_bar": downstream_pressure_Pa / 100000.0,
    }
    if not isinstance(accumulator_enabled, bool):
        return CapacityShadowRefusal(
            "accumulator_enabled must be a boolean",
            0,
        )
    if isinstance(capacity, FiniteCapacity):
        controls.update({
            "k_relief_kg_hr_Pa": k_relief_kg_hr_Pa,
            "p_open_Pa": p_open_Pa,
            "vessel_rating_Pa": vessel_rating_Pa,
        })
        if accumulator_enabled:
            controls.update({
                "accumulator_enabled": True,
                "cistern_fill_kg": cistern_fill_kg,
                "cavern_capacity_kg": cavern_capacity_kg,
            })
    invalid = BuiltinOverheadBleedProvider._invalid_destructive_control(controls)
    if invalid is not None:
        return CapacityShadowRefusal(invalid, 0)
    if not all(
        math.isfinite(value) and value > 0.0
        for value in (temperature_K, volume_m3, dt_hr)
    ):
        return CapacityShadowRefusal(
            "temperature_K, volume_m3, and dt_hr must be finite and positive",
            0,
        )
    if vessel_rating_Pa is not None and (
        not math.isfinite(vessel_rating_Pa) or vessel_rating_Pa <= 0.0
    ):
        return CapacityShadowRefusal(
            "vessel_rating_Pa must be finite and positive",
            0,
        )
    if not isinstance(capacity, FiniteCapacity):
        if (
            isinstance(capacity, NoColdTrain)
            and equilibrium_pressures_Pa is not None
            and pipe_molar_G_mol_s_Pa2 is not None
        ):
            return solve_source_duct_closure(
                P_eq_Pa=equilibrium_pressures_Pa,
                flux_kg_hr_at_partials=flux_kg_hr_at_partials,
                molar_mass_kg_mol=molar_mass_kg_mol,
                G_mol_s_Pa2=float(pipe_molar_G_mol_s_Pa2),
                capacity=capacity,
                P_downstream_Pa=downstream_pressure_Pa,
                H_mol_s_Pa=hertz_knudsen_conductance_mol_s_Pa,
                incoming_partials_Pa=incoming_partials_Pa,
                carrier_partials_Pa=carrier_partials_Pa,
                P_carrier_Pa=P_carrier_Pa,
                head_bled_species_mol=head_bled_species_mol,
                pipe_diameter_m=pipe_diameter_m,
                pipe_length_m=pipe_length_m,
                temperature_K=temperature_K,
                carrier_gas=carrier_gas,
            )
        oxygen = OxygenShadowPartition(0.0, 0.0, 0.0, 0.0, 0.0)
        return CapacityShadowResult(
            capacity=capacity,
            partial_pressures_Pa={},
            evaporation_flux_kg_hr={},
            bled_species_mol=dict(head_bled_species_mol),
            terminal_offgas_mol={
                species: mol
                for species, mol in head_bled_species_mol.items()
                if species != OXYGEN_SPECIES
            },
            oxygen=oxygen,
            saturation=SaturationShadow(0.0, None, 0.0, "pipe"),
            iterations=0,
            max_delta_history_Pa=(),
            mass_closure_error_pct=0.0,
        )

    species_order = tuple(sorted(set(pre_holdup_mol) | set(molar_mass_kg_mol)))
    partials = {
        species: max(0.0, float(pre_holdup_mol.get(species, 0.0)))
        * GAS_CONSTANT
        * temperature_K
        / volume_m3
        for species in species_order
    }
    delta_history: list[float] = []
    final_bled: dict[str, float] = {}
    final_offgas: dict[str, float] = {}
    final_oxygen = OxygenShadowPartition(0.0, 0.0, 0.0, 0.0, 0.0)
    final_flux: dict[str, float] = {}
    final_source_mol_hr: dict[str, float] = {}
    final_evolved: dict[str, float] = {}
    final_residual: dict[str, float] = {}
    final_flow_capacity = None
    retained_holdup_kg = sum(
        max(0.0, float(pre_holdup_mol.get(species, 0.0)))
        * molar_mass_kg_mol[species]
        for species in species_order
    )
    from simulator.evaporation import EvaporationFluxRefusal

    # Premise: HK source rates depend on the pressure produced by their own
    # post-source, post-bleed residual holdup. Algebra defines the Picard map
    # F_s(p) = (RT/V)[n0_s + q_s(p)*dt/M_s - d_s(p)], with d_O2 equal to the
    # external+admitted+relieved debit and other d_s equal to head bleed; the
    # iteration residual is F(p)-p. Unit check: q*dt/M is mol and
    # mol*(J/(mol K))*K/m3 is Pa. Sanity anchor: zero source and zero debit
    # returns the initial ideal-gas pressure, and convergence requires
    # p*=F(p*) within the absolute/relative pressure tolerance.
    for iteration in range(1, max_iterations + 1):
        try:
            final_flux = {
                str(species): max(0.0, float(rate))
                for species, rate in flux_kg_hr_at_partials(partials).items()
            }
        except EvaporationFluxRefusal:
            raise
        except Exception as exc:
            return CapacityShadowRefusal(
                f"evaporation_flux_refused:{type(exc).__name__}:{exc}",
                iteration - 1,
            )
        if overhead_source_mol_hr_at_partials is None:
            source_mol_hr = {
                species: final_flux.get(species, 0.0)
                / molar_mass_kg_mol[species]
                for species in species_order
            }
        else:
            try:
                source_mol_hr = {
                    str(species): float(rate)
                    for species, rate in overhead_source_mol_hr_at_partials(
                        partials
                    ).items()
                }
            except EvaporationFluxRefusal:
                raise
            except Exception as exc:
                return CapacityShadowRefusal(
                    f"overhead_source_refused:{type(exc).__name__}:{exc}",
                    iteration - 1,
                )
        final_source_mol_hr = dict(source_mol_hr)
        final_evolved = {
            species: max(
                0.0,
                max(0.0, float(pre_holdup_mol.get(species, 0.0)))
                + source_mol_hr.get(species, 0.0) * dt_hr,
            )
            for species in species_order
        }
        total_mol = sum(final_evolved.values())
        total_kg = sum(
            final_evolved[species] * molar_mass_kg_mol[species]
            for species in species_order
        )
        total_pressure_Pa = (
            float(total_pressure_Pa_at_partials(partials))
            if total_pressure_Pa_at_partials is not None
            else sum(partials.values())
        )
        bleed_controls = dict(controls)
        bleed_controls["p_total_bar"] = total_pressure_Pa / 100000.0
        bleed_controls["bleed_conductance_kg_s"] = bleed_conductance_kg_s
        if controlled_flow:
            positive_source_kg_hr = sum(
                max(0.0, source_mol_hr.get(species, 0.0))
                * molar_mass_kg_mol[species]
                for species in species_order
            )
            final_flow_capacity = controlled_flow_capacity(
                pipe_capacity_kg_hr=bleed_conductance_kg_s * 3600.0,
                equipment_capacity_kg_hr=capacity.value_kg_hr,
                evolved_flux_kg_hr=positive_source_kg_hr,
                retained_holdup_kg=retained_holdup_kg,
                dt_hr=dt_hr,
                upstream_pressure_bar=total_pressure_Pa / 100000.0,
            )
            bleed_controls["effective_transport_capacity"] = (
                final_flow_capacity
            )
            bleed_controls["p_downstream_bar"] = (
                final_flow_capacity.downstream_pressure_bar
            )
        ordinary_bled = BuiltinOverheadBleedProvider._bled_species_mol(
            final_evolved,
            total_mol=total_mol,
            total_kg=total_kg,
            controls=bleed_controls,
        ) if total_mol > 0.0 and total_kg > 0.0 else {}
        bled_o2 = ordinary_bled.get(OXYGEN_SPECIES, 0.0)
        if OXYGEN_SPECIES in molar_mass_kg_mol:
            final_oxygen = partition_melt_oxygen(
                bled_o2_mol=bled_o2,
                overhead_o2_mol=final_evolved.get(OXYGEN_SPECIES, 0.0),
                external_o2_holdup_mol=external_o2_holdup_mol,
                capacity=capacity,
                dt_hr=dt_hr,
                p_o2_Pa=partials.get(OXYGEN_SPECIES, 0.0),
                k_relief_kg_hr_Pa=k_relief_kg_hr_Pa,
                p_open_Pa=p_open_Pa,
                molar_mass_kg_mol=molar_mass_kg_mol[OXYGEN_SPECIES],
                accumulator_enabled=accumulator_enabled,
                cistern_fill_kg=cistern_fill_kg,
                cavern_capacity_kg=cavern_capacity_kg,
            )
        final_bled = dict(ordinary_bled)
        if final_oxygen.debited_mol > 0.0:
            final_bled[OXYGEN_SPECIES] = final_oxygen.debited_mol
        else:
            final_bled.pop(OXYGEN_SPECIES, None)
        final_offgas = {
            species: mol
            for species, mol in ordinary_bled.items()
            if species != OXYGEN_SPECIES
        }
        final_residual = {
            species: max(
                0.0,
                final_evolved[species]
                - (
                    final_oxygen.debited_mol
                    if species == OXYGEN_SPECIES
                    else ordinary_bled.get(species, 0.0)
                ),
            )
            for species in species_order
        }
        next_partials = {
            species: final_residual[species]
            * GAS_CONSTANT
            * temperature_K
            / volume_m3
            for species in species_order
        }
        max_delta = max(
            (abs(next_partials[s] - partials[s]) for s in species_order),
            default=0.0,
        )
        scale = max(
            (
                max(abs(next_partials[s]), abs(partials[s]))
                for s in species_order
            ),
            default=0.0,
        )
        delta_history.append(max_delta)
        if max_delta <= max(DEFAULT_ABS_TOL_PA, rel_tol * scale):
            break
        partials = next_partials
    else:
        return CapacityShadowRefusal(
            "picard_non_convergence",
            max_iterations,
        )

    total_pressure_Pa = (
        float(total_pressure_Pa_at_partials(partials))
        if total_pressure_Pa_at_partials is not None
        else sum(partials.values())
    )
    if (
        vessel_rating_Pa is not None
        and total_pressure_Pa > vessel_rating_Pa
    ):
        return CapacityShadowRefusal(
            "vessel_total_pressure_exceeds_rating:"
            f"{total_pressure_Pa:.17g}>{vessel_rating_Pa:.17g}",
            iteration,
        )

    initial_plus_source_kg = sum(
        final_evolved[s] * molar_mass_kg_mol[s] for s in species_order
    )
    accounted_kg = sum(
        final_residual[s] * molar_mass_kg_mol[s] for s in species_order
    ) + sum(
        final_offgas[s] * molar_mass_kg_mol[s] for s in final_offgas
    ) + final_oxygen.debited_mol * molar_mass_kg_mol.get(OXYGEN_SPECIES, 0.0)
    closure_pct = (
        abs(initial_plus_source_kg - accounted_kg) / initial_plus_source_kg * 100.0
        if initial_plus_source_kg > 0.0
        else 0.0
    )
    positive_source_kg_hr = {
        species: max(0.0, source_mol_hr)
        * molar_mass_kg_mol[species]
        for species, source_mol_hr in final_source_mol_hr.items()
    }
    if controlled_flow and final_flow_capacity is not None:
        saturation = SaturationShadow(
            final_flow_capacity.saturation,
            None,
            final_flow_capacity.saturation,
            final_flow_capacity.binding_cause,
        )
    else:
        pipe_capacity_kg_hr = (
            bleed_conductance_kg_s
            * compressible_pressure_capacity_fraction(
                total_pressure_Pa / 100000.0,
                downstream_pressure_Pa / 100000.0,
            )
            * 3600.0
        )
        saturation = combined_saturation(
            total_evaporation_kg_hr=sum(positive_source_kg_hr.values()),
            oxygen_evaporation_kg_hr=positive_source_kg_hr.get(
                OXYGEN_SPECIES, 0.0
            ),
            pipe_capacity_kg_hr=pipe_capacity_kg_hr,
            capacity=capacity,
        )
    return CapacityShadowResult(
        capacity=capacity,
        partial_pressures_Pa=partials,
        evaporation_flux_kg_hr=final_flux,
        bled_species_mol=final_bled,
        terminal_offgas_mol=final_offgas,
        oxygen=final_oxygen,
        saturation=saturation,
        iterations=iteration,
        max_delta_history_Pa=tuple(delta_history),
        mass_closure_error_pct=closure_pct,
    )
