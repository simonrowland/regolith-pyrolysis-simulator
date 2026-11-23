"""Per-bead Langmuir residue extent scoring for Mendybaev Table 1."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    Engine,
    MetricOperation,
    MethodToken,
    NoticeKind,
    Quantity,
    Rail,
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
    score_store,
)


ROOT = Path(__file__).resolve().parents[2]
EXTRACT = ROOT / "data/literature/extracts/mendybaev-2013-fun-cai-evaporation.yaml"
RICHTER_EXTRACT = ROOT / "data/literature/extracts/kems-010-richter-2007.yaml"


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
        # MATCH is band membership; score_eligible is the pass gate for this tier.
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
            assert changed["p_bulk_Pa"] == 0.0
            assert str(pressure) in changed["printed_pressure"]
        except ResidueInventoryRefusal as exc:
            visible_notices = exc.notices
            assert exc.provenance["p_bulk_Pa"] == 0.0
            assert str(pressure) in exc.provenance["printed_pressure"]
        assert any(
            "extent uses p_bulk=0 Pa" in notice.reason
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

    migrator = Migrator(root=ROOT, index={}, aliases={})
    extract = ROOT / f"data/literature/extracts/{HASHIMOTO_SOURCE_ID}.yaml"
    migrator._migrate_extract(extract)
    migrator.finalize()
    rows = dict(migrator.result.observations)
    context = ScoreContext(
        {}, dict(migrator.result.experiments), rows, root=ROOT
    )
    reference = next(
        row
        for row in rows.values()
        if row.source_id == HASHIMOTO_SOURCE_ID
        and row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    )
    experiment = context.experiments[reference.experiment_id]
    declared = declared_residue_predictor(HASHIMOTO_SOURCE_ID)
    assert declared is HASHIMOTO_RESIDUE_PREDICTOR
    assert experiment.method.value is MethodToken.LANGMUIR_FREE_EVAPORATION
    assert _extent_residue_prediction(
        context, reference, Engine.INTERNAL_ANALYTICAL
    ) is None


def test_richter_langmuir_residue_stays_out_of_mendybaev_dispatch():
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(RICHTER_EXTRACT)
    migrator.finalize()
    observations = dict(migrator.result.observations)
    context = ScoreContext(
        {}, dict(migrator.result.experiments), observations, root=ROOT
    )
    reference = next(
        row
        for row in observations.values()
        if row.observation_id
        == (
            "kems-010-richter-2007::richter_2007_residue_table1_r3-12::"
            "T=2173.15:row=r3-12:h=319547b12699"
        )
    )
    assert reference.source_id == "kems-010-richter-2007"
    assert _extent_residue_prediction(
        context, reference, Engine.INTERNAL_ANALYTICAL
    ) is None


def test_score_store_caches_langmuir_extent_per_bead_and_rechecks_anchors(monkeypatch):
    import simulator.battery.residue as residue

    context, rows = _mendybaev_context()
    references = [_row(rows, "FUN1-10"), _row(rows, "FUN1-3")]
    anchors = {
        row.locator.note: row
        for row in rows.values()
        if row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        and row.locator is not None
        and row.locator.note in {"row=FUN1-10", "row=FUN1-3"}
    }
    experiment_id = references[0].experiment_id
    assert all(row.experiment_id == experiment_id for row in references)

    def context_with(observations):
        return ScoreContext(
            {},
            {experiment_id: context.experiments[experiment_id]},
            {row.observation_id: row for row in observations},
            root=ROOT,
        )

    calls = []

    def sentinel(_engine, reference, _experiment, observations):
        note = reference.locator.note
        calls.append(note)
        matching = [
            row
            for row in observations
            if row.locator is not None
            and row.locator.note == note
            and row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        ]
        if len(matching) != 1:
            raise ResidueInventoryRefusal(
                "measured_mass_loss_fraction_missing_or_invalid"
            )
        return {
            "composition_wt_pct": {
                "SiO2": Decimal("10") + matching[0].value.point
            },
            "notices": (),
            "anchor": matching[0].observation_id,
        }

    monkeypatch.setattr(residue, "predict_langmuir_residue_composition", sentinel)
    selected = context_with((*references, *anchors.values()))
    residuals, _ = score_store(
        selected,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert len(residuals) == 2
    assert set(calls) == {"row=FUN1-10", "row=FUN1-3"}
    assert len(calls) == 2
    by_reference = {residual.reference: residual for residual in residuals}
    predictions = {}
    for reference in references:
        anchor = anchors[reference.locator.note]
        residual = by_reference[reference.observation_id]
        assert residual.numeric is not None
        assert residual.numeric.value == (
            Decimal("10") + anchor.value.point - reference.value.point
        )
        predictions[reference.locator.note] = (
            residual.numeric.value + reference.value.point
        )
    assert predictions == {
        "row=FUN1-10": Decimal("10") + anchors["row=FUN1-10"].value.point,
        "row=FUN1-3": Decimal("10") + anchors["row=FUN1-3"].value.point,
    }
    assert predictions["row=FUN1-10"] != predictions["row=FUN1-3"]

    calls.clear()
    missing_later_anchor = context_with(
        (references[0], references[1], anchors["row=FUN1-10"])
    )
    residuals, _ = score_store(
        missing_later_anchor,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert calls == ["row=FUN1-10", "row=FUN1-3"]
    by_reference = {residual.reference: residual for residual in residuals}
    assert by_reference[references[0].observation_id].numeric is not None
    later = by_reference[references[1].observation_id]
    assert later.refusal is not None
    assert (
        later.refusal.detail["reason"]
        == "measured_mass_loss_fraction_missing_or_invalid"
    )
