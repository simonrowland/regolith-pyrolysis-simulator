r"""Solid-state fO2 buffer reproduction (t-706 commissioning battery, item 4).

The first battery item that tests CORRECTNESS against an EXTERNAL anchor rather
than internal consistency. Gibbs-Duhem, the Raoultian limit and van 't Hoff all
ask whether a model contradicts itself; a model can pass all three and still be
wrong. Published metal/metal-oxide buffer curves are measured, long-settled, and
sit on exactly the axis the mandate calls a control lever (fO2).

PHYSICS. For a condensed metal/oxide couple written per mole of O2, with both
condensed phases at unit activity,

    (2/n) M + O2  <->  (2/n) MO_n           dG0 = -R T ln K,  K = 1 / fO2
    =>  log10 fO2 = dG0 / (ln(10) R T)

Unit check: J/mol divided by (J mol^-1 K^-1 * K) is dimensionless. Sanity: dG0
is negative (metals oxidise spontaneously), so log10 fO2 is large and negative —
buffers do sit at very low oxygen fugacity.

THE RESIDUAL DECOMPOSITION, WITH ITS SIGNS THE RIGHT WAY ROUND. Substituting
dG0 = dH0 - T dS0,

    log10 fO2 = [dH0 / (ln(10) R)] * (1/T)  +  [-dS0 / (ln(10) R)]
              =        A         * (1/T)  +         B

which is the `a/T + b` form the published fits use, so fitting A and B to our own
curve recovers the enthalpy and entropy our data implies. Differencing against
the published pair:

    residual(T) = d(dH) / (ln(10) R T)  -  d(dS) / (ln(10) R)
                  \_______ 1/T _______/    \____ constant ____/

Therefore — and an earlier version of this module stated BOTH of these backwards,
caught by review 2026-08-22 and confirmed against our own IW numbers:

  * an ENTROPY error is a CONSTANT vertical offset in log fO2, identical at
    every temperature, which never decays under extrapolation;
  * an ENTHALPY error is a 1/T-SHAPED residual, largest at low temperature and
    shrinking as T rises.

The verdicts below are therefore named for the disagreeing THERMODYNAMIC
QUANTITY, never for a residual shape, so the two can no longer be transposed.

TOLERANCES, DERIVED FROM ONE POLICY NUMBER. Only `RESIDUAL_TOLERANCE_DEX` is a
judgement call. Both thermodynamic tolerances follow from it by the algebra
above: the entropy error that produces exactly that residual is
`tol * ln(10) * R` (constant in T), and the enthalpy error that produces it at
temperature T is `tol * ln(10) * R * T`. Nothing here is an independent
assertion about third-law uncertainty.

WINDOW DISCIPLINE. Every comparison happens ONLY inside the intersection of the
published fit's validity range and the engine's own fit range. Comparing outside
a fit's window measures extrapolation, not agreement. This is not hypothetical:
the first run of this instrument carried an NNO upper bound 100 K above Frost's
and produced a headline "entropy disagrees" verdict that DISSOLVED once the
window was corrected — the instrument's own stated failure mode, committed by
the instrument.

SEGMENTED ENGINE DATA. A two-parameter fit assumes one (dH, dS) across the
window. Where the engine's data is piecewise — the Ellingham Fe rail changes
phase basis at Fe(alpha)->Fe(gamma) = 1184 K — a single regression over a window
spanning the boundary returns sampling-weighted effective coefficients, not a
reaction enthalpy and entropy. Callers may pass `segment_boundaries_K`; any that
fall inside the window are reported as phase-pure sub-fits, and the global pair
is labelled a regression summary rather than reaction thermodynamics.

Instrument-first: real-engine residuals are findings to report, not values to
pin.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

GAS_CONSTANT_J_MOL_K = 8.314462618
LN10 = math.log(10.0)

#: THE one policy number. Residual tolerance in log10 fO2 units ("dex").
#: Published buffer fits from different laboratories disagree with each other by
#: order 0.1-0.2 dex over their common range, so a model inside 0.2 dex cannot
#: be meaningfully separated from the spread of the anchors themselves. This is
#: a stated project choice, not an externally certified uncertainty limit.
RESIDUAL_TOLERANCE_DEX = 0.20

#: DERIVED, not asserted: the entropy error that produces exactly
#: RESIDUAL_TOLERANCE_DEX of residual. Because an entropy error is a constant
#: offset, this is temperature-independent. 0.20 * ln10 * R = 3.83 J/mol/K.
ENTROPY_TOLERANCE_J_MOL_K = RESIDUAL_TOLERANCE_DEX * LN10 * GAS_CONSTANT_J_MOL_K


def enthalpy_tolerance_J_mol_O2(T_K: float) -> float:
    """DERIVED: the enthalpy error producing RESIDUAL_TOLERANCE_DEX at T_K.

    An enthalpy error enters as d(dH)/(ln10 R T), so the enthalpy equivalent of
    the residual tolerance scales with temperature; evaluated at the window
    midpoint it is ~4.4 kJ/mol O2 near 1150 K.
    """

    return RESIDUAL_TOLERANCE_DEX * LN10 * GAS_CONSTANT_J_MOL_K * float(T_K)


#: Anchor trust vocabulary. Duplication inside this repository is NOT
#: independent verification when every copy descends from the same secondary
#: source — a distinction review 2026-08-22 drew and this taxonomy now encodes.
PROVENANCE_VERIFIED = "verified_against_primary"
PROVENANCE_DUPLICATED = "duplicated_in_repo_same_source"
PROVENANCE_RECALL = "unverified_recall"


@dataclass(frozen=True)
class PublishedBuffer:
    """A published log10 fO2 = A/T + B buffer fit and its validity window."""

    name: str
    A_K: float
    B: float
    T_min_K: float
    T_max_K: float
    citation: str
    #: Frost's pressure coefficient C in log10 fO2 = B/T + A + C(P-1)/T, K/bar.
    #: Recorded for completeness; this instrument compares at the 1 bar
    #: reference where the term vanishes.
    pressure_coefficient_K_per_bar: float = 0.0
    #: Ellingham species whose metal/oxide couple IS this buffer, or None when
    #: the buffer is a multi-phase assemblage this data cannot express.
    ellingham_species: str | None = None
    provenance: str = PROVENANCE_RECALL
    notes: str = ""

    def log10_fo2(self, T_K: float) -> float:
        return self.A_K / float(T_K) + self.B


#: Coefficients AND windows read from Frost (1991) Table 1 and checked against
#: the primary during review 2026-08-22. Frost tabulates validity in degrees
#: Celsius; the kelvin bounds here are those values + 273.15, which is why they
#: carry the .15. An earlier revision of this registry had IW and WM truncated
#: at 1273 K and NNO extended to 1573 K, and the NNO error alone flipped a
#: headline verdict.
PUBLISHED_BUFFERS: dict[str, PublishedBuffer] = {
    "IW": PublishedBuffer(
        name="IW",
        A_K=-27489.0,
        B=6.702,
        T_min_K=838.15,   # 565 C
        T_max_K=1473.15,  # 1200 C
        pressure_coefficient_K_per_bar=0.055,
        citation="Frost (1991) Rev. Mineral. 25, 1-10, Table 1 (iron-wustite)",
        ellingham_species="Fe",
        provenance=PROVENANCE_VERIFIED,
        notes=(
            "CAUSALLY AMBIGUOUS BY CONSTRUCTION — do not read a gap here as a "
            "known convention difference. TWO confounded candidates produce the "
            "same sign and a similar magnitude: (a) the buffer solid is wustite "
            "Fe(1-x)O in equilibrium with Fe, not the stoichiometric FeO an "
            "Ellingham metal/oxide row carries; (b) Hirschmann (2021, GCA 313, "
            "74-84, doi:10.1016/j.gca.2021.08.039) finds the NIST-JANAF "
            "stoichiometric-FeO properties themselves erroneous, predicting IW "
            "0.2-1.1 log units too reducing over 1000-3000 K — and the rail "
            "under test is refit from Chase 1998 NIST-JANAF Fe-020. This "
            "regression cannot separate (a) from (b); saying which dominates "
            "needs an independent FeO dataset, not a better fit."
        ),
    ),
    "NNO": PublishedBuffer(
        name="NNO",
        A_K=-24930.0,
        B=9.36,
        T_min_K=873.15,   # 600 C
        T_max_K=1473.15,  # 1200 C
        pressure_coefficient_K_per_bar=0.046,
        citation="Frost (1991) Rev. Mineral. 25, 1-10, Table 1 (nickel-nickel oxide)",
        ellingham_species="Ni",
        provenance=PROVENANCE_VERIFIED,
        notes="2 Ni + O2 = 2 NiO; both phases stoichiometric, and the Ellingham "
        "Ni rail is a single phase-pure segment across this whole window, so "
        "this is the cleanest metal/oxide anchor available to that table.",
    ),
    "QFM": PublishedBuffer(
        name="QFM",
        A_K=-25096.3,
        B=8.735,
        T_min_K=846.15,   # ~573 C, the beta-quartz branch
        T_max_K=1473.15,  # 1200 C
        pressure_coefficient_K_per_bar=0.110,
        citation=(
            "Frost (1991) Rev. Mineral. 25, 1-10, Table 1 (quartz-fayalite-"
            "magnetite, beta-quartz branch); the same coefficients are carried "
            "by PySulfSat 1.0.12 and used in simulator/melt_backend/sulfsat.py"
        ),
        ellingham_species=None,
        provenance=PROVENANCE_VERIFIED,
        notes="3 Fe2SiO4 + O2 = 2 Fe3O4 + 3 SiO2 — a three-phase silicate/spinel "
        "assemblage. A metal/metal-oxide Ellingham row cannot express it, so this "
        "entry exists to be REFUSED rather than approximated by the Fe row.",
    ),
    "WM": PublishedBuffer(
        name="WM",
        A_K=-32807.0,
        B=13.012,
        T_min_K=838.15,   # 565 C
        T_max_K=1473.15,  # 1200 C
        pressure_coefficient_K_per_bar=0.083,
        citation="Frost (1991) Rev. Mineral. 25, 1-10, Table 1 (wustite-magnetite)",
        ellingham_species=None,
        provenance=PROVENANCE_VERIFIED,
        notes="An oxide-oxide couple (Fe(1-x)O / Fe3O4), not metal/oxide; the "
        "Ellingham table has no magnetite row.",
    ),
}


@dataclass(frozen=True)
class SegmentFit:
    """A phase-pure sub-window fit, when the engine's data is piecewise."""

    T_lo_K: float
    T_hi_K: float
    delta_dH_J_mol_O2: float
    delta_dS_J_mol_K_O2: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "T_lo_K": self.T_lo_K,
            "T_hi_K": self.T_hi_K,
            "delta_dH_J_mol_O2": self.delta_dH_J_mol_O2,
            "delta_dS_J_mol_K_O2": self.delta_dS_J_mol_K_O2,
        }


