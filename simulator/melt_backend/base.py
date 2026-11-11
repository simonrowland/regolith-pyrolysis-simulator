"""
Melt Backend — Abstract Interface & Data Classes
=================================================

Defines the abstract MeltBackend interface and EquilibriumResult
that all thermodynamic backends must implement:
AlphaMELTSBackend, VapoRockBackend, MAGEMinBackend, and
InternalAnalyticalBackend.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import math
from typing import Any, ClassVar, Dict, List, Mapping, Optional, Sequence

from engines.domain_reason import OutOfDomainReason
from simulator.melt_regime import liquid_fraction_invalidity
from simulator.scalar_boundary import finite_float


BACKEND_CAPABILITY_KEYS = (
    'silicate_melt',
    'gas_volatiles',
    'salt_phase',
    'sulfide_matte',
    'metal_alloy',
)

DEFAULT_BACKEND_CAPABILITIES = {
    key: (key == 'silicate_melt')
    for key in BACKEND_CAPABILITY_KEYS
}

REDOX_MODE_NOT_AN_INPUT = 'not_an_input'

ANTOINE_FALLBACK_SOURCE_PREFIX = 'antoine_fallback_from_vaporock'
_EQUILIBRIUM_CACHE_HOT_FIELDS = frozenset({
    'activity_coefficients',
    'reported_activity_coefficients',
    'activity_coefficient_details',
    'vapor_pressures_Pa',
    'vapor_pressures_source',
    'vaporock_full_speciation_Pa',
    'liquid_fraction',
    'phase_assemblage_available',
    'status',
    'liquid_composition_wt_pct',
    'fe_redox_split',
})
_EQUILIBRIUM_CACHE_CONSUMED_DIAGNOSTICS = frozenset({
    'authority',
    'authoritative_for_requested_vapor_pressure',
    'vapor_pressure_backend_status',
    'vapor_pressure_backend_status_reason',
    'commissioning_notice',
    'certified_band',
    'imcc_notices',
    'fo2_gas_only',
    'backend_status_reason',
    'empty_speciation_cause',
    'backend_failure_reason_code',
    'backend_failure_category',
    'input_composition_projection',
    'engine_reason',
    'backend_failure_disposition',
    'reported_activity_coefficients',
    'subprocess_failure',
})

# Append-only wire table for consumer-visible engine notices and warnings.
# Numbers are scoped by engine and never reused. Retired entries remain here.
ENGINE_CACHE_NOTICE_CODE_TABLE = (
    ('openimcc', 1, 'openimcc_flag', 'OpenIMCC emitted a melt or gas flag.'),
    ('openimcc', 2, 'imcc_complex_saturation', 'OpenIMCC complex saturation boundary.'),
    ('openimcc', 3, 'openimcc_temperature_extrapolated', 'OpenIMCC temperature is outside the datapack band.'),
    ('openimcc', 4, 'openimcc_composition_outside_validated_envelope', 'OpenIMCC composition is outside its validated envelope.'),
    ('openimcc', 5, 'openimcc_notice', 'OpenIMCC emitted a bridge notice.'),
    ('openimcc', 6, 'openimcc_fe2o3_fold', 'Ferric iron was folded to total FeO.'),
    ('openimcc', 7, 'openimcc_ferric_component_collapsed', 'OpenIMCC collapsed a ferric component.'),
    ('openimcc', 8, 'openimcc_projection_crmn_relaxed', 'OpenIMCC relaxed the Cr/Mn projection threshold.'),
    ('openimcc', 9, 'openimcc_projected_bulk', 'OpenIMCC projected bulk composition.'),
    ('openimcc', 10, 'openimcc_gas_flag', 'OpenIMCC gas calculation emitted a species flag.'),
    ('openimcc', 11, 'openimcc_gas_unavailable', 'OpenIMCC gas datapack was unavailable.'),
    ('openimcc', 12, 'fo2_oxygen_balance_effusion_solved', 'OpenIMCC solved oxygen-balance effusion.'),
    ('openimcc', 13, 'input_omitted', 'An OpenIMCC gas channel was omitted.'),
    ('openimcc', 90, 'warning_text', 'OpenIMCC warning text used by cell classification.'),
    ('alphamelts', 1, 'out_of_certified_band', 'AlphaMELTS ran outside its certified band.'),
    ('alphamelts', 2, 'engine_model_caveat', 'AlphaMELTS reported a model caveat.'),
    ('alphamelts', 3, 'input_omitted', 'AlphaMELTS omitted an input.'),
    ('alphamelts', 4, 'fo2_gas_only', 'External oxygen fugacity was applied to gas only.'),
    ('alphamelts', 90, 'warning_text', 'AlphaMELTS warning text used by cell classification.'),
    ('thermoengine', 1, 'out_of_certified_band', 'ThermoEngine ran outside its certified band.'),
    ('thermoengine', 2, 'engine_model_caveat', 'ThermoEngine reported a model caveat.'),
    ('thermoengine', 3, 'fo2_gas_only', 'External oxygen fugacity was applied to gas only.'),
    ('thermoengine', 90, 'warning_text', 'ThermoEngine warning text used by cell classification.'),
    ('vaporock', 1, 'out_of_certified_band', 'VapoRock ran outside its certified band.'),
    ('vaporock', 90, 'warning_text', 'VapoRock warning text used by cell classification.'),
    ('magemin', 1, 'out_of_certified_band', 'MAGEMin ran outside its certified band.'),
    ('magemin', 2, 'engine_model_caveat', 'MAGEMin reported a model caveat.'),
    ('magemin', 3, 'input_omitted', 'MAGEMin omitted an input.'),
    ('magemin', 90, 'warning_text', 'MAGEMin warning text used by cell classification.'),
)
_ENGINE_NOTICE_BY_CODE = {
    (engine, code): number
    for engine, number, code, _description in ENGINE_CACHE_NOTICE_CODE_TABLE
}
_ENGINE_NOTICE_BY_NUMBER = {
    (engine, number): code
    for engine, number, code, _description in ENGINE_CACHE_NOTICE_CODE_TABLE
}
_OPENIMCC_PAYLOAD_CODES = frozenset({
    'openimcc_fe2o3_fold',
    'openimcc_ferric_component_collapsed',
    'openimcc_projection_crmn_relaxed',
    'openimcc_projected_bulk',
})


def encode_engine_notice_codes(
    engine_id: str,
    notices: Sequence[Mapping[str, Any]],
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...]]:
    """Encode ordered notice rows as sorted codes plus typed row values."""
    encoded: list[tuple[int, int, dict[str, Any]]] = []
    for position, notice in enumerate(notices):
        raw_code = notice.get('code')
        code = str(raw_code or notice.get('kind') or '')
        number = _ENGINE_NOTICE_BY_CODE.get((engine_id, code))
        if number is None:
            raise ValueError(
                f'unregistered {engine_id} engine notice code {code!r}'
            )
        fields = {
            str(key): _equilibrium_cache_json(value)
            for key, value in notice.items()
            if key not in {'kind', 'code'}
        }
        encoded.append((number, position, fields))
    encoded.sort(key=lambda row: row[0])
    return (
        tuple(number for number, _position, _fields in encoded),
        tuple(
            {'position': position, **fields}
            for _number, position, fields in encoded
        ),
    )


def decode_engine_notice_codes(
    engine_id: str,
    codes: Sequence[int],
    values: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Decode notice codes with the append-only table and restore input order."""
    if len(codes) != len(values) or tuple(codes) != tuple(sorted(codes)):
        raise ValueError('invalid engine notice code payload')
    decoded: list[tuple[int, dict[str, Any]]] = []
    for number, payload in zip(codes, values):
        code = _ENGINE_NOTICE_BY_NUMBER.get((engine_id, int(number)))
        if code is None:
            raise ValueError(
                f'unregistered {engine_id} engine notice number {number!r}'
            )
        row = dict(payload)
        position = row.pop('position', None)
        if type(position) is not int:
            raise ValueError('invalid engine notice position')
        if code in _OPENIMCC_PAYLOAD_CODES:
            row['kind'] = 'openimcc_notice'
            row['code'] = code
        else:
            row['kind'] = code
        decoded.append((position, row))
    decoded.sort(key=lambda item: item[0])
    return [row for _position, row in decoded]


