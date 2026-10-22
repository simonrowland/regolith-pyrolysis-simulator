"""d-216 source conditions: printed pressures and independent JANAF arithmetic."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import Engine, EvidenceClass, Phase, Quantity
from simulator.battery.records import Species, State
from simulator.battery.score import _bench_for_score, compile_residual, load_score_context
from simulator.battery.source_oxygen import (
    SourceOxygenCondition,
    SourceOxygenFallback,
    SourceOxygenFallbackReason as FallbackReason,
    resolve_source_oxygen,
)
from simulator.diagnostic_helpers import binary_pot_battery as battery


@pytest.fixture(scope="module")
def source_context():
    return load_score_context(
        Path(__file__).resolve().parents[2],
        sources=("kems-053-stolyarova-1991", "shornikov-1994-mullite-kems", "stolyarova-1996-cao-alumina-silica-kems"),
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


def _fallback_notice(context, monkeypatch, row, rows):
    requests, handles = _capture_engine(monkeypatch, row)
    residual, _ = compile_residual(
        row, Engine.OPENIMCC, context=context, handles=handles,
        point_observations=tuple(rows),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_OXYGEN_BALANCE_EFFUSION
    return next(
        notice for notice in residual.notices
        if notice.reason.startswith("model assumption: vapour-only zero-storage closure;")
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
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context, handles=handles,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert len(requests) == 1
    assert requests[0].mode == battery.PO2_COMMANDED
    assert requests[0].po2_bar == pytest.approx(expected_bar, rel=1e-10)
    assert residual.numeric is not None
    assert candidate is not None
    condition = candidate.provenance["source_oxygen_condition"]
    assert condition["derived_from"]
    assert condition["derivation"]["inputs"] == condition["derived_from"]
    assert "cell-metal activity as assumed by the source" in condition["derivation"]["relation"]
    assert float(condition["value"]["point"]) / 1e5 == pytest.approx(expected_bar, rel=1e-10)


def test_conditioning_cell_gases_remain_visible_but_are_not_scored(source_context):
    row = _row(source_context, "shornikov-1994-mullite-kems", "MoO2")
    residual, candidate = compile_residual(
        row, Engine.OPENIMCC, context=source_context,
        point_observations=tuple(r for r in source_context.observations.values() if r.experiment_id == row.experiment_id),
    )
    assert residual.numeric is None and candidate is None
    assert residual.refusal.detail["reason"] == "used_as_scoring_condition"
    assert residual.refusal.detail["derivation"]["inputs"]


def test_untyped_1996_alternative_oxygen_columns_keep_closure(source_context, monkeypatch):
    row = _row(source_context, "stolyarova-1996-cao-alumina-silica-kems", "AlO")
    # The primary lineage is untyped; other columns depend on sample gases.
    # Multiple raw columns are not multiple independently eligible conditions.
    assert _resolve(source_context, row).reason is FallbackReason.UNTYPED_LINEAGE
    notice = _fallback_notice(source_context, monkeypatch, row, source_context.observations.values())
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
    other = replace(measured, observation_id="other-printed-oxygen", identity=replace(measured.identity, species=Species("O2", Phase.G)))
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
    from simulator.battery import score

    row = _row(source_context, "kems-053-stolyarova-1991")
    row = replace(row, identity=replace(row.identity, fO2_Pa=State.of(Decimal("1e-5"))))
    requests, handles = _capture_engine(monkeypatch, row)

    def must_not_resolve(*args, **kwargs):
        raise AssertionError("a printed oxygen command reached source-condition selection")

    monkeypatch.setattr(score, "resolve_source_oxygen", must_not_resolve)
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
