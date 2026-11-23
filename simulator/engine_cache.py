"""One process-local HOT SQLite cache for engine outputs.

The key is the engine number, the chemistry version, and the request.
Read and write never delete an output row. Species expiry and version prune
are manual tools in scripts/engine_cache_sync.py.

Refusal masks are a separate table, not a key dimension. A request whose
coarse cell was learned from a deterministic refusal returns that typed
refusal, flagged ``mask_hit``, without calling the engine. One live key in
32 is solved again. A different outcome deletes that cell and records a
``false_mask`` witness. That cell stays mixed until ``clear-mask``: a later
refusal does not teach it again. The teaching request is one ``landed``
audit row per cell, replaced when that cell is taught again. ``clear-mask``
drops one engine's cells and audit rows for one class, and this process's
memory of that file. Merge copies output rows only, not mask tables. A
readonly cache cannot write the witness, but a contradiction seen in this
process is not served again. The in-process counter is the one the score
reads.
"""

from __future__ import annotations

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
    cannot be shown to be unaffected. --all and prune still delete it too.
    An empty frozenset is a real request that named no species; it is not
    this case, and expire --species leaves it in place. A flag other than
    0x00 or 0xff raises. expire counts that row and leaves it.
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
_MASK_TABLE = "engine_refusal_mask"
_MASK_AUDIT_TABLE = "engine_refusal_audit"
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
# Cells a live sample contradicted in this process. The file may still
# hold the row when it is readonly. Lookup and the in-process replay
# both treat a denied cell as absent until clear-mask forgets the file.
_MASK_DENY: set[tuple[str, str, str, bytes]] = set()
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
    """Return the configured cache path, or None when caching is disabled."""
    path = _ENGINE_CACHE_PATH_OVERRIDE.get()
    if path is not _ENGINE_CACHE_PATH_UNSET:
        return path or None
    return _cache_environment_setting("REGOLITH_ENGINE_CACHE") or None


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
    optional_tables = {_SYNC_WATERMARKS_TABLE, _MASK_TABLE, _MASK_AUDIT_TABLE}
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
    alone so a merge can copy the source rows, including species a blake2b
    key can no longer decode.
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


def not_stored_refusal_label(solved: Any, hot: Any) -> str | None:
    """Token for ``not_stored:refusal:<token>`` on a returned miss.

    None when the value is not an uncacheable refusal. Readonly mode does
    not consult this: it counts ``not_stored:readonly`` and returns. A caller
    that has the solved value can still ask what the writable branch would
    have counted. ``cacheable=False`` is a different counter and is not
    labeled here.
    """

    if isinstance(solved, EngineCacheValue) and not solved.cacheable:
        return None
    wrapped_refusal = (
        isinstance(solved, EngineCacheValue) and solved.refusal_reason is not None
    )
    refusal_not_cacheable = (
        wrapped_refusal and not cacheable_refusal(solved)
    ) or (
        _is_refusal(hot) and not cacheable_refusal(hot)
    )
    if not refusal_not_cacheable:
        return None
    refusal_reason = (
        solved.refusal_reason if wrapped_refusal
        else hot.get("refusal_reason") if isinstance(hot, Mapping)
        else getattr(hot, "refusal_reason", None)
    )
    return str(refusal_reason) if refusal_reason is not None else "untyped"


# Coarse preset of ControlQuantization (simulator/reduced_real_determinism.py):
# t_k 1 K, pressure 0.001 bar, log fO2 0.01, composition 3 significant figures.
# This leaf cannot import that module. No module already imports both, and
# a caller that forgot to register a quantizer would skip the mask. The
# copy stays. The property test sweeps these functions against the owner's
# _quantize and _sigfig. _sigfig_snap is a different rule and stays there.
_MASK_T_QUANTUM = 1.0
_MASK_T_DECIMALS = 0
_MASK_PRESSURE_QUANTUM = 0.001
_MASK_PRESSURE_DECIMALS = 3
_MASK_LOG_FO2_QUANTUM = 0.01
_MASK_LOG_FO2_DECIMALS = 2
_MASK_COMPOSITION_SIG_FIGS = 3
_MASK_COMPOSITION_FIELDS = frozenset({
    "composition_wt_pct", "composition_mol", "composition_kg", "composition",
})
_MASK_TEMPERATURE_FIELDS = frozenset({"temperature_K", "temperature_C"})
_MASK_PRESSURE_FIELDS = frozenset({"pressure_bar", "physical_pressure_bar"})
_MASK_LOG_FO2_FIELDS = frozenset({"fO2_log"})
# po2_bar is the nested number. po2 and vapor_transport_pO2_bar are the
# names the battery puts on the request. Same significant-figure bucket.
_MASK_PO2_FIELDS = frozenset({
    "po2_bar", "po2", "vapor_transport_pO2_bar",
})
_MASK_EXACT_SUBTREES = frozenset({"engine_settings"})
# One live solve out of this many exact keys, chosen from the key bytes.
# The same key always verifies or never verifies.
MASK_VERIFY_EVERY = 32
# Classes the phase-A probe replayed identically. alphaMELTS ValueError
# costs 19 µs. A battery-shaped coarsen plus lookup was not under that,
# so the class is left unmasked. tests/t1917_mask_probe.py exits 2 if
# this set disagrees with a fresh measurement. Timeouts and untyped
# refusals stay out: a load-dependent miss must not become a cell.
MASKABLE_REFUSAL_CLASSES = frozenset({
    ("alphamelts", "refusal:engine_crash"),
    ("internal-analytical", "refusal:unavailable"),
    ("thermoengine", "refusal:unavailable"),
    ("thermoengine", "exception:ThermoEngineFO2OmittedError"),
    ("thermoengine", "exception:ThermoEngineNonFiniteField"),
    ("thermoengine", "exception:ThermoEngineOutOfDomainError"),
})


