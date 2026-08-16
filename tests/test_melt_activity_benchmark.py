"""Fast smoke tests for the tracked melt-activity benchmark harness."""

from __future__ import annotations

from types import SimpleNamespace

import json
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
        return benchmark.EngineResult(status="ok")


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
    assert {row["composition_id"] for row in rows} == {
        "sf04_tholeiite",
        "sf04_alkali_basalt",
        "sf04_komatiite",
        "sf04_dunite",
    }


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
    assert "computed activity under unmapped" in row["reason"]
    assert "Na2SiO3" in row["reason"]
    assert "no authoritative Na2O basis conversion" in row["reason"]


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
    assert decisions[0]["decision"] == "imcc-published"


def test_paired_verdict_does_not_call_win_plus_tie_better_on_every_group():
    decisions = [
        {"imcc_engine": "imcc-published", "decision": "imcc-published"},
        {"imcc_engine": "imcc-published", "decision": "tie"},
        {"imcc_engine": "imcc-ext", "decision": "internal_analytic"},
        {"imcc_engine": "imcc-ext", "decision": "tie"},
    ]

    verdict = benchmark._paired_verdict(decisions)

    assert "`imcc-published`: IMCC better or tied" in verdict
    assert "`imcc-ext`: internal_analytic better or tied" in verdict


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
    engine._provider = _Provider()
    result = engine.evaluate({"SiO2": 50.0, "MgO": 50.0}, 1900.0, 1.0e-9)

    assert result.status == "crash"
    assert "SIGSEGV" in result.reason


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
    assert "Na2SiO3" in row["reason"]
