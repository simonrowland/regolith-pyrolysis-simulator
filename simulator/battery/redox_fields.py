"""Source fields and information flow for predictive redox controls."""

from collections.abc import Callable, Mapping
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


def _cell_coordinates(locator: Locator) -> tuple[str | None, ...]:
    # Migration stores row/column as note segments. Other notes are prose.
    coordinates = {}
    for segment in (locator.note or "").split(";"):
        key, separator, value = segment.strip().partition("=")
        if separator and key in {"row", "column"}:
            coordinates[key] = value.strip() or None
    page = locator.pdf_page_index + 1 if locator.pdf_page_index is not None else locator.page
    return (
        locator.source_path, str(page) if page is not None else None,
        locator.table, locator.figure, coordinates.get("row") or locator.record,
        coordinates.get("column"),
    )


@dataclass(frozen=True)
class _PrintedCell:
    address: tuple[str | None, ...]
    value: str | Decimal | int


def _printed_cell(value: Located, *, inherited_row: bool = False) -> _PrintedCell | None:
    if value.locator is None or not value.state.is_value:
        return None
    scalar = value.state.value
    if isinstance(scalar, Value):
        scalar = scalar.point
    if not isinstance(scalar, (str, Decimal, int)) or isinstance(scalar, bool):
        return None
    address = _cell_coordinates(value.locator)
    if inherited_row:
        address = (*address[:-1], None)
    return _PrintedCell(address, scalar)


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
    if isinstance(a.value, str) and a.value == b.value:
        return "ambiguous"
    try:
        first, second = Decimal(a.value), Decimal(b.value)
    except InvalidOperation:
        return None
    return (
        "ambiguous" if first.is_finite() and second.is_finite() and first == second else None
    )


def _condition_references(observation: Observation, field: str) -> set[str]:
    path = f"point_conditions.{field}"
    return {field, path, f"observation[{observation.observation_id}].{path}"}


def _raw_printed_value(value: Located) -> Located:
    if value.inference is not None:
        for key, operand in value.inference.parameters:
            if key == "original" and operand.locator is not None and operand.state.is_value:
                return operand
    return value


def _target_cell(observation: Observation) -> Located:
    return _raw_printed_value(Located(
        State.of(observation.value), observation.locator, observation.derivation,
    ))


def _is_imposed_oxygen_cell(value: Located, experiment: Experiment | None) -> bool:
    control = experiment.fO2_control if experiment is not None else None
    return (
        control is not None
        and control.channel.is_value
        and control.channel.value is not FO2Channel.INTRINSIC
        and control.oxygen_partial_pressure_Pa is not None
        and same_printed_cell(
            _printed_cell(value), _printed_cell(_raw_printed_value(control.oxygen_partial_pressure_Pa)),
        ) is not None
    )


