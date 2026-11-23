"""Printed field names used to resolve measured iron redox state."""

from collections.abc import Callable
from dataclasses import replace

from simulator.battery.records import Locator, Observation

FE3_FE2_RATIO_PRINTED_FIELDS: tuple[str, ...] = ("fe3_fe2_ratio", "Fe3_over_Fe2")
FERRIC_FRACTION_FIELDS: tuple[str, ...] = ("Fe3_over_FeT", "Fe3_over_sum_Fe")
KRESS_FE2O3_WT_PCT_FIELD = "Fe2O3_wt_pct"
KRESS_FEO_WT_PCT_FIELD = "FeO_wt_pct"

_FERRIC_CONDITION_FIELDS = frozenset((
    *FE3_FE2_RATIO_PRINTED_FIELDS, *FERRIC_FRACTION_FIELDS,
    KRESS_FE2O3_WT_PCT_FIELD, KRESS_FEO_WT_PCT_FIELD,
))


def _cell_coordinates(locator: Locator) -> tuple[object, ...] | None:
    # The extract decoder preserves row and column keys as note segments.
    coordinates = {}
    for segment in (locator.note or "").split(";"):
        key, separator, value = segment.strip().partition("=")
        if separator and key in {"row", "column"}:
            coordinates[key] = value.strip()
    row = coordinates.get("row") or locator.record
    page = locator.page
    if locator.pdf_page_index is not None:
        page = locator.pdf_page_index + 1
    column = coordinates.get("column")
    if row is None or (page is None and locator.table is None):
        return None
    # An address inside a table or figure identifies a row or point until a
    # column is supplied. A standalone record on a page can identify a cell.
    if column is None and (locator.table is not None or locator.figure is not None):
        return None
    return (
        str(page) if page is not None else None, locator.table, locator.figure, row, column,
    )


def _same_redox_cell(target: Locator | None, condition: Locator | None) -> bool:
    if target is None or condition is None:
        return False
    if target.source_path and condition.source_path and target.source_path != condition.source_path:
        return False
    cell = _cell_coordinates(target)
    return cell is not None and cell == _cell_coordinates(condition)


def independent_redox_conditions(
    observation: Observation, *,
    ferric_target: bool,
    condition_identity_axis: Callable[[str], str | None],
) -> Observation:
    """Remove target operands and their projections before choosing controls."""
    conditions = observation.point_conditions or {}

    derivation = observation.derivation
    operands = set()
    if derivation is not None:
        operands.update(
            name.removeprefix("original_")
            for name, _ in derivation.parameters
            if name.startswith("original_")
        )
    operands.update(
        field
        for field, condition in conditions.items()
        # Migration attaches the target locator to bulk maps and generated
        # temperatures. Check their operands through lineage instead.
        if (
            field not in {"composition", "printed_composition"}
            and (field != "temperature_K" or condition.inference is None)
            and _same_redox_cell(observation.locator, condition.locator)
        )
        or (ferric_target and field in _FERRIC_CONDITION_FIELDS and (
            condition.locator is None
            or observation.locator is None
            or condition.locator == observation.locator
        ))
    )
    observation_ref = f"observation[{observation.observation_id}]"
    while True:
        target_inputs = {
            observation.observation_id, observation_ref, f"{observation_ref}.value",
        }
        for field in operands:
            target_inputs.update((
                field, f"point_conditions.{field}",
                f"{observation_ref}.point_conditions.{field}",
            ))
        dependent = {
            field for field, condition in conditions.items()
            if field not in operands and condition.inference is not None
            and not target_inputs.isdisjoint(condition.inference.inputs)
        }
        if not dependent:
            break
        operands.update(dependent)

    identity_fields = {}
    for field in operands:
        axis = condition_identity_axis(field)
        if axis is not None:
            identity_fields[axis] = None
    if not (identity_fields or operands.intersection(conditions)):
        return observation
    return replace(
        observation,
        identity=replace(observation.identity, **identity_fields),
        point_conditions=(
            {field: condition for field, condition in conditions.items() if field not in operands}
            if observation.point_conditions is not None else None
        ),
    )