def encode_engine_warning_codes(
    engine_id: str,
    warnings: Sequence[Any],
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...]]:
    """Encode warning messages with a registered engine warning code."""
    number = _ENGINE_NOTICE_BY_CODE.get((engine_id, 'warning_text'))
    if number is None:
        raise ValueError(f'unregistered {engine_id} engine warning code')
    return (
        tuple(number for _warning in warnings),
        tuple(
            {'position': position, 'text': _equilibrium_cache_json(warning)}
            for position, warning in enumerate(warnings)
        ),
    )


def decode_engine_warning_codes(
    engine_id: str,
    codes: Sequence[int],
    values: Sequence[Mapping[str, Any]],
) -> list[Any]:
    """Restore warning values using the engine's registered warning code."""
    number = _ENGINE_NOTICE_BY_CODE.get((engine_id, 'warning_text'))
    if number is None or len(codes) != len(values) or any(code != number for code in codes):
        raise ValueError('invalid engine warning code payload')
    restored: list[tuple[int, Any]] = []
    for payload in values:
        position = payload.get('position')
        if type(position) is not int or 'text' not in payload:
            raise ValueError('invalid engine warning value')
        restored.append((position, payload['text']))
    restored.sort(key=lambda item: item[0])
    return [value for _position, value in restored]


def _equilibrium_cache_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _equilibrium_cache_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_equilibrium_cache_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _cache_map(
    value: Any, *, positive: bool = False, pressure: bool = False
) -> dict[str, float | None]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float | None] = {}
    for key, raw in sorted(value.items(), key=lambda item: str(item[0])):
        number = finite_float(raw)
        if pressure:
            if number is not None and number > 0.0:
                result[str(key)] = number
            elif number is None or number < 0.0:
                result[str(key)] = None
        elif number is not None and (not positive or number > 0.0):
            result[str(key)] = number
    return result


def vapor_pressure_source_code(source: Any) -> str | None:
    """Keep only source labels that affect battery classification."""
    if not isinstance(source, str):
        return None
    if source in {"fallback", "speciation", "floor"}:
        return source
    if source.startswith(ANTOINE_FALLBACK_SOURCE_PREFIX) or "fallback" in source:
        return 'fallback'
    if source == 'vaporock' or source.startswith(('vaporock', 'builtin_authoritative')):
        return 'speciation'
    if (
        'melt_dissociation_pO2_floor_inverted_through_mass_action' in source
        or 'value_is_floor' in source
    ):
        return 'floor'
    return None


def _activity_detail_pairs(value: Any) -> list[list[Any]]:
    if not isinstance(value, Mapping):
        return []
    pairs: list[list[Any]] = []
    for key, detail in sorted(value.items(), key=lambda item: str(item[0])):
        if not isinstance(detail, Mapping):
            continue
        standard = detail.get('standard_state')
        if not isinstance(standard, Mapping):
            standard = {}
        values = (
            _equilibrium_cache_json(detail.get('coefficient_basis')),
            _equilibrium_cache_json(standard.get('convention')),
            _equilibrium_cache_json(standard.get('phase')),
            _equilibrium_cache_json(standard.get('component_basis')),
        )
        if any(value is not None for value in values):
            pairs.append([str(key), *values])
    return pairs


def equilibrium_cache_from_value(
    payload: Any,
    *,
    engine_id: str | None = None,
) -> dict[str, Any]:
    """Restore the fixed scorer projection from its compact JSON array."""
    if not isinstance(payload, list) or len(payload) not in {11, 12}:
        raise ValueError('invalid compact equilibrium cache value')

    def restore_pairs(pairs: Any) -> dict[str, Any]:
        if isinstance(pairs, Mapping):
            return {str(key): value for key, value in pairs.items()}
        if not isinstance(pairs, list):
            raise ValueError('invalid compact equilibrium cache map')
        result: dict[str, Any] = {}
        for pair in pairs:
            if not isinstance(pair, list) or len(pair) != 2 or not isinstance(pair[0], str):
                raise ValueError('invalid compact equilibrium cache map entry')
            result[pair[0]] = pair[1]
        return result

    restored: dict[str, Any] = {}
    status, liquid_fraction, phase_available = payload[:3]
    if status is not None:
        restored['status'] = status
    if liquid_fraction is not None:
        restored['liquid_fraction'] = liquid_fraction
    if phase_available is not None:
        restored['phase_assemblage_available'] = bool(phase_available)
    for index, field_name in (
        (3, 'activity_coefficients'),
        (4, 'reported_activity_coefficients'),
        (6, 'vapor_pressures_Pa'),
        (7, 'vapor_pressures_source'),
        (8, 'vaporock_full_speciation_Pa'),
        (9, 'liquid_composition_wt_pct'),
        (10, 'fe_redox_split'),
    ):
        values = restore_pairs(payload[index])
        if values:
            restored[field_name] = values
    details: dict[str, Any] = {}
    if not isinstance(payload[5], list):
        raise ValueError('invalid compact equilibrium activity details')
    for pair in payload[5]:
        if not isinstance(pair, list) or len(pair) != 5 or not isinstance(pair[0], str):
            raise ValueError('invalid compact equilibrium activity detail')
        _name, basis, convention, phase, component_basis = pair
        detail: dict[str, Any] = {}
        if basis is not None:
            detail['coefficient_basis'] = basis
        standard = {
            name: value
            for name, value in (
                ('convention', convention),
                ('phase', phase),
                ('component_basis', component_basis),
            )
            if value is not None
        }
        if standard:
            detail['standard_state'] = standard
        details[_name] = detail
    if details:
        restored['activity_coefficient_details'] = details
    if len(payload) == 12:
        metadata = payload[11]
        if not isinstance(metadata, Mapping):
            raise ValueError('invalid compact equilibrium consumer metadata')
        diagnostics = metadata.get('diagnostics')
        diagnostics = dict(diagnostics) if isinstance(diagnostics, Mapping) else {}
        notice_codes = metadata.get('notice_codes', ())
        notice_values = metadata.get('notice_values', ())
        if notice_codes:
            if engine_id is None:
                raise ValueError('engine_id is required to restore engine notices')
            for row in decode_engine_notice_codes(
                engine_id, notice_codes, notice_values
            ):
                source = row.pop('_source', None)
                if source == 'commissioning_notice':
                    diagnostics['commissioning_notice'] = row
                elif source == 'diagnostics_imcc_notices':
                    diagnostics.setdefault('imcc_notices', []).append(row)
                    restored.setdefault('imcc_notices', []).append(row)
                elif source == 'result_imcc_notices':
                    restored.setdefault('imcc_notices', []).append(row)
                elif source == 'fo2_gas_only':
                    diagnostics['fo2_gas_only'] = {
                        'notice': FO2_GAS_ONLY_NOTICE,
                        **row,
                    }
                else:
                    raise ValueError(f'invalid engine notice source {source!r}')
        warning_codes = metadata.get('warning_codes', ())
        warning_values = metadata.get('warning_values', ())
        if warning_codes:
            if engine_id is None:
                raise ValueError('engine_id is required to restore engine warnings')
            restored['warnings'] = decode_engine_warning_codes(
                engine_id, warning_codes, warning_values
            )
        if diagnostics:
            restored['diagnostics'] = diagnostics
        if 'imcc_model_id' in metadata:
            restored['imcc_model_id'] = metadata['imcc_model_id']
    return restored


