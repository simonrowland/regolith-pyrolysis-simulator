from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from scripts import source_battery_report
from simulator.battery.enums import (
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    PerBasis,
    Phase,
    Quantity,
    ResidualStatus,
)
from simulator.battery.records import Execution, State
from simulator.battery.score import ScoreContext, refusal_census
from tests.battery import factories as F


def test_landed_richter_source_report_known_counts_and_refusal_reason_tally(
    tmp_path: Path, monkeypatch
) -> None:
    scored_residuals = []
    production_score_store = source_battery_report.score_store
    production_load_score_context = source_battery_report.load_score_context
    loaded_sources = []

    def capture_context_load(root=None, *, sources=None):
        loaded_sources.append(sources)
        return production_load_score_context(root, sources=sources)

    def capture_score(context, *, engines=None, work_id=None):
        residuals, candidates = production_score_store(
            context, engines=engines, work_id=work_id
        )
        scored_residuals.extend(residuals)
        return residuals, candidates

    monkeypatch.setattr(
        source_battery_report, "load_score_context", capture_context_load
    )
    monkeypatch.setattr(source_battery_report, "score_store", capture_score)
    report = source_battery_report.build_source_report(
        "kems-010-richter-2007",
        scratch_out_dir=tmp_path / "source-report",
    )

    assert report["extract_rows"] == 197
    assert report["migrated_observations"] == 350
    assert report["candidates"]["admitted"]["count"] == 254
    assert report["candidates"]["pending"]["count"] == 85
    assert report["hard_issue_count"] == 0
    assert report["scoring"]["engine_observation_events"] == 2712
    assert None in loaded_sources
    assert ("kems-010-richter-2007",) in loaded_sources
    overlay_store = (
        Path(report["scratch_out_dir"])
        / "score-context/data/literature/extracts-v2/kems-010-richter-2007.yaml"
    )
    assert overlay_store.is_file()
    assert not overlay_store.is_symlink()
    assert report["scoring"]["numeric_residuals"] == {
        "distinct_observations": 0,
        "engine_observation_events": 0,
    }
    assert report["scoring"]["score_eligible"] == {
        "distinct_observations": 0,
        "engine_observation_events": 0,
    }
    assert report["scoring"]["inside_band"] == {
        "distinct_observations": 0,
        "engine_observation_events": 0,
    }

    expected_refusals: Counter[str] = Counter()
    expected_detail_reasons: Counter[tuple[str, str | None]] = Counter()
    for key, count in refusal_census(scored_residuals).items():
        reason, separator, detail_reason = key.partition(":")
        expected_refusals[reason] += count
        expected_detail_reasons[(reason, detail_reason if separator else None)] += count
    assert expected_refusals
    actual_refusals = {
        row["reason"]: (row["distinct_observations"], row["engine_observation_events"])
        for row in report["scoring"]["refusals_by_reason"]
    }
    assert actual_refusals == {
        "attempted_unavailable": (21, 21),
        "identity_incomplete": (5, 10),
        "identity_unknown": (85, 680),
        "underdetermined_apparatus": (45, 69),
        "unsupported": (254, 1932),
    }
    assert all(
        len(row["example_observation_ids"]) == 3
        for row in report["scoring"]["refusals_by_reason"]
    )
    # The exact reason tally makes omitting any refusal reason fail this test.
    actual_refusal_counts = {
        row["reason"]: row["engine_observation_events"]
        for row in report["scoring"]["refusals_by_reason"]
    }
    assert actual_refusal_counts == dict(expected_refusals)
    actual_detail_reasons = {
        (row["reason"], detail["reason"]): detail["engine_observation_events"]
        for row in report["scoring"]["refusals_by_reason"]
        for detail in row["detail_reasons"]
    }
    assert actual_detail_reasons == dict(expected_detail_reasons)

    dropped_reason = next(iter(expected_refusals))

    def tally_without_one_reason(residuals):
        return {
            key: count
            for key, count in refusal_census(residuals).items()
            if key.partition(":")[0] != dropped_reason
        }

    monkeypatch.setattr(
        source_battery_report, "refusal_census", tally_without_one_reason
    )
    mutated_refusals = {
        row["reason"]: (row["distinct_observations"], row["engine_observation_events"])
        for row in source_battery_report._score_rows(scored_residuals)[
            "refusals_by_reason"
        ]
    }
    with pytest.raises(AssertionError):
        assert mutated_refusals == {
            "attempted_unavailable": (21, 21),
            "identity_incomplete": (5, 10),
            "identity_unknown": (85, 680),
            "underdetermined_apparatus": (45, 69),
            "unsupported": (254, 1932),
        }


