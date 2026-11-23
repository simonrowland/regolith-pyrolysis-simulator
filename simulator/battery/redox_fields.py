"""Source fields and information flow for predictive redox controls."""

from collections.abc import Callable, Mapping
from dataclasses import replace

from simulator.battery.enums import FO2Channel, NoticeKind, Quantity
from simulator.battery.records import Experiment, Located, Locator, Notice, Observation, State

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


def _cell_relation(target: Locator | None, condition: Locator | None) -> str | None:
    """Match an addressed row unless the supplied physical coordinates disagree."""
    if target is None or condition is None:
        return None
    left, right = _cell_coordinates(target), _cell_coordinates(condition)
    if left[4] is None or left[4] != right[4]:
        return None
    if not any(a is not None and a == b for a, b in zip(left[1:4], right[1:4])):
        return None
    if any(a is not None and b is not None and a != b for a, b in zip(left, right)):
        return None
    return "ambiguous" if left[5] is None or right[5] is None else "same"


def _condition_references(observation: Observation, field: str) -> set[str]:
    path = f"point_conditions.{field}"
    return {field, path, f"observation[{observation.observation_id}].{path}"}


def _is_imposed_oxygen_cell(locator: Locator | None, experiment: Experiment | None) -> bool:
    control = experiment.fO2_control if experiment is not None else None
    return (
        control is not None
        and control.channel.is_value
        and control.channel.value is not FO2Channel.INTRINSIC
        and control.oxygen_partial_pressure_Pa is not None
        and _cell_relation(locator, control.oxygen_partial_pressure_Pa.locator) == "same"
    )


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
) -> tuple[Observation, tuple[Notice, ...]]:
    """Build the one permitted input view and retain the reason for each exclusion."""
    conditions = observation.point_conditions or {}
    identity = observation.identity
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
                and _is_imposed_oxygen_cell(condition.locator, experiment)
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
        if any(value not in (None, original_value) for value in (*axis_conditions.values(), projected_value)):
            excluded.update({
                key: "original_condition_differs_from_run"
                for key, value in axis_conditions.items() if value == original_value
            })
            if projected_value in (None, original_value):
                correction_axes.add(axis)

    for field, condition in conditions.items():
        if not condition.state.is_value:
            continue
        # Generated run axes can carry the target's row locator. They retain
        # their own units or source map and still undergo the lineage check.
        axis = condition_identity_axis(field)
        if (
            field in {"sample_id", "quench_state", "quench_direction"}
            or axis == "composition"
            or (axis == "temperature_K" and condition.inference is not None)
        ):
            continue
        relation = _cell_relation(observation.locator, condition.locator)
        if relation is not None:
            excluded[field] = relation
        elif ferric_target and field in _FERRIC_CONDITION_FIELDS and (
            condition.locator is None or observation.locator is None
            or condition.locator == observation.locator
        ):
            excluded[field] = "unlocated_ferric_assay"

    observation_ref = f"observation[{observation.observation_id}]"
    target_inputs = {observation.observation_id, observation_ref, f"{observation_ref}.value"}
    while True:
        inputs = target_inputs | {
            reference for field, reason in excluded.items()
            if reason != "original_condition_differs_from_run"
            for reference in _condition_references(observation, field)
        }
        dependent = {
            field for field, condition in conditions.items()
            if field not in excluded and condition.inference is not None and (
                not inputs.isdisjoint(condition.inference.inputs)
                or any(
                    parameter.locator != condition.locator
                    and _cell_relation(observation.locator, parameter.locator) == "same"
                    for name, parameter in condition.inference.parameters if name.startswith("original")
                )
            )
        }
        if not dependent:
            break
        excluded.update(dict.fromkeys(dependent, "derived_from_target"))

    independent = {field: condition for field, condition in conditions.items() if field not in excluded}
    invalid_axes = correction_axes | {
        axis for field, reason in excluded.items()
        if reason != "original_condition_differs_from_run"
        and (axis := condition_identity_axis(field)) is not None
    }
    identity_fields = dict.fromkeys(invalid_axes)
    if "temperature_K" in invalid_axes or temperature_operand:
        selected_temperature = temperature_selection(independent)
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
            source=repr(conditions[field].locator) if field in conditions else None,
            destination=repr(observation.locator),
        )
        for field, reason in sorted(excluded.items())
        if (field in conditions or condition_identity_axis(field) in invalid_axes)
    )
    if not identity_fields and independent == conditions:
        return observation, notices
    return replace(
        observation, identity=replace(identity, **identity_fields),
        point_conditions=independent if observation.point_conditions is not None else None,
    ), notices


def imposed_oxygen_target(observation: Observation, experiment: Experiment | None, quantity: Quantity) -> bool:
    """Identify a setpoint observation by its recorded control provenance."""
    species = observation.identity.species
    if quantity not in {Quantity.FUGACITY, Quantity.P_PARTIAL} or species is None or species.formula != "O2":
        return False
    control = experiment.fO2_control if experiment is not None else None
    derivation = observation.derivation
    if control is not None and control.channel.is_value and control.channel.value is FO2Channel.INTRINSIC:
        return False
    if _is_imposed_oxygen_cell(observation.locator, experiment):
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