def _equilibrium_cache_consumer_metadata(
    values: Mapping[str, Any],
    *,
    engine_id: str | None,
) -> dict[str, Any]:
    diagnostics = values.get('diagnostics')
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    encoded_diagnostics = {
        'commissioning_notice',
        'imcc_notices',
        'fo2_gas_only',
    }
    selected_diagnostics = {
        name: _equilibrium_cache_json(diagnostics[name])
        for name in _EQUILIBRIUM_CACHE_CONSUMED_DIAGNOSTICS
        if name in diagnostics and name not in encoded_diagnostics
    }
    failure = selected_diagnostics.get('subprocess_failure')
    if isinstance(failure, Mapping):
        selected_diagnostics['subprocess_failure'] = {
            'returncode': failure.get('returncode')
        }

    metadata: dict[str, Any] = {}
    if selected_diagnostics:
        metadata['diagnostics'] = selected_diagnostics

    notice_rows: list[dict[str, Any]] = []
    commissioning = diagnostics.get('commissioning_notice')
    if isinstance(commissioning, Mapping):
        notice_rows.append({'_source': 'commissioning_notice', **commissioning})
    imcc_notices = diagnostics.get('imcc_notices')
    imcc_source = 'diagnostics_imcc_notices'
    if not imcc_notices:
        imcc_notices = values.get('imcc_notices')
        imcc_source = 'result_imcc_notices'
    if isinstance(imcc_notices, (list, tuple)):
        notice_rows.extend(
            {'_source': imcc_source, **notice}
            for notice in imcc_notices
            if isinstance(notice, Mapping)
        )
    gas_only = diagnostics.get('fo2_gas_only')
    if isinstance(gas_only, Mapping):
        notice_rows.append({
            '_source': 'fo2_gas_only',
            'kind': 'fo2_gas_only',
            'printed_fO2_log': gas_only.get('printed_fO2_log'),
        })
    warnings = values.get('warnings')
    warnings = warnings if isinstance(warnings, (list, tuple)) else ()
    if notice_rows or warnings:
        if engine_id is None:
            raise ValueError('engine_id is required to cache engine notices')
        notice_codes, notice_values = encode_engine_notice_codes(
            engine_id, notice_rows
        )
        warning_codes, warning_values = encode_engine_warning_codes(
            engine_id, warnings
        )
        metadata['notice_codes'] = notice_codes
        metadata['notice_values'] = notice_values
        if warnings:
            metadata['warning_codes'] = warning_codes
            metadata['warning_values'] = warning_values
    model_id = values.get('imcc_model_id')
    if model_id is not None:
        metadata['imcc_model_id'] = _equilibrium_cache_json(model_id)
    return metadata


def equilibrium_cache_value(
    values: Mapping[str, Any],
    *,
    engine_id: str | None = None,
) -> Any:
    """Split the scorer-consumed equilibrium projection from COLD fields."""
    from simulator.engine_cache import CacheValue

    sources = values.get('vapor_pressures_source')
    compact_sources = []
    if isinstance(sources, Mapping):
        for name, source in sorted(sources.items(), key=lambda item: str(item[0])):
            code = vapor_pressure_source_code(source)
            if code is not None:
                compact_sources.append([str(name), code])

    liquid_fraction = finite_float(values.get('liquid_fraction'))
    phase_available = values.get('phase_assemblage_available', True)
    status = _equilibrium_cache_json(values.get('status'))
    diagnostics = values.get('diagnostics')
    if not isinstance(diagnostics, Mapping):
        diagnostics = {}
    reported_coefficients = values.get('reported_activity_coefficients')
    if not isinstance(reported_coefficients, Mapping) or not reported_coefficients:
        reported_coefficients = diagnostics.get('reported_activity_coefficients')
    hot = [
        status,
        liquid_fraction,
        None if phase_available is True else bool(phase_available),
        _cache_map(values.get('activity_coefficients'), positive=True),
        _cache_map(reported_coefficients, positive=True),
        _activity_detail_pairs(values.get('activity_coefficient_details')),
        _cache_map(values.get('vapor_pressures_Pa'), pressure=True),
        {name: code for name, code in compact_sources},
        _cache_map(values.get('vaporock_full_speciation_Pa'), pressure=True),
        _cache_map(values.get('liquid_composition_wt_pct')),
        _cache_map(values.get('fe_redox_split')),
    ]
    metadata = _equilibrium_cache_consumer_metadata(values, engine_id=engine_id)
    if metadata:
        hot.append(metadata)
    cold: dict[str, Any] = {}
    for name, value in values.items():
        if name not in _EQUILIBRIUM_CACHE_HOT_FIELDS:
            if name == 'diagnostics' and isinstance(value, Mapping):
                remaining = {
                    key: item
                    for key, item in value.items()
                    if key not in _EQUILIBRIUM_CACHE_CONSUMED_DIAGNOSTICS
                    and key not in {
                        'commissioning_notice',
                        'imcc_notices',
                        'fo2_gas_only',
                    }
                }
                if remaining:
                    cold[name] = remaining
            elif name in {'warnings', 'imcc_notices', 'imcc_model_id'}:
                if name not in metadata and not (
                    name == 'warnings' and metadata.get('warning_codes')
                ) and not (
                    name == 'imcc_notices' and metadata.get('notice_codes')
                ):
                    cold[name] = value
            else:
                cold[name] = value
    # Keep the original provenance and detail maps in COLD; HOT carries only
    # authority codes and the exact coefficient basis the scorer reads.
    for name in ('vapor_pressures_source', 'activity_coefficient_details'):
        if name in values:
            cold[name] = values[name]
    return CacheValue(hot, cold)

# More than one oxidation state in silicate melts over the fO2 range these
# engines are asked to run. The melt-backend contract owns this eligibility
# rule so scorers and backend calls use the same component test.
MULTIVALENT_MELT_ELEMENTS: dict[str, str] = {
    'Fe': 'Fe2+/Fe3+',
    'Ti': 'Ti4+/Ti3+',
    'Cr': 'Cr3+/Cr2+',
    'Mn': 'Mn2+/Mn3+',
    'V': 'V3+/V4+/V5+',
    'Eu': 'Eu3+/Eu2+',
    'Ce': 'Ce4+/Ce3+',
    'S': 'S2-/S6+',
    'Ga': 'Ga3+/Ga+',
}


