"""Gibbs-Duhem consistency residual for melt-activity engines (commissioning battery).

WHY THIS EXISTS (owner-ratified 2026-08-18). Cross-engine agreement is weak
evidence (engines can share a premise) and external anchors are scarce. The
Gibbs-Duhem relation is a third kind of check: an IDENTITY every thermodynamically
consistent activity model must satisfy, testable with no external data at all.
A nonzero residual is proof of a wrong-SHAPE model — the class of defect that
bug-hunting cannot reach. Stated carefully after review (2026-08-19): GD
catches activity sets NOT derived from any single G function (independent
per-species correlations, patched continuity shells). It does NOT catch a
wrong-but-self-consistent G — an ideal-associated-solution with a mis-wired
species is still a consistent model of a different melt, so the IMCC
alkali defect specifically would have needed the external anchor regardless.
The gate any future multi-component excess-G model (MC-5 / t-529) must pass
before its numbers are citable is this one; the anchor comparisons remain the
detector for consistent-but-wrong.

DERIVATION (constant T and P, closed system):
    Gibbs-Duhem:            sum_i x_i d(mu_i) = 0
    mu_i = mu_i0 + RT ln a_i  =>  sum_i x_i d ln a_i = 0
    a_i = gamma_i x_i         =>  sum_i x_i d ln gamma_i + sum_i x_i d ln x_i = 0
    and sum_i x_i d ln x_i = sum_i dx_i = d(sum_i x_i) = 0, hence

        sum_i x_i d ln gamma_i = 0        (the tested identity)

    Units: dimensionless throughout (mole fractions x, ln-ratios).
    Sanity: any CONSTANT-gamma model satisfies it trivially (d ln gamma = 0);
    a one-parameter regular solution satisfies it by construction (checked in
    the tests analytically); independent per-species gamma correlations that
    were never derived from one G function generically violate it.

DOMAIN LIMIT, stated so nobody widens it silently: the closed-system form above
does NOT apply to iron-bearing paths evaluated at fixed fO2 — the FeO/Fe2O3
couple exchanges oxygen with the gas, making the melt an OPEN system whose
consistency condition carries an exchange term. This module therefore refuses
Fe-bearing paths rather than reporting a residual that conflates redox exchange
with model inconsistency. CMAS (SiO2-CaO-MgO-Al2O3) paths are the intended use.

NUMERICS. Along a composition path x(s), per-segment midpoint residual

    r_k = sum_i xbar_i * (ln gamma_i[k+1] - ln gamma_i[k]),   xbar = (x_k + x_k+1)/2

with the total-variation normaliser TV = sum_k sum_i |xbar_i * delta ln gamma_i|.
The CONSISTENCY INDEX |sum_k r_k| / TV is scale-free: ~0 for a consistent model,
O(1) for an inconsistent one. Discretisation error shrinks ~4x on step halving
(midpoint rule); a genuine inconsistency does not — `residual_at_double_resolution`
is reported so the two are distinguishable. DIAGNOSTIC ONLY: nothing gates on
this yet (instrument before gate); the tests certify the CHECKER against
synthetic ground truth, and real-engine numbers are a report.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

#: Molar masses (g/mol) for the CMAS parent-oxide formula units this checker
#: supports. IUPAC 2021 atomic weights; formula-unit basis matches the
#: benchmark's EngineResult.activities convention.
_MOLAR_MASS_G_MOL: dict[str, float] = {
    "SiO2": 60.084,
    "CaO": 56.077,
    "MgO": 40.304,
    "Al2O3": 101.961,
    # Alkalis: review 2026-08-19 caught that the module was motivated by the
    # alkali-shape defect class and then refused Na2O as unsupported. The hole
    # was this table, not thermodynamics — closed-system GD applies to
    # alkali-bearing melts exactly as to CMAS.
    "Na2O": 61.979,
    "K2O": 94.196,
}

#: Species this checker refuses because of an OPERATIONAL mismatch, restated
#: after review (2026-08-19): for an equilibrium phase the GD identity itself
#: still holds with SPECIATED components (FeO and Fe2O3 both carried). What
#: this checker cannot do is re-speciate — it pairs INPUT mole fractions with
#: post-redox activities computed at fixed fO2, and that mismatch, not the
#: thermodynamics, is what would corrupt the residual. MnO and Cr2O3 were
#: removed from an earlier version of this list as too broad: in this project
#: MnO enters only as a Kress91 Fe-redox covariate and Cr2O3 is a
#: single-valence table-gamma oxide, so Fe-free paths carrying them are
#: closed-system for the engines as modelled.
_REDOX_OPEN_SPECIES = ("FeO", "Fe2O3", "FeOt")


#: Below this total variation the path traversed no PHYSICAL gamma variation.
#: Justification: the per-term floating-point noise of ln(a/x) is ~1e-16, so a
#: few dozen segments accumulate ~1e-14 of spurious TV even for an exactly
#: constant gamma; 1e-9 sits five orders above that noise floor and many orders
#: below any physically meaningful gamma variation (the smallest gamma effects
#: this project handles are ~1e-3 in ln units). Without this floor a
#: constant-gamma model divides noise by noise and reports a meaningless index.
TRIVIAL_TOTAL_VARIATION_FLOOR = 1e-9


class GibbsDuhemInapplicable(Exception):
    """Raised when the closed-system identity does not apply to the request."""


@dataclass(frozen=True)
class GibbsDuhemReport:
    """Residual of sum_i x_i d ln gamma_i along one composition path."""

    schema: str = "gibbs_duhem_residual.v1"
    engine: str = ""
    T_K: float = 0.0
    n_nodes: int = 0
    components: tuple[str, ...] = ()
    #: Signed path-integrated residual (should be ~0 for a consistent model).
    integrated_residual: float = 0.0
    #: Total variation of the summand — the normaliser.
    total_variation: float = 0.0
    #: |integrated_residual| / total_variation; ~0 consistent, O(1) inconsistent.
    #: None when the path traversed no gamma variation at all (TV == 0), in
    #: which case the identity is satisfied trivially and there is nothing to
    #: normalise — reporting 0.0 there would overstate what was tested.
    #: READING DISCIPLINE (review 2026-08-19): this signed index can MISS an
    #: inconsistency whose residual changes sign along the path (measured:
    #: ln gamma = 2 sin(2 pi x) scores 0.035 here), and a coarse grid can
    #: false-alarm a consistent model. Read it together with rectified_index
    #: and residual_at_double_resolution, never alone.
    consistency_index: float | None = None
    #: sum_k |r_k| / TV — immune to sign cancellation between segments. A
    #: consistent model gives ~O(h^2): the midpoint rule is second order, so
    #: this is pure discretisation and shrinks ~4x per halving of h (measured
    #: 100x per 10x in n). A sign-changing inconsistency stays O(1) here even
    #: when the signed index cancels to ~0. An earlier revision said O(h),
    #: contradicting this module's own 4x-shrink statement two dozen lines up
    #: — in the same passage that justified the battery threshold.
    rectified_index: float | None = None
    #: Same integrated residual at 2x nodes: discretisation error shrinks ~4x,
    #: a real inconsistency persists.
    residual_at_double_resolution: float | None = None
    max_segment_residual: float = 0.0
    #: Nodes the engine refused / returned unusable activities for, by index.
    skipped_nodes: tuple[int, ...] = ()
    #: Segments actually integrated (adjacent usable pairs with a shared
    #: component set). Zero means NOTHING was tested — isolated usable nodes
    #: and all-birth paths accumulate no residual and no TV, which is not the
    #: same thing as a constant-gamma trivial pass (review 2026-08-19, P1-1).
    closed_segments: int = 0
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "engine": self.engine,
            "T_K": self.T_K,
            "n_nodes": self.n_nodes,
            "components": list(self.components),
            "integrated_residual": self.integrated_residual,
            "total_variation": self.total_variation,
            "consistency_index": self.consistency_index,
            "rectified_index": self.rectified_index,
            "residual_at_double_resolution": self.residual_at_double_resolution,
            "max_segment_residual": self.max_segment_residual,
            "skipped_nodes": list(self.skipped_nodes),
            "closed_segments": self.closed_segments,
            "notes": list(self.notes),
        }


def mole_fractions_from_wt(
    composition_wt_pct: Mapping[str, float]
) -> dict[str, float]:
    """Formula-unit mole fractions from wt% on the supported component set.

    Algebra: n_i = w_i / M_i; x_i = n_i / sum n. Unit check: (g / (g/mol)) = mol.
    Sanity: equal weights of SiO2 and MgO give MORE moles of MgO (lighter unit).
    Refuses unsupported or redox-open components rather than dropping them —
    a dropped component makes the GD sum silently incomplete, which is exactly
    the kind of fabricated closure this checker exists to catch in others.
    """

    moles: dict[str, float] = {}
    for name, wt in composition_wt_pct.items():
        wt = float(wt)
        # A NEGATIVE or NON-FINITE weight is a data error, not an absent
        # component, and dropping it is the exact fabricated closure the
        # docstring above forbids. Measured consequence of the old
        # `if wt <= 0.0: continue` (sweep 2026-08-22, GD-2): the checker
        # renormalised the survivors while the caller's adapter kept the raw
        # composition, so the residual was formed across TWO DIFFERENT
        # composition bases. An adapter that is GD-consistent by construction
        # (a_i = x_i on its own basis) then scored rectified_index
        # 0.9999999999999998 -> `inconsistent`, with zero notes, zero skipped
        # nodes and no other tell: a maximal false accusation of thermodynamic
        # inconsistency, silently manufactured by the drop.
        if not math.isfinite(wt):
            raise GibbsDuhemInapplicable(
                f"{name}: non-finite weight {wt!r}; a composition that cannot "
                "be evaluated must refuse rather than be silently reshaped"
            )
        if wt < 0.0:
            raise GibbsDuhemInapplicable(
                f"{name}: negative weight {wt!r}. This is invalid input, not an "
                "absent component: dropping it would renormalise the remaining "
                "oxides into a melt the caller never supplied, and score the "
                "caller's activities against that different melt."
            )
        if wt == 0.0:
            continue
        if name in _REDOX_OPEN_SPECIES:
            raise GibbsDuhemInapplicable(
                f"{name}: this checker pairs input mole fractions with "
                "post-redox activities at fixed fO2 and cannot re-speciate "
                "the Fe couple, so its residual would conflate speciation "
                "with model inconsistency. Use an Fe-free path."
            )
        if name not in _MOLAR_MASS_G_MOL:
            raise GibbsDuhemInapplicable(
                f"unsupported component {name!r}; supported: "
                + ", ".join(sorted(_MOLAR_MASS_G_MOL))
            )
        moles[name] = wt / _MOLAR_MASS_G_MOL[name]
    total = sum(moles.values())
    if total <= 0.0:
        raise GibbsDuhemInapplicable("empty composition")
    return {k: v / total for k, v in moles.items()}


def _path_nodes(
    start_wt: Mapping[str, float],
    end_wt: Mapping[str, float],
    n_nodes: int,
) -> list[dict[str, float]]:
    keys = sorted(set(start_wt) | set(end_wt))
    return [
        {
            k: (1.0 - t) * float(start_wt.get(k, 0.0)) + t * float(end_wt.get(k, 0.0))
            for k in keys
        }
        for t in (i / (n_nodes - 1) for i in range(n_nodes))
    ]


def _residual_over_nodes(
    activity_fn: Callable[[Mapping[str, float]], Mapping[str, float] | None],
    nodes: Sequence[Mapping[str, float]],
) -> tuple[float, float, float, list[int], tuple[str, ...] | None]:
    """Core accumulation. Returns (residual, TV, max_segment, skipped, components)."""

    evaluated: list[tuple[dict[str, float], dict[str, float]] | None] = []
    skipped: list[int] = []
    components: tuple[str, ...] | None = None

    for index, node_wt in enumerate(nodes):
        x = mole_fractions_from_wt(node_wt)
        activities = activity_fn(node_wt)
        if activities is None:
            evaluated.append(None)
            skipped.append(index)
            continue
        ln_gamma: dict[str, float] = {}
        usable = True
        for name, x_i in x.items():
            a_i = activities.get(name)
            if a_i is None or not math.isfinite(float(a_i)) or float(a_i) <= 0.0:
                # A component present in the melt but missing from the engine's
                # activity set means the GD sum cannot close at this node.
                usable = False
                break
            ln_gamma[name] = math.log(float(a_i) / x_i)
        if not usable:
            evaluated.append(None)
            skipped.append(index)
            continue
        if components is None:
            components = tuple(sorted(x))
        evaluated.append((x, ln_gamma))

    residual = 0.0
    rectified = 0.0
    total_variation = 0.0
    max_segment = 0.0
    birth_segments = 0
    closed_segments = 0
    for left, right in zip(evaluated, evaluated[1:]):
        if left is None or right is None:
            continue
        x_left, lg_left = left
        x_right, lg_right = right
        if set(lg_left) != set(lg_right):
            # A component is born or dies inside this segment: the sum cannot
            # close across it (ln gamma has no value at x = 0 on one side).
            # Skipping is honest; a shared-set partial sum is the silent
            # incomplete closure this module refuses to commit (review P3).
            birth_segments += 1
            continue
        segment = 0.0
        for name in lg_left:
            xbar = 0.5 * (x_left.get(name, 0.0) + x_right.get(name, 0.0))
            term = xbar * (lg_right[name] - lg_left[name])
            segment += term
            total_variation += abs(term)
        residual += segment
        rectified += abs(segment)
        max_segment = max(max_segment, abs(segment))
        closed_segments += 1
    return (
        residual,
        rectified,
        total_variation,
        max_segment,
        skipped,
        birth_segments,
        closed_segments,
        components,
    )


def gibbs_duhem_residual(
    activity_fn: Callable[[Mapping[str, float]], Mapping[str, float] | None],
    start_wt: Mapping[str, float],
    end_wt: Mapping[str, float],
    *,
    T_K: float,
    engine_name: str = "",
    n_nodes: int = 21,
) -> GibbsDuhemReport:
    """Residual of the GD identity along a linear wt% blend start -> end.

    ``activity_fn`` maps a wt% composition to {component: activity} on the same
    formula-unit basis, or None for a typed engine refusal at that node.
    """

    if n_nodes < 3:
        raise ValueError("need at least 3 nodes for a meaningful path")

    nodes = _path_nodes(start_wt, end_wt, n_nodes)
    (
        residual,
        rectified,
        tv,
        max_segment,
        skipped,
        birth_segments,
        closed_segments,
        components,
    ) = _residual_over_nodes(activity_fn, nodes)

    # Step-halved comparison: discretisation error drops ~4x, real
    # inconsistency does not. Only meaningful when the coarse pass had no
    # skipped nodes (a refusing engine would make the two passes incomparable).
    double_res: float | None = None
    if not skipped:
        fine_nodes = _path_nodes(start_wt, end_wt, 2 * n_nodes - 1)
        fine_residual, _, _, _, fine_skipped, _, _, _ = _residual_over_nodes(
            activity_fn, fine_nodes
        )
        if not fine_skipped:
            double_res = fine_residual

    trivial = tv < TRIVIAL_TOTAL_VARIATION_FLOOR
    notes: list[str] = []
    if trivial and closed_segments > 0:
        notes.append(
            "no gamma variation along this path: the identity is satisfied "
            "trivially (e.g. a constant-gamma model) and this run is NOT "
            "evidence of consistency on paths where gamma varies"
        )
    if closed_segments == 0:
        # Review 2026-08-19 (grok P1-1): TV == 0 here means NOTHING WAS
        # INTEGRATED, not that gamma was constant — isolated usable nodes and
        # all-birth paths form zero segments. Without this note the trivial
        # wording above would dress an untested path as a tested-and-small one.
        notes.append(
            "zero closed segments: no adjacent usable node pair existed, so "
            "the identity was never integrated on this path — this is an "
            "untested path, not a trivially-satisfied one"
        )
    if skipped:
        notes.append(
            f"{len(skipped)} of {n_nodes} nodes skipped (engine refusal or "
            "incomplete activity coverage); the residual covers the remaining "
            f"{closed_segments} contiguous segment(s) only"
        )
    if birth_segments:
        notes.append(
            f"{birth_segments} segment(s) skipped where a component appears or "
            "vanishes: the sum cannot close across a birth, and a shared-set "
            "partial sum would be a silent incomplete closure"
        )

    return GibbsDuhemReport(
        engine=engine_name,
        T_K=float(T_K),
        n_nodes=n_nodes,
        components=components or (),
        integrated_residual=residual,
        total_variation=tv,
        consistency_index=(abs(residual) / tv) if not trivial else None,
        rectified_index=(rectified / tv) if not trivial else None,
        residual_at_double_resolution=double_res,
        max_segment_residual=max_segment,
        skipped_nodes=tuple(skipped),
        closed_segments=closed_segments,
        notes=tuple(notes),
    )


#: Battery verdict thresholds, ratified by the first commissioning runs
#: (t-706, 2026-08-19) and made RESOLUTION-AWARE after the SC-130 sweep
#: (2026-08-22). A scale-free index alone screams 1.0 on near-trivial paths
#: (measured: internal_analytic shell-adjacent rows at rectified 1.0 over TV
#: 7e-8..8e-6 ln units), so the battery also requires material variation.
#: Materiality floor 1e-3 ln units: six orders above the 1e-9 float-noise TV
#: floor, at the bottom of the gamma effects this project acts on.
#: Index threshold 0.1: measured consistent engines sit at 1e-4..4e-3 and
#: measured true violations at 1.0.
BATTERY_INDEX_THRESHOLD = 0.1
BATTERY_MATERIALITY_FLOOR_LN = 1e-3

#: Minimum step-halving shrink for a residual to be called DISCRETISATION.
#: Derivation: the midpoint rule is second order, so halving h shrinks a
#: consistent model's residual by 2^2 = 4 (measured exactly 4.00x on this
#: module's own certification models). A genuine O(1) inconsistency does not
#: shrink at all — ratio ~1 (measured: an independent-per-species-gamma model
#: holds 1.0000 at n = 5, 21, 101, 1001). 2.0 is the geometric mean of those
#: two regimes, a clean factor-2 margin either side.
#:
#: WHY THE VERDICT NEEDS THIS AT ALL (sweep 2026-08-22, GD-1). rectified_index
#: for a CONSISTENT model is a pure discretisation quantity, so any fixed
#: threshold on it is a statement about grid resolution, not about the model.
#: Measured: this suite's own Redlich-Kister model — introduced in the tests
#: precisely because it is GD-consistent BY CONSTRUCTION — scores 0.031 on the
#: tests' narrow path and 0.149 on a wide one at the same n_nodes=3, crossing
#: the 0.1 threshold and being convicted `inconsistent`, while its step-halving
#: ratio is exactly 4.00x, the unambiguous discretisation signature. The report
#: had ALREADY computed that disproof in `residual_at_double_resolution` and
#: the verdict ignored it — while GibbsDuhemReport's own docstring warned that
#: this index "can false-alarm a consistent model" and "must never be read
#: alone".
BATTERY_DISCRETISATION_SHRINK_MIN = 2.0

def battery_verdict(
    report: GibbsDuhemReport,
    *,
    index_threshold: float = BATTERY_INDEX_THRESHOLD,
    materiality_floor_ln: float = BATTERY_MATERIALITY_FLOOR_LN,
    discretisation_shrink_min: float = BATTERY_DISCRETISATION_SHRINK_MIN,
) -> str:
    """Two-condition commissioning verdict for one GD path report.

    The evaluability predicate is CLOSED SEGMENTS, not usable-node count
    (review 2026-08-19, grok P1-1, measured both failure directions): one
    closed segment needs two ADJACENT usable nodes, so eleven alternating
    usable nodes can integrate nothing while two adjacent ones carry a real
    material residual. Counting nodes misclassified both.

    Vocabulary (no bare "ok"/"pass" — each token states what was actually
    established):

    - ``not_evaluable``: zero closed segments — the identity was never
      integrated on this path (engine refusals, incomplete activity coverage,
      or all-birth segments). This is the structural outcome for adapters
      that expose activities for only a subset of melt components (e.g. the
      MELTS-family parent-oxide adapter, which refuses CaO/MgO and carries no
      alkali activities): the sum cannot close, and the checker refuses to
      fabricate a partial closure. Also the token for a non-finite or absent
      index on an otherwise material path: a number that cannot be read is
      not evidence either way.
    - ``immaterial_variation``: segments were integrated but traversed gamma
      variation at or below the materiality floor; whatever the index says
      there, it is not evidence of consistency OR of actionable
      inconsistency.
    - ``inconsistent``: rectified index above threshold on material
      variation AND the residual fails to shrink under step halving, so the
      excess is not discretisation. Not path-suffixed on purpose: one
      integrated counterexample falsifies model consistency globally
      (existential claim), where consistency is only ever established
      path-by-path (universal claim).
    - ``discretisation_limited``: index above threshold, but the residual
      shrinks ~4x when the step halves, which is the signature of the
      quadrature rather than the model. Refine the grid and re-run; this is
      NOT a finding against the engine.
    - ``inconsistent_resolution_unverified``: index above threshold and the
      doubled-resolution pass could not run (it is skipped whenever a node was
      refused), so discretisation could not be ruled out. Report it, do not
      cite it as a violation.
    - ``consistent_on_this_path``: the identity closes on material
      variation. Path-scoped on purpose: consistency elsewhere, and
      correctness anywhere, are not established (GD is a wrong-shape
      detector, never a correctness certificate).
    """

    if report.closed_segments == 0:
        return "not_evaluable"
    if report.total_variation <= materiality_floor_ln:
        return "immaterial_variation"
    index = report.rectified_index
    if index is None or not math.isfinite(index):
        return "not_evaluable"
    if index <= index_threshold:
        return "consistent_on_this_path"

    # Over threshold. Before convicting, run the discriminator the report
    # already carries: a consistent model's residual shrinks ~4x when the step
    # is halved, an inconsistency holds flat. Convicting without this check
    # convicts the GRID (sweep 2026-08-22, GD-1).
    coarse = abs(report.integrated_residual)
    fine = report.residual_at_double_resolution
    if fine is None:
        # The doubled-resolution pass is skipped when any node was refused, so
        # the discriminator is unavailable rather than negative. Saying
        # `inconsistent` here would assert something this run cannot support.
        return "inconsistent_resolution_unverified"
    fine = abs(fine)
    if fine <= 0.0:
        # Residual vanished entirely on refinement: discretisation by any
        # reading (an O(1) inconsistency cannot converge to zero).
        return "discretisation_limited"
    if coarse / fine >= discretisation_shrink_min:
        return "discretisation_limited"
    return "inconsistent"


__all__ = [
    "BATTERY_DISCRETISATION_SHRINK_MIN",
    "BATTERY_INDEX_THRESHOLD",
    "BATTERY_MATERIALITY_FLOOR_LN",
    "TRIVIAL_TOTAL_VARIATION_FLOOR",
    "GibbsDuhemInapplicable",
    "GibbsDuhemReport",
    "battery_verdict",
    "gibbs_duhem_residual",
    "mole_fractions_from_wt",
]
