"""Project-owned engine commissioning bands.

Loaded once from ``data/engine_commissioning.yaml``. Certified SiO2 and
temperature windows are predict-and-flag (notice + authority=extrapolated
+ certified_band). ``observed_crash_floor_wt_pct`` is evidence metadata,
not a pre-run refusal: AlphaMELTS records the measured 34 wt% SIGABRT
floor; ThermoEngine is ``not_applicable``. The only runtime use is
annotating an actual AlphaMELTS subprocess death below that floor.
Adapters must not re-hardcode a second copy of these numbers.

FALLBACK (loud): the published adapter/domain constants this table replaced,
used only if a caller asks for the documented historical values. Runtime
loads the YAML; a missing or invalid table fails loud rather than inventing
a second band.

    SiO2 certified [30, 80] wt%  <- domain.py DEFAULT_SIO2_MIN/MAX_WT_PCT
    SiO2 observed_crash_floor_wt_pct 34  <- domain.py _SIO2_CRASH_FLOOR_WT_PCT
    T certified [1073.15, 1700] K
        <- alphamelts.py ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C = 800 C
           and melt_envelope.py T_calib_max_K = 1700 K
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from engines.domain_reason import OutOfDomainReason

# Historical published constants this table replaced. Cited by tests that
# pin defaults; not a second runtime band.
PUBLISHED_SIO2_CERTIFIED_WT_PCT: tuple[float, float] = (30.0, 80.0)
PUBLISHED_SIO2_CRASH_FLOOR_WT_PCT: float = 34.0
PUBLISHED_TEMPERATURE_CERTIFIED_K: tuple[float, float] = (1073.15, 1700.0)
PUBLISHED_AUTHORITY_OUTSIDE: str = 'extrapolated'

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMMISSIONING_PATH = _REPO_ROOT / 'data' / 'engine_commissioning.yaml'

_REQUIRED_ENGINES: frozenset[str] = frozenset({'alphamelts', 'thermoengine'})
_OPTIONAL_ENGINES: frozenset[str] = frozenset(
    {'vaporock', 'magemin', 'imcc_sf04', 'imcc_sf04_ext'}
)
_ALLOWED_ENGINES: frozenset[str] = _REQUIRED_ENGINES | _OPTIONAL_ENGINES

_TOP_KEYS: frozenset[str] = frozenset({'schema_version', 'engines'})
_ENGINE_KEYS: frozenset[str] = frozenset(
    {'sio2_wt_pct', 'temperature_K', 'authority_outside', 'source'}
)
_SIO2_KEYS: frozenset[str] = frozenset(
    {'certified', 'observed_crash_floor_wt_pct'}
)
_NOT_APPLICABLE = 'not_applicable'
_TEMPERATURE_KEYS: frozenset[str] = frozenset({'certified'})
_SOURCE_KEYS: frozenset[str] = frozenset({'kind', 'ref', 'note'})
_SOURCE_KINDS: frozenset[str] = frozenset(
    {'adapter_default', 'battery_qualification'}
)
_AUTHORITIES: frozenset[str] = frozenset({'certified', 'bridge', 'extrapolated'})

CONSTRAINT_SILICATE_NETWORK_BAND = 'silicate_network_band'
CONSTRAINT_TEMPERATURE_RANGE = 'temperature_range'

COMMISSIONING_NOTICE_KIND = 'engine_commissioning'


class EngineCommissioningError(ValueError):
    """Invalid commissioning table (closed keys, types, or bounds)."""


@dataclass(frozen=True)
class CertifiedInterval:
    minimum: float
    maximum: float

    def as_tuple(self) -> tuple[float, float]:
        return (self.minimum, self.maximum)

    def contains(self, value: float) -> bool:
        return self.minimum <= float(value) <= self.maximum


@dataclass(frozen=True)
class SiO2Commissioning:
    certified: CertifiedInterval
    observed_crash_floor_wt_pct: float | None


@dataclass(frozen=True)
class TemperatureCommissioning:
    certified: CertifiedInterval


@dataclass(frozen=True)
class CommissioningSource:
    kind: str
    ref: str
    note: str


@dataclass(frozen=True)
class EngineCommissioning:
    name: str
    sio2_wt_pct: SiO2Commissioning
    temperature_K: TemperatureCommissioning
    authority_outside: str
    source: CommissioningSource

    def certified_band(self) -> dict[str, Any]:
        return {
            'sio2_wt_pct': list(self.sio2_wt_pct.certified.as_tuple()),
            'temperature_K': list(self.temperature_K.certified.as_tuple()),
        }


@dataclass(frozen=True)
class CommissioningTable:
    schema_version: int
    engines: dict[str, EngineCommissioning]
    path: Path


@dataclass(frozen=True)
class CommissioningAssessment:
    engine: str
    sio2_wt_pct: float
    temperature_K: float
    below_crash_floor: bool
    outside_certified: bool
    failed_constraints: tuple[str, ...]
    notice: dict[str, Any] | None
    spec: EngineCommissioning


def _closed_mapping(
    payload: object,
    allowed: frozenset[str],
    *,
    context: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise EngineCommissioningError(f'{context} must be a mapping')
    keys = {str(key) for key in payload}
    unknown = keys - allowed
    if unknown:
        raise EngineCommissioningError(
            f'{context} has unknown key(s) {sorted(unknown)}; '
            f'allowed keys are {sorted(allowed)}'
        )
    return {str(key): value for key, value in payload.items()}


def _finite_float(value: object, *, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EngineCommissioningError(
            f'{context} must be a finite float, got {value!r}'
        ) from exc
    if number != number or number in (float('inf'), float('-inf')):
        raise EngineCommissioningError(
            f'{context} must be a finite float, got {value!r}'
        )
    return number


def _certified_interval(value: object, *, context: str) -> CertifiedInterval:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise EngineCommissioningError(
            f'{context} must be a [min, max] pair, got {value!r}'
        )
    minimum = _finite_float(value[0], context=f'{context}[0]')
    maximum = _finite_float(value[1], context=f'{context}[1]')
    if minimum > maximum:
        raise EngineCommissioningError(
            f'{context} is inverted: min {minimum} > max {maximum}'
        )
    return CertifiedInterval(minimum=minimum, maximum=maximum)


def _parse_engine(name: str, payload: object) -> EngineCommissioning:
    if name not in _ALLOWED_ENGINES:
        raise EngineCommissioningError(
            f'unknown engine {name!r}; allowed engines are '
            f'{sorted(_ALLOWED_ENGINES)}'
        )
    body = _closed_mapping(payload, _ENGINE_KEYS, context=f'engines.{name}')
    missing = _ENGINE_KEYS - set(body)
    if missing:
        raise EngineCommissioningError(
            f'engines.{name} missing required key(s) {sorted(missing)}'
        )

    sio2_body = _closed_mapping(
        body['sio2_wt_pct'],
        _SIO2_KEYS,
        context=f'engines.{name}.sio2_wt_pct',
    )
    if 'certified' not in sio2_body:
        raise EngineCommissioningError(
            f'engines.{name}.sio2_wt_pct missing certified band'
        )
    certified_sio2 = _certified_interval(
        sio2_body['certified'],
        context=f'engines.{name}.sio2_wt_pct.certified',
    )
    if 'observed_crash_floor_wt_pct' not in sio2_body:
        raise EngineCommissioningError(
            f'engines.{name}.sio2_wt_pct missing observed_crash_floor_wt_pct'
        )
    raw_floor = sio2_body['observed_crash_floor_wt_pct']
    observed_crash_floor: float | None
    if raw_floor == _NOT_APPLICABLE:
        observed_crash_floor = None
    else:
        observed_crash_floor = _finite_float(
            raw_floor,
            context=f'engines.{name}.sio2_wt_pct.observed_crash_floor_wt_pct',
        )

    temperature_body = _closed_mapping(
        body['temperature_K'],
        _TEMPERATURE_KEYS,
        context=f'engines.{name}.temperature_K',
    )
    if 'certified' not in temperature_body:
        raise EngineCommissioningError(
            f'engines.{name}.temperature_K missing certified band'
        )
    certified_t = _certified_interval(
        temperature_body['certified'],
        context=f'engines.{name}.temperature_K.certified',
    )

    authority = str(body['authority_outside']).strip()
    if authority not in _AUTHORITIES:
        raise EngineCommissioningError(
            f'engines.{name}.authority_outside {authority!r} is not one of '
            f'{sorted(_AUTHORITIES)}'
        )

    source_body = _closed_mapping(
        body['source'],
        _SOURCE_KEYS,
        context=f'engines.{name}.source',
    )
    missing_source = _SOURCE_KEYS - set(source_body)
    if missing_source:
        raise EngineCommissioningError(
            f'engines.{name}.source missing key(s) {sorted(missing_source)}'
        )
    kind = str(source_body['kind']).strip()
    if kind not in _SOURCE_KINDS:
        raise EngineCommissioningError(
            f'engines.{name}.source.kind {kind!r} is not one of '
            f'{sorted(_SOURCE_KINDS)}'
        )

    return EngineCommissioning(
        name=name,
        sio2_wt_pct=SiO2Commissioning(
            certified=certified_sio2,
            observed_crash_floor_wt_pct=observed_crash_floor,
        ),
        temperature_K=TemperatureCommissioning(certified=certified_t),
        authority_outside=authority,
        source=CommissioningSource(
            kind=kind,
            ref=str(source_body['ref']),
            note=str(source_body['note']).strip(),
        ),
    )


def _parse_table(payload: object, *, path: Path) -> CommissioningTable:
    body = _closed_mapping(payload, _TOP_KEYS, context='engine_commissioning')
    if 'engines' not in body:
        raise EngineCommissioningError(
            'engine_commissioning missing engines mapping'
        )
    schema_version = body.get('schema_version', 1)
    try:
        schema_int = int(schema_version)
    except (TypeError, ValueError) as exc:
        raise EngineCommissioningError(
            f'schema_version must be an int, got {schema_version!r}'
        ) from exc
    if schema_int != 1:
        raise EngineCommissioningError(
            f'unsupported engine_commissioning schema_version {schema_int}'
        )
    engines_payload = body['engines']
    if not isinstance(engines_payload, Mapping) or not engines_payload:
        raise EngineCommissioningError(
            'engines must be a non-empty mapping'
        )
    engines: dict[str, EngineCommissioning] = {}
    for raw_name, raw_spec in engines_payload.items():
        name = str(raw_name)
        engines[name] = _parse_engine(name, raw_spec)
    missing_required = _REQUIRED_ENGINES - set(engines)
    if missing_required:
        raise EngineCommissioningError(
            'engine_commissioning missing required engine(s) '
            f'{sorted(missing_required)}'
        )
    return CommissioningTable(
        schema_version=schema_int,
        engines=engines,
        path=path,
    )


@lru_cache(maxsize=8)
def load_engine_commissioning(
    path: str | Path | None = None,
) -> CommissioningTable:
    """Load and validate the commissioning table (cached per path)."""

    table_path = (
        DEFAULT_COMMISSIONING_PATH if path is None else Path(path)
    )
    if not table_path.is_file():
        raise EngineCommissioningError(
            f'engine commissioning table not found: {table_path}'
        )
    try:
        payload = yaml.safe_load(table_path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        raise EngineCommissioningError(
            f'engine commissioning table is not valid YAML: {table_path}'
        ) from exc
    return _parse_table(payload, path=table_path)


def engine_commissioning(
    name: str,
    *,
    path: str | Path | None = None,
) -> EngineCommissioning:
    table = load_engine_commissioning(path)
    try:
        return table.engines[str(name)]
    except KeyError as exc:
        raise EngineCommissioningError(
            f'no commissioning row for engine {name!r}'
        ) from exc


def assess_engine_commissioning(
    name: str,
    *,
    sio2_wt_pct: float,
    temperature_K: float,
    path: str | Path | None = None,
) -> CommissioningAssessment:
    """Classify a (SiO2, T) point against the project-owned table.

    ``below_crash_floor`` is evidence metadata (observed AlphaMELTS
    SIGABRT floor). It is not a pre-run refusal: the engine still runs.
    """

    spec = engine_commissioning(name, path=path)
    sio2 = _finite_float(sio2_wt_pct, context='sio2_wt_pct')
    temperature = _finite_float(temperature_K, context='temperature_K')
    crash_floor = spec.sio2_wt_pct.observed_crash_floor_wt_pct
    below_crash_floor = crash_floor is not None and sio2 < crash_floor
    failed: list[str] = []
    warnings: list[str] = []
    if not spec.sio2_wt_pct.certified.contains(sio2):
        failed.append(CONSTRAINT_SILICATE_NETWORK_BAND)
        lo, hi = spec.sio2_wt_pct.certified.as_tuple()
        warnings.append(
            f'SiO2 {sio2:.3f} wt% outside certified band [{lo:g}, {hi:g}] wt%'
        )
    if not spec.temperature_K.certified.contains(temperature):
        failed.append(CONSTRAINT_TEMPERATURE_RANGE)
        lo, hi = spec.temperature_K.certified.as_tuple()
        warnings.append(
            f'temperature {temperature:g} K outside certified band '
            f'[{lo:g}, {hi:g}] K'
        )
    outside_certified = bool(failed)
    notice: dict[str, Any] | None = None
    if outside_certified:
        reason = (
            OutOfDomainReason.SILICATE_WINDOW.value
            if CONSTRAINT_SILICATE_NETWORK_BAND in failed
            else OutOfDomainReason.TEMPERATURE_RANGE.value
        )
        notice = {
            'kind': COMMISSIONING_NOTICE_KIND,
            'reason': reason,
            'authority': spec.authority_outside,
            'certified_band': spec.certified_band(),
            'failed_constraints': tuple(failed),
            'warnings': tuple(warnings),
            'sio2_wt_pct': sio2,
            'temperature_K': temperature,
        }
    return CommissioningAssessment(
        engine=spec.name,
        sio2_wt_pct=sio2,
        temperature_K=temperature,
        below_crash_floor=below_crash_floor,
        outside_certified=outside_certified,
        failed_constraints=tuple(failed),
        notice=notice,
        spec=spec,
    )


def clear_engine_commissioning_cache() -> None:
    load_engine_commissioning.cache_clear()


__all__ = (
    'COMMISSIONING_NOTICE_KIND',
    'CONSTRAINT_SILICATE_NETWORK_BAND',
    'CONSTRAINT_TEMPERATURE_RANGE',
    'CertifiedInterval',
    'CommissioningAssessment',
    'CommissioningSource',
    'CommissioningTable',
    'DEFAULT_COMMISSIONING_PATH',
    'EngineCommissioning',
    'EngineCommissioningError',
    'PUBLISHED_AUTHORITY_OUTSIDE',
    'PUBLISHED_SIO2_CERTIFIED_WT_PCT',
    'PUBLISHED_SIO2_CRASH_FLOOR_WT_PCT',
    'PUBLISHED_TEMPERATURE_CERTIFIED_K',
    'SiO2Commissioning',
    'TemperatureCommissioning',
    'assess_engine_commissioning',
    'clear_engine_commissioning_cache',
    'engine_commissioning',
    'load_engine_commissioning',
)