def _elements_of_component(
    name: str,
    oxide_aliases: Mapping[str, str],
) -> frozenset[str]:
    key = str(name).strip()
    if key.endswith('_Liq'):
        key = key[:-4]
    if key.endswith('*'):
        key = key[:-1]
    canonical = oxide_aliases.get(key.lower())
    formula = 'FeO' if canonical == 'FeO_total' else (canonical or key)
    from simulator.reference_data.janaf import formula_composition

    parsed = formula_composition(formula)
    if not parsed:
        return frozenset()
    return frozenset(element for element, _count in parsed)


def is_printed_total_iron_alias(
    name: str,
    oxide_aliases: Mapping[str, str],
) -> bool:
    """True for a printed total-iron label, not the ferrous endmember FeO.

    The alias map sends the total-iron spellings to ``FeO_total``. A
    trailing ``*`` is the same strip ``_elements_of_component`` uses, so
    ``FeO*`` is that label. ``FeO`` itself stays the endmember.
    """

    raw = str(name).strip()
    key = raw[:-1] if raw.endswith("*") else raw
    canonical = oxide_aliases.get(key.lower())
    if canonical == "FeO_total":
        return True
    return raw.endswith("*") and canonical == "FeO"


def _is_multivalent_component(
    name: str,
    oxide_aliases: Mapping[str, str],
) -> bool:
    return any(
        element in MULTIVALENT_MELT_ELEMENTS
        for element in _elements_of_component(name, oxide_aliases)
    )


def multivalent_components(
    composition: Any,
    species: str | None = None,
    *,
    oxide_aliases: Mapping[str, str],
) -> tuple[str, ...]:
    """Return positive multivalent composition components and measured species."""
    if composition is None:
        items = ()
    elif isinstance(composition, Mapping):
        items = composition.items()
    else:
        items = getattr(composition, 'components', ())

    present: list[str] = []
    for name, amount in items:
        try:
            number = Decimal(str(amount))
        except (ArithmeticError, ValueError):
            continue
        if number <= 0:
            continue
        if _is_multivalent_component(str(name), oxide_aliases):
            present.append(str(name))
    if (
        species
        and _is_multivalent_component(species, oxide_aliases)
        and species not in present
    ):
        present.append(species)
    return tuple(sorted(set(present)))


# alphaMELTS writes an oxide into the .melts file only when its wt% is
# strictly above this floor (simulator/melt_backend/alphamelts.py serializer).
# Iron the file does not carry cannot be the FeO/Fe2O3 buffer the MELTS
# family uses to impose fO2, so the couple test and the serializer are one
# threshold. A sum test of FeO+Fe2O3 <= 0.001 would still call
# FeO=0.0006 plus Fe2O3=0.0006 Fe-bearing, then impose fO2 into a binary
# that received neither oxide.
MELTS_CARRIED_COMPONENT_MIN_WT_PCT = 0.001

# Exact sentence the adapters attach when the external fO2 is applied to
# the gas only. Battery wiring copies this text; it does not restate it.
FO2_GAS_ONLY_NOTICE = (
    'the engine did not resolve intrinsic fO2; '
    'the external fO2 was imposed on the gas only.'
)


def imposed_absolute_fo2_without_iron_couple(
    composition_wt_pct: Mapping[str, float],
    *,
    oxide_aliases: Mapping[str, str],
    fO2_log: Optional[float],
    redox_mode: Optional[str],
    redox_buffer: Optional[str],
    fo2_offset: Optional[float],
) -> Optional[tuple[str, tuple[str, ...]]]:
    """Classify an absolute fO2 request that the Fe redox couple cannot carry.

    Premise: a MELTS-family engine imposes absolute fO2 only by moving iron
    between FeO and Fe2O3 (2 FeO + 1/2 O2 = Fe2O3). The ferric fraction is
    undefined when that inventory is absent, which is why ThermoEngine raises
    ``FO2_REQUIRES_IRON`` and alphaMELTS aborts after ``log fo2 path: absolute``.

    Without a redox couple the condensed equilibria have no fO2 degree of
    freedom: the stated bulk fixes the melt. fO2 still enters gas speciation,
    for example NaO0.5(melt) = Na(g) + 1/4 O2, so the printed absolute fO2 is
    a gas coordinate, not a melt constraint.

    Units: wt% on the normalized MELTS bulk, the same basis as the .melts
    file and the ThermoEngine bulk vector. An iron oxide is carried only when
    its wt% is strictly above ``MELTS_CARRIED_COMPONENT_MIN_WT_PCT`` (0.001).
    Fe-free means no iron oxide is carried, so the carried FeO+Fe2O3 sum is 0,
    which is below that floor.

    Sanity: a CMAS melt with both iron oxides at 0 is Fe-free. A basalt with
    about 18 wt% FeO is not. FeO = 0.001 exactly is not carried (the serializer
    uses ``>``) and is Fe-free. FeO = 0.0010001 is Fe-bearing and stays on
    today's imposed-fO2 path. Any other element in ``MULTIVALENT_MELT_ELEMENTS``
    with a positive amount, including one below the emission floor, refuses:
    the engine may still have a redox variable, and dropping it would be a
    silent pass.

    Returns None when today's path must stay in force: Fe is carried, the
    request is not a finite absolute fO2, a named buffer or an fO2 offset is
    set, or redox is ``not_an_input``. Otherwise ``('gas_only', ())`` or
    ``('other_redox', component_names)``.
    """
    if redox_mode == REDOX_MODE_NOT_AN_INPUT:
        return None
    if redox_buffer is not None or fo2_offset is not None:
        return None
    try:
        requested = float(fO2_log) if fO2_log is not None else None
    except (TypeError, ValueError):
        return None
    if requested is None or not math.isfinite(requested):
        return None

    fe_carried = False
    others: list[str] = []
    items = (
        composition_wt_pct.items()
        if isinstance(composition_wt_pct, Mapping)
        else ()
    )
    for name, amount in items:
        try:
            wt_pct = float(amount)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(wt_pct) or wt_pct <= 0.0:
            continue
        elements = _elements_of_component(str(name), oxide_aliases)
        if (
            'Fe' in elements
            and wt_pct > MELTS_CARRIED_COMPONENT_MIN_WT_PCT
        ):
            fe_carried = True
        if any(
            element != 'Fe' and element in MULTIVALENT_MELT_ELEMENTS
            for element in elements
        ):
            others.append(str(name))
    if fe_carried:
        return None
    if others:
        return ('other_redox', tuple(sorted(set(others))))
    return ('gas_only', ())


def fo2_gas_only_diagnostic(printed_fO2_log: float) -> dict[str, Any]:
    """Diagnostics for an external fO2 applied to the gas only."""
    printed = float(printed_fO2_log)
    return {
        'fo2_gas_only': {
            'printed_fO2_log': printed,
            'notice': FO2_GAS_ONLY_NOTICE,
        },
        'fO2_transport': 'external_on_gas_only',
    }


