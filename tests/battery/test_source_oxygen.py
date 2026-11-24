"""d-216 source conditions: printed pressures and independent JANAF arithmetic."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    BenchIdentityBasis,
    CellMaterial,
    Engine,
    EvidenceClass,
    FO2Channel,
    NoticeKind,
    Phase,
    Quantity,
    RelativeSeriesUnavailableReason,
    ValueKind,
)
from simulator.battery import score as battery_score
from simulator.battery import score_metrics as battery_score_metrics
from simulator.battery.records import (
    Bench,
    BenchIdentity,
    Derivation,
    FO2Control,
    Located,
    Notice,
    Reaction,
    ReactionTerm, RelativeSeriesAnchor, RelativeSeriesClock,
    RelativeSeriesEvidence, RelativeSeriesPoint, Species, State, Value,
)
from simulator.battery.score import (
    ScoreContext,
    _bench_for_score,
    compile_residual,
    load_score_context,
    predict_with_engine,
    _score_relative_qms_series,
)
from simulator.battery.source_oxygen import (
    SourceOxygenCondition,
    SourceOxygenFallback,
    SourceOxygenFallbackReason as FallbackReason,
    linked_source_pressure,
    resolve_source_oxygen,
)
from simulator.source_ids import RYS_SOURCE_ID
from simulator.diagnostic_helpers import binary_pot_battery as battery
from tests.battery import factories as F


@pytest.fixture(scope="module")
def source_context():
    return load_score_context(
        Path(__file__).resolve().parents[2],
        sources=("kems-053-stolyarova-1991", "shornikov-1994-mullite-kems", "stolyarova-1996-cao-alumina-silica-kems"),
    )


@pytest.fixture(scope="module")
def demaria_context():
    return load_score_context(
        Path(__file__).resolve().parents[2],
        sources=("kems-022-demaria-1971",),
    )


@pytest.fixture(scope="module")
def costa_context():
    return load_score_context(
        Path(__file__).resolve().parents[2],
        sources=("kems-007-costa-2015",),
    )


def _row(context, source, formula="SiO"):
    return next(
        row for row in context.observations.values()
        if row.source_id == source and row.identity.species.formula == formula
        and row.identity.temperature_K.is_value and row.identity.temperature_K.value == 1933
        and (
            source != "kems-053-stolyarova-1991"
            or dict(row.identity.composition.value.components).get("CaO") == Decimal("0.6")
        )
    )


def _demaria_o_sample(context, sample):
    row_marker = f":row={sample}:"
    return next(
        row
        for row in context.observations.values()
        if row.source_id == "kems-022-demaria-1971"
        and row.experiment_id.endswith("::demaria-1971-main-kems")
        and row.identity.species.formula == "O"
        and "::demaria_1971_o_atomic_psat_table5::" in row.observation_id
        and row_marker in row.observation_id
    )


def _resolve(context, row, rows=None):
    experiment = context.experiments[row.experiment_id]
    return resolve_source_oxygen(
        row, context.observations.values() if rows is None else rows,
        _bench_for_score(experiment, context.benches),
    )


def _capture_engine(monkeypatch, row):
    requests = []
    handle = battery.EngineHandle(
        name="openimcc", backend=object(), available=True, unavailable_reason=None,
        takes_fo2=False, supports_intrinsic_fo2=True,
    )

    def equilibrate(_handle, pot, *, temperature_K, po2, **kwargs):
        requests.append(po2)
        return battery.EquilibrateCell(
            pot_id=pot.pot_id, engine="openimcc", temperature_K=temperature_K,
            po2=po2, status="ok", refusal_reason=None, engine_status="ok",
            engine_reason=None, melt_activities={},
            gas_partial_pressures_Pa={row.identity.species.formula: float(row.value.point)},
            liquid_fraction=1.0, wall_s=0.0, cpu_s=0.0, hostname="test",
        )

    monkeypatch.setattr(battery, "equilibrate_cell", equilibrate)
    return requests, {"openimcc": handle}


def _capture_comparison_identities(monkeypatch):
    from simulator.battery import score

    compared = []
    original = score._comparison_identity

    def capture(identity, reference, prediction):
        result = original(identity, reference, prediction)
        compared.append(result)
        return result

    monkeypatch.setattr(score, "_comparison_identity", capture)
    return compared


def _single_physics_rung(notices):
    labels = [notice.reason for notice in notices if "physics_rung=" in notice.reason]
    assert len(labels) == 1
    return labels[0]


def _relative_qms_inputs(source_trace, model_input_ids, *, target_species="O2"):
    run_id = "claimed-run"
    observed = tuple(
        RelativeSeriesPoint(
            run_id=run_id,
            coordinate=Decimal(str(time_s)),
            value=Value(ValueKind.POINT, point=Decimal(str(signal))),
            observable=f"{target_species}+",
            boundary="cell-exit",
            coordinate_unit="s",
            locator=F.loc(page=3),
            observation_id=source_trace.observation_id,
        )
        for time_s, signal in ((0, 2), (1, 4))
    )
    model = tuple(
        RelativeSeriesPoint(
            run_id=run_id,
            coordinate=Decimal(str(time_s)),
            value=Value(ValueKind.POINT, point=Decimal(str(pressure))),
            observable=target_species,
            boundary="cell-exit",
            coordinate_unit="s",
        )
        for time_s, pressure in ((10, 5), (11, 10))
    )
    anchor = RelativeSeriesAnchor(
        source_coordinate=Decimal(0),
        model_coordinate=Decimal(10),
        source_event="acquisition-start",
        model_event="trajectory-start",
        physical_event="heater-enable",
    )
    clock = RelativeSeriesClock(
        source_event="acquisition-start",
        model_event="trajectory-start",
        physical_event="heater-enable",
        mapping_supported=True,
        locator=F.loc(page=2),
    )
    evidence = RelativeSeriesEvidence(
        quantity=Quantity.ION_INTENSITY,
        model_quantity=Quantity.P_PARTIAL,
        channel=f"{target_species}+",
        target_species=target_species,
        boundary="cell-exit",
        target_isolated_or_interferences_corrected=True,
        background_treatment_supported=True,
        response_proportional_over_interval=True,
        locator=F.loc(page=1),
        model_run_id=run_id,
        reference_run_id=run_id,
        model_oxygen_input_observation_ids=model_input_ids,
    )
    return observed, model, anchor, clock, evidence


def _fallback_notice(context, monkeypatch, row, rows):
    requests, handles = _capture_engine(monkeypatch, row)
    residual, _ = compile_residual(
        row, Engine.OPENIMCC, context=context, handles=handles,
        point_observations=tuple(rows),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_OXYGEN_BALANCE_EFFUSION
    assert _single_physics_rung(residual.notices) == (
        "fo2_tier=3; physics_rung=3:congruent_effusion_balance; "
        "route=effusion_oxygen_balance"
    )
    return next(
        notice for notice in residual.notices
        if notice.reason.startswith("predict-and-flag substitution: congruent vapour-only zero-storage closure;")
    )


@pytest.mark.parametrize(
    "source,expected_bar",
    (("kems-053-stolyarova-1991", 3.0730622426279393e-12),
     ("shornikov-1994-mullite-kems", 4.6508175e-11)),
)
def test_linked_row_commands_source_oxygen(source_context, monkeypatch, source, expected_bar):
    # 1991 Table Ib pO=6.8e-10 atm; JANAF O-001 interpolates to
    # G(1933)=126.02894 kJ/mol. 1994 Table 2 directly prints pO2=4.59e-11 atm.
    row = _row(source_context, source)
    requests, handles = _capture_engine(monkeypatch, row)
    compared = _capture_comparison_identities(monkeypatch)
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(expected_bar, rel=1e-10)
    assert residual.numeric is not None
    assert candidate is not None
    assert len(compared) == 2
    expected_pa = Decimal(str(requests[0].po2_bar)) * Decimal("1e5")
    assert all(identity.fO2_Pa == State.of(expected_pa) for identity in compared)
    selected = candidate.provenance["selected_oxygen_condition"]
    assert Decimal(selected["value_Pa"]) == expected_pa
    assert selected["fo2_tier"] == 3
    assert selected["physics_rung"] == 2
    assert selected["route"] == "source_linked_oxygen_condition"
    assert _single_physics_rung(residual.notices) == (
        "fo2_tier=3; physics_rung=2:measured_same_cell_oxygen; "
        "route=source_linked_oxygen_condition"
    )
    condition = candidate.provenance["source_oxygen_condition"]
    assert condition["derived_from"]
    assert condition["derivation"]["inputs"] == condition["derived_from"]
    assert "cell-metal activity as assumed by the source" in condition["derivation"]["relation"]
    substitution = next(
        notice for notice in residual.notices
        if notice.kind is NoticeKind.INPUT_OMITTED
        and notice.origin == "source oxygen selection"
        and notice.reason.startswith("measured condition: same-cell source-linked oxygen;")
    )
    assert condition["observation_id"] in substitution.reason
    assert "target row has no printed fO2" in substitution.reason
    assert "condition=measured_same_cell_oxygen" in substitution.reason
    assert "rung=2:measured_same_cell_oxygen" in substitution.reason
    assert "basis=source_oxygen_condition;" in substitution.reason
    assert "inputs=" in substitution.reason
    assert float(condition["value"]["point"]) / 1e5 == pytest.approx(expected_bar, rel=1e-10)


def test_linked_oxygen_replaces_stale_derived_identity_in_prediction_and_comparison(
    source_context, monkeypatch
):
    row = _row(source_context, "kems-053-stolyarova-1991")
    row = replace(
        row,
        identity=replace(row.identity, fO2_Pa=State.of(Decimal("1e-4"))),
        notices=(
            *row.notices,
            Notice(
                kind=NoticeKind.PRESSURE_PROVENANCE_UNKNOWN,
                affected_quantities=(Quantity.P_PARTIAL,),
                reason="fO2_Pa is a DERIVED condition from scorer context",
                origin="test derived condition",
            ),
        ),
    )
    requests, handles = _capture_engine(monkeypatch, row)
    compared = _capture_comparison_identities(monkeypatch)
    scoring_context = replace(
        source_context,
        observations={**source_context.observations, row.observation_id: row},
    )
    point_observations = tuple(
        observation
        for observation in scoring_context.observations.values()
        if observation.experiment_id == row.experiment_id
    )

    residual, candidate = compile_residual(
        row,
        Engine.OPENIMCC,
        context=scoring_context,
        handles=handles,
        point_observations=point_observations,
    )

    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert candidate is not None and residual.numeric is not None
    commanded_pa = Decimal(str(requests[0].po2_bar)) * Decimal("1e5")
    assert all(identity.fO2_Pa == State.of(commanded_pa) for identity in compared)
    assert candidate.identity.fO2_Pa == State.of(commanded_pa)
    selected = candidate.provenance["selected_oxygen_condition"]
    assert Decimal(selected["value_Pa"]) == commanded_pa
    assert selected["physics_rung"] == 2
    assert selected["route"] == "source_linked_oxygen_condition"


@pytest.mark.parametrize("printed_control", ("oxygen_pressure", "IW_buffer"))
def test_linked_oxygen_is_below_printed_controls_in_the_scorer(
    source_context, monkeypatch, printed_control
):
    from simulator.battery.fo2_ladder import resolve_row_fo2

    original = _row(source_context, "shornikov-1994-mullite-kems")
    temperature = Decimal("1200")
    identity = replace(
        original.identity,
        temperature_K=State.of(temperature),
        fO2_Pa=State.unknown("no target fO2 printed"),
    )
    linked = Located(
        State.of(Value.point_of("1e-4")),
        locator=original.locator,
        inference=Derivation(
            "source_oxygen_condition; same-cell measured O/O2",
            inputs=("same-cell-O2-observation",),
            parameters=(),
            output_unit="Pa",
        ),
    )
    row = replace(
        original,
        identity=identity,
        point_conditions={
            **(original.point_conditions or {}),
            "fO2_Pa": linked,
            "temperature_K": Located(State.of(temperature), locator=original.locator),
            "total_pressure_Pa": Located(
                State.of(Value.point_of("100000")), locator=original.locator
            ),
        },
        provenance={"source_oxygen_condition": {"observation_id": "same-cell-O2-observation"}},
    )
    experiment = source_context.experiments[row.experiment_id]
    experiment = replace(
        experiment,
        conditions={
            **experiment.conditions,
            "temperature_K": Located(State.of(temperature), locator=row.locator),
        },
    )
    if printed_control == "oxygen_pressure":
        experiment = replace(
            experiment,
            fO2_control=FO2Control(
                channel=State.unknown("channel not printed"),
                oxygen_partial_pressure_Pa=Located(
                    State.of(Value.point_of("1e-8")), locator=row.locator
                ),
            ),
        )
    else:
        experiment = replace(
            experiment,
            fO2_control=FO2Control(
                channel=State.of(FO2Channel.BUFFER),
                buffer=Located(State.of("IW"), locator=row.locator),
            ),
        )
    bench = _bench_for_score(experiment, source_context.benches)
    assert bench is not None
    linked_pa = linked_source_pressure(row)
    assert linked_pa is not None
    resolved = resolve_row_fo2(
        experiment,
        bench,
        row,
        composition=row.identity.composition.value,
        temperature_K=float(temperature),
        quantity=Quantity.P_PARTIAL,
        source_oxygen_bar=float(linked_pa) / 1.0e5,
    )
    assert resolved is not None and not resolved.closure
    assert resolved.route == (
        "oxygen_partial_pressure_to_log_fO2"
        if printed_control == "oxygen_pressure"
        else "buffer_relation"
    )

    requests, handles = _capture_engine(monkeypatch, row)
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        row,
        handles=handles,
        isolated=False,
        experiment=experiment,
        bench=bench,
    )
    expected_bar = resolved.po2_bar or 10.0 ** resolved.log10_fO2_bar
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(expected_bar)
    assert any(
        notice.reason == (
            f"fo2_tier={resolved.tier}; "
            f"physics_rung=1:printed_fO2_or_buffer; route={resolved.route}"
        )
        for notice in prediction.notices
    )
    assert not any(
        notice.origin == "source oxygen selection"
        and notice.reason.startswith("measured condition: same-cell source-linked oxygen;")
        for notice in prediction.notices
    )

    requests.clear()
    compared = _capture_comparison_identities(monkeypatch)
    scoring_context = replace(
        source_context,
        experiments={**source_context.experiments, experiment.experiment_id: experiment},
        observations={**source_context.observations, row.observation_id: row},
    )
    point_observations = tuple(
        observation for observation in source_context.observations.values()
        if observation.experiment_id == row.experiment_id
        and observation.observation_id != row.observation_id
    ) + (row,)
    residual, candidate = compile_residual(
        row,
        Engine.OPENIMCC,
        context=scoring_context,
        handles=handles,
        point_observations=point_observations,
    )
    assert residual.numeric is not None
    assert candidate is not None
    assert len(compared) == 2
    selected_pa = Decimal(str(requests[0].po2_bar)) * Decimal("1e5")
    assert all(identity.fO2_Pa == State.of(selected_pa) for identity in compared)
    selected = candidate.provenance["selected_oxygen_condition"]
    assert Decimal(selected["value_Pa"]) == selected_pa
    assert selected["fo2_tier"] == resolved.tier
    assert selected["physics_rung"] == 1
    assert selected["route"] == resolved.route
    assert _single_physics_rung(residual.notices) == (
        f"fo2_tier={resolved.tier}; physics_rung=1:printed_fO2_or_buffer; "
        f"route={resolved.route}"
    )


def test_conditioning_cell_gases_remain_visible_but_are_not_scored(source_context):
    row = _row(source_context, "shornikov-1994-mullite-kems", "MoO2")
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert residual.numeric is None and candidate is None
    assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
    assert residual.refusal.detail["derivation"]["inputs"]


def test_typed_1996_source_oxygen_commands_the_reduced_condition(
    tmp_path, monkeypatch
):
    from tests.battery.test_migrate import _migrate_real_extract

    migrated = _migrate_real_extract(
        tmp_path,
        "stolyarova-1996-cao-alumina-silica-kems.yaml",
        use_repository_index_row=True,
    )
    source_context = ScoreContext(
        works=migrated.works,
        experiments=migrated.experiments,
        observations=migrated.observations,
        benches=migrated.benches,
    )
    row = _row(source_context, "stolyarova-1996-cao-alumina-silica-kems", "AlO")
    condition = _resolve(source_context, row)
    assert isinstance(condition, SourceOxygenCondition)
    assert condition.observation.identity.species.formula == "O2"
    assert condition.observation.derived_from
    assert set(condition.observation.derived_from) <= condition.used_observations
    oxygen_row = source_context.observations[condition.observation.derived_from[0]]
    assert oxygen_row.identity.species.formula == "O"
    assert oxygen_row.evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    assert oxygen_row.identity.reaction is not None
    assert oxygen_row.identity.reaction.is_value
    assert {
        term.species.formula
        for term in oxygen_row.identity.reaction.value.terms
    } == {"MoO3", "MoO2", "O"}

    requests, handles = _capture_engine(monkeypatch, row)
    residual, candidate = compile_residual(
        row,
        Engine.OPENIMCC,
        context=source_context,
        handles=handles,
        point_observations=tuple(source_context.observations.values()),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(
        float(condition.observation.value.point) / 1.0e5
    )
    assert residual.numeric is not None
    assert candidate is not None
    assert (
        candidate.provenance["source_oxygen_condition"]["observation_id"]
        == condition.observation.observation_id
    )


def test_stolyarova_1996_alternative_oxygen_columns_use_mo_cell_condition(source_context, tmp_path):
    from simulator.battery.migrate import load_migrated_context
    from simulator.battery.validate import validate_corpus
    from tests.battery.test_migrate import _migrate_real_extract
    from tests.battery.test_score import _series_stem

    source_id = "stolyarova-1996-cao-alumina-silica-kems"
    context = source_context
    oxygen_rows = tuple(
        observation
        for observation in context.observations.values()
        if "stolyarova_1996_table2_pO" in observation.observation_id
        and observation.identity.species is not None
        and observation.identity.species.formula in {"O", "O2"}
    )
    assert oxygen_rows
    oxygen_class = oxygen_rows[0].evidence.class_.value
    assert all(observation.evidence.class_.value is oxygen_class for observation in oxygen_rows)
    if oxygen_class is EvidenceClass.MODEL_DERIVED:
        fresh = _migrate_real_extract(
            tmp_path, f"{source_id}.yaml", use_repository_index_row=True,
        )
        stored_source_rows = {
            key: observation for key, observation in context.observations.items()
            if observation.source_id == source_id
        }
        # A stale committed sibling may have different :h= row hashes; the
        # series stems must still match the fresh migration.
        assert {
            _series_stem(observation_id) for observation_id in fresh.observations
        } == {
            _series_stem(observation_id) for observation_id in stored_source_rows
        }
        validation = validate_corpus(
            context.works,
            context.experiments,
            fresh.observations,
            benches=context.benches,
            context_rows=load_migrated_context(context.root),
        )
        context = replace(
            context,
            observations={**context.observations, **fresh.observations},
            hard_issues_by_observation={
                **context.hard_issues_by_observation,
                **validation.hard_issues_by_observation,
            },
        )
    else:
        assert oxygen_class is EvidenceClass.QUOTED_ATTRIBUTED

    row = _row(context, source_id, "AlO")
    condition = _resolve(context, row)
    assert isinstance(condition, SourceOxygenCondition)
    assert condition.observation.identity.species.formula == "O2"
    used = tuple(context.observations[observation_id] for observation_id in condition.used_observations)
    mo_cell_column = tuple(
        observation for observation in used
        if observation.identity.species.formula == "O"
        and "stolyarova_1996_table2_pO_1933k" in observation.observation_id
    )
    assert len(mo_cell_column) == 1
    assert mo_cell_column[0].evidence.class_.value is EvidenceClass.QUOTED_ATTRIBUTED
    reaction = mo_cell_column[0].identity.reaction
    assert reaction is not None and reaction.is_value
    assert {term.species.formula for term in reaction.value.terms} == {"MoO2", "MoO3", "O"}


def test_untyped_oxygen_lineage_fixture_keeps_closure(source_context, monkeypatch):
    context = source_context
    row = _row(context, "stolyarova-1996-cao-alumina-silica-kems", "AlO")
    oxygen = replace(
        row,
        observation_id="untyped-oxygen-lineage-fixture",
        identity=replace(
            row.identity,
            species=Species("O", Phase.G),
            composition=State.unknown("oxygen column does not carry a typed composition"),
        ),
        evidence=replace(row.evidence, class_=State.of(EvidenceClass.MODEL_DERIVED)),
        derivation=None,
        derived_from=(),
    )
    rows = (row, oxygen)
    assert _resolve(context, row, rows).reason is FallbackReason.UNTYPED_LINEAGE
    notice = _fallback_notice(context, monkeypatch, row, rows)
    assert "source_oxygen_fallback=lineage_untyped" in notice.reason


def test_no_oxygen_in_supplied_point_records_is_reported(source_context, monkeypatch):
    row = _row(source_context, "kems-053-stolyarova-1991")
    assert _resolve(source_context, row, (row,)).reason is FallbackReason.NO_PRINTED_CONDITION
    notice = _fallback_notice(source_context, monkeypatch, row, (row,))
    assert "source_oxygen_fallback=no_oxygen_condition_printed" in notice.reason


@pytest.mark.parametrize("axis", ("temperature_K", "composition"))
def test_missing_point_axes_report_untyped_linkage(source_context, axis):
    row = _row(source_context, "kems-053-stolyarova-1991")
    row = replace(row, identity=replace(row.identity, **{axis: State.unknown("not printed")}))
    assert _resolve(source_context, row).reason is FallbackReason.UNTYPED_LINEAGE


def test_missing_temperature_is_labeled_as_physics_rung_four_refusal(
    source_context, monkeypatch
):
    row = _row(source_context, "kems-053-stolyarova-1991")
    row = replace(
        row,
        identity=replace(
            row.identity, temperature_K=State.unknown("temperature not printed")
        ),
    )
    experiment = source_context.experiments[row.experiment_id]
    requests, handles = _capture_engine(monkeypatch, row)
    prediction = predict_with_engine(
        Engine.OPENIMCC,
        row,
        handles=handles,
        experiment=experiment,
        bench=_bench_for_score(experiment, source_context.benches),
    )
    assert prediction.value is None
    assert prediction.refusal_detail["reason"] == "temperature_unknown"
    assert requests == []
    assert _single_physics_rung(prediction.notices) == "physics_rung=4:refusal"


@pytest.mark.parametrize("axis", ("source", "temperature", "composition"))
def test_source_oxygen_never_matches_another_point(source_context, axis):
    row = _row(source_context, "kems-053-stolyarova-1991")
    if axis == "source":
        row = replace(row, source_id="another-source")
    elif axis == "temperature":
        row = replace(row, identity=replace(row.identity, temperature_K=State.of(Decimal("1934"))))
    else:
        composition = replace(row.identity.composition.value, components=(("CaO", Decimal("0.9")), ("SiO2", Decimal("0.1"))))
        row = replace(row, identity=replace(row.identity, composition=State.of(composition)))
    assert isinstance(_resolve(source_context, row), SourceOxygenFallback)


def test_multiple_measured_oxygen_conditions_are_not_averaged(source_context, monkeypatch):
    row = _row(source_context, "kems-053-stolyarova-1991")
    condition = _resolve(source_context, row)
    original = source_context.observations[next(iter(condition.used_observations))]
    measured = replace(
        original,
        evidence=replace(original.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)),
        identity=replace(original.identity, reaction=State.unknown("direct pressure")),
    )
    # Two measured atomic-O rows stay ambiguous. This copy used to be
    # restamped to O2; that pair is now the co-measured condition below.
    other = replace(measured, observation_id="other-printed-oxygen")
    rows = (row, measured, other)
    assert _resolve(source_context, row, rows).reason is FallbackReason.AMBIGUOUS
    notice = _fallback_notice(source_context, monkeypatch, row, rows)
    assert "source_oxygen_fallback=several_candidates_survive" in notice.reason


def test_oxygen_from_any_sample_target_is_excluded(source_context, monkeypatch):
    row = _row(source_context, "kems-053-stolyarova-1991")
    condition = _resolve(source_context, row)
    original = source_context.observations[next(iter(condition.used_observations))]
    magnesium = replace(row, observation_id="sample-Mg", identity=replace(row.identity, species=Species("Mg", Phase.G)))
    oxygen = replace(original, derived_from=(magnesium.observation_id,))
    rows = (row, magnesium, oxygen)
    assert _resolve(source_context, row, rows).reason is FallbackReason.SAMPLE_DERIVED
    notice = _fallback_notice(source_context, monkeypatch, row, rows)
    assert "source_oxygen_fallback=derived_from_scored_sample_species" in notice.reason


def test_typed_source_inconsistency_prevents_conditioning(source_context, monkeypatch):
    from simulator.battery.enums import NoticeKind
    from simulator.battery.records import Notice, SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX

    row = _row(source_context, "kems-053-stolyarova-1991")
    original = source_context.observations[next(iter(_resolve(source_context, row).used_observations))]
    oxygen = replace(original, notices=(*original.notices, Notice(
        kind=NoticeKind.SOURCE_DISAGREEMENT,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason=SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX + " conflicting printed O/O2",
        origin="source table consistency audit",
    )))
    rows = (row, oxygen)
    assert _resolve(source_context, row, rows).reason is FallbackReason.SOURCE_INCONSISTENT
    notice = _fallback_notice(source_context, monkeypatch, row, rows)
    assert "source_oxygen_fallback=source_inconsistent" in notice.reason


def test_printed_command_is_preserved(source_context, monkeypatch):
    # The selector now runs for a printed fO2 as well, so a condition input
    # is refused either way (d-216 fix round 1). This SiO row is not an
    # input, and the printed command is still what the engine receives.
    row = _row(source_context, "kems-053-stolyarova-1991")
    row = replace(row, identity=replace(row.identity, fO2_Pa=State.of(Decimal("1e-5"))))
    requests, handles = _capture_engine(monkeypatch, row)
    compile_residual(
        row, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(1e-10)


def test_measured_oxygen_precedes_cell_ratio(source_context):
    row = _row(source_context, "kems-053-stolyarova-1991")
    original = source_context.observations[next(iter(_resolve(source_context, row).used_observations))]
    measured = replace(
        original, observation_id="directly-measured-O2",
        identity=replace(original.identity, species=Species("O2", Phase.G), reaction=State.unknown("direct measurement")),
        evidence=replace(original.evidence, class_=State.of(EvidenceClass.MEASURED_DIRECT)),
    )
    selected = _resolve(source_context, row, (row, original, measured))
    assert selected.observation.value.point == measured.value.point
    assert selected.used_observations == frozenset({measured.observation_id})


def test_squared_atomic_oxygen_does_not_reuse_atomic_uncertainty(source_context):
    from simulator.battery.enums import UncertaintyKind
    from simulator.battery.records import Uncertainty

    row = _row(source_context, "kems-053-stolyarova-1991")
    original = source_context.observations[next(iter(_resolve(source_context, row).used_observations))]
    oxygen = replace(
        original,
        uncertainty=Uncertainty(kind=UncertaintyKind.PRINTED, verbatim="atomic O uncertainty", value=Decimal("1e-7"), basis="Pa"),
    )
    selected = _resolve(source_context, row, (row, oxygen))
    assert selected.observation.uncertainty.kind is UncertaintyKind.NONE
    assert selected.observation.uncertainty.value is None
    assert oxygen.observation_id in selected.observation.derived_from


@pytest.mark.parametrize("key", ("fO2_Pa", "oxygen_partial_pressure_Pa"))
def test_unit_conversion_of_printed_oxygen_is_still_printed(source_context, key):
    from simulator.battery.migrate import conversion_derivation
    from simulator.battery.records import Located, Value
    from simulator.battery.score import _has_printed_fo2, _source_oxygen_pressure_for_score

    row = _row(source_context, "shornikov-1994-mullite-kems")
    row = replace(
        row,
        point_conditions={key: Located(
            State.of(Value.point_of(Decimal("0.0000046508175"))), locator=row.locator,
            inference=conversion_derivation("atm_to_Pa", Decimal("4.59e-11"), row.locator),
        )},
    )
    assert _has_printed_fo2(row, row.identity)
    assert _source_oxygen_pressure_for_score(row) == Decimal("0.0000046508175")


def test_shornikov_2020_all_printed_oxygen_stamps_are_accepted():
    from simulator.battery.score import _has_printed_fo2

    context = load_score_context(Path(__file__).resolve().parents[2], sources=("shornikov-2020-cao-tio2-melts",))
    rows = [
        row for row in context.observations.values()
        if "fO2_Pa" in (row.point_conditions or {})
        and row.identity.quantity.value is Quantity.P_PARTIAL
    ]
    assert len(rows) == 240
    assert all(_has_printed_fo2(row, row.identity) for row in rows)


def test_printed_point_oxygen_keeps_unmodelled_reactive_cell_notice(source_context, monkeypatch):
    from simulator.battery.enums import CellMaterial, NoticeKind
    from simulator.battery.records import Located, Value
    from simulator.battery.score import predict_with_engine

    row = _row(source_context, "shornikov-1994-mullite-kems")
    row = replace(row, point_conditions={"fO2_Pa": Located(State.of(Value.point_of("4.6508175e-6")), locator=row.locator)})
    experiment = source_context.experiments[row.experiment_id]
    bench = replace(_bench_for_score(experiment, source_context.benches), cell_materials=(Located(State.of(CellMaterial.TA), locator=row.locator),))
    requests = []
    request_type = battery.Po2Request

    def capture_request(**kwargs):
        request = request_type(**kwargs)
        requests.append(request)
        return request

    monkeypatch.setattr(battery, "Po2Request", capture_request)
    monkeypatch.setattr(
        battery, "open_battery_engine",
        lambda name: battery.EngineHandle(name=name, backend=None, available=False, unavailable_reason="test", takes_fo2=False, supports_intrinsic_fo2=True),
    )
    prediction = predict_with_engine(Engine.OPENIMCC, row, experiment=experiment, bench=bench)
    assert any(request.mode == battery.PO2_COMMANDED and request.po2_bar == pytest.approx(4.6508175e-11) for request in requests)
    assert any(notice.kind is NoticeKind.REACTIVE_CELL_NOT_MODELLED for notice in prediction.notices)


# Shornikov's printed pair at 1933 K: O = 2.65e-9 atm, O2 = 4.59e-11 atm.
_ATM_PA = Decimal("101325")
_SHORNIKOV_O_PA = Decimal("2.65e-9") * _ATM_PA
_SHORNIKOV_O2_PA = Decimal("4.59e-11") * _ATM_PA


def _melt_composition():
    from simulator.battery.enums import AmountBasis
    from simulator.battery.records import Composition

    return State.of(Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.6")), ("SiO2", Decimal("0.4"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    ))


def _partial_pressure(observation_id, formula, value_pa, *, evidence=EvidenceClass.MEASURED_DIRECT):
    from simulator.battery.identity import Identity
    from tests.battery.factories import observation

    row = observation(
        observation_id,
        "exp-comeasured",
        Identity(
            quantity=Quantity.P_PARTIAL,
            species=Species(formula, Phase.G),
            temperature_K=State.of(Decimal("1933")),
            composition=_melt_composition(),
        ),
        value_pa,
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id="synthetic-comeasured",
    )
    if evidence is not EvidenceClass.MEASURED_DIRECT:
        row = replace(row, evidence=replace(row.evidence, class_=State.of(evidence)))
    return row


def _pair_rows(o_uncertainty=None, o2_uncertainty=None):
    sample = _partial_pressure("sample-sio", "SiO", Decimal("1"))
    # O is measured_reduced with no derived_from: the factory would invent
    # a parent, which fails measured eligibility.
    atomic = _partial_pressure(
        "printed-o", "O", _SHORNIKOV_O_PA, evidence=EvidenceClass.MEASURED_REDUCED,
    )
    molecular = _partial_pressure("printed-o2", "O2", _SHORNIKOV_O2_PA)
    if o_uncertainty is not None:
        atomic = replace(atomic, uncertainty=o_uncertainty)
    if o2_uncertainty is not None:
        molecular = replace(molecular, uncertainty=o2_uncertainty)
    return sample, (sample, atomic, molecular)


def test_rys_oxygen_selection_requires_independent_measurement_status():
    sample = _partial_pressure("rys-sample", "SiO", Decimal("1"))
    sample = replace(sample, source_id=RYS_SOURCE_ID)
    oxygen = _partial_pressure("rys-o2", "O2", Decimal("1e-8"))
    oxygen = replace(
        oxygen,
        source_id=RYS_SOURCE_ID,
        provenance={"oxygen_measurement_status": "unknown"},
    )

    rejected = resolve_source_oxygen(sample, (sample, oxygen), None)
    assert isinstance(rejected, SourceOxygenFallback)
    assert rejected.reason is FallbackReason.UNTYPED_LINEAGE

    oxygen = replace(
        oxygen,
        provenance={"oxygen_measurement_status": "measured_independent"},
    )
    selected = resolve_source_oxygen(sample, (sample, oxygen), None)
    assert isinstance(selected, SourceOxygenCondition)
    assert selected.observation.derivation is not None
    assert selected.observation.derivation.inputs == (oxygen.observation_id,)


def _mo_pair_atomic_oxygen(*, evidence, derived_from=()):
    sample = _partial_pressure("mo-target", "SiO", Decimal("1"))
    moo2 = _partial_pressure("mo-cell-moo2", "MoO2", Decimal("1"))
    moo3 = _partial_pressure("mo-cell-moo3", "MoO3", Decimal("1"))
    atomic = _partial_pressure("mo-cell-oxygen", "O", Decimal("1e-9"), evidence=evidence)
    atomic = replace(
        atomic,
        identity=replace(
            atomic.identity,
            reaction=State.of(Reaction((
                ReactionTerm(Species("MoO2", Phase.G), 1),
                ReactionTerm(Species("MoO3", Phase.G), -1),
                ReactionTerm(Species("O", Phase.G), 1),
            ))),
        ),
        derived_from=derived_from,
        derivation=None,
    )
    bench = Bench(
        id="mo-cell-bench", work_id="synthetic-comeasured",
        identity=BenchIdentity(
            basis=BenchIdentityBasis.INFERRED_FROM_EMBEDDED_EVIDENCE,
            reason="literal Mo-cell fixture",
        ),
        cell_materials=(Located(State.of(CellMaterial.MO), locator=atomic.locator),),
    )
    return sample, moo2, moo3, atomic, bench


def test_mo_pair_model_derived_without_lineage_is_refused():
    sample, moo2, moo3, atomic, bench = _mo_pair_atomic_oxygen(evidence=EvidenceClass.MODEL_DERIVED)
    assert atomic.derived_from == ()
    selected = resolve_source_oxygen(sample, (sample, moo2, moo3, atomic), bench)
    assert isinstance(selected, SourceOxygenFallback)
    assert selected.reason is FallbackReason.UNTYPED_LINEAGE


@pytest.mark.parametrize("evidence", (
    EvidenceClass.QUOTED_ATTRIBUTED,
    EvidenceClass.MEASURED_REDUCED,
    EvidenceClass.MEASURED_DIRECT,
))
def test_mo_pair_atomic_oxygen_is_admitted(evidence):
    sample, moo2, moo3, atomic, bench = _mo_pair_atomic_oxygen(evidence=evidence)
    selected = resolve_source_oxygen(sample, (sample, moo2, moo3, atomic), bench)
    assert isinstance(selected, SourceOxygenCondition)
    assert "mo-cell-oxygen" in selected.used_observations


@pytest.mark.parametrize("source,calcium_series,oxygen_series", (
    ("stolyarova-1995-cao-alumina-kems",
     "stolyarova_1995_ca_partial_pressure_table2",
     "stolyarova_1995_o_partial_pressure_eqn1_table2"),
    ("shornikov-1997-cao-alumina-vapor",
     "shornikov_1997_ca_pressure_table2_1933k",
     "shornikov_1997_o_pressure_eq1_table2_1933k"),
))
def test_measured_cell_eqn1_commands_all_eight_ca_points(source, calcium_series, oxygen_series):
    context = load_score_context(sources=(source,))
    calcium = [row for row in context.observations.values()
               if calcium_series in row.observation_id]
    assert len(calcium) == 8
    for row in calcium:
        selected = _resolve(context, row)
        assert isinstance(selected, SourceOxygenCondition), (row.observation_id, selected)
        assert len(selected.used_observations) == 1
        command = context.observations[next(iter(selected.used_observations))]
        assert oxygen_series in command.observation_id
        assert command.evidence.class_.value is EvidenceClass.MEASURED_REDUCED
        assert "cell-metal activity as assumed by the source" in selected.observation.derivation.relation


@pytest.mark.parametrize("oxygen_reduction", (False, True))
def test_unprinted_currents_do_not_imply_an_unidentified_oxygen_input(oxygen_reduction):
    from simulator.battery.enums import RawInputStatus

    sample, moo2, moo3, atomic, bench = _mo_pair_atomic_oxygen(
        evidence=EvidenceClass.MEASURED_REDUCED,
    )
    molecular = _partial_pressure("measured-o2", "O2", Decimal("1e-12"))
    molecular = replace(
        molecular,
        derived_from=(),
        derivation=Derivation(
            relation="printed reduction", inputs=(), parameters=(), output_unit="Pa",
            raw_inputs=Located(State.of(RawInputStatus.NOT_PRINTED), locator=molecular.locator),
        ),
        identity=replace(molecular.identity, reaction=(
            State.of(Reaction((
                ReactionTerm(Species("O2", Phase.G), -1),
                ReactionTerm(Species("O", Phase.G), 2),
            ))) if oxygen_reduction else State.unknown("direct KEMS pressure reduction")
        )),
    )
    selected = resolve_source_oxygen(sample, (sample, moo2, moo3, atomic, molecular), bench)
    assert isinstance(selected, SourceOxygenCondition)
    expected = atomic if oxygen_reduction else molecular
    assert selected.used_observations == frozenset({expected.observation_id})


def test_mo_pair_atomic_oxygen_with_derived_from_is_admitted():
    sample, moo2, moo3, atomic, bench = _mo_pair_atomic_oxygen(evidence=EvidenceClass.MODEL_DERIVED)
    atomic = replace(
        atomic,
        derived_from=("mo-cell-moo2", "mo-cell-moo3"),
    )
    selected = resolve_source_oxygen(sample, (sample, moo2, moo3, atomic), bench)
    assert isinstance(selected, SourceOxygenCondition)
    assert {
        "mo-cell-oxygen", "mo-cell-moo2", "mo-cell-moo3",
    } <= selected.used_observations


def _selected_pair(o_uncertainty=None, o2_uncertainty=None):
    sample, rows = _pair_rows(o_uncertainty, o2_uncertainty)
    return resolve_source_oxygen(sample, rows, None)


def _printed_uncertainty(verbatim, value=None, basis=None):
    from simulator.battery.enums import UncertaintyKind
    from simulator.battery.records import Uncertainty

    return Uncertainty(kind=UncertaintyKind.PRINTED, verbatim=verbatim, value=value, basis=basis)


def _pressure_percent_verbatim(percent):
    return _printed_uncertainty({
        "kind": "printed",
        "verbatim": (
            "Table 1 pressures have an error no greater than "
            f"{percent}%; no point-specific uncertainty is printed."
        ),
    })


def _structured_percent(percent):
    # The 2020 extract shape: the percent lives in the verbatim mapping,
    # and the prose does not say "pressure".
    return _printed_uncertainty({
        "relative_error_percent": {
            "bound_operator": "<",
            "bound_value": Decimal(percent),
            "units": "%",
        },
        "verbatim": f"error less than {percent}%",
    })


def _consistency_notice(selected):
    return next(
        (
            notice for notice in selected.observation.notices
            if notice.origin == "co-measured oxygen consistency"
        ),
        None,
    )


def _independent_shornikov_1933_dex():
    """Shornikov's 1933 K pair from the JANAF table, not the production helper.

    JANAF O-001 is 1/2 O2 = O, so pO/(1 bar) = K_O * sqrt(pO2/(1 bar)) and
    K_O = exp(-ΔfG/(R T)) with ΔfG converted from kJ/mol to J/mol. Gibbs at
    1933 K is the linear interpolation of the bracketing tabulated points.
    """
    import math

    from simulator.physical_constants import GAS_CONSTANT
    from simulator.reference_data.janaf import load_table_document

    path = (
        Path(__file__).resolve().parents[2]
        / "data/literature/compilations/janaf/tables/O-001.yaml"
    )
    document = load_table_document(path)
    points = []
    for row in document["table"]["values"]:
        temperature = row.get("temperature") if isinstance(row, dict) else None
        gibbs = row.get("formation_gibbs_energy") if isinstance(row, dict) else None
        t_value = temperature.get("value") if isinstance(temperature, dict) else None
        g_value = gibbs.get("value") if isinstance(gibbs, dict) else None
        if t_value is None or g_value is None:
            continue
        points.append((Decimal(str(t_value)), Decimal(str(g_value))))
    points.sort()
    temperature = Decimal("1933")
    left, right = next(
        (earlier, later)
        for earlier, later in zip(points, points[1:])
        if earlier[0] <= temperature <= later[0]
    )
    span = right[0] - left[0]
    gibbs = left[1] + (right[1] - left[1]) * (temperature - left[0]) / span
    constant = math.exp(-float(gibbs) * 1000.0 / (GAS_CONSTANT * float(temperature)))
    # Decimal square, not the production float expression: q = (pO_bar / K_O)^2.
    o_bar = _SHORNIKOV_O_PA / Decimal(100000)
    implied_pa = (o_bar / Decimal(str(constant))) ** 2 * Decimal(100000)
    dex = (_SHORNIKOV_O2_PA / implied_pa).ln() / Decimal(10).ln()
    gap = abs(_SHORNIKOV_O2_PA / implied_pa - 1)
    return dex, gap


def test_comeasured_pair_selects_printed_o2():
    selected = _selected_pair()
    assert isinstance(selected, SourceOxygenCondition)
    assert selected.observation.identity.species.formula == "O2"
    assert selected.observation.value.point == _SHORNIKOV_O2_PA
    relation = selected.observation.derivation.relation
    assert "printed pO2 in Pa (recorded source-unit conversion)" in relation
    assert "cell-metal activity" not in relation
    assert "printed-o" in selected.observation.derivation.inputs
    assert selected.observation.derivation.inputs == selected.observation.derived_from


def test_comeasured_pair_records_o_input_and_independent_dex():
    selected = _selected_pair()
    assert isinstance(selected, SourceOxygenCondition)
    derivation = selected.observation.derivation
    assert "printed-o" in derivation.inputs
    assert "printed-o2" in derivation.inputs
    parameters = dict(derivation.parameters)
    for name in ("pO2_from_O_Pa", "consistency_dex", "K_O", "JANAF_O_001_delta_fG_kJ_mol"):
        assert parameters[name].locator.source_path.endswith("/O-001.yaml")
        assert parameters[name].locator.record
    dex = parameters["consistency_dex"].state.value
    independent, gap = _independent_shornikov_1933_dex()
    assert abs(dex - independent) < Decimal("1e-8")
    assert independent < 0
    assert abs(independent - Decimal("-0.0016")) < Decimal("0.0002")
    assert Decimal("0.003") < gap < Decimal("0.004")


@pytest.mark.parametrize(
    ("o_uncertainty", "o2_uncertainty"),
    (
        (None, _pressure_percent_verbatim("0.0001")),
        (None, _printed_uncertainty("stated relative pressure uncertainty", Decimal("1e-8"), "relative")),
        (None, _structured_percent("0.0001")),
    ),
    ids=("verbatim-0.0001-percent", "relative-1e-8", "structured-0.0001-percent"),
)
def test_comeasured_consistency_notice_fires(o_uncertainty, o2_uncertainty):
    from simulator.battery.enums import NoticeKind, UncertaintyKind
    from simulator.battery.records import SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX

    selected = _selected_pair(o_uncertainty, o2_uncertainty)
    assert isinstance(selected, SourceOxygenCondition)
    notice = _consistency_notice(selected)
    assert notice is not None
    assert notice.kind is NoticeKind.SOURCE_DISAGREEMENT
    assert notice.affected_quantities == (Quantity.P_PARTIAL,)
    assert notice.reason.startswith("co_measured_o_o2_consistency:")
    assert not notice.reason.startswith(SOURCE_INTERNALLY_INCONSISTENT_REASON_PREFIX)
    if o2_uncertainty is not None:
        assert selected.observation.uncertainty == o2_uncertainty
        assert selected.observation.uncertainty.kind is not UncertaintyKind.NONE


@pytest.mark.parametrize(
    ("o_uncertainty", "o2_uncertainty"),
    (
        (None, None),
        (_pressure_percent_verbatim("8"), _pressure_percent_verbatim("8")),
        (
            None,
            _printed_uncertainty(
                "Table pressures have an error no greater than 0.0001%",
                Decimal("0.5"),
                "relative",
            ),
        ),
        (_printed_uncertainty("stated relative pressure uncertainty", Decimal(1) - Decimal(10) ** Decimal("-0.001"), "relative"), None),
        (None, _structured_percent("10")),
    ),
    ids=(
        "none",
        "verbatim-8-percent",
        "relative-0.5-beats-tiny-verbatim",
        "atomic-squared-envelope",
        "structured-10-percent",
    ),
)
def test_comeasured_consistency_notice_does_not_fire(o_uncertainty, o2_uncertainty):
    selected = _selected_pair(o_uncertainty, o2_uncertainty)
    assert isinstance(selected, SourceOxygenCondition)
    assert _consistency_notice(selected) is None
    names = [name for name, _located in selected.observation.derivation.parameters]
    assert "consistency_dex" in names
    assert "pO2_from_O_Pa" in names


@pytest.mark.parametrize(
    "formulas",
    (("O", "O"), ("O2", "O2"), ("O", "O2", "O2")),
    ids=("two-o", "two-o2", "three"),
)
def test_other_oxygen_multiplicities_stay_ambiguous(formulas):
    sample = _partial_pressure("sample-sio", "SiO", Decimal("1"))
    rows = [sample]
    for index, formula in enumerate(formulas):
        rows.append(_partial_pressure(f"oxygen-{index}", formula, Decimal("1e-6") * (index + 1)))
    selected = resolve_source_oxygen(sample, rows, None)
    assert isinstance(selected, SourceOxygenFallback)
    assert selected.reason is FallbackReason.AMBIGUOUS


@pytest.mark.parametrize(
    "case",
    (
        "rejected-o",
        "rejected-o2",
        "tabulated-o",
        "derived-o2",
        "unlocated-o",
        "both-rejected",
    ),
)
def test_ineligible_oxygen_partner_is_not_the_co_measured_condition(monkeypatch, case):
    from simulator.battery.enums import AdmissionStatus

    sample = _partial_pressure("sample-sio", "SiO", Decimal("1"))
    atomic = _partial_pressure("printed-o", "O", _SHORNIKOV_O_PA)
    molecular = _partial_pressure("printed-o2", "O2", _SHORNIKOV_O2_PA)
    if case == "rejected-o":
        atomic = replace(atomic, admission=replace(atomic.admission, status=AdmissionStatus.REJECTED))
    elif case == "rejected-o2":
        molecular = replace(molecular, admission=replace(molecular.admission, status=AdmissionStatus.REJECTED))
    elif case == "tabulated-o":
        atomic = replace(
            atomic,
            evidence=replace(atomic.evidence, class_=State.of(EvidenceClass.MEASURED_TABULATED)),
        )
    elif case == "derived-o2":
        molecular = replace(molecular, derived_from=("printed-o",))
    elif case == "unlocated-o":
        atomic = replace(atomic, locator=None)
    elif case == "both-rejected":
        atomic = replace(atomic, admission=replace(atomic.admission, status=AdmissionStatus.REJECTED))
        molecular = replace(
            molecular, admission=replace(molecular.admission, status=AdmissionStatus.REJECTED),
        )
    else:
        raise AssertionError(case)

    def fail_janaf(*_args, **_kwargs):
        raise AssertionError("an ineligible partner must not evaluate JANAF K_O")

    monkeypatch.setattr("simulator.reference_data.janaf._fusion_table_points", fail_janaf)
    selected = resolve_source_oxygen(sample, (sample, atomic, molecular), None)
    assert isinstance(selected, SourceOxygenFallback)
    assert selected.reason is FallbackReason.AMBIGUOUS


@pytest.mark.parametrize(
    ("pressure_atm", "expected_pa"),
    (
        ("6.8e-10", "3.073062242627939E-7"),
        ("2.65e-9", "4.667080363074115E-6"),
    ),
    ids=("W-6.8e-10-atm", "Shornikov-2.65e-9-atm"),
)
def test_atomic_oxygen_conversion_is_bit_identical(pressure_atm, expected_pa):
    """One measured atomic-O row at 1933 K, with no O2 partner.

    The stored pascal values are the atomic branch's Decimal(str(float))
    output. W 6.8e-10 atm is 3.073062242627939e-7 Pa, which is the
    3.0730622426279393e-12 bar figure after the pascal string round-trip.
    Shornikov's 2.65e-9 atm is 4.667080363074115e-6 Pa
    (4.6670803630741150e-11 bar). The literals are those stored numbers.
    """
    from simulator.battery.enums import AmountBasis
    from simulator.battery.identity import Identity
    from simulator.battery.records import Composition
    from tests.battery.factories import observation

    composition = State.of(Composition(
        basis="printed_mole_fraction",
        components=(("CaO", Decimal("0.6")), ("SiO2", Decimal("0.4"))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    ))
    temperature = State.of(Decimal("1933"))

    def partial(observation_id, formula, value_pa):
        return observation(
            observation_id,
            "exp-atomic-pin",
            Identity(
                quantity=Quantity.P_PARTIAL,
                species=Species(formula, Phase.G),
                temperature_K=temperature,
                composition=composition,
            ),
            value_pa,
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id="synthetic-atomic-pin",
        )

    sample = partial("sample-sio", "SiO", Decimal("1"))
    atomic = partial("printed-o", "O", Decimal(pressure_atm) * Decimal("101325"))
    selected = resolve_source_oxygen(sample, (sample, atomic), None)
    assert isinstance(selected, SourceOxygenCondition)
    assert selected.observation.value.point == Decimal(expected_pa)
    assert selected.observation.identity.species.formula == "O2"
    assert "pO2_Pa=100000*(pO_Pa/100000/K_O)^2" in selected.observation.derivation.relation


def test_atomic_oxygen_target_is_refused_before_engine_gets_derived_po2(
    source_context, monkeypatch,
):
    sample = next(
        row
        for row in source_context.observations.values()
        if row.source_id == "kems-053-stolyarova-1991"
        and row.identity.species.formula == "O"
        and row.identity.temperature_K.is_value
        and row.identity.temperature_K.value == Decimal("1933")
        and dict(row.identity.composition.value.components).get("CaO")
        == Decimal("0.6")
    )
    point_rows = tuple(
        row
        for row in source_context.observations.values()
        if row.experiment_id == sample.experiment_id
    )
    selected = resolve_source_oxygen(
        sample,
        point_rows,
        _bench_for_score(
            source_context.experiments[sample.experiment_id],
            source_context.benches,
        ),
    )
    assert isinstance(selected, SourceOxygenCondition)
    assert sample.observation_id in selected.used_observations

    requests, handles = _capture_engine(monkeypatch, sample)
    residual, candidate = compile_residual(
        sample,
        Engine.OPENIMCC,
        context=source_context,
        handles=handles,
        point_observations=point_rows,
    )
    assert residual.refusal is not None
    assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
    assert residual.numeric is None and candidate is None
    assert requests == []

    changed = replace(
        sample,
        value=Value.point_of(sample.value.point * Decimal("1.7")),
    )
    changed_context = replace(
        source_context,
        observations={**source_context.observations, changed.observation_id: changed},
    )
    changed_rows = tuple(
        changed if row.observation_id == changed.observation_id else row
        for row in point_rows
    )
    changed_residual, changed_candidate = compile_residual(
        changed,
        Engine.OPENIMCC,
        context=changed_context,
        handles=handles,
        point_observations=changed_rows,
    )
    assert changed_residual.refusal is not None
    assert changed_residual.refusal.detail["reason"] == "used_as_scoring_condition"
    assert changed_residual.numeric is None and changed_candidate is None
    assert requests == []


def test_printed_fo2_condition_input_is_not_scored(source_context):
    row = _row(source_context, "shornikov-1994-mullite-kems", "MoO2")
    stamped = replace(row, identity=replace(row.identity, fO2_Pa=State.of(Decimal("1e-5"))))
    residual, candidate = compile_residual(
        stamped, Engine.OPENIMCC, context=source_context,
        point_observations=tuple(
            r for r in source_context.observations.values()
            if r.experiment_id == row.experiment_id
        ),
    )
    assert residual.numeric is None and candidate is None
    assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
    assert residual.refusal.detail["derivation"]["inputs"]


def test_stolyarova_sio_pressure_equal_to_the_o2_condition_is_scored(source_context, monkeypatch):
    """A sample gas whose pressure equals the oxygen condition is still scored.

    Real Stolyarova 1933 K, X(CaO)=0.6 SiO. Printing identity fO2 equal to
    that SiO pressure used to refuse the row by numeric coincidence. Setting
    the SiO pressure itself equal to the selected O2 condition does too.
    The row is in neither derivation. A matching number is not an input.
    A synthetic 100000 Pa total completes the printed-command identity.
    The synthetic equal-pressure sample has no published uncertainty.
    """
    from simulator.battery.enums import UncertaintyKind
    from simulator.battery.records import Uncertainty, Value

    row = _row(source_context, "kems-053-stolyarova-1991")
    point_rows = tuple(
        item for item in source_context.observations.values()
        if item.experiment_id == row.experiment_id
    )
    selected = _resolve(source_context, row, point_rows)
    assert isinstance(selected, SourceOxygenCondition)
    assert row.observation_id not in selected.used_observations
    assert row.observation_id not in selected.observation.derivation.inputs

    stamped = replace(row, identity=replace(
        row.identity,
        fO2_Pa=State.of(row.value.point),
        total_pressure_Pa=State.of(Decimal("100000")),
    ))
    requests, handles = _capture_engine(monkeypatch, stamped)
    residual, candidate = compile_residual(
        stamped, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=point_rows,
    )
    assert residual.refusal is None
    assert residual.numeric is not None and candidate is not None
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(float(row.value.point) / 1e5, rel=1e-12)

    equal = replace(
        row,
        value=Value.point_of(selected.observation.value.point),
        uncertainty=Uncertainty(kind=UncertaintyKind.NONE),
    )
    requests, handles = _capture_engine(monkeypatch, equal)
    residual, candidate = compile_residual(
        equal, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=point_rows,
    )
    assert residual.refusal is None
    assert residual.numeric is not None and candidate is not None
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED


@pytest.mark.parametrize("units", ("atm", "Pa"))
@pytest.mark.parametrize("qualified", (False, True), ids=("local-id", "qualified-id"))
def test_migrated_printed_fo2_source_is_excluded_by_scorer(tmp_path, monkeypatch, units, qualified):
    """Real migrated rows/provenance, with complete synthetic KEMS apparatus.

    Ca's typed width comes from its preserved published percent envelope.
    """
    import yaml

    from simulator.battery.migrate import convert_pressure_to_pa
    from simulator.battery.score import ScoreContext, _convert_uncertainty_width, _has_printed_fo2, _printed_fo2_names_observation
    from tests.battery.factories import kems_experiment, migrate_one_extract

    root = Path(__file__).resolve().parents[2]
    path = root / "data/literature/extracts/shornikov-2020-cao-tio2-melts.yaml"
    document = yaml.safe_load(path.read_text())
    source_id = document["source_id"]
    oxygen_rows = [
        row for row in document["species"]["O2"]["observations"]
        if row["locator"].get("table") == "3"
    ]
    assert len(oxygen_rows) == 15
    for row in oxygen_rows:
        node = row["values"]["oxygen_partial_pressure"]
        token = row["observation_id"]
        node["source_observation_id"] = f"{source_id}::{token}" if qualified else token
        if units == "Pa":
            pressure, _trail = convert_pressure_to_pa(Decimal(str(node["value"])), node["units"])
            node.update(value=str(pressure), units="Pa")
    sample = next(
        row for row in document["species"]["Ca"]["observations"]
        if row["observation_id"] == "shornikov_2020_table3_ca_partial_pressure_2278k"
    )
    source_ref = oxygen_rows[0]["values"]["oxygen_partial_pressure"]["source_observation_id"]
    sample["values"]["oxygen_partial_pressure"]["source_observation_id"] = source_ref
    fixture = tmp_path / path.name
    fixture.write_text(yaml.safe_dump(document, sort_keys=False))
    migrated = migrate_one_extract(root, fixture).result
    assert not migrated.registry_issues
    sample_row = migrated.observations[f"{source_id}::{sample['observation_id']}"]
    printed_width = sample_row.uncertainty.verbatim["relative_error_percent"]
    width, basis = _convert_uncertainty_width(
        Quantity.P_PARTIAL, Decimal(str(printed_width["bound_value"])), printed_width["units"],
    )
    sample_row = replace(sample_row, uncertainty=replace(sample_row.uncertainty, value=width, basis=basis))
    prerequisites = kems_experiment()
    context = ScoreContext(
        works=migrated.works,
        experiments={
            key: replace(
                experiment,
                apparatus=prerequisites.apparatus,
                pressure_environment=replace(
                    experiment.pressure_environment,
                    regime=prerequisites.pressure_environment.regime,
                ),
            )
            for key, experiment in migrated.experiments.items()
        },
        observations={**migrated.observations, sample_row.observation_id: sample_row},
        benches=migrated.benches, root=root,
    )
    requests, handles = _capture_engine(monkeypatch, sample_row)
    point_rows = tuple(context.observations.values())
    for original in oxygen_rows:
        row = migrated.observations[f"{source_id}::{original['observation_id']}"]
        assert _has_printed_fo2(row, row.identity)
        inference = row.point_conditions["fO2_Pa"].inference
        assert row.observation_id in inference.inputs
        assert _printed_fo2_names_observation(row)
        assert inference.relation == ("atm_to_Pa" if units == "atm" else "Pa_to_Pa")
        parameters = dict(inference.parameters)
        assert parameters["factor"].state.value == (Decimal("101325") if units == "atm" else Decimal(1))
        assert parameters["original"].state.value == Decimal(str(original["values"]["oxygen_partial_pressure"]["value"]))
        residual, candidate = compile_residual(
            row, Engine.OPENIMCC, context=context, handles=handles,
            point_observations=point_rows,
        )
        # 45cea9498 types Table 3 O2 measured_reduced, so the row is the
        # selected source-oxygen condition. That exclusion lists derived_from
        # and does not set the printed-name match key (9c40ad728).
        assert residual.refusal is not None
        assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
        assert row.observation_id in residual.refusal.detail["derived_from"]
        assert residual.numeric is None and candidate is None
    assert requests == []
    row = sample_row
    assert _has_printed_fo2(row, row.identity)
    assert f"{source_id}::{oxygen_rows[0]['observation_id']}" in row.point_conditions["fO2_Pa"].inference.inputs
    assert not _printed_fo2_names_observation(row)
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=context, handles=handles,
        point_observations=point_rows,
    )
    assert residual.refusal is None
    assert residual.numeric is not None and candidate is not None
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(float(row.identity.fO2_Pa.value) / 1e5)


@pytest.mark.parametrize(
    "source_ref",
    (None, "", 123, "missing-oxygen", "another-source::oxygen",
     "shornikov_2020_table3_ca_partial_pressure_2278k", "not-printed"),
    ids=("unset", "empty", "wrong-type", "missing", "foreign-source", "sample-species", "not-printed"),
)
def test_migrated_printed_fo2_source_reference_validation(tmp_path, source_ref):
    import yaml

    from simulator.battery.enums import RefusalReason
    from simulator.battery.score import _printed_fo2_names_observation
    from tests.battery.factories import migrate_one_extract

    root = Path(__file__).resolve().parents[2]
    path = root / "data/literature/extracts/shornikov-2020-cao-tio2-melts.yaml"
    document = yaml.safe_load(path.read_text())
    row = document["species"]["O2"]["observations"][0]
    node = row["values"]["oxygen_partial_pressure"]
    node["source_observation_id"] = row["observation_id"] if source_ref == "not-printed" else source_ref
    if source_ref == "not-printed":
        node["inferred"] = True
    fixture = tmp_path / path.name
    fixture.write_text(yaml.safe_dump(document, sort_keys=False))
    migrated = migrate_one_extract(root, fixture).result
    target = migrated.observations[f"{document['source_id']}::{row['observation_id']}"]
    assert not _printed_fo2_names_observation(target)
    if source_ref is None:
        assert not migrated.registry_issues
    else:
        assert len(migrated.registry_issues) == 1
        issue = migrated.registry_issues[0]
        assert issue.reason is RefusalReason.REFERENTIAL_INTEGRITY
        assert issue.path.endswith(".values.oxygen_partial_pressure.source_observation_id")


def test_comeasured_consistency_notice_reaches_scored_residual(source_context, monkeypatch):
    from simulator.battery.enums import NoticeKind
    from simulator.battery.records import Notice

    row = _row(source_context, "kems-053-stolyarova-1991")
    _sample, pair = _pair_rows(o2_uncertainty=_pressure_percent_verbatim("0.0001"))
    unrelated = Notice(
        kind=NoticeKind.FIGURE_ONLY,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason="not the consistency check",
        origin="unrelated-o2-source-notice",
    )
    oxygen = []
    for item in pair:
        if item.identity.species.formula not in {"O", "O2"}:
            continue
        item = replace(
            item,
            experiment_id=row.experiment_id,
            source_id=row.source_id,
            identity=replace(
                row.identity,
                species=Species(item.identity.species.formula, Phase.G),
                reaction=State.unknown("printed pressure"),
            ),
        )
        if item.identity.species.formula == "O2":
            item = replace(item, notices=(unrelated,))
        oxygen.append(item)
    requests, handles = _capture_engine(monkeypatch, row)
    residual, _candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=(row, *oxygen),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert any(notice.origin == "co-measured oxygen consistency" for notice in residual.notices)
    assert not any(notice.origin == "unrelated-o2-source-notice" for notice in residual.notices)
    assert any(
        notice.reason.startswith("co_measured_o_o2_consistency:") for notice in residual.notices
    )


def test_comeasured_consistency_notice_reaches_printed_fo2_residual(source_context, monkeypatch):
    """A printed command stays. The pair's consistency notice still arrives.

    Shornikov's 1933 K O2 is 4.59e-11 atm = 4.6508175e-11 bar. The stated
    molecular relative width is 1e-8, which is inside the notice bound.
    """
    from simulator.battery.enums import NoticeKind
    from simulator.battery.records import Notice

    row = _row(source_context, "kems-053-stolyarova-1991")
    _sample, pair = _pair_rows(
        o2_uncertainty=_printed_uncertainty(
            "stated relative pressure uncertainty", Decimal("1e-8"), "relative",
        ),
    )
    unrelated = Notice(
        kind=NoticeKind.FIGURE_ONLY,
        affected_quantities=(Quantity.P_PARTIAL,),
        reason="not the consistency check",
        origin="unrelated-o2-source-notice",
    )
    oxygen = []
    for item in pair:
        if item.identity.species.formula not in {"O", "O2"}:
            continue
        item = replace(
            item,
            experiment_id=row.experiment_id,
            source_id=row.source_id,
            identity=replace(
                row.identity,
                species=Species(item.identity.species.formula, Phase.G),
                reaction=State.unknown("printed pressure"),
            ),
        )
        if item.identity.species.formula == "O2":
            item = replace(item, notices=(unrelated,))
        oxygen.append(item)
    printed = replace(row, identity=replace(row.identity, fO2_Pa=State.of(_SHORNIKOV_O2_PA)))
    requests, handles = _capture_engine(monkeypatch, printed)
    residual, _candidate = compile_residual(
        printed, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=(printed, *oxygen),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(4.6508175e-11, rel=1e-12)
    assert any(notice.origin == "co-measured oxygen consistency" for notice in residual.notices)
    assert not any(notice.origin == "unrelated-o2-source-notice" for notice in residual.notices)
    assert any(
        notice.reason.startswith("co_measured_o_o2_consistency:") for notice in residual.notices
    )


def test_relative_qms_oxygen_inputs_cannot_validate_themselves(
    source_context, monkeypatch
):
    melt_row = _row(source_context, "kems-053-stolyarova-1991")
    condition = _resolve(source_context, melt_row)
    assert isinstance(condition, SourceOxygenCondition)
    assert condition.observation.identity.species.formula == "O2"
    assert condition.observation.derived_from
    component = source_context.observations[condition.observation.derived_from[0]]
    assert component.observation_id in condition.used_observations

    trace = replace(
        melt_row,
        observation_id="relative-qms-o2-source-trace",
        identity=replace(
            melt_row.identity,
            species=Species("O2", Phase.G),
            quantity=Quantity.ION_INTENSITY,
            reaction=State.unknown("ion channel"),
            fO2_Pa=None,
        ),
        value=Value(ValueKind.POINT, point=Decimal("2")),
        derived_from=None,
        derivation=None,
    )
    scoring_context = replace(
        source_context,
        observations={
            **source_context.observations,
            trace.observation_id: trace,
        },
    )
    resolved = _resolve(scoring_context, melt_row)
    assert isinstance(resolved, SourceOxygenCondition)

    observed, model, anchor, clock, evidence = _relative_qms_inputs(
        trace, tuple(sorted((*resolved.used_observations, trace.observation_id)))
    )
    result = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        evidence,
        context=scoring_context,
        reference=melt_row,
        source_trace=trace,
    )
    assert result.unavailable_reason is RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    assert result.points == ()

    def self_agreement_contract():
        mutated = _score_relative_qms_series(
            observed,
            model,
            anchor,
            clock,
            evidence,
            context=scoring_context,
            reference=melt_row,
            source_trace=trace,
        )
        assert mutated.unavailable_reason is (
            RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            battery_score_metrics,
            "_relative_series_model_oxygen_unavailable_reason",
            lambda *_args, **_kwargs: None,
        )
        with pytest.raises(AssertionError):
            self_agreement_contract()

    mixed_model = (model[0], replace(model[1], run_id="other-model-run"))
    mixed_run = _score_relative_qms_series(
        observed,
        mixed_model,
        anchor,
        clock,
        evidence,
        context=scoring_context,
        reference=melt_row,
        source_trace=trace,
    )
    assert mixed_run.unavailable_reason is (
        RelativeSeriesUnavailableReason.MIXED_MODEL_RUNS
    )
    assert mixed_run.points == ()

    def mixed_run_contract():
        result = _score_relative_qms_series(
            observed,
            mixed_model,
            anchor,
            clock,
            evidence,
            context=scoring_context,
            reference=melt_row,
            source_trace=trace,
        )
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.MIXED_MODEL_RUNS
        )

    with monkeypatch.context() as patch:
        patch.setattr(
            battery_score_metrics,
            "_relative_series_model_oxygen_unavailable_reason",
            lambda *_args, **_kwargs: None,
        )
        with pytest.raises(AssertionError):
            mixed_run_contract()

    unrelated_id = replace(observed[0], observation_id=melt_row.observation_id)
    unrelated = (unrelated_id, *observed[1:])
    mismatched = _score_relative_qms_series(
        unrelated,
        model,
        anchor,
        clock,
        evidence,
        context=scoring_context,
        reference=melt_row,
        source_trace=trace,
    )
    assert mismatched.unavailable_reason is (
        RelativeSeriesUnavailableReason.SOURCE_TRACE_UNAVAILABLE
    )
    assert mismatched.points == ()

    unresolved_inputs = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        replace(evidence, model_oxygen_input_observation_ids=()),
        context=scoring_context,
        reference=melt_row,
        source_trace=trace,
    )
    assert unresolved_inputs.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert unresolved_inputs.points == ()

    duplicate_id = next(iter(resolved.used_observations))
    ambiguous_inputs = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        replace(
            evidence,
            model_oxygen_input_observation_ids=(duplicate_id, duplicate_id),
        ),
        context=scoring_context,
        reference=melt_row,
        source_trace=trace,
    )
    assert ambiguous_inputs.unavailable_reason is (
        RelativeSeriesUnavailableReason.MODEL_OXYGEN_INPUTS_UNAVAILABLE
    )

    fallback_trace = replace(trace, source_id="unresolved-oxygen-source")
    fallback_reference = replace(melt_row, source_id="unresolved-oxygen-source")
    fallback_context = replace(
        scoring_context,
        observations={
            **scoring_context.observations,
            fallback_trace.observation_id: fallback_trace,
            fallback_reference.observation_id: fallback_reference,
        },
    )
    fallback_observed = tuple(
        replace(point, observation_id=fallback_trace.observation_id)
        for point in observed
    )
    fallback = _score_relative_qms_series(
        fallback_observed,
        model,
        anchor,
        clock,
        replace(evidence, model_oxygen_input_observation_ids=()),
        context=fallback_context,
        reference=fallback_reference,
        source_trace=fallback_trace,
    )
    assert fallback.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert fallback.points == ()


def _printed_store_comparison(context, row):
    """Printed O2 command on one store row; the t-1913 producer is absent."""

    reference = replace(
        row,
        observation_id=f"{row.observation_id}:printed-o2",
        identity=replace(
            row.identity,
            species=Species("O2", Phase.G),
            quantity=Quantity.P_PARTIAL,
            fO2_Pa=State.of(Decimal("4.2e-7")),
        ),
        point_conditions=None,
        notices=(),
    )
    trace = replace(
        reference,
        observation_id=f"{row.observation_id}:ion-trace",
        identity=replace(
            reference.identity,
            quantity=Quantity.ION_INTENSITY,
            fO2_Pa=None,
        ),
        derived_from=None,
        derivation=None,
        value=Value(ValueKind.POINT, point=Decimal("2")),
    )
    scoring = replace(
        context,
        observations={
            **context.observations,
            reference.observation_id: reference,
            trace.observation_id: trace,
        },
    )
    observed, model, anchor, clock, evidence = _relative_qms_inputs(trace, ())
    assert evidence.reference_run_id == evidence.model_run_id == "claimed-run"
    assert {point.run_id for point in (*observed, *model)} == {"claimed-run"}
    return _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        evidence,
        context=scoring,
        reference=reference,
        source_trace=trace,
    )


def test_adapter_reports_missing_producer_for_demaria_store_row(demaria_context):
    result = _printed_store_comparison(
        demaria_context, _demaria_o_sample(demaria_context, "12022")
    )
    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert result.points == ()


def test_adapter_reports_missing_producer_for_costa_store_row(costa_context):
    row = next(
        item for item in costa_context.observations.values()
        if item.source_id == "kems-007-costa-2015"
        and item.identity.species is not None
        and item.identity.species.formula == "Fe"
        and item.identity.quantity.is_value
        and item.identity.quantity.value is Quantity.P_PARTIAL
    )
    result = _printed_store_comparison(costa_context, row)
    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert result.points == ()


def test_adapter_reports_missing_producer_for_synthetic_store_row(source_context):
    melt = _row(source_context, "kems-053-stolyarova-1991")
    alone = replace(source_context, observations={melt.observation_id: melt})
    result = _printed_store_comparison(alone, melt)
    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert result.points == ()


def test_probe_store_adapter_reports_capability_gap_not_run_ambiguity(
    demaria_context, monkeypatch
):
    row = _demaria_o_sample(demaria_context, "12022")

    def capability_contract():
        result = _printed_store_comparison(demaria_context, row)
        assert result.unavailable_reason is (
            RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
        )
        message = result.unavailable_reason.value
        assert "t-1913" in message
        assert "producer" in message
        assert "per-run key" in message
        assert "sample-coordinate contract" in message

    producer_gap = battery_score._qms_store_producer_unavailable
    with monkeypatch.context() as patch:
        patch.setattr(
            battery_score,
            "_qms_store_producer_unavailable",
            lambda: replace(
                producer_gap(),
                unavailable_reason=RelativeSeriesUnavailableReason.AMBIGUOUS_RUN_IDENTITY,
            ),
        )
        with pytest.raises(AssertionError):
            capability_contract()


def test_relative_qms_keeps_an_explicit_fO2_command(source_context):
    melt_row = _row(source_context, "kems-053-stolyarova-1991")
    command = Decimal("4.2e-7")
    reference = replace(
        melt_row,
        observation_id="relative-qms-explicit-fo2",
        identity=replace(
            melt_row.identity,
            species=Species("O2", Phase.G),
            quantity=Quantity.P_PARTIAL,
            fO2_Pa=State.of(command),
        ),
        point_conditions=None,
    )
    trace = replace(
        reference,
        observation_id="relative-qms-explicit-fo2-trace",
        identity=replace(
            reference.identity,
            quantity=Quantity.ION_INTENSITY,
            fO2_Pa=None,
        ),
        point_conditions=None,
        derived_from=None,
        derivation=None,
        value=Value(ValueKind.POINT, point=Decimal("2")),
    )
    scoring_context = replace(
        source_context,
        observations={
            **source_context.observations,
            reference.observation_id: reference,
            trace.observation_id: trace,
        },
    )
    observed, model, anchor, clock, evidence = _relative_qms_inputs(trace, ())

    result = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        evidence,
        context=scoring_context,
        reference=reference,
        source_trace=trace,
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert result.points == ()
    assert reference.identity.fO2_Pa == State.of(command)

    trace_input = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        replace(
            evidence,
            model_oxygen_input_observation_ids=(trace.observation_id,),
        ),
        context=scoring_context,
        reference=reference,
        source_trace=trace,
    )
    assert trace_input.unavailable_reason is (
        RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    )
    assert trace_input.points == ()

    converted_reference = replace(
        reference,
        point_conditions={
            "fO2_Pa": Located(
                State.of(command),
                locator=F.loc(page=1),
                inference=Derivation(
                    relation="Pa_to_Pa",
                    inputs=(trace.observation_id,),
                    parameters=(
                        ("original", Located(State.of(command))),
                        ("factor", Located(State.of(Decimal(1)))),
                    ),
                    output_unit="Pa",
                ),
            )
        },
    )
    converted_context = replace(
        scoring_context,
        observations={
            **scoring_context.observations,
            converted_reference.observation_id: converted_reference,
        },
    )
    converted_control = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        evidence,
        context=converted_context,
        reference=converted_reference,
        source_trace=trace,
    )
    assert converted_control.unavailable_reason is (
        RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    )
    assert converted_control.points == ()

    upstream_trace = replace(
        trace, observation_id="relative-qms-explicit-fo2-upstream"
    )
    derived_trace = replace(
        trace,
        observation_id="relative-qms-explicit-fo2-derived-trace",
        derived_from=(upstream_trace.observation_id,),
    )
    upstream_reference = replace(
        converted_reference,
        point_conditions={
            "fO2_Pa": replace(
                converted_reference.point_conditions["fO2_Pa"],
                inference=replace(
                    converted_reference.point_conditions[
                        "fO2_Pa"
                    ].inference,
                    inputs=(upstream_trace.observation_id,),
                ),
            )
        },
    )
    upstream_context = replace(
        converted_context,
        observations={
            **converted_context.observations,
            upstream_trace.observation_id: upstream_trace,
            derived_trace.observation_id: derived_trace,
            upstream_reference.observation_id: upstream_reference,
        },
    )
    upstream_observed = tuple(
        replace(point, observation_id=derived_trace.observation_id)
        for point in observed
    )
    upstream_control = _score_relative_qms_series(
        upstream_observed,
        model,
        anchor,
        clock,
        evidence,
        context=upstream_context,
        reference=upstream_reference,
        source_trace=derived_trace,
    )
    assert upstream_control.unavailable_reason is (
        RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    )
    assert upstream_control.points == ()

    named_input = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        replace(
            evidence,
            model_oxygen_input_observation_ids=(reference.observation_id,),
        ),
        context=scoring_context,
        reference=reference,
        source_trace=trace,
    )
    assert named_input.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert named_input.points == ()

    resolved = _resolve(source_context, melt_row)
    assert isinstance(resolved, SourceOxygenCondition)
    reduction_inputs = _score_relative_qms_series(
        observed,
        model,
        anchor,
        clock,
        replace(
            evidence,
            model_oxygen_input_observation_ids=tuple(
                sorted(resolved.used_observations)
            ),
        ),
        context=scoring_context,
        reference=reference,
        source_trace=trace,
    )
    assert reduction_inputs.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert reduction_inputs.points == ()


def _pressure_reduced_from_current(source_context, pressure_inputs):
    """A measured O2 pressure whose reduction is not in the resolver's gas map.

    The resolver only walks partial-pressure rows, so a current named in
    derivation.inputs stays out of used_observations. The scorer has to
    walk that pressure's full observation lineage itself.
    """

    melt = _row(source_context, "kems-053-stolyarova-1991")
    reference = replace(
        melt,
        identity=replace(melt.identity, fO2_Pa=None),
        point_conditions=None,
    )
    trace = replace(
        reference,
        observation_id="relative-qms-current",
        identity=replace(
            reference.identity,
            quantity=Quantity.ION_INTENSITY,
            species=Species("O2", Phase.G),
            composition=State.not_applicable("current channel"),
        ),
        read_from=None,
        derived_from=None,
        derivation=None,
        value=Value(ValueKind.POINT, point=Decimal("2")),
        engine=None,
    )
    pressure = replace(
        reference,
        observation_id="relative-qms-pressure-from-current",
        identity=replace(
            reference.identity,
            quantity=Quantity.P_PARTIAL,
            species=Species("O2", Phase.G),
            reaction=State.unknown("direct reduction"),
        ),
        evidence=replace(
            reference.evidence,
            class_=State.of(EvidenceClass.MEASURED_REDUCED),
        ),
        read_from=None,
        derived_from=None,
        derivation=Derivation(
            relation="trace_current_to_pressure",
            inputs=tuple(pressure_inputs),
            parameters=(),
            output_unit="Pa",
        ),
        value=Value(ValueKind.POINT, point=Decimal("1e-6")),
        engine=None,
    )
    rows = (reference, trace, pressure)
    context = replace(
        source_context,
        observations={row.observation_id: row for row in rows},
    )
    return context, reference, trace, pressure


def _assert_selected_pressure(context, reference, pressure):
    control = _resolve(context, reference)
    assert isinstance(control, SourceOxygenCondition)
    assert control.used_observations == {pressure.observation_id}
    return control


def test_selected_pressure_reduced_from_its_current_cannot_validate_that_current(
    source_context,
):
    context, reference, trace, pressure = _pressure_reduced_from_current(
        source_context, ("relative-qms-current",)
    )
    _assert_selected_pressure(context, reference, pressure)

    result = _score_relative_qms_series(
        *_relative_qms_inputs(trace, (pressure.observation_id,)),
        context=context,
        reference=reference,
        source_trace=trace,
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    )
    assert result.points == ()


def test_selected_pressure_hides_its_current_behind_another_row(source_context):
    context, reference, trace, pressure = _pressure_reduced_from_current(
        source_context, ()
    )
    bridge = replace(
        trace,
        observation_id="relative-qms-current-bridge",
        derivation=Derivation(
            relation="current_bridge",
            inputs=(trace.observation_id,),
            parameters=(),
            output_unit="arb",
        ),
    )
    pressure = replace(
        pressure,
        derivation=replace(
            pressure.derivation,
            inputs=(bridge.observation_id,),
        ),
    )
    context = replace(
        context,
        observations={
            reference.observation_id: reference,
            trace.observation_id: trace,
            bridge.observation_id: bridge,
            pressure.observation_id: pressure,
        },
    )
    _assert_selected_pressure(context, reference, pressure)

    result = _score_relative_qms_series(
        *_relative_qms_inputs(trace, (pressure.observation_id,)),
        context=context,
        reference=reference,
        source_trace=trace,
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.OXYGEN_SELF_AGREEMENT
    )
    assert result.points == ()


def test_unresolved_selected_pressure_lineage_stays_unknown(source_context):
    context, reference, trace, pressure = _pressure_reduced_from_current(
        source_context, ("missing-printed-oxygen-input",)
    )
    _assert_selected_pressure(context, reference, pressure)

    result = _score_relative_qms_series(
        *_relative_qms_inputs(trace, (pressure.observation_id,)),
        context=context,
        reference=reference,
        source_trace=trace,
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert result.points == ()


def test_selected_pressure_not_reduced_from_the_trace_refuses(source_context):
    context, reference, trace, pressure = _pressure_reduced_from_current(
        source_context, ()
    )
    _assert_selected_pressure(context, reference, pressure)

    result = _score_relative_qms_series(
        *_relative_qms_inputs(trace, (pressure.observation_id,)),
        context=context,
        reference=reference,
        source_trace=trace,
    )

    assert result.unavailable_reason is (
        RelativeSeriesUnavailableReason.QMS_PRODUCER_UNIMPLEMENTED
    )
    assert result.points == ()
