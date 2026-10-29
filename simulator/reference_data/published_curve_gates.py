"""Shared pass rule for sampled published-curve table gates."""

from __future__ import annotations

from typing import Mapping, Sequence


def three_cell_gate_passes(residuals: Sequence[Mapping[str, float]]) -> bool:
    """A published equation passes only when three sampled cells fit tolerance."""
    return len(residuals) >= 3 and all(
        abs(row["residual"]) <= row["tolerance"] for row in residuals
    )
