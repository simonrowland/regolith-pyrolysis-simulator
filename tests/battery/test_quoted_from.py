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
    assert restored.sample.initial_composition == primary.sample.initial_composition
    assert primary_observation.identity.composition.is_not_applicable
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


def test_ambiguous_printed_run_match_stays_unlinked() -> None:
    temperature = Decimal("1453.15")
    primary_runs = []
    primary_observations = []
    for experiment_id, observation_id, composition in (
        ("primary-run-a", "primary-row-a", _composition()),
        (
            "primary-run-b",
            "primary-row-b",
            replace(
                _composition(),
                components=(
                    ("SiO2", Decimal("0.4")),
                    ("MgO", Decimal("0.6")),
                ),
            ),
        ),
    ):
        experiment = F.tabulation_experiment(
            experiment_id, T_K=temperature
        )
        experiment = replace(
            experiment,
            sample=replace(
                experiment.sample,
                initial_composition=F.located(composition),
            ),
        )
        observation = F.observation(
            observation_id,
            experiment_id,
            F.o2_identity(T_K=temperature),
            "1",
        )
        observation = replace(
            observation,
            provenance={"sample_id_as_printed": "W-1."},
            point_conditions={
                "fO2_log": F.located(
                    "-12.2", table="included experimental data", record="E4"
                )
            },
        )
        primary_runs.append(experiment)
        primary_observations.append(observation)

    quoted = F.tabulation_experiment(
        "quoted-experiment", T_K=temperature
    )
    quoted_observation = F.observation(
        "quoted-row",
        quoted.experiment_id,
        F.o2_identity(T_K=temperature),
        "1",
    )
    quoted_observation = replace(
        quoted_observation,
        provenance={"sample_id_as_printed": "W-1."},
        point_conditions={
            "fO2_log": F.located(
                "-12.2", table="included experimental data", record="E4"
            )
        },
    )

    report = validate_corpus(
        [F.work()],
        [*primary_runs, quoted],
        [*primary_observations, quoted_observation],
    )

    assert len(primary_runs) == 2
    assert quoted.quoted_from is None
    assert quoted.sample.initial_composition is None
    assert quoted.sample.initial_composition_inherited_from is None
    assert report.hard_issues == ()


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
