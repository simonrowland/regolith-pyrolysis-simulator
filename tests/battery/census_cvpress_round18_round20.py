#!/usr/bin/env python3
"""Pair round-18 exemptions with the admitted engine input. No engine calls.

Round 18 is the classifier at 5ac2ecd4b5, loaded from git. Round 21 is
``independent_redox_conditions`` in this tree. The historical module is
registered before exec so its dataclasses resolve under the caller's
future-annotations flag.

Round 18 filtered only after ``imposed_oxygen_target`` and, when that
filter still had a scalar, buffer or gas command, returned that command
instead of calling the engine. Every other round-18 row kept the raw
observation. Round 21 always filters, and never grants an imposed-control
exemption.

The admitted input is one total pressure plus the one oxygen the ordinary
cell would read. A surviving identity fO2 outranks a point oxygen, a
buffer and a gas ratio. Shadowed fields are not counted.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

from simulator.battery.enums import MethodToken, Quantity
from simulator.battery.identity import Identity, quantity_token
from simulator.battery.migrate import (
    condition_identity_axis,
    located_condition_identity_value,
    located_run_pressure,
    located_run_temperature,
    redox_identity_sources,
)
from simulator.battery.records import Experiment, Located, Observation
from simulator.battery.redox_fields import independent_redox_conditions
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


def _load_round18(root: Path):
    source = subprocess.check_output(
        ["git", "show", f"{ROUND18}:simulator/battery/redox_fields.py"],
        cwd=root,
        text=True,
    )
    module = types.ModuleType("redox_fields_round18")
    module.__file__ = "<redox_fields_round18>"
    # Dataclasses resolve annotations through sys.modules. Register first.
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _value_token(value) -> str | None:
    if value is None:
        return None
    point = getattr(value, "point", None)
    if point is not None:
        return format(point, "f") if hasattr(point, "as_tuple") else str(point)
    if hasattr(value, "as_tuple"):
        return format(value, "f")
    return str(value)


def _state_token(state) -> str | None:
    if state is None or not state.is_value or state.value is None:
        return None
    return _value_token(state.value)


def _located_token(located) -> str | None:
    if located is None:
        return None
    return _state_token(getattr(located, "state", None))


def _knudsen(experiment: Experiment | None) -> bool:
    return bool(
        experiment is not None
        and experiment.method.is_value
        and experiment.method.value is MethodToken.KNUDSEN_EFFUSION
    )


def _pressure_token(observation: Observation, experiment: Experiment | None) -> tuple:
    """First recorded total pressure, in the ordinary cell's order."""

    identity = observation.identity
    token = _state_token(getattr(identity, "total_pressure_Pa", None))
    if token is not None:
        return ("identity.total_pressure_Pa", token)
    point = (observation.point_conditions or {}).get("total_pressure_Pa")
    if isinstance(point, Located) and point.inference is None:
        token = _located_token(point)
        if token is not None:
            return ("point_conditions.total_pressure_Pa", token)
    if experiment is not None and not _knudsen(experiment):
        token = _located_token(experiment.pressure_environment.total_pressure_Pa)
        if token is not None:
            return ("experiment.pressure_environment.total_pressure_Pa", token)
    return ("default", "DP-001")


def _control_oxygen(experiment: Experiment | None) -> tuple | None:
    """Scalar, then buffer, then gas. The historical imposed command order."""

    control = None if experiment is None else experiment.fO2_control
    if control is None:
        return None
    scalar = _located_token(control.oxygen_partial_pressure_Pa)
    if scalar is not None:
        return ("experiment.fO2_control.oxygen_partial_pressure_Pa", scalar)
    buffer = _located_token(control.buffer)
    if buffer is not None:
        return ("experiment.fO2_control.buffer", buffer)
    gas = control.gas_ratio
    if gas is not None:
        token = _located_token(gas.value)
        if token is not None:
            return (
                "experiment.fO2_control.gas_ratio",
                f"{gas.numerator_species}/{gas.denominator_species}:{token}",
            )
    return None


def _point_oxygen(observation: Observation) -> tuple | None:
    for field, condition in sorted((observation.point_conditions or {}).items()):
        if condition_identity_axis(field) != "fO2_Pa":
            continue
        token = _located_token(condition)
        if token is not None:
            return (f"point_conditions.{field}", token)
    return None


