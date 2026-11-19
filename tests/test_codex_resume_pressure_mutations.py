"""Temporary mutation probes for the MAGEMin pressure rules."""

from dataclasses import replace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


def _load_magemin_backend_tests():
    test_path = Path(__file__).with_name("test_magemin_backend.py")
    spec = spec_from_file_location("resume_magemin_backend_tests", test_path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_widened_pressure_window_mutant_is_killed(monkeypatch):
    from engines.magemin import domain

    backend_tests = _load_magemin_backend_tests()
    monkeypatch.setattr(
        domain,
        "MAGEMIN_NATIVE_ACTIVITY_PRESSURE_BAR_WINDOW",
        (1.0e-6, 2.0),
    )

    with pytest.raises(AssertionError):
        backend_tests.test_magemin_native_activity_pressure_outside_window_is_typed(
            2.0
        )


def test_dropped_score_pressure_mutant_is_killed(monkeypatch):
    from simulator.battery.generators import bench as bench_module
    from tests.battery import test_melt_activity_requirements as activity_tests

    original = bench_module.activity_request_for_engine

    def without_pressure(*args, **kwargs):
        generated = original(*args, **kwargs)
        if generated is None or generated.payload is None:
            return generated
        payload = {
            key: value
            for key, value in generated.payload.items()
            if key != "pressure_bar"
        }
        return replace(generated, payload=payload)

    monkeypatch.setattr(
        bench_module, "activity_request_for_engine", without_pressure
    )
    with pytest.raises(AssertionError):
        activity_tests.test_magemin_activity_score_passes_printed_row_pressure(
            monkeypatch
        )


def test_wrong_missing_pressure_notice_kind_mutant_is_killed(monkeypatch):
    from simulator.battery import score as score_module
    from simulator.battery.enums import NoticeKind
    from tests.battery import test_melt_activity_requirements as activity_tests

    original = score_module.Notice

    def wrong_missing_pressure_notice_kind(**kwargs):
        if (
            kwargs.get("kind") is NoticeKind.ENGINE_MODEL_CAVEAT
            and "row printed no total pressure" in str(kwargs.get("reason"))
        ):
            kwargs["kind"] = NoticeKind.INPUT_OMITTED
        return original(**kwargs)

    monkeypatch.setattr(
        score_module, "Notice", wrong_missing_pressure_notice_kind
    )
    with pytest.raises(StopIteration):
        activity_tests.test_magemin_activity_without_row_pressure_uses_default_and_flags_it(
            monkeypatch
        )
