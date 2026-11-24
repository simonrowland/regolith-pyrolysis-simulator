"""Species sit beside a cache row. The key stays engine, version, and request."""

from __future__ import annotations

import io
import json
import math
import os
import sqlite3
import zlib
from contextlib import redirect_stderr, redirect_stdout
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
    # The train takes max+1 against Markova; keep the resulting v4 header pinned.
    "internal-analytical": "02000400080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "vaporock": "03000100080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "magemin": "04000400080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
    "alphamelts": "05000500080280080205034d674f090104380014000000000000050453694f3209010438001e000000000000840901043d001a900000000000",
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
        frozenset({"sio2"}),
        frozenset({"mgo"}),
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
    assert folded["matched_by_engine"]["openimcc"] == len(_SPECIES_FIELDS) + 2

    applied = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("SiO2",), apply=True
    )
    assert applied["deleted_by_engine"]["openimcc"] == len(_SPECIES_FIELDS) + 2
    assert applied["vacuumed"] is True
    remaining = _species_by_row(path)
    assert all(names == {"mgo"} or names == {"sio2"} for names in remaining.values())
    assert sum(names == {"mgo"} for names in remaining.values()) == len(_SPECIES_FIELDS)
    assert {"sio2"} in remaining.values()  # magemin, a different engine
    engines = {engine for engine, _seq, _key in _output_rows(path)}
    assert engines == {"openimcc", "magemin"}
    openimcc_species = {
        frozenset(names)
        for seq, names in remaining.items()
        if any(engine == "openimcc" and row_seq == seq for engine, row_seq, _key in _output_rows(path))
    }
    assert openimcc_species == {frozenset({"mgo"})}
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
    assert list(_species_by_row(path).values()) == [{"mgo"}]


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
    assert report["vacuumed"] is True
    assert report["species_unknown_by_engine"] == {"magemin": 1}
    assert report["skipped_unreadable_keys"] == 0
    assert isinstance(report["before"]["hot"]["database_bytes"], int)
    assert isinstance(report["after"]["hot"]["database_bytes"], int)
    assert isinstance(report["after"]["cold"]["database_bytes"], int)
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

    assert not path.with_name(path.name + ".v3.bak").exists()
    assert not path.with_name(path.name + ".v4-new").exists()
    cold_file = Path(str(path) + ".cold")
    assert not cold_file.with_name(cold_file.name + ".v3.bak").exists()
    assert not cold_file.with_name(cold_file.name + ".v4-new").exists()

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
    # The unlinked value has no HOT row. apply sweeps it. The magemin
    # value stays because its HOT row is still there.
    assert left == {first + 1}


def test_expire_species_drops_migrated_digest_keys(tmp_path) -> None:
    """A blake2b row with no species record is unknown for every species name.

    A natural key that names a different species stays. So does a natural key
    that named no species, and a digest key of another engine.
    """

    path = tmp_path / "digest-unknown.sqlite"
    natural_request = {"composition_wt_pct": {"MgO": 40.0}, "temperature_K": 1700.0}
    empty_request = {"temperature_K": 1800.0}
    digest_request = {
        "composition_mol": {f"Ox{index}": float(index + 1) for index in range(40)}
    }
    natural_key = engine_cache.engine_cache_key("openimcc", 1, natural_request)
    empty_key = engine_cache.engine_cache_key("openimcc", 1, empty_request)
    digest_key = engine_cache.engine_cache_key("openimcc", 1, digest_request)
    other_key = engine_cache.engine_cache_key("magemin", 4, digest_request)
    assert digest_key[3] == 0xFF
    assert other_key[3] == 0xFF
    first = engine_cache.ENGINE_CACHE_BOX_RANGES["laptop"][0]
    with _schema_v3(path) as connection:
        rows = (
            (natural_key, "openimcc", "1", first),
            (empty_key, "openimcc", "1", first + 1),
            (digest_key, "openimcc", "1", first + 2),
            (other_key, "magemin", "4", first + 3),
        )
        for key, engine, version, row_seq in rows:
            connection.execute(
                "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
                (key, pack({"answer": row_seq}), engine, version, "laptop", row_seq, 0),
            )
        connection.execute(
            "INSERT INTO box_sequences VALUES (?,?)", ("laptop", first + 4)
        )
    engine_cache.migrate_cache_file(path)

    dry = engine_cache.expire_cache_file(path, engine="openimcc", species=("SiO2",))
    assert dry["dry_run"] is True
    assert dry["species_unknown_rows"] == 1
    assert dry["matched_by_engine"]["openimcc"] == 1
    assert dry["deleted_by_engine"]["openimcc"] == 0
    assert len(_output_rows(path)) == 4

    from scripts.engine_cache_sync import main

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        assert main(["expire", str(path), "--engine", "openimcc", "--species", "SiO2"]) == 0
    assert stderr.getvalue().splitlines()[0] == "species-unknown rows: 1"
    assert len(_output_rows(path)) == 4

    applied = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("SiO2",), apply=True
    )
    assert applied["deleted_by_engine"]["openimcc"] == 1
    assert applied["species_unknown_rows"] == 1
    left = {bytes(key) for _engine, _seq, key in _output_rows(path)}
    assert left == {natural_key, empty_key, other_key}


