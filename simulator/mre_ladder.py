"""MRE voltage ladder parsing and branch cap helpers."""

from __future__ import annotations

import math
from typing import Any


MRE_VOLTAGE_LADDER_FALLBACK = (
    {'voltage': 0.6, 'species': ('FeO',), 'min_hold_hours': 3},
    {'voltage': 0.9, 'species': ('Cr2O3',), 'min_hold_hours': 2},
    {'voltage': 1.0, 'species': ('MnO',), 'min_hold_hours': 2},
    {'voltage': 1.4, 'species': ('SiO2',), 'min_hold_hours': 5},
    {'voltage': 1.5, 'species': ('TiO2',), 'min_hold_hours': 3},
    {'voltage': 1.9, 'species': ('Al2O3',), 'min_hold_hours': 8},
    {'voltage': 2.2, 'species': ('MgO',), 'min_hold_hours': 5},
    {'voltage': 2.5, 'species': ('CaO',), 'min_hold_hours': 10},
)

MRE_DEFAULT_MIN_HOLD_HOURS = 3
C5_BRANCH_TWO_MAX_V_FALLBACK = 1.6


def coerce_mre_decomposition_voltage(value: Any) -> float | None:
    """Coerce a YAML decomposition voltage to a finite float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        voltage = float(value)
        return voltage if math.isfinite(voltage) else None
    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            return None
        try:
            low = float(value[0])
            high = float(value[1])
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(low) and math.isfinite(high)):
            return None
        return 0.5 * (low + high)
    if isinstance(value, str):
        stripped = value.strip()
        for prefix in ("<", ">", "~", "\u00b1"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
                break
        try:
            voltage = float(stripped)
        except (TypeError, ValueError):
            return None
        return voltage if math.isfinite(voltage) else None
    return None


def parse_mre_voltage_sequence_yaml(setpoints: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse ``setpoints['mre_voltage_sequence']['sequence']``.

    Returns the Python ladder shape used by the simulator:
    ``{voltage, species, min_hold_hours}``. Malformed entries are skipped.
    """
    block = ((setpoints or {}).get('mre_voltage_sequence', {}) or {})
    entries = block.get('sequence', []) or []
    if not isinstance(entries, list):
        return []

    parsed: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        species = entry.get('species')
        if not species or not isinstance(species, str):
            continue
        voltage = coerce_mre_decomposition_voltage(entry.get('decomposition_V'))
        if voltage is None:
            continue
        raw_hold = entry.get('min_hold_hours', MRE_DEFAULT_MIN_HOLD_HOURS)
        try:
            min_hold = max(0, int(raw_hold))
        except (TypeError, ValueError):
            min_hold = MRE_DEFAULT_MIN_HOLD_HOURS
        parsed.append({
            'voltage': float(voltage),
            'species': [str(species)],
            'min_hold_hours': min_hold,
        })

    parsed.sort(key=lambda e: e['voltage'])
    return parsed


def build_mre_voltage_sequence(setpoints: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the YAML-derived MRE ladder, or fallback if YAML is unusable."""
    sequence = parse_mre_voltage_sequence_yaml(setpoints)
    if sequence:
        return sequence
    return [
        {
            'voltage': entry['voltage'],
            'species': list(entry['species']),
            'min_hold_hours': entry['min_hold_hours'],
        }
        for entry in MRE_VOLTAGE_LADDER_FALLBACK
    ]


def branch_two_voltage_cap(setpoints: dict[str, Any] | None) -> float:
    """Return the C5 Branch Two maximum voltage from YAML strategy config."""
    block = ((setpoints or {}).get('mre_voltage_sequence', {}) or {})
    strategy = block.get('voltage_strategy', {}) or {}
    if not isinstance(strategy, dict):
        return C5_BRANCH_TWO_MAX_V_FALLBACK
    branch_two = strategy.get('branch_two', {}) or {}
    if not isinstance(branch_two, dict):
        return C5_BRANCH_TWO_MAX_V_FALLBACK
    voltage = coerce_mre_decomposition_voltage(branch_two.get('max_V'))
    if voltage is None or voltage <= 0.0:
        return C5_BRANCH_TWO_MAX_V_FALLBACK
    return float(voltage)


def c5_voltage_ladder(
    sequence: list[dict[str, Any]],
    setpoints: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Return the C5 Branch Two prefix selected by the YAML max voltage."""
    cap = branch_two_voltage_cap(setpoints)
    selected: list[dict[str, Any]] = []
    for entry in sequence or []:
        if not isinstance(entry, dict):
            continue
        voltage = coerce_mre_decomposition_voltage(entry.get('voltage'))
        if voltage is None or voltage > cap:
            continue
        species = entry.get('species') or []
        if isinstance(species, str):
            species_list = [species]
        else:
            species_list = list(species)
        raw_hold = entry.get('min_hold_hours', MRE_DEFAULT_MIN_HOLD_HOURS)
        try:
            min_hold = max(0, int(raw_hold))
        except (TypeError, ValueError):
            min_hold = MRE_DEFAULT_MIN_HOLD_HOURS
        selected.append({
            'voltage': float(voltage),
            'species': species_list,
            'min_hold_hours': min_hold,
        })
    return selected