def test_landed_extract_without_rows_is_reported_explicitly(tmp_path: Path) -> None:
    report = source_battery_report.build_source_report(
        "yang-2025-na2o-cao-silica-thermodynamics",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        scratch_out_dir=tmp_path / "empty-source-report",
    )

    assert report["extract_rows"] == 0
    assert report["extract_has_rows"] is False
    assert "extract has no rows" in source_battery_report._markdown(report)


def test_source_scoring_reconciles_full_context_cross_source_numeric_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import simulator.battery.score as score_module

    source_id = "report-target"
    experiment = F.tabulation_experiment("report-band-experiment", "report-work")
    work = F.work("report-work")
    identity = F.oxide_identity(
        "MgO",
        Phase.CR,
        T_K=Decimal("1234.567"),
        per=PerBasis.MOL_SPECIES,
        metal_formula="Mg",
        nu_metal=Fraction(1),
        nu_o2=Fraction(1, 2),
    )
    identity = replace(
        identity,
        quantity=Quantity.CP,
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    observations = tuple(
        F.observation(
            f"{source}::report-band-point",
            experiment.experiment_id,
            identity,
            Decimal(value),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            source_id=source,
        )
        for source, value in (
            (source_id, "10"),
            ("independent-a", "9"),
            ("independent-b", "11"),
        )
    )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row for row in observations},
        hostname="test",
    )

    def predict(engine, observation, **_kwargs):
        return score_module.EnginePrediction(
            engine=engine,
            channel=score_module.ENGINE_CHANNELS[engine],
            execution=Execution(
                state=ExecutionState.PRODUCED, call_evidence="test prediction"
            ),
            value=Decimal("10"),
            unit=score_module.QUANTITY_UNITS[Quantity.CP],
            authority=Authority.CERTIFIED,
            coefficient_sources=("test",),
            lineage_complete=True,
            identity=observation.identity,
        )

    production_score_store = score_module.score_store

    def score_with_fixture_prediction(context, *, engines=None, work_id=None):
        return production_score_store(
            context,
            engines=engines,
            work_id=work_id,
            predict=predict,
        )

    monkeypatch.setattr(
        source_battery_report, "score_store", score_with_fixture_prediction
    )
    full_residuals, _ = production_score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=predict,
    )
    target_observation_ids = {
        row.observation_id
        for row in observations
        if row.source_id == source_id
    }
    expected = tuple(
        row for row in full_residuals if row.reference in target_observation_ids
    )
    candidates, reported = source_battery_report._score_source(
        context, source_id, engines=(Engine.INTERNAL_ANALYTICAL,)
    )
    assert candidates == ()
    assert reported == expected
    assert len(reported) == 1
    residual = reported[0]
    assert residual.numeric is not None
    assert residual.numeric.decision_band is not None
    assert "n_sources=3" in residual.numeric.decision_band.rule
    assert residual.status is ResidualStatus.MATCH
    assert source_battery_report._score_rows(reported) == source_battery_report._score_rows(
        expected
    )

    source_only_context = replace(
        context,
        observations={
            observation_id: observation
            for observation_id, observation in context.observations.items()
            if observation.source_id == source_id
        },
    )
    source_only, _ = production_score_store(
        source_only_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        work_id=source_id,
        predict=predict,
    )
    assert len(source_only) == 1
    assert source_only[0].numeric is not None
    assert source_only[0].numeric.decision_band is None


def test_json_cli_keeps_progress_out_of_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def report_with_progress(_source_id, *, engines=None):
        print("score progress 1/1 residuals")
        return {"source_id": "fixture-source"}

    monkeypatch.setattr(
        source_battery_report, "build_source_report", report_with_progress
    )
    assert source_battery_report.main(["fixture-source", "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"source_id": "fixture-source"}
    assert "score progress 1/1 residuals" in captured.err
