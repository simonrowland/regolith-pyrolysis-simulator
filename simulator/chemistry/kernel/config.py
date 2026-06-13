"""Diagnostic-only chemistry-kernel configuration values."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class OxygenSinkChannelMode(str, Enum):
    LEGACY_SOURCE_EQUILIBRIUM = "legacy_source_equilibrium"
    PLUME_OXIDATION_DIAGNOSTIC = "plume_oxidation_diagnostic"
    DEPOSIT_GETTERING_DIAGNOSTIC = "deposit_gettering_diagnostic"
    MELT_REDOX_DIAGNOSTIC = "melt_redox_diagnostic"
    POST_RUN_AIR_ANNOTATION = "post_run_air_annotation"


OXYGEN_SINK_CHANNEL_MODE_KEY = "oxygen_sink_channel_mode"
DEFAULT_OXYGEN_SINK_CHANNEL_MODE = OxygenSinkChannelMode.LEGACY_SOURCE_EQUILIBRIUM
OXYGEN_SINK_CHANNEL_MODE_VALUES: tuple[str, ...] = tuple(
    mode.value for mode in OxygenSinkChannelMode
)


def normalize_oxygen_sink_channel_mode(value: Any = None) -> OxygenSinkChannelMode:
    if value is None:
        return DEFAULT_OXYGEN_SINK_CHANNEL_MODE
    if isinstance(value, OxygenSinkChannelMode):
        return value
    if isinstance(value, str):
        try:
            return OxygenSinkChannelMode(value)
        except ValueError as exc:
            joined = ", ".join(OXYGEN_SINK_CHANNEL_MODE_VALUES)
            raise ValueError(
                f"{OXYGEN_SINK_CHANNEL_MODE_KEY} must be one of: {joined}"
            ) from exc
    raise TypeError(f"{OXYGEN_SINK_CHANNEL_MODE_KEY} must be a string")


def normalize_chemistry_kernel_config(
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if config is None:
        return {}
    if not isinstance(config, Mapping):
        raise TypeError("chemistry_kernel config must be a mapping")
    if not all(isinstance(key, str) for key in config):
        raise ValueError("chemistry_kernel keys must be strings")
    normalized = dict(config)
    if OXYGEN_SINK_CHANNEL_MODE_KEY in normalized:
        normalized[OXYGEN_SINK_CHANNEL_MODE_KEY] = normalize_oxygen_sink_channel_mode(
            normalized[OXYGEN_SINK_CHANNEL_MODE_KEY]
        ).value
    return normalized