def test_expire_matches_iron_total_and_case(tmp_path, monkeypatch) -> None:
    path = tmp_path / "names.sqlite"
    _store(
        monkeypatch,
        path,
        "openimcc",
        {"composition_wt_pct": {"FeO_total": 10.0}, "marker": 1},
    )
    _store(
        monkeypatch,
        path,
        "openimcc",
        {"composition_wt_pct": {"sio2": 20.0}, "marker": 2},
    )
    _store(
        monkeypatch,
        path,
        "openimcc",
        {"composition_wt_pct": {"Fe2O3": 3.0}, "marker": 3},
    )
    recorded = set().union(*_species_by_row(path).values())
    assert recorded == {"feo_total", "sio2", "fe2o3"}

    # The leaf matches the stored spelling. It does not expand oxide aliases.
    iron = engine_cache.expire_cache_file(path, engine="openimcc", species=("FeO",))
    assert iron["matched_by_engine"]["openimcc"] == 0
    spelled = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("feo_total",)
    )
    assert spelled["matched_by_engine"]["openimcc"] == 1
    silica = engine_cache.expire_cache_file(path, engine="openimcc", species=("SIO2",))
    assert silica["matched_by_engine"]["openimcc"] == 1

    from scripts.engine_cache_sync import main

    def _expire(species: str, apply: bool = False) -> dict:
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["expire", str(path), "--engine", "openimcc", "--species", species]
        if apply:
            argv.append("--apply")
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert main(argv) == 0
        assert stderr.getvalue().splitlines()[0] == "species-unknown rows: 0"
        return json.loads(stdout.getvalue())

    iron_cli = _expire("FeO")
    assert iron_cli["matched_by_engine"]["openimcc"] == 1
    assert "feo_total" in iron_cli["species"]
    assert "fe2o3" not in iron_cli["species"]
    silica_cli = _expire("sio2")
    assert silica_cli["matched_by_engine"]["openimcc"] == 1
    assert "sio2" in silica_cli["species"]
    assert "feo_total" not in silica_cli["species"]

    applied = _expire("FeO", apply=True)
    assert applied["deleted_by_engine"]["openimcc"] == 1
    left = set().union(*_species_by_row(path).values())
    assert left == {"sio2", "fe2o3"}


