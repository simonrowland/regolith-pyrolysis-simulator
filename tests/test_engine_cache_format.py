"""Storage contracts independent of the engine implementations."""

import os
import sqlite3
from pathlib import Path

import pytest

from simulator import engine_cache

pytestmark = pytest.mark.engine_cache_off


def test_binary_scalar_wire_pin_before_leaf_consolidation() -> None:
    from simulator.engine_cache import pack, unpack

    values = (None, False, True, -1, 1.25, -0.0, 0.0)
    expected = bytes.fromhex("07070001020301043ff40000000000000c0b")
    assert pack(values) == expected
    restored = unpack(expected)
    assert restored[:5] == values[:5]
    assert tuple(value.hex() for value in restored[4:]) == (
        "0x1.4000000000000p+0", "-0x0.0p+0", "0x0.0p+0",
    )


def test_natural_key_is_exact_canonical_and_local() -> None:
    request = {
        "composition_wt_pct": {"SiO2": 60.0, "MgO": 40.0},
        "temperature_K": 1700.0,
        "pressure_bar": 1.0,
        "fO2_log": -9.0,
    }
    key = engine_cache.engine_cache_key("openimcc", 1, request)
    assert key[:3] == bytes((1, 0, 1))
    assert key == engine_cache.engine_cache_key("openimcc", "1", dict(reversed(tuple(request.items()))))
    assert key != engine_cache.engine_cache_key("openimcc", 1, {**request, "temperature_K": float.fromhex("0x1.a900000000001p+10")})
    assert key != engine_cache.engine_cache_key("openimcc", 2, request)
    assert key < engine_cache.engine_cache_key("openimcc", 1, {**request, "temperature_K": 1701.0})
    assert len(key) < 128


def test_long_natural_key_uses_only_a_16_byte_request_digest() -> None:
    request = {"composition_mol": {f"component-{index}": index + 0.25 for index in range(100)}}
    key = engine_cache.engine_cache_key("openimcc", 1, request)
    assert len(key) == 20
    assert key[:3] == bytes((1, 0, 1))
    assert key[3] == 255
    assert key != engine_cache.engine_cache_key("openimcc", 1, {**request, "temperature_K": 1700.0})


def test_natural_key_wire_pin_and_signed_zero_survive_repeated_builds() -> None:
    # 1700 = 0x1.a9p+10: frexp exponent 11, significand 0x1a900000000000.
    expected = (
        bytes.fromhex("010001")
        + bytes.fromhex("000801840901043d001a900000000000")
    )
    for _ in range(3):
        assert engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": 1700.0}) == expected
        negative = engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": -0.0})
        positive = engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": 0.0})
        assert negative != positive


def test_key_namespace_is_explicit_and_store_is_without_rowid(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.cached_engine_call("openimcc", {"temperature_K": 1700.0}, lambda: {"answer": 1.0})
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='engine_outputs'").fetchone()[0]
        assert "WITHOUT ROWID" in schema


def test_schema_check_accepts_fresh_cache_after_interrupted_version_write(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={engine_cache._CACHE_SCHEMA_VERSION}")

    with sqlite3.connect(path) as connection:
        engine_cache._check_cache_schema(connection, allow_empty=True)
        engine_cache._initialize_cache(connection)
        engine_cache._check_cache_schema(connection)


def test_readonly_version_only_database_can_be_opened_twice(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "cache.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={engine_cache._CACHE_SCHEMA_VERSION}")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache._close_connections()
    try:
        for attempt in range(2):
            expected = {"answer": attempt}
            assert engine_cache.cached_engine_call(
                "openimcc", {"request": attempt}, lambda: expected
            ) == expected
    finally:
        engine_cache._close_connections()


@pytest.mark.parametrize(
    ("missing_tables", "missing_indexes"),
    [
        (
            ("provenance", "box_sequences"),
            ("engine_outputs_box_seq", "engine_outputs_engine_version"),
        ),
        (
            ("box_sequences",),
            ("engine_outputs_box_seq", "engine_outputs_engine_version"),
        ),
        ((), ("engine_outputs_box_seq", "engine_outputs_engine_version")),
    ],
    ids=("engine-outputs-only", "engine-outputs-and-provenance", "missing-indexes"),
)
@pytest.mark.parametrize("readonly", [False, True], ids=("writable", "readonly"))
def test_partial_cache_schema_is_repaired_or_missed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_tables: tuple[str, ...],
    missing_indexes: tuple[str, ...],
    readonly: bool,
) -> None:
    path = tmp_path / "partial.sqlite"
    with sqlite3.connect(path) as connection:
        engine_cache._initialize_cache(connection)
        for table in missing_tables:
            connection.execute(f"DROP TABLE {table}")
        for index in missing_indexes:
            connection.execute(f"DROP INDEX {index}")

    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly" if readonly else "")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache._close_connections()
    engine_cache.engine_cache_counters(reset=True)
    expected = {"answer": 42}
    try:
        assert engine_cache.cached_engine_call(
            "openimcc", {"request": "partial-schema"}, lambda: expected
        ) == expected
        counts = engine_cache.engine_cache_counters()["openimcc"]
        if readonly:
            assert counts["stores"] == 0
            assert engine_cache._CONNECTIONS == {}
            assert engine_cache._BLOOMS == {}
            with sqlite3.connect(path) as connection:
                names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
                    )
                }
            assert not (set(missing_tables) | set(missing_indexes)) & names
        else:
            assert counts["stores"] == 1
            assert counts["store_error"] == 0
            engine_cache._MEMORY_CACHE.clear()
            assert engine_cache.cached_engine_call(
                "openimcc",
                {"request": "partial-schema"},
                lambda: pytest.fail("repaired cache entry was not persisted"),
            ) == expected
            with sqlite3.connect(path) as connection:
                names = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
                    )
                }
            assert {
                "engine_outputs",
                "provenance",
                "box_sequences",
                "engine_output_species",
            } <= names
            assert {
                "engine_outputs_box_seq",
                "engine_outputs_engine_version",
                "engine_output_species_species",
            } <= names
    finally:
        engine_cache._close_connections()


