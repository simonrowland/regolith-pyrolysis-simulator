"""Lean binary HOT/COLD records preserve consumer values exactly."""

import sqlite3
import zlib

import pytest

from simulator import engine_cache
from simulator.engine_cache import unpack
from simulator.melt_backend.base import EquilibriumResult

pytestmark = pytest.mark.engine_cache_off


def test_equilibrium_hot_round_trip_is_exact_and_cold_is_numeric(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    number = float.fromhex("0x1.0000000000001p-3")
    result = EquilibriumResult(
        liquid_fraction=0.25,
        activity_coefficients={"FeO": number},
        system_enthalpy=-1234.5,
        diagnostics={"unused": "console noise" * 1000},
    )
    payload = engine_cache.cached_engine_call("magemin", {}, lambda: result.to_hot(engine_id="magemin"))
    engine_cache._MEMORY_CACHE.clear()
    warm = engine_cache.cached_engine_call("magemin", {}, lambda: 1 / 0)
    for value in (payload, warm):
        restored = EquilibriumResult.from_hot(value, engine_id="magemin")
        assert restored.activity_coefficients["FeO"].hex() == number.hex()
    with sqlite3.connect(path) as connection:
        hot, row_seq = connection.execute(
            "SELECT value,row_seq FROM engine_outputs"
        ).fetchone()
        assert len(hot) < 200
        assert b"console noise" not in hot
    with sqlite3.connect(str(path) + ".cold") as connection:
        cold = connection.execute(
            "SELECT value FROM engine_values WHERE row_seq=?", (row_seq,)
        ).fetchone()[0]
    assert unpack(zlib.decompress(cold)) == {"system_enthalpy": -1234.5}
    assert len(cold) <= 300


def test_long_notices_are_interned_once_and_not_in_hot(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    result = EquilibriumResult(liquid_fraction=1.0, warnings=["same notice " * 100])
    for temperature in (1700.0, 1800.0):
        engine_cache.cached_engine_call("magemin", {"temperature_K": temperature}, lambda: result.to_hot(engine_id="magemin"))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM provenance").fetchone()[0] == 1
        values = connection.execute("SELECT value,provenance_id FROM engine_outputs").fetchall()
    assert values[0] == values[1]
    assert b"same notice" not in values[0][0]
    assert len(values[0][0]) < 200


def test_unknown_shape_solves_without_a_fallback_payload(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    engine_cache.engine_cache_counters(reset=True)
    result = object()
    for _ in range(2):
        assert engine_cache.cached_engine_call("magemin", {}, lambda: result) is result
    counts = engine_cache.engine_cache_counters()["magemin"]
    assert counts["uncacheable:object"] == 2
    assert counts["stores"] == counts["hits"] == 0


def test_binary_value_encoding_is_canonical_and_exact():
    from simulator.engine_cache import pack

    assert pack({"b": 2.0, "a": -0.0}) == pack({"a": -0.0, "b": 2.0})
    assert unpack(pack({"a": -0.0}))["a"].hex() == "-0x0.0p+0"
