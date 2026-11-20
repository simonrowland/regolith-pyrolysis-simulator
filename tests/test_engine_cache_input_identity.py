"""Engine cache keys ignore source identity; cold rows retain it for tracing."""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from simulator import engine_cache
from simulator.engine_cache import EngineCacheValue


_EXPECTED_SOURCE_PATHS = {
    "pyproject.toml",
    "engines/engines.lock.toml",
    "data/vapor_pressures.yaml",
    "data/setpoints.yaml",
    "data/species_catalog.yaml",
    "data/literature/extracts/nasa-cea-thermo.yaml",
    "data/literature/extracts/ivtan-mno-coo-thermo.yaml",
}


@pytest.fixture(autouse=True)
def _isolated_cache():
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    yield
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()


def _mutate_source(relative_path: str, original: bytes) -> bytes:
    if relative_path == "pyproject.toml":
        match = re.search(
            rb'(?m)^\s*"openimcc @ [^"]+@([0-9a-f]{40})"', original
        )
        assert match is not None
        start, stop = match.span(1)
    elif relative_path == "engines/engines.lock.toml":
        section = re.search(
            rb"(?ms)^\[engines\.magemin\]\s*$\n(.*?)(?=^\[|\Z)",
            original,
        )
        assert section is not None
        match = re.search(
            rb'(?m)^commit\s*=\s*"([0-9a-f]{40})"', section.group(1)
        )
        assert match is not None
        start = section.start(1) + match.start(1)
        stop = section.start(1) + match.end(1)
    else:
        return original + b"\n# source mutation for cache-key invariance\n"

    token = original[start:stop]
    changed = (b"0" if token[:1] != b"0" else b"1") + token[1:]
    return original[:start] + changed + original[stop:]


def _assert_probe_key_is_checkout_and_source_invariant(
    engine: str,
    version: int,
    probe_request: dict,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declared = {
        ref.path
        for refs in engine_cache.ENGINE_CACHE_INPUTS.values()
        for ref in refs
    }
    assert declared == _EXPECTED_SOURCE_PATHS

    source_root = engine_cache._ENGINE_CACHE_ROOT
    copied_root = tmp_path / "second-checkout"
    for relative_path in declared | {"data/binary_pots.yaml"}:
        source = source_root / relative_path
        target = copied_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def clear_fingerprint_memo() -> None:
        clear = getattr(
            engine_cache._engine_cache_input_identity_digest, "cache_clear", None
        )
        if clear is not None:
            clear()

    checkout_keys = []
    for root in (source_root, copied_root):
        monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", root)
        clear_fingerprint_memo()
        checkout_keys.append(
            engine_cache.engine_cache_key(engine, version, probe_request)
        )
    assert checkout_keys[0] == checkout_keys[1], (
        f"{engine} canonical probe key changed across checkout roots"
    )

    monkeypatch.setattr(engine_cache, "_ENGINE_CACHE_ROOT", copied_root)
    expected_key = checkout_keys[1]
    for relative_path in sorted(declared):
        source = copied_root / relative_path
        original = source.read_bytes()
        source.write_bytes(_mutate_source(relative_path, original))
        clear_fingerprint_memo()
        assert engine_cache.engine_cache_key(
            engine, version, probe_request
        ) == expected_key, relative_path
        source.write_bytes(original)
        clear_fingerprint_memo()


def test_new_cold_row_stores_current_input_fingerprint(tmp_path, monkeypatch) -> None:
    path = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    request = {"traceability": "cold-row"}

    result = engine_cache.cached_engine_call(
        "magemin",
        request,
        lambda: EngineCacheValue({"answer": 1}, {"enthalpy": 2.0}),
    )

    assert result == {"answer": 1}
    expected = engine_cache._engine_cache_input_identity_digest("magemin")
    with sqlite3.connect(str(path) + ".cold") as connection:
        row = connection.execute(
            "SELECT input_fingerprint FROM engine_values"
        ).fetchone()
    assert row == (expected,)


def test_stale_cold_fingerprint_does_not_affect_a_hit_or_emit_notice(
    tmp_path,
    monkeypatch,
    caplog,
) -> None:
    path = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    request = {"traceability": "stale-cold-row"}
    expected = {"answer": 7}

    engine_cache.cached_engine_call(
        "magemin",
        request,
        lambda: EngineCacheValue(expected, {"enthalpy": 8.0}),
    )
    with sqlite3.connect(str(path) + ".cold") as connection:
        connection.execute(
            "UPDATE engine_values SET input_fingerprint=?", (b"stale",)
        )
    engine_cache._MEMORY_CACHE.clear()

    def digest_must_not_be_read(_engine):
        raise AssertionError("cold fingerprint was read on the hit path")

    monkeypatch.setattr(
        engine_cache, "_engine_cache_input_identity_digest", digest_must_not_be_read
    )
    with caplog.at_level(logging.INFO):
        actual = engine_cache.cached_engine_call(
            "magemin", request, lambda: pytest.fail("stale COLD row caused a miss")
        )

    assert actual == expected
    assert not any("engine inputs changed" in record.getMessage() for record in caplog.records)
