from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simulator import engine_cache
from simulator.engine_cache import drop_chemistry_version, engine_cache_key, load_cache_rows
from simulator.diagnostic_helpers.binary_pot_battery import EquilibrateCell, Po2Request
from simulator.melt_backend.base import (
    EquilibriumResult,
    equilibrium_cache_from_value,
    equilibrium_cache_value,
)
from simulator.engine_pool import EngineWorkerPool, WarmEngineWorker

pytestmark = pytest.mark.engine_cache_off


def _cached_engine_call():
    call = getattr(engine_cache, "cached_engine_call", None)
    assert callable(call), "engine_cache must expose cached_engine_call"
    return call


def _engine_cache_counters(*, reset: bool = False):
    counters = getattr(engine_cache, "engine_cache_counters", None)
    assert callable(counters), "engine_cache must expose process-local counters"
    return counters(reset=reset)


def _stored_hot(path: Path):
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT e.value, COALESCE(p.value, X'') FROM engine_outputs e "
            "LEFT JOIN provenance p ON p.id=e.provenance_id LIMIT 1"
        ).fetchone()
    assert row is not None
    return engine_cache._decode_hot(row[0], row[1])


def test_engine_cache_path_scope_overrides_without_mutating_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", "ambient-cache.sqlite")
    scoped_path = tmp_path / "scoped-cache.sqlite"

    with engine_cache._engine_cache_path_scope(scoped_path):
        assert engine_cache._configured_engine_cache_path() == str(scoped_path)
        assert engine_cache._engine_cache_path_scope_state() == (
            True,
            str(scoped_path),
        )
        assert os.environ["REGOLITH_ENGINE_CACHE"] == "ambient-cache.sqlite"

    assert engine_cache._configured_engine_cache_path() == "ambient-cache.sqlite"
    with engine_cache._engine_cache_path_scope(None):
        assert engine_cache._configured_engine_cache_path() is None
        assert os.environ["REGOLITH_ENGINE_CACHE"] == "ambient-cache.sqlite"


@pytest.mark.parametrize(
    "engine", tuple(engine_cache._ENGINE_CACHE_SETTINGS_PROJECTIONS)
)
def test_engine_cache_settings_rejects_each_missing_normalized_field(
    engine: str,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import (
        EngineCacheSettingsError,
        engine_cache_settings,
    )

    projection_fields = tuple(
        engine_cache._ENGINE_CACHE_SETTINGS_PROJECTIONS[engine]
    )
    with pytest.raises(EngineCacheSettingsError) as exc_info:
        engine_cache_settings(engine, {})

    assert exc_info.value.engine_name == engine
    assert exc_info.value.missing_fields == projection_fields
    assert "normalize_engine_settings(...)" in str(exc_info.value)

    normalized = normalize_engine_settings(engine, None)
    for field in projection_fields:
        incomplete = dict(normalized)
        del incomplete[field]
        with pytest.raises(EngineCacheSettingsError) as exc_info:
            engine_cache_settings(engine, incomplete)
        assert exc_info.value.engine_name == engine
        assert exc_info.value.missing_fields == (field,)


@pytest.mark.parametrize(
    "engine", tuple(engine_cache._ENGINE_CACHE_SETTINGS_PROJECTIONS)
)
def test_engine_cache_settings_ignores_unprojected_fields(engine: str) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )

    normalized = normalize_engine_settings(engine, None)
    with_extra = {**normalized, "unprojected_test_field": "ignored"}
    projection = engine_cache_settings(engine, normalized)

    assert engine_cache_settings(engine, with_extra) == projection
    assert engine_cache_key(
        engine,
        ENGINE_CHEMISTRY_VERSION[engine],
        {"engine_settings": engine_cache_settings(engine, with_extra)},
    ) == engine_cache_key(
        engine,
        ENGINE_CHEMISTRY_VERSION[engine],
        {"engine_settings": projection},
    )


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
        startup_timeout_s=30.0,
        call_timeout_s=30.0,
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


def test_internal_analytical_backend_hits_before_its_nested_engine_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
    )
    from simulator.melt_backend.base import _call_with_hard_timeout

    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    _engine_cache_counters(reset=True)

    handle = SimpleNamespace(
        name="internal-analytical", engine_settings={},
        backend=SimpleNamespace(_core=SimpleNamespace(_high_t_melt_activity="openimcc")),
    )

    def solve():
        nested = _call_with_hard_timeout(
            lambda: _cached_engine_call()(
                "openimcc", {"request": "nested-internal-analytical"},
                lambda: {"answer": 7},
            ),
            timeout_s=1.0,
        )
        return SimpleNamespace(status="ok", liquid_fraction=1.0, activity_coefficients={"FeO": nested["answer"]})

    for _ in range(2):
        assert _call_cached_backend_equilibrate(handle, {}, solve).activity_coefficients == {"FeO": 7}
    counts = _engine_cache_counters(reset=True)
    assert counts["internal-analytical"]["hits"] == 1
    assert counts["internal-analytical"]["stores"] == 1
    assert counts.get("openimcc", {}) == {}


