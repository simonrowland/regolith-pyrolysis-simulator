"""Recognize extract names for graphite / C–CO oxygen buffering."""

from __future__ import annotations

import re
import math


# Chase 1998 NIST-JANAF formation values at 298 K give the linear
# Ellingham approximation for 2 C + O2 -> 2 CO, per mol O2:
# ΔH = 2*(-110.527) = -221.054 kJ; ΔG = 2*(-137.163) = -274.326 kJ;
# ΔS = (ΔH - ΔG)/T = 0.17877 kJ/K. The pure-CO limit is distinct from the
# CCO buffer in simulator.oxygen_buffers, which includes the CO2 share
# through Boudouard equilibrium.
_C_TO_CO_DH_KJ_PER_MOL_O2 = -221.054
_C_TO_CO_DS_KJ_PER_K_PER_MOL_O2 = 0.17877

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


def carbon_delta_g_co_kj_per_mol_o2(temperature_K: float) -> float:
    """ΔG(2 C + O2 -> 2 CO) per mol O2. Falls with T."""
    try:
        temperature = float(temperature_K)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"temperature_K must be a finite number, got {temperature_K!r}"
        ) from exc
    if not math.isfinite(temperature):
        raise ValueError(
            f"temperature_K must be a finite number, got {temperature_K!r}"
        )
    return (
        _C_TO_CO_DH_KJ_PER_MOL_O2
        - temperature * _C_TO_CO_DS_KJ_PER_K_PER_MOL_O2
    )


__all__ = ["carbon_delta_g_co_kj_per_mol_o2", "is_c_co_buffer_token"]
