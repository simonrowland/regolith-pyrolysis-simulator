"""Feasible-run objective vector projection for recipe optimization."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from collections.abc import Callable, Sequence
from typing import Any, Mapping, TypeVar

from simulator.config import DEFAULT_DATA_DIR, load_config_bundle
from simulator.three_product_report import classify_products
from simulator.trace import wall_deposit_kg_by_zone_species


_MISSING = object()
VALID_OBJECTIVE_SENSES = {"minimize", "maximize"}
_OBJECTIVE_SENSE_ALIASES = {
    "min": "minimize",
    "minimum": "minimize",
    "minimize": "minimize",
    "max": "maximize",
    "maximum": "maximize",
    "maximize": "maximize",
}


class ObjectiveProfileError(ValueError):
    """Raised when an optimizer profile cannot define an objective vector."""


class ObjectiveComputationError(RuntimeError):
    """Raised when declared objectives cannot be computed from run outputs."""


@dataclass(frozen=True)
class ObjectiveDefinition:
    metric: str
    sense: str
    units: str = ""
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.metric:
            raise ObjectiveProfileError("objective metric is required")
        object.__setattr__(self, "sense", normalize_objective_sense(self.sense))
        ordinal = int(self.ordinal)
        if ordinal < 0:
            raise ObjectiveProfileError("objective ordinal must be non-negative")
        object.__setattr__(self, "ordinal", ordinal)


@dataclass(frozen=True)
class ObjectiveValue:
    metric: str
    sense: str
    value: float
    units: str = ""
    ordinal: int = 0

    def __post_init__(self) -> None:
        if not self.metric:
            raise ObjectiveProfileError("objective metric is required")
        object.__setattr__(self, "sense", normalize_objective_sense(self.sense))
        if not math.isfinite(float(self.value)):
            raise ObjectiveComputationError(
                f"objective {self.metric!r} produced non-finite value"
            )
        object.__setattr__(self, "value", float(self.value))
        ordinal = int(self.ordinal)
        if ordinal < 0:
            raise ObjectiveProfileError("objective ordinal must be non-negative")
        object.__setattr__(self, "ordinal", ordinal)


@dataclass(frozen=True)
class ObjectiveVector:
    values: tuple[ObjectiveValue, ...]

    def __post_init__(self) -> None:
        metrics = tuple(value.metric for value in self.values)
        if len(set(metrics)) != len(metrics):
            raise ObjectiveProfileError("objective metrics must be unique")

    def as_mapping(self) -> Mapping[str, float]:
        return MappingProxyType({value.metric: value.value for value in self.values})


ObjectiveLike = ObjectiveVector | Mapping[str, float]
T = TypeVar("T")


def objective_definitions(profile: Mapping[str, Any]) -> tuple[ObjectiveDefinition, ...]:
    raw_objectives = profile.get("objectives")
    if not isinstance(raw_objectives, (list, tuple)) or not raw_objectives:
        raise ObjectiveProfileError("profile.objectives must be a non-empty list")

    definitions: list[ObjectiveDefinition] = []
    for ordinal, raw in enumerate(raw_objectives):
        if not isinstance(raw, Mapping):
            raise ObjectiveProfileError("each objective must be a mapping")
        definitions.append(
            ObjectiveDefinition(
                metric=str(raw.get("metric", "")),
                sense=str(raw.get("sense", "")),
                units=str(raw.get("units", "")),
                ordinal=ordinal,
            )
        )
    return tuple(definitions)


def compute_objectives(profile: Mapping[str, Any], run_execution: Any) -> ObjectiveVector:
    """Compute the declared objective vector from real simulator outputs."""

    definitions = objective_definitions(profile)
    sim = getattr(run_execution, "simulator", run_execution)
    product_classes = classify_products(
        sim,
        early_tap_mode=bool(profile.get("early_tap_mode", False)),
    )
    product_ledger = _product_ledger(sim)

    values = tuple(
        ObjectiveValue(
            metric=definition.metric,
            sense=definition.sense,
            value=_metric_value(definition.metric, sim, product_ledger, product_classes),
            units=definition.units,
            ordinal=definition.ordinal,
        )
        for definition in definitions
    )
    return ObjectiveVector(values)


def normalize_objective_sense(sense: str) -> str:
    normalized = _OBJECTIVE_SENSE_ALIASES.get(str(sense).strip().lower())
    if normalized is None:
        raise ObjectiveProfileError(
            "objective sense must be 'minimize' or 'maximize'"
        )
    return normalized


def objective_scores(
    objectives: ObjectiveLike,
    definitions: Sequence[ObjectiveDefinition],
) -> tuple[float, ...]:
    """Render objective values as maximize-native scores in profile order."""

    mapping = _objective_mapping(objectives)
    scores: list[float] = []
    for definition in definitions:
        try:
            value = mapping[definition.metric]
        except KeyError as exc:
            raise ObjectiveComputationError(
                f"objective {definition.metric!r} is missing"
            ) from exc
        numeric = _finite_float(value, definition.metric)
        scores.append(numeric if definition.sense == "maximize" else -numeric)
    return tuple(scores)


def dominates(
    left: ObjectiveLike,
    right: ObjectiveLike,
    definitions: Sequence[ObjectiveDefinition],
) -> bool:
    """Return true when left Pareto-dominates right for the profile directions."""

    left_scores = objective_scores(left, definitions)
    right_scores = objective_scores(right, definitions)
    return all(a >= b for a, b in zip(left_scores, right_scores)) and any(
        a > b for a, b in zip(left_scores, right_scores)
    )


def pareto_front(
    items: Sequence[T],
    definitions: Sequence[ObjectiveDefinition],
    *,
    objective_getter: Callable[[T], ObjectiveLike],
) -> tuple[T, ...]:
    """Stable non-dominated subset using profile objective order and directions."""

    front: list[T] = []
    for index, item in enumerate(items):
        objectives = objective_getter(item)
        if any(
            other_index != index
            and dominates(objective_getter(other), objectives, definitions)
            for other_index, other in enumerate(items)
        ):
            continue
        front.append(item)
    return tuple(front)


def product_summary(run_execution: Any, profile: Mapping[str, Any]) -> Mapping[str, Any]:
    sim = getattr(run_execution, "simulator", run_execution)
    summary: dict[str, Any] = {
        "product_ledger_kg": MappingProxyType(dict(_product_ledger(sim))),
        "product_classes": product_classes_summary(sim, profile),
    }
    summary.update(_coating_product_summary(run_execution))
    return MappingProxyType(summary)


def _coating_product_summary(run_execution: Any) -> Mapping[str, Any]:
    trace = getattr(run_execution, "trace", None)
    if trace is None:
        return MappingProxyType({})
    raw_by_segment = getattr(trace, "wall_deposit_by_segment_species_kg", None)
    if raw_by_segment is None:
        raise ObjectiveComputationError(
            "wall_deposit_by_segment_species_kg trace is missing"
        )
    if not isinstance(raw_by_segment, Mapping):
        raise ObjectiveComputationError(
            "wall_deposit_by_segment_species_kg trace is not a mapping"
        )
    by_segment = _wall_deposit_by_segment_species_summary(raw_by_segment)
    zone_by_segment = getattr(trace, "wall_zone_by_segment", None)
    if zone_by_segment is None:
        raise ObjectiveComputationError("wall_zone_by_segment trace is missing")
    if not isinstance(zone_by_segment, Mapping):
        raise ObjectiveComputationError("wall_zone_by_segment trace is not a mapping")
    try:
        by_zone = wall_deposit_kg_by_zone_species(raw_by_segment, zone_by_segment)
    except (TypeError, ValueError) as exc:
        raise ObjectiveComputationError(str(exc)) from exc
    return MappingProxyType({
        "wall_deposit_kg_by_segment_species": by_segment,
        "wall_deposit_kg_by_zone_species": MappingProxyType({
            zone: MappingProxyType(dict(species_kg))
            for zone, species_kg in by_zone.items()
        }),
        "campaigns_to_resinter": _campaigns_to_resinter(raw_by_segment),
    })


def _wall_deposit_by_segment_species_summary(
    raw: Mapping[Any, Any],
) -> Mapping[str, Mapping[str, float]]:
    by_segment: dict[str, dict[str, float]] = {}
    for key, kg in raw.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ObjectiveComputationError(
                "wall deposit key must be (segment, species)"
            )
        segment, species = str(key[0]), str(key[1])
        amount = _finite_float(kg, f"wall_deposit[{segment!r}][{species!r}]")
        if amount <= 1e-12:
            continue
        species_kg = by_segment.setdefault(segment, {})
        species_kg[species] = species_kg.get(species, 0.0) + amount
    return MappingProxyType({
        segment: MappingProxyType(dict(sorted(species_kg.items())))
        for segment, species_kg in sorted(by_segment.items())
    })


def _campaigns_to_resinter(
    wall_deposit_by_segment_species: Mapping[tuple[str, str], float],
) -> float | str:
    by_species: dict[str, float] = {}
    for key, kg in wall_deposit_by_segment_species.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ObjectiveComputationError(
                "wall deposit key must be (segment, species)"
            )
        species = str(key[1])
        amount = _finite_float(kg, f"wall_deposit[{key!r}]")
        if amount > 1e-12:
            by_species[species] = by_species.get(species, 0.0) + amount
    if not by_species:
        return "infinite"
    dominant_species = max(by_species, key=by_species.get)
    dominant_kg = by_species[dominant_species]
    threshold = _wall_resinter_threshold_kg()
    if threshold is None:
        return f"resinter_threshold_kg / {dominant_kg:.12g}"
    return threshold / dominant_kg


def _wall_resinter_threshold_kg() -> float | None:
    materials = load_config_bundle(DEFAULT_DATA_DIR).materials
    surfaces = materials.get("wall_surfaces", {}) or {}
    surface = (
        surfaces.get("interstage_duct", {}) or {}
        if isinstance(surfaces, Mapping)
        else {}
    )
    liner_material = str(surface.get("liner_material") or "")
    liners = materials.get("liner_materials", {}) or {}
    liner = (
        liners.get(liner_material, {}) or {}
        if isinstance(liners, Mapping)
        else {}
    )
    threshold = liner.get("resinter_threshold_kg")
    if threshold is None:
        return None
    return _finite_float(threshold, "resinter_threshold_kg")


def product_classes_summary(sim: Any, profile: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(
        classify_products(
            sim,
            early_tap_mode=bool(profile.get("early_tap_mode", False)),
        )
    )


def _metric_value(
    metric: str,
    sim: Any,
    product_ledger: Mapping[str, float],
    product_classes: Mapping[str, Any],
) -> float:
    if metric == "pure_silica_glass_kg":
        return _nested_float(product_classes, ("pure_silica_glass", "class_total_kg"))
    if metric == "metals_plus_o2_kg":
        return _nested_float(product_classes, ("metals_plus_O2", "class_total_kg"))
    if metric == "metals_total_kg":
        return _nested_float(product_classes, ("metals_plus_O2", "metals_total_kg"))
    if metric in {"O2_kg", "o2_kg", "oxygen_kg"}:
        return _nested_float(product_classes, ("metals_plus_O2", "O2_kg"))
    if metric == "oxygen_stored_kg":
        return _oxygen_partition_value(sim, "stored")
    if metric == "oxygen_vented_kg":
        return _oxygen_partition_value(sim, "vented")
    if metric in {"energy_kWh", "energy_total_kWh"}:
        return _sim_float(sim, "energy_cumulative_kWh", "energy_total_kWh")
    if metric in {"duration_h", "total_hours"}:
        return _duration_hours(sim)
    if metric.endswith("_kg"):
        species = metric[:-3]
        if species in product_ledger:
            return _finite_float(product_ledger[species], metric)
    raise ObjectiveComputationError(
        f"objective metric {metric!r} is not available from run outputs"
    )


def _objective_mapping(objectives: ObjectiveLike) -> Mapping[str, float]:
    if isinstance(objectives, ObjectiveVector):
        return objectives.as_mapping()
    if isinstance(objectives, Mapping):
        return objectives
    accessor = getattr(objectives, "as_mapping", None)
    if callable(accessor):
        raw = accessor()
        if isinstance(raw, Mapping):
            return raw
    raise ObjectiveComputationError("objective values must be a mapping")


def _product_ledger(sim: Any) -> Mapping[str, float]:
    ledger_method = getattr(sim, "product_ledger", None)
    if callable(ledger_method):
        raw = ledger_method()
    else:
        raw = getattr(getattr(sim, "record", None), "products_kg", {})
    if not isinstance(raw, Mapping):
        raise ObjectiveComputationError("product ledger is not a mapping")
    return MappingProxyType({
        str(species): _finite_float(kg, f"product_ledger[{species!r}]")
        for species, kg in raw.items()
    })


def _nested_float(root: Mapping[str, Any], path: tuple[str, ...]) -> float:
    node: Any = root
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            raise ObjectiveComputationError(
                f"objective source missing {'.'.join(path)}"
            )
        node = node[key]
    return _finite_float(node, ".".join(path))


def _oxygen_partition_value(sim: Any, key: str) -> float:
    partition_method = getattr(sim, "_oxygen_terminal_partition_kg", None)
    if callable(partition_method):
        partition = partition_method()
        if not isinstance(partition, Mapping):
            raise ObjectiveComputationError("oxygen terminal partition is not a mapping")
        if key not in partition or partition[key] is None:
            raise ObjectiveComputationError(
                f"oxygen terminal partition missing {key!r}"
            )
        return _finite_float(partition[key], f"oxygen_partition[{key!r}]")
    record = getattr(sim, "record", None)
    if record is not None:
        attr = "oxygen_stored_kg" if key == "stored" else "oxygen_vented_kg"
        return _required_attr_float(record, attr)
    raise ObjectiveComputationError("oxygen terminal partition unavailable")


def _sim_float(sim: Any, sim_attr: str, record_attr: str) -> float:
    value = getattr(sim, sim_attr, _MISSING)
    if value is not _MISSING:
        if value is None:
            raise ObjectiveComputationError(f"{sim_attr} is missing")
        return _finite_float(value, sim_attr)
    record = getattr(sim, "record", None)
    if record is not None:
        return _required_attr_float(record, record_attr)
    raise ObjectiveComputationError(f"{sim_attr} unavailable")


def _duration_hours(sim: Any) -> float:
    melt = getattr(sim, "melt", None)
    if melt is not None:
        value = getattr(melt, "hour", _MISSING)
        if value is not _MISSING:
            if value is None:
                raise ObjectiveComputationError("melt.hour is missing")
            return _finite_float(value, "melt.hour")
    record = getattr(sim, "record", None)
    if record is not None:
        return _required_attr_float(record, "total_hours")
    raise ObjectiveComputationError("run duration unavailable")


def _required_attr_float(obj: Any, attr: str) -> float:
    value = getattr(obj, attr, _MISSING)
    if value is _MISSING or value is None:
        raise ObjectiveComputationError(f"{attr} is missing")
    return _finite_float(value, attr)


def _finite_float(value: Any, label: str) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ObjectiveComputationError(f"{label} is not numeric") from exc
    if not math.isfinite(converted):
        raise ObjectiveComputationError(f"{label} is non-finite")
    return converted