def test_concurrent_processes_can_write_the_same_cache_key(
    tmp_path: Path,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    start = tmp_path / "start"
    ready_files = [tmp_path / f"ready-{index}" for index in range(4)]
    child_code = """
import sys, time
from pathlib import Path
from simulator.engine_cache import cached_engine_call
database, start, ready = map(Path, sys.argv[1:])
ready.touch()
deadline = time.monotonic() + 20
while not start.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('cache writer barrier timed out')
    time.sleep(.005)
def solve():
    time.sleep(.15)
    return {'answer': 42}
assert cached_engine_call('openimcc', {'request': 'same'}, solve) == {'answer': 42}
"""
    environment = dict(os.environ)
    environment["REGOLITH_ENGINE_CACHE"] = str(database)
    environment.pop("REGOLITH_ENGINE_CACHE_MODE", None)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    processes: list[subprocess.Popen[str]] = []
    try:
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(database),
                    str(start),
                    str(ready),
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for ready in ready_files
        ]
        deadline = time.monotonic() + 20
        while not all(ready.exists() for ready in ready_files):
            if time.monotonic() >= deadline:
                pytest.fail("cache writers did not reach the start barrier")
            time.sleep(0.01)
        start.touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


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
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        # Species rows live in this file. They name the stored HOT request.
        # A separate file would be written for a value that has no cold part.
        assert tables == [
            ("engine_outputs",),
            ("provenance",),
            ("box_sequences",),
            ("engine_output_species",),
        ]
        columns = connection.execute("PRAGMA table_info(engine_outputs)").fetchall()
        assert [column[1] for column in columns] == [
            "key", "value", "engine", "chem_version", "source_box", "row_seq", "provenance_id"
        ]
        schema = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='engine_outputs'"
        ).fetchone()[0]
        assert "WITHOUT ROWID" in schema.upper()


def test_engine_cache_writes_only_the_hot_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))

    assert _cached_engine_call()(
        "openimcc", {"call": "hot-only-storage"}, lambda: {"answer": 42}
    ) == {"answer": 42}

    assert database.is_file()
    assert not database.with_name(database.name + ".cold").exists()
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    assert tables == [
        ("engine_outputs",),
        ("provenance",),
        ("box_sequences",),
        ("engine_output_species",),
    ]


def test_schema_mismatch_refuses_without_deleting_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE engine_outputs "
            "(key BLOB PRIMARY KEY, value BLOB NOT NULL, unexpected TEXT NOT NULL) "
            "WITHOUT ROWID"
        )
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?, ?, ?)",
            (b"existing-key", b"existing-value", "keep-me"),
        )
    before = database.read_bytes()
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    _engine_cache_counters(reset=True)

    with caplog.at_level("WARNING", logger=engine_cache.__name__):
        result = _cached_engine_call()(
            "openimcc", {"call": "schema-mismatch"}, lambda: {"answer": 42}
        )

    assert result == {"answer": 42}
    counts = _engine_cache_counters()["openimcc"]
    assert counts["hits"] == 0
    assert counts["stores"] == 0
    assert counts["not_stored:schema_mismatch"] == 1
    notices = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("engine_cache_schema_mismatch")
    ]
    assert len(notices) == 1
    assert "where=engine_outputs" in notices[0]
    assert "unexpected" in notices[0]
    assert "scripts/engine_cache_sync.py migrate" in notices[0]
    assert database.read_bytes() == before
    with sqlite3.connect(database) as connection:
        columns = [
            row[1] for row in connection.execute("PRAGMA table_info(engine_outputs)")
        ]
        rows = connection.execute("SELECT key, value FROM engine_outputs").fetchall()
    assert columns == ["key", "value", "unexpected"]
    assert rows == [(b"existing-key", b"existing-value")]


def _assert_bad_file_solves_uncached(
    path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    *,
    where: str,
    expected: str,
    found: str,
) -> None:
    """A bad cache file is one notice, then uncached solves, and is not migrated."""

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    before = path.read_bytes()
    calls = {"solve": 0, "connect": 0}
    real_connect = engine_cache.sqlite3.connect

    def connect(*args, **kwargs):
        calls["connect"] += 1
        return real_connect(*args, **kwargs)

    def solve() -> dict[str, int]:
        calls["solve"] += 1
        return {"answer": 42}

    request = {"call": "bad-cache-file"}
    monkeypatch.setattr(engine_cache.sqlite3, "connect", connect)
    with caplog.at_level("WARNING", logger=engine_cache.__name__):
        first = engine_cache.cached_engine_call("openimcc", request, solve)
        second = engine_cache.cached_engine_call("openimcc", request, solve)
        warmed = engine_cache.engine_cache_counters()["openimcc"]
        notices = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("engine_cache_schema_mismatch")
        ]
        engine_cache.engine_cache_counters(reset=True)
        third = engine_cache.cached_engine_call("openimcc", request, solve)
        notices_after_reset = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("engine_cache_schema_mismatch")
        ]
    assert first == second == third == {"answer": 42}
    assert calls["solve"] == 3
    assert calls["connect"] == 1
    assert warmed["hits"] == 0
    assert warmed["stores"] == 0
    assert warmed["misses"] == 2
    assert warmed["not_stored:schema_mismatch"] == 2
    assert warmed.get("read_error", 0) == 0
    assert warmed.get("not_stored:readonly", 0) == 0
    counts = engine_cache.engine_cache_counters()["openimcc"]
    assert counts["hits"] == 0
    assert counts["stores"] == 0
    assert counts["misses"] == 1
    assert counts["not_stored:schema_mismatch"] == 1
    assert counts.get("read_error", 0) == 0
    assert counts.get("not_stored:readonly", 0) == 0
    assert notices == notices_after_reset
    assert len(notices) == 1
    identity = os.path.realpath(path)
    assert f"path={identity} " in notices[0]
    assert f"where={where} " in notices[0]
    assert f"expected={expected} " in notices[0]
    assert f"found={found} " in notices[0]
    assert notices[0].endswith(f"fix=scripts/engine_cache_sync.py migrate {identity}")
    assert path.read_bytes() == before


def test_schema3_file_solves_uncached_and_notifies_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tests.test_engine_cache_species import _schema_v3

    path = tmp_path / "schema3.sqlite"
    with _schema_v3(path):
        pass
    _assert_bad_file_solves_uncached(
        path,
        monkeypatch,
        caplog,
        where="user_version",
        expected="4",
        found="3",
    )
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "engine_output_species" not in names


def test_garbage_file_solves_uncached_and_notifies_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "garbage.sqlite"
    path.write_bytes(b"this is not sqlite\n")
    _assert_bad_file_solves_uncached(
        path,
        monkeypatch,
        caplog,
        where="file",
        expected="4",
        found="not-a-sqlite-database",
    )