def test_expire_species_includes_feedstock_total_iron_spellings(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "feedstock-iron.sqlite"
    for marker, name in enumerate(
        ("FeO_T", "FeO*", "FeOT", "FeOt", "FeO_total", "feo"), start=1
    ):
        _store(
            monkeypatch,
            path,
            "openimcc",
            {"composition_wt_pct": {name: 1.0}, "marker": marker},
        )
    _store(
        monkeypatch,
        path,
        "openimcc",
        {"composition_wt_pct": {"Fe2O3": 1.0}, "marker": 7},
    )
    # The leaf matches the stored casefold only. "feo" is that spelling.
    leaf = engine_cache.expire_cache_file(path, engine="openimcc", species=("FeO",))
    assert leaf["matched_by_engine"]["openimcc"] == 1

    from scripts.engine_cache_sync import main

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        assert main(
            ["expire", str(path), "--engine", "openimcc", "--species", "FeO", "--apply"]
        ) == 0
    assert stderr.getvalue().splitlines()[0] == "species-unknown rows: 0"
    report = json.loads(stdout.getvalue())
    assert report["deleted_by_engine"]["openimcc"] == 6
    for spelling in ("feo_t", "feo*", "feot", "feo_total", "feo"):
        assert spelling in report["species"]
    assert "fe2o3" not in report["species"]
    assert set().union(*_species_by_row(path).values()) == {"fe2o3"}


def test_expire_counts_an_unclassifiable_key_and_continues(tmp_path, monkeypatch) -> None:
    path = tmp_path / "bad-flag.sqlite"
    _store(monkeypatch, path, "openimcc", {"composition_wt_pct": {"SiO2": 1.0}})
    bad = bytearray(
        engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": 1800.0})
    )
    bad[3] = 0x01
    with sqlite3.connect(path) as connection:
        seq = connection.execute("SELECT MAX(row_seq) FROM engine_outputs").fetchone()[0]
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (bytes(bad), pack({"answer": 9}), "openimcc", "1", "laptop", seq + 1, 0),
        )
    dry = engine_cache.expire_cache_file(path, engine="openimcc", species=("SiO2",))
    assert dry["unclassifiable_rows"] == 1
    assert dry["species_unknown_rows"] == 0
    assert dry["matched_by_engine"]["openimcc"] == 1
    assert len(_output_rows(path)) == 2

    from scripts.engine_cache_sync import main

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        assert main(
            ["expire", str(path), "--engine", "openimcc", "--species", "SiO2", "--apply"]
        ) == 0
    lines = stderr.getvalue().splitlines()
    assert lines[0] == "species-unknown rows: 0"
    assert "unclassifiable rows: 1" in lines
    report = json.loads(stdout.getvalue())
    assert report["deleted_by_engine"]["openimcc"] == 1
    assert report["unclassifiable_rows"] == 1
    left = _output_rows(path)
    assert len(left) == 1
    assert bytes(left[0][2])[3] == 0x01


def test_migrate_skips_an_unreadable_key_and_warns(tmp_path) -> None:
    path = tmp_path / "flag.sqlite"
    natural = engine_cache.engine_cache_key(
        "openimcc", 1, {"composition_wt_pct": {"MgO": 1.0}}
    )
    first = engine_cache.ENGINE_CACHE_BOX_RANGES["laptop"][0]
    with _schema_v3(path) as connection:
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (natural, pack({"answer": 1}), "openimcc", "1", "laptop", first, 0),
        )
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (b"race-key", pack({"answer": 2}), "openimcc", "1", "laptop", first + 1, 0),
        )
        connection.execute("INSERT INTO box_sequences VALUES (?,?)", ("laptop", first + 2))

    from scripts.engine_cache_sync import main

    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        assert main(["migrate", str(path), "--no-vacuum"]) == 0
    report = json.loads(stdout.getvalue())
    assert report["skipped_unreadable_keys"] == 1
    assert report["natural_keys"] == 1
    assert report["vacuumed"] is False
    assert report["species_unknown_by_engine"] == {}
    assert "prefer re-warming over migrating" in stderr.getvalue()
    assert "migrate bytes:" in stderr.getvalue()
    assert "species-unknown rows: 0 per engine" in stderr.getvalue()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 2
        names = {
            name
            for (name,) in connection.execute("SELECT species FROM engine_output_species")
        }
    assert names == {"mgo"}


def test_migrate_aborts_when_a_row_changes_and_says_rerun(tmp_path, monkeypatch) -> None:
    path = tmp_path / "writer.sqlite"
    key = engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": 1700.0})
    original = pack({"answer": 1})
    first = engine_cache.ENGINE_CACHE_BOX_RANGES["laptop"][0]
    with _schema_v3(path) as connection:
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (key, original, "openimcc", "1", "laptop", first, 0),
        )
        connection.execute("INSERT INTO box_sequences VALUES (?,?)", ("laptop", first + 1))
    real = engine_cache._create_species_table

    def mutate(connection: sqlite3.Connection) -> None:
        real(connection)
        connection.execute("UPDATE engine_outputs SET value=?", (b"changed",))

    monkeypatch.setattr(engine_cache, "_create_species_table", mutate)
    with pytest.raises(engine_cache.EngineCacheSchemaMismatch, match="rerun with no writer"):
        engine_cache.migrate_cache_file(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT value FROM engine_outputs").fetchone()[0] == original
    assert not path.with_name(path.name + ".v4-new").exists()
    assert not path.with_name(path.name + ".v3.bak").exists()


def test_refused_open_keeps_the_journal_mode(tmp_path) -> None:
    path = tmp_path / "v3.sqlite"
    with _schema_v3(path) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (b"01234567", pack({"answer": 1}), "openimcc", "1", "laptop", 1, 0),
        )
    try:
        with pytest.raises(engine_cache.EngineCacheSchemaMismatch):
            engine_cache._connect(path, readonly=False)
    finally:
        engine_cache._close_connections()
    with sqlite3.connect(path) as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "delete"
    assert not path.with_name(path.name + "-wal").exists()