def test_wrong_auxiliary_cache_schema_is_not_repaired(
    tmp_path: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "wrong-partial.sqlite"
    with sqlite3.connect(path) as connection:
        engine_cache._initialize_cache(connection)
        connection.execute("DROP TABLE box_sequences")
        connection.execute(
            "CREATE TABLE box_sequences (source_box TEXT PRIMARY KEY, wrong INTEGER NOT NULL) WITHOUT ROWID"
        )
    before = path.read_bytes()
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    try:
        with caplog.at_level("WARNING", logger=engine_cache.__name__):
            result = engine_cache.cached_engine_call(
                "openimcc", {"request": "wrong-partial-schema"}, lambda: {"answer": 42}
            )
        assert result == {"answer": 42}
        assert engine_cache._CONNECTIONS == {}
        assert engine_cache._BLOOMS == {}
        counts = engine_cache.engine_cache_counters()["openimcc"]
        assert counts["hits"] == 0
        assert counts["stores"] == 0
        assert counts["not_stored:schema_mismatch"] == 1
        notices = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("engine_cache_schema_mismatch")
        ]
        assert len(notices) == 1
        assert "where=box_sequences" in notices[0]
        assert "found=source_box,wrong" in notices[0]
        assert "scripts/engine_cache_sync.py migrate" in notices[0]
        assert path.read_bytes() == before
        with sqlite3.connect(path) as connection:
            columns = [
                row[1] for row in connection.execute("PRAGMA table_info(box_sequences)")
            ]
        assert columns == ["source_box", "wrong"]
    finally:
        engine_cache._close_connections()


def test_lossy_v2_cache_cannot_supply_a_hit(
    tmp_path: Path,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "legacy.sqlite"
    key = engine_cache.engine_cache_key("internal-analytical", 1, {})
    payload = engine_cache.pack({"vapor_pressure_backend_status_reason": "fallback"})
    with sqlite3.connect(path) as connection:
        engine_cache._initialize_cache(connection)
        connection.execute("PRAGMA user_version=2")
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (key, payload, "internal-analytical", "1", "laptop", 1, 0),
        )
    before = path.read_bytes()
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    live = {"answer": "live"}
    with caplog.at_level("WARNING", logger=engine_cache.__name__):
        result = engine_cache.cached_engine_call("internal-analytical", {}, lambda: live)
    assert result == live
    counts = engine_cache.engine_cache_counters()["internal-analytical"]
    assert counts["hits"] == 0
    assert counts["stores"] == 0
    assert counts["not_stored:schema_mismatch"] == 1
    notices = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("engine_cache_schema_mismatch")
    ]
    assert len(notices) == 1
    identity = os.path.realpath(path)
    assert "where=user_version " in notices[0]
    assert "expected=4 " in notices[0]
    assert "found=2 " in notices[0]
    assert notices[0].endswith(f"fix=scripts/engine_cache_sync.py migrate {identity}")
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM engine_outputs").fetchone()[0] == payload
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_key_refuses_nonfinite_inputs(value: float) -> None:
    with pytest.raises(ValueError):
        engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": value})
