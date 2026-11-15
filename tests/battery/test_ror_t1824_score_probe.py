from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    Engine,
    EvidenceClass,
    QuotedTargetKind,
    RefusalReason,
    SourceRelation,
)
from simulator.battery.records import QuotedFrom
from simulator.battery.score import ScoreContext, comparison_candidates, resolve_source_relation, score_store
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