def test_drop_chemistry_version_commits_hot_before_cold(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "drop.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)

    def store() -> None:
        engine_cache.cached_engine_call(
            "openimcc",
            {"composition_wt_pct": {"MgO": 1.0}, "marker": 1},
            lambda: engine_cache.EngineCacheValue(
                hot={"answer": 1}, cold={"enthalpy": 2.0}
            ),
        )

    cold_path = path.with_name(path.name + ".cold")
    store()
    assert cold_path.is_file()
    assert engine_cache.drop_chemistry_version("openimcc", 1) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM engine_output_species").fetchone()[0] == 0
    with sqlite3.connect(cold_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_values").fetchone()[0] == 0

    store()

    def boom(connection, _pairs) -> None:
        hot_left = connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0]
        if hot_left != 0:
            raise AssertionError("cold delete ran while hot rows remained")
        raise sqlite3.OperationalError("injected between hot and cold")

    original = engine_cache._drop_attached_cold
    monkeypatch.setattr(engine_cache, "_drop_attached_cold", boom)
    with pytest.raises(sqlite3.OperationalError, match="injected between hot and cold"):
        engine_cache.drop_chemistry_version("openimcc", 1)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM engine_output_species").fetchone()[0] == 0
    with sqlite3.connect(cold_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_values").fetchone()[0] == 1

    # The HOT row is gone, so the same request is not a hit. A plain dict
    # does not write a new COLD value, and the orphan stays until the retry.
    served = engine_cache.cached_engine_call(
        "openimcc",
        {"composition_wt_pct": {"MgO": 1.0}, "marker": 1},
        lambda: {"answer": 9},
    )
    assert served == {"answer": 9}
    with sqlite3.connect(cold_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_values").fetchone()[0] == 1

    monkeypatch.setattr(engine_cache, "_drop_attached_cold", original)
    assert engine_cache.drop_chemistry_version("openimcc", 1) == 1
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM engine_output_species").fetchone()[0] == 0
    with sqlite3.connect(cold_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM engine_values").fetchone()[0] == 0


def test_known_empty_digest_survives_species_expire(tmp_path, monkeypatch) -> None:
    """A blake2b request that named nothing is not species-unknown.

    The empty side-row marker is not a species name. A raw flag-0xff row
    with no side rows, the pre-migration shape, is still expired. A natural
    key that named nothing has no side row and also survives.
    """

    path = tmp_path / "empty-digest.sqlite"
    empty_digest = {"label": "x" * 200}
    natural_empty = {"temperature_K": 1700.0}
    digest_key = engine_cache.engine_cache_key("openimcc", 1, empty_digest)
    natural_key = engine_cache.engine_cache_key("openimcc", 1, natural_empty)
    assert digest_key[3] == 0xFF
    assert natural_key[3] == 0x00
    assert engine_cache.cache_request_species(empty_digest) == frozenset()
    assert engine_cache.cache_request_species(natural_empty) == frozenset()
    _store(monkeypatch, path, "openimcc", empty_digest)
    _store(monkeypatch, path, "openimcc", natural_empty)
    recorded = _species_by_row(path)
    assert list(recorded.values()) == [{""}]
    raw = engine_cache.engine_cache_key("openimcc", 1, {"label": "y" * 200, "other": 1})
    assert raw[3] == 0xFF and raw != digest_key
    engine_cache._close_connections()
    with sqlite3.connect(path) as connection:
        seq = connection.execute("SELECT MAX(row_seq) FROM engine_outputs").fetchone()[0]
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (raw, pack({"answer": 3}), "openimcc", "1", "laptop", seq + 1, 0),
        )
    dry = engine_cache.expire_cache_file(path, engine="openimcc", species=("SiO2",))
    assert dry["species_unknown_rows"] == 1
    assert dry["matched_by_engine"]["openimcc"] == 1
    assert dry["deleted_by_engine"]["openimcc"] == 0
    applied = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("SiO2",), apply=True
    )
    assert applied["deleted_by_engine"]["openimcc"] == 1
    left = {bytes(key) for _engine, _seq, key in _output_rows(path)}
    assert left == {digest_key, natural_key}
    assert set().union(*_species_by_row(path).values()) == {""}


def _plant_orphan_cold(path: Path) -> None:
    engine_cache._close_connections()
    cold = Path(str(path) + ".cold")
    with sqlite3.connect(cold) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO engine_builds(engine, identity) "
            "VALUES ('unlinked', 'planted-orphan')"
        )
        build_id = connection.execute(
            "SELECT id FROM engine_builds WHERE identity='planted-orphan'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO engine_values(row_seq, value, engine_build_id) VALUES (?,?,?)",
            (-1, b"orphan-value", build_id),
        )


def test_expire_and_prune_sweep_orphan_cold(tmp_path, monkeypatch) -> None:
    path = tmp_path / "sweep.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache.cached_engine_call(
        "openimcc",
        {"composition_wt_pct": {"MgO": 1.0}},
        lambda: engine_cache.EngineCacheValue(hot={"answer": 1}, cold={"enthalpy": 2.0}),
    )
    cold = Path(str(path) + ".cold")
    _plant_orphan_cold(path)

    def cold_seqs() -> set[int]:
        with sqlite3.connect(cold) as connection:
            return {
                int(row_seq)
                for (row_seq,) in connection.execute("SELECT row_seq FROM engine_values")
            }

    linked = cold_seqs() - {-1}
    assert len(linked) == 1
    dry = engine_cache.expire_cache_file(path, engine="openimcc", species=("SiO2",))
    assert dry["dry_run"] is True
    assert dry["matched_by_engine"]["openimcc"] == 0
    assert cold_seqs() == linked | {-1}
    unrelated = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("SiO2",), apply=True
    )
    assert unrelated["matched_by_engine"]["openimcc"] == 0
    assert unrelated["deleted_by_engine"]["openimcc"] == 0
    assert unrelated["vacuumed"] is True
    assert cold_seqs() == linked

    _plant_orphan_cold(path)
    pruned = engine_cache.prune_cache_file(path, apply=True)
    assert pruned["matched_by_engine"]["openimcc"] == 0
    assert pruned["vacuumed"] is True
    assert cold_seqs() == linked

    _plant_orphan_cold(path)
    removed = engine_cache.expire_cache_file(
        path, engine="openimcc", species=("MgO",), apply=True
    )
    assert removed["deleted_by_engine"]["openimcc"] == 1
    assert cold_seqs() == set()
    assert _output_rows(path) == []


