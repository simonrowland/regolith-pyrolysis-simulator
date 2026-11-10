from __future__ import annotations

import sqlite3
import pickle
import zlib
from dataclasses import dataclass
from pathlib import Path

import pytest

from simulator import engine_cache
from simulator.engine_cache import CacheValue, drop_chemistry_version, engine_cache_key, load_cache_rows
from simulator.melt_backend.base import EquilibriumResult
from simulator.engine_pool import EngineWorkerPool, WarmEngineWorker


def _cached_engine_call():
    call = getattr(engine_cache, "cached_engine_call", None)
    assert callable(call), "engine_cache must expose cached_engine_call"
    return call


def _engine_cache_counters(*, reset: bool = False):
    counters = getattr(engine_cache, "engine_cache_counters", None)
    assert callable(counters), "engine_cache must expose process-local counters"
    return counters(reset=reset)


def _bootstrap_cache_counter_worker():
    return None, "cache-counter-test-ready"


def _handle_cache_counter_request(_resource, request, _diagnostic_log):
    return engine_cache.cached_engine_call(
        "openimcc",
        request,
        lambda: {"answer": request["answer"]},
    )


def _cache_counter_worker(_index):
    return WarmEngineWorker(
        name="cache-counter-test",
        bootstrap=_bootstrap_cache_counter_worker,
        handler=_handle_cache_counter_request,
        startup_timeout_s=5.0,
        call_timeout_s=5.0,
    )


@dataclass(frozen=True)
class Refusal:
    status: str = "refusal"
    reason: str = "declined"


def test_disabled_cache_calls_solver_without_creating_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    _engine_cache_counters(reset=True)
    calls = 0

    def solve() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"answer": 7}

    call = _cached_engine_call()
    assert call("openimcc", {"temperature_K": 1700.0}, solve) == {"answer": 7}
    assert call("openimcc", {"temperature_K": 1700.0}, solve) == {"answer": 7}
    assert calls == 2
    assert _engine_cache_counters(reset=True) == {}
    assert list(tmp_path.iterdir()) == []


def test_cache_uses_canonical_request_and_wal_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    calls = 0

    def solve() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"answer": 42}

    call = _cached_engine_call()
    assert call("openimcc", {"temperature_K": 1700.0, "pack": "default"}, solve) == {
        "answer": 42
    }
    assert call("openimcc", {"pack": "default", "temperature_K": 1700.0}, solve) == {
        "answer": 42
    }
    assert calls == 1
    assert _engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
    }

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert tables == [("engine_outputs",)]
        columns = connection.execute("PRAGMA table_info(engine_outputs)").fetchall()
        assert [column[1] for column in columns] == [
            "key", "value", "engine", "chem_version", "source_box", "row_seq"
        ]
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='engine_outputs'"
        ).fetchone()[0]
        assert "WITHOUT ROWID" in schema.upper()


def test_raw_key_hot_json_cold_compression_and_exact_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    hot = {"status": "ok", "activity": 0.12345678901234566}
    cold = {"diagnostic": "not read on the hot path", "values": [1, 2, 3]}
    request = {"temperature_K": 1700.0}
    digest = engine_cache_key("openimcc", "1", request)

    assert isinstance(digest, bytes) and len(digest) == 32
    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: CacheValue(hot, cold)
    ) == hot
    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: pytest.fail("expected hot hit")
    ) == hot

    cold_database = database.with_name(database.name + ".cold")
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT key, value, engine, chem_version, source_box, row_seq "
            "FROM engine_outputs"
        ).fetchone()
    assert row[0] == digest
    assert row[1] == '{"activity":0.12345678901234566,"status":"ok"}'
    assert row[2:4] == ("openimcc", "1")
    with sqlite3.connect(cold_database) as connection:
        cold_value = connection.execute(
            "SELECT value FROM engine_outputs_cold WHERE key=?", (digest,)
        ).fetchone()[0]
    assert pickle.loads(zlib.decompress(cold_value)) == cold


def test_equilibrium_result_is_the_same_type_on_cold_and_warm_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "engine-cache.sqlite"))
    _engine_cache_counters(reset=True)
    expected = EquilibriumResult(
        liquid_fraction=0.25,
        phase_compositions={"melt": {"FeO": 0.5}},
        activity_coefficients={"FeO": 0.75},
    )
    expected.diagnostics = {"backend_status_reason": "fixture-code"}
    handle = SimpleNamespace(name="thermoengine", engine_settings={})
    calls = 0

    def solve() -> EquilibriumResult:
        nonlocal calls
        calls += 1
        return expected

    cold = _call_cached_backend_equilibrate(handle, {"temperature_C": 1000.0}, solve)
    warm = _call_cached_backend_equilibrate(handle, {"temperature_C": 1000.0}, solve)

    assert isinstance(cold, EquilibriumResult)
    assert isinstance(warm, EquilibriumResult)
    assert cold.phase_compositions == {}
    assert warm.phase_compositions == {}
    assert warm.activity_coefficients == expected.activity_coefficients
    assert warm.diagnostics == expected.diagnostics
    assert calls == 1
    assert _engine_cache_counters(reset=True)["thermoengine"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
    }


