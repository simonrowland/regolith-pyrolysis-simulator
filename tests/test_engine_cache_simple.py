from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from simulator import engine_cache


def _cached_engine_call():
    call = getattr(engine_cache, "cached_engine_call", None)
    assert callable(call), "engine_cache must expose cached_engine_call"
    return call


def _engine_cache_counters(*, reset: bool = False):
    counters = getattr(engine_cache, "engine_cache_counters", None)
    assert callable(counters), "engine_cache must expose process-local counters"
    return counters(reset=reset)


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
        assert [column[1] for column in columns] == ["key", "value"]


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


def test_corrupt_pickle_is_a_miss_and_is_replaced(
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