def admitted_engine_input(observation: Observation, experiment: Experiment | None) -> tuple:
    """One pressure and the one oxygen the ordinary cell would read.

    Identity fO2 wins over a point oxygen and over every experiment
    control. The token is that single command, not the shadowed fields.
    """

    identity = observation.identity
    token = _state_token(getattr(identity, "fO2_Pa", None))
    if token is not None:
        oxygen = ("identity.fO2_Pa", token)
    else:
        oxygen = (
            _control_oxygen(experiment)
            or _point_oxygen(observation)
            or ("default", "DP-010")
        )
    return (_pressure_token(observation, experiment), oxygen)


def _filter_kwargs(quantity: Quantity, experiment, identity_sources):
    return dict(
        quantity=quantity,
        ferric_target=quantity is Quantity.FE3_FE2_RATIO,
        condition_identity_axis=condition_identity_axis,
        condition_identity_value=located_condition_identity_value,
        temperature_selection=located_run_temperature,
        pressure_selection=located_run_pressure,
        identity_sources=identity_sources,
        experiment=experiment,
    )


def round18_admitted(old, row: Observation, experiment, quantity: Quantity) -> tuple[bool, tuple]:
    """Round 18's actual command, and whether the row was an imposed exemption."""

    imposed = (
        experiment is not None
        and old.imposed_oxygen_target(
            row, experiment, resolve_identity_sources=redox_identity_sources,
        )
    )
    if not imposed:
        return False, admitted_engine_input(row, experiment)
    viewed, filtered, _notices = old.independent_redox_conditions(
        row, **_filter_kwargs(quantity, experiment, redox_identity_sources(row, experiment)),
    )
    command = _control_oxygen(filtered)
    if command is None:
        # The imposed short-circuit returned nothing, so the engine ran
        # on the unfiltered row.
        return True, admitted_engine_input(row, experiment)
    return True, (_pressure_token(viewed, filtered), command)


def round21_admitted(row: Observation, experiment, quantity: Quantity) -> tuple:
    viewed, filtered, _notices = independent_redox_conditions(
        row, **_filter_kwargs(quantity, experiment, redox_identity_sources(row, experiment)),
    )
    return admitted_engine_input(viewed, filtered)


def census_source(old, root: Path, source: str) -> dict:
    path = root / "data/literature/extracts" / f"{source}.yaml"
    migrator = migrate_one_extract(root, path)
    migrator.finalize()
    rows = imposed = lost = gained = changed = 0
    for row in migrator.result.observations.values():
        if not isinstance(row.identity, Identity):
            continue
        quantity = quantity_token(row.identity)
        if quantity not in REDOX_QUANTITIES:
            continue
        experiment = migrator.result.experiments.get(row.experiment_id)
        rows += 1
        was_imposed, previous = round18_admitted(old, row, experiment, quantity)
        current = round21_admitted(row, experiment, quantity)
        # Round 21 has no imposed-control relation.
        is_imposed = False
        imposed += int(was_imposed)
        lost += int(was_imposed and not is_imposed)
        gained += int(is_imposed and not was_imposed)
        changed += int(previous != current)
    return {
        "source": source,
        "rows": rows,
        "imposed": imposed,
        "lost_exemptions": lost,
        "changed_engine_inputs": changed,
        "gained_exemptions": gained,
    }


def census_table(root: Path | None = None) -> list[dict]:
    root = Path(__file__).resolve().parents[2] if root is None else root
    old = _load_round18(root)
    table = [census_source(old, root, source) for source in SOURCES]
    # simulator.battery.__init__ imports the scorer. This census does not
    # call it and does not load the oxygen ladder.
    if "simulator.battery.fo2_ladder" in sys.modules:
        raise RuntimeError("census loaded the oxygen ladder")
    return table


def main() -> int:
    try:
        table = census_table()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    for row in table:
        print(
            f"{row['source']} rows={row['rows']} "
            f"lost_exemptions={row['lost_exemptions']} "
            f"changed_engine_inputs={row['changed_engine_inputs']} "
            f"gained_exemptions={row['gained_exemptions']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
