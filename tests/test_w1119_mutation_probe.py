"""Temporary Studio mutation probes for w1119eAf P1 regressions."""

from dataclasses import replace

import pytest


def test_native_feot_coordinate_regression_kills_source_label_mutant(
    request, monkeypatch
):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    from engines.magemin import domain

    test_path = Path(__file__).with_name("test_magemin_backend.py")
    spec = spec_from_file_location("w1119_backend_test_module", test_path)
    assert spec is not None and spec.loader is not None
    backend_tests = module_from_spec(spec)
    spec.loader.exec_module(backend_tests)

    mutant = dict(domain.MAGEMIN_NATIVE_MELT_ACTIVITIES)
    field, polymorph, _component = mutant["FeO"]
    mutant["FeO"] = (field, polymorph, "FeO")
    monkeypatch.setattr(domain, "MAGEMIN_NATIVE_MELT_ACTIVITIES", mutant)

    with pytest.raises(AssertionError):
        backend_tests.test_magemin_live_native_feo_activity_uses_projected_feot(
            request, "Fe2O3"
        )


def test_score_pressure_route_regression_kills_dropped_pressure_mutant(
    monkeypatch,
):
    from simulator.battery.generators import bench as bench_module
    from tests.battery import (
        test_melt_activity_requirements as activity_tests,
    )

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