@dataclass(frozen=True)
class BufferReproductionReport:
    schema: str = "buffer_reproduction.v2"
    engine: str = ""
    buffer: str = ""
    species: str | None = None
    citation: str = ""
    anchor_provenance: str = ""
    window_K: tuple[float, float] | None = None
    published_window_K: tuple[float, float] | None = None
    engine_window_K: tuple[float, float] | None = None
    n_nodes: int = 0
    max_abs_residual_dex: float | None = None
    mean_residual_dex: float | None = None
    residual_span_dex: float | None = None
    implied_dH_J_mol_O2: float | None = None
    published_dH_J_mol_O2: float | None = None
    implied_dS_J_mol_K_O2: float | None = None
    published_dS_J_mol_K_O2: float | None = None
    delta_dH_J_mol_O2: float | None = None
    delta_dS_J_mol_K_O2: float | None = None
    enthalpy_tolerance_J_mol_O2: float | None = None
    #: True when a segment boundary falls inside the window, which makes the
    #: global (dH, dS) a regression summary rather than reaction thermodynamics.
    global_fit_is_regression_summary: bool = False
    segment_fits: tuple[SegmentFit, ...] = field(default_factory=tuple)
    verdict: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "engine": self.engine,
            "buffer": self.buffer,
            "species": self.species,
            "citation": self.citation,
            "anchor_provenance": self.anchor_provenance,
            "window_K": list(self.window_K) if self.window_K else None,
            "published_window_K": (
                list(self.published_window_K) if self.published_window_K else None
            ),
            "engine_window_K": (
                list(self.engine_window_K) if self.engine_window_K else None
            ),
            "n_nodes": self.n_nodes,
            "max_abs_residual_dex": self.max_abs_residual_dex,
            "mean_residual_dex": self.mean_residual_dex,
            "residual_span_dex": self.residual_span_dex,
            "implied_dH_J_mol_O2": self.implied_dH_J_mol_O2,
            "published_dH_J_mol_O2": self.published_dH_J_mol_O2,
            "implied_dS_J_mol_K_O2": self.implied_dS_J_mol_K_O2,
            "published_dS_J_mol_K_O2": self.published_dS_J_mol_K_O2,
            "delta_dH_J_mol_O2": self.delta_dH_J_mol_O2,
            "delta_dS_J_mol_K_O2": self.delta_dS_J_mol_K_O2,
            "enthalpy_tolerance_J_mol_O2": self.enthalpy_tolerance_J_mol_O2,
            "global_fit_is_regression_summary": self.global_fit_is_regression_summary,
            "segment_fits": [s.as_dict() for s in self.segment_fits],
            "verdict": self.verdict,
            "notes": list(self.notes),
        }