def test_engine_cache_key_preserves_exact_float_values() -> None:
    assert engine_cache_key(
        "openimcc", 1, {"temperature_K": 1700.0}
    ) != engine_cache_key(
        "openimcc", 1, {"temperature_K": 1700.0000000000002}
    )


def test_hot_round_trip_preserves_float_hex_exactly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "hex.sqlite"))
    pins = (
        "0x0.0p+0", "-0x0.0p+0", "0x0.0000000000001p-1022",
        "0x1.0000000000001p+0", "0x1.fffffffffffffp+1023",
    )
    expected = {str(index): float.fromhex(pin) for index, pin in enumerate(pins)}
    request = {"call": "float-hex-pin"}
    cold = engine_cache.cached_engine_call("openimcc", request, lambda: expected)
    engine_cache._MEMORY_CACHE.clear()
    warm = engine_cache.cached_engine_call(
        "openimcc", request, lambda: pytest.fail("expected disk hit"),
    )
    for result in (cold, warm):
        assert tuple(result[str(index)].hex() for index in range(len(pins))) == pins


def test_engine_cache_binary_matches_shared_canonical_encoder() -> None:
    from simulator.engine_cache import pack

    values = (
        ["openimcc", 1, {"temperature_K": 1700.0000000000002}],
        {"nested": {"z": (1, 2.5), "a": "café"}, "flag": True},
    )
    for value in values:
        cache_bytes, provenance = engine_cache._encode_hot(value)
        assert cache_bytes == pack(value)
        assert not provenance
        assert engine_cache._decode_hot(cache_bytes) == value


def test_raw_key_hot_binary_and_exact_round_trip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    monkeypatch.setattr(engine_cache, "_RUNTIME_IDENTITIES", {})
    _engine_cache_counters(reset=True)
    hot = {"status": "ok", "activity": 0.12345678901234566}
    request = {"temperature_K": 1700.0}
    digest = engine_cache_key("openimcc", "1", request)

    assert isinstance(digest, bytes) and 4 <= len(digest) <= 128
    assert digest[:3] == bytes((1, 0, 1))
    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: hot
    ) == hot
    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: pytest.fail("expected hot hit")
    ) == hot

    assert not database.with_name(database.name + ".cold").exists()
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT key, value, engine, chem_version, source_box, row_seq "
            "FROM engine_outputs"
        ).fetchone()
    assert row[0] == digest
    assert _stored_hot(database) == {
        "activity": 0.12345678901234566,
        "status": "ok",
    }
    assert row[2:4] == ("openimcc", "1")


def test_sqlite_hit_populates_bounded_memory_cache_and_next_hit_skips_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()
    request = {"temperature_K": 1700.0, "call": "evaluate"}

    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: {"answer": 7}
    ) == {"answer": 7}
    engine_cache._MEMORY_CACHE.clear()
    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: pytest.fail("expected SQLite hit")
    ) == {"answer": 7}

    def sqlite_is_not_touched(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("memory-cache hit reached SQLite")

    monkeypatch.setattr(engine_cache, "_connect", sqlite_is_not_touched)
    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: pytest.fail("expected memory hit")
    ) == {"answer": 7}
    assert len(engine_cache._MEMORY_CACHE) == 1
    assert len(engine_cache._MEMORY_CACHE) <= engine_cache._MEMORY_CACHE_MAXSIZE


def test_memory_hit_does_not_compute_runtime_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache._MEMORY_CACHE.clear()
    request = {"call": "evaluate_redox", "pack": "default"}
    assert engine_cache.cached_engine_call("openimcc", request, lambda: {"ok": 1}) == {
        "ok": 1
    }
    monkeypatch.setattr(
        engine_cache,
        "runtime_identity",
        lambda _engine: pytest.fail("hit path computed runtime identity"),
    )
    assert engine_cache.cached_engine_call(
        "openimcc", request, lambda: pytest.fail("expected cached result")
    ) == {"ok": 1}


def test_recursive_simple_namespace_is_an_uncacheable_shape_without_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    expected = SimpleNamespace(
        vapor_pressures_Pa={"Na(g)": 0.0},
        diagnostics=SimpleNamespace(
            vapor_pressure_diagnostic={
                "below_reporting_threshold_omissions": {
                    "Na": {"computed_pressure_Pa": 0.25, "reporting_threshold_Pa": 1.0}
                }
            }
        ),
    )

    cold = engine_cache.cached_engine_call(
        "openimcc", {"call": "generic namespace"}, lambda: expected
    )
    warm = engine_cache.cached_engine_call(
        "openimcc",
        {"call": "generic namespace"},
        lambda: expected,
    )

    assert cold == expected
    assert warm == expected
    assert isinstance(warm, SimpleNamespace)
    assert isinstance(warm.diagnostics, SimpleNamespace)
    assert _engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 0,
        "misses": 2,
        "stores": 0,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
        "uncacheable:SimpleNamespace": 2,
    }


def test_stateless_internal_analytical_cache_call_hits(
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

    request = {"call": "stateless residue"}
    assert engine_cache.cached_engine_call("internal-analytical", request, solve) == {
        "answer": 42
    }
    assert engine_cache.cached_engine_call("internal-analytical", request, solve) == {
        "answer": 42
    }
    assert calls == 1
    counts = _engine_cache_counters(reset=True)["internal-analytical"]
    assert counts["hits"] == counts["misses"] == counts["stores"] == 1
    assert database.exists()


def test_openimcc_chemistry_version_change_with_same_pack_name_is_a_miss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    monkeypatch.setitem(engine_cache.ENGINE_CHEMISTRY_VERSION, "openimcc", 1)
    request = {
        "call": "evaluate_gas",
        "pack": "default",
    }
    calls = 0

    def solve() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"chemistry_version": str(engine_cache.ENGINE_CHEMISTRY_VERSION["openimcc"])}

    first = engine_cache.cached_engine_call("openimcc", request, solve)
    monkeypatch.setitem(engine_cache.ENGINE_CHEMISTRY_VERSION, "openimcc", 2)
    second = engine_cache.cached_engine_call("openimcc", request, solve)

    assert first == {"chemistry_version": "1"}
    assert second == {"chemistry_version": "2"}
    assert calls == 2


