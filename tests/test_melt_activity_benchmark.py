"""Fast smoke tests for the tracked melt-activity benchmark harness."""

from __future__ import annotations

from types import SimpleNamespace

import json
import math

import pytest
import yaml

from benchmarks import melt_activity_benchmark as benchmark
from simulator.regeneration_guard import (
    PlannedArtifactNotWrittenError,
    RegenerationShrinkageError,
    RetiredArtifactWarning,
)


class _FakeActivityEngine:
    name = "fake"

    def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
        del composition_wt_pct, temperature_K, fO2_bar
        return benchmark.EngineResult(
            status="ok",
            activities={"MgO": 0.02, "SiO2": 0.2, "K2O": 0.01},
            gammas={"MgO": 0.1, "SiO2": 0.2, "K2O": 0.1},
        )

    def coverage(self, composition_wt_pct, temperature_K):
        del composition_wt_pct, temperature_K
        return benchmark.EngineResult(
            status="ok",
            details={"observable_family": "activity"},
        )


def test_harness_runs_end_to_end_on_tiny_fixture(tmp_path, monkeypatch):
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["points"] = fixture["points"][:1]
    fixture["composition_probes"] = fixture["composition_probes"][:1]
    fixture_path = tmp_path / "tiny.yaml"
    fixture_path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        benchmark,
        "build_engines",
        lambda names, fixture, alphamelts_timeout_s: [_FakeActivityEngine()],
    )

    output_dir = tmp_path / "out"
    result = benchmark.run_benchmark(
        bench_set_path=fixture_path,
        output_dir=output_dir,
        engine_names=("fake",),
        coverage_steps=3,
    )

    assert result["point_rows"][0]["status"] == "ok"
    assert result["point_rows"][0]["residual_dex"] is not None
    assert (output_dir / "bench-set.yaml").is_file()
    assert (output_dir / "benchmark-results.csv").is_file()
    assert (output_dir / "coverage-map.csv").is_file()
    assert (output_dir / "paired-decisions.csv").is_file()
    assert (output_dir / "reference-anchor-results.csv").is_file()
    assert (output_dir / "live-vaporock-check.csv").is_file()
    assert result["metadata"]["reference_anchor"]["shared_magma_count"] == 288
    assert "Literal SF04 basalt empirical points: **0**" in (
        output_dir / "report.md"
    ).read_text(encoding="utf-8")
    assert "AlphaMELTS was not selected" in (
        output_dir / "report.md"
    ).read_text(encoding="utf-8")


def _run_tiny_full_benchmark(output_dir, monkeypatch, **overrides):
    monkeypatch.setattr(
        benchmark,
        "build_engines",
        lambda names, fixture, alphamelts_timeout_s: [_FakeActivityEngine()],
    )
    kwargs = dict(
        output_dir=output_dir,
        engine_names=("fake",),
        coverage_steps=3,
    )
    kwargs.update(overrides)
    return benchmark.run_benchmark(**kwargs)


def test_regeneration_without_live_check_refuses_to_drop_anchor_csv(
    tmp_path, monkeypatch
):
    """b-200: skipping the live anchor check must not silently delete its CSV."""
    output_dir = tmp_path / "out"
    _run_tiny_full_benchmark(output_dir, monkeypatch)
    assert (output_dir / "live-vaporock-check.csv").is_file()

    with pytest.raises(RegenerationShrinkageError) as excinfo:
        _run_tiny_full_benchmark(
            output_dir, monkeypatch, live_vaporock_anchor_check=False
        )

    assert "live-vaporock-check.csv" in str(excinfo.value)
    assert "benchmark-results.csv" not in str(excinfo.value)
    assert (output_dir / "live-vaporock-check.csv").is_file()


def test_retire_artifact_opt_out_removes_the_anchor_csv_loudly(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "out"
    _run_tiny_full_benchmark(output_dir, monkeypatch)

    with pytest.warns(RetiredArtifactWarning, match="live-vaporock-check.csv"):
        result = _run_tiny_full_benchmark(
            output_dir,
            monkeypatch,
            live_vaporock_anchor_check=False,
            retired_artifacts=("live-vaporock-check.csv",),
        )

    assert not (output_dir / "live-vaporock-check.csv").exists()
    assert (output_dir / "benchmark-results.csv").is_file()
    guard_metadata = result["metadata"]["artifact_guard"]
    assert guard_metadata["retired"] == ["live-vaporock-check.csv"]
    assert guard_metadata["retired_removed"] == ["live-vaporock-check.csv"]
    assert result["metadata"]["live_vaporock_anchor_check"]["requested"] is False
    on_disk = json.loads(
        (output_dir / "run-metadata.json").read_text(encoding="utf-8")
    )
    assert on_disk["artifact_guard"]["retired"] == ["live-vaporock-check.csv"]


def test_mode_scoped_run_refuses_to_drop_unregenerated_artifacts(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "out"
    _run_tiny_full_benchmark(output_dir, monkeypatch)

    dropped = (
        "benchmark-results.csv",
        "composition-probes.csv",
        "live-vaporock-check.csv",
        "paired-decisions.csv",
        "reference-anchor-results.csv",
        "report.md",
    )
    with pytest.raises(RegenerationShrinkageError) as excinfo:
        _run_tiny_full_benchmark(output_dir, monkeypatch, mode="coverage")
    for name in dropped:
        assert name in str(excinfo.value)

    with pytest.warns(RetiredArtifactWarning):
        _run_tiny_full_benchmark(
            output_dir, monkeypatch, mode="coverage", retired_artifacts=dropped
        )

    assert (output_dir / "coverage-map.csv").is_file()
    for name in dropped:
        assert not (output_dir / name).exists()


def test_live_vaporock_anchor_check_defaults_on():
    args = benchmark.build_arg_parser().parse_args([])

    assert args.live_vaporock_anchor_check is True
    assert "live-vaporock-check.csv" in benchmark._planned_artifact_names(
        "all", True
    )
    assert "live-vaporock-check.csv" not in benchmark._planned_artifact_names(
        "all", False
    )
    assert benchmark._planned_artifact_names(
        "benchmark", False
    ) | {"live-vaporock-check.csv"} == benchmark._planned_artifact_names(
        "benchmark", True
    )


def test_run_benchmark_refuses_when_planned_live_vaporock_csv_is_not_written(
    tmp_path, monkeypatch
):
    """Pin the run_benchmark post-check (the :1862 call-site hunk).

    Reverting only verify_planned_artifacts_written(...) in run_benchmark
    reopens the planned-but-unwritten hole while every guard-module test
    stays green. This test goes red on that revert.
    """
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["points"] = fixture["points"][:1]
    fixture["composition_probes"] = fixture["composition_probes"][:1]
    fixture_path = tmp_path / "tiny.yaml"
    fixture_path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        benchmark,
        "build_engines",
        lambda names, fixture, alphamelts_timeout_s: [_FakeActivityEngine()],
    )
    monkeypatch.setattr(
        benchmark, "run_live_vaporock_anchor_check", lambda *a, **k: []
    )
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "live-vaporock-check.csv").write_text("previous run\n")

    with pytest.raises(PlannedArtifactNotWrittenError) as excinfo:
        benchmark.run_benchmark(
            bench_set_path=fixture_path,
            output_dir=output_dir,
            engine_names=("fake",),
            coverage_steps=3,
        )

    assert "live-vaporock-check.csv" in str(excinfo.value)
    assert not (output_dir / "live-vaporock-check.csv").is_file()


def test_cross_engine_shared_count_requires_both_engine_families_to_score():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    rows = [
        {
            "point_id": "p1",
            "species": "Mg",
            "observable": "activity_coefficient",
            "engine": "alphamelts",
            "status": "ok",
            "residual_dex": 0.1,
        },
        {
            "point_id": "p1",
            "species": "Mg",
            "observable": "activity_coefficient",
            "engine": "imcc-published",
            "status": "refused",
            "residual_dex": None,
        },
    ]

    report = benchmark.generate_report(fixture, rows, [], [])

    assert "empirical verdict: **none**" in report
    assert "share 1 scored" not in report


def test_build_engines_includes_intrinsic_thermoengine_leg():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)

    engines = benchmark.build_engines(
        ("thermoengine",), fixture, alphamelts_timeout_s=17.0
    )

    assert len(engines) == 1
    assert isinstance(engines[0], benchmark.ThermoEngineMeltActivityEngine)
    assert engines[0].name == "thermoengine"
    assert engines[0].timeout_s == 17.0


def test_coverage_map_records_melts_refusal_below_30_sio2():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    rows = benchmark.run_coverage_map(
        fixture, [benchmark.AlphaMeltsEngine()], steps=11
    )
    low_silica = [row for row in rows if float(row["SiO2_wt_pct"]) < 30.0]

    assert low_silica
    assert all(row["alphamelts_status"] == "out_of_domain" for row in low_silica)
    assert all("SiO2" in row["alphamelts_reason"] for row in low_silica)
    assert all("alphamelts_finite_prediction" not in row for row in rows)
    assert all("alphamelts_n_pressures" not in row for row in rows)
    assert {row["composition_id"] for row in rows} == {
        "sf04_tholeiite",
        "sf04_alkali_basalt",
        "sf04_komatiite",
        "sf04_dunite",
    }


