"""Species sit beside a cache row. The key stays engine, version, and request."""

from __future__ import annotations

import math
import sqlite3
import zlib
from pathlib import Path

import pytest

from simulator import engine_cache
from simulator.engine_cache import EngineCacheValue, pack, unpack


# Recorded from engine_cache_key at 7714e73c20 for this probe. The function
# body is unchanged; these bytes are that output, not a second implementation.
_PROBE = {
    "temperature_K": 1700.0,
    "composition_wt_pct": {"SiO2": 60.0, "MgO": 40.0},
}
_KEY_PINS = {
    "openimcc": "01000100080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "internal-analytical": "02000300080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "vaporock": "03000100080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "magemin": "04000400080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "alphamelts": "05000400080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "thermoengine": "06000400080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
}
_SPECIES_FIELDS = (
    "composition_wt_pct",
    "composition_mol",
    "composition_kg",
    "composition",
    "parent_oxide_activities",
    "inventory",
    "parent_oxides",
    "native_activity_formula",
    "kwargs",
)


@pytest.fixture(autouse=True)
def _isolated_cache():
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    engine_cache.engine_cache_counters(reset=True)
    yield
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()


def _request_naming(field: str, name: str, marker: float) -> dict:
    if field == "parent_oxides":
        return {"parent_oxides": [name], "marker": marker}
    if field == "native_activity_formula":
        return {"native_activity_formula": name, "marker": marker}
    if field == "kwargs":
        return {"kwargs": {"composition_mol": {name: 1.0}}, "marker": marker}
    amount = 0.0 if field == "composition_wt_pct" else 1.0
    return {field: {name: amount}, "marker": marker}


def _store(monkeypatch, path: Path, engine: str, request: dict, answer: int = 1) -> None:
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache.cached_engine_call(
        engine, request, lambda answer=answer: {"answer": answer}
    )


def _output_rows(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT engine, row_seq, key FROM engine_outputs ORDER BY row_seq"
        ).fetchall()


def _species_by_row(path: Path) -> dict[int, set[str]]:
    with sqlite3.connect(path) as connection:
        found: dict[int, set[str]] = {}
        for row_seq, name in connection.execute(
            "SELECT row_seq, species FROM engine_output_species"
        ):
            found.setdefault(int(row_seq), set()).add(name)
        return found


def test_engine_cache_key_bytes_are_unchanged_for_every_engine() -> None:
    assert set(_KEY_PINS) == set(engine_cache.ENGINE_CHEMISTRY_VERSION)
    for engine, pinned in _KEY_PINS.items():
        version = engine_cache.ENGINE_CHEMISTRY_VERSION[engine]
        key = engine_cache.engine_cache_key(engine, version, _PROBE)
        assert key.hex() == pinned, engine
        assert len(key) == 57


def test_key_unpack_round_trip_keeps_negative_zero() -> None:
    request = {"temperature_K": -0.0, "composition_wt_pct": {"SiO2": 1.0}}
    restored = unpack(pack(request, key=True), key=True)
    assert restored["composition_wt_pct"] == {"SiO2": 1.0}
    assert math.copysign(1.0, restored["temperature_K"]) == -1.0
    positive = unpack(pack({"temperature_K": 0.0}, key=True), key=True)
    assert math.copysign(1.0, positive["temperature_K"]) == 1.0


def test_species_record_follows_each_request_field(tmp_path, monkeypatch) -> None:
    assert set(engine_cache._SPECIES_MAP_FIELDS) | set(
        engine_cache._SPECIES_SEQUENCE_FIELDS
    ) | set(engine_cache._SPECIES_NAME_FIELDS) | set(
        engine_cache._SPECIES_NESTED_FIELDS
    ) == set(_SPECIES_FIELDS)
    path = tmp_path / "species.sqlite"
    for index, field in enumerate(_SPECIES_FIELDS):
        _store(monkeypatch, path, "openimcc", _request_naming(field, "SiO2", index))
        _store(
            monkeypatch,
            path,
            "openimcc",
            _request_naming(field, "MgO", 100 + index),
        )
    recorded = _species_by_row(path)
    assert len(recorded) == len(_SPECIES_FIELDS) * 2
    assert {frozenset(names) for names in recorded.values()} == {
        frozenset({"SiO2"}),
        frozenset({"MgO"}),
    }


