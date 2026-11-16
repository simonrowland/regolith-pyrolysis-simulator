"""Storage contracts independent of the engine implementations."""

from pathlib import Path
import sqlite3

import pytest

from simulator import engine_cache


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
    assert key[:4] == bytes((1, 0, 1, 255))
    assert key != engine_cache.engine_cache_key("openimcc", 1, {**request, "temperature_K": 1700.0})


def test_key_namespace_is_explicit_and_store_is_without_rowid(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.cached_engine_call("openimcc", {"temperature_K": 1700.0}, lambda: {"answer": 1.0})
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='engine_outputs'").fetchone()[0]
        assert "WITHOUT ROWID" in schema


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_key_refuses_nonfinite_inputs(value: float) -> None:
    with pytest.raises(ValueError):
        engine_cache.engine_cache_key("openimcc", 1, {"temperature_K": value})