def test_alphamelts_parent_environment_does_not_change_adapter_request_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
        normalize_engine_settings,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    settings = normalize_engine_settings(
        "alphamelts",
        {
            "transport": "subprocess",
            "raw_model_name": "MELTSv1.0.2",
            "ambient_calc_mode": "MELTS",
        },
    )
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "MELTS")
    engine_cache.engine_cache_counters(reset=True)
    handle = SimpleNamespace(
        name="alphamelts", backend=SimpleNamespace(), engine_settings=settings
    )
    calls = 0

    def solve() -> EquilibriumResult:
        nonlocal calls
        calls += 1
        return EquilibriumResult(status="ok", liquid_fraction=0.5)

    first = _call_cached_backend_equilibrate(
        handle, {"temperature_C": 1000.0}, solve
    )
    monkeypatch.setenv("ALPHAMELTS_CALC_MODE", "pMELTS")
    second = _call_cached_backend_equilibrate(
        handle, {"temperature_C": 1000.0}, solve
    )

    assert first.liquid_fraction == second.liquid_fraction == 0.5
    assert calls == 1


def test_alphamelts_rejects_invalid_raw_python_model_before_cache_projection() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        normalize_engine_settings,
    )

    with pytest.raises(ValueError, match="not verified"):
        normalize_engine_settings(
            "alphamelts",
            {"transport": "python_api", "raw_model_name": " pMELTS "},
        )


def test_internal_analytical_runtime_identity_hashes_backend_source_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "engine_cache_identity_test_backend"
    source = tmp_path / "backend.py"
    bytecode = tmp_path / "backend.pyc"
    source.write_text("class Backend: pass\n", encoding="utf-8")
    bytecode.write_bytes(b"bytecode artifact")
    backend_type = type("Backend", (), {"__module__": module_name})
    module = SimpleNamespace(__file__=str(bytecode))
    monkeypatch.setitem(sys.modules, module_name, module)
    monkeypatch.setitem(
        engine_cache._RUNTIME_BACKENDS,
        "internal-analytical",
        SimpleNamespace(_core=backend_type()),
    )

    identity = engine_cache._internal_analytical_identity()

    expected = hashlib.sha256(source.read_bytes()).hexdigest()
    assert identity == f"source-sha256:{expected}"


def test_legacy_battery_payload_is_a_miss_after_layout_key_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
        normalize_engine_settings,
    )

    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    settings = normalize_engine_settings("thermoengine", None)
    handle = SimpleNamespace(
        name="thermoengine", backend=SimpleNamespace(), engine_settings=settings
    )
    kwargs = {"temperature_C": 1000.0}
    old_request = {
        "battery_cache_schema": 2,
        "kwargs": kwargs,
        "engine_settings": engine_cache.engine_cache_settings(
            "thermoengine", settings
        ),
    }
    engine_cache.cached_engine_call(
        "thermoengine", old_request, lambda: ["legacy cache layout"]
    )
    engine_cache._MEMORY_CACHE.clear()
    calls = 0

    def solve() -> EquilibriumResult:
        nonlocal calls
        calls += 1
        return EquilibriumResult(status="ok", liquid_fraction=0.375)

    result = _call_cached_backend_equilibrate(handle, kwargs, solve)

    assert calls == 1
    assert isinstance(result, EquilibriumResult)
    assert result.liquid_fraction == 0.375


def test_every_chemistry_version_has_changelog_and_runtime_identity() -> None:
    import re

    source = Path(engine_cache.__file__).read_text(encoding="utf-8")
    changelog = {
        (engine, int(version))
        for engine, version in re.findall(
            r"^\s*#\s*(\S+) v(\d+):", source, re.MULTILINE
        )
    }
    assert all(
        (engine, version) in changelog
        for engine, version in engine_cache.ENGINE_CHEMISTRY_VERSION.items()
    )
    assert set(engine_cache.ENGINE_CHEMISTRY_VERSION_IDENTITY_AT) == set(
        engine_cache.ENGINE_CHEMISTRY_VERSION
    )
    assert all(
        {"identity", "date"}
        <= set(engine_cache.ENGINE_CHEMISTRY_VERSION_IDENTITY_AT[engine])
        for engine in engine_cache.ENGINE_CHEMISTRY_VERSION
    )
    assert all(
        "stat" not in identity
        for identity in engine_cache.ENGINE_CHEMISTRY_VERSION_IDENTITY_AT.values()
    )


def test_stale_runtime_warns_once_without_changing_hits_or_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    engine_cache.runtime_identity.cache_clear()
    engine_cache.engine_cache_runtime_identities(reset=True)
    monkeypatch.setattr(engine_cache, "_compute_runtime_identity", lambda _engine: "old")
    monkeypatch.setitem(
        engine_cache.ENGINE_CHEMISTRY_VERSION_IDENTITY_AT,
        "openimcc",
        {"identity": "old", "date": "2026-11-08"},
    )
    calls = 0

    def solve() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"answer": 42}

    engine_cache.record_engine_open("openimcc")
    first = engine_cache.cached_engine_call(
        "openimcc", {"call": "evaluate", "pack": "default"}, solve
    )
    _engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.runtime_identity.cache_clear()
    engine_cache.engine_cache_runtime_identities(reset=True)
    monkeypatch.setattr(engine_cache, "_compute_runtime_identity", lambda _engine: "new")

    with caplog.at_level("WARNING", logger=engine_cache.__name__):
        engine_cache.record_engine_open("openimcc")
        second = engine_cache.cached_engine_call(
            "openimcc", {"call": "evaluate", "pack": "default"}, solve
        )

    warnings = [
        record.getMessage()
        for record in caplog.records
        if "engine cache:" in record.getMessage()
    ]
    assert first == second == {"answer": 42}
    assert calls == 1
    assert len(warnings) == 1
    assert _engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 1,
        "misses": 0,
        "stores": 0,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
    }