def test_migrate_publish_failure_leaves_the_schema_3_pair(tmp_path, monkeypatch) -> None:
    path = tmp_path / "publish.sqlite"
    key = engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": 1700.0})
    original = pack({"answer": 1})
    cold_value = b"cold-bytes"
    first = engine_cache.ENGINE_CACHE_BOX_RANGES["laptop"][0]
    with _schema_v3(path) as connection:
        connection.execute(
            "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
            (key, original, "openimcc", "1", "laptop", first, 0),
        )
        connection.execute("INSERT INTO box_sequences VALUES (?,?)", ("laptop", first + 1))
    cold = Path(str(path) + ".cold")
    with sqlite3.connect(cold) as connection:
        connection.execute(
            "CREATE TABLE engine_values ("
            "key BLOB PRIMARY KEY, value BLOB NOT NULL, input_fingerprint BLOB"
            ") WITHOUT ROWID"
        )
        connection.execute(
            "INSERT INTO engine_values VALUES (?,?,?)",
            (key, cold_value, b"fingerprint"),
        )

    def boom(_live, _hot_new, _cold_new) -> None:
        raise sqlite3.OperationalError("injected before publish")

    monkeypatch.setattr(engine_cache, "_publish_migrated_pair", boom)
    with pytest.raises(sqlite3.OperationalError, match="injected before publish"):
        engine_cache.migrate_cache_file(path)
    assert not path.with_name(path.name + ".v4-new").exists()
    assert not path.with_name(path.name + ".v3.bak").exists()
    assert not cold.with_name(cold.name + ".v4-new").exists()
    assert not cold.with_name(cold.name + ".v3.bak").exists()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT value FROM engine_outputs").fetchone()[0] == original
    with sqlite3.connect(cold) as connection:
        assert connection.execute("SELECT value FROM engine_values").fetchone()[0] == cold_value


