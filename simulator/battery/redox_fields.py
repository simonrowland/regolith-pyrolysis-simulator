"""Source fields and information flow for predictive redox controls."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum

from simulator.battery.enums import FO2Channel, NoticeKind, Quantity, ValueKind
from simulator.battery.identity import quantity_token
from simulator.battery.records import Experiment, Located, Notice, Observation, State, Value
from simulator.battery.printed_cells import (
    PrintedCell as _PrintedCell,
    inherited_row_locator,
    located_printed_cell,
    printed_cell as _printed_cell,
    printed_cell_address,
    same_printed_cell,
)

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


class TargetRelation(str, Enum):
    SAME_PRINTED_CELL = "same_printed_cell"
    DERIVED_FROM_TARGET = "derived_from_target"
    TARGET_ANALYTE_OPERAND = "target_analyte_operand"
    INDEPENDENT_RUN_CONTROL = "independent_run_control"
    IMPOSED_CONTROL_TARGET = "imposed_control_target"
    INDEPENDENT = "independent"


@dataclass(frozen=True)
class CandidateInput:
    field: str
    condition: Located
    axis: str | None = None
    imposed_oxygen: bool = False
    experiment: Experiment | None = None
    resolve_identity_sources: Callable[[], Mapping[str, tuple[str, ...]]] | None = None


@dataclass(frozen=True)
class TargetProvenance:
    relation: TargetRelation
    ambiguous_cell: bool = False
    target_operand: bool = False

    @property
    def exclusion_reason(self) -> str | None:
        if self.ambiguous_cell:
            return "ambiguous"
        return {
            TargetRelation.SAME_PRINTED_CELL: "same",
            TargetRelation.DERIVED_FROM_TARGET: "derived_from_target",
            TargetRelation.TARGET_ANALYTE_OPERAND: "analyte_operand",
        }.get(self.relation)


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


def classify_target_provenance(
    observation: Observation, candidate: CandidateInput,
) -> TargetProvenance:
    """Classify the information the candidate carries about the scored value."""
    targets = _target_cells(observation)
    conditions = observation.point_conditions or {}
    scope = f"observation[{observation.observation_id}]"
    seen: set[int] = set()
    references_run_control = False
    imposed_inputs = _imposed_oxygen_inputs(candidate.experiment)

    def derivation_match(condition: Located) -> str | None:
        nonlocal references_run_control
        inference = condition.inference
        if inference is None or id(condition) in seen:
            return None
        seen.add(id(condition))
        operands = [operand for _, operand in inference.parameters]
        for reference in inference.inputs:
            path = reference.removeprefix(f"{scope}.")
            if reference in {observation.observation_id, scope} or (
                path != reference and path.partition(".")[0] in {"value", "values"}
            ):
                return "same"
            axis = None
            sources = (path,)
            if path != reference and path.startswith("identity."):
                axis = path.removeprefix("identity.")
                resolve_sources = candidate.resolve_identity_sources
                sources = () if resolve_sources is None else resolve_sources().get(axis, ())
            for source in sources:
                field = source.removeprefix("point_conditions.")
                operand = conditions.get(field) or _referenced_run_input(candidate.experiment, source)
                if operand is not None:
                    operands.append(operand)
                    references_run_control |= _is_run_control(
                        CandidateInput(field, operand, axis, field in imposed_inputs),
                    )
        for operand in operands:
            match = derivation_match(operand) or _match_target_cell(
                targets, _printed_cell(operand, address_only=operand.inference is not None),
            )
            if match is not None:
                return match
        return None

    condition = candidate.condition
    dependency = derivation_match(condition)
    if dependency is not None:
        return TargetProvenance(TargetRelation.DERIVED_FROM_TARGET, dependency == "ambiguous")

    cell = _printed_cell(condition, address_only=condition.inference is not None)
    match = _match_target_cell(targets, cell)
    if match == "ambiguous":
        return TargetProvenance(TargetRelation.SAME_PRINTED_CELL, ambiguous_cell=True)

    derivation = observation.derivation
    field = candidate.field
    references = _condition_references(observation, field)
    if candidate.imposed_oxygen and (
        match == "same" or (
            derivation is not None
            and derivation.inputs == (field,)
            and not derivation.parameters
            and observation.value.point is not None
            and condition.state.value in (observation.value, observation.value.point)
            # The named scalar copy establishes provenance. If both locators
            # are present, they must agree with that provenance.
            and (
                observation.locator is None or condition.locator is None
                or same_printed_cell(
                    _printed_cell(Located(State.of(observation.value), observation.locator)),
                    _printed_cell(condition),
                ) is not None
            )
        )
    ):
        species = observation.identity.species
        if species is not None and species.formula == "O2" and quantity_token(observation.identity) in {
            Quantity.FUGACITY, Quantity.P_PARTIAL,
        }:
            return TargetProvenance(TargetRelation.IMPOSED_CONTROL_TARGET)
    if match is not None:
        return TargetProvenance(TargetRelation.SAME_PRINTED_CELL)
    target_operand = derivation is not None and (
        not references.isdisjoint(derivation.inputs)
        or any(
            name == field or name == f"original_{field}"
            or (name == "original" and operand.inference is not None
                and not references.isdisjoint(operand.inference.inputs))
            or (name != "original" and same_printed_cell(cell, _printed_cell(operand)) is not None)
            for name, operand in derivation.parameters
        )
    )
    is_oxygen_control = candidate.axis == "fO2_Pa" and any(
        same_printed_cell(cell, _printed_cell(operand)) is not None
        and derivation_match(operand) is None
        for field, operand in imposed_inputs.items()
        if field == "experiment.fO2_control.oxygen_partial_pressure_Pa"
    )
    if _is_run_control(candidate) or is_oxygen_control:
        return TargetProvenance(TargetRelation.INDEPENDENT_RUN_CONTROL, target_operand=target_operand)
    if target_operand:
        return TargetProvenance(TargetRelation.TARGET_ANALYTE_OPERAND)
    if references_run_control:
        return TargetProvenance(TargetRelation.INDEPENDENT_RUN_CONTROL)
    return TargetProvenance(TargetRelation.INDEPENDENT)


def _is_run_control(candidate: CandidateInput) -> bool:
    field = candidate.field.rsplit(".", 1)[-1]
    axis = candidate.axis or field
    return (
        candidate.imposed_oxygen
        or CONTROL_VARIABLE_FIELDS.get(axis) == frozenset()
        or any(field in fields for fields in CONTROL_VARIABLE_FIELDS.values())
    )


def _imposed_oxygen_inputs(experiment: Experiment | None) -> dict[str, Located]:
    if experiment is None:
        return {}
    control = experiment.fO2_control
    if control is not None and control.channel.is_value and control.channel.value is FO2Channel.INTRINSIC:
        return {}
    inputs = {"experiment.sample.container": experiment.sample.container}
    if control is not None:
        inputs.update({
            "experiment.fO2_control.oxygen_partial_pressure_Pa": control.oxygen_partial_pressure_Pa,
            "experiment.fO2_control.buffer": control.buffer,
            "experiment.fO2_control.gas_ratio": control.gas_ratio.value if control.gas_ratio is not None else None,
        })
    return {
        field: condition for field, condition in inputs.items()
        if condition is not None and condition.state.is_value
    }


def _referenced_run_input(experiment: Experiment | None, reference: str) -> Located | None:
    scope, _, path = reference.partition(".")
    if experiment is None or scope != "experiment":
        return None
    group, _, field = path.partition(".")
    if group == "conditions":
        return experiment.conditions.get(field)
    if group in {"sample", "pressure_environment", "fO2_control"}:
        if field == "gas_ratio":
            field = "gas_ratio.value"
        name, _, member = field.partition(".")
        value = getattr(getattr(experiment, group), name, None)
        if member:
            value = getattr(value, member, None)
        return value if isinstance(value, Located) else None
    return None


def independent_redox_conditions(
    observation: Observation, *,
    quantity: Quantity,
    ferric_target: bool,
    condition_identity_axis: Callable[[str], str | None],
    condition_identity_value: Callable[[str, object, Mapping[str, Located]], object],
    temperature_selection: Callable[[Mapping[str, Located]], State | None],
    pressure_selection: Callable[[Mapping[str, Located]], Decimal | Value | None],
    identity_sources: Mapping[str, tuple[str, ...]],
    experiment: Experiment | None = None,
) -> tuple[Observation, Experiment | None, tuple[Notice, ...]]:
    """Build the one permitted input view and retain the reason for each exclusion."""
    conditions = observation.point_conditions or {}
    identity = observation.identity
    imposed_inputs = _imposed_oxygen_inputs(experiment)
    derivation = observation.derivation
    parameters = dict(derivation.parameters) if derivation is not None else {}
    excluded: dict[str, str] = {}
    correction_axes = set()
    correction_pressure = None
    temperature_operand = False

    def classify(field: str, condition: Located) -> TargetProvenance:
        return classify_target_provenance(observation, CandidateInput(
            field, condition, condition_identity_axis(field.rsplit(".", 1)[-1]),
            imposed_oxygen=field in imposed_inputs, experiment=experiment,
            resolve_identity_sources=lambda: identity_sources,
        ))

    point_inputs = {
        field: classify(field, condition) for field, condition in conditions.items()
        if field not in {"sample_id", "quench_state", "quench_direction"}
    }
    for field in conditions.keys() | {
        name.removeprefix("original_") for name in parameters if name.startswith("original_")
    }:
        axis = condition_identity_axis(field)
        original = parameters.get(f"original_{field}")
        if axis == "temperature_K" and field in point_inputs and point_inputs[field].target_operand:
            temperature_operand = True
        if (
            original is None or not original.state.is_value
            or axis not in {"temperature_K", "total_pressure_Pa"}
        ):
            continue
        original_value = condition_identity_value(field, original.state.value, conditions)
        projected = getattr(identity, axis)
        if original_value is None:
            continue
        axis_conditions = {
            key: condition_identity_value(key, condition.state.value, conditions)
            for key, condition in conditions.items()
            if condition.state.is_value and condition_identity_axis(key) == axis
            and point_inputs[key].exclusion_reason is None
        }
        projected_value = (
            projected.value if projected is not None and projected.is_value else None
        )
        run_value = None
        if experiment is not None:
            run_conditions = {
                key: condition for key, condition in experiment.conditions.items()
                if classify(f"experiment.conditions.{key}", condition).exclusion_reason is None
            }
            if axis == "temperature_K":
                selected = temperature_selection(run_conditions)
                if selected is not None:
                    run_value = selected.value
            else:
                pressure = experiment.pressure_environment.total_pressure_Pa
                if pressure.state.is_value and classify(
                    "experiment.pressure_environment.total_pressure_Pa", pressure,
                ).exclusion_reason is None:
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

    for field, provenance in point_inputs.items():
        condition = conditions[field]
        if provenance.exclusion_reason is not None:
            excluded[field] = provenance.exclusion_reason
            continue
        if not condition.state.is_value:
            continue
        if ferric_target and field in _FERRIC_CONDITION_FIELDS and (
            condition.locator is None or observation.locator is None
        ):
            excluded[field] = "unlocated_ferric_assay"

    independent = {field: condition for field, condition in conditions.items() if field not in excluded}
    located_inputs = {field: conditions[field] for field in excluded if field in conditions}
    excluded_sources = {f"point_conditions.{field}" for field in excluded}
    if experiment is not None:
        def independent_control(field: str, condition: Located | None) -> Located | None:
            if condition is None or (reason := classify(field, condition).exclusion_reason) is None:
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
        if oxygen is not None:
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


def imposed_oxygen_target(
    observation: Observation, experiment: Experiment | None, *,
    resolve_identity_sources: Callable[[Observation, Experiment | None], Mapping[str, tuple[str, ...]]],
) -> bool:
    """Identify a setpoint observation by its recorded control provenance."""
    return any(
        classify_target_provenance(
            observation, CandidateInput(
                field, condition, imposed_oxygen=True, experiment=experiment,
                resolve_identity_sources=lambda: resolve_identity_sources(observation, experiment),
            ),
        ).relation is TargetRelation.IMPOSED_CONTROL_TARGET
        for field, condition in _imposed_oxygen_inputs(experiment).items()
    )
