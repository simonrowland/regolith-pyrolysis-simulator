from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery.enums import Engine, EvidenceClass, Phase, RefusalReason
from simulator.battery.records import Species
from simulator.battery.score import (
    ScoreContext,
    compile_residual,
    parse_species_formula,
)
from tests.battery import factories as F


NON_FORMULA_OR_AMBIGUOUS = (
    "unknown",
    "kems-007",
    "quartz",
    "gibbsite",
    "Aegirine natural sample",
    "CaCO3(calcite)",
    "Fe1-xO",
    "NH.C1",
    "DE-1",
    "D2E-1",
)


@pytest.mark.parametrize(
    ("formula", "expected"),
    (
        ("Fe.875S", (("Fe", 0.875), ("S", 1.0))),
        ("Fe.90S", (("Fe", 0.90), ("S", 1.0))),
        ("Fe.95O", (("Fe", 0.95), ("O", 1.0))),
    ),
)
def test_scoring_formula_parser_accepts_class_a_spelling(
    formula: str, expected: tuple[tuple[str, float], ...]
) -> None:
    assert parse_species_formula(formula) == expected


@pytest.mark.parametrize("formula", NON_FORMULA_OR_AMBIGUOUS)
def test_non_formula_and_ambiguous_species_keep_typed_refusal(
    formula: str,
) -> None:
    experiment = F.tabulation_experiment(T_K=Decimal("1000"))
    identity = replace(
        F.o2_identity(Decimal("1000")),
        species=Species(formula, Phase.G),
    )
    observation = F.observation(
        f"t1795-refusal-{formula}",
        experiment.experiment_id,
        identity,
        Decimal("1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="t1795-test-source",
    )
    context = ScoreContext(
        works={experiment.work_id: F.work(experiment.work_id)},
        experiments={experiment.experiment_id: experiment},
        observations={observation.observation_id: observation},
    )

    residual, _candidate = compile_residual(
        observation,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
    )

    assert residual.refusal is not None
    assert residual.refusal.reason is RefusalReason.IDENTITY_UNKNOWN
    assert residual.refusal.detail == {
        "reason": "species_formula_unparsed",
        "formula": formula,
    }
