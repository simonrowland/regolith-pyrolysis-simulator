"""A printed Richter mass-loss extent of 0 is the starting composition.

d-460: zero extent with a printed temperature is callable. Negative extents and
missing mass-loss values stay typed refusals. cai-r-13 has a blank mass loss
and no mass-loss observation, so those four oxide rows stay refused.
"""

from pathlib import Path

import pytest

from simulator.battery.enums import Engine, Rail
from simulator.battery.migrate import Migrator
from simulator.battery.residue import (
    ResidueChannel,
    ResidueInventoryRefusal,
    integrate_knudsen_residue_inventory,
    predict_langmuir_residue_composition,
)
from simulator.battery.score import ScoreContext, score_store


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/kems-010-richter-2007.yaml"
_ZERO_PATH = ((0.0, 1873.15),)


def _channels():
    return (
        ResidueChannel("FeO", "FeO", "FeO", 1.0, "equilibrium effusion"),
        ResidueChannel("O2", "O2", None, 1.0, "equilibrium effusion"),
    )


def _integrate(**changes):
    kwargs = dict(
        initial_inventory_mol={"FeO": 2.0},
        channels=_channels(),
        pressure_model=lambda inventory, log_p, temperature: {
            "FeO": 1.0,
            "O2": 0.0,
        },
        temperature_path=_ZERO_PATH,
        measured_mass_loss_fraction=0.0,
        near_saturated=True,
        source_fO2_log_by_interval=(None,),
    )
    kwargs.update(changes)
    return integrate_knudsen_residue_inventory(**kwargs)


def test_zero_extent_returns_the_starting_inventory():
    result = _integrate()
    assert result.residue_mol == {"FeO": 2.0}
    assert result.evaporated_mol["FeO"] == 0.0
    assert result.evaporated_mol["O2"] == 0.0


@pytest.mark.parametrize(
    "changes,reason",
    (
        (
            {"temperature_path": ((-0.01, 1873.15),)},
            "printed_temperature_path_missing_or_invalid",
        ),
        (
            {"temperature_path": ()},
            "printed_temperature_path_missing_or_invalid",
        ),
        (
            {"measured_mass_loss_fraction": -0.01},
            "measured_mass_loss_fraction_missing_or_invalid",
        ),
        (
            {"measured_mass_loss_fraction": None},
            "measured_mass_loss_fraction_missing_or_invalid",
        ),
    ),
)
def test_negative_and_missing_extents_stay_typed_refusals(changes, reason):
    with pytest.raises(ResidueInventoryRefusal) as exc:
        _integrate(**changes)
    assert exc.value.reason == reason


def test_restoring_the_nonpositive_extent_gate_refuses_zero(monkeypatch):
    import simulator.battery.residue as residue

    result = _integrate()
    assert result.residue_mol == {"FeO": 2.0}
    real = residue.integrate_knudsen_residue_inventory

    def refuse_nonpositive(*args, **kwargs):
        path = kwargs["temperature_path"]
        if any(float(extent) <= 0.0 for extent, _temperature in path):
            raise ResidueInventoryRefusal(
                "printed_temperature_path_missing_or_invalid"
            )
        return real(*args, **kwargs)

    monkeypatch.setattr(
        residue, "integrate_knudsen_residue_inventory", refuse_nonpositive
    )
    with pytest.raises(ResidueInventoryRefusal) as exc:
        residue.integrate_knudsen_residue_inventory(
            {"FeO": 2.0},
            _channels(),
            lambda inventory, log_p, temperature: {"FeO": 1.0, "O2": 0.0},
            temperature_path=_ZERO_PATH,
            measured_mass_loss_fraction=0.0,
            near_saturated=True,
            source_fO2_log_by_interval=(None,),
        )
    assert exc.value.reason == "printed_temperature_path_missing_or_invalid"


def _richter_context():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = dict(migrator.result.observations)
    context = ScoreContext(
        {}, dict(migrator.result.experiments), observations, root=ROOT
    )
    return context, observations


def _table1_residue_rows(observations):
    return [
        row
        for row in observations.values()
        if "richter_2007_residue_table1_" in row.observation_id
    ]


def test_richter_printed_zero_extent_scores_as_the_starting_composition():
    import simulator.battery.residue as residue

    context, observations = _richter_context()
    residue_rows = _table1_residue_rows(observations)
    assert len(residue_rows) == 204
    residuals, _candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    by_reference = {residual.reference: residual for residual in residuals}
    assert set(by_reference) >= {row.observation_id for row in residue_rows}
    table_residuals = [by_reference[row.observation_id] for row in residue_rows]
    numeric = [
        residual for residual in table_residuals if residual.numeric is not None
    ]
    refused = [
        residual for residual in table_residuals if residual.numeric is None
    ]
    assert len(numeric) == 200
    assert len(refused) == 4
    assert all(
        "::richter_2007_residue_table1_r-13::" in residual.reference
        for residual in refused
    )
    assert all(
        residual.refusal is not None
        and residual.refusal.detail["reason"]
        == "measured_mass_loss_fraction_missing_or_invalid"
        for residual in refused
    )
    zero_rows = [
        row
        for row in residue_rows
        if "::richter_2007_residue_table1_r3-2::" in row.observation_id
    ]
    assert len(zero_rows) == 4
    assert all(by_reference[row.observation_id].numeric is not None for row in zero_rows)
    reference = zero_rows[0]
    experiment = context.experiments[reference.experiment_id]
    prediction = predict_langmuir_residue_composition(
        Engine.INTERNAL_ANALYTICAL.value,
        reference,
        experiment,
        observations.values(),
    )
    assert prediction["measured_mass_loss_fraction"] == 0.0
    assert prediction["final_inventory_mol"] == prediction["initial_inventory_mol"]
    starting = residue._initial_oxide_wt_percent(experiment.sample)
    for oxide, weight in starting.items():
        assert prediction["composition_wt_pct"][oxide] == pytest.approx(
            weight, rel=1.0e-12, abs=0.0
        )


def test_restoring_the_zero_extent_refusal_rejects_richter_r3_2(monkeypatch):
    import simulator.battery.residue as residue

    context, observations = _richter_context()
    reference = next(
        row
        for row in _table1_residue_rows(observations)
        if "::richter_2007_residue_table1_r3-2::" in row.observation_id
    )
    real = residue.integrate_knudsen_residue_inventory

    def refuse_nonpositive(*args, **kwargs):
        path = kwargs["temperature_path"]
        if any(float(extent) <= 0.0 for extent, _temperature in path):
            raise ResidueInventoryRefusal(
                "printed_temperature_path_missing_or_invalid"
            )
        return real(*args, **kwargs)

    monkeypatch.setattr(
        residue, "integrate_knudsen_residue_inventory", refuse_nonpositive
    )
    with pytest.raises(ResidueInventoryRefusal) as exc:
        predict_langmuir_residue_composition(
            Engine.INTERNAL_ANALYTICAL.value,
            reference,
            context.experiments[reference.experiment_id],
            observations.values(),
        )
    assert exc.value.reason == "printed_temperature_path_missing_or_invalid"
