"""Live numeric probes require engine version bumps when outputs change."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence

import pytest

from simulator import engine_cache


# Each value pins the current cache chemistry version with one live solver
# result. Update the pair together after a reviewed numeric engine change.
ENGINE_GOLDEN_PROBES = {
    "openimcc": (1, "98623a0ecc0edd9e4c9ac83bb9ed41b31c0953b6233da63a9240979b54bfb6c8"),
    "internal-analytical": (2, "473e906f52d87e6c1d7292ed97d229b7ae58dc88b9d825ed2e44fcb71895c731"),
    "vaporock": (1, "ba3407422969eb4e26eb6e94095d5c6d2e25db7c7686eaaef8e41580064dad86"),
    "magemin": (3, "06a864bc03d479db26924099e01d3e7f4edbf90271c7c04dcba1c8f5cb98a6d4"),
    "alphamelts": (3, "c829f082038f1e2690b4d024f7355fb66068572d09a94affb07dbc8623203db9"),
    "thermoengine": (3, "670470cc86c39483275c4eb3d0e7eb59c4d5f0975d45c6d25133584a2ab974df"),
}

_NON_OUTPUT_NUMERIC_FIELDS = frozenset(
    {
        "wall_s",
        "cpu_s",
        "temperature_K",
        "physical_pressure_bar",
        "pressure_bar",
        "po2",
        "fO2_log",
    }
)


def _numeric_leaves(value, path=()):
    if isinstance(value, bool) or value is None:
        return {}
    if isinstance(value, int):
        return {".".join(path): value}
    if isinstance(value, float):
        if math.isfinite(value):
            return {".".join(path): value.hex()}
        return {".".join(path): repr(value)}
    if isinstance(value, Mapping):
        leaves = {}
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            name = str(key)
            if not path and name in _NON_OUTPUT_NUMERIC_FIELDS:
                continue
            leaves.update(_numeric_leaves(item, (*path, name)))
        return leaves
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        leaves = {}
        for index, item in enumerate(value):
            leaves.update(_numeric_leaves(item, (*path, str(index))))
        return leaves
    return {}


def _numeric_output_digest(cell) -> str:
    numeric = _numeric_leaves(cell.as_payload())
    assert numeric, f"{cell.engine}: probe returned no numeric output fields"
    encoded = json.dumps(numeric, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _assert_probe_matches_golden(engine: str, version: int, actual: str) -> None:
    expected_version, expected_digest = ENGINE_GOLDEN_PROBES[engine]
    assert version == expected_version, (
        f"{engine}: ENGINE_CHEMISTRY_VERSION changed from {expected_version} "
        f"to {version}; review the numeric probe and update its pinned pair"
    )
    assert actual == expected_digest, (
        f"{engine}: live numeric probe changed without its pinned version/digest "
        f"pair; bump ENGINE_CHEMISTRY_VERSION after confirming the numeric change. "
        f"Expected {expected_digest}, got {actual}"
    )


@pytest.mark.xdist_group("engine-cache-golden-probes")
@pytest.mark.parametrize("engine_name", tuple(engine_cache.ENGINE_CHEMISTRY_VERSION))
def test_live_engine_numeric_probe_matches_version(engine_name, request) -> None:
    from simulator.battery.enums import Engine
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
    )
    from simulator.engine_cache import _without_engine_cache
    from tests.battery.cache_test_support import open_available_engine

    loaded_pots, _metadata = load_binary_pots()
    pots = {pot.pot_id: pot for pot in loaded_pots}
    handle = open_available_engine(Engine(engine_name), nodeid=request.node.nodeid)
    try:
        with _without_engine_cache():
            cell = equilibrate_cell(
                handle,
                pots["mgo_sio2_40_60"],
                temperature_K=2000.0,
                po2=Po2Request(mode="commanded", po2_bar=1.0e-8),
                timeout_s=90.0,
                isolated=False,
            )
        _assert_probe_matches_golden(
            engine_name,
            engine_cache.ENGINE_CHEMISTRY_VERSION[engine_name],
            _numeric_output_digest(cell),
        )
    finally:
        close_battery_engine_handle(handle)


def test_numeric_probe_change_without_version_bump_fails_guard() -> None:
    version, digest = ENGINE_GOLDEN_PROBES["internal-analytical"]
    changed = "0" * len(digest)
    assert changed != digest
    with pytest.raises(AssertionError, match="bump ENGINE_CHEMISTRY_VERSION"):
        _assert_probe_matches_golden("internal-analytical", version, changed)
