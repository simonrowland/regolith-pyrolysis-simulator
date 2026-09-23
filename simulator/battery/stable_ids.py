"""Content-stable observation / point id fragments (F4 / S15 R-ord).

Durable ids must not embed list ordinals (segment-N, row=N, ::point:N).
Key by published temperature, column/field name, or closed T-bounds instead.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


def temperature_token(value: Any) -> str:
    """Canonical temperature fragment for an id (as-published when given a str)."""

    if value is None:
        raise ValueError("temperature token is required for a content-stable id")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("temperature token is empty")
        return text
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")


def phase_window_suffix(
    quantity: str,
    *,
    lower_K: Any = None,
    upper_K: Any = None,
) -> str:
    """JANAF series segment key from closed T-bounds, not ordinal segment index."""

    lo = "open" if lower_K is None else temperature_token(lower_K)
    hi = "open" if upper_K is None else temperature_token(upper_K)
    if lo == "open" and hi == "open":
        return f"{quantity}:phase-window:whole"
    return f"{quantity}:phase-window:T={lo}..{hi}"


def _name_slug(name: str) -> str:
    return "-".join(str(name).strip().lower().split())


def tabulated_cell_suffix(
    quantity: str,
    *,
    temperature: Any,
    column: str,
    basis: str | None = None,
    name: str | None = None,
    extra: str | None = None,
) -> str:
    """USGS (and peers) cell key: quantity + T + column (+ printed name) — no row ordinal."""

    mid = f"{basis}:" if basis else ""
    suffix = f"{quantity}:{mid}T={temperature_token(temperature)}:col={column}"
    if name:
        suffix = f"{suffix}:name={_name_slug(name)}"
    if extra:
        suffix = f"{suffix}:{extra}"
    return suffix


def series_point_id(parent_id: str, *, temperature: Any = None, field: str | None = None) -> str:
    """Exploded series / multi-field child id without an encounter-order index."""

    if field:
        return f"{parent_id}::field:{field}"
    if temperature is not None:
        return f"{parent_id}::T={temperature_token(temperature)}"
    raise ValueError(
        "series point id requires a temperature or field name (no ordinal index)"
    )


def cao_raw_pca_id(*, temperature: Any) -> str:
    return f"cao_raw_pCa:T={temperature_token(temperature)}"