def test_equilibrium_hot_value_contains_only_scoring_inputs() -> None:
    result = EquilibriumResult(
        temperature_C=1000.0,
        pressure_bar=1.0,
        phases_present=["melt"],
        phase_masses_kg={"melt": 1.0},
        liquid_fraction=0.25,
        phase_assemblage_available=True,
        vapor_pressures_Pa={"H2O": 1200.0},
        vapor_pressures_source={"H2O": "thermoengine"},
        activity_coefficients={"FeO": 0.75},
        warnings=["AlphaMELTS model notice"],
        status="ok",
        diagnostics={"backend_status_reason": "fixture-code", "unused": "large"},
    )
    result.reported_activity_coefficients = {"FeO": 0.9}
    result.activity_coefficient_details = {"FeO": {"basis": "fixture"}}
    result.vaporock_full_speciation_Pa = {"CO": 42.0}
    result.imcc_notices = [{"kind": "fixture_flag"}]
    result.imcc_model_id = "fixture-model"
    result.liquid_composition_wt_pct = {"FeO": 10.0}
    result.fe_redox_split = {"FeO": 0.2}

    value = result.to_engine_cache_value()

    assert set(value.hot) == {
        "liquid_fraction",
        "phase_assemblage_available",
        "vapor_pressures_Pa",
        "vapor_pressures_source",
        "activity_coefficients",
        "status",
        "reported_activity_coefficients",
        "activity_coefficient_details",
        "vaporock_full_speciation_Pa",
        "liquid_composition_wt_pct",
        "fe_redox_split",
    }
    assert "phase_masses_kg" in value.cold
    assert "temperature_C" in value.cold
    assert "diagnostics" in value.cold
    assert value.cold["diagnostics"] == {
        "backend_status_reason": "fixture-code",
        "unused": "large",
    }
    assert "warnings" in value.cold
    assert "imcc_notices" in value.cold
    assert "imcc_model_id" in value.cold
    restored = EquilibriumResult.from_engine_cache_value(value.hot)
    assert restored.activity_coefficients == result.activity_coefficients
    assert restored.vapor_pressures_Pa == result.vapor_pressures_Pa
    assert restored.diagnostics == {}
    assert restored.phase_masses_kg == {}


def test_bulk_loader_is_idempotent_and_chemistry_version_can_be_dropped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    digest = engine_cache_key("magemin", "1", {"pressure_bar": 1.0})
    rows = [(digest, {"status": "ok"}, "magemin", "1", "box-a", 1001)]

    assert load_cache_rows(database, rows) == 1
    assert load_cache_rows(database, rows) == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1
    assert drop_chemistry_version("magemin", "1") == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_readonly_mode_serves_hits_and_solves_misses_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    digest = engine_cache_key("vaporock", "1", {"temperature_K": 900.0})
    load_cache_rows(
        database,
        [(digest, {"pressure": 2.0}, "vaporock", "1", "box-b", 2001)],
    )
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    _engine_cache_counters(reset=True)

    assert engine_cache.cached_engine_call(
        "vaporock", {"temperature_K": 900.0}, lambda: pytest.fail("expected hit")
    ) == {"pressure": 2.0}
    assert engine_cache.cached_engine_call(
        "vaporock", {"temperature_K": 901.0}, lambda: {"pressure": 3.0}
    ) == {"pressure": 3.0}
    assert _engine_cache_counters(reset=True)["vaporock"] == {
        "hits": 1, "misses": 1, "stores": 0
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 1


def test_non_authoritative_engine_result_is_cacheable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    _engine_cache_counters(reset=True)
    calls = 0

    def solve() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"status": "non_authoritative", "pressure_Pa": 42.0}

    first = engine_cache.cached_engine_call("vaporock", {"T_K": 1700.0}, solve)
    second = engine_cache.cached_engine_call(
        "vaporock", {"T_K": 1700.0}, solve
    )

    assert first == second == {"status": "non_authoritative", "pressure_Pa": 42.0}
    assert calls == 1
    assert _engine_cache_counters(reset=True)["vaporock"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
    }


def test_engine_worker_returns_cache_counters_to_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    _engine_cache_counters(reset=True)
    pool = EngineWorkerPool(_cache_counter_worker, size=1)
    request = {"answer": 42}
    try:
        assert pool.submit(request, timeout_s=5.0).result(timeout=6.0) == request
        assert pool.submit(request, timeout_s=5.0).result(timeout=6.0) == request
        assert _engine_cache_counters(reset=True)["openimcc"] == {
            "hits": 1,
            "misses": 1,
            "stores": 1,
        }
    finally:
        pool.close(cancel_pending=True, timeout_s=5.0)


def test_exceptions_and_typed_refusals_are_not_stored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    _engine_cache_counters(reset=True)
    call = _cached_engine_call()
    request = {"composition": {"SiO2": 1.0}}

    with pytest.raises(ValueError, match="engine failed"):
        call("magemin", request, lambda: (_ for _ in ()).throw(ValueError("engine failed")))
    refusals = iter((Refusal(), Refusal()))
    assert call("magemin", request, lambda: next(refusals)) == Refusal()
    assert call("magemin", request, lambda: next(refusals)) == Refusal()
    assert _engine_cache_counters(reset=True)["magemin"] == {
        "hits": 0,
        "misses": 3,
        "stores": 0,
    }


def test_corrupt_hot_json_is_a_miss_and_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    call = _cached_engine_call()
    request = {"temperature_K": 1600.0}
    calls = 0

    def solve() -> str:
        nonlocal calls
        calls += 1
        return "result"

    assert call("alphamelts", request, solve) == "result"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE engine_outputs SET value = ?", (b"not a pickle",))
    assert call("alphamelts", request, solve) == "result"
    assert call("alphamelts", request, solve) == "result"
    assert calls == 2
    assert _engine_cache_counters(reset=True)["alphamelts"] == {
        "hits": 1,
        "misses": 2,
        "stores": 2,
    }
