from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping

import pytest

from simulator import engine_cache
from simulator.battery.score import is_store_stamp_payload
from simulator.diagnostic_helpers.binary_pot_battery import (
    _IsolatedCellWorker,
    normalize_engine_settings,
)
from scripts.battery_cache_equivalence import _without_cache_counters

pytestmark = pytest.mark.engine_cache_off


def _canonical_residual_rows(path: Path) -> bytes:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            payload = json.loads(line)
            if not is_store_stamp_payload(payload):
                rows.append(
                    json.dumps(
                        _without_cache_counters(payload),
                        sort_keys=True,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
    return b"\n".join(rows) + (b"\n" if rows else b"")


class _FakeWorker:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, request: Any, *, timeout_s: float) -> dict[str, Any]:
        self.calls += 1
        return {"status": "ok", "refusal_reason": None, "result": 17}


def test_isolated_cell_qualification_decoration_is_omitted_from_cache_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    worker = object.__new__(_IsolatedCellWorker)
    worker.engine = "magemin"
    worker.engine_settings = normalize_engine_settings("magemin", None)
    worker._lock = threading.Lock()
    worker._worker = _FakeWorker()
    first = {
        "engine": "magemin",
        "pot_id": "first-pot",
        "qualification": False,
        "composition_wt_pct": {"SiO2": 50.0, "FeO": 50.0},
        "temperature_K": 1700.0,
        "physical_pressure_bar": 1.0,
        "po2": {"mode": "engine_default", "po2_bar": None},
        "engine_settings": {},
        "arm": "headline",
    }
    second = {
        **first,
        "pot_id": "second-pot",
        "qualification": True,
        "arm": "qualification",
    }

    first_result = worker.request(first, timeout_s=90.0)
    second_result = worker.request(second, timeout_s=90.0)

    assert worker._worker.calls == 1
    assert first_result["pot_id"] == "first-pot"
    assert second_result["pot_id"] == "second-pot"
    assert second_result["arm"] == "qualification"
    assert engine_cache.engine_cache_counters(reset=True)["magemin"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
    }


def test_openimcc_isolated_cell_cache_hits_in_parent_without_worker_round_trip(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import os
    import sqlite3

    from simulator.battery.enums import Engine
    from simulator.diagnostic_helpers.binary_pot_battery import (
        Po2Request,
        _close_isolated_cell_workers,
        close_battery_engine_handle,
        equilibrate_cell,
        load_binary_pots,
    )
    from simulator.engine_pool import WarmEngineWorker
    from tests.battery.cache_test_support import open_available_engine

    cache_path = tmp_path / "openimcc-cell.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(cache_path))
    engine_cache.engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()
    _close_isolated_cell_workers()

    worker_calls: list[Any] = []
    parent_requests: list[Mapping[str, Any]] = []
    parent_pid = os.getpid()
    original_worker_call = WarmEngineWorker.call
    original_cached_call = engine_cache.cached_engine_call

    def counted_worker_call(self, request: Any, *, timeout_s: float) -> Any:
        if self.name == "openimcc battery cell":
            worker_calls.append(request)
        return original_worker_call(self, request, timeout_s=timeout_s)

    def tracked_cached_call(
        engine_id: str,
        request: Any,
        solve: Any,
        *,
        disclosure_runtime: Any | None = None,
    ) -> Any:
        if os.getpid() == parent_pid and engine_id == "openimcc":
            parent_requests.append(dict(request))
        return original_cached_call(
            engine_id,
            request,
            solve,
            disclosure_runtime=disclosure_runtime,
        )

    monkeypatch.setattr(WarmEngineWorker, "call", counted_worker_call)
    monkeypatch.setattr(engine_cache, "cached_engine_call", tracked_cached_call)

    handle = open_available_engine(
        Engine.OPENIMCC, nodeid="OpenIMCC isolated battery cache hit"
    )
    pots, _metadata = load_binary_pots()
    pot = next(pot for pot in pots if pot.pot_id == "mgo_sio2_40_60")

    def evaluate(*, simulate_crash: str | None = None):
        return equilibrate_cell(
            handle,
            pot,
            temperature_K=1700.0,
            po2=Po2Request(mode="engine_default", po2_bar=None),
            timeout_s=90.0,
            isolated=True,
            simulate_crash=simulate_crash,
        )

    def cache_bytes(cell: Any) -> bytes:
        return json.dumps(
            cell.to_engine_cache_value(),
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")

    try:
        cold = evaluate()
        assert cold.status == "ok"
        assert len(worker_calls) == 1

        engine_cache.engine_cache_counters(reset=True)
        warm_lru = evaluate()
        assert len(worker_calls) == 1
        assert len(parent_requests) == 2
        assert cache_bytes(warm_lru) == cache_bytes(cold)
        assert engine_cache.engine_cache_counters(reset=True)["openimcc"] == {
            "hits": 1,
            "misses": 0,
            "stores": 0,
            "store_error": 0,
            "read_error": 0,
            "conflicts": 0,
        }

        request_key = engine_cache.engine_cache_key(
            "openimcc",
            engine_cache.ENGINE_CHEMISTRY_VERSION["openimcc"],
            parent_requests[0],
        )
        memory_key = (str(cache_path), request_key, False)
        assert memory_key in engine_cache._MEMORY_CACHE
        with sqlite3.connect(cache_path) as connection:
            stored = connection.execute(
                "SELECT COUNT(*) FROM engine_outputs WHERE key=?", (request_key,)
            ).fetchone()[0]
            total_stored = connection.execute(
                "SELECT COUNT(*) FROM engine_outputs"
            ).fetchone()[0]
        assert stored == 1
        assert total_stored == 1

        engine_cache._MEMORY_CACHE.clear()
        engine_cache.engine_cache_counters(reset=True)
        warm_sqlite = evaluate()
        assert len(worker_calls) == 1
        assert len(parent_requests) == 3
        assert cache_bytes(warm_sqlite) == cache_bytes(cold)
        assert engine_cache.engine_cache_counters(reset=True)["openimcc"] == {
            "hits": 1,
            "misses": 0,
            "stores": 0,
            "store_error": 0,
            "read_error": 0,
            "conflicts": 0,
        }

        engine_cache.engine_cache_counters(reset=True)
        refusal = evaluate(simulate_crash="SIGABRT")
        assert refusal.status == "refusal"
        assert len(worker_calls) == 2
        assert len(parent_requests) == 4
        refusal_key = engine_cache.engine_cache_key(
            "openimcc",
            engine_cache.ENGINE_CHEMISTRY_VERSION["openimcc"],
            parent_requests[-1],
        )
        with sqlite3.connect(cache_path) as connection:
            refusal_stored = connection.execute(
                "SELECT COUNT(*) FROM engine_outputs WHERE key=?", (refusal_key,)
            ).fetchone()[0]
        assert refusal_stored == 0
        assert engine_cache.engine_cache_counters(reset=True)["openimcc"][
            "stores"
        ] == 0
    finally:
        close_battery_engine_handle(handle)
        _close_isolated_cell_workers()