def test_expire_deletes_only_rows_that_name_the_species(tmp_path, monkeypatch) -> None:
    path = tmp_path / "expire.sqlite"
    for index, field in enumerate(_SPECIES_FIELDS):
        _store(monkeypatch, path, "openimcc", _request_naming(field, "SiO2", index))
        _store(
            monkeypatch,
            path,
            "openimcc",
            _request_naming(field, "MgO", 100 + index),
        )
    _store(
        monkeypatch,
        path,
        "openimcc",
        {"composition_wt_pct": {"SiO2": 10.0, "FeO": 5.0}, "marker": 9},
    )
    _store(monkeypatch, path, "openimcc", {"temperature_K": 1700.0})
    long_request = {
        "composition_mol": {f"Ox{index}": 1.0 for index in range(40)},
        "marker": 8,
    }
    long_request["composition_mol"]["SiO2"] = 1.0
    assert engine_cache.engine_cache_key("openimcc", 1, long_request)[3] == 0xFF
    _store(monkeypatch, path, "openimcc", long_request)
    _store(
        monkeypatch,
        path,
        "magemin",
        {"composition_wt_pct": {"SiO2": 3.0}, "marker": 7},
    )
    before = len(_output_rows(path))

    dry = engine_cache.expire_cache_file(path, engine="openimcc", species=("SiO2",))
    assert dry["dry_run"] is True and dry["apply"] is False
    assert dry["deleted_by_engine"]["openimcc"] == 0
    assert dry["vacuumed"] is False
    assert dry["rows_by_engine"]["openimcc"] == before - 1
    assert dry["rows_by_engine"]["magemin"] == 1
    # One SiO2 row per field, the multi-species row, and the digest-key write.
    assert dry["matched_by_engine"]["openimcc"] == len(_SPECIES_FIELDS) + 2
    assert len(_output_rows(path)) == before

    folded = engine_cache.expire_cache_file(path, engine="openimcc", species=("sio2",))
    assert folded["matched_by_engine"]["openimcc"] == 0

    applied = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("SiO2",), apply=True
    )
    assert applied["deleted_by_engine"]["openimcc"] == len(_SPECIES_FIELDS) + 2
    assert applied["vacuumed"] is True
    remaining = _species_by_row(path)
    assert all(names == {"MgO"} or names == {"SiO2"} for names in remaining.values())
    assert sum(names == {"MgO"} for names in remaining.values()) == len(_SPECIES_FIELDS)
    assert {"SiO2"} in remaining.values()  # magemin, a different engine
    engines = {engine for engine, _seq, _key in _output_rows(path)}
    assert engines == {"openimcc", "magemin"}
    openimcc_species = {
        frozenset(names)
        for seq, names in remaining.items()
        if any(engine == "openimcc" and row_seq == seq for engine, row_seq, _key in _output_rows(path))
    }
    assert openimcc_species == {frozenset({"MgO"})}
    empty_rows = [
        row_seq
        for engine, row_seq, _key in _output_rows(path)
        if engine == "openimcc" and row_seq not in remaining
    ]
    assert len(empty_rows) == 1


