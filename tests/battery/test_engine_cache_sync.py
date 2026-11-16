from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from simulator import engine_cache


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    yield
    engine_cache._close_connections()
    engine_cache._MEMORY_CACHE.clear()


def write_box(monkeypatch, path, box, records):
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", box)
    with engine_cache._engine_cache_path_scope(path):
        for engine, request, result in records:
            engine_cache.cached_engine_call(engine, request, lambda: result)


def rows(path):
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT key, value, engine, chem_version, source_box, row_seq "
            "FROM engine_outputs ORDER BY key"
        ).fetchall()


def invoke(capsys, *args):
    from scripts.engine_cache_sync import main

    assert main([str(arg) for arg in args]) == 0
    return json.loads(capsys.readouterr().out)


def test_merge_conflict_and_idempotence(tmp_path, monkeypatch, capsys):
    first, second, canonical = (tmp_path / name for name in ("a", "b", "master"))
    write_box(monkeypatch, first, "s1", [
        ("openimcc", {"T": 1000}, {"activity": 0.25}),
        ("openimcc", {"T": 1100}, {"activity": 0.5}),
        ("magemin", {"T": 1000}, {"liquid_fraction": 0.75}),
    ])
    first_rows = rows(first)
    write_box(monkeypatch, second, "s2", [
        ("openimcc", {"T": 1000}, {"activity": 0.25}),
        ("openimcc", {"T": 1100}, {"activity": 0.9}),
        ("magemin", {"T": 1200}, {"liquid_fraction": 1.0}),
    ])
    second_rows = rows(second)
    report = invoke(capsys, "merge", "--into", canonical, first, second)
    assert report["by_engine"] == {
        "openimcc": {"read": 4, "inserted": 2, "already_present": 1, "conflicts": 1},
        "magemin": {"read": 2, "inserted": 2, "already_present": 0, "conflicts": 0},
    }
    assert report["by_source_box"] == {
        "s1": {"read": 3, "inserted": 3, "already_present": 0, "conflicts": 0},
        "s2": {"read": 3, "inserted": 1, "already_present": 1, "conflicts": 1},
    }
    key = engine_cache.engine_cache_key("openimcc", 1, {"T": 1100})
    kept = next(row for row in first_rows if row[0] == key)
    incoming = next(row for row in second_rows if row[0] == key)
    assert next(row for row in rows(canonical) if row[0] == key) == kept
    conflicts = [json.loads(line) for line in Path(report["conflict_report"]).read_text().splitlines()]
    assert conflicts == [{
        "key": key.hex(), "engine": "openimcc", "chem_version": "1",
        "source_boxes": ["s1", "s2"],
        "value_sha256": [hashlib.sha256(kept[1]).hexdigest(), hashlib.sha256(incoming[1]).hexdigest()],
    }]
    before = rows(canonical)
    again = invoke(capsys, "merge", "--into", canonical, first, second)
    assert rows(canonical) == before
    assert all(counts["inserted"] == 0 for counts in again["by_engine"].values())
    assert again["by_engine"]["openimcc"]["conflicts"] == 1
    assert rows(first) == first_rows
    assert rows(second) == second_rows