class _MaskReplay:
    """In-process copy of a mask outcome. Emitting it does not call the engine."""

    __slots__ = ("refusal_class", "recipe")

    def __init__(self, refusal_class: str, recipe: Mapping[str, Any]) -> None:
        self.refusal_class = refusal_class
        self.recipe = recipe


def _build_value_error(recipe: Mapping[str, Any]) -> BaseException:
    args = recipe.get("args")
    if not isinstance(args, list):
        raise TypeError("ValueError mask recipe has no args")
    return ValueError(*args)


_MASK_EXCEPTION_BUILDERS: dict[str, Callable[[Mapping[str, Any]], BaseException]] = {
    "ValueError": _build_value_error,
}


def register_mask_exception(
    type_name: str, builder: Callable[[Mapping[str, Any]], BaseException],
) -> None:
    """Teach the mask how to raise one exception type without importing it.

    The cache leaf cannot import engine adapters. The adapter registers a
    builder when it is imported. A cell whose type has no builder is not a
    hit: the engine still runs.
    """

    if not isinstance(type_name, str) or not type_name.isidentifier():
        raise ValueError(f"mask exception type must be an identifier, got {type_name!r}")
    _MASK_EXCEPTION_BUILDERS[type_name] = builder


def _mask_quantize(value: float | None, quantum: float, digits: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    bucket = round(number / quantum) * quantum
    return round(bucket, digits)


def _mask_sigfig(value: float | None, sig_figs: int) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if number == 0.0:
        return 0.0
    digits = sig_figs - int(math.floor(math.log10(abs(number)))) - 1
    return round(number, digits)


def _mask_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _coarsen_composition(value: Any) -> Any:
    if _mask_number(value):
        return _mask_sigfig(float(value), _MASK_COMPOSITION_SIG_FIGS)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("composition names must be strings")
        return {key: _coarsen_composition(item) for key, item in value.items()}
    return value


def _coarsen_request(value: Any, field: str | None = None) -> Any:
    if field in _MASK_EXACT_SUBTREES:
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("mask cell names must be strings")
        if field in _MASK_COMPOSITION_FIELDS:
            return {key: _coarsen_composition(item) for key, item in value.items()}
        return {
            key: _coarsen_request(item, key) for key, item in value.items()
        }
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes)):
        items = [_coarsen_request(item) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    if field in _MASK_TEMPERATURE_FIELDS and _mask_number(value):
        return _mask_quantize(float(value), _MASK_T_QUANTUM, _MASK_T_DECIMALS)
    if field in _MASK_PRESSURE_FIELDS and _mask_number(value):
        return _mask_quantize(
            float(value), _MASK_PRESSURE_QUANTUM, _MASK_PRESSURE_DECIMALS,
        )
    if field in _MASK_LOG_FO2_FIELDS and _mask_number(value):
        return _mask_quantize(
            float(value), _MASK_LOG_FO2_QUANTUM, _MASK_LOG_FO2_DECIMALS,
        )
    if field in _MASK_PO2_FIELDS and _mask_number(value):
        return _mask_sigfig(float(value), _MASK_COMPOSITION_SIG_FIGS)
    return value


def refusal_mask_cell(request: Any) -> bytes | None:
    """Packed coarse cell for one request, or None when it cannot be named."""

    try:
        return pack(_coarsen_request(request), key=True)
    except (TypeError, ValueError):
        return None


def mask_verify_selected(cache_key: bytes) -> bool:
    """True for the deterministic live sample of this exact cache key."""

    digest = hashlib.blake2b(
        cache_key, digest_size=8, person=b"t1917maskverify",
    ).digest()
    return int.from_bytes(digest, "little") % MASK_VERIFY_EVERY == 0


class _MaskedList(list):
    """Production list hot plus the mask class.

    ``equilibrium_cache_from_value`` accepts a list of length 11 or 12.
    A subclass is still a list, so that decoder keeps working. Callers
    read ``mask_hit`` off the object. The stored recipe stays a plain list.
    """

    mask_hit: str


def _replayable(recipe: Mapping[str, Any]) -> bool:
    kind = recipe.get("kind")
    if kind == "refusal":
        hot = recipe.get("hot")
        return isinstance(hot, (Mapping, list))
    if kind == "exception":
        return str(recipe.get("type")) in _MASK_EXCEPTION_BUILDERS
    return False


def _flag_refusal_hot(hot: Any, refusal_class: str) -> Any:
    if isinstance(hot, Mapping):
        flagged = dict(hot)
        flagged["mask_hit"] = refusal_class
        return flagged
    if isinstance(hot, list):
        flagged = _MaskedList(hot)
        flagged.mask_hit = refusal_class
        return flagged
    raise TypeError("refusal mask recipe has no mapping or list")


def _emit_mask(recipe: Mapping[str, Any], refusal_class: str) -> Any:
    if recipe.get("kind") == "exception":
        builder = _MASK_EXCEPTION_BUILDERS.get(str(recipe.get("type")))
        if builder is None:
            raise TypeError(f"no mask builder for {recipe.get('type')!r}")
        exc = builder(recipe)
        try:
            exc.mask_hit = refusal_class  # type: ignore[attr-defined]
        except AttributeError:
            pass
        raise exc
    return _flag_refusal_hot(recipe.get("hot"), refusal_class)


def _observed_mask_class(solved: Any, hot: Any, exc: BaseException | None) -> str | None:
    if exc is not None:
        return "exception:" + _not_stored_exception_label(exc)
    label = not_stored_refusal_label(solved, hot)
    if label is None:
        return None
    return "refusal:" + label


def _mask_matches(
    recipe: Mapping[str, Any],
    refusal_class: str,
    solved: Any,
    hot: Any,
    exc: BaseException | None,
) -> bool:
    if _observed_mask_class(solved, hot, exc) != refusal_class:
        return False
    if recipe.get("kind") != "exception":
        return True
    if exc is None or type(exc).__name__ != recipe.get("type"):
        return False
    if "cause" in recipe:
        cause = getattr(exc, "cause", None)
        token = getattr(cause, "value", None)
        if not isinstance(token, str):
            token = cause if isinstance(cause, str) else None
        return token == recipe.get("cause")
    return tuple(exc.args) == tuple(recipe.get("args") or ())


def _plain_exception_args(exc: BaseException) -> list[Any] | None:
    args: list[Any] = []
    for arg in exc.args:
        if isinstance(arg, bool) or arg is None or isinstance(arg, (str, int, float)):
            if isinstance(arg, float) and not math.isfinite(arg):
                return None
            args.append(arg)
            continue
        return None
    return args


def _mask_recipe(
    refusal_class: str, solved: Any, hot: Any, exc: BaseException | None,
) -> dict[str, Any] | None:
    if exc is not None:
        name = type(exc).__name__
        if refusal_class != f"exception:{name}":
            return None
        if name not in _MASK_EXCEPTION_BUILDERS:
            return None
        args = _plain_exception_args(exc)
        if args is None:
            return None
        recipe: dict[str, Any] = {"kind": "exception", "type": name, "args": args}
        cause = getattr(exc, "cause", None)
        if cause is not None:
            token = getattr(cause, "value", None)
            if not isinstance(token, str):
                return None
            recipe["cause"] = token
        return recipe
    if isinstance(hot, Mapping):
        payload: Any = dict(hot)
        payload.pop("mask_hit", None)
    elif isinstance(hot, list):
        # The compact scorer list from equilibrium_to_hot. A subclass's
        # mask_hit attribute is not an element and is not stored.
        payload = list(hot)
    else:
        return None
    try:
        frozen = unpack(pack({"kind": "refusal", "hot": payload}))
    except (TypeError, ValueError):
        return None
    if not isinstance(frozen, Mapping) or not _replayable(frozen):
        return None
    return dict(frozen)


def _ensure_mask_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {_MASK_TABLE} ("
        "engine TEXT NOT NULL, chem_version TEXT NOT NULL, cell BLOB NOT NULL, "
        "refusal_class TEXT NOT NULL, outcome BLOB NOT NULL, "
        "PRIMARY KEY (engine, chem_version, cell)) WITHOUT ROWID"
    )
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {_MASK_AUDIT_TABLE} ("
        "key BLOB NOT NULL, disposition TEXT NOT NULL, engine TEXT NOT NULL, "
        "refusal_class TEXT NOT NULL, chem_version TEXT NOT NULL, "
        "cell BLOB NOT NULL, observed TEXT NOT NULL, "
        "PRIMARY KEY (key, disposition)) WITHOUT ROWID"
    )


def _select_mask(
    connection: sqlite3.Connection, engine: str, version: str, cell: bytes,
) -> tuple[str, Mapping[str, Any]] | None:
    try:
        row = connection.execute(
            f"SELECT refusal_class, outcome FROM {_MASK_TABLE} "
            "WHERE engine=? AND chem_version=? AND cell=?",
            (engine, version, cell),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None
        raise
    if row is None:
        return None
    try:
        recipe = unpack(bytes(row[1]))
    except (TypeError, ValueError):
        return None
    if not isinstance(recipe, Mapping) or not _replayable(recipe):
        return None
    return str(row[0]), recipe


def _add_counter(engine_id: str, name: str) -> None:
    bucket = _counter_bucket(engine_id)
    bucket[name] = bucket.get(name, 0) + 1


def _mask_deny_key(
    path_value: str, engine: str, version: str, cell: bytes,
) -> tuple[str, str, str, bytes]:
    try:
        resolved = str(Path(path_value).expanduser().resolve())
    except OSError:
        resolved = str(path_value)
    return (resolved, engine, version, cell)


def _deny_mask_cell(
    path_value: str, engine: str, version: str, cell: bytes,
) -> None:
    with _LOCK:
        _MASK_DENY.add(_mask_deny_key(path_value, engine, version, cell))


def _mask_cell_denied(
    path_value: str, engine: str, version: str, cell: bytes,
) -> bool:
    with _LOCK:
        return _mask_deny_key(path_value, engine, version, cell) in _MASK_DENY


def _drop_mask_memory(path_value: str) -> None:
    with _LOCK:
        for memory_key in tuple(_MEMORY_CACHE):
            if memory_key[0] == path_value and isinstance(
                _MEMORY_CACHE[memory_key], _MaskReplay,
            ):
                del _MEMORY_CACHE[memory_key]


def _learn_mask_cell(
    connection: sqlite3.Connection,
    *,
    engine: str,
    version: str,
    cell: bytes,
    refusal_class: str,
    recipe: Mapping[str, Any],
    cache_key: bytes,
) -> None:
    """Learn one cell and keep the teaching request as the landed audit row.

    A ``false_mask`` witness for this engine, chemistry version, and cell
    means the cell is mixed. Learn commits and returns without writing:
    the keys that were not sampled must not be refused again. ``clear-mask``
    deletes that witness. Older landed rows for a cell that is still being
    taught expire here. The new teaching row stays. False-mask rows are
    not deleted.
    """

    outcome = pack(dict(recipe))
    with _LOCK:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _ensure_mask_tables(connection)
            mixed = connection.execute(
                f"SELECT 1 FROM {_MASK_AUDIT_TABLE} "
                "WHERE engine=? AND chem_version=? AND cell=? "
                "AND disposition='false_mask' LIMIT 1",
                (engine, version, cell),
            ).fetchone()
            if mixed is not None:
                connection.commit()
                return
            connection.execute(
                f"DELETE FROM {_MASK_AUDIT_TABLE} "
                "WHERE engine=? AND refusal_class=? AND chem_version=? "
                "AND cell=? AND disposition='landed'",
                (engine, refusal_class, version, cell),
            )
            connection.execute(
                f"INSERT OR REPLACE INTO {_MASK_TABLE} "
                "(engine, chem_version, cell, refusal_class, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                (engine, version, cell, refusal_class, outcome),
            )
            connection.execute(
                f"INSERT OR REPLACE INTO {_MASK_AUDIT_TABLE} "
                "(key, disposition, engine, refusal_class, chem_version, cell, observed) "
                "VALUES (?, 'landed', ?, ?, ?, ?, ?)",
                (cache_key, engine, refusal_class, version, cell, refusal_class),
            )
            connection.commit()
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise


def _unmask_cell(
    connection: sqlite3.Connection,
    *,
    engine: str,
    version: str,
    cell: bytes,
    refusal_class: str,
    cache_key: bytes,
    observed: str,
    path_value: str,
) -> None:
    with _LOCK:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _ensure_mask_tables(connection)
            connection.execute(
                f"DELETE FROM {_MASK_TABLE} "
                "WHERE engine=? AND chem_version=? AND cell=?",
                (engine, version, cell),
            )
            connection.execute(
                f"INSERT OR REPLACE INTO {_MASK_AUDIT_TABLE} "
                "(key, disposition, engine, refusal_class, chem_version, cell, observed) "
                "VALUES (?, 'false_mask', ?, ?, ?, ?, ?)",
                (cache_key, engine, refusal_class, version, cell, observed),
            )
            connection.commit()
        except BaseException:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
    _drop_mask_memory(path_value)


def _try_mask_write(engine_id: str, write: Callable[[], None]) -> None:
    """A mask bookkeeping failure must not replace the engine outcome."""

    try:
        write()
    except Exception:
        with _LOCK:
            _add_counter(engine_id, "mask_error")


def _try_learn_mask(
    connection: sqlite3.Connection | None,
    *,
    readonly: bool,
    engine_id: str,
    version: str,
    request: Any,
    cache_key: bytes,
    solved: Any,
    hot: Any,
    exc: BaseException | None,
) -> None:
    if readonly or connection is None:
        return
    if exc is not None and not isinstance(exc, Exception):
        return
    refusal_class = _observed_mask_class(solved, hot, exc)
    if refusal_class is None:
        return
    if (engine_id, refusal_class) not in MASKABLE_REFUSAL_CLASSES:
        return
    recipe = _mask_recipe(refusal_class, solved, hot, exc)
    if recipe is None or not _replayable(recipe):
        return
    cell = refusal_mask_cell(request)
    if cell is None:
        return

    def write() -> None:
        _learn_mask_cell(
            connection,
            engine=engine_id,
            version=version,
            cell=cell,
            refusal_class=refusal_class,
            recipe=recipe,
            cache_key=cache_key,
        )

    _try_mask_write(engine_id, write)


def _remember_mask(
    memory_key: tuple[str, bytes, bool],
    refusal_class: str,
    recipe: Mapping[str, Any],
) -> None:
    with _LOCK:
        _prepare_memory_cache()
        _remember_memory_value(
            memory_key, _MaskReplay(refusal_class, dict(recipe)),
        )


def _flag_live(hot: Any, exc: BaseException | None, refusal_class: str) -> Any:
    if exc is not None:
        try:
            exc.mask_hit = refusal_class  # type: ignore[attr-defined]
        except AttributeError:
            pass
        raise exc
    if isinstance(hot, (Mapping, list)):
        return _flag_refusal_hot(hot, refusal_class)
    return hot


def _capture_solve(solve: Any) -> tuple[Any, Any, BaseException | None]:
    try:
        solved = solve()
    except BaseException as exc:
        return None, None, exc
    return solved, live_hot(solved), None


def _lookup_refusal_mask(
    connection: sqlite3.Connection | None,
    engine_id: str,
    version: str,
    request: Any,
    path_value: str,
) -> tuple[str, Mapping[str, Any], bytes] | None:
    if connection is None:
        return None
    cell = refusal_mask_cell(request)
    if cell is None:
        return None
    # A contradiction already seen in this process wins over the file.
    # Readonly cannot delete the row, and a failed delete must not either.
    if _mask_cell_denied(path_value, engine_id, version, cell):
        return None
    try:
        selected = _select_mask(connection, engine_id, version, cell)
    except sqlite3.Error:
        with _LOCK:
            _counter_bucket(engine_id)["read_error"] += 1
        return None
    if selected is None:
        return None
    refusal_class, recipe = selected
    return refusal_class, recipe, cell


def _serve_mask_without_engine(
    engine_id: str,
    refusal_class: str,
    recipe: Mapping[str, Any],
    memory_key: tuple[str, bytes, bool],
) -> Any:
    _remember_mask(memory_key, refusal_class, recipe)
    with _LOCK:
        _add_counter(engine_id, f"mask_hit:{refusal_class}")
    return _emit_mask(recipe, refusal_class)


def _serve_verified_mask(
    engine_id: str,
    refusal_class: str,
    recipe: Mapping[str, Any],
    memory_key: tuple[str, bytes, bool],
    solved: Any,
    hot: Any,
    exc: BaseException | None,
) -> Any:
    live_recipe = _mask_recipe(refusal_class, solved, hot, exc)
    remembered = recipe
    if live_recipe is not None and _replayable(live_recipe):
        remembered = live_recipe
    _remember_mask(memory_key, refusal_class, remembered)
    with _LOCK:
        _add_counter(engine_id, f"mask_hit:{refusal_class}")
        _add_counter(engine_id, f"mask_verify:{refusal_class}")
    return _flag_live(hot, exc, refusal_class)


def _reject_false_mask(
    connection: sqlite3.Connection,
    readonly: bool,
    engine_id: str,
    version: str,
    cell: bytes,
    refusal_class: str,
    cache_key: bytes,
    path_value: str,
    solved: Any,
    hot: Any,
    exc: BaseException | None,
) -> None:
    observed = _observed_mask_class(solved, hot, exc) or "accepted"
    # Deny before the write. A readonly file cannot record the witness,
    # and a failed delete must not keep serving the cell in this process.
    _deny_mask_cell(path_value, engine_id, version, cell)
    if not readonly:
        def write() -> None:
            _unmask_cell(
                connection,
                engine=engine_id,
                version=version,
                cell=cell,
                refusal_class=refusal_class,
                cache_key=cache_key,
                observed=observed,
                path_value=path_value,
            )
        _try_mask_write(engine_id, write)
    _drop_mask_memory(path_value)
    with _LOCK:
        _add_counter(engine_id, f"mask_false:{refusal_class}")


def _count_exception_and_learn(
    connection: sqlite3.Connection | None,
    *,
    readonly: bool,
    engine_id: str,
    chem_version: str | int,
    request: Any,
    cache_key: bytes,
    exc: BaseException,
) -> None:
    with _LOCK:
        bucket = _counter_bucket(engine_id)
        label = _not_stored_exception_label(exc)
        name = f"not_stored:exception:{label}"
        bucket[name] = bucket.get(name, 0) + 1
    _try_learn_mask(
        connection,
        readonly=readonly,
        engine_id=engine_id,
        version=str(chem_version),
        request=request,
        cache_key=cache_key,
        solved=None,
        hot=None,
        exc=exc,
    )


def _finish_cache_miss(
    *,
    engine_id: str,
    request: Any,
    solve: Any,
    readonly: bool,
    connection: sqlite3.Connection | None,
    path: Path,
    source_box: str,
    chem_version: str | int,
    cache_key: bytes,
    memory_key: tuple[str, bytes, bool],
    precomputed: tuple[Any, Any, BaseException | None] | None,
) -> Any:
    """Count one real miss and store it, or return the named not-stored bucket.

    A mask short-circuit does not come here. A sample that disagreed already
    holds the live result in ``precomputed`` and must not solve again.
    """

    with _LOCK:
        _counter_bucket(engine_id)["misses"] += 1
    if precomputed is None:
        try:
            solved = solve()
            hot = live_hot(solved)
            exc = None
        except BaseException as raised:
            _count_exception_and_learn(
                connection,
                readonly=readonly,
                engine_id=engine_id,
                chem_version=chem_version,
                request=request,
                cache_key=cache_key,
                exc=raised,
            )
            raise
    else:
        solved, hot, exc = precomputed
        if exc is not None:
            _count_exception_and_learn(
                connection,
                readonly=readonly,
                engine_id=engine_id,
                chem_version=chem_version,
                request=request,
                cache_key=cache_key,
                exc=exc,
            )
            raise exc
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
    reason_label = not_stored_refusal_label(solved, hot)
    if reason_label is not None:
        with _LOCK:
            counter = f"not_stored:refusal:{reason_label}"
            counts = _counter_bucket(engine_id)
            counts[counter] = counts.get(counter, 0) + 1
        _try_learn_mask(
            connection,
            readonly=readonly,
            engine_id=engine_id,
            version=str(chem_version),
            request=request,
            cache_key=cache_key,
            solved=solved,
            hot=hot,
            exc=None,
        )
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


def cached_engine_call(engine_id: str, request: Any, solve: Any) -> Any:
    if _CACHE_BYPASSED.get():
        return live_hot(solve())
    path_value = _configured_engine_cache_path()
    if not path_value:
        return live_hot(solve())
    mode = (_cache_environment_setting("REGOLITH_ENGINE_CACHE_MODE", "") or "").lower()
    if mode not in {"", "readonly"}:
        raise ValueError(f"unknown engine cache mode {mode!r}")
    readonly = mode == "readonly"
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
            if isinstance(result, _MaskReplay):
                # A repeated key in this process does not re-verify.
                # The counter is the one the score reads. A cell this
                # process already contradicted is not served from memory.
                cell = refusal_mask_cell(request)
                denied = (
                    cell is not None
                    and _mask_cell_denied(
                        path_value, engine_id, str(chem_version), cell,
                    )
                )
                if denied:
                    del _MEMORY_CACHE[memory_key]
                else:
                    _add_counter(engine_id, f"mask_hit:{result.refusal_class}")
                    return _emit_mask(result.recipe, result.refusal_class)
            else:
                counts["hits"] += 1
                return result

    path = Path(path_value).expanduser()
    try:
        connection = _connect(path, readonly=readonly)
    except (sqlite3.Error, OSError):
        with _LOCK:
            counts = _counter_bucket(engine_id)
            counts["read_error"] += 1
            counts["misses"] += 1
            counts["not_stored:readonly"] = counts.get("not_stored:readonly", 0) + 1
        return live_hot(solve())
    with _LOCK:
        try:
            row = None if connection is None or _definitely_absent(connection, cache_key) else connection.execute(
                f"SELECT value, provenance_id FROM {_TABLE} WHERE key=?", (cache_key,)
            ).fetchone()
        except sqlite3.Error:
            counts["read_error"] += 1
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

    version = str(chem_version)
    masked = _lookup_refusal_mask(
        connection, engine_id, version, request, path_value,
    )
    finish = {
        "engine_id": engine_id,
        "request": request,
        "solve": solve,
        "readonly": readonly,
        "connection": connection,
        "path": path,
        "source_box": source_box,
        "chem_version": chem_version,
        "cache_key": cache_key,
        "memory_key": memory_key,
    }
    if masked is None:
        return _finish_cache_miss(**finish, precomputed=None)
    refusal_class, recipe, cell = masked
    if not mask_verify_selected(cache_key):
        return _serve_mask_without_engine(
            engine_id, refusal_class, recipe, memory_key,
        )
    solved, hot, exc = _capture_solve(solve)
    if _mask_matches(recipe, refusal_class, solved, hot, exc):
        return _serve_verified_mask(
            engine_id, refusal_class, recipe, memory_key, solved, hot, exc,
        )
    _reject_false_mask(
        connection, readonly, engine_id, version, cell,
        refusal_class, cache_key, path_value, solved, hot, exc,
    )
    return _finish_cache_miss(**finish, precomputed=(solved, hot, exc))


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
    database = Path(path).expanduser()
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
            inserted, conflict = _insert_cache_row_if_absent(
                connection,
                digest,
                value,
                engine,
                version,
                source_box,
                row_seq,
                provenance=provenance,
                species=frozenset() if decoded is None else decoded,
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


def snapshot_cache(live: Path, out: Path) -> dict[str, Any]:
    """Back up a live HOT/COLD pair into exclusively new, standalone files."""
    live, out = live.expanduser(), out.expanduser()
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
                cold.execute("DELETE FROM engine_values WHERE NOT EXISTS ("
                             "SELECT 1 FROM hot.engine_outputs h WHERE h.row_seq=engine_values.row_seq)")
                cold.commit()
    return engine_cache_stats(out)


def merge_cache_files(into: Path, inputs: list[Path], *, vacuum: bool = False) -> dict[str, Any]:
    """Accumulate new per-box sequences without replacing a natural-key value."""
    into = into.expanduser()
    inputs = [path.expanduser() for path in inputs]
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
        # Same path comparison as the memory drop. Deny keys already store
        # the resolved path, so a symlink does not leave a cell servable.
        resolved = str(expanded)
        for deny_key in tuple(_MASK_DENY):
            if deny_key[0] == resolved:
                _MASK_DENY.discard(deny_key)
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
    first. The residue is a COLD value with no HOT row. That value is not
    served, and snapshot drops it. Deleting COLD first would leave a HOT
    row whose COLD value is already gone, and that row would still be a
    hit. ``cold_delete`` is only the drop_chemistry_version seam. expire
    and prune use the default delete.
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
        # the value. Snapshot still drops a COLD row with no HOT row.
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


def migrate_cache_file(path: str | Path, *, vacuum: bool = True) -> dict[str, Any]:
    """Convert a schema-3 HOT/COLD pair in place. HOT key and value bytes stay.

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
        if connection.in_transaction:
            connection.commit()
        before: dict[str, Any] = {
            "hot": _cache_space(connection, "main", _TABLE),
            "hot_payload_bytes": int(connection.execute(
                "SELECT COALESCE(SUM(length(key) + length(value)), 0) FROM engine_outputs"
            ).fetchone()[0]),
        }
        cold_path = _cache_pair(path)[1]
        has_cold = cold_path.exists()
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
            for key, value in connection.execute("SELECT key, value FROM engine_outputs ORDER BY row_seq")
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
            for key, value in connection.execute("SELECT key, value FROM engine_outputs ORDER BY row_seq")
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
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            connection.close()
        raise
    connection.close()
    connection = None
    if vacuum:
        _vacuum_cache_files(path)
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
        "after": engine_cache_stats(path),
        "after_payload": _payload_bytes(path),
        "vacuumed": bool(vacuum),
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
    key that named no species is not that case. ``all_rows`` deletes every
    row of that engine. Nothing on the read or write path calls this.
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
        if apply and pairs:
            try:
                _delete_cached_rows(connection, has_cold, pairs)
            except BaseException:
                _forget_process_cache(path)
                raise
            connection.execute("VACUUM main")
            if has_cold:
                connection.execute("VACUUM cold")
            _forget_process_cache(path)
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
            "vacuumed": bool(apply and pairs),
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
    rows. A natural key that named no species returns an empty set and stays.
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
    ``apply``. Nothing on the read or write path calls this.
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
        if apply and pairs:
            try:
                _delete_cached_rows(connection, has_cold, pairs)
            except BaseException:
                _forget_process_cache(path)
                raise
            connection.execute("VACUUM main")
            if has_cold:
                connection.execute("VACUUM cold")
            _forget_process_cache(path)
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
            "vacuumed": bool(apply and pairs),
        }
    finally:
        connection.close()


def clear_refusal_mask(
    path: str | Path,
    *,
    engine: str,
    refusal_class: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Delete one engine's mask cells and audit rows for one class.

    Every chemistry version of that pair goes. Dry-run unless ``apply``.
    A file that has never learned a mask reports zeros and is not given
    the tables just so they can be deleted. Nothing on the read or write
    path calls this.
    """

    if engine not in ENGINE_CHEMISTRY_VERSION:
        raise ValueError(f"unknown engine {engine!r}")
    if not isinstance(refusal_class, str) or refusal_class == "":
        raise ValueError("refusal class must be a non-empty string")
    path = Path(path).expanduser()
    connection, _has_cold = _maintenance_connection(path)
    try:
        present = {
            str(name)
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?, ?)",
                (_MASK_TABLE, _MASK_AUDIT_TABLE),
            )
        }
        mask_cells = 0
        audit_records = 0
        if _MASK_TABLE in present:
            mask_cells = int(connection.execute(
                f"SELECT COUNT(*) FROM {_MASK_TABLE} WHERE engine=? AND refusal_class=?",
                (engine, refusal_class),
            ).fetchone()[0])
        if _MASK_AUDIT_TABLE in present:
            audit_records = int(connection.execute(
                f"SELECT COUNT(*) FROM {_MASK_AUDIT_TABLE} "
                "WHERE engine=? AND refusal_class=?",
                (engine, refusal_class),
            ).fetchone()[0])
        if apply and (mask_cells or audit_records):
            try:
                connection.execute("BEGIN IMMEDIATE")
                if _MASK_TABLE in present:
                    connection.execute(
                        f"DELETE FROM {_MASK_TABLE} WHERE engine=? AND refusal_class=?",
                        (engine, refusal_class),
                    )
                if _MASK_AUDIT_TABLE in present:
                    connection.execute(
                        f"DELETE FROM {_MASK_AUDIT_TABLE} "
                        "WHERE engine=? AND refusal_class=?",
                        (engine, refusal_class),
                    )
                connection.commit()
            except BaseException:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                _forget_process_cache(path)
                raise
        if apply:
            # The process copy would keep serving a cell this command deleted.
            _forget_process_cache(path)
        return {
            "command": "clear-mask",
            "apply": bool(apply),
            "dry_run": not apply,
            "engine": engine,
            "refusal_class": refusal_class,
            "mask_cells": mask_cells,
            "audit_records": audit_records,
            "deleted_mask_cells": mask_cells if apply else 0,
            "deleted_audit_records": audit_records if apply else 0,
        }
    finally:
        connection.close()


def _drop_attached_cold(
    connection: sqlite3.Connection, pairs: list[tuple[str, int]]
) -> None:
    """Delete COLD values inside the caller's open COLD transaction.

    The HOT and species deletes are already committed. A failure here
    rolls back only this COLD delete, so the file keeps COLD values
    with no HOT row. Those values are not hits. Snapshot drops them.
    One COMMIT is not atomic across a WAL database and an attached
    database, so the two deletes are not one transaction.
    """

    _delete_cold_values(connection, pairs)


def drop_chemistry_version(engine_id: str, chem_version: str | int) -> int:
    path_value = _configured_engine_cache_path()
    if not path_value:
        return 0
    path = Path(path_value).expanduser()
    connection = _connect(path, readonly=False)
    assert connection is not None
    pairs = [
        (str(source_box), int(row_seq))
        for source_box, row_seq in connection.execute(
            f"SELECT source_box, row_seq FROM {_TABLE} WHERE engine=? AND chem_version=?",
            (engine_id, str(chem_version)),
        )
    ]
    if not pairs:
        return 0
    cold_path = _cache_pair(path)[1]
    has_cold = cold_path.exists()
    if connection.in_transaction:
        connection.commit()
    if has_cold and not any(
        row[1] == "cold" for row in connection.execute("PRAGMA database_list")
    ):
        connection.execute("ATTACH DATABASE ? AS cold", (str(cold_path.resolve()),))
        _check_cold_schema(connection)
    try:
        _delete_cached_rows(
            connection,
            has_cold,
            pairs,
            cold_delete=_drop_attached_cold,
        )
    except BaseException:
        # The HOT delete may already be committed. Drop the process copy
        # either way: a rolled-back delete reloads from the file, and a
        # committed one must not keep serving.
        _forget_process_cache(path)
        raise
    _forget_process_cache(path)
    return len(pairs)
