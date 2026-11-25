"""One process-local HOT SQLite cache for engine outputs.

The cache exists to speed up CI, gates, the accuracy scorecard, user runs,
and the optimizer. Engines are stateless and deterministic: the same engine,
chemistry version, and request always give the same prediction, so a cached
value is the engine's answer. CI uses the cache read-only by default. The
key is engine + version + request; do not add to it. Read and write never
delete a row. A species-local chemistry change (e.g. a re-pin that adds or
fixes some species) does NOT bump the version: the superseded species' rows
are expired and re-warmed (expire --species, t-1915). Species expiry and
version prune are manual tools in scripts/engine_cache_sync.py. Bump the
chemistry version only for a change that moves every output of the engine.
The cache accumulates; never expire rows that do not need it.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.metadata
import inspect
import json
import logging
import math
import struct
import os
import sqlite3
import threading
import time
import zlib
from unittest.mock import Mock
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote


@dataclass(frozen=True)
class EngineCacheValue:
    hot: object
    cold: Mapping = field(default_factory=dict)
    cacheable: bool = True
    # Ephemeral refusal metadata: the stored HOT value keeps its existing
    # projection and wire shape. These fields only let the cache apply its
    # refusal allowlist before encoding that projection.
    refusal_reason: str | None = None
    refusal_status: str | None = None


@dataclass(frozen=True)
class Provenance:
    value: object


@dataclass(frozen=True)
class _Reference:
    index: int


_KEY_FIELDS = (
    "composition_wt_pct", "composition_mol", "composition_kg", "composition",
    "temperature_K", "temperature_C", "physical_pressure_bar", "pressure_bar",
    "fO2_log", "po2", "kwargs", "engine_settings",
)
_KEY_FIELD_ORDER = {value: index for index, value in enumerate(_KEY_FIELDS)}

# Wire tokens only: oxide/species names remain inline. Append, never reorder.
_VALUE_TOKENS = (
    "ok", "non_authoritative", "single_cation", "raoultian_pure_endmember", "l",
    "liquid", "solid", "speciation", "fallback", "floor", "raoultian",
    "oxide_mole_fraction", "diagnostics", "authority", "extrapolated", "certified",
    "authoritative_for_requested_vapor_pressure", "vapor_pressure_backend_status",
    "vapor_pressure_backend_status_reason", "backend_status_reason", "position",
    "notice_codes", "notice_values", "warning_codes", "warning_values", "text",
    "kind", "code", "reason", "origin", "engine", "codes", "values",
    "certified_band", "temperature_K", "sio2_wt_pct", "temperature_range",
    "commissioning_notice", "imcc_notices", "fo2_gas_only", "_source",
    "diagnostics_imcc_notices", "result_imcc_notices", "printed_fO2_log",
    "coefficient_basis", "standard_state", "convention", "phase", "component_basis",
    "value", "basis", "input_composition_projection", "failed_constraints",
    "warnings", "silicate_network_band", "engine_commissioning", "species",
    "mole_fraction", "mole_fraction_single_cation", "activity", "parent_oxide",
    "reported_activity_coefficients", "dropped_components", "dropped_mass_fraction",
    "dropped_total_wt_pct", "dropped_component_wt_pct", "source_sum_wt_pct",
    "verdict", "projection_verdict", "within_threshold", "projected_bulk",
    "retained_total_wt_pct", "invalid_reason", "dropped_component_mol_per_kg",
    "dropped_component_mol_unavailable_reasons", "thresholded_dropped_total_wt_pct",
    "thresholded_dropped_components", "relaxed_dropped_components",
    "relaxed_dropped_component_wt_pct", "method", "model", "source",
    "backend_failure_reason_code", "backend_failure_category", "engine_reason",
    "backend_failure_disposition", "subprocess_failure", "returncode",
    "empty_speciation_cause", "engine_model_caveat", "flag", "status",
)
_VALUE_TOKEN_NUMBERS = {value: index + 128 for index, value in enumerate(_VALUE_TOKENS)}


def _unsigned(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


@lru_cache(maxsize=8192)
def _string_bytes(value: str, key: bool) -> bytes:
    if key and value in _KEY_FIELD_ORDER:
        return bytes((128 + _KEY_FIELD_ORDER[value],))
    if not key and value in _VALUE_TOKEN_NUMBERS:
        return bytes((_VALUE_TOKEN_NUMBERS[value],))
    data = value.encode("utf-8")
    return b"\x05" + _unsigned(len(data)) + data


@lru_cache(maxsize=65536)
def _key_float_bytes(value: float, negative: bool) -> bytes:
    mantissa, exponent = math.frexp(abs(value))
    if value == 0.0:
        exponent = -1074
    fixed = int(mantissa * (1 << 53))
    body = struct.pack(">HQ", exponent + 1074, fixed)
    return b"\x09" + bytes((0 if negative else 1,)) + (
        bytes(byte ^ 255 for byte in body) if negative else body
    )


def pack(value, *, key: bool = False) -> bytes:
    if isinstance(value, dict):
        if any(not isinstance(name, str) for name in value):
            raise TypeError("cache map names must be strings")
        names = sorted(value, key=lambda name: (
            _KEY_FIELD_ORDER.get(name, len(_KEY_FIELDS)) if key else len(_KEY_FIELDS), name,
        ))
        return b"\x08" + _unsigned(len(names)) + b"".join(
            pack(name, key=key) + pack(value[name], key=key) for name in names
        )
    if isinstance(value, _Reference):
        return b"\x0a" + _unsigned(value.index)
    if value is None:
        return b"\x00"
    if value is False:
        return b"\x01"
    if value is True:
        return b"\x02"
    if isinstance(value, int):
        return b"\x03" + _unsigned(2 * value if value >= 0 else -2 * value - 1)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cache numbers must be finite")
        if not key:
            if value == 0.0:
                return b"\x0c" if math.copysign(1.0, value) < 0 else b"\x0b"
            if value.is_integer() and abs(value) < (1 << 21):
                integer = int(value)
                return b"\x0d" + _unsigned(2 * integer if integer >= 0 else -2 * integer - 1)
            return b"\x04" + struct.pack(">d", value)
        return _key_float_bytes(value, math.copysign(1.0, value) < 0)
    if isinstance(value, str):
        return _string_bytes(value, key)
    if isinstance(value, (tuple, list)):
        tag = b"\x07" if isinstance(value, tuple) else b"\x06"
        return tag + _unsigned(len(value)) + b"".join(pack(item, key=key) for item in value)
    if isinstance(value, Mapping):
        return pack(dict(value), key=key)
    raise TypeError(f"unsupported cache shape: {type(value).__name__}")


def unpack(data: bytes, *, key: bool = False):
    position = 0

    def take(size):
        nonlocal position
        result = data[position:position + size]
        if len(result) != size:
            raise ValueError("truncated cache value")
        position += size
        return result

    def unsigned():
        result = shift = 0
        while True:
            byte = take(1)[0]
            result |= (byte & 127) << shift
            if byte < 128:
                return result
            shift += 7

    def read_key_float():
        # Inverse of _key_float_bytes. Sign 0 is the negated, bit-inverted body.
        sign = take(1)[0]
        raw = take(10)
        if sign == 0:
            raw = bytes(byte ^ 255 for byte in raw)
        elif sign != 1:
            raise ValueError(f"invalid key float sign {sign}")
        exponent, fixed = struct.unpack(">HQ", raw)
        exponent -= 1074
        if fixed == 0 and exponent == -1074:
            value = 0.0
        else:
            value = math.ldexp(fixed / (1 << 53), exponent)
        return -value if sign == 0 else value

    def read():
        tag = take(1)[0]
        if tag == 0:
            return None
        if tag in (1, 2):
            return tag == 2
        if tag == 3:
            integer = unsigned()
            return -(integer // 2) - 1 if integer & 1 else integer // 2
        if tag == 4:
            return struct.unpack(">d", take(8))[0]
        if tag == 5:
            return take(unsigned()).decode("utf-8")
        if tag in (6, 7):
            items = [read() for _ in range(unsigned())]
            return tuple(items) if tag == 7 else items
        if tag == 8:
            return {read(): read() for _ in range(unsigned())}
        if tag == 9:
            if not key:
                raise ValueError("invalid cache value tag 9")
            return read_key_float()
        if tag == 10:
            return _Reference(unsigned())
        if tag == 11:
            return 0.0
        if tag == 12:
            return -0.0
        if tag == 13:
            integer = unsigned()
            return float(-(integer // 2) - 1 if integer & 1 else integer // 2)
        if key and 128 <= tag < 128 + len(_KEY_FIELDS):
            return _KEY_FIELDS[tag - 128]
        if not key and 128 <= tag < 128 + len(_VALUE_TOKENS):
            return _VALUE_TOKENS[tag - 128]
        raise ValueError(f"invalid cache value tag {tag}")

    result = read()
    if position != len(data):
        raise ValueError("trailing cache bytes")
    return result


_PROVENANCE_FIELDS = frozenset({
    "identity", "pack_model_id", "pack_version", "pack_digest",
    "openimcc_version", "redox_pack_digest", "imcc_model_id", "model_id",
    "coefficient_sources", "message", "text", "warnings",
})


def _map_values(value, transform):
    if isinstance(value, Mapping):
        return {name: transform(item, name) for name, item in value.items()}
    if isinstance(value, (list, tuple)):
        items = [transform(item, None) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def live_hot(value):
    """Unwrap explicitly interned metadata even when the store is disabled."""
    if isinstance(value, EngineCacheValue):
        value = value.hot
    if isinstance(value, Provenance):
        return value.value
    return _map_values(value, lambda item, _name: live_hot(item))


def pack_hot(value):
    records = {}

    def separate(item, name=None):
        if isinstance(item, Provenance):
            content = pack(item.value)
        elif name in _PROVENANCE_FIELDS or (
            isinstance(item, str) and (len(item.encode("utf-8")) > 64 or " " in item)
        ):
            content = pack(item)
        else:
            return _map_values(item, separate)
        records[content] = item.value if isinstance(item, Provenance) else item
        return Provenance(content)

    separated = separate(value)
    ordered = sorted(records)
    positions = {content: index for index, content in enumerate(ordered)}

    def references(item, _name=None):
        if isinstance(item, Provenance):
            return _Reference(positions[item.value])
        return _map_values(item, references)

    return pack(references(separated)), pack(tuple(records[content] for content in ordered)) if ordered else b""


def unpack_hot(data, provenance=b""):
    records = unpack(provenance) if provenance else ()

    def restore(item, _name=None):
        if isinstance(item, _Reference):
            try:
                return records[item.index]
            except IndexError as exc:
                raise ValueError("missing cache provenance") from exc
        return _map_values(item, restore)

    return restore(unpack(data))


REFUSAL_OUT_OF_BASIS = "out_of_basis"
REFUSAL_COMPOSITION_PROJECTED = "composition_projected"
REFUSAL_MAJOR_SUM = "sum_below_95_wt_pct"
REFUSAL_NO_LIQUID = "no_liquid"
REFUSAL_TIMEOUT = "engine_timeout"
REFUSAL_ENGINE_CRASH = "engine_crash"
REFUSAL_GATE_REFUSED_IN_ADAPTER = "gate_refused_in_adapter"
REFUSAL_UNAVAILABLE = "unavailable"
REFUSAL_VALUE_IS_FLOOR = "value_is_floor"
# engine_cache is a standard-library leaf (tests/test_import_boundary.py::
# test_engine_cache_is_a_standard_library_leaf), so it cannot import
# engines.domain_reason. These literals mirror the enum's values;
# tests/test_engine_cache_refusal_codes.py pins each one to it.
REFUSAL_FORBIDDEN_SPECIES = "forbidden_species"
REFUSAL_LIQUID_STATE = "liquid_state"
REFUSAL_INVALID_PRESSURE_BAR = "invalid_pressure_bar"
REFUSAL_INVALID_FO2_LOG = "invalid_fo2_log"
REFUSAL_SUM_PRESSURE_SANITY = "sum_pressure_sanity"
REFUSAL_SPECIATION_NO_FINITE_VALUES = "speciation_no_finite_values"
# Distinct from REFUSAL_MAJOR_SUM ("sum_below_95_wt_pct"). This is
# OutOfDomainReason.MAJOR_SUM.
REFUSAL_DOMAIN_MAJOR_SUM = "major_sum"
REFUSAL_SILICATE_WINDOW = "silicate_window"
REFUSAL_INVALID_INPUT = "invalid_input"
REFUSAL_UNSUPPORTED_INPUT = "unsupported_input"
REFUSAL_CLAMPED_OPERATING_POINT = "clamped_operating_point"
# Probe 2026-11-20, three fresh processes. Sample sizes are the evidence.
# s2: 12 requests, 12/12 identical, 12 stayed this reason.
REFUSAL_MAGEMIN_MODE_VECTOR_MASS_DEFICIT = "magemin_mode_vector_mass_deficit"
# s3: 2 requests, 2/2 identical, 2 stayed this reason.
REFUSAL_NO_CONVERGENCE = "no_convergence"
# s3: 11 requests, 11/11 identical, 11 stayed this reason.
REFUSAL_FO2_NONMONOTONIC_BUFFERED = "fo2_nonmonotonic_buffered"
# s3 cell label engine_crash: 11 requests, 11/11 identical, 11 stayed
# engine_crash. The classifier collapses subprocess_died into that cell
# token. Stored only when the signal is the binary's own fault
# (SIGABRT, SIGSEGV, SIGBUS). A timeout, SIGKILL, SIGPROF, a worker
# death, and a missing runtime carry other labels and are not this code.
REFUSAL_SUBPROCESS_DIED = "subprocess_died"

# Wire tokens owned by alphamelts.py. Repeated here so this module does not
# import the adapter. The allowlist pin binds each token to that constant.
# These three are composition predicates evaluated before the binary starts,
# so not_converged here is the guard's status, not a solver outcome.
_PRELAUNCH_CRASH_REFUSAL_CODES = frozenset({
    "subprocess_fe_free_absolute_fo2_crash",
    "subprocess_cao_sio2_binary_crash",
    "subprocess_fe_free_silicate_imposed_fo2_crash",
})

CACHEABLE_REFUSAL_CODES = frozenset({
    REFUSAL_OUT_OF_BASIS, REFUSAL_COMPOSITION_PROJECTED, REFUSAL_MAJOR_SUM,
    REFUSAL_NO_LIQUID,
    REFUSAL_FORBIDDEN_SPECIES,
    REFUSAL_LIQUID_STATE,
    REFUSAL_INVALID_PRESSURE_BAR,
    REFUSAL_INVALID_FO2_LOG,
    REFUSAL_SUM_PRESSURE_SANITY,
    REFUSAL_SPECIATION_NO_FINITE_VALUES,
    REFUSAL_SILICATE_WINDOW,
    REFUSAL_DOMAIN_MAJOR_SUM,
    REFUSAL_INVALID_INPUT,
    REFUSAL_UNSUPPORTED_INPUT,
    REFUSAL_CLAMPED_OPERATING_POINT,
    REFUSAL_MAGEMIN_MODE_VECTOR_MASS_DEFICIT,
    REFUSAL_NO_CONVERGENCE,
    REFUSAL_FO2_NONMONOTONIC_BUFFERED,
    REFUSAL_SUBPROCESS_DIED,
    *_PRELAUNCH_CRASH_REFUSAL_CODES,
    # Raised before any kernel or package call. Repeated from the raise
    # sites; the real-refusal tests bind them. Oxygen-balance failure is
    # a solver outcome and stays off this set.
    "internal_analytical_missing_composition",
    "internal_analytical_invalid_temperature",
    "internal_analytical_invalid_total_pressure",
    "internal_analytical_invalid_composition",
    "internal_analytical_missing_oxygen_condition",
    "internal_analytical_invalid_oxygen_condition",
    "openimcc_composition_canonicalization",
    # OpenIMCC input refusals. Each is a predicate of the request
    # (composition, temperature, oxygen fugacity, pack, and the two allow
    # flags) and the version-pinned datapack. Repeated here so this module
    # does not import openimcc; the importorskip pin binds each token to
    # the package class. Nonconvergence, gas oxygen-balance failure, and
    # datapack-load errors stay off this set. imcc_species_not_found is
    # the parent-name lookup on the solved parent list, still fixed by
    # that request. imcc_gas_invalid_fO2 is checked on the fugacity
    # argument before any gas table is read. imcc_gas_species_not_found
    # and imcc_gas_T_outside_domain depend on the gas tables
    # OPENIMCC_VAPOROCK_ROOT can swap, and that root is not in the key,
    # so they stay off this set.
    "imcc_composition_incomplete",
    "imcc_ferric_input_unsupported",
    "imcc_component_outside_domain",
    "imcc_species_not_found",
    "imcc_sp_extension_required",
    "imcc_composition_outside_validated_envelope",
    "imcc_T_outside_datapack_domain",
    "imcc_gas_invalid_fO2",
})

_NOT_CONVERGED_CACHEABLE_REASONS = frozenset({
    REFUSAL_SPECIATION_NO_FINITE_VALUES,
    *_PRELAUNCH_CRASH_REFUSAL_CODES,
})


def _cacheable_refusal_status(
    refusal_reason: str | None, status: str | None,
) -> bool:
    if status in {
        None, "out_of_domain", "input_refusal", "refusal", "no_liquid", "ok",
    }:
        return True
    # process_failure is intentionally absent: a returncode, signal, or
    # timeout on the result stays unstored even when the reason is listed.
    return (
        refusal_reason in _NOT_CONVERGED_CACHEABLE_REASONS
        and status == "not_converged"
    )


def cacheable_refusal(result) -> bool:
    if isinstance(result, EngineCacheValue):
        return (
            result.cacheable
            and result.refusal_reason in CACHEABLE_REFUSAL_CODES
            and _cacheable_refusal_status(
                result.refusal_reason, result.refusal_status,
            )
        )
    return (
        isinstance(result, Mapping)
        and result.get("status") == "refusal"
        and result.get("refusal_reason") in CACHEABLE_REFUSAL_CODES
        and _cacheable_refusal_status(
            result.get("refusal_reason"), result.get("engine_status"),
        )
        and not result.get("exit_signal")
        and not result.get("exit_code")
    )

ENGINE_CHEMISTRY_VERSION = {
    "openimcc": 1,
    "internal-analytical": 3,
    "vaporock": 1,
    "magemin": 4,
    "alphamelts": 4,
    "thermoengine": 4,
}

# Bump only when an engine change can alter predictions. Runtime identity is
# diagnostic and does not change cache keys or expire cached outputs.
# alphamelts v1: 2026-11-08 alphaMELTS 2.3.1 Darwin arm64 binary - initial cache version
# alphamelts v2: 2026-11-11 alphaMELTS 2.3.1, b257r landing 982a8d9fa - canonical activity labels, oxide-vapour projection and typed missing-pO2 refusal (18be4a5cd, b4e6e0dda, 959731207)
# thermoengine v1: 2026-11-08 ThermoEngine df7a5f4609116a83bf40a5a352a19e3826a822d9 - initial cache version
# thermoengine v2: 2026-11-11 ThermoEngine df7a5f4609116a83bf40a5a352a19e3826a822d9 - apply configured redox buffer, fO2 offset and Fe3Fet ratio during backend initialization (bd99e209c)
# magemin v1: 2026-11-08 MAGEMin commit 40ed109d07d4246001cab59382596d33fed79086 - initial cache version
# magemin v2: 2026-11-11 MAGEMin binary pin unchanged, b257r landing 982a8d9fa - d-367 mode-vector closure before liquid-fraction and composition extraction (cd48a78fe)
# vaporock v1: 2026-11-08 VapoRock 0159678a8eefb51dfe3f12bb646dc38507a07230 - initial cache version
# internal-analytical v1: 2026-11-08 simulator code at d5e6b8dc2 - initial cache version
# openimcc v1: 2026-11-08 OpenIMCC pin 66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84 - initial cache version
# magemin v3: 2026-11-17 MAGEMin build and patch changes altered native activities (7e55ce7f24, c204deaf36)
# alphamelts v3: 2026-11-17 shared _MELTSBackendSupport numeric changes altered cached results (93bbb34d5d, df3abcff40)
# thermoengine v3: 2026-11-17 shared _MELTSBackendSupport numeric changes altered cached results (93bbb34d5d, df3abcff40)
# internal-analytical v2: 2026-11-17 stateless activity rewrite changed repeated-call vapor pressures (0e9b03818f)
# internal-analytical v3: 2026-11-18 b265 e7fbd4c052 onto b-1244 71806e046 - vapour-rail continuation notice, wall-saturation thermo and evaporation drive
# magemin v4: 2026-11-18 MAGEMin pin unchanged, b265 e7fbd4c052 onto b-1244 71806e046 - positive SiO2 outside [30, 80] wt% is predicted and flagged; Fe-free cells leave fO2 indeterminate
# alphamelts v4: 2026-11-18 alphaMELTS binary pin unchanged, b265 e7fbd4c052 onto b-1244 71806e046 - subfloor oxygen extrapolation notice and freeze-gate cooling path
# thermoengine v4: 2026-11-18 ThermoEngine pin unchanged, b265 e7fbd4c052 onto b-1244 71806e046 - same subfloor oxygen extrapolation notice


_ENGINE_CACHE_ROOT = Path(__file__).resolve().parents[1]
_ENGINE_CACHE_KEY_PREFIX_BYTES = 4
_UNKNOWN_PRE_MIGRATION_BUILD = "unknown-pre-migration"

# Species live in the request the key already packs. They are recorded beside
# the row so a manual expire can find them. They are not added to the key:
# a key change would miss every row the studios are warming.

# Production request builders name species in these fields.
# composition_*: openimcc evaluate, internal-analytical, and the isolated-cell
# engines. parent_oxide_activities / parent_oxides: openimcc evaluate_gas.
# inventory: openimcc evaluate_redox. native_activity_formula: the magemin
# activity request. kwargs: the battery vapor path wraps those same fields.
_SPECIES_MAP_FIELDS = (
    "composition_wt_pct",
    "composition_mol",
    "composition_kg",
    "composition",
    "parent_oxide_activities",
    "inventory",
)
_SPECIES_SEQUENCE_FIELDS = ("parent_oxides",)
_SPECIES_NAME_FIELDS = ("native_activity_formula",)
_SPECIES_NESTED_FIELDS = ("kwargs",)


class EngineCacheSpeciesError(ValueError):
    """A cache request names species in a shape this record cannot store."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"engine cache species field {field!r}: {reason}")