_RECOVER_MESSAGE = "interrupted migrate; run engine_cache_sync.py migrate --recover"


def _hot_pairs(path: Path) -> list[tuple[bytes, bytes]]:
    with sqlite3.connect(path) as connection:
        return [
            (bytes(key), bytes(value))
            for key, value in connection.execute(
                "SELECT key, value FROM engine_outputs ORDER BY row_seq"
            )
        ]


def _schema3_pair(path: Path) -> list[tuple[bytes, bytes]]:
    """Two HOT rows and one linked COLD row at schema 3, including its indexes."""

    rows: list[tuple[bytes, bytes]] = []
    first = engine_cache.ENGINE_CACHE_BOX_RANGES["laptop"][0]
    with _schema_v3(path) as connection:
        connection.commit()
        connection.execute("PRAGMA journal_mode=DELETE")
        for offset in (0, 1):
            key = engine_cache.engine_cache_key(
                "openimcc",
                1,
                {
                    "temperature_K": 1700.0 + offset,
                    "composition_wt_pct": {"SiO2": 60.0, "MgO": 40.0},
                },
            )
            value = pack({"answer": offset + 1})
            rows.append((key, value))
            connection.execute(
                "INSERT INTO engine_outputs VALUES (?,?,?,?,?,?,?)",
                (key, value, "openimcc", "1", "laptop", first + offset, 0),
            )
        connection.execute("INSERT INTO box_sequences VALUES (?,?)", ("laptop", first + 2))
    cold = Path(str(path) + ".cold")
    with sqlite3.connect(cold) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute(
            "CREATE TABLE engine_values ("
            "key BLOB PRIMARY KEY, value BLOB NOT NULL, input_fingerprint BLOB"
            ") WITHOUT ROWID"
        )
        connection.execute("PRAGMA user_version=3")
        connection.execute(
            "INSERT INTO engine_values VALUES (?,?,?)",
            (rows[0][0], b"cold-bytes", b"fingerprint"),
        )
    return rows


def _user_version(path: Path) -> int | None:
    if not path.is_file():
        return None
    with sqlite3.connect(path) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _open_outcome(path: Path, *, readonly: bool) -> tuple:
    """What ``_connect`` does. A served pair must have rows; a refusal must not create a file."""

    engine_cache._close_connections()
    existed = path.is_file()
    try:
        connection = engine_cache._connect(path, readonly=readonly)
        if connection is None:
            return ("missing",)
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        count = int(connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0])
        cold_version = _user_version(Path(str(path) + ".cold"))
        return ("served", version, count, cold_version)
    except engine_cache.EngineCacheSchemaMismatch as exc:
        return ("refused", str(exc))
    finally:
        engine_cache._close_connections()
        if not existed:
            assert not path.exists()


def _assert_open_keeps_rows(path: Path, rows: list[tuple[bytes, bytes]], *, step: str) -> None:
    """No open yields an empty cache. It refuses, or it reads a complete pair."""

    for readonly in (True, False):
        outcome = _open_outcome(path, readonly=readonly)
        if step == "hot":
            assert outcome[0] == "served"
            version, count, cold_version = outcome[1:]
            assert count == len(rows)
            assert version == engine_cache._CACHE_SCHEMA_VERSION
            assert cold_version == version
        else:
            assert outcome[0] == "refused"
            if step == "cold":
                assert _RECOVER_MESSAGE in outcome[1]
    assert path.is_file()
    version = _user_version(path)
    if version == engine_cache._CACHE_SCHEMA_VERSION:
        assert _hot_pairs(path) == rows
    else:
        assert version == 3
        assert _hot_pairs(path) == rows
    if step != "hot":
        with sqlite3.connect(path) as connection:
            mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        assert mode == "delete"
        assert not path.with_name(path.name + "-wal").exists()
    if step == "cold":
        assert _user_version(Path(str(path) + ".cold")) == engine_cache._CACHE_SCHEMA_VERSION


