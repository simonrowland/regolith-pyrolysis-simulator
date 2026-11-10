from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping

from simulator import engine_cache
from simulator.battery.score import is_store_stamp_payload
from simulator.diagnostic_helpers.binary_pot_battery import _IsolatedCellWorker
from scripts.battery_cache_equivalence import _without_cache_counters


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


def test_battery_engine_worker_request_is_cached_at_the_adapter_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.engine_cache_counters(reset=True)
    worker = object.__new__(_IsolatedCellWorker)
    worker.engine = "magemin"
    worker.engine_settings = {}
    worker._lock = threading.Lock()
    worker._worker = _FakeWorker()
    first = {
        "engine": "magemin",
        "pot_id": "first-pot",
        "composition_wt_pct": {"SiO2": 50.0, "FeO": 50.0},
        "temperature_K": 1700.0,
        "physical_pressure_bar": 1.0,
        "po2": {"mode": "engine_default", "po2_bar": None},
        "engine_settings": {},
        "arm": "headline",
    }
    second = {**first, "pot_id": "second-pot", "arm": "qualification"}

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
    }