@pytest.mark.parametrize("name", ["SiO2", "FeO"])
def test_multi_species_row_expires_when_either_name_is_given(
    tmp_path, monkeypatch, name
) -> None:
    path = tmp_path / "multi.sqlite"
    _store(
        monkeypatch,
        path,
        "openimcc",
        {"composition_mol": {"SiO2": 2.0, "FeO": 1.0}},
    )
    _store(monkeypatch, path, "openimcc", {"composition_mol": {"MgO": 1.0}})
    unrelated = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("Na2O",), apply=True
    )
    assert unrelated["matched_by_engine"]["openimcc"] == 0
    assert len(_output_rows(path)) == 2
    applied = engine_cache.expire_cache_file(
        path, engine="openimcc", species=(name,), apply=True
    )
    assert applied["deleted_by_engine"]["openimcc"] == 1
    assert list(_species_by_row(path).values()) == [{"MgO"}]


def test_expire_all_includes_rows_with_no_species(tmp_path, monkeypatch) -> None:
    path = tmp_path / "all.sqlite"
    _store(monkeypatch, path, "openimcc", {"temperature_K": 1600.0})
    _store(monkeypatch, path, "magemin", {"temperature_K": 1600.0})
    dry = engine_cache.expire_cache_file(path, engine="openimcc", all_rows=True)
    assert dry["matched_by_engine"]["openimcc"] == 1
    assert dry["deleted_by_engine"]["openimcc"] == 0
    assert len(_output_rows(path)) == 2
    applied = engine_cache.expire_cache_file(
        path, engine="openimcc", all_rows=True, apply=True
    )
    assert applied["deleted_by_engine"]["openimcc"] == 1
    assert [engine for engine, _seq, _key in _output_rows(path)] == ["magemin"]


def test_expire_refuses_an_ambiguous_selector() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        engine_cache.expire_cache_file(
            "unused.sqlite", engine="openimcc", species=("SiO2",), all_rows=True
        )
    with pytest.raises(ValueError, match="unknown engine"):
        engine_cache.expire_cache_file(
            "unused.sqlite", engine="not-an-engine", species=("SiO2",)
        )


def test_unreadable_species_is_not_stored_as_empty(tmp_path, monkeypatch) -> None:
    path = tmp_path / "bad-shape.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.engine_cache_counters(reset=True)
    result = engine_cache.cached_engine_call(
        "openimcc",
        {"composition_wt_pct": ["SiO2"]},
        lambda: {"answer": 5},
    )
    assert result == {"answer": 5}
    counts = engine_cache.engine_cache_counters()["openimcc"]
    assert counts["not_stored:species"] == 1
    assert counts["stores"] == 0
    assert _output_rows(path) == []


def test_read_and_write_never_expire(tmp_path, monkeypatch) -> None:
    def explode(*_args, **_kwargs):
        raise AssertionError("cache read/write called a maintenance tool")

    monkeypatch.setattr(engine_cache, "expire_cache_file", explode)
    monkeypatch.setattr(engine_cache, "prune_cache_file", explode)
    monkeypatch.setattr(engine_cache, "migrate_cache_file", explode)
    path = tmp_path / "live.sqlite"
    _store(monkeypatch, path, "openimcc", {"composition_wt_pct": {"SiO2": 1.0}})
    engine_cache._MEMORY_CACHE.clear()
    assert engine_cache.cached_engine_call(
        "openimcc",
        {"composition_wt_pct": {"SiO2": 1.0}},
        lambda: pytest.fail("stored row caused a miss"),
    ) == {"answer": 1}


