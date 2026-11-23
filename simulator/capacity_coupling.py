"""Pure shadow solve for cold-train/overhead capacity coupling."""

from __future__ import annotations

import contextvars
import math
import sys
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any

from engines.builtin.overhead_bleed import (
    BuiltinOverheadBleedProvider,
    compressible_pressure_capacity_fraction,
    controlled_flow_capacity,
)
from simulator.overhead import pipe_species_outflow_mol_s
from simulator.physical_constants import GAS_CONSTANT, PA_PER_BAR
from simulator.thermal_train import FiniteCapacity, NoColdTrain
from simulator.transport_regime import TransportRegimeRefusal


OXYGEN_SPECIES = "O2"
DEFAULT_REL_TOL = 1.0e-9
DEFAULT_ABS_TOL_PA = 1.0e-6
DEFAULT_MAX_ITERATIONS = 50

# A diagnostic flux sample. The opening-scale probe and the frozen
# species probe both evaluate q, and neither is a solver step. The
# callback arms an undo on this sample; the sample runs it on exit.
# Default None means a real evaluation, which may latch.
_DIAGNOSTIC_FLUX: contextvars.ContextVar = contextvars.ContextVar(
    "headspace_diagnostic_flux",
    default=None,
)


class _DiagnosticFluxSample:
    """One non-solver flux sample. The callback arms its own undo."""

    def __init__(self) -> None:
        self._token = None
        self._undo: list = []
        self.armed = False

    def arm(self, undo) -> None:
        # The first evaluation snapshots. A later redox read in the
        # same sample must not replace that snapshot with the mutated
        # state, or the undo would keep the sample's writes.
        if self.armed:
            return
        self.armed = True
        self._undo.append(undo)

    def __enter__(self) -> "_DiagnosticFluxSample":
        self._token = _DIAGNOSTIC_FLUX.set(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            for undo in reversed(self._undo):
                undo()
        finally:
            if self._token is not None:
                _DIAGNOSTIC_FLUX.reset(self._token)
        return False


def diagnostic_flux_sample_active() -> _DiagnosticFluxSample | None:
    sample = _DIAGNOSTIC_FLUX.get()
    if isinstance(sample, _DiagnosticFluxSample):
        return sample
    return None


class _DiagnosticLawSample:
    """Restore the law's solver marks around one diagnostic sample.

    The callback's undo is the flux sample's. This one owns the law
    fields the sample writes: the flux counter, the gross snapshot,
    the accepted partials, the pipe piece, the notices, and the redox
    stash. A refusal leaves those fields as they were.
    """

    def __init__(self, law) -> None:
        self._law = law
        self._sample = _DiagnosticFluxSample()
        self._saved = None

    def __enter__(self) -> _DiagnosticFluxSample:
        law = self._law
        self._saved = (
            law.flux_evaluations,
            dict(law._last_gross_kg_s),
            law.accepted_flux_partials,
            law.accepted_flux_key,
            law._last_pipe_piece,
            dict(law.species_notices),
            dict(law._last_redox_mol_s),
            law.paired_redox_mol_s,
            law.accepted_redox_key,
            law._last_evaporation_drive_delta_Pa,
            law.evaporation_drive_at_refusal,
        )
        return self._sample.__enter__()

    def __exit__(self, exc_type, exc, tb) -> bool:
        law = self._law
        saved = self._saved
        try:
            if saved is not None:
                (
                    law.flux_evaluations,
                    law._last_gross_kg_s,
                    law.accepted_flux_partials,
                    law.accepted_flux_key,
                    law._last_pipe_piece,
                    law.species_notices,
                    law._last_redox_mol_s,
                    law.paired_redox_mol_s,
                    law.accepted_redox_key,
                    law._last_evaporation_drive_delta_Pa,
                    law.evaporation_drive_at_refusal,
                ) = saved
        finally:
            self._sample.__exit__(exc_type, exc, tb)
        return False


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
    species_notices: tuple[Mapping[str, Any], ...] = ()
    # Kilograms of gross vapour the step integrated, and moles of S
    # the step integrated. Empty on a shadow that did not track them.
    # The hour posts these. It does not sample the endpoint.
    vapour_extent_kg: Mapping[str, float] = field(default_factory=dict)
    redox_added_mol: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "partial_pressures_Pa",
            "evaporation_flux_kg_hr",
            "bled_species_mol",
            "terminal_offgas_mol",
            "vapour_extent_kg",
            "redox_added_mol",
        ):
            object.__setattr__(
                self,
                name,
                MappingProxyType(dict(getattr(self, name))),
            )
        object.__setattr__(
            self,
            "species_notices",
            tuple(
                MappingProxyType(dict(notice))
                for notice in self.species_notices
            ),
        )


@dataclass(frozen=True)
class CapacityShadowRefusal:
    reason: str
    iterations: int
    authoritative: bool = False
    # Set on headspace_balance_work_cap. cap is "flux" or "integrator".
    # Both counts are the evaluations that crossed the cap. Other
    # refusals leave them unset.
    cap: str | None = None
    flux_evaluations: int | None = None
    integrator_evaluations: int | None = None
    # Solve path of the hour that refused: fast_root, explicit_euler,
    # explicit_ladder, midpoint, radau_fallback, backward_euler, or
    # frozen_composition.
    # None when the refusal is raised before a path is chosen.
    # The inventory refusal, headspace_negative_bleed, and
    # headspace_balance_nonconvergence carry the per-species ledger
    # balance and the O2 writes. A work-cap refusal carries the last
    # accepted integrator state in species_balance (mol, partial_Pa),
    # S(P) in redox_mol_s, and the hour-open buffer stock. The release
    # refusal carries that post-vapour stock and leaves S unset.
    # fo2_log is the melt potential the hour opened on. pipe_piece is
    # the last accepted pipe piece (binding is the regime). capture
    # fraction is the fraction that evaluation stored. Plain dicts
    # and lists, so vars() serialises.
    path: str | None = None
    species_balance: dict[str, dict[str, Any]] | None = None
    overhead_o2_writes: list[dict[str, Any]] | None = None
    redox_mol_s: dict[str, float] | None = None
    buffer_o2_mol: float | None = None
    buffer_o2_mol_signed: float | None = None
    fo2_log: float | None = None
    pipe_piece: dict[str, Any] | None = None
    capture_fraction: dict[str, float] | None = None
    # Collected on the slow path. The work cap attaches them, and so
    # does headspace_balance_nonconvergence. None when that refusal
    # fired before Radau ran. accepted_step_sizes_s is the last
    # _STIFFNESS_STEPS accepted steps, in seconds. rejected_steps
    # counts error-test rejections, not Newton failures, and it is
    # not the length of the dominator list. A nonconvergence tape
    # leaves the eigenvalue fields unset: filling them evaluates
    # another Jacobian, which is another flux call.
    # A dominator names the component with the largest |error|/scale
    # on one of the last rejections: species, block (n, A, B, and G
    # or R when the hour tracks them), the proposed value, and the
    # atol/rtol scale that test used. The eigenvalue fields are |λ|
    # of one production Jacobian at the last accepted state. No
    # accepted step, or a Jacobian that itself hits the cap, leaves
    # them unset.
    accepted_step_sizes_s: list[float] | None = None
    rejected_steps: int | None = None
    rejected_error_dominators: list[dict[str, Any]] | None = None
    jacobian_abs_eigenvalue_min: float | None = None
    jacobian_abs_eigenvalue_max: float | None = None
    # Hour-open scales and the integrator's flux. tau_s is n/Q at the
    # opening holdup: n is the positive inventory (mol) and Q is the
    # outflow the pipe or the cap publishes there (mol/s). dt_over_tau
    # is dt/tau. Both stay unset when Q is not positive. A shut pipe
    # has no flush time. composition_tau_s is n/Σ(q+S) at that same
    # holdup. It is not a solved P*: the source is the opening
    # partials. flux_per_rhs_* counts flux evaluations from one
    # right-hand-side entry to the next, including the Jacobian and a
    # stall callback in that gap. An even count's median is the mean
    # of the two middle samples. pstar_* counts P* solves: the warm
    # Newton that published inside the kink band, the geometric Brent
    # after a warm miss, and the sign-scan fallback. A shut return is
    # none of the three. A fast-root scan that is not a P* solve is
    # not counted. Unset means the hour never measured them.
    tau_s: float | None = None
    dt_over_tau: float | None = None
    composition_tau_s: float | None = None
    dt_over_composition_tau: float | None = None
    flux_per_rhs_min: int | None = None
    flux_per_rhs_median: float | None = None
    flux_per_rhs_max: int | None = None
    pstar_warm_newton: int | None = None
    pstar_brent_band: int | None = None
    pstar_sign_scan: int | None = None
    reduced_rhs_calls: int | None = None
    reduced_jacobian_builds: int | None = None
    reduced_ift_residual_check_rejections: int | None = None
    reduced_second_corrections: int | None = None
    reduced_resolver_warm_root_fallbacks: int | None = None
    reduced_resolver_bracket_fallbacks: int | None = None
    reduced_shut_branch_hits: int | None = None
    reduced_evaporation_drive_probe_evaluations: int | None = None
    reduced_evaporation_drive_near_zero_evaluations: int | None = None
    reduced_evaporation_drive_near_zero_species_counts: dict[str, int] | None = None
    reduced_evaporation_drive_jacobian_crossing_counts: dict[str, int] | None = None
    reduced_evaporation_drive_stage_crossing_counts: dict[str, int] | None = None
    reduced_rejections_with_evaporation_drive_crossing: int | None = None
    reduced_rejection_evaporation_drive_crossing_fraction: float | None = None
    reduced_rejection_evaporation_drive_crossing_species_counts: (
        dict[str, int] | None
    ) = None
    reduced_evaporation_drive_band_Pa: float | None = None
    reduced_error_test_rejections: int | None = None
    reduced_newton_nonconvergence_failures: int | None = None
    reduced_error_test_top3_component_counts: dict[str, int] | None = None
    reduced_newton_failure_diagnostics: list[dict[str, Any]] | None = None
    reduced_accepted_steps: int | None = None
    # Error-test rejections plus failed Newton collocation attempts.
    reduced_rejected_steps: int | None = None
    reduced_step_size_min_s: float | None = None
    reduced_step_size_median_s: float | None = None
    reduced_step_size_max_s: float | None = None
    reduced_time_covered_s: float | None = None
    full_system_radau_time_covered_s: float | None = None
    handoff_time_s: float | None = None
    # Post-settle buffer-release record. Set on the inventory refusal
    # only. The hour notice carries the same dict. The comparison does
    # not read it. excess_integrated is inside the dict, by that name.
    buffer_release: dict[str, Any] | None = None
    # Last flux evaluation of a headspace_balance_nonconvergence.
    # One row per vapour the publisher priced. drive_sign is the sign
    # of (p_eq - p_bulk) at that evaluation: 1, 0, -1, or None when
    # the publisher did not form a drive. recondensing is true when
    # the signed kilogram rate the headspace law integrated is
    # negative. A recondensing row also carries parent_oxide,
    # parent_inventory_kg (cleaned-melt kilograms of that oxide), and
    # p_eq_Pa. Other refusals leave the list unset. The ledger
    # species_balance is not this list.
    evaporation_drive_at_refusal: list[dict[str, Any]] | None = None


class CapacityCouplingRefusalError(RuntimeError):
    def __init__(self, refusal: CapacityShadowRefusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.reason)


def headspace_partial_key(
    partials: Mapping[str, float] | None,
) -> tuple[tuple[str, float], ...]:
    """Sorted (species, pressure) identity of one partial vector."""

    return tuple(sorted(
        (str(species), float(pressure))
        for species, pressure in dict(partials or {}).items()
    ))


def headspace_integral_added_kg(
    partials_Pa: Mapping[str, float] | None,
    pre_holdup_mol: Mapping[str, float] | None,
    bled_mol: Mapping[str, float] | None,
    molar_mass_kg_mol: Mapping[str, float] | None,
    *,
    temperature_K: float,
    volume_m3: float,
) -> dict[str, float]:
    """Kilograms the hour integral added to the headspace.

    Premise: the published holdup is n_end = n0 + added − bled.
    A fast root's added mass is q(end)·Δt. A shut Radau hour's
    q(end) is not that integral, and the ledger must post the
    integral or the published pressure is not the inventory.
    Algebra: added_kg = (P V / RT − n0 + bled) M.
    Unit check: mol * kg/mol = kg this hour.
    Limit: no volume or temperature has no pressure, so nothing
    is added. Oxygen is not a routed vapor and is left out.
    """

    if (
        temperature_K <= 0.0
        or volume_m3 <= 0.0
        or not math.isfinite(temperature_K)
        or not math.isfinite(volume_m3)
    ):
        return {}
    factor = GAS_CONSTANT * float(temperature_K) / float(volume_m3)
    partials = dict(partials_Pa or {})
    pre = dict(pre_holdup_mol or {})
    bled = dict(bled_mol or {})
    masses = dict(molar_mass_kg_mol or {})
    added: dict[str, float] = {}
    for species in set(partials) | set(pre) | set(bled):
        label = str(species)
        if label == OXYGEN_SPECIES:
            continue
        mass = masses.get(label, masses.get(species))
        if mass is None:
            continue
        molar_mass = float(mass)
        if molar_mass <= 0.0 or not math.isfinite(molar_mass):
            continue
        n_end = float(partials.get(label, partials.get(species, 0.0)) or 0.0) / factor
        n0 = float(pre.get(label, pre.get(species, 0.0)) or 0.0)
        bleed = float(bled.get(label, bled.get(species, 0.0)) or 0.0)
        kilograms = (n_end - n0 + bleed) * molar_mass
        if not math.isfinite(kilograms):
            continue
        added[label] = kilograms
    return added


def headspace_capture_fractions(
    gross: Mapping[str, float],
    partials_Pa: Mapping[str, float] | None,
    *,
    capture_C: Mapping[str, float] | None,
    fractions_frozen: Mapping[str, float] | None,
) -> dict[str, float]:
    """Capture fraction at this total pressure from the C/P scale.

    Premise: a captured vapor's headspace remainder is C/P, with C
    measured once. An uncaptured vapor stays at its gross.
    Algebra: q = min(gross, C/P), f = 1 - q/gross,
    C = gross_ref * (1 - f_ref) * P_ref.
    Unit check: (kg/h) / Pa * Pa = kg/h.
    Limit: P <= 0 is an empty inventory, so f = 0 and the gross
    enters the headspace. q is never above the gross. Oxygen is
    not a captured vapor.
    """

    total = math.fsum(
        max(0.0, float(pressure))
        for pressure in dict(partials_Pa or {}).values()
    )
    scale = dict(capture_C or {})
    frozen = dict(fractions_frozen or {})
    fractions: dict[str, float] = {}
    for species, raw_rate in gross.items():
        label = str(species)
        if label == OXYGEN_SPECIES:
            continue
        rate = max(0.0, float(raw_rate or 0.0))
        if rate <= 0.0 or total <= 0.0:
            fractions[label] = 0.0
            continue
        remainder_Pa = scale.get(label)
        if remainder_Pa is None:
            fractions[label] = min(
                1.0,
                max(0.0, float(frozen.get(label, 0.0) or 0.0)),
            )
            continue
        remaining = min(rate, max(0.0, float(remainder_Pa)) / total)
        fractions[label] = 1.0 - remaining / rate
    return fractions


def headspace_capture_scale(
    gross: Mapping[str, float],
    measured: Mapping[str, float],
    total_Pa: float,
    *,
    fractions_frozen: Mapping[str, float] | None,
    capture_C: Mapping[str, float] | None,
) -> tuple[dict[str, float], dict[str, float]]:
    """Install C = gross * (1 - f) * P from one measured capture.

    Returns the frozen fractions and the scale. Oxygen is skipped.
    A non-positive capture drops that species from the scale.
    """

    frozen = dict(fractions_frozen or {})
    scale = dict(capture_C or {})
    for species, raw_rate in gross.items():
        label = str(species)
        if label == OXYGEN_SPECIES:
            continue
        rate = max(0.0, float(raw_rate or 0.0))
        captured = min(
            1.0,
            max(0.0, float(measured.get(label, 0.0) or 0.0)),
        )
        frozen[label] = captured
        if rate > 0.0 and captured > 0.0 and total_Pa > 0.0:
            scale[label] = rate * (1.0 - captured) * total_Pa
        elif captured <= 0.0:
            scale.pop(label, None)
    return frozen, scale


def headspace_capture_move_Pa(
    previous: Mapping[str, float],
    current: Mapping[str, float],
    gross: Mapping[str, float],
    partials_Pa: Mapping[str, float] | None,
    *,
    molar_mass_kg_mol: Mapping[str, float],
    temperature_K: float,
    volume_m3: float,
    rate_floor_kg_hr: float,
) -> float:
    """Inventory pressure move from a capture change, in pascals.

    Premise: each species' pipe flow tracks its own partial. Captured
    vapor leaves q_i = C_i / P, so a remainder change moves that
    partial by Δp_i = p_i |Δq_i| / q_i. The inventory move is the sum
    of those partials. Summing P |Δq_i| / q_i prices a trace vapor as
    if it held the whole headspace, and |Σ Δq_i| lets one species'
    extra remainder cancel another's.
    Algebra: q_i = gross_i (1 - f_i), Δp_i = p_i |Δq_i| / q_i.
    The sum is math.fsum over species names in sorted order. A set
    walk plus += changes the bit next to 1e-6 Pa when PYTHONHASHSEED
    changes, and that bit decides the re-solve.
    Unit check: Pa * (kg/h) / (kg/h) = Pa.
    Limit: a species with no solved partial, or a carried source at
    the rate floor, adds the sealed accumulation |Δq| R T / (M V).
    A non-positive volume cannot judge the figure and returns +inf,
    which fails any finite agreement test. Oxygen is not a captured
    vapor.
    """

    if volume_m3 <= 0.0:
        return math.inf
    partials = {
        str(species): max(0.0, float(pressure))
        for species, pressure in dict(partials_Pa or {}).items()
    }
    names = sorted(
        {str(name) for name in previous}
        | {str(name) for name in current}
        | {str(name) for name in gross}
    )
    terms: list[float] = []
    for name in names:
        if name == OXYGEN_SPECIES:
            continue
        rate = max(0.0, float(gross.get(name, 0.0) or 0.0))
        before_f = min(
            1.0, max(0.0, float(previous.get(name, 0.0) or 0.0)),
        )
        after_f = min(
            1.0, max(0.0, float(current.get(name, 0.0) or 0.0)),
        )
        # Δq = q_after - q_before = rate * (before_f - after_f).
        delta_q = rate * (before_f - after_f)
        flowing = rate * (1.0 - before_f)
        partial = partials.get(name, 0.0)
        if flowing > rate_floor_kg_hr and partial > 0.0:
            terms.append(partial * abs(delta_q) / flowing)
            continue
        mass = float(molar_mass_kg_mol.get(name, 0.0) or 0.0)
        if mass <= 0.0:
            continue
        terms.append(
            abs(delta_q) / mass * GAS_CONSTANT * temperature_K / volume_m3
        )
    return math.fsum(terms)


def headspace_committed_bleed_mol(
    bled_mol: Mapping[str, float] | None,
    *,
    pa_per_mol: float,
) -> tuple[dict[str, float], dict[str, float]]:
    """Bleed the solver published. A negative above the gate refuses.

    Premise: bled = n0 + added − n_end. The fast root rewrites a
    negative whose pressure is within ``DEFAULT_ABS_TOL_PA`` before
    this map is built, so the published holdup is already n0 + added
    and the bleed is 0. A negative that is still above that gate is
    not a trace. Booking ``max(0, bled)`` would drop the drain and
    keep the high partial, and the ledger would not match it.
    Algebra: refuse when −bled · (R T / V) > 1e-6 Pa. Inside the
    gate, bled = 0 and the partial grows by −bled · (R T / V), which
    is the same rewrite. Unit check: mol · Pa/mol = Pa.
    Limit: a non-negative drain is copied and adds no partial.
    """

    if not math.isfinite(pa_per_mol) or pa_per_mol < 0.0:
        raise CapacityCouplingRefusalError(
            CapacityShadowRefusal("headspace_negative_bleed", 0)
        )
    booked: dict[str, float] = {}
    extra_pa: dict[str, float] = {}
    for species in sorted(dict(bled_mol or {})):
        moles = float(dict(bled_mol or {})[species])
        if not math.isfinite(moles):
            raise CapacityCouplingRefusalError(
                CapacityShadowRefusal("headspace_negative_bleed", 0)
            )
        if moles < 0.0 and -moles * pa_per_mol > DEFAULT_ABS_TOL_PA:
            raise CapacityCouplingRefusalError(
                CapacityShadowRefusal("headspace_negative_bleed", 0)
            )
        if moles < 0.0:
            extra_pa[str(species)] = -moles * pa_per_mol
            booked[str(species)] = 0.0
            continue
        booked[str(species)] = moles
    return booked, extra_pa


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
    pipe_diameter_m: float | None = None,
    pipe_length_m: float | None = None,
    outflow_cap_kg_s: float | None = None,
    redox_source_mol_s: Mapping[str, float] | None = None,
    redox_mol_s_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
    authoritative: bool = False,
    rel_tol: float = DEFAULT_REL_TOL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    extent_holder: dict | None = None,
) -> CapacityShadowResult | CapacityShadowRefusal:
    controls = {
        "cold_train_capacity": capacity,
        "dt_hr": dt_hr,
        "bleed_conductance_kg_s": bleed_conductance_kg_s,
        "p_total_bar": 0.0,
        "p_downstream_bar": downstream_pressure_Pa / PA_PER_BAR,
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
    # Geometry, or an explicit outflow cap (0 is a closed line), is the
    # §2 balance. The Picard loop below stays the no-geometry shadow.
    # The live hour passes geometry and does not commit this loop.
    if (
        pipe_diameter_m is not None
        or pipe_length_m is not None
        or outflow_cap_kg_s is not None
    ):
        return _commit_headspace_balance(
            pre_holdup_mol=pre_holdup_mol,
            molar_mass_kg_mol=molar_mass_kg_mol,
            flux_kg_hr_at_partials=flux_kg_hr_at_partials,
            capacity=capacity,
            temperature_K=temperature_K,
            volume_m3=volume_m3,
            dt_hr=dt_hr,
            downstream_pressure_Pa=downstream_pressure_Pa,
            vessel_rating_Pa=vessel_rating_Pa,
            pipe_diameter_m=pipe_diameter_m,
            pipe_length_m=pipe_length_m,
            outflow_cap_kg_s=outflow_cap_kg_s,
            redox_source_mol_s=redox_source_mol_s,
            redox_mol_s_at_partials=redox_mol_s_at_partials,
            authoritative=authoritative,
            rel_tol=rel_tol,
            max_iterations=max_iterations,
            extent_holder=extent_holder,
        )
    if not isinstance(capacity, FiniteCapacity):
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
            authoritative=authoritative,
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
                str(species): float(rate)
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
        bleed_controls["p_total_bar"] = total_pressure_Pa / PA_PER_BAR
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
                upstream_pressure_bar=total_pressure_Pa / PA_PER_BAR,
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
                total_pressure_Pa / PA_PER_BAR,
                downstream_pressure_Pa / PA_PER_BAR,
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
        authoritative=authoritative,
    )


# --- Headspace balance (§2). One owner, two equations. ---
#
# Fast commits G = q_head + S - Q = 0 when that scalar has one sign
# change and the admission bound holds. Slow integrates
# dn_i/dt = q_i + S_i - Q_i(n) with solve_ivp (Radau). The booked
# bleed on that branch is B_i = ∫ Q_i dt, not q(end)·Δt. Newton
# proposes on the fast residual. Brent publishes the root. There is
# no seam that injects a Newton step: a returned Newton value is
# discarded.
#
# The slow tolerance is per species. atol_i = 1e-7 Pa * V / (R T) is
# one tenth of the 1e-6 Pa partial floor, in moles. A test on the total
# pressure is not the acceptance test: two partials can cancel there.

_ADMISSION_EXP_UNDERFLOW = 745.0
# The log grid is the fallback when a geometric sample reverses.
# A monotone bracket does not walk it.
_SIGN_SCAN_POINTS = 4096
_GEOMETRIC_CAP_PA = 1.0e12
# A step the wrong way by more than this is a kink, not a rounding.
# Relative to the larger sample, and at least this absolute amount
# (mol/s for G, mol for the slow residual). A few ulps of a rate near
# 1 sit under 1e-15. A rise of the two-root well does not.
_MONOTONE_REL = 1.0e-9
_MONOTONE_ABS = 1.0e-15
# Absolute partial tolerance imposed on every species. The solver's
# local test is rtol * |n_i| + atol_i, with atol_i this pressure times
# V / (R T). rtol is 1e-12. On the stranded N2 trace the frozen-initial
# decay rate already sits about 8.5e-7 Pa from the true varying-a drain,
# and rtol 1e-10 leaves another ~2e-7 Pa, which exceeds the 1e-6 Pa
# floor. 1e-12 keeps the solver's own gap under 1e-8 Pa. A multistep
# BDF at 1e-12 is still ~1e-4 Pa high on the choked megapascal piece.
_SLOW_ATOL_PA = 1.0e-7
_SLOW_RTOL = 1.0e-12
# Gross extent is kilograms. 1e-18 kg of SiO is about 1.6e-12 Pa of
# its stoichiometric O2 on this vessel, under the 1e-9 Pa ledger
# gate. n, A, B and the redox integral keep the mole tolerance.
# A scalar atol would retune those.
_SLOW_EXTENT_ATOL_KG = 1.0e-18
# One 1250 C hour dispatches the flux about 1253 times, and the
# sign-scan fallback walks 4096 points. Hour 21 of the canonical
# lunar walk stayed inside one Radau call for 34 minutes. The caps
# sit above a healthy hour plus one fallback scan, and they stop
# that stall. A closed pipe whose shut grids agree does not spend
# them. A shut grid that stays apart uses the slow integral, and
# these caps are the work cap on that path.
_MAX_FLUX_EVALUATIONS = 250_000
_MAX_INTEGRATOR_EVALUATIONS = 100_000
# Trailing window on a work-cap refusal. The rejected-step count is
# the full count. These bounds are the payload, not a step-size knob.
_STIFFNESS_STEPS = 200
_STIFFNESS_REJECTIONS = 16
# The pipe kink. Q is 0 at and below P_down, and dQ/dP jumps from 0
# to a finite slope on the open side. This width is how far a shut
# stall may be scaled onto P_down, and how close a second open root
# must lie to count as the one the pressure has reached. It is not
# the stall detector. A larger source puts the open root itself past
# this cut: the 1199 C hour stalls about 1.0e-4 Pa above P_down.
_KINK_BAND_PA = 1.0e-4
_KINK_STEP_S = 1.0e-3
# The first Newton miss often refreshes the Jacobian. The second is
# the step being halved onto the kink.
_KINK_NEWTON_FAILS = 2
# tau / t_rem. The held-interval pressure error is about
# |P_stall − P*| times this. At the band edge that product is the
# 1e-6 Pa floor, so a larger fraction is not this shortcut.
_QUASI_STEADY_MAX_TAU_FRACTION = 1.0e-2
# dt/tau on the manifold. Freezing s_i(y) over the hour leaves a
# mole error of order n*(dt/tau)^2 when ds/dy is order s. At 1e-3
# that relative error is 1e-6. The same cut bounds the fast pressure
# transient: t_fast/dt <= 1e-3, so the sliver of source during the
# projection is at most another 1e-6 of the inventory once dt/tau
# is also inside the gate. A larger gate is not this shortcut.
_FROZEN_COMPOSITION_MAX_DT_OVER_TAU = 1.0e-3


class _BalanceRefused(Exception):
    def __init__(
        self,
        reason: str,
        *,
        cap: str | None = None,
        flux_evaluations: int | None = None,
        integrator_evaluations: int | None = None,
    ) -> None:
        self.reason = reason
        self.cap = cap
        self.flux_evaluations = flux_evaluations
        self.integrator_evaluations = integrator_evaluations
        super().__init__(reason)


class _QuasiSteadyStall(Exception):
    """The open integral stopped. The step is already booked."""

    def __init__(self, step: "_HeadspaceStep") -> None:
        self.step = step
        super().__init__("quasi_steady_fixed_point")


def _shadow_from_balance(
    refused: _BalanceRefused,
    iterations: int = 0,
    *,
    path: str | None = None,
    species_balance: dict[str, dict[str, Any]] | None = None,
    redox_mol_s: dict[str, float] | None = None,
    buffer_o2_mol: float | None = None,
    buffer_o2_mol_signed: float | None = None,
    fo2_log: float | None = None,
    pipe_piece: dict[str, Any] | None = None,
    capture_fraction: dict[str, float] | None = None,
    accepted_step_sizes_s: list[float] | None = None,
    rejected_steps: int | None = None,
    rejected_error_dominators: list[dict[str, Any]] | None = None,
    jacobian_abs_eigenvalue_min: float | None = None,
    jacobian_abs_eigenvalue_max: float | None = None,
) -> CapacityShadowRefusal:
    """Copy a balance refusal onto the shadow payload.

    The work cap names which counter fired and both counts. The path
    is the branch the law had entered. Other reasons leave the cap
    unset. The reason string is unchanged. A work-cap caller passes
    the last accepted state and, when Radau ran, its stiffness tape.
    Other callers leave both unset.
    """

    return CapacityShadowRefusal(
        refused.reason,
        iterations,
        cap=refused.cap,
        flux_evaluations=refused.flux_evaluations,
        integrator_evaluations=refused.integrator_evaluations,
        path=path,
        species_balance=species_balance,
        redox_mol_s=redox_mol_s,
        buffer_o2_mol=buffer_o2_mol,
        buffer_o2_mol_signed=buffer_o2_mol_signed,
        fo2_log=fo2_log,
        pipe_piece=pipe_piece,
        capture_fraction=capture_fraction,
        accepted_step_sizes_s=accepted_step_sizes_s,
        rejected_steps=rejected_steps,
        rejected_error_dominators=rejected_error_dominators,
        jacobian_abs_eigenvalue_min=jacobian_abs_eigenvalue_min,
        jacobian_abs_eigenvalue_max=jacobian_abs_eigenvalue_max,
    )


def _pipe_piece_payload(piece: tuple | None) -> dict[str, Any] | None:
    """The pipe piece as a JSON object. binding is the regime.

    The tuple is (binding, blend bucket, cap, empty holdup). A missing
    piece is unset, not a closed pipe.
    """

    if piece is None:
        return None
    binding, bucket, capped, empty = piece
    return {
        "binding": str(binding),
        "blend_bucket": int(bucket),
        "capped": bool(capped),
        "empty_holdup": bool(empty),
    }


