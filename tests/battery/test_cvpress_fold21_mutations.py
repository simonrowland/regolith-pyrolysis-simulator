"""One mutation per fold-21 ruling, at the ordinary partial-pressure boundary.

Each mutant restores one deleted inference. The baseline is the production
result. The spy records the oxygen command and returns before any engine
call. DP-010 ambient air is 0.21 * 101325 Pa = 0.2127825 bar.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from simulator.battery import redox_fields as provenance
from simulator.battery import score as score_module
from simulator.battery.enums import (
    AmountBasis,
    BenchIdentityBasis,
    Engine,
    ExecutionState,
    FO2Channel,
    NoticeKind,
    Quantity,
    RefusalReason,
)
from simulator.battery.records import (
    Bench,
    BenchIdentity,
    Composition,
    Derivation,
    Execution,
    FO2Control,
    GasRatio,
    Located,
    Locator,
    State,
    SweepGas,
    Value,
)
from simulator.battery.score import EnginePrediction, predict_with_engine
from simulator.battery.printed_cells import located_printed_cell
from tests.battery import factories as F
from tests.battery.census_cvpress_round18_round20 import actual_engine_request

DP010_BAR = 0.2127825
COMMAND_FIELD = "experiment.fO2_control.oxygen_partial_pressure_Pa"


def _composition() -> Composition:
    return Composition(
        "oxides",
        (("FeO", Decimal("0.2")), ("SiO2", Decimal("0.8"))),
        AmountBasis.MOLE_FRACTION,
    )


def _carrier(run):
    return replace(
        run,
        pressure_environment=replace(
            run.pressure_environment,
            sweep_gas=Located(State.of(SweepGas(
                species="N2",
                flow_sccm=State.not_applicable("carrier is not the oxygen command"),
                partial_pressure_Pa=State.not_applicable("carrier is not the oxygen command"),
            )), Locator(page=1, table="I", record="carrier")),
        ),
    )


def _partial_identity(*, fo2: Decimal | None):
    fo2_state = (
        State.unknown("oxygen not printed on this partial-pressure row")
        if fo2 is None
        else State.of(fo2)
    )
    return replace(
        F.psat_identity("O2"),
        quantity=State.of(Quantity.P_PARTIAL),
        reservoir=None,
        composition=State.of(_composition()),
        fO2_Pa=fo2_state,
        total_pressure_Pa=State.of(Decimal("101325")),
    )


def _row(value: str, location: Locator, *, fo2: Decimal | None, derivation=None, experiment=True):
    identity = _partial_identity(fo2=fo2)
    row = replace(
        F.observation("partial-oxygen", "exp-1", identity, Decimal(value)),
        locator=location,
        derivation=derivation,
        point_conditions={
            "temperature_K": Located(
                State.of(identity.temperature_K.value),
                Locator(page=1, record="thermocouple"),
            ),
            "total_pressure_Pa": Located(
                State.of(Decimal("101325")),
                Locator(page=1, record="pressure"),
            ),
        },
    )
    if not experiment:
        return row, None
    run = _carrier(F.tabulation_experiment())
    return row, run


def _command(run, pascals: str, location: Locator, *, buffer: str | None = None):
    if buffer is not None:
        control = FO2Control(
            State.of(FO2Channel.BUFFER),
            buffer=Located(State.of(buffer), location),
        )
    else:
        control = FO2Control(
            State.of(FO2Channel.COMMANDED),
            oxygen_partial_pressure_Pa=Located(
                State.of(Value.point_of(Decimal(pascals))), location,
            ),
        )
    return replace(run, fO2_control=control)


def _bench(run):
    return Bench(
        "test-bench",
        run.work_id,
        BenchIdentity(BenchIdentityBasis.DESCRIBED_IN_THIS_WORK),
    )


def _install_spy(monkeypatch):
    seen: dict[str, object] = {}
    original = score_module._oxygen_input_request

    def spy(**kwargs):
        decision = original(**kwargs)
        seen["po2"] = getattr(decision, "po2_bar", None)
        seen["input_notices"] = tuple(kwargs["input_notices"])
        return EnginePrediction(
            engine=kwargs["engine"],
            channel=kwargs["channel"],
            execution=Execution(state=ExecutionState.NOT_PROBED),
            refusal_reason=RefusalReason.IDENTITY_INCOMPLETE,
            refusal_detail={"reason": "mutation_spy"},
            identity=kwargs["identity"],
        )

    monkeypatch.setattr(score_module, "_oxygen_input_request", spy)
    return seen


def _po2(seen, row, run, bench=None) -> float:
    seen.clear()
    prediction = predict_with_engine(
        Engine.ALPHAMELTS,
        row,
        experiment=run,
        bench=bench,
        isolated=False,
        handles={},
    )
    assert "po2" in seen, (
        prediction.refusal_reason,
        dict(prediction.refusal_detail),
    )
    assert seen["po2"] is not None
    return seen["po2"]


def test_derivation_shape_does_not_keep_a_same_cell_command(monkeypatch):
    """Ruling 1. A derivation-shaped same-cell command is target-dependent.

    The mutant classifies that shape as independent, which is the deleted
    setpoint exemption.
    """

    location = Locator(page=3, table="2", record="run 1", note="column=pO2")
    row, run = _row(
        "10", location, fo2=None,
        derivation=Derivation("declared setpoint", (COMMAND_FIELD,), (), "Pa"),
    )
    run = _command(run, "10", location)
    bench = _bench(run)
    seen = _install_spy(monkeypatch)
    assert _po2(seen, row, run, bench) == pytest.approx(DP010_BAR)

    original = provenance.classify_target_provenance

    def derivation_is_independent(observation, candidate):
        result = original(observation, candidate)
        derivation = observation.derivation
        if (
            derivation is not None
            and not derivation.parameters
            and derivation.inputs == (candidate.field,)
            and result.exclusion_reason is not None
        ):
            return provenance.TargetProvenance(provenance.TargetRelation.INDEPENDENT)
        return result

    monkeypatch.setattr(provenance, "classify_target_provenance", derivation_is_independent)
    assert _po2(seen, row, run, bench) == pytest.approx(0.0001)


def test_value_equality_does_not_decide_target_dependence(monkeypatch):
    """Ruling 2. An equal number on a different cell stays admitted.

    The mutant classifies equal values as the same cell. Production keeps
    the independent command.
    """

    scored = Locator(page=3, table="2", record="run 1", note="column=SiO")
    command_at = Locator(page=9, table="4", record="setup", note="column=pO2")
    different, run = _row("10", scored, fo2=None)
    run = _command(run, "10", command_at)
    bench = _bench(run)
    seen = _install_spy(monkeypatch)
    assert _po2(seen, different, run, bench) == pytest.approx(0.0001)

    original = provenance.classify_target_provenance

    def equal_values_are_same(observation, candidate):
        result = original(observation, candidate)
        if result.exclusion_reason is not None:
            return result
        scored_point = getattr(observation.value, "point", None)
        state = candidate.condition.state
        if scored_point is None or not state.is_value or state.value is None:
            return result
        point = getattr(state.value, "point", state.value)
        if point == scored_point:
            return provenance.TargetProvenance(provenance.TargetRelation.SAME_PRINTED_CELL)
        return result

    monkeypatch.setattr(provenance, "classify_target_provenance", equal_values_are_same)
    assert _po2(seen, different, run, bench) == pytest.approx(DP010_BAR)


def test_unitless_operand_at_another_cell_cannot_reuse_the_target(monkeypatch):
    """The conversion skip is limited to its own printed address.

    The ordinary request must drop a control whose conversion operand points
    at the measured target, even when both cells print 12.
    """

    target = Locator(page=3, table="2", record="run 1", note="column=assay")
    row, run = _row("12", target, fo2=None)
    command = Located(
        State.of(Value.point_of(Decimal("1200000"))),
        located_printed_cell(
            replace(target, note="column=command"), "12", "bar",
        ),
        Derivation(
            "pressure conversion", (),
            (("original", Located(State.of(row.value), target)),),
            "Pa",
        ),
    )
    run = replace(run, fO2_control=FO2Control(
        State.of(FO2Channel.COMMANDED), oxygen_partial_pressure_Pa=command,
    ))

    backend_request = actual_engine_request(score_module, row, run, _bench(run))
    assert backend_request[0] == "backend_call", backend_request
    assert backend_request[3] == pytest.approx(DP010_BAR)
    assert any(
        notice[0] == NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY.value
        for notice in backend_request[5][0][3]
    ), backend_request[5]

    seen = _install_spy(monkeypatch)

    def request():
        po2 = _po2(seen, row, run, _bench(run))
        excluded = any(
            notice.kind is NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY
            for notice in seen["input_notices"]
        )
        return po2, excluded

    expected = (pytest.approx(DP010_BAR), True)
    assert request() == expected

    original = provenance.classify_target_provenance

    def value_only_restatement_skip(observation, candidate):
        result = original(observation, candidate)
        condition = candidate.condition
        inference = condition.inference
        own_print = provenance._printed_cell(condition)
        if inference is not None and own_print is not None and own_print.unit is not None:
            for name, operand in inference.parameters:
                cell = provenance._printed_cell(operand)
                if (
                    name == "original"
                    and cell is not None
                    and cell.unit is None
                    and cell.value == own_print.value
                ):
                    return provenance.TargetProvenance(
                        provenance.TargetRelation.INDEPENDENT
                    )
        return result

    monkeypatch.setattr(
        provenance, "classify_target_provenance", value_only_restatement_skip,
    )
    with pytest.raises(AssertionError):
        assert request() == expected


def test_gas_ratio_without_bench_defaults_for_both_oxygen_quantities(monkeypatch):
    target = Locator(page=3, table="2", record="run 1", note="column=assay")
    row, run = _row("12", target, fo2=None)
    gas_ratio = GasRatio(
        "CO2", "CO",
        Located(State.of(Value.point_of(Decimal("1"))), Locator(page=9, record="gas ratio")),
        "partial_pressure",
    )
    run = replace(run, fO2_control=FO2Control(
        State.of(FO2Channel.GAS_MIX), gas_ratio=gas_ratio,
    ))
    fugacity = replace(
        row, identity=replace(row.identity, quantity=State.of(Quantity.FUGACITY)),
    )

    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, fugacity, experiment=run, bench=None, isolated=False,
    )
    seen = _install_spy(monkeypatch)
    partial_pressure = _po2(seen, row, run, bench=None)

    assert prediction.value == Decimal("21278.25")
    assert partial_pressure == pytest.approx(DP010_BAR)
    fugacity_default = next(
        notice for notice in prediction.notices
        if notice.policy is not None and notice.policy.id == "DP-010"
    )
    partial_default = next(
        notice for notice in seen["input_notices"]
        if notice.policy is not None and notice.policy.id == "DP-010"
    )
    assert "original_reason=gas_ratio_requires_bench_but_none_was_supplied" in fugacity_default.reason
    assert "original_reason=gas_ratio_requires_bench_but_none_was_supplied" in partial_default.reason

    bench = _bench(run)
    selected_route = _po2(seen, row, run, bench=bench)
    assert selected_route != pytest.approx(DP010_BAR)
    assert any(
        "route=experiment_gas_ratio" in notice.reason
        for notice in seen["input_notices"]
    )

    from simulator.battery import fo2_ladder

    original_resolver = fo2_ladder.resolve_row_fo2

    def gas_ratio_without_bench(experiment, bench, observation, **kwargs):
        if bench is None and experiment is not None and experiment.fO2_control is not None:
            ratio = experiment.fO2_control.gas_ratio
            temperature = kwargs.get("temperature_K")
            if ratio is not None and temperature is not None:
                return fo2_ladder._gas_ratio_fo2(experiment, temperature)
        return original_resolver(experiment, bench, observation, **kwargs)

    monkeypatch.setattr(fo2_ladder, "resolve_row_fo2", gas_ratio_without_bench)
    with pytest.raises(AssertionError):
        assert _po2(seen, row, run, bench=None) == pytest.approx(DP010_BAR)


def test_buffer_with_sibling_scalar_and_no_bench_uses_the_default(monkeypatch):
    target = Locator(page=3, table="2", record="run 1", note="column=assay")
    row, run = _row("12", target, fo2=None)
    run = replace(run, fO2_control=FO2Control(
        State.of(FO2Channel.BUFFER),
        oxygen_partial_pressure_Pa=Located(
            State.of(Value.point_of(Decimal("10000"))),
            Locator(page=9, record="sibling scalar"),
        ),
        buffer=Located(State.of("QFM"), Locator(page=9, record="buffer")),
    ))
    fugacity = replace(
        row, identity=replace(row.identity, quantity=State.of(Quantity.FUGACITY)),
    )
    prediction = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, fugacity, experiment=run, bench=None, isolated=False,
    )
    seen = _install_spy(monkeypatch)
    partial_pressure = _po2(seen, row, run, bench=None)

    assert prediction.value == Decimal("21278.25")
    assert partial_pressure == pytest.approx(DP010_BAR)
    fugacity_default = next(
        notice for notice in prediction.notices
        if notice.policy is not None and notice.policy.id == "DP-010"
    )
    partial_default = next(
        notice for notice in seen["input_notices"]
        if notice.policy is not None and notice.policy.id == "DP-010"
    )
    assert "original_reason=buffer_requires_bench_but_none_was_supplied" in fugacity_default.reason
    assert "original_reason=buffer_requires_bench_but_none_was_supplied" in partial_default.reason
    assert any(
        notice.policy is not None and notice.policy.id == "DP-010"
        for notice in prediction.notices
    )
    assert any(
        notice.policy is not None and notice.policy.id == "DP-010"
        for notice in seen["input_notices"]
    )


def test_unsourced_identity_equal_to_the_target_is_commanded(monkeypatch):
    """Ruling 2, the deleted identity echo. No located oxygen source."""

    location = Locator(page=4, table="3", record="row 1", note="column=SiO")
    row, run = _row("10", location, fo2=Decimal("10"), experiment=False)
    seen = _install_spy(monkeypatch)
    assert _po2(seen, row, run) == pytest.approx(0.0001)

    real_view = score_module._redox_input_view

    def clear_when_values_match(observation, quantity, experiment):
        viewed, filtered, notices = real_view(observation, quantity, experiment)
        fo2 = viewed.identity.fO2_Pa
        scored_point = getattr(observation.value, "point", None)
        if fo2 is not None and fo2.is_value and fo2.value == scored_point:
            viewed = replace(viewed, identity=replace(viewed.identity, fO2_Pa=None))
        return viewed, filtered, notices

    monkeypatch.setattr(score_module, "_redox_input_view", clear_when_values_match)
    assert _po2(seen, row, run) == pytest.approx(DP010_BAR)


def test_filter_bypass_restores_excluded_partial_pressure_commands(monkeypatch):
    """Ruling 3. The ordinary cell reads the filtered assembly.

    Bypassing ``_redox_input_view`` puts the same-cell command back in the
    actual backend request.
    """

    attached_at = Locator(page=3, table="2", record="run 1", note="column=pO2")
    attached, run = _row("10", attached_at, fo2=None)
    run = _command(run, "10", attached_at)
    bench = _bench(run)

    detached_at = Locator(page=4, table="3", record="row 1", note="column=fO2")
    detached, no_run = _row("12", detached_at, fo2=Decimal("12"), experiment=False)
    detached = replace(
        detached,
        point_conditions={
            **detached.point_conditions,
            "fO2_Pa": Located(State.of(Decimal("12")), detached_at),
        },
    )

    original_oxygen_input_request = score_module._oxygen_input_request
    seen = _install_spy(monkeypatch)
    assert _po2(seen, attached, run, bench) == pytest.approx(DP010_BAR)
    assert _po2(seen, detached, no_run) == pytest.approx(DP010_BAR)

    with monkeypatch.context() as request_patch:
        request_patch.setattr(
            score_module, "_oxygen_input_request", original_oxygen_input_request,
        )
        backend_baseline = actual_engine_request(score_module, attached, run, bench)
    assert backend_baseline[0] == "backend_call", backend_baseline
    assert backend_baseline[3] == pytest.approx(DP010_BAR)

    def passthrough(observation, _quantity, experiment):
        return observation, experiment, ()

    monkeypatch.setattr(score_module, "_redox_input_view", passthrough)
    assert _po2(seen, attached, run, bench) == pytest.approx(0.0001)
    assert _po2(seen, detached, no_run) == pytest.approx(0.00012)
    with monkeypatch.context() as request_patch:
        request_patch.setattr(
            score_module, "_oxygen_input_request", original_oxygen_input_request,
        )
        backend_mutant = actual_engine_request(score_module, attached, run, bench)
    assert backend_mutant[0] == "backend_call", backend_mutant
    assert backend_mutant[3] == pytest.approx(0.0001)


def test_missing_apparatus_stays_a_flagged_default(monkeypatch):
    """Ruling 4. A buffer with no bench is flagged DP-010, not a refusal.

    The mutant returns the engine refusal unchanged.
    """

    run = F.tabulation_experiment()
    buffer_at = Locator(page=3, table="2", record="run 1", note="column=buffer")
    run = replace(run, fO2_control=FO2Control(
        State.of(FO2Channel.BUFFER),
        buffer=Located(State.of("QFM"), Locator(page=9, record="buffer")),
    ))
    identity = replace(
        F.psat_identity("O2"),
        quantity=State.of(Quantity.FUGACITY),
        reservoir=None,
        total_pressure_Pa=None,
    )
    row = replace(
        F.observation("buffer-only", run.experiment_id, identity, Decimal("12")),
        locator=buffer_at,
    )
    baseline = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, row, experiment=run, bench=None, isolated=False,
    )
    assert baseline.refusal_reason is None
    assert baseline.value is not None
    assert any(notice.policy and notice.policy.id == "DP-010" for notice in baseline.notices)

    monkeypatch.setattr(
        score_module,
        "_recover_fugacity_from_shared_oxygen",
        lambda prediction, *_args, **_kwargs: prediction,
    )
    mutant = predict_with_engine(
        Engine.INTERNAL_ANALYTICAL, row, experiment=run, bench=None, isolated=False,
    )
    assert mutant.refusal_reason is RefusalReason.UNSUPPORTED
    assert mutant.refusal_detail["reason"] == "quantity_not_predicted"