def test_prune_deletes_only_stale_known_versions(tmp_path, monkeypatch) -> None:
    path = tmp_path / "prune.sqlite"
    _store(monkeypatch, path, "openimcc", {"temperature_K": 1700.0}, answer=1)
    _store(monkeypatch, path, "openimcc", {"temperature_K": 1800.0}, answer=2)
    _store(monkeypatch, path, "magemin", {"temperature_K": 1700.0}, answer=3)
    engine_cache._close_connections()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE engine_outputs SET chem_version='0' "
            "WHERE engine='openimcc' AND row_seq=("
            "SELECT MIN(row_seq) FROM engine_outputs WHERE engine='openimcc')"
        )
        connection.execute(
            "UPDATE engine_outputs SET engine='retired-engine', chem_version='1' "
            "WHERE engine='magemin'"
        )
    dry = engine_cache.prune_cache_file(path)
    assert dry["dry_run"] is True and dry["apply"] is False
    assert dry["matched_by_engine"]["openimcc"] == 1
    assert dry["deleted_by_engine"]["openimcc"] == 0
    assert dry["skipped_unknown_engine"] == {"retired-engine": 1}
    assert dry["vacuumed"] is False
    assert len(_output_rows(path)) == 3
    applied = engine_cache.prune_cache_file(path, apply=True)
    assert applied["deleted_by_engine"]["openimcc"] == 1
    assert applied["vacuumed"] is True
    remaining = _output_rows(path)
    assert sorted(engine for engine, _seq, _key in remaining) == [
        "openimcc",
        "retired-engine",
    ]


