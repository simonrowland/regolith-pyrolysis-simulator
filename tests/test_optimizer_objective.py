from __future__ import annotations

import math

import pytest

from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveProfileError,
    dominates,
    objective_importance_evidence,
    pareto_front,
)


DEFINITIONS = (
    ObjectiveDefinition("oxygen_kg", "maximize", "kg", ordinal=0),
    ObjectiveDefinition("energy_kWh", "minimize", "kWh", ordinal=1),
)


def test_dominates_requires_strict_improvement() -> None:
    assert dominates(
        {"oxygen_kg": 2.0, "energy_kWh": 4.0},
        {"oxygen_kg": 1.0, "energy_kWh": 5.0},
        DEFINITIONS,
    )


def test_equal_objective_vectors_do_not_dominate() -> None:
    left = {"oxygen_kg": 1.0, "energy_kWh": 5.0}
    right = {"oxygen_kg": 1.0, "energy_kWh": 5.0}

    assert not dominates(left, right, DEFINITIONS)
    assert not dominates(right, left, DEFINITIONS)


def test_dominates_honors_mixed_minimize_maximize_directions() -> None:
    lower_energy = {"oxygen_kg": 1.0, "energy_kWh": 4.0}
    higher_energy = {"oxygen_kg": 1.0, "energy_kWh": 5.0}

    assert dominates(lower_energy, higher_energy, DEFINITIONS)
    assert not dominates(higher_energy, lower_energy, DEFINITIONS)


def test_missing_or_nonfinite_objectives_raise() -> None:
    with pytest.raises(ObjectiveComputationError, match="energy_kWh.*missing"):
        dominates({"oxygen_kg": 1.0}, {"oxygen_kg": 1.0, "energy_kWh": 5.0}, DEFINITIONS)

    with pytest.raises(ObjectiveComputationError, match="oxygen_kg is non-finite"):
        dominates(
            {"oxygen_kg": math.nan, "energy_kWh": 5.0},
            {"oxygen_kg": 1.0, "energy_kWh": 5.0},
            DEFINITIONS,
        )


def test_incomplete_objective_importance_evidence_raises_insufficient_evidence() -> None:
    profile = {
        "objectives": [
            {
                "metric": "oxygen_kg",
                "sense": "maximize",
                "units": "kg",
                "weight": 1.0,
            }
        ]
    }

    with pytest.raises(
        ObjectiveProfileError,
        match="insufficient-evidence: objectives\\[0\\] 'oxygen_kg' missing rationale",
    ):
        objective_importance_evidence(profile)


def test_pareto_front_preserves_stable_non_dominated_order() -> None:
    items = (
        {"id": "d", "objectives": {"oxygen_kg": 1.5, "energy_kWh": 3.0}},
        {"id": "a", "objectives": {"oxygen_kg": 1.0, "energy_kWh": 5.0}},
        {"id": "c", "objectives": {"oxygen_kg": 2.0, "energy_kWh": 4.0}},
        {"id": "b", "objectives": {"oxygen_kg": 2.0, "energy_kWh": 5.0}},
    )

    front = pareto_front(
        items,
        DEFINITIONS,
        objective_getter=lambda item: item["objectives"],
    )

    assert [item["id"] for item in front] == ["d", "c"]
