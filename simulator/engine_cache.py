"""One process-local HOT SQLite cache for engine outputs.

Chemistry changes expire cached outputs only when the engine's manually
maintained chemistry version is bumped.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import json
import logging
import math
import re
import struct
import os
import sqlite3
import threading
import time
import tomllib
import zlib
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping
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


def unpack(data: bytes):
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
        if tag == 10:
            return _Reference(unsigned())
        if tag == 11:
            return 0.0
        if tag == 12:
            return -0.0
        if tag == 13:
            integer = unsigned()
            return float(-(integer // 2) - 1 if integer & 1 else integer // 2)
        if 128 <= tag < 128 + len(_VALUE_TOKENS):
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

CACHEABLE_REFUSAL_CODES = frozenset({
    REFUSAL_OUT_OF_BASIS, REFUSAL_COMPOSITION_PROJECTED, REFUSAL_MAJOR_SUM,
    REFUSAL_NO_LIQUID,
    REFUSAL_FORBIDDEN_SPECIES,
    REFUSAL_LIQUID_STATE,
    REFUSAL_INVALID_PRESSURE_BAR,
    REFUSAL_INVALID_FO2_LOG,
    REFUSAL_SUM_PRESSURE_SANITY,
    REFUSAL_SPECIATION_NO_FINITE_VALUES,
})


def _cacheable_refusal_status(
    refusal_reason: str | None, status: str | None,
) -> bool:
    if status in {
        None, "out_of_domain", "input_refusal", "refusal", "no_liquid", "ok",
    }:
        return True
    return (
        refusal_reason == REFUSAL_SPECIATION_NO_FINITE_VALUES
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
    "openimcc": 2,
    "internal-analytical": 3,
    "vaporock": 2,
    "magemin": 5,
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
# vaporock v2: 2026-11-23 t-1923 pressure/fO2 car, round 7 - retain gas-channel completeness for DP-108
# internal-analytical v1: 2026-11-08 simulator code at d5e6b8dc2 - initial cache version
# openimcc v1: 2026-11-08 OpenIMCC pin 66ec4a3d8fd4c0741ca2fdf9cf0569d4daec7d84 - initial cache version
# openimcc v2: 2026-11-23 t-1923 pressure/fO2 car, round 11 - retain isolated-cell gas-channel completeness for DP-108
# magemin v3: 2026-11-17 MAGEMin build and patch changes altered native activities (7e55ce7f24, c204deaf36)
# alphamelts v3: 2026-11-17 shared _MELTSBackendSupport numeric changes altered cached results (93bbb34d5d, df3abcff40)
# thermoengine v3: 2026-11-17 shared _MELTSBackendSupport numeric changes altered cached results (93bbb34d5d, df3abcff40)
# internal-analytical v2: 2026-11-17 stateless activity rewrite changed repeated-call vapor pressures (0e9b03818f)
# internal-analytical v3: 2026-11-18 b265 e7fbd4c052 onto b-1244 71806e046 - vapour-rail continuation notice, wall-saturation thermo and evaporation drive
# magemin v4: 2026-11-18 MAGEMin pin unchanged, b265 e7fbd4c052 onto b-1244 71806e046 - positive SiO2 outside [30, 80] wt% is predicted and flagged; Fe-free cells leave fO2 indeterminate
# magemin v5: 2026-11-23 t-1923 pressure/fO2 car, round 15 - omitted oxygen uses the nominal transport control with a typed per-call flag
# alphamelts v4: 2026-11-18 alphaMELTS binary pin unchanged, b265 e7fbd4c052 onto b-1244 71806e046 - subfloor oxygen extrapolation notice and freeze-gate cooling path
# thermoengine v4: 2026-11-18 ThermoEngine pin unchanged, b265 e7fbd4c052 onto b-1244 71806e046 - same subfloor oxygen extrapolation notice


@dataclass(frozen=True)
class EngineCacheInput:
    path: str
    kind: str
    selector: str | None


_ENGINE_CACHE_ROOT = Path(__file__).resolve().parents[1]

# Only inputs read by a numeric engine path belong here. Package code/data
# selected by each lock or OpenIMCC pin are covered by that immutable pin;
# code-side tables stay under the versioned live-probe guard.
# PySulfSat runs after cached equilibrium, and Thermobar has no cached-engine
# caller, so neither lock entry can affect the value stored by this cache.
ENGINE_CACHE_INPUTS = {
    "openimcc": (
        EngineCacheInput(
            "pyproject.toml", "pyproject-dependency", "openimcc"
        ),  # This pin selects OpenIMCC's solver and packaged datapacks.
    ),
    "internal-analytical": (
        EngineCacheInput(
            "pyproject.toml", "pyproject-dependency", "openimcc"
        ),  # The oxygen-balance branch delegates to OpenIMCC.
        EngineCacheInput(
            "data/vapor_pressures.yaml", "data", None
        ),  # The builtin vapor-pressure provider reads these coefficients.
        EngineCacheInput(
            "data/literature/extracts/nasa-cea-thermo.yaml", "data", None
        ),  # The builtin catalog reads these executable NASA thermo rows.
        EngineCacheInput(
            "data/literature/extracts/ivtan-mno-coo-thermo.yaml", "data", None
        ),  # The builtin catalog reads these executable IVTAN thermo rows.
        EngineCacheInput(
            "data/setpoints.yaml", "data", None
        ),  # The core reads fallback and activity settings from this file.
        EngineCacheInput(
            "data/species_catalog.yaml", "data", None
        ),  # The core's formula registry supplies formula masses for this path.
    ),
    "vaporock": (
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.vaporock"
        ),  # VapoRock supplies the cached vapor result.
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.thermoengine"
        ),  # The VapoRock package loads ThermoEngine native libraries.
    ),
    "magemin": (
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.magemin"
        ),  # This entry selects MAGEMin source, patches and built artifact.
    ),
    "alphamelts": (
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.alphamelts"
        ),  # This entry selects the alphaMELTS runtime artifact.
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.petthermotools"
        ),  # The alternate Python transport runs PetThermoTools.
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.vaporock"
        ),  # The Python transport delegates vapor equilibrium to VapoRock.
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.thermoengine"
        ),  # VapoRock loads ThermoEngine native libraries.
        EngineCacheInput(
            "pyproject.toml", "pyproject-dependency", "openimcc"
        ),  # Builtin subprocess projection may evaluate melt activity with OpenIMCC.
        EngineCacheInput(
            "data/vapor_pressures.yaml", "data", None
        ),  # Shared MELTS support reads coefficients for oxide vapor projection.
        EngineCacheInput(
            "data/literature/extracts/nasa-cea-thermo.yaml", "data", None
        ),  # Shared MELTS support reads CEA rows for O2 dissociation.
        EngineCacheInput(
            "data/literature/extracts/ivtan-mno-coo-thermo.yaml", "data", None
        ),  # Catalog fallback reads this executable IVTAN extract.
    ),
    "thermoengine": (
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.thermoengine"
        ),  # This entry selects ThermoEngine source, patches and native libraries.
        EngineCacheInput(
            "engines/engines.lock.toml", "engine-lock", "engines.vaporock"
        ),  # ThermoEngine delegates its vapor path to VapoRock.
        EngineCacheInput(
            "data/vapor_pressures.yaml", "data", None
        ),  # Shared MELTS support reads coefficients for oxide vapor projection.
        EngineCacheInput(
            "data/literature/extracts/nasa-cea-thermo.yaml", "data", None
        ),  # Shared MELTS support reads CEA rows for O2 dissociation.
        EngineCacheInput(
            "data/literature/extracts/ivtan-mno-coo-thermo.yaml", "data", None
        ),  # Catalog-backed vapor fallback reads this executable IVTAN extract.
    ),
}
_ENGINE_CACHE_INPUT_IDENTITY_BYTES = 16
_ENGINE_CACHE_KEY_PREFIX_BYTES = 4


class EngineCacheInputIdentityError(ValueError):
    """A declared engine pin or runtime data input cannot be identified."""

    def __init__(self, engine_id: str, path: str, reason: str) -> None:
        self.engine_id = engine_id
        self.path = path
        self.reason = reason
        super().__init__(
            f"engine cache input identity unavailable for {engine_id!r} at "
            f"{path!r}: {reason}"
        )


def _cache_input_component(engine_id: str, input_ref: EngineCacheInput) -> bytes:
    path = _ENGINE_CACHE_ROOT / input_ref.path
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise EngineCacheInputIdentityError(
            engine_id, input_ref.path, f"cannot read declared input ({exc})"
        ) from exc

    if input_ref.kind == "data":
        return source

    try:
        document = tomllib.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise EngineCacheInputIdentityError(
            engine_id, input_ref.path, f"invalid TOML input ({exc})"
        ) from exc

    if input_ref.kind == "engine-lock":
        try:
            selector_root, selector_name = (input_ref.selector or "").split(".", 1)
            value = document[selector_root][selector_name]
        except (KeyError, ValueError, TypeError) as exc:
            raise EngineCacheInputIdentityError(
                engine_id,
                input_ref.path,
                f"missing lock entry {input_ref.selector!r}",
            ) from exc
    elif input_ref.kind == "pyproject-dependency":
        dependency_name = (input_ref.selector or "").replace("_", "-").lower()
        project = document.get("project")
        if not isinstance(project, Mapping):
            raise EngineCacheInputIdentityError(
                engine_id, input_ref.path, "project is not a TOML table"
            )
        dependencies = project.get("dependencies", ())
        if not isinstance(dependencies, list):
            raise EngineCacheInputIdentityError(
                engine_id, input_ref.path, "project.dependencies is not a list"
            )
        matches = []
        for dependency in dependencies:
            if not isinstance(dependency, str):
                continue
            match = re.match(r"\s*([A-Za-z0-9._-]+)", dependency)
            if match and match.group(1).replace("_", "-").lower() == dependency_name:
                matches.append(dependency)
        if len(matches) != 1:
            raise EngineCacheInputIdentityError(
                engine_id,
                input_ref.path,
                f"expected one {dependency_name!r} dependency, found {len(matches)}",
            )
        value = matches[0]
    else:
        raise EngineCacheInputIdentityError(
            engine_id, input_ref.path, f"unknown input kind {input_ref.kind!r}"
        )

    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise EngineCacheInputIdentityError(
            engine_id, input_ref.path, f"input is not canonicalizable ({exc})"
        ) from exc


@lru_cache(maxsize=None)
def _engine_cache_input_identity_digest(engine_id: str) -> bytes:
    try:
        inputs = ENGINE_CACHE_INPUTS[engine_id]
    except KeyError as exc:
        raise EngineCacheInputIdentityError(
            engine_id, "ENGINE_CACHE_INPUTS", "engine has no declared inputs"
        ) from exc

    digest = hashlib.blake2b(digest_size=_ENGINE_CACHE_INPUT_IDENTITY_BYTES)
    digest.update(b"engine-cache-input-identity-v1\0")
    digest.update(engine_id.encode("utf-8"))
    for input_ref in inputs:
        label = json.dumps(
            (input_ref.path, input_ref.kind, input_ref.selector),
            separators=(",", ":"),
        ).encode("utf-8")
        component = _cache_input_component(engine_id, input_ref)
        digest.update(len(label).to_bytes(4, "big"))
        digest.update(label)
        digest.update(len(component).to_bytes(8, "big"))
        digest.update(component)
    return digest.digest()


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
_CACHE_SCHEMA_VERSION = 3
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
)
_CACHE_INDEX_DEFINITIONS = (
    ("engine_outputs_box_seq", _TABLE, ("source_box", "row_seq"), True),
    ("engine_outputs_engine_version", _TABLE, ("engine", "chem_version"), False),
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
            found = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
            if found != expected:
                raise EngineCacheSchemaMismatch(table, expected, found)

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
        table_indexes = {
            row[1]: bool(row[2])
            for row in connection.execute(f"PRAGMA index_list({_TABLE})")
        } if _TABLE in found_tables else {}
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
    try:
        incomplete = _check_cache_schema(connection, allow_empty=True)
        if incomplete and readonly:
            connection.close()
            return None
        if not readonly:
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
) -> tuple[bool, bool]:
    """Insert one output only when absent; report whether existing bytes conflict."""
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
    return True, False


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


def _initialize_cold(connection: sqlite3.Connection, schema: str = "main") -> None:
    # Trace metadata only; cache lookup reads HOT and never consults this field.
    connection.execute(f"CREATE TABLE IF NOT EXISTS {schema}.engine_values "
                       "(key BLOB PRIMARY KEY, value BLOB NOT NULL, "
                       "input_fingerprint BLOB) WITHOUT ROWID")


def _store_cold(
    connection: sqlite3.Connection,
    path: Path,
    key: bytes,
    values: Mapping[str, Any],
    input_fingerprint: bytes | None = None,
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
        value = zlib.compress(pack(values), level=6)
        connection.execute(
            "INSERT OR REPLACE INTO cold.engine_values "
            "(key, value, input_fingerprint) VALUES (?, ?, ?)",
            (key, value, input_fingerprint),
        )
    else:
        connection.execute("DELETE FROM cold.engine_values WHERE key=?", (key,))


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
        counts["misses"] += 1

    solved = solve()
    hot = live_hot(solved)
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
            )
            if inserted:
                cold_values = solved.cold if isinstance(solved, EngineCacheValue) else {}
                input_fingerprint = None
                # Fingerprint source inputs only for a newly created COLD output row.
                if cold_values:
                    try:
                        input_fingerprint = _engine_cache_input_identity_digest(engine_id)
                    except EngineCacheInputIdentityError:
                        # Trace metadata must never prevent an otherwise valid cache write.
                        pass
                _store_cold(
                    connection,
                    path,
                    cache_key,
                    cold_values,
                    input_fingerprint,
                )
            connection.commit()
            if inserted:
                _record_bloom_key(connection, cache_key)
            counts = _counter_bucket(engine_id)
            if inserted:
                counts["stores"] += 1
            if conflict:
                counts["conflicts"] += 1
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
            inserted, conflict = _insert_cache_row_if_absent(
                connection,
                digest,
                value,
                engine,
                version,
                source_box,
                row_seq,
                provenance=provenance,
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
    expected = ["key", "value", "input_fingerprint"]
    found = [row[1] for row in connection.execute("PRAGMA cold.table_info(engine_values)")]
    if found != expected:
        raise EngineCacheSchemaMismatch("cold.engine_values", expected, found)
    # The lean COLD format has always used SQLite's default namespace (0).
    namespace = connection.execute("PRAGMA cold.user_version").fetchone()[0]
    if namespace != 0:
        raise EngineCacheSchemaMismatch("cold.user_version", ("0",), (str(namespace),))


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
                cold.execute("DELETE FROM engine_values WHERE NOT EXISTS ("
                             "SELECT 1 FROM hot.engine_outputs h WHERE h.key=engine_values.key)")
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
                            "SELECT value,input_fingerprint FROM cold.engine_values WHERE key=?", (key,),
                        ).fetchone() if has_cold else None
                        kept_cold = destination.execute(
                            "SELECT value,input_fingerprint FROM cold.engine_values WHERE key=?", (key,),
                        ).fetchone()
                        inserted, conflict = _insert_cache_row_if_absent(
                            destination, key, value, engine, version, box, seq, provenance=provenance,
                        )
                        conflict = conflict or bool(
                            incoming_cold and kept_cold and incoming_cold[0] != kept_cold[0]
                        )
                        counts["inserted"] += int(inserted)
                        counts["conflicts"] += int(conflict)
                        counts["already_present"] += int(not inserted and not conflict)
                        if conflict:
                            kept_box, kept_seq = destination.execute(
                                "SELECT source_box,row_seq FROM engine_outputs WHERE key=?", (key,),
                            ).fetchone()
                            report.write(json.dumps({
                                "key": key.hex(), "master": {"source_box": kept_box, "row_seq": kept_seq},
                                "input": {"source_box": box, "row_seq": seq},
                            }, sort_keys=True) + "\n")
                        elif incoming_cold:
                            destination.execute(
                                "INSERT OR IGNORE INTO cold.engine_values "
                                "(key, value, input_fingerprint) VALUES (?, ?, ?)",
                                (key, incoming_cold[0], incoming_cold[1]),
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


def drop_chemistry_version(engine_id: str, chem_version: str | int) -> int:
    path_value = _configured_engine_cache_path()
    if not path_value:
        return 0
    path = Path(path_value).expanduser()
    connection = _connect(path, readonly=False)
    assert connection is not None
    cursor = connection.execute(
        f"DELETE FROM {_TABLE} WHERE engine=? AND chem_version=?",
        (engine_id, str(chem_version)),
    )
    connection.commit()
    with _LOCK:
        for memory_key in tuple(_MEMORY_CACHE):
            if memory_key[0] == path_value:
                del _MEMORY_CACHE[memory_key]
    return cursor.rowcount
