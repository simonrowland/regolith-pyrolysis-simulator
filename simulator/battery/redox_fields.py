"""Source fields and information flow for predictive redox controls."""

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from simulator.battery.enums import FO2Channel, NoticeKind, Quantity, ValueKind
from simulator.battery.records import Experiment, Located, Locator, Notice, Observation, State, Value

FE3_FE2_RATIO_PRINTED_FIELDS: tuple[str, ...] = ("fe3_fe2_ratio", "Fe3_over_Fe2")
FERRIC_FRACTION_FIELDS: tuple[str, ...] = ("Fe3_over_FeT", "Fe3_over_sum_Fe")
KRESS_FE2O3_WT_PCT_FIELD = "Fe2O3_wt_pct"
KRESS_FEO_WT_PCT_FIELD = "FeO_wt_pct"

_FERRIC_CONDITION_FIELDS = frozenset((
    *FE3_FE2_RATIO_PRINTED_FIELDS, *FERRIC_FRACTION_FIELDS,
    KRESS_FE2O3_WT_PCT_FIELD, KRESS_FEO_WT_PCT_FIELD,
))
CONTROL_VARIABLE_FIELDS = {
    "temperature_K": frozenset(),
    "total_pressure_Pa": frozenset(),
    "composition": frozenset({"composition", "starting_composition", "initial_composition"}),
    "gas_mixture": frozenset({"gas_composition", "gas_mix", "sweep_gas", "atmosphere"}),
    "buffer_setpoint": frozenset({"fO2_control", "oxygen_buffer_as_published",
                                  "oxygen_fugacity_buffer_relation"}),
    "duration": frozenset({"duration_h", "duration_s", "hold_duration_s"}),
}


def _address_note_segments(locator: Locator) -> Iterator[tuple[str, str | None, str | None]]:
    # Migration stores row/column as note segments. Other notes are prose.
    for segment in (locator.note or "").split(";"):
        key, separator, value = segment.strip().partition("=")
        yield segment, key.strip() if separator else None, value.strip() or None


def inherited_row_locator(locator: Locator | None) -> Locator | None:
    """An inherited observation locator locates the row, not another column."""
    if locator is None or not locator.note:
        return locator
    parts = [segment for segment, key, _ in _address_note_segments(locator) if key != "column"]
    note = ";".join(parts)
    if note == locator.note:
        return locator
    try:
        return replace(locator, note=note.strip() or None)
    except ValueError:  # The inherited column was the only locator field.
        return None


def printed_cell_address(locator: Locator) -> tuple[str | None, ...]:
    coordinates = {}
    for _, key, value in _address_note_segments(locator):
        if key in {"row", "column"}:
            coordinates[key] = value
    page = locator.pdf_page_index + 1 if locator.pdf_page_index is not None else locator.page
    return (
        locator.source_path, str(page) if page is not None else None,
        locator.table, locator.figure, coordinates.get("row") or locator.record,
        coordinates.get("column"),
    )


@dataclass(frozen=True)
class _PrintedCell:
    address: tuple[str | None, ...]
    value: str | None
    unit: str | None = None


_PRINTED_CELL_NOTE = "printed_cell="


def located_printed_cell(locator: Locator | None, value: str, unit: str | None) -> Locator | None:
    """Keep the raw scalar and unit beside the source address in its locator."""
    if locator is None:
        return None
    # Escape semicolons because the locator also uses them to delimit addresses.
    encoded = json.dumps([value, unit], ensure_ascii=False).replace(";", r"\u003b")
    note = f"{locator.note}; " if locator.note else ""
    return replace(locator, note=f"{note}{_PRINTED_CELL_NOTE}{encoded}")


def _printed_cell(value: Located, *, address_only: bool = False) -> _PrintedCell | None:
    if value.locator is None or not value.state.is_value:
        return None
    address = printed_cell_address(value.locator)
    for segment, _, _ in reversed(list(_address_note_segments(value.locator))):
        if segment.strip().startswith(_PRINTED_CELL_NOTE):
            try:
                raw, unit = json.loads(segment.strip().removeprefix(_PRINTED_CELL_NOTE))
            except (ValueError, TypeError):
                continue
            if isinstance(raw, str) and (unit is None or isinstance(unit, str)):
                return _PrintedCell(address, raw, unit)
    if address_only:
        return _PrintedCell(address, None)
    # Legacy stores and in-memory callers can still supply an unconverted print.
    scalar = value.state.value
    if isinstance(scalar, Value):
        if scalar.kind is ValueKind.INTERVAL:
            # Old records can establish the address even without a raw range.
            return _PrintedCell(address, None)
        scalar = scalar.point
    if not isinstance(scalar, (str, Decimal, int)) or isinstance(scalar, bool):
        return None
    return _PrintedCell(address, str(scalar))


