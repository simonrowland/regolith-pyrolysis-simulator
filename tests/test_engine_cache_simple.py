from __future__ import annotations

import json
import sqlite3
import pickle
import threading
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
    assert json.loads(zlib.decompress(row[1])) == {
        "activity": 0.12345678901234566,
        "status": "ok",
    }
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
    assert cold.diagnostics == {}
    assert warm.diagnostics == {}
    assert calls == 1
    assert _engine_cache_counters(reset=True)["thermoengine"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
    }


def test_mapping_equilibrium_result_uses_compact_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )

    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    handle = SimpleNamespace(name="thermoengine", engine_settings={})
    calls = 0
    result = {
        "status": "ok",
        "liquid_fraction": 0.25,
        "phase_assemblage_available": True,
        "activity_coefficients": {"FeO": 0.75},
        "vapor_pressures_Pa": {"CO": 42.0},
        "vapor_pressures_source": {"CO": "thermoengine"},
        "phase_compositions": {"melt": {"FeO": 0.5}},
        "diagnostics": {"backend_status_reason": "unused diagnostic prose"},
    }

    def solve() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return result

    cold = _call_cached_backend_equilibrate(
        handle, {"temperature_C": 1000.0}, solve
    )
    warm = _call_cached_backend_equilibrate(
        handle, {"temperature_C": 1000.0}, solve
    )

    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT value FROM engine_outputs"
        ).fetchone()[0]
    assert isinstance(json.loads(zlib.decompress(stored)), list)
    assert cold.activity_coefficients == warm.activity_coefficients == {"FeO": 0.75}
    assert cold.diagnostics == warm.diagnostics == {}
    assert cold.phase_compositions == warm.phase_compositions == {}
    assert calls == 1


def test_isolated_cell_cache_stores_only_compact_hot_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import _IsolatedCellWorker

    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    result = {
        "pot_id": "worker-pot",
        "engine": "thermoengine",
        "temperature_K": 1800.0,
        "po2": {"mode": "engine_default", "po2_bar": None},
        "status": "ok",
        "refusal_reason": None,
        "engine_status": "ok",
        "engine_reason": "liquid reference from a verbose engine diagnostic",
        "melt_activities": {"SiO2": 0.5},
        "gas_partial_pressures_Pa": {"SiO": 1.25},
        "liquid_fraction": 1.0,
        "wall_s": 99.0,
        "cpu_s": 22.0,
        "hostname": "worker-host",
        "vapor_pressures_source": {"SiO": "antoine_fallback_from_vaporock:detail"},
        "notices": [{"kind": "fallback", "reason": "long diagnostic prose"}],
        "authority": "extrapolated",
        "certified_band": {"temperature_K": [1500.0, 1900.0]},
        "melt_activity_coefficients": {"SiO2": 0.75},
        "melt_activity_coefficient_details": {
            "SiO2": {
                "coefficient_basis": "single_cation",
                "standard_state": {"convention": "raoultian", "phase": "l"},
                "unused_detail": "long per-phase detail",
            }
        },
        "activities_standard_state": {"convention": "raoultian", "phase": "l"},
        "liquid_composition_wt_pct": {"SiO2": 51.0},
        "fe_redox_split": {"Fe3Fet": 0.1},
    }

    class Worker:
        calls = 0

        def call(self, _payload, *, timeout_s):
            self.calls += 1
            assert timeout_s == 12.0
            return dict(result)

    isolated = object.__new__(_IsolatedCellWorker)
    isolated.engine = "thermoengine"
    isolated.engine_settings = {}
    isolated._lock = threading.Lock()
    isolated._worker = Worker()
    request = {
        "pot_id": "caller-pot",
        "temperature_K": 1800.0,
        "po2": {"mode": "engine_default", "po2_bar": None},
        "arm": "headline",
        "composition_wt_pct": {"SiO2": 50.0},
    }

    cold = isolated.request(request, timeout_s=12.0)
    warm = isolated.request(request, timeout_s=12.0)

    with sqlite3.connect(database) as connection:
        hot_value = connection.execute("SELECT value FROM engine_outputs").fetchone()[0]
    hot_json = zlib.decompress(hot_value)
    assert isinstance(json.loads(hot_json), list)
    assert len(hot_value) <= 400
    assert "verbose engine diagnostic" not in hot_json.decode("ascii")
    assert cold["pot_id"] == warm["pot_id"] == "caller-pot"
    assert cold["melt_activities"] == warm["melt_activities"] == {"SiO2": 0.5}
    assert cold["gas_partial_pressures_Pa"] == warm["gas_partial_pressures_Pa"] == {
        "SiO": 1.25
    }
    assert cold["engine_reason"] == warm["engine_reason"] == "liquid"
    assert isolated._worker.calls == 1


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
    result.activity_coefficient_details = {
        "FeO": {
            "value": 0.9,
            "coefficient_basis": "single_cation",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": "FeO",
            },
            "unused_detail": "large",
        }
    }
    result.vaporock_full_speciation_Pa = {"CO": 42.0}
    result.imcc_notices = [{"kind": "fixture_flag"}]
    result.imcc_model_id = "fixture-model"
    result.liquid_composition_wt_pct = {"FeO": 10.0}
    result.fe_redox_split = {"FeO": 0.2}

    value = result.to_engine_cache_value()

    assert isinstance(value.hot, list)
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
    assert restored.vapor_pressures_source == {}
    assert restored.status == "ok"
    assert restored.phase_assemblage_available is True
    assert restored.reported_activity_coefficients == {"FeO": 0.9}
    assert restored.activity_coefficient_details == {
        "FeO": {
            "coefficient_basis": "single_cation",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": "FeO",
            },
        }
    }
    assert restored.vaporock_full_speciation_Pa == {"CO": 42.0}
    assert restored.liquid_composition_wt_pct == {"FeO": 10.0}
    assert restored.fe_redox_split == {"FeO": 0.2}
    assert restored.diagnostics == {}
    assert restored.phase_masses_kg == {}