def test_runtime_identity_is_computed_once_per_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import Counter

    engine_cache.runtime_identity.cache_clear()
    calls: Counter[str] = Counter()

    def compute(engine_id: str) -> str:
        calls[engine_id] += 1
        return f"identity:{engine_id}"

    monkeypatch.setattr(engine_cache, "_compute_runtime_identity", compute)
    for engine_id in engine_cache.ENGINE_CHEMISTRY_VERSION:
        for _ in range(5):
            assert engine_cache.runtime_identity(engine_id) == f"identity:{engine_id}"
    assert calls == Counter(dict.fromkeys(engine_cache.ENGINE_CHEMISTRY_VERSION, 1))


@pytest.mark.parametrize(
    ("engine", "distribution_name", "metadata_name", "failure", "reason"),
    [
        (
            "openimcc",
            "openimcc",
            "direct_url.json",
            "decode",
            "distribution_metadata_unreadable",
        ),
        (
            "openimcc",
            "openimcc",
            "direct_url.json",
            "read",
            "distribution_metadata_unreadable",
        ),
        (
            "openimcc",
            "openimcc",
            "direct_url.json",
            "parse",
            "distribution_metadata_invalid",
        ),
        (
            "openimcc",
            "openimcc",
            "direct_url.json",
            "shape",
            "distribution_metadata_invalid",
        ),
        (
            "thermoengine",
            "thermoengine",
            "RECORD",
            "read",
            "distribution_metadata_unreadable",
        ),
        (
            "vaporock",
            "vaporock",
            "RECORD",
            "decode",
            "distribution_metadata_unreadable",
        ),
    ],
)
def test_bad_distribution_metadata_is_reported_without_aborting_engine_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    engine: str,
    distribution_name: str,
    metadata_name: str,
    failure: str,
    reason: str,
) -> None:
    dist_info = tmp_path / f"{distribution_name}-1.2.3.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {distribution_name}\nVersion: 1.2.3\n\n",
        encoding="utf-8",
    )
    metadata_path = dist_info / metadata_name
    if failure == "decode":
        metadata_path.write_bytes(bytes([0xFF]))
    elif failure == "read":
        metadata_path.write_text("metadata", encoding="utf-8")
        original_read_text = Path.read_text
        metadata_read_attempts = 0

        def fail_metadata_read(path: Path, *args, **kwargs) -> str:
            nonlocal metadata_read_attempts
            if path == metadata_path:
                metadata_read_attempts += 1
                raise OSError("injected metadata I/O failure")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", fail_metadata_read)
    elif failure == "parse":
        metadata_path.write_text("{", encoding="utf-8")
    else:
        metadata_path.write_text("[]", encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    engine_cache.runtime_identity.cache_clear()
    engine_cache.engine_cache_runtime_identities(reset=True)

    try:
        identity = engine_cache.runtime_identity(engine)
        if failure == "read":
            assert metadata_read_attempts == 1
        assert isinstance(identity, engine_cache.RuntimeIdentityUnknown)
        assert identity.engine_id == engine
        assert identity.reason == reason
        assert metadata_name in identity.detail

        with caplog.at_level("WARNING", logger=engine_cache.__name__):
            assert engine_cache.record_engine_open(engine) is None

        warnings = [
            record.getMessage()
            for record in caplog.records
            if "runtime identity unknown" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert reason in warnings[0]
        assert engine_cache.engine_cache_runtime_identities()[engine].startswith(
            f"runtime_identity_unknown:{reason}:"
        )
    finally:
        engine_cache.runtime_identity.cache_clear()
        engine_cache.engine_cache_runtime_identities(reset=True)


def test_native_identity_hashes_once_per_process_without_a_stat_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    binary = tmp_path / "engine"
    binary.write_bytes(b"initial binary")
    monkeypatch.setitem(
        engine_cache.ENGINE_CHEMISTRY_VERSION_IDENTITY_AT,
        "alphamelts",
        {"identity": "sha256:recorded", "date": "2026-11-08"},
    )
    monkeypatch.setitem(
        engine_cache._RUNTIME_BACKENDS,
        "alphamelts",
        SimpleNamespace(_binary_path=binary),
    )
    hashes: list[Path] = []

    def digest(path: Path) -> str:
        hashes.append(path)
        return "changed"

    monkeypatch.setattr(engine_cache, "_file_sha256", digest)

    engine_cache.runtime_identity.cache_clear()
    assert engine_cache.runtime_identity("alphamelts") == "sha256:changed"
    assert hashes == [binary]

    binary.write_bytes(b"changed binary")
    assert engine_cache.runtime_identity("alphamelts") == "sha256:changed"
    assert hashes == [binary]


def test_local_cache_rows_require_an_explicit_configured_box(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_BOX", raising=False)
    engine_cache._MEMORY_CACHE.clear()

    with pytest.raises(ValueError, match="REGOLITH_ENGINE_CACHE_BOX"):
        engine_cache.cached_engine_call(
            "openimcc", {"call": "unrecorded-box"}, lambda: {"answer": 42}
        )
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.cached_engine_call(
        "openimcc", {"call": "recorded-box"}, lambda: {"answer": 43}
    )

    with sqlite3.connect(database) as connection:
        boxes = connection.execute(
            "SELECT source_box FROM engine_outputs ORDER BY key"
        ).fetchall()
    assert {row[0] for row in boxes} == {"laptop"}


def test_equilibrium_result_is_the_same_type_on_cold_and_warm_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
        normalize_engine_settings,
    )

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "engine-cache.sqlite"))
    _engine_cache_counters(reset=True)
    expected = EquilibriumResult(
        liquid_fraction=0.25,
        phase_compositions={"melt": {"FeO": 0.5}},
        activity_coefficients={"FeO": 0.75},
    )
    expected.diagnostics = {"backend_status_reason": "fixture-code"}
    handle = SimpleNamespace(
        name="thermoengine",
        engine_settings=normalize_engine_settings("thermoengine", None),
        backend=SimpleNamespace(),
    )
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
    assert cold.diagnostics == {"backend_status_reason": "fixture-code"}
    assert warm.diagnostics == {"backend_status_reason": "fixture-code"}
    assert calls == 1
    assert _engine_cache_counters(reset=True)["thermoengine"] == {
        "hits": 1,
        "misses": 1,
        "stores": 1,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
    }


