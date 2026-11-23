import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from simulator import engine_cache

pytestmark = pytest.mark.engine_cache_off


def test_bloom_definite_miss_skips_the_disk_lookup(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.cached_engine_call("openimcc", {"temperature_K": 1700.0}, lambda: {"answer": 1})
    connection = engine_cache._connect(path, readonly=False)
    queries = []
    before_solve = []
    connection.set_trace_callback(queries.append)
    def solve():
        before_solve.extend(queries)
        return {"answer": 2}
    engine_cache.cached_engine_call("openimcc", {"temperature_K": 1701.0}, solve)
    assert not any(query.startswith("SELECT value, provenance_id FROM engine_outputs") for query in before_solve)


def test_readonly_reader_sees_external_wal_inserts(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.cached_engine_call("openimcc", {"temperature_K": 1700.0}, lambda: {"answer": 1})
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._MEMORY_CACHE.clear()
    assert engine_cache.cached_engine_call("openimcc", {"temperature_K": 1700.0}, lambda: 1 / 0) == {"answer": 1}
    environment = {**os.environ, "REGOLITH_ENGINE_CACHE_MODE": ""}
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    subprocess.run([
        sys.executable, "-c",
        "from simulator.engine_cache import cached_engine_call; "
        "cached_engine_call('openimcc', {'temperature_K':1701.0}, lambda:{'answer':2})",
    ], env=environment, check=True, timeout=30)
    assert engine_cache.cached_engine_call("openimcc", {"temperature_K": 1701.0}, lambda: 1 / 0) == {"answer": 2}


def _sqlite_max_mmap_bytes(connection: sqlite3.Connection) -> int:
    for (option,) in connection.execute("PRAGMA compile_options"):
        if option.startswith("MAX_MMAP_SIZE="):
            return int(option.split("=", 1)[1], 0)
    return 0


def test_readonly_hit_keeps_the_stored_bytes_when_pages_are_mapped(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    stored = {"answer": 1, "phase": "liquid"}
    try:
        assert engine_cache.cached_engine_call(
            "openimcc", {"temperature_K": 1700.0}, lambda: dict(stored)
        ) == stored
        writable = engine_cache._connect(path, readonly=False)
        assert writable is not None
        _assert_lookup_pragmas(writable)
        with sqlite3.connect(engine_cache._cache_uri(path), uri=True) as raw:
            stored_blob = bytes(raw.execute("SELECT value FROM engine_outputs").fetchone()[0])
        engine_cache._close_connections()
        engine_cache._MEMORY_CACHE.clear()
        monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
        assert engine_cache.cached_engine_call(
            "openimcc", {"temperature_K": 1700.0}, lambda: 1 / 0
        ) == stored
        readonly = engine_cache._connect(path, readonly=True)
        assert readonly is not None
        _assert_lookup_pragmas(readonly)
        assert bytes(readonly.execute("SELECT value FROM engine_outputs").fetchone()[0]) == stored_blob
    finally:
        engine_cache._close_connections()
        engine_cache._MEMORY_CACHE.clear()


def _assert_lookup_pragmas(connection: sqlite3.Connection) -> None:
    mapped = connection.execute("PRAGMA mmap_size").fetchone()[0]
    expected = min(engine_cache._LOOKUP_MMAP_BYTES, _sqlite_max_mmap_bytes(connection))
    assert expected > 0
    assert mapped == expected
    assert connection.execute("PRAGMA cache_size").fetchone()[0] == -engine_cache._LOOKUP_CACHE_KIB


def test_drop_version_invalidates_the_process_hot_table(tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.cached_engine_call("openimcc", {}, lambda: {"answer": 1})
    assert engine_cache.drop_chemistry_version("openimcc", 1) == 1
    assert engine_cache.cached_engine_call("openimcc", {}, lambda: {"answer": 2}) == {"answer": 2}