def _schema_v3(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE engine_outputs ("
        "key BLOB PRIMARY KEY, value BLOB NOT NULL, engine TEXT NOT NULL, "
        "chem_version TEXT NOT NULL, source_box TEXT NOT NULL, "
        "row_seq INTEGER NOT NULL, provenance_id INTEGER NOT NULL DEFAULT 0"
        ") WITHOUT ROWID"
    )
    connection.execute(
        "CREATE TABLE provenance (id INTEGER PRIMARY KEY, value BLOB NOT NULL UNIQUE)"
    )
    connection.execute(
        "CREATE TABLE box_sequences ("
        "source_box TEXT PRIMARY KEY, next_seq INTEGER NOT NULL) WITHOUT ROWID"
    )
    connection.execute(
        "CREATE UNIQUE INDEX engine_outputs_box_seq "
        "ON engine_outputs(source_box, row_seq)"
    )
    connection.execute(
        "CREATE INDEX engine_outputs_engine_version "
        "ON engine_outputs(engine, chem_version)"
    )
    connection.execute("PRAGMA user_version=3")
    return connection


def test_migrate_keeps_every_hot_and_cold_value(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite"
    natural_request = {
        "composition_wt_pct": {"SiO2": 60.0, "MgO": 40.0},
        "temperature_K": 1700.0,
    }
    digest_request = {"composition_mol": {f"Ox{index}": float(index + 1) for index in range(40)}}
    natural_key = engine_cache.engine_cache_key("openimcc", 1, natural_request)
    digest_key = engine_cache.engine_cache_key("magemin", 4, digest_request)
    assert digest_key[3] == 0xFF
    assert engine_cache.species_from_natural_key(digest_key) is None
    first = engine_cache.ENGINE_CACHE_BOX_RANGES["laptop"][0]
    natural_value = pack({"answer": 1})
    digest_value = pack({"answer": 2})
    linked_cold = zlib.compress(pack({"enthalpy": 3.0}))
    digest_cold = zlib.compress(pack({"enthalpy": 5.0}))
    orphan_cold = zlib.compress(pack({"enthalpy": 4.0}))
    with _schema_v3(path) as connection:
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (natural_key, natural_value, "openimcc", "1", "laptop", first, 0),
        )
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (digest_key, digest_value, "magemin", "4", "laptop", first + 1, 0),
        )
        connection.execute("INSERT INTO box_sequences VALUES (?,?)", ("laptop", first + 2))
    with sqlite3.connect(str(path) + ".cold") as cold:
        cold.execute(
            "CREATE TABLE engine_values ("
            "key BLOB PRIMARY KEY, value BLOB NOT NULL, input_fingerprint BLOB"
            ") WITHOUT ROWID"
        )
        cold.executemany(
            "INSERT INTO engine_values VALUES (?,?,?)",
            [
                (natural_key, linked_cold, b"old-fingerprint"),
                (digest_key, digest_cold, b"old-fingerprint"),
                (b"orphan-key", orphan_cold, b"old-fingerprint"),
            ],
        )
    before_hot = [(natural_key, natural_value), (digest_key, digest_value)]

    report = engine_cache.migrate_cache_file(path)

    assert report["migrated"] is True
    assert report["already_current"] is False
    assert report["vacuumed"] is False
    assert report["natural_keys"] == 1
    assert report["digest_keys"] == 1
    assert report["unlinked_cold"] == 1
    assert report["before"]["cold_value_bytes"] == (
        len(linked_cold) + len(digest_cold) + len(orphan_cold)
    )
    assert report["before"]["cold_key_bytes"] > 0
    assert report["before"]["cold_fingerprint_bytes"] > 0
    assert report["after_payload"]["cold_value_bytes"] == report["before"]["cold_value_bytes"]
    with sqlite3.connect(path) as connection:
        after_hot = [
            (bytes(key), bytes(value))
            for key, value in connection.execute(
                "SELECT key, value FROM engine_outputs ORDER BY row_seq"
            )
        ]
        species: dict[int, set[str]] = {}
        for row_seq, name in connection.execute(
            "SELECT row_seq, species FROM engine_output_species"
        ):
            species.setdefault(int(row_seq), set()).add(name)
    assert after_hot == before_hot
    assert set(species) == {first}
    assert species[first] == set(engine_cache.cache_request_species(natural_request))
    with sqlite3.connect(str(path) + ".cold") as cold:
        columns = [row[1] for row in cold.execute("PRAGMA table_info(engine_values)")]
        stored = {
            int(row_seq): bytes(value)
            for row_seq, value in cold.execute("SELECT row_seq, value FROM engine_values")
        }
        identities = cold.execute(
            "SELECT v.row_seq, b.engine, b.identity FROM engine_values v "
            "JOIN engine_builds b ON b.id=v.engine_build_id"
        ).fetchall()
        assert cold.execute("PRAGMA user_version").fetchone()[0] == 4
    assert columns == ["row_seq", "value", "engine_build_id"]
    assert stored[first] == linked_cold
    assert stored[first + 1] == digest_cold
    orphan_seq = next(row_seq for row_seq in stored if row_seq < 0)
    assert stored[orphan_seq] == orphan_cold
    by_seq = {int(row_seq): (engine, identity) for row_seq, engine, identity in identities}
    assert by_seq[first] == ("openimcc", engine_cache._UNKNOWN_PRE_MIGRATION_BUILD)
    assert by_seq[first + 1] == ("magemin", engine_cache._UNKNOWN_PRE_MIGRATION_BUILD)
    assert by_seq[orphan_seq] == ("unlinked", engine_cache._UNKNOWN_PRE_MIGRATION_BUILD)

    again = engine_cache.migrate_cache_file(path)
    assert again["already_current"] is True
    assert again["migrated"] is False
    with sqlite3.connect(str(path) + ".cold") as cold:
        assert cold.execute("SELECT COUNT(*) FROM engine_values").fetchone()[0] == 3

    expired = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("SiO2",), apply=True
    )
    assert expired["deleted_by_engine"]["openimcc"] == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT engine FROM engine_outputs"
        ).fetchall() == [("magemin",)]
    with sqlite3.connect(str(path) + ".cold") as cold:
        left = {
            int(row_seq)
            for (row_seq,) in cold.execute("SELECT row_seq FROM engine_values")
        }
    assert left == {first + 1, orphan_seq}


def test_migrate_refuses_user_version_2(tmp_path) -> None:
    path = tmp_path / "v2.sqlite"
    with _schema_v3(path) as connection:
        connection.execute("PRAGMA user_version=2")
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (b"key", pack({"answer": 1}), "openimcc", "1", "laptop", 1, 0),
        )
    with pytest.raises(engine_cache.EngineCacheSchemaMismatch, match="user_version"):
        engine_cache.migrate_cache_file(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name='engine_output_species'"
        ).fetchall() == []
        assert connection.execute("SELECT value FROM engine_outputs").fetchone()[0] == pack(
            {"answer": 1}
        )
