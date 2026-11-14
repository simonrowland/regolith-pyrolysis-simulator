from __future__ import annotations

import json
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
    Polymorph,
    Quantity,
    ResidualStatus,
)
from simulator.battery.records import Execution, State
from simulator.battery.score import ScoreContext, refusal_census
from tests.battery import factories as F


def test_oxide_identity_missing_crystal_polymorph_is_typed_unknown() -> None:
    polymorph = F.oxide_identity("MgO", Phase.CR).species.polymorph

    assert polymorph is not None
    assert polymorph.is_unknown
    assert polymorph.reason == "test factory did not specify crystal polymorph"


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
            context, engines=engines, work_id=work_id, limit=2
        )
        scored_residuals.extend(residuals)
        return residuals, candidates

    monkeypatch.setattr(
        source_battery_report, "load_score_context", capture_context_load
    )
    monkeypatch.setattr(source_battery_report, "score_store", capture_score)
    report = source_battery_report.build_source_report(
        "kems-010-richter-2007",
        engines=(Engine.INTERNAL_ANALYTICAL,),
        scratch_out_dir=tmp_path / "source-report",
    )

    assert report["extract_rows"] == 197
    assert report["migrated_observations"] == 350
    assert report["candidates"]["admitted"]["count"] == 254
    assert report["candidates"]["pending"]["count"] == 85
    assert report["hard_issue_count"] == 0
    # score_store sorts the source references by id; these are its first two.
    # Two scalar references × one IA engine = 2 events; both unknown quantities
    # produce one identity_unknown:quantity_unknown refusal each.
    expected_ids = [
        "kems-010-richter-2007::richter_2007_al_quoted_non_loss_bound",
        "kems-010-richter-2007::richter_2007_ca_quoted_non_loss_bound",
    ]
    assert [row.reference for row in scored_residuals] == expected_ids
    assert report["scoring"]["engine_observation_events"] == 2
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

    expected_refusals = {
        "identity_unknown:quantity_unknown": 2,
    }
    assert refusal_census(scored_residuals) == expected_refusals
    assert report["scoring"]["refusals_by_reason"] == [
        {
            "reason": "identity_unknown",
            "distinct_observations": 2,
            "engine_observation_events": 2,
            "example_observation_ids": expected_ids,
            "detail_reasons": [
                {
                    "reason": "quantity_unknown",
                    "engine_observation_events": 2,
                    "example_observation_ids": expected_ids,
                }
            ],
        }
    ]

    monkeypatch.setattr(source_battery_report, "refusal_census", lambda _rows: {})
    mutated_refusals = {
        row["reason"]: (row["distinct_observations"], row["engine_observation_events"])
        for row in source_battery_report._score_rows(scored_residuals)[
            "refusals_by_reason"
        ]
    }
    assert mutated_refusals == {}
    with pytest.raises(AssertionError):
        assert mutated_refusals == {"identity_unknown": (2, 2)}


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
        polymorph=Polymorph.PERICLASE.value,
    )
    identity = replace(
        identity,
        quantity=Quantity.CP,
        reaction=State.not_applicable("not a formation quantity"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    assert identity.species.polymorph.value == Polymorph.PERICLASE.value
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
    # Three named source observations × one IA event each; the report keeps
    # the target's single numeric, in-band event from that full-context run.
    assert len(full_residuals) == 3
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
    source_counts = source_battery_report._score_rows(reported)
    assert source_counts["engine_observation_events"] == 1
    assert source_counts["numeric_residuals"] == {
        "distinct_observations": 1,
        "engine_observation_events": 1,
    }
    assert source_counts["inside_band"] == {
        "distinct_observations": 1,
        "engine_observation_events": 1,
    }

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
