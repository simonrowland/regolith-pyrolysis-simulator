#!/usr/bin/env python3
"""Mutations for the typed oxygen-setpoint rule.

Each mutation is one defect. Exit 0 means every mutation changes the
production result the regression asserts. Exit 1 means a mutation survived.
This script does not start an engine and does not call pytest.
"""

from __future__ import annotations

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


def main() -> int:
    baseline()
    # The helper is the production predicate. A missing name is a failed load.
    assert _declared_imposed_oxygen_target is not None
    status = mutation_value_match()
    status |= mutation_typed_declaration()
    status |= mutation_unfiltered_command()
    if status == 0:
        print("three mutations detected: value-match, typed-declaration, unfiltered-command")
    return status


if __name__ == "__main__":
    sys.exit(main())