def same_printed_cell(a: _PrintedCell | None, b: _PrintedCell | None) -> str | None:
    """Return the match kind for two raw printed values, or None for distinct cells."""
    if a is None or b is None:
        return None
    left, right = a.address, b.address
    if left[4] is None or left[4] != right[4]:
        return None
    if not any(locus is not None and locus == other for locus, other in zip(left[1:4], right[1:4])):
        return None
    if any(locus is not None and other is not None and locus != other for locus, other in zip(left, right)):
        return None
    if left[5] is not None and right[5] is not None:
        return "same"
    if a.value is None or b.value is None:
        return None
    try:
        first, second = Decimal(a.value), Decimal(b.value)
    except InvalidOperation:
        units_agree = a.unit is None or b.unit is None or a.unit == b.unit
        return "ambiguous" if a.value == b.value and units_agree else None
    return (
        "ambiguous" if first.is_finite() and second.is_finite() and first == second else None
    )


def _condition_references(observation: Observation, field: str) -> set[str]:
    path = f"point_conditions.{field}"
    return {field, path, f"observation[{observation.observation_id}].{path}"}


def _printed_cells(value: Located) -> tuple[_PrintedCell, ...]:
    derivation = value.inference
    own = _printed_cell(value, address_only=derivation is not None)
    originals = () if derivation is None else tuple(
        _printed_cell(operand)
        for key, operand in derivation.parameters if key == "original"
    )
    return tuple(cell for cell in (own, *originals) if cell is not None)


def _target_cells(observation: Observation) -> tuple[_PrintedCell, ...]:
    value = Located(State.of(observation.value), observation.locator, observation.derivation)
    cells = _printed_cells(value)
    printed = (observation.provenance or {}).get("printed_target")
    if not isinstance(printed, Mapping) or not isinstance(printed.get("value"), str):
        return cells
    selected_address = printed.get("address")
    own_address = printed_cell_address(value.locator) if value.locator is not None else None
    if isinstance(selected_address, (list, tuple)) and len(selected_address) == 6:
        address = tuple(selected_address)
    else:
        address = own_address
    if address is None:
        return cells
    selected = _PrintedCell(address, printed["value"], printed.get("unit"))
    if address == own_address:
        return selected, *cells[1:]
    return selected, *cells


def _match_target_cell(targets: tuple[_PrintedCell, ...], candidate: _PrintedCell | None) -> str | None:
    matches = {same_printed_cell(target, candidate) for target in targets}
    return "same" if "same" in matches else "ambiguous" if "ambiguous" in matches else None


def _is_imposed_oxygen_cell(targets: tuple[_PrintedCell, ...], experiment: Experiment | None) -> bool:
    control = experiment.fO2_control if experiment is not None else None
    return (
        control is not None
        and control.channel.is_value
        and control.channel.value is not FO2Channel.INTRINSIC
        and control.oxygen_partial_pressure_Pa is not None
        and _target_dependency(targets, control.oxygen_partial_pressure_Pa) is not None
    )


def _target_dependency(target: tuple[_PrintedCell, ...], condition: Located) -> str | None:
    inference = condition.inference
    match = _match_target_cell(target, _printed_cell(
        condition, address_only=inference is not None,
    ))
    if match is not None:
        return match
    if inference is not None:
        for _, operand in inference.parameters:
            match = _target_dependency(target, operand)
            if match is not None:
                return "ambiguous" if match == "ambiguous" else "derived_from_target"
    return None


def _operand_fields(observation: Observation, conditions: Mapping[str, Located]) -> set[str]:
    derivation = observation.derivation
    if derivation is None:
        return set()
    inputs = set(derivation.inputs)
    inputs.update(
        reference
        for name, original in derivation.parameters
        if name == "original" and original.inference is not None
        for reference in original.inference.inputs
    )
    return {
        field for field in conditions
        if not _condition_references(observation, field).isdisjoint(inputs)
    } | {
        name.removeprefix("original_") for name, _ in derivation.parameters
        if name.startswith("original_")
    }