def test_mapping_equilibrium_result_uses_compact_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _call_cached_backend_equilibrate,
        normalize_engine_settings,
    )

    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    handle = SimpleNamespace(
        name="thermoengine",
        engine_settings=normalize_engine_settings("thermoengine", None),
        backend=SimpleNamespace(),
    )
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
    assert isinstance(_stored_hot(database), list)
    assert isinstance(stored, bytes)
    assert cold.activity_coefficients == warm.activity_coefficients == {"FeO": 0.75}
    assert cold.diagnostics == warm.diagnostics == {
        "backend_status_reason": "unused diagnostic prose"
    }
    assert cold.phase_compositions == warm.phase_compositions == {}
    assert calls == 1


def test_isolated_cell_cache_stores_only_compact_hot_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        _IsolatedCellWorker,
        normalize_engine_settings,
    )

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
    isolated.engine_settings = normalize_engine_settings("thermoengine", None)
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
    stored = _stored_hot(database)
    assert isinstance(stored["cell"], list)
    assert stored["activities_standard_state"] == result["activities_standard_state"]
    assert b"verbose engine diagnostic" not in hot_value
    assert cold["pot_id"] == warm["pot_id"] == "caller-pot"
    assert cold["melt_activities"] == warm["melt_activities"] == {"SiO2": 0.5}
    assert cold["gas_partial_pressures_Pa"] == warm["gas_partial_pressures_Pa"] == {
        "SiO": 1.25
    }
    assert cold["engine_reason"] == warm["engine_reason"] == result["engine_reason"]
    assert cold["activities_standard_state"] == warm["activities_standard_state"] == result["activities_standard_state"]
    assert isolated._worker.calls == 1


def test_equilibrium_cache_value_contains_only_scoring_inputs() -> None:
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
    result.imcc_notices = [{"kind": "openimcc_flag", "reason": "fixture"}]
    result.imcc_model_id = "fixture-model"
    result.liquid_composition_wt_pct = {"FeO": 10.0}
    result.fe_redox_split = {"FeO": 0.2}

    value = result.to_engine_cache_value(engine_id="openimcc")

    assert isinstance(value, list)
    restored = EquilibriumResult.from_engine_cache_value(
        value, engine_id="openimcc"
    )
    assert restored.activity_coefficients == result.activity_coefficients
    assert restored.vapor_pressures_Pa == result.vapor_pressures_Pa
    assert restored.vapor_pressures_source == result.vapor_pressures_source
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
    assert restored.diagnostics == {"backend_status_reason": "fixture-code"}
    assert restored.warnings == ["AlphaMELTS model notice"]
    assert restored.imcc_notices == [
        {"kind": "openimcc_flag", "reason": "fixture"}
    ]
    assert restored.phase_masses_kg == {}


def test_openimcc_hot_binary_preserves_all_pressure_values() -> None:
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

    hot = result.to_engine_cache_value()
    raw, provenance = engine_cache._encode_hot(hot)
    restored = EquilibriumResult.from_hot(engine_cache._decode_hot(raw, provenance))
    assert {name: value.hex() for name, value in restored.vapor_pressures_Pa.items()} == {
        name: value.hex() for name, value in result.vapor_pressures_Pa.items()
    }


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


def test_bulk_loader_counts_conflicts_without_replacing_existing_bytes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    digest = engine_cache_key("magemin", 2, {"call": "duplicate"})
    _engine_cache_counters(reset=True)

    assert load_cache_rows(
        database, [(digest, {"answer": "first"}, "magemin", 2, "box-a", 1)]
    ) == 1
    assert load_cache_rows(
        database, [(digest, {"answer": "first"}, "magemin", 2, "box-b", 2)]
    ) == 0
    assert load_cache_rows(
        database, [(digest, {"answer": "different"}, "magemin", 2, "box-b", 2)]
    ) == 0
    assert _engine_cache_counters(reset=True)["magemin"]["conflicts"] == 1
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT value FROM engine_outputs WHERE key=?", (digest,)
        ).fetchone()[0]
    assert engine_cache._decode_hot(stored) == {"answer": "first"}


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
    counts = _engine_cache_counters(reset=True)["vaporock"]
    assert counts == {
        "hits": 1, "misses": 1, "stores": 0,
        "store_error": 0, "read_error": 0, "conflicts": 0,
        "not_stored:readonly": 1,
    }
    assert counts["misses"] == counts["stores"] + sum(
        value for key, value in counts.items() if key.startswith("not_stored:")
    )
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
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
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
            "store_error": 0,
            "read_error": 0,
            "conflicts": 0,
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
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
        "not_stored:refusal:untyped": 2,
        "not_stored:exception:ValueError": 1,
    }


