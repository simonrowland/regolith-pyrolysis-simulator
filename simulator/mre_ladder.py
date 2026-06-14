"""MRE voltage ladder parsing and C5 preset helpers."""

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
C5_LIMITED_MRE_CURRENT_A = 1000.0
C5_DEPLETION_AT_CAP_MARGIN_V = 0.05
C5_DEPLETION_LOW_CURRENT_A = 5.0
C5_DEPLETION_CONSECUTIVE_HOURS = 3
C5_DEPLETION_SAFETY_MAX_HOLD_HR = 800.0
DISABLED_PRESET_TARGETS = {
    'Na2O': 'pre-depleted by C3; not a selectable C5 target',
    'K2O': 'pre-depleted by C3; not a selectable C5 target',
}


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


def parse_ladder_from_setpoints(setpoints: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Canonical dispatch helper: return the usable MRE ladder for setpoints."""
    return build_mre_voltage_sequence(setpoints)


def _coerce_ladder_step(entry: dict[str, Any]) -> dict[str, Any] | None:
    voltage = coerce_mre_decomposition_voltage(entry.get('voltage'))
    if voltage is None:
        return None
    species = entry.get('species') or []
    if isinstance(species, str):
        species_list = [species]
    else:
        try:
            species_list = [str(item) for item in species if item]
        except TypeError:
            return None
    if not species_list:
        return None
    raw_hold = entry.get('min_hold_hours', MRE_DEFAULT_MIN_HOLD_HOURS)
    try:
        min_hold = max(0, int(raw_hold))
    except (TypeError, ValueError):
        min_hold = MRE_DEFAULT_MIN_HOLD_HOURS
    return {
        'voltage': float(voltage),
        'species': species_list,
        'min_hold_hours': min_hold,
    }


def max_voltage_for_target(
    target_oxide: str,
    ladder: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> float:
    """Return the ladder voltage that first reaches ``target_oxide``."""
    target = str(target_oxide or '').strip()
    if not target:
        return 0.0
    for entry in ladder or []:
        if not isinstance(entry, dict):
            continue
        step = _coerce_ladder_step(entry)
        if step is None:
            continue
        if target in step['species']:
            return float(step['voltage'])
    return 0.0


def allowed_oxides_for_target(
    target_oxide: str,
    ladder: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    max_voltage_V: Any,
) -> frozenset[str] | None:
    """Return the operator stage-targeting oxide prefix through ``target_oxide``.

    This is an EvalSpec/recipe selectivity filter (which ladder steps the
    operator asked to run), not a Nernst-derived voltage gate — physical
    reducibility is already enforced by the decomposition-voltage cap.
    """
    target = str(target_oxide or '').strip()
    if not target:
        return None
    allowed: set[str] = set()
    for step in filter_steps_up_to_max_v(ladder, max_voltage_V):
        allowed.update(step['species'])
        if target in step['species']:
            break
    return frozenset(allowed) if allowed else frozenset()


def filter_steps_up_to_max_v(
    ladder: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    max_voltage_V: Any,
) -> list[dict[str, Any]]:
    """Return normalized ladder steps whose threshold is at or below max V."""
    cap = coerce_mre_decomposition_voltage(max_voltage_V)
    if cap is None or cap <= 0.0:
        return []
    selected: list[dict[str, Any]] = []
    for entry in ladder or []:
        if not isinstance(entry, dict):
            continue
        step = _coerce_ladder_step(entry)
        if step is not None and step['voltage'] <= cap:
            selected.append(step)
    selected.sort(key=lambda entry: entry['voltage'])
    return selected


def preset_catalog(setpoints: dict[str, Any] | None) -> tuple[dict[str, Any], ...]:
    """Return UI-ready C5/MRE target presets from the canonical ladder."""
    presets: list[dict[str, Any]] = [{
        'id': 'off',
        'label': 'MRE off',
        'c5_enabled': False,
        'mre_target_species': '',
        'mre_max_voltage_V': 0.0,
        'enabled': True,
        'legacy': False,
    }]
    for entry in parse_ladder_from_setpoints(setpoints):
        step = _coerce_ladder_step(entry)
        if step is None:
            continue
        target = step['species'][0]
        disabled_reason = DISABLED_PRESET_TARGETS.get(target, '')
        presets.append({
            'id': f'target:{target}',
            'label': target,
            'target_oxide': target,
            'c5_enabled': True,
            'mre_target_species': target,
            'mre_max_voltage_V': step['voltage'],
            'enabled': not disabled_reason,
            'disabled_reason': disabled_reason,
            'legacy': False,
        })
    return tuple(presets)
