import os
from pathlib import Path
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
    assert not any("engine_outputs" in query for query in before_solve)


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
    current_version = engine_cache.ENGINE_CHEMISTRY_VERSION["openimcc"]
    assert engine_cache.drop_chemistry_version("openimcc", current_version) == 1
    assert engine_cache.cached_engine_call("openimcc", {}, lambda: {"answer": 2}) == {"answer": 2}


def test_magemin_omitted_oxygen_cache_misses_old_record_and_uses_default(tmp_path, monkeypatch):
    from simulator.diagnostic_helpers.binary_pot_battery import (
        EngineHandle,
        _call_cached_backend_equilibrate,
        normalize_engine_settings,
    )
    from simulator.melt_backend import magemin as magemin_module
    from simulator.melt_backend.magemin import MAGEMinBackend

    database = tmp_path / "magemin.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache._MEMORY_CACHE.clear()
    settings = normalize_engine_settings("magemin", None)
    kwargs = {
        "temperature_C": 1500.0,
        "composition_mol": {"SiO2": 1.0},
        "pressure_bar": 1.0,
    }
    request = {
        "kwargs": kwargs,
        "engine_settings": engine_cache.engine_cache_settings("magemin", settings),
    }
    old_key = engine_cache.engine_cache_key("magemin", 4, request)
    connection = engine_cache._connect(database, readonly=False)
    assert connection is not None
    engine_cache._insert_cache_row_if_absent(
        connection,
        old_key,
        engine_cache.pack({"status": "ok", "fO2_log": None}),
        "magemin",
        "4",
        "laptop",
    )
    connection.commit()

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    calls = []

    def fail_native_call(**native_kwargs):
        calls.append(native_kwargs)
        raise magemin_module._MAGEMinEngineCallError("TestFailure", "probe")

    backend._call_magemin = fail_native_call
    handle = EngineHandle(
        name="magemin",
        backend=backend,
        available=True,
        unavailable_reason=None,
        takes_fo2=True,
        supports_intrinsic_fo2=True,
        engine_settings=settings,
    )
    result = _call_cached_backend_equilibrate(
        handle, kwargs, lambda: backend.equilibrate(**kwargs)
    )

    assert engine_cache.ENGINE_CHEMISTRY_VERSION["magemin"] == 5
    assert result.status == "not_converged"
    assert calls and calls[0]["fO2_log"] == -9.0
    assert result.diagnostics["magemin_omitted_oxygen_default"]["value"] == -9.0
