"""d-443 literature precision scopes, established only by located method facts."""

from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Mapping

import yaml

from simulator.battery.identity import quantity_token
from simulator.battery.records import Experiment, Located, Observation


@lru_cache(maxsize=1)
def method_class_bands() -> Mapping[str, Mapping[str, object]]:
    path = Path(__file__).resolve().parents[2] / "data/battery/method_class_bands.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["classes"]


def _located_fact(observation: Observation, experiment: Experiment, name: str):
    groups = [observation.point_conditions or {}, experiment.conditions]
    if experiment.apparatus is not None:
        groups.append(experiment.apparatus.calibration or {})
    facts = [group[name] for group in groups if name in group]
    if not facts or any(
        not isinstance(fact, Located) or fact.locator is None or not fact.state.is_value
        for fact in facts
    ):
        return None
    values = [fact.state.value for fact in facts]
    return values[0] if all(value == values[0] for value in values) else None


def select_method_class_band(
    observation: Observation, experiment: Experiment | None,
) -> dict[str, object]:
    """Unknown, contradictory, and unsupported provenance never implies a band."""
    unknown = {"refusal": {"reason": "method_class_applicability_unknown"}}
    if experiment is None or not experiment.method.is_value:
        return unknown
    quantity = quantity_token(observation.identity)
    candidates = []
    for class_id, entry in method_class_bands().items():
        if (
            entry["method_token"] != experiment.method.value.value
            or quantity is None or quantity.value not in entry["quantities"]
        ):
            continue
        conditions = {"technique": entry["technique"], **entry["applicability"]}
        matches = True
        for name, expected in conditions.items():
            bound = name.endswith(("_min", "_max"))
            fact_name = name.rsplit("_", 1)[0] if bound else name
            actual = _located_fact(observation, experiment, fact_name)
            if fact_name == "temperature_K":
                temperature = observation.identity.temperature_K
                actual = temperature.value if temperature.is_value else None
            if actual is None:
                matches = False
                break
            if bound:
                try:
                    actual, expected = Decimal(str(actual)), Decimal(str(expected))
                    matches = actual.is_finite() and (
                        actual >= expected if name.endswith("_min") else actual <= expected
                    )
                except InvalidOperation:
                    matches = False
            else:
                matches = actual == expected
            if not matches:
                break
        if matches:
            candidates.append((class_id, entry))
    if not candidates:
        return unknown
    if len(candidates) > 1:
        return {"refusal": {"reason": "method_class_ambiguous"}}
    class_id, entry = candidates[0]
    return {"class_id": class_id, "version": "method_class_bands.v1", **entry}
