from __future__ import annotations

import json
import shutil
from inspect import signature

import pytest

from simulator.corpus_paths import regolith_corpus_root
from simulator.diagnostic_helpers.reproduction_compare import (
    SCORING_STATUSES,
    TimeSeriesComparisonRefusal,
    records_to_json,
)
from simulator.diagnostic_helpers.sauerborn_2005 import (
    SAUERBORN_FIG555_FILES,
    SauerbornCorpusError,
    compare_sauerborn_o2_partial_pressure_time_series,
    load_sauerborn_fig555_path_series,
)
from simulator.core import Atmosphere
from simulator.lab_schedule import (
    LAB_SCHEDULE_DEFAULT_GAS_BOUNDARY,
    LabScheduleValidationError,
    normalize_lab_schedule,
)
from simulator.runner import PyrolysisRun


def _require_corpus_files() -> None:
    corpus_root = regolith_corpus_root()
    missing = [
        str(corpus_root / relative_path)
        for relative_path, _ in SAUERBORN_FIG555_FILES.values()
        if not (corpus_root / relative_path).is_file()
    ]
    if missing:
        pytest.skip(
            "Sauerborn Fig. 5.55 corpus files are missing: "
            f"{', '.join(missing)}"
        )


def test_reader_loads_cao_path_without_subtracting_blank() -> None:
    _require_corpus_files()
    series = load_sauerborn_fig555_path_series()

    assert set(series) == {"CaO", "SiO2", "ZnO", "ohne_Probe"}
    cao_path = series["CaO"]
    assert all(row["point_type"].startswith("path_") for row in cao_path)

    flat_top = [row for row in cao_path if 65.0 <= row["time_s"] <= 65.6]
    assert flat_top
    assert min(row["time_s"] for row in flat_top) == pytest.approx(65.02, abs=0.01)
    assert max(row["time_s"] for row in flat_top) == pytest.approx(65.59, abs=0.01)
    assert all(
        row["value_mbar"] == pytest.approx(8.23e-6, rel=0.001)
        for row in flat_top
    )
    blank_peak = max(row["value_mbar"] for row in series["ohne_Probe"])
    assert blank_peak < flat_top[0]["value_mbar"]
    # Provenance gives about 6.92e-6 mbar at 68 s. This check uses the first
    # path vertex after 68 s, at 68.448943 s, rather than an interpolated value.
    after_68 = next(row for row in cao_path if row["time_s"] > 68.0)
    assert after_68["time_s"] == pytest.approx(68.448943, abs=0.001)
    assert after_68["value_mbar"] < flat_top[0]["value_mbar"]


@pytest.mark.parametrize("trace", tuple(SAUERBORN_FIG555_FILES))
def test_reader_rejects_a_changed_corpus_sha256(tmp_path, trace: str) -> None:
    _require_corpus_files()
    source_root = regolith_corpus_root()
    for relative_path, _ in SAUERBORN_FIG555_FILES.values():
        source = source_root / relative_path
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    relative_path, _ = SAUERBORN_FIG555_FILES[trace]
    mutated_path = tmp_path / relative_path
    mutated_path.write_bytes(mutated_path.read_bytes() + b"\n")

    with pytest.raises(SauerbornCorpusError, match=f"SHA-256 mismatch for {trace}"):
        load_sauerborn_fig555_path_series(tmp_path)


def test_sauerborn_comparison_refuses_missing_runtime_pressure_series() -> None:
    assert "argon_flow_nl_min" not in signature(
        compare_sauerborn_o2_partial_pressure_time_series
    ).parameters

    with pytest.raises(TimeSeriesComparisonRefusal) as refusal:
        compare_sauerborn_o2_partial_pressure_time_series(
            case_id="sauerborn_2005_fig555",
            source_id="kems-035-sauerborn-2005",
            observable_id="sauerborn_o2_partial_pressure_time_series",
            observation_points=(),
            runtime_pressure_samples=None,
            pre_high_temperature_base_mbar=1e-7,
            evidence_scope="QMS-cross O2 partial pressure",
            source_locator={"figure": "5.55"},
            recipe={},
            observation={},
            runtime={},
        )

    assert refusal.value.reason_code == "qms_po2_series_missing"