def test_coverage_empty_pressure_dict_is_not_typed_ok():
    """A coverage cell that computed nothing must not be recorded as ok.

    Safety-net fixture: the *old* hollow shape (ok + empty +
    finite_prediction=False). Post-fix VapoRockEngine.evaluate already
    returns observable_unavailable; this keeps the consumer remap pinned.
    """
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)

    class _HollowVaporock:
        name = "vaporock"

        def coverage(self, composition_wt_pct, temperature_K):
            del composition_wt_pct, temperature_K
            return benchmark.EngineResult(
                status="ok",
                partial_pressures={},
                details={
                    "finite_prediction": False,
                    "observable_family": "partial_pressure",
                },
            )

    rows = benchmark.run_coverage_map(fixture, [_HollowVaporock()], steps=2)

    assert rows
    assert all(row["vaporock_status"] != "ok" for row in rows)
    assert all(row["vaporock_status"] == "observable_unavailable" for row in rows)
    assert all(row["vaporock_finite_prediction"] is False for row in rows)
    assert all(row["vaporock_n_pressures"] == 0 for row in rows)
    assert all(not benchmark.coverage_cell_accepted(row, "vaporock") for row in rows)


def test_coverage_ok_empty_without_finite_prediction_is_not_typed_ok():
    """Generic hole: ok + empty pressures + no finite_prediction flag."""
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)

    class _SilentPressure:
        name = "silent"

        def coverage(self, composition_wt_pct, temperature_K):
            del composition_wt_pct, temperature_K
            return benchmark.EngineResult(status="ok", partial_pressures={})

    rows = benchmark.run_coverage_map(fixture, [_SilentPressure()], steps=2)

    assert rows
    assert all(row["silent_status"] == "observable_unavailable" for row in rows)
    assert all(row["silent_finite_prediction"] is False for row in rows)
    assert all(row["silent_n_pressures"] == 0 for row in rows)
    assert sum(benchmark.coverage_cell_accepted(row, "silent") for row in rows) == 0


def test_coverage_non_pressure_engine_omits_pressure_columns():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)

    class _DomainOk:
        name = "alphamelts"

        def coverage(self, composition_wt_pct, temperature_K):
            del composition_wt_pct, temperature_K
            return benchmark.EngineResult(
                status="ok",
                details={"observable_family": "domain_gate"},
            )

    rows = benchmark.run_coverage_map(fixture, [_DomainOk()], steps=2)

    assert rows
    assert all(row["alphamelts_status"] == "ok" for row in rows)
    assert all("alphamelts_finite_prediction" not in row for row in rows)
    assert all("alphamelts_n_pressures" not in row for row in rows)
    assert all(benchmark.coverage_cell_accepted(row, "alphamelts") for row in rows)


def test_vaporock_evaluate_empty_speciation_is_not_typed_ok(monkeypatch):
    class _Result:
        status = "not_converged"
        warnings = (
            "VapoRock speciation table has no finite log10_bar values",
        )
        vapor_pressures_Pa = {}
        vaporock_full_speciation_Pa = {}
        diagnostics = {
            "empty_speciation_cause": "no_finite_values",
            "finite_prediction": False,
        }

    class _Backend:
        def initialize(self, config):
            del config
            return True

        def equilibrate(self, **kwargs):
            del kwargs
            return _Result()

    monkeypatch.setattr(
        "simulator.melt_backend.vaporock.VapoRockBackend",
        lambda: _Backend(),
    )
    engine = benchmark.VapoRockEngine()
    result = engine.evaluate({"SiO2": 31.65, "MgO": 6.79}, 1900.0, 1.0e-9)

    assert result.status == "observable_unavailable"
    assert result.status != "ok"
    assert result.partial_pressures == {}
    assert result.details["finite_prediction"] is False
    assert "no finite log10_bar" in result.reason


def test_rump_coverage_reports_internal_only_and_imcc_only_counts():
    rows = [
        {
            "SiO2_wt_pct": 20.0,
            "internal_analytic_status": "ok",
            "imcc-published_status": "ok",
        },
        {
            "SiO2_wt_pct": 25.0,
            "internal_analytic_status": "ok",
            "imcc-published_status": "refused",
        },
        {
            "SiO2_wt_pct": 10.0,
            "internal_analytic_status": "refused",
            "imcc-published_status": "ok",
        },
        {
            "SiO2_wt_pct": 40.0,
            "internal_analytic_status": "refused",
            "imcc-published_status": "refused",
        },
    ]

    summary = benchmark.summarize_rump_coverage(rows)

    assert summary == [
        {
            "imcc_engine": "imcc-published",
            "below_30_count": 3,
            "both_accept_count": 1,
            "internal_analytic_only_count": 1,
            "imcc_only_count": 1,
            "neither_count": 0,
        }
    ]


def test_reference_anchor_reproduces_controller_join_and_kems_column():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    rows = benchmark.run_reference_anchors(fixture)
    summary = benchmark.summarize_reference_anchors(fixture, rows)

    assert summary["shared_magma_count"] == 288
    assert summary["max_reference_difference_dex"] < 5.0e-5
    assert summary["controller_pool_imcc_rmse_dex"] == pytest.approx(
        0.274, abs=5.0e-4
    )
    assert summary["controller_pool_vaporock_rmse_dex"] == pytest.approx(
        0.503, abs=5.0e-4
    )
    assert summary["controller_anchor_reproduced"] is True
    assert summary["empirical_kems_scored"] == 7
    assert summary["empirical_kems_total"] == 9


