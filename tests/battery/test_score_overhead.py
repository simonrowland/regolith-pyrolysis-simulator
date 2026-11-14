from __future__ import annotations

import hashlib
from decimal import Decimal

from simulator.battery.enums import (
    Authority,
    Engine,
    EvidenceClass,
    ExecutionState,
    QUANTITY_UNITS,
)
from simulator.battery.identity import quantity_token
from simulator.battery.records import Execution
from simulator.battery.score import (
    EnginePrediction,
    ScoreContext,
    compile_residual,
    dumps_residual_line,
)
from tests.battery import factories as F


def test_residual_bytes_are_pinned_before_score_overhead_refactor() -> None:
    work = F.work("overhead-pin-work")
    experiment = F.tabulation_experiment("overhead-pin-exp", work.work_id)
    reference = F.observation(
        "overhead-pin-reference",
        experiment.experiment_id,
        F.o2_identity(),
        Decimal("-0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=work.work_id,
    )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={reference.observation_id: reference},
        hostname="pin",
    )
    prediction = EnginePrediction(
        engine=Engine.INTERNAL_ANALYTICAL,
        channel=Engine.INTERNAL_ANALYTICAL.value,
        execution=Execution(state=ExecutionState.PRODUCED, call_evidence="overhead-pin"),
        value=Decimal("-0.2"),
        unit=QUANTITY_UNITS[quantity_token(reference.identity)],
        authority=Authority.CERTIFIED,
        coefficient_sources=("nasa-cea-thermo",),
        lineage_complete=True,
        identity=reference.identity,
    )
    residual, candidate = compile_residual(
        reference,
        Engine.INTERNAL_ANALYTICAL,
        context=context,
        comparison_ids={reference.observation_id},
        predict=lambda *_args, **_kwargs: prediction,
    )

    output = dumps_residual_line(residual, candidate).encode("utf-8")
    assert hashlib.sha256(output).hexdigest() == (
        "559d10350ef88aa3dc3214689823e59becbbbd31325af0f87ece2438a0a47bf2"
    )