def _work_cap_payload(law: _HeadspaceLaw) -> dict[str, Any]:
    """Last accepted state, S(P), buffer stock, fO2, piece, capture.

    The cap check runs at the start of the next evaluation, so the
    stash is the previous completed step. A Jacobian probe does not
    replace that step. A flux cap that fires before any completed
    step uses the last completed flux partials and leaves S unset
    when that partial was not the one S was evaluated at. fO2 is the
    hour-open melt potential. The piece and the capture fraction are
    the ones that completed step stored. An unset one stays unset.
    The stiffness tape is the slow path's. A cap that fired before
    Radau ran leaves it unset, not an empty count.
    """

    balance: dict[str, dict[str, Any]] | None = None
    redox: dict[str, float] | None = None
    if law.accepted_moles is not None:
        partials = law.accepted_partials or {}
        balance = {
            name: {
                "mol": float(law.accepted_moles.get(name, 0.0)),
                "partial_Pa": float(partials.get(name, 0.0)),
            }
            for name in law.species
        }
        redox = {
            name: float(rate)
            for name, rate in dict(law.accepted_step_redox or {}).items()
        }
    elif law.accepted_flux_partials is not None:
        factor = GAS_CONSTANT * law.temperature_K / law.volume_m3
        partials = law.accepted_flux_partials
        balance = {
            name: {
                "mol": (
                    float(partials.get(name, 0.0)) / factor
                    if factor > 0.0
                    else 0.0
                ),
                "partial_Pa": float(partials.get(name, 0.0)),
            }
            for name in law.species
        }
        if (
            law.accepted_redox_key is not None
            and law.accepted_redox_key == law.accepted_flux_key
        ):
            redox = {
                name: float(rate)
                for name, rate in dict(law.paired_redox_mol_s or {}).items()
            }
    capture = law.accepted_capture_fractions
    diagnostic = law.stiffness_diagnostic
    if diagnostic is None:
        step_sizes = None
        rejected = None
        dominators = None
        eigenvalue_min = None
        eigenvalue_max = None
    else:
        step_sizes = list(diagnostic["accepted_step_sizes_s"])
        rejected = diagnostic["rejected_steps"]
        raw_dominators = diagnostic["rejected_error_dominators"]
        dominators = (
            None
            if raw_dominators is None
            else [dict(row) for row in raw_dominators]
        )
        eigenvalue_min = diagnostic["jacobian_abs_eigenvalue_min"]
        eigenvalue_max = diagnostic["jacobian_abs_eigenvalue_max"]
    return {
        "species_balance": balance,
        "redox_mol_s": redox,
        "buffer_o2_mol": law.buffer_o2_mol,
        "buffer_o2_mol_signed": law.buffer_o2_mol_signed,
        "fo2_log": law.fo2_log,
        "pipe_piece": _pipe_piece_payload(law.accepted_pipe_piece),
        "capture_fraction": (
            None if capture is None else dict(capture)
        ),
        "accepted_step_sizes_s": step_sizes,
        "rejected_steps": rejected,
        "rejected_error_dominators": dominators,
        "jacobian_abs_eigenvalue_min": eigenvalue_min,
        "jacobian_abs_eigenvalue_max": eigenvalue_max,
    }


def _rhs_flux_span(
    counts: list[int],
) -> tuple[int | None, float | None, int | None]:
    """Min, median, max of one rhs-interval list.

    An odd count takes the middle sample. An even count takes the
    mean of the two middle samples. That mean is still a count of
    flux evaluations, so a half is a real value, not a rounded int.
    An empty list was not an integrator hour.
    """

    if not counts:
        return None, None, None
    ordered = sorted(int(item) for item in counts)
    size = len(ordered)
    mid = size // 2
    if size % 2 == 1:
        median = float(ordered[mid])
    else:
        median = 0.5 * (ordered[mid - 1] + ordered[mid])
    return ordered[0], median, ordered[-1]


def _hour_cost_payload(law: "_HeadspaceLaw") -> dict[str, Any]:
    """The opening scales and the integrator's flux, as plain values.

    The caller closes the open rhs interval first. A fast root has
    no interval, and the three flux fields stay unset. The P* counts
    are zero when no reduced step ran. They are not unset: the hour
    did measure them.
    """

    low, median, high = _rhs_flux_span(law._rhs_flux_counts)
    reduced_steps = sorted(law.reduced_step_sizes_s)
    if reduced_steps:
        middle = len(reduced_steps) // 2
        if len(reduced_steps) % 2:
            reduced_step_median = reduced_steps[middle]
        else:
            reduced_step_median = 0.5 * (
                reduced_steps[middle - 1] + reduced_steps[middle]
            )
        reduced_step_min = reduced_steps[0]
        reduced_step_max = reduced_steps[-1]
    else:
        reduced_step_min = None
        reduced_step_median = None
        reduced_step_max = None
    return {
        "tau_s": law.opening_tau_s,
        "dt_over_tau": law.opening_dt_over_tau,
        "composition_tau_s": law.opening_composition_tau_s,
        "dt_over_composition_tau": law.opening_dt_over_composition_tau,
        "flux_per_rhs_min": low,
        "flux_per_rhs_median": median,
        "flux_per_rhs_max": high,
        "pstar_warm_newton": int(law.pstar_warm_newton),
        "pstar_brent_band": int(law.pstar_brent_band),
        "pstar_sign_scan": int(law.pstar_sign_scan),
        "reduced_rhs_calls": int(law.reduced_rhs_calls),
        "reduced_jacobian_builds": int(law.reduced_jacobian_builds),
        "reduced_ift_residual_check_rejections": int(
            law.reduced_ift_residual_check_rejections
        ),
        "reduced_second_corrections": int(law.reduced_second_corrections),
        "reduced_resolver_warm_root_fallbacks": int(
            law.reduced_resolver_warm_root_fallbacks
        ),
        "reduced_resolver_bracket_fallbacks": int(
            law.reduced_resolver_bracket_fallbacks
        ),
        "reduced_shut_branch_hits": int(law.reduced_shut_branch_hits),
        "reduced_evaporation_drive_probe_evaluations": int(
            law.reduced_evaporation_drive_probe_evaluations
        ),
        "reduced_evaporation_drive_near_zero_evaluations": int(
            law.reduced_evaporation_drive_near_zero_evaluations
        ),
        "reduced_evaporation_drive_near_zero_species_counts": dict(
            sorted(law.reduced_evaporation_drive_near_zero_species_counts.items())
        ),
        "reduced_evaporation_drive_jacobian_crossing_counts": dict(
            sorted(law.reduced_evaporation_drive_jacobian_crossing_counts.items())
        ),
        "reduced_evaporation_drive_stage_crossing_counts": dict(
            sorted(law.reduced_evaporation_drive_stage_crossing_counts.items())
        ),
        "reduced_rejections_with_evaporation_drive_crossing": (
            None
            if law.reduced_rejections_with_evaporation_drive_crossing is None
            else int(law.reduced_rejections_with_evaporation_drive_crossing)
        ),
        "reduced_rejection_evaporation_drive_crossing_fraction": (
            law.reduced_rejection_evaporation_drive_crossing_fraction
        ),
        "reduced_rejection_evaporation_drive_crossing_species_counts": (
            dict(
                sorted(
                    law.reduced_rejection_evaporation_drive_crossing_species_counts.items()
                )
            )
        ),
        "reduced_evaporation_drive_band_Pa": _SLOW_ATOL_PA,
        "reduced_error_test_rejections": law.reduced_error_test_rejections,
        "reduced_newton_nonconvergence_failures": (
            law.reduced_newton_nonconvergence_failures
        ),
        "reduced_error_test_top3_component_counts": (
            None
            if law.reduced_error_test_top3_component_counts is None
            else dict(sorted(law.reduced_error_test_top3_component_counts.items()))
        ),
        "reduced_newton_failure_diagnostics": (
            None
            if law.reduced_newton_failure_diagnostics is None
            else [dict(row) for row in law.reduced_newton_failure_diagnostics]
        ),
        "reduced_accepted_steps": int(law.reduced_accepted_steps),
        "reduced_rejected_steps": int(law.reduced_rejected_steps),
        "reduced_step_size_min_s": reduced_step_min,
        "reduced_step_size_median_s": reduced_step_median,
        "reduced_step_size_max_s": reduced_step_max,
        "reduced_time_covered_s": law.reduced_time_covered_s,
        "full_system_radau_time_covered_s": (
            law.full_system_radau_time_covered_s
        ),
        "handoff_time_s": law.handoff_time_s,
    }


# Fields the work-cap hour-cost and Radau tape carry. A
# headspace_balance_nonconvergence refusal copies these through the
# ledger rebuild. species_balance is not in this list.
_NONCONVERGENCE_DIAGNOSTIC_FIELDS = (
    "accepted_step_sizes_s",
    "rejected_steps",
    "rejected_error_dominators",
    "jacobian_abs_eigenvalue_min",
    "jacobian_abs_eigenvalue_max",
    "tau_s",
    "dt_over_tau",
    "composition_tau_s",
    "dt_over_composition_tau",
    "flux_per_rhs_min",
    "flux_per_rhs_median",
    "flux_per_rhs_max",
    "pstar_warm_newton",
    "pstar_brent_band",
    "pstar_sign_scan",
    "reduced_rhs_calls",
    "reduced_jacobian_builds",
    "reduced_ift_residual_check_rejections",
    "reduced_second_corrections",
    "reduced_resolver_warm_root_fallbacks",
    "reduced_resolver_bracket_fallbacks",
    "reduced_shut_branch_hits",
    "reduced_evaporation_drive_probe_evaluations",
    "reduced_evaporation_drive_near_zero_evaluations",
    "reduced_evaporation_drive_near_zero_species_counts",
    "reduced_evaporation_drive_jacobian_crossing_counts",
    "reduced_evaporation_drive_stage_crossing_counts",
    "reduced_rejections_with_evaporation_drive_crossing",
    "reduced_rejection_evaporation_drive_crossing_fraction",
    "reduced_rejection_evaporation_drive_crossing_species_counts",
    "reduced_evaporation_drive_band_Pa",
    "reduced_error_test_rejections",
    "reduced_newton_nonconvergence_failures",
    "reduced_error_test_top3_component_counts",
    "reduced_newton_failure_diagnostics",
    "reduced_accepted_steps",
    "reduced_rejected_steps",
    "reduced_step_size_min_s",
    "reduced_step_size_median_s",
    "reduced_step_size_max_s",
    "reduced_time_covered_s",
    "full_system_radau_time_covered_s",
    "handoff_time_s",
    "evaporation_drive_at_refusal",
)


def _nonconvergence_stiffness_payload(law: "_HeadspaceLaw") -> dict[str, Any]:
    """Step sizes and the rejection split from the Radau tape.

    The eigenvalue span is not evaluated. A missing tape leaves the
    fields out, so the refusal keeps them unset.
    """

    diagnostic = law.stiffness_diagnostic
    if not isinstance(diagnostic, Mapping):
        return {}
    step_sizes = diagnostic.get("accepted_step_sizes_s")
    raw_dominators = diagnostic.get("rejected_error_dominators")
    return {
        "accepted_step_sizes_s": (
            None if step_sizes is None else [float(step) for step in step_sizes]
        ),
        "rejected_steps": diagnostic.get("rejected_steps"),
        "rejected_error_dominators": (
            None
            if raw_dominators is None
            else [dict(row) for row in raw_dominators]
        ),
        "jacobian_abs_eigenvalue_min": diagnostic.get(
            "jacobian_abs_eigenvalue_min"
        ),
        "jacobian_abs_eigenvalue_max": diagnostic.get(
            "jacobian_abs_eigenvalue_max"
        ),
    }


def refusal_keeping_nonconvergence_diagnostics(rebuilt, source):
    """Copy the reduced tape onto a ledger rebuild of the same refusal.

    The hour wrapper replaces a nonconvergence with the ledger balance.
    That rebuild does not take the reduced counters. Copying them here
    is what the walk serialises. Any other reason is returned as built.
    """

    if getattr(source, "reason", None) != "headspace_balance_nonconvergence":
        return rebuilt
    payload = {
        name: getattr(source, name, None)
        for name in _NONCONVERGENCE_DIAGNOSTIC_FIELDS
    }
    return replace(rebuilt, **payload)


def _refused_shadow(
    law: _HeadspaceLaw,
    refused: _BalanceRefused,
) -> CapacityShadowRefusal:
    """Copy a refusal with the path and the iterations it had reached.

    ``_shadow_from_balance`` defaults iterations to 0. Passing only
    the path then reports that 0, and the hour wrapper treats it as
    the solver's count. The law stores the count the branch reached.
    An unset count stays 0.
    """

    count = law.balance_iterations
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        count = 0
    # The cap fires inside the open rhs interval. Close it before the
    # span is read, so the evaluation that crossed the cap is in the
    # last sample. A second close is a no-op.
    law._close_rhs_flux()
    cost = _hour_cost_payload(law)
    law._remember_hour_cost(cost)
    payload: dict[str, Any] = {}
    if refused.reason == "headspace_balance_work_cap":
        # The hour wrapper returns a work-cap refusal unchanged.
        # Filling the last accepted state here is what the walk shows.
        payload = _work_cap_payload(law)
    shadow = _shadow_from_balance(
        refused,
        count,
        path=law.balance_path,
        **payload,
    )
    if refused.reason == "headspace_balance_work_cap":
        return replace(shadow, **cost)
    if refused.reason != "headspace_balance_nonconvergence":
        return shadow
    # Same reduced counters, Newton dominators, step sizes, and
    # rejection split as the work cap. species_balance stays unset
    # here so the hour wrapper can still attach the ledger balance.
    # The stiffness fields are the Radau tape. |λ| stays unset.
    rows = law.evaporation_drive_at_refusal
    return replace(
        shadow,
        **cost,
        **_nonconvergence_stiffness_payload(law),
        evaporation_drive_at_refusal=(
            None if rows is None else [dict(row) for row in rows]
        ),
    )


@dataclass
class _HeadspaceStep:
    moles: dict[str, float]
    partials: dict[str, float]
    flux_kg_hr: dict[str, float]
    bled_mol: dict[str, float]
    bled_kg: float
    source_kg: float
    mass_end_kg: float
    mass_start_kg: float
    pressure_Pa: float
    iterations: int
    binding: str
    residual_Pa: float
    vapour_extent_kg: dict[str, float]
    redox_added_mol: dict[str, float]


def _ideal_gas_partials_Pa(
    moles: Mapping[str, float],
    *,
    temperature_K: float,
    volume_m3: float,
) -> dict[str, float]:
    # Premise: isothermal ideal gas. The inventory is the partial.
    # Algebra: p_i = n_i * R * T / V. R is GAS_CONSTANT, the full
    # CODATA value this module already imports. The 6-significant-figure
    # reader misses the 25.695650006 Pa pin by about 8e-6 Pa.
    # Unit check: mol * J/(mol·K) * K / m^3 = Pa.
    # Limit: n_i = 0 gives p_i = 0. The commanded millibar is not added.
    factor = GAS_CONSTANT * temperature_K / volume_m3
    return {species: moles[species] * factor for species in moles}