def test_reference_anchor_refuses_tracked_snapshot_hash_mismatch():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["reference_anchors"]["imcc_magma"]["tracked_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="tracked snapshot hash mismatch"):
        benchmark.run_reference_anchors(fixture)


def test_hastie_kems_points_use_shared_gas_layer_for_melt_engines():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["points"] = [
        point
        for point in fixture["points"]
        if point["population"] == "hastie1981_kems"
    ]

    rows = benchmark.run_points(fixture, [_FakeActivityEngine()])

    assert len(rows) == 6
    assert all(row["status"] == "ok" for row in rows)
    assert all(row["residual_dex"] is not None for row in rows)


@pytest.mark.parametrize(
    ("parent_oxide", "parent_activity", "single_cation_activity"),
    (
        ("Na2O", 0.25, 0.5),
        ("K2O", 0.16, 0.4),
        ("Li2O", 0.09, 0.3),
        ("Al2O3", 0.36, 0.6),
        ("MgO", 0.25, 0.25),
    ),
)
def test_parent_formula_activity_is_converted_to_single_cation_gas_basis(
    parent_oxide, parent_activity, single_cation_activity
):
    converted = benchmark._single_cation_gas_activities(
        {parent_oxide: parent_activity}
    )

    assert converted[parent_oxide] == pytest.approx(single_cation_activity)


def test_internal_analytical_live_adapter_uses_shared_gas_layer_at_pinned_fo2():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    point = next(
        point
        for point in fixture["points"]
        if point["id"] == "hastie_sio_1907_796"
    )
    composition = benchmark._normalize_wt(
        fixture["compositions"][point["composition_id"]]["composition_wt_pct"]
    )
    low_fo2 = float(point["fO2_bar"])
    high_fo2 = low_fo2 * 10.0
    engine = benchmark.InternalAnalyticalEngine()

    low_result = engine.evaluate(composition, point["temperature_K"], low_fo2)
    high_result = engine.evaluate(composition, point["temperature_K"], high_fo2)
    low_prediction, low_reason = benchmark._prediction_for_point(
        {**point, "composition_wt_pct": composition, "fO2_bar": low_fo2},
        low_result,
    )
    high_prediction, high_reason = benchmark._prediction_for_point(
        {**point, "composition_wt_pct": composition, "fO2_bar": high_fo2},
        high_result,
    )

    assert low_result.status == high_result.status == "ok"
    assert low_reason == high_reason == ""
    assert low_prediction is not None and high_prediction is not None
    assert high_prediction < low_prediction

    from simulator.diagnostic_helpers.alphamelts_volatility import (
        _analytical_vapor_pressures_from_activities,
        _load_default_vapor_pressure_data,
    )

    expected = _analytical_vapor_pressures_from_activities(
        vapor_pressure_data=_load_default_vapor_pressure_data(),
        temperature_C=float(point["temperature_K"]) - 273.15,
        pO2_bar=low_fo2,
        melt_oxide_activities=benchmark._single_cation_gas_activities(
            low_result.activities
        ),
        composition_wt_pct=composition,
    )["species"]["SiO"]["P_eq_Pa"]
    assert low_prediction == pytest.approx(expected)


def test_internal_analytical_live_adapter_converts_gamma_to_parent_oxide_basis():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    point = next(
        point
        for point in fixture["points"]
        if point["id"] == "richter_mg_gamma_1873"
    )
    composition = benchmark._normalize_wt(
        fixture["compositions"][point["composition_id"]]["composition_wt_pct"]
    )

    result = benchmark.InternalAnalyticalEngine().evaluate(
        composition, point["temperature_K"], 1.0e-9
    )

    assert result.status == "ok"
    assert result.gammas["MgO"] == pytest.approx(0.8968179981767649)


def test_missing_point_observable_is_recorded_as_observable_unavailable():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["points"] = [
        point for point in fixture["points"] if point["id"] == "hastie_k_1917_186"
    ]

    class _MissingActivity(_FakeActivityEngine):
        name = "internal_analytic"

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            return benchmark.EngineResult(status="ok")

    row = benchmark.run_points(fixture, [_MissingActivity()])[0]

    assert row["status"] == "observable_unavailable"
    assert row["prediction"] is None
    assert "no positive partial_pressure" in row["reason"]


def test_activity_observable_without_oxide_label_is_typed_separately_from_refusal():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    point = next(
        point
        for point in fixture["points"]
        if point["observable"] == "activity_coefficient"
    )
    fixture["points"] = [{
        **point,
        "species": "Na",
        "parent_oxide": "Na2O",
        "observable": "activity",
        "fO2_bar": 1.0e-6,
    }]

    class _EndmemberLabelEngine(_FakeActivityEngine):
        name = "alphamelts"

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K
            assert fO2_bar is None
            return benchmark.EngineResult(
                status="ok",
                activities={"SiO2": 0.2},
                details={"unmapped_activity_labels": ["Na2SiO3"]},
            )

    row = benchmark.run_points(fixture, [_EndmemberLabelEngine()])[0]

    assert row["status"] == "observable_unavailable"
    assert row["prediction"] is None
    assert "typed-refusal:melts_endmember_not_parent_oxide:Na2O" in row["reason"]
    assert "Na2SiO3" in row["reason"]
    assert "standard state the model does not define" in row["reason"]


def test_paired_decision_uses_identical_scored_points():
    rows = [
        {
            "point_id": "p1",
            "species": "Mg",
            "observable": "activity_coefficient",
            "engine": "internal_analytic",
            "residual_dex": 0.4,
        },
        {
            "point_id": "p1",
            "species": "Mg",
            "observable": "activity_coefficient",
            "engine": "imcc-published",
            "residual_dex": 0.1,
        },
        {
            "point_id": "p2",
            "species": "Mg",
            "observable": "activity_coefficient",
            "engine": "imcc-published",
            "residual_dex": 0.01,
        },
    ]

    decisions = benchmark.summarize_paired_decisions(rows)

    assert len(decisions) == 1
    assert decisions[0]["paired_count"] == 1
    assert decisions[0]["engine_a"] == "imcc-published"
    assert decisions[0]["engine_b"] == "internal_analytic"
    assert decisions[0]["decision"] == "imcc-published"


def test_paired_verdict_does_not_call_win_plus_tie_better_on_every_group():
    decisions = [
        {
            "engine_a": "imcc-published",
            "engine_b": "internal_analytic",
            "decision": "imcc-published",
            "paired_count": 3,
        },
        {
            "engine_a": "imcc-published",
            "engine_b": "internal_analytic",
            "decision": "tie",
            "paired_count": 3,
        },
        {
            "engine_a": "imcc-ext",
            "engine_b": "internal_analytic",
            "decision": "internal_analytic",
            "paired_count": 3,
        },
        {
            "engine_a": "imcc-ext",
            "engine_b": "internal_analytic",
            "decision": "tie",
            "paired_count": 3,
        },
    ]

    verdict = benchmark._paired_verdict(decisions)

    assert "`imcc-published` vs `internal_analytic`" in verdict
    assert "imcc-published better or tied" in verdict
    assert "`imcc-ext` vs `internal_analytic`" in verdict
    assert "internal_analytic better or tied" in verdict
    assert "n=3,3" in verdict


def test_unscored_point_preserves_engine_crash_status():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["points"] = [
        point
        for point in fixture["points"]
        if point["id"] == "richter_mg_flux_2173"
    ]

    class _Crash(_FakeActivityEngine):
        name = "alphamelts"

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            return benchmark.EngineResult(status="crash", reason="SIGSEGV")

    rows = benchmark.run_points(fixture, [_Crash()])

    assert rows[0]["status"] == "crash"
    assert rows[0]["reason"] == "SIGSEGV"


def test_wt_to_mol_returns_numeric_mole_amounts():
    result = benchmark._wt_to_mol({"SiO2": 50.0, "MgO": 50.0})

    assert result["SiO2"] > 0.0
    assert result["MgO"] > result["SiO2"]


def test_per_point_status_capture_turns_simulated_sigsegv_into_result():
    class _CrashingEngine:
        name = "alphamelts"

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            raise RuntimeError("subprocess died with SIGSEGV (returncode -11)")

    result = benchmark.execute_engine(
        _CrashingEngine(), {"SiO2": 50.0, "MgO": 50.0}, 1900.0, 1.0e-9
    )

    assert result.status == "crash"
    assert "SIGSEGV" in result.reason


def test_provider_crash_diagnostic_overrides_out_of_domain_status():
    class _Provider:
        def dispatch(self, request):
            del request
            return SimpleNamespace(
                status="out_of_domain",
                warnings=("SIGSEGV",),
                diagnostic={
                    "backend_diagnostics": {
                        "backend_failure_category": "engine_crash",
                        "backend_status_reason": "subprocess_died",
                        "backend_status_reason_message": "SIGSEGV (returncode -11)",
                    }
                },
            )

    engine = benchmark.AlphaMeltsEngine()
    engine.activity_observable_supported = True
    engine._provider = _Provider()
    result = engine.evaluate({"SiO2": 50.0, "MgO": 50.0}, 1900.0, 1.0e-9)

    assert result.status == "crash"
    assert "SIGSEGV" in result.reason


def test_coverage_silicate_band_is_rail_owned():
    # UPDATED 2026-08-16: was SiO2 10.0 with band (0, 100). That asserted the
    # rail could open alphaMELTS down to 10 wt%; the rump-hotwire measurement
    # found it SIGABRTs on all 40 multi-component sub-30 points, so a band
    # below the 34.0 wt% crash floor is now refused. Widening is still
    # rail-owned -- it goes UPWARD past the default 80 wt% max.
    composition = {"SiO2": 85.0, "MgO": 7.5, "FeO": 7.5}
    default = benchmark.AlphaMeltsEngine()
    widened = benchmark.AlphaMeltsEngine(silicate_network_band=(34.0, 100.0))

    refused = default.coverage(composition, 1900.0)
    admitted = widened.coverage(composition, 1900.0)

    assert refused.status == "out_of_domain"
    assert "silicate_network_band" in refused.details["failed_constraints"]
    assert refused.details["silicate_network_band_wt_pct"] == [30.0, 80.0]
    assert admitted.status == "ok"
    assert admitted.details["failed_constraints"] == []


def test_vaporock_activity_call_does_not_ask_the_library(monkeypatch):
    calls = []

    class _Backend:
        def initialize(self, config):
            del config
            return True

        def equilibrate(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("activity path must not call equilibrate")

    monkeypatch.setattr(
        "simulator.melt_backend.vaporock.VapoRockBackend",
        lambda: _Backend(),
    )
    engine = benchmark.VapoRockEngine()
    result = engine.evaluate({"SiO2": 50.0, "MgO": 50.0}, 1900.0, None)

    assert result.status == "ok"
    assert result.activities == {}
    assert result.partial_pressures == {}
    assert result.details["observable_supported"] is False
    assert "partial pressures" in result.reason
    assert calls == []


def test_vaporock_evaluate_returns_native_partial_pressures(monkeypatch):
    class _Result:
        status = "ok"
        warnings = ()
        vapor_pressures_Pa = {"SiO": 0.11, "K": 2.5}
        vaporock_full_speciation_Pa = {"SiO": 0.11, "K": 2.5, "O2": 0.5}

    class _Backend:
        def initialize(self, config):
            del config
            return True

        def equilibrate(self, **kwargs):
            assert kwargs["fO2_log"] == pytest.approx(-5.0)
            return _Result()

    monkeypatch.setattr(
        "simulator.melt_backend.vaporock.VapoRockBackend",
        lambda: _Backend(),
    )
    engine = benchmark.VapoRockEngine()
    result = engine.evaluate({"SiO2": 50.0, "MgO": 50.0}, 1900.0, 1.0e-5)

    assert result.status == "ok"
    assert result.partial_pressures["SiO"] == pytest.approx(0.11)
    assert result.activities == {}
    assert result.details["observable_family"] == "partial_pressure"


def test_vaporock_partial_pressure_is_scored_on_native_offgas():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    point = next(
        item
        for item in fixture["points"]
        if item["id"] == "hastie_sio_1907_796"
    )
    composition = benchmark._normalize_wt(
        fixture["compositions"][point["composition_id"]]["composition_wt_pct"]
    )
    produced = benchmark.EngineResult(
        status="ok",
        partial_pressures={"SiO": 0.2, "K": 3.0},
        details={"observable_family": "partial_pressure"},
    )

    prediction, reason = benchmark._prediction_for_point(
        {**point, "composition_wt_pct": composition},
        produced,
    )

    assert reason == ""
    assert prediction == pytest.approx(0.2)


def test_vaporock_activity_rows_are_typed_not_dead():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    point = next(
        item
        for item in fixture["points"]
        if item["observable"] == "activity"
    )
    fixture["points"] = [point]

    class _Vaporock(benchmark.VapoRockEngine):
        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            assert fO2_bar is None
            return super().evaluate(composition_wt_pct, temperature_K, fO2_bar)

        def _initialize(self):
            return object()

    row = benchmark.run_points(fixture, [_Vaporock()])[0]

    assert row["engine"] == "vaporock"
    assert row["status"] == "observable_unavailable"
    assert row["prediction"] is None
    assert row["status"] != "unavailable"
    assert "partial pressures" in row["reason"]


def test_report_presents_vaporock_as_vapour_pressure_leg_not_empty_activity():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    rows = [
        {
            "point_id": "hastie_sio_1907_796",
            "species": "SiO",
            "observable": "partial_pressure",
            "engine": "vaporock",
            "status": "ok",
            "residual_dex": 0.05,
        },
        {
            "point_id": "tsaplin_dummy",
            "species": "Na",
            "observable": "activity",
            "engine": "vaporock",
            "status": "observable_unavailable",
            "residual_dex": None,
        },
    ]

    report = benchmark.generate_report(fixture, rows, [], [])

    assert "## VapoRock vapour-pressure leg" in report
    assert "1 scored / 1 planned" in report
    assert "| Na | activity | vaporock |" not in report
    assert "| SiO | partial_pressure | vaporock | 1 |" in report


def test_real_alphamelts_producer_types_unmapped_label_as_ok_not_refused():
    """The REAL AlphaMeltsEngine producer must emit ok/completed_without_observable
    when the engine computed an activity under an unmapped endmember label.

    The sibling typed-differentiation tests inject an already-typed EngineResult, so
    they stay green even if the producer regresses to `refused` — the mutation the
    2026-08-13 adversarial closer found surviving. This test drives the real
    producer through a stubbed provider so that mutation is killed: a provider that
    reports only `Na2SiO3` (no oxide mapping) must yield producer status `ok`, and
    the pipeline must type the row `observable_unavailable`, never `refused`.
    """
    class _UnmappedLabelProvider:
        def dispatch(self, request):
            del request
            return SimpleNamespace(
                status="ok",
                warnings=[],
                diagnostic={
                    "backend_diagnostics": {
                        "diagnostic_reported_activities": {"Na2SiO3": 0.31},
                        "diagnostic_oxide_activities": {},
                        "diagnostic_activity_label_map": {"Na2SiO3": {}},
                        "diagnostic_activity_basis": "endmember",
                    },
                },
            )

    engine = benchmark.AlphaMeltsEngine()
    engine.activity_observable_supported = True
    engine._provider = _UnmappedLabelProvider()

    produced = engine.evaluate({"SiO2": 70.0, "Na2O": 30.0}, 1473.0, None)

    # Producer half: computed-but-unmapped is NOT a refusal.
    assert produced.status == "ok"
    assert produced.details["execution_status"] == "completed_without_observable"
    assert produced.details["unmapped_activity_labels"] == ["Na2SiO3"]
    assert not produced.activities

    # Consumer half: the row types as unavailable-observable, not refused.
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    point = next(
        p for p in fixture["points"] if p["observable"] == "activity_coefficient"
    )
    fixture["points"] = [{
        **point,
        "species": "Na",
        "parent_oxide": "Na2O",
        "observable": "activity",
        "fO2_bar": 1.0e-6,
    }]
    row = benchmark.run_points(fixture, [engine])[0]
    assert row["status"] == "observable_unavailable"
    assert row["status"] != "refused"
    assert "typed-refusal:melts_endmember_not_parent_oxide:Na2O" in row["reason"]
    assert "Na2SiO3" in row["reason"]


def test_kume_style_cao_mgo_rows_are_typed_refusals_not_residuals() -> None:
    """Red-by-revert: CaSiO3/Mg2SiO4 must not become scored a(CaO)/a(MgO)."""
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    kume_points = [
        point
        for point in fixture["points"]
        if str(point.get("composition_id") or "").startswith("kume2000_")
        and point["parent_oxide"] in {"SiO2", "Al2O3", "CaO", "MgO"}
        and point.get("score", True)
    ]
    by_oxide = {}
    for point in kume_points:
        by_oxide.setdefault(point["parent_oxide"], point)
    assert set(by_oxide) == {"SiO2", "Al2O3", "CaO", "MgO"}
    fixture = {**fixture, "points": [by_oxide[oxide] for oxide in sorted(by_oxide)]}

    class _KumeLabelEngine(_FakeActivityEngine):
        name = "thermoengine"

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            return benchmark.EngineResult(
                status="ok",
                activities={"SiO2": 0.42, "Al2O3": 0.25},
                details={
                    "reported_activity_labels": [
                        "SiO2",
                        "Al2O3",
                        "CaSiO3",
                        "Mg2SiO4",
                        "Ca3(PO4)2",
                        "CoSiO3",
                    ],
                    "unmapped_activity_labels": [
                        "CaSiO3",
                        "Mg2SiO4",
                        "Ca3(PO4)2",
                        "CoSiO3",
                    ],
                },
            )

    rows = benchmark.run_points(fixture, [_KumeLabelEngine()])
    by_parent = {row["parent_oxide"]: row for row in rows}

    assert by_parent["SiO2"]["prediction"] == pytest.approx(0.42)
    assert by_parent["SiO2"]["residual_dex"] is not None
    assert by_parent["Al2O3"]["prediction"] == pytest.approx(0.25)
    assert by_parent["Al2O3"]["residual_dex"] is not None

    for oxide in ("CaO", "MgO"):
        assert by_parent[oxide]["prediction"] is None
        assert by_parent[oxide]["residual_dex"] is None
        assert by_parent[oxide]["status"] == "observable_unavailable"
        assert (
            f"typed-refusal:melts_endmember_not_parent_oxide:{oxide}"
            in by_parent[oxide]["reason"]
        )


def _te_row(point_id, status, *, prediction=None, reason=""):
    return {
        "point_id": point_id,
        "species": "SiO2",
        "observable": "activity",
        "engine": "thermoengine",
        "status": status,
        "prediction": prediction,
        "reason": reason,
    }


def test_short_latch_reason_strips_traceback():
    text = (
        "RuntimeError: ThermoEngine equilibrium failed: "
        "ThermoEngine Liquid GibbsFreeEnergy is not finite: nan "
        "Traceback (most recent call last): File \"x.py\", line 1"
    )
    assert benchmark._short_latch_reason(text) == (
        "RuntimeError: ThermoEngine equilibrium failed: "
        "ThermoEngine Liquid GibbsFreeEnergy is not finite: nan"
    )


def test_detect_thermoengine_adapter_latch_finds_post_refuse_unavailable_run():
    rows = [
        _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
        _te_row(
            "tsaplin2000_a_sio2_x0477_1373",
            "refused",
            reason="RuntimeError: ThermoEngine equilibrium failed",
        ),
        _te_row(
            "tsaplin2000_a_sio2_x0430_1473",
            "unavailable",
            reason="AlphaMELTS adapter not available (no ThermoEngine, PetThermoTools, or subprocess transport)",
        ),
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "unavailable",
            reason="AlphaMELTS adapter not available (no ThermoEngine, PetThermoTools, or subprocess transport)",
        ),
    ]

    latch = benchmark.detect_thermoengine_adapter_latch(rows)

    assert latch is not None
    assert latch["latch_after_point_id"] == "tsaplin2000_a_sio2_x0477_1373"
    assert latch["sequential_usable"] == 1
    assert latch["sequential_total"] == 4
    assert latch["latched_count"] == 2
    assert latch["yamaguchi_latched_count"] == 1
    assert latch["latched_point_ids"] == [
        "tsaplin2000_a_sio2_x0430_1473",
        "yamaguchi1983_a_sio2_liquid_x0205_1373",
    ]


def test_detect_thermoengine_adapter_latch_absent_when_all_rows_are_live():
    rows = [
        _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
        _te_row("tsaplin2000_a_sio2_x0753_1273", "ok", prediction=0.2),
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "ok",
            prediction=0.6,
        ),
    ]

    assert benchmark.detect_thermoengine_adapter_latch(rows) is None


