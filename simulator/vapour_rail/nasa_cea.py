"""Typed NASA CEA polynomial evaluators (NASA-7 and NASA-9 / Glenn).

Pure thermo leaf for vapour-rail property tooling (VR-4 / t-425). Runtime
evaluates **source** coefficients over declared temperature segments — it does
**not** refit spreadsheet rows. Curve-fit residual checks belong in fixtures
only.

Canonical forms (McBride, Zehe, Gordon, NASA TP-2002-211556; Gordon & McBride
NASA SP-273 / Chemkin 7-coeff):

**NASA-9 (Glenn nine-coefficient, ``nasa_cea_9``)**
  Premise: heat-capacity polynomial in powers
  ``T^{-2}, T^{-1}, T^0, T, T^2, T^3, T^4`` with two integration constants
  ``b1, b2`` for enthalpy and entropy.

  Algebra (dimensionless):
    ``Cp0/R = a1 T^{-2} + a2 T^{-1} + a3 + a4 T + a5 T^2 + a6 T^3 + a7 T^4``
    ``H0/(R T) = -a1 T^{-2} + a2 ln(T)/T + a3 + a4 T/2 + a5 T^2/3
                 + a6 T^3/4 + a7 T^4/5 + b1/T``
    ``S0/R = -a1 T^{-2}/2 - a2 T^{-1} + a3 ln(T) + a4 T + a5 T^2/2
             + a6 T^3/3 + a7 T^4/4 + b2``
    ``G0/(R T) = H0/(R T) - S0/R``

  Units: ``T`` in K; ``R`` is the molar gas constant; ``Cp0`` in energy/(mol·K);
  ``H0, G0`` in energy/mol; ``S0`` in energy/(mol·K). Coefficients are
  dimensionless as published for the ratio forms above.

  Sanity: monatomic ideal gas has ``Cp/R → 2.5``; O2 at 298.15 K yields
  ``Cp ≈ 29.4 J/(mol·K)``; adjacent segments must agree at the shared
  breakpoint within floating-point noise when the source is continuous.

**NASA-7 (classical seven-coefficient, ``nasa_cea_7``)**
  Premise: heat-capacity polynomial in non-negative powers only
  ``T^0 … T^4`` with integration constants ``a6`` (H) and ``a7`` (S).

  Algebra:
    ``Cp0/R = a1 + a2 T + a3 T^2 + a4 T^3 + a5 T^4``
    ``H0/(R T) = a1 + a2 T/2 + a3 T^2/3 + a4 T^3/4 + a5 T^4/5 + a6/T``
    ``S0/R = a1 ln(T) + a2 T + a3 T^2/2 + a4 T^3/3 + a5 T^4/4 + a7``
    ``G0/(R T) = H0/(R T) - S0/R``

  Units / sanity: same conventions as NASA-9. Constant-``Cp`` monatomic test
  species with ``a1=2.5`` (or 3.5 for diatomic classical) recovers
  ``Cp/R = a1`` exactly for all T.

Segment contracts:
- Intervals must form a contiguous, non-overlapping cover (shared endpoints OK).
- A gap or interior overlap raises :class:`NasaCeaSegmentError`.
- Missing standard-state convention raises :class:`NasaCeaConventionError`.
- Temperature outside the declared domain raises :class:`NasaCeaDomainError`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Sequence

# CODATA 2018 molar gas constant (J/(mol·K)). Used only when converting
# dimensionless ratios to dimensional thermo; ratio-form methods do not need it.
R_J_PER_MOL_K = 8.314462618

EvaluatorFamily = Literal["nasa_cea_7", "nasa_cea_9"]
StandardState = Literal["gas", "condensed_solid", "condensed_liquid", "condensed"]

# Default NASA-9 exponent set printed on thermo.inp interval headers.
NASA9_DEFAULT_EXPONENTS: tuple[float, ...] = (-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0)


class NasaCeaError(ValueError):
    """Base error for NASA CEA polynomial evaluation / construction."""


class NasaCeaSegmentError(NasaCeaError):
    """Segment gap, overlap, or empty segment list."""


class NasaCeaConventionError(NasaCeaError):
    """Missing or unsupported standard-state / family convention."""


class NasaCeaDomainError(NasaCeaError):
    """Temperature outside the declared segment domain."""


@dataclass(frozen=True)
class ThermoState:
    """Molar thermodynamic state at one temperature.

    Ratio fields are dimensionless (``Cp/R``, ``H/(R T)``, ``S/R``, ``G/(R T)``).
    Dimensional companions use SI: J/(mol·K) and J/mol.
    """

    T_K: float
    cp_over_R: float
    h_over_RT: float
    s_over_R: float
    g_over_RT: float

    @property
    def cp_J_per_mol_K(self) -> float:
        return self.cp_over_R * R_J_PER_MOL_K

    @property
    def h_J_per_mol(self) -> float:
        return self.h_over_RT * R_J_PER_MOL_K * self.T_K

    @property
    def s_J_per_mol_K(self) -> float:
        return self.s_over_R * R_J_PER_MOL_K

    @property
    def g_J_per_mol(self) -> float:
        return self.g_over_RT * R_J_PER_MOL_K * self.T_K


@dataclass(frozen=True)
class Nasa7Segment:
    """One temperature interval of a NASA-7 polynomial."""

    T_min_K: float
    T_max_K: float
    # a1..a5 Cp poly; a6 H integration; a7 S integration
    coefficients: tuple[float, float, float, float, float, float, float]

    def __post_init__(self) -> None:
        if not (self.T_min_K < self.T_max_K):
            raise NasaCeaSegmentError(
                f"NASA-7 segment requires T_min < T_max; got "
                f"[{self.T_min_K}, {self.T_max_K}]"
            )
        if len(self.coefficients) != 7:
            raise NasaCeaSegmentError(
                f"NASA-7 segment requires exactly 7 coefficients; "
                f"got {len(self.coefficients)}"
            )

    def contains(self, T_K: float, *, include_max: bool) -> bool:
        if include_max:
            return self.T_min_K <= T_K <= self.T_max_K
        return self.T_min_K <= T_K < self.T_max_K

    def evaluate_ratios(self, T_K: float) -> tuple[float, float, float, float]:
        """Return (Cp/R, H/(RT), S/R, G/(RT)) at T_K.

        Derivation
        ----------
        Premise: classical NASA 7-coefficient Cp polynomial
          Cp/R = a1 + a2 T + a3 T^2 + a4 T^3 + a5 T^4.
        Enthalpy integration: H/R = ∫ Cp/R dT + const
          ⇒ H/(R T) = a1 + a2 T/2 + a3 T^2/3 + a4 T^3/4 + a5 T^4/5 + a6/T.
        Entropy integration: S/R = ∫ (Cp/R)/T dT + const
          ⇒ S/R = a1 ln T + a2 T + a3 T^2/2 + a4 T^3/3 + a5 T^4/4 + a7.
        Gibbs: G = H − T S ⇒ G/(R T) = H/(R T) − S/R.
        Units: T in K; ratios dimensionless; multiply by R (J/(mol·K)) for SI.
        Sanity: a2=…=a5=0 ⇒ Cp/R = a1 constant; monatomic ideal gas a1=2.5.
        """
        a1, a2, a3, a4, a5, a6, a7 = self.coefficients
        T = float(T_K)
        T2 = T * T
        T3 = T2 * T
        T4 = T3 * T
        cp_R = a1 + a2 * T + a3 * T2 + a4 * T3 + a5 * T4
        h_RT = (
            a1
            + a2 * T / 2.0
            + a3 * T2 / 3.0
            + a4 * T3 / 4.0
            + a5 * T4 / 5.0
            + a6 / T
        )
        s_R = (
            a1 * math.log(T)
            + a2 * T
            + a3 * T2 / 2.0
            + a4 * T3 / 3.0
            + a5 * T4 / 4.0
            + a7
        )
        return cp_R, h_RT, s_R, h_RT - s_R


@dataclass(frozen=True)
class Nasa9Segment:
    """One temperature interval of a NASA-9 (Glenn) polynomial."""

    T_min_K: float
    T_max_K: float
    # a1..a7 for the seven power terms
    coefficients: tuple[float, float, float, float, float, float, float]
    b1: float
    b2: float
    exponents: tuple[float, ...] = NASA9_DEFAULT_EXPONENTS

    def __post_init__(self) -> None:
        if not (self.T_min_K < self.T_max_K):
            raise NasaCeaSegmentError(
                f"NASA-9 segment requires T_min < T_max; got "
                f"[{self.T_min_K}, {self.T_max_K}]"
            )
        if len(self.coefficients) != 7:
            raise NasaCeaSegmentError(
                f"NASA-9 segment requires exactly 7 a-coefficients; "
                f"got {len(self.coefficients)}"
            )
        if len(self.exponents) < 7:
            raise NasaCeaSegmentError(
                f"NASA-9 segment requires at least 7 exponents; "
                f"got {len(self.exponents)}"
            )
        expected = NASA9_DEFAULT_EXPONENTS
        if tuple(self.exponents[:7]) != expected:
            raise NasaCeaConventionError(
                "NASA-9 evaluator only supports the standard Glenn exponent "
                f"set {expected}; got {self.exponents[:7]}. "
                "Nonstandard exponent sets require an explicit extension."
            )

    def contains(self, T_K: float, *, include_max: bool) -> bool:
        if include_max:
            return self.T_min_K <= T_K <= self.T_max_K
        return self.T_min_K <= T_K < self.T_max_K

    def evaluate_ratios(self, T_K: float) -> tuple[float, float, float, float]:
        """Return (Cp/R, H/(RT), S/R, G/(RT)) at T_K.

        Derivation
        ----------
        Premise: NASA Glenn (TP-2002-211556) 9-coefficient form — seven Cp
        terms in powers T^{-2}…T^{4} plus integration constants b1 (H), b2 (S).

        Algebra:
          Cp/R = a1 T^{-2} + a2 T^{-1} + a3 + a4 T + a5 T^2 + a6 T^3 + a7 T^4
          H/(R T) = −a1 T^{-2} + a2 ln(T)/T + a3 + a4 T/2 + a5 T^2/3
                    + a6 T^3/4 + a7 T^4/5 + b1/T
          S/R = −a1 T^{-2}/2 − a2 T^{-1} + a3 ln(T) + a4 T + a5 T^2/2
                + a6 T^3/3 + a7 T^4/4 + b2
          G/(R T) = H/(R T) − S/R

        Units: T in K; ratios dimensionless. SI: multiply Cp/R and S/R by R;
        multiply H/(R T) and G/(R T) by R·T.

        Sanity: O2 (tpis89) at 298.15 K → Cp ≈ 29.38 J/(mol·K); low/mid
        segments of continuous thermo.inp records agree at the 1000 K
        breakpoint to ~1e-9 relative in the ratio forms.
        """
        a1, a2, a3, a4, a5, a6, a7 = self.coefficients
        T = float(T_K)
        T2 = T * T
        T3 = T2 * T
        T4 = T3 * T
        invT = 1.0 / T
        invT2 = invT * invT
        lnT = math.log(T)
        cp_R = (
            a1 * invT2
            + a2 * invT
            + a3
            + a4 * T
            + a5 * T2
            + a6 * T3
            + a7 * T4
        )
        h_RT = (
            -a1 * invT2
            + a2 * lnT * invT
            + a3
            + a4 * T / 2.0
            + a5 * T2 / 3.0
            + a6 * T3 / 4.0
            + a7 * T4 / 5.0
            + self.b1 * invT
        )
        s_R = (
            -a1 * invT2 / 2.0
            - a2 * invT
            + a3 * lnT
            + a4 * T
            + a5 * T2 / 2.0
            + a6 * T3 / 3.0
            + a7 * T4 / 4.0
            + self.b2
        )
        return cp_R, h_RT, s_R, h_RT - s_R


Segment = Nasa7Segment | Nasa9Segment


def _validate_segment_coverage(
    segments: Sequence[Segment],
    *,
    family: EvaluatorFamily,
) -> tuple[Segment, ...]:
    if not segments:
        raise NasaCeaSegmentError(f"{family}: at least one temperature segment is required")
    ordered = tuple(sorted(segments, key=lambda s: (s.T_min_K, s.T_max_K)))
    for i in range(len(ordered) - 1):
        lo = ordered[i]
        hi = ordered[i + 1]
        # Shared endpoint is required continuity geometry: lo.T_max == hi.T_min.
        if hi.T_min_K < lo.T_max_K:
            raise NasaCeaSegmentError(
                f"{family}: overlapping segments "
                f"[{lo.T_min_K}, {lo.T_max_K}] and [{hi.T_min_K}, {hi.T_max_K}]"
            )
        if hi.T_min_K > lo.T_max_K:
            raise NasaCeaSegmentError(
                f"{family}: gap between segments "
                f"[{lo.T_min_K}, {lo.T_max_K}] and [{hi.T_min_K}, {hi.T_max_K}] "
                f"(missing cover over ({lo.T_max_K}, {hi.T_min_K}))"
            )
    return ordered


@dataclass(frozen=True)
class NasaCeaPolynomial:
    """Multi-segment NASA CEA polynomial for one species / phase record."""

    name: str
    family: EvaluatorFamily
    standard_state: StandardState
    segments: tuple[Segment, ...]
    formula: str | None = None
    molecular_weight_g_per_mol: float | None = None
    delta_f_H_298_15_J_per_mol: float | None = None
    citation: str | None = None
    source_ref_code: str | None = None
    reference_pressure_Pa: float = 100_000.0  # CEA / JANAF standard state P°

    def __post_init__(self) -> None:
        if self.family not in ("nasa_cea_7", "nasa_cea_9"):
            raise NasaCeaConventionError(
                f"unsupported evaluator family {self.family!r}; "
                "expected 'nasa_cea_7' or 'nasa_cea_9'"
            )
        if self.standard_state not in (
            "gas",
            "condensed_solid",
            "condensed_liquid",
            "condensed",
        ):
            raise NasaCeaConventionError(
                f"missing or unsupported standard_state convention "
                f"{self.standard_state!r} for {self.name!r}"
            )
        if self.family == "nasa_cea_7":
            if not all(isinstance(s, Nasa7Segment) for s in self.segments):
                raise NasaCeaConventionError(
                    f"{self.name}: nasa_cea_7 requires Nasa7Segment instances"
                )
        else:
            if not all(isinstance(s, Nasa9Segment) for s in self.segments):
                raise NasaCeaConventionError(
                    f"{self.name}: nasa_cea_9 requires Nasa9Segment instances"
                )
        object.__setattr__(
            self,
            "segments",
            _validate_segment_coverage(self.segments, family=self.family),
        )

    @property
    def T_min_K(self) -> float:
        return self.segments[0].T_min_K

    @property
    def T_max_K(self) -> float:
        return self.segments[-1].T_max_K

    def segment_for(self, T_K: float) -> Segment:
        T = float(T_K)
        # NaN needs its own test BEFORE the range check, because the range check
        # cannot reject it: `T < lo` and `T > hi` are both False for NaN, so it
        # passes, then matches no segment and no shared breakpoint, and lands on
        # the "internal gap after construction?" refusal at the bottom of this
        # method -- which sends the reader hunting a coverage bug in the extract
        # that is not there. The construction-time validator guarantees the
        # segments are contiguous; a caller's NaN is the only way to reach that
        # message. +/-inf does NOT need this, since inf compares normally and is
        # correctly reported as outside the domain.
        if math.isnan(T):
            raise NasaCeaDomainError(
                f"{self.name}: T is NaN, which is not a temperature. The domain "
                f"is [{self.T_min_K}, {self.T_max_K}] K, but no comparison "
                "against NaN can place it inside or outside that interval."
            )
        if T < self.T_min_K or T > self.T_max_K:
            raise NasaCeaDomainError(
                f"{self.name}: T={T} K outside domain "
                f"[{self.T_min_K}, {self.T_max_K}] K"
            )
        last = len(self.segments) - 1
        for i, seg in enumerate(self.segments):
            if seg.contains(T, include_max=(i == last)):
                return seg
        # Shared breakpoints assign to the higher segment when T equals a
        # non-final max: fall through by preferring the segment that starts at T.
        for seg in self.segments:
            if seg.T_min_K == T:
                return seg
        raise NasaCeaDomainError(
            f"{self.name}: T={T} K not covered by any segment "
            f"(internal gap after construction?)"
        )

    def evaluate(self, T_K: float) -> ThermoState:
        """Evaluate thermo state at T_K using the covering source segment."""
        seg = self.segment_for(T_K)
        cp_R, h_RT, s_R, g_RT = seg.evaluate_ratios(T_K)
        return ThermoState(
            T_K=float(T_K),
            cp_over_R=cp_R,
            h_over_RT=h_RT,
            s_over_R=s_R,
            g_over_RT=g_RT,
        )

    def evaluate_at_breakpoint_pair(
        self, T_K: float
    ) -> tuple[ThermoState, ThermoState] | None:
        """If T_K is an interior shared breakpoint, evaluate both abutting segments.

        Returns ``(lower_segment_state, upper_segment_state)`` or ``None`` when
        ``T_K`` is not an interior breakpoint. Used to assert source continuity.
        """
        T = float(T_K)
        for i in range(len(self.segments) - 1):
            lo = self.segments[i]
            hi = self.segments[i + 1]
            if lo.T_max_K == hi.T_min_K == T:
                cp_lo = lo.evaluate_ratios(T)
                cp_hi = hi.evaluate_ratios(T)
                return (
                    ThermoState(T, *cp_lo),
                    ThermoState(T, *cp_hi),
                )
        return None

    def pure_psat_over_Pstd(
        self,
        condensed: "NasaCeaPolynomial",
        T_K: float,
    ) -> float:
        """``P_sat / P° = exp(−(G_gas − G_cond)/(R T))`` for pure-component vaporization.

        Derivation
        ----------
        Premise: equilibrium ``M(cond) ⇌ M(g)`` with standard states at P°.
        ΔG°_vap = G°_gas − G°_cond; K = P_sat/P° = exp(−ΔG°_vap / (R T)).
        Algebra: ΔG°/(R T) = G_gas/(R T) − G_cond/(R T) from the ratio forms.
        Units: dimensionless pressure ratio; multiply by ``reference_pressure_Pa``
        (default 1e5 Pa, CEA/JANAF) for absolute P_sat.
        Sanity: at the normal boiling point of a pure substance under 1 atm,
        P_sat ≈ 101325 Pa when both phases and the standard pressure are
        consistent — not asserted here because CEA P° is 1 bar.
        """
        if self.standard_state != "gas":
            raise NasaCeaConventionError(
                f"pure_psat_over_Pstd requires gas standard_state on the vapor "
                f"record; got {self.standard_state!r} for {self.name!r}"
            )
        if condensed.standard_state == "gas":
            raise NasaCeaConventionError(
                f"pure_psat_over_Pstd requires condensed standard_state on the "
                f"condensed record; got {condensed.standard_state!r} for "
                f"{condensed.name!r}"
            )
        g_gas = self.evaluate(T_K).g_over_RT
        g_cond = condensed.evaluate(T_K).g_over_RT
        return math.exp(-(g_gas - g_cond))


def continuity_residuals(
    poly: NasaCeaPolynomial,
    T_K: float,
) -> dict[str, float] | None:
    """Absolute residuals of Cp/R, H/(RT), S/R across an interior breakpoint."""
    pair = poly.evaluate_at_breakpoint_pair(T_K)
    if pair is None:
        return None
    lo, hi = pair
    return {
        "T_K": T_K,
        "d_cp_over_R": hi.cp_over_R - lo.cp_over_R,
        "d_h_over_RT": hi.h_over_RT - lo.h_over_RT,
        "d_s_over_R": hi.s_over_R - lo.s_over_R,
        "d_g_over_RT": hi.g_over_RT - lo.g_over_RT,
    }


def reaction_equilibrium_constant(
    terms: Sequence[tuple[float, ThermoState | float]],
    *,
    T_K: float | None = None,
) -> float:
    """``K(T) = exp(−ΔG°_rxn / (R T))`` from per-species standard Gibbs terms.

    Derivation
    ----------
    Premise: ideal-gas / standard-state equilibrium for a balanced reaction
    ``0 = Σ ν_i M_i`` (ν > 0 products, ν < 0 reactants). Each species carries
    a standard molar Gibbs free energy ``G°_i(T)`` from a NASA-7/9, Shomate,
    or other thermo family that exposes ``G°/(R T)``.

    Algebra::

      ΔG°_rxn(T) = Σ_i ν_i G°_i(T)
      ΔG°_rxn / (R T) = Σ_i ν_i (G°_i / (R T))
      K(T) = exp(−ΔG°_rxn / (R T))

    Units: ``G°/(R T)`` dimensionless; ``K`` dimensionless in the standard-
    state convention of the input polynomials (CEA/JANAF ``P° = 1 bar`` when
    the records use that reference). Absolute ``G°`` never needed — only the
    dimensionless ratio form.

    Sanity: for pure vaporization ``M(cond) ⇌ M(g)`` with ν_gas = +1,
    ν_cond = −1, ``K = P_sat / P°`` recovers the same ratio as
    :meth:`NasaCeaPolynomial.pure_psat_over_Pstd`. Against JANAF tables for
    a simple dissociation (e.g. O₂ ⇌ 2 O near 3000 K) ``K`` sits within the
    table's order of magnitude once both sides use the same segment source.
    """
    delta_g_over_RT = 0.0
    for nu, state in terms:
        if isinstance(state, ThermoState):
            g_over_RT = state.g_over_RT
        else:
            g_over_RT = float(state)
        delta_g_over_RT += float(nu) * g_over_RT
    # T_K is accepted for call-site documentation / future checks; the ratio
    # form already cancels R T, so evaluation does not re-scale by T.
    del T_K
    return math.exp(-delta_g_over_RT)


__all__ = [
    "EvaluatorFamily",
    "NASA9_DEFAULT_EXPONENTS",
    "Nasa7Segment",
    "Nasa9Segment",
    "NasaCeaConventionError",
    "NasaCeaDomainError",
    "NasaCeaError",
    "NasaCeaPolynomial",
    "NasaCeaSegmentError",
    "R_J_PER_MOL_K",
    "StandardState",
    "ThermoState",
    "continuity_residuals",
    "reaction_equilibrium_constant",
]
