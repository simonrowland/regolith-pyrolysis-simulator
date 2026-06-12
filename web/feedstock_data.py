"""Feedstock loading helpers for the simulator UI."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from simulator.feedstock_guard import loadable_feedstocks


DATA_DIR = Path(__file__).parent.parent / 'data'


def _env_flag(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }


def debug_feedstocks_enabled() -> bool:
    return (
        _env_flag('REGOLITH_DEBUG_FEEDSTOCKS')
        or _env_flag('REGOLITH_FLASK_DEBUG')
    )


def load_yaml(filename: str) -> dict[str, Any]:
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_feedstock_groups(
    *,
    include_custom: bool = False,
    include_debug: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    base = load_yaml('feedstocks.yaml')
    if include_custom:
        custom = load_yaml('custom_compositions.yaml')
        if custom:
            base.update(custom)
    base = loadable_feedstocks(base)

    if include_debug is None:
        include_debug = debug_feedstocks_enabled()
    debug = (
        loadable_feedstocks(load_yaml('debug_feedstocks.yaml'))
        if include_debug
        else {}
    )
    return base, debug


def load_visible_feedstocks(
    *,
    include_custom: bool = False,
    include_debug: bool | None = None,
) -> dict[str, Any]:
    base, debug = load_feedstock_groups(
        include_custom=include_custom,
        include_debug=include_debug,
    )
    merged = dict(base)
    merged.update(debug)
    return merged


def get_visible_feedstock(
    key: str,
    *,
    include_custom: bool = False,
    include_debug: bool | None = None,
) -> Any | None:
    return load_visible_feedstocks(
        include_custom=include_custom,
        include_debug=include_debug,
    ).get(key)
