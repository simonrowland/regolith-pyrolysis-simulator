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
MENDYBAEV_BEAD_GROUPS = (
    "FUN1-1",
    "FUN1-3",
    "FUN1-4",
    "FUN1-5",
    "FUN1-9",
    "FUN1-10",
    "FUN1-11",
    "FUN1-12",
    "FUN1-13",
    "FUN1-14",
    "FUN1-15",
    "FUN1-16",
    "FUN1-17",
    "FUN1-18",
    "FUN2-1",
    "FUN2-2",
    "FUN2-3",
    "FUN2-4",
    "FUN2-5",
    "FUN2-6",
    "FUN2-7",
    "FUN2-8",
    "FUN2-9",
    "FUN2-10",
)
MENDYBAEV_SCORE_GROUPS = tuple(
    MENDYBAEV_BEAD_GROUPS[index : index + 6]
    for index in range(0, len(MENDYBAEV_BEAD_GROUPS), 6)
)
MENDYBAEV_ZERO_TIME_BEADS = {"FUN1-3", "FUN1-12", "FUN2-4"}


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


@pytest.fixture(scope="module")
def mendybaev_context():
    context, rows = _mendybaev_context()
    residue_rows = [
        row
        for row in rows.values()
        if row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    bead_groups = {
        row.locator.note.removeprefix("row=")
        for row in residue_rows
        if row.locator is not None
        and row.locator.note is not None
        and row.locator.note.startswith("row=")
    }
    assert len(residue_rows) == 96
    assert len(MENDYBAEV_BEAD_GROUPS) == len(set(MENDYBAEV_BEAD_GROUPS))
    assert bead_groups == set(MENDYBAEV_BEAD_GROUPS)
    return context, rows


@pytest.mark.parametrize("bead_group", MENDYBAEV_BEAD_GROUPS)
def test_mendybaev_residue_scores_are_flagged_by_bead_group(bead_group, mendybaev_context):
    context, rows = mendybaev_context
    cache = {}
    all_residue_rows = [
        row for row in rows.values()
        if row.identity.quantity.value is Quantity.RESIDUE_COMPONENT_COMPOSITION
    ]
    assert len(all_residue_rows) == 96
    residue_rows = [
        row
        for row in all_residue_rows
        if row.locator is not None
        and row.locator.note == f"row={bead_group}"
    ]
    assert len(residue_rows) == 4
    has_zero_time = False
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
            has_zero_time = True
            anchor = next(
                row
                for row in rows.values()
                if row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
                and row.locator is not None
                and row.locator.note == reference.locator.note
            )
            assert anchor.value.point > 0
    assert has_zero_time is (bead_group in MENDYBAEV_ZERO_TIME_BEADS)


@pytest.mark.parametrize("bead_groups", MENDYBAEV_SCORE_GROUPS)
def test_score_store_emits_all_mendybaev_langmuir_residue_rows(
    bead_groups, mendybaev_context
):
    context, _rows = mendybaev_context
    selected_observations = {
        observation_id: row
        for observation_id, row in context.observations.items()
        if row.identity.quantity.value is not Quantity.RESIDUE_COMPONENT_COMPOSITION
        or row.locator.note.removeprefix("row=") in bead_groups
    }
    score_context = replace(context, observations=selected_observations)
    residuals, _ = score_store(
        score_context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        rail=Rail.RESIDUE_COMPOSITION,
    )
    assert len(residuals) == 4 * len(bead_groups)
    for residual in residuals:
        assert residual.numeric is not None
        assert residual.status is not ResidualStatus.REFUSED
        assert residual.score_eligible is False
        assert (
            FLAGGED_STRATUM_OUT_OF_CERTIFIED_BAND
            in flagged_strata(residual.notices)
        )


def test_pressure_model_receives_located_per_bead_starting_inventory(monkeypatch):
    import simulator.battery.residue as residue

    context, rows = _mendybaev_context()
    reference = _row(rows, "FUN1-10")
    expected_mass_kg = float(Decimal("0.0214") / Decimal("1000"))
    original = residue._make_residue_pressure_model
    seen_inventories = []

    def capture_inventory(*args, **kwargs):
        inventory = kwargs["starting_inventory"]
        seen_inventories.append(dict(inventory))
        return original(*args, **kwargs)

    monkeypatch.setattr(residue, "_make_residue_pressure_model", capture_inventory)
    result = predict_langmuir_residue_composition(
        "internal-analytical",
        reference,
        context.experiments[reference.experiment_id],
        rows.values(),
    )
    assert len(seen_inventories) == 1
    assert result["initial_inventory_mol"] == seen_inventories[0]
    reconstructed_mass_kg = sum(
        amount * residue._formula_terms(oxide)[1]
        for oxide, amount in seen_inventories[0].items()
    )
    assert abs(reconstructed_mass_kg - expected_mass_kg) < 1e-12
    assert result["sample_mass_kg"] == expected_mass_kg
    assert not any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and "sample mass defaulted" in notice.reason
        for notice in result["notices"]
    )


def test_unprinted_run_mass_uses_visible_reference_mass_default():
    context, rows = _mendybaev_context()
    reference = _row(rows, "FUN1-10")
    point_conditions = dict(reference.point_conditions)
    point_conditions.pop("sample_mass_kg")
    reference_without_mass = replace(
        reference,
        identity=replace(
            reference.identity,
            sample_mass_kg=State.unknown("per_run_initial_charge_mass_not_published"),
        ),
        point_conditions=point_conditions,
    )
    result = predict_langmuir_residue_composition(
        "internal-analytical",
        reference_without_mass,
        context.experiments[reference.experiment_id],
        rows.values(),
    )
    assert result["sample_mass_kg"] == 1.0
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and "sample mass defaulted to 1 kg" in notice.reason
        and "per_run_initial_charge_mass_not_published" in notice.reason
        for notice in result["notices"]
    )


def test_conflicting_located_run_masses_use_flagged_reference_default():
    context, rows = _mendybaev_context()
    reference = _row(rows, "FUN1-10")
    conflicting_reference = replace(
        reference,
        identity=replace(
            reference.identity,
            sample_mass_kg=State.of(Decimal("0.03")),
        ),
    )
    result = predict_langmuir_residue_composition(
        "internal-analytical",
        conflicting_reference,
        context.experiments[reference.experiment_id],
        rows.values(),
    )
    assert result["sample_mass_kg"] == 1.0
    assert any(
        notice.kind is NoticeKind.INPUT_OMITTED
        and "sample mass defaulted to 1 kg" in notice.reason
        and "located mass alternatives disagree" in notice.reason
        for notice in result["notices"]
    )


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