def test_openimcc_hot_json_compresses_to_400_bytes() -> None:
    result = EquilibriumResult(
        liquid_fraction=1.0,
        activity_coefficients={
            "SiO2": 0.007355492127623946,
            "MgO": 0.0,
            "FeO": 0.0,
            "CaO": 0.025718780542185595,
            "Al2O3": 0.0,
            "TiO2": 0.0,
            "Na2O": 0.0,
            "K2O": 0.0,
        },
        vapor_pressures_Pa={
            "SiO": 0.05272248338791592,
            "SiO2": 5.687460979368064e-05,
            "O": 0.0022838425816733105,
            "Si": 2.4665766051704816e-09,
            "CaO": 4.2523914297704835e-06,
            "Ca": 0.0019192690260704188,
            "O2": 0.000337681211470004,
            "Si2": 2.783098537113361e-20,
            "Si3": 7.889378813945906e-30,
            "WO": 5.383975292508744e-06,
            "WO2": 0.0003703733875241695,
            "WO3": 0.0019867368862648144,
            "W2O6": 0.024686751898453776,
            "W3O8": 0.0004717790627170212,
            "W3O9": 0.0002566385044588118,
            "W4O12": 2.079863897110883e-07,
        },
        vapor_pressures_source={
            "SiO": "openimcc-gas-table:sha256:d6998a34dd62e344e6756f1ddc76d276454cf749be0da99b20436f0588ddb87d",
            "WO": "cell_shared_janaf:O-027:a7b478fbb89b4bf1fbd560c7c8a1400aa8f67b406db3e6dd5d380f037254b722",
        },
    )
    result.reported_activity_coefficients = {
        "SiO2": 0.01838873031905986,
        "CaO": 0.04286463423697599,
    }
    result.activity_coefficient_details = {
        name: {
            "value": value,
            "coefficient_basis": "single_cation",
            "standard_state": {
                "convention": "raoultian_pure_endmember",
                "phase": "l",
                "component_basis": name,
            },
        }
        for name, value in result.reported_activity_coefficients.items()
    }

    hot = result.to_engine_cache_value().hot
    raw = json.dumps(hot, separators=(",", ":"), ensure_ascii=True).encode("ascii")

    assert len(zlib.compress(raw, level=6)) <= 400


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
