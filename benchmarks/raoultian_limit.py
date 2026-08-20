"""Raoultian endmember-limit checker (t-706 commissioning battery, item 2).

Physics premise: with a pure-liquid standard state, the activity of the pure
liquid is 1 BY DEFINITION, so gamma_i(x_i = 1) = 1 identically and
ln gamma_i -> 0 as x_i -> 1. This is not a model choice — it is standard-state
coherence. A model whose majority-component gamma does not approach 1 near the
endmember (e.g. a constant Henry-side gamma table extended to the endmember)
contradicts its own standard state, and every activity it reports near that
corner inherits the contradiction.

Like the Gibbs-Duhem checker this is a wrong-SHAPE detector: approaching the
Raoultian limit says nothing about correctness at finite dilution, and a model
can pass here while carrying large mid-range errors. External anchors remain
the correctness detectors.

Instrument-first (owner ruling, instrument-before-gate): real-engine verdicts
are findings to report, deliberately unpinned in tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from benchmarks.gibbs_duhem import mole_fractions_from_wt

#: The limit is only testable if the walk actually gets near the endmember.
#: 0.99 mole fraction: 1% impurity — also the onset of internal_analytic's
#: X > 0.99 shell continuity patch, whose entire purpose is the Raoultian
#: approach this checker measures.
X_NEAR_ENDMEMBER = 0.99

#: |ln gamma| tolerance at the closest reachable node — the "already
#: converged" shortcut, ON THE FORMULA-UNIT BASIS this checker (like the GD
#: checker) works in throughout. Review 2026-08-19 (grok P1-1) measured that
#: a parent-formula activity a_fu = a_cat^n inflates the residual by the
#: cation count (n = 2 for K2O/Na2O/Al2O3), and that a steep continuity
#: shell (|ln gamma*| ~ 10) is still ~0.07 in fu units at the 0.05 wt%%
#: ladder floor — so a magnitude gate ALONE misreads a genuine approach as a
#: violation. The verdict therefore keys on the DECAY EXPONENT below;
#: this tolerance only grants the early pass.
LN_GAMMA_TOLERANCE = 0.05

#: Local decay exponent p = d ln|ln gamma| / d ln(1 - x), estimated from the
#: two closest usable nodes. Derivation: any analytic Raoultian approach has
#: ln gamma ~ C (1-x)^p with p >= 1 near the endmember (regular solution and
#: the engines' (1-X)^2 continuity shells both give p = 2; a constant gamma
#: gives p = 0 exactly; a divergent gamma gives p < 0). Unit check: both
#: numerator and denominator are logs of dimensionless ratios. Sanity: one
#: impurity DECADE closer at p = 2 shrinks |ln gamma| 100x, so a shape with
#: p >= 1 measurably heads to zero while p ~ 0 measurably does not — this is
#: the discriminator a one-node magnitude cannot provide. Threshold 1.0 sits
#: between those regimes with a factor-2 margin each side.
DECAY_EXPONENT_MIN = 1.0


@dataclass(frozen=True)
class RaoultianLimitReport:
    schema: str = "raoultian_limit.v1"
    engine: str = ""
    component: str = ""
    diluent: str = ""
    T_K: float = 0.0
    n_nodes: int = 0
    n_usable: int = 0
    #: Largest majority-component mole fraction the engine answered at.
    x_reached: float = 0.0
    gamma_at_reached: float | None = None
    abs_ln_gamma_at_reached: float | None = None
    #: |ln gamma| at the median usable node, context for the walk shape.
    abs_ln_gamma_mid: float | None = None
    #: Local decay exponent from the two closest usable nodes (see
    #: DECAY_EXPONENT_MIN); None when fewer than two distinct nodes or a
    #: zero |ln gamma| makes it undefined.
    decay_exponent: float | None = None
    verdict: str = ""
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "engine": self.engine,
            "component": self.component,
            "diluent": self.diluent,
            "T_K": self.T_K,
            "n_nodes": self.n_nodes,
            "n_usable": self.n_usable,
            "x_reached": self.x_reached,
            "gamma_at_reached": self.gamma_at_reached,
            "abs_ln_gamma_at_reached": self.abs_ln_gamma_at_reached,
            "abs_ln_gamma_mid": self.abs_ln_gamma_mid,
            "decay_exponent": self.decay_exponent,
            "verdict": self.verdict,
            "notes": list(self.notes),
        }


def _impurity_wt_ladder(
    n_nodes: int, wt_impurity_start: float, wt_impurity_floor: float
) -> list[float]:
    """Geometric ladder of impurity wt%% from start down to the floor.

    Geometric, not linear: the Raoultian question lives in the last decades
    toward the endmember, so the nodes must crowd there. The floor default of
    0.01 wt%% puts the majority FORMULA-UNIT mole fraction above ~0.9997 even
    for the heaviest/lightest supported pairs (review P1-1/P3-1: 0.05 wt%%
    only reached x_fu = 0.9987 on Al2O3/MgO and K2O/MgO — not "above 0.999
    for every pair" as previously claimed, and not deep enough for a steep
    continuity shell to fall inside the magnitude tolerance).
    """

    if n_nodes < 3:
        raise ValueError("need at least 3 nodes for a limit walk")
    if not (0.0 < wt_impurity_floor < wt_impurity_start < 100.0):
        raise ValueError(
            "need 0 < wt_impurity_floor < wt_impurity_start < 100, got "
            f"floor={wt_impurity_floor} start={wt_impurity_start}"
        )
    ratio = (wt_impurity_floor / wt_impurity_start) ** (1.0 / (n_nodes - 1))
    return [wt_impurity_start * ratio**k for k in range(n_nodes)]


def raoultian_limit(
    activity_fn: Callable[[Mapping[str, float]], Mapping[str, float] | None],
    component: str,
    diluent: str,
    *,
    T_K: float,
    engine_name: str = "",
    n_nodes: int = 25,
    wt_impurity_start: float = 50.0,
    wt_impurity_floor: float = 0.01,
) -> RaoultianLimitReport:
    """Walk a binary blend toward the pure ``component`` and read gamma.

    ``activity_fn`` has the Gibbs-Duhem checker's contract: wt%% composition in,
    {component: activity} on the same formula-unit basis out, or None for a
    typed engine refusal. Unsupported or redox-open components refuse via
    mole_fractions_from_wt (GibbsDuhemInapplicable), never silently drop.

    Basis declaration (review P1-1): mole fractions, gammas, and the
    tolerance are all on the FORMULA-UNIT basis. A parent-formula activity
    a_fu = a_cat^n makes |ln gamma_fu| = n |ln gamma_cat|, so single-cation
    engines look up to n times farther from unity here than on their native
    basis — the decay-exponent verdict is basis-invariant, which is exactly
    why the verdict keys on it rather than on the magnitude alone.
    """

    usable: list[tuple[float, float]] = []  # (x_component, gamma_component)
    refused_beyond_last_usable: float | None = None
    for wt_imp in _impurity_wt_ladder(n_nodes, wt_impurity_start, wt_impurity_floor):
        wt = {component: 100.0 - wt_imp, diluent: wt_imp}
        x = mole_fractions_from_wt(wt)
        x_c = x[component]
        activities = activity_fn(wt)
        a_c = None if activities is None else activities.get(component)
        if a_c is None or not math.isfinite(float(a_c)) or float(a_c) <= 0.0:
            if refused_beyond_last_usable is None or x_c > refused_beyond_last_usable:
                refused_beyond_last_usable = x_c
            continue
        usable.append((x_c, float(a_c) / x_c))

    notes: list[str] = []
    if not usable:
        return RaoultianLimitReport(
            engine=engine_name,
            component=component,
            diluent=diluent,
            T_K=float(T_K),
            n_nodes=n_nodes,
            n_usable=0,
            verdict="not_evaluable",
            notes=("engine answered at no node on this walk",),
        )

    usable.sort(key=lambda pair: pair[0])
    x_reached, gamma_reached = usable[-1]
    abs_ln_reached = abs(math.log(gamma_reached))
    x_mid, gamma_mid = usable[len(usable) // 2]
    abs_ln_mid = abs(math.log(gamma_mid))

    # Local decay exponent from the two closest usable nodes (basis-invariant
    # shape evidence; see DECAY_EXPONENT_MIN derivation).
    decay_p: float | None = None
    if len(usable) >= 2:
        x_prev, gamma_prev = usable[-2]
        ln_prev = abs(math.log(gamma_prev))
        if (
            abs_ln_reached > 0.0
            and ln_prev > 0.0
            and 0.0 < (1.0 - x_reached) < (1.0 - x_prev)
        ):
            decay_p = math.log(abs_ln_reached / ln_prev) / math.log(
                (1.0 - x_reached) / (1.0 - x_prev)
            )

    if refused_beyond_last_usable is not None and refused_beyond_last_usable > x_reached:
        notes.append(
            f"engine refused above x={x_reached:.4f} (highest-x refusal at "
            f"x={refused_beyond_last_usable:.4f}); the domain boundary, not "
            "this checker, decides how close the limit is approachable"
        )

    if x_reached < X_NEAR_ENDMEMBER:
        verdict = "endmember_unreachable"
        notes.append(
            f"closest reachable node x={x_reached:.4f} is below "
            f"{X_NEAR_ENDMEMBER}: the Raoultian limit was not tested — this is "
            "a domain disclosure, not a pass and not a violation"
        )
        notes.append(
            "gamma_at_reached is a MID-RANGE reading at that x, not an "
            "endmember measurement"
        )
    elif abs_ln_reached <= LN_GAMMA_TOLERANCE:
        verdict = "approaches_raoultian"
    elif decay_p is not None and decay_p >= DECAY_EXPONENT_MIN:
        # Over tolerance at the closest node BUT measurably decaying with a
        # Raoultian-tail exponent: granting a pass would overclaim, flagging a
        # violation would state a falsehood (review P1-1: a steep continuity
        # shell IS approaching 1, just not yet inside the band at this walk
        # depth). The honest token is inconclusive-with-direction.
        verdict = "walk_inconclusive"
        notes.append(
            f"|ln gamma|={abs_ln_reached:.4f} at x={x_reached:.4f} is over "
            f"{LN_GAMMA_TOLERANCE} but decaying with exponent "
            f"p={decay_p:.2f} (Raoultian tails give p~2, constant gamma "
            "p~0): walk closer (lower wt_impurity_floor) to resolve"
        )
    else:
        verdict = "violates_raoultian"
        notes.append(
            f"|ln gamma|={abs_ln_reached:.4f} at x={x_reached:.4f} exceeds "
            f"{LN_GAMMA_TOLERANCE} with no Raoultian decay "
            f"(p={'undefined' if decay_p is None else f'{decay_p:.2f}'}): "
            "gamma does not approach 1 at the engine's own pure-liquid "
            "standard state"
        )

    return RaoultianLimitReport(
        engine=engine_name,
        component=component,
        diluent=diluent,
        T_K=float(T_K),
        n_nodes=n_nodes,
        n_usable=len(usable),
        x_reached=x_reached,
        gamma_at_reached=gamma_reached,
        abs_ln_gamma_at_reached=abs_ln_reached,
        abs_ln_gamma_mid=abs_ln_mid,
        decay_exponent=decay_p,
        verdict=verdict,
        notes=tuple(notes),
    )


__all__ = [
    "DECAY_EXPONENT_MIN",
    "LN_GAMMA_TOLERANCE",
    "X_NEAR_ENDMEMBER",
    "RaoultianLimitReport",
    "raoultian_limit",
]
