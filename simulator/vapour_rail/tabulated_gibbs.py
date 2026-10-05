"""Tabulated formation-Gibbs evaluator (JANAF ΔfG° tables).

Runtime family ``tabulated_janaf``: linear interpolation of tabulated
ΔfG°(T) on the printed grid, then G°/(R T) for Mode 2 reactions and
condensed-phase saturation. This is not a new pressure Mode — it is the
third polynomial-like thermo family beside nasa_cea_7/9 and shomate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

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

    def __init__(self, message: str, *, kind: str = "domain") -> None:
        self.kind = kind
        super().__init__(message)


class TabulatedMissingNodeError(TabulatedGibbsError):
    """A blank node lies strictly inside the interpolation bracket."""

    def __init__(self, missing_node: Any, temperature: Any) -> None:
        self.missing_node = missing_node
        self.temperature = temperature
        super().__init__(
            "needed tabulated formation Gibbs row missing at "
            f"{missing_node} for interpolation at {temperature}"
        )


def _missing_tabulated_node(
    points: Sequence[tuple[Any, Any]],
    missing_nodes: Sequence[Any],
    temperature: Any,
) -> Any | None:
    if any(node_temperature == temperature for node_temperature, _y in points):
        return None
    for (left, _y0), (right, _y1) in zip(points, points[1:]):
        if left < temperature < right:
            return next(
                (node for node in missing_nodes if left < node < right),
                None,
            )
    return None


def interpolate_tabulated(
    points: Sequence[tuple[Any, Any]],
    temperature_K: Any,
    *,
    missing_nodes: Sequence[Any] = (),
) -> Any:
    """Linear interpolation of y(T) on a strictly increasing T grid.

    Accepts float or Decimal points. Arithmetic is the caller's type.
    A blank ``missing_nodes`` entry strictly inside the bracket is a refusal;
    a temperature that lands on a printed node is that node's value.
    """
    if missing_nodes:
        missing = _missing_tabulated_node(points, missing_nodes, temperature_K)
        if missing is not None:
            raise TabulatedMissingNodeError(missing, temperature_K)
    if temperature_K < points[0][0] or temperature_K > points[-1][0]:
        raise TabulatedDomainError(
            f"{temperature_K} K is outside tabulated range "
            f"[{points[0][0]}, {points[-1][0]}] K",
            kind="out_of_range",
        )
    for (t0, y0), (t1, y1) in zip(points, points[1:]):
        if t0 <= temperature_K <= t1:
            if temperature_K == t0:
                return y0
            if temperature_K == t1:
                return y1
            return y0 + (y1 - y0) * (temperature_K - t0) / (t1 - t0)
    raise TabulatedDomainError(
        f"{temperature_K} K is not bracketed by tabulated rows",
        kind="not_bracketed",
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
    # Temperatures with a row but no printed y. Empty for series that do not
    # record blanks (Pankratz has no blank-node index).
    missing_nodes: tuple[float, ...] = ()

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
        g_j = interpolate_tabulated(
            self.formation_gibbs_J_per_mol, T, missing_nodes=self.missing_nodes
        )
        g_over_RT = g_j / (R_J_PER_MOL_K * T)
        return ThermoState(
            T_K=T,
            cp_over_R=math.nan,
            h_over_RT=math.nan,
            s_over_R=math.nan,
            g_over_RT=g_over_RT,
        )
