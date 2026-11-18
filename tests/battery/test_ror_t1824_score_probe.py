from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    Engine,
    EvidenceClass,
    QUANTITY_UNITS,
    QuotedTargetKind,
    RefusalReason,
    SourceRelation,
)
from simulator.battery.records import QuotedFrom
from simulator.battery import score as score_module
from simulator.battery.score import (
    ProprietaryProduced,
    ScoreContext,
    _duplicate_observation_refusal_detail,
    comparison_candidates,
    compile_residual,
    resolve_source_relation,
    score_proprietary_aggregate,
    score_store,
)
from simulator.battery.validate import _quoted_source_observation_ids
from tests.battery import factories as F
from tests.battery.test_score import _predict


def _score_context(target_kind: QuotedTargetKind) -> tuple[ScoreContext, str, str]:
    work = F.work()
    primary_experiment = F.tabulation_experiment("primary-experiment")
    target_id = (
        primary_experiment.experiment_id
        if target_kind is QuotedTargetKind.EXPERIMENT
        else "primary-observation"
    )
    quoted_experiment = replace(
        F.tabulation_experiment("quoted-experiment"),
        quoted_from=QuotedFrom(target_kind, target_id),
    )
    identity = F.o2_identity()
    primary = F.observation(
        "primary-observation",
        primary_experiment.experiment_id,
        identity,
        Decimal("-100"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
        source_id="primary-source",
    )
    quoted = F.observation(
        "quoted-observation",
        quoted_experiment.experiment_id,
        identity,
        Decimal("-100"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
        source_id="quoted-source",
    )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={
            primary_experiment.experiment_id: primary_experiment,
            quoted_experiment.experiment_id: quoted_experiment,
        },
        observations={
            primary.observation_id: primary,
            quoted.observation_id: quoted,
        },
        extract_review={"primary-source": "reviewed", "quoted-source": "reviewed"},
        hostname="test",
    )
    return context, primary.observation_id, quoted.observation_id


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_citation_edges_make_the_quoted_observation_dependent(
    target_kind: QuotedTargetKind,
) -> None:
    context, primary_id, quoted_id = _score_context(target_kind)
    quoted = context.observations[quoted_id]

    assert resolve_source_relation(
        quoted,
        (primary_id,),
        True,
        works=context.works,
        observations=context.observations,
        experiments=context.experiments,
    ) is SourceRelation.SAME_INPUT


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_quoted_twin_is_not_scored_as_an_independent_measurement(
    target_kind: QuotedTargetKind,
) -> None:
    context, primary_id, quoted_id = _score_context(target_kind)
    quoted = context.observations[quoted_id]

    assert quoted_id not in {
        observation.observation_id
        for observation in comparison_candidates(context)
    }
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        predict=lambda _engine, point, **_kwargs: _predict(
            point.value.point,
            point.identity,
            coefficient_sources=(primary_id,),
        ),
    )

    assert {
        residual.reference for residual in residuals if residual.numeric is not None
    } == {primary_id}
    quoted_residuals = [
        residual for residual in residuals if residual.reference == quoted_id
    ]
    assert len(quoted_residuals) == 1
    assert quoted_residuals[0].numeric is None
    assert quoted_residuals[0].refusal is not None
    assert quoted_residuals[0].refusal.reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert quoted_residuals[0].source_relation is SourceRelation.SAME_INPUT


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_compile_residual_suppresses_quoted_twin(target_kind: QuotedTargetKind) -> None:
    context, primary_id, quoted_id = _score_context(target_kind)
    primary = context.observations[primary_id]
    quoted = context.observations[quoted_id]

    primary_residual, _ = compile_residual(
        primary,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(
            primary.value.point,
            primary.identity,
            coefficient_sources=(primary_id,),
        ),
    )
    quoted_residual, _ = compile_residual(
        quoted,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        prediction=_predict(
            quoted.value.point,
            quoted.identity,
            coefficient_sources=(primary_id,),
        ),
    )

    assert primary_residual.numeric is not None
    assert quoted_residual.numeric is None
    assert quoted_residual.refusal is not None
    assert quoted_residual.refusal.reason is RefusalReason.REFERENTIAL_INTEGRITY
    assert quoted_residual.source_relation is SourceRelation.SAME_INPUT


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_quoted_citation_refusal_uses_validator_resolution(
    target_kind: QuotedTargetKind,
) -> None:
    context, _primary_id, quoted_id = _score_context(target_kind)
    quoted = context.observations[quoted_id]
    quoted_experiment = context.experiments[quoted.experiment_id]
    experiments = dict(context.experiments)
    observations = dict(context.observations)

    if target_kind is QuotedTargetKind.EXPERIMENT:
        experiments.pop("primary-experiment")
    else:
        primary = observations.pop("primary-observation")
        renamed_primary = replace(
            primary, observation_id="primary-source::primary-observation"
        )
        observations[renamed_primary.observation_id] = renamed_primary

    assert (
        _quoted_source_observation_ids(
            quoted_experiment, observations, experiments
        )
        is None
    )
    mismatched_context = replace(
        context, experiments=experiments, observations=observations
    )
    detail = _duplicate_observation_refusal_detail(
        mismatched_context, mismatched_context.observations[quoted_id]
    )

    assert detail is not None
    assert detail["resolution"] == "primary_missing"


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_proprietary_aggregate_excludes_quoted_twin(
    target_kind: QuotedTargetKind,
) -> None:
    context, primary_id, quoted_id = _score_context(target_kind)
    identity = F.psat_identity("Fe")
    observations = {
        observation_id: replace(
            context.observations[observation_id],
            identity=identity,
            value=replace(context.observations[observation_id].value, point=Decimal("1")),
        )
        for observation_id in (primary_id, quoted_id)
    }
    aggregate_context = replace(context, observations=observations)

    class Provider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def score(self, request):
            self.calls.append(request.observation_id)
            return ProprietaryProduced(
                observation_id=request.observation_id,
                quantity=request.quantity,
                scalar=Decimal("1"),
                unit=QUANTITY_UNITS[request.quantity],
                engine_version="test-version",
                datafile_fingerprint="sha256:test-data",
            )

    provider = Provider()
    aggregates = score_proprietary_aggregate(
        aggregate_context,
        engine=Engine.PROPRIETARY_REMOTE,
        observation_ids=(primary_id, quoted_id),
        provider=provider,
    )

    assert len(aggregates) == 1
    assert aggregates[0].n == 1
    assert dict(aggregates[0].refusal_counts_by_code) == {
        RefusalReason.REFERENTIAL_INTEGRITY.value: 1
    }
    assert provider.calls == [primary_id]


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_scoring_routes_detect_removed_duplicate_suppression(
    monkeypatch, target_kind: QuotedTargetKind
) -> None:
    monkeypatch.setattr(
        score_module, "_duplicate_observation_refusal_detail", lambda *_args: None
    )

    with pytest.raises(AssertionError):
        test_compile_residual_suppresses_quoted_twin(target_kind)
    with pytest.raises(AssertionError):
        test_proprietary_aggregate_excludes_quoted_twin(target_kind)


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_resolution_probe_detects_an_independent_scorer_resolver(
    monkeypatch, target_kind: QuotedTargetKind
) -> None:
    monkeypatch.setattr(
        score_module,
        "_quoted_source_observation_ids",
        lambda *_args: ("primary-source::primary-observation",),
    )

    with pytest.raises(AssertionError):
        test_quoted_citation_refusal_uses_validator_resolution(target_kind)