def test_generate_report_does_not_publish_latched_count_as_engine_capability():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    rows = [
        _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
        _te_row(
            "tsaplin2000_a_sio2_x0477_1373",
            "refused",
            reason="RuntimeError: ThermoEngine equilibrium failed",
        ),
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "unavailable",
            reason="AlphaMELTS adapter not available (no ThermoEngine, PetThermoTools, or subprocess transport)",
        ),
    ]

    report = benchmark.generate_report(fixture, rows, [], [])

    assert "ThermoEngine sequential one-process yield: 1/3" in report
    assert "post-latch artifact" in report
    assert "tsaplin2000_a_sio2_x0477_1373" in report
    assert "Do not read the sequential count" in report
    assert "ThermoEngine produced 1/3 usable" not in report
    assert "Isolated ThermoEngine re-evaluation" in report
    assert "not asserted here" in report


def test_generate_report_states_measured_unlatched_coverage_when_supplied():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    rows = [
        _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
        _te_row(
            "tsaplin2000_a_sio2_x0477_1373",
            "refused",
            reason="RuntimeError: ThermoEngine equilibrium failed",
        ),
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "unavailable",
            reason="AlphaMELTS adapter not available (no ThermoEngine, PetThermoTools, or subprocess transport)",
        ),
    ]
    latch = benchmark.detect_thermoengine_adapter_latch(rows)
    assert latch is not None
    latch = {
        **latch,
        "isolated_usable": 1,
        "isolated_total": 1,
        "isolated_yamaguchi_usable": 1,
        "isolated_yamaguchi_total": 1,
        "true_usable": 2,
        "true_total": 3,
    }

    report = benchmark.generate_report(
        fixture, rows, [], [], thermoengine_latch=latch
    )

    assert "post-latch artifact" in report
    assert "produced 1/1 usable predictions (Yamaguchi: 1/1)" in report
    assert "2/3" in report
    assert "not taken from the latched CSV" in report
    assert "isolated-mode ceiling" in report
    assert "isolated retry remains required" in report
    assert "ThermoEngineFO2UndefinedError" in report