def _side_names(path: Path) -> list[Path]:
    cold = Path(str(path) + ".cold")
    return [
        path.with_name(path.name + ".v3.bak"),
        path.with_name(path.name + ".v4-new"),
        cold.with_name(cold.name + ".v3.bak"),
        cold.with_name(cold.name + ".v4-new"),
    ]


@pytest.mark.parametrize("step", ["backups", "cold", "hot"])
def test_publish_crash_never_opens_an_empty_cache(tmp_path, monkeypatch, step: str) -> None:
    """Cut inside publish. The whole-function failure test stays beside this one."""

    path = tmp_path / "live.sqlite"
    rows = _schema3_pair(path)

    def crash(name: str) -> None:
        if name == step:
            raise sqlite3.OperationalError(f"injected after {step}")

    monkeypatch.setattr(engine_cache, "_publish_checkpoint", crash)
    with pytest.raises(sqlite3.OperationalError, match=f"injected after {step}"):
        engine_cache.migrate_cache_file(path)
    monkeypatch.setattr(engine_cache, "_publish_checkpoint", lambda _name: None)

    if step == "backups":
        assert os.path.samefile(path, path.with_name(path.name + ".v3.bak"))
        assert path.with_name(path.name + ".v4-new").is_file()
    elif step == "cold":
        assert os.path.samefile(path, path.with_name(path.name + ".v3.bak"))
        assert not os.path.samefile(
            Path(str(path) + ".cold"),
            Path(str(path) + ".cold.v3.bak"),
        )
    else:
        assert not path.with_name(path.name + ".v4-new").exists()
        assert _user_version(path) == engine_cache._CACHE_SCHEMA_VERSION

    _assert_open_keeps_rows(path, rows, step=step)
    report = engine_cache.recover_cache_file(path)
    assert report["hot_rows"] == len(rows)
    assert _hot_pairs(path) == rows
    assert all(not side.exists() for side in _side_names(path))
    if step == "cold":
        assert report["action"] == "restored"
        assert report["schema_version"] == 3
        with sqlite3.connect(Path(str(path) + ".cold")) as connection:
            assert connection.execute("SELECT value FROM engine_values").fetchone()[0] == b"cold-bytes"
    else:
        assert report["schema_version"] == engine_cache._CACHE_SCHEMA_VERSION
        assert _user_version(Path(str(path) + ".cold")) == engine_cache._CACHE_SCHEMA_VERSION
        assert report["action"] == ("published" if step == "backups" else "removed-leftovers")
    again = engine_cache.recover_cache_file(path)
    assert again["action"] == "nothing"
    assert _hot_pairs(path) == rows


def test_open_creates_a_cache_only_without_migration_leftovers(tmp_path) -> None:
    fresh = tmp_path / "fresh.sqlite"
    connection = engine_cache._connect(fresh, readonly=False)
    try:
        assert connection is not None
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 0
    finally:
        engine_cache._close_connections()

    path = tmp_path / "backed.sqlite"
    rows = _schema3_pair(path)
    os.link(path, path.with_name(path.name + ".v3.bak"))
    path.unlink()
    assert not path.exists()
    for readonly in (False, True):
        with pytest.raises(engine_cache.EngineCacheSchemaMismatch, match=_RECOVER_MESSAGE):
            engine_cache._connect(path, readonly=readonly)
        engine_cache._close_connections()
        assert not path.exists()
    restored = engine_cache.recover_cache_file(path)
    assert restored["action"] == "restored"
    assert restored["hot_rows"] == len(rows)
    assert _hot_pairs(path) == rows
    assert _user_version(path) == 3


