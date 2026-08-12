"""Fast smoke tests for the tracked melt-activity benchmark harness."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from benchmarks import melt_activity_benchmark as benchmark


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
    assert (output_dir / "reference-anchor-results.csv").is_file()
    assert result["metadata"]["reference_anchor"]["shared_magma_count"] == 288
    assert "Literal SF04 basalt empirical points: **0**" in (
        output_dir / "report.md"
    ).read_text(encoding="utf-8")
    assert "AlphaMELTS was not selected" in (
        output_dir / "report.md"
    ).read_text(encoding="utf-8")


def test_mode_specific_run_removes_stale_incompatible_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        benchmark,
        "build_engines",
        lambda names, fixture, alphamelts_timeout_s: [_FakeActivityEngine()],
    )
    output_dir = tmp_path / "out"
    benchmark.run_benchmark(
        output_dir=output_dir,
        engine_names=("fake",),
        coverage_steps=3,
    )

    benchmark.run_benchmark(
        output_dir=output_dir,
        engine_names=("fake",),
        mode="coverage",
        coverage_steps=3,
    )

    assert (output_dir / "coverage-map.csv").is_file()
    assert not (output_dir / "benchmark-results.csv").exists()
    assert not (output_dir / "composition-probes.csv").exists()
    assert not (output_dir / "reference-anchor-results.csv").exists()
    assert not (output_dir / "live-vaporock-check.csv").exists()
    assert not (output_dir / "report.md").exists()


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
