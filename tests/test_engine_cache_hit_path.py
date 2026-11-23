import os
from pathlib import Path
import subprocess
import sys

from simulator import engine_cache


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


def test_drop_version_invalidates_the_process_hot_table(tmp_path, monkeypatch):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.cached_engine_call("openimcc", {}, lambda: {"answer": 1})
    assert engine_cache.drop_chemistry_version("openimcc", engine_cache.ENGINE_CHEMISTRY_VERSION["openimcc"]) == 1
    assert engine_cache.cached_engine_call("openimcc", {}, lambda: {"answer": 2}) == {"answer": 2}