def test_isolated_thermoengine_retries_after_adapter_unavailable():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    yamaguchi = next(
        point
        for point in fixture["points"]
        if point["id"].startswith("yamaguchi1983_a_sio2_liquid")
    )
    fixture = {**fixture, "points": [yamaguchi]}

    class _DieOnceThenScore:
        calls = 0

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            type(self).calls += 1
            if type(self).calls == 1:
                return benchmark.EngineResult(
                    status="unavailable",
                    reason=(
                        "ThermoEngine transport closed after "
                        "RuntimeError: affinity shape"
                    ),
                )
            return benchmark.EngineResult(
                status="ok",
                activities={"SiO2": 0.55},
                gammas={"SiO2": 0.55},
            )

    measured = benchmark.measure_isolated_thermoengine_points(
        fixture,
        [yamaguchi["id"]],
        engine_factory=_DieOnceThenScore,
    )

    assert measured["usable"] == 1
    assert measured["total"] == 1
    assert measured["yamaguchi_usable"] == 1
    assert measured["restarts"] == 1
    assert measured["rows"][0]["prediction"] is not None


def test_ok_and_adapter_unavailable_is_self_contradiction():
    rows = [
        _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "unavailable",
            reason=(
                "AlphaMELTS adapter not available "
                "(no ThermoEngine, PetThermoTools, or subprocess transport)"
            ),
        ),
    ]
    assert benchmark.engines_with_ok_and_adapter_unavailable(rows) == (
        "thermoengine",
    )

    edited = [
        _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "unavailable",
            reason=(
                "AlphaMELTS adapter unavailable "
                "(is_available=False; backend=ThermoEngineBackend)"
            ),
        ),
    ]
    assert benchmark.engines_with_ok_and_adapter_unavailable(edited) == (
        "thermoengine",
    )

    typed_refuse = [
        _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
        _te_row(
            "tsaplin2000_a_na2o_x0477_1373",
            "refused",
            reason=(
                "ThermoEngineNonFiniteField: ThermoEngine Liquid "
                "GibbsFreeEnergy is not finite: nan"
            ),
        ),
    ]
    assert benchmark.engines_with_ok_and_adapter_unavailable(typed_refuse) == ()


def test_detect_thermoengine_adapter_latch_survives_reason_string_edit():
    """Detector keys on ok+unavailable contradiction, not unavailable prose.

    This is the test that would have caught the t-681 wording-blindness:
    after the canned "adapter not available" sentence was retired, a
    token detector returned None and isolated retry never ran. A reason
    that contains none of the historical tokens must still fire.
    """
    edited_reasons = (
        "AlphaMELTS adapter unavailable "
        "(is_available=False; backend=ThermoEngineBackend)",
        "ThermoEngine transport closed after RuntimeError: affinity shape",
        "entirely new prose that names no adapter at all",
    )
    for edited_reason in edited_reasons:
        assert "adapter not available" not in edited_reason.lower()
        rows = [
            _te_row("hastie_sio_1907_796", "ok", prediction=0.05),
            _te_row(
                "tsaplin2000_a_sio2_x0477_1373",
                "refused",
                reason=(
                    "ThermoEngineNonFiniteField: ThermoEngine Liquid "
                    "GibbsFreeEnergy is not finite: nan"
                ),
            ),
            _te_row(
                "yamaguchi1983_a_sio2_liquid_x0205_1373",
                "unavailable",
                reason=edited_reason,
            ),
        ]
        latch = benchmark.detect_thermoengine_adapter_latch(rows)
        assert latch is not None, edited_reason
        assert latch["latched_count"] == 1
        assert latch["latch_after_point_id"] == "tsaplin2000_a_sio2_x0477_1373"
        assert latch["latch_first_point_id"] == (
            "yamaguchi1983_a_sio2_liquid_x0205_1373"
        )
        assert benchmark.engines_with_ok_and_adapter_unavailable(rows) == (
            "thermoengine",
        )


def test_detect_thermoengine_adapter_latch_absent_when_never_ok():
    rows = [
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "unavailable",
            reason="ThermoEngine transport not initialized",
        ),
    ]
    assert benchmark.detect_thermoengine_adapter_latch(rows) is None
    assert benchmark.engines_with_ok_and_adapter_unavailable(rows) == ()


def test_detect_thermoengine_adapter_latch_fires_on_first_row_death():
    """Producer close marker detects a latch that begins before any ok.

    Red-by-revert of the marker branch: the ok+unavailable contradiction
    cannot see [refused, unavailable, unavailable], which is the
    die-on-first-row shape the retired token detector did fire on.
    """
    rows = [
        _te_row(
            "tsaplin2000_a_sio2_x0477_1373",
            "refused",
            reason="RuntimeError: ThermoEngine equilibrium failed: boom",
        ),
        _te_row(
            "tsaplin2000_a_sio2_x0430_1473",
            "unavailable",
            reason="ThermoEngine transport closed after RuntimeError: boom",
        ),
        _te_row(
            "yamaguchi1983_a_sio2_liquid_x0205_1373",
            "unavailable",
            reason="ThermoEngine transport closed after RuntimeError: boom",
        ),
    ]
    assert benchmark.engines_with_ok_and_adapter_unavailable(rows) == ()
    assert benchmark.detect_thermoengine_adapter_latch(rows) is None
    latch = benchmark.detect_thermoengine_adapter_latch(
        rows, transport_closed_mid_run=True
    )
    assert latch is not None
    assert latch["transport_closed_mid_run"] is True
    assert latch["ok_unavailable_contradiction"] is False
    assert latch["latch_after_point_id"] == "tsaplin2000_a_sio2_x0477_1373"
    assert latch["latch_first_point_id"] == "tsaplin2000_a_sio2_x0430_1473"
    assert latch["latched_count"] == 2
    assert latch["yamaguchi_latched_count"] == 1


def test_detect_thermoengine_adapter_latch_accepts_probe_row_ids():
    rows = [
        {
            "probe_id": "sf04_tholeiite",
            "engine": "thermoengine",
            "status": "ok",
            "reason": "",
        },
        {
            "probe_id": "sf04_dunite",
            "engine": "thermoengine",
            "status": "unavailable",
            "reason": "ThermoEngine transport closed after RuntimeError",
        },
    ]
    latch = benchmark.detect_thermoengine_adapter_latch(rows)
    assert latch is not None
    assert latch["latch_after_point_id"] == "sf04_tholeiite"
    assert latch["latch_first_point_id"] == "sf04_dunite"
    assert latch["latched_count"] == 1


def test_classify_keep_handle_is_type_not_substring():
    """Keep-handle status is decided by type, not the word 'unavailable'.

    Red-by-revert of the type-first gate: a substring classifier maps
    this message to status=unavailable, which the structural detector
    then treats as adapter-absence.
    """
    from engines.alphamelts.thermoengine import (
        ThermoEngineFO2OmittedError,
        ThermoEngineFO2UndefinedError,
        ThermoEngineNonFiniteField,
    )

    nonfinite = ThermoEngineNonFiniteField(
        "ThermoEngine Liquid GibbsFreeEnergy is unavailable: nan"
    )
    status, reason = benchmark.classify_engine_exception(nonfinite)
    assert status == "refused"
    assert "unavailable" in reason.lower()

    fo2 = ThermoEngineFO2UndefinedError(
        "zero-ferric liquid; finite Kress91 fO2 echo is unavailable"
    )
    status, reason = benchmark.classify_engine_exception(fo2)
    assert status == "refused"
    assert "unavailable" in reason.lower()

    omitted = ThermoEngineFO2OmittedError(
        "ThermoEngine equilibrium omitted solved fO2 without a "
        "typed proven-undefined reason: adapter unavailable after close"
    )
    status, reason = benchmark.classify_engine_exception(omitted)
    assert status == "refused"
    assert "unavailable" in reason.lower()


def test_classify_bare_overflow_is_crash_not_refusal():
    """t-717: OverflowError is a numerical crash; raw errno must not be the reason."""

    status, reason = benchmark.classify_engine_exception(
        OverflowError(34, "Result too large")
    )
    assert status == "crash"
    assert reason == "numerical_overflow"
    assert "OverflowError" not in reason