def test_corrupt_hot_json_is_a_counted_conflict_and_is_not_replaced(
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
        connection.execute("UPDATE engine_outputs SET value = ?", (b"not compressed JSON",))
    engine_cache._MEMORY_CACHE.clear()
    assert call("alphamelts", request, solve) == "result"
    assert call("alphamelts", request, solve) == "result"
    assert calls == 2
    assert _engine_cache_counters(reset=True)["alphamelts"] == {
        "hits": 1,
        "misses": 2,
        "stores": 1,
        "store_error": 0,
        "read_error": 1,
        "conflicts": 1,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM engine_outputs").fetchone()[0] == b"not compressed JSON"


def test_concurrent_cache_writer_counts_different_bytes_without_replacing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    engine_cache.engine_cache_counters(reset=True)
    request = {"call": "conflicting-writer"}
    digest = engine_cache.engine_cache_key("openimcc", 1, request)

    def solve() -> dict[str, str]:
        connection = engine_cache._CONNECTIONS[(str(database), False)]
        existing_value, _provenance = engine_cache._encode_hot({"answer": "existing"})
        connection.execute(
            "INSERT INTO engine_outputs "
            "(key, value, engine, chem_version, source_box, row_seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                digest,
                sqlite3.Binary(existing_value),
                "openimcc",
                "1",
                "existing-box",
                1,
            ),
        )
        connection.commit()
        return {"answer": "live"}

    result = engine_cache.cached_engine_call("openimcc", request, solve)

    assert result == {"answer": "live"}
    assert _engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 0,
        "misses": 1,
        "stores": 0,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 1,
    }
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT value FROM engine_outputs WHERE key=?", (digest,)
        ).fetchone()[0]
    assert engine_cache._decode_hot(stored) == {"answer": "existing"}


def test_store_encoding_failure_returns_live_value_and_counts_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    hot = object()

    result = _cached_engine_call()(
        "openimcc",
        {"temperature_K": 1700.0},
        lambda: hot,
    )

    assert result is hot
    assert _engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 0,
        "misses": 1,
        "stores": 0,
        "store_error": 0,
        "read_error": 0,
        "conflicts": 0,
        "uncacheable:object": 1,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0


def test_malformed_hot_value_is_a_counted_conflict_and_not_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "engine-cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(database))
    _engine_cache_counters(reset=True)
    request = {"temperature_K": 1700.0}
    call = _cached_engine_call()
    assert call("openimcc", request, lambda: {"answer": 1}) == {"answer": 1}

    connection = engine_cache._connect(database, readonly=False)
    assert connection is not None
    digest = engine_cache_key("openimcc", 1, request)
    connection.execute(
        "UPDATE engine_outputs SET value=? WHERE key=?",
        (sqlite3.Binary(b"not-zlib"), digest),
    )
    connection.commit()
    _engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    assert call("openimcc", request, lambda: {"answer": 2}) == {"answer": 2}
    assert _engine_cache_counters(reset=True)["openimcc"] == {
        "hits": 0,
        "misses": 1,
        "stores": 0,
        "store_error": 0,
        "read_error": 1,
        "conflicts": 1,
    }
    assert connection.execute(
        "SELECT value FROM engine_outputs WHERE key=?", (digest,)
    ).fetchone()[0] == b"not-zlib"


def test_equilibrium_cache_maps_use_compact_canonical_json_objects() -> None:
    result = equilibrium_cache_value(
        {
            "status": "ok",
            "activity_coefficients": {"FeO": 0.91},
            "reported_activity_coefficients": {"FeO": 0.89},
            "vapor_pressures_Pa": {"SiO": 12.5},
            "vapor_pressures_source": {"SiO": "vaporock"},
            "liquid_composition_wt_pct": {"FeO": 25.0},
            "fe_redox_split": {"Fe2O3_wt_pct": 3.0},
        }
    )

    assert isinstance(result[3], dict)
    assert isinstance(result[4], dict)
    assert isinstance(result[6], dict)
    assert isinstance(result[7], dict)
    assert isinstance(result[9], dict)
    assert isinstance(result[10], dict)
    restored = equilibrium_cache_from_value(result)
    assert restored["activity_coefficients"] == {"FeO": 0.91}
    assert restored["reported_activity_coefficients"] == {"FeO": 0.89}
    assert restored["vapor_pressures_Pa"] == {"SiO": 12.5}
    assert restored["vapor_pressures_source"] == {"SiO": "vaporock"}
    assert restored["liquid_composition_wt_pct"] == {"FeO": 25.0}
    assert restored["fe_redox_split"] == {"Fe2O3_wt_pct": 3.0}


def test_engine_notice_codes_round_trip_with_typed_values() -> None:
    from simulator.melt_backend.base import (
        ENGINE_CACHE_NOTICE_CODE_TABLE,
        decode_engine_notice_codes,
        encode_engine_notice_codes,
    )

    notices = [
        {
            "kind": "openimcc_temperature_extrapolated",
            "authority": "extrapolated",
            "certified_band": {"temperature_K": [1400.0, 2200.0]},
        },
        {
            "kind": "openimcc_gas_flag",
            "authority": "extrapolated",
            "species": "CO(g)",
            "reason": "outside-domain",
        },
    ]
    codes, values = encode_engine_notice_codes("openimcc", notices)

    assert codes == tuple(sorted(codes))
    assert all(type(code) is int and 0 < code < 256 for code in codes)
    assert decode_engine_notice_codes("openimcc", codes, values) == notices
    commissioning = {
        "kind": "out_of_certified_band",
        "authority": "extrapolated",
        "certified_band": {"temperature_K": [1400.0, 1950.0]},
    }
    commissioning_codes, commissioning_values = encode_engine_notice_codes(
        "thermoengine", [commissioning]
    )
    assert decode_engine_notice_codes(
        "thermoengine", commissioning_codes, commissioning_values
    ) == [commissioning]
    assert len({(engine, number) for engine, number, _code, _description in ENGINE_CACHE_NOTICE_CODE_TABLE}) == len(
        ENGINE_CACHE_NOTICE_CODE_TABLE
    )
    assert len({(engine, code) for engine, _number, code, _description in ENGINE_CACHE_NOTICE_CODE_TABLE}) == len(
        ENGINE_CACHE_NOTICE_CODE_TABLE
    )


