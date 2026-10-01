from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from simulator.battery.enums import AdmissionStatus, EvidenceClass, RefusalReason
from simulator.battery.score import load_score_context
from simulator.battery.validity import run_validity_gates
from tests.battery import factories as F


def test_kems_gate_treats_missing_point_conditions_as_empty() -> None:
    experiment = F.kems_experiment(
        orifice_area=None, clausing=None, kn=None, calibrated=False
    )
    identity = F.psat_identity("Na")
    observation = F.observation(
        "missing-point-conditions",
        experiment.experiment_id,
        identity,
        "1",
        evidence=EvidenceClass.MEASURED_DIRECT,
        admission=AdmissionStatus.ADMITTED,
    )
    missing = replace(observation, point_conditions=None)
    empty = replace(observation, point_conditions={})

    missing_outcome = run_validity_gates(experiment, missing)
    empty_outcome = run_validity_gates(experiment, empty)

    assert missing_outcome == empty_outcome
    assert missing_outcome.passed is False
    assert missing_outcome.reason is RefusalReason.EFFUSION_REGIME_UNVERIFIED


@pytest.mark.serial
@pytest.mark.xdist_group("serial")
def test_full_store_validity_gates_do_not_raise() -> None:
    context = load_score_context(Path(__file__).resolve().parents[2])

    for observation in context.observations.values():
        experiment = context.experiments[observation.experiment_id]
        run_validity_gates(experiment, observation)
