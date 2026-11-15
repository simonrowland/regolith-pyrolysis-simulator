from __future__ import annotations

import inspect
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmountBasis,
    MethodToken,
    QuotedTargetKind,
    RefusalReason,
)
from simulator.battery.records import Composition, QuotedFrom, State
from simulator.battery import validate as V
from simulator.battery.validate import validate_corpus
from tests.battery import factories as F
from tests.battery import test_quoted_from as Q


def _composition(sio2: str, mgo: str) -> Composition:
    return Composition(
        basis="oxide_mole_fraction",
        components=(("SiO2", Decimal(sio2)), ("MgO", Decimal(mgo))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )


def _quoted_records(target_kind: QuotedTargetKind):
    primary = F.tabulation_experiment("primary-experiment")
    primary = replace(
        primary,
        sample=replace(
            primary.sample,
            initial_composition=F.located(_composition("0.6", "0.4")),
        ),
    )
    primary_observation = F.observation(
        "primary-observation",
        primary.experiment_id,
        F.o2_identity(),
        Decimal("1"),
    )
    target_id = (
        primary.experiment_id
        if target_kind is QuotedTargetKind.EXPERIMENT
        else primary_observation.observation_id
    )
    quoted = Q._quoted_experiment(
        QuotedFrom(target_kind, target_id),
        composition=_composition("0.4", "0.6"),
    )
    quoted = replace(quoted, method=State.of(MethodToken.STATIC_EQUILIBRIUM))
    return primary, primary_observation, quoted


@pytest.mark.parametrize("target_kind", tuple(QuotedTargetKind))
def test_inherited_values_must_match_the_cited_primary(
    target_kind: QuotedTargetKind,
) -> None:
    primary, primary_observation, quoted = _quoted_records(target_kind)
    report = validate_corpus(
        [F.work()],
        [primary, quoted],
        [primary_observation],
    )

    issues = {
        issue.path: issue
        for issue in report.hard_issues
        if issue.reason is RefusalReason.CONDITIONAL_FIELD
    }
    assert issues["experiment[quoted-experiment].method_inherited_from"]
    assert issues[
        "experiment[quoted-experiment].sample.initial_composition_inherited_from"
    ]


@pytest.mark.parametrize(
    ("target_kind", "target_id"),
    [
        (QuotedTargetKind.EXPERIMENT, "quoted-experiment"),
        (QuotedTargetKind.OBSERVATION, "self-observation"),
    ],
)
def test_quoted_from_rejects_the_quoting_experiments_own_records(
    target_kind: QuotedTargetKind,
    target_id: str,
) -> None:
    quoted = Q._quoted_experiment(QuotedFrom(target_kind, target_id))
    observations = (
        [
            F.observation(
                target_id,
                quoted.experiment_id,
                F.o2_identity(),
                Decimal("1"),
            )
        ]
        if target_kind is QuotedTargetKind.OBSERVATION
        else []
    )

    report = validate_corpus([F.work()], [quoted], observations)

    assert any(
        issue.path == "experiment[quoted-experiment].quoted_from"
        and issue.reason is RefusalReason.CONDITIONAL_FIELD
        for issue in report.hard_issues
    )


def test_quoted_from_resolution_guard_is_load_bearing(monkeypatch) -> None:
    source = inspect.getsource(V.validate_experiment)
    source = source.replace(
        "if experiment.quoted_from is not None:",
        "if False:  # reviewer mutation",
        1,
    )
    namespace = dict(V.__dict__)
    exec(compile(source, "<ror-t1824-remove-link-guard>", "exec"), namespace)
    monkeypatch.setattr(V, "validate_experiment", namespace["validate_experiment"])
    with pytest.raises(AssertionError):
        Q.test_quoted_from_requires_a_resolving_target()


def test_quoted_from_contract_is_documented() -> None:
    root = Path(__file__).resolve().parents[2]
    documentation = (root / "docs" / "validation-battery.md").read_text(
        encoding="utf-8"
    )

    assert "## Quoted records and inherited values" in documentation
    for token in (
        "quoted_from",
        "target_kind",
        "target_id",
        "observation",
        "experiment",
        "method_inherited_from",
        "initial_composition_inherited_from",
        "derived_from",
        "REFERENTIAL_INTEGRITY",
        "CONDITIONAL_FIELD",
    ):
        assert token in documentation