def other_redox_without_iron_couple_refusal(
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: Optional[float],
    components: tuple[str, ...],
) -> 'EquilibriumResult':
    """Typed refusal: imposed fO2, no iron couple, another redox element present."""
    reason = OutOfDomainReason.UNSUPPORTED_INPUT.value
    message = (
        'imposed absolute fO2 has no iron redox couple and other '
        'redox-active components are present: ' + ', '.join(components)
    )
    return EquilibriumResult(
        temperature_C=float(temperature_C),
        pressure_bar=float(pressure_bar),
        fO2_log=None if fO2_log is None else float(fO2_log),
        status='out_of_domain',
        warnings=[message],
        diagnostics={
            'backend_status': 'out_of_domain',
            'backend_status_reason': reason,
            'backend_failure_reason_code': 'redox_active_without_iron_couple',
            'redox_active_components': list(components),
            'authoritative_for_requested_conditions': False,
        },
    )


class RealBackendFamily(str, Enum):
    """Closed typed authority carried by explicit real-engine adapters."""

    ALPHAMELTS = "alphamelts"
    THERMOENGINE = "thermoengine"
    MAGEMIN = "magemin"


class RealBackendAuthority(ABC):
    """Marker for adapters trusted to assert a real-engine family."""

    real_backend_family: ClassVar[RealBackendFamily]


class MeltBackendError(RuntimeError):
    """Base class for melt backend contract violations."""


class LiquidFractionInvalidError(MeltBackendError):
    """Raised when an ok equilibrium result lacks a real liquid fraction."""


class MeltCompositionError(MeltBackendError):
    """Raised when backend phase/composition output is physically unusable."""


_LIQUID_PHASE_NAMES = frozenset({
    'liq', 'liquid', 'LIQUID', 'melt', 'Melt',
})


def liquid_fraction_from_phase_masses(
    phase_masses_kg: Mapping[str, float],
) -> Optional[float]:
    """Return liquid mass / total phase mass, or None when mass is unknown."""
    total_mass_kg = 0.0
    liquid_mass_kg = 0.0
    for phase, mass_kg in phase_masses_kg.items():
        try:
            mass = float(mass_kg)
        except (TypeError, ValueError) as exc:
            raise LiquidFractionInvalidError(
                f'phase_mass_invalid: {phase}={mass_kg!r}'
            ) from exc
        if not math.isfinite(mass) or mass < 0.0:
            raise LiquidFractionInvalidError(
                f'phase_mass_invalid: {phase}={mass_kg!r}'
            )
        if mass == 0.0:
            continue
        phase_name = str(phase)
        total_mass_kg += mass
        if (
            phase_name in _LIQUID_PHASE_NAMES
            or phase_name.lower().startswith('liq')
            or phase_name.endswith('_Liq')
        ):
            liquid_mass_kg += mass
    if total_mass_kg <= 0.0:
        return None
    return liquid_mass_kg / total_mass_kg


def normalize_backend_capabilities(value: Any = None) -> Dict[str, bool]:
    """
    Normalize backend capability config.

    Accepted forms:
    - None: default silicate melt only
    - mapping: {"silicate_melt": true, "gas_volatiles": false}
    - sequence/string: enabled capability names
    """
    capabilities = dict(DEFAULT_BACKEND_CAPABILITIES)
    if value is None:
        return capabilities

    if isinstance(value, str):
        raw_items = [(value, True)]
    elif isinstance(value, Mapping):
        raw_items = value.items()
    elif isinstance(value, (list, tuple, set)):
        raw_items = [(item, True) for item in value]
    else:
        raise ValueError('backend capabilities must be a mapping or list')

    for item in raw_items:
        name, enabled = item
        key = str(name).strip()
        if key not in BACKEND_CAPABILITY_KEYS:
            raise ValueError(f'unknown backend capability: {key}')
        capabilities[key] = bool(enabled)
    return capabilities


def battery_adapter_class(engine_name: str) -> type | None:
    """Adapter class for one battery engine name, or None.

    This is the class that declares ``models_dissolved_sulfur`` and
    ``models_dissolved_chloride``. It does not open the engine.
    """

    if engine_name == "alphamelts":
        from simulator.melt_backend.alphamelts import AlphaMELTSBackend

        return AlphaMELTSBackend
    if engine_name == "thermoengine":
        from simulator.melt_backend.thermoengine import ThermoEngineBackend

        return ThermoEngineBackend
    if engine_name == "magemin":
        from simulator.melt_backend.magemin import MAGEMinBackend

        return MAGEMinBackend
    if engine_name == "vaporock":
        from simulator.melt_backend.vaporock import VapoRockBackend

        return VapoRockBackend
    if engine_name == "internal-analytical":
        return InternalAnalyticalBackend
    return None


# Cleaned silicate melt is the only ledger account the silicate-oxide
# adapters (VapoRock, MAGEMin) may consume; every other account is
# filtered out before the upstream library is called (binding spec §7).
CLEANED_MELT_ACCOUNT = 'process.cleaned_melt'


def split_cleaned_melt_account(
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
) -> tuple[Dict[str, float], List[str]]:
    """
    Extract the cleaned-melt account; report every other account.

    Returns ``(melt_species_mol, dropped_account_names)``.  The
    silicate-oxide adapters only consume ``process.cleaned_melt``; any
    other account that carries positive material is reported back so the
    caller can record a warning (binding spec §7 — these adapters must
    not receive metal / sulfide / salt / halide accounts).
    """
    melt_mol: Dict[str, float] = {}
    for species, mol in (
        composition_mol_by_account.get(CLEANED_MELT_ACCOUNT, {}) or {}
    ).items():
        value = float(mol)
        if value > 0.0:
            melt_mol[str(species)] = melt_mol.get(str(species), 0.0) + value

    dropped: List[str] = []
    for account, species_mol in composition_mol_by_account.items():
        if str(account) == CLEANED_MELT_ACCOUNT:
            continue
        if any(float(mol) > 0.0 for mol in (species_mol or {}).values()):
            dropped.append(str(account))
    return melt_mol, sorted(dropped)


@dataclass(frozen=True)
class MeltOxideProjection:
    oxide_wt_pct: Dict[str, float]
    dropped_mass_kg_by_species: Dict[str, float]
    warnings: tuple[str, ...] = ()

    @property
    def diagnostics(self) -> Dict[str, Any]:
        total = sum(self.dropped_mass_kg_by_species.values())
        if total <= 0.0:
            return {}
        return {
            "dropped_non_basis_melt_mass_kg_by_species": dict(
                self.dropped_mass_kg_by_species
            ),
            "dropped_non_basis_melt_mass_kg": total,
        }


def project_melt_to_oxide_wt_pct(
    *,
    composition_kg: Optional[Dict[str, float]],
    composition_mol: Optional[Dict[str, float]],
    oxide_basis: tuple,
    species_formula_registry: Optional[Mapping[str, Any]] = None,
) -> Dict[str, float]:
    return project_melt_to_oxide_projection(
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        oxide_basis=oxide_basis,
        species_formula_registry=species_formula_registry,
    ).oxide_wt_pct