def test_classify_overflow_subclass_keeps_typed_message():
    """Subclasses that already carry a typed prefix are not collapsed."""

    class VaporishOverflow(OverflowError):
        pass

    status, reason = benchmark.classify_engine_exception(
        VaporishOverflow("vapor_pressure_numerical_overflow: species=Na field=K_decomp")
    )
    assert status == "refused"
    assert "vapor_pressure_numerical_overflow" in reason
    assert reason != "numerical_overflow"


def test_classify_thermoengine_fo2_valueerror_paths_not_retuned():
    """t-718 sibling: OverflowError mapping must not retune ValueError paths."""

    outside = ValueError(
        "ThermoEngine absolute fO2 target is outside the attainable "
        "Fe-redox bracket: requested=-9"
    )
    status, reason = benchmark.classify_engine_exception(outside)
    assert status == "out_of_domain"
    assert "ValueError" in reason
    assert "OverflowError" not in reason

    finite = ValueError("ThermoEngine absolute fO2 target must be finite")
    status, reason = benchmark.classify_engine_exception(finite)
    assert status == "refused"
    assert "ValueError" in reason

    wrapped_unavailable = ValueError(
        "ThermoEngine absolute fO2 target solve unavailable after close"
    )
    status, reason = benchmark.classify_engine_exception(wrapped_unavailable)
    assert status == "unavailable"


@pytest.mark.parametrize(
    "composition",
    (
        {"SiO2": 2.0, "CaO": 98.0},
        {"SiO2": 0.5, "CaO": 99.5},
    ),
)
def test_internal_analytic_lime_rich_binary_does_not_overflow(composition):
    """t-717: CaO-SiO2 at X_Ca >= 0.981 must not wear OverflowError as a refusal.

    Table-gamma activities are computable (the 0.5 wt% SiO2 point is inside
    the X>0.99 Raoultian shell). The structural diagnostic is out of
    domain; it must not abort the engine.
    """

    engine = benchmark.InternalAnalyticalEngine()
    result = benchmark.execute_engine(engine, composition, 1673.15, 1.0e-9)
    assert "OverflowError" not in (result.reason or "")
    if result.status != "ok":
        assert result.status in benchmark.POINT_STATUSES
        assert result.status != "crash"
        reason = result.reason or ""
        assert "dilute_network_former_out_of_domain" in reason or reason
        return
    assert result.activities
    for name, value in result.activities.items():
        assert math.isfinite(value) and value > 0.0, (name, value)


def test_isolated_retry_rebuilds_on_producer_close_not_reason_prose():
    """Isolated restart keys on the producer close count, not reason text.

    Red-by-revert of the de-prosed `_adapter_killed_this_call`: a
    substring test on "equilibrium failed" / "transport unavailable"
    would miss the first engine (no historical tokens) and would
    falsely rebuild the second (tokens, no close).
    """
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    yamaguchi = [
        point
        for point in fixture["points"]
        if point["id"].startswith("yamaguchi1983_a_sio2_liquid")
    ][:2]
    assert len(yamaguchi) == 2
    fixture = {**fixture, "points": yamaguchi}

    class _CloseWithoutHistoricalTokens:
        created = 0

        def __init__(self):
            type(self).created += 1
            self._closes = 0
            self.calls = 0

        def transport_close_count(self):
            return self._closes

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            self.calls += 1
            if type(self).created == 1 and self.calls == 1:
                self._closes += 1
                return benchmark.EngineResult(
                    status="refused",
                    reason="row-local death with no historical tokens",
                )
            return benchmark.EngineResult(
                status="ok",
                activities={"SiO2": 0.55},
                gammas={"SiO2": 0.55},
            )

    _CloseWithoutHistoricalTokens.created = 0
    measured = benchmark.measure_isolated_thermoengine_points(
        fixture,
        [point["id"] for point in yamaguchi],
        engine_factory=_CloseWithoutHistoricalTokens,
    )
    assert _CloseWithoutHistoricalTokens.created == 2
    assert measured["restarts"] == 1
    assert measured["usable"] == 1

    class _ProseWithoutClose:
        created = 0

        def __init__(self):
            type(self).created += 1
            self._closes = 0

        def transport_close_count(self):
            return self._closes

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            return benchmark.EngineResult(
                status="refused",
                reason="ThermoEngine equilibrium failed: transport unavailable",
            )

    _ProseWithoutClose.created = 0
    measured_prose = benchmark.measure_isolated_thermoengine_points(
        fixture,
        [point["id"] for point in yamaguchi],
        engine_factory=_ProseWithoutClose,
    )
    assert _ProseWithoutClose.created == 1
    assert measured_prose["restarts"] == 0


def test_run_benchmark_passes_producer_close_marker_to_point_and_probe_detectors(
    tmp_path, monkeypatch
):
    seen: list[tuple[bool, bool]] = []

    def capturing_detect(rows, *, transport_closed_mid_run=False):
        has_probe_id = any("probe_id" in row for row in rows)
        has_point_id = any("point_id" in row for row in rows)
        seen.append((has_point_id, has_probe_id, transport_closed_mid_run))
        return None

    class _ClosedThermoEngine:
        name = "thermoengine"

        def transport_close_count(self):
            return 1

        def transport_closed_mid_run(self):
            return True

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            return benchmark.EngineResult(
                status="refused",
                reason="first-row death",
            )

        def coverage(self, composition_wt_pct, temperature_K):
            del composition_wt_pct, temperature_K
            return benchmark.EngineResult(status="ok")

    monkeypatch.setattr(
        benchmark, "detect_thermoengine_adapter_latch", capturing_detect
    )
    monkeypatch.setattr(
        benchmark,
        "build_engines",
        lambda names, fixture, alphamelts_timeout_s: [_ClosedThermoEngine()],
    )
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["points"] = fixture["points"][:1]
    fixture["composition_probes"] = fixture["composition_probes"][:1]
    fixture_path = tmp_path / "tiny.yaml"
    fixture_path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")

    result = benchmark.run_benchmark(
        bench_set_path=fixture_path,
        output_dir=tmp_path / "out",
        engine_names=("thermoengine",),
        mode="all",
        coverage_steps=2,
        live_vaporock_anchor_check=False,
    )

    assert any(item[0] and not item[1] and item[2] for item in seen)
    assert any(item[1] and item[2] for item in seen)
    assert result["metadata"]["thermoengine_probe_latch"] is None


def test_tracked_melt_activity_report_marks_thermoengine_narrative_stale():
    text = (
        benchmark.DEFAULT_OUTPUT_DIR / "report.md"
    ).read_text(encoding="utf-8")
    assert "STALE (t-681" in text
    assert "isolated-mode ceiling" in text
    assert "retired" in text.lower()
    assert "adapter not available" in text


def test_run_benchmark_rebuilds_engines_before_probes(tmp_path, monkeypatch):
    constructed: list[object] = []

    class CountingEngine:
        name = "fake"

        def __init__(self):
            constructed.append(self)

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del composition_wt_pct, temperature_K, fO2_bar
            return benchmark.EngineResult(
                status="ok",
                activities={"SiO2": 0.2},
                gammas={"SiO2": 0.2},
            )

        def coverage(self, composition_wt_pct, temperature_K):
            del composition_wt_pct, temperature_K
            return benchmark.EngineResult(status="ok")

    monkeypatch.setattr(
        benchmark,
        "build_engines",
        lambda names, fixture, alphamelts_timeout_s: [CountingEngine()],
    )
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    fixture["points"] = fixture["points"][:1]
    fixture["composition_probes"] = fixture["composition_probes"][:1]
    fixture_path = tmp_path / "tiny.yaml"
    fixture_path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")

    benchmark.run_benchmark(
        bench_set_path=fixture_path,
        output_dir=tmp_path / "out",
        engine_names=("fake",),
        mode="benchmark",
        live_vaporock_anchor_check=False,
    )

    assert len(constructed) >= 2
    assert constructed[0] is not constructed[1]


def test_positive_finite_refuses_nonfinite_instead_of_shrinking_the_sample():
    """NaN/inf in a bench input must refuse, not silently drop the oxide.

    Unfixed `_positive_finite` kept only the finite keys, so a poisoned
    composition renormalized as if the missing oxide had never been there.
    """
    healthy = {"SiO2": 50.0, "MgO": 25.0}
    assert benchmark._positive_finite(healthy) == healthy
    assert benchmark._positive_finite(
        {"SiO2": 50.0, "MgO": 0.0, "FeO": -1.0}
    ) == {"SiO2": 50.0}

    with pytest.raises(ValueError, match="non-finite") as nan_info:
        benchmark._positive_finite({"SiO2": 50.0, "Na2O": float("nan")})
    assert "Na2O" in str(nan_info.value)

    with pytest.raises(ValueError, match="non-finite") as inf_info:
        benchmark._positive_finite({"SiO2": 50.0, "MgO": float("inf")})
    assert "MgO" in str(inf_info.value)


