"""Canonical binary scalars for cache keys, values and provenance.

Floats in keys use an integer significand and a binary fixed-point scale;
no decimal rounding is introduced. Values retain their IEEE-754 bits.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import struct


@dataclass(frozen=True)
class EngineCacheValue:
    hot: object
    cold: Mapping = field(default_factory=dict)


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


def pack(value, *, key: bool = False) -> bytes:
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
        mantissa, exponent = math.frexp(abs(value))
        if value == 0.0:
            exponent = -1074
        fixed = int(mantissa * (1 << 53))
        body = struct.pack(">HQ", exponent + 1074, fixed)
        negative = math.copysign(1.0, value) < 0
        return b"\x09" + bytes((0 if negative else 1,)) + (
            bytes(byte ^ 255 for byte in body) if negative else body
        )
    if isinstance(value, str):
        if key and value in _KEY_FIELDS:
            return bytes((128 + _KEY_FIELDS.index(value),))
        if not key and value in _VALUE_TOKEN_NUMBERS:
            return bytes((_VALUE_TOKEN_NUMBERS[value],))
        data = value.encode("utf-8")
        return b"\x05" + _unsigned(len(data)) + data
    if isinstance(value, (tuple, list)):
        tag = b"\x07" if isinstance(value, tuple) else b"\x06"
        return tag + _unsigned(len(value)) + b"".join(pack(item, key=key) for item in value)
    if isinstance(value, Mapping):
        if any(not isinstance(name, str) for name in value):
            raise TypeError("cache map names must be strings")
        names = sorted(value, key=lambda name: (
            _KEY_FIELDS.index(name) if key and name in _KEY_FIELDS else len(_KEY_FIELDS), name,
        ))
        return b"\x08" + _unsigned(len(names)) + b"".join(
            pack(name, key=key) + pack(value[name], key=key) for name in names
        )
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
