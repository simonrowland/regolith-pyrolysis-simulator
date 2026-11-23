import sqlite3

import pytest

from simulator import engine_cache

pytestmark = pytest.mark.engine_cache_off


def test_readwrite_refuses_an_empty_box_before_creating_a_store(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_BOX", raising=False)
    with pytest.raises(ValueError, match="REGOLITH_ENGINE_CACHE_BOX"):
        engine_cache.cached_engine_call("openimcc", {}, lambda: {"answer": 1})
    assert not path.exists()


def test_per_box_sequence_ranges_persist_without_max_scans(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    assert set(engine_cache.ENGINE_CACHE_BOX_RANGES) == {"laptop", "s1", "s2", "s3", "rpp"}
    queries = []
    for box, (first, stop) in engine_cache.ENGINE_CACHE_BOX_RANGES.items():
        monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", box)
        connection = engine_cache._connect(path, readonly=False)
        connection.set_trace_callback(queries.append)
        for request in (1, 2):
            engine_cache.cached_engine_call("openimcc", {"box": box, "request": request}, lambda: {"answer": 1})
            engine_cache._close_connections()
        with sqlite3.connect(path) as connection:
            values = connection.execute("SELECT row_seq FROM engine_outputs WHERE source_box=? ORDER BY row_seq", (box,)).fetchall()
        assert values == [(first,), (first + 1,)]
        assert first + 1 < stop
    assert not any("MAX(" in query.upper() for query in queries)
    with sqlite3.connect(path) as connection:
        indexes = connection.execute("SELECT sql FROM sqlite_master WHERE type='index'").fetchall()
    assert any("source_box, row_seq" in (row[0] or "") for row in indexes)


def test_exhausted_box_range_refuses_without_reusing_a_sequence(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    first, stop = engine_cache.ENGINE_CACHE_BOX_RANGES["laptop"]
    connection = engine_cache._connect(path, readonly=False)
    connection.execute("INSERT INTO box_sequences VALUES (?,?)", ("laptop", stop))
    connection.commit()
    with pytest.raises(ValueError, match="exhausted"):
        engine_cache._next_row_seq(connection, "laptop")
    assert first < stop