def test_informational_residual_cannot_reach_scored_rmse_or_decision():
    """informational_residual_dex must not feed RMSE or the decision column.

    summarize_metrics / summarize_paired_decisions filter on residual_dex
    is not None and never inspect status. A residual placed in the normal
    field would silently promote the extrapolated tier.
    """

    scored = [
        {
            "point_id": "strict-1",
            "species": "SiO",
            "observable": "activity",
            "engine": "internal_analytic",
            "status": "ok",
            "residual_dex": 0.4,
        },
        {
            "point_id": "strict-1",
            "species": "SiO",
            "observable": "activity",
            "engine": "imcc-published",
            "status": "ok",
            "residual_dex": 0.1,
        },
    ]
    leaked = {
        "point_id": "extrap-1",
        "species": "SiO",
        "observable": "activity",
        "engine": "imcc-published",
        "status": "ok",
        "residual_dex": None,
        "informational_residual_dex": 3.5,
        "extrapolated": True,
        "envelope_status": "inside",
        "score": True,
    }
    rows = [*scored, leaked]

    metrics = benchmark.summarize_metrics(rows)
    imcc = next(row for row in metrics if row["engine"] == "imcc-published")
    assert imcc["scored_count"] == 1
    assert imcc["rmse_dex"] == pytest.approx(0.1)

    decisions = benchmark.summarize_paired_decisions(rows)
    assert len(decisions) == 1
    assert decisions[0]["paired_count"] == 1
    assert decisions[0]["engine_a"] == "imcc-published"
    assert decisions[0]["engine_a_rmse_dex"] == pytest.approx(0.1)

    polluted = dict(leaked)
    polluted["residual_dex"] = leaked["informational_residual_dex"]
    polluted_metrics = benchmark.summarize_metrics([*scored, polluted])
    polluted_imcc = next(
        row for row in polluted_metrics if row["engine"] == "imcc-published"
    )
    assert polluted_imcc["scored_count"] == 2
    polluted_decisions = benchmark.summarize_paired_decisions([*scored, polluted])
    assert polluted_decisions[0]["paired_count"] == 1
    # paired_count stays 1 because extrap-1 has no internal_analytic twin,
    # but RMSE would still move if residual_dex were set on a paired id.
    paired_polluted = dict(leaked)
    paired_polluted["point_id"] = "strict-1"
    paired_polluted["residual_dex"] = leaked["informational_residual_dex"]
    moved = benchmark.summarize_paired_decisions([*scored, paired_polluted])
    assert moved[0]["paired_count"] == 2
    assert moved[0]["engine_a_rmse_dex"] != pytest.approx(0.1)

    produced = benchmark.as_imcc_informational_row(
        {
            "point_id": "strict-1",
            "species": "SiO",
            "observable": "activity",
            "engine": "imcc-published",
            "status": "ok",
            "residual_dex": 3.5,
            "measured": 1.0,
            "prediction": 10 ** 3.5,
            "score": True,
        },
        extrapolated=True,
        envelope_status="inside",
    )
    assert produced["residual_dex"] is None
    assert produced["informational_residual_dex"] == pytest.approx(3.5)
    via_helper = benchmark.summarize_metrics([*scored, produced])
    helper_imcc = next(row for row in via_helper if row["engine"] == "imcc-published")
    assert helper_imcc["scored_count"] == 1
    assert helper_imcc["rmse_dex"] == pytest.approx(0.1)
    via_helper_decisions = benchmark.summarize_paired_decisions([*scored, produced])
    assert via_helper_decisions[0]["paired_count"] == 1
    assert via_helper_decisions[0]["engine_a_rmse_dex"] == pytest.approx(0.1)

    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    report = benchmark.generate_report(
        fixture,
        rows,
        [],
        [],
        extrapolated_rows=[leaked],
    )
    assert "## Per-species comparison" in report
    assert "| SiO | activity | imcc-published | 1 | 0.1 |" in report
    assert "computed-and-marked, not validated" in report
    assert "| extrap-1 | imcc-published |" in report
    assert "3.5" in report.split("## IMCC extrapolated tier")[1]


def test_extrapolated_tier_carries_both_marks_together():
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    wanted = {
        "yamaguchi1983_a_sio2_liquid_x0205_1373",
        "tsaplin2000_a_sio2_x0349_1373",
    }
    fixture = {
        **fixture,
        "points": [point for point in fixture["points"] if point["id"] in wanted],
    }
    engines = benchmark.build_engines(
        ("imcc-published",), fixture, alphamelts_timeout_s=1.0
    )

    strict = benchmark.run_points(fixture, engines)
    assert {row["status"] for row in strict} == {"out_of_domain"}
    assert all(row["residual_dex"] is None for row in strict)

    rows = benchmark.run_imcc_extrapolated_points(fixture, engines)
    assert len(rows) == 2
    envelopes = {row["envelope_status"] for row in rows}
    assert envelopes == {"inside", "outside_validated"}
    for row in rows:
        assert row["residual_dex"] is None
        assert row["informational_residual_dex"] is not None
        assert row["extrapolated"] is True
        assert row["envelope_status"] in {"inside", "outside_validated"}
        assert "extrapolated" in row
        assert "envelope_status" in row
        helper = benchmark.as_imcc_informational_row(
            {
                "point_id": row["point_id"],
                "engine": row["engine"],
                "measured": row["measured"],
                "prediction": row["prediction"],
                "residual_dex": row["informational_residual_dex"],
            },
            extrapolated=row["extrapolated"],
            envelope_status=row["envelope_status"],
        )
        assert helper["residual_dex"] is None
        assert helper["extrapolated"] is True
        assert helper["envelope_status"] == row["envelope_status"]

    with pytest.raises(ValueError, match="non-finite"):
        benchmark._normalize_wt({"SiO2": 50.0, "Na2O": float("nan")})

    renormalized = benchmark._normalize_wt({"SiO2": 25.0, "MgO": 25.0})
    assert renormalized["SiO2"] == pytest.approx(50.0)
    assert renormalized["MgO"] == pytest.approx(50.0)


def test_kinetic_observables_are_refused_not_scored_as_activities():
    """t-658: a Langmuir alpha must never be scored as an equilibrium activity.

    The two measurement kinds are not interconvertible. KEMS/effusion measures
    near-equilibrium vapour in a cell and yields activities. Langmuir free
    evaporation measures open-surface flux,

        J = alpha * P_eq * sqrt(M / (2*pi*R*T))

    where alpha, the evaporation coefficient (<= 1), is KINETIC. P_eq is
    therefore NOT recoverable from a Langmuir measurement without knowing
    alpha, so an alpha row carries no equilibrium activity to score against.
    Scoring one as if it did would fabricate agreement out of a unit mismatch.

    This is already true by construction: _prediction_for_point dispatches on
    a CLOSED allowlist -- activity, activity_coefficient, partial_pressure,
    evaporation_flux -- and everything else falls through to a typed refusal.
    The protection was correct but silent about WHY, so this test states the
    reason and fails if someone widens that allowlist to admit a kinetic
    observable.

    Real data makes this reachable rather than hypothetical:
    data/literature/extracts holds kems-037-richter-2002 and
    kems-005-fedkin-2006, both carrying observable 'alpha' on silicate-melt
    phases. Neither is in the bench set today.

    NOTE the converse is a separate concern with a separate mechanism: an
    equilibrium row must not be consumed as an alpha either. That direction is
    guarded on the rail side by the source_class/system_class provenance keys
    in simulator/evaporation_classes.py, not here.
    """

    ok_result = benchmark.EngineResult(
        status="ok",
        activities={"Na2O": 1.0e-3},
        gammas={"Na2O": 1.0e-3},
    )
    kinetic_point = {
        "observable": "alpha",
        "parent_oxide": "Na2O",
        "species": "Na",
        "temperature_K": 1873.15,
    }

    value, reason = benchmark._prediction_for_point(kinetic_point, ok_result)

    assert value is None, (
        "a kinetic alpha observable produced a scored value; equilibrium and "
        "kinetic measurements must not cross harnesses"
    )
    assert "unsupported observable" in reason and "alpha" in reason, reason

    # The control: the engine above CAN answer an equilibrium question, so the
    # refusal is about the observable kind and not about a dead result.
    activity_point = dict(kinetic_point, observable="activity")
    activity_value, activity_reason = benchmark._prediction_for_point(
        activity_point, ok_result
    )
    assert activity_value == pytest.approx(1.0e-3), activity_reason