def _fit_inverse_T(inv_T: list[float], y: list[float]) -> tuple[float, float]:
    """Least-squares y = A * x + B on x = 1/T. Returns (A, B)."""

    n = len(inv_T)
    mx = sum(inv_T) / n
    my = sum(y) / n
    sxx = sum((x - mx) ** 2 for x in inv_T)
    if sxx <= 0.0:
        raise ValueError("degenerate temperature grid: no spread in 1/T")
    sxy = sum((x - mx) * (v - my) for x, v in zip(inv_T, y))
    A = sxy / sxx
    return A, my - A * mx


def log10_fo2_from_delta_g(delta_g_J_per_mol_O2: float, T_K: float) -> float:
    """log10 fO2 for a condensed couple at unit activity (derivation in module
    docstring). Both arguments are per mole of O2."""

    return delta_g_J_per_mol_O2 / (LN10 * GAS_CONSTANT_J_MOL_K * float(T_K))


def _implied_thermo(temps: list[float], ours: list[float]) -> tuple[float, float]:
    """(dH, dS) implied by a log10 fO2 curve, inverting the fit-form
    substitution: A = dH/(ln10 R) and B = -dS/(ln10 R)."""

    A, B = _fit_inverse_T([1.0 / T for T in temps], ours)
    scale = LN10 * GAS_CONSTANT_J_MOL_K
    return A * scale, -B * scale