def _casefolded_species_name(name: str) -> str:
    """Spelling stored for a request name, and the form expire matches.

    The side table keeps the name the request used, casefolded. Oxide
    aliases are not folded here. This module is a stdlib leaf, so it
    cannot import the engine alias map. ``expire --species`` in
    ``scripts/engine_cache_sync.py`` expands those spellings before it
    calls in. A stored ``feo_total`` then expires when the operator
    names ``FeO``. Folding that alias inside this module would either
    import ``engines`` or copy the map, and a missed expansion would
    keep serving the old row. This does not change ``engine_cache_key``.
    """

    folded = name.strip().casefold()
    if not folded:
        raise EngineCacheSpeciesError("species", "names must be non-empty strings")
    return folded


def _species_name(field: str, name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise EngineCacheSpeciesError(field, "names must be non-empty strings")
    return _casefolded_species_name(name)


def _collect_species_fields(payload: Mapping[str, Any], found: set[str]) -> None:
    for field in _SPECIES_MAP_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            continue
        if not isinstance(value, Mapping):
            raise EngineCacheSpeciesError(
                field, f"must be a mapping or null, found {type(value).__name__}"
            )
        for name in value:
            found.add(_species_name(field, name))
    for field in _SPECIES_SEQUENCE_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if value is None:
            continue
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
            raise EngineCacheSpeciesError(
                field, f"must be a sequence of names or null, found {type(value).__name__}"
            )
        for name in value:
            found.add(_species_name(field, name))
    for field in _SPECIES_NAME_FIELDS:
        if field not in payload or payload[field] is None:
            continue
        found.add(_species_name(field, payload[field]))


def cache_request_species(request: Any) -> frozenset[str]:
    """Species the request names as inputs. Empty when it names none.

    A field that is present but not a species map refuses. Storing that
    request as "no species" would let expire --species keep a row that
    depended on one.
    """

    if not isinstance(request, Mapping):
        raise EngineCacheSpeciesError("request", "must be a mapping")
    found: set[str] = set()
    _collect_species_fields(request, found)
    for field in _SPECIES_NESTED_FIELDS:
        if field not in request or request[field] is None:
            continue
        nested = request[field]
        if not isinstance(nested, Mapping):
            raise EngineCacheSpeciesError(
                field, f"must be a mapping or null, found {type(nested).__name__}"
            )
        _collect_species_fields(nested, found)
    return frozenset(found)


def species_from_natural_key(key: bytes) -> frozenset[str] | None:
    """Species packed in a natural key.

    None means the key is the blake2b fallback (flag 0xff). The payload is
    not recoverable, so a migrated row has no species record. expire --species
    treats that row as species-unknown and deletes it: the species it served
    cannot be shown to be unaffected. A store of a blake2b request that named
    no species writes one side row whose species text is empty, so that row
    is known-empty and is not this None result. --all and prune still delete
    either row when its chemistry version matches. An empty frozenset is a
    real request that named no species; it is not this case, and expire
    --species leaves it in place. A flag other than 0x00 or 0xff raises.
    expire counts that row and leaves it.
    """

    if len(key) < _ENGINE_CACHE_KEY_PREFIX_BYTES:
        raise EngineCacheSpeciesError("key", "shorter than the engine/version prefix")
    flag = key[3]
    if flag == 0xFF:
        return None
    if flag != 0:
        raise EngineCacheSpeciesError("key", f"unknown key flag {flag}")
    try:
        request = unpack(key[_ENGINE_CACHE_KEY_PREFIX_BYTES:], key=True)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise EngineCacheSpeciesError(
            "key", f"natural payload is not a request ({exc})"
        ) from exc
    return cache_request_species(request)


ENGINE_CHEMISTRY_VERSION_IDENTITY_AT = {
    "openimcc": {
        "identity": "commit:66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84",
        "date": "2026-11-08",
    },
    "internal-analytical": {
        "identity": "source-sha256:ac03c54243b61890960158318fb40a52972f301e9ac1dc9c381fa3a495818740",
        "date": "2026-11-18",
    },
    "vaporock": {
        "identity": "version:1.0.0;record-sha256:89e860e3d6995f542337748019c4f6198f76c127ce3ca90fbbc2063bd401fc5a",
        "date": "2026-11-08",
    },
    "magemin": {
        "identity": "sha256:6c623e220e5fa10ebdc063f5c8ea80cda52b06593e98d59be677e25de9ee4d28",
        "date": "2026-11-18",
    },
    "alphamelts": {
        "identity": "sha256:d91bd8baee106dee03136e7bf16a9e2c6c17d0dda6c978ff1bfd698c0b073a85",
        "date": "2026-11-18",
    },
    "thermoengine": {
        "identity": "version:1.2.0;record-sha256:462a81b3fda92acf74a1e9e38f260dac65232d855340f72307e05a65219c1b27",
        "date": "2026-11-18",
    },
}

OPENIMCC_CANONICAL_OUTPUT_SHA256 = "2eafdcb2139bb1cce9309ceff1aa0b981b609f305406dff8b1d7c3e3002fa69d"

_TABLE = "engine_outputs"
_SPECIES_TABLE = "engine_output_species"
_CACHE_SCHEMA_VERSION = 4
_V3_OUTPUT_COLUMNS = (
    "key", "value", "engine", "chem_version", "source_box", "row_seq", "provenance_id",
)
_CACHE_TABLE_DEFINITIONS = (
    (
        _TABLE,
        (
            ("key", "BLOB PRIMARY KEY"),
            ("value", "BLOB NOT NULL"),
            ("engine", "TEXT NOT NULL"),
            ("chem_version", "TEXT NOT NULL"),
            ("source_box", "TEXT NOT NULL"),
            ("row_seq", "INTEGER NOT NULL"),
            ("provenance_id", "INTEGER NOT NULL DEFAULT 0"),
        ),
        True,
    ),
    ("provenance", (("id", "INTEGER PRIMARY KEY"), ("value", "BLOB NOT NULL UNIQUE")), False),
    ("box_sequences", (("source_box", "TEXT PRIMARY KEY"), ("next_seq", "INTEGER NOT NULL")), True),
    (
        _SPECIES_TABLE,
        (
            ("source_box", "TEXT NOT NULL"),
            ("row_seq", "INTEGER NOT NULL"),
            ("species", "TEXT NOT NULL"),
        ),
        True,
    ),
)
# Composite primary key is not a column clause. WITHOUT ROWID requires it.
_CACHE_TABLE_CONSTRAINTS = {
    _SPECIES_TABLE: "PRIMARY KEY(source_box, row_seq, species)",
}
_CACHE_TABLE_PRIMARY_KEYS = {
    _SPECIES_TABLE: ("source_box", "row_seq", "species"),
}
_CACHE_INDEX_DEFINITIONS = (
    ("engine_outputs_box_seq", _TABLE, ("source_box", "row_seq"), True),
    ("engine_outputs_engine_version", _TABLE, ("engine", "chem_version"), False),
    # expire --species looks up by species. The primary key leads with the row,
    # so it does not serve that lookup. Without this index the manual tool
    # would scan every warmed row.
    ("engine_output_species_species", _SPECIES_TABLE, ("species",), False),
)
_SYNC_WATERMARKS_TABLE = "sync_watermarks"
_ENGINE_NUMBERS = {
    name: number for number, name in enumerate((
        "openimcc", "internal-analytical", "vaporock", "magemin",
        "alphamelts", "thermoengine",
    ), start=1)
}
_NATURAL_KEY_LIMIT = 128
ENGINE_CACHE_BOX_RANGES = {
    box: (index * 1_000_000_000_000 + 1, (index + 1) * 1_000_000_000_000 + 1)
    for index, box in enumerate(("laptop", "s1", "s2", "s3", "rpp"))
}
_TIMEOUT_MS = 60_000
_LOCK = threading.RLock()
_CONNECTIONS: dict[tuple[str, bool], sqlite3.Connection] = {}
_CONNECTION_PID: int | None = None
_CONNECTION_PATH: Path | None = None
_CONNECTION_READONLY = False
_COUNTERS: dict[str, dict[str, int]] = {}
_COUNTER_NAMES = (
    "hits",
    "misses",
    "stores",
    "store_error",
    "read_error",
    "conflicts",
)
_MEMORY_CACHE_MAXSIZE = 65536
_MEMORY_CACHE: OrderedDict[tuple[str, bytes, bool], Any] = OrderedDict()
_MEMORY_CACHE_PID = os.getpid()
_RUNTIME_BACKENDS: dict[str, Any] = {}


class _Bloom:
    """RAM-only negative filter; external commits revoke negative answers."""

    def __init__(self, rows: int, version: int) -> None:
        bit_count = 1 << (max(4096, rows * 10) - 1).bit_length()
        self.bits = bytearray(bit_count // 8)
        self.mask = bit_count - 1
        self.version = version
        self.trusted = True
        self.count = 0

    def _positions(self, key: bytes):
        hashed = hash(key)
        stride = (hashed >> 32) | 1
        for index in range(7):
            yield (hashed + index * stride) & self.mask

    def add(self, key: bytes) -> None:
        for bit in self._positions(key):
            self.bits[bit >> 3] |= 1 << (bit & 7)
        self.count += 1

    def maybe_contains(self, key: bytes) -> bool:
        return not self.trusted or all(
            self.bits[bit >> 3] & (1 << (bit & 7)) for bit in self._positions(key)
        )


_BLOOMS: dict[sqlite3.Connection, _Bloom] = {}


def _build_bloom(connection: sqlite3.Connection) -> _Bloom:
    rows = connection.execute(f"SELECT count(*) FROM {_TABLE}").fetchone()[0]
    bloom = _Bloom(rows, connection.execute("PRAGMA data_version").fetchone()[0])
    for (key,) in connection.execute(f"SELECT key FROM {_TABLE}"):
        bloom.add(bytes(key))
    return bloom


def _record_bloom_key(connection: sqlite3.Connection, key: bytes) -> None:
    bloom = _BLOOMS.get(connection)
    if bloom is not None:
        bloom.add(key)
        if bloom.count * 10 > len(bloom.bits) * 8:
            _BLOOMS[connection] = _build_bloom(connection)


def _definitely_absent(connection: sqlite3.Connection, key: bytes) -> bool:
    bloom = _BLOOMS[connection]
    if bloom.maybe_contains(key):
        return False
    if connection.execute("PRAGMA data_version").fetchone()[0] != bloom.version:
        # Imported rows may arrive out of sequence. Never trust a stale negative.
        bloom.trusted = False
        return False
    return True


@dataclass(frozen=True)
class RuntimeIdentityUnknown:
    """A diagnostic identity could not be read from runtime metadata."""

    engine_id: str
    reason: str
    detail: str


_RUNTIME_IDENTITIES: dict[str, str | RuntimeIdentityUnknown] = {}
_CACHE_BYPASSED: ContextVar[bool] = ContextVar(
    "regolith_engine_cache_bypassed", default=False
)
_ENGINE_CACHE_PATH_UNSET = object()
_ENGINE_CACHE_PATH_OVERRIDE: ContextVar[str | None | object] = ContextVar(
    "regolith_engine_cache_path_override",
    default=_ENGINE_CACHE_PATH_UNSET,
)
# Set by the pytest fixture from the runner environment. None outside pytest,
# so scoring scripts keep the process mode and are not treated as test doubles.
_RUNNER_CACHE_SNAPSHOT: tuple[str | None, str] | None = None
# Paths that are the wrong schema or not a sqlite file. The process does not
# open them again. Migration stays a manual command.
_SCHEMA_MISMATCH_DISABLED: set[str] = set()
_MISSING = object()

ENGINE_SETTINGS_PREDICTION_NEUTRAL_REASONS = {
    "openimcc": {
        "warm_worker": "The OpenIMCC battery adapter has no worker scheduling option."
    },
    "alphamelts": {
        "warm_worker": "The opener fixes warm_worker=False; worker reuse changes scheduling only.",
        "constructor:python_bridge": "The battery opener selects transport through canonical mode and does not forward the legacy alias.",
        "constructor:alphamelts": "The battery opener supplies flat settings and does not forward nested legacy config.",
        "constructor:redox_buffer_fallback": "The opener always supplies fO2_buffer, so the lower-priority redox_buffer fallback is unused.",
        "constructor:fe3fet_ratio_fallback": "The opener always supplies Fe3Fet_Liq, so the lower-priority fe3fet_ratio fallback is unused.",
        "constructor:petthermotools_warm_call_timeout_s": "The opener leaves this optional startup timeout at its fixed backend default.",
        "constructor:timeout_s": "The opener leaves the backend subprocess timeout at its fixed default; request timeout is handled by the battery worker.",
        "constructor:require_petthermotools": "The opener does not request this legacy flag; the canonical mode selects transport.",
        "constructor:calc_mode_conflict": "This flag is derived from the projected model and effective calculation mode.",
        "constructor:model_calc_mode": "This mode is derived from the projected model and is carried only to explain uncached conflicts.",
        "constructor:subprocess_model_error": "This diagnostic is derived from the projected model and is used only for uncached refusals.",
    },
    "vaporock": {
        "warm_worker": "The opener fixes warm_worker=False; worker reuse changes scheduling only."
    },
    "magemin": {
        "warm_worker": "The opener fixes warm_worker=False; worker reuse changes scheduling only."
    },
    "thermoengine": {
        "warm_worker": "The ThermoEngine battery opener does not forward worker scheduling.",
        "constructor:mode": "The battery opener selects ThermoEngine by backend name and uses the constructor's fixed thermoengine default mode.",
        "constructor:alphamelts": "The battery opener supplies flat settings and does not forward nested AlphaMELTS config.",
        "constructor:thermoengine": "The battery opener supplies flat settings and does not forward nested ThermoEngine config.",
        "constructor:redox_buffer_fallback": "The opener always supplies fO2_buffer, so the lower-priority redox_buffer fallback is unused.",
        "constructor:fe3fet_ratio_fallback": "The opener always supplies Fe3Fet_Liq, so the lower-priority fe3fet_ratio fallback is unused.",
        "constructor:thermoengine_health_timeout_s": "The battery opener leaves the health timeout at its fixed operational default.",
        "constructor:thermoengine_equilibrate_timeout_s": "The battery opener leaves the equilibrate timeout at its fixed operational default.",
    },
}


class EngineCacheSchemaMismatch(RuntimeError):
    """The configured cache store has columns this reader cannot use."""

    def __init__(
        self,
        table: str,
        expected_columns: Iterable[str],
        found_columns: Iterable[str],
    ) -> None:
        self.table = table
        self.expected_columns = tuple(expected_columns)
        self.found_columns = tuple(found_columns)
        super().__init__(
            f"engine cache schema mismatch in {table}: expected columns "
            f"{self.expected_columns!r}, found {self.found_columns!r}"
        )


class EngineCacheAliasError(ValueError):
    """A synchronization output aliases an input or another output."""


class EngineCacheReadonlyError(ValueError):
    """A write targeted the runner cache while that file is read-only.

    CI opens the runner file read-only by default so shared engine results
    are reused, never rewritten. Store stays a counted no-op. Every other
    write API refuses here, before it creates or opens the file.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"engine cache is read-only: refusing write to {path}")


class EngineCacheSettingsError(ValueError):
    """Engine cache settings do not contain the normalized projection inputs."""

    def __init__(self, engine_name: str, missing_fields: Iterable[str]) -> None:
        self.engine_name = engine_name
        self.missing_fields = tuple(missing_fields)
        super().__init__(
            f"engine cache settings for {engine_name!r} are missing normalized "
            f"fields {self.missing_fields!r}; pass normalize_engine_settings(...) "
            "output"
        )


def engine_cache_key(engine_id: str, chem_version: str | int, request: Any) -> bytes:
    if engine_id not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown engine {engine_id!r}")
    version = int(chem_version)
    if not 0 <= version <= 65535:
        raise ValueError("chemistry version must fit u16")
    try:
        payload = pack(request, key=True)
    except (TypeError, ValueError) as exc:
        raise ValueError("engine request must contain finite canonical scalars") from exc
    header = (
        bytes((_ENGINE_NUMBERS[engine_id],))
        + version.to_bytes(2, "big")
    )
    if len(payload) + _ENGINE_CACHE_KEY_PREFIX_BYTES > _NATURAL_KEY_LIMIT:
        return header + b"\xff" + hashlib.blake2b(payload, digest_size=16).digest()
    return header + b"\x00" + payload


def _tuple_to_list_if_needed(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


_ENGINE_CACHE_SETTINGS_PROJECTIONS: dict[str, dict[str, Any]] = {
    "internal-analytical": {
        "oxygen_balance_backend": lambda settings: settings["pack"],
        "high_t_melt_activity": None,
    },
    "openimcc": {"pack": "melt_pack", "gas_pack": None},
    "alphamelts": {
        "transport": "transport_mode",
        "effective_calc_mode": None,
        "raw_model_name": "model",
        "python_api_model": _tuple_to_list_if_needed,
        "redox_buffer": None,
        "fO2_offset": "fo2_offset",
        "Fe3Fet_ratio": "fe3fet_ratio",
        "configured_vapor_transport_pO2_bar": "vapor_transport_pO2_bar",
        "configured_vapor_transport_floor_bar": "vapor_transport_floor_bar",
    },
    "vaporock": {
        "temperature_units": None,
        "pressure_units": None,
        "vapor_pressure_units": None,
    },
    "magemin": {"database": None, "bridge": None, "fO2_buffer": None},
    "thermoengine": {
        "model": None,
        "configured_vapor_transport_pO2_bar": "vapor_transport_pO2_bar",
        "configured_vapor_transport_floor_bar": "vapor_transport_floor_bar",
        "redox_buffer": None,
        "fO2_offset": "fo2_offset",
        "Fe3Fet_ratio": "fe3fet_ratio",
    },
}


def engine_cache_settings(
    engine_name: str,
    normalized_settings: Mapping[str, Any],
) -> dict[str, Any]:
    """Project prediction inputs from normalized opener settings."""
    try:
        projection = _ENGINE_CACHE_SETTINGS_PROJECTIONS[engine_name]
    except KeyError as exc:
        raise ValueError(f"unknown engine {engine_name!r}") from exc

    if isinstance(normalized_settings, Mapping):
        missing = tuple(
            name for name in projection if name not in normalized_settings
        )
    else:
        missing = tuple(projection)
    if missing:
        raise EngineCacheSettingsError(engine_name, missing)

    return {
        target if isinstance(target, str) else name: (
            target(normalized_settings[name])
            if callable(target)
            else normalized_settings[name]
        )
        for name, target in projection.items()
    }


def _cache_environment_setting(
    name: str,
    default: str | None = None,
) -> str | None:
    return os.environ.get(name, default)


@contextmanager
def _engine_cache_path_scope(path: str | Path | None) -> Iterator[None]:
    """Set a cache path for one score run without changing process environment."""
    token = _ENGINE_CACHE_PATH_OVERRIDE.set(None if path is None else str(path))
    try:
        yield
    finally:
        _ENGINE_CACHE_PATH_OVERRIDE.reset(token)


def _engine_cache_path_scope_state() -> tuple[bool, str | None]:
    """Return the scoped path override for propagation to spawned score workers."""
    path = _ENGINE_CACHE_PATH_OVERRIDE.get()
    if path is _ENGINE_CACHE_PATH_UNSET:
        return False, None
    return True, cast(str | None, path)


def _set_engine_cache_path_scope_state(
    state: tuple[bool, str | None],
) -> None:
    """Install an explicit scope in a newly spawned, single-run worker."""
    configured, path = state
    if configured:
        _ENGINE_CACHE_PATH_OVERRIDE.set(path)


def _configured_engine_cache_path() -> str | None:
    """Return the configured cache path, or None when caching is disabled.

    ``REGOLITH_ENGINE_CACHE`` is how CI, gates, the accuracy scorecard, user
    runs, and the optimizer reuse engine work. CI sets it and opens the file
    read-only. Unset or empty disables the cache. Do not clear it to isolate
    a test: serving a cached row is valid. The key is engine + version +
    request.
    """
    path = _ENGINE_CACHE_PATH_OVERRIDE.get()
    if path is not _ENGINE_CACHE_PATH_UNSET:
        return path or None
    return _cache_environment_setting("REGOLITH_ENGINE_CACHE") or None


@contextmanager
def runner_engine_cache_snapshot(path: str | None, mode: str = "") -> Iterator[None]:
    """Remember the runner cache file and mode for one test.

    An explicit cache path that is not this file does not inherit ``mode``.
    """

    global _RUNNER_CACHE_SNAPSHOT
    previous = _RUNNER_CACHE_SNAPSHOT
    _RUNNER_CACHE_SNAPSHOT = (path or None, mode or "")
    try:
        yield
    finally:
        _RUNNER_CACHE_SNAPSHOT = previous


def _configured_cache_mode() -> str:
    """Return ``readonly`` or read/write.

    CI sets ``REGOLITH_ENGINE_CACHE_MODE=readonly`` by default. A store in
    that mode is a counted no-op; the shared file is never written. Any
    other non-empty value is a typed refusal, not a silent read/write.
    """

    mode = (_cache_environment_setting("REGOLITH_ENGINE_CACHE_MODE", "") or "").lower()
    if mode not in {"", "readonly"}:
        raise ValueError(f"unknown engine cache mode {mode!r}")
    return mode


def _expanded_cache_path(path: str | os.PathLike[str]) -> Path:
    """Expand ``~`` for a str or a Path. Do not resolve.

    Environment values, relative spellings, and symlinks arrive as ``str``.
    ``str`` has no ``expanduser``. ``os.path.expanduser`` accepts both.
    Identity comparison uses ``_cache_path_identity`` (``os.path.realpath``),
    not ``Path.resolve``.
    """

    return Path(os.path.expanduser(os.fspath(path)))


def _cache_path_identity(path: str | os.PathLike[str] | None) -> str | None:
    """Canonical path for runner-file identity.

    Symlinks and relative spellings are the same file. ``os.path.realpath``
    (not ``Path.resolve``) so the comparison does not follow pathlib's
    strict flag. Expand ``~`` first, through ``_expanded_cache_path``, so a
    ``str`` and a ``Path`` name the same file. Empty is not a file.
    """

    if path is None:
        return None
    text = os.fspath(path).strip()
    if not text:
        return None
    return os.path.realpath(os.fspath(_expanded_cache_path(text)))


def _runner_cache_identity() -> str | None:
    """The runner file, or None when this process has no runner cache.

    Outside pytest the runner file is ``REGOLITH_ENGINE_CACHE``. A pytest
    snapshot with an empty path (``engine_cache_off``) has no runner file,
    so a test-owned cache stays writable. An empty snapshot path is not
    the runner file.
    """

    snapshot = _RUNNER_CACHE_SNAPSHOT
    if snapshot is None:
        configured = _cache_environment_setting("REGOLITH_ENGINE_CACHE") or None
        return _cache_path_identity(configured)
    if not snapshot[0]:
        return None
    return _cache_path_identity(snapshot[0])


def _refuse_runner_readonly_write(path: str | os.PathLike[str]) -> None:
    """Refuse every write of the runner file while it is read-only.

    One owner for the rule. ``_connect`` (drop and load), ``merge_cache_files``,
    and ``snapshot_cache`` (sync and seed) call this before they create or
    open the target. Store does not: a store attempt stays a counted no-op.
    A different file is not the runner file, so a sync into another master
    still writes, and a test-owned cache does not inherit this refusal.
    """

    runner = _runner_cache_identity()
    if runner is None or _cache_path_identity(path) != runner:
        return
    if _configured_cache_mode() != "readonly":
        return
    raise EngineCacheReadonlyError(runner)


def _active_cache_is_runner_file(active_path: str) -> bool:
    """True when this lookup is the snapshotted runner file.

    Identity is ``os.path.realpath`` after ``expanduser``, so a symlink or
    a relative spelling is still the runner file. Outside pytest the
    snapshot is unset and this stays false: the double check is a test
    seam, and read-only writes use ``_runner_cache_identity`` instead.
    """

    snapshot = _RUNNER_CACHE_SNAPSHOT
    if snapshot is None or not snapshot[0]:
        return False
    runner = _cache_path_identity(snapshot[0])
    if runner is None:
        return False
    override = _ENGINE_CACHE_PATH_OVERRIDE.get()
    if override is not _ENGINE_CACHE_PATH_UNSET:
        return _cache_path_identity(override) == runner
    return _cache_path_identity(active_path) == runner


def _engine_cache_is_readonly(active_path: str) -> bool:
    """Read-only is the cache layer's rule, not a caller convention.

    CI opens the runner file read-only by default. That file keeps the
    runner mode, so a store attempt does not write it. A test-owned file
    does not inherit that mode: the test's own cache stays writable unless
    the test set a different mode itself.
    """

    mode = _configured_cache_mode()
    snapshot = _RUNNER_CACHE_SNAPSHOT
    if snapshot is None or _active_cache_is_runner_file(active_path):
        return mode == "readonly"
    if mode != (snapshot[1] or "").lower():
        return mode == "readonly"
    return False


def _is_test_module(module: str | None) -> bool:
    if not module:
        return False
    return (
        module == "tests"
        or module.startswith("tests.")
        or module == "conftest"
        or module.startswith("conftest.")
        or module == "unittest.mock"
        or module.startswith("unittest.mock.")
    )


def _function_is_double(func: Any) -> bool:
    """True when this function was not compiled from a real adapter module.

    A real adapter function's ``__module__`` is its package and its code
    filename is that package's source file. Neither is ``tests``, ``conftest``,
    or ``unittest.mock``. Replacing ``__module__`` cannot hide a test file,
    and a real adapter file cannot acquire a ``tests`` path component.
    """

    if isinstance(func, Mock):
        return True
    module = getattr(func, "__module__", None)
    if _is_test_module(module if isinstance(module, str) else None):
        return True
    code = getattr(func, "__code__", None)
    filename = getattr(code, "co_filename", "") or ""
    if not filename or filename.startswith("<"):
        return True
    parts = Path(filename).parts
    return "tests" in parts or "conftest.py" in parts


def _unwrap_callable(value: Any) -> Any:
    if isinstance(value, (classmethod, staticmethod)):
        return value.__func__
    func = getattr(value, "__func__", None)
    if inspect.isfunction(func):
        return func
    return value


def _adapter_methods_replaced(obj: Any) -> bool:
    """True when a snapshotted adapter method is no longer the class-body function."""

    cls = obj if inspect.isclass(obj) else type(obj)
    if inspect.isclass(obj):
        instance_dict: Mapping[str, Any] = {}
    else:
        raw_dict = getattr(obj, "__dict__", None)
        instance_dict = raw_dict if isinstance(raw_dict, dict) else {}
    seen_names: set[str] = set()
    for klass in cls.__mro__:
        recorded = klass.__dict__.get("_real_adapter_methods")
        if not isinstance(recorded, dict):
            continue
        for name, original in recorded.items():
            if name in seen_names:
                continue
            seen_names.add(name)
            if name in instance_dict:
                current = instance_dict[name]
            else:
                current = getattr(cls, name, None)
                if current is None:
                    continue
            func = _unwrap_callable(current)
            if (
                isinstance(current, Mock)
                or isinstance(func, Mock)
                or func is not original
                or _function_is_double(func)
            ):
                return True
    return False


def _has_adapter_snapshot(obj: Any) -> bool:
    if obj is None or isinstance(obj, (str, bytes, int, float, bool)):
        return False
    cls = obj if inspect.isclass(obj) else type(obj)
    return any(
        "_real_adapter_methods" in klass.__dict__
        for klass in getattr(cls, "__mro__", ())
    )


def _callable_is_double(value: Any) -> bool:
    if isinstance(value, Mock):
        return True
    if inspect.isclass(value):
        return _is_test_module(getattr(value, "__module__", None)) or _adapter_methods_replaced(value)
    func = _unwrap_callable(value)
    if inspect.isfunction(func) or inspect.ismethod(value):
        return _function_is_double(func)
    return False


def _instance_is_double(obj: Any, seen: set[int]) -> bool:
    if obj is None or isinstance(obj, (str, bytes, int, float, bool, tuple, list, dict, set)):
        return False
    marker = id(obj)
    if marker in seen:
        return False
    seen.add(marker)
    if isinstance(obj, Mock):
        return True
    if inspect.isclass(obj):
        return _is_test_module(getattr(obj, "__module__", None)) or _adapter_methods_replaced(obj)
    if _is_test_module(getattr(type(obj), "__module__", None)):
        return True
    if _adapter_methods_replaced(obj):
        return True
    instance_dict = getattr(obj, "__dict__", None)
    if not isinstance(instance_dict, dict):
        return False
    for value in instance_dict.values():
        if isinstance(value, Mock) or _callable_is_double(value):
            return True
        if _has_adapter_snapshot(value) and _adapter_methods_replaced(value):
            return True
    return False


def _function_closes_over_double(func: Any, seen: set[int], depth: int) -> bool:
    if depth > 8 or not inspect.isfunction(func):
        return False
    marker = id(func)
    if marker in seen:
        return False
    seen.add(marker)
    if _function_is_double(func):
        return True
    globals_dict = getattr(func, "__globals__", {})
    for name in func.__code__.co_names:
        target = globals_dict.get(name, _MISSING)
        if target is _MISSING:
            continue
        if _callable_is_double(target):
            return True
    closure = func.__closure__
    if not closure:
        return False
    for cell in closure:
        try:
            closed = cell.cell_contents
        except ValueError:
            continue
        if _value_is_double(closed, seen, depth + 1):
            return True
    return False


def _value_is_double(value: Any, seen: set[int], depth: int) -> bool:
    if isinstance(value, Mock):
        return True
    if isinstance(value, functools.partial):
        if _value_is_double(value.func, seen, depth):
            return True
        keywords = value.keywords or {}
        return any(_value_is_double(item, seen, depth + 1) for item in value.args) or any(
            _value_is_double(item, seen, depth + 1) for item in keywords.values()
        )
    if inspect.ismethod(value):
        func = value.__func__
        if _function_is_double(func) or _instance_is_double(value.__self__, seen):
            return True
        return False
    if inspect.isfunction(value):
        return _function_closes_over_double(value, seen, depth)
    return _instance_is_double(value, seen)


def _cache_open_is_schema_mismatch(exc: BaseException) -> bool:
    """True when the file itself is stale, wrong-schema, or not sqlite.

    ``OperationalError`` (locked, busy) is not this: that open can be
    retried. An exact ``DatabaseError`` is "file is not a database" or a
    malformed image. Schema mismatch is the typed wrong-schema refusal.
    """

    if isinstance(exc, EngineCacheSchemaMismatch):
        return True
    return type(exc) is sqlite3.DatabaseError


def _disable_schema_mismatch(path_value: str, exc: BaseException) -> None:
    """Stop opening this path, and say so once.

    A cache problem must not stop the run. The solve is uncached. The file
    is not migrated from the read path. The notice names the path, the
    expected and found schema, and ``scripts/engine_cache_sync.py migrate``.
    """

    identity = _cache_path_identity(path_value) or path_value
    with _LOCK:
        first = identity not in _SCHEMA_MISMATCH_DISABLED
        _SCHEMA_MISMATCH_DISABLED.add(identity)
    if not first:
        return
    if isinstance(exc, EngineCacheSchemaMismatch):
        where = exc.table
        expected = ",".join(exc.expected_columns)
        found = ",".join(exc.found_columns)
    else:
        where = "file"
        expected = str(_CACHE_SCHEMA_VERSION)
        found = "not-a-sqlite-database"
    logging.getLogger(__name__).warning(
        "engine_cache_schema_mismatch path=%s where=%s expected=%s found=%s "
        "fix=scripts/engine_cache_sync.py migrate %s",
        identity,
        where,
        expected,
        found,
        identity,
    )


def _schema_mismatch_disabled(path_value: str) -> bool:
    identity = _cache_path_identity(path_value)
    if identity is None:
        return False
    with _LOCK:
        return identity in _SCHEMA_MISMATCH_DISABLED


def _count_schema_mismatch(engine_id: str) -> None:
    with _LOCK:
        counts = _counter_bucket(engine_id)
        counts["misses"] += 1
        counts["not_stored:schema_mismatch"] = (
            counts.get("not_stored:schema_mismatch", 0) + 1
        )


def _solve_is_test_double(solve: Any) -> bool:
    """True when ``solve`` is not the real registered engine adapter.

    One check at the open/lookup seam. The cache key is unchanged.
    """

    return _value_is_double(solve, set(), 0)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distribution_identity(
    engine_id: str,
    names: tuple[str, ...],
) -> str | RuntimeIdentityUnknown:
    for name in names:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        try:
            record = distribution.read_text("RECORD")
        except (OSError, UnicodeDecodeError) as exc:
            return RuntimeIdentityUnknown(
                engine_id,
                "distribution_metadata_unreadable",
                f"RECORD: {exc}",
            )
        if record is not None and not isinstance(record, str):
            return RuntimeIdentityUnknown(
                engine_id,
                "distribution_metadata_invalid",
                "RECORD is not text",
            )
        record_digest = (
            hashlib.sha256(record.encode("utf-8")).hexdigest()
            if record is not None and len(record) <= 2_000_000
            else "unavailable"
        )
        try:
            version = distribution.version
        except (OSError, UnicodeDecodeError) as exc:
            return RuntimeIdentityUnknown(
                engine_id,
                "distribution_metadata_unreadable",
                f"version: {exc}",
            )
        return f"version:{version};record-sha256:{record_digest}"
    return "unavailable"


def _native_binary_identity(engine_id: str) -> str:
    backend = _RUNTIME_BACKENDS.get(engine_id)
    binary = getattr(backend, "_binary_path", None)
    if binary is None:
        return "unavailable"
    path = Path(binary)
    try:
        digest = _file_sha256(path)
    except OSError:
        return "unavailable"
    return f"sha256:{digest}"


def _openimcc_dist_identity() -> str | RuntimeIdentityUnknown:
    try:
        distribution = importlib.metadata.distribution("openimcc")
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"
    try:
        direct_url = distribution.read_text("direct_url.json")
    except (OSError, UnicodeDecodeError) as exc:
        return RuntimeIdentityUnknown(
            "openimcc",
            "distribution_metadata_unreadable",
            f"direct_url.json: {exc}",
        )
    if direct_url:
        try:
            payload = json.loads(direct_url)
        except (TypeError, json.JSONDecodeError) as exc:
            return RuntimeIdentityUnknown(
                "openimcc",
                "distribution_metadata_invalid",
                f"direct_url.json: {exc}",
            )
        if not isinstance(payload, Mapping):
            return RuntimeIdentityUnknown(
                "openimcc",
                "distribution_metadata_invalid",
                "direct_url.json must contain an object",
            )
        vcs_info = payload.get("vcs_info", {})
        if not isinstance(vcs_info, Mapping):
            return RuntimeIdentityUnknown(
                "openimcc",
                "distribution_metadata_invalid",
                "direct_url.json vcs_info must contain an object",
            )
        commit = vcs_info.get("commit_id")
        if commit:
            return f"commit:{commit}"
    try:
        return f"version:{distribution.version}"
    except (OSError, UnicodeDecodeError) as exc:
        return RuntimeIdentityUnknown(
            "openimcc",
            "distribution_metadata_unreadable",
            f"version: {exc}",
        )


def _internal_analytical_identity() -> str:
    backend = _RUNTIME_BACKENDS.get("internal-analytical")
    core = getattr(backend, "_core", backend)
    if core is None:
        return "unavailable"
    try:
        module_file = inspect.getsourcefile(type(core))
    except (OSError, TypeError):
        return "unavailable"
    if not module_file:
        return "unavailable"
    path = Path(module_file)
    try:
        return f"source-sha256:{_file_sha256(path)}"
    except OSError:
        return "unavailable"


def _compute_runtime_identity(engine_id: str) -> str | RuntimeIdentityUnknown:
    if engine_id in {"alphamelts", "magemin"}:
        return _native_binary_identity(engine_id)
    if engine_id == "openimcc":
        return _openimcc_dist_identity()
    if engine_id == "internal-analytical":
        return _internal_analytical_identity()
    if engine_id == "thermoengine":
        return _distribution_identity(
            engine_id, ("ThermoEngine", "thermoengine")
        )
    if engine_id == "vaporock":
        return _distribution_identity(engine_id, ("VapoRock", "vaporock"))
    raise ValueError(f"unknown engine {engine_id!r}")


@lru_cache(maxsize=None)
def runtime_identity(engine_id: str) -> str | RuntimeIdentityUnknown:
    """Return the one per-process runtime identity captured at engine open."""
    return _compute_runtime_identity(engine_id)


def record_engine_open(engine_id: str, backend: Any | None = None) -> str | None:
    """Capture and report staleness once, outside the request-key path."""
    if _configured_engine_cache_path() is None:
        return None
    if engine_id in _RUNTIME_IDENTITIES:
        identity = _RUNTIME_IDENTITIES[engine_id]
        return identity if isinstance(identity, str) else None
    if backend is not None:
        _RUNTIME_BACKENDS.setdefault(engine_id, backend)
    identity = runtime_identity(engine_id)
    if isinstance(identity, RuntimeIdentityUnknown):
        _RUNTIME_IDENTITIES[engine_id] = identity
        logging.getLogger(__name__).warning(
            "engine cache: %s runtime identity unknown (%s; %s)",
            engine_id,
            identity.reason,
            identity.detail,
        )
        return None
    _RUNTIME_IDENTITIES[engine_id] = identity
    recorded = ENGINE_CHEMISTRY_VERSION_IDENTITY_AT[engine_id]
    stored = str(recorded["identity"])
    if identity != stored:
        logging.getLogger(__name__).warning(
            "engine cache: %s runtime changed (stored %s, now %s). "
            "Cached outputs are still used. Bump ENGINE_CHEMISTRY_VERSION['%s'] "
            "ONLY if this change affects chemistry; leave it for non-chemistry patches.",
            engine_id,
            stored,
            identity,
            engine_id,
        )
    return identity


def engine_cache_runtime_identities(*, reset: bool = False) -> dict[str, str]:
    identities = {
        engine: (
            identity
            if isinstance(identity, str)
            else "runtime_identity_unknown:"
            f"{identity.reason}:{identity.detail}"
        )
        for engine, identity in _RUNTIME_IDENTITIES.items()
    }
    if reset:
        _RUNTIME_IDENTITIES.clear()
    return identities


def _encode_hot(value: Any) -> tuple[bytes, bytes]:
    return pack_hot(value)


def _decode_hot(value: bytes, provenance: bytes = b"") -> Any:
    return unpack_hot(value, provenance)


def _close_connections() -> None:
    global _CONNECTION_PID, _CONNECTION_PATH
    for connection in _CONNECTIONS.values():
        try:
            connection.close()
        except sqlite3.Error:
            pass
    _CONNECTIONS.clear()
    _BLOOMS.clear()
    _CONNECTION_PID = os.getpid()
    _CONNECTION_PATH = None


def _prepare_memory_cache() -> None:
    global _MEMORY_CACHE_PID
    pid = os.getpid()
    if _MEMORY_CACHE_PID != pid:
        _MEMORY_CACHE.clear()
        _MEMORY_CACHE_PID = pid


def _remember_memory_value(key: tuple[str, bytes, bool], value: Any) -> None:
    _MEMORY_CACHE[key] = value
    _MEMORY_CACHE.move_to_end(key)
    while len(_MEMORY_CACHE) > _MEMORY_CACHE_MAXSIZE:
        _MEMORY_CACHE.popitem(last=False)


def _check_cache_schema(connection: sqlite3.Connection, *, allow_empty: bool = False) -> bool:
    expected_columns = {
        table: tuple(name for name, _ in columns)
        for table, columns, _ in _CACHE_TABLE_DEFINITIONS
    }
    expected_tables = set(expected_columns)
    optional_tables = {_SYNC_WATERMARKS_TABLE}
    own_transaction = not connection.in_transaction
    if own_transaction:
        connection.execute("BEGIN")
    try:
        found_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        namespace = connection.execute("PRAGMA user_version").fetchone()[0]
        unexpected_tables = found_tables - expected_tables - optional_tables

        missing_tables = expected_tables - found_tables
        for table, _, _ in _CACHE_TABLE_DEFINITIONS:
            if table not in found_tables:
                continue
            expected = expected_columns[table]
            info = list(connection.execute(f"PRAGMA table_info({table})"))
            found = tuple(row[1] for row in info)
            if found != expected:
                raise EngineCacheSchemaMismatch(table, expected, found)
            expected_pk = _CACHE_TABLE_PRIMARY_KEYS.get(table)
            if expected_pk is not None:
                found_pk = tuple(
                    row[1] for row in sorted((item for item in info if item[5]), key=lambda item: item[5])
                )
                if found_pk != expected_pk:
                    raise EngineCacheSchemaMismatch(table, expected_pk, found_pk)

        empty = not found_tables
        if empty:
            if allow_empty and namespace in (0, _CACHE_SCHEMA_VERSION):
                if own_transaction:
                    connection.commit()
                return True
            if namespace != _CACHE_SCHEMA_VERSION:
                raise EngineCacheSchemaMismatch(
                    "user_version", (str(_CACHE_SCHEMA_VERSION),), (str(namespace),)
                )
            raise EngineCacheSchemaMismatch(_TABLE, expected_columns[_TABLE], ())

        if namespace != _CACHE_SCHEMA_VERSION:
            raise EngineCacheSchemaMismatch(
                "user_version", (str(_CACHE_SCHEMA_VERSION),), (str(namespace),)
            )

        found_indexes = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, tbl_name FROM sqlite_master WHERE type='index'"
            )
        }
        table_indexes: dict[str, bool] = {}
        for table_name in expected_tables:
            if table_name not in found_tables:
                continue
            for row in connection.execute(f"PRAGMA index_list({table_name})"):
                table_indexes[row[1]] = bool(row[2])
        missing_indexes = set()
        for name, table, columns, unique in _CACHE_INDEX_DEFINITIONS:
            if name not in found_indexes:
                missing_indexes.add(name)
                continue
            if found_indexes[name] != table:
                raise EngineCacheSchemaMismatch(name, columns, ())
            found = tuple(
                row[2] for row in connection.execute(f"PRAGMA index_info('{name}')")
            )
            if found != columns:
                raise EngineCacheSchemaMismatch(name, columns, found)
            if table_indexes.get(name) is not unique:
                expected_unique = ("unique",) if unique else ("non-unique",)
                found_unique = ("unique",) if table_indexes.get(name) else ("non-unique",)
                raise EngineCacheSchemaMismatch(name, expected_unique, found_unique)

        incomplete = bool(missing_tables or missing_indexes)
        if incomplete and unexpected_tables:
            raise EngineCacheSchemaMismatch(
                "tables", sorted(expected_tables | optional_tables), sorted(found_tables)
            )
        if incomplete and not allow_empty:
            if missing_tables:
                table = sorted(missing_tables)[0]
                raise EngineCacheSchemaMismatch(table, expected_columns[table], ())
            name, table, columns, _ = next(
                item for item in _CACHE_INDEX_DEFINITIONS if item[0] in missing_indexes
            )
            raise EngineCacheSchemaMismatch(name, columns, ())
    except BaseException:
        if own_transaction:
            connection.rollback()
        raise
    else:
        if own_transaction:
            connection.commit()
        return incomplete


def _initialize_cache(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise sqlite3.ProgrammingError("cache initialization requires a connection with no active transaction")
    # An interrupted publish leaves *.v3.bak or *.v4-new beside this path.
    # Creating the schema would mark an empty file as the current cache.
    _initialize_cache_refuses_leftovers(connection)
    isolation_level = connection.isolation_level
    connection.isolation_level = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table, columns, without_rowid in _CACHE_TABLE_DEFINITIONS:
                definitions = ", ".join(f"{name} {sql}" for name, sql in columns)
                constraint = _CACHE_TABLE_CONSTRAINTS.get(table)
                if constraint:
                    definitions = f"{definitions}, {constraint}"
                suffix = " WITHOUT ROWID" if without_rowid else ""
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} ({definitions}){suffix}"
                )
            for name, table, columns, unique in _CACHE_INDEX_DEFINITIONS:
                unique_sql = "UNIQUE " if unique else ""
                column_sql = ", ".join(columns)
                connection.execute(
                    f"CREATE {unique_sql}INDEX IF NOT EXISTS {name} "
                    f"ON {table}({column_sql})"
                )
            # BEGIN IMMEDIATE serializes first openers before schema writes. SQLite commits these DDLs
            # and user_version together, so a concurrent check in one read transaction sees either
            # the empty database or the complete schema, never a version-only intermediate state.
            connection.execute(f"PRAGMA user_version={_CACHE_SCHEMA_VERSION}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.isolation_level = isolation_level


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection | None:
    global _CONNECTION_PID, _CONNECTION_PATH, _CONNECTION_READONLY
    # Read-only is enforced here, before mkdir or a writable sqlite open.
    # The store path passes readonly=True and stays a counted no-op.
    if not readonly:
        _refuse_runner_readonly_write(path)
    pid = os.getpid()
    if _CONNECTION_PID != pid:
        _close_connections()
    if _CONNECTION_PATH is not None and _CONNECTION_PATH != path:
        _close_connections()
    if _CONNECTION_PATH is None:
        _CONNECTION_PATH = path
        _CONNECTION_READONLY = readonly
    elif _CONNECTION_READONLY != readonly:
        _close_connections()
        _CONNECTION_PATH = path
        _CONNECTION_READONLY = readonly
    key = (str(path), readonly)
    if key in _CONNECTIONS:
        return _CONNECTIONS[key]
    # sqlite3.connect creates a missing path. That empty file must not become
    # a schema-4 cache while a publish backup is sitting beside the name.
    _refuse_interrupted_cache_open(path)
    if readonly:
        if not path.exists():
            return None
        connection = sqlite3.connect(_cache_uri(path), uri=True, timeout=_TIMEOUT_MS / 1000, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            path, timeout=_TIMEOUT_MS / 1000, check_same_thread=False
        )
        connection.execute(f"PRAGMA busy_timeout={_TIMEOUT_MS}")
    try:
        incomplete = _check_cache_schema(connection, allow_empty=True)
        if incomplete and readonly:
            connection.close()
            return None
        if not readonly:
            # journal_mode persists in the file. The schema check above
            # raises before this, so a refused schema-3 open stays as it was.
            deadline = time.monotonic() + _TIMEOUT_MS / 1000
            while True:
                try:
                    mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
                    if str(mode).lower() != "wal":
                        connection.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as exc:
                    if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                        connection.close()
                        raise
                    if time.monotonic() >= deadline:
                        connection.close()
                        raise
                    time.sleep(0.01)
            _initialize_cache(connection)
    except Exception:
        connection.close()
        raise
    _CONNECTIONS[key] = connection
    _BLOOMS[connection] = _build_bloom(connection)
    return connection


def engine_cache_counters(*, reset: bool = False) -> dict[str, dict[str, int]]:
    with _LOCK:
        snapshot = {engine: dict(counts) for engine, counts in _COUNTERS.items()}
        if reset:
            _COUNTERS.clear()
        return snapshot


def _counter_bucket(engine: str) -> dict[str, int]:
    return _COUNTERS.setdefault(engine, dict.fromkeys(_COUNTER_NAMES, 0))


def _record_engine_cache_counters(
    counters: Mapping[str, Mapping[str, int]],
) -> None:
    with _LOCK:
        for engine, values in counters.items():
            target = _counter_bucket(engine)
            for name, value in values.items():
                target.setdefault(name, 0)
                target[name] += int(value)


def _counter_token(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if all(character.isalnum() or character == "_" for character in value):
        return value
    return None


def _not_stored_exception_label(exc: BaseException) -> str:
    """Name a miss that left solve() by raising. The value is never stored.

    Worker timeouts and process exits use those facts, not the wrapper
    class. A package ``code`` or isolated ``error_name`` keeps the source
    token so the next run shows which refusal escaped.
    """

    if getattr(exc, "timed_out", False) is True:
        return "timeout"
    returncode = getattr(exc, "returncode", None)
    if isinstance(returncode, int) and returncode != 0:
        return "worker_exit"
    for attribute in ("error_name", "code"):
        token = _counter_token(getattr(exc, attribute, None))
        if token is not None:
            return token
    return type(exc).__name__


def _is_refusal(result: Any) -> bool:
    value = result
    status = value.get("status") if isinstance(value, Mapping) else getattr(value, "status", None)
    refusal = (
        value.get("refusal_reason")
        if isinstance(value, Mapping)
        else getattr(value, "refusal_reason", None)
    )
    return isinstance(value, BaseException) or refusal is not None or (
        status is not None
        and status not in {"ok", "success", "converged", "non_authoritative"}
    )


def _insert_cache_row_if_absent(
    connection: sqlite3.Connection,
    digest: bytes,
    value: bytes,
    engine: str,
    chem_version: str,
    source_box: str,
    row_seq: int | None = None,
    *,
    provenance: bytes = b"",
    species: frozenset[str] | None = None,
) -> tuple[bool, bool]:
    """Insert one output only when absent; report whether existing bytes conflict.

    ``species`` is recorded only for a new row. None leaves the side table
    alone so a merge can copy the source rows, and so a loaded blake2b key
    stays species-unknown: this loader did not see the request. An empty
    set on a blake2b key is a request that named nothing. One side row with
    an empty species text marks it known-empty. expire --species matches
    stored names, and ``_casefolded_species_name`` rejects the empty string,
    so that marker is not a species and the row survives. A natural key
    that named nothing has flag 0x00 and stays with no side row, because
    ``species_from_natural_key`` can still read it.
    """
    row = connection.execute(
        f"SELECT value, provenance_id FROM {_TABLE} WHERE key=?", (digest,)
    ).fetchone()
    if row is not None:
        existing_provenance = _read_provenance(connection, row[1])
        return False, bytes(row[0]) != bytes(value) or existing_provenance != provenance
    provenance_id = 0
    if provenance:
        connection.execute("INSERT OR IGNORE INTO provenance(value) VALUES (?)", (provenance,))
        provenance_id = connection.execute("SELECT id FROM provenance WHERE value=?", (provenance,)).fetchone()[0]
    if row_seq is None:
        row_seq = _next_row_seq(connection, source_box)
    elif source_box in ENGINE_CACHE_BOX_RANGES:
        _advance_box_sequence(connection, source_box, row_seq)
    connection.execute(
        f"INSERT INTO {_TABLE} "
        "(key, value, engine, chem_version, source_box, row_seq, provenance_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            digest,
            sqlite3.Binary(value),
            engine,
            chem_version,
            source_box,
            row_seq,
            provenance_id,
        ),
    )
    if species:
        _write_output_species(connection, source_box, row_seq, species)
    elif (
        species is not None
        and len(digest) >= _ENGINE_CACHE_KEY_PREFIX_BYTES
        and digest[3] == 0xFF
    ):
        # Same flag ``species_from_natural_key`` treats as unreadable.
        # Without this row the key is indistinguishable from a migrated
        # digest, and the next expire --species would delete a request
        # that named nothing.
        connection.execute(
            f"INSERT INTO {_SPECIES_TABLE}(source_box, row_seq, species) VALUES (?,?,?)",
            (source_box, row_seq, ""),
        )
    return True, False


def _write_output_species(
    connection: sqlite3.Connection,
    source_box: str,
    row_seq: int,
    species: frozenset[str],
) -> None:
    connection.executemany(
        f"INSERT OR IGNORE INTO {_SPECIES_TABLE}(source_box, row_seq, species) VALUES (?,?,?)",
        [(source_box, row_seq, name) for name in sorted(species)],
    )


def _advance_box_sequence(connection: sqlite3.Connection, source_box: str, row_seq: int) -> None:
    first, stop = ENGINE_CACHE_BOX_RANGES[source_box]
    if not first <= row_seq < stop:
        raise ValueError(f"row sequence is outside the {source_box!r} range")
    connection.execute("INSERT OR IGNORE INTO box_sequences VALUES (?,?)", (source_box, first))
    connection.execute(
        "UPDATE box_sequences SET next_seq=? WHERE source_box=? AND next_seq<=?",
        (row_seq + 1, source_box, row_seq),
    )


def _next_row_seq(connection: sqlite3.Connection, source_box: str) -> int:
    first, stop = ENGINE_CACHE_BOX_RANGES[source_box]
    connection.execute("INSERT OR IGNORE INTO box_sequences VALUES (?,?)", (source_box, first))
    row = connection.execute(
        "UPDATE box_sequences SET next_seq=next_seq+1 WHERE source_box=? AND next_seq<? RETURNING next_seq-1",
        (source_box, stop),
    ).fetchone()
    if row is None:
        raise ValueError(f"row sequence range exhausted for {source_box!r}")
    return int(row[0])


def _read_provenance(connection: sqlite3.Connection, provenance_id: int) -> bytes:
    if not provenance_id:
        return b""
    row = connection.execute("SELECT value FROM provenance WHERE id=?", (provenance_id,)).fetchone()
    if row is None:
        raise ValueError("missing cache provenance")
    return bytes(row[0])


def _forensic_build_identity(engine_id: str) -> str:
    """Runtime identity for a new COLD row. Hits never call this.

    A failure becomes an ``unknown:`` string. Trace metadata must not reject
    a cache write, and it must not pretend the build was identified.
    """

    try:
        identity = runtime_identity(engine_id)
    except Exception as exc:
        return f"unknown:{type(exc).__name__}:{exc}"
    if isinstance(identity, RuntimeIdentityUnknown):
        return f"unknown:{identity.reason}:{identity.detail}"
    if isinstance(identity, str) and identity:
        return identity
    return f"unknown:untyped:{identity!r}"


def _ensure_build_id(
    connection: sqlite3.Connection, schema: str, engine: str, identity: str
) -> int:
    # UNIQUE(engine, identity), not identity alone: two engines that share a
    # forensic string must not be collapsed into one row.
    connection.execute(
        f"INSERT OR IGNORE INTO {schema}.engine_builds(engine, identity) VALUES (?, ?)",
        (engine, identity),
    )
    row = connection.execute(
        f"SELECT id FROM {schema}.engine_builds WHERE engine=? AND identity=?",
        (engine, identity),
    ).fetchone()
    if row is None:
        raise EngineCacheSpeciesError("engine_build", "insert did not yield an id")
    return int(row[0])


def _initialize_cold(connection: sqlite3.Connection, schema: str = "main") -> None:
    # An existing table is left for the schema check or for migrate. Creating
    # over a schema-3 file would hide the key blob this migration has to move.
    existing = connection.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name='engine_values'"
    ).fetchone()
    if existing:
        return
    connection.execute(
        f"CREATE TABLE {schema}.engine_builds ("
        "id INTEGER PRIMARY KEY, engine TEXT NOT NULL, identity TEXT NOT NULL, "
        "UNIQUE(engine, identity))"
    )
    # row_seq alone, not (source_box, row_seq). ENGINE_CACHE_BOX_RANGES are
    # disjoint 1e12-wide intervals, and the allocator refuses a sequence
    # outside its box, so one integer names the HOT row without repeating
    # the key blob. A colliding row_seq fails this primary key instead of
    # attaching the value to the wrong row.
    connection.execute(
        f"CREATE TABLE {schema}.engine_values ("
        "row_seq INTEGER PRIMARY KEY, value BLOB NOT NULL, "
        "engine_build_id INTEGER NOT NULL) WITHOUT ROWID"
    )
    connection.execute(f"PRAGMA {schema}.user_version={_CACHE_SCHEMA_VERSION}")


def _store_cold(
    connection: sqlite3.Connection,
    path: Path,
    row_seq: int,
    values: Mapping[str, Any],
    engine_id: str,
) -> None:
    if not any(row[1] == "cold" for row in connection.execute("PRAGMA database_list")):
        cold_path = _cache_pair(path)[1]
        if not values and not cold_path.exists():
            return
        # Set up WAL before attaching to the active HOT transaction. No payload
        # is committed here; both payload writes share the caller's commit/rollback.
        with closing(sqlite3.connect(cold_path, timeout=_TIMEOUT_MS / 1000)) as setup:
            setup.execute("PRAGMA journal_mode=WAL")
            _initialize_cold(setup)
            setup.commit()
        connection.execute("ATTACH DATABASE ? AS cold", (str(cold_path.resolve()),))
        _check_cold_schema(connection)
    if values:
        build_id = _ensure_build_id(
            connection, "cold", engine_id, _forensic_build_identity(engine_id)
        )
        value = zlib.compress(pack(values), level=6)
        connection.execute(
            "INSERT OR REPLACE INTO cold.engine_values "
            "(row_seq, value, engine_build_id) VALUES (?, ?, ?)",
            (row_seq, value, build_id),
        )
    else:
        connection.execute("DELETE FROM cold.engine_values WHERE row_seq=?", (row_seq,))


def cached_engine_call(engine_id: str, request: Any, solve: Any) -> Any:
    """Serve or store one engine result.

    This is the open/lookup seam. Engines are stateless, and the key is
    engine + chemistry version + request, so a cached value is the engine's
    answer. Serving it in tests is valid. CI uses the runner file read-only
    by default. Only ``engine_cache_off`` tests opt out.
    """

    if _CACHE_BYPASSED.get():
        return live_hot(solve())
    path_value = _configured_engine_cache_path()
    if not path_value:
        return live_hot(solve())
    # A wrong-schema or non-sqlite file stays closed for this process.
    # The run solves uncached. Do not migrate it from this path.
    if _schema_mismatch_disabled(path_value):
        _count_schema_mismatch(engine_id)
        return live_hot(solve())
    readonly = _engine_cache_is_readonly(path_value)
    if (
        engine_id in ENGINE_CHEMISTRY_VERSION
        and _active_cache_is_runner_file(path_value)
        and _solve_is_test_double(solve)
    ):
        # One check, before lookup. A double is a mock, a test-module
        # callable, or an adapter method that is no longer the class-body
        # function. Real adapter functions are compiled from their packages,
        # so this cannot flag them. The check stops at the outer function:
        # a monkeypatch of a dependency that solve calls (engines,
        # melt_backend, openimcc) is not walked. Those tests opt out with
        # engine_cache_off so a warm runner row cannot hide the patch.
        # Do not serve or store a double in the runner file. A test-owned
        # file is unchanged: cache tests store synthetic solves there.
        with _LOCK:
            counts = _counter_bucket(engine_id)
            counts["misses"] += 1
            counts["not_stored:double"] = counts.get("not_stored:double", 0) + 1
        return live_hot(solve())
    source_box = _cache_environment_setting("REGOLITH_ENGINE_CACHE_BOX", "") or ""
    if not readonly:
        if not source_box.strip():
            raise ValueError("REGOLITH_ENGINE_CACHE_BOX must be set in read-write mode")
        if source_box not in ENGINE_CACHE_BOX_RANGES:
            raise ValueError(f"engine cache box {source_box!r} has no configured row range")
    chem_version = ENGINE_CHEMISTRY_VERSION[engine_id]
    cache_key = engine_cache_key(engine_id, chem_version, request)
    memory_key = (path_value, cache_key, readonly)
    with _LOCK:
        _prepare_memory_cache()
        counts = _counter_bucket(engine_id)
        if memory_key in _MEMORY_CACHE:
            result = _MEMORY_CACHE[memory_key]
            _MEMORY_CACHE.move_to_end(memory_key)
            counts["hits"] += 1
            return result

    path = _expanded_cache_path(path_value)
    try:
        connection = _connect(path, readonly=readonly)
    except (sqlite3.Error, OSError, EngineCacheSchemaMismatch) as exc:
        if _cache_open_is_schema_mismatch(exc):
            # One notice per path. Later calls skip the open entirely.
            _disable_schema_mismatch(path_value, exc)
            _count_schema_mismatch(engine_id)
            return live_hot(solve())
        with _LOCK:
            counts = _counter_bucket(engine_id)
            counts["read_error"] += 1
            counts["misses"] += 1
            counts["not_stored:readonly"] = counts.get("not_stored:readonly", 0) + 1
        return live_hot(solve())
    with _LOCK:
        try:
            row = None if connection is None or _definitely_absent(connection, cache_key) else connection.execute(
                f"SELECT value, provenance_id, engine, chem_version "
                f"FROM {_TABLE} WHERE key=?",
                (cache_key,),
            ).fetchone()
        except sqlite3.Error:
            counts["read_error"] += 1
            row = None
        if row is not None and (
            row[2] != engine_id or row[3] != str(chem_version)
        ):
            # The key is authoritative for lookup, but persisted metadata is
            # also checked so a mislabeled row cannot masquerade as current.
            row = None
        if row is not None:
            try:
                result = _decode_hot(row[0], _read_provenance(connection, row[1]))
            except (TypeError, ValueError, UnicodeDecodeError):
                counts["read_error"] += 1
                row = None
            else:
                counts["hits"] += 1
                _remember_memory_value(memory_key, result)
                return result
        counts["misses"] += 1

    try:
        solved = solve()
        hot = live_hot(solved)
    except BaseException as exc:
        # A raise used to count a miss and then leave no bucket. That is
        # the openimcc / magemin / thermoengine gap: the call recomputes
        # on every run and the summary cannot say why.
        with _LOCK:
            bucket = _counter_bucket(engine_id)
            label = _not_stored_exception_label(exc)
            name = f"not_stored:exception:{label}"
            bucket[name] = bucket.get(name, 0) + 1
        raise
    if readonly or connection is None:
        # Readonly state takes precedence: a refusal or uncacheable value
        # cannot be stored either way, so count this miss in one bucket.
        with _LOCK:
            counts = _counter_bucket(engine_id)
            counts["not_stored:readonly"] = counts.get("not_stored:readonly", 0) + 1
        return hot
    if isinstance(solved, EngineCacheValue) and not solved.cacheable:
        with _LOCK:
            counts = _counter_bucket(engine_id)
            counts["not_stored:cacheable_false"] = counts.get(
                "not_stored:cacheable_false", 0
            ) + 1
        return hot
    wrapped_refusal = (
        isinstance(solved, EngineCacheValue)
        and solved.refusal_reason is not None
    )
    refusal_not_cacheable = (
        wrapped_refusal and not cacheable_refusal(solved)
    ) or (
        _is_refusal(hot) and not cacheable_refusal(hot)
    )
    if refusal_not_cacheable:
        refusal_reason = (
            solved.refusal_reason if wrapped_refusal
            else hot.get("refusal_reason") if isinstance(hot, Mapping)
            else getattr(hot, "refusal_reason", None)
        )
        reason_label = str(refusal_reason) if refusal_reason is not None else "untyped"
        with _LOCK:
            counter = f"not_stored:refusal:{reason_label}"
            counts = _counter_bucket(engine_id)
            counts[counter] = counts.get(counter, 0) + 1
    if refusal_not_cacheable:
        return hot
    try:
        if not isinstance(solved, EngineCacheValue) and (
            isinstance(hot, Mapping) and any(isinstance(item, (Mapping, list, tuple)) for item in hot.values())
            or isinstance(hot, (list, tuple))
        ):
            raise TypeError("result has no declared HOT projection")
        hot_value, provenance = _encode_hot(solved.hot if isinstance(solved, EngineCacheValue) else solved)
    except (TypeError, ValueError):
        with _LOCK:
            counts = _counter_bucket(engine_id)
            name = f"uncacheable:{type(hot).__name__}"
            counts[name] = counts.get(name, 0) + 1
        return hot
    try:
        # Recorded beside the row, never inside engine_cache_key. A key change
        # would miss every warmed entry. An unreadable species field is not
        # stored as an empty set: expire --species would then keep it.
        species = cache_request_species(request)
    except EngineCacheSpeciesError:
        with _LOCK:
            counts = _counter_bucket(engine_id)
            counts["not_stored:species"] = counts.get("not_stored:species", 0) + 1
        return hot
    try:
        with _LOCK:
            connection.execute("BEGIN IMMEDIATE")
            inserted, conflict = _insert_cache_row_if_absent(
                connection,
                cache_key,
                hot_value,
                engine_id,
                str(chem_version),
                source_box,
                provenance=provenance,
                species=species,
            )
            if inserted:
                cold_values = solved.cold if isinstance(solved, EngineCacheValue) else {}
                # The insert result stays (inserted, conflict). Callers that
                # stub an already-present row return that pair, and the miss
                # counter below depends on the unpack succeeding. The sequence
                # is on the row just written.
                stored_seq = connection.execute(
                    f"SELECT row_seq FROM {_TABLE} WHERE key=?", (cache_key,)
                ).fetchone()
                if stored_seq is None:
                    raise EngineCacheSpeciesError(
                        "row_seq", "a new row did not receive a sequence"
                    )
                # Build id is forensic only and is written with the COLD value.
                # The hit path above returns before this call.
                _store_cold(connection, path, int(stored_seq[0]), cold_values, engine_id)
            connection.commit()
            if inserted:
                _record_bloom_key(connection, cache_key)
            counts = _counter_bucket(engine_id)
            if inserted:
                counts["stores"] += 1
            elif conflict:
                counts["conflicts"] += 1
            else:
                # The row appeared with the same bytes after the miss read.
                # It is not a store and not a byte conflict.
                name = "not_stored:already_present"
                counts[name] = counts.get(name, 0) + 1
    except Exception:
        with _LOCK:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            _counter_bucket(engine_id)["store_error"] += 1
    with _LOCK:
        _prepare_memory_cache()
        _remember_memory_value(memory_key, hot)
    return hot


@contextmanager
def _without_engine_cache():
    """Bypass nested engine cache calls for an uncached parent calculation."""
    token = _CACHE_BYPASSED.set(True)
    try:
        yield
    finally:
        _CACHE_BYPASSED.reset(token)


def load_cache_rows(
    path: str | Path,
    rows: Iterable[tuple[bytes, Any, str, str | int, str, int]],
) -> int:
    database = _expanded_cache_path(path)
    connection = _connect(database, readonly=False)
    assert connection is not None
    values = []
    for digest, hot, engine, version, source_box, row_seq in rows:
        if isinstance(hot, EngineCacheValue) and not hot.cacheable:
            raise ValueError("transient engine results are not cacheable")
        if (
            isinstance(hot, EngineCacheValue)
            and hot.refusal_reason is not None
            and not cacheable_refusal(hot)
        ):
            raise ValueError("refusal code is not cacheable")
        if not isinstance(digest, bytes) or not 4 <= len(digest) <= _NATURAL_KEY_LIMIT:
            raise ValueError("cache keys must be natural packed keys or 16-byte request digests")
        if engine not in ENGINE_CHEMISTRY_VERSION:
            raise ValueError(f"unknown engine {engine!r}")
        payload = hot.hot if isinstance(hot, EngineCacheValue) else hot
        if _is_refusal(payload) and not cacheable_refusal(live_hot(payload)):
            raise ValueError("refusal code is not cacheable")
        encoded, provenance = _encode_hot(payload)
        values.append(
            (
                digest,
                encoded,
                engine,
                str(version),
                source_box,
                int(row_seq),
                provenance,
            )
        )
    inserted_count = 0
    conflicts: dict[str, int] = {}
    connection.execute("BEGIN IMMEDIATE")
    try:
        for digest, value, engine, version, source_box, row_seq, provenance in values:
            decoded = species_from_natural_key(digest)
            # None keeps a blake2b key species-unknown. An empty set would
            # mean the request named nothing, which this loader cannot know.
            inserted, conflict = _insert_cache_row_if_absent(
                connection,
                digest,
                value,
                engine,
                version,
                source_box,
                row_seq,
                provenance=provenance,
                species=None if decoded is None else decoded,
            )
            inserted_count += int(inserted)
            if inserted:
                _record_bloom_key(connection, digest)
            if conflict:
                conflicts[engine] = conflicts.get(engine, 0) + 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    if conflicts:
        _record_engine_cache_counters(
            {engine: {"conflicts": count} for engine, count in conflicts.items()}
        )
    return inserted_count


def _cache_pair(path: Path) -> tuple[Path, Path]:
    return path, Path(str(path) + ".cold")


def _refuse_cache_aliases(outputs: Iterable[Path], inputs: Iterable[Path]) -> None:
    outputs, inputs = list(outputs), list(inputs)
    for index, output in enumerate(outputs):
        for other in inputs + outputs[:index]:
            if output.resolve() == other.resolve() or (
                output.exists() and other.exists() and output.samefile(other)
            ):
                raise EngineCacheAliasError(f"cache output aliases another file: {output} -> {other}")


def _cache_uri(path: Path, mode: str = "ro") -> str:
    return f"file:{quote(str(path.resolve()), safe='/')}?mode={mode}"


def _check_cold_schema(connection: sqlite3.Connection) -> None:
    expected_values = ["row_seq", "value", "engine_build_id"]
    found = [row[1] for row in connection.execute("PRAGMA cold.table_info(engine_values)")]
    if found != expected_values:
        raise EngineCacheSchemaMismatch("cold.engine_values", expected_values, found)
    expected_builds = ["id", "engine", "identity"]
    found_builds = [row[1] for row in connection.execute("PRAGMA cold.table_info(engine_builds)")]
    if found_builds != expected_builds:
        raise EngineCacheSchemaMismatch("cold.engine_builds", expected_builds, found_builds)
    namespace = connection.execute("PRAGMA cold.user_version").fetchone()[0]
    if namespace != _CACHE_SCHEMA_VERSION:
        raise EngineCacheSchemaMismatch(
            "cold.user_version", (str(_CACHE_SCHEMA_VERSION),), (str(namespace),)
        )


@contextmanager
def _read_cache(path: Path):
    with closing(sqlite3.connect(_cache_uri(path), uri=True, timeout=_TIMEOUT_MS / 1000)) as connection:
        cold = _cache_pair(path)[1]
        has_cold = cold.exists()
        if has_cold:
            connection.execute("ATTACH DATABASE ? AS cold", (_cache_uri(cold),))
        connection.execute("BEGIN")
        _check_cache_schema(connection)
        # Pin both read views, including schemas needed to interpret the HOT bytes.
        connection.execute("SELECT id,value FROM provenance LIMIT 0")
        connection.execute("SELECT source_box,next_seq FROM box_sequences LIMIT 0")
        if has_cold:
            _check_cold_schema(connection)
        yield connection, has_cold


def _cache_space(connection: sqlite3.Connection, schema: str, table: str) -> dict[str, Any]:
    count = connection.execute(f"SELECT count(*) FROM {schema}.{table}").fetchone()[0]
    size = (connection.execute(f"PRAGMA {schema}.page_count").fetchone()[0]
            * connection.execute(f"PRAGMA {schema}.page_size").fetchone()[0])
    return {"rows": count, "database_bytes": size, "bytes_per_row": size / count if count else None}


def engine_cache_stats(path: Path) -> dict[str, Any]:
    """Allocated SQLite bytes, including pages currently in WAL, per stored row."""
    with _read_cache(path) as (connection, has_cold):
        return {
            "file": str(path),
            "hot": _cache_space(connection, "main", _TABLE),
            "cold": _cache_space(connection, "cold", "engine_values") if has_cold else {
                "rows": 0, "database_bytes": 0, "bytes_per_row": None,
            },
        }


def snapshot_cache(
    live: str | os.PathLike[str], out: str | os.PathLike[str]
) -> dict[str, Any]:
    """Back up a live HOT/COLD pair into exclusively new, standalone files."""
    live, out = _expanded_cache_path(live), _expanded_cache_path(out)
    # Sync and seed both call this. The live file is opened read-write to
    # take its lock, and the destination is created. Either one being the
    # runner file in read-only mode refuses before any open.
    _refuse_runner_readonly_write(live)
    _refuse_runner_readonly_write(out)
    _refuse_cache_aliases(_cache_pair(out), _cache_pair(live))
    for path in _cache_pair(out):
        if path.exists() or path.is_symlink():
            raise FileExistsError(str(path))
    with ExitStack() as stack:
        # Serialize with the runtime's HOT transaction while pinning both views.
        # Release the lock before the potentially large backup; readers retain WAL snapshots.
        with closing(sqlite3.connect(_cache_uri(live, "rw"), uri=True, timeout=_TIMEOUT_MS / 1000)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            source, has_cold = stack.enter_context(_read_cache(live))
            lock.rollback()
        for name, path in (("main", out), ("cold", _cache_pair(out)[1])):
            if name == "cold" and not has_cold:
                continue
            with path.open("xb"):
                pass
            with closing(sqlite3.connect(path)) as destination:
                source.backup(destination, name=name)
                destination.execute("PRAGMA journal_mode=DELETE")
        if _cache_pair(out)[1].exists():
            with closing(sqlite3.connect(_cache_pair(out)[1])) as cold:
                cold.execute("ATTACH DATABASE ? AS hot", (_cache_uri(out),))
                # A failed live solve may have committed COLD before rolling back HOT.
                # COLD is keyed by row_seq. A value with no HOT row is the orphan
                # a failed solve left behind; snapshot still drops those.
                cold.execute(
                    _unmatched_cold_delete_sql(hot_schema="hot", cold_schema="main")
                )
                cold.commit()
    return engine_cache_stats(out)


def merge_cache_files(
    into: str | os.PathLike[str],
    inputs: list[str | os.PathLike[str]],
    *,
    vacuum: bool = False,
) -> dict[str, Any]:
    """Accumulate new per-box sequences without replacing a natural-key value."""
    into = _expanded_cache_path(into)
    inputs = [_expanded_cache_path(path) for path in inputs]
    # Reading an input, including the runner file, stays allowed. Writing
    # the runner file in read-only mode refuses before mkdir or sqlite open.
    _refuse_runner_readonly_write(into)
    report_path = Path(str(into) + ".conflicts.jsonl")
    _refuse_cache_aliases((*_cache_pair(into), report_path),
                         [part for path in inputs for part in _cache_pair(path)])
    counts = dict.fromkeys(("read", "inserted", "already_present", "conflicts"), 0)
    with ExitStack() as stack:
        # All inputs are validated before creating a master or a conflict report.
        sources = [stack.enter_context(_read_cache(path)) for path in inputs]
        into.parent.mkdir(parents=True, exist_ok=True)
        destination = stack.enter_context(closing(sqlite3.connect(into, timeout=_TIMEOUT_MS / 1000, uri=True)))
        _check_cache_schema(destination, allow_empty=True)
        cold_path = _cache_pair(into)[1]
        cold_exists = cold_path.exists()
        destination.execute("ATTACH DATABASE ? AS cold", (str(cold_path),))
        if cold_exists:
            _check_cold_schema(destination)
        _initialize_cache(destination)
        destination.execute("PRAGMA journal_mode=WAL")
        destination.execute("PRAGMA cold.journal_mode=WAL")
        _initialize_cold(destination, "cold")
        destination.execute(f"CREATE TABLE IF NOT EXISTS {_SYNC_WATERMARKS_TABLE} "
                            "(source_box TEXT PRIMARY KEY, row_seq INTEGER NOT NULL) WITHOUT ROWID")
        destination.commit()
        report = stack.enter_context(report_path.open("a", encoding="utf-8"))
        destination.execute("BEGIN IMMEDIATE")
        try:
            for source, has_cold in sources:
                for box, next_seq in source.execute("SELECT source_box,next_seq FROM box_sequences"):
                    if box not in ENGINE_CACHE_BOX_RANGES:
                        raise ValueError(f"unknown cache source box {box!r}")
                    watermark = destination.execute(
                        "SELECT row_seq FROM sync_watermarks WHERE source_box=?", (box,),
                    ).fetchone()
                    last = watermark[0] if watermark else 0
                    cursor = source.execute(
                        "SELECT e.key,e.value,e.engine,e.chem_version,e.row_seq,COALESCE(p.value,X'') "
                        "FROM engine_outputs e LEFT JOIN provenance p ON p.id=e.provenance_id "
                        "WHERE e.source_box=? AND e.row_seq>? ORDER BY e.row_seq", (box, last),
                    )
                    for key, value, engine, version, seq, provenance in cursor:
                        counts["read"] += 1
                        incoming_cold = source.execute(
                            "SELECT v.value, b.engine, b.identity "
                            "FROM cold.engine_values v "
                            "LEFT JOIN cold.engine_builds b ON b.id=v.engine_build_id "
                            "WHERE v.row_seq=?",
                            (seq,),
                        ).fetchone() if has_cold else None
                        if incoming_cold and (incoming_cold[1] is None or incoming_cold[2] is None):
                            raise EngineCacheSchemaMismatch(
                                "cold.engine_builds", ("engine", "identity"), ()
                            )
                        inserted, conflict = _insert_cache_row_if_absent(
                            destination, key, value, engine, version, box, seq, provenance=provenance,
                        )
                        kept_box, kept_seq = destination.execute(
                            "SELECT source_box,row_seq FROM engine_outputs WHERE key=?", (key,),
                        ).fetchone()
                        kept_cold = destination.execute(
                            "SELECT value FROM cold.engine_values WHERE row_seq=?", (kept_seq,),
                        ).fetchone()
                        conflict = conflict or bool(
                            incoming_cold and kept_cold and incoming_cold[0] != kept_cold[0]
                        )
                        counts["inserted"] += int(inserted)
                        counts["conflicts"] += int(conflict)
                        counts["already_present"] += int(not inserted and not conflict)
                        if conflict:
                            report.write(json.dumps({
                                "key": key.hex(), "master": {"source_box": kept_box, "row_seq": kept_seq},
                                "input": {"source_box": box, "row_seq": seq},
                            }, sort_keys=True) + "\n")
                        else:
                            # Same stored value. Fill species the master does not
                            # yet name, including a digest key recorded at write.
                            # A conflicting value does not contribute species:
                            # that would let expire delete the kept row.
                            names = source.execute(
                                f"SELECT species FROM {_SPECIES_TABLE} WHERE source_box=? AND row_seq=?",
                                (box, seq),
                            ).fetchall()
                            destination.executemany(
                                f"INSERT OR IGNORE INTO {_SPECIES_TABLE}"
                                "(source_box, row_seq, species) VALUES (?,?,?)",
                                [(kept_box, kept_seq, name) for (name,) in names],
                            )
                            if incoming_cold:
                                build_id = _ensure_build_id(
                                    destination, "cold", incoming_cold[1], incoming_cold[2]
                                )
                                destination.execute(
                                    "INSERT OR IGNORE INTO cold.engine_values "
                                    "(row_seq, value, engine_build_id) VALUES (?, ?, ?)",
                                    (kept_seq, incoming_cold[0], build_id),
                                )
                        last = seq
                    if last > (watermark[0] if watermark else 0):
                        destination.execute(
                            "INSERT INTO sync_watermarks VALUES (?,?) ON CONFLICT(source_box) "
                            "DO UPDATE SET row_seq=excluded.row_seq", (box, last),
                        )
                    # Seeded boxes must never reuse sequences, including deduplicated rows.
                    if next_seq > ENGINE_CACHE_BOX_RANGES[box][0]:
                        _advance_box_sequence(destination, box, next_seq - 1)
            report.flush()
            destination.commit()
        except Exception:
            destination.rollback()
            raise
        if vacuum:
            destination.execute("VACUUM main")
            destination.execute("VACUUM cold")
    return {**counts, **engine_cache_stats(into), "conflict_report": str(report_path), "vacuumed": vacuum}


def _create_species_table(connection: sqlite3.Connection) -> None:
    columns = next(
        spec for table, spec, _without in _CACHE_TABLE_DEFINITIONS if table == _SPECIES_TABLE
    )
    definitions = ", ".join(f"{name} {sql}" for name, sql in columns)
    definitions = f"{definitions}, {_CACHE_TABLE_CONSTRAINTS[_SPECIES_TABLE]}"
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {_SPECIES_TABLE} ({definitions}) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS engine_output_species_species "
        f"ON {_SPECIES_TABLE}(species)"
    )


def _forget_process_cache(path: Path) -> None:
    expanded = path.expanduser().resolve()
    with _LOCK:
        for memory_key in tuple(_MEMORY_CACHE):
            try:
                same = Path(str(memory_key[0])).expanduser().resolve() == expanded
            except OSError:
                same = False
            if same:
                del _MEMORY_CACHE[memory_key]
    if _CONNECTION_PATH is not None and _CONNECTION_PATH.expanduser().resolve() == expanded:
        _close_connections()


def _maintenance_connection(path: Path) -> tuple[sqlite3.Connection, bool]:
    """Open a schema-4 file for a manual tool. Does not migrate and does not expire on its own."""
    path = path.expanduser()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    connection = sqlite3.connect(str(path), timeout=_TIMEOUT_MS / 1000)
    try:
        _check_cache_schema(connection)
        cold_path = _cache_pair(path)[1]
        has_cold = cold_path.exists()
        if has_cold:
            if connection.in_transaction:
                connection.commit()
            connection.execute("ATTACH DATABASE ? AS cold", (str(cold_path.resolve()),))
            _check_cold_schema(connection)
        return connection, has_cold
    except BaseException:
        connection.close()
        raise


def _rows_by_engine(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        str(engine): int(count)
        for engine, count in connection.execute(
            f"SELECT engine, COUNT(*) FROM {_TABLE} GROUP BY engine ORDER BY engine"
        )
    }


def _unmatched_cold_delete_sql(*, hot_schema: str, cold_schema: str) -> str:
    """COLD values whose row_seq has no HOT row.

    Snapshot attaches the HOT file onto a COLD connection (``hot`` / ``main``).
    Maintenance attaches COLD onto the HOT connection (``main`` / ``cold``).
    Both are this predicate. A second copy would let one path keep a row
    the other drops.
    """

    if hot_schema not in {"main", "hot"} or cold_schema not in {"main", "cold"}:
        raise ValueError(f"unexpected cache schema {hot_schema!r}/{cold_schema!r}")
    return (
        f"DELETE FROM {cold_schema}.engine_values WHERE NOT EXISTS ("
        f"SELECT 1 FROM {hot_schema}.engine_outputs AS h "
        f"WHERE h.row_seq = {cold_schema}.engine_values.row_seq)"
    )


def _sweep_orphan_cold(connection: sqlite3.Connection) -> int:
    """Delete COLD values with no HOT row. Returns how many went.

    A failed paired delete leaves this residue. It is not a hit. The next
    expire, prune, or drop removes it, including when that call matches
    no HOT rows. Dry-run callers do not call this.
    """

    if connection.in_transaction:
        connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            _unmatched_cold_delete_sql(hot_schema="main", cold_schema="cold")
        )
        swept = int(connection.execute("SELECT changes()").fetchone()[0])
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return swept


def _delete_cold_values(
    connection: sqlite3.Connection, pairs: list[tuple[str, int]]
) -> None:
    # row_seq is unique across boxes because the box ranges do not overlap.
    connection.executemany(
        "DELETE FROM cold.engine_values WHERE row_seq=?",
        [(row_seq,) for _source_box, row_seq in pairs],
    )


def _delete_cached_rows(
    connection: sqlite3.Connection,
    has_cold: bool,
    pairs: list[tuple[str, int]],
    cold_delete: Callable[[sqlite3.Connection, list[tuple[str, int]]], None] | None = None,
) -> None:
    """Delete the HOT row and its species, then the COLD value.

    Two commits. SQLite does not guarantee that one COMMIT updates both
    a WAL database and an attached database: a crash can change one file
    and leave the other. The HOT row is what a hit serves, so it commits
    first. A crash between the commits leaves a COLD value with no HOT
    row. That value is not served. The caller sweeps it on this call when
    the paired delete commits, and on the next expire, prune, or drop
    when the paired delete raises. Deleting COLD first would leave a HOT
    row whose COLD value is already gone, and that row would still be a
    hit: a stale serve. ``cold_delete`` is only the drop_chemistry_version
    seam for the paired delete. The orphan sweep does not use it, so a
    test that injects a failure there still leaves the orphan for the
    retry. An empty ``pairs`` list returns without writing; the caller
    still sweeps.
    """

    if not pairs:
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.executemany(
            f"DELETE FROM {_SPECIES_TABLE} WHERE source_box=? AND row_seq=?",
            pairs,
        )
        connection.executemany(
            f"DELETE FROM {_TABLE} WHERE source_box=? AND row_seq=?",
            pairs,
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    if not has_cold:
        return
    connection.execute("BEGIN IMMEDIATE")
    try:
        if cold_delete is None:
            _delete_cold_values(connection, pairs)
        else:
            cold_delete(connection, pairs)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _apply_maintenance_deletes(
    connection: sqlite3.Connection,
    path: Path,
    has_cold: bool,
    pairs: list[tuple[str, int]],
    *,
    cold_delete: Callable[[sqlite3.Connection, list[tuple[str, int]]], None] | None = None,
    vacuum: bool,
) -> int:
    """Delete matched HOT rows, then sweep COLD rows that have no HOT row.

    Returns how many orphan COLD rows the sweep removed. Nothing on the
    read or write path calls this. A failure of the paired COLD delete
    does not sweep in this call: the HOT rows are already gone, so the
    value is not served, and the next call's sweep removes the orphan.
    The drop injection stays on the paired delete, so the retry can sweep
    without running that hook again.
    """

    if not pairs and not has_cold:
        return 0
    try:
        if pairs:
            _delete_cached_rows(connection, has_cold, pairs, cold_delete=cold_delete)
        swept = _sweep_orphan_cold(connection) if has_cold else 0
    except BaseException:
        _forget_process_cache(path)
        raise
    if pairs or swept:
        if vacuum:
            connection.execute("VACUUM main")
            if has_cold:
                connection.execute("VACUUM cold")
        _forget_process_cache(path)
    return swept


def _migrate_cold_v3(connection: sqlite3.Connection) -> int:
    """Re-link schema-3 COLD values by HOT row_seq. Values are copied unchanged."""
    connection.execute(
        "CREATE TABLE oldcold.engine_builds ("
        "id INTEGER PRIMARY KEY, engine TEXT NOT NULL, identity TEXT NOT NULL, "
        "UNIQUE(engine, identity))"
    )
    connection.execute(
        "CREATE TABLE oldcold.engine_values_v4 ("
        "row_seq INTEGER PRIMARY KEY, value BLOB NOT NULL, "
        "engine_build_id INTEGER NOT NULL) WITHOUT ROWID"
    )
    linked = connection.execute(
        "SELECT h.engine, h.row_seq, c.value FROM oldcold.engine_values c "
        "JOIN engine_outputs h ON h.key = c.key"
    ).fetchall()
    build_ids: dict[str, int] = {}
    for engine, row_seq, value in linked:
        engine_name = str(engine)
        if engine_name not in build_ids:
            build_ids[engine_name] = _ensure_build_id(
                connection, "oldcold", engine_name, _UNKNOWN_PRE_MIGRATION_BUILD
            )
        connection.execute(
            "INSERT INTO oldcold.engine_values_v4(row_seq, value, engine_build_id) "
            "VALUES (?,?,?)",
            (int(row_seq), value, build_ids[engine_name]),
        )
    unlinked_rows = connection.execute(
        "SELECT c.value FROM oldcold.engine_values c WHERE NOT EXISTS ("
        "SELECT 1 FROM engine_outputs h WHERE h.key = c.key)"
    ).fetchall()
    if unlinked_rows:
        # No HOT row has no row_seq. Negative sequences are outside every box
        # range, so they cannot be served as hits. Migration does not delete
        # the value. Snapshot drops a COLD row with no HOT row, and the next
        # expire, prune, or drop sweeps it. The value stays until that call.
        build_id = _ensure_build_id(
            connection, "oldcold", "unlinked", _UNKNOWN_PRE_MIGRATION_BUILD
        )
        for index, (value,) in enumerate(unlinked_rows, start=1):
            connection.execute(
                "INSERT INTO oldcold.engine_values_v4(row_seq, value, engine_build_id) "
                "VALUES (?,?,?)",
                (-index, value, build_id),
            )
    connection.execute("DROP TABLE oldcold.engine_values")
    connection.execute("ALTER TABLE oldcold.engine_values_v4 RENAME TO engine_values")
    connection.execute(f"PRAGMA oldcold.user_version={_CACHE_SCHEMA_VERSION}")
    return len(unlinked_rows)


def _payload_bytes(path: Path) -> dict[str, int]:
    with closing(sqlite3.connect(_cache_uri(path), uri=True, timeout=_TIMEOUT_MS / 1000)) as connection:
        hot_payload = connection.execute(
            "SELECT COALESCE(SUM(length(key) + length(value)), 0) FROM engine_outputs"
        ).fetchone()[0]
        report = {"hot_payload_bytes": int(hot_payload), "cold_value_bytes": 0}
        cold = _cache_pair(path)[1]
        if cold.exists():
            connection.execute("ATTACH DATABASE ? AS cold", (_cache_uri(cold),))
            report["cold_value_bytes"] = int(connection.execute(
                "SELECT COALESCE(SUM(length(value)), 0) FROM cold.engine_values"
            ).fetchone()[0])
        return report


def _fsync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _copy_database_file(source: Path, destination: Path) -> None:
    """Copy one database. DELETE journal mode keeps the bytes in the main file."""

    for suffix in ("", "-wal", "-shm"):
        side = Path(str(destination) + suffix)
        if side.exists() or side.is_symlink():
            side.unlink()
    with closing(sqlite3.connect(str(source), timeout=_TIMEOUT_MS / 1000)) as src:
        with closing(sqlite3.connect(str(destination), timeout=_TIMEOUT_MS / 1000)) as dst:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")


# The open and recover paths both match this sentence. It names the only
# repair: a later migrate of an empty schema-4 file would say already_current
# and leave the accumulated rows unserved.
_INTERRUPTED_MIGRATE = "interrupted migrate; run engine_cache_sync.py migrate --recover"


def _raise_interrupted_migrate(found: str) -> None:
    raise EngineCacheSchemaMismatch("migration", (_INTERRUPTED_MIGRATE,), (found,))


def _migration_names(path: Path) -> dict[str, Path]:
    """Live pair plus the side names publish and recover share.

    One owner for the suffixes. A second spelling would let recover look
    beside a different file than the one publish wrote.
    """

    cold = _cache_pair(path)[1]
    return {
        "hot_bak": path.with_name(path.name + ".v3.bak"),
        "hot_new": path.with_name(path.name + ".v4-new"),
        "cold": cold,
        "cold_bak": cold.with_name(cold.name + ".v3.bak"),
        "cold_new": cold.with_name(cold.name + ".v4-new"),
    }


def _migration_leftovers(path: Path) -> tuple[Path, ...]:
    names = _migration_names(path)
    return tuple(
        candidate
        for candidate in (
            names["hot_bak"],
            names["hot_new"],
            names["cold_bak"],
            names["cold_new"],
        )
        if candidate.exists() or candidate.is_symlink()
    )


def _read_user_version(path: Path) -> int | None:
    """user_version through a read-only URI.

    ``sqlite3.connect(path)`` would create a missing file. A read-only URI
    does not, and it does not flip journal_mode to WAL.
    """

    if not path.is_file():
        return None
    try:
        with closing(
            sqlite3.connect(_cache_uri(path), uri=True, timeout=_TIMEOUT_MS / 1000)
        ) as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error:
        return None
    return None if row is None else int(row[0])


def _pair_versions_disagree(path: Path) -> bool:
    """True when one side is schema 4 and the other is not.

    A schema-3 hot file beside a cold file that never set user_version is
    the unmigrated pair, not a torn publish. Only a current side opposite
    an older side is the crash between the two replaces.
    """

    names = _migration_names(path)
    if not path.is_file() or not names["cold"].is_file():
        return False
    hot_version = _read_user_version(path)
    cold_version = _read_user_version(names["cold"])
    if hot_version == cold_version:
        return False
    return hot_version == _CACHE_SCHEMA_VERSION or cold_version == _CACHE_SCHEMA_VERSION


def _refuse_interrupted_cache_open(path: Path) -> None:
    """Fail closed before ``sqlite3.connect`` can create an empty cache.

    Checked only when a side file is present, so a normal open does not
    probe user_version twice. A missing live name is not created. An empty
    live file is not initialized. A schema-4 cold beside a schema-3 hot is
    not served.
    """

    if not _migration_leftovers(path):
        return
    if not path.is_file():
        _raise_interrupted_migrate("no live cache file")
    if _read_user_version(path) in (None, 0):
        _raise_interrupted_migrate("empty live cache beside migration leftovers")
    if _pair_versions_disagree(path):
        _raise_interrupted_migrate("hot and cold schema versions differ")


def _connection_main_path(connection: sqlite3.Connection) -> Path | None:
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        return None
    return Path(str(row[2]))


def _initialize_cache_refuses_leftovers(connection: sqlite3.Connection) -> None:
    """Do not create a schema beside publish leftovers.

    ``_connect`` refuses before it creates a file. Merge and any other
    caller that already holds a connection hit this gate. A finished
    schema-4 file may still be opened while ``*.v3.bak`` names remain:
    those are the originals of a publish that has not unlinked them yet.
    """

    path = _connection_main_path(connection)
    if path is None or not _migration_leftovers(path):
        return
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    has_outputs = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (_TABLE,),
    ).fetchone()
    if version == _CACHE_SCHEMA_VERSION and has_outputs:
        return
    _raise_interrupted_migrate("migration leftovers beside an uninitialized cache")


def _unlink_named(paths: Iterable[Path]) -> None:
    for candidate in paths:
        if candidate.exists() or candidate.is_symlink():
            candidate.unlink()


def _unlink_migration_copies(path: Path) -> None:
    names = _migration_names(path)
    _unlink_named((names["hot_new"], names["cold_new"]))


def _unlink_migration_backups(path: Path) -> None:
    names = _migration_names(path)
    _unlink_named((names["hot_bak"], names["cold_bak"]))


def _stored_hot_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with closing(
            sqlite3.connect(_cache_uri(path), uri=True, timeout=_TIMEOUT_MS / 1000)
        ) as connection:
            found = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='engine_outputs'"
            ).fetchone()
            if not found:
                return 0
            return int(connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0])
    except sqlite3.Error:
        return 0


def _v4_new_is_complete(path: Path) -> bool:
    """True when the copies are a finished schema-4 pair.

    user_version becomes 4 at the end of the transform. A crash during the
    copy leaves 3, 0, or an unreadable file. Publishing that would replace
    the live pair with a partial one. A cold side is any of the live cold
    name, its backup, or its copy. Hot-only caches have none of those.
    """

    names = _migration_names(path)
    if _read_user_version(names["hot_new"]) != _CACHE_SCHEMA_VERSION:
        return False
    cold_side = (
        names["cold"].exists() or names["cold_bak"].exists() or names["cold_new"].exists()
    )
    if not cold_side:
        return True
    return _read_user_version(names["cold_new"]) == _CACHE_SCHEMA_VERSION


def _should_finish_publish(path: Path) -> bool:
    if not _v4_new_is_complete(path):
        return False
    if not path.is_file():
        return True
    # The live names already hold a consistent schema-4 pair. Replacing
    # them with the copy would drop rows written after that copy was made.
    if (
        _read_user_version(path) == _CACHE_SCHEMA_VERSION
        and not _pair_versions_disagree(path)
        and _stored_hot_rows(path) > 0
    ):
        return False
    return True


def _should_restore_migration(path: Path) -> bool:
    names = _migration_names(path)
    if not (names["hot_bak"].exists() or names["cold_bak"].exists()):
        return False
    if not path.is_file():
        return True
    version = _read_user_version(path)
    if version in (None, 0):
        return True
    if _pair_versions_disagree(path):
        return True
    # Both replaces finished: the live pair is schema 4 and still has its
    # rows, and the backups are the pre-migrate inodes. Restoring would
    # undo that publish. An empty schema-4 file beside a backup that has
    # rows is the cache the rename gap used to create; the backup is the pair.
    if _stored_hot_rows(path) == 0 and _stored_hot_rows(names["hot_bak"]) > 0:
        return True
    return False


def _link_backup(live: Path, backup: Path) -> None:
    """Add ``backup`` as a second name for ``live``. The live name stays.

    ``os.replace`` of the new file onto ``live`` changes one directory
    entry. The backup name keeps the original inode, so a crash never
    leaves the live name absent. A backup that already names that inode is
    recover calling publish again. A backup that names a different inode
    is not overwritten: that would drop the original.
    """

    if backup.is_symlink() or live.is_symlink():
        _raise_interrupted_migrate(f"{backup.name} is a symlink")
    if backup.exists():
        if live.exists() and os.path.samefile(live, backup):
            return
        if not live.exists():
            return
        _raise_interrupted_migrate(f"{backup.name} is not a hard link of the live file")
    if live.exists():
        os.link(live, backup)


def _restore_backup_name(live: Path, backup: Path) -> None:
    """Point the live name at the backup inode.

    When the two names are already one inode, ``os.replace`` of a name onto
    itself fails on macOS. The live name is already the original, so only
    the extra backup name is removed.
    """

    if backup.is_symlink():
        _raise_interrupted_migrate(f"{backup.name} is a symlink")
    if not backup.exists():
        return
    if live.exists() and os.path.samefile(live, backup):
        backup.unlink()
        return
    os.replace(backup, live)


def _restore_migration_backups(path: Path) -> None:
    """Put the original inodes back at the live names.

    Cold is restored first. Restoring hot first while cold is already the
    new file would leave a schema-4 hot beside a schema-3 cold, which is
    the pair this publish order exists to avoid.
    """

    names = _migration_names(path)
    if not names["hot_bak"].exists() and not names["cold_bak"].exists():
        _raise_interrupted_migrate("no schema-3 backup to restore")
    if names["cold_bak"].exists():
        _restore_backup_name(names["cold"], names["cold_bak"])
    if names["hot_bak"].exists():
        _restore_backup_name(path, names["hot_bak"])
    elif not path.is_file():
        _raise_interrupted_migrate("no hot backup to restore")
    _fsync_path(path.parent)


def _discard_unpublished_migration(
    live: Path, hot_new: Path, cold_new: Path | None
) -> None:
    """Delete ``.v4-new`` copies only when publish has not linked a backup.

    A ``*.v3.bak`` hard link means publish started. Those copies are what
    ``migrate --recover`` finishes. Deleting them would leave no schema-4
    pair. A failure before any link still has the untouched live pair, so
    the copies are safe to delete. A missing live name is left alone:
    nothing here invents a replacement file.
    """

    if not live.exists():
        return
    names = _migration_names(live)
    if names["hot_bak"].exists() or names["cold_bak"].exists():
        return
    cold = names["cold"]
    for side in (hot_new, cold_new):
        if side is None or not (side.exists() or side.is_symlink()):
            continue
        if side.is_symlink():
            side.unlink()
            continue
        try:
            resolved = side.resolve()
        except OSError:
            side.unlink()
            continue
        if resolved == live.resolve():
            continue
        if cold.exists() and resolved == cold.resolve():
            continue
        side.unlink()


def _publish_checkpoint(step: str) -> None:
    """Crash seam after ``backups``, ``cold``, or ``hot``.

    Production calls these three names and continues. A test replaces this
    function and raises. That raise is a process death after the directory
    update the name stands for, and before the next one.
    """

    if step not in {"backups", "cold", "hot"}:
        _raise_interrupted_migrate(f"unknown publish step {step}")


def _publish_migrated_pair(live: Path, hot_new: Path, cold_new: Path | None) -> None:
    """Fsync the schema-4 copies and put them at the live names.

    Backups are hard links, made before either live name changes, and the
    directory is fsynced before a replace. ``os.replace`` then installs
    cold, then hot, over the names that already exist. Both live names are
    present at every instant. A crash after the links still has the original
    pair at the live names. A crash after the cold replace has a new cold
    and the original hot; opening that pair fails closed. A crash after the
    hot replace has the new pair at the live names. Backup names are removed
    only after both replaces have returned.
    """

    if not hot_new.is_file():
        _raise_interrupted_migrate("schema-4 hot copy is missing")
    _fsync_path(hot_new)
    names = _migration_names(live)
    if cold_new is not None:
        if not cold_new.is_file():
            _raise_interrupted_migrate("schema-4 cold copy is missing")
        _fsync_path(cold_new)
        _link_backup(names["cold"], names["cold_bak"])
    _link_backup(live, names["hot_bak"])
    _fsync_path(live.parent)
    _publish_checkpoint("backups")
    if cold_new is not None:
        os.replace(cold_new, names["cold"])
        _publish_checkpoint("cold")
    os.replace(hot_new, live)
    _publish_checkpoint("hot")
    _fsync_path(live.parent)
    _unlink_migration_backups(live)


def _transform_v3_copy(hot_path: Path, cold_path: Path | None) -> dict[str, Any]:
    """Schema-4 transform of a copied pair. The live files are not opened."""

    connection = sqlite3.connect(str(hot_path), timeout=_TIMEOUT_MS / 1000)
    try:
        if connection.in_transaction:
            connection.commit()
        before: dict[str, Any] = {
            "hot": _cache_space(connection, "main", _TABLE),
            "hot_payload_bytes": int(connection.execute(
                "SELECT COALESCE(SUM(length(key) + length(value)), 0) FROM engine_outputs"
            ).fetchone()[0]),
        }
        has_cold = cold_path is not None
        if has_cold:
            connection.execute("ATTACH DATABASE ? AS oldcold", (str(cold_path.resolve()),))
            cold_columns = [
                row[1] for row in connection.execute("PRAGMA oldcold.table_info(engine_values)")
            ]
            if cold_columns != ["key", "value", "input_fingerprint"]:
                raise EngineCacheSchemaMismatch(
                    "cold.engine_values",
                    ("key", "value", "input_fingerprint"),
                    tuple(cold_columns),
                )
            before["cold"] = _cache_space(connection, "oldcold", "engine_values")
            before["cold_value_bytes"] = int(connection.execute(
                "SELECT COALESCE(SUM(length(value)), 0) FROM oldcold.engine_values"
            ).fetchone()[0])
            before["cold_key_bytes"] = int(connection.execute(
                "SELECT COALESCE(SUM(length(key)), 0) FROM oldcold.engine_values"
            ).fetchone()[0])
            before["cold_fingerprint_bytes"] = int(connection.execute(
                "SELECT COALESCE(SUM(COALESCE(length(input_fingerprint), 0)), 0) "
                "FROM oldcold.engine_values"
            ).fetchone()[0])
        else:
            before["cold"] = {"rows": 0, "database_bytes": 0, "bytes_per_row": None}
            before["cold_value_bytes"] = 0
            before["cold_key_bytes"] = 0
            before["cold_fingerprint_bytes"] = 0
        before_hot_keys = [
            (bytes(key), bytes(value))
            for key, value in connection.execute(
                "SELECT key, value FROM engine_outputs ORDER BY row_seq"
            )
        ]
        connection.execute("BEGIN")
        _create_species_table(connection)
        natural_keys = 0
        digest_keys = 0
        skipped_unreadable_keys = 0
        species_unknown_by_engine: dict[str, int] = {}
        for source_box, row_seq, key, engine in connection.execute(
            "SELECT source_box, row_seq, key, engine FROM engine_outputs"
        ):
            try:
                species = species_from_natural_key(bytes(key))
            except EngineCacheSpeciesError:
                # A flag other than 0x00 or 0xff is not a request we can
                # read. Skip the row and keep the migration. Expire does
                # not treat it as a digest key.
                skipped_unreadable_keys += 1
                continue
            if species is None:
                digest_keys += 1
                engine_name = str(engine)
                species_unknown_by_engine[engine_name] = (
                    species_unknown_by_engine.get(engine_name, 0) + 1
                )
                continue
            natural_keys += 1
            _write_output_species(connection, str(source_box), int(row_seq), species)
        unlinked = _migrate_cold_v3(connection) if has_cold else 0
        after_hot_keys = [
            (bytes(key), bytes(value))
            for key, value in connection.execute(
                "SELECT key, value FROM engine_outputs ORDER BY row_seq"
            )
        ]
        if after_hot_keys != before_hot_keys:
            raise EngineCacheSchemaMismatch(
                "engine_outputs",
                ("key", "value"),
                ("changed during migrate; rerun with no writer",),
            )
        connection.execute(f"PRAGMA user_version={_CACHE_SCHEMA_VERSION}")
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error:
            pass
        connection.close()
        raise
    connection.close()
    return {
        "migrated": True,
        "already_current": False,
        "schema_version": _CACHE_SCHEMA_VERSION,
        "natural_keys": natural_keys,
        "digest_keys": digest_keys,
        "species_unknown_by_engine": species_unknown_by_engine,
        "skipped_unreadable_keys": skipped_unreadable_keys,
        "unlinked_cold": unlinked,
        "before": before,
    }


def migrate_cache_file(path: str | Path, *, vacuum: bool = True) -> dict[str, Any]:
    """Convert a schema-3 HOT/COLD pair by publishing a schema-4 copy.

    The transform runs on ``*.v4-new`` files. Publish hard-links the live
    names to ``*.v3.bak`` and then ``os.replace``s the copies onto those
    names, cold first, so the live names are never absent. A crash leaves
    the original pair, a new cold beside the original hot, or the new pair.
    It does not leave a schema-4 hot file beside a schema-3 cold file.
    HOT key and value bytes stay. Leftovers refuse this call; ``recover``
    finishes or restores them.

    Old fingerprints are dropped. engine_build_id is the explicit
    ``unknown-pre-migration`` row: a schema-3 file did not record the open
    identity per row. Natural keys gain species rows. A blake2b key does
    not: its request cannot be recovered, so it is species-unknown and
    ``expire --species`` on that engine deletes it.

    Live warm files are re-warmed with this code instead of migrated, so
    those rows record the species the request named. Vacuum reclaims the
    freed key and fingerprint pages. ``vacuum=False`` skips that.
    """

    path = Path(path).expanduser()
    if _migration_leftovers(path):
        _raise_interrupted_migrate("migration leftovers are beside this cache")
    if not path.is_file():
        raise FileNotFoundError(str(path))
    connection = sqlite3.connect(str(path), timeout=_TIMEOUT_MS / 1000)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == _CACHE_SCHEMA_VERSION:
            _check_cache_schema(connection)
            cold_path = _cache_pair(path)[1]
            if cold_path.exists():
                if connection.in_transaction:
                    connection.commit()
                connection.execute("ATTACH DATABASE ? AS cold", (str(cold_path.resolve()),))
                _check_cold_schema(connection)
            connection.close()
            connection = None
            if vacuum:
                _vacuum_cache_files(path)
            stats = engine_cache_stats(path)
            return {
                "migrated": False,
                "already_current": True,
                "schema_version": _CACHE_SCHEMA_VERSION,
                "vacuumed": bool(vacuum),
                **stats,
            }
        if version != 3:
            raise EngineCacheSchemaMismatch(
                "user_version",
                ("3", str(_CACHE_SCHEMA_VERSION)),
                (str(version),),
            )
        found = tuple(row[1] for row in connection.execute("PRAGMA table_info(engine_outputs)"))
        if found != _V3_OUTPUT_COLUMNS:
            raise EngineCacheSchemaMismatch("engine_outputs", _V3_OUTPUT_COLUMNS, found)
    except BaseException:
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            connection.close()
        raise
    connection.close()

    hot_new = path.with_name(path.name + ".v4-new")
    cold_live = _cache_pair(path)[1]
    cold_new = (
        cold_live.with_name(cold_live.name + ".v4-new") if cold_live.is_file() else None
    )
    try:
        _copy_database_file(path, hot_new)
        if cold_new is not None:
            _copy_database_file(cold_live, cold_new)
        migrated = _transform_v3_copy(hot_new, cold_new)
        _publish_migrated_pair(path, hot_new, cold_new)
    except BaseException:
        _discard_unpublished_migration(path, hot_new, cold_new)
        raise
    if vacuum:
        _vacuum_cache_files(path)
    return {
        **migrated,
        "after": engine_cache_stats(path),
        "after_payload": _payload_bytes(path),
        "vacuumed": bool(vacuum),
    }


def recover_cache_file(path: str | Path) -> dict[str, Any]:
    """Finish or undo an interrupted schema-4 publish. Idempotent.

    A complete ``*.v4-new`` pair is published when the live names are
    missing, empty, or still the old schema. A torn pair, a missing live
    file, or an empty schema-4 file beside a backup that has rows is
    restored from ``*.v3.bak``. When the live pair is already one schema
    and still has its rows, backup names are leftovers of a finished
    publish: removing them keeps those rows. Restoring in that case would
    undo replaces that both completed. A second call finds nothing to do.
    """

    path = Path(path).expanduser()
    if not path.is_file() and not _migration_leftovers(path):
        raise FileNotFoundError(str(path))
    if _should_finish_publish(path):
        names = _migration_names(path)
        cold_new = names["cold_new"] if names["cold_new"].is_file() else None
        _publish_migrated_pair(path, names["hot_new"], cold_new)
        action = "published"
    elif _should_restore_migration(path):
        _restore_migration_backups(path)
        _unlink_migration_copies(path)
        _fsync_path(path.parent)
        action = "restored"
    elif _migration_leftovers(path):
        _unlink_migration_copies(path)
        _unlink_migration_backups(path)
        _fsync_path(path.parent)
        action = "removed-leftovers"
    else:
        action = "nothing"
    return {
        "action": action,
        "schema_version": _read_user_version(path),
        "hot_rows": _stored_hot_rows(path),
        "file": str(path),
    }


def _vacuum_cache_files(path: Path) -> None:
    """Reclaim free pages in the HOT file and, when present, the COLD file."""

    connection = sqlite3.connect(str(path), timeout=_TIMEOUT_MS / 1000)
    try:
        connection.execute("VACUUM")
    finally:
        connection.close()
    cold = _cache_pair(path)[1]
    if not cold.exists():
        return
    connection = sqlite3.connect(str(cold), timeout=_TIMEOUT_MS / 1000)
    try:
        connection.execute("VACUUM")
    finally:
        connection.close()


def expire_cache_file(
    path: str | Path,
    *,
    engine: str,
    species: tuple[str, ...] | None = None,
    all_rows: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    """Delete one engine's rows whose recorded inputs include a named species.

    Dry-run unless ``apply``. Names are matched casefolded, as stored.
    This function does not expand oxide aliases. A blake2b key with no
    species rows is included for any species list: its inputs are unknown,
    so a superseded species would otherwise keep being served. A natural
    key that named no species is not that case, and neither is a blake2b
    key that stored the empty known-empty marker. ``all_rows`` deletes
    every row of that engine. ``apply`` also deletes COLD values that
    have no HOT row, so a failed earlier delete can finish. A dry run
    does not. Nothing on the read or write path calls this.
    """

    if engine not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown engine {engine!r}")
    if all_rows == (species is not None):
        raise ValueError("expire needs exactly one of species or all_rows")
    names: tuple[str, ...] = ()
    if species is not None:
        if not species or any(not isinstance(name, str) or not name.strip() for name in species):
            raise ValueError("expire species must be non-empty names")
        names = tuple(dict.fromkeys(_casefolded_species_name(name) for name in species))
    path = Path(path).expanduser()
    connection, has_cold = _maintenance_connection(path)
    try:
        totals = _rows_by_engine(connection)
        totals.setdefault(engine, 0)
        if all_rows:
            matched = connection.execute(
                f"SELECT source_box, row_seq FROM {_TABLE} WHERE engine=?",
                (engine,),
            ).fetchall()
        else:
            placeholders = ",".join("?" * len(names))
            matched = connection.execute(
                f"SELECT DISTINCT e.source_box, e.row_seq FROM {_TABLE} e "
                f"JOIN {_SPECIES_TABLE} s "
                "ON s.source_box=e.source_box AND s.row_seq=e.row_seq "
                f"WHERE e.engine=? AND s.species IN ({placeholders})",
                (engine, *names),
            ).fetchall()
        pairs = [(str(source_box), int(row_seq)) for source_box, row_seq in matched]
        # A pre-migration digest key has no species rows. Any --species on
        # its engine expires it. A natural key with no names is not unknown.
        unknown_pairs: list[tuple[str, int]] = []
        unclassifiable_rows = 0
        if not all_rows:
            unknown_pairs, unclassifiable_rows = _species_unknown_pairs(connection, engine)
            already = set(pairs)
            pairs.extend(pair for pair in unknown_pairs if pair not in already)
        swept = 0
        if apply:
            swept = _apply_maintenance_deletes(
                connection, path, has_cold, pairs, vacuum=True
            )
        matched_by_engine = {name: 0 for name in totals}
        matched_by_engine[engine] = len(pairs)
        return {
            "command": "expire",
            "apply": bool(apply),
            "dry_run": not apply,
            "engine": engine,
            "species": list(names) if species is not None else None,
            "all_rows": bool(all_rows),
            "rows_by_engine": totals,
            "matched_by_engine": matched_by_engine,
            "deleted_by_engine": {
                name: (count if apply else 0) for name, count in matched_by_engine.items()
            },
            "vacuumed": bool(apply and (pairs or swept)),
            "species_unknown_rows": None if all_rows else len(unknown_pairs),
            "unclassifiable_rows": None if all_rows else unclassifiable_rows,
        }
    finally:
        connection.close()


def _species_unknown_pairs(
    connection: sqlite3.Connection, engine: str
) -> tuple[list[tuple[str, int]], int]:
    """Digest keys of one engine that recorded no species, and the rest.

    ``species_from_natural_key`` returns None only for the blake2b fallback.
    Migrate uses that same result to count digest keys and to skip species
    rows. A store of a blake2b request that named nothing writes an empty
    species marker, so that row has a side row and is not selected here.
    A natural key that named no species returns an empty set and stays.
    A flag other than 0x00 or 0xff is counted and left. Deleting it would
    drop a row this code cannot show is the named species, and raising
    would abort the expire before any other row was removed.
    """

    rows = connection.execute(
        f"SELECT e.source_box, e.row_seq, e.key FROM {_TABLE} e "
        f"WHERE e.engine=? AND NOT EXISTS ("
        f"SELECT 1 FROM {_SPECIES_TABLE} s "
        "WHERE s.source_box=e.source_box AND s.row_seq=e.row_seq)",
        (engine,),
    ).fetchall()
    found: list[tuple[str, int]] = []
    unclassifiable = 0
    for source_box, row_seq, key in rows:
        try:
            species = species_from_natural_key(bytes(key))
        except EngineCacheSpeciesError:
            unclassifiable += 1
            continue
        if species is None:
            found.append((str(source_box), int(row_seq)))
    return found, unclassifiable


def prune_cache_file(path: str | Path, *, apply: bool = False) -> dict[str, Any]:
    """Delete rows whose chemistry version is not the current one.

    The build id is not a predicate. An engine name this code does not
    version is left in place: it is not known to be disused. Dry-run unless
    ``apply``. ``apply`` also deletes COLD values that have no HOT row.
    Nothing on the read or write path calls this.
    """

    path = Path(path).expanduser()
    connection, has_cold = _maintenance_connection(path)
    try:
        totals = _rows_by_engine(connection)
        known = tuple(ENGINE_CHEMISTRY_VERSION)
        placeholders = ",".join("?" * len(known))
        unknown = {
            str(engine): int(count)
            for engine, count in connection.execute(
                f"SELECT engine, COUNT(*) FROM {_TABLE} "
                f"WHERE engine NOT IN ({placeholders}) GROUP BY engine",
                known,
            )
        }
        matched_by_engine = {engine: 0 for engine in totals}
        pairs: list[tuple[str, int]] = []
        for engine, version in ENGINE_CHEMISTRY_VERSION.items():
            found = connection.execute(
                f"SELECT source_box, row_seq FROM {_TABLE} "
                "WHERE engine=? AND chem_version!=?",
                (engine, str(version)),
            ).fetchall()
            matched_by_engine[engine] = len(found)
            pairs.extend((str(source_box), int(row_seq)) for source_box, row_seq in found)
        swept = 0
        if apply:
            swept = _apply_maintenance_deletes(
                connection, path, has_cold, pairs, vacuum=True
            )
        return {
            "command": "prune",
            "apply": bool(apply),
            "dry_run": not apply,
            "rows_by_engine": totals,
            "matched_by_engine": matched_by_engine,
            "deleted_by_engine": {
                engine: (count if apply else 0) for engine, count in matched_by_engine.items()
            },
            "skipped_unknown_engine": unknown,
            "vacuumed": bool(apply and (pairs or swept)),
        }
    finally:
        connection.close()


def _drop_attached_cold(
    connection: sqlite3.Connection, pairs: list[tuple[str, int]]
) -> None:
    """Delete COLD values inside the caller's open COLD transaction.

    The HOT and species deletes are already committed. A failure here
    rolls back only this COLD delete, so the file keeps COLD values
    with no HOT row. Those values are not hits. The next expire, prune,
    or drop sweeps them; this hook does not, so an injected failure
    stays visible to that retry. One COMMIT is not atomic across a WAL
    database and an attached database, so the two deletes are not one
    transaction.
    """

    _delete_cold_values(connection, pairs)


def drop_chemistry_version(engine_id: str, chem_version: str | int) -> int:
    path_value = _configured_engine_cache_path()
    if not path_value:
        return 0
    path = _expanded_cache_path(path_value)
    connection = _connect(path, readonly=False)
    assert connection is not None
    pairs = [
        (str(source_box), int(row_seq))
        for source_box, row_seq in connection.execute(
            f"SELECT source_box, row_seq FROM {_TABLE} WHERE engine=? AND chem_version=?",
            (engine_id, str(chem_version)),
        )
    ]
    cold_path = _cache_pair(path)[1]
    has_cold = cold_path.exists()
    # No HOT rows and no COLD file: nothing to sweep. Returning here leaves
    # the process cache alone, as a no-op drop did before the sweep existed.
    if not pairs and not has_cold:
        return 0
    if connection.in_transaction:
        connection.commit()
    if has_cold and not any(
        row[1] == "cold" for row in connection.execute("PRAGMA database_list")
    ):
        connection.execute("ATTACH DATABASE ? AS cold", (str(cold_path.resolve()),))
        _check_cold_schema(connection)
    # The paired delete goes through ``_drop_attached_cold``. The sweep
    # does not, so a retry after that hook fails still removes the orphan.
    _apply_maintenance_deletes(
        connection,
        path,
        has_cold,
        pairs,
        cold_delete=_drop_attached_cold,
        vacuum=False,
    )
    return len(pairs)