def test_snapshot_reads_committed_wal_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    live, snapshot = tmp_path / "live", tmp_path / "snapshot"
    write_box(monkeypatch, live, "s1", [("openimcc", {"T": 1000}, {"x": 0.5})])
    connection = engine_cache._connect(live, readonly=False)
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    connection.execute("PRAGMA wal_autocheckpoint=0")
    write_box(monkeypatch, live, "s1", [("magemin", {"T": 1200}, {"x": 0.75})])
    committed = rows(live)
    assert Path(str(live) + "-wal").stat().st_size > 0
    connection.execute("BEGIN IMMEDIATE")
    engine_cache._insert_cache_row_if_absent(
        connection, engine_cache.engine_cache_key("openimcc", 1, {"T": 999}),
        committed[0][1], "openimcc", "1", "s1",
    )
    try:
        report = invoke(capsys, "snapshot", live, snapshot)
    finally:
        connection.rollback()
    assert rows(snapshot) == committed
    with sqlite3.connect(snapshot) as copied:
        assert copied.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert report["by_engine"] == {"magemin": 1, "openimcc": 1}
    assert report["sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    before = snapshot.read_bytes()
    from scripts.engine_cache_sync import main

    assert main(["snapshot", str(live), str(snapshot)]) != 0
    assert snapshot.read_bytes() == before


def test_seed_preserves_local_value_and_adds_union(tmp_path, monkeypatch, capsys):
    canonical, live = tmp_path / "canonical", tmp_path / "live"
    write_box(monkeypatch, canonical, "s3", [
        ("openimcc", {"T": 1000}, {"x": 0.25}),
        ("magemin", {"T": 1200}, {"x": 0.75}),
    ])
    canonical_rows = rows(canonical)
    write_box(monkeypatch, live, "local", [
        ("openimcc", {"T": 1000}, {"x": 0.9}),
        ("openimcc", {"T": 1500}, {"x": 1.0}),
    ])
    local_rows = rows(live)
    inode = live.stat().st_ino
    writer = sqlite3.connect(live)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.commit()
        invoke(capsys, "seed", "--from", canonical, "--into", live)
        assert writer.execute("SELECT COUNT(*) FROM engine_outputs").fetchone()[0] == 3
    finally:
        writer.close()
    assert live.stat().st_ino == inode
    assert all(row in rows(live) for row in local_rows)
    assert next(row for row in canonical_rows if row[2] == "magemin") in rows(live)
    before = rows(live)
    invoke(capsys, "seed", "--from", canonical, "--into", live)
    assert rows(live) == before


def test_seed_waits_for_live_writer(tmp_path, monkeypatch, capsys):
    canonical, live = tmp_path / "canonical", tmp_path / "live"
    write_box(monkeypatch, canonical, "s3", [("openimcc", {"T": 1000}, {"x": 0.25})])
    write_box(monkeypatch, live, "local", [("magemin", {"T": 1200}, {"x": 0.75})])
    engine_cache._close_connections()
    locked = threading.Event()
    release = threading.Event()

    def writer():
        with sqlite3.connect(live) as connection:
            connection.execute("BEGIN IMMEDIATE")
            locked.set()
            release.wait(5)
            connection.commit()

    thread = threading.Thread(target=writer)
    thread.start()
    assert locked.wait(5)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        invoke(capsys, "seed", "--from", canonical, "--into", live)
    finally:
        release.set()
        thread.join(5)
        timer.join(5)
    assert len(rows(live)) == 2


@pytest.mark.parametrize("existing", [False, True])
def test_corrupt_input_refused_before_any_write(tmp_path, monkeypatch, capsys, existing):
    good, corrupt, canonical = (tmp_path / name for name in ("good", "corrupt", "master"))
    write_box(monkeypatch, good, "s1", [("openimcc", {"T": 1000}, {"x": 0.25})])
    corrupt.write_bytes(b"not a SQLite database")
    if existing:
        write_box(monkeypatch, canonical, "s3", [("magemin", {"T": 1500}, {"x": 0.75})])
    engine_cache._close_connections()
    before = canonical.read_bytes() if existing else None
    from scripts.engine_cache_sync import main

    assert main(["merge", "--into", str(canonical), str(good), str(corrupt)]) != 0
    if existing:
        assert canonical.read_bytes() == before
    else:
        assert not canonical.exists()
    assert not Path(str(canonical) + ".conflicts.jsonl").exists()
    assert main(["seed", "--from", str(corrupt), "--into", str(canonical)]) != 0
    assert main(["snapshot", str(corrupt), str(tmp_path / "out")]) != 0
    assert not (tmp_path / "out").exists()


def test_stats_groups_by_engine_version_and_box(tmp_path, monkeypatch, capsys):
    database = tmp_path / "cache"
    write_box(monkeypatch, database, "s1", [("openimcc", {"T": 1000}, {"x": 0.25})])
    version = engine_cache.ENGINE_CHEMISTRY_VERSION["magemin"]
    key = engine_cache.engine_cache_key("magemin", version, {"T": 1200})
    engine_cache.load_cache_rows(database, [(key, {"x": 0.75}, "magemin", version, "s2", 10)])
    report = invoke(capsys, "stats", database)
    assert report["rows"] == 2
    assert report["by_engine"] == {"openimcc": 1, "magemin": 1}
    assert report["groups"] == [
        {"engine": "magemin", "chem_version": str(version), "source_box": "s2", "rows": 1},
        {"engine": "openimcc", "chem_version": "1", "source_box": "s1", "rows": 1},
    ]
    assert report["file_bytes"] == database.stat().st_size
    assert report["bytes_per_row"] == report["file_bytes"] / 2
