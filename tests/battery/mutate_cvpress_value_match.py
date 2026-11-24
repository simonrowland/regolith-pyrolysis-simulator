#!/usr/bin/env python3
"""Mutations for the typed oxygen-setpoint rule.

Each mutation is one defect. Exit 0 means every mutation changes the
production result the regression asserts. Exit 1 means a mutation survived.
This script does not start an engine and does not call pytest.
"""

from __future__ import annotations

import math
import sys
from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import FO2Channel, Quantity
from simulator.battery.records import FO2Control, Located, Locator, State, Value
from simulator.battery.redox_fields import (
    CandidateInput,
    TargetProvenance,
    TargetRelation,
    _declared_imposed_oxygen_target,
    classify_target_provenance,
    independent_redox_conditions,
)
from tests.battery import factories as F


FIELD = "experiment.fO2_control.oxygen_partial_pressure_Pa"


def _row(value: str, column: str | None, derivation=None):
    run = F.tabulation_experiment()
    note = None if column is None else f"column={column}"
    location = Locator(page=3, table="2", record="run 1", note=note)
    control = Located(State.of(Value.point_of(Decimal(value))), location)
    run = replace(run, fO2_control=FO2Control(
        State.of(FO2Channel.COMMANDED), oxygen_partial_pressure_Pa=control,
    ))
    identity = replace(
        F.psat_identity("O2"), quantity=State.of(Quantity.FUGACITY), reservoir=None,
    )
    row = replace(
        F.observation("oxygen", run.experiment_id, identity, Decimal(value)),
        locator=location,
        derivation=derivation,
    )
    return row, run, control


def _command_relation(row, run, control) -> str:
    return classify_target_provenance(row, CandidateInput(
        FIELD, control, imposed_oxygen=True, experiment=run,
    )).relation.value


def _filter_keeps_command(row, run) -> bool:
    def axis(name: str) -> str | None:
        leaf = name.rsplit(".", 1)[-1]
        if leaf in {"oxygen_partial_pressure_Pa", "fO2_Pa", "buffer", "gas_ratio"}:
            return "fO2_Pa"
        return None

    _view, filtered, _notices = independent_redox_conditions(
        row,
        quantity=Quantity.FUGACITY,
        ferric_target=False,
        condition_identity_axis=axis,
        condition_identity_value=lambda _field, value, _conditions: value,
        temperature_selection=lambda _conditions: None,
        pressure_selection=lambda _conditions: None,
        identity_sources={},
        experiment=run,
    )
    oxygen = filtered.fO2_control
    return oxygen is not None and oxygen.oxygen_partial_pressure_Pa is not None


def baseline() -> None:
    from simulator.battery.records import Derivation

    bare, bare_run, bare_control = _row("10", None)
    assert _command_relation(bare, bare_run, bare_control) == "same_printed_cell"
    assert not _filter_keeps_command(bare, bare_run)

    declared_derivation = Derivation("declared setpoint", (FIELD,), (), "Pa")
    declared, declared_run, declared_control = _row("10", None, declared_derivation)
    assert _command_relation(declared, declared_run, declared_control) == "imposed_control_target"
    assert _filter_keeps_command(declared, declared_run)

    assay = Locator(page=3, table="2", record="run 1", note="column=assay")
    command = replace(assay, note="column=setpoint")
    original = Located(State.of(Value.point_of(Decimal("10"))), command)
    corrected, corrected_run, corrected_control = _row("12", "assay", Derivation(
        "measurement correction", (), (("original", original),), "Pa",
    ))
    corrected_control = Located(State.of(Value.point_of(Decimal("10"))), command)
    corrected_run = replace(corrected_run, fO2_control=FO2Control(
        State.of(FO2Channel.COMMANDED), oxygen_partial_pressure_Pa=corrected_control,
    ))
    assert _command_relation(corrected, corrected_run, corrected_control) == "same_printed_cell"
    assert not _filter_keeps_command(corrected, corrected_run)


def _fail(name: str) -> int:
    print(f"mutation survived: {name}", file=sys.stderr)
    return 1


def mutation_value_match() -> int:
    """Restoring an explicit same cell as the setpoint must be visible."""

    import simulator.battery.redox_fields as fields

    original = fields.classify_target_provenance

    def restore(observation, candidate):
        result = original(observation, candidate)
        if (
            result.relation is TargetRelation.SAME_PRINTED_CELL
            and not result.ambiguous_cell
            and candidate.imposed_oxygen
        ):
            return TargetProvenance(TargetRelation.IMPOSED_CONTROL_TARGET)
        return result

    # Same column as the scored cell, no typed declaration.
    same, run, control = _row("10", "assay")
    before = _command_relation(same, run, control)
    fields.classify_target_provenance = restore
    try:
        after = fields.classify_target_provenance(same, CandidateInput(
            FIELD, control, imposed_oxygen=True, experiment=run,
        )).relation.value
    finally:
        fields.classify_target_provenance = original
    if before == "imposed_control_target" or after != "imposed_control_target":
        return _fail("value-match")
    return 0


