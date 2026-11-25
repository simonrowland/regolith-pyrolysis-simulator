"""JSON-safe conversion for simulator diagnostic and result payloads."""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from typing import Any


def _json_safe(value: Any) -> Any:
    """Recursively convert ``value`` into a JSON-serialisable form.

    Non-finite numeric telemetry exports as ``None`` so strict JSON consumers
    see schema-compatible nulls instead of Python-only NaN/Infinity tokens or
    stringified pseudo-numbers.
    """

    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_json_safe(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, bool)) or value is None:
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if dataclasses.is_dataclass(value):
        return _json_safe(dataclasses.asdict(value))
    # Enums, IntentResult-like wrappers -- fall back to repr so the
    # shadow_trace stays informative without leaking object identity.
    return repr(value)
