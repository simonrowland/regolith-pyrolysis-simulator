"""A new COLD row records the runtime build. A hit does not read it."""

from __future__ import annotations

import sqlite3

import pytest

from simulator import engine_cache
from simulator.engine_cache import EngineCacheValue

pytestmark = pytest.mark.engine_cache_off


@pytest.fixture(autouse=True)
def _isolated_cache():
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    yield
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()


def _assert_probe_key_ignores_source_changes(
    engine: str,
    version: int,
    probe_request: dict,
    _tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing forensic build identity must not change engine request keys."""
    expected_key = engine_cache.engine_cache_key(engine, version, probe_request)
    original_identity = engine_cache._forensic_build_identity
    original_value = original_identity(engine)

    def changed_identity(engine_id: str) -> str:
        if engine_id == engine:
            return f"{original_value}:changed-source"
        return original_identity(engine_id)

    monkeypatch.setattr(engine_cache, "_forensic_build_identity", changed_identity)
    assert engine_cache._forensic_build_identity(engine) != original_value
    assert engine_cache.engine_cache_key(engine, version, probe_request) == expected_key


def test_new_cold_row_records_forensic_build_identity(tmp_path, monkeypatch) -> None:
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
    expected = engine_cache._forensic_build_identity("magemin")
    with sqlite3.connect(str(path) + ".cold") as connection:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(engine_values)")
        ]
        row = connection.execute(
            "SELECT b.engine, b.identity FROM engine_values v "
            "JOIN engine_builds b ON b.id = v.engine_build_id"
        ).fetchone()
    assert columns == ["row_seq", "value", "engine_build_id"]
    assert row == ("magemin", expected)


def test_hit_does_not_read_build_identity(tmp_path, monkeypatch) -> None:
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
    engine_cache._MEMORY_CACHE.clear()

    def identity_must_not_be_read(_engine):
        raise AssertionError("build identity was read on the hit path")

    monkeypatch.setattr(
        engine_cache, "_forensic_build_identity", identity_must_not_be_read
    )
    actual = engine_cache.cached_engine_call(
        "magemin", request, lambda: pytest.fail("stored row caused a miss")
    )

    assert actual == expected
