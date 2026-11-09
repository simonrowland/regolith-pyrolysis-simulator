from __future__ import annotations

import inspect
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from simulator import engine_cache
from simulator.engine_cache import (
    ENGINE_CHEMISTRY_VERSION,
    EngineCache,
    TransientEngineFailure,
    cached_solve,
    key,
)


class _Outcome:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value
        self.status = str(value.get("status", "ok"))
        self.refusal_reason = (
            None
            if value.get("refusal_reason") is None
            else str(value["refusal_reason"])
        )
        self.engine_status = (
            None
            if value.get("engine_status") is None
            else str(value["engine_status"])
        )
        self.engine_reason = (
            None
            if value.get("engine_reason") is None
            else str(value["engine_reason"])
        )

    def to_cache_payload(self) -> dict[str, object]:
        return dict(self.value)

    @classmethod
    def from_cache_payload(cls, payload: dict[str, object]) -> "_Outcome":
        return cls(dict(payload))


def test_key_is_stable_across_mapping_order_and_process_restart() -> None:
    request = {
        "composition": {"FeO": 12.0, "SiO2": 48.0},
        "temperature_K": 1600.0,
        "oxygen": {"mode": "pO2", "value": 1.0e-8},
    }
    reordered = {
        "oxygen": {"value": 1.0e-8, "mode": "pO2"},
        "temperature_K": 1600.0,
        "composition": {"SiO2": 48.0, "FeO": 12.0},
    }

    expected = key("openimcc", request)
    assert key("openimcc", reordered) == expected

    code = (
        "import json, sys; "
        "from simulator.engine_cache import key; "
        "print(key('openimcc', json.loads(sys.argv[1])))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code, json.dumps(request)],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert completed.stdout.strip() == expected


def test_key_preserves_exact_float_values() -> None:
    left = key("internal-analytical", {"temperature_K": 1.0})
    right = key("internal-analytical", {"temperature_K": 1.0000000000000002})

    assert left != right


def test_key_uses_chemistry_version_and_only_accepts_request_vector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = {"temperature_K": 1700.0}
    before = key("vaporock", request)
    monkeypatch.setitem(
        ENGINE_CHEMISTRY_VERSION,
        "vaporock",
        ENGINE_CHEMISTRY_VERSION["vaporock"] + 1,
    )
    after = key("vaporock", request)

    assert before != after
    assert list(inspect.signature(key).parameters) == ["engine_id", "request_vector"]
    with pytest.raises(TypeError):
        key("vaporock", request, observation_id="not-an-engine-input")  # type: ignore[call-arg]


def test_concurrent_process_writes_preserve_same_and_different_keys(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "engine-cache.sqlite3"
    writes = [
        ("same", {"value": 1}),
        ("same", {"value": 1}),
        ("left", {"value": 2}),
        ("right", {"value": 3}),
    ]
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import json, sys; "
                "from simulator.engine_cache import EngineCache; "
                "EngineCache(sys.argv[1]).put(sys.argv[2], json.loads(sys.argv[3]), {'runtime': 'test'})",
                str(db_path),
                key("magemin", {"request": name}),
                json.dumps(payload),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=os.environ.copy(),
        )
        for name, payload in writes
    ]

    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"

    connection = sqlite3.connect(db_path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"
    finally:
        connection.close()

    cache = EngineCache(db_path)
    assert cache.get(key("magemin", {"request": "same"})) == {"value": 1}
    assert cache.get(key("magemin", {"request": "left"})) == {"value": 2}
    assert cache.get(key("magemin", {"request": "right"})) == {"value": 3}


def test_cached_solve_skips_transient_failure_and_caches_typed_refusal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = EngineCache(tmp_path / "engine-cache.sqlite3")
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    transient_request = {"temperature_K": 1500.0}

    def fail_transiently() -> _Outcome:
        raise TransientEngineFailure("worker startup failed")

    with pytest.raises(TransientEngineFailure):
        cached_solve(cache, "alphamelts", transient_request, fail_transiently)
    assert cache.get(key("alphamelts", transient_request)) is None

    worker_request = {"temperature_K": 1525.0}
    worker_failure = _Outcome(
        {
            "status": "refusal",
            "refusal_reason": "engine_crash",
            "engine_status": "EngineWorkerDied",
        }
    )
    assert (
        cached_solve(cache, "alphamelts", worker_request, lambda: worker_failure)
        is worker_failure
    )
    assert cache.get(key("alphamelts", worker_request)) is None

    refusal_request = {"temperature_K": 1550.0}
    refusal = _Outcome(
        {"status": "refusal", "refusal_reason": "unsupported_composition"}
    )
    assert cached_solve(cache, "alphamelts", refusal_request, lambda: refusal) is refusal
    hit = cached_solve(
        cache,
        "alphamelts",
        refusal_request,
        lambda: pytest.fail("cached refusal unexpectedly called solve_fn"),
    )

    assert isinstance(hit, _Outcome)
    assert hit.value == refusal.value


def test_runtime_identity_tripwire_logs_once_and_keeps_the_hit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cache = EngineCache(tmp_path / "engine-cache.sqlite3")
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    runtime = {"version": "old"}
    monkeypatch.setattr(engine_cache, "runtime_identity", lambda _engine: dict(runtime))
    request = {"pressure_bar": 1.0}
    original = _Outcome({"status": "ok", "value": 7})
    assert cached_solve(cache, "thermoengine", request, lambda: original) is original

    runtime["version"] = "new"
    solve_calls = 0

    def must_not_solve() -> _Outcome:
        nonlocal solve_calls
        solve_calls += 1
        return _Outcome({"status": "wrong"})

    with caplog.at_level("INFO", logger=engine_cache.__name__):
        first_hit = cached_solve(cache, "thermoengine", request, must_not_solve)
        second_hit = cached_solve(cache, "thermoengine", request, must_not_solve)

    notices = [
        record
        for record in caplog.records
        if getattr(record, "notice", {}).get("kind")
        == "engine_cache_runtime_changed"
    ]
    assert first_hit.value == original.value
    assert second_hit.value == original.value
    assert solve_calls == 0
    assert len(notices) == 1
    assert "old" in notices[0].getMessage()
    assert "new" in notices[0].getMessage()
    assert (
        "thermoengine runtime changed (stored {\"version\":\"old\"}, now "
        "{\"version\":\"new\"}). Cached outputs are still used. Bump "
        "ENGINE_CHEMISTRY_VERSION['thermoengine'] ONLY if this change affects "
        "chemistry; leave it for non-chemistry patches."
    ) == notices[0].getMessage()


def test_cached_solve_tracks_per_engine_work_unit_counters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = EngineCache(tmp_path / "engine-cache.sqlite3")
    monkeypatch.setattr(
        engine_cache,
        "_from_cache_payload",
        _Outcome.from_cache_payload,
    )
    request = {"oxygen": {"mode": "intrinsic"}}

    cached_solve(cache, "internal-analytical", request, lambda: _Outcome({"n": 1}))
    cached_solve(cache, "internal-analytical", request, lambda: pytest.fail("expected hit"))

    with pytest.raises(TransientEngineFailure):
        cached_solve(
            cache,
            "internal-analytical",
            {"oxygen": {"mode": "fO2"}},
            lambda: (_ for _ in ()).throw(TransientEngineFailure("resource error")),
        )
    refusal = _Outcome({"status": "refusal", "refusal_reason": "no_liquid"})
    cached_solve(
        cache,
        "internal-analytical",
        {"oxygen": {"mode": "pO2"}},
        lambda: refusal,
    )

    assert cache.counters["internal-analytical"] == {
        "hits": 1,
        "misses": 3,
        "stores": 2,
        "transient_skips": 1,
        "calls_by_kind": {
            "equilibrate_cell": 3,
            "evaluate": 0,
            "evaluate_redox": 0,
        },
    }
    assert cache.counters["openimcc"] == {
        "hits": 0,
        "misses": 0,
        "stores": 0,
        "transient_skips": 0,
        "calls_by_kind": {
            "equilibrate_cell": 0,
            "evaluate": 0,
            "evaluate_redox": 0,
        },
    }


def test_runtime_identity_is_memoized_per_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_cache.runtime_identity.cache_clear()
    original_find_spec = engine_cache.importlib.util.find_spec
    calls = 0

    def counted_find_spec(module_name: str):
        nonlocal calls
        calls += 1
        return original_find_spec(module_name)

    monkeypatch.setattr(engine_cache.importlib.util, "find_spec", counted_find_spec)
    try:
        first = engine_cache.runtime_identity("internal-analytical")
        second = engine_cache.runtime_identity("internal-analytical")
    finally:
        engine_cache.runtime_identity.cache_clear()

    assert first == second
    assert calls == 1


def test_every_chemistry_version_has_a_changelog_entry() -> None:
    source = Path(engine_cache.__file__).read_text(encoding="utf-8")
    entries = {
        (engine, int(version))
        for engine, version in re.findall(
            r"^\s*#\s*(\S+) v(\d+):", source, re.MULTILINE
        )
    }
    assert all(
        (engine, version) in entries
        for engine, version in ENGINE_CHEMISTRY_VERSION.items()
    )
