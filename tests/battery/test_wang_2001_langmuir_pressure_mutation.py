"""Mutation probe for Langmuir pressure-contract forwarding in the owner."""

import pytest

import simulator.battery.residue as residue_module
from simulator.battery.enums import Engine
from tests.battery.wang_2001_langmuir_support import (
    wang_context,
    wang_residue_rows,
    wang_sc1_residue_pair,
)


def test_dropping_pressure_contract_fails_wang_residue_prediction(monkeypatch):
    context, observations = wang_context()
    references = wang_residue_rows(observations)
    reference, anchor = wang_sc1_residue_pair(references, observations)
    experiment = context.experiments[reference.experiment_id]

    # Mutation: the wrapped engine closure loses its fixed-activity declaration.
    monkeypatch.setattr(
        residue_module,
        "_inherit_residue_pressure_contract",
        lambda _target, _source: None,
    )
    with pytest.raises(residue_module.ResidueInventoryRefusal) as caught:
        residue_module.predict_langmuir_residue_composition(
            Engine.INTERNAL_ANALYTICAL.value,
            reference,
            experiment,
            (reference, anchor),
        )

    assert caught.value.reason == "residue_pressure_law_exponent_mismatch"