def project_melt_to_oxide_projection(
    *,
    composition_kg: Optional[Dict[str, float]],
    composition_mol: Optional[Dict[str, float]],
    oxide_basis: tuple,
    species_formula_registry: Optional[Mapping[str, Any]] = None,
) -> MeltOxideProjection:
    """
    Project the simulator's mol/kg melt composition to oxide wt% in the
    given oxide basis.

    Shared by the silicate-oxide adapters (VapoRock, MAGEMin), whose
    upstream bases are identical to MELTS for the oxides shared with the
    simulator, so this is a straight rename + normalisation.  Any species
    not in ``oxide_basis`` is dropped after being recorded in diagnostics.

    ``species_formula_registry`` is the simulator's formula registry
    (threaded through from the layered ABC) used for the mol -> kg
    projection; ``None`` falls back to the builtin formula table.
    """
    from simulator.accounting.formulas import resolve_species_formula

    if composition_mol is not None:
        kg_by_species: Dict[str, float] = {}
        for species, mol in composition_mol.items():
            value = float(mol)
            if value <= 0.0:
                continue
            kg = value * resolve_species_formula(
                species, species_formula_registry).molar_mass_kg_per_mol()
            kg_by_species[species] = kg
    else:
        kg_by_species = {
            species: float(value)
            for species, value in (composition_kg or {}).items()
            if float(value) > 0.0
        }

    filtered = {
        species: kg
        for species, kg in kg_by_species.items()
        if species in oxide_basis
    }

    dropped = {
        str(species): float(kg)
        for species, kg in kg_by_species.items()
        if species not in oxide_basis and float(kg) > 0.0
    }

    total = sum(filtered.values())
    if total <= 0:
        oxide_wt_pct: Dict[str, float] = {}
    else:
        oxide_wt_pct = {
            species: kg / total * 100.0
            for species, kg in filtered.items()
        }

    warnings_out: tuple[str, ...] = ()
    if dropped:
        total_dropped = sum(dropped.values())
        species_text = ", ".join(
            f"{species}={kg:.12g} kg" for species, kg in sorted(dropped.items())
        )
        warnings_out = (
            "dropped_non_basis_melt_mass: "
            f"total={total_dropped:.12g} kg; species={species_text}",
        )

    return MeltOxideProjection(
        oxide_wt_pct=oxide_wt_pct,
        dropped_mass_kg_by_species=dict(sorted(dropped.items())),
        warnings=warnings_out,
    )


def positive_melt_mass_by_species(
    *,
    composition_kg: Optional[Mapping[str, float]],
    composition_mol: Optional[Mapping[str, float]],
    species_formula_registry: Optional[Mapping[str, Any]],
) -> Dict[str, float]:
    """Return positive melt input mass by species for projection diagnostics."""
    if composition_mol is None:
        return {
            str(species): float(value)
            for species, value in (composition_kg or {}).items()
            if float(value) > 0.0
        }

    from simulator.accounting.formulas import resolve_species_formula

    masses: Dict[str, float] = {}
    for species, mol in composition_mol.items():
        value = float(mol)
        if value <= 0.0:
            continue
        mass_kg = value * resolve_species_formula(
            species, species_formula_registry
        ).molar_mass_kg_per_mol()
        masses[str(species)] = masses.get(str(species), 0.0) + mass_kg
    return masses


