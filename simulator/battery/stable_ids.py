"""Content-stable observation / point id fragments (F4 / S15 R-ord).

Durable ids must not embed list ordinals (segment-N, row=N, ::point:N).
Key by published temperature, column/field name, or closed T-bounds instead.
"""

from __future__ import annotations

import hashlib
import json

from decimal import Decimal
from typing import Any, Mapping


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


def series_row_extra(raw_item: Any) -> str | None:
    """Content suffix for a series row when T alone is not unique.

    Prefer a printed row label (run / sample / locator.paragraph) plus a
    short sha1 of the row payload with temperature fields removed. Distinct
    printed rows rematerialize to distinct ids without encounter ordinals.
    """

    if not isinstance(raw_item, Mapping):
        return None
    parts: list[str] = []
    label = raw_item.get("run")
    if label is None:
        label = raw_item.get("sample")
    if label is None:
        loc = raw_item.get("locator")
        if isinstance(loc, Mapping):
            label = loc.get("paragraph") or loc.get("row")
    if label is not None and str(label).strip():
        parts.append(f"row={_name_slug(str(label))}")
    skip = {
        "T_K",
        "T_C",
        "temperature_K",
        "temperature_quote",
        "quote",
        "locator",
    }
    residual = {k: raw_item[k] for k in raw_item if k not in skip}
    if residual:
        blob = json.dumps(residual, sort_keys=True, default=str, separators=(",", ":"))
        digest = hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]
        parts.append(f"h={digest}")
    return ":".join(parts) if parts else None



def series_point_id(
    parent_id: str,
    *,
    temperature: Any = None,
    field: str | None = None,
    extra: str | None = None,
) -> str:
    """Exploded series / multi-field child id without an encounter-order index."""

    if field:
        base = f"{parent_id}::field:{field}"
        return f"{base}:{extra}" if extra else base
    if temperature is not None:
        base = f"{parent_id}::T={temperature_token(temperature)}"
        return f"{base}:{extra}" if extra else base
    raise ValueError(
        "series point id requires a temperature or field name (no ordinal index)"
    )


def cao_raw_pca_id(*, temperature: Any) -> str:
    return f"cao_raw_pCa:T={temperature_token(temperature)}"
