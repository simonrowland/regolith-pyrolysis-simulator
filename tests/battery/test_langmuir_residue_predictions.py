"""Per-bead Langmuir residue extent scoring for Mendybaev Table 1."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulator.battery.enums import (
    Engine,
    MetricOperation,
    NoticeKind,
    Quantity,
    ResidualStatus,
    ValueKind,
)
from simulator.battery.migrate import Migrator
from simulator.battery.records import State, Value
from simulator.battery.residue import (
    ResidueChannel,
    ResidueInventoryRefusal,
    integrate_knudsen_residue_inventory,
    predict_langmuir_residue_composition,
)
from simulator.battery.score import (
    FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND,
    ScoreContext,
    _extent_residue_prediction,
    _langmuir_residue_prediction,
    compile_residual,
    flagged_strata,
)


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2013-fun-cai-evaporation.yaml"


def _mendybaev_context():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    observations = dict(migrator.result.observations)
    context = ScoreContext(
        {}, dict(migrator.result.experiments), observations, root=ROOT
    )
    return context, observations


def _row(rows, sample: str, formula: str = "SiO2"):
    return next(
        row
        for row in rows.values()
        if row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
        and row.identity.species.formula == formula
        and row.locator is not None
        and row.locator.note == f"row={sample}"
    )


def _assert_langmuir_notices(notices):
    assert any(
        notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and "DP-005 alpha_assumed" in notice.reason
        and "relative alpha" in notice.reason
        and "MgO and SiO2" in notice.reason
        for notice in notices
    )
    assert any(
        notice.kind is NoticeKind.ENGINE_MODEL_CAVEAT
        and "melt_well_mixed_assumed" in notice.reason
        and "R^2/D" in notice.reason
        and "1e-10 to 1e-9 m2/s" in notice.reason
        and "2.5 mm loop" in notice.reason
        and "radius, which is unprinted" in notice.reason
        for notice in notices
    )
    assert any(
        notice.kind is NoticeKind.OUT_OF_CERTIFIED_BAND
        and "xi includes the furnace ramp" in notice.reason
        and "isothermal at 1900 C" in notice.reason
        for notice in notices
    )
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and "congruent vapour-only zero-storage closure" in notice.reason
        and "W/Mo" not in notice.reason
        and "SC-360" not in notice.reason
        for notice in notices
    )
    assert any(
        "p_bulk=0 Pa" in notice.reason
        and "printed ceiling <0.0001 Pa" in notice.reason
        for notice in notices
    )
    assert all(notice.kind is not NoticeKind.UNVERIFIED_APPARATUS for notice in notices)
    assert all("DP-004" not in notice.reason for notice in notices)


def test_every_mendybaev_residue_score_is_flagged_absolute_and_scores_zero_time():
    context, rows = _mendybaev_context()
    cache = {}
    residue_rows = [
        row for row in rows.values()
        if row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(residue_rows) == 96
    zero_time_beads = set()
    for reference in residue_rows:
        prediction = _langmuir_residue_prediction(
            context, reference, Engine.INTERNAL_ANALYTICAL, cache
        )
        residual, _ = compile_residual(
            reference,
            Engine.INTERNAL_ANALYTICAL,
            context=context,
            prediction=prediction,
        )
        assert reference.identity.subtype.value == "oxide_wt_percent"
        assert residual.numeric is not None
        assert residual.numeric.operation is MetricOperation.ABSOLUTE
        assert residual.numeric.value == prediction.value - reference.value.point
        assert residual.score_eligible is False
        assert residual.status is not ResidualStatus.MATCH
        assert residual.status is not ResidualStatus.REFUSED
        assert FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND in flagged_strata(residual.notices)
        _assert_langmuir_notices(residual.notices)
        if reference.point_conditions["time_min"].state.value == 0:
            zero_time_beads.add(reference.locator.note.removeprefix("row="))
            anchor = next(
                row
                for row in rows.values()
                if row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
                and row.locator is not None
                and row.locator.note == reference.locator.note
            )
            assert anchor.value.point > 0
    assert zero_time_beads == {"FUN1-3", "FUN1-12", "FUN2-4"}


def test_nonunit_alpha_keeps_the_shared_extent_integrator_refusal():
    with pytest.raises(ResidueInventoryRefusal) as caught:
        integrate_knudsen_residue_inventory(
            {"FeO": 1.0},
            (
                ResidueChannel("Fe", "Fe", "FeO", 0.5, "sensitivity"),
                ResidueChannel("O2", "O2", None, 1.0, "sensitivity"),
            ),
            lambda _inventory, _logp, _temperature: {"Fe": 1.0, "O2": 0.0},
            temperature_path=((0.1, 1900.0),),
            measured_mass_loss_fraction=0.1,
            near_saturated=True,
            source_fO2_log_by_interval=(-10.0,),
        )
    assert caught.value.reason == "knudsen_equilibrium_channels_required"


def test_charge_pressure_and_missing_anchor_are_visible_without_clock_fallback(monkeypatch):
    import simulator.battery.residue as residue

    context, rows = _mendybaev_context()
    reference = _row(rows, "FUN1-10")
    experiment = context.experiments[reference.experiment_id]
    base = predict_langmuir_residue_composition(
        "internal-analytical", reference, experiment, rows.values()
    )
    fun2 = next(
        item for item in context.experiments.values()
        if item.experiment_id.endswith("mendybaev-fun2-vacuum-evaporation")
    )
    swapped = replace(
        experiment,
        sample=replace(
            experiment.sample,
            initial_composition=fun2.sample.initial_composition,
        ),
    )
    swapped_prediction = predict_langmuir_residue_composition(
        "internal-analytical", reference, swapped, rows.values()
    )
    assert (
        swapped_prediction["composition_wt_pct"]["SiO2"]
        != base["composition_wt_pct"]["SiO2"]
    )

    for pressure in (Decimal("101325"), Decimal("0.1")):
        located = experiment.conditions["total_pressure_Pa"]
        point_pressure = replace(
            located,
            state=State.of(Value(kind=ValueKind.POINT, point=pressure)),
        )
        pressure_mutation = replace(
            experiment,
            conditions={**experiment.conditions, "total_pressure_Pa": point_pressure},
        )
        try:
            changed = predict_langmuir_residue_composition(
                "internal-analytical", reference, pressure_mutation, rows.values()
            )
            visible_notices = changed["notices"]
            assert changed["p_bulk_Pa"] == float(pressure)
            assert str(pressure) in changed["printed_pressure"]
        except ResidueInventoryRefusal as exc:
            visible_notices = exc.notices
            assert exc.provenance["p_bulk_Pa"] == float(pressure)
            assert str(pressure) in exc.provenance["printed_pressure"]
        assert any(
            f"p_bulk={float(pressure):g} Pa" in notice.reason
            and str(pressure) in notice.reason
            for notice in visible_notices
        )

    anchor = next(
        row for row in rows.values()
        if row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and row.locator is not None
        and row.locator.note == "row=FUN1-10"
    )

    def fail_clock(*_args, **_kwargs):
        raise AssertionError("Langmuir residue scoring called the duration clock")

    monkeypatch.setattr(residue, "predict_mass_loss_fraction", fail_clock)
    context_without_anchor = ScoreContext(
        {},
        context.experiments,
        {key: row for key, row in rows.items() if key != anchor.observation_id},
        root=ROOT,
    )
    refused = _extent_residue_prediction(
        context_without_anchor, reference, Engine.INTERNAL_ANALYTICAL
    )
    assert refused is not None
    assert refused.refusal_detail["reason"] == (
        "measured_mass_loss_fraction_missing_or_invalid"
    )


def test_declared_hashimoto_cohort_does_not_enter_langmuir_extent_dispatch():
    from simulator.battery.source_declarations import (
        HASHIMOTO_RESIDUE_PREDICTOR,
        declared_residue_predictor,
    )
    from simulator.source_ids import HASHIMOTO_SOURCE_ID

    context, rows = _mendybaev_context()
    reference = _row(rows, "FUN1-10")
    experiment = context.experiments[reference.experiment_id]
    declared = declared_residue_predictor(HASHIMOTO_SOURCE_ID)
    assert declared is HASHIMOTO_RESIDUE_PREDICTOR
    reference = replace(reference, source_id=HASHIMOTO_SOURCE_ID)
    context_stub = SimpleNamespace(experiments={experiment.experiment_id: experiment})
    assert _extent_residue_prediction(
        context_stub, reference, Engine.INTERNAL_ANALYTICAL
    ) is None
