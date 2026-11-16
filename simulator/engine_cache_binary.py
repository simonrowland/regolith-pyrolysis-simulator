"""Canonical binary scalars for cache keys, values and provenance.

Floats in keys use an integer significand and a binary fixed-point scale;
no decimal rounding is introduced. Values retain their IEEE-754 bits.
"""

from collections.abc import Mapping
import math
import struct


_KEY_FIELDS = (
    "composition_wt_pct", "composition_mol", "composition_kg", "composition",
    "temperature_K", "temperature_C", "physical_pressure_bar", "pressure_bar",
    "fO2_log", "po2", "kwargs", "engine_settings",
)


def _unsigned(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def pack(value, *, key: bool = False) -> bytes:
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
        raise ValueError(f"invalid cache value tag {tag}")

    result = read()
    if position != len(data):
        raise ValueError("trailing cache bytes")
    return result
