"""Printed field names used to resolve measured iron redox state."""

from dataclasses import replace

from simulator.battery.records import Observation

FE3_FE2_RATIO_PRINTED_FIELDS: tuple[str, ...] = ("fe3_fe2_ratio", "Fe3_over_Fe2")
FERRIC_FRACTION_FIELDS: tuple[str, ...] = ("Fe3_over_FeT", "Fe3_over_sum_Fe")
KRESS_FE2O3_WT_PCT_FIELD = "Fe2O3_wt_pct"
KRESS_FEO_WT_PCT_FIELD = "FeO_wt_pct"

_FERRIC_CONDITION_FIELDS = frozenset((
    *FE3_FE2_RATIO_PRINTED_FIELDS, *FERRIC_FRACTION_FIELDS,
    KRESS_FE2O3_WT_PCT_FIELD, KRESS_FEO_WT_PCT_FIELD,
))


def independent_redox_conditions(observation: Observation) -> Observation:
    """Project conditions for a Fe redox target, retaining independent assays."""
    conditions = observation.point_conditions
    if not conditions:
        return observation

    derivation = observation.derivation
    operands = set()
    if derivation is not None:
        operands.update(
            name.removeprefix("original_")
            for name, _ in derivation.parameters
            if name.startswith("original_")
        )
    # A ferric source field on the target cell can echo the measurement.
    # An assay used in its place must carry its own source location.
    operands.update(
        field
        for field, condition in conditions.items()
        if field in _FERRIC_CONDITION_FIELDS and (
            condition.locator is None
            or observation.locator is None
            or condition.locator == observation.locator
        )
    )
    target_inputs = operands | {observation.observation_id}
    independent = {
        field: condition
        for field, condition in conditions.items()
        if field not in operands and (
            condition.inference is None
            or target_inputs.isdisjoint(condition.inference.inputs)
        )
    }
    if len(independent) == len(conditions):
        return observation

    identity = observation.identity
    if any(key in conditions and key not in independent for key in ("fO2_log", "fO2_Pa")):
        identity = replace(identity, fO2_Pa=None)
    return replace(observation, identity=identity, point_conditions=independent)