def test_run_self_contradiction_fails_synthetic_run(tmp_path, monkeypatch):
    """Red-by-revert target: ok + unavailable for one engine fails the run.

    Status only. Reason text that never says unavailable/absent still
    flags, because the predicate does not read prose.
    """

    class _Contradict:
        name = "fake"

        def evaluate(self, composition_wt_pct, temperature_K, fO2_bar):
            del temperature_K, fO2_bar
            if float(composition_wt_pct.get("SiO2", 0.0)) >= 50.0:
                return benchmark.EngineResult(
                    status="ok",
                    activities={"SiO2": 0.5},
                    gammas={"SiO2": 1.0},
                )
            return benchmark.EngineResult(
                status="unavailable",
                reason="this host has no such engine installed",
            )

        def coverage(self, composition_wt_pct, temperature_K):
            del composition_wt_pct, temperature_K
            return benchmark.EngineResult(
                status="ok",
                details={"observable_family": "activity"},
            )

    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    compositions = dict(fixture["compositions"])
    high = next(
        point
        for point in fixture["points"]
        if compositions[point["composition_id"]]["composition_wt_pct"].get(
            "SiO2", 0.0
        )
        >= 50.0
    )
    low = next(
        point
        for point in fixture["points"]
        if compositions[point["composition_id"]]["composition_wt_pct"].get(
            "SiO2", 0.0
        )
        < 50.0
        and point["composition_id"] != high["composition_id"]
    )
    fixture = {
        **fixture,
        "points": [high, low],
        "composition_probes": fixture["composition_probes"][:1],
    }
    fixture_path = tmp_path / "tiny.yaml"
    fixture_path.write_text(yaml.safe_dump(fixture, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        benchmark,
        "build_engines",
        lambda names, fixture, alphamelts_timeout_s: [_Contradict()],
    )

    rows = [
        {"engine": "fake", "status": "ok", "reason": "computed"},
        {
            "engine": "fake",
            "status": "unavailable",
            "reason": "this host has no such engine installed",
        },
    ]
    assert benchmark.engines_with_ok_and_adapter_unavailable(rows) == ("fake",)
    with pytest.raises(benchmark.EngineSelfContradictionError, match="fake"):
        benchmark.assert_run_not_self_contradictory(rows)

    output_dir = tmp_path / "out"
    with pytest.raises(benchmark.EngineSelfContradictionError, match="fake"):
        benchmark.run_benchmark(
            bench_set_path=fixture_path,
            output_dir=output_dir,
            engine_names=("fake",),
            coverage_steps=2,
            live_vaporock_anchor_check=False,
        )
    report = (output_dir / "report.md").read_text(encoding="utf-8")
    assert "RUN INVALID: engine self-contradiction" in report
    metadata = json.loads((output_dir / "run-metadata.json").read_text())
    assert metadata["engine_self_contradiction"]["engines"] == ["fake"]


def test_alphamelts_activity_is_one_capability_refusal():
    """Default AlphaMeltsEngine does not invent per-point activity reasons."""

    engine = benchmark.AlphaMeltsEngine()
    first = engine.evaluate({"SiO2": 70.0, "Na2O": 30.0}, 1900.0, None)
    second = engine.evaluate(
        {"SiO2": 48.0, "Al2O3": 15.0, "FeO": 10.0, "MgO": 10.0, "CaO": 17.0},
        1700.0,
        1.0e-9,
    )
    expected = benchmark.alphamelts_activity_capability_refusal()
    assert first.status == "observable_unavailable"
    assert first.status == second.status
    assert first.reason == second.reason == expected.reason
    assert first.details == second.details == expected.details
    assert first.details["capability_refusal"] is True
    assert (
        first.details["limitation"]
        == benchmark.ALPHAMELTS_ACTIVITY_CAPABILITY_LIMITATION
    )
    assert first.details["scope"] == "all_compositions"
    assert engine._provider is None


def test_thermoengine_coverage_is_not_measured():
    engine = benchmark.ThermoEngineMeltActivityEngine()
    result = engine.coverage({"SiO2": 50.0, "MgO": 25.0, "FeO": 25.0}, 1900.0)
    assert result.status == "refused"
    assert result.details["not_measured"] is True
    assert result.details["reason_code"] == "coverage_not_measured_for_this_engine"
    assert result.reason == "coverage not measured for this engine"
    assert result.status != "ok"
    assert result.status != "unavailable"


def test_paired_decisions_include_any_both_ok_pair():
    rows = [
        {
            "point_id": "k1",
            "species": "K",
            "observable": "partial_pressure",
            "engine": "internal_analytic",
            "residual_dex": 0.4,
        },
        {
            "point_id": "k1",
            "species": "K",
            "observable": "partial_pressure",
            "engine": "vaporock",
            "residual_dex": 0.08,
        },
        {
            "point_id": "k1",
            "species": "K",
            "observable": "partial_pressure",
            "engine": "imcc-published",
            "residual_dex": 0.9,
        },
        {
            "point_id": "k2",
            "species": "K",
            "observable": "partial_pressure",
            "engine": "internal_analytic",
            "residual_dex": 0.42,
        },
        {
            "point_id": "k2",
            "species": "K",
            "observable": "partial_pressure",
            "engine": "vaporock",
            "residual_dex": 0.08,
        },
    ]
    decisions = benchmark.summarize_paired_decisions(rows)
    pairs = {(row["engine_a"], row["engine_b"]) for row in decisions}
    assert ("internal_analytic", "vaporock") in pairs
    assert ("imcc-published", "internal_analytic") in pairs
    assert ("imcc-published", "vaporock") in pairs
    vaporock_vs_internal = next(
        row
        for row in decisions
        if row["engine_a"] == "internal_analytic" and row["engine_b"] == "vaporock"
    )
    assert vaporock_vs_internal["paired_count"] == 2
    assert vaporock_vs_internal["decision"] == "vaporock"
    verdict = benchmark._paired_verdict(decisions)
    assert "n=" in verdict
    assert "n=1" in verdict or "n=2" in verdict


def test_retyped_crash_guard_is_engine_crash_not_out_of_domain():
    from simulator.melt_backend.alphamelts import (
        ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH,
        AlphaMELTSBackend,
    )

    backend = AlphaMELTSBackend()
    backend._mode = "subprocess"
    backend._binary_path = "/nonexistent/alphamelts"
    backend._timeout_s = 1.0
    backend._engine_version = "alphamelts-test-stub"
    result = backend._fe_free_absolute_fo2_crash_result(
        1400.0,
        1.0,
        -9.0,
        active_components=frozenset({"SiO2", "Na2O"}),
    )
    assert result.status != "out_of_domain"
    assert result.status == "not_converged"
    assert result.diagnostics["backend_failure_category"] == "engine_crash"
    assert (
        result.diagnostics["backend_status_reason"]
        == ALPHAMELTS_REASON_FE_FREE_ABSOLUTE_FO2_CRASH
    )
    assert result.diagnostics["subprocess_input_guard"]["predicate"] == (
        "fe_free_and_imposed_absolute_fo2"
    )
    assert result.diagnostics["subprocess_input_guard"]["not_the_predicate"] == (
        "no_Fe"
    )
    assert "two-component alkali-silica" not in " ".join(result.warnings)

    class _CrashGuardProvider:
        def dispatch(self, request):
            del request
            return SimpleNamespace(
                status="not_converged",
                warnings=result.warnings,
                diagnostic={"backend_diagnostics": dict(result.diagnostics)},
            )

    engine = benchmark.AlphaMeltsEngine()
    engine.activity_observable_supported = True
    engine._provider = _CrashGuardProvider()
    produced = engine.evaluate({"SiO2": 75.0, "Na2O": 25.0}, 1400.0, 1.0e-9)
    assert produced.status == "crash"
    assert produced.status != "out_of_domain"


def test_kume_expanded_set_completes_without_self_contradiction():
    """Full 292-point Kume set must finish; the guard must have nothing to fire on.

    Pre-fix the sequential adapter latched at s226 (parent-side omitted-fO2
    RuntimeError, not in the keep-handle set). s226 cached as refused;
    s227 and the remainder inherited status=unavailable after earlier ok
    rows, and assert_run_not_self_contradictory aborted the run.
    """
    fixture = benchmark.load_bench_set(benchmark.DEFAULT_BENCH_SET)
    kume_points = [
        point
        for point in fixture["points"]
        if str(point.get("composition_id") or "").startswith("kume2000_")
    ]
    assert len(kume_points) == 292
    fixture = {**fixture, "points": kume_points}

    engine = benchmark.ThermoEngineMeltActivityEngine(timeout_s=30.0)
    provider = engine._initialize()
    if provider is None:
        pytest.skip(f"ThermoEngine unavailable: {engine._initialization_error}")

    try:
        rows = benchmark.run_points(fixture, [engine])
        assert len(rows) == 292
        assert engine.transport_close_count() == 0
        benchmark.assert_run_not_self_contradictory(rows)
        unavailable = [row for row in rows if row["status"] == "unavailable"]
        assert unavailable == []
        s227 = [row for row in rows if "kume2000_s227" in str(row["point_id"])]
        assert s227
        assert all(row["status"] != "unavailable" for row in s227)
        # Unmapped MELTS endmembers: this fix must not invent CaO/MgO scores.
        for oxide in ("CaO", "MgO"):
            oxide_rows = [row for row in rows if row["parent_oxide"] == oxide]
            assert oxide_rows
            assert all(row.get("residual_dex") is None for row in oxide_rows)
            unavailable = [
                row
                for row in oxide_rows
                if row["status"] == "observable_unavailable"
            ]
            assert unavailable
            assert all(
                f"typed-refusal:melts_endmember_not_parent_oxide:{oxide}"
                in str(row["reason"])
                for row in unavailable
            )
    finally:
        backend = getattr(provider, "_backend", None)
        closer = getattr(backend, "close", None)
        if callable(closer):
            closer()
