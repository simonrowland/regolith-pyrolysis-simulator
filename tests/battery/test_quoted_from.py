from dataclasses import replace
from decimal import Decimal

import pytest
import yaml

from simulator.battery import migrate as M
from simulator.battery.enums import AmountBasis, QuotedTargetKind, RefusalReason
from simulator.battery.records import Composition, QuotedFrom, State
from simulator.battery.validate import validate_corpus
from tests.battery import factories as F


def _composition() -> Composition:
    return Composition(
        basis="oxide_mole_fraction",
        components=(
            ("SiO2", Decimal("0.6")),
            ("MgO", Decimal("0.4")),
        ),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )


def _quoted_experiment(
    target: QuotedFrom | None, *, composition: Composition | None = None
):
    base = F.tabulation_experiment("quoted-experiment")
    return replace(
        base,
        method_inherited_from=True,
        quoted_from=target,
        sample=replace(
            base.sample,
            initial_composition=F.located(composition or _composition()),
            initial_composition_inherited_from=True,
        ),
    )


@pytest.mark.parametrize(
    ("target_kind", "target_id"),
    [
        (QuotedTargetKind.EXPERIMENT, "primary-experiment"),
        (QuotedTargetKind.OBSERVATION, "primary-observation"),
    ],
)
def test_quoted_from_roundtrips_and_resolves_either_primary_record(
    target_kind: QuotedTargetKind, target_id: str
) -> None:
    primary = F.tabulation_experiment("primary-experiment")
    composition = _composition()
    primary = replace(
        primary,
        sample=replace(
            primary.sample,
            initial_composition=F.located(composition),
        ),
    )
    primary_observation = F.observation(
        "primary-observation", primary.experiment_id, F.o2_identity(), "1"
    )
    target = QuotedFrom(target_kind, target_id)
    quoted = _quoted_experiment(target, composition=composition)

    restored = M.experiment_from_plain(
        yaml.safe_load(yaml.safe_dump(M.to_plain(quoted)))
    )

    assert restored.quoted_from == target
    assert restored.method_inherited_from is True
    assert restored.sample.initial_composition_inherited_from is True
    assert restored.sample.initial_composition == quoted.sample.initial_composition
    assert M.to_plain(restored)["quoted_from"] == {
        "target_kind": target_kind.value,
        "target_id": target_id,
    }
    report = validate_corpus(
        [F.work()],
        [primary, restored],
        [primary_observation],
    )
    assert report.hard_issues == ()


def test_measured_liquid_composition_cannot_stand_in_for_run_composition() -> None:
    measured_liquid = _composition()
    primary = F.tabulation_experiment("primary-experiment")
    measured_observation = F.observation(
        "measured-liquid-observation",
        primary.experiment_id,
        replace(
            F.o2_identity(),
            composition=State.of(measured_liquid),
        ),
        "1",
    )
    quoted = _quoted_experiment(
        QuotedFrom(
            QuotedTargetKind.OBSERVATION,
            measured_observation.observation_id,
        ),
        composition=measured_liquid,
    )

    report = validate_corpus(
        [F.work()], [primary, quoted], [measured_observation]
    )

    assert primary.sample.initial_composition is None
    assert measured_observation.identity.composition.value == measured_liquid
    assert any(
        issue.path
        == "experiment[quoted-experiment].sample.initial_composition_inherited_from"
        and issue.reason is RefusalReason.CONDITIONAL_FIELD
        for issue in report.hard_issues
    )


def test_inherited_initial_composition_must_match_cited_run() -> None:
    primary = F.tabulation_experiment("primary-experiment")
    starting_composition = _composition()
    primary = replace(
        primary,
        sample=replace(
            primary.sample,
            initial_composition=F.located(starting_composition),
        ),
    )
    different_composition = replace(
        starting_composition,
        components=(
            ("SiO2", Decimal("0.4")),
            ("MgO", Decimal("0.6")),
        ),
    )
    quoted = _quoted_experiment(
        QuotedFrom(QuotedTargetKind.EXPERIMENT, primary.experiment_id),
        composition=different_composition,
    )
    report = validate_corpus([F.work()], [primary, quoted], [])
    assert any(
        issue.path
        == "experiment[quoted-experiment].sample.initial_composition_inherited_from"
        and issue.reason is RefusalReason.CONDITIONAL_FIELD
        for issue in report.hard_issues
    )


def test_quoted_from_requires_a_resolving_target() -> None:
    primary = F.tabulation_experiment("primary-experiment")
    quoted = _quoted_experiment(
        QuotedFrom(QuotedTargetKind.OBSERVATION, "missing-observation")
    )

    report = validate_corpus([F.work()], [primary, quoted], [])

    assert any(
        issue.path == "experiment[quoted-experiment].quoted_from"
        and issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        for issue in report.hard_issues
    )


def test_inherited_fields_require_a_quoted_source() -> None:
    quoted = _quoted_experiment(None)

    report = validate_corpus([F.work()], [quoted], [])

    assert {
        issue.path for issue in report.hard_issues
        if issue.reason is RefusalReason.CONDITIONAL_FIELD
    } >= {
        "experiment[quoted-experiment].method_inherited_from",
        "experiment[quoted-experiment].sample.initial_composition_inherited_from",
    }


def test_quoted_from_rejects_unknown_target_kind() -> None:
    raw = M.to_plain(_quoted_experiment(None))
    raw["quoted_from"] = {"target_kind": "work", "target_id": "work-1"}

    with pytest.raises(ValueError, match="invalid quoted_from"):
        M.experiment_from_plain(raw)