def test_recover_replaces_an_empty_schema4_file_with_the_backup(tmp_path) -> None:
    """The rename gap used to leave a user_version-4 file with no rows.

    That file is the one ``_connect`` creates. A hand-written two-column
    ``engine_outputs`` has no ``row_seq``, so it is not the cache the
    initializer writes and ``_hot_pairs`` cannot read it.
    """

    path = tmp_path / "emptied.sqlite"
    rows = _schema3_pair(path)
    cold = Path(str(path) + ".cold")
    hot_bak = path.with_name(path.name + ".v3.bak")
    cold_bak = cold.with_name(cold.name + ".v3.bak")
    hot_bak.write_bytes(path.read_bytes())
    cold_bak.write_bytes(cold.read_bytes())
    path.unlink()
    # Build the empty schema-4 file away from the backups. ``_connect``
    # refuses to create a cache while those leftovers sit beside the path.
    fresh = tmp_path / "fresh-empty.sqlite"
    connection = engine_cache._connect(fresh, readonly=False)
    try:
        assert connection is not None
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 0
    finally:
        engine_cache._close_connections()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(fresh) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    fresh.replace(path)
    with sqlite3.connect(cold) as connection:
        connection.execute("PRAGMA user_version=4")
    assert _user_version(path) == 4
    assert _hot_pairs(path) == []
    report = engine_cache.recover_cache_file(path)
    assert report["action"] == "restored"
    assert _hot_pairs(path) == rows
    assert _user_version(path) == 3
    assert all(not side.exists() for side in _side_names(path))
    again = engine_cache.recover_cache_file(path)
    assert again["action"] == "nothing"


def test_migrate_recover_cli_prints_the_action(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cli.sqlite"
    rows = _schema3_pair(path)

    def crash(name: str) -> None:
        if name == "backups":
            raise sqlite3.OperationalError("injected after backups")

    monkeypatch.setattr(engine_cache, "_publish_checkpoint", crash)
    with pytest.raises(sqlite3.OperationalError):
        engine_cache.migrate_cache_file(path)
    monkeypatch.setattr(engine_cache, "_publish_checkpoint", lambda _name: None)

    from scripts.engine_cache_sync import main

    def _run(argv: list[str]) -> tuple[int, dict, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(argv)
        text = stdout.getvalue()
        payload = json.loads(text) if text.strip() else {}
        return code, payload, stderr.getvalue()

    refused, _payload, refused_err = _run(["migrate", str(path)])
    assert refused == 1
    assert _RECOVER_MESSAGE in refused_err
    assert _hot_pairs(path) == rows
    code, payload, stderr = _run(["migrate", str(path), "--recover"])
    assert code == 0
    assert payload["action"] == "published"
    assert stderr.splitlines()[0].startswith("recover: published;")
    assert _hot_pairs(path) == rows
    again_code, again, again_err = _run(["migrate", str(path), "--recover"])
    assert again_code == 0
    assert again["action"] == "nothing"
    assert again_err.splitlines()[0].startswith("recover: nothing;")


def test_recover_of_an_untouched_schema3_pair_does_nothing(tmp_path) -> None:
    path = tmp_path / "untouched.sqlite"
    rows = _schema3_pair(path)
    report = engine_cache.recover_cache_file(path)
    assert report["action"] == "nothing"
    assert report["schema_version"] == 3
    assert _hot_pairs(path) == rows
    with pytest.raises(FileNotFoundError):
        engine_cache.recover_cache_file(tmp_path / "absent.sqlite")


def test_expire_species_feo_t_expires_the_feo_family(tmp_path, monkeypatch) -> None:
    path = tmp_path / "feo-t.sqlite"
    for marker, name in enumerate(("feo", "FeO_total", "Fe2O3"), start=1):
        _store(
            monkeypatch,
            path,
            "openimcc",
            {"composition_wt_pct": {name: 1.0}, "marker": marker},
        )

    from scripts.engine_cache_sync import main

    def _expire(species: str, apply: bool = False) -> tuple[dict, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        argv = ["expire", str(path), "--engine", "openimcc", "--species", species]
        if apply:
            argv.append("--apply")
        with redirect_stdout(stdout), redirect_stderr(stderr):
            assert main(argv) == 0
        assert stderr.getvalue().splitlines()[0] == "species-unknown rows: 0"
        return json.loads(stdout.getvalue()), stderr.getvalue()

    star = _expire("FeO*")[0]
    assert star["dry_run"] is True
    assert star["matched_by_engine"]["openimcc"] == 2
    for spelling in ("feo", "feo_total", "feo_t", "feo*"):
        assert spelling in star["species"]
    assert "fe2o3" not in star["species"]
    applied = _expire("FeO_T", apply=True)[0]
    assert applied["deleted_by_engine"]["openimcc"] == 2
    assert set().union(*_species_by_row(path).values()) == {"fe2o3"}


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