def _as_finite(value: object, reason: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _BalanceRefused(reason) from exc
    if not math.isfinite(number):
        raise _BalanceRefused(reason)
    return number


class _HeadspaceLaw:
    """Trial-pressure source and outflow for one headspace step.

    S_redox is a molar rate (mol/s). A partition of the hour passes the
    hour's rate N/3600 s into every substep, including a halved
    substep. N/dt_substep would put the substep length into G and move
    the fast root. The callback, when present, is that hour rate and
    is not rescaled by the substep. A missing callback leaves the
    constant map, which chunk 2 passes as zeros.
    """

    def __init__(
        self,
        *,
        species: tuple[str, ...],
        masses: Mapping[str, float],
        flux: Callable[[Mapping[str, float]], Mapping[str, float]],
        temperature_K: float,
        volume_m3: float,
        downstream_pa: float,
        diameter_m: float | None,
        length_m: float | None,
        cap_kg_s: float | None,
        redox_mol_s: Mapping[str, float],
        redox_mol_s_at_partials: (
            Callable[[Mapping[str, float]], Mapping[str, float]] | None
        ) = None,
        extent_holder: dict | None = None,
    ) -> None:
        self.species = species
        self.masses = dict(masses)
        self.flux = flux
        self.temperature_K = temperature_K
        self.volume_m3 = volume_m3
        self.downstream_pa = downstream_pa
        self.diameter_m = diameter_m
        self.length_m = length_m
        self.cap_kg_s = cap_kg_s
        self.redox_mol_s = {
            species_name: float(redox_mol_s.get(species_name, 0.0))
            for species_name in species
        }
        self.redox_mol_s_at_partials = redox_mol_s_at_partials
        # The hour's flux writes gross kilograms per hour here. Absent
        # on every other caller, and those callers grow no extra states.
        self.extent_holder = extent_holder
        self._last_gross_kg_s: dict[str, float] = {}
        self._last_redox_mol_s: dict[str, float] = {}
        # Hour-open stock S drains. signed_mol is the ledger figure
        # and may be negative. mol is max(0, signed). Set by the hour
        # on the extent holder before the solve. Absent on a caller
        # that does not pass one.
        self.buffer_o2_mol: float | None = None
        self.buffer_o2_mol_signed: float | None = None
        raw_stock = None
        if isinstance(extent_holder, dict):
            raw_stock = extent_holder.get("_opening_buffer_o2")
        if isinstance(raw_stock, dict):
            mol = raw_stock.get("mol")
            signed = raw_stock.get("signed_mol")
            if isinstance(mol, (int, float)) and not isinstance(mol, bool):
                self.buffer_o2_mol = float(mol)
            if isinstance(signed, (int, float)) and not isinstance(signed, bool):
                self.buffer_o2_mol_signed = float(signed)
        # Hour-open melt fO2. The solve does not move it. Absent on a
        # caller that does not pass one, and that absence stays unset.
        self.fo2_log: float | None = None
        if isinstance(extent_holder, dict) and "_opening_fo2_log" in extent_holder:
            raw_fo2 = extent_holder.get("_opening_fo2_log")
            if (
                isinstance(raw_fo2, (int, float))
                and not isinstance(raw_fo2, bool)
                and math.isfinite(float(raw_fo2))
            ):
                self.fo2_log = float(raw_fo2)
        # Last completed integrator step. A later Jacobian probe must
        # not replace it: the cap fires on the next evaluation, and
        # the walk wants the step that was accepted.
        self.accepted_moles: dict[str, float] | None = None
        self.accepted_partials: dict[str, float] | None = None
        self.accepted_step_redox: dict[str, float] | None = None
        self.accepted_pipe_piece: tuple | None = None
        self.accepted_capture_fractions: dict[str, float] | None = None
        # Slow-path tape. The work cap stores it, including |λ|. A
        # nonconvergence stores the step sizes and the rejection split
        # and leaves |λ| unset.
        self.stiffness_diagnostic: dict[str, Any] | None = None
        # This hour already declined the fixed-point hold. A later
        # step must not try again: the integrator is the remaining
        # engine, and the work cap is its refusal.
        self._quasi_steady_declined = False
        # Pressure at the previous accepted open step. The stall
        # detector compares the next collapsed step with it.
        self._open_step_pressure: float | None = None
        self._last_pipe_piece: tuple | None = None
        self.paired_redox_mol_s: dict[str, float] | None = None
        self.accepted_flux_partials: dict[str, float] | None = None
        self.accepted_flux_key: tuple | None = None
        self.accepted_redox_key: tuple | None = None
        # First notice per (species, reason). The sweep calls the pipe
        # on every trial; the hour keeps one flag, not one per sample.
        self.species_notices: dict[tuple[str, str], Mapping[str, Any]] = {}
        self.flux_evaluations = 0
        self.integrator_evaluations = 0
        # Overwritten when the hour enters a branch. A refusal copies
        # both. None is an unset iteration count, not a solved 0.
        self.balance_path: str | None = None
        self.balance_iterations: int | None = None
        # Opening scales. None until the hour records them. The sample
        # that records them is not a counted flux evaluation.
        self.opening_tau_s: float | None = None
        self.opening_dt_over_tau: float | None = None
        self.opening_composition_tau_s: float | None = None
        self.opening_dt_over_composition_tau: float | None = None
        # Opening outflow, mol/s. The frozen-composition hour prices
        # the fast pressure transient with it. None until that sample
        # runs.
        self.opening_q_mol_s: float | None = None
        # Flux from one rhs entry to the next. The mark is the counter
        # at the open entry. None means no interval is open.
        self._rhs_flux_counts: list[int] = []
        self._flux_at_rhs: int | None = None
        # P* solves. Mutually exclusive per solve: warm Newton, the
        # geometric Brent after a miss, or the sign-scan fallback.
        self.pstar_warm_newton = 0
        self.pstar_brent_band = 0
        self.pstar_sign_scan = 0
        self._counting_pstar = False
        self._pstar_scan_seen = False
        self.reduced_rhs_calls = 0
        self.reduced_jacobian_builds = 0
        self.reduced_ift_residual_check_rejections = 0
        self.reduced_second_corrections = 0
        self.reduced_resolver_warm_root_fallbacks = 0
        self.reduced_resolver_bracket_fallbacks = 0
        self.reduced_shut_branch_hits = 0
        self.reduced_evaporation_drive_probe_evaluations = 0
        self.reduced_evaporation_drive_near_zero_evaluations = 0
        self.reduced_evaporation_drive_near_zero_species_counts: dict[str, int] = {}
        self.reduced_evaporation_drive_jacobian_crossing_counts: dict[str, int] = {}
        self.reduced_evaporation_drive_stage_crossing_counts: dict[str, int] = {}
        self.reduced_rejections_with_evaporation_drive_crossing = 0
        self.reduced_rejection_evaporation_drive_crossing_fraction: float | None = None
        self.reduced_rejection_evaporation_drive_crossing_species_counts: dict[str, int] = {}
        self._last_evaporation_drive_delta_Pa: dict[str, float] | None = None
        # Last publisher drive rows. The nonconvergence refusal copies
        # this list. A diagnostic sample restores it with the other
        # law marks, so an opening probe does not become the refusal.
        self.evaporation_drive_at_refusal: list[dict[str, Any]] | None = None
        self.reduced_error_test_rejections: int | None = None
        self.reduced_newton_nonconvergence_failures: int | None = None
        self.reduced_error_test_top3_component_counts: dict[str, int] | None = None
        self.reduced_newton_failure_diagnostics: list[dict[str, Any]] | None = None
        self.reduced_accepted_steps = 0
        self.reduced_rejected_steps = 0
        self.reduced_step_sizes_s: list[float] = []
        self.reduced_time_covered_s: float | None = None
        self.full_system_radau_time_covered_s: float | None = None
        self.handoff_time_s: float | None = None

    def _remember_species_notices(
        self,
        notices: tuple[Mapping[str, Any], ...] | tuple,
    ) -> None:
        for notice in notices:
            species = str(notice.get("species", ""))
            reason = str(notice.get("reason", ""))
            key = (species, reason)
            if key not in self.species_notices:
                self.species_notices[key] = dict(notice)

    def _redox_rates(self, partials: Mapping[str, float]) -> dict[str, float]:
        # Membership stays on the constant map built with the species
        # set. The callback is the hour rate N/3600 s. It is not divided
        # by the substep. A missing callback returns that constant map,
        # so a chunk-2 zero is unchanged.
        if self.redox_mol_s_at_partials is None:
            self._last_redox_mol_s = self.redox_mol_s
            self._remember_redox_partials(partials, self.redox_mol_s)
            return self.redox_mol_s
        try:
            raw = self.redox_mol_s_at_partials(partials)
        except _BalanceRefused:
            raise
        except Exception as exc:
            raise _BalanceRefused(
                f"redox_source_refused:{type(exc).__name__}:{exc}"
            ) from exc
        if not isinstance(raw, Mapping):
            raise _BalanceRefused("redox_source_not_a_mapping")
        out = dict(self.redox_mol_s)
        for name, rate in raw.items():
            label = str(name)
            if label not in self.masses or label not in self.redox_mol_s:
                raise _BalanceRefused(f"missing_molar_mass:{label}")
            out[label] = _as_finite(rate, f"redox_source_not_finite:{label}")
        self._last_redox_mol_s = out
        self._remember_redox_partials(partials, out)
        return out

    def _remember_redox_partials(
        self,
        partials: Mapping[str, float],
        redox: Mapping[str, float],
    ) -> None:
        # S at this partial. The integrator step copies the same pair
        # after it accepts the step. A flux cap before any step uses
        # this only when the partial matches the last completed flux.
        self.paired_redox_mol_s = {
            str(name): float(rate) for name, rate in redox.items()
        }
        self.accepted_redox_key = headspace_partial_key(partials)

    def _remember_flux_partials(self, partials: Mapping[str, float]) -> None:
        self.accepted_flux_partials = {
            str(name): float(pressure) for name, pressure in partials.items()
        }
        self.accepted_flux_key = headspace_partial_key(partials)

    def partials_of(self, moles: Mapping[str, float]) -> dict[str, float]:
        return _ideal_gas_partials_Pa(
            moles,
            temperature_K=self.temperature_K,
            volume_m3=self.volume_m3,
        )

    def pressure_of(self, moles: Mapping[str, float]) -> float:
        return math.fsum(self.partials_of(moles).values())

    def moles_at_pressure(self, pressure_pa: float) -> dict[str, float]:
        # One species carries the whole pressure. Several species use
        # the sweep's own mole vector, not this inversion.
        species = self.species[0]
        if pressure_pa <= 0.0:
            return {species: 0.0}
        # n = p * V / (R T). Unit check: Pa * m^3 / (J/(mol·K) * K) = mol.
        moles = (
            pressure_pa
            * self.volume_m3
            / (GAS_CONSTANT * self.temperature_K)
        )
        return {species: moles}

    def _note_rhs_flux(self) -> None:
        """Open a new rhs interval, closing the one the previous entry left.

        The closed span is the flux evaluations since that entry. It
        includes the body, and the Jacobian and stall callback that
        ran before this entry. The booking after the integrator
        returns is not an rhs, and it is not in this span.
        """

        mark = self._flux_at_rhs
        if mark is not None:
            self._rhs_flux_counts.append(self.flux_evaluations - mark)
        self._flux_at_rhs = self.flux_evaluations

    def _close_rhs_flux(self) -> None:
        """Close the open rhs interval. A second call does not append."""

        mark = self._flux_at_rhs
        if mark is None:
            return
        self._rhs_flux_counts.append(self.flux_evaluations - mark)
        self._flux_at_rhs = None

    def _remember_hour_cost(self, cost: Mapping[str, Any]) -> None:
        # One notice per hour. The walk publishes species notices.
        # The balance terms and the work-cap payload carry the same
        # numbers. A later call does not replace the first.
        notice = {
            "species": "headspace",
            "reason": "headspace_hour_cost",
        }
        notice.update(dict(cost))
        self._remember_species_notices((notice,))

    def _record_opening_scales(
        self,
        n0: Mapping[str, float],
        dt_s: float,
    ) -> None:
        """Stash n/Q and n/Σ(q+S) at the opening holdup.

        The sample is not a solver evaluation. The flux counter, the
        accepted-step stash, and the species notices are restored, and
        the callback undoes its own latch, so the root still sees the
        hour it would have seen without the sample. A refusal from the
        sample leaves the scales unset and does not become the hour's
        refusal: the solve still runs.

        Premise: dn_i/dt = s_i − y_i Q, with one composition time
        shared by every species. The flush time is tau = n/Q. The
        composition time is n/Σ s. Both are taken at the opening
        holdup. Neither is a solved P*.
        Algebra: n = Σ max(n_i, 0), Q = Σ Q_i, s = Σ (q_i + S_i),
        and dt/tau = dt · Q / n.
        Unit check: mol / (mol/s) = s, and s/s = 1.
        Limit: Q <= 0 leaves tau unset. A shut pipe has no flush
        time. Σ s <= 0 leaves the composition time unset. n <= 0
        leaves both unset.
        """

        source: dict[str, float] | None = None
        outflow: dict[str, float] | None = None
        redox: dict[str, float] | None = None
        try:
            # Diagnostic: the callback must not latch capture, recorded,
            # or the holder. The law marks are restored with them.
            with _DiagnosticLawSample(self):
                partials = self.partials_of(n0)
                source, outflow, _binding, _piece = self._flows(
                    partials,
                    counted=False,
                )
                redox = self._redox_rates(partials)
        except _BalanceRefused:
            source = None
        if source is None or outflow is None or redox is None:
            return
        total = math.fsum(
            max(0.0, float(n0[name])) for name in self.species
        )
        outflow_mol_s = math.fsum(float(outflow[name]) for name in self.species)
        source_mol_s = math.fsum(
            float(source[name]) + float(redox[name]) for name in self.species
        )
        if math.isfinite(outflow_mol_s) and outflow_mol_s >= 0.0:
            self.opening_q_mol_s = outflow_mol_s
        if not math.isfinite(total) or total <= 0.0:
            return
        if (
            math.isfinite(outflow_mol_s)
            and outflow_mol_s > 0.0
        ):
            tau = total / outflow_mol_s
            if math.isfinite(tau) and tau > 0.0:
                self.opening_tau_s = tau
                ratio = float(dt_s) / tau
                if math.isfinite(ratio):
                    self.opening_dt_over_tau = ratio
        if math.isfinite(source_mol_s) and source_mol_s > 0.0:
            composition = total / source_mol_s
            if math.isfinite(composition) and composition > 0.0:
                self.opening_composition_tau_s = composition
                ratio = float(dt_s) / composition
                if math.isfinite(ratio):
                    self.opening_dt_over_composition_tau = ratio

    def _flux_kg_hr(
        self,
        partials: Mapping[str, float],
        *,
        counted: bool = True,
    ) -> dict[str, float]:
        from simulator.evaporation import EvaporationFluxRefusal

        if counted:
            self.flux_evaluations += 1
            if self.flux_evaluations > _MAX_FLUX_EVALUATIONS:
                self._close_rhs_flux()
                raise _BalanceRefused(
                    "headspace_balance_work_cap",
                    cap="flux",
                    flux_evaluations=self.flux_evaluations,
                    integrator_evaluations=self.integrator_evaluations,
                )
        self._last_evaporation_drive_delta_Pa = None
        try:
            raw = self.flux(partials)
        except EvaporationFluxRefusal:
            raise
        except _BalanceRefused:
            raise
        except Exception as exc:
            raise _BalanceRefused(
                f"evaporation_flux_refused:{type(exc).__name__}:{exc}"
            ) from exc
        if isinstance(self.extent_holder, dict):
            raw_drive = self.extent_holder.get(
                "_evaporation_driving_delta_Pa"
            )
            if isinstance(raw_drive, Mapping):
                drive_delta: dict[str, float] = {}
                for species, value in raw_drive.items():
                    try:
                        delta = float(value)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if math.isfinite(delta):
                        drive_delta[str(species)] = delta
                self._last_evaporation_drive_delta_Pa = drive_delta
        raw_rows = None
        if isinstance(self.extent_holder, dict):
            raw_rows = self.extent_holder.get("_evaporation_drive_rows")
        if isinstance(raw_rows, list):
            self.evaporation_drive_at_refusal = [
                dict(row) for row in raw_rows if isinstance(row, Mapping)
            ]
        else:
            self.evaporation_drive_at_refusal = None
        out: dict[str, float] = {}
        for name, rate in raw.items():
            if name not in self.masses or name not in self.redox_mol_s:
                raise _BalanceRefused(f"missing_molar_mass:{name}")
            number = _as_finite(rate, f"evaporation_flux_not_finite:{name}")
            # Signed net flux (d-468). Positive vapour leaves the melt.
            # Negative vapour recondenses. The oxidative debit stays on
            # the fO2 buffer and is not in this map. A clip here is a
            # kink in the reduced residual.
            out[str(name)] = number
        # The callback just wrote the gross it projected, in kg/h.
        # kg/s = (kg/h) / 3600. 3600 is exact in binary64. A caller
        # with no holder leaves the snapshot empty, and the fast
        # default extent is then zero. That caller does not post it.
        gross_kg_s: dict[str, float] = {}
        holder = self.extent_holder
        if isinstance(holder, dict):
            raw = holder.get("gross_kg_hr") or {}
            if isinstance(raw, Mapping):
                for name, rate in raw.items():
                    label = str(name)
                    if label not in self.masses:
                        continue
                    number = _as_finite(
                        rate,
                        f"evaporation_extent_not_finite:{label}",
                    )
                    gross_kg_s[label] = number / 3600.0
        self._last_gross_kg_s = gross_kg_s
        self._remember_flux_partials(partials)
        return out

    def _source_mol_s(
        self,
        partials: Mapping[str, float],
        *,
        counted: bool = True,
    ) -> dict[str, float]:
        # kg/h / (kg/mol) / (s/h) = mol/s. 3600 is exact in binary64.
        # Limit: a negative vapour rate removes moles. Zero adds none.
        flux = self._flux_kg_hr(partials, counted=counted)
        return {
            species: flux.get(species, 0.0) / self.masses[species] / 3600.0
            for species in self.species
        }

    def _flows(
        self,
        partials: Mapping[str, float],
        *,
        counted: bool = True,
        evaluate_source: bool = True,
    ) -> tuple[dict[str, float], dict[str, float], str, tuple]:
        # The piece tuple is the pipe binding, the blend bucket, the
        # cap, and the empty holdup. It does not read q. A piece
        # sample that also evaluated the source counted as a flux
        # evaluation on every Newton probe.
        if evaluate_source:
            source = self._source_mol_s(partials, counted=counted)
        else:
            source = {species: 0.0 for species in self.species}
        pressure = math.fsum(partials[species] for species in self.species)
        moles = {
            species: partials[species]
            * self.volume_m3
            / (GAS_CONSTANT * self.temperature_K)
            for species in self.species
        }
        total_mol = math.fsum(max(0.0, moles[species]) for species in self.species)
        pipe_mol = {species: 0.0 for species in self.species}
        binding = "pipe"
        blend = 0.0
        if (
            self.diameter_m is not None
            and self.length_m is not None
            and total_mol > 0.0
            and pressure > 0.0
        ):
            holdup = {
                species: moles[species]
                for species in self.species
                if moles[species] > 0.0
            }
            try:
                outflow = pipe_species_outflow_mol_s(
                    pressure,
                    self.downstream_pa,
                    self.temperature_K,
                    holdup,
                    self.diameter_m,
                    self.length_m,
                    molar_mass_kg_mol=self.masses,
                )
            except TransportRegimeRefusal as exc:
                raise _BalanceRefused(str(exc)) from exc
            # The pipe flags an uncertified diameter and still returns
            # the flow. A missing mass stays a typed refusal above.
            self._remember_species_notices(outflow.species_notices)
            pipe_mol = {
                species: float(outflow.species_mol_s.get(species, 0.0))
                for species in self.species
            }
            binding = outflow.binding_cause
            blend = float(outflow.blend_weight)
        mass_kg_s = math.fsum(
            pipe_mol[species] * self.masses[species] for species in self.species
        )
        capped = False
        outflow_mol = pipe_mol
        if self.cap_kg_s is not None and self.diameter_m is None:
            # Constant cap, no duct. Q is that mass flow for any n > 0
            # and zero at an empty holdup, so one backward-Euler step of
            # a cap below the source is exact.
            if total_mol <= 0.0 or pressure <= 0.0 or self.cap_kg_s == 0.0:
                outflow_mol = {species: 0.0 for species in self.species}
            elif len(self.species) == 1:
                species = self.species[0]
                outflow_mol = {species: self.cap_kg_s / self.masses[species]}
            else:
                outflow_mol = {
                    species: self.cap_kg_s
                    * (moles[species] / total_mol)
                    / self.masses[species]
                    for species in self.species
                }
            binding = (
                "line_closed" if self.cap_kg_s == 0.0 else "controlled_o2_equipment"
            )
            capped = self.cap_kg_s > 0.0
            blend = 0.0
        elif self.cap_kg_s is not None and mass_kg_s > self.cap_kg_s:
            scale = 0.0 if mass_kg_s <= 0.0 else self.cap_kg_s / mass_kg_s
            outflow_mol = {
                species: pipe_mol[species] * scale for species in self.species
            }
            capped = True
            binding = "controlled_o2_equipment"
        if blend <= 0.0:
            bucket = 0
        elif blend >= 1.0:
            bucket = 2
        else:
            bucket = 1
        # The floor flag is the n_i = 0 kink. A difference that steps
        # off zero is not a derivative of the smooth piece. The shut
        # at P_down is not in this tuple: the slow stencil adds it
        # to its own key, and the fast piece identity stays this one.
        piece = (binding, bucket, capped, pressure <= 0.0)
        self._last_pipe_piece = piece
        return source, outflow_mol, binding, piece

    def _scalar_g(self, pressure_pa: float) -> float:
        # G = q_head + S - Q. n0 and Δt are not arguments.
        # Unit check: each term is mol/s. Limit: Q = 0 and q + S > 0
        # leaves G positive, so a closed line has no fast root.
        moles = self.moles_at_pressure(pressure_pa)
        partials = self.partials_of(moles)
        source, outflow, _binding, _piece = self._flows(partials)
        species = self.species[0]
        return (
            source[species]
            + self._redox_rates(partials)[species]
            - outflow[species]
        )

    def _scalar_residual(
        self,
        n0: Mapping[str, float],
        dt_s: float,
        pressure_pa: float,
    ) -> float:
        # Premise: backward Euler, end-state rates over the whole substep.
        # Algebra: R = n - n0 - Δt (q + S - Q).
        # Unit check: (mol/s) * s = mol.
        # Limit: Q = 0 and constant q gives n = n0 + (q + S) Δt.
        moles = self.moles_at_pressure(pressure_pa)
        partials = self.partials_of(moles)
        source, outflow, _binding, _piece = self._flows(partials)
        species = self.species[0]
        return (
            moles[species]
            - n0[species]
            - dt_s
            * (
                source[species]
                + self._redox_rates(partials)[species]
                - outflow[species]
            )
        )

    def g_samples(self, *, refuse_multiple: bool = True) -> list[tuple[float, float]]:
        return _g_pressure_samples(
            self,
            self._scalar_g,
            "G(P) rose on the geometric bracket; the log sign scan "
            "published the crossings",
            refuse_multiple=refuse_multiple,
        )

    def piece_at(self, pressure_pa: float) -> tuple:
        _source, _outflow, _binding, piece = self._flows(
            self.partials_of(self.moles_at_pressure(max(pressure_pa, 0.0))),
            evaluate_source=False,
        )
        return piece

    def outflow_kg_s(self, pressure_pa: float) -> tuple[float, tuple]:
        _source, outflow, _binding, piece = self._flows(
            self.partials_of(self.moles_at_pressure(max(pressure_pa, 0.0)))
        )
        mass = math.fsum(
            outflow[species] * self.masses[species] for species in self.species
        )
        return mass, piece

    def a_min(self, pressure_a: float, pressure_b: float) -> float:
        # a = dQ/dm. For one ideal-gas species dm = dP * M * V / (R T),
        # so a = (dQ_kg/dP) * R * T / (M * V).
        # Unit check: (kg/s/Pa) * (J/(mol·K)) * K / ((kg/mol) * m^3)
        # = 1/s. A flat sample has dQ/dP = 0, so a_min = 0 and the
        # step does not admit, even when another sample on the same
        # interval is steep. A pipe below P_down is that flat piece.
        # Skipping the zero and keeping the steep sample would admit
        # a fast root the holdup has not reached. A stencil that
        # changes piece (sonic kink, blend knot, cap, or the
        # empty-holdup floor) is not a measured slope and is skipped.
        # The low-pressure end, where this pipe's positive slope is
        # smallest, stays in the grid.
        lo = min(pressure_a, pressure_b)
        hi = max(pressure_a, pressure_b)
        start = lo if lo > 0.0 else max(hi * 1.0e-8, 1.0e-8)
        points = [start, hi]
        if hi > start * (1.0 + 1.0e-12):
            log_lo = math.log(start)
            log_hi = math.log(hi)
            points.extend(
                math.exp(log_lo + (log_hi - log_lo) * index / 64.0)
                for index in range(1, 64)
            )
        species = self.species[0]
        scale = (
            GAS_CONSTANT
            * self.temperature_K
            / (self.masses[species] * self.volume_m3)
        )
        slopes: list[float] = []
        for pressure in points:
            slope = self._dq_kg_dp(pressure)
            if slope is None or not math.isfinite(slope):
                continue
            if slope <= 0.0:
                return 0.0
            slopes.append(slope * scale)
        if not slopes:
            return 0.0
        return min(slopes)

    def _dq_kg_dp(self, pressure_pa: float) -> float | None:
        if pressure_pa <= 0.0:
            return None
        # Central relative 1e-6, the same step the design probe used
        # for a(P). One-sided when the other side would leave the piece.
        step = max(abs(pressure_pa) * 1.0e-6, 1.0e-9)
        mass_0, piece_0 = self.outflow_kg_s(pressure_pa)
        mass_up: float | None = None
        mass_down: float | None = None
        if pressure_pa + step > 0.0:
            mass, piece = self.outflow_kg_s(pressure_pa + step)
            if piece == piece_0:
                mass_up = mass
        if pressure_pa - step > 0.0:
            mass, piece = self.outflow_kg_s(pressure_pa - step)
            if piece == piece_0:
                mass_down = mass
        if mass_up is not None and mass_down is not None:
            return (mass_up - mass_down) / (2.0 * step)
        if mass_up is not None:
            return (mass_up - mass_0) / step
        if mass_down is not None:
            return (mass_0 - mass_down) / step
        return None

    def step_from_moles(
        self,
        n0: Mapping[str, float],
        dt_s: float,
        moles: Mapping[str, float],
        iterations: int,
        residual_Pa: float,
        *,
        added_mol: Mapping[str, float] | None = None,
        bled_mol_in: Mapping[str, float] | None = None,
        vapour_extent_kg: Mapping[str, float] | None = None,
        redox_added_mol: Mapping[str, float] | None = None,
    ) -> _HeadspaceStep:
        # The booking below evaluates the flux. That is not an rhs.
        # Close the open interval first so the span stays the
        # integrator's.
        self._close_rhs_flux()
        partials = self.partials_of(moles)
        source, outflow, binding, _piece = self._flows(partials)
        flux = self._flux_kg_hr(partials)
        mass_start = math.fsum(
            n0[species] * self.masses[species] for species in self.species
        )
        published = {
            species: float(moles[species]) for species in self.species
        }
        mass_end = math.fsum(
            published[species] * self.masses[species]
            for species in self.species
        )
        redox_end: Mapping[str, float] | None = None
        rewrote = False
        added_record: dict[str, float] = {}
        if added_mol is None:
            # Fast root. m_source = (q + S) * M * Δt. At the root
            # q + S = Q, so the bled mass is the hour's source plus
            # the drained holdup. This is not the slow branch: a
            # source that changes inside the step is not q(end)·Δt.
            # Unit check: mol/s * kg/mol * s = kg.
            # Limit: already at the root, m0 = m_end and bled = (q + S) Δt.
            redox = self._redox_rates(partials)
            redox_end = redox
            source_kg = math.fsum(
                (source[species] + redox[species])
                * self.masses[species]
                * dt_s
                for species in self.species
            )
            # q(end) = 0 with n_end a trace above n0 makes bled
            # negative. The slow integral zeros a bleed under the
            # 1e-6 Pa floor and publishes n_end = n0 + added, which
            # is the holdup a zero bleed leaves on the ledger. A
            # larger negative is not that trace: the caller still
            # sees it. Rewriting it would delete a real holdup.
            # Algebra: under the floor, bled = 0 and n_end = n0 + added.
            # Unit check: mol * R * T / V = Pa.
            # Limit: a positive drain is unchanged.
            pa_per_mol = GAS_CONSTANT * self.temperature_K / self.volume_m3
            bled_mol = {}
            for species in self.species:
                added = (source[species] + redox[species]) * dt_s
                added_record[species] = added
                bled = float(n0[species]) + added - published[species]
                if (
                    bled < 0.0
                    and -bled * pa_per_mol <= DEFAULT_ABS_TOL_PA
                ):
                    published[species] = float(n0[species]) + added
                    bled = 0.0
                    rewrote = True
                bled_mol[species] = bled
            if rewrote:
                partials = self.partials_of(published)
                mass_end = math.fsum(
                    published[species] * self.masses[species]
                    for species in self.species
                )
                # The booked fast source stays the pre-rewrite rate.
                # The extent the hour posts is the gross at the
                # partials this step publishes. flux_kg_hr stays the
                # pre-rewrite sample: saturation reads that rate.
                self._flux_kg_hr(partials)
                redox_end = self._redox_rates(partials)
        else:
            # Slow branch. added_mol is A_i(Δt) = ∫ (q_i + S_i) dt and
            # bled_mol_in is B_i(Δt) = ∫ Q_i dt, both in mol. The caller
            # published n_end = n0 + A − B, so this booking is that
            # identity. Unit check: mol * kg/mol = kg.
            # Limit: constant q and Q = 0 gives A = (q + S) Δt and B = 0.
            source_kg = math.fsum(
                added_mol[species] * self.masses[species]
                for species in self.species
            )
            bled_mol = {
                species: bled_mol_in[species] for species in self.species
            }
            added_record = {
                species: float(added_mol[species]) for species in self.species
            }
            # A slow caller that passes added_mol and omits the
            # integrals would post the endpoint. That is the leak.
            # A caller with no holder has nothing to track, and the
            # end-rate default below is unread.
            if self.extent_holder is not None and (
                vapour_extent_kg is None or redox_added_mol is None
            ):
                raise _BalanceRefused("headspace_extent_missing")
        if vapour_extent_kg is None:
            # Fast root, or a diagnostic step with no holder. The
            # integral of a rate the root held fixed is that rate
            # times the step. Unit check: (kg/s) * s = kg.
            vapour_extent_kg = {
                name: float(self._last_gross_kg_s.get(name, 0.0)) * dt_s
                for name in self.species
            }
        if redox_added_mol is None:
            if redox_end is None:
                redox_end = self._redox_rates(partials)
            redox_added_mol = {
                name: float(redox_end.get(name, 0.0)) * dt_s
                for name in self.species
            }
        bled_kg = math.fsum(
            bled_mol[species] * self.masses[species] for species in self.species
        )
        # The inventory refusal reads this. A success hour does not.
        # The key is not a species and not a rate the flux callback reads.
        # The cost notice is what the walk publishes on a success hour.
        # The balance terms carry the same numbers when a holder exists.
        cost = _hour_cost_payload(self)
        self._remember_hour_cost(cost)
        holder = self.extent_holder
        if isinstance(holder, dict):
            gross = dict(vapour_extent_kg)
            redox_map = dict(redox_added_mol)

            def _stored(mapping: Mapping[str, float], species: str):
                if species not in mapping:
                    return None
                return float(mapping[species])

            holder["_balance_terms"] = {
                "path": self.balance_path,
                "iterations": int(iterations),
                "n0_mol": {
                    species: float(n0[species]) for species in self.species
                },
                "A_mol": {
                    species: _stored(added_record, species)
                    for species in self.species
                },
                "B_mol": {
                    species: _stored(bled_mol, species)
                    for species in self.species
                },
                "R_mol": {
                    species: _stored(redox_map, species)
                    for species in self.species
                },
                "G_kg": {
                    species: _stored(gross, species)
                    for species in self.species
                },
                **cost,
            }
        return _HeadspaceStep(
            moles=dict(published),
            partials=partials,
            flux_kg_hr=flux,
            bled_mol=bled_mol,
            bled_kg=bled_kg,
            source_kg=source_kg,
            mass_end_kg=mass_end,
            mass_start_kg=mass_start,
            pressure_Pa=math.fsum(partials.values()),
            iterations=iterations,
            binding=binding,
            residual_Pa=residual_Pa,
            vapour_extent_kg={
                str(name): float(kilograms)
                for name, kilograms in dict(vapour_extent_kg).items()
            },
            redox_added_mol={
                str(name): float(moles)
                for name, moles in dict(redox_added_mol).items()
            },
        )


def _log_pressure_samples(
    lo: float,
    hi: float,
    count: int,
    value_at: Callable[[float], float],
) -> list[tuple[float, float]]:
    """Log-spaced samples on [lo, hi]. Stop once two sign changes are in.

    The one-species grid and the mixture ray both used this spacing,
    the same two-crossing stop, and the same 1e12 Pa extension. One
    edit has to move both, or a second root past 1e8 Pa is a refusal
    on one path and a fast commit on the other.
    """

    log_lo = math.log(lo)
    log_hi = math.log(hi)
    samples: list[tuple[float, float]] = []
    crossings = 0
    previous: float | None = None
    for index in range(count):
        pressure = math.exp(log_lo + (log_hi - log_lo) * index / (count - 1))
        value = value_at(pressure)
        if not math.isfinite(value):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        if previous is not None and previous * value < 0.0:
            crossings += 1
        if value == 0.0 and previous not in (None, 0.0):
            crossings += 1
        previous = value
        samples.append((pressure, value))
        if crossings >= 2:
            break
    return samples


def _pressure_sign_scan(
    downstream_pa: float,
    value_at: Callable[[float], float],
) -> list[tuple[float, float]]:
    """Sign samples of one scalar from vacuum or P_down up to 1e12 Pa.

    Floor is P_down, or 1e-8 Pa when the downstream pressure is 0, then
    a 4096-point log grid through 1e8 Pa. A still-positive end extends
    the same grid to 1e12 Pa. The explicit 0 Pa sample is only for a
    vacuum downstream, where P = 0 is the closed end of the pipe.
    """

    lo = downstream_pa if downstream_pa > 0.0 else 1.0e-8
    hi = max(1.0e8, lo * 10.0)
    samples: list[tuple[float, float]] = []
    if downstream_pa <= 0.0:
        samples.append((0.0, value_at(0.0)))
    samples.extend(_log_pressure_samples(lo, hi, _SIGN_SCAN_POINTS, value_at))
    if samples[-1][1] > 0.0:
        samples.extend(
            _log_pressure_samples(hi, _GEOMETRIC_CAP_PA, 1024, value_at)[1:]
        )
    return samples


def _remember_sign_scan_fallback(law: _HeadspaceLaw, detail: str) -> None:
    # One flag per hour. The sweep samples every species; the notice
    # is the fallback, not one row per flux call. A P* solve counts
    # the scan once, even if this notice was already stored.
    if law._counting_pstar and not law._pstar_scan_seen:
        law._pstar_scan_seen = True
        law.pstar_sign_scan += 1
    law._remember_species_notices((
        {
            "species": "headspace",
            "reason": "sign_scan_fallback",
            "detail": detail,
        },
    ))


def _mark_balance_path(law: _HeadspaceLaw, path: str) -> None:
    # One string for the branch now running. A fall-through overwrites
    # it, so the refusal names the path that refused, not the grid
    # that returned.
    law.balance_path = path


def _remember_balance_iterations(law: _HeadspaceLaw, iterations: int) -> None:
    # The count a refusal reports. A later branch overwrites it, the
    # same way the path does. 0 is the unset default and is not stored.
    count = int(iterations)
    if count > 0:
        law.balance_iterations = count


def _remember_shut_path(law: _HeadspaceLaw, path: str) -> None:
    # One flag per hour. midpoint is the fast shut grid. radau_fallback
    # is the slow integral after that grid stays apart. The snapshot
    # already carries species notices, so the hour record is this notice.
    law._remember_species_notices((
        {
            "species": "headspace",
            "reason": "shut_path",
            "detail": path,
        },
    ))


def _geometric_pressures(downstream_pa: float) -> list[float]:
    """Factor-of-two pressures from the downstream floor through 1e12 Pa.

    Premise: G(P) = q_head + S − Q. Along one composition, q_head is
    non-increasing in P and Q is non-decreasing once the mean free path
    is continuous. A choked throat has dQ/dP = 0, which is still
    non-decreasing. S is fixed on a trial. G is therefore non-increasing,
    and one confirming sample past the first strictly negative value
    cannot hide a second root unless G rises.
    Algebra: P_0 = max(P_down, 1e-8 Pa), P_{k+1} = min(2 P_k, 1e12 Pa).
    Vacuum downstream also samples P = 0, the closed end of the pipe.
    The named two-root well is negative only on [2e8, 5e8) Pa. Factor 2
    places a sample in that well (2^55 × 1e-8 = 3.60e8) and the next
    sample past it (7.21e8). Factor 4 jumps 1.80e8 to 7.21e8 and skips
    the well, so the factor stays 2.
    Unit check: each node is a pressure in Pa.
    Limit: a run of exact zeros is not a root and does not stop the
    walk. The closed-pipe plateau stays a plateau.
    """

    points: list[float] = []
    if downstream_pa <= 0.0:
        points.append(0.0)
    pressure = downstream_pa if downstream_pa > 0.0 else 1.0e-8
    while True:
        points.append(pressure)
        if pressure >= _GEOMETRIC_CAP_PA:
            break
        nxt = pressure * 2.0
        if nxt > _GEOMETRIC_CAP_PA:
            nxt = _GEOMETRIC_CAP_PA
        if nxt <= pressure:
            break
        pressure = nxt
    return points


def _geometric_sign_samples(
    points: list[float],
    value_at: Callable[[float], float],
    *,
    direction: str,
    crossings_of: Callable[
        [list[tuple[float, float]]],
        list[tuple[str, float, float]],
    ] | None = None,
    refuse_multiple: bool = True,
) -> tuple[list[tuple[float, float]], bool]:
    """Walk `points` until one confirmed root, or report a reversal.

    direction "down" is a non-increasing residual (G). The far side of
    its one root is strictly negative. direction "up" is a
    non-decreasing residual (R). Its far side is strictly positive.
    A reversal returns fallback True and the samples taken so far are
    not a bracket: the caller scans. Two filtered crossings are
    multiple roots. The fast scan refuses. A running integral asks
    for the samples instead and keeps the root the pressure approaches.
    One confirming sample past the first far-side value is what catches
    a well that is negative for less than one factor-of-two step and
    then positive. Stopping on the first negative sample would commit
    that well's first root.
    """

    classify = crossings_of if crossings_of is not None else _crossings
    samples: list[tuple[float, float]] = []
    previous: float | None = None
    armed = False
    for point in points:
        value = value_at(point)
        if not math.isfinite(value):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        if previous is not None:
            if direction == "down":
                adverse = value - previous
            else:
                adverse = previous - value
            scale = max(abs(previous), abs(value))
            limit = max(_MONOTONE_ABS, _MONOTONE_REL * scale)
            if adverse > limit:
                return samples, True
        samples.append((point, value))
        previous = value
        found = classify(samples)
        if len(found) >= 2:
            if refuse_multiple:
                raise _BalanceRefused("headspace_balance_multiple_roots")
            return samples, False
        far = value < 0.0 if direction == "down" else value > 0.0
        if len(found) == 1 and far:
            if armed:
                return samples, False
            armed = True
        else:
            armed = False
    return samples, False


def _g_pressure_samples(
    law: _HeadspaceLaw,
    value_at: Callable[[float], float],
    detail: str,
    *,
    refuse_multiple: bool = True,
) -> list[tuple[float, float]]:
    samples, fallback = _geometric_sign_samples(
        _geometric_pressures(law.downstream_pa),
        value_at,
        direction="down",
        refuse_multiple=refuse_multiple,
    )
    if not fallback:
        return samples
    _remember_sign_scan_fallback(law, detail)
    return _pressure_sign_scan(law.downstream_pa, value_at)


def _species_log_scan(
    func: Callable[[float], float],
    n_hi: float,
    roots_of: Callable[
        [list[tuple[float, float]]],
        list[tuple[str, float, float]],
    ],
) -> list[tuple[float, float]]:
    """The 257-point mole grid. Fallback only, when the bracket reverses."""

    samples = [(0.0, func(0.0))]
    if not math.isfinite(samples[0][1]):
        raise _BalanceRefused("headspace_balance_nonconvergence")
    if n_hi > 0.0:
        log_lo = math.log(1.0e-16)
        log_hi = math.log(max(n_hi, 1.0e-16))
        for index in range(1, 257):
            amount = math.exp(log_lo + (log_hi - log_lo) * index / 256.0)
            value = func(amount)
            if not math.isfinite(value):
                raise _BalanceRefused("headspace_balance_nonconvergence")
            samples.append((amount, value))
            if len(roots_of(samples)) >= 2:
                raise _BalanceRefused("headspace_balance_multiple_roots")
    return samples


def _crossings(
    samples: list[tuple[float, float]],
) -> list[tuple[str, float, float]]:
    """One entry per sign change. A run of exact zeros is not a run of roots.

    product < 0 is a bracket. An isolated 0 with a nonzero neighbor is
    one exact root. A function that is 0 everywhere has no nonzero
    neighbor, so it is not a sign change: the slow branch then keeps
    a closed, sourceless holdup where it is.
    """

    found: list[tuple[str, float, float]] = []
    count = len(samples)
    for index, (pressure, value) in enumerate(samples):
        if value == 0.0:
            previous = samples[index - 1][1] if index else None
            nxt = samples[index + 1][1] if index + 1 < count else None
            neighbors = [item for item in (previous, nxt) if item is not None]
            if any(item != 0.0 for item in neighbors) and previous != 0.0:
                found.append(("exact", pressure, pressure))
            continue
        if index == 0:
            continue
        previous_p, previous_g = samples[index - 1]
        if previous_g == 0.0:
            continue
        if previous_g * value < 0.0:
            found.append(("bracket", previous_p, pressure))
    return found


def _newton_propose(
    func: Callable[[float], float],
    piece_at: Callable[[float], tuple],
    guess: float,
    lo: float,
    hi: float,
) -> float | None:
    """Damped Newton on one smooth piece. The caller discards the return.

    A step that leaves the piece (sonic kink, blend knot, cold-train
    cap, or n = 0) is rejected and shortened. Backtracking that only
    asks the residual to fall can stall on a kink; stalling returns
    None and is not a commit. The first guess may be the pressure of
    n0, clamped into the bracket. It is not the commanded pressure.
    """

    if not hi > lo:
        return None
    trial_x = min(max(guess, lo), hi)
    for _iteration in range(DEFAULT_MAX_ITERATIONS):
        value = func(trial_x)
        if value == 0.0:
            return trial_x
        piece = piece_at(trial_x)
        step_h = max(abs(trial_x) * 1.0e-6, 1.0e-9)
        right: float | None = None
        left: float | None = None
        if trial_x + step_h <= hi and piece_at(trial_x + step_h) == piece:
            right = func(trial_x + step_h)
        if trial_x - step_h >= lo and piece_at(trial_x - step_h) == piece:
            left = func(trial_x - step_h)
        if right is None and left is None:
            return None
        if right is not None and left is not None:
            derivative = (right - left) / (2.0 * step_h)
        elif right is not None:
            derivative = (right - value) / step_h
        else:
            derivative = (value - left) / step_h  # type: ignore[operator]
        if derivative == 0.0 or not math.isfinite(derivative):
            return None
        step = -value / derivative
        if not math.isfinite(step):
            return None
        accepted = False
        taken = step
        for _back in range(8):
            candidate = trial_x + step
            if lo <= candidate <= hi and piece_at(candidate) == piece:
                trial_value = func(candidate)
                if abs(trial_value) < abs(value):
                    trial_x = candidate
                    taken = step
                    accepted = True
                    break
            step *= 0.5
        if not accepted:
            return None
        if abs(taken) <= max(DEFAULT_ABS_TOL_PA, DEFAULT_REL_TOL * abs(trial_x)):
            return trial_x
    return None


def _brent(func: Callable[[float], float], lo: float, hi: float) -> tuple[float, int]:
    """Publish the root. An endpoint that is already 0 is that root.

    brentq needs a strict sign change, so an exact zero is returned
    before the call. xtol 1e-12 Pa and rtol 1e-14 sit under the
    shadow's 1e-6 Pa pressure tolerance.
    """

    if hi < lo:
        lo, hi = hi, lo
    low = func(lo)
    high = func(hi)
    if low == 0.0:
        return lo, 1
    if high == 0.0:
        return hi, 1
    if low * high > 0.0 or not hi > lo:
        raise _BalanceRefused("headspace_balance_nonconvergence")
    from scipy.optimize import brentq

    try:
        root, info = brentq(
            func,
            lo,
            hi,
            xtol=1.0e-12,
            rtol=1.0e-14,
            maxiter=200,
            full_output=True,
            disp=False,
        )
    except ValueError as exc:
        raise _BalanceRefused("headspace_balance_nonconvergence") from exc
    return float(root), max(int(info.iterations), 1)


def _solve_bracket(
    func: Callable[[float], float],
    piece_at: Callable[[float], tuple],
    crossing: tuple[str, float, float],
    guess: float,
) -> tuple[float, int]:
    # Newton first, including when it converges. Brent then publishes.
    # The last Newton iterate is not the commit.
    lo, hi = crossing[1], crossing[2]
    _newton_propose(func, piece_at, guess, min(lo, hi), max(lo, hi))
    if crossing[0] == "exact":
        return crossing[1], 1
    return _brent(func, lo, hi)


def _admits(law: _HeadspaceLaw, p0: float, p_root: float, dt_s: float) -> bool:
    # Premise: a linear drain dm/dt = -a (m - m_root) leaves a gap
    # |P(m0) - P_root| * exp(-a_min Δt). Admit when that gap is at
    # most 1e-6 Pa. exp underflows to 0 in binary64 once a Δt is
    # about 745 or more, and that underflow admits.
    # Limit: a gap already inside 1e-6 Pa admits with a_min unused.
    # A non-positive a_min (flat cap) does not admit.
    gap = abs(p0 - p_root)
    if gap <= DEFAULT_ABS_TOL_PA:
        return True
    a_min = law.a_min(p0, p_root)
    if a_min <= 0.0:
        return False
    product = a_min * dt_s
    if product >= _ADMISSION_EXP_UNDERFLOW:
        return True
    factor = math.exp(-product)
    if factor == 0.0:
        return True
    return gap * factor <= DEFAULT_ABS_TOL_PA


def _one_euler(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    """One backward-Euler step. Never the fast root of G."""

    _mark_balance_path(law, "backward_euler")
    if len(law.species) == 1:
        return _euler_one(law, n0, dt_s)
    return _euler_many(law, n0, dt_s, DEFAULT_REL_TOL, DEFAULT_MAX_ITERATIONS)


def _euler_one(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    species = law.species[0]
    # No reverse flow. §1 sets the outflow to 0 when P_up <= P_down, so
    # a holdup already below downstream stays there and the root of R
    # is outside a bracket that opens at P_down. Vacuum downstream is
    # already 0. A holdup above P_down keeps that floor.
    if law.pressure_of(n0) <= law.downstream_pa:
        p_lo = 0.0
    else:
        p_lo = law.downstream_pa if law.downstream_pa > 0.0 else 0.0
    zero = law.partials_of(law.moles_at_pressure(0.0))
    source0, _outflow0, _binding0, _piece0 = law._flows(zero)
    n_hi = n0[species] + max(
        0.0,
        source0[species] + law._redox_rates(zero)[species],
    ) * dt_s
    p_hi = n_hi * GAS_CONSTANT * law.temperature_K / law.volume_m3
    if p_hi <= p_lo:
        p_hi = p_lo + max(DEFAULT_ABS_TOL_PA, abs(p_lo))

    def residual(pressure: float) -> float:
        return law._scalar_residual(n0, dt_s, pressure)

    low = residual(p_lo)
    high = residual(p_hi)
    expands = 0
    while low * high > 0.0 and expands < 40:
        p_hi = p_hi * 2.0 if p_hi > 0.0 else 1.0
        high = residual(p_hi)
        expands += 1
    if low * high > 0.0 and low != 0.0 and high != 0.0:
        raise _BalanceRefused("headspace_balance_nonconvergence")
    guess = law.pressure_of(n0)
    # Discarded. Brent, or an endpoint that is already 0, publishes.
    _newton_propose(residual, law.piece_at, guess, p_lo, p_hi)
    if low == 0.0:
        root, iterations = p_lo, 1
    elif high == 0.0:
        root, iterations = p_hi, 1
    else:
        root, iterations = _brent(residual, p_lo, p_hi)
    moles = law.moles_at_pressure(root)
    residual_pa = abs(residual(root)) * (
        GAS_CONSTANT * law.temperature_K / law.volume_m3
    )
    return law.step_from_moles(n0, dt_s, moles, iterations, residual_pa)


# The active slow solve. Nested _slow_converge calls stack this.
_ACTIVE_STIFFNESS: contextvars.ContextVar = contextvars.ContextVar(
    "headspace_radau_stiffness",
    default=None,
)
_RADAU_NORM = None
_RADAU_STEP_CODE = None
_RADAU_ERROR_LINES: tuple[int, int] | None = None
_RADAU_ERROR_LINES_READY = False
# The unwrapped collocation solver. Newton misses are counted through
# the active recorder. The headspace step decides whether that count
# is a stall. This name is never a copy of Radau's Newton loop.
_RADAU_COLLOCATION = None


def _prepare_radau_error_lines() -> tuple[int, int] | None:
    """Line numbers of Radau's two error-norm calls.

    The first is the error test. The second runs only when that test
    already failed once and still fails, and it replaces the error.
    A scipy that no longer has that pair records nothing: a missing
    count stays unset rather than a zero.
    """

    global _RADAU_NORM, _RADAU_STEP_CODE, _RADAU_ERROR_LINES
    global _RADAU_ERROR_LINES_READY
    _remember_radau_collocation()
    if _RADAU_ERROR_LINES_READY:
        return _RADAU_ERROR_LINES
    _RADAU_ERROR_LINES_READY = True
    import inspect
    import scipy.integrate._ivp.radau as radau

    current = radau.norm
    if current is _stiffness_norm:
        return None
    _RADAU_NORM = current
    _RADAU_STEP_CODE = radau.Radau._step_impl.__code__
    try:
        source, start = inspect.getsourcelines(radau.Radau._step_impl)
    except (OSError, TypeError):
        return None
    first = None
    second = None
    guard = False
    for offset, line in enumerate(source):
        text = line.strip()
        if text.startswith("if rejected and error_norm > 1"):
            guard = True
        if "norm(error / scale)" in text or "norm(error/scale)" in text:
            number = start + offset
            if first is None:
                first = number
            elif guard and second is None:
                second = number
    if first is None or second is None:
        return None
    _RADAU_ERROR_LINES = (first, second)
    return _RADAU_ERROR_LINES


def _remember_radau_collocation() -> None:
    """Keep the original collocation solver so the count can wrap it."""

    global _RADAU_COLLOCATION
    if _RADAU_COLLOCATION is not None:
        return
    import scipy.integrate._ivp.radau as radau

    current = radau.solve_collocation_system
    if current is _stiffness_collocation:
        return
    _RADAU_COLLOCATION = current


def _stiffness_collocation(*args, **kwargs):
    """radau.solve_collocation_system while a slow solve is counting.

    A false converged flag is one Newton miss. The wrapper does not
    change the step and does not decide the stall. Nested solves share
    this one wrapper; the context variable selects the recorder, so a
    miss is counted once.
    """

    recorder = _ACTIVE_STIFFNESS.get()
    if recorder is None or not recorder.capture_reduced_diagnostics:
        result = _RADAU_COLLOCATION(*args, **kwargs)
        if isinstance(result, tuple) and result and result[0] is False:
            if recorder is not None:
                recorder.note_newton_failure()
        return result

    positional = list(args)
    keyword = dict(kwargs)
    if len(positional) >= 10:
        scale = positional[5]
        solve_lu = positional[9]
        use_positional_solver = True
    elif "scale" in keyword and "solve_lu" in keyword:
        scale = keyword["scale"]
        solve_lu = keyword["solve_lu"]
        use_positional_solver = False
    else:
        result = _RADAU_COLLOCATION(*args, **kwargs)
        recorder.mark_failed()
        if isinstance(result, tuple) and result and result[0] is False:
            recorder.note_newton_failure()
        return result

    solved_increments = []

    def capture_solve(lu, rhs):
        increment = solve_lu(lu, rhs)
        solved_increments.append(increment)
        return increment

    if use_positional_solver:
        positional[9] = capture_solve
        result = _RADAU_COLLOCATION(*positional, **keyword)
    else:
        keyword["solve_lu"] = capture_solve
        result = _RADAU_COLLOCATION(*positional, **keyword)
    if isinstance(result, tuple) and result and result[0] is False:
        recorder.note_newton_failure(
            result=result,
            scale=scale,
            increments=solved_increments[-2:],
        )
    return result


def _stiffness_norm(vector):
    """radau.norm while a slow solve is recording error tests.

    Newton convergence uses the same name on a stage matrix. Only the
    one-dimensional call inside Radau._step_impl is an error test.
    """

    value = _RADAU_NORM(vector)
    recorder = _ACTIVE_STIFFNESS.get()
    if recorder is None:
        return value
    try:
        recorder.observe(sys._getframe(1), vector, value)
    except Exception:
        recorder.mark_failed()
    return value


class _RadauNormTape:
    """Install the error-norm hook for one slow solve and restore it."""

    def __init__(self, recorder: "_StiffnessRecord") -> None:
        self._recorder = recorder
        self._token = None
        self._previous = None
        self._previous_collocation = None

    def __enter__(self) -> "_RadauNormTape":
        import scipy.integrate._ivp.radau as radau

        _prepare_radau_error_lines()
        self._previous = radau.norm
        self._previous_collocation = radau.solve_collocation_system
        self._token = _ACTIVE_STIFFNESS.set(self._recorder)
        radau.norm = _stiffness_norm
        if _RADAU_COLLOCATION is not None:
            radau.solve_collocation_system = _stiffness_collocation
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        import scipy.integrate._ivp.radau as radau

        if self._previous is not None:
            radau.norm = self._previous
        if self._previous_collocation is not None:
            radau.solve_collocation_system = self._previous_collocation
        if self._token is not None:
            _ACTIVE_STIFFNESS.reset(self._token)
        return False


class _StiffnessRecord:
    """Accepted steps and error-test rejections for one Radau call."""

    def __init__(
        self,
        species,
        block_names,
        *,
        capture_reduced_diagnostics: bool = False,
    ) -> None:
        self.species = tuple(species)
        self.block_names = tuple(block_names)
        self.count = len(self.species)
        self.capture_reduced_diagnostics = bool(
            capture_reduced_diagnostics
        )
        self.steps: deque = deque(maxlen=_STIFFNESS_STEPS)
        self.dominators: deque = deque(maxlen=_STIFFNESS_REJECTIONS)
        self.rejected = 0
        self.last_y = None
        self.newton_failures = 0
        self.error_test_top3_component_counts: dict[str, int] = {}
        self.newton_failure_diagnostics: list[dict[str, Any]] = []
        self._pending = False
        self._drive_crossings_since_error_test: set[str] = set()
        self._pending_drive_crossing_species: set[str] = set()
        self.rejected_with_drive_crossing = 0
        self.rejected_drive_crossing_species_counts: dict[str, int] = {}
        self._failed = False
        self._lines = _prepare_radau_error_lines()
        self._unavailable = self._lines is None or _RADAU_STEP_CODE is None

    def mark_failed(self) -> None:
        self._failed = True
        self._pending = False

    def begin_step(self) -> None:
        self._pending = False
        self._drive_crossings_since_error_test.clear()
        self._pending_drive_crossing_species.clear()

    def note_drive_crossings(self, species_names) -> None:
        self._drive_crossings_since_error_test.update(
            str(name) for name in species_names
        )

    def _record_rejected_drive_crossings(self, species_names) -> None:
        names = set(species_names)
        if not names:
            return
        self.rejected_with_drive_crossing += 1
        for name in names:
            self.rejected_drive_crossing_species_counts[name] = (
                self.rejected_drive_crossing_species_counts.get(name, 0) + 1
            )

    def note_newton_failure(
        self,
        *,
        result=None,
        scale=None,
        increments=(),
    ) -> None:
        self.newton_failures += 1
        crossings = set(self._drive_crossings_since_error_test)
        self._drive_crossings_since_error_test.clear()
        self._record_rejected_drive_crossings(crossings)
        if not self.capture_reduced_diagnostics:
            return
        try:
            self.newton_failure_diagnostics.append(
                self._newton_failure_diagnostic(result, scale, increments)
            )
        except Exception:
            self.mark_failed()

    def _newton_failure_diagnostic(self, result, scale, increments):
        import numpy as np

        rate = result[3] if isinstance(result, tuple) and len(result) > 3 else None
        if rate is not None:
            rate = float(rate)
            if not math.isfinite(rate):
                rate = None
        iterations = (
            int(result[1])
            if isinstance(result, tuple) and len(result) > 1
            else None
        )
        components = []
        if len(increments) == 2 and scale is not None:
            real = np.asarray(increments[0], dtype=float).reshape(-1)
            complex_part = np.asarray(increments[1], dtype=complex).reshape(-1)
            widths = np.asarray(scale, dtype=float).reshape(-1)
            if (
                real.size == complex_part.size == widths.size
                and real.size > 0
                and np.isfinite(widths).all()
                and np.all(widths > 0.0)
            ):
                rows = np.vstack((real, complex_part.real, complex_part.imag))
                scores = np.max(np.abs(rows) / widths[None, :], axis=0)
                indices = np.argsort(scores)[::-1][:3]
                for index in indices:
                    named = self._name(int(index))
                    score = float(scores[index])
                    if named is None or not math.isfinite(score) or score <= 0.0:
                        continue
                    species_name, state_name = named
                    components.append({
                        "species": species_name,
                        "state": state_name,
                        "scaled_increment": score,
                    })
        return {
            "convergence_rate": rate,
            "iterations": iterations,
            "dominant_components": components,
        }

    def accept(self, step_s: float, y) -> None:
        import numpy as np

        step = float(step_s)
        if not math.isfinite(step) or step < 0.0:
            return
        self.steps.append(step)
        self.last_y = np.array(y, dtype=float, copy=True)

    def observe(self, frame, vector, value) -> None:
        if self._failed or self._unavailable:
            return
        try:
            self._observe(frame, vector, float(value))
        except Exception:
            self.mark_failed()

    def _observe(self, frame, vector, value: float) -> None:
        if frame.f_code is not _RADAU_STEP_CODE:
            return
        if getattr(vector, "ndim", None) != 1:
            return
        first, second = self._lines
        lineno = frame.f_lineno
        rejected = bool(frame.f_locals.get("rejected", False))
        if lineno == second:
            pending = self._pending
            self._pending = False
            crossings = self._pending_drive_crossing_species | (
                self._drive_crossings_since_error_test
            )
            self._drive_crossings_since_error_test.clear()
            if pending and value > 1.0:
                self._commit(frame, vector)
                self._record_rejected_drive_crossings(crossings)
            self._pending_drive_crossing_species.clear()
            return
        if lineno != first:
            return
        crossings = set(self._drive_crossings_since_error_test)
        self._drive_crossings_since_error_test.clear()
        if value > 1.0 and rejected:
            self._pending = True
            self._pending_drive_crossing_species = crossings
            return
        self._pending_drive_crossing_species.clear()
        if value > 1.0:
            self._commit(frame, vector)
            self._record_rejected_drive_crossings(crossings)

    def _name(self, index: int):
        count = self.count
        if count <= 0:
            return None
        block = index // count
        species_index = index % count
        if block >= len(self.block_names) or species_index >= count:
            return None
        return self.species[species_index], self.block_names[block]

    def _commit(self, frame, vector) -> None:
        import numpy as np

        y_new = frame.f_locals.get("y_new")
        scale = frame.f_locals.get("scale")
        if y_new is None or scale is None:
            self.mark_failed()
            return
        weighted = np.asarray(vector, dtype=float).reshape(-1)
        proposed = np.asarray(y_new, dtype=float).reshape(-1)
        denom = np.asarray(scale, dtype=float).reshape(-1)
        if (
            weighted.size == 0
            or weighted.size != proposed.size
            or weighted.size != denom.size
        ):
            self.mark_failed()
            return
        index = int(np.argmax(np.abs(weighted)))
        named = self._name(index)
        if named is None:
            self.mark_failed()
            return
        component = float(proposed[index])
        weight = float(weighted[index])
        width = float(denom[index])
        if not (
            math.isfinite(component)
            and math.isfinite(weight)
            and math.isfinite(width)
        ):
            self.mark_failed()
            return
        if self.capture_reduced_diagnostics:
            order = np.argsort(np.abs(weighted))[::-1][:3]
            for rank, component_index in enumerate(order, start=1):
                if not math.isfinite(float(weighted[component_index])):
                    continue
                if float(weighted[component_index]) == 0.0:
                    continue
                component_name = self._name(int(component_index))
                if component_name is None:
                    self.mark_failed()
                    return
                component_species, component_state = component_name
                label = f"{rank}:{component_state}:{component_species}"
                self.error_test_top3_component_counts[label] = (
                    self.error_test_top3_component_counts.get(label, 0) + 1
                )
        species_name, block = named
        self.rejected += 1
        self.dominators.append({
            "species": species_name,
            "block": block,
            "value": component,
            "weighted_error": weight,
            "scale": width,
        })

    def payload(self) -> dict[str, Any]:
        steps = [float(step) for step in self.steps]
        if self._failed or self._unavailable:
            return {
                "accepted_step_sizes_s": steps,
                "rejected_steps": None,
                "rejected_error_dominators": None,
                "jacobian_abs_eigenvalue_min": None,
                "jacobian_abs_eigenvalue_max": None,
            }
        return {
            "accepted_step_sizes_s": steps,
            "rejected_steps": self.rejected,
            "rejected_error_dominators": [dict(row) for row in self.dominators],
            "jacobian_abs_eigenvalue_min": None,
            "jacobian_abs_eigenvalue_max": None,
        }


def _jacobian_abs_eigenvalue_span(jacobian, y):
    """Largest and smallest |eigenvalue| of the production Jacobian.

    One finite-difference evaluation at the last accepted state.
    A non-finite matrix is unset, not a zero.
    """

    import numpy as np

    matrix = np.asarray(jacobian(0.0, y), dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1]:
        return None
    if not np.isfinite(matrix).all():
        return None
    try:
        values = np.linalg.eigvals(matrix)
    except np.linalg.LinAlgError:
        return None
    magnitudes = np.abs(values[np.isfinite(values)])
    if magnitudes.size == 0:
        return None
    return float(magnitudes.min()), float(magnitudes.max())


def _store_stiffness(law, recorder: _StiffnessRecord, jacobian) -> None:
    """Attach the tape. A Jacobian that hits the cap leaves |λ| unset."""

    diagnostic = recorder.payload()
    if recorder.last_y is not None:
        # The extra Jacobian calls the flux. A cap or a flux refusal
        # here must not replace the work-cap refusal already in hand.
        try:
            span = _jacobian_abs_eigenvalue_span(jacobian, recorder.last_y)
        except Exception:
            span = None
        if span is not None:
            diagnostic["jacobian_abs_eigenvalue_min"] = span[0]
            diagnostic["jacobian_abs_eigenvalue_max"] = span[1]
    law.stiffness_diagnostic = diagnostic


def _remember_radau_tape(law, recorder: _StiffnessRecord) -> None:
    """Keep the Radau step tape. Does not evaluate the Jacobian.

    The work cap's eigenvalue span is another flux evaluation. On a
    nonconvergence that call would move the last drive snapshot and
    the flux counters, so the tape is stored without it. |λ| stays
    unset. A tape the work cap already stored is left as it is.
    """

    if law.stiffness_diagnostic is not None:
        return
    law.stiffness_diagnostic = recorder.payload()


def _recording_radau(
    recorder: _StiffnessRecord | None = None,
    on_step=None,
):
    """Radau that keeps each accepted step on this solve's tape.

    A recorder is optional for a reduced solve that only needs the
    accepted-step callback and does not publish a stiffness tape.
    When passed, the recorder sees an accepted step before ``on_step``;
    that callback may raise. Newton misses are not decided here.
    """

    from scipy.integrate._ivp.radau import Radau

    class _RecordingRadau(Radau):
        def _step_impl(self):
            if recorder is not None:
                recorder.begin_step()
                fails_before = recorder.newton_failures
            else:
                fails_before = 0
            accepted, message = Radau._step_impl(self)
            if recorder is not None and accepted:
                recorder.accept(float(self.t - self.t_old), self.y)
            if on_step is not None:
                on_step(self, accepted, fails_before, message)
            return accepted, message

    return _RecordingRadau


def _solve_open_crossing(
    value_at: Callable[[float], float],
    piece_at: Callable[[float], tuple],
    crossing: tuple[str, float, float],
    downstream: float,
) -> float | None:
    """One open root. A work cap still propagates. A bad bracket does not."""

    try:
        root, _iterations = _solve_bracket(
            value_at,
            piece_at,
            crossing,
            downstream,
        )
    except _BalanceRefused as refused:
        if refused.reason == "headspace_balance_work_cap":
            raise
        return None
    if not math.isfinite(root) or root <= downstream:
        return None
    return float(root)


def _pressure_root_attracts(
    value_at: Callable[[float], float],
    root: float,
    downstream: float,
) -> bool:
    """True when G falls through the root, so the pressure mode returns to it."""

    span = max(abs(root) * 1.0e-6, 1.0e-9)
    down = min(span, (root - downstream) * 0.5)
    if down <= 0.0:
        return False
    below = value_at(root - down)
    above = value_at(root + span)
    return (
        math.isfinite(below)
        and math.isfinite(above)
        and below > 0.0
        and above < below
    )


def _continuous_open_root(
    roots: list[float],
    pressure: float,
    residual_at_pressure: float,
) -> float | None:
    """The attracting root the integrated pressure is moving toward.

    G > 0 fills, so the pressure rises toward the next root at or
    above it. G < 0 drains, so the next root is at or below it. A
    root inside the kink band of the state is the one already in
    hand. No root on that side means neither root is reachable.
    """

    if not roots or not math.isfinite(pressure) or not math.isfinite(
        residual_at_pressure
    ):
        return None
    band = _KINK_BAND_PA
    if residual_at_pressure > 0.0:
        ahead = [root for root in roots if root >= pressure - band]
    elif residual_at_pressure < 0.0:
        ahead = [root for root in roots if root <= pressure + band]
    else:
        ahead = list(roots)
    if not ahead:
        return None
    return min(ahead, key=lambda root: abs(root - pressure))


def _warm_started_open_root(
    value_at: Callable[[float], float],
    piece_at: Callable[[float], tuple],
    guess: float,
    downstream: float,
) -> float | None:
    """The open root Newton reaches from the previous one.

    The caller has already found G(P_down) > 0. The reduced step and
    the stall check hold a pressure next to P*. A damped Newton step
    that stays inside the kink band, then Brent on the sign change it
    finds, is that same root. Brent publishes, as it does on the scan.

    The band is the continuity cut a second root must lie inside to
    be the one the pressure has reached. A root outside it, a step
    that leaves the piece, a window with no sign change, or a root
    the pressure mode does not fall through is not this commit: the
    bracket still publishes. A guess at or below P_down is the scan.
    """

    if not (math.isfinite(guess) and guess > downstream):
        return None
    half = _KINK_BAND_PA
    lo = max(guess - half, downstream)
    hi = guess + half
    if not hi > lo:
        return None
    proposed = _newton_propose(value_at, piece_at, guess, lo, hi)
    center = float(guess if proposed is None else proposed)
    if not (lo <= center <= hi):
        return None
    try:
        g_center = value_at(center)
    except _BalanceRefused:
        raise
    if not math.isfinite(g_center):
        return None
    if g_center == 0.0:
        root = center
    else:
        span = max(abs(center) * 1.0e-8, 1.0e-12)
        left_g = g_center
        right_g = g_center
        bracket: tuple[float, float] | None = None
        root = center
        for _expand in range(16):
            left = max(lo, center - span)
            right = min(hi, center + span)
            try:
                if left < center:
                    left_g = value_at(left)
                if right > center:
                    right_g = value_at(right)
            except _BalanceRefused:
                raise
            if not math.isfinite(left_g) or not math.isfinite(right_g):
                return None
            if left_g == 0.0:
                root = left
                bracket = None
                break
            if right_g == 0.0:
                root = right
                bracket = None
                break
            if left_g * g_center < 0.0:
                bracket = (left, center)
                break
            if g_center * right_g < 0.0:
                bracket = (center, right)
                break
            if left <= lo and right >= hi:
                return None
            span *= 4.0
        else:
            return None
        if bracket is not None:
            try:
                root, _iterations = _brent(value_at, bracket[0], bracket[1])
            except _BalanceRefused as refused:
                if refused.reason == "headspace_balance_work_cap":
                    raise
                return None
    if not (math.isfinite(root) and root > downstream):
        return None
    if not _pressure_root_attracts(value_at, root, downstream):
        return None
    return float(root)


def _sign_change_root(
    value_at: Callable[[float], float],
    lo: float,
    hi: float,
) -> float | None:
    """One root of a smooth piece. No sign change is not a root.

    Brent stays inside [lo, hi]. The caller keeps that interval on
    one piece, so a jump is not a root. A work cap still propagates.
    Any other refusal is not a root of this piece.
    """

    if not hi > lo:
        return None
    try:
        low = value_at(lo)
        high = value_at(hi)
    except _BalanceRefused as refused:
        if refused.reason == "headspace_balance_work_cap":
            raise
        return None
    if not (math.isfinite(low) and math.isfinite(high)):
        return None
    if low == 0.0:
        return float(lo)
    if high == 0.0:
        return float(hi)
    if low * high > 0.0:
        return None
    try:
        root, _iterations = _brent(value_at, lo, hi)
    except _BalanceRefused as refused:
        if refused.reason == "headspace_balance_work_cap":
            raise
        return None
    if not math.isfinite(root):
        return None
    return float(root)


def _piece_jump(
    piece_at: Callable[[float], tuple],
    left: float,
    right: float,
) -> tuple[float, float] | None:
    """The pressures that bound one piece change, or None if there is none.

    The left pressure keeps the left piece. The right pressure is the
    other piece. The gap is at most 1e-12 Pa, under the 1e-9 Pa root
    dedup. Unit: both pressures are Pa.
    """

    left_piece = piece_at(left)
    if left_piece == piece_at(right):
        return None
    bound_lo = left
    bound_hi = right
    for _step in range(60):
        if bound_hi - bound_lo <= 1.0e-12:
            break
        mid = 0.5 * (bound_lo + bound_hi)
        if piece_at(mid) == left_piece:
            bound_lo = mid
        else:
            bound_hi = mid
    return bound_lo, bound_hi


def _continuous_warm_root(
    value_at: Callable[[float], float],
    piece_at: Callable[[float], tuple],
    guess: float,
    downstream: float,
    warmed: float,
) -> tuple[str, float | None]:
    """Accept the warm root only when the continuity rule selects it.

    Newton refuses a step that leaves the piece. A second attractor
    on the other side of a jump inside the kink band is then
    invisible, and the same-piece root would be the commit. A window
    with one piece at both ends and at the warm root has no such
    jump: the warm root is the commit, and the window is not scanned.
    A window whose piece changes is split at the jump. Each smooth
    side is Brent's only when its residual changes sign. The
    continuity rule then picks among those attractors.

    "accept" keeps the warm root. "replace" is the other attractor
    the rule selects. "refuse" is attractors with none on the side
    the residual is moving. "fallthrough" leaves the geometric
    bracket to publish.

    The closest attractor the rule can select lies inside the kink
    band of the guess. An attractor outside that band is farther
    than one inside it, so the window is the whole candidate set.
    """

    half = _KINK_BAND_PA
    lo = max(float(guess) - half, float(downstream))
    hi = float(guess) + half
    if not hi > lo or not (lo <= warmed <= hi):
        return "fallthrough", None
    if piece_at(lo) == piece_at(hi) == piece_at(warmed):
        return "accept", float(warmed)
    points = [lo]
    if lo < warmed < hi:
        points.append(float(warmed))
    points.append(hi)
    intervals: list[tuple[float, float]] = []
    cursor = lo
    for left, right in zip(points, points[1:]):
        if not right > left:
            continue
        jump = _piece_jump(piece_at, left, right)
        if jump is None:
            continue
        bound_lo, bound_hi = jump
        if bound_lo > cursor:
            intervals.append((cursor, bound_lo))
        cursor = max(cursor, bound_hi)
    if hi > cursor:
        intervals.append((cursor, hi))
    roots: list[float] = []

    def _keep(root: float | None) -> None:
        if root is None or not math.isfinite(root) or root <= downstream:
            return
        if any(abs(root - earlier) <= 1.0e-9 for earlier in roots):
            return
        roots.append(float(root))

    for start, stop in intervals:
        _keep(_sign_change_root(value_at, start, stop))
    _keep(float(warmed))
    attractors = [
        root
        for root in roots
        if _pressure_root_attracts(value_at, root, downstream)
    ]
    if not attractors:
        return "fallthrough", None
    chosen = _continuous_open_root(
        attractors, float(guess), value_at(float(guess)),
    )
    if chosen is None:
        return "refuse", None
    if abs(chosen - float(warmed)) <= 1.0e-9:
        return "accept", float(warmed)
    return "replace", float(chosen)


def _open_or_shut_fixed_point(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    near_Pa: float | None = None,
) -> tuple[str | None, float | None]:
    """Bracket q + S − Q on one composition.

    ("shut", None) when the net source at P_down is <= 0: the fixed
    point is the shut branch. ("open", P*) is the crossing with
    P* > P_down. One open crossing is that root. Two open crossings
    keep the root continuous with `near_Pa`, the pressure the
    integral has already reached, and flag the hour. Neither of
    those roots lying on the side the residual is moving toward is
    the multiple-root refusal: the engine cannot pick a value.
    (None, None) is not a bracket this hour can hold. The fast-root
    admission test is not this decision, and the fast scan still
    refuses two roots before an integral starts.
    """

    downstream = law.downstream_pa
    if len(law.species) == 1:
        def value_at(pressure: float) -> float:
            return law._scalar_g(pressure)

        def piece_at(pressure: float) -> tuple:
            return law.piece_at(pressure)
    else:
        def value_at(pressure: float) -> float:
            return _mixture_g(law, fractions, pressure)

        def piece_at(pressure: float) -> tuple:
            return _mixture_piece(law, fractions, pressure)

    # One solve, one bucket. The warm Newton, the geometric Brent,
    # and the sign scan are mutually exclusive. A shut return, or no
    # bracket, is none of them. The scan counter moves inside
    # _remember_sign_scan_fallback while _counting_pstar is set.
    law._counting_pstar = True
    law._pstar_scan_seen = False
    scan_before = law.pstar_sign_scan

    def _count_brent(root: float) -> tuple[str, float]:
        if law.pstar_sign_scan == scan_before:
            law.pstar_brent_band += 1
        return "open", root

    try:
        if value_at(downstream) <= 0.0:
            return "shut", None
        # The scan is the publisher when this neighborhood has no root.
        # A warm hit is that root only when the continuity rule selects
        # it. Newton will not cross a jump, so the other attractor is
        # checked here, inside the kink band, and not on the grid.
        if near_Pa is not None:
            warmed = _warm_started_open_root(
                value_at,
                piece_at,
                float(near_Pa),
                downstream,
            )
            if warmed is not None:
                decision, chosen = _continuous_warm_root(
                    value_at,
                    piece_at,
                    float(near_Pa),
                    downstream,
                    float(warmed),
                )
                if decision == "accept":
                    law.pstar_warm_newton += 1
                    return "open", warmed
                if decision == "replace" and chosen is not None:
                    law._remember_species_notices((
                        {
                            "species": "headspace",
                            "reason": "headspace_balance_multiple_roots",
                            "warm_root_Pa": float(warmed),
                            "root_Pa": chosen,
                        },
                    ))
                    return _count_brent(chosen)
                if decision == "refuse":
                    raise _BalanceRefused("headspace_balance_multiple_roots")
        if len(law.species) == 1:
            samples = law.g_samples(refuse_multiple=False)
        else:
            samples = _ray_samples(law, fractions, refuse_multiple=False)
        open_crossings = [
            crossing
            for crossing in _crossings(samples)
            if max(crossing[1], crossing[2]) > downstream
        ]
        if len(open_crossings) == 0:
            return None, None
        roots: list[float] = []
        for crossing in open_crossings:
            root = _solve_open_crossing(
                value_at, piece_at, crossing, downstream,
            )
            if root is None:
                continue
            if all(abs(root - earlier) > 1.0e-9 for earlier in roots):
                roots.append(root)
        if len(roots) == 0:
            return None, None
        if len(roots) == 1 and len(open_crossings) == 1:
            return _count_brent(roots[0])
        attractors = [
            root
            for root in roots
            if _pressure_root_attracts(value_at, root, downstream)
        ]
        anchor = downstream if near_Pa is None else float(near_Pa)
        chosen = _continuous_open_root(
            attractors, anchor, value_at(anchor),
        )
        if chosen is None:
            raise _BalanceRefused("headspace_balance_multiple_roots")
        law._remember_species_notices((
            {
                "species": "headspace",
                "reason": "continuous_root",
                "root_Pa": chosen,
                "discarded_roots": len(roots) - 1,
            },
        ))
        return _count_brent(chosen)
    finally:
        law._counting_pstar = False


def _reduced_open_fixed_point(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    hint_Pa: float,
) -> tuple[str | None, float | None]:
    """Advance P* on the continuous open branch; bracket on a miss."""

    downstream = law.downstream_pa

    def resolve_fallback() -> tuple[str | None, float | None]:
        warm_before = law.pstar_warm_newton
        brent_before = law.pstar_brent_band
        scan_before = law.pstar_sign_scan
        resolved = None
        try:
            resolved = _open_or_shut_fixed_point(
                law, fractions, near_Pa=hint_Pa,
            )
            return resolved
        finally:
            law.reduced_resolver_warm_root_fallbacks += (
                law.pstar_warm_newton - warm_before
            )
            law.reduced_resolver_bracket_fallbacks += (
                law.pstar_brent_band - brent_before
                + law.pstar_sign_scan - scan_before
            )
            if resolved is not None and resolved[0] == "shut":
                law.reduced_shut_branch_hits += 1

    def open_derivative(pressure: float, residual: float) -> float | None:
        # Implicit-function premise: G(P*, n) = 0, so
        # dP*/dn_j = -(∂G/∂n_j)/(∂G/∂P). Difference the same species-level
        # G used by root selection backward on the open piece. Cap h at
        # half the distance to P_down so both pressure samples stay strictly
        # above the pipe kink. Each G sample's Q is the production pipe law
        # through `_flows`; divide by the actual pressure span. Unit check:
        # (mol/s) / (mol/(s·Pa)) = Pa. Constant source and locally linear Q
        # give a linear G, so one correction lands the closed-form root.
        step = max(abs(float(pressure)) * 1.0e-6, 1.0e-9)
        step = min(
            step,
            0.25 * _KINK_BAND_PA,
            0.5 * (float(pressure) - downstream),
        )
        lower = float(pressure) - step
        if not (step > 0.0 and lower > downstream):
            return None
        residual_lower = _mixture_g(law, fractions, lower)
        derivative_at_pressure = (residual - residual_lower) / step
        if not (
            math.isfinite(derivative_at_pressure)
            and derivative_at_pressure < 0.0
        ):
            return None
        return derivative_at_pressure

    if not (math.isfinite(hint_Pa) and hint_Pa > downstream):
        return resolve_fallback()

    residual_down = _mixture_g(law, fractions, downstream)
    if residual_down <= 0.0:
        # The existing resolver owns the shut return and its diagnostics.
        return resolve_fallback()

    residual = _mixture_g(law, fractions, hint_Pa)
    if residual == 0.0:
        corrected = float(hint_Pa)
        derivative = None
    else:
        derivative = open_derivative(float(hint_Pa), residual)
        if derivative is None:
            return resolve_fallback()
        corrected = float(hint_Pa) - residual / derivative

    if (
        not math.isfinite(corrected)
        or corrected <= downstream
        or abs(corrected - float(hint_Pa)) > _KINK_BAND_PA
    ):
        # The established bracket remains the publisher outside the
        # local IFT neighborhood, including multiple-root handling.
        return resolve_fallback()

    if derivative is not None:
        # The reduced Radau species atol maps to _SLOW_ATOL_PA because
        # atol_mol · R T / V = 1e-7 Pa. Keep the root error 1000x below
        # that outer tolerance: |δP| ≈ |G|/|dG/dP| <= 1e-3 · atol,
        # with four ulps as the binary64 floor. G is mol/s and dG/dP is
        # mol/(s·Pa), so this ratio is Pa. Recompute the one-sided open
        # slope after each correction so curvature is not left in the
        # frozen-anchor chord estimate. A linear G (constant source and
        # linear pipe flow) reaches its root in the first correction.
        converged = False
        for correction_index in range(3):
            corrected_residual = _mixture_g(law, fractions, corrected)
            if not math.isfinite(corrected_residual):
                return resolve_fallback()
            if corrected_residual == 0.0:
                converged = True
                break

            derivative = open_derivative(float(corrected), corrected_residual)
            if derivative is None:
                return resolve_fallback()

            correction_Pa = -corrected_residual / derivative
            correction_tolerance_Pa = max(
                1.0e-3 * _SLOW_ATOL_PA,
                4.0 * math.ulp(float(corrected)),
            )
            if abs(correction_Pa) <= correction_tolerance_Pa:
                converged = True
                break
            law.reduced_ift_residual_check_rejections += 1
            if correction_index == 2:
                return resolve_fallback()
            law.reduced_second_corrections += 1
            candidate = float(corrected) + correction_Pa
            if (
                not math.isfinite(candidate)
                or candidate <= downstream
                or abs(candidate - float(hint_Pa)) > _KINK_BAND_PA
            ):
                return resolve_fallback()
            corrected = candidate

        if not converged:
            return resolve_fallback()

    decision, continuous = _continuous_warm_root(
        lambda pressure: _mixture_g(law, fractions, pressure),
        lambda pressure: _mixture_piece(law, fractions, pressure),
        float(hint_Pa),
        downstream,
        corrected,
    )
    if decision == "accept" and continuous is not None:
        return "open", float(continuous)
    # Reuse the bracket publisher for a replacement attractor, a
    # multiple-root refusal, or a window with no continuous warm root.
    return resolve_fallback()


def _quasi_steady_tau(jacobian, state) -> float | None:
    """tau = 1/|λ|_max. Unset when the spectrum is not a relaxation."""

    span = _jacobian_abs_eigenvalue_span(jacobian, state)
    if span is None:
        return None
    lam_max = span[1]
    if not math.isfinite(lam_max) or lam_max <= 0.0:
        return None
    return 1.0 / lam_max


def _finish_downstream_remainder(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    landed: Mapping[str, float],
    added: Mapping[str, float],
    bled: Mapping[str, float],
    extent_kwargs: dict,
    t_hit: float,
    open_iterations: int,
    *,
    track_extent: bool,
) -> _HeadspaceStep:
    """Book the open prefix and finish the hour on the shut path.

    The event landing and a stall whose fixed point is shut share
    this tail. A source that would open the pipe again is not clamped
    onto the switch: the shut path returns None and the open integral
    continues from the landed holdup.
    """

    remain = float(dt_s) - float(t_hit)
    if remain <= 0.0:
        _mark_balance_path(law, "radau_fallback")
        _remember_balance_iterations(law, open_iterations)
        return law.step_from_moles(
            n0,
            dt_s,
            landed,
            open_iterations,
            _SLOW_ATOL_PA,
            added_mol=added,
            bled_mol_in=bled,
            **extent_kwargs,
        )
    shut = _closed_pipe_accumulation(law, landed, remain)
    if shut is None:
        rest = _slow_converge(law, landed, remain)
    else:
        rest = shut
    return _join_switch_remainder(
        law,
        n0,
        dt_s,
        landed,
        added,
        bled,
        extent_kwargs,
        rest,
        open_iterations,
        track_extent=track_extent,
    )


def _hold_shut_fixed_point(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    state,
    t_stall: float,
    *,
    track_extent: bool,
) -> _HeadspaceStep | None:
    """Scale the stall onto P_down and hand the remainder to the shut path.

    The event's projection refuses a root farther than 1e-6 Pa. A
    stall inside the kink band can be 1e-4 Pa out, so the moles are
    scaled onto P_down first. The projection then rewrites the bleed
    and keeps n = n0 + A − B. No quasi-steady notice: this is the
    shut handoff the event already publishes.
    """

    end, added, _bled, extent_kwargs = _integrated_holdup(
        law,
        n0,
        state,
        track_extent=track_extent,
    )
    target = law.downstream_pa
    pressure = law.pressure_of(end)
    if pressure != target:
        if (
            not math.isfinite(pressure)
            or pressure <= 0.0
            or abs(pressure - target) > _KINK_BAND_PA
        ):
            return None
        scale = target / pressure
        end = {name: float(end[name]) * scale for name in law.species}
    try:
        landed, bled = _project_onto_downstream(law, n0, end, added)
    except _BalanceRefused:
        return None
    return _finish_downstream_remainder(
        law,
        n0,
        dt_s,
        landed,
        added,
        bled,
        extent_kwargs,
        t_stall,
        max(int(law.integrator_evaluations), 1),
        track_extent=track_extent,
    )


class _ManifoldLost(Exception):
    """The reduced right-hand side could not re-solve P*."""


def _manifold_rates(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    pressure: float,
    *,
    counted: bool = True,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """s_i = q_i + S_i, the redox part, and the gross, at one total pressure."""

    partials = law.partials_of(_moles_along(law, fractions, pressure))
    source, _outflow, _binding, _piece = law._flows(
        partials,
        counted=counted,
    )
    redox = law._redox_rates(partials)
    inflow = {
        name: source[name] + redox[name] for name in law.species
    }
    redox_only = {name: float(redox[name]) for name in law.species}
    gross = {
        name: float(law._last_gross_kg_s.get(name, 0.0)) for name in law.species
    }
    return inflow, redox_only, gross


def _inflow_follows_total_pressure(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    pressure: float,
    inflow: Mapping[str, float],
    redox: Mapping[str, float],
    gross: Mapping[str, float],
) -> bool:
    """True when a second composition at the same P* returns the same s.

    One species has no composition to change. A mismatch, or a probe
    the flux will not evaluate, is the partial-dependent reduction.
    """

    count = len(law.species)
    if count <= 1:
        return True
    mixed = {
        name: 0.5 * max(fractions[name], 0.0) + 0.5 / count
        for name in law.species
    }
    total = math.fsum(mixed.values())
    if total <= 0.0:
        return False
    mixed = {name: mixed[name] / total for name in law.species}
    try:
        other, other_redox, other_gross = _manifold_rates(law, mixed, pressure)
    except _BalanceRefused:
        return False
    for name in law.species:
        if (
            other[name] != inflow[name]
            or other_redox[name] != redox[name]
            or other_gross[name] != gross[name]
        ):
            return False
    return True


def _closed_manifold(
    species: tuple[str, ...] | list[str],
    n_start: Mapping[str, float],
    inflow: Mapping[str, float],
    redox: Mapping[str, float],
    gross: Mapping[str, float],
    t_rem: float,
) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    float,
    float,
] | None:
    """n_i(t) and ∫ y_i Q dt when s_i is constant on the manifold."""

    n_tot = math.fsum(n_start[name] for name in species)
    q_sum = math.fsum(inflow[name] for name in species)
    if (
        n_tot <= 0.0
        or q_sum <= 0.0
        or not math.isfinite(n_tot)
        or not math.isfinite(q_sum)
    ):
        return None
    composition_time = n_tot / q_sum
    if not math.isfinite(composition_time) or composition_time <= 0.0:
        return None
    decay = math.exp(-t_rem / composition_time)
    if not math.isfinite(decay):
        return None
    n_end: dict[str, float] = {}
    added: dict[str, float] = {}
    bled: dict[str, float] = {}
    vapour: dict[str, float] = {}
    redox_added: dict[str, float] = {}
    for name in species:
        n0 = float(n_start[name])
        rate = float(inflow[name])
        n_inf = n_tot * rate / q_sum
        added[name] = rate * t_rem
        y0 = n0 / n_tot
        y_inf = rate / q_sum
        bled[name] = q_sum * (
            y_inf * t_rem
            + (y0 - y_inf) * composition_time * (1.0 - decay)
        )
        # The identity, not the pre-round exponential. One ulp in the
        # decay must not split n from n0 + A − B.
        n_end[name] = n0 + added[name] - bled[name]
        vapour[name] = float(gross[name]) * t_rem
        redox_added[name] = float(redox[name]) * t_rem
        if not all(
            math.isfinite(item)
            for item in (
                n_end[name],
                added[name],
                bled[name],
                vapour[name],
                redox_added[name],
            )
        ):
            return None
    n_end_tot = math.fsum(n_end.values())
    if n_end_tot <= 0.0:
        return None
    dy = max(
        abs(n_start[name] / n_tot - n_end[name] / n_end_tot)
        for name in species
    )
    return n_end, added, bled, vapour, redox_added, composition_time, dy


def _reduced_manifold(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    n_start: Mapping[str, float],
    root: float,
    t_rem: float,
    *,
    track_extent: bool,
) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    float,
    float,
] | None:
    """Integrate composition with Radau and book passive extents by quadrature."""

    import numpy as np
    from scipy.integrate import solve_ivp
    from scipy.integrate._ivp.radau import C as radau_nodes

    species = list(law.species)
    count = len(species)
    law.reduced_time_covered_s = 0.0
    y0 = [float(n_start[name]) for name in species]
    nodes = np.asarray(radau_nodes, dtype=float)
    powers = np.arange(nodes.size)
    # SciPy exposes C but not b. The moment equations Σ b_j c_j^k =
    # ∫₀¹ x^k dx = 1/(k+1), for k=0,1,2, recover the three IIA weights;
    # nodes and weights are dimensionless.
    moments = 1.0 / (powers + 1.0)
    quadrature_weights = np.linalg.solve(
        nodes[None, :] ** powers[:, None], moments,
    )
    added = {name: 0.0 for name in species}
    bled = {name: 0.0 for name in species}
    vapour = {name: 0.0 for name in species}
    redox_added = {name: 0.0 for name in species}
    extent_accumulators = [added, bled]
    if track_extent:
        extent_accumulators.extend((vapour, redox_added))
    accepted_root = [float(root)]
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    atol_mol = _SLOW_ATOL_PA / pa_per_mol
    recorder = _StiffnessRecord(
        species,
        ("n",),
        capture_reduced_diagnostics=True,
    )
    last_stage_drive_delta_Pa: list[dict[str, float] | None] = [None]

    def record_drive_sample(
        drive_delta_Pa: dict[str, float] | None,
    ) -> None:
        if drive_delta_Pa is None:
            return
        law.reduced_evaporation_drive_probe_evaluations += 1
        near_zero_species = {
            name
            for name, delta in drive_delta_Pa.items()
            if abs(delta) <= _SLOW_ATOL_PA
        }
        if near_zero_species:
            law.reduced_evaporation_drive_near_zero_evaluations += 1
            for name in near_zero_species:
                law.reduced_evaporation_drive_near_zero_species_counts[name] = (
                    law.reduced_evaporation_drive_near_zero_species_counts.get(
                        name, 0,
                    ) + 1
                )

    def crossing_species(
        before: dict[str, float] | None,
        after: dict[str, float] | None,
    ) -> set[str]:
        if before is None or after is None:
            return set()
        crossed: set[str] = set()
        for name in before.keys() & after.keys():
            left = before[name]
            right = after[name]
            if (
                (left < 0.0 < right)
                or (right < 0.0 < left)
                or (left == 0.0 and right != 0.0)
                or (right == 0.0 and left != 0.0)
            ):
                crossed.add(name)
        return crossed

    def record_crossings(
        counts: dict[str, int],
        before: dict[str, float] | None,
        after: dict[str, float] | None,
    ) -> None:
        crossed = crossing_species(before, after)
        for name in crossed:
            counts[name] = counts.get(name, 0) + 1
        if crossed:
            recorder.note_drive_crossings(crossed)

    def evaluate(state, root_hint: float):
        raw = [max(float(state[index]), 0.0) for index in range(count)]
        total = math.fsum(raw)
        if total <= 0.0:
            zero = [0.0] * count
            return (
                zero,
                tuple([0.0] * count for _unused in extent_accumulators),
                float(root_hint),
                None,
            )
        evolved = {name: raw[index] / total for index, name in enumerate(species)}
        kind, found = _reduced_open_fixed_point(
            law, evolved, hint_Pa=root_hint,
        )
        if kind != "open" or found is None:
            raise _ManifoldLost()
        inflow, redox, gross = _manifold_rates(law, evolved, float(found))
        drive_delta_Pa = law._last_evaporation_drive_delta_Pa
        record_drive_sample(drive_delta_Pa)
        q_sum = math.fsum(inflow.values())
        if not math.isfinite(q_sum):
            raise _ManifoldLost()
        dn: list[float] = []
        da: list[float] = []
        db: list[float] = []
        dg: list[float] = []
        dr: list[float] = []
        for index, name in enumerate(species):
            rate = inflow[name]
            removed = raw[index] / total * q_sum
            if not math.isfinite(rate) or not math.isfinite(removed):
                raise _ManifoldLost()
            dn.append(rate - removed)
            da.append(rate)
            db.append(removed)
            if track_extent:
                dg.append(gross[name])
                dr.append(redox[name])
        extent_rates = (da, db)
        if track_extent:
            extent_rates += (dg, dr)
        return dn, extent_rates, float(found), drive_delta_Pa

    def rhs_rates(_time: float, state, root_hint: float):
        law.reduced_rhs_calls += 1
        law._note_rhs_flux()
        rates, extent_rates, found, drive_delta_Pa = evaluate(state, root_hint)
        record_crossings(
            law.reduced_evaporation_drive_stage_crossing_counts,
            last_stage_drive_delta_Pa[0],
            drive_delta_Pa,
        )
        last_stage_drive_delta_Pa[0] = drive_delta_Pa
        return rates, extent_rates, found

    def rhs(_time: float, state) -> list[float]:
        rates, _extent_rates, _found = rhs_rates(
            _time, state, accepted_root[0],
        )
        return rates

    def jacobian(time: float, state):
        law.reduced_jacobian_builds += 1
        # Each composition column differences the reduced RHS once; that
        # RHS uses one IFT correction, not the bracketed warm solve.
        law._note_rhs_flux()
        base_hint = accepted_root[0]
        base_rates, _base_extents, _base_root, base_drive_delta_Pa = evaluate(
            state, base_hint,
        )
        base = np.asarray(base_rates, dtype=float)
        matrix = np.zeros((count, count), dtype=float)
        fd_scale = math.sqrt(np.finfo(float).eps)
        scale = max(
            math.fsum(max(float(state[i]), 0.0) for i in range(count)),
            1.0e-12,
        )
        for column in range(count):
            value = float(state[column])
            step = max(
                fd_scale * max(abs(value), scale * 1.0e-8),
                10.0 * atol_mol,
            )
            if value < 0.0:
                step = max(step, -value + 10.0 * atol_mol)
            sample = np.asarray(state, dtype=float).copy()
            sample[column] += step
            law._note_rhs_flux()
            perturbed, _sample_extents, _sample_root, sample_drive_delta_Pa = (
                evaluate(sample, base_hint)
            )
            record_crossings(
                law.reduced_evaporation_drive_jacobian_crossing_counts,
                base_drive_delta_Pa,
                sample_drive_delta_Pa,
            )
            matrix[:, column] = (
                np.asarray(perturbed, dtype=float) - base
            ) / step
        return matrix

    def accepted_step(solver, accepted, _fails_before, _message) -> None:
        if not accepted:
            return
        step_s = float(solver.t - solver.t_old)
        law.reduced_accepted_steps += 1
        law.reduced_step_sizes_s.append(step_s)
        law.reduced_time_covered_s = float(solver.t)
        raw = [max(float(solver.y[index]), 0.0) for index in range(count)]
        total = math.fsum(raw)
        if total <= 0.0:
            raise _ManifoldLost()
        evolved = {
            name: raw[index] / total for index, name in enumerate(species)
        }
        # The collocation polynomial for n is cubic and n' = dA-dB is
        # quadratic. At the three Radau IIA nodes n' = f; the quadrature is
        # exact for n', so h Σ b_j(dA-dB)_j = ∫n' dt = n_end-n_start.
        # dA,dB,dR are mol/s and dG is kg/s; multiplying by h seconds gives
        # mol for A/B/R and kg for G. If dA=dB=0, n_end=n_start and closure is 0.
        dense = solver.dense_output()
        stage_root_hint = accepted_root[0]
        for node, weight in zip(nodes, quadrature_weights):
            stage_time = float(solver.t_old) + float(node) * step_s
            stage_state = dense(stage_time)
            _dn, extent_rates, stage_root_hint = rhs_rates(
                stage_time, stage_state, stage_root_hint,
            )
            for accumulator, rates in zip(extent_accumulators, extent_rates):
                for index, name in enumerate(species):
                    accumulator[name] += step_s * float(weight) * rates[index]
        law._note_rhs_flux()
        kind, found = _reduced_open_fixed_point(
            law, evolved, hint_Pa=accepted_root[0],
        )
        if kind != "open" or found is None:
            raise _ManifoldLost()
        accepted_root[0] = float(found)

    atol = [atol_mol] * count
    try:
        with _RadauNormTape(recorder):
            solution = solve_ivp(
                rhs,
                (0.0, float(t_rem)),
                y0,
                method=_recording_radau(recorder, accepted_step),
                rtol=_SLOW_RTOL,
                atol=atol,
                jac=jacobian,
            )
    except _ManifoldLost:
        return None
    finally:
        # Include both local error-test rejections and failed Newton
        # collocation attempts; accepted step sizes are recorded above.
        law.reduced_rejected_steps = (
            recorder.rejected + recorder.newton_failures
        )
        if recorder._failed or recorder._unavailable:
            law.reduced_rejections_with_evaporation_drive_crossing = None
            law.reduced_rejection_evaporation_drive_crossing_species_counts = {}
            law.reduced_rejection_evaporation_drive_crossing_fraction = None
            law.reduced_error_test_rejections = None
            law.reduced_newton_nonconvergence_failures = None
            law.reduced_error_test_top3_component_counts = None
            law.reduced_newton_failure_diagnostics = None
        else:
            law.reduced_rejections_with_evaporation_drive_crossing = (
                recorder.rejected_with_drive_crossing
            )
            law.reduced_rejection_evaporation_drive_crossing_species_counts = (
                dict(recorder.rejected_drive_crossing_species_counts)
            )
            law.reduced_rejection_evaporation_drive_crossing_fraction = (
                recorder.rejected_with_drive_crossing
                / law.reduced_rejected_steps
                if law.reduced_rejected_steps > 0
                else None
            )
            law.reduced_error_test_rejections = recorder.rejected
            law.reduced_newton_nonconvergence_failures = (
                recorder.newton_failures
            )
            law.reduced_error_test_top3_component_counts = dict(
                sorted(recorder.error_test_top3_component_counts.items())
            )
            law.reduced_newton_failure_diagnostics = [
                dict(row) for row in recorder.newton_failure_diagnostics
            ]
    if not solution.success:
        return None
    end = solution.y[:, -1]
    n_end = {name: float(end[index]) for index, name in enumerate(species)}
    if not all(
        math.isfinite(value)
        for mapping in (n_end, added, bled, vapour, redox_added)
        for value in mapping.values()
    ):
        return None
    closure_error_pa = max(
        abs(
            (
                n_end[name]
                - float(n_start[name])
                - added[name]
                + bled[name]
            ) * pa_per_mol
        )
        for name in species
    )
    if closure_error_pa > DEFAULT_ABS_TOL_PA:
        return None
    start_inflow, _redox, _gross = _manifold_rates(law, fractions, root)
    q_sum = math.fsum(start_inflow.values())
    n_tot = math.fsum(n_start[name] for name in species)
    if q_sum <= 0.0 or n_tot <= 0.0:
        return None
    composition_time = n_tot / q_sum
    n_end_tot = math.fsum(n_end.values())
    if n_end_tot <= 0.0 or not math.isfinite(composition_time):
        return None
    end_fractions = _fractions_of(law, n_end)
    if end_fractions is None:
        return None
    end_kind, end_root = _open_or_shut_fixed_point(
        law, end_fractions, near_Pa=accepted_root[0],
    )
    inventory_pressure = law.pressure_of(n_end)
    if (
        end_kind != "open"
        or end_root is None
        or not math.isfinite(inventory_pressure)
        or abs(float(end_root) - inventory_pressure) > DEFAULT_ABS_TOL_PA
    ):
        # The caller declines the reduced result and keeps the full-system
        # Radau solve alive from its stall state. Its existing B = ∫Q dt
        # continues the bleed booking; no reduced extent is booked twice.
        return None
    dy = max(
        abs(n_start[name] / n_tot - n_end[name] / n_end_tot) for name in species
    )
    return n_end, added, bled, vapour, redox_added, composition_time, dy


def _slow_manifold(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    root: float,
    t_rem: float,
    *,
    track_extent: bool,
) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    float,
    float,
] | None:
    """Continue one stalled composition on the slow manifold.

    Two time scales. The total-pressure mode relaxes in
    tau = 1/|λ|_max. The composition mode relaxes in
    T_c = n_tot / Σ s. On the walked tape that is ~1e7 s, so freezing
    the stall composition drops the partials by millipascals over the
    hour. On the manifold the pressure is algebraic, pinned at the
    open root P*, and
      dn_i/dt = s_i(P*) − y_i Q(P*),
      y_i = n_i / n_tot,  Q(P*) = Σ_i s_i,
    with s_i = q_i + S_i. n_tot is constant because the two sums match.
    The pipe's species outflow is y_i Q. It is not an identity residual.

    Where s_i depends only on the total pressure, each species
    relaxes to the source composition:
      n_i(t) = n_i∞ + (n_i0 − n_i∞) exp(−t/T_c),
      n_i∞ = n_tot · s_i / Σ s,
      A_i = s_i t_rem,
      B_i = Q (y_i∞ t_rem + (y_i0 − y_i∞) T_c (1 − exp(−t_rem/T_c))).
    n_end = n0 + A − B by that algebra.

    Where s_i depends on the partials, the same reduced ODE is
    integrated with one solve_ivp. Each evaluation advances P* from
    the previous root with one IFT correction on the open side of the
    pipe kink. The existing bracket publishes a root only when that
    correction leaves its continuity band or cannot select one.

    Unit check: (mol/s)·s = mol, and n_tot / (mol/s) = s.
    Limit: s_i / Σ s = y_i0 leaves the composition fixed, so
    B_i = A_i and n_i(t) = n_i0. T_c ≫ t_rem is the linear drift
    n_i(t) ≈ n_i0 + (n_i∞ − n_i0) t / T_c. A non-positive Σ s, or a
    P* the reduced step cannot re-solve, is not this continuation.
    """

    n_start = _moles_along(law, fractions, root)
    try:
        inflow, redox, gross = _manifold_rates(law, fractions, root)
    except _BalanceRefused as refused:
        if refused.reason == "headspace_balance_work_cap":
            raise
        return None
    if any(
        not math.isfinite(value)
        for mapping in (inflow, redox, gross)
        for value in mapping.values()
    ):
        return None
    if math.fsum(inflow.values()) <= 0.0:
        return None
    if _inflow_follows_total_pressure(
        law,
        fractions,
        root,
        inflow,
        redox,
        gross,
    ):
        # The probe evaluated another composition and may have replaced
        # the gross. The closed form uses the stall composition.
        inflow, redox, gross = _manifold_rates(law, fractions, root)
        return _closed_manifold(
            law.species,
            n_start,
            inflow,
            redox,
            gross,
            t_rem,
        )
    return _reduced_manifold(
        law,
        fractions,
        n_start,
        root,
        t_rem,
        track_extent=track_extent,
    )


def _hold_open_fixed_point(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    state,
    t_stall: float,
    pressure: float,
    root: float,
    tau: float,
    fractions: Mapping[str, float],
    *,
    track_extent: bool,
) -> _HeadspaceStep | None:
    """Book the slow manifold over the time the stall left in the hour.

    The prefix is the integral Radau already accepted. The manifold
    adds A_i = ∫ s_i dt and B_i = ∫ y_i Q dt. n_end is the manifold
    end state, and the booked bleed is whatever makes
    n_end = n0 + A − B, including the projection from the stall
    pressure onto P*. A bleed below −1e-6 Pa is not that projection.
    The caller keeps integrating.
    """

    t_rem = float(dt_s) - float(t_stall)
    if not math.isfinite(t_rem) or t_rem <= 0.0:
        return None
    _end, added_prefix, _bled_prefix, extent_prefix = _integrated_holdup(
        law,
        n0,
        state,
        track_extent=track_extent,
    )
    law.handoff_time_s = float(t_stall)
    continued = _slow_manifold(
        law,
        fractions,
        root,
        t_rem,
        track_extent=track_extent,
    )
    if continued is None:
        return None
    n_end, added_hold, bled_hold, vapour_hold, redox_hold, composition_time, dy = (
        continued
    )
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    added: dict[str, float] = {}
    bled: dict[str, float] = {}
    for name in law.species:
        grown = float(added_prefix[name]) + float(added_hold[name])
        bleed = float(n0[name]) + grown - float(n_end[name])
        if bleed < 0.0 and -bleed * pa_per_mol > DEFAULT_ABS_TOL_PA:
            return None
        if bleed < 0.0:
            bleed = 0.0
        # Published n is this triple. Rebuilding it keeps the partial
        # on n0 + A − B when the bleed arithmetic sheds one ulp.
        n_end[name] = float(n0[name]) + grown - bleed
        added[name] = grown
        bled[name] = bleed
    extent_kwargs: dict = {}
    if track_extent:
        gross_prefix = extent_prefix["vapour_extent_kg"]
        redox_prefix = extent_prefix["redox_added_mol"]
        extent_kwargs = {
            "vapour_extent_kg": {
                name: float(gross_prefix[name]) + float(vapour_hold[name])
                for name in law.species
            },
            "redox_added_mol": {
                name: float(redox_prefix[name]) + float(redox_hold[name])
                for name in law.species
            },
        }
    gap = abs(float(root) - float(pressure))
    law._remember_species_notices((
        {
            "species": "headspace",
            "reason": "quasi_steady_fixed_point",
            "tau_s": float(tau),
            "composition_time_s": float(composition_time),
            "t_rem_s": t_rem,
            "abs_mole_fraction_change": float(dy),
            "abs_pressure_gap_Pa": gap,
            "tau_over_t_rem": float(tau) / t_rem,
        },
    ))
    iterations = max(int(law.integrator_evaluations), 1)
    _mark_balance_path(law, "radau_fallback")
    _remember_balance_iterations(law, iterations)
    return law.step_from_moles(
        n0,
        dt_s,
        n_end,
        iterations,
        gap,
        added_mol=added,
        bled_mol_in=bled,
        **extent_kwargs,
    )


def _hold_fixed_point(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    state,
    t_stall: float,
    pressure: float,
    kind: str,
    root: float | None,
    tau: float,
    fractions: Mapping[str, float],
    *,
    track_extent: bool,
) -> _HeadspaceStep | None:
    """Book the fixed point. A later step of this hour does not retry.

    The latch is set before the shut remainder, which may integrate.
    That remainder is at P_down and is not another open stall.
    """

    law._quasi_steady_declined = True
    if kind == "shut":
        return _hold_shut_fixed_point(
            law,
            n0,
            dt_s,
            state,
            t_stall,
            track_extent=track_extent,
        )
    if root is None:
        return None
    return _hold_open_fixed_point(
        law,
        n0,
        dt_s,
        state,
        t_stall,
        pressure,
        root,
        tau,
        fractions,
        track_extent=track_extent,
    )


def _slow_converge(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    """Integrate dn_i/dt = q_i + S_i - Q_i over dt_s.

    Radau IIA, order 5, L-stable, one step. A rejected step drops its
    stage history, so a regime kink (sonic edge, blend knot) does not
    leave a multistep stencil straddling the jump. BDF at rtol 1e-12
    is still ~1e-4 Pa high on the choked piece at megapascal pressure,
    because that tolerance times the state is larger than the 1e-6 Pa
    floor and the multistep does not beat it there. Radau at rtol 1e-12
    does. The achieved gap is the per-species partial, not the total.

    The Jacobian is a difference that stays on the current pipe piece.
    The mixture mean free path is continuous in mole fraction, so a
    step across equal moles is not a kink. A probe that leaves the
    piece shrinks until it does not, then takes the one-sided
    difference. Error control crosses a sonic edge or a blend knot;
    this difference does not have to.

    Premise: the booked bleed is the outflow the step removed. A source
    that changes inside the step is not the end rate times the whole
    step. Algebra, one extra state per species for each integral:
      dA_i/dt = q_i + S_i
      dB_i/dt = Q_i
      dn_i/dt = dA_i/dt − dB_i/dt
    so n_i(dt) = n0_i + A_i(dt) − B_i(dt) and the bled moles are
    B_i(dt). That identity is what is published, so the closure is
    exact in the booked moles, not only when q is constant.
    When the hour passed an extent holder, two more states ride the
    same step. G_i is the gross vapour in kilograms and R_i is S in
    moles. Neither enters dn/dt. Their columns stay zero.
      dG_i/dt = gross_i
      dR_i/dt = S_i
    A caller with no holder keeps the three-block state, so its step
    size is the one it had.
    atol on A, on B and on R is the same per-species mole tolerance
    as n. G's atol is _SLOW_EXTENT_ATOL_KG. A scalar atol would
    retune n.
    Unit check: (mol/s)·s = mol, (kg/s)·s = kg, and
    Pa·m³/(J/(mol·K)·K) = mol.
    Limit: Q = 0 and constant q gives A = (q + S) dt, B = 0, and
    n = n0 + A, a degree-1 polynomial this order integrates in one step.
    Constant gross gives G = gross·dt.
    """

    import numpy as np
    from scipy.integrate import solve_ivp

    _mark_balance_path(law, "radau_fallback")
    law.full_system_radau_time_covered_s = 0.0
    species = law.species
    count = len(species)
    # The holder is the hour. Other callers keep n, A, B only.
    track_extent = isinstance(law.extent_holder, dict)
    # n, A, B, and with a holder G then R. The tape names blocks from
    # this tuple, so the state width and the label are one list.
    block_names = ("n", "A", "B", "G", "R") if track_extent else ("n", "A", "B")
    blocks = len(block_names)
    # State is n, then A = ∫(q+S) dt, then B = ∫Q dt. A(0) = B(0) = 0.
    # With a holder, G = ∫gross dt (kg) and R = ∫S dt (mol) follow,
    # both starting at 0.
    y0 = (
        [float(n0[name]) for name in species]
        + [0.0] * (count * (blocks - 1))
    )
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    # Premise: each partial is held to 1e-7 Pa. Algebra: n = p V / (R T),
    # so the mole tolerance is that pressure times V / (R T). A, B and
    # R are moles, so they take that same tolerance. G is kilograms.
    atol_mol = _SLOW_ATOL_PA / pa_per_mol
    if track_extent:
        atol = (
            [atol_mol] * (3 * count)
            + [_SLOW_EXTENT_ATOL_KG] * count
            + [atol_mol] * count
        )
    else:
        atol = atol_mol

    def moles_of(state) -> dict[str, float]:
        # A stage may step a draining inventory slightly negative.
        # The pipe law is defined on n >= 0. The end state is checked
        # against the partial floor before that clamp is published.
        # A and B are integrals, not an inventory.
        return {
            name: (float(state[index]) if float(state[index]) > 0.0 else 0.0)
            for index, name in enumerate(species)
        }

    def rates_of(moles: Mapping[str, float]):
        partials = law.partials_of(moles)
        source, outflow, _binding, piece = law._flows(partials)
        redox = law._redox_rates(partials)
        added: list[float] = []
        bled: list[float] = []
        net: list[float] = []
        gross: list[float] = []
        redox_rate: list[float] = []
        for name in species:
            inflow = source[name] + redox[name]
            removed = outflow[name]
            rate = inflow - removed
            gross_kg_s = float(law._last_gross_kg_s.get(name, 0.0))
            redox_mol_s = float(redox[name])
            if not (
                math.isfinite(inflow)
                and math.isfinite(removed)
                and math.isfinite(rate)
                and math.isfinite(gross_kg_s)
                and math.isfinite(redox_mol_s)
            ):
                raise _BalanceRefused("headspace_balance_nonconvergence")
            added.append(inflow)
            bled.append(removed)
            net.append(rate)
            gross.append(gross_kg_s)
            redox_rate.append(redox_mol_s)
        # Binding, blend bucket, cap. The empty-holdup flag is not a
        # kink a fill is forbidden to leave. Mole fraction is not a
        # piece: the mean free path is continuous across equal moles.
        # The shut side is a different piece of the derivative.
        # Premise: Q is 0 at and below P_down, and both the Poiseuille
        # member (P^2 - P_down^2) and the effusion member
        # (P - P_down) are 0 at P = P_down, so the value is continuous.
        # The slope is not: dQ/dP is 0 on the shut side and finite on
        # the open side. A central difference across that kink is
        # neither slope.
        # Algebra: the key gains (P <= P_down) when the duct has a
        # positive downstream. P = Σ p_i.
        # Unit check: the flag is dimensionless. The rates stay mol/s.
        # Limit: P_down = 0 has no shut piece above the empty holdup,
        # so the key stays the triple. A probe that stays on one side
        # takes the same difference it took without the flag.
        compared = piece[:3]
        if law.diameter_m is not None and law.downstream_pa > 0.0:
            compared = compared + (
                law.pressure_of(moles) <= law.downstream_pa,
            )
        return net, added, bled, gross, redox_rate, compared

    def rhs(_time: float, state) -> list[float]:
        law._note_rhs_flux()
        law.integrator_evaluations += 1
        if law.integrator_evaluations > _MAX_INTEGRATOR_EVALUATIONS:
            raise _BalanceRefused(
                "headspace_balance_work_cap",
                cap="integrator",
                flux_evaluations=law.flux_evaluations,
                integrator_evaluations=law.integrator_evaluations,
            )
        moles = moles_of(state)
        packed = rates_of(moles)
        # The cap check is at the top of the next call. This is the
        # step that call had already accepted. A Jacobian probe updates
        # the paired redox below and does not replace these three.
        law.accepted_moles = dict(moles)
        law.accepted_partials = law.partials_of(moles)
        law.accepted_step_redox = dict(law.paired_redox_mol_s or {})
        law.accepted_pipe_piece = law._last_pipe_piece
        holder = law.extent_holder
        if isinstance(holder, dict):
            raw_capture = holder.get("_capture_fractions")
            if isinstance(raw_capture, dict):
                law.accepted_capture_fractions = {
                    str(name): float(fraction)
                    for name, fraction in raw_capture.items()
                    if isinstance(fraction, (int, float))
                    and not isinstance(fraction, bool)
                    and math.isfinite(float(fraction))
                }
        # n, A, B. G and R only when the hour asked to track them.
        # Leaving them out keeps the untracked rhs the three lists
        # it integrated before.
        if track_extent:
            return packed[0] + packed[1] + packed[2] + packed[3] + packed[4]
        return packed[0] + packed[1] + packed[2]

    def jacobian(_time: float, state):
        # |dn| starts at |n| * 1e-8 and shrinks until the sample stays
        # on this piece. A column of zeros is not the fallback: that
        # is the flat update a crossed stencil returned on the
        # free-molecular piece. A, B, G and R do not enter the rates,
        # so their columns stay zero.
        moles = moles_of(state)
        base = rates_of(moles)
        piece = base[-1]
        width_count = blocks * count
        matrix = np.zeros((width_count, width_count))

        def column_of(up, down, span: float) -> list[float]:
            # One sample difference. ∂n/∂n_j = ∂A/∂n_j − ∂B/∂n_j.
            # G and R are differentiated the same way when tracked.
            return [
                (up[block][index] - down[block][index]) / span
                for block in range(blocks)
                for index in range(count)
            ]

        for column, name in enumerate(species):
            amount = moles[name]
            width = 1.0e-14 if amount == 0.0 else max(abs(amount) * 1.0e-8, 1.0e-18)
            chosen: list[float] | None = None
            for _shrink in range(24):
                up_moles = dict(moles)
                up_moles[name] = amount + width
                up = rates_of(up_moles)
                up_ok = up[-1] == piece
                down = None
                down_ok = False
                if amount > width:
                    down_moles = dict(moles)
                    down_moles[name] = amount - width
                    down = rates_of(down_moles)
                    down_ok = down[-1] == piece
                if up_ok and down_ok and down is not None:
                    chosen = column_of(up, down, 2.0 * width)
                    break
                if up_ok:
                    chosen = column_of(up, base, width)
                    break
                if down_ok and down is not None:
                    chosen = column_of(base, down, width)
                    break
                next_width = width * 0.1
                if next_width < 1.0e-18:
                    chosen = column_of(up, base, width)
                    break
                width = next_width
            if chosen is None:
                raise _BalanceRefused("headspace_balance_nonconvergence")
            for row in range(width_count):
                matrix[row, column] = chosen[row]
        return matrix

    # A holdup that opens above P_down can relax onto the switch.
    # The event is not installed on a shut opening: the closed path
    # already owns that hour, and a root that starts at the switch
    # is not a downward crossing.
    events = None
    if (
        law.diameter_m is not None
        and law.downstream_pa > 0.0
        and law.pressure_of(n0) > law.downstream_pa
    ):

        def downstream_switch(_time, state):
            """Terminal root where the open pipe meets the shut pipe.

            Premise: forward flow is zero at and below P_down. The
            open law is continuous there. Poiseuille is
            Q ~ (P^2 - P_down^2) and effusion is
            Q_i = C_i * x_i * (P - P_down) / (R T). Both are 0 at
            P = P_down, so the shut return of an empty rate does not
            change the value. The slope does change, from 0 on the
            shut side to a finite dQ/dP on the open side. Radau's
            error test rejects a step that straddles that kink, and
            a negligible source chatters on it until the work cap.
            The equilibrium of that source is the switch, with zero
            net flow.
            Algebra: g = Σ p_i(n) - P_down. Direction is downward,
            and the root is terminal. The remainder of the hour is
            the shut-pipe accumulation, which integrates q + S with
            Q held at 0 while the holdup stays at or below P_down.
            Unit check: Pa - Pa = Pa.
            Limit: P -> P_down from above gives Q -> 0, the same
            flow the shut path books. P_down = 0 is not this event.
            Every positive holdup is open, and the shut path
            returns None.
            """

            return law.pressure_of(moles_of(state)) - law.downstream_pa

        downstream_switch.terminal = True
        downstream_switch.direction = -1.0
        events = downstream_switch

    recorder = _StiffnessRecord(species, block_names)

    def _on_radau_step(solver, accepted, fails_before, _message) -> None:
        if accepted:
            step_s = float(solver.t - solver.t_old)
            if law.handoff_time_s is None:
                covered = law.full_system_radau_time_covered_s or 0.0
                law.full_system_radau_time_covered_s = covered + step_s
        # A collapsed step on the open path is the stall, wherever
        # the root sits. Newton has already missed twice, or the
        # accepted step itself is below 1e-3 s, and the pressure is
        # not still moving by more than the 1e-6 Pa floor. The 1199 C
        # hour chatters a few microseconds at a time with its root
        # just past 1e-4 Pa above P_down; a fixed band there never
        # fires. An approach from several pascals still moves, so it
        # keeps integrating. No bracket, or a relaxation that is not
        # fast beside t_rem, leaves the latch set and the integrator
        # running. The work cap is still the refusal.
        if law._quasi_steady_declined:
            return
        if law.diameter_m is None or not law.downstream_pa > 0.0:
            return
        fails = recorder.newton_failures - fails_before
        step_s = float(solver.t - solver.t_old) if accepted else None
        tiny = (
            step_s is not None
            and math.isfinite(step_s)
            and 0.0 < step_s < _KINK_STEP_S
        )
        if not tiny and fails < _KINK_NEWTON_FAILS:
            return
        t_stall = float(solver.t)
        if not math.isfinite(t_stall) or float(dt_s) - t_stall <= 0.0:
            return
        moles = moles_of(solver.y)
        pressure = law.pressure_of(moles)
        above = pressure - law.downstream_pa
        if not (math.isfinite(above) and above > 0.0):
            return
        if accepted:
            previous = law._open_step_pressure
            law._open_step_pressure = pressure
        else:
            previous = law._open_step_pressure
        if fails < _KINK_NEWTON_FAILS:
            if (
                previous is None
                or abs(pressure - previous) > DEFAULT_ABS_TOL_PA
            ):
                return
        fractions = _fractions_of(law, moles)
        if fractions is None:
            law._quasi_steady_declined = True
            return
        try:
            kind, root = _open_or_shut_fixed_point(
                law,
                fractions,
                near_Pa=pressure,
            )
        except _BalanceRefused as refused:
            if refused.reason in (
                "headspace_balance_multiple_roots",
                "headspace_balance_work_cap",
            ):
                raise
            law._quasi_steady_declined = True
            return
        if kind is None:
            law._quasi_steady_declined = True
            return
        try:
            tau = _quasi_steady_tau(jacobian, solver.y)
        except _BalanceRefused:
            raise
        except Exception:
            tau = None
        t_rem = float(dt_s) - t_stall
        if (
            tau is None
            or tau / t_rem > _QUASI_STEADY_MAX_TAU_FRACTION
        ):
            law._quasi_steady_declined = True
            return
        # The pressure error of pinning P* now is about
        # |P − P*| · tau / t_rem. That product is the 1e-6 Pa floor.
        # P* − P_down is the source/slope offset, so a root past the
        # old cut still holds, and a drain that is still pascals away
        # does not. Do not latch on that miss: a later step can arrive.
        if kind == "open":
            if root is None:
                law._quasi_steady_declined = True
                return
            gap_to_root = abs(pressure - float(root))
        else:
            gap_to_root = above
        if gap_to_root * tau / t_rem > DEFAULT_ABS_TOL_PA:
            return
        # The shut projection refuses a scale farther than the kink
        # width and would latch the hour on the way there. The open
        # event still lands that drain.
        if kind == "shut" and above > _KINK_BAND_PA:
            return
        step = _hold_fixed_point(
            law,
            n0,
            dt_s,
            solver.y,
            t_stall,
            pressure,
            kind,
            root,
            tau,
            fractions,
            track_extent=track_extent,
        )
        if step is None:
            return
        raise _QuasiSteadyStall(step)

    try:
        solver_kwargs = {
            "method": _recording_radau(recorder, _on_radau_step),
            "rtol": _SLOW_RTOL,
            "atol": atol,
            "jac": jacobian,
        }
        if events is not None:
            solver_kwargs["events"] = events
        with _RadauNormTape(recorder):
            solution = solve_ivp(
                rhs,
                (0.0, float(dt_s)),
                y0,
                **solver_kwargs,
            )
    except _QuasiSteadyStall as stalled:
        return stalled.step
    except _BalanceRefused as refused:
        # The cap fired inside the right-hand side, so nfev is not
        # back. The rhs counter is how far this attempt got. The tape
        # is the steps Radau had already accepted. The extra Jacobian
        # for its eigenvalues can itself hit the flux cap; that leaves
        # |λ| unset and does not replace this refusal.
        _remember_balance_iterations(law, law.integrator_evaluations)
        if refused.reason == "headspace_balance_work_cap":
            _store_stiffness(law, recorder, jacobian)
        elif refused.reason == "headspace_balance_nonconvergence":
            _remember_radau_tape(law, recorder)
        raise
    iterations = max(int(solution.nfev), 1)
    _remember_balance_iterations(law, iterations)
    if not solution.success:
        _remember_radau_tape(law, recorder)
        raise _BalanceRefused("headspace_balance_nonconvergence")
    try:
        y_end = solution.y[:, -1]
        hit = (
            solution.t_events is not None
            and len(solution.t_events) > 0
            and len(solution.t_events[0]) > 0
        )
        if hit:
            return _land_open_pipe_on_downstream(
                law,
                n0,
                dt_s,
                y_end,
                float(solution.t[-1]),
                iterations,
                track_extent=track_extent,
            )
        end, added_mol, bled_mol, extent_kwargs = _integrated_holdup(
            law,
            n0,
            y_end,
            track_extent=track_extent,
        )
        return law.step_from_moles(
            n0,
            dt_s,
            end,
            iterations,
            _SLOW_ATOL_PA,
            added_mol=added_mol,
            bled_mol_in=bled_mol,
            **extent_kwargs,
        )
    except _BalanceRefused:
        _remember_radau_tape(law, recorder)
        raise


def _integrated_holdup(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    y_end,
    *,
    track_extent: bool,
) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict]:
    """n = n0 + A − B from one Radau state, with the partial floor.

    The state is n, then A = ∫(q+S) dt, then B = ∫Q dt. With a
    holder, G and R follow. The published moles are this identity,
    not the n component, which a stage may have stepped negative.
    """

    species = law.species
    count = len(species)
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    for value in y_end[:count]:
        amount = float(value)
        if not math.isfinite(amount):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        # The integrated inventory may undershoot. A partial below
        # -1e-6 Pa is not the tolerance. The published moles are the
        # closure identity below, not this component.
        if amount < 0.0 and -amount * pa_per_mol > DEFAULT_ABS_TOL_PA:
            raise _BalanceRefused("headspace_balance_nonconvergence")
    added_mol: dict[str, float] = {}
    bled_mol: dict[str, float] = {}
    end: dict[str, float] = {}
    extent_kg: dict[str, float] = {}
    redox_mol: dict[str, float] = {}
    for index, name in enumerate(species):
        added = float(y_end[count + index])
        bled = float(y_end[2 * count + index])
        if not math.isfinite(added) or not math.isfinite(bled):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        # Q is nonnegative. An undershoot of B inside the partial
        # floor is the tolerance. Past that floor the integral failed.
        if bled < 0.0:
            if -bled * pa_per_mol > DEFAULT_ABS_TOL_PA:
                raise _BalanceRefused("headspace_balance_nonconvergence")
            bled = 0.0
        # n_end = n0 + A − B. Publishing this, and booking B, is the
        # closure. A reconstructed partial below -1e-6 Pa is a refusal.
        # Inside the floor the inventory is empty and the bleed is the
        # holdup plus the source, which keeps the same identity.
        amount = float(n0[name]) + added - bled
        if amount < 0.0:
            if -amount * pa_per_mol > DEFAULT_ABS_TOL_PA:
                raise _BalanceRefused("headspace_balance_nonconvergence")
            amount = 0.0
            bled = float(n0[name]) + added
        added_mol[name] = added
        bled_mol[name] = bled
        end[name] = amount
        if track_extent:
            extent = float(y_end[3 * count + index])
            redox_added = float(y_end[4 * count + index])
            if not math.isfinite(extent) or not math.isfinite(redox_added):
                raise _BalanceRefused("headspace_balance_nonconvergence")
            # Gross and S are nonnegative. A kilogram undershoot is
            # priced as moles of that species. R is already moles.
            # Inside the partial floor the integral is zero. Past it
            # the step failed.
            mass = law.masses[name]
            extent_mol = 0.0 if mass <= 0.0 else -extent / mass
            if extent < 0.0:
                if extent_mol * pa_per_mol > DEFAULT_ABS_TOL_PA:
                    raise _BalanceRefused("headspace_balance_nonconvergence")
                extent = 0.0
            if redox_added < 0.0:
                if -redox_added * pa_per_mol > DEFAULT_ABS_TOL_PA:
                    raise _BalanceRefused("headspace_balance_nonconvergence")
                redox_added = 0.0
            extent_kg[name] = extent
            redox_mol[name] = redox_added
    extent_kwargs: dict = {}
    if track_extent:
        extent_kwargs = {
            "vapour_extent_kg": extent_kg,
            "redox_added_mol": redox_mol,
        }
    return end, added_mol, bled_mol, extent_kwargs


def _project_onto_downstream(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    end: Mapping[str, float],
    added: Mapping[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Put the event holdup on P_down and keep n = n0 + A − B.

    Premise: the terminal root is the switch. The collocation
    polynomial at that root can miss it by a rounding, and the shut
    path treats any pressure above P_down as still open.
    Algebra: n' = n * P_down / P, then B' = n0 + A − n', so
    n' = n0 + A − B'. A one-ulp overshoot is taken off the largest
    holdup so the shut check sees P <= P_down.
    Unit check: Pa / Pa = 1, and mol − mol = mol.
    Limit: P = P_down leaves n and B unchanged. A root farther than
    the 1e-6 Pa floor, or a non-positive pressure, is not this
    switch, and it is a refusal. A holdup the bleed floor puts back
    above the switch is the same refusal: the shut path would
    otherwise return None and this event would be installed again.
    """

    target = law.downstream_pa
    landed = {name: float(end[name]) for name in law.species}
    pressure = law.pressure_of(landed)
    # A non-positive root is not the switch. Skipping the distance
    # check there would publish a vacuum holdup as if it were P_down.
    if pressure != target and (
        not math.isfinite(pressure)
        or pressure <= 0.0
        or abs(pressure - target) > DEFAULT_ABS_TOL_PA
    ):
        raise _BalanceRefused("headspace_balance_nonconvergence")
    if pressure != target:
        scale = target / pressure
        landed = {
            name: float(end[name]) * scale for name in law.species
        }
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    new_bled: dict[str, float] = {}
    for name in law.species:
        bleed = float(n0[name]) + float(added[name]) - landed[name]
        if bleed < 0.0:
            if -bleed * pa_per_mol > DEFAULT_ABS_TOL_PA:
                raise _BalanceRefused("headspace_balance_nonconvergence")
            bleed = 0.0
            landed[name] = float(n0[name]) + float(added[name])
        new_bled[name] = bleed
    # The bleed floor can put one species back above the switch.
    # Wider than the partial floor, this is not the root. A binary64
    # overshoot is taken off the largest holdup, and its bleed is
    # rewritten so n = n0 + A − B still holds. A holdup that is still
    # above the switch would make the shut path return None and the
    # open integral reinstall this same event.
    if abs(law.pressure_of(landed) - target) > DEFAULT_ABS_TOL_PA:
        raise _BalanceRefused("headspace_balance_nonconvergence")
    for _ulp in range(4):
        over = law.pressure_of(landed) - target
        if over <= 0.0:
            break
        donor = max(landed, key=lambda name: landed[name])
        landed[donor] = landed[donor] - over / pa_per_mol
        if landed[donor] < 0.0:
            landed[donor] = 0.0
        new_bled[donor] = (
            float(n0[donor]) + float(added[donor]) - landed[donor]
        )
    if law.pressure_of(landed) > target:
        raise _BalanceRefused("headspace_balance_nonconvergence")
    return landed, new_bled


def _join_switch_remainder(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    landed: Mapping[str, float],
    added: Mapping[str, float],
    bled: Mapping[str, float],
    extent_kwargs: dict,
    rest: _HeadspaceStep,
    open_iterations: int,
    *,
    track_extent: bool,
) -> _HeadspaceStep:
    """Book the open integral up to the switch plus the shut remainder.

    The remainder's own step used the switch as its opening. Its
    identity is n_end = n_switch + A_rest − B_rest, so
    A_rest = n_end − n_switch + B_rest. The hour's identity is
    n_end = n0 + (A_open + A_rest) − (B_open + B_rest).
    """

    added_total: dict[str, float] = {}
    bled_total: dict[str, float] = {}
    for name in law.species:
        rest_added = (
            float(rest.moles[name])
            - float(landed[name])
            + float(rest.bled_mol[name])
        )
        added_total[name] = float(added[name]) + rest_added
        bled_total[name] = float(bled[name]) + float(rest.bled_mol[name])
    kwargs = dict(extent_kwargs)
    if track_extent:
        event_gross = extent_kwargs["vapour_extent_kg"]
        event_redox = extent_kwargs["redox_added_mol"]
        kwargs = {
            "vapour_extent_kg": {
                name: float(event_gross[name])
                + float(rest.vapour_extent_kg[name])
                for name in law.species
            },
            "redox_added_mol": {
                name: float(event_redox[name])
                + float(rest.redox_added_mol[name])
                for name in law.species
            },
        }
    iterations = open_iterations + max(int(rest.iterations), 0)
    _mark_balance_path(law, "radau_fallback")
    _remember_balance_iterations(law, iterations)
    return law.step_from_moles(
        n0,
        dt_s,
        rest.moles,
        iterations,
        _SLOW_ATOL_PA,
        added_mol=added_total,
        bled_mol_in=bled_total,
        **kwargs,
    )


def _land_open_pipe_on_downstream(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    y_hit,
    t_hit: float,
    open_iterations: int,
    *,
    track_extent: bool,
) -> _HeadspaceStep:
    """Stop the open integral at P_down and finish on the shut path.

    A source that would open the pipe again is not clamped onto the
    switch. The shut path returns None for that remainder, and the
    open integral continues from the switch.
    """

    end, added, bled, extent_kwargs = _integrated_holdup(
        law,
        n0,
        y_hit,
        track_extent=track_extent,
    )
    landed, bled = _project_onto_downstream(law, n0, end, added)
    return _finish_downstream_remainder(
        law,
        n0,
        dt_s,
        landed,
        added,
        bled,
        extent_kwargs,
        t_hit,
        open_iterations,
        track_extent=track_extent,
    )


def _closed_pipe_accumulation(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep | None:
    """Exact holdup when the pipe stays shut for the whole step.

    Premise: Q is 0 whenever P <= P_down, because the pipe law has no
    reverse flow. A source that cannot lift the inventory to P_down
    never opens the pipe, so dn_i/dt = q_i + S_i. On the vacuum
    plateau q does not depend on P, and the hour is
    n_end = n0 + (q + S) Δt with bleed 0. That is the closed-pipe
    case under a downstream pressure. Integrating it with Radau walks
    a kink the trajectory never reaches.
    Algebra: P_end = P(n0 + (q(n0) + S(n0)) Δt). Admit that step when
    it stays shut and the pressure of |q(n_end) - q(n0)| Δt is at
    most 1e-6 Pa. A 1/P source at a few hundredths of a pascal makes
    that extrapolation cross P_down, and an 8-step grid does too,
    while the hour integral stays shut. Those attempts use a finer
    RK4, then implicit midpoint, instead of the slow solver. A shut
    grid that still disagrees at the last spacing falls through to
    that integral. A grid that opens the pipe returns None.
    Unit check: mol/s * s = mol, and mol * R * T / V = Pa.
    Limit: q = 0 leaves the opening holdup. P_down = 0 is open at
    every positive pressure, so this returns None. A sealed cap with
    no duct is closed at every pressure.
    """

    sealed = law.diameter_m is None and law.cap_kg_s == 0.0
    below_downstream = (
        law.diameter_m is not None and law.downstream_pa > 0.0
    )
    if not sealed and not below_downstream:
        return None
    if below_downstream and law.pressure_of(n0) > law.downstream_pa:
        return None
    _mark_balance_path(law, "explicit_euler")
    partials_0 = law.partials_of(n0)
    source_0, outflow_0, _binding_0, _piece_0 = law._flows(partials_0)
    redox_0 = law._redox_rates(partials_0)
    # The admission check evaluates the end state next and overwrites
    # these snapshots. The Euler integral is the opening rate times
    # the step, the same rate added_mol books.
    opening_gross_kg_s = dict(law._last_gross_kg_s)
    opening_redox_mol_s = dict(law._last_redox_mol_s)
    if any(outflow_0[species] > 0.0 for species in law.species):
        return None
    added: dict[str, float] = {}
    n_end: dict[str, float] = {}
    euler_usable = True
    for species in law.species:
        rate = source_0[species] + redox_0[species]
        if not math.isfinite(rate):
            return None
        grown = rate * dt_s
        amount = float(n0[species]) + grown
        if amount < 0.0:
            # The opening rate would drain the holdup. The integral
            # can still stay non-negative, so this is not a refusal
            # of the closed pipe.
            euler_usable = False
        added[species] = grown
        n_end[species] = amount
    if (
        euler_usable
        and below_downstream
        and law.pressure_of(n_end) > law.downstream_pa
    ):
        # q(n0) * Δt crosses P_down. A source that falls as 1/P does
        # that at a near-empty opening and still finishes shut.
        euler_usable = False
    if euler_usable:
        partials_1 = law.partials_of(n_end)
        source_1, outflow_1, _binding_1, _piece_1 = law._flows(partials_1)
        redox_1 = law._redox_rates(partials_1)
        if any(outflow_1[species] > 0.0 for species in law.species):
            euler_usable = False
        else:
            pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
            mismatch_pa = math.fsum(
                abs(
                    (source_1[species] + redox_1[species])
                    - (source_0[species] + redox_0[species])
                )
                * dt_s
                * pa_per_mol
                for species in sorted(law.species)
            )
            if mismatch_pa <= DEFAULT_ABS_TOL_PA:
                return law.step_from_moles(
                    n0,
                    dt_s,
                    n_end,
                    1,
                    mismatch_pa,
                    added_mol=added,
                    bled_mol_in={species: 0.0 for species in law.species},
                    vapour_extent_kg={
                        species: opening_gross_kg_s.get(species, 0.0) * dt_s
                        for species in law.species
                    },
                    redox_added_mol={
                        species: opening_redox_mol_s.get(species, 0.0) * dt_s
                        for species in law.species
                    },
                )
    # The pipe can still be shut for the whole hour. An Euler step
    # that stays shut uses explicit RK4. One that would cross
    # downstream goes straight to the midpoint: the explicit ladder
    # is a full evaporation at every stage, and on the C3_NA hour
    # that ladder in front of Radau was the multi-minute stall.
    return _closed_pipe_refined(
        law,
        n0,
        dt_s,
        below_downstream=below_downstream,
        explicit=euler_usable,
    )


def _closed_pipe_refined(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    *,
    below_downstream: bool,
    explicit: bool = True,
) -> _HeadspaceStep | None:
    """Closed-pipe accumulation when q changes inside the hour.

    RK4 on dn/dt = q + S with Q held at 0. Each stage that opens
    the pipe, crosses downstream, or drives a mole negative rejects
    that spacing. A rejected spacing is not the end of the case:
    the opening rate of a 1/P source steps over P_down on a coarse
    grid and stays under it on a finer one. Doubling stops at the
    first pair within 1e-6 Pa. 64 steps is the last explicit
    spacing; a pair that still disagrees uses implicit midpoint.
    """

    _mark_balance_path(
        law,
        "explicit_ladder" if explicit else "midpoint",
    )

    def rates(amount: Mapping[str, float]) -> dict[str, float] | None:
        if any(value < 0.0 for value in amount.values()):
            return None
        if below_downstream and law.pressure_of(amount) > law.downstream_pa:
            return None
        partials = law.partials_of(amount)
        source, outflow, _binding, _piece = law._flows(partials)
        redox = law._redox_rates(partials)
        if any(outflow[species] > 0.0 for species in law.species):
            return None
        out: dict[str, float] = {}
        for species in law.species:
            rate = source[species] + redox[species]
            if not math.isfinite(rate):
                return None
            out[species] = rate
        return out

    def integrate(steps: int):
        amount = {species: float(n0[species]) for species in law.species}
        span = dt_s / steps
        gross_acc = {species: 0.0 for species in law.species}
        redox_acc = {species: 0.0 for species in law.species}
        for _step in range(steps):
            k1 = rates(amount)
            if k1 is None:
                return None
            g1 = dict(law._last_gross_kg_s)
            r1 = dict(law._last_redox_mol_s)
            k2 = rates({
                species: amount[species] + 0.5 * span * k1[species]
                for species in law.species
            })
            if k2 is None:
                return None
            g2 = dict(law._last_gross_kg_s)
            r2 = dict(law._last_redox_mol_s)
            k3 = rates({
                species: amount[species] + 0.5 * span * k2[species]
                for species in law.species
            })
            if k3 is None:
                return None
            g3 = dict(law._last_gross_kg_s)
            r3 = dict(law._last_redox_mol_s)
            k4 = rates({
                species: amount[species] + span * k3[species]
                for species in law.species
            })
            if k4 is None:
                return None
            g4 = dict(law._last_gross_kg_s)
            r4 = dict(law._last_redox_mol_s)
            nxt: dict[str, float] = {}
            for species in law.species:
                grown = amount[species] + span * (
                    k1[species] + 2.0 * k2[species] + 2.0 * k3[species] + k4[species]
                ) / 6.0
                if grown < 0.0:
                    return None
                nxt[species] = grown
                # Same weights as n. kg/s * s = kg, mol/s * s = mol.
                gross_acc[species] += span * (
                    g1.get(species, 0.0)
                    + 2.0 * g2.get(species, 0.0)
                    + 2.0 * g3.get(species, 0.0)
                    + g4.get(species, 0.0)
                ) / 6.0
                redox_acc[species] += span * (
                    r1.get(species, 0.0)
                    + 2.0 * r2.get(species, 0.0)
                    + 2.0 * r3.get(species, 0.0)
                    + r4.get(species, 0.0)
                ) / 6.0
            amount = nxt
        if rates(amount) is None:
            return None
        return amount, gross_acc, redox_acc

    def gap_pa(left: Mapping[str, float], right: Mapping[str, float]) -> float:
        pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
        # Sorted names. A set walk would move the bit next to 1e-6 Pa.
        return math.fsum(
            abs(left[species] - right[species]) * pa_per_mol
            for species in sorted(law.species)
        )

    # 8 steps is the first spacing. A spacing that crosses downstream
    # is discarded and the next doubling is tried on its own. Two
    # consecutive shut grids within 1e-6 Pa publish the finer one.
    # 64 * 4 stages is 256 flux calls on the last grid. Past that,
    # a 1/P source still has a large local error while P is small,
    # and the midpoint below is the exact integral of that rate.
    # An opening rate that already crosses downstream skips this
    # ladder and uses the midpoint below.
    previous: dict[str, float] | None = None
    steps = 8
    while explicit and steps <= 64:
        integrated = integrate(steps)
        if integrated is None:
            previous = None
            steps *= 2
            continue
        current, gross_acc, redox_acc = integrated
        if previous is not None:
            gap = gap_pa(previous, current)
            if gap <= DEFAULT_ABS_TOL_PA:
                return law.step_from_moles(
                    n0,
                    dt_s,
                    current,
                    steps,
                    gap,
                    added_mol={
                        species: current[species] - float(n0[species])
                        for species in law.species
                    },
                    bled_mol_in={species: 0.0 for species in law.species},
                    vapour_extent_kg=gross_acc,
                    redox_added_mol=redox_acc,
                )
        previous = current
        steps *= 2
    # Explicit grids through 64 steps still disagree, or the opening
    # rate already crosses downstream. A 1/P source at a few
    # hundredths of a pascal is that case: the local error is large
    # while P is small, and Radau then walks the downstream kink the
    # trajectory never reaches. Implicit midpoint takes
    # n1 = n0 + h r((n0+n1)/2). For r proportional to 1/n that step
    # is the exact integral, so a coarse pair agrees.
    return _closed_pipe_midpoint(
        law,
        n0,
        dt_s,
        rates,
        gap_pa,
    )


def _closed_pipe_midpoint(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    rates,
    gap_pa,
) -> _HeadspaceStep | None:
    """Implicit midpoint while the pipe stays shut.

    Premise: the stage state m satisfies m = n + (h/2) r(m), and the
    end state is 2m − n. For r = a/n the identity m^2 − n^2 = a h is
    the separated integral, so one step has no truncation error.
    Algebra: P_end^2 − P^2 = 2 K h when dP/dt = K/P.
    Unit check: mol/s * s = mol.
    The stage equation is a damped fixed point, one rate evaluation
    per pass. The blend that stays shut is chosen from the pressure,
    which is linear in the moles, so a rejected blend does not call
    the flux. A finite-difference Jacobian would repeat the
    evaporation once per species per pass, which is the stall.
    Limit: a step that would open the pipe, or a fixed point that
    does not settle in 80 passes, rejects that spacing. Two shut
    grids within 1e-6 Pa publish the finer one. 64 steps is the
    last spacing. A shut grid that is still apart there is not
    the authority: the slow integral publishes, and only that
    integral's evaluation caps refuse. A spacing sequence that
    never stays shut returns None, and the open-pipe solver runs.
    """

    _mark_balance_path(law, "midpoint")
    names = tuple(law.species)
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    # Each accepted step may stop this far from the stage equation.
    # Sixty-four steps at the 1e-6 Pa gate would accumulate past it.
    # 1e-12 Pa times 64 steps stays under the gate.
    mole_tol = DEFAULT_ABS_TOL_PA * 1.0e-6 / pa_per_mol

    def trial_shut(amount: Mapping[str, float]) -> bool:
        # P = n R T / V is linear, so the shut set is convex and
        # this check is algebraic. Q is 0 on that set: the pipe
        # law has no reverse flow, and a sealed cap has no duct.
        # Calling rates() here was one evaporation per damp halving.
        if any(value < 0.0 for value in amount.values()):
            return False
        if (
            law.diameter_m is not None
            and law.downstream_pa > 0.0
            and law.pressure_of(amount) > law.downstream_pa
        ):
            return False
        return True

    def one_step(amount: Mapping[str, float], span: float):
        base = {name: float(amount[name]) for name in names}
        mid = dict(base)
        # Damping 1/2. On r = a/n the undamped map's slope is near
        # −1 for a full-hour step, so it rings. Half the update
        # settles in about 15 passes, and the fixed point is exact.
        # The first blend of a huge opening rate can step over
        # downstream even though the fixed point is shut. Shrink
        # that blend until the trial stays shut. 2^-40 is past a
        # full-hour step of any rate that still finishes under
        # 100 Pa on a finer spacing.
        # The stage residual has to fall under ~1e-12 Pa. Halving
        # from a hundred-pascal residual takes about 50 passes.
        # Forty returned no shut grid, and the hour then paid for
        # Radau. Eighty covers that halving.
        accepted_gross: dict[str, float] | None = None
        accepted_redox: dict[str, float] | None = None
        for _pass in range(80):
            rate = rates(mid)
            if rate is None:
                return None
            # The rate that built this target. n_end − n = span · r(mid),
            # so the gross and S integrals of the step are span times
            # the same sample, not the end rate. The check at `end`
            # below overwrites the law snapshot.
            accepted_gross = dict(law._last_gross_kg_s)
            accepted_redox = dict(law._last_redox_mol_s)
            target = {
                name: base[name] + 0.5 * span * rate[name]
                for name in names
            }
            error = max(
                abs(mid[name] - target[name]) for name in names
            )
            damp = 0.5
            placed = False
            while damp >= 1.0e-12:
                trial = {
                    name: (1.0 - damp) * mid[name] + damp * target[name]
                    for name in names
                }
                if trial_shut(trial):
                    mid = trial
                    placed = True
                    break
                damp *= 0.5
            if not placed:
                return None
            if error <= mole_tol:
                break
        else:
            return None
        end = {
            name: 2.0 * mid[name] - base[name]
            for name in names
        }
        if any(end[name] < 0.0 for name in names):
            return None
        if rates(end) is None or accepted_gross is None or accepted_redox is None:
            return None
        return end, accepted_gross, accepted_redox

    def grid(steps: int):
        span = dt_s / steps
        amount = {name: float(n0[name]) for name in names}
        gross_acc = {name: 0.0 for name in names}
        redox_acc = {name: 0.0 for name in names}
        for _step in range(steps):
            stepped = one_step(amount, span)
            if stepped is None:
                return None
            amount, gross_mid, redox_mid = stepped
            for name in names:
                # span · r(mid). kg/s · s = kg, mol/s · s = mol.
                gross_acc[name] += span * float(gross_mid.get(name, 0.0))
                redox_acc[name] += span * float(redox_mid.get(name, 0.0))
        return amount, gross_acc, redox_acc

    previous = None
    saw_shut = False
    steps = 1
    while steps <= 64:
        current = grid(steps)
        if current is None:
            previous = None
            steps *= 2
            continue
        amount, gross_acc, redox_acc = current
        saw_shut = True
        if previous is not None and gap_pa(previous, amount) <= DEFAULT_ABS_TOL_PA:
            _remember_shut_path(law, "midpoint")
            return law.step_from_moles(
                n0,
                dt_s,
                amount,
                steps,
                gap_pa(previous, amount),
                added_mol={
                    name: amount[name] - float(n0[name])
                    for name in names
                },
                bled_mol_in={name: 0.0 for name in names},
                vapour_extent_kg=gross_acc,
                redox_added_mol=redox_acc,
            )
        previous = amount
        steps *= 2
    # The pipe stayed shut on a grid and no pair agreed within
    # 1e-6 Pa. The grid is the fast path. The slow integral is
    # bounded by the flux cap and the integrator cap, and those
    # are the only work-cap refusals. A sequence that never stayed
    # shut returns None, and the open-pipe solver runs.
    if saw_shut:
        _remember_shut_path(law, "radau_fallback")
        return _slow_converge(law, n0, dt_s)
    return None


def _refuse_unless_work_or_roots(refused: _BalanceRefused) -> None:
    """A cap or two roots is the hour's refusal. Anything else falls through."""

    if refused.reason in (
        "headspace_balance_work_cap",
        "headspace_balance_multiple_roots",
    ):
        raise refused


def _frozen_composition_hour(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep | None:
    """Exact hour when the composition is frozen on the slow manifold.

    Premise: dn_i/dt = s_i(P, n) − y_i Q(P), with y_i = n_i/n and
    s_i = q_i + S_i. Every species shares the composition time
    tau = n/Σ s. Total pressure is the fast mode (milliseconds on
    these hours), so P sits on P*(y), the root of Q(P*) = Σ s at
    the frozen opening fractions. Freezing s_i(y) over the hour
    leaves a mole error of order n (dt/tau)^2 when ds/dy is order
    s. The gate is dt/tau <= 1e-3, which is a relative error of
    about 1e-6. The same cut bounds the fast transient: its
    duration t_fast has to be at most dt times that gate, so the
    source during the projection is another 1e-6 of the inventory.
    An opening composition time already past the gate returns
    before any P* solve. An ordinary hour, where tau is a fraction
    of a second, pays nothing. A missing opening outflow cannot
    price the transient and falls through.

    Algebra: solve P* once at the opening fractions. Project
    n*_i = y_i0 P* V / (R T). The excess is n_open − n*. While
    the excess is draining, the rate is Q_open − Σ s; while it is
    filling, Σ s − Q_open. t_fast = |excess| / rate, or 0 when
    the excess is already inside 1e-6 Pa. Over that sliver
    A_fast,i = s_i t_fast and
    B_fast,i = A_fast,i + n0_i − n*_i, so
    n0 + A_fast − B_fast = n*. A bleed past −1e-6 Pa is not this
    projection. Over t_rem = dt − t_fast the manifold integral is
    the constant-s solution
    n_i(t) = n*_i exp(−t/tau) + s_i tau (1 − exp(−t/tau)),
    booked by the closed manifold (A_slow,i = s_i t_rem and the
    bleed that recovers that n_i). The hour's states are the sums.
    G_i = gross_i dt and R_i = S_i dt, split across the same two
    intervals. Re-solve P* at the end fractions. A move past
    1e-6 Pa means s(y) left the frozen root, and this hour is
    not the shortcut. A fixed P* is not a species check. s_i(y_end)
    is read at that same P*. The mole error of a linear drift is
    |Δs_i| t_rem / 2, compared with the Radau per-species tolerance.
    A larger bound is the reduced ODE or Radau, not this shortcut.

    Unit check: mol/s · s = mol, and Pa·m³ / (J/(mol·K)·K) = mol.
    dt/tau is dimensionless.

    Limit: t << tau expands the exponential,
    n_i(t) = n_i0 + (s_i − y_i0 Q) t, because
    n_i0/tau = y_i0 Σ s and on the manifold Q = Σ s. That is the
    opening drift, and it is the drift about n* once the fast
    mode has put the holdup there. t >> tau sends the exponential
    to 0, so n_i → s_i tau. A second limit of the fast piece:
    t_fast = 0 (already on P*) leaves A_fast = 0 and the hour is
    only the slow integral. Σ s <= 0, a shut pipe, or a transient
    longer than the gate is not this hour.
    """

    if not math.isfinite(dt_s) or dt_s <= 0.0:
        return None
    opening_tau = law.opening_composition_tau_s
    if (
        opening_tau is not None
        and math.isfinite(opening_tau)
        and opening_tau > 0.0
        and dt_s / opening_tau > _FROZEN_COMPOSITION_MAX_DT_OVER_TAU
    ):
        return None
    if law.opening_q_mol_s is None:
        return None
    fractions = _fractions_of(law, n0)
    if fractions is None:
        return None
    try:
        kind, root = _open_or_shut_fixed_point(
            law,
            fractions,
            near_Pa=law.pressure_of(n0),
        )
    except _BalanceRefused as refused:
        _refuse_unless_work_or_roots(refused)
        return None
    if kind != "open" or root is None:
        return None
    try:
        inflow, redox, gross = _manifold_rates(law, fractions, float(root))
    except _BalanceRefused as refused:
        _refuse_unless_work_or_roots(refused)
        return None
    n_star = _moles_along(law, fractions, float(root))
    n_star_tot = math.fsum(n_star[name] for name in law.species)
    s_sum = math.fsum(float(inflow[name]) for name in law.species)
    if (
        n_star_tot <= 0.0
        or s_sum <= 0.0
        or not math.isfinite(n_star_tot)
        or not math.isfinite(s_sum)
    ):
        return None
    tau = n_star_tot / s_sum
    if not math.isfinite(tau) or tau <= 0.0:
        return None
    if dt_s / tau > _FROZEN_COMPOSITION_MAX_DT_OVER_TAU:
        return None
    n_open = math.fsum(max(0.0, float(n0[name])) for name in law.species)
    excess = n_open - n_star_tot
    pa_per_mol = GAS_CONSTANT * law.temperature_K / law.volume_m3
    if abs(excess) * pa_per_mol <= DEFAULT_ABS_TOL_PA:
        t_fast = 0.0
    else:
        q_open = float(law.opening_q_mol_s)
        if excess > 0.0:
            rate = q_open - s_sum
        else:
            rate = s_sum - q_open
        if not math.isfinite(rate) or rate <= 0.0:
            return None
        t_fast = abs(excess) / rate
        if (
            not math.isfinite(t_fast)
            or t_fast < 0.0
            or t_fast > dt_s * _FROZEN_COMPOSITION_MAX_DT_OVER_TAU
        ):
            return None
    t_rem = dt_s - t_fast
    if not math.isfinite(t_rem) or t_rem <= 0.0:
        return None
    a_fast: dict[str, float] = {}
    b_fast: dict[str, float] = {}
    g_fast: dict[str, float] = {}
    r_fast: dict[str, float] = {}
    for name in law.species:
        grown = float(inflow[name]) * t_fast
        bleed = grown + float(n0[name]) - float(n_star[name])
        if (
            not math.isfinite(grown)
            or not math.isfinite(bleed)
            or (bleed < 0.0 and -bleed * pa_per_mol > DEFAULT_ABS_TOL_PA)
        ):
            return None
        a_fast[name] = grown
        b_fast[name] = bleed
        g_fast[name] = float(gross.get(name, 0.0)) * t_fast
        r_fast[name] = float(redox.get(name, 0.0)) * t_fast
    slow = _closed_manifold(
        law.species,
        n_star,
        inflow,
        redox,
        gross,
        t_rem,
    )
    if slow is None:
        return None
    _n_slow, a_slow, b_slow, g_slow, r_slow, composition_time, _dy = slow
    added: dict[str, float] = {}
    bled: dict[str, float] = {}
    vapour: dict[str, float] = {}
    redox_added: dict[str, float] = {}
    n_end: dict[str, float] = {}
    for name in law.species:
        grown = a_fast[name] + float(a_slow[name])
        bleed = b_fast[name] + float(b_slow[name])
        arrived = float(n0[name]) + grown - bleed
        if (
            not all(math.isfinite(item) for item in (grown, bleed, arrived))
            or (bleed < 0.0 and -bleed * pa_per_mol > DEFAULT_ABS_TOL_PA)
            or (arrived < 0.0 and -arrived * pa_per_mol > DEFAULT_ABS_TOL_PA)
        ):
            return None
        added[name] = grown
        bled[name] = bleed
        n_end[name] = arrived
        vapour[name] = g_fast[name] + float(g_slow[name])
        redox_added[name] = r_fast[name] + float(r_slow[name])
    end_fractions = _fractions_of(law, n_end)
    if end_fractions is None:
        return None
    try:
        end_kind, end_root = _open_or_shut_fixed_point(
            law,
            end_fractions,
            near_Pa=float(root),
        )
    except _BalanceRefused as refused:
        _refuse_unless_work_or_roots(refused)
        return None
    if end_kind != "open" or end_root is None:
        return None
    pressure_change = abs(float(end_root) - float(root))
    if pressure_change > DEFAULT_ABS_TOL_PA:
        return None
    if not math.isfinite(composition_time) or composition_time <= 0.0:
        return None
    # Species bound. Σ s can stay fixed while s_i(y) moves, and then
    # P* does not move. The total-pressure re-solve above cannot see
    # that error.
    #
    # Premise: on the manifold Q(P*) = Σ s. The frozen hour holds
    # s_i at the opening composition. The source error is
    # δs_i(t) = s_i(y(t)) − s_i(y_start). While dt/τ ≪ 1 the
    # integrating factor of the composition mode is about 1, so the
    # mole error is ∫ δs_i dt. A linear drift from 0 to
    # Δs_i = s_i(y_end) − s_i(y_start) over t_rem integrates to
    # Δs_i · t_rem / 2. The fast sliver flushes total moles at the
    # frozen opening source and does not add a composition error.
    # s_i(y_end) is evaluated at this opening P*, the pressure the
    # frozen integral used, not at the end re-solve.
    #
    # Algebra: n_err,i = |Δs_i| · t_rem / 2,
    # bound_Pa = n_err,i · R · T / V.
    # When |∂s/∂y| is order s and |Δy| is order t/τ, |Δs| is order
    # s·(t/τ) and n_err is order n·(t/τ)^2 / 2. At dt/τ = 9.9e-4
    # and an 18 Pa partial that is about 7.8e-6 Pa.
    #
    # Unit check: (mol/s)·s = mol, and mol·R·T/V = Pa.
    # Limit: Δs_i = 0 leaves the bound at 0, so a source that does
    # not depend on composition stays on this shortcut. An a-priori
    # (dt/τ)^2 · P* bound would not: (8e-4)^2 · 20 Pa is about
    # 1.3e-5 Pa, above the species tolerance, and the constant-s
    # hour inside the gate has to stay frozen.
    #
    # The tolerance is Radau's per-species test, in pressure:
    # _SLOW_ATOL_PA + _SLOW_RTOL · |P_i|. The local test is
    # rtol·|n_i| + atol_i with atol_i = _SLOW_ATOL_PA · V / (R T).
    # A total-pressure comparison is not that test. _SLOW_ATOL_PA
    # is one tenth of the 1e-6 Pa floor.
    #
    # A bound past that tolerance is not this shortcut. Returning
    # None would mark fast_root, and the mixture admission accepts a
    # total-pressure gap inside 1e-6 Pa with no composition check.
    # A y-dependent source on the manifold has that gap, so the fast
    # root would publish the equilibrium split. The bound is the
    # reduced ODE or Radau. _slow_converge integrates
    # dn_i/dt = q_i + S_i − Q_i and does not call this hour.
    try:
        with _DiagnosticLawSample(law):
            end_inflow, _end_redox, _end_gross = _manifold_rates(
                law,
                end_fractions,
                float(root),
                counted=False,
            )
    except _BalanceRefused as refused:
        _refuse_unless_work_or_roots(refused)
        return None
    dt_over_tau = dt_s / composition_time
    for name in law.species:
        start_y = float(fractions[name])
        end_y = float(end_fractions[name])
        delta_y = end_y - start_y
        delta_s_signed = float(end_inflow[name]) - float(inflow[name])
        y_resolution = sys.float_info.epsilon * max(
            1.0, abs(start_y), abs(end_y),
        )
        if abs(delta_y) <= y_resolution:
            # Fractions are dimensionless in [0, 1], so this change is
            # below one ulp at their scale. Treat its unresolved slope as
            # zero; the bound below still uses the measured |Δs|.
            lambda_i = 0.0
        else:
            lambda_i = delta_s_signed / (s_sum * delta_y)
        # Premise: the composition perturbation evolves at rate (lambda − 1)/tau.
        # Algebra: over r = dt/tau its factor is exp((lambda − 1) r), so
        # it grows only for lambda > 1; there the endpoint triangle can
        # underbound integrated error and this hour must use Radau.
        # Unit check: lambda and r are dimensionless. Limit: lambda = 1
        # is neutral, while a restoring lambda = −1 decays as exp(−2 r).
        if not math.isfinite(lambda_i) or lambda_i > 1.0:
            return _slow_converge(law, n0, dt_s)
        delta_s = abs(delta_s_signed)
        mole_err = delta_s * t_rem / 2.0
        bound_pa = mole_err * pa_per_mol
        # Premise: the exact error / triangle ratio is
        # 1 + (2λ − 1)r/6 + λ(λ − 1)r²/12 + O(r³). For 0 ≤ λ ≤ 1,
        # the quadratic term is nonpositive. If λ > 0.5, the positive
        # linear term is at most λr/6, leaving 5λr/6 of margin; for
        # λ ≤ 0.5 it is nonpositive. At r ≤ 1e-3 that margin covers the
        # higher-order remainder, so 1 + λr bounds the ratio here.
        # Unit check: r and λ are dimensionless. Limit: λ ≤ 0 leaves
        # the original triangle unchanged; λ = 1 gets a 1 + r margin.
        bound_pa *= 1.0 + max(lambda_i, 0.0) * dt_over_tau
        partial_pa = abs(float(end_fractions[name]) * float(root))
        tol = _SLOW_ATOL_PA + _SLOW_RTOL * partial_pa
        if not math.isfinite(bound_pa) or bound_pa > tol:
            return _slow_converge(law, n0, dt_s)
    law._remember_species_notices((
        {
            "species": "headspace",
            "reason": "quasi_steady_frozen_composition",
            "dt_over_tau": dt_s / float(composition_time),
            "tau_s": float(composition_time),
            "t_fast_s": float(t_fast),
            "pressure_change_Pa": float(pressure_change),
        },
    ))
    _mark_balance_path(law, "frozen_composition")
    return law.step_from_moles(
        n0,
        dt_s,
        n_end,
        2,
        pressure_change,
        added_mol=added,
        bled_mol_in=bled,
        vapour_extent_kg=vapour,
        redox_added_mol=redox_added,
    )


def _commit_one(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
) -> _HeadspaceStep:
    closed = _closed_pipe_accumulation(law, n0, dt_s)
    if closed is not None:
        return closed
    frozen = _frozen_composition_hour(law, n0, dt_s)
    if frozen is not None:
        return frozen
    _mark_balance_path(law, "fast_root")
    samples = law.g_samples()
    crossings = _crossings(samples)
    if len(crossings) >= 2:
        raise _BalanceRefused("headspace_balance_multiple_roots")
    if len(crossings) == 1:
        root, iterations = _solve_bracket(
            law._scalar_g,
            law.piece_at,
            crossings[0],
            law.pressure_of(n0),
        )
        if _admits(law, law.pressure_of(n0), root, dt_s):
            moles = law.moles_at_pressure(root)
            # |G| * Δt * R T / V is the pressure that imbalance would
            # accumulate. At the published root it is under 1e-6 Pa.
            imbalance = abs(law._scalar_g(root)) * dt_s
            residual_pa = imbalance * (
                GAS_CONSTANT * law.temperature_K / law.volume_m3
            )
            return law.step_from_moles(
                n0,
                dt_s,
                moles,
                iterations,
                residual_pa,
            )
    return _slow_converge(law, n0, dt_s)


def _slice_value(
    law: _HeadspaceLaw,
    moles: Mapping[str, float],
    species: str,
    n_i: float,
    n0: Mapping[str, float],
    dt_s: float,
    mode: str,
) -> float:
    trial = dict(moles)
    trial[species] = n_i
    partials = law.partials_of(trial)
    source, outflow, _binding, _piece = law._flows(partials)
    redox = law._redox_rates(partials)[species]
    if mode == "fast":
        return source[species] + redox - outflow[species]
    return (
        n_i
        - n0[species]
        - dt_s * (source[species] + redox - outflow[species])
    )


def _expand_from_guess(
    func: Callable[[float], float],
    guess: float,
    n_hi: float,
    *,
    direction: str,
    crossings_of: Callable[
        [list[tuple[float, float]]],
        list[tuple[str, float, float]],
    ],
    empty_below: float | None = None,
) -> tuple[list[tuple[float, float]], bool]:
    """Factor-of-two expansion from `guess` until one confirmed root.

    Premise: the fast residual decreases in n_i and the slow residual
    increases, once the capture fraction is held fixed. The root is
    then on one side of the guess. Endpoints of the other side are
    one sample, not a walk from 1e-16 mol. A far-side sample at or
    below `empty_below` is the empty root: the caller drops a
    crossing whose upper mole is inside the pressure floor, so
    doubling on up to 1e8 Pa cannot publish a different root.
    Algebra: n_{k+1} = min(2 n_k, n_hi). The first far-side sample
    arms the stop; the next sample confirms it, so a well narrower
    than one factor of two still shows a second crossing.
    Unit check: each node is moles.
    Limit: a non-positive guess starts at 1e-16 mol when n_hi is
    positive. A reversal does not append the bad sample. The caller
    then uses the log mole scan.
    """

    hi = max(0.0, float(n_hi))
    center = min(max(float(guess), 0.0), hi) if hi > 0.0 else 0.0
    samples: list[tuple[float, float]] = []

    def evaluate(point: float) -> float:
        value = func(point)
        if not math.isfinite(value):
            raise _BalanceRefused("headspace_balance_nonconvergence")
        return value

    def adverse(earlier: float, later: float) -> bool:
        # earlier is the residual at the lower mole.
        if direction == "down":
            step = later - earlier
        else:
            step = earlier - later
        scale = max(abs(earlier), abs(later))
        return step > max(_MONOTONE_ABS, _MONOTONE_REL * scale)

    def append(point: float, value: float) -> bool:
        if samples and point < samples[-1][0]:
            raise _BalanceRefused("headspace_balance_nonconvergence")
        if samples and point == samples[-1][0]:
            return False
        if samples and adverse(samples[-1][1], value):
            return True
        samples.append((point, value))
        if len(crossings_of(samples)) >= 2:
            raise _BalanceRefused("headspace_balance_multiple_roots")
        return False

    def far(value: float) -> bool:
        return value < 0.0 if direction == "down" else value > 0.0

    def confirmed() -> bool:
        found = crossings_of(samples)
        return len(found) == 1 and far(samples[-1][1])

    g0 = evaluate(0.0)
    if append(0.0, g0):
        return samples, True
    if center > 0.0:
        g_center = evaluate(center)
        if append(center, g_center):
            return samples, True
    else:
        g_center = g0
    # A zero at the empty holdup that never crosses has no bracket.
    # The closed-pipe plateau is that state. Halving down to it
    # would only repeat the zero.
    if direction == "down" and g0 == 0.0 and g_center <= 0.0:
        return samples, False
    if direction == "up" and g0 == 0.0 and g_center >= 0.0:
        return samples, False
    if direction == "down":
        upward = g_center > 0.0
    else:
        upward = g_center < 0.0
    if not upward:
        # The sign change sits between 0 and the guess. Halve until
        # the bracket is one factor of two. Brent on [0, guess]
        # spends an evaluation per bit of that range.
        # Algebra: n_{k+1} = n_k / 2. Stop at the first far-side
        # sample, or after 16 steps.
        # Unit check: moles.
        # Limit: a same-sign pair is not a bracket and is not halved.
        crossed_below = (
            (direction == "down" and g0 > 0.0 and g_center < 0.0)
            or (direction == "up" and g0 < 0.0 and g_center > 0.0)
        )
        if center > 0.0 and crossed_below:
            chain = [(0.0, g0), (center, g_center)]
            point = center
            previous_value = g_center
            for _step in range(16):
                nxt = point * 0.5
                value = evaluate(nxt)
                if adverse(value, previous_value):
                    return samples, True
                chain.append((nxt, value))
                chain.sort()
                samples = list(chain)
                if len(crossings_of(samples)) >= 2:
                    raise _BalanceRefused("headspace_balance_multiple_roots")
                point = nxt
                previous_value = value
                crossed = (
                    value >= 0.0 if direction == "down" else value <= 0.0
                )
                if crossed:
                    break
                # Still on the near side at the pressure floor. The
                # root is under that floor. The caller commits the
                # empty root. Further halvings do not move it.
                if empty_below is not None and nxt <= empty_below:
                    break
            return samples, False
        return samples, False
    point = center
    if point <= 0.0 and hi > 0.0:
        point = min(1.0e-16, hi)
        if append(point, evaluate(point)):
            return samples, True
    floor = None if empty_below is None else max(0.0, float(empty_below))
    if floor is not None and point < floor < hi:
        # One sample at the floor mole. A far-side residual there
        # means the only sign change is under the floor.
        # Algebra: the next walk node is max(2 point, n_floor).
        # Unit check: moles. Limit: a near-side residual at the
        # floor means the root is above it, and doubling continues.
        if append(floor, evaluate(floor)):
            return samples, True
        point = floor
        if far(samples[-1][1]):
            return samples, False
    armed = confirmed()
    while point < hi:
        nxt = point * 2.0
        if nxt >= hi or nxt <= point:
            nxt = hi
        if append(nxt, evaluate(nxt)):
            return samples, True
        point = nxt
        if confirmed():
            if armed:
                return samples, False
            armed = True
        else:
            armed = False
        if point >= hi:
            break
    return samples, False


def _solve_slice(
    law: _HeadspaceLaw,
    moles: Mapping[str, float],
    species: str,
    n0: Mapping[str, float],
    dt_s: float,
    mode: str,
    guess: float | None = None,
) -> tuple[dict[str, float], float, int]:
    def func(n_i: float) -> float:
        return _slice_value(law, moles, species, n_i, n0, dt_s, mode)

    if mode == "slow":
        frozen = dict(moles)
        frozen[species] = 0.0
        frozen_partials = law.partials_of(frozen)
        source0, _outflow, _binding, _piece = law._flows(frozen_partials)
        n_hi = n0[species] + max(
            0.0,
            source0[species] + law._redox_rates(frozen_partials)[species],
        ) * dt_s
    else:
        n_hi = max(
            1.0e8 * law.volume_m3 / (GAS_CONSTANT * law.temperature_K),
            n0[species],
            moles[species],
            1.0e-12,
        )
    # Premise: the fast residual cannot tell two partials apart once
    # both sit inside DEFAULT_ABS_TOL_PA. A sign change whose upper
    # mole is at or below that floor is the empty root, not a second
    # physical root. An exact zero at n = 0 stays, so a sourceless
    # species still commits empty.
    # Algebra: n_floor = P_floor * V / (R T).
    # Unit check: Pa * m^3 / (J/(mol·K) * K) = mol.
    # Limit: a bracket above the floor still counts. Two of those
    # still refuse.
    n_floor = (
        DEFAULT_ABS_TOL_PA
        * law.volume_m3
        / (GAS_CONSTANT * law.temperature_K)
    )

    def roots_of(samples_so_far):
        kept = []
        for kind, lo, hi in _crossings(samples_so_far):
            if hi <= n_floor and not (kind == "exact" and lo == 0.0):
                continue
            kept.append((kind, lo, hi))
        return kept

    # Fast G is non-increasing in n_i. Slow R is non-decreasing.
    # A reversal keeps the 257-point grid and flags the hour.
    # The expansion center is the ray-root mole on the first pass
    # and the current mole after that. It is not a walk from 1e-16.
    direction = "up" if mode == "slow" else "down"
    center = float(moles[species]) if guess is None else max(0.0, float(guess))
    samples, fallback = _expand_from_guess(
        func,
        center,
        n_hi,
        direction=direction,
        crossings_of=roots_of,
        empty_below=n_floor,
    )
    if fallback:
        _remember_sign_scan_fallback(
            law,
            "the species residual reversed on the geometric bracket; "
            "the log mole scan published the crossings",
        )
        samples = _species_log_scan(func, n_hi, roots_of)
    crossings = roots_of(samples)
    if len(crossings) >= 2:
        raise _BalanceRefused("headspace_balance_multiple_roots")
    if len(crossings) == 0:
        incoming = float(moles[species])
        # Premise: P_up <= P_down closes the pipe, so Q is 0. A
        # species with q + S = 0 then has G = 0 at every mole that
        # leaves the total pressure on that closed side. That plateau
        # is the root. The scan samples are zero and then negative
        # once the pipe opens, and a run of zeros is not a sign
        # change, so the bracket search reports no root.
        # Algebra: n_end = n_incoming. With q + S = 0,
        # bled = n0 - n_end.
        # Unit check: both amounts are moles.
        # Limit: a positive source on the closed side has G = q > 0,
        # so this does not fire. A residual that is nonzero at the
        # incoming mole still falls through.
        if func(incoming) == 0.0:
            crossings = [("exact", incoming, incoming)]
    if len(crossings) == 0:
        # A sign change that lived entirely under the floor was
        # dropped above. The holdup it would publish is inside
        # 1e-6 Pa, so the empty root is that root. A scan whose
        # largest residual cannot accumulate that floor over the
        # hour is the same state. A residual that stays on one side
        # and can accumulate more than the floor has no root here.
        raw = _crossings(samples)
        under_floor = bool(raw) and all(
            hi <= n_floor for _kind, _lo, hi in raw
        )
        peak = max(abs(value) for _pressure, value in samples)
        if under_floor or peak * dt_s <= n_floor:
            crossings = [("exact", 0.0, 0.0)]
        else:
            raise _BalanceRefused("headspace_balance_nonconvergence")

    def piece_at(n_i: float) -> tuple:
        trial = dict(moles)
        trial[species] = max(n_i, 0.0)
        _source, _outflow, _binding, piece = law._flows(
            law.partials_of(trial),
            evaluate_source=False,
        )
        return piece

    root, iterations = _solve_bracket(
        func,
        piece_at,
        crossings[0],
        moles[species],
    )
    updated = dict(moles)
    old_partial = law.partials_of(moles)[species]
    updated[species] = root
    new_partial = law.partials_of(updated)[species]
    return updated, abs(new_partial - old_partial), iterations


def _sweep(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    mode: str,
    *,
    rel_tol: float,
    max_iterations: int,
    seed: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], int, float]:
    # The mole vector starts at n0. A ray seed is only the first
    # pass's expansion center. The plateau commits n0 when q + S = 0,
    # and a seed must not replace that holdup.
    moles = {species: max(0.0, n0[species]) for species in law.species}
    total_iterations = 0
    last_move = math.inf
    for sweep_index in range(max_iterations):
        last_move = 0.0
        for species in law.species:
            hint = None
            if seed is not None and sweep_index == 0:
                hint = seed[species]
            moles, move, iterations = _solve_slice(
                law,
                moles,
                species,
                n0,
                dt_s,
                mode,
                guess=hint,
            )
            last_move = max(last_move, move)
            total_iterations += iterations
            _remember_balance_iterations(law, total_iterations)
        scale = max(law.pressure_of(moles), 1.0)
        if last_move <= max(DEFAULT_ABS_TOL_PA, rel_tol * scale):
            return moles, max(total_iterations, 1), last_move
    _remember_balance_iterations(law, total_iterations)
    raise _BalanceRefused("headspace_balance_nonconvergence")


def _euler_many(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    rel_tol: float,
    max_iterations: int,
) -> _HeadspaceStep:
    moles, iterations, residual_pa = _sweep(
        law,
        n0,
        dt_s,
        "slow",
        rel_tol=rel_tol,
        max_iterations=max_iterations,
    )
    return law.step_from_moles(n0, dt_s, moles, iterations, residual_pa)


def _mixture_partials(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    pressure: float,
) -> dict[str, float]:
    if pressure <= 0.0:
        return {species: 0.0 for species in law.species}
    return {
        species: pressure * fractions[species] for species in law.species
    }


def _mixture_g(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    pressure: float,
) -> float:
    # G = Σ (q_i + S_i − Q_i) along one composition. Capture is held
    # outside this walk: a capture that jumps with P is not a
    # continuous mean free path, and the bracket would fall back.
    partials = _mixture_partials(law, fractions, pressure)
    source, outflow, _binding, _piece = law._flows(partials)
    redox = law._redox_rates(partials)
    return math.fsum(
        source[species] + redox[species] - outflow[species]
        for species in law.species
    )


def _mixture_piece(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    pressure: float,
) -> tuple:
    # q is not in the piece tuple. The same sample as law.piece_at.
    _source, _outflow, _binding, piece = law._flows(
        _mixture_partials(law, fractions, max(pressure, 0.0)),
        evaluate_source=False,
    )
    return piece


def _ray_samples(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    *,
    refuse_multiple: bool = True,
) -> list[tuple[float, float]]:
    return _g_pressure_samples(
        law,
        lambda pressure: _mixture_g(law, fractions, pressure),
        "G(P) rose on the geometric bracket; the log sign scan "
        "published the crossings",
        refuse_multiple=refuse_multiple,
    )


def _fractions_of(
    law: _HeadspaceLaw,
    moles: Mapping[str, float],
) -> dict[str, float] | None:
    total = math.fsum(max(0.0, moles[species]) for species in law.species)
    if total <= 0.0:
        return None
    return {
        species: max(0.0, moles[species]) / total for species in law.species
    }


def _moles_along(
    law: _HeadspaceLaw,
    fractions: Mapping[str, float],
    pressure: float,
) -> dict[str, float]:
    """n_i = max(y_i, 0) * max(P, 0) * V / (R T).

    The fast-root seed and the quasi-steady hold both place one
    composition at one total pressure. Unit check:
    Pa * m^3 / (J/(mol·K) * K) = mol. Limit: P <= 0 is an empty holdup.
    """

    if pressure <= 0.0:
        return {species: 0.0 for species in law.species}
    per_pascal = law.volume_m3 / (GAS_CONSTANT * law.temperature_K)
    return {
        species: pressure * max(0.0, fractions[species]) * per_pascal
        for species in law.species
    }


def _state_slope(law: _HeadspaceLaw, moles: Mapping[str, float]) -> float | None:
    """dQ_kg/dm at fixed composition. None when the stencil crosses a kink.

    Algebra: a = (Q(m(1+ε)) - Q(m(1-ε))) / (m(1+ε) - m(1-ε)).
    Unit check: (kg/s) / kg = 1/s. This is the mixture form of a_min.
    The one-species commit uses the pressure form instead, which the
    stranded-hour pins already exercise. Limit: an empty holdup has
    no slope.
    """

    total = math.fsum(max(0.0, moles[species]) for species in law.species)
    if total <= 0.0:
        return None
    epsilon = 1.0e-6

    def evaluate(scale: float) -> tuple[float, float, tuple]:
        scaled = {
            species: max(0.0, moles[species]) * scale for species in law.species
        }
        _source, outflow, _binding, piece = law._flows(law.partials_of(scaled))
        mass = math.fsum(
            scaled[species] * law.masses[species] for species in law.species
        )
        flow = math.fsum(
            outflow[species] * law.masses[species] for species in law.species
        )
        return mass, flow, piece

    mass_0, _flow_0, piece_0 = evaluate(1.0)
    mass_up, flow_up, piece_up = evaluate(1.0 + epsilon)
    mass_down, flow_down, piece_down = evaluate(1.0 - epsilon)
    if piece_up != piece_0 or piece_down != piece_0:
        return None
    delta_mass = mass_up - mass_down
    if delta_mass <= 0.0 or mass_0 <= 0.0:
        return None
    slope = (flow_up - flow_down) / delta_mass
    if not math.isfinite(slope):
        return None
    # A flat piece is a measured 0, not a missing stencil. Admission
    # treats 0 as a_min = 0. A kink above still returns None.
    if slope <= 0.0:
        return 0.0
    return slope


def _admits_mixture(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    n_root: Mapping[str, float],
    dt_s: float,
) -> bool:
    # Same bound as the one-species test. a_min is the smallest
    # positive dQ/dm on the segment. A flat sample is 0 and does not
    # admit. A kink (None) is skipped. No measured positive slope does
    # not admit: the slow branch then publishes the backward-Euler state.
    gap = abs(law.pressure_of(n0) - law.pressure_of(n_root))
    if gap <= DEFAULT_ABS_TOL_PA:
        return True
    slopes: list[float] = []
    for weight in (0.0, 0.25, 0.5, 0.75, 1.0):
        state = {
            species: (1.0 - weight) * n0[species] + weight * n_root[species]
            for species in law.species
        }
        slope = _state_slope(law, state)
        if slope is None:
            continue
        if slope <= 0.0:
            return False
        slopes.append(slope)
    if not slopes:
        return False
    product = min(slopes) * dt_s
    if product >= _ADMISSION_EXP_UNDERFLOW:
        return True
    factor = math.exp(-product)
    if factor == 0.0:
        return True
    return gap * factor <= DEFAULT_ABS_TOL_PA


def _commit_many(
    law: _HeadspaceLaw,
    n0: Mapping[str, float],
    dt_s: float,
    *,
    rel_tol: float,
    max_iterations: int,
) -> _HeadspaceStep:
    closed = _closed_pipe_accumulation(law, n0, dt_s)
    if closed is not None:
        return closed
    frozen = _frozen_composition_hour(law, n0, dt_s)
    if frozen is not None:
        return frozen
    _mark_balance_path(law, "fast_root")
    fractions = _fractions_of(law, n0)
    if fractions is None:
        zero = {species: 0.0 for species in law.species}
        zero_partials = law.partials_of(zero)
        source, _outflow, _binding, _piece = law._flows(zero_partials)
        redox = law._redox_rates(zero_partials)
        weights = {
            species: max(0.0, source[species] + redox[species])
            for species in law.species
        }
        total = math.fsum(weights.values())
        if total <= 0.0:
            # Premise: n0 = 0 and q + S <= 0 at the zero partial, so
            # G = q + S - Q is 0 at P = 0. The pipe flow Q is 0 there
            # because the holdup is empty. A composition ray needs a
            # positive weight, and this state has none.
            # Algebra: n_end = 0, bled = 0.
            # Unit check: 0 mol/s * s = 0 mol.
            # Limit: a positive source builds the ray below instead.
            return law.step_from_moles(n0, dt_s, zero, 1, 0.0)
        fractions = {species: weights[species] / total for species in law.species}
    crossings = _crossings(_ray_samples(law, fractions))
    if len(crossings) >= 2:
        raise _BalanceRefused("headspace_balance_multiple_roots")
    if len(crossings) == 1:
        # Brent the mixture residual on the geometric bracket, then
        # seed the species sweep at that composition. The seed is
        # n_i = y_i P V / (R T). The sweep still starts from n0, so
        # a closed-pipe plateau keeps the opening holdup.
        # Unit check: Pa * m^3 / (J/(mol·K) * K) = mol.
        # Limit: an exact root at 0 Pa seeds an empty composition.
        def total_g(pressure: float) -> float:
            return _mixture_g(law, fractions, pressure)

        root_pressure, _ray_iterations = _solve_bracket(
            total_g,
            lambda pressure: _mixture_piece(law, fractions, pressure),
            crossings[0],
            law.pressure_of(n0),
        )
        seed = _moles_along(law, fractions, root_pressure)
        # A fast sweep that finds no bracket, or misses its tolerance,
        # is a state the slow branch can still solve. Two roots already
        # raised above. Any other refusal still propagates.
        try:
            moles, iterations, residual_pa = _sweep(
                law,
                n0,
                dt_s,
                "fast",
                rel_tol=rel_tol,
                max_iterations=max_iterations,
                seed=seed,
            )
        except _BalanceRefused as refused:
            if refused.reason != "headspace_balance_nonconvergence":
                raise
            return _slow_converge(law, n0, dt_s)
        if _admits_mixture(law, n0, moles, dt_s):
            return law.step_from_moles(n0, dt_s, moles, iterations, residual_pa)
    return _slow_converge(law, n0, dt_s)


def _publish(
    law: _HeadspaceLaw,
    step: _HeadspaceStep,
    capacity: NoColdTrain | FiniteCapacity,
    vessel_rating_Pa: float | None,
    *,
    authoritative: bool = False,
) -> CapacityShadowResult | CapacityShadowRefusal:
    if (
        vessel_rating_Pa is not None
        and step.pressure_Pa > vessel_rating_Pa
    ):
        return CapacityShadowRefusal(
            "vessel_total_pressure_exceeds_rating:"
            f"{step.pressure_Pa:.17g}>{vessel_rating_Pa:.17g}",
            step.iterations,
        )
    evap_kg_hr = math.fsum(step.flux_kg_hr.values())
    oxygen_evap = step.flux_kg_hr.get(OXYGEN_SPECIES, 0.0)
    _source, outflow, _binding, _piece = law._flows(step.partials)
    pipe_kg_hr = (
        math.fsum(
            outflow[species] * law.masses[species] for species in law.species
        )
        * 3600.0
    )
    if pipe_kg_hr > 0.0:
        # Premise: saturation = evaporation / pipe flow. At the
        # quasi-steady root Q = q, so the ratio is 1. That is the
        # root, not spare capacity and not an overload. The hour ramp
        # throttles only when the combined ratio is above 1. A reported
        # 1.012 is a real excess on a binding conductance and stays
        # 1.012. Do not clamp a root of 1 down onto a spare-capacity
        # reading, and do not clamp a real excess onto 1.
        # Algebra: saturation = evap_kg_hr / pipe_kg_hr.
        # Unit check: (kg/h) / (kg/h) = 1.
        # Limit: Q = q gives 1. Q slightly under q gives a number
        # above 1, and the ramp scales that hour.
        pipe_saturation = evap_kg_hr / pipe_kg_hr
    elif (
        evap_kg_hr > 0.0
        and step.pressure_Pa <= DEFAULT_ABS_TOL_PA
        and step.bled_kg > 0.0
    ):
        # Premise: the ramp stops at infinite saturation. End-state
        # pipe flow is 0 when the committed holdup is empty. A source
        # that was booked out (bled_kg > 0) at a pressure inside the
        # 1e-6 Pa floor did leave through the pipe. That is within
        # capacity, so the ratio is 1. The throttle starts above 100%.
        # Algebra: saturation = source / throughput = 1 at a cleared root.
        # Unit check: both masses are kilograms, so the ratio is 1.
        # Limit: a line that is idle only because P_up is still below
        # P_down is the next branch. A sealed end with pressure above
        # that downstream pressure still reports inf.
        pipe_saturation = 1.0
    elif (
        evap_kg_hr > 0.0
        and law.diameter_m is not None
        and step.pressure_Pa <= law.downstream_pa
    ):
        # Premise: Q is 0 because P_up <= P_down. The duct is idle,
        # not full. The hour's source is accumulating toward the
        # pressure that opens the pipe. Infinite saturation freezes
        # the ramp on that approach, so the inventory never gets there.
        # Algebra: saturation = 0 while the forward check is closed.
        # Unit check: the ratio is dimensionless.
        # Limit: P_up above P_down with Q still 0 is a sealed end,
        # and the branch below reports inf.
        pipe_saturation = 0.0
    else:
        pipe_saturation = math.inf if evap_kg_hr > 0.0 else 0.0
    oxygen_saturation = (
        oxygen_evap / capacity.value_kg_hr
        if isinstance(capacity, FiniteCapacity)
        else None
    )
    if oxygen_saturation is None or pipe_saturation >= oxygen_saturation:
        combined = pipe_saturation
    else:
        combined = oxygen_saturation
    bled = dict(step.bled_mol)
    terminal = {
        species: moles
        for species, moles in bled.items()
        if species != OXYGEN_SPECIES
    }
    booked = step.mass_start_kg + step.source_kg
    accounted = step.mass_end_kg + step.bled_kg
    closure = (
        abs(booked - accounted) / booked * 100.0 if booked > 0.0 else 0.0
    )
    return CapacityShadowResult(
        capacity=capacity,
        partial_pressures_Pa=step.partials,
        evaporation_flux_kg_hr=step.flux_kg_hr,
        bled_species_mol=bled,
        terminal_offgas_mol=terminal,
        oxygen=OxygenShadowPartition(0.0, 0.0, 0.0, 0.0, 0.0),
        saturation=SaturationShadow(
            pipe_saturation,
            oxygen_saturation,
            combined,
            step.binding,
        ),
        iterations=step.iterations,
        max_delta_history_Pa=(step.residual_Pa,),
        mass_closure_error_pct=closure,
        authoritative=authoritative,
        species_notices=tuple(law.species_notices.values()),
        vapour_extent_kg=dict(step.vapour_extent_kg),
        redox_added_mol=dict(step.redox_added_mol),
    )


def _build_law(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    capacity: NoColdTrain | FiniteCapacity,
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    downstream_pressure_Pa: float,
    pipe_diameter_m: float | None,
    pipe_length_m: float | None,
    outflow_cap_kg_s: float | None,
    redox_source_mol_s: Mapping[str, float] | None,
    redox_mol_s_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
    extent_holder: dict | None = None,
) -> tuple[_HeadspaceLaw, dict[str, float]] | CapacityShadowRefusal:
    try:
        if not all(
            math.isfinite(value) and value > 0.0
            for value in (temperature_K, volume_m3, dt_hr)
        ):
            raise _BalanceRefused(
                "temperature_K, volume_m3, and dt_hr must be finite and positive"
            )
        downstream = _as_finite(
            downstream_pressure_Pa,
            "downstream_pressure_Pa must be finite and >= 0",
        )
        if downstream < 0.0:
            raise _BalanceRefused(
                "downstream_pressure_Pa must be finite and >= 0"
            )
        has_diameter = pipe_diameter_m is not None
        has_length = pipe_length_m is not None
        if has_diameter != has_length:
            raise _BalanceRefused("pipe_geometry_incomplete")
        diameter: float | None = None
        length: float | None = None
        if has_diameter:
            diameter = _as_finite(
                pipe_diameter_m,
                "pipe_diameter_m and pipe_length_m must be finite and positive",
            )
            length = _as_finite(
                pipe_length_m,
                "pipe_diameter_m and pipe_length_m must be finite and positive",
            )
            if diameter <= 0.0 or length <= 0.0:
                raise _BalanceRefused(
                    "pipe_diameter_m and pipe_length_m must be finite and positive"
                )
        cap_kg_s: float | None = None
        if isinstance(capacity, FiniteCapacity):
            # value_kg_hr is kg/h. / 3600 is kg/s. Unit check: kg/h / (s/h) = kg/s.
            cap_kg_s = capacity.value_kg_hr / 3600.0
        if outflow_cap_kg_s is not None:
            explicit = _as_finite(
                outflow_cap_kg_s,
                "outflow_cap_kg_s must be finite and >= 0",
            )
            if explicit < 0.0:
                raise _BalanceRefused("outflow_cap_kg_s must be finite and >= 0")
            cap_kg_s = explicit if cap_kg_s is None else min(cap_kg_s, explicit)
        if diameter is None and cap_kg_s is None:
            raise _BalanceRefused("headspace_balance_no_outflow")
        redox: dict[str, float] = {}
        if redox_source_mol_s is not None:
            for name, rate in redox_source_mol_s.items():
                redox[str(name)] = _as_finite(
                    rate,
                    f"redox_source_mol_s must be finite:{name}",
                )
        # One flux evaluation at the opening inventory. Species that
        # source introduces join the set at n0 = 0. A name with no
        # molar mass is a typed refusal, not a dropped source. Later
        # trials that invent a further name still refuse in _flux_kg_hr.
        factor = GAS_CONSTANT * temperature_K / volume_m3
        opening_partials: dict[str, float] = {}
        for name, amount in pre_holdup_mol.items():
            number = _as_finite(
                amount,
                "pre_holdup_mol must be finite and >= 0",
            )
            if number < 0.0:
                raise _BalanceRefused("pre_holdup_mol must be finite and >= 0")
            if number > 0.0:
                opening_partials[str(name)] = number * factor
        from simulator.evaporation import EvaporationFluxRefusal

        try:
            discovered = flux_kg_hr_at_partials(opening_partials)
        except EvaporationFluxRefusal:
            raise
        except _BalanceRefused:
            raise
        except Exception as exc:
            raise _BalanceRefused(
                f"evaporation_flux_refused:{type(exc).__name__}:{exc}"
            ) from exc
        if not isinstance(discovered, Mapping):
            raise _BalanceRefused("evaporation_flux_not_a_mapping")
        discovered_names: set[str] = set()
        for name, rate in discovered.items():
            _as_finite(rate, f"evaporation_flux_not_finite:{name}")
            discovered_names.add(str(name))
        names = set(pre_holdup_mol) | set(redox) | discovered_names
        if not names:
            raise _BalanceRefused("headspace_balance_no_species")
        masses: dict[str, float] = {}
        n0: dict[str, float] = {}
        for name in sorted(names):
            if name not in molar_mass_kg_mol:
                raise _BalanceRefused(f"missing_molar_mass:{name}")
            mass = _as_finite(
                molar_mass_kg_mol[name],
                f"missing_molar_mass:{name}",
            )
            if mass <= 0.0:
                raise _BalanceRefused(f"missing_molar_mass:{name}")
            masses[name] = mass
            amount = _as_finite(
                pre_holdup_mol.get(name, 0.0),
                "pre_holdup_mol must be finite and >= 0",
            )
            if amount < 0.0:
                raise _BalanceRefused("pre_holdup_mol must be finite and >= 0")
            n0[name] = amount
        law = _HeadspaceLaw(
            species=tuple(sorted(names)),
            masses=masses,
            flux=flux_kg_hr_at_partials,
            temperature_K=temperature_K,
            volume_m3=volume_m3,
            downstream_pa=downstream,
            diameter_m=diameter,
            length_m=length,
            cap_kg_s=cap_kg_s,
            redox_mol_s=redox,
            redox_mol_s_at_partials=redox_mol_s_at_partials,
            extent_holder=extent_holder,
        )
        return law, n0
    except _BalanceRefused as refused:
        return _shadow_from_balance(refused)


def _commit_headspace_balance(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    capacity: NoColdTrain | FiniteCapacity,
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    downstream_pressure_Pa: float,
    vessel_rating_Pa: float | None,
    pipe_diameter_m: float | None,
    pipe_length_m: float | None,
    outflow_cap_kg_s: float | None,
    redox_source_mol_s: Mapping[str, float] | None,
    redox_mol_s_at_partials: (
        Callable[[Mapping[str, float]], Mapping[str, float]] | None
    ) = None,
    authoritative: bool = False,
    rel_tol: float = DEFAULT_REL_TOL,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    extent_holder: dict | None = None,
) -> CapacityShadowResult | CapacityShadowRefusal:
    built = _build_law(
        pre_holdup_mol=pre_holdup_mol,
        molar_mass_kg_mol=molar_mass_kg_mol,
        flux_kg_hr_at_partials=flux_kg_hr_at_partials,
        capacity=capacity,
        temperature_K=temperature_K,
        volume_m3=volume_m3,
        dt_hr=dt_hr,
        downstream_pressure_Pa=downstream_pressure_Pa,
        pipe_diameter_m=pipe_diameter_m,
        pipe_length_m=pipe_length_m,
        outflow_cap_kg_s=outflow_cap_kg_s,
        redox_source_mol_s=redox_source_mol_s,
        redox_mol_s_at_partials=redox_mol_s_at_partials,
        extent_holder=extent_holder,
    )
    if isinstance(built, CapacityShadowRefusal):
        return built
    law, n0 = built
    dt_s = dt_hr * 3600.0
    from simulator.evaporation import EvaporationFluxRefusal

    try:
        law._record_opening_scales(n0, dt_s)
        if len(law.species) == 1:
            step = _commit_one(law, n0, dt_s)
        else:
            step = _commit_many(
                law,
                n0,
                dt_s,
                rel_tol=rel_tol,
                max_iterations=max_iterations,
            )
    except EvaporationFluxRefusal:
        raise
    except _BalanceRefused as refused:
        return _refused_shadow(law, refused)
    return _publish(
        law,
        step,
        capacity,
        vessel_rating_Pa,
        authoritative=authoritative,
    )


def diagnostic_backward_euler_headspace_step(
    *,
    pre_holdup_mol: Mapping[str, float],
    molar_mass_kg_mol: Mapping[str, float],
    flux_kg_hr_at_partials: Callable[[Mapping[str, float]], Mapping[str, float]],
    temperature_K: float,
    volume_m3: float,
    dt_hr: float,
    downstream_pressure_Pa: float,
    pipe_diameter_m: float | None = None,
    pipe_length_m: float | None = None,
    outflow_cap_kg_s: float | None = None,
    redox_source_mol_s: Mapping[str, float] | None = None,
    capacity: NoColdTrain | FiniteCapacity | None = None,
    vessel_rating_Pa: float | None = None,
) -> CapacityShadowResult | CapacityShadowRefusal:
    """One R = 0 step. Not the fast root, and not halved.

    The commit solves G = 0 when admission holds. This function always
    solves the backward-Euler residual, so a single 3600 s step of the
    stranded hour stays at 248.85783689 Pa.
    """

    if capacity is None:
        capacity = NoColdTrain()
    built = _build_law(
        pre_holdup_mol=pre_holdup_mol,
        molar_mass_kg_mol=molar_mass_kg_mol,
        flux_kg_hr_at_partials=flux_kg_hr_at_partials,
        capacity=capacity,
        temperature_K=temperature_K,
        volume_m3=volume_m3,
        dt_hr=dt_hr,
        downstream_pressure_Pa=downstream_pressure_Pa,
        pipe_diameter_m=pipe_diameter_m,
        pipe_length_m=pipe_length_m,
        outflow_cap_kg_s=outflow_cap_kg_s,
        redox_source_mol_s=redox_source_mol_s,
    )
    if isinstance(built, CapacityShadowRefusal):
        return built
    law, n0 = built
    from simulator.evaporation import EvaporationFluxRefusal

    try:
        step = _one_euler(law, n0, dt_hr * 3600.0)
    except EvaporationFluxRefusal:
        raise
    except _BalanceRefused as refused:
        return _refused_shadow(law, refused)
    return _publish(law, step, capacity, vessel_rating_Pa)
