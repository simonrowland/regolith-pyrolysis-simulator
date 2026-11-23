"""Synchronization contracts use only tmp_path caches, including child writers."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import warnings
import zlib

import pytest

from simulator import engine_cache as cache
from simulator.engine_cache import EngineCacheValue, Provenance, pack, unpack


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    cache._close_connections()
    cache._MEMORY_CACHE.clear()
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    yield
    cache._close_connections()
    cache._MEMORY_CACHE.clear()


def write_box(monkeypatch, path, box, records):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", box)
    with cache._engine_cache_path_scope(path):
        for request, answer in records:
            cache.cached_engine_call("openimcc", {"request": request}, lambda: answer)


def rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT e.key,e.value,e.engine,e.chem_version,e.source_box,e.row_seq,"
            "COALESCE(p.value,X'') FROM engine_outputs e "
            "LEFT JOIN provenance p ON p.id=e.provenance_id ORDER BY e.key"
        ).fetchall()


def cold_rows(path):
    with sqlite3.connect(str(path) + ".cold") as connection:
        return connection.execute("SELECT key,value FROM engine_values ORDER BY key").fetchall()


def invoke(capsys, *args, status=0):
    from scripts.engine_cache_sync import main

    assert main([str(arg) for arg in args]) == status
    return json.loads(capsys.readouterr().out)


def test_merge_is_incremental_idempotent_and_preserves_disjoint_sequences(tmp_path, monkeypatch):
    first, second, master = (tmp_path / name for name in ("s1", "s2", "master"))
    write_box(monkeypatch, first, "s1", [(1, {"answer": 1})])
    write_box(monkeypatch, second, "s2", [(2, {"answer": 2})])
    original = sorted(rows(first) + rows(second))
    result = cache.merge_cache_files(master, [first, second])
    assert result["read"] == result["inserted"] == 2
    assert result["conflicts"] == 0
    assert rows(master) == original
    assert len({row[5] for row in original}) == 2
    for row in original:
        assert row[5] == cache.ENGINE_CACHE_BOX_RANGES[row[4]][0]
    with sqlite3.connect(master) as connection:
        assert dict(connection.execute("SELECT source_box,row_seq FROM sync_watermarks")) == {
            row[4]: row[5] for row in original
        }
    assert cache.merge_cache_files(master, [first, second])["read"] == 0
    write_box(monkeypatch, first, "s1", [(3, {"answer": 3})])
    result = cache.merge_cache_files(master, [first, second])
    assert result["read"] == result["inserted"] == 1
    assert rows(master) == sorted(rows(first) + rows(second))


def test_merge_carries_cold_fingerprint_without_treating_it_as_payload_conflict(
    tmp_path, monkeypatch
):
    first, second, master = (
        tmp_path / name for name in ("first", "second", "master")
    )
    value = EngineCacheValue({"answer": 1}, {"enthalpy": 2.0})
    write_box(monkeypatch, first, "s1", [(1, value)])
    write_box(monkeypatch, second, "s2", [(1, value)])
    with sqlite3.connect(str(first) + ".cold") as connection:
        expected_fingerprint = connection.execute(
            "SELECT input_fingerprint FROM engine_values"
        ).fetchone()[0]
    with sqlite3.connect(str(second) + ".cold") as connection:
        connection.execute(
            "UPDATE engine_values SET input_fingerprint=?", (b"different-metadata",)
        )

    result = cache.merge_cache_files(master, [first, second])

    assert result["conflicts"] == 0
    with sqlite3.connect(str(master) + ".cold") as connection:
        merged_fingerprint = connection.execute(
            "SELECT input_fingerprint FROM engine_values"
        ).fetchone()[0]
    assert merged_fingerprint == expected_fingerprint


def test_conflicts_keep_master_report_both_origins_and_strict_only_exit(tmp_path, monkeypatch, capsys):
    first, second, master, strict_master = (tmp_path / n for n in ("a", "b", "m", "strict"))
    write_box(monkeypatch, first, "s1", [(1, {"answer": 1})])
    write_box(monkeypatch, second, "s2", [(1, {"answer": 2})])
    before = rows(first), rows(second)
    result = invoke(capsys, "merge", master, first, second)
    assert result["conflicts"] == 1
    assert rows(master) == before[0]
    report = [json.loads(line) for line in Path(result["conflict_report"]).read_text().splitlines()]
    assert report == [{
        "key": before[0][0][0].hex(),
        "master": {"source_box": "s1", "row_seq": before[0][0][5]},
        "input": {"source_box": "s2", "row_seq": before[1][0][5]},
    }]
    assert invoke(capsys, "merge", "--strict", strict_master, first, second, status=2)["conflicts"] == 1
    assert cache.merge_cache_files(master, [first, second])["read"] == 0
    assert (rows(first), rows(second)) == before
    seeded = tmp_path / "seeded"
    cache.snapshot_cache(master, seeded)
    write_box(monkeypatch, seeded, "s2", [(2, {"answer": 3})])
    assert next(row[5] for row in rows(seeded) if row[4] == "s2") == before[1][0][5] + 1


def test_cold_provenance_and_notice_values_follow_only_matching_hot(tmp_path, monkeypatch):
    first, second, master = (tmp_path / n for n in ("a", "b", "m"))
    hot = {"answer": 1, "notice_codes": [7], "notice_values": [{"temperature_K": 1700}],
           "text": Provenance("shared notice")}
    write_box(monkeypatch, first, "s1", [(1, EngineCacheValue(hot, {"enthalpy": -123.0})),
                                          (2, EngineCacheValue(hot, {"enthalpy": -124.0}))])
    write_box(monkeypatch, second, "s2", [(1, EngineCacheValue(hot, {"enthalpy": -999.0}))])
    assert cache.merge_cache_files(master, [first, second])["conflicts"] == 1
    assert rows(master) == rows(first)
    assert cold_rows(master) == cold_rows(first)
    with sqlite3.connect(master) as connection:
        assert connection.execute("SELECT count(*) FROM provenance").fetchone()[0] == 1
    # A provenance-only difference also conflicts even with identical HOT bytes.
    third = tmp_path / "c"
    write_box(monkeypatch, third, "s3", [(1, EngineCacheValue({**hot, "text": Provenance("other notice")}))])
    assert cache.merge_cache_files(master, [third])["conflicts"] == 1
    assert rows(master) == rows(first)


def test_snapshot_and_seed_are_new_standalone_pairs_and_seed_can_grow(tmp_path, monkeypatch, capsys):
    live, snapshot, seeded = (tmp_path / n for n in ("live", "snapshot", "seeded"))
    write_box(monkeypatch, live, "s1", [(1, EngineCacheValue({"answer": 1}, {"enthalpy": 2.0}))])
    expected, expected_cold = rows(live), cold_rows(live)
    invoke(capsys, "snapshot", live, snapshot)
    invoke(capsys, "seed", snapshot, seeded)
    assert rows(seeded) == rows(snapshot) == expected
    assert cold_rows(seeded) == cold_rows(snapshot) == expected_cold
    for path in (snapshot, seeded):
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        with pytest.raises(FileExistsError):
            cache.snapshot_cache(live, path)
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    with cache._engine_cache_path_scope(seeded):
        assert cache.cached_engine_call("openimcc", {"request": 1}, lambda: 1 / 0) == {"answer": 1}
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE")
    write_box(monkeypatch, seeded, "s1", [(2, {"answer": 2})])
    assert max(row[5] for row in rows(seeded)) == expected[0][5] + 1
    assert rows(live) == expected


@pytest.mark.parametrize("operation", ["snapshot", "seed", "merge"])
@pytest.mark.parametrize("alias", ["same", "hardlink", "symlink", "cold"])
def test_aliases_refused_without_touching_sources(tmp_path, monkeypatch, operation, alias, capsys):
    source, target = tmp_path / "source", tmp_path / "target"
    write_box(monkeypatch, source, "s1", [(1, EngineCacheValue({"answer": 1}, {"enthalpy": 2.0}))])
    expected, expected_cold = rows(source), cold_rows(source)
    if alias == "same":
        target = source
    elif alias == "hardlink":
        os.link(source, target)
    elif alias == "symlink":
        target.symlink_to(source)
    else:
        os.link(source, Path(str(target) + ".cold"))
    from scripts.engine_cache_sync import main

    args = [operation, str(target), str(source)] if operation == "merge" else [operation, str(source), str(target)]
    assert main(args) == 1
    assert "EngineCacheAliasError" in capsys.readouterr().err
    assert rows(source) == expected
    assert cold_rows(source) == expected_cold


@pytest.mark.parametrize("namespace", [1, 99])
def test_namespace_mismatch_is_typed_and_does_not_create_destination(tmp_path, monkeypatch, namespace):
    source, target = tmp_path / "source", tmp_path / "target"
    write_box(monkeypatch, source, "s1", [(1, {"answer": 1})])
    cache._close_connections()
    with sqlite3.connect(source) as connection:
        connection.execute(f"PRAGMA user_version={namespace}")
    for call in (lambda: cache.snapshot_cache(source, target), lambda: cache.merge_cache_files(target, [source])):
        with pytest.raises(cache.EngineCacheSchemaMismatch):
            call()
        assert not target.exists()


def test_cold_is_written_before_hot_commit(tmp_path, monkeypatch):
    path = tmp_path / "cache"
    original = cache._store_cold

    def store(*args):
        with sqlite3.connect(path) as reader:
            assert reader.execute("SELECT count(*) FROM engine_outputs").fetchone()[0] == 0
        original(*args)

    monkeypatch.setattr(cache, "_store_cold", store)
    write_box(monkeypatch, path, "s1", [(1, EngineCacheValue({"answer": 1}, {"enthalpy": 2.0}))])
    assert len(rows(path)) == len(cold_rows(path)) == 1


def test_failed_cold_write_rolls_back_the_entire_solve(tmp_path, monkeypatch):
    live = tmp_path / "live"
    original = cache._store_cold

    def fail_after_cold_write(*args):
        original(*args)
        raise sqlite3.OperationalError("injected failure after COLD write")

    monkeypatch.setattr(cache, "_store_cold", fail_after_cold_write)
    write_box(monkeypatch, live, "s1", [(1, EngineCacheValue({"answer": 1}, {"enthalpy": 111.0}))])
    assert rows(live) == []
    assert cold_rows(live) == []


@pytest.mark.parametrize("retry_kind", ["cold", "empty_cold", "hot_only"])
def test_failure_after_legacy_cold_commit_retry_keeps_matching_pair(tmp_path, monkeypatch, retry_kind):
    live, snapshot, master = (tmp_path / name for name in ("live", "snapshot", "master"))
    key = cache.engine_cache_key("openimcc", cache.ENGINE_CHEMISTRY_VERSION["openimcc"], {"request": 1})
    cache._connect(live, readonly=False)
    # Reproduce the persisted orphan left by the old independent COLD commit.
    with sqlite3.connect(str(live) + ".cold") as cold:
        cache._initialize_cold(cold)
        cold.execute(
            "INSERT INTO engine_values (key,value,input_fingerprint) VALUES (?,?,NULL)",
            (key, zlib.compress(pack({"enthalpy": 111.0}))),
        )
    original = cache._store_cold

    def fail_after_cold_write(*args):
        original(*args)
        raise sqlite3.OperationalError("injected failure after COLD write, before HOT commit")

    monkeypatch.setattr(cache, "_store_cold", fail_after_cold_write)
    write_box(monkeypatch, live, "s1", [(1, EngineCacheValue({"answer": 1}, {"enthalpy": 111.0}))])
    assert rows(live) == []
    assert unpack(zlib.decompress(cold_rows(live)[0][1])) == {"enthalpy": 111.0}
    cache._close_connections()
    cache._MEMORY_CACHE.clear()
    monkeypatch.setattr(cache, "_store_cold", original)
    answer = {"answer": 2}
    expected_cold = {"enthalpy": 222.0} if retry_kind == "cold" else {}
    retry = answer if retry_kind == "hot_only" else EngineCacheValue(answer, expected_cold)
    write_box(monkeypatch, live, "s1", [(1, retry)])
    cache.snapshot_cache(live, snapshot)
    assert cache.merge_cache_files(master, [snapshot])["conflicts"] == 0
    for path in (live, snapshot, master):
        stored = rows(path)
        assert len(stored) == 1
        assert cache._decode_hot(stored[0][1], stored[0][6]) == answer
        assert {k: unpack(zlib.decompress(v)) for k, v in cold_rows(path)} == (
            {key: expected_cold} if expected_cold else {}
        )


def test_racing_writer_keeps_the_committed_hot_and_cold_pair(tmp_path, monkeypatch):
    live = tmp_path / "live"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "s1")

    def racing_solve():
        write_box(monkeypatch, live, "s1", [(1, EngineCacheValue({"answer": 1}, {"enthalpy": 111.0}))])
        return EngineCacheValue({"answer": 2}, {"enthalpy": 222.0})

    with cache._engine_cache_path_scope(live):
        assert cache.cached_engine_call("openimcc", {"request": 1}, racing_solve) == {"answer": 2}
    stored = rows(live)[0]
    assert cache._decode_hot(stored[1], stored[6]) == {"answer": 1}
    assert unpack(zlib.decompress(cold_rows(live)[0][1])) == {"enthalpy": 111.0}


def test_old_master_namespace_is_refused_even_without_a_hot_table(tmp_path, monkeypatch):
    source, master = tmp_path / "source", tmp_path / "master"
    write_box(monkeypatch, source, "s1", [(1, {"answer": 1})])
    with sqlite3.connect(master) as connection:
        connection.execute("PRAGMA user_version=1")
    with pytest.raises(cache.EngineCacheSchemaMismatch):
        cache.merge_cache_files(master, [source])
    with sqlite3.connect(master) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()


def test_live_subprocess_snapshot_has_all_cold_rows_and_committed_wal(tmp_path, monkeypatch):
    live, snapshot, stop = (tmp_path / n for n in ("live", "snapshot", "stop"))
    code = """
