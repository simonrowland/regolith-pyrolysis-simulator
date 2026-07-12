"""MRE voltage ladder parsing and C5 preset helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulator.chemistry.ellingham_thermo import (
    ELLINGHAM_METAL_PHASE_CONDENSED,
    ellingham_fit_extrapolation,
    ellingham_metal_phase_kind,
    ellingham_segment_for_temperature,
)
from simulator.physical_constants import FARADAY

# Static decomposition voltages at ~1873 K / ~1600 C (V).
# Retained as the non-authoritative fallback anchor for species outside the
# Ellingham graph coverage or failed graph queries.
DECOMP_VOLTAGES = {
    # NiO source: DeltaGf(NiO, ~1873 K) ~= -76 kJ/mol
    # [Hemingway 1990 Am. Mineral. 75:781 + Robie & Hemingway + NEA
    # Chemical Thermodynamics of Nickel]; E = -DeltaGf/(2F) ~= 0.39 V
    # standard-state. Runtime Nernst applies melt activity + pO2.
    'NiO': 0.39,
    # Na2O/K2O volatility caveat: condensed-phase DeltaGf at 1873 K is
    # estimated; Na/K are volatile above their boiling points, so activity
    # and vapor partitioning can lower the effective threshold. Hold legacy
    # 0.5 V pending activity/vapor-aware grounding.
    # provenance: Na2O/K2O legacy rungs -- REF-019 Table 2 plus REF-050/REF-051
    # activity primaries; value intentionally unchanged.
    'Na2O': 0.5,
    'K2O': 0.5,
    # O'Neill 1988 + Chase 1998 Fe-O emf/raw-thermo anchor.
    'FeO': 0.75,
    # Reference-only legacy full-reduction threshold. Live MRE fixed
    # reduction excludes Fe2O3 because ferric Fe is represented by the
    # fO2-coupled Kress91 split, not by a terminal-O2 full-reduction rung.
    'Fe2O3': 0.90,
    # NIST-JANAF/Chase 1998 + Barin; modest-confidence upper-range anchor.
    'Cr2O3': 0.95,
    # NIST-JANAF/Chase 1998 + Barin; modest-confidence anchor.
    'MnO': 1.05,
    # Chase 1998 raw-thermo anchor.
    'SiO2': 1.45,
    # Chase 1998 + Barin raw-thermo anchor.
    'TiO2': 1.70,
    # NIST-JANAF/Chase 1998 + Barin raw-thermo anchor.
    'Al2O3': 1.95,
    'MgO': 2.2,
    'CaO': 2.5,
}

CANONICAL_DECOMPOSITION_VOLTAGE_TOKEN = "canonical"
MRE_REFERENCE_TEMPERATURE_K = 1873.15
MRE_GRAPH_AUTHORITY = "ellingham_graph"
MRE_GRAPH_FALLBACK_AUTHORITY = "ellingham_fallback"
MRE_DECLARED_AUTHORITY = "operator_declared"
MRE_MN_DIAGNOSTIC_STATUS = (
    "diagnostic_reconstructed_mn_row_not_authoritative_for_mre"
)

MRE_ELLINGHAM_METAL_BY_OXIDE = {
    "Na2O": "Na",
    "K2O": "K",
    "FeO": "Fe",
    "Cr2O3": "Cr",
    "MnO": "Mn",
    "SiO2": "Si",
    "TiO2": "Ti",
    "Al2O3": "Al",
    "MgO": "Mg",
    "CaO": "Ca",
}


@dataclass(frozen=True)
class MREDecompositionVoltageReference:
    oxide: str
    voltage: float
    temperature_K: float
    authority: str
    authoritative: bool
    status: str
    ellingham_species: str | None = None
    raw_graph_voltage_V: float | None = None
    metal_product_phase: str | None = None
    ellingham_phase_basis: str | None = None


def _coerce_temperature_K(temperature_K: float | None) -> float:
    if temperature_K is None:
        return MRE_REFERENCE_TEMPERATURE_K
    try:
        value = float(temperature_K)
    except (TypeError, ValueError):
        return MRE_REFERENCE_TEMPERATURE_K
    return value if math.isfinite(value) and value > 0.0 else MRE_REFERENCE_TEMPERATURE_K


def mre_decomposition_voltage_reference(
    species: Any,
    *,
    temperature_K: float | None = None,
) -> MREDecompositionVoltageReference | None:
    """Return the graph-derived MRE E0(T), or flagged static fallback."""
    if not species or isinstance(species, bool):
        return None
    oxide = str(species)
    T_K = _coerce_temperature_K(temperature_K)
    metal_species = MRE_ELLINGHAM_METAL_BY_OXIDE.get(oxide)
    failure_status: str | None = None
    raw_graph_voltage_V: float | None = None
    metal_product_phase: str | None = None
    ellingham_phase_basis: str | None = None

    if metal_species is None:
        failure_status = "ellingham_query_failed:species_not_graph_covered"
    else:
        try:
            segment = ellingham_segment_for_temperature(metal_species, T_K)
            delta_g_kj_per_mol_o2 = segment.delta_g_kJ_per_mol_O2(T_K)
            metal_product_phase = ellingham_metal_phase_kind(metal_species, T_K)
            ellingham_phase_basis = segment.phase_basis
        except Exception as exc:
            failure_status = f"ellingham_query_failed:{type(exc).__name__}"
        else:
            try:
                delta_g = float(delta_g_kj_per_mol_o2)
            except (TypeError, ValueError):
                delta_g = math.nan
            voltage = -delta_g * 1000.0 / (4.0 * FARADAY)
            if math.isfinite(voltage) and voltage > 0.0:
                raw_graph_voltage_V = voltage
            extrapolation = ellingham_fit_extrapolation(
                T_K,
                species=metal_species,
                consumer="mre-decomposition-voltage",
            )
            if not math.isfinite(delta_g):
                failure_status = "ellingham_nonfinite_refused:delta_g"
            elif not math.isfinite(voltage):
                failure_status = "ellingham_nonfinite_refused:voltage"
            elif voltage <= 0.0:
                failure_status = "ellingham_nonpositive_refused:voltage"
            elif extrapolation is not None:
                failure_status = "ellingham_extrapolation_refused:extrapolation_limited"
            else:
                authoritative = True
                status = "ok"
                if oxide == "MnO":
                    authoritative = False
                    status = MRE_MN_DIAGNOSTIC_STATUS
                return MREDecompositionVoltageReference(
                    oxide=oxide,
                    voltage=voltage,
                    temperature_K=T_K,
                    authority=MRE_GRAPH_AUTHORITY,
                    authoritative=authoritative,
                    status=status,
                    ellingham_species=metal_species,
                    raw_graph_voltage_V=raw_graph_voltage_V,
                    metal_product_phase=metal_product_phase,
                    ellingham_phase_basis=ellingham_phase_basis,
                )

    fallback_voltage = DECOMP_VOLTAGES.get(oxide)
    if fallback_voltage is None:
        return None
    return MREDecompositionVoltageReference(
        oxide=oxide,
        voltage=float(fallback_voltage),
        temperature_K=T_K,
        authority=MRE_GRAPH_FALLBACK_AUTHORITY,
        authoritative=False,
        status=failure_status or "ellingham_query_failed:unknown",
        ellingham_species=metal_species,
        raw_graph_voltage_V=raw_graph_voltage_V,
        metal_product_phase=metal_product_phase or ELLINGHAM_METAL_PHASE_CONDENSED,
        ellingham_phase_basis=ellingham_phase_basis,
    )


def _reference_metadata(
    reference: MREDecompositionVoltageReference,
) -> dict[str, Any]:
    return {
        "voltage_authority": reference.authority,
        "voltage_authoritative": reference.authoritative,
        "voltage_status": reference.status,
        "voltage_temperature_K": reference.temperature_K,
        "ellingham_species": reference.ellingham_species,
        "metal_product_phase": reference.metal_product_phase,
        "ellingham_phase_basis": reference.ellingham_phase_basis,
    }

# Fallback ladder used when the YAML MRE sequence is missing/unusable. Rung
# voltages resolve through the same graph-first canonical helper as the YAML
# "canonical" token; DECOMP_VOLTAGES remains only the flagged static fallback
# anchor for graph-uncovered species.
# Na2O/K2O (C3-depleted, DISABLED_PRESET_TARGETS) and Fe2O3 (ferric Fe is
# represented by fO2-coupled speciation, not a fixed full-reduction rung) are
# intentionally absent from the C5 fallback ladder.
# Each tuple is (species, min_hold_hours); voltage is resolved at build time.
_FALLBACK_LADDER_RUNGS = (
    (('NiO',), 2),
    (('FeO',), 3),
    (('Cr2O3',), 2),
    (('MnO',), 2),
    (('SiO2',), 5),
    (('TiO2',), 3),
    (('Al2O3',), 8),
    (('MgO',), 5),
    (('CaO',), 10),
)


def _fallback_ladder(
    *,
    temperature_K: float | None = None,
) -> tuple[dict[str, Any], ...]:
    entries = []
    for species, min_hold_hours in _FALLBACK_LADDER_RUNGS:
        reference = mre_decomposition_voltage_reference(
            species[0],
            temperature_K=temperature_K,
        )
        if reference is None:
            continue
        entry = {
            'voltage': reference.voltage,
            'species': species,
            'min_hold_hours': min_hold_hours,
        }
        entry.update(_reference_metadata(reference))
        entries.append(entry)
    entries.sort(key=lambda entry: entry['voltage'])
    return tuple(entries)


MRE_VOLTAGE_LADDER_FALLBACK = _fallback_ladder(
    temperature_K=MRE_REFERENCE_TEMPERATURE_K,
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


def canonical_mre_decomposition_voltage(
    species: Any,
    *,
    temperature_K: float | None = None,
) -> float | None:
    """Return the canonical MRE decomposition voltage for ``species``."""
    reference = mre_decomposition_voltage_reference(
        species,
        temperature_K=temperature_K,
    )
    return None if reference is None else reference.voltage


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


def resolve_mre_decomposition_voltage(
    species: Any,
    value: Any,
    *,
    temperature_K: float | None = None,
) -> float | None:
    """Resolve an explicit YAML voltage or the canonical-voltage token."""
    if isinstance(value, str) and (
        value.strip().lower() == CANONICAL_DECOMPOSITION_VOLTAGE_TOKEN
    ):
        return canonical_mre_decomposition_voltage(
            species,
            temperature_K=temperature_K,
        )
    return coerce_mre_decomposition_voltage(value)


def parse_mre_voltage_sequence_yaml(
    setpoints: dict[str, Any] | None,
    *,
    temperature_K: float | None = None,
) -> list[dict[str, Any]]:
    """Parse ``setpoints['mre_voltage_sequence']['sequence']``.

    Returns the Python ladder shape used by the simulator:
    ``{voltage, species, min_hold_hours}``. ``decomposition_V: "canonical"``
    resolves through the graph-first canonical helper. Malformed entries
    are skipped.
    """
    if not isinstance(setpoints, Mapping):
        return []
    block = setpoints.get('mre_voltage_sequence', {}) or {}
    if not isinstance(block, Mapping):
        return []
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
        raw_voltage = entry.get('decomposition_V')
        reference = None
        if isinstance(raw_voltage, str) and (
            raw_voltage.strip().lower() == CANONICAL_DECOMPOSITION_VOLTAGE_TOKEN
        ):
            reference = mre_decomposition_voltage_reference(
                species,
                temperature_K=temperature_K,
            )
            voltage = None if reference is None else reference.voltage
        else:
            voltage = coerce_mre_decomposition_voltage(raw_voltage)
        if voltage is None:
            continue
        raw_hold = entry.get('min_hold_hours', MRE_DEFAULT_MIN_HOLD_HOURS)
        try:
            min_hold = max(0, int(raw_hold))
        except (TypeError, ValueError):
            min_hold = MRE_DEFAULT_MIN_HOLD_HOURS
        parsed_entry = {
            'voltage': float(voltage),
            'species': [str(species)],
            'min_hold_hours': min_hold,
        }
        if reference is not None:
            parsed_entry.update(_reference_metadata(reference))
        else:
            parsed_entry.update({
                "voltage_authority": MRE_DECLARED_AUTHORITY,
                "voltage_authoritative": False,
                "voltage_status": "operator_declared",
                "voltage_temperature_K": _coerce_temperature_K(temperature_K),
                "ellingham_species": None,
            })
        parsed.append(parsed_entry)

    parsed.sort(key=lambda e: e['voltage'])
    return parsed


def build_mre_voltage_sequence(
    setpoints: dict[str, Any] | None,
    *,
    temperature_K: float | None = None,
) -> list[dict[str, Any]]:
    """Return the YAML-derived MRE ladder, or fallback if YAML is unusable."""
    sequence = parse_mre_voltage_sequence_yaml(
        setpoints,
        temperature_K=temperature_K,
    )
    if sequence:
        return sequence
    return [
        dict(entry, species=list(entry['species']))
        for entry in _fallback_ladder(temperature_K=temperature_K)
    ]


def parse_ladder_from_setpoints(
    setpoints: dict[str, Any] | None,
    *,
    temperature_K: float | None = None,
) -> list[dict[str, Any]]:
    """Canonical dispatch helper: return the usable MRE ladder for setpoints."""
    return build_mre_voltage_sequence(setpoints, temperature_K=temperature_K)


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
    """Return the operator stage-targeting oxide set for ``target_oxide``.

    This is an EvalSpec/recipe selectivity filter (which ladder step the
    operator asked to run), not a Nernst-derived voltage gate — physical
    reducibility is already enforced by the decomposition-voltage cap.
    """
    target = str(target_oxide or '').strip()
    if not target:
        return None
    for step in filter_steps_up_to_max_v(ladder, max_voltage_V):
        if target in step['species']:
            return frozenset(step['species'])
    return frozenset()


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