def test_engine_notice_emitters_use_registered_codes() -> None:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        engine_flags_from_result,
    )
    from simulator.melt_backend.base import (
        decode_engine_notice_codes,
        decode_engine_warning_codes,
        encode_engine_notice_codes,
        encode_engine_warning_codes,
    )

    openimcc_notices = [
        {"kind": "openimcc_flag"},
        {"kind": "imcc_complex_saturation"},
        {"kind": "openimcc_temperature_extrapolated"},
        {"kind": "openimcc_composition_outside_validated_envelope"},
        {"kind": "openimcc_notice"},
        {"kind": "openimcc_notice", "code": "openimcc_fe2o3_fold"},
        {
            "kind": "openimcc_notice",
            "code": "openimcc_ferric_component_collapsed",
        },
        {
            "kind": "openimcc_notice",
            "code": "openimcc_projection_crmn_relaxed",
        },
        {"kind": "openimcc_notice", "code": "openimcc_projected_bulk"},
        {"kind": "openimcc_gas_flag"},
        {"kind": "openimcc_gas_unavailable"},
        {"kind": "fo2_oxygen_balance_effusion_solved"},
        {"kind": "input_omitted"},
    ]
    emitted = {
        "openimcc": [
            SimpleNamespace(
                diagnostics={
                    "imcc_notices": openimcc_notices
                },
                warnings=(),
            )
        ],
        "alphamelts": [
            SimpleNamespace(
                diagnostics={
                    "commissioning_notice": {
                        "kind": "out_of_certified_band",
                        "authority": "extrapolated",
                    },
                    "fo2_gas_only": {"notice": "external_fO2_gas_only"},
                },
                warnings=(
                    "AlphaMELTS provider omitted diagnostic_oxide_activities field FeO",
                ),
            )
        ],
        "thermoengine": [
            SimpleNamespace(
                diagnostics={
                    "commissioning_notice": {
                        "kind": "engine_model_caveat",
                        "authority": "extrapolated",
                    },
                    "fo2_gas_only": {"notice": "external_fO2_gas_only"},
                    "imcc_notices": [{"kind": "fallback"}],
                },
                warnings=(),
            )
        ],
        "vaporock": [
            SimpleNamespace(
                diagnostics={
                    "commissioning_notice": {
                        "kind": "out_of_certified_band",
                        "authority": "extrapolated",
                    }
                },
                warnings=(),
            )
        ],
        "magemin": [
            SimpleNamespace(
                diagnostics={
                    "commissioning_notice": {
                        "kind": "input_omitted",
                        "authority": "extrapolated",
                    }
                },
                warnings=(),
            )
        ],
    }

    for engine_id, results in emitted.items():
        warning_codes, warning_values = encode_engine_warning_codes(
            engine_id, [f"{engine_id} warning"]
        )
        assert decode_engine_warning_codes(
            engine_id, warning_codes, warning_values
        ) == [f"{engine_id} warning"]
        for result in results:
            notices, _authority, _band = engine_flags_from_result(result)
            codes, values = encode_engine_notice_codes(engine_id, notices)
            assert decode_engine_notice_codes(engine_id, codes, values) == notices


def test_unknown_engine_notice_code_requires_table_entry() -> None:
    from simulator.melt_backend.base import encode_engine_notice_codes

    with pytest.raises(ValueError, match="unregistered openimcc engine notice code"):
        encode_engine_notice_codes("openimcc", [{"kind": "future_notice"}])


def test_real_commissioning_notices_round_trip_for_every_emitting_engine() -> None:
    from engines.engine_commissioning import (
        assess_engine_commissioning, load_engine_commissioning,
    )
    from simulator.melt_backend.base import decode_engine_notice_codes, encode_engine_notice_codes

    for engine, specification in load_engine_commissioning().engines.items():
        notice = assess_engine_commissioning(
            engine,
            sio2_wt_pct=specification.sio2_wt_pct.certified.minimum,
            temperature_K=specification.temperature_K.certified.maximum + 1.0,
        ).notice
        assert notice is not None
        codes, values = encode_engine_notice_codes(engine, [notice])
        assert decode_engine_notice_codes(engine, codes, values) == json.loads(json.dumps([notice]))


def test_equilibrium_hot_uses_notice_and_warning_codes_with_parameters() -> None:
    from simulator.melt_backend.base import (
        equilibrium_cache_from_value,
        equilibrium_cache_value,
    )

    notice = {
        "kind": "openimcc_gas_flag",
        "authority": "extrapolated",
        "species": "CO(g)",
        "reason": "outside-domain",
    }
    value = equilibrium_cache_value(
        {
            "status": "ok",
            "warnings": ["OpenIMCC emitted a diagnostic warning"],
            "diagnostics": {"imcc_notices": [notice]},
        },
        engine_id="openimcc",
    )

    metadata = value[11]
    assert metadata["notice_codes"] == tuple(sorted(metadata["notice_codes"]))
    assert metadata["warning_codes"] == tuple(sorted(metadata["warning_codes"]))
    assert all(type(code) is int for code in metadata["notice_codes"])
    restored = equilibrium_cache_from_value(value, engine_id="openimcc")
    assert restored["diagnostics"]["imcc_notices"] == [notice]
    assert restored["warnings"] == ["OpenIMCC emitted a diagnostic warning"]


def test_equilibrate_cell_hot_reuses_cell_certified_band_for_notices() -> None:
    band = {"temperature_K": [1400.0, 2200.0]}
    cell = EquilibrateCell(
        pot_id="compact-notice",
        engine="openimcc",
        temperature_K=1800.0,
        po2=Po2Request(mode="engine_default", po2_bar=None),
        status="ok",
        refusal_reason=None,
        engine_status="ok",
        engine_reason=None,
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        wall_s=0.0,
        cpu_s=0.0,
        hostname="test",
        authority="extrapolated",
        certified_band=band,
        notices=[
            {
                "kind": "openimcc_temperature_extrapolated",
                "authority": "extrapolated",
                "certified_band": band,
            }
        ],
    )

    hot = cell.to_engine_cache_value()

    assert hot[11][6] == band
    assert hot[11][7]["codes"] == (3,)
    assert hot[11][7]["values"] == (
        {
            "position": 0,
            "authority": "extrapolated",
            "certified_band": band,
        },
    )
    restored = EquilibrateCell.from_engine_cache_value(hot)
    assert restored.notices[0]["certified_band"] == band