import sys, time
from pathlib import Path
from simulator import engine_cache as c
from simulator.engine_cache import EngineCacheValue
with c._engine_cache_path_scope(Path(sys.argv[1])):
    i = 0
    while not Path(sys.argv[2]).exists():
        c.cached_engine_call('openimcc', {'request': i}, lambda: EngineCacheValue({'answer': i}, {'enthalpy': float(i)}))
        if i == 0:
            print('ready', flush=True)
        i += 1
        time.sleep(0.002)
"""
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "s1")
    writer = subprocess.Popen([sys.executable, "-c", code, str(live), str(stop)], stdout=subprocess.PIPE, text=True)
    try:
        assert writer.stdout.readline().strip() == "ready"
        assert Path(str(live) + "-wal").stat().st_size > 0
        cache.snapshot_cache(live, snapshot)
        hot_keys = {row[0] for row in rows(snapshot)}
        assert hot_keys
        assert {key for key, _ in cold_rows(snapshot)} == hot_keys
    finally:
        stop.touch()
        writer.wait(timeout=30)
    assert writer.returncode == 0


def test_space_report_and_vacuum_is_explicit(tmp_path, monkeypatch):
    source, master = tmp_path / "source", tmp_path / "master"
    write_box(monkeypatch, source, "s1", [(i, EngineCacheValue({"answer": i}, {"enthalpy": float(i)})) for i in range(50)])
    result = cache.merge_cache_files(master, [source])
    for name in ("hot", "cold"):
        assert result[name]["rows"] == 50
        assert result[name]["bytes_per_row"] == result[name]["database_bytes"] / 50
    warnings.warn("sync-space " + json.dumps({k: result[k] for k in ("hot", "cold")}), stacklevel=1)
    assert result["vacuumed"] is False
    assert cache.merge_cache_files(master, [source], vacuum=True)["vacuumed"] is True