def mutation_typed_declaration() -> int:
    """Deleting the typed declaration must drop the exemption."""

    import simulator.battery.redox_fields as fields
    from simulator.battery.records import Derivation

    original = fields._declared_imposed_oxygen_target
    fields._declared_imposed_oxygen_target = lambda *_args, **_kwargs: False
    declared, run, control = _row("10", None, Derivation("declared setpoint", (FIELD,), (), "Pa"))
    try:
        relation = fields.classify_target_provenance(declared, CandidateInput(
            FIELD, control, imposed_oxygen=True, experiment=run,
        )).relation.value
    finally:
        fields._declared_imposed_oxygen_target = original
    if relation == "imposed_control_target":
        return _fail("typed-declaration")
    return 0


def mutation_unfiltered_command() -> int:
    """A passthrough filter must keep the ambiguous command."""

    bare, run, _control = _row("10", None)
    if _filter_keeps_command(bare, run):
        return _fail("unfiltered-command-baseline")

    def passthrough(observation, **_kwargs):
        return observation, _kwargs["experiment"], ()

    import simulator.battery.redox_fields as fields

    original = fields.independent_redox_conditions
    fields.independent_redox_conditions = passthrough
    try:
        _view, filtered, _notices = fields.independent_redox_conditions(
            bare, experiment=run,
        )
        kept = filtered.fO2_control.oxygen_partial_pressure_Pa is not None
    finally:
        fields.independent_redox_conditions = original
    if not kept:
        return _fail("unfiltered-command")
    return 0


def _is_pa(bar, pa: str) -> bool:
    if bar is None:
        return False
    expected = float(Decimal(pa) / Decimal(100000))
    return math.isclose(bar, expected, rel_tol=1e-6, abs_tol=1e-12)


def _request_bar(row, run):
    """One request-builder call. It does not equilibrate a melt engine."""

    import simulator.battery.score as score

    decision = score._oxygen_input_request(
        engine=score.Engine.INTERNAL_ANALYTICAL,
        channel="mutation",
        sources=(),
        identity=row.identity,
        requested=None,
        quantity=Quantity.FUGACITY,
        composition_value=None,
        input_notices=[],
        observation=row,
        experiment=run,
    )
    return getattr(decision, "po2_bar", None)


def _admit(row, run):
    from simulator.battery.migrate import condition_identity_axis, redox_identity_sources
    from simulator.battery.redox_fields import oxygen_request_inputs

    return oxygen_request_inputs(
        row,
        run,
        quantity=Quantity.FUGACITY,
        is_oxygen_field=lambda name: condition_identity_axis(name) == "fO2_Pa",
        identity_sources=redox_identity_sources(row, run),
    )


def _buffer_and_scalar():
    from simulator.battery.records import Derivation

    run = F.tabulation_experiment()
    buffer_at = Locator(page=3, table="2", record="run 1", note="column=buffer")
    scalar_at = Locator(page=3, table="2", record="run 1", note="column=pO2")
    run = replace(run, fO2_control=FO2Control(
        State.of(FO2Channel.COMMANDED),
        buffer=Located(State.of("QFM"), buffer_at),
        oxygen_partial_pressure_Pa=Located(
            State.of(Value.point_of(Decimal("10"))), scalar_at,
        ),
    ))
    identity = replace(
        F.psat_identity("O2"), quantity=State.of(Quantity.FUGACITY), reservoir=None,
    )
    row = replace(
        F.observation("buffer", run.experiment_id, identity, Decimal("12")),
        locator=buffer_at,
        derivation=Derivation(
            "declared setpoint", ("experiment.fO2_control.buffer",), (), "1",
        ),
    )
    return row, run


def _detached_echo():
    location = Locator(page=4, table="3", record="row 1", note="column=fO2")
    identity = replace(
        F.psat_identity("O2"),
        quantity=State.of(Quantity.FUGACITY),
        reservoir=None,
        total_pressure_Pa=None,
        fO2_Pa=State.of(Decimal("12")),
    )
    return replace(
        F.observation("detached", "none", identity, Decimal("12")),
        locator=location,
        point_conditions={"fO2_Pa": Located(State.of(Decimal("12")), location)},
    )


