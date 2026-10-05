"""Tabulated formation-Gibbs evaluator (JANAF ΔfG° tables).

Runtime family ``tabulated_janaf``: linear interpolation of tabulated
ΔfG°(T) on the printed grid, then G°/(R T) for Mode 2 reactions and
condensed-phase saturation. This is not a new pressure Mode — it is the
third polynomial-like thermo family beside nasa_cea_7/9 and shomate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from simulator.vapour_rail.nasa_cea import (
    R_J_PER_MOL_K,
    StandardState,
    ThermoState,
)


class TabulatedGibbsError(ValueError):
    """Base error for tabulated formation-Gibbs construction / evaluation."""


class TabulatedGibbsConventionError(TabulatedGibbsError):
    """Missing standard-state convention or empty / non-finite points."""


class TabulatedDomainError(TabulatedGibbsError):
    """T outside the tabulated grid, or T is not finite and > 0 K."""


def interpolate_tabulated(
    points: Sequence[tuple[float, float]], temperature_K: float
) -> float:
    """Linear interpolation of y(T) on a strictly increasing T grid."""
    if temperature_K < points[0][0] or temperature_K > points[-1][0]:
        raise TabulatedDomainError(
            f"{temperature_K} K is outside tabulated range "
            f"[{points[0][0]}, {points[-1][0]}] K"
        )
    for (t0, y0), (t1, y1) in zip(points, points[1:]):
        if t0 <= temperature_K <= t1:
            if temperature_K == t0:
                return y0
            if temperature_K == t1:
                return y1
            return y0 + (y1 - y0) * (temperature_K - t0) / (t1 - t0)
    raise TabulatedDomainError(
        f"{temperature_K} K is not bracketed by tabulated rows"
    )


@dataclass(frozen=True)
class TabulatedThermo:
    """One tabulated ΔfG° series, already converted to the reaction P°."""

    name: str
    standard_state: StandardState
    # (T_K, ΔfG° J/mol) at reference_pressure_Pa, increasing T.
    formation_gibbs_J_per_mol: tuple[tuple[float, float], ...]
    formula: str | None = None
    citation: str | None = None
    reference_pressure_Pa: float = 100_000.0
    source_id: str | None = None
    record_id: str | None = None
    native_phase: str | None = None
    native_reference_pressure_Pa: float | None = None

    def __post_init__(self) -> None:
        if self.standard_state not in (
            "gas",
            "condensed_solid",
            "condensed_liquid",
            "condensed",
        ):
            raise TabulatedGibbsConventionError(
                f"{self.name}: missing or unsupported standard_state "
                f"{self.standard_state!r}"
            )
        points = self.formation_gibbs_J_per_mol
        if len(points) < 2:
            raise TabulatedGibbsConventionError(
                f"{self.name}: tabulated ΔfG requires at least two points"
            )
        previous_t = 0.0
        for index, (t_k, g_j) in enumerate(points):
            t_f = float(t_k)
            g_f = float(g_j)
            if not math.isfinite(t_f) or t_f <= 0.0:
                raise TabulatedGibbsConventionError(
                    f"{self.name}: point[{index}] T must be finite and > 0 K"
                )
            if not math.isfinite(g_f):
                raise TabulatedGibbsConventionError(
                    f"{self.name}: point[{index}] ΔfG must be finite"
                )
            if index and t_f <= previous_t:
                raise TabulatedGibbsConventionError(
                    f"{self.name}: tabulated T must be strictly increasing"
                )
            previous_t = t_f
        pstd = float(self.reference_pressure_Pa)
        if not math.isfinite(pstd) or pstd <= 0.0:
            raise TabulatedGibbsConventionError(
                f"{self.name}: reference_pressure_Pa must be finite and > 0"
            )

    @property
    def T_min_K(self) -> float:
        return float(self.formation_gibbs_J_per_mol[0][0])

    @property
    def T_max_K(self) -> float:
        return float(self.formation_gibbs_J_per_mol[-1][0])

    def evaluate(self, T_K: float) -> ThermoState:
        T = float(T_K)
        if not math.isfinite(T) or T <= 0.0:
            raise TabulatedDomainError(
                f"{self.name}: T must be finite and > 0 K; got {T_K!r}"
            )
        if T < self.T_min_K or T > self.T_max_K:
            raise TabulatedDomainError(
                f"{self.name}: T={T} K outside domain "
                f"[{self.T_min_K}, {self.T_max_K}] K"
            )
        g_j = interpolate_tabulated(self.formation_gibbs_J_per_mol, T)
        g_over_RT = g_j / (R_J_PER_MOL_K * T)
        return ThermoState(
            T_K=T,
            cp_over_R=math.nan,
            h_over_RT=math.nan,
            s_over_R=math.nan,
            g_over_RT=g_over_RT,
        )