def _target_dependency(
    target: _PrintedCell | None, condition: Located, *, projected_axis: bool = False,
    inherited_row: bool = False,
) -> str | None:
    if not projected_axis:
        match = same_printed_cell(target, _printed_cell(
            _raw_printed_value(condition), inherited_row=inherited_row,
        ))
        if match is not None:
            return match
    if condition.inference is not None:
        for _, operand in condition.inference.parameters:
            # Migration can stamp a whole projected axis and its operands with the
            # observation's locator. That inherited column does not address the operand.
            match = _target_dependency(
                target, operand, inherited_row=(
                    inherited_row or (projected_axis and operand.locator == condition.locator)
                ),
            )
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
    experiment: Experiment | None = None,
) -> tuple[Observation, Experiment | None, tuple[Notice, ...]]:
    """Build the one permitted input view and retain the reason for each exclusion."""
    conditions = observation.point_conditions or {}
    identity = observation.identity
    target_value = _target_cell(observation)
    target = _printed_cell(target_value)
    imposed = imposed_oxygen_target(observation, experiment, quantity)
    derivation = observation.derivation
    parameters = dict(derivation.parameters) if derivation is not None else {}
    excluded: dict[str, str] = {}
    correction_axes = set()
    temperature_operand = False
    for field in _operand_fields(observation, conditions):
        axis = condition_identity_axis(field)
        condition = conditions.get(field)
        if not (
            axis in CONTROL_VARIABLE_FIELDS and not CONTROL_VARIABLE_FIELDS[axis]
            or any(field in fields for fields in CONTROL_VARIABLE_FIELDS.values())
            or (
                axis == "fO2_Pa" and condition is not None
                and _is_imposed_oxygen_cell(_raw_printed_value(condition), experiment)
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
        }
        projected_value = (
            projected.value if projected is not None and projected.is_value else None
        )
        run_value = None
        if experiment is not None:
            run_condition = (
                experiment.pressure_environment.total_pressure_Pa
                if axis == "total_pressure_Pa" else experiment.conditions.get(axis)
            )
            if (
                run_condition is not None and run_condition.state.is_value
                and _target_dependency(target, run_condition) is None
            ):
                recorded = run_condition.state.value
                run_value = (
                    recorded if isinstance(recorded, Value) and recorded.kind is ValueKind.INTERVAL
                    else condition_identity_value(axis, recorded)
                )
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

    for field, condition in conditions.items():
        if not condition.state.is_value:
            continue
        if field in {"sample_id", "quench_state", "quench_direction"}:
            continue
        relation = _target_dependency(
            target, condition, projected_axis=(
                condition.inference is not None
                and (
                    condition_identity_axis(field) == "composition"
                    or condition.inference.relation == "celsius_to_kelvin"
                )
            ),
        )
        if relation is not None:
            excluded[field] = relation
        elif ferric_target and field in _FERRIC_CONDITION_FIELDS and (
            condition.locator is None or observation.locator is None
        ):
            excluded[field] = "unlocated_ferric_assay"

    independent = {field: condition for field, condition in conditions.items() if field not in excluded}
    invalid_axes = correction_axes | {
        axis for field, reason in excluded.items()
        if reason != "original_condition_differs_from_run"
        and (axis := condition_identity_axis(field)) is not None
    }
    located_inputs = {field: conditions[field] for field in excluded if field in conditions}
    if experiment is not None:
        def independent_control(
            field: str, condition: Located | None, identity_field: str | None = None,
        ) -> Located | None:
            if condition is None or (reason := _target_dependency(target, condition)) is None:
                return condition
            excluded[field] = reason
            located_inputs[field] = condition
            if identity_field is not None and (axis := condition_identity_axis(identity_field)) is not None:
                projected = getattr(identity, axis)
                if projected is not None and projected.is_value and condition.state.is_value:
                    scalar = condition.state.value
                    if isinstance(scalar, Value):
                        scalar = scalar.point
                    value = condition_identity_value(identity_field, scalar)
                    if projected.value == value:
                        invalid_axes.add(axis)
            return None

        unavailable = Located(State.unknown("condition depends on the scored target"))
        run_conditions = {
            field: independent_control(f"experiment.conditions.{field}", condition, field) or unavailable
            for field, condition in experiment.conditions.items()
        }
        environment = experiment.pressure_environment
        pressure = independent_control("experiment.pressure_environment.total_pressure_Pa",
                                       environment.total_pressure_Pa, "total_pressure_Pa")
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
                    oxygen.oxygen_partial_pressure_Pa, "oxygen_partial_pressure_Pa",
                ),
                buffer=independent_control(
                    "experiment.fO2_control.buffer", oxygen.buffer, "oxygen_buffer_as_published",
                ),
            )
        sample = replace(
            experiment.sample,
            initial_composition=independent_control(
                "experiment.sample.initial_composition", experiment.sample.initial_composition,
                "composition",
            ),
            printed_composition=independent_control(
                "experiment.sample.printed_composition", experiment.sample.printed_composition,
                "printed_composition",
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
    identity_fields = dict.fromkeys(invalid_axes)
    if "temperature_K" in invalid_axes or temperature_operand:
        selected_temperature = temperature_selection(independent)
        if selected_temperature is None and experiment is not None:
            selected_temperature = temperature_selection(experiment.conditions)
        if selected_temperature is not None:
            identity_fields["temperature_K"] = selected_temperature
    if "total_pressure_Pa" in correction_axes:
        candidates = (
            condition_identity_value(field, condition.state.value)
            for field, condition in independent.items()
            if condition.state.is_value and condition_identity_axis(field) == "total_pressure_Pa"
        )
        replacement = next((candidate for candidate in candidates if candidate is not None), None)
        if replacement is not None:
            identity_fields["total_pressure_Pa"] = State.of(replacement)

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
            destination=repr(target_value.locator),
        )
        for field, reason in sorted(excluded.items())
        if field in located_inputs or condition_identity_axis(field) in invalid_axes
    )
    if not identity_fields and independent == conditions:
        return observation, experiment, notices
    return replace(
        observation, identity=replace(identity, **identity_fields),
        point_conditions=independent if observation.point_conditions is not None else None,
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
    if _is_imposed_oxygen_cell(_target_cell(observation), experiment):
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
