"""Shomate polynomial evaluator (third vapour-rail thermo family).

Typed family for catalog rows that declare ``evaluator_family: shomate`` (or
``evaluator: shomate``). The algebra follows the NIST Chemistry WebBook
Shomate form (Chase 1998 / SRD 69) — that is the polynomial *basis*, not a
provenance filter. Coefficient values are caller-supplied: this class does
not inspect source tables, ``citation`` is optional, and a least-squares
transcription of a non-NIST table is a valid input to the same algebra.
The production extract ``data/literature/extracts/ivtan-mno-coo-thermo.yaml``
declares method ``HT-C8 Wave-2 least-squares Shomate transcription of the
IVTAN electronic tables`` and uses this family.

Canonical form (``t = T / 1000``, ``T`` in K)::

  Cp° = A + B t + C t² + D t³ + E / t²          [J/(mol·K)]
  H° − H°₂₉₈.₁₅ = A t + B t²/2 + C t³/3 + D t⁴/4 − E/t + F − H
                                                 [kJ/mol]
  S° = A ln(t) + B t + C t²/2 + D t³/3 − E/(2 t²) + G
                                                 [J/(mol·K)]

Derivation (premise → algebra → units → sanity)
-----------------------------------------------
Premise: published Shomate A…H on a declared temperature segment; the
WebBook enthalpy equation already folds the integration constant so that
``H°(298.15) − H°₂₉₈.₁₅ ≈ 0`` when the segment covers 298.15 K.

Algebra: as above; free-energy conveniences used by reaction ``K(T)``::

  H°(T) [J/mol] ≈ (H° − H°₂₉₈.₁₅)[kJ/mol]·1000 + ΔfH°₂₉₈.₁₅ [J/mol]
  G°(T) [J/mol] = H°(T) − T · S°(T)
  G°/(R T) = H°/(R T) − S°/R

Units: ``T`` in K; ``Cp, S`` in J/(mol·K); Shomate ``H`` equation in kJ/mol;
SI companions multiply accordingly. ``R = 8.314462618`` J/(mol·K).

Sanity (NIST WebBook O2(g), 100–700 K segment, Chase 1998)::

  Cp°(298.15 K) ≈ 29.38 J/(mol·K)
  S°(298.15 K)  ≈ 205.15 J/(mol·K)

Cover contracts on :class:`ShomatePolynomial` (this class):
- Intervals must form a contiguous, non-overlapping cover (shared endpoints OK).
- A gap or interior overlap raises :class:`ShomateSegmentError`.
- Missing or unsupported ``standard_state`` raises :class:`ShomateConventionError`.
- Non-finite coefficients or non-finite ``delta_f_H_298_15_J_per_mol`` raise
  :class:`ShomateConventionError` at construction.
- :meth:`ShomatePolynomial.evaluate` / :meth:`ShomatePolynomial.segment_for`
  refuse T outside the aggregate cover and refuse non-finite T
  (:class:`ShomateDomainError`).

:meth:`ShomateSegment.evaluate`, when called directly, requires finite T > 0
and does not re-check that T lies in that segment's ``[T_min_K, T_max_K]``.
The interval check lives on :meth:`ShomatePolynomial.segment_for`.

These dataclasses have no ``transition_temperature_K`` or ``boiling_point_K``
field. Segment joins are coefficient-cover bounds; Shomate supplies
energetics on those bounds.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from simulator.vapour_rail.nasa_cea import (
    R_J_PER_MOL_K,
    StandardState,
    ThermoState,
)

StandardStateShomate = StandardState  # same typed conventions

# Eight NIST WebBook Shomate coefficients in order A…H.
_SHOMATE_KEYS = ("A", "B", "C", "D", "E", "F", "G", "H")


class ShomateError(ValueError):
    """Base error for Shomate evaluation / construction."""


class ShomateSegmentError(ShomateError):
    """Segment gap, overlap, empty list, or bound failure."""


class ShomateConventionError(ShomateError):
    """Missing or unsupported standard-state / coefficient convention."""


class ShomateDomainError(ShomateError):
    """Temperature outside the declared segment domain."""


def _require_finite(
    value: object, *, what: str, error: type[ShomateError]
) -> float:
    """Return ``float(value)``; refuse NaN/inf.

    Premise: Shomate uses ``t = T/1000``, ``ln(t)``, ``1/t``, ``1/t²``, and
    sums of A…H. Those operations are defined on finite reals.
    Algebra: ``math.isfinite(x)`` is False for NaN, +inf, and -inf, and True
    for every other IEEE float (including the T > 0 values this evaluator
    uses). A NaN in any addend poisons Cp, H, S, and G; inf overflows.
    Units: dimensionless predicate; ``value`` keeps the caller's unit.
    Sanity: ``298.15`` is finite; ``float('nan')`` and ``float('inf')`` are not.
    """
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise error(f"{what} must be a finite real; got {value!r}") from exc
    if not math.isfinite(x):
        raise error(f"{what} must be a finite real; got {value!r}")
    return x


@dataclass(frozen=True)
class ShomateSegment:
    """One temperature interval of a NIST WebBook Shomate polynomial."""

    T_min_K: float
    T_max_K: float
    # A, B, C, D, E, F, G, H — NIST WebBook order
    coefficients: tuple[float, float, float, float, float, float, float, float]

    def __post_init__(self) -> None:
        _require_finite(
            self.T_min_K, what="Shomate segment T_min_K", error=ShomateSegmentError
        )
        _require_finite(
            self.T_max_K, what="Shomate segment T_max_K", error=ShomateSegmentError
        )
        if not (self.T_min_K < self.T_max_K):
            raise ShomateSegmentError(
                f"Shomate segment requires T_min < T_max; got "
                f"[{self.T_min_K}, {self.T_max_K}]"
            )
        if len(self.coefficients) != 8:
            raise ShomateSegmentError(
                f"Shomate segment requires exactly 8 coefficients (A…H); "
                f"got {len(self.coefficients)}"
            )
        for i, coeff in enumerate(self.coefficients):
            _require_finite(
                coeff,
                what=f"Shomate coefficient {_SHOMATE_KEYS[i]}",
                error=ShomateConventionError,
            )

    def contains(self, T_K: float, *, include_max: bool) -> bool:
        if include_max:
            return self.T_min_K <= T_K <= self.T_max_K
        return self.T_min_K <= T_K < self.T_max_K

    def evaluate(self, T_K: float) -> ThermoState:
        """Return molar thermo state (ratio + SI via ThermoState helpers).

        This method requires finite T > 0. It does not re-check that T lies
        in ``[T_min_K, T_max_K]``; that interval check lives on
        :meth:`ShomatePolynomial.segment_for`.

        Derivation
        ----------
        Premise: NIST WebBook Shomate (``t = T/1000``).
        Algebra: Cp, H−H298, S as in module docstring; then
          H_abs ≈ (H−H298)·1000  (J/mol, relative to 298.15 unless ΔfH folded
          by the caller) and G/(R T) = H/(R T) − S/R for the relative form.
        Units: Cp, S in J/(mol·K); Shomate H equation in kJ/mol → ×1000 → J/mol.
        Sanity: O2 at 298.15 K → Cp ≈ 29.38, S ≈ 205.15 (JANAF / WebBook).
        Finite-T gate: ``t``, ``ln(t)``, and ``1/t`` are defined for finite
        T > 0. IEEE ``NaN <= 0`` and ``+inf <= 0`` are False, so a ``T <= 0``
        test alone admits both and returns a ``ThermoState`` whose fields are
        non-finite. O2 at 298.15 K stays finite; NaN/+inf must refuse.
        """
        A, B, C, D, E, F, G, H = self.coefficients
        T = float(T_K)
        if not math.isfinite(T) or T <= 0.0:
            raise ShomateDomainError(
                f"Shomate requires finite T > 0 K; got {T}"
            )
        t = T / 1000.0
        t2 = t * t
        t3 = t2 * t
        t4 = t3 * t
        inv_t = 1.0 / t
        inv_t2 = inv_t * inv_t
        cp = A + B * t + C * t2 + D * t3 + E * inv_t2
        # kJ/mol → J/mol for ratio forms
        h_minus_h298_J = 1000.0 * (
            A * t + B * t2 / 2.0 + C * t3 / 3.0 + D * t4 / 4.0 - E * inv_t + F - H
        )
        s = A * math.log(t) + B * t + C * t2 / 2.0 + D * t3 / 3.0 - E / (2.0 * t2) + G
        cp_R = cp / R_J_PER_MOL_K
        s_R = s / R_J_PER_MOL_K
        h_RT = h_minus_h298_J / (R_J_PER_MOL_K * T)
        g_RT = h_RT - s_R
        return ThermoState(
            T_K=T,
            cp_over_R=cp_R,
            h_over_RT=h_RT,
            s_over_R=s_R,
            g_over_RT=g_RT,
        )


def _validate_shomate_coverage(
    segments: Sequence[ShomateSegment],
) -> tuple[ShomateSegment, ...]:
    if not segments:
        raise ShomateSegmentError("shomate: at least one temperature segment is required")
    ordered = tuple(sorted(segments, key=lambda s: (s.T_min_K, s.T_max_K)))
    for i in range(len(ordered) - 1):
        lo = ordered[i]
        hi = ordered[i + 1]
        if hi.T_min_K < lo.T_max_K:
            raise ShomateSegmentError(
                f"shomate: overlapping segments "
                f"[{lo.T_min_K}, {lo.T_max_K}] and [{hi.T_min_K}, {hi.T_max_K}]"
            )
        if hi.T_min_K > lo.T_max_K:
            raise ShomateSegmentError(
                f"shomate: gap between segments "
                f"[{lo.T_min_K}, {lo.T_max_K}] and [{hi.T_min_K}, {hi.T_max_K}] "
                f"(missing cover over ({lo.T_max_K}, {hi.T_min_K}))"
            )
    return ordered


@dataclass(frozen=True)
class ShomatePolynomial:
    """Multi-segment NIST Shomate polynomial for one species / phase record."""

    name: str
    standard_state: StandardState
    segments: tuple[ShomateSegment, ...]
    formula: str | None = None
    delta_f_H_298_15_J_per_mol: float | None = None
    citation: str | None = None
    reference_pressure_Pa: float = 100_000.0

    def __post_init__(self) -> None:
        if self.standard_state not in (
            "gas",
            "condensed_solid",
            "condensed_liquid",
            "condensed",
        ):
            raise ShomateConventionError(
                f"missing or unsupported standard_state convention "
                f"{self.standard_state!r} for {self.name!r}"
            )
        if self.delta_f_H_298_15_J_per_mol is not None:
            _require_finite(
                self.delta_f_H_298_15_J_per_mol,
                what=f"{self.name}: delta_f_H_298_15_J_per_mol",
                error=ShomateConventionError,
            )
        object.__setattr__(
            self, "segments", _validate_shomate_coverage(self.segments)
        )

    @property
    def family(self) -> Literal["shomate"]:
        return "shomate"

    @property
    def T_min_K(self) -> float:
        return self.segments[0].T_min_K

    @property
    def T_max_K(self) -> float:
        return self.segments[-1].T_max_K

    def segment_for(self, T_K: float) -> ShomateSegment:
        T = float(T_K)
        # On this method, NaN fails both range comparisons (`T < lo` and
        # `T > hi` are False), matches no `contains()` interval, and matches
        # no shared breakpoint `T_min_K == T`. It would otherwise land on
        # the "not covered by any Shomate segment" refusal — a coverage
        # message for a caller NaN, not a gap in the extract.
        # +/-inf compare normally and are reported as outside the domain
        # when the constructed bounds are finite.
        if math.isnan(T):
            raise ShomateDomainError(
                f"{self.name}: T is NaN, which is not a temperature. The domain "
                f"is [{self.T_min_K}, {self.T_max_K}] K, but no comparison "
                "against NaN can place it inside or outside that interval."
            )
        if T < self.T_min_K or T > self.T_max_K:
            raise ShomateDomainError(
                f"{self.name}: T={T} K outside domain "
                f"[{self.T_min_K}, {self.T_max_K}] K"
            )
        last = len(self.segments) - 1
        for i, seg in enumerate(self.segments):
            if seg.contains(T, include_max=(i == last)):
                return seg
        for seg in self.segments:
            if seg.T_min_K == T:
                return seg
        raise ShomateDomainError(
            f"{self.name}: T={T} K not covered by any Shomate segment"
        )

    def formation_enthalpy_J_per_mol(self) -> float:
        """Absolute formation enthalpy at 298.15 K (J/mol).

        Prefer the explicit ``delta_f_H_298_15_J_per_mol`` field. When absent,
        derive it from the NIST Shomate coefficient ``H`` (kJ/mol → ×1000),
        which WebBook tables carry as ΔfH°298 for the species. On this method,
        after :class:`ShomateSegment` construction has refused non-finite
        ``H``, remaining finite ``H`` values must agree to within 1e-6 kJ/mol.
        IEEE ``abs(NaN - x) > 1e-6`` is False, so a NaN later-segment ``H``
        cannot fail that comparison — which is why non-finite ``H`` is
        refused at segment construction rather than here.
        """
        if self.delta_f_H_298_15_J_per_mol is not None:
            return float(self.delta_f_H_298_15_J_per_mol)
        h_kJ = [float(seg.coefficients[7]) for seg in self.segments]
        if any(not math.isfinite(h) for h in h_kJ) or any(
            abs(h - h_kJ[0]) > 1.0e-6 for h in h_kJ[1:]
        ):
            raise ShomateConventionError(
                f"{self.name}: Shomate segments disagree on coefficient H "
                f"(formation enthalpy); got {h_kJ}"
            )
        # Null-hypothesis: if H is only a relative-offset fit constant and
        # not ΔfH°, absolute G for formation reactions is wrong — require
        # explicit delta_f_H_298_15_J_per_mol in that case.
        return 1000.0 * h_kJ[0]

    def evaluate(self, T_K: float) -> ThermoState:
        """Evaluate thermo state at T_K on the covering source segment.

        Always folds formation enthalpy into H and G so reaction ``ΔG°``
        differences share a common formation reference::

          H_f(T) = (H°−H°298) + ΔfH°298
          G_f/(R T) = H_f/(R T) − S/R

        ``ΔfH°298`` comes from :meth:`formation_enthalpy_J_per_mol` (explicit
        field or NIST coefficient ``H`` × 1000). Relative H°−H°298 alone is
        never returned for G/K/Psat use — pure-vaporization of phases with
        different formation enthalpies would otherwise cancel the latent heat
        of vaporization at 298.15 K.
        """
        st = self.segment_for(T_K).evaluate(T_K)
        T = float(T_K)
        h_rel = st.h_over_RT * R_J_PER_MOL_K * T
        h_abs = h_rel + self.formation_enthalpy_J_per_mol()
        h_RT = h_abs / (R_J_PER_MOL_K * T)
        return ThermoState(
            T_K=T,
            cp_over_R=st.cp_over_R,
            h_over_RT=h_RT,
            s_over_R=st.s_over_R,
            g_over_RT=h_RT - st.s_over_R,
        )


def coefficients_from_mapping(raw: object) -> tuple[float, ...]:
    """Parse A…H from a mapping or 8-length sequence; loud on convention miss.

    On this function, each of A…H must be a finite real. ``float('nan')`` and
    ``float('inf')`` survive ``float()`` and are refused after conversion.
    """
    if isinstance(raw, Mapping):
        try:
            values = tuple(float(raw[k]) for k in _SHOMATE_KEYS)  # type: ignore[index]
        except (KeyError, TypeError, ValueError) as exc:
            raise ShomateConventionError(
                f"Shomate coefficients require keys A…H; got {raw!r}"
            ) from exc
        return tuple(
            _require_finite(
                v, what=f"Shomate coefficient {k}", error=ShomateConventionError
            )
            for k, v in zip(_SHOMATE_KEYS, values, strict=True)
        )
    if isinstance(raw, (list, tuple)) and len(raw) == 8:
        try:
            values = tuple(float(x) for x in raw)
        except (TypeError, ValueError) as exc:
            raise ShomateConventionError(
                "Shomate coefficient sequence must be eight numerics"
            ) from exc
        return tuple(
            _require_finite(
                v, what=f"Shomate coefficient {_SHOMATE_KEYS[i]}", error=ShomateConventionError
            )
            for i, v in enumerate(values)
        )
    raise ShomateConventionError(
        f"Shomate coefficients must be A…H mapping or 8-length sequence; "
        f"got {type(raw).__name__}"
    )


__all__ = [
    "ShomateConventionError",
    "ShomateDomainError",
    "ShomateError",
    "ShomatePolynomial",
    "ShomateSegment",
    "ShomateSegmentError",
    "coefficients_from_mapping",
]