def _sauerborn_blank_schedule() -> dict[str, object]:
    return {
        "id": "sauerborn_blank_pressure_floor",
        "duration_h": 1.0,
        "interpolation": "piecewise_linear",
        "interpolation_source_class": "literature_sidecar",
        "furnace_ceiling_C": 1300.0,
        "melt_temperature_C": [
            {"t_h": time_h, "value": 500.0, "unit": "C"}
            for time_h in (0.0, 1.0)
        ],
        "chamber_pressure_mbar": [
            {"t_h": time_h, "value": 1.7e-4, "unit": "mbar"}
            for time_h in (0.0, 1.0)
        ],
    }


def test_blank_chamber_pressure_below_floor_runs_with_default_boundary() -> None:
    # 08e7129868 made the sub-floor schedule a domain notice; d-460 says a
    # printed chamber pressure remains callable with the vacuum/free-molecular
    # default when the source does not print a gas boundary.
    schedule = _sauerborn_blank_schedule()
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=1,
        mass_kg=1.0,
        backend_name="internal-analytical",
        runtime_campaign_overrides={"C2A": {"lab_schedule": schedule}},
        force_builtin_vapor_pressure=True,
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()

    assert session.simulator.melt.atmosphere is Atmosphere.HARD_VACUUM
    assert session.simulator.melt.p_total_mbar == pytest.approx(1.7e-4)
    assert session.simulator.melt.background_gas_species == ""
    runtime_schedule = session.simulator.campaign_mgr._lab_schedule(
        session.simulator.melt.campaign
    )
    assert runtime_schedule["gas_boundary"]["default_boundary"] == (
        LAB_SCHEDULE_DEFAULT_GAS_BOUNDARY
    )

    result = run._run_session(session)
    assert result["status"] == "ok"
    enforcement = result["per_hour_summary"][0]["pO2_enforcement"]
    pressure_notices = enforcement["pressure_domain_notices"]
    assert len(pressure_notices) == 2
    assert all(
        notice["reason"] == "lab_schedule_pressure_below_implemented_floor"
        for notice in pressure_notices
    )
    gas_boundary_notices = enforcement["gas_boundary_notices"]
    assert len(gas_boundary_notices) == 2
    assert all(
        notice["reason"] == "lab_schedule_gas_boundary_default_unprinted"
        and notice["pressure_mbar"] == pytest.approx(1.7e-4)
        and notice["default_boundary"] == LAB_SCHEDULE_DEFAULT_GAS_BOUNDARY
        for notice in gas_boundary_notices
    )


def test_missing_gas_boundary_without_printed_pressure_still_refuses() -> None:
    schedule = _sauerborn_blank_schedule()
    schedule.pop("chamber_pressure_mbar")

    with pytest.raises(
        LabScheduleValidationError,
        match="lab_schedule_missing_gas_boundary",
    ):
        normalize_lab_schedule(schedule)


