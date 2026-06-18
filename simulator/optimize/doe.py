"""DOE protocol and deterministic recipe samplers.

Design P0 requires proving fidelity correlation before trusting fast screening:
run a Sobol/LHC DOE across the recipe allowlist, then compare fast and high
fidelity with Spearman rank correlation, feasible/infeasible agreement, and
top-K recall. This module defines that protocol and sampler only; it does not
evaluate recipes or wire into RunExecutor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from types import MappingProxyType
from typing import Any, Mapping
import warnings

from simulator.optimize.recipe import KeyPath, RecipePatch, RecipeSchema

DEFAULT_ANCHOR_DELTA_FRACTION = 0.15

SCIPY_SOBOL_SAMPLER = "scipy-sobol"
DEPENDENCY_FREE_LHC_SAMPLER = "dependency-free-lhc"
SAMPLER_NAMES = (SCIPY_SOBOL_SAMPLER, DEPENDENCY_FREE_LHC_SAMPLER)
STREAMING_SAMPLER_NAMES = (SCIPY_SOBOL_SAMPLER,)

FIDELITY_CORRELATION_METRICS: tuple[str, ...] = (
    "spearman_rank_correlation",
    "feasible_infeasible_agreement",
    "top_k_recall",
)
PHASE_ORDER_VACUUM_FIRST = "vacuum_first"
PHASE_ORDER_OXIDIZE_FIRST = "oxidize_first"
PHASE_ORDER_SKELETONS = (
    PHASE_ORDER_VACUUM_FIRST,
    PHASE_ORDER_OXIDIZE_FIRST,
)
PHASE_ORDER_PHASE_COUNTS = (2, 3)
PHASE_ORDER_DWELL_H = (0.5, 1.0)
PHASE_ORDER_CONTINUOUS_KNOBS: tuple[KeyPath, ...] = (
    ("campaigns", "C0", "stage0_phase_temperature_C"),
    ("campaigns", "C0", "stage0_phase_pressure_mbar"),
    ("campaigns", "C0", "stage0_phase_pO2_mbar"),
)


@dataclass(frozen=True)
class PhaseOrderSkeleton:
    """Discrete Stage-0 phase order handed to the continuous optimizer."""

    order: str
    phase_count: int
    dwell_h: float
    sequence: tuple[str, ...]
    continuous_knob_paths: tuple[KeyPath, ...] = PHASE_ORDER_CONTINUOUS_KNOBS

    def __post_init__(self) -> None:
        if self.order not in PHASE_ORDER_SKELETONS:
            raise ValueError(f"unsupported phase order {self.order!r}")
        _validate_positive_int("phase_count", self.phase_count)
        if self.phase_count != len(self.sequence):
            raise ValueError("phase_count must match sequence length")
        if isinstance(self.dwell_h, bool) or not math.isfinite(float(self.dwell_h)):
            raise ValueError("dwell_h must be finite")
        if float(self.dwell_h) <= 0.0:
            raise ValueError("dwell_h must be positive")
        if not self.continuous_knob_paths:
            raise ValueError("continuous_knob_paths must be non-empty")
        object.__setattr__(self, "dwell_h", float(self.dwell_h))
        object.__setattr__(
            self,
            "continuous_knob_paths",
            tuple(tuple(str(part) for part in path) for path in self.continuous_knob_paths),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "phase_count": self.phase_count,
            "dwell_h": self.dwell_h,
            "sequence": list(self.sequence),
            "continuous_knob_paths": [list(path) for path in self.continuous_knob_paths],
        }


def phase_order_grid(
    *,
    orders: tuple[str, ...] = PHASE_ORDER_SKELETONS,
    phase_counts: tuple[int, ...] = PHASE_ORDER_PHASE_COUNTS,
    dwell_h_values: tuple[float, ...] = PHASE_ORDER_DWELL_H,
) -> tuple[PhaseOrderSkeleton, ...]:
    grid: list[PhaseOrderSkeleton] = []
    for order in orders:
        for phase_count in phase_counts:
            for dwell_h in dwell_h_values:
                grid.append(
                    PhaseOrderSkeleton(
                        order=str(order),
                        phase_count=int(phase_count),
                        dwell_h=float(dwell_h),
                        sequence=_phase_order_sequence(str(order), int(phase_count)),
                    )
                )
    return tuple(grid)


def _phase_order_sequence(order: str, phase_count: int) -> tuple[str, ...]:
    if order == PHASE_ORDER_VACUUM_FIRST:
        phases = ("vacuum", "oxidize")
    elif order == PHASE_ORDER_OXIDIZE_FIRST:
        phases = ("oxidize", "vacuum")
    else:
        raise ValueError(f"unsupported phase order {order!r}")
    return tuple(phases[index % len(phases)] for index in range(phase_count))


@dataclass(frozen=True)
class DoeSpec:
    """Deterministic DOE description for design P0 fidelity-correlation runs."""

    schema: RecipeSchema = field(default_factory=RecipeSchema, repr=False, compare=False)
    n_samples: int = 64
    seed: int = 0
    sampler_name: str = field(default_factory=lambda: active_sampler_name())
    # Optional seed-anchored (neighborhood) sampling. When ``anchor`` is set,
    # sampling perturbs each numeric knob within +/- ``delta_fraction`` of its
    # full schema range about the anchor center instead of sweeping the full
    # range. ``anchor`` carries no canonical-id semantics, so it is excluded
    # from equality/repr like ``schema``. It is still serialized for provenance.
    anchor: RecipePatch | None = field(default=None, repr=False, compare=False)
    delta_fraction: float = DEFAULT_ANCHOR_DELTA_FRACTION

    def __post_init__(self) -> None:
        _validate_positive_int("n_samples", self.n_samples)
        _validate_seed(self.seed)
        _validate_delta_fraction(self.delta_fraction)
        if self.anchor is not None:
            _validate_anchor(self.schema, self.anchor)

    @property
    def recipe_schema_version(self) -> str:
        return self.schema.recipe_schema_version

    @property
    def allowlist_version(self) -> str:
        return self.schema.allowlist_version

    @property
    def knob_paths(self) -> tuple[KeyPath, ...]:
        return tuple(spec.path for spec in self.schema.search_allowlist)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "seed": self.seed,
            "sampler_name": self.sampler_name,
            "recipe_schema_version": self.recipe_schema_version,
            "allowlist_version": self.allowlist_version,
            "knob_paths": [list(path) for path in self.knob_paths],
            "anchor": _anchor_to_entries(self.anchor),
            "delta_fraction": float(self.delta_fraction),
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, schema: RecipeSchema | None = None
    ) -> "DoeSpec":
        active_schema = schema or RecipeSchema()
        active_knob_paths = tuple(
            spec.path for spec in active_schema.search_allowlist
        )
        expected_paths = tuple(tuple(path) for path in payload.get("knob_paths", ()))
        if expected_paths and expected_paths != active_knob_paths:
            raise ValueError("DOE spec knob paths do not match the active schema")
        spec = cls(
            schema=active_schema,
            n_samples=int(payload["n_samples"]),
            seed=int(payload["seed"]),
            sampler_name=str(payload["sampler_name"]),
            anchor=_anchor_from_entries(payload.get("anchor")),
            delta_fraction=payload.get(
                "delta_fraction", DEFAULT_ANCHOR_DELTA_FRACTION
            ),
        )
        return spec


@dataclass(frozen=True)
class FidelityCorrelationProtocol:
    """Protocol for the later design P0 fast-vs-high fidelity gate.

    The later O-P0b runner must prove fidelity correlation before trusting fast
    screening by filling this protocol's metrics: per-objective Spearman rank
    correlation, feasible/infeasible agreement, and top-K recall.
    """

    doe: DoeSpec = field(default_factory=DoeSpec)
    fast_fidelity_name: str = "fast"
    high_fidelity_name: str = "high"
    objective_names: tuple[str, ...] = ()
    top_k_values: tuple[int, ...] = (5, 10, 20)
    metrics: tuple[str, ...] = FIDELITY_CORRELATION_METRICS

    def __post_init__(self) -> None:
        if tuple(self.metrics) != FIDELITY_CORRELATION_METRICS:
            raise ValueError("fidelity protocol metrics must match design P0")
        if not self.fast_fidelity_name or not self.high_fidelity_name:
            raise ValueError("fidelity names must be non-empty")
        for value in self.top_k_values:
            _validate_positive_int("top_k_values", value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doe": self.doe.to_dict(),
            "fast_fidelity_name": self.fast_fidelity_name,
            "high_fidelity_name": self.high_fidelity_name,
            "objective_names": list(self.objective_names),
            "top_k_values": list(self.top_k_values),
            "metrics": list(self.metrics),
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, schema: RecipeSchema | None = None
    ) -> "FidelityCorrelationProtocol":
        return cls(
            doe=DoeSpec.from_dict(payload["doe"], schema=schema),
            fast_fidelity_name=str(payload["fast_fidelity_name"]),
            high_fidelity_name=str(payload["high_fidelity_name"]),
            objective_names=tuple(str(name) for name in payload.get("objective_names", ())),
            top_k_values=tuple(int(value) for value in payload["top_k_values"]),
            metrics=tuple(str(metric) for metric in payload["metrics"]),
        )


@dataclass(frozen=True)
class FidelityCorrelationResult:
    """Container populated by the later O-P0b fidelity-correlation run."""

    protocol: FidelityCorrelationProtocol
    spearman_by_objective: Mapping[str, float | None] = field(default_factory=dict)
    feasible_infeasible_agreement: float | None = None
    top_k_recall: Mapping[int, float | None] = field(default_factory=dict)
    n_samples_compared: int = 0
    notes: tuple[str, ...] = ()
    fast_screen_trustworthy: bool = False
    n_samples_total: int = 0
    n_samples_dropped: int = 0
    confidence: str = "low"
    thresholds: Mapping[str, Any] = field(default_factory=dict)
    dropped_evaluations: tuple[Mapping[str, Any], ...] = ()
    artifact_paths: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.n_samples_compared < 0:
            raise ValueError("n_samples_compared must be non-negative")
        if self.n_samples_total < 0:
            raise ValueError("n_samples_total must be non-negative")
        if self.n_samples_dropped < 0:
            raise ValueError("n_samples_dropped must be non-negative")
        object.__setattr__(
            self,
            "spearman_by_objective",
            MappingProxyType(dict(self.spearman_by_objective)),
        )
        object.__setattr__(
            self,
            "top_k_recall",
            MappingProxyType(dict(self.top_k_recall)),
        )
        object.__setattr__(self, "thresholds", MappingProxyType(dict(self.thresholds)))
        object.__setattr__(
            self,
            "dropped_evaluations",
            tuple(MappingProxyType(dict(item)) for item in self.dropped_evaluations),
        )
        object.__setattr__(self, "artifact_paths", MappingProxyType(dict(self.artifact_paths)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol.to_dict(),
            "spearman_by_objective": dict(self.spearman_by_objective),
            "feasible_infeasible_agreement": self.feasible_infeasible_agreement,
            "top_k_recall": {
                str(k): v for k, v in sorted(self.top_k_recall.items(), key=lambda item: item[0])
            },
            "n_samples_compared": self.n_samples_compared,
            "notes": list(self.notes),
            "fast_screen_trustworthy": self.fast_screen_trustworthy,
            "n_samples_total": self.n_samples_total,
            "n_samples_dropped": self.n_samples_dropped,
            "confidence": self.confidence,
            "thresholds": dict(self.thresholds),
            "dropped_evaluations": [dict(item) for item in self.dropped_evaluations],
            "artifact_paths": dict(self.artifact_paths),
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, schema: RecipeSchema | None = None
    ) -> "FidelityCorrelationResult":
        return cls(
            protocol=FidelityCorrelationProtocol.from_dict(
                payload["protocol"], schema=schema
            ),
            spearman_by_objective={
                str(k): _optional_float(v)
                for k, v in payload.get("spearman_by_objective", {}).items()
            },
            feasible_infeasible_agreement=_optional_float(
                payload.get("feasible_infeasible_agreement")
            ),
            top_k_recall={
                int(k): _optional_float(v)
                for k, v in payload.get("top_k_recall", {}).items()
            },
            n_samples_compared=int(payload.get("n_samples_compared", 0)),
            notes=tuple(str(note) for note in payload.get("notes", ())),
            fast_screen_trustworthy=bool(payload.get("fast_screen_trustworthy", False)),
            n_samples_total=int(payload.get("n_samples_total", 0)),
            n_samples_dropped=int(payload.get("n_samples_dropped", 0)),
            confidence=str(payload.get("confidence", "low")),
            thresholds=dict(payload.get("thresholds", {})),
            dropped_evaluations=tuple(
                dict(item) for item in payload.get("dropped_evaluations", ())
            ),
            artifact_paths={
                str(k): str(v) for k, v in payload.get("artifact_paths", {}).items()
            },
        )


def sample_recipe_patches(
    schema: RecipeSchema | None = None,
    *,
    n_samples: int,
    seed: int,
    sampler_name: str | None = None,
    anchor: RecipePatch | None = None,
    delta_fraction: float = DEFAULT_ANCHOR_DELTA_FRACTION,
) -> tuple[RecipePatch, ...]:
    """Sample validated RecipePatch objects across the schema allowlist.

    With ``anchor=None`` (default) this sweeps each knob's full schema range and
    behaviour is unchanged. With an ``anchor`` RecipePatch, each numeric knob is
    instead perturbed within ``+/- delta_fraction * (high - low)`` of the anchor
    center, clamped to ``[low, high]`` -- a small neighborhood around a known
    recipe. The same unit-hypercube generator / ``sampler_name`` is reused, so
    results stay deterministic for a given seed.
    """

    active_schema = schema or RecipeSchema()
    _validate_positive_int("n_samples", n_samples)
    _validate_seed(seed)
    _validate_delta_fraction(delta_fraction)
    active_sampler = active_sampler_name() if sampler_name is None else sampler_name
    _validate_sampler_name(active_sampler)

    search_allowlist = active_schema.search_allowlist
    specs = tuple(
        spec for spec in search_allowlist if not active_schema.is_forbidden(spec.path)
    )
    if len(specs) != len(search_allowlist):
        raise ValueError("RecipeSchema allowlist contains forbidden paths")

    mapper = _resolve_value_mapper(active_schema, specs, anchor, delta_fraction)
    if not specs:
        return tuple(RecipePatch({}).validated(active_schema) for _ in range(n_samples))

    points = _unit_hypercube_points(len(specs), n_samples, seed, active_sampler)
    patches: list[RecipePatch] = []
    for row in points:
        values = _map_unit_row(
            active_schema,
            specs,
            row,
            mapper,
            anchor=anchor,
            delta_fraction=delta_fraction,
        )
        patches.append(RecipePatch(values).validated(active_schema))
    return tuple(patches)


def sample_recipe_patch_at_index(
    schema: RecipeSchema | None = None,
    *,
    index: int,
    seed: int,
    sampler_name: str | None = None,
    anchor: RecipePatch | None = None,
    delta_fraction: float = DEFAULT_ANCHOR_DELTA_FRACTION,
) -> RecipePatch:
    """Sample one validated RecipePatch at a stable global sequence index.

    Honors the same ``anchor`` / ``delta_fraction`` neighborhood semantics as
    :func:`sample_recipe_patches` for chunk-invariant samplers. Anchored
    dependency-free LHC streaming is unsupported; use ``sample_recipe_patches``
    for that sampler.
    """

    active_schema = schema or RecipeSchema()
    _validate_non_negative_int("index", index)
    _validate_seed(seed)
    _validate_delta_fraction(delta_fraction)
    active_sampler = active_sampler_name() if sampler_name is None else sampler_name
    _validate_sampler_name(active_sampler)

    search_allowlist = active_schema.search_allowlist
    specs = tuple(
        spec for spec in search_allowlist if not active_schema.is_forbidden(spec.path)
    )
    if len(specs) != len(search_allowlist):
        raise ValueError("RecipeSchema allowlist contains forbidden paths")

    if anchor is not None and active_sampler == DEPENDENCY_FREE_LHC_SAMPLER:
        _validate_anchor(active_schema, anchor, specs=specs)
        raise ValueError(
            "anchored sample_recipe_patch_at_index is unsupported for "
            "dependency-free-lhc because the sampler is not chunk-invariant; "
            "use sample_recipe_patches"
        )

    mapper = _resolve_value_mapper(active_schema, specs, anchor, delta_fraction)
    if not specs:
        return RecipePatch({}).validated(active_schema)

    point = _unit_hypercube_point(len(specs), index, seed, active_sampler)
    values = _map_unit_row(
        active_schema,
        specs,
        point,
        mapper,
        anchor=anchor,
        delta_fraction=delta_fraction,
    )
    return RecipePatch(values).validated(active_schema)


def active_sampler_name() -> str:
    return SCIPY_SOBOL_SAMPLER if _scipy_sobol_available() else DEPENDENCY_FREE_LHC_SAMPLER


def _unit_hypercube_points(
    dimensions: int, n_samples: int, seed: int, sampler_name: str
) -> tuple[tuple[float, ...], ...]:
    if sampler_name == SCIPY_SOBOL_SAMPLER:
        if not _scipy_sobol_available():
            raise RuntimeError("scipy-sobol sampler requested but scipy is unavailable")
        return _scipy_sobol_points(dimensions, n_samples, seed)
    if sampler_name == DEPENDENCY_FREE_LHC_SAMPLER:
        return _dependency_free_lhc_points(dimensions, n_samples, seed)
    raise ValueError(f"unsupported DOE sampler {sampler_name!r}")


def _unit_hypercube_point(
    dimensions: int, index: int, seed: int, sampler_name: str
) -> tuple[float, ...]:
    if sampler_name == SCIPY_SOBOL_SAMPLER:
        if not _scipy_sobol_available():
            raise RuntimeError("scipy-sobol sampler requested but scipy is unavailable")
        return _scipy_sobol_point(dimensions, index, seed)
    if sampler_name == DEPENDENCY_FREE_LHC_SAMPLER:
        raise ValueError(
            "dependency-free-lhc sampler is not chunk-invariant for streaming ask"
        )
    raise ValueError(f"unsupported DOE sampler {sampler_name!r}")


def _scipy_sobol_points(
    dimensions: int, n_samples: int, seed: int
) -> tuple[tuple[float, ...], ...]:
    """Return deterministic Sobol points.

    Non-power-of-two sample counts intentionally generate the next larger Sobol
    net and truncate to n_samples, which is deterministic but not balance-preserving.
    """

    from scipy.stats import qmc

    sampler = qmc.Sobol(d=dimensions, scramble=True, seed=seed)
    power = math.ceil(math.log2(n_samples))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        points = sampler.random_base2(power)
    return tuple(tuple(float(value) for value in row) for row in points[:n_samples])


def _scipy_sobol_point(dimensions: int, index: int, seed: int) -> tuple[float, ...]:
    from scipy.stats import qmc

    sampler = qmc.Sobol(d=dimensions, scramble=True, seed=seed)
    if index:
        sampler.fast_forward(index)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        point = sampler.random(1)[0]
    return tuple(float(value) for value in point)


def _dependency_free_lhc_points(
    dimensions: int, n_samples: int, seed: int
) -> tuple[tuple[float, ...], ...]:
    rng = random.Random(seed)
    columns: list[list[float]] = []
    for _ in range(dimensions):
        order = list(range(n_samples))
        rng.shuffle(order)
        columns.append([(bucket + rng.random()) / n_samples for bucket in order])
    return tuple(tuple(columns[dim][row] for dim in range(dimensions)) for row in range(n_samples))


def _scipy_sobol_available() -> bool:
    try:
        from scipy.stats import qmc  # noqa: F401
    except Exception:
        return False
    return True


def _map_unit_value(spec: Any, unit_value: float) -> Any:
    if spec.kind == "categorical":
        if not spec.choices:
            raise ValueError(f"{'.'.join(spec.path)} categorical knob has no choices")
        index = min(int(unit_value * len(spec.choices)), len(spec.choices) - 1)
        return spec.choices[index]

    low, high = _numeric_bounds(spec)
    value = low + unit_value * (high - low)
    if spec.kind == "int":
        low_int = int(low)
        high_int = int(high)
        bucket = min(int(unit_value * (high_int - low_int + 1)), high_int - low_int)
        return low_int + bucket
    if spec.kind == "float":
        return float(value)
    raise ValueError(f"{'.'.join(spec.path)} has unsupported knob kind {spec.kind!r}")


def _map_unit_row(
    schema: RecipeSchema,
    specs: tuple[Any, ...],
    row: tuple[float, ...],
    mapper,
    *,
    anchor: RecipePatch | None = None,
    delta_fraction: float = DEFAULT_ANCHOR_DELTA_FRACTION,
) -> dict[KeyPath, Any]:
    values: dict[KeyPath, Any] = {}
    unit_by_path: dict[KeyPath, float] = {}
    for spec, unit_value in zip(specs, row, strict=True):
        unit_by_path[spec.path] = float(unit_value)
        values[spec.path] = mapper(spec, unit_value)
    _couple_pressure_default_pairs(
        schema,
        specs,
        values,
        unit_by_path,
        anchor=anchor,
        delta_fraction=delta_fraction,
    )
    return values


def _couple_pressure_default_pairs(
    schema: RecipeSchema,
    specs: tuple[Any, ...],
    values: dict[KeyPath, Any],
    unit_by_path: Mapping[KeyPath, float],
    *,
    anchor: RecipePatch | None,
    delta_fraction: float,
) -> None:
    spec_by_path = {spec.path: spec for spec in specs}
    for po2_path, total_path in schema.PRESSURE_COUPLED_DEFAULT_PAIRS:
        if po2_path not in values or total_path not in values:
            continue
        po2_spec = spec_by_path[po2_path]
        po2_low, po2_high = _numeric_bounds(po2_spec)
        total = float(values[total_path])
        if anchor is not None:
            po2_low, po2_high = _anchored_numeric_interval(
                po2_spec, anchor.values[po2_path], delta_fraction
            )
            feasible_high = min(po2_high, total)
            tolerance = max(1e-12, 1e-12 * max(1.0, abs(po2_low), abs(total)))
            if feasible_high + tolerance < po2_low:
                raise ValueError(
                    "pressure_default_pair_infeasible_bounds: "
                    f"{'.'.join(po2_path)} anchored low {po2_low:.12g} exceeds "
                    f"{'.'.join(total_path)} {total:.12g}"
                )
            values[po2_path] = float(
                min(max(float(values[po2_path]), po2_low), feasible_high)
            )
            continue
        feasible_high = min(po2_high, total)
        tolerance = max(1e-12, 1e-12 * max(1.0, abs(po2_low), abs(total)))
        if feasible_high + tolerance < po2_low:
            raise ValueError(
                "pressure_default_pair_infeasible_bounds: "
                f"{'.'.join(po2_path)} low {po2_low:.12g} exceeds "
                f"{'.'.join(total_path)} {total:.12g}"
            )
        values[po2_path] = float(
            po2_low + unit_by_path[po2_path] * (feasible_high - po2_low)
        )


def _resolve_value_mapper(
    schema: RecipeSchema,
    specs: tuple[Any, ...],
    anchor: RecipePatch | None,
    delta_fraction: float,
):
    """Return the per-knob unit->value mapper for full-range or anchored sampling."""

    _validate_delta_fraction(delta_fraction)
    if anchor is None:
        return _map_unit_value
    _validate_anchor(schema, anchor, specs=specs)
    centers = dict(anchor.values)

    def _mapper(spec: Any, unit_value: float) -> Any:
        return _map_unit_value_anchored(spec, unit_value, centers[spec.path], delta_fraction)

    return _mapper


def _map_unit_value_anchored(
    spec: Any, unit_value: float, center: Any, delta_fraction: float
) -> Any:
    # Categorical knobs have no metric neighborhood: hold the anchor choice
    # fixed across the sweep. _validate_anchor has already confirmed it is a
    # legal choice for this spec.
    if spec.kind == "categorical":
        return center

    low, high = _numeric_bounds(spec)
    anchored_low, anchored_high = _anchored_numeric_interval(
        spec, center, delta_fraction
    )
    # value = clamp(c + (2u-1) * delta_fraction * (hi-lo), lo, hi)
    half_width = delta_fraction * (high - low)
    value = float(center) + (2.0 * unit_value - 1.0) * half_width
    value = min(max(value, anchored_low), anchored_high)
    if spec.kind == "int":
        return int(round(value))
    if spec.kind == "float":
        return float(value)
    raise ValueError(f"{'.'.join(spec.path)} has unsupported knob kind {spec.kind!r}")


def _anchored_numeric_interval(
    spec: Any, center: Any, delta_fraction: float
) -> tuple[float, float]:
    low, high = _numeric_bounds(spec)
    half_width = delta_fraction * (high - low)
    center_value = float(center)
    return max(center_value - half_width, low), min(center_value + half_width, high)


def _numeric_bounds(spec: Any) -> tuple[float, float]:
    if spec.low is None or spec.high is None:
        raise ValueError(f"{'.'.join(spec.path)} numeric knob lacks bounds")
    low = float(spec.low)
    high = float(spec.high)
    if not math.isfinite(low) or not math.isfinite(high) or low >= high:
        raise ValueError(f"{'.'.join(spec.path)} has invalid numeric bounds")
    return low, high


def _validate_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int")


def _validate_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _validate_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative int")


def _validate_sampler_name(sampler_name: str) -> None:
    if sampler_name not in SAMPLER_NAMES:
        raise ValueError(f"unsupported DOE sampler {sampler_name!r}")


def _validate_delta_fraction(delta_fraction: float) -> None:
    if isinstance(delta_fraction, bool) or not isinstance(delta_fraction, (int, float)):
        raise ValueError("delta_fraction must be a number in (0, 1.0]")
    value = float(delta_fraction)
    if not math.isfinite(value) or value <= 0.0 or value > 1.0:
        raise ValueError(
            f"delta_fraction must be in (0, 1.0]; got {delta_fraction!r}"
        )


def _sampled_specs(schema: RecipeSchema) -> tuple[Any, ...]:
    search_allowlist = schema.search_allowlist
    specs = tuple(spec for spec in search_allowlist if not schema.is_forbidden(spec.path))
    if len(specs) != len(search_allowlist):
        raise ValueError("RecipeSchema allowlist contains forbidden paths")
    return specs


def _validate_anchor(
    schema: RecipeSchema,
    anchor: RecipePatch,
    *,
    specs: tuple[Any, ...] | None = None,
) -> None:
    """Fail loudly unless the anchor pins exactly every sampled knob, in bounds.

    No silent fallback: a missing knob is NOT auto full-ranged, an
    out-of-[low, high] anchor value raises, and stray anchor paths (not in the
    sampled set) raise rather than being ignored.
    """

    if not isinstance(anchor, RecipePatch):
        raise ValueError("anchor must be a RecipePatch")
    active_specs = _sampled_specs(schema) if specs is None else specs
    expected_paths = {spec.path for spec in active_specs}
    anchor_paths = set(anchor.values)

    missing = expected_paths - anchor_paths
    if missing:
        formatted = ", ".join(sorted(".".join(path) for path in missing))
        raise ValueError(f"anchor is missing sampled knob(s): {formatted}")
    extra = anchor_paths - expected_paths
    if extra:
        formatted = ", ".join(sorted(".".join(path) for path in extra))
        raise ValueError(f"anchor has knob(s) not in the sampled set: {formatted}")

    for spec in active_specs:
        center = anchor.values[spec.path]
        if spec.kind == "categorical":
            if not spec.choices or not isinstance(center, str) or center not in spec.choices:
                raise ValueError(
                    f"{'.'.join(spec.path)} anchor value {center!r} not in choices"
                )
            continue
        if spec.kind == "int":
            if isinstance(center, bool) or not isinstance(center, int):
                raise ValueError(
                    f"{'.'.join(spec.path)} anchor value {center!r} must be int"
                )
            low, high = _numeric_bounds(spec)
            center_value = float(center)
            if center_value < low or center_value > high:
                raise ValueError(
                    f"{'.'.join(spec.path)} anchor value {center!r} outside "
                    f"bounds [{low!r}, {high!r}]"
                )
            continue
        if isinstance(center, bool) or not isinstance(center, (int, float)):
            raise ValueError(
                f"{'.'.join(spec.path)} anchor value {center!r} must be numeric"
            )
        low, high = _numeric_bounds(spec)
        center_value = float(center)
        if not math.isfinite(center_value) or center_value < low or center_value > high:
            raise ValueError(
                f"{'.'.join(spec.path)} anchor value {center!r} outside "
                f"bounds [{low!r}, {high!r}]"
            )


def _anchor_to_entries(anchor: RecipePatch | None) -> list[dict[str, Any]] | None:
    if anchor is None:
        return None
    return [
        {"path": list(path), "value": value}
        for path, value in sorted(anchor.values.items())
    ]


def _anchor_from_entries(entries: Any) -> RecipePatch | None:
    if entries is None:
        return None
    if not isinstance(entries, list):
        raise ValueError("DOE spec anchor must be null or a list of knob-path entries")
    values: dict[KeyPath, Any] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise ValueError("DOE spec anchor entries must be mappings")
        if "path" not in entry or "value" not in entry:
            raise ValueError("DOE spec anchor entries require path and value")
        raw_path = entry["path"]
        if (
            not isinstance(raw_path, list)
            or not raw_path
            or any(not isinstance(part, str) for part in raw_path)
        ):
            raise ValueError("DOE spec anchor entry path must be a non-empty string list")
        path = tuple(raw_path)
        if path in values:
            raise ValueError(f"DOE spec anchor duplicates knob path {'.'.join(path)}")
        values[path] = entry["value"]
    return RecipePatch(values)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
