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

SCIPY_SOBOL_SAMPLER = "scipy-sobol"
DEPENDENCY_FREE_LHC_SAMPLER = "dependency-free-lhc"

FIDELITY_CORRELATION_METRICS: tuple[str, ...] = (
    "spearman_rank_correlation",
    "feasible_infeasible_agreement",
    "top_k_recall",
)


@dataclass(frozen=True)
class DoeSpec:
    """Deterministic DOE description for design P0 fidelity-correlation runs."""

    schema: RecipeSchema = field(default_factory=RecipeSchema, repr=False, compare=False)
    n_samples: int = 64
    seed: int = 0
    sampler_name: str = field(default_factory=lambda: active_sampler_name())

    def __post_init__(self) -> None:
        _validate_positive_int("n_samples", self.n_samples)
        _validate_seed(self.seed)

    @property
    def recipe_schema_version(self) -> str:
        return self.schema.recipe_schema_version

    @property
    def allowlist_version(self) -> str:
        return self.schema.allowlist_version

    @property
    def knob_paths(self) -> tuple[KeyPath, ...]:
        return tuple(spec.path for spec in self.schema.allowlist)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "seed": self.seed,
            "sampler_name": self.sampler_name,
            "recipe_schema_version": self.recipe_schema_version,
            "allowlist_version": self.allowlist_version,
            "knob_paths": [list(path) for path in self.knob_paths],
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, schema: RecipeSchema | None = None
    ) -> "DoeSpec":
        active_schema = schema or RecipeSchema()
        spec = cls(
            schema=active_schema,
            n_samples=int(payload["n_samples"]),
            seed=int(payload["seed"]),
            sampler_name=str(payload["sampler_name"]),
        )
        expected_paths = tuple(tuple(path) for path in payload.get("knob_paths", ()))
        if expected_paths and expected_paths != spec.knob_paths:
            raise ValueError("DOE spec knob paths do not match the active schema")
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

    def __post_init__(self) -> None:
        if self.n_samples_compared < 0:
            raise ValueError("n_samples_compared must be non-negative")
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
        )


def sample_recipe_patches(
    schema: RecipeSchema | None = None,
    *,
    n_samples: int,
    seed: int,
    sampler_name: str | None = None,
) -> tuple[RecipePatch, ...]:
    """Sample validated RecipePatch objects across the schema allowlist."""

    active_schema = schema or RecipeSchema()
    _validate_positive_int("n_samples", n_samples)
    _validate_seed(seed)
    active_sampler = sampler_name or active_sampler_name()

    specs = tuple(
        spec for spec in active_schema.allowlist if not active_schema.is_forbidden(spec.path)
    )
    if len(specs) != len(active_schema.allowlist):
        raise ValueError("RecipeSchema allowlist contains forbidden paths")
    if not specs:
        return tuple(RecipePatch({}).validated(active_schema) for _ in range(n_samples))

    points = _unit_hypercube_points(len(specs), n_samples, seed, active_sampler)
    patches: list[RecipePatch] = []
    for row in points:
        values = {
            spec.path: _map_unit_value(spec, unit_value)
            for spec, unit_value in zip(specs, row, strict=True)
        }
        patches.append(RecipePatch(values).validated(active_schema))
    return tuple(patches)


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


def _numeric_bounds(spec: Any) -> tuple[float, float]:
    if spec.low is None or spec.high is None:
        raise ValueError(f"{'.'.join(spec.path)} numeric knob lacks bounds")
    low = float(spec.low)
    high = float(spec.high)
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise ValueError(f"{'.'.join(spec.path)} has invalid numeric bounds")
    return low, high


def _validate_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int")


def _validate_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative int")


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