def _assert_sauerborn_trace_comparison_uses_base_normalization(
    trace_name: str,
    feature_time_s: float,
    feature_value_mbar: float,
) -> None:
    _require_corpus_files()
    trace_path = load_sauerborn_fig555_path_series()[trace_name]
    baseline_point = trace_path[0]
    feature_point = min(
        trace_path,
        key=lambda point: abs(point["time_s"] - feature_time_s),
    )
    assert feature_point["time_s"] == pytest.approx(feature_time_s, abs=0.001)
    assert feature_point["value_mbar"] == pytest.approx(
        feature_value_mbar,
        rel=1e-9,
    )

    synthetic_runtime_pressures = (
        {"time_s": baseline_point["time_s"], "value_mbar": 2e-7},
        {"time_s": feature_point["time_s"], "value_mbar": 4e-4},
    )
    evidence_scope = (
        "Fig. 5.55 digitization bounds only; synthetic runtime residual; "
        "no per-point instrument uncertainty; residual not graded"
    )
    records = compare_sauerborn_o2_partial_pressure_time_series(
        case_id="sauerborn_2005_fig555",
        source_id="kems-035-sauerborn-2005",
        observable_id="sauerborn_o2_partial_pressure_time_series",
        observation_points=(baseline_point, feature_point),
        runtime_pressure_samples=synthetic_runtime_pressures,
        pre_high_temperature_base_mbar=2e-7,
        evidence_scope=evidence_scope,
        source_locator={"figure": "5.55", "trace": trace_name},
        recipe={},
        observation={
            "trace": trace_name,
            "points": (baseline_point, feature_point),
        },
        runtime={"origin": "synthetic", "samples": synthetic_runtime_pressures},
    )

    assert len(records) == 2
    assert [record.units for record in records] == ["mbar", "mbar"]
    assert [record.actual_value for record in records] == pytest.approx(
        [1e-7, 2e-4]
    ), "comparison failed: runtime pressures must use base normalization"
    assert records[0].coordinate["time_s"] == pytest.approx(
        baseline_point["time_s"]
    )
    assert records[1].coordinate["time_s"] == pytest.approx(feature_point["time_s"])
    assert records[1].expected_value == feature_point["value_mbar"]
    assert records[1].residual == pytest.approx(
        2e-4 - feature_point["value_mbar"]
    )
    assert all(record.expected_uncertainty is None for record in records)
    assert all(record.evidence_scope == evidence_scope for record in records)
    assert all(record.status == "assumed-input" for record in records)
    assert records[1].source_locator == {
        "figure": "5.55",
        "trace": trace_name,
        "point_type": feature_point["point_type"],
        "time_half_width_s": feature_point["time_half_width_s"],
        "digitization_bounds_mbar": {
            "lower": feature_point["value_lower_mbar"],
            "upper": feature_point["value_upper_mbar"],
        },
    }


def test_sauerborn_comparison_scales_from_base_before_pointwise_compare() -> None:
    _assert_sauerborn_trace_comparison_uses_base_normalization(
        "ZnO",
        75.879726,
        1.1218782056e-4,
    )


def test_sauerborn_comparison_preserves_source_seconds_exactly() -> None:
    _require_corpus_files()
    traces = load_sauerborn_fig555_path_series()
    point = next(
        row
        for rows in traces.values()
        for row in rows
        if row["time_s"] == 30.401905
    )
    records = compare_sauerborn_o2_partial_pressure_time_series(
        case_id="sauerborn_2005_fig555",
        source_id="kems-035-sauerborn-2005",
        observable_id="sauerborn_o2_partial_pressure_time_series",
        observation_points=(point,),
        runtime_pressure_samples=(
            {"time_s": point["time_s"], "value_mbar": point["value_mbar"]},
        ),
        pre_high_temperature_base_mbar=1e-7,
        evidence_scope="Fig. 5.55 digitization bounds only; ungraded residual",
        source_locator={"figure": "5.55"},
        recipe={},
        observation={"point": point},
        runtime={"origin": "synthetic"},
    )

    assert records[0].coordinate["time_s"] == 30.401905
    serialized = json.loads(records_to_json(records))
    assert serialized[0]["temperature/time/window"]["time_s"] == 30.401905
    assert records[0].expected_uncertainty is None
    assert records[0].status not in SCORING_STATUSES


def test_sauerborn_comparison_handoff_reads_pinned_cao_path() -> None:
    _assert_sauerborn_trace_comparison_uses_base_normalization(
        "CaO",
        65.019351,
        8.2282872826e-6,
    )
