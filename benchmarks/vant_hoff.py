"""Van 't Hoff / temperature-consistency checker (t-706 battery, item 3).

Physics premise (Gibbs–Helmholtz applied to the partial molar excess
chemical potential): mu_ex,i = R T ln gamma_i, and
d(mu_ex,i / T) / d(1/T) = H_ex,i  =>  d ln gamma_i / d(1/T) = H_ex,i / R
at fixed composition and pressure. So the SLOPE of ln gamma_i against 1/T is
the model's implied partial molar excess enthalpy over R. Unit check: slope b
has units K; H = R*b gives (J mol^-1 K^-1)(K) = J/mol. Sanity: a regular
solution ln gamma = (A0/T) x_j^2 gives b = A0 x_j^2 exactly and H_ex = R b.

UNLIKE Gibbs–Duhem and the Raoultian limit, van 't Hoff LINEARITY is not an
identity — a nonzero excess heat capacity legitimately curves ln gamma vs 1/T.
This checker therefore reports SHAPES and implied enthalpies, and flags only
one thing as a claim needing scrutiny: an ATHERMAL gamma (no T-dependence at
all), which asserts zero mixing enthalpy where real silicate melts show tens
of kJ/mol (e.g. Na2O-SiO2 calorimetric mixing enthalpies are strongly
negative). The implied H_ex numbers are the deliverable, ON THE FORMULA-UNIT
BASIS the activity_fn reports (a parent-formula activity a_fu = a_cat^n makes
the fu-basis H_ex n times the per-cation value — the same basis inflation the
Raoultian checker's review measured; declare the basis before comparing
against calorimetric anchors, which is where correctness lives).

Instrument-first: real-engine values are findings, deliberately unpinned.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from benchmarks.gibbs_duhem import mole_fractions_from_wt

#: |H_ex| below this reads athermal. 1 kJ/mol is 1-2 orders below any real
#: silicate mixing enthalpy this project would act on, and far above the
#: numerical noise of a two-decade 1/T fit on float64 activities.
ATHERMAL_H_FLOOR_J_MOL = 1.0e3

#: Relative drift between the low-T-half and high-T-half fitted slopes, above
#: which the T-dependence is reported nonlinear. Why slope DRIFT and not fit
#: rms: over a realistic window (e.g. 1500-1900 K) 1/T spans only ~±12%, so
#: even a strongly curved ln gamma ~ B/T^2 fits a straight line to ~1% rms —
#: rms has no power there (measured while certifying this module). Each
#: half-window FITTED slope represents the local slope at its half's midpoint,
#: so the halves' separation is half the window: for ln gamma = B u^2
#: (u = 1/T) the drift is ~ (u_hi - u_lo)/(2 u_mid) — measured 0.118 on the
#: certification window — while a truly linear-in-1/T model drifts < 1e-6
#: (pure float noise). 0.05 sits a factor ~2.4 under the curved case and
#: orders of magnitude above the linear one.
NONLINEARITY_REL_FLOOR = 0.05

GAS_CONSTANT_J_MOL_K = 8.31446261815324


@dataclass(frozen=True)
class VantHoffReport:
    schema: str = "vant_hoff.v1"
    engine: str = ""
    component: str = ""
    composition_wt_pct: tuple[tuple[str, float], ...] = ()
    T_nodes_K: tuple[float, ...] = ()
    n_usable: int = 0
    #: R * (slope of ln gamma vs 1/T): the implied partial molar excess
    #: enthalpy, J/mol of the FORMULA UNIT the activity_fn reports on.
    implied_H_ex_J_mol: float | None = None
    #: |half-window slope difference| / |full-window slope| (see
    #: NONLINEARITY_REL_FLOOR for why drift, not fit rms).
    slope_drift_rel: float | None = None
    ln_gamma_span: float | None = None
    #: R * |half-window slope difference|: the curvature magnitude in
    #: enthalpy units (the materiality half of the nonlinear condition, and
    #: the curvature half of the athermal condition).
    slope_drift_H_J_mol: float | None = None
    #: Declared activity basis of everything in this report (H_ex, floors):
    #: the formula-unit basis of the activity_fn contract. n-cation parents
    #: carry n x the per-cation enthalpy; the verdict floors are fu-basis.
    activity_basis: str = "formula_unit"
    verdict: str = ""
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "engine": self.engine,
            "component": self.component,
            "composition_wt_pct": dict(self.composition_wt_pct),
            "T_nodes_K": list(self.T_nodes_K),
            "n_usable": self.n_usable,
            "implied_H_ex_J_mol": self.implied_H_ex_J_mol,
            "slope_drift_rel": self.slope_drift_rel,
            "ln_gamma_span": self.ln_gamma_span,
            "slope_drift_H_J_mol": self.slope_drift_H_J_mol,
            "activity_basis": self.activity_basis,
            "verdict": self.verdict,
            "notes": list(self.notes),
        }


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """Least-squares y = a + b x. Returns (a, b, rms_residual)."""

    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    rms = math.sqrt(sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / n)
    return a, b, rms


def vant_hoff(
    activity_fn: Callable[[Mapping[str, float], float], Mapping[str, float] | None],
    component: str,
    composition_wt_pct: Mapping[str, float],
    T_nodes_K: list[float],
    *,
    engine_name: str = "",
) -> VantHoffReport:
    """ln gamma_component vs 1/T at fixed composition.

    ``activity_fn(wt, T_K)`` returns {component: activity} on a consistent
    formula-unit basis, or None for a typed refusal at that T. Composition
    validity (unsupported / redox-open components) refuses loud via
    mole_fractions_from_wt, exactly as the sibling checkers do.

    PREMISE THE CALLER MUST GUARANTEE (codex review P1): Gibbs-Helmholtz
    applies at fixed PHASE composition. The activities must describe one
    homogeneous liquid evaluated at exactly the input composition at every T
    (internal_analytic and IMCC satisfy this by construction). An
    EQUILIBRIUM adapter that crystallizes solids, shifts melt fraction, or
    switches solution phases across the window moves along a phase path, and
    d ln(a/x_input)/d(1/T) is then NOT the partial molar excess enthalpy —
    do not feed such an adapter to this checker.
    """

    if len(T_nodes_K) < 3:
        raise ValueError("need at least 3 temperature nodes")
    for T in T_nodes_K:
        if not (math.isfinite(float(T)) and float(T) > 0.0):
            raise ValueError(f"non-physical temperature node {T!r}")
    x = mole_fractions_from_wt(composition_wt_pct)
    if component not in x:
        raise ValueError(f"{component!r} not present in the composition")
    x_c = x[component]

    pairs: list[tuple[float, float]] = []
    # dedupe: duplicate T nodes would zero a half-window variance and crash
    # the fit (review P2-3); a duplicate carries no new information anyway.
    for T in sorted(set(float(t) for t in T_nodes_K)):
        activities = activity_fn(composition_wt_pct, T)
        a_c = None if activities is None else activities.get(component)
        if a_c is None or not math.isfinite(float(a_c)) or float(a_c) <= 0.0:
            continue
        pairs.append((1.0 / T, math.log(float(a_c) / x_c)))
    pairs.sort()  # ascending 1/T, i.e. HIGH temperature first (review P3-1)
    # distinct RECIPROCALS, not just distinct T floats: adjacent-ulp T values
    # collapse in 1/T and zero a half-window variance (codex P2).
    deduped: list[tuple[float, float]] = []
    for u, g in pairs:
        if not deduped or u != deduped[-1][0]:
            deduped.append((u, g))
    pairs = deduped
    inv_T = [u for u, _ in pairs]
    ln_g = [g for _, g in pairs]

    notes: list[str] = []
    base = dict(
        engine=engine_name,
        component=component,
        composition_wt_pct=tuple(sorted(composition_wt_pct.items())),
        T_nodes_K=tuple(sorted(T_nodes_K)),
        n_usable=len(inv_T),
    )
    if len(inv_T) < 3:
        return VantHoffReport(
            **base,
            verdict="not_evaluable",
            notes=(
                f"only {len(inv_T)} usable temperature nodes (engine refusals "
                "or unusable activities); the slope needs at least 3",
            ),
        )

    _, slope_K, _ = _linear_fit(inv_T, ln_g)
    h_ex = GAS_CONSTANT_J_MOL_K * slope_K
    span = max(ln_g) - min(ln_g)

    # Half-window local slopes over ascending 1/T: the FIRST half is the
    # high-temperature end. Halves overlap by one node when the count is odd.
    half = (len(inv_T) + 1) // 2
    _, b_first, _ = _linear_fit(inv_T[:half], ln_g[:half])
    _, b_last, _ = _linear_fit(inv_T[-half:], ln_g[-half:])
    # GRID-INVARIANT drift (codex P1: raw half-slope difference depends on
    # node parity — odd halves overlap, even halves are disjoint, so their
    # centroid separation differs and the same smooth curve flipped verdicts
    # between 4/5/6/7 nodes). Each fitted half slope is the local slope at
    # its half's u-CENTROID; the difference over the centroid separation
    # estimates the second derivative, and multiplying by the FULL window
    # span gives the slope change across the whole window — independent of
    # how many nodes sampled it. Degenerate centroid separation (heavy
    # refusals collapsing a half) falls back to the raw difference.
    cen_first = sum(inv_T[:half]) / half
    cen_last = sum(inv_T[-half:]) / half
    u_span = inv_T[-1] - inv_T[0]
    if cen_last > cen_first:
        delta_b_window = (b_last - b_first) / (cen_last - cen_first) * u_span
    else:
        delta_b_window = b_last - b_first
    # Curvature magnitude in ENTHALPY units (first commissioning run: a
    # near-zero mean slope makes the RATIO blow up on tiny wiggle). A
    # sign-changing H_ex(T) — U-shaped ln gamma — has a near-zero MEAN slope
    # with LARGE half slopes (review P1-1): the ratio is then reported as
    # infinite, never fabricated to zero, and h_drift carries the magnitude.
    h_drift = GAS_CONSTANT_J_MOL_K * abs(delta_b_window)
    if slope_K != 0.0:
        rel_nl = abs(delta_b_window) / abs(slope_K)
    else:
        rel_nl = math.inf if delta_b_window != 0.0 else 0.0

    # Athermal is a THREE-condition claim: small mean H_ex AND small
    # curvature AND a flat curve overall. The third condition (codex P1: an
    # oscillation orthogonal to all slope moments read athermal at span 2.0)
    # bounds the total ln-gamma span by what a floor-sized enthalpy could
    # produce across this window: |Delta ln gamma| = (H/R) * u_span.
    span_floor = (ATHERMAL_H_FLOOR_J_MOL / GAS_CONSTANT_J_MOL_K) * u_span
    if (
        abs(h_ex) < ATHERMAL_H_FLOOR_J_MOL
        and h_drift < ATHERMAL_H_FLOOR_J_MOL
        and span <= span_floor
    ):
        verdict = "athermal_gamma"
        notes.append(
            f"implied |H_ex| = {abs(h_ex):.1f} J/mol < {ATHERMAL_H_FLOOR_J_MOL:.0f}: "
            "the model's gamma carries (essentially) no temperature "
            "dependence at THIS composition. Caveat before reading it as a "
            "defect: the PARTIAL molar H_ex of a majority component is "
            "legitimately small at low co-solute fraction (regular solution: "
            "H_ex,i = Omega x_j^2), so a single dilute-majority row proves "
            "little — the claim becomes strong when athermal holds across "
            "compositions, including concentrated ones, where real silicate "
            "mixing enthalpies are tens of kJ/mol. A disclosure, not an "
            "identity violation."
        )
    elif (
        abs(h_ex) < ATHERMAL_H_FLOOR_J_MOL
        and h_drift < ATHERMAL_H_FLOOR_J_MOL
        and span > span_floor
    ):
        verdict = "nonlinear_T_dependence"
        notes.append(
            f"ln gamma spans {span:.3f} across the window while every slope "
            "moment is ~zero: T-structure orthogonal to the two-slope "
            "estimator (oscillatory or higher-order). Reported nonlinear; "
            "the fitted H_ex is NOT a usable summary of this curve."
        )
    elif rel_nl > NONLINEARITY_REL_FLOOR and h_drift > ATHERMAL_H_FLOOR_J_MOL:
        verdict = "nonlinear_T_dependence"
        notes.append(
            f"half-window slopes drift by {rel_nl:.3f} (> "
            f"{NONLINEARITY_REL_FLOOR}), {h_drift/1e3:.1f} kJ/mol in enthalpy "
            "terms: curvature is present. This can be legitimate physics "
            "(nonzero excess heat capacity, or a multi-reaction activity sum "
            "that is not a single van 't Hoff line) — it is a shape report, "
            "not a violation; the fitted H_ex is a range-average."
        )
    else:
        verdict = "vant_hoff_linear"

    return VantHoffReport(
        **base,
        implied_H_ex_J_mol=h_ex,
        slope_drift_rel=rel_nl,
        ln_gamma_span=span,
        slope_drift_H_J_mol=h_drift,
        verdict=verdict,
        notes=tuple(notes),
    )


__all__ = [
    "ATHERMAL_H_FLOOR_J_MOL",
    "GAS_CONSTANT_J_MOL_K",
    "NONLINEARITY_REL_FLOOR",
    "VantHoffReport",
    "vant_hoff",
]
