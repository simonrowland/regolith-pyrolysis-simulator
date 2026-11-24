#!/usr/bin/env python3
"""Pair round-18 and round-20 oxygen classifications. No engine calls.

Round 18 is the classifier at 5ac2ecd4b5, loaded from git. Round 20 is the
selected-control filter in this tree. For each redox row of the four
pressure-car sources this prints lost exemptions (round 18 imposed, round
20 not) and rows whose admitted oxygen command changed. It does not import
the scorer and it does not call an engine.

Round 18 applied that filter only when an experiment was supplied, so a
detached row kept its point condition and identity. Round 20 filters every
path. An admitted command is the oxygen route, field and value that remain
after that round's classifier.
"""

from __future__ import annotations

import subprocess
import sys
import types
from decimal import Decimal
from pathlib import Path

from simulator.battery.enums import Quantity
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.migrate import (
    condition_identity_axis,
    located_condition_identity_value,
    located_run_pressure,
    located_run_temperature,
    redox_identity_sources,
)
from simulator.battery.redox_fields import oxygen_request_inputs
from tests.battery.factories import migrate_one_extract

ROUND18 = "5ac2ecd4b5"
SOURCES = (
    "borisov-2018-compilation",
    "kress-carmichael-1988-iron-oxidation-stoichiometry",
    "sossi-2020-magma-ocean-redox",
    "aithala-2026-magmatic-iron-redox-to-2100c",
)
REDOX_QUANTITIES = {
    Quantity.FE3_FE2_RATIO,
    Quantity.VALENCE_RATIO,
    Quantity.FUGACITY,
    Quantity.P_PARTIAL,
}
_LEAVES = {
    "oxygen_partial_pressure_Pa": "scalar_pressure",
    "fO2_Pa": "scalar_pressure",
    "buffer": "named_buffer",
    "gas_ratio": "gas_control",
}


def _load_round18(root: Path):
    source = subprocess.check_output(
        ["git", "show", f"{ROUND18}:simulator/battery/redox_fields.py"],
        cwd=root,
        text=True,
    )
    module = types.ModuleType("redox_fields_round18")
    module.__file__ = "<redox_fields_round18>"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _state_token(state) -> str | None:
    if state is None or not state.is_value or state.value is None:
        return None
    value = state.value
    point = getattr(value, "point", None)
    if isinstance(point, Decimal):
        return format(point, "f")
    if point is not None:
        return str(point)
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _located_token(located) -> str | None:
    if located is None:
        return None
    return _state_token(getattr(located, "state", None))


def admitted_commands(observation, experiment) -> tuple:
    """Oxygen commands still on the row after one round's filter."""

    found = []
    token = _state_token(getattr(observation.identity, "fO2_Pa", None))
    if token is not None:
        found.append(("scalar_pressure", "identity.fO2_Pa", token))
    for field, condition in sorted((observation.point_conditions or {}).items()):
        if condition_identity_axis(field) != "fO2_Pa":
            continue
        token = _located_token(condition)
        if token is not None:
            found.append(("scalar_pressure", f"point_conditions.{field}", token))
    control = None if experiment is None else experiment.fO2_control
    if control is not None:
        gas = None if control.gas_ratio is None else control.gas_ratio.value
        fields = (
            ("oxygen_partial_pressure_Pa", control.oxygen_partial_pressure_Pa),
            ("buffer", control.buffer),
            ("gas_ratio", gas),
        )
        for name, located in fields:
            token = _located_token(located)
            if token is not None:
                found.append((_LEAVES[name], f"experiment.fO2_control.{name}", token))
    return tuple(found)


def round18_admitted(old, row, experiment, quantity):
    if experiment is None:
        return admitted_commands(row, None)
    viewed, filtered, _notices = old.independent_redox_conditions(
        row,
        quantity=quantity,
        ferric_target=quantity is Quantity.FE3_FE2_RATIO,
        condition_identity_axis=condition_identity_axis,
        condition_identity_value=located_condition_identity_value,
        temperature_selection=located_run_temperature,
        pressure_selection=located_run_pressure,
        identity_sources=redox_identity_sources(row, experiment),
        experiment=experiment,
    )
    return admitted_commands(viewed, filtered)


def round20_admitted(row, experiment, quantity):
    viewed, filtered, selected = oxygen_request_inputs(
        row,
        experiment,
        quantity=quantity,
        is_oxygen_field=lambda name: condition_identity_axis(name) == "fO2_Pa",
        identity_sources=redox_identity_sources(row, experiment),
    )
    return selected.imposed, admitted_commands(viewed, filtered)


def _census_source(old, root: Path, source: str) -> None:
    path = root / "data/literature/extracts" / f"{source}.yaml"
    migrator = migrate_one_extract(root, path)
    migrator.finalize()
    rows = lost = gained = changed = 0
    for row in migrator.result.observations.values():
        if not isinstance(row.identity, Identity):
            continue
        quantity = quantity_token(row.identity)
        if quantity not in REDOX_QUANTITIES:
            continue
        experiment = migrator.result.experiments.get(row.experiment_id)
        rows += 1
        was_imposed = old.imposed_oxygen_target(
            row, experiment, resolve_identity_sources=redox_identity_sources,
        )
        is_imposed, current = round20_admitted(row, experiment, quantity)
        previous = round18_admitted(old, row, experiment, quantity)
        lost += int(was_imposed and not is_imposed)
        gained += int(is_imposed and not was_imposed)
        changed += int(previous != current)
    print(
        f"{source} rows={rows} lost_exemptions={lost} "
        f"changed_engine_inputs={changed} gained_exemptions={gained}"
    )


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    old = _load_round18(root)
    for source in SOURCES:
        _census_source(old, root, source)
    if "simulator.battery.score" in sys.modules:
        print("census loaded the scorer", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
