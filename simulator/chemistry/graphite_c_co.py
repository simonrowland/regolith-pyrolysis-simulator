"""Recognize extract names for graphite / C–CO oxygen buffering."""

from __future__ import annotations

import re


# Buffer tokens that mean graphite / C–CO (not Frost IW/NNO/QFM/WM, and not
# free prose about CO/Ar mixing). Normalized by _normalize_buffer_token.
_C_CO_BUFFER_TOKENS = frozenset(
    {
        "C-CO",
        "CCO",
        "C/CO",
        "GRAPHITE-CO",
        "GRAPHITE/CO",
        "GRAPHITE-C-CO",
        "GRAPHITE/C-CO",
        "C-CO-CO2",
        "CCO2",
        "C-CO2",
    }
)


def _normalize_buffer_token(token: str) -> str:
    cleaned = token.strip().upper().replace(" ", "").replace("_", "-")
    # Collapse runs of hyphens from mixed separators.
    return re.sub(r"-{2,}", "-", cleaned)


def is_c_co_buffer_token(token: str) -> bool:
    """True when the extract's fO2_control.buffer names graphite / C–CO."""
    if not token or not isinstance(token, str):
        return False
    return _normalize_buffer_token(token) in _C_CO_BUFFER_TOKENS


__all__ = ["is_c_co_buffer_token"]