def buffer_reproduction(
    delta_g_fn: Callable[[str, float], float],
    buffer_name: str,
    *,
    engine_window_K: tuple[float, float],
    engine_name: str = "",
    n_nodes: int = 13,
    segment_boundaries_K: Sequence[float] = (),
) -> BufferReproductionReport:
    """Compare an engine's metal/oxide couple against a published buffer fit.

    ``delta_g_fn(species, T_K)`` returns the couple's standard Gibbs energy of
    oxidation in **J per mole O2** (negative for a spontaneous oxidation).
    ``engine_window_K`` is the engine's own declared fit range; the comparison
    runs only where it overlaps the published fit's validity window.
    ``segment_boundaries_K`` are temperatures where the engine's data changes
    basis (phase transitions); any inside the window trigger phase-pure sub-fits.
    """

    if buffer_name not in PUBLISHED_BUFFERS:
        raise ValueError(
            f"unknown buffer {buffer_name!r}; known: {sorted(PUBLISHED_BUFFERS)}"
        )
    pub = PUBLISHED_BUFFERS[buffer_name]
    base = dict(
        engine=engine_name,
        buffer=pub.name,
        species=pub.ellingham_species,
        citation=pub.citation,
        anchor_provenance=pub.provenance,
        published_window_K=(pub.T_min_K, pub.T_max_K),
        engine_window_K=(float(engine_window_K[0]), float(engine_window_K[1])),
    )

    if pub.ellingham_species is None:
        return BufferReproductionReport(
            **base,
            verdict="not_expressible",
            notes=(
                f"{pub.name} is not a metal/metal-oxide couple: {pub.notes} "
                "Approximating it with a different couple would be a fabricated "
                "comparison, so this instrument refuses it.",
            ),
        )

    for bound, label in (
        (engine_window_K[0], "engine window lower"),
        (engine_window_K[1], "engine window upper"),
    ):
        if not (math.isfinite(float(bound)) and float(bound) > 0.0):
            raise ValueError(f"non-physical {label} bound {bound!r}")

    lo = max(pub.T_min_K, float(engine_window_K[0]))
    hi = min(pub.T_max_K, float(engine_window_K[1]))
    if not (hi > lo):
        return BufferReproductionReport(
            **base,
            verdict="not_evaluable",
            notes=(
                f"no overlap between the published window "
                f"[{pub.T_min_K:.0f}, {pub.T_max_K:.0f}] K and the engine window "
                f"[{engine_window_K[0]:.0f}, {engine_window_K[1]:.0f}] K: any "
                "comparison here would measure extrapolation, not agreement",
            ),
        )
    if n_nodes < 3:
        raise ValueError("need at least 3 nodes to separate slope from intercept")

    temps = [lo + (hi - lo) * i / (n_nodes - 1) for i in range(n_nodes)]
    ours: list[float] = []
    for T in temps:
        g = delta_g_fn(pub.ellingham_species, T)
        if not math.isfinite(float(g)):
            # Fail-closed: a non-finite dG must not become a scientific verdict
            # with NaN thermodynamics attached (review 2026-08-22).
            return BufferReproductionReport(
                **base,
                window_K=(lo, hi),
                n_nodes=n_nodes,
                verdict="not_evaluable",
                notes=(
                    f"engine returned a non-finite dG ({g!r}) at {T:.1f} K; a "
                    "curve that cannot be evaluated cannot be scored, and "
                    "fitting through it would publish NaN thermodynamics as a "
                    "typed result",
                ),
            )
        ours.append(log10_fo2_from_delta_g(float(g), T))

    residuals = [o - pub.log10_fo2(T) for o, T in zip(ours, temps)]
    dH_ours, dS_ours = _implied_thermo(temps, ours)
    scale = LN10 * GAS_CONSTANT_J_MOL_K
    dH_pub, dS_pub = pub.A_K * scale, -pub.B * scale
    d_dH, d_dS = dH_ours - dH_pub, dS_ours - dS_pub

    max_abs = max(abs(r) for r in residuals)
    mean_res = sum(residuals) / len(residuals)
    span = max(residuals) - min(residuals)
    T_mid = 0.5 * (lo + hi)
    dH_tol = enthalpy_tolerance_J_mol_O2(T_mid)

    # Phase-pure sub-fits wherever the engine changes basis inside the window.
    inner = sorted(b for b in map(float, segment_boundaries_K) if lo < b < hi)
    seg_fits: list[SegmentFit] = []
    for a, b in zip([lo] + inner, inner + [hi]):
        sub = [T for T in temps if a <= T <= b]
        if len(sub) < 3:
            sub = [a + (b - a) * i / 4 for i in range(5)]
        sub_ours = [
            log10_fo2_from_delta_g(float(delta_g_fn(pub.ellingham_species, T)), T)
            for T in sub
        ]
        s_dH, s_dS = _implied_thermo(sub, sub_ours)
        seg_fits.append(SegmentFit(a, b, s_dH - dH_pub, s_dS - dS_pub))

    notes: list[str] = []
    if pub.provenance != PROVENANCE_VERIFIED:
        notes.append(
            f"ANCHOR PROVENANCE: {pub.provenance} — a disagreement here indicts "
            "the anchor as readily as the engine. Verify against the primary "
            "before acting on this verdict."
        )
    if pub.notes:
        notes.append(pub.notes)
    notes.append(
        f"compared over [{lo:.1f}, {hi:.1f}] K, the overlap of the published "
        f"[{pub.T_min_K:.1f}, {pub.T_max_K:.1f}] and engine "
        f"[{engine_window_K[0]:.1f}, {engine_window_K[1]:.1f}] windows"
    )
    if inner:
        notes.append(
            f"the engine changes basis at {', '.join(f'{b:.0f} K' for b in inner)} "
            "inside this window, so the global (dH, dS) pair is a "
            "SAMPLING-WEIGHTED REGRESSION SUMMARY, not a reaction enthalpy and "
            "entropy; read segment_fits for the phase-pure values"
        )

    if max_abs <= RESIDUAL_TOLERANCE_DEX:
        verdict = "reproduces_within_tolerance"
        notes.append(
            f"max |residual| {max_abs:.3f} dex is inside {RESIDUAL_TOLERANCE_DEX} "
            "dex, the spread between published fits themselves — this model "
            "cannot be distinguished from the anchor over this window"
        )
        # A pass can be produced by two LARGE errors of opposite sign that
        # cancel inside the window (the 1/T enthalpy term against the constant
        # entropy term). That agreement is arithmetic, not thermodynamic, and
        # it decays the moment the window is left — which matters because the
        # engine's own fit range usually extends past the anchor's.
        if abs(d_dS) > ENTROPY_TOLERANCE_J_MOL_K or abs(d_dH) > dH_tol:
            beyond = max(
                abs(d_dH / (LN10 * GAS_CONSTANT_J_MOL_K * T)
                    - d_dS / (LN10 * GAS_CONSTANT_J_MOL_K))
                for T in (float(engine_window_K[0]), float(engine_window_K[1]))
            )
            notes.append(
                "COMPENSATING ERRORS: it passes inside the window while the "
                f"implied enthalpy ({d_dH/1000.0:+.2f} kJ/mol O2) and entropy "
                f"({d_dS:+.2f} J/mol/K) BOTH sit outside their tolerances "
                f"({dH_tol/1000.0:.2f} kJ/mol O2, "
                f"{ENTROPY_TOLERANCE_J_MOL_K:.2f} J/mol/K). The 1/T and "
                "constant terms are cancelling here, so the agreement is "
                "arithmetic rather than thermodynamic; extrapolated to the "
                f"edges of the engine's own range the residual reaches "
                f"{beyond:.3f} dex. Do not read this pass as a licence to use "
                "the couple outside the compared window."
            )
    else:
        entropy_off = abs(d_dS) > ENTROPY_TOLERANCE_J_MOL_K
        enthalpy_off = abs(d_dH) > dH_tol
        if entropy_off and enthalpy_off:
            verdict = "enthalpy_and_entropy_disagree"
        elif entropy_off:
            verdict = "entropy_disagrees"
        else:
            verdict = "enthalpy_disagrees"
        if entropy_off:
            notes.append(
                f"ENTROPY differs by {d_dS:+.2f} J/mol/K (tolerance "
                f"{ENTROPY_TOLERANCE_J_MOL_K:.2f}), which is a CONSTANT "
                f"{-d_dS / (LN10 * GAS_CONSTANT_J_MOL_K):+.3f} dex offset at every "
                "temperature — it does not decay under extrapolation"
            )
        if enthalpy_off:
            notes.append(
                f"ENTHALPY differs by {d_dH/1000.0:+.2f} kJ/mol O2 (tolerance "
                f"{dH_tol/1000.0:.2f} at the {T_mid:.0f} K midpoint), which is a "
                "1/T-shaped residual — largest at the cold end of the window and "
                "shrinking as temperature rises"
            )

    return BufferReproductionReport(
        **base,
        window_K=(lo, hi),
        n_nodes=n_nodes,
        max_abs_residual_dex=max_abs,
        mean_residual_dex=mean_res,
        residual_span_dex=span,
        implied_dH_J_mol_O2=dH_ours,
        published_dH_J_mol_O2=dH_pub,
        implied_dS_J_mol_K_O2=dS_ours,
        published_dS_J_mol_K_O2=dS_pub,
        delta_dH_J_mol_O2=d_dH,
        delta_dS_J_mol_K_O2=d_dS,
        enthalpy_tolerance_J_mol_O2=dH_tol,
        global_fit_is_regression_summary=bool(inner),
        segment_fits=tuple(seg_fits),
        verdict=verdict,
        notes=tuple(notes),
    )


__all__ = [
    "ENTROPY_TOLERANCE_J_MOL_K",
    "GAS_CONSTANT_J_MOL_K",
    "PROVENANCE_DUPLICATED",
    "PROVENANCE_RECALL",
    "PROVENANCE_VERIFIED",
    "PUBLISHED_BUFFERS",
    "RESIDUAL_TOLERANCE_DEX",
    "BufferReproductionReport",
    "PublishedBuffer",
    "SegmentFit",
    "buffer_reproduction",
    "enthalpy_tolerance_J_mol_O2",
    "log10_fo2_from_delta_g",
]