def projection_diagnostics_for_melt_input(
    *,
    backend: str,
    projection: MeltOxideProjection,
    composition_kg: Optional[Mapping[str, float]],
    composition_mol: Optional[Mapping[str, float]],
    oxide_basis: Sequence[str],
    species_formula_registry: Optional[Mapping[str, Any]],
    dropped_accounts: Sequence[str] = (),
    dropped_account_species: Optional[Mapping[str, Sequence[str]]] = None,
    extra_projection_details: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build the canonical diagnostic payload for projected melt inputs.

    Silicate adapters share the same projection semantics: report dropped
    species/accounts and the exact renormalization from the original melt mass
    to the backend oxide basis. Backend-specific projection metadata can be
    appended through ``extra_projection_details`` without reimplementing the
    common mass accounting.
    """
    diagnostics = dict(projection.diagnostics)
    masses = positive_melt_mass_by_species(
        composition_kg=composition_kg,
        composition_mol=composition_mol,
        species_formula_registry=species_formula_registry,
    )
    basis = set(oxide_basis)
    dropped_species_mass = {
        species: mass
        for species, mass in masses.items()
        if species not in basis and mass > 0.0
    }
    retained_mass = sum(
        mass
        for species, mass in masses.items()
        if species in basis and mass > 0.0
    )
    dropped_mass = sum(dropped_species_mass.values())
    input_mass = retained_mass + dropped_mass
    dropped_account_species = dropped_account_species or {}
    extra_projection_details = extra_projection_details or {}
    if not (
        dropped_species_mass
        or dropped_accounts
        or dropped_account_species
        or extra_projection_details
    ):
        return diagnostics

    details: Dict[str, Any] = {
        'status': 'projected',
        'reason': 'input_composition_projected',
        'backend': backend,
        'projected_species': sorted(str(k) for k in projection.oxide_wt_pct),
    }
    if input_mass > 0.0:
        details['input_melt_mass_kg'] = input_mass
        details['retained_basis_melt_mass_kg'] = retained_mass
    if dropped_species_mass:
        details['dropped_species'] = sorted(dropped_species_mass)
        details['dropped_species_mass_kg'] = dict(
            sorted(dropped_species_mass.items())
        )
        details['dropped_non_basis_melt_mass_kg'] = dropped_mass
    if retained_mass > 0.0 and dropped_mass > 0.0:
        factor = input_mass / retained_mass
        details['renormalization_factor'] = factor
        details['renormalization_delta'] = factor - 1.0
        details['dropped_mass_fraction'] = dropped_mass / input_mass
    if dropped_accounts:
        details['dropped_accounts'] = sorted(
            str(account) for account in dropped_accounts
        )
    if dropped_account_species:
        details['dropped_account_species'] = {
            str(account): list(species)
            for account, species in sorted(dropped_account_species.items())
        }
    details.update(extra_projection_details)
    diagnostics['input_composition_projection'] = details
    return diagnostics


@dataclass
class EquilibriumResult:
    """
    Result of a thermodynamic equilibrium calculation.

    Returned by MeltBackend.equilibrate() with phase assemblage,
    species mol inventories where available, kg projections for external
    reporting, thermodynamic activities, and vapor pressures.
    """
    temperature_C: float = 0.0
    pressure_bar: float = 0.0

    # Phase assemblage
    phases_present: List[str] = field(default_factory=list)
    phase_masses_kg: Dict[str, float] = field(default_factory=dict)
    phase_species_mol: Dict[str, Dict[str, float]] = field(default_factory=dict)
    phase_species_kg: Dict[str, Dict[str, float]] = field(default_factory=dict)
    phase_compositions: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Liquid state
    liquid_fraction: Optional[float] = None
    # False for vapor-only backends (e.g. VapoRock) that never solve a melt
    # phase assemblage; ``__post_init__`` then permits ``liquid_fraction=None``
    # on ``status='ok'``.  Melt-solving backends must leave this True.
    phase_assemblage_available: bool = True
    liquid_composition_wt_pct: Dict[str, float] = field(default_factory=dict)
    # Unknown unless a backend supplies a sourced viscosity value.
    liquid_viscosity_Pa_s: Optional[float] = None

    # Vapor pressures (Pa) for each volatile species
    vapor_pressures_Pa: Dict[str, float] = field(default_factory=dict)

    # Per-species source for ``vapor_pressures_Pa`` values.
    vapor_pressures_source: Dict[str, str] = field(default_factory=dict)

    # Thermodynamic activities in the melt. Field name is legacy.
    activity_coefficients: Dict[str, float] = field(default_factory=dict)

    # Oxygen fugacity
    # ``None`` records an intrinsic request that was rejected before a solve.
    fO2_log: Optional[float] = -9.0  # log10(fO2 / 1 bar)

    # Backend diagnostics
    warnings: List[str] = field(default_factory=list)

    # Optional atom-conserving redistribution emitted by thermodynamic backends.
    # Backends that report phase species after equilibrium must provide this so
    # AtomLedger remains authoritative.
    ledger_transition: Any | None = None

    # Per-call backend outcome.  Unlike engine identity (known from the
    # user's backend selection at web/events.py::_get_backend) and intent
    # authority (an engine x intent lookup against the binding-spec matrix),
    # status is a genuinely per-call runtime signal:
    #
    #   'ok'            - the engine ran and produced a usable result.
    #   'not_converged' - the engine ran but did not converge / produce one.
    #   'out_of_domain' - a DomainGate / account filter rejected the input.
    #   'unavailable'   - the engine/library/binary is absent for this call.
    #
    # Consumers use status as a control signal: non-ok results can be rejected,
    # trigger a fallback, or stop a liquidus/solidus sample. ``__post_init__``
    # validates liquid-fraction payloads only for ok results.
    status: str = 'ok'

    # Optional sulfur-saturation gate (SULFUR_SATURATION_GATE intent) result,
    # attached by ``simulator/core.py::_get_equilibrium`` after a backend
    # equilibration succeeds and Stage 0 sulfide/sulfate inventory is
    # non-zero.  Typed as ``Any`` to avoid an import cycle between this
    # module and ``simulator.melt_backend.sulfsat``; the concrete type is
    # ``simulator.melt_backend.sulfsat.SulfurSaturationResult``.  Backends
    # do not populate this themselves — the SulfSat gate runs *outside*
    # the backend (it is a post-equilibrium gate, not a ``MeltBackend``).
    sulfur_saturation: Any | None = None

    # Optional liquidus temperature (°C) for this composition + pressure.
    # Populated by backends that compute a liquidus alongside the per-T
    # equilibration (e.g., AlphaMELTS subprocess parses one from its
    # findLiq output; ThermoEngine python_api gets one from
    # PetThermoTools.findLiq). Read by the AlphaMELTS provider's diagnostic
    # projection (``engines/alphamelts/parser.py``) and by C6 termination /
    # freeze-margin diagnostics.
    #
    # 0.5.4 W6 (M3 historical-audit closure, 2026-05-28): pre-W6, the
    # AlphaMELTS subprocess path wrote the value ONLY as a warning string
    # (``eq.warnings.append('AlphaMELTS liquidus_C=...')``) which downstream
    # consumers had to regex-parse out. The structured field is the
    # canonical source going forward; the warning is still emitted for
    # legacy log consumers but the field is what kernel + provider read.
    # Field name mirrors ``LiquidusSolidusResult.liquidus_T_C`` in
    # ``simulator/melt_backend/liquidus.py`` — that dataclass remains the
    # canonical surface for the *dedicated* liquidus-finder workflow; this
    # field carries the per-equilibration liquidus only when a backend
    # opportunistically computes one alongside the equilibration request.
    #
    # 0.5.4.1 (2026-05-28 post-push P2 fix): field landed at the END of
    # the dataclass to preserve positional-constructor ABI for external
    # callers. Initial 0.5.4 placement (between ``warnings`` and
    # ``ledger_transition``) silently shifted positional indices for
    # ``ledger_transition``, ``status``, and ``sulfur_saturation``;
    # placing the new field last keeps the historic order intact.
    liquidus_T_C: Optional[float] = None

    # Optional per-call structured backend diagnostics. Kept at the ABI-safe
    # tail for the same positional-constructor reason as ``liquidus_T_C``.
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    # Requested and executed temperatures are distinct at engine boundaries.
    # ``temperature_C`` is the executed temperature; adapters that can report
    # the caller's request separately populate this ABI-safe tail field.
    requested_temperature_C: Optional[float] = None

    # Engine-reported liquid density.  This remains ``None`` when the engine
    # did not print a density; adapters must not synthesize a substitute.
    liquid_density_kg_m3: Optional[float] = None

    # Thermodynamic extras on the engine solver reference mass recorded in
    # ``diagnostics['thermodynamic_basis']``. H is J, S/Cp are J/K, and V is
    # normalized across engines to m3; scaled dVdP/dVdT remain as printed.
    system_enthalpy: Optional[float] = None
    system_entropy: Optional[float] = None
    system_volume: Optional[float] = None
    system_heat_capacity_Cp: Optional[float] = None
    system_dVdP: Optional[float] = None
    system_dVdT: Optional[float] = None
    system_fO2_delta_QFM: Optional[float] = None
    system_solid_density_rhos: Optional[float] = None
    system_phi: Optional[float] = None
    system_chisqr: Optional[float] = None
    phase_thermo: Dict[str, Dict[str, Any]] = field(
        default_factory=dict
    )
    solid_composition_wt_pct: Dict[str, float] = field(default_factory=dict)
    bulk_composition_wt_pct: Dict[str, float] = field(default_factory=dict)

    # Engine-specific diagnostic chemistry. ``None`` means the emitting
    # engine does not provide this facet; cache writers preserve that as SQL
    # NULL rather than padding rows with empty JSON objects.
    chem_potentials: Optional[Dict[str, Dict[str, Any]]] = None
    phase_affinities: Optional[Dict[str, Dict[str, Any]]] = None

    # AlphaMELTS table rows before same-base phases are aggregated. Kept at
    # the ABI-safe tail so legacy positional constructors retain their shape.
    phase_instances: List[Dict[str, Any]] = field(default_factory=list)

    # AlphaMELTS subprocess distinguishes the absolute fO2 serialized into the
    # melts file from System_main_tbl.txt's 3-decimal printed confirmation.
    # Other backends leave these fields unset.
    requested_fO2_log: Optional[float] = None
    printed_fO2_echo_log: Optional[float] = None

    def to_engine_cache_value(self, *, engine_id: str | None = None) -> Any:
        """Split scorer-consumed fields into HOT and the remaining fields into COLD."""
        return equilibrium_cache_value(vars(self), engine_id=engine_id)

    @classmethod
    def from_engine_cache_value(
        cls, payload: Any, *, engine_id: str | None = None
    ) -> EquilibriumResult:
        """Rebuild the battery-visible equilibrium fields from the HOT JSON."""
        from dataclasses import fields

        if isinstance(payload, list):
            payload = equilibrium_cache_from_value(payload, engine_id=engine_id)
        if not isinstance(payload, Mapping):
            raise TypeError('equilibrium cache value must be a mapping or compact array')
        field_names = {item.name for item in fields(cls)}
        result = cls(**{name: value for name, value in payload.items() if name in field_names})
        for name, value in payload.items():
            if name not in field_names:
                setattr(result, name, value)
        return result

    def __post_init__(self) -> None:
        if self.status != 'ok':
            return
        if self.liquid_fraction is None:
            if not self.phase_assemblage_available:
                return
            raise LiquidFractionInvalidError('liquid_fraction_missing')
        try:
            liquid_fraction = float(self.liquid_fraction)
        except (TypeError, ValueError) as exc:
            raise LiquidFractionInvalidError(
                f'liquid_fraction_invalid: {self.liquid_fraction!r}'
            ) from exc
        if liquid_fraction_invalidity(liquid_fraction) is not None:
            raise LiquidFractionInvalidError(
                f'liquid_fraction_invalid: {self.liquid_fraction!r}'
            )
        self.liquid_fraction = liquid_fraction


class MeltBackend(ABC):
    """
    Abstract interface for thermodynamic melt calculations.

    Implementations wrap different thermodynamic engines:
    - AlphaMELTSBackend (silicate phase equilibrium via PetThermoTools
      or subprocess)
    - VapoRockBackend (vapor-melt equilibrium / vapor-side only)
    - MAGEMinBackend (silicate phase equilibrium, shadow second opinion)
    - InternalAnalyticalBackend (no phase equilibrium; core.py owns Antoine fallback)
    """

    backend_name = 'unknown'
    supports_intrinsic_fO2 = False
    # Dissolved S or Cl in the silicate liquid at an imposed gas fugacity.
    # equilibrate must accept sulfur_fugacity_bar or chloride_fugacity_bar
    # (bar, the printed pS2 or pCl2) and return that solute's weight percent
    # on the phase composition. Sulfide-matte and a bulk sulfur column are
    # not this capability. False until an adapter does both.
    models_dissolved_sulfur: ClassVar[bool] = False
    models_dissolved_chloride: ClassVar[bool] = False

    def _not_an_input_redox_refusal(
        self,
        *,
        redox_mode: Optional[str],
        composition_kg: Optional[Mapping[str, float]],
        composition_mol: Optional[Mapping[str, float]],
        composition_mol_by_account: Optional[
            Mapping[str, Mapping[str, float]]
        ],
        temperature_C: float,
        pressure_bar: float,
        fO2_log: Optional[float],
        oxide_aliases: Mapping[str, str],
    ) -> EquilibriumResult | None:
        if redox_mode != REDOX_MODE_NOT_AN_INPUT:
            return None
        if composition_mol_by_account is not None:
            composition = composition_mol_by_account.get(
                CLEANED_MELT_ACCOUNT, {}
            )
        elif composition_mol is not None:
            composition = composition_mol
        else:
            composition = composition_kg
        components = multivalent_components(
            composition,
            oxide_aliases=oxide_aliases,
        )
        if not components:
            return None

        reason = OutOfDomainReason.INVALID_INPUT.value
        message = (
            "redox_mode='not_an_input' requires a composition without "
            "multivalent elements; found " + ", ".join(components)
        )
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            status='out_of_domain',
            warnings=[message],
            diagnostics={
                'backend_status': 'out_of_domain',
                'backend_status_reason': reason,
                'backend_failure_reason_code': reason,
                'redox_mode': redox_mode,
                'multivalent_components': list(components),
                'authoritative_for_requested_conditions': False,
            },
        )

    @abstractmethod
    def initialize(self, config: dict) -> bool:
        """
        Initialize the backend with configuration.

        Returns True if the backend is ready to use.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this backend is installed and functional."""

    @abstractmethod
    def equilibrate(self, temperature_C: float,
                    composition_kg: Optional[Dict[str, float]] = None,
                    fO2_log: Optional[float] = -9.0,
                    pressure_bar: float = 1e-6,
                    *,
                    composition_mol: Optional[Dict[str, float]] = None,
                    composition_mol_by_account: Optional[
                        Mapping[str, Mapping[str, float]]
                    ] = None,
                    species_formula_registry: Optional[Mapping[str, Any]] = None,
                    redox_mode: Optional[str] = None,
                    ) -> EquilibriumResult:
        """
        Calculate thermodynamic equilibrium at given conditions.

        Args:
            temperature_C:   Melt temperature (°C)
            composition_kg:  External kg projection of melt species
            composition_mol: Canonical melt species inventory in mol
            composition_mol_by_account:
                              Canonical per-ledger-account species inventory
            species_formula_registry: Simulator formula registry for kg adapters
            fO2_log:         log10(oxygen fugacity / 1 bar). ``None`` requests
                              intrinsic closed-system redox only from backends
                              declaring ``supports_intrinsic_fO2``. The ABC's
                              finite default preserves legacy callers; an
                              intrinsic-capable implementation may default to
                              ``None`` explicitly.
            redox_mode:     ``'not_an_input'`` distinguishes a scorer omission
                              from an intrinsic closed-system fO2 request. The
                              default preserves the legacy ``fO2_log`` meaning.
            pressure_bar:    Total pressure (bar)

        Returns:
            EquilibriumResult with phases, activities, vapor pressures
        """

    @abstractmethod
    def get_vapor_species(self) -> List[str]:
        """Return list of vapor species this backend can calculate."""

    def get_engine_version(self) -> str:
        """Return the configured engine's stable provenance string."""
        return 'unavailable'

    @property
    def engine_version(self) -> str:
        return self.get_engine_version()

    def health_check(self) -> tuple[bool, str]:
        """Return backend readiness without exposing transport mechanics."""
        available = self.is_available()
        return available, 'ok' if available else 'backend unavailable'

    def capabilities(self) -> Dict[str, bool]:
        """Return chemistry/process coverage exposed by this backend."""
        return dict(DEFAULT_BACKEND_CAPABILITIES)

    def ledger_account_policies(self) -> tuple[Any, ...]:
        """Return backend-required AtomLedger account policies."""
        return ()

    def capability_summary(self) -> str:
        """Human-readable capability status."""
        enabled = [
            key.replace('_', ' ')
            for key, value in self.capabilities().items()
            if value
        ]
        if enabled == ['silicate melt']:
            return 'silicate melt only'
        return ', '.join(enabled) if enabled else 'none'


class InternalAnalyticalBackend(MeltBackend):
    """
    The builtin analytical melt backend (``internal-analytical``).

    Named and serialized as ``internal-analytical``; ``stub`` is a legacy input
    alias. Returns empty
    equilibrium results; the simulator's
    ``_internal_analytical_equilibrium()`` method handles Ellingham/Antoine
    vapor pressures independently of this class. Denylisted from certification
    gates: it never holds ledger authority and never certifies.
    """

    def initialize(self, config: dict) -> bool:
        return True

    def is_available(self) -> bool:
        return False  # Signals core.py to use its internal analytical model.

    def equilibrate(self, temperature_C, composition_kg=None,
                    fO2_log=-9.0, pressure_bar=1e-6, *,
                    composition_mol=None, composition_mol_by_account=None,
                    species_formula_registry=None):
        # The internal analytical backend wraps no external engine;
        # is_available() is False and
        # core.py runs its own Ellingham/Antoine path instead.
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            status='unavailable',
            liquid_fraction=None,
            phase_assemblage_available=False,
        )

    def get_vapor_species(self):
        return ['Na', 'K', 'Fe', 'Mg', 'Ca', 'SiO']