def independent_redox_conditions(
    observation: Observation, *,
    quantity: Quantity,
    ferric_target: bool,
    condition_identity_axis: Callable[[str], str | None],
    condition_identity_value: Callable[[str, object], object],
    temperature_selection: Callable[[Mapping[str, Located]], State | None],
    pressure_selection: Callable[[Mapping[str, Located]], Decimal | Value | None],
    identity_sources: Mapping[str, tuple[str, ...]],
    experiment: Experiment | None = None,
) -> tuple[Observation, Experiment | None, tuple[Notice, ...]]:
    """Build the one permitted input view and retain the reason for each exclusion."""
    conditions = observation.point_conditions or {}
    identity = observation.identity
    target = _target_cells(observation)
    imposed = imposed_oxygen_target(observation, experiment, quantity)
    derivation = observation.derivation
    parameters = dict(derivation.parameters) if derivation is not None else {}
    excluded: dict[str, str] = {}
    correction_axes = set()
    correction_pressure = None
    temperature_operand = False
    for field in _operand_fields(observation, conditions):
        axis = condition_identity_axis(field)
        condition = conditions.get(field)
        if not (
            axis in CONTROL_VARIABLE_FIELDS and not CONTROL_VARIABLE_FIELDS[axis]
            or any(field in fields for fields in CONTROL_VARIABLE_FIELDS.values())
            or (
                axis == "fO2_Pa" and condition is not None
                and _is_imposed_oxygen_cell(_printed_cells(condition), experiment)
            )
        ):
            excluded[field] = "analyte_operand"
            continue
        original = parameters.get(f"original_{field}")
        if axis == "temperature_K" and field in conditions:
            temperature_operand = True
        if (
            original is None or not original.state.is_value
            or axis not in {"temperature_K", "total_pressure_Pa"}
        ):
            continue
        original_value = condition_identity_value(field, original.state.value)
        projected = getattr(identity, axis)
        if original_value is None:
            continue
        axis_conditions = {
            key: condition_identity_value(key, condition.state.value)
            for key, condition in conditions.items()
            if condition.state.is_value and condition_identity_axis(key) == axis
            and _target_dependency(target, condition) is None
        }
        projected_value = (
            projected.value if projected is not None and projected.is_value else None
        )
        run_value = None
        if experiment is not None:
            run_conditions = {
                key: condition for key, condition in experiment.conditions.items()
                if _target_dependency(target, condition) is None
            }
            if axis == "temperature_K":
                selected = temperature_selection(run_conditions)
                if selected is not None:
                    run_value = selected.value
            else:
                pressure = experiment.pressure_environment.total_pressure_Pa
                if pressure.state.is_value and _target_dependency(target, pressure) is None:
                    run_conditions[axis] = pressure
                run_value = pressure_selection(run_conditions)
        if any(
            value not in (None, original_value)
            for value in (*axis_conditions.values(), projected_value, run_value)
        ):
            excluded.update({
                key: "original_condition_differs_from_run"
                for key, value in axis_conditions.items() if value == original_value
            })
            if projected_value in (None, original_value):
                correction_axes.add(axis)
                if axis == "total_pressure_Pa":
                    correction_pressure = run_value

    for field, condition in conditions.items():
        if not condition.state.is_value:
            continue
        if field in {"sample_id", "quench_state", "quench_direction"}:
            continue
        relation = _target_dependency(target, condition)
        if relation is not None:
            excluded[field] = relation
        elif ferric_target and field in _FERRIC_CONDITION_FIELDS and (
            condition.locator is None or observation.locator is None
        ):
            excluded[field] = "unlocated_ferric_assay"

    independent = {field: condition for field, condition in conditions.items() if field not in excluded}
    located_inputs = {field: conditions[field] for field in excluded if field in conditions}
    excluded_sources = {f"point_conditions.{field}" for field in excluded}
    if experiment is not None:
        def independent_control(field: str, condition: Located | None) -> Located | None:
            if condition is None or (reason := _target_dependency(target, condition)) is None:
                return condition
            excluded[field] = reason
            located_inputs[field] = condition
            excluded_sources.add(field)
            return None

        unavailable = Located(State.unknown("condition depends on the scored target"))
        run_conditions = {
            field: independent_control(f"experiment.conditions.{field}", condition) or unavailable
            for field, condition in experiment.conditions.items()
        }
        environment = experiment.pressure_environment
        pressure = independent_control("experiment.pressure_environment.total_pressure_Pa",
                                       environment.total_pressure_Pa)
        sweep = independent_control("experiment.pressure_environment.sweep_gas",
                                    environment.sweep_gas)
        oxygen = experiment.fO2_control
        if oxygen is not None and not imposed:
            gas = oxygen.gas_ratio
            if gas is not None and independent_control("experiment.fO2_control.gas_ratio", gas.value) is None:
                gas = None
            oxygen = replace(
                oxygen,
                gas_ratio=gas,
                oxygen_partial_pressure_Pa=independent_control(
                    "experiment.fO2_control.oxygen_partial_pressure_Pa",
                    oxygen.oxygen_partial_pressure_Pa,
                ),
                buffer=independent_control(
                    "experiment.fO2_control.buffer", oxygen.buffer,
                ),
            )
        sample = replace(
            experiment.sample,
            initial_composition=independent_control(
                "experiment.sample.initial_composition", experiment.sample.initial_composition,
            ),
            printed_composition=independent_control(
                "experiment.sample.printed_composition", experiment.sample.printed_composition,
            ),
            container=independent_control("experiment.sample.container", experiment.sample.container),
        )
        experiment = replace(
            experiment, conditions=run_conditions, fO2_control=oxygen, sample=sample,
            pressure_environment=replace(
                environment,
                total_pressure_Pa=pressure or unavailable,
                sweep_gas=sweep or unavailable,
            ),
        )
    invalid_axes = correction_axes | {
        axis for axis, sources in identity_sources.items()
        if not excluded_sources.isdisjoint(sources)
    }
    identity_fields = dict.fromkeys(invalid_axes)
    if "temperature_K" in invalid_axes or (
        temperature_operand and (identity.temperature_K is None or not identity.temperature_K.is_value)
    ):
        selected_temperature = temperature_selection(independent)
        if selected_temperature is None and experiment is not None:
            selected_temperature = temperature_selection(experiment.conditions)
        if selected_temperature is not None:
            identity_fields["temperature_K"] = selected_temperature
    if "total_pressure_Pa" in correction_axes:
        replacement = pressure_selection(independent)
        if replacement is None:
            replacement = correction_pressure
        if isinstance(replacement, Decimal):
            identity_fields["total_pressure_Pa"] = State.of(replacement)
        elif isinstance(replacement, Value) and replacement.kind is ValueKind.INTERVAL:
            # Preserve the printed band for the scorer's interval policy.
            independent["total_pressure_Pa"] = Located(State.of(replacement))

    notices = tuple(
        Notice(
            kind={
                "ambiguous": NoticeKind.CONDITION_EXCLUDED_AMBIGUOUS_TARGET_CELL,
                "original_condition_differs_from_run": NoticeKind.CONDITION_EXCLUDED_ORIGINAL_RUN_MISMATCH,
            }.get(reason, NoticeKind.CONDITION_EXCLUDED_TARGET_DEPENDENCY),
            affected_quantities=(quantity,),
            reason=f"{field}: {reason}",
            origin=observation.observation_id,
            dropped=(field,),
            source=repr(located_inputs[field].locator) if field in located_inputs else None,
            destination=repr(observation.locator),
        )
        for field, reason in sorted(excluded.items())
        if field in located_inputs or condition_identity_axis(field) in invalid_axes
    )
    if not identity_fields and independent == conditions:
        return observation, experiment, notices
    return replace(
        observation, identity=replace(identity, **identity_fields),
        point_conditions=independent or ({} if observation.point_conditions is not None else None),
    ), experiment, notices


def imposed_oxygen_target(observation: Observation, experiment: Experiment | None, quantity: Quantity) -> bool:
    """Identify a setpoint observation by its recorded control provenance."""
    species = observation.identity.species
    if quantity not in {Quantity.FUGACITY, Quantity.P_PARTIAL} or species is None or species.formula != "O2":
        return False
    control = experiment.fO2_control if experiment is not None else None
    derivation = observation.derivation
    if control is not None and control.channel.is_value and control.channel.value is FO2Channel.INTRINSIC:
        return False
    if _is_imposed_oxygen_cell(_target_cells(observation), experiment):
        return True
    if derivation is None or experiment is None:
        return False
    return any(
        (control is not None and (
            name == "experiment.fO2_control" or name.startswith("experiment.fO2_control.")
        ))
        or (name == "experiment.sample.container" and experiment.sample.container is not None)
        for name in derivation.inputs
    )
