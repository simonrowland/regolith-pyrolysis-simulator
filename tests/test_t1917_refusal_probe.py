"""Contract tests for the t-1917 refusal probes. They do not call an engine."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from simulator.accounting.ledger_api import wt_pct_from_kg
from simulator.engine_cache import _not_stored_exception_label
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


def _probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "t1917_refusal_probe.py"
    spec = importlib.util.spec_from_file_location("t1917_refusal_probe_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_measured_classes_match_the_pass2_counters():
    probe = _probe()
    root = Path(__file__).resolve().parents[1]
    assert set(probe.SCRIPT_CLASSES) == {
        "probe_t1917_alphamelts_engine_crash.py",
        "probe_t1917_magemin_engine_worker_timeout.py",
        "probe_t1917_magemin_engine_not_converged.py",
        "probe_t1917_magemin_not_converged.py",
        "probe_t1917_thermoengine_nonfinite.py",
        "probe_t1917_thermoengine_out_of_domain.py",
        "probe_t1917_internal_analytical_oxygen_balance.py",
    }
    assert set(probe.SCRIPT_CLASSES.values()) == {
        ("alphamelts", "refusal", "engine_crash"),
        ("magemin", "exception", "EngineWorkerTimeout"),
        ("magemin", "refusal", "engine_not_converged"),
        ("magemin", "refusal", "not_converged"),
        ("thermoengine", "exception", "ThermoEngineNonFiniteField"),
        ("thermoengine", "exception", "ThermoEngineOutOfDomainError"),
        (
            "internal-analytical",
            "exception",
            "internal_analytical_oxygen_balance_unavailable",
        ),
    }
    for name in probe.SCRIPT_CLASSES:
        assert (root / "scripts" / name).is_file()


def test_exception_label_delegates_to_the_cache_counter():
    probe = _probe()

    class Boom(Exception):
        code = "internal_analytical_oxygen_balance_unavailable"

    exc = Boom("oxygen balance failed")
    assert probe.cache_classification("internal-analytical", None, exc) == (
        "exception",
        _not_stored_exception_label(exc),
    )


def test_mapping_refusal_uses_the_cache_reason_token():
    probe = _probe()
    assert probe.cache_classification(
        "alphamelts",
        {"status": "refusal", "refusal_reason": "engine_crash"},
        None,
    ) == ("refusal", "engine_crash")


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
    right["cache_token"] = "subprocess_died"
    assert probe.outcome_identity(left) != probe.outcome_identity(right)