def mutation_selected_route() -> int:
    """A buffer declaration must not become the sibling scalar."""

    import simulator.battery.redox_fields as fields
    from simulator.battery.migrate import condition_identity_axis, redox_identity_sources

    row, run = _buffer_and_scalar()
    before = fields.selected_oxygen_control(
        row, run, quantity=Quantity.FUGACITY,
        is_oxygen_field=lambda name: condition_identity_axis(name) == "fO2_Pa",
        identity_sources=redox_identity_sources(row, run),
    )
    if before.route is not fields.OxygenControlRoute.NAMED_BUFFER:
        return _fail("selected-route-baseline")
    original = fields._select_imposed

    def scalar_first(candidates):
        scalars = [
            candidate for candidate in candidates
            if fields._oxygen_route(candidate.field) is fields.OxygenControlRoute.SCALAR_PRESSURE
        ]
        return scalars[0] if scalars else original(candidates)

    fields._select_imposed = scalar_first
    try:
        after = fields.selected_oxygen_control(
            row, run, quantity=Quantity.FUGACITY,
            is_oxygen_field=lambda name: condition_identity_axis(name) == "fO2_Pa",
            identity_sources=redox_identity_sources(row, run),
        )
    finally:
        fields._select_imposed = original
    point = None if after.value is None else after.value.state.value.point
    if after.route is not fields.OxygenControlRoute.SCALAR_PRESSURE or point != Decimal("10"):
        return _fail("selected-route")
    return 0


def mutation_request_filter() -> int:
    """A passthrough request filter must let the target command through."""

    import simulator.battery.score as score
    from simulator.battery.redox_fields import SelectedOxygenControl

    bare, run, _control = _row("10", None)
    _view, filtered, _selected = _admit(bare, run)
    if filtered is None or filtered.fO2_control.oxygen_partial_pressure_Pa is not None:
        return _fail("request-filter-baseline")
    if _is_pa(_request_bar(bare, run), "10"):
        return _fail("request-filter-baseline")

    def passthrough(observation, experiment=None, **_kwargs):
        return observation, experiment, SelectedOxygenControl(None, None, None, None, ())

    original = score.oxygen_request_inputs
    score.oxygen_request_inputs = passthrough
    try:
        commanded = _is_pa(_request_bar(bare, run), "10")
    finally:
        score.oxygen_request_inputs = original
    if not commanded:
        return _fail("request-filter")
    return 0


def mutation_evaluator_route() -> int:
    """A scalar-first evaluator must return the sibling 10 Pa."""

    import simulator.battery.fo2_ladder as ladder

    row, run = _buffer_and_scalar()
    before = ladder.imposed_oxygen_pressure_pa(
        run, None, row, route=ladder.OxygenControlRoute.NAMED_BUFFER,
    )
    if before == Decimal("10"):
        return _fail("evaluator-route-baseline")
    original = ladder.imposed_oxygen_pressure_pa

    def scalar_first(experiment, bench, observation, *, route):
        point = ladder._scalar_oxygen_pressure_pa(experiment)
        if point is not None:
            return point
        return original(experiment, bench, observation, route=route)

    ladder.imposed_oxygen_pressure_pa = scalar_first
    try:
        after = ladder.imposed_oxygen_pressure_pa(
            run, None, row, route=ladder.OxygenControlRoute.NAMED_BUFFER,
        )
    finally:
        ladder.imposed_oxygen_pressure_pa = original
    if after != Decimal("10"):
        return _fail("evaluator-route")
    return 0


def mutation_detached_self_echo() -> int:
    """Without the echo filter the scored cell commands 0.00012 bar."""

    import simulator.battery.score as score
    from simulator.battery.redox_fields import SelectedOxygenControl

    row = _detached_echo()
    viewed, _run, selected = _admit(row, None)
    fo2 = viewed.identity.fO2_Pa
    if fo2 is not None and fo2.is_value:
        return _fail("detached-self-echo-baseline")
    if "identity.fO2_Pa" not in dict(selected.excluded):
        return _fail("detached-self-echo-baseline")
    if _is_pa(_request_bar(row, None), "12"):
        return _fail("detached-self-echo-baseline")

    def passthrough(observation, experiment=None, **_kwargs):
        return observation, experiment, SelectedOxygenControl(None, None, None, None, ())

    original = score.oxygen_request_inputs
    score.oxygen_request_inputs = passthrough
    try:
        commanded = _is_pa(_request_bar(row, None), "12")
    finally:
        score.oxygen_request_inputs = original
    if not commanded:
        return _fail("detached-self-echo")
    return 0


def main() -> int:
    baseline()
    # The helper is the production predicate. A missing name is a failed load.
    assert _declared_imposed_oxygen_target is not None
    status = mutation_value_match()
    status |= mutation_typed_declaration()
    status |= mutation_unfiltered_command()
    status |= mutation_selected_route()
    status |= mutation_request_filter()
    status |= mutation_evaluator_route()
    status |= mutation_detached_self_echo()
    if status == 0:
        print(
            "seven mutations detected: value-match, typed-declaration, "
            "unfiltered-command, selected-route, request-filter, "
            "evaluator-route, detached-self-echo"
        )
    return status


if __name__ == "__main__":
    sys.exit(main())
