"""Contract tests for the t-1917 refusal probe. They do not call an engine."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from simulator.accounting.ledger_api import wt_pct_from_kg
from simulator.engine_cache import (
    EngineCacheValue,
    engine_cache_counters,
    live_hot,
    not_stored_refusal_label,
)
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET
import simulator.engine_cache as engine_cache


def _probe():
    path = Path(__file__).resolve().parent / "t1917_refusal_probe.py"
    spec = importlib.util.spec_from_file_location(
        "t1917_refusal_probe_under_test", path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_refusal_label_matches_the_live_counter(tmp_path, monkeypatch):
    path = tmp_path / "cache.sqlite"
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(path))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE_MODE", raising=False)
    engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    crash = {"status": "refusal", "refusal_reason": "engine_crash"}
    engine_cache.cached_engine_call(
        "alphamelts", {"case": "crash"}, lambda: crash,
    )
    untyped = {"status": "refusal"}
    engine_cache.cached_engine_call(
        "alphamelts", {"case": "untyped"}, lambda: untyped,
    )

    counts = engine_cache_counters()["alphamelts"]
    assert counts["not_stored:refusal:engine_crash"] == 1
    assert counts["not_stored:refusal:untyped"] == 1
    assert not_stored_refusal_label(crash, crash) == "engine_crash"
    assert not_stored_refusal_label(untyped, untyped) == "untyped"


def test_readonly_miss_records_the_request_and_the_writable_label(tmp_path, monkeypatch):
    probe = _probe()
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()
    found: dict = {}
    seen: dict = {}
    details: dict = {}
    dropped: dict = {}
    request = {"kwargs": {"temperature_C": 1400.0}}
    probe.observe_cached_call(
        "alphamelts",
        request,
        lambda: {"status": "refusal", "refusal_reason": "engine_crash"},
        found,
        seen,
        details,
        dropped,
    )
    assert seen["alphamelts"]["not_stored:readonly"] == 1
    assert seen["alphamelts"].get("not_stored:refusal:engine_crash", 0) == 0
    assert len(found["alphamelts"]["refusal:engine_crash"]) == 1
    assert probe.coverage_error(
        {"alphamelts": {"not_stored:readonly": 1}}, seen,
    ) is None
    assert "no request was recorded" in probe.coverage_error(
        {"alphamelts": {"not_stored:refusal:engine_crash": 2}}, {},
    )


def test_exception_miss_uses_the_counter_label(tmp_path, monkeypatch):
    probe = _probe()
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE", str(tmp_path / "cache.sqlite"))
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_BOX", "laptop")
    monkeypatch.setenv("REGOLITH_ENGINE_CACHE_MODE", "readonly")
    engine_cache_counters(reset=True)
    engine_cache._MEMORY_CACHE.clear()

    class Boom(Exception):
        code = "internal_analytical_oxygen_balance_unavailable"

    found: dict = {}
    seen: dict = {}
    with pytest.raises(Boom):
        probe.observe_cached_call(
            "alphamelts",
            {"kwargs": {"temperature_C": 1200.0}},
            lambda: (_ for _ in ()).throw(Boom("oxygen balance failed")),
            found,
            seen,
            {},
            {},
        )
    assert seen["alphamelts"][
        "not_stored:exception:internal_analytical_oxygen_balance_unavailable"
    ] == 1
    assert len(found["alphamelts"][
        "exception:internal_analytical_oxygen_balance_unavailable"
    ]) == 1


def test_cacheable_false_is_not_labeled_as_a_refusal():
    probe = _probe()
    value = EngineCacheValue(
        {"status": "refusal", "refusal_reason": "engine_crash"},
        cacheable=False,
        refusal_reason="engine_crash",
    )
    assert probe.detail_for(value, live_hot(value), None) == "cacheable_false"
    assert not_stored_refusal_label(value, live_hot(value)) is None


def test_missing_cache_is_a_loud_error(monkeypatch):
    probe = _probe()
    monkeypatch.delenv("REGOLITH_ENGINE_CACHE", raising=False)
    with pytest.raises(SystemExit, match="T1917_PROBE_NO_CACHE"):
        probe.configured_cache_path()


def test_kilogram_features_use_the_ledger_weight_percent():
    probe = _probe()
    composition = {"SiO2": 0.3, "MgO": 0.7}
    features = probe.request_features({
        "kwargs": {
            "composition_kg": composition,
            "temperature_C": 1400.0,
            "pressure_bar": 1.0e-6,
            "fO2_log": -12.0,
        },
    })
    assert features["wt_pct"] == wt_pct_from_kg(composition)
    assert features["sio2_wt_pct"] == wt_pct_from_kg(composition)["SiO2"]
    assert features["positive_components"] == ["MgO", "SiO2"]
    assert features["temperature_K"] == 1400.0 + CELSIUS_TO_KELVIN_OFFSET


def test_zero_silica_weight_percent_stays_zero():
    probe = _probe()
    features = probe.request_features({
        "composition_wt_pct": {"SiO2": 0.0, "MgO": 60.0, "Al2O3": 40.0},
        "temperature_K": 1800.0,
    })
    assert features["sio2_wt_pct"] == 0.0
    assert features["zero_components"] == ["SiO2"]
    assert "SiO2" not in features["positive_components"]


def test_replay_kwargs_restore_timeout_and_po2_payload():
    probe = _probe()
    kwargs = probe.replay_kwargs({
        "request": {
            "kwargs": {
                "temperature_C": 1400.0,
                "po2_request": {"mode": "commanded", "po2_bar": 1.0e-8},
            },
        },
        "call_timeout_s": 15.0,
    })
    assert kwargs["call_timeout_s"] == 15.0
    assert kwargs["po2_request"].mode == "commanded"
    assert kwargs["po2_request"].po2_bar == 1.0e-8


def test_outcome_identity_ignores_wall_time():
    probe = _probe()
    left = {"cache_kind": "refusal", "cache_token": "engine_crash", "wall_s": 1.0}
    right = {"cache_kind": "refusal", "cache_token": "engine_crash", "wall_s": 30.0}
    assert probe.outcome_identity(left) == probe.outcome_identity(right)
    right["cache_token"] = "unavailable"
    assert probe.outcome_identity(left) != probe.outcome_identity(right)


def test_scripts_are_not_the_probe_entry():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "scripts" / "t1917_refusal_probe.py").exists()
    assert list((root / "scripts").glob("probe_t1917_*.py")) == []
