"""Optuna TPE ask/tell optimizer strategy."""

from __future__ import annotations

import importlib
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from simulator.optimize.objective import ObjectiveDefinition, objective_definitions
from simulator.optimize.recipe import KeyPath, KnobSpec, RecipePatch, RecipeSchema
from simulator.optimize.strategy.protocol import Candidate

if TYPE_CHECKING:
    from simulator.optimize.evaluate import ScoredResult

TellBatchRow = tuple[
    Candidate,
    "ScoredResult",
    Any,
    tuple[float, ...],
    tuple[float, ...],
    tuple[str, ...],
]

OPTUNA_REQUIRED_MESSAGE = (
    "optuna is required for OptunaTPEStrategy; install the [optimize] extra"
)
_CONSTRAINT_VALUES_ATTR = "regolith_constraint_values"
_CONSTRAINT_NAMES_ATTR = "regolith_constraint_names"
_CANDIDATE_ID_ATTR = "regolith_candidate_id"
_INFEASIBLE_ATTR = "regolith_infeasible"
_BAD_MAXIMIZE_VALUE = -1.0e30
_BAD_MINIMIZE_VALUE = 1.0e30


class OptunaUnavailableError(ImportError):
    """Raised when OptunaTPEStrategy is used without the optional dependency."""


class OptunaTPEStrategy:
    """Constrained multi-objective Optuna TPE strategy over RecipeSchema knobs."""

    name = "optuna-tpe"

    def __init__(
        self,
        schema: RecipeSchema | None = None,
        *,
        seed: int,
        objective_profile: Mapping[str, Any] | None = None,
        profile: Mapping[str, Any] | None = None,
        n_startup_trials: int = 10,
        n_ei_candidates: int = 24,
    ) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative int")
        if profile is not None and objective_profile is not None:
            raise ValueError("pass either objective_profile or profile, not both")
        active_profile = objective_profile if objective_profile is not None else profile
        if active_profile is None:
            raise ValueError("objective_profile is required")
        _validate_non_negative_int("n_startup_trials", n_startup_trials)
        _validate_positive_int("n_ei_candidates", n_ei_candidates)

        self.schema = schema or RecipeSchema()
        self._seed = seed
        self.objective_profile = MappingProxyType(dict(active_profile))
        self._objective_definitions = objective_definitions(self.objective_profile)
        self._directions = tuple(
            definition.sense for definition in self._objective_definitions
        )
        self._specs = tuple(self.schema.allowlist)
        if any(self.schema.is_forbidden(spec.path) for spec in self._specs):
            raise ValueError("RecipeSchema allowlist contains forbidden paths")

        optuna = _require_optuna()
        sampler = optuna.samplers.TPESampler(
            seed=self.seed,
            multivariate=True,
            n_startup_trials=n_startup_trials,
            n_ei_candidates=n_ei_candidates,
            constraints_func=_constraints_for_trial,
        )
        self._study = optuna.create_study(
            directions=self._directions,
            sampler=sampler,
        )
        self._tell_count = 0
        self._planned_by_id: dict[str, Candidate] = {}
        self._trial_by_candidate_id: dict[str, Any] = {}
        self._result_by_id: dict[str, ScoredResult] = {}
        self._results: list[tuple[Candidate, ScoredResult]] = []

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def objective_definitions(self) -> tuple[ObjectiveDefinition, ...]:
        return self._objective_definitions

    @property
    def objective_metrics(self) -> tuple[str, ...]:
        return tuple(definition.metric for definition in self._objective_definitions)

    @property
    def directions(self) -> tuple[str, ...]:
        return self._directions

    @property
    def study(self) -> Any:
        return self._study

    @property
    def best_trials(self) -> tuple[Any, ...]:
        return tuple(self._study.best_trials)

    @property
    def pareto_front(self) -> tuple[Any, ...]:
        return self.best_trials

    @property
    def tell_count(self) -> int:
        return self._tell_count

    @property
    def results(self) -> tuple[tuple[Candidate, "ScoredResult"], ...]:
        return tuple(self._results)

    def ask(self, n: int) -> list[Candidate]:
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError("n must be a non-negative int")
        if n == 0:
            return []

        candidates: list[Candidate] = []
        for _ in range(n):
            trial = self._study.ask()
            values = {
                spec.path: _suggest_value(trial, spec)
                for spec in self._specs
                if not self.schema.is_forbidden(spec.path)
            }
            _couple_suggested_pressure_defaults(self.schema, values)
            patch = RecipePatch(values).validated(self.schema)
            candidate = Candidate(
                id=f"tpe-{self.seed}-{trial.number:06d}",
                patch=patch,
                metadata={
                    "strategy": self.name,
                    "seed": self.seed,
                    "trial_number": trial.number,
                    "objective_metrics": self.objective_metrics,
                    "directions": self.directions,
                },
            )
            self._planned_by_id[candidate.id] = candidate
            self._trial_by_candidate_id[candidate.id] = trial
            candidates.append(candidate)
        return candidates

    def tell(self, results: Sequence[tuple[Candidate, "ScoredResult"]]) -> None:
        batch: list[TellBatchRow] = []
        seen: set[str] = set()
        recorded = set(self._result_by_id)
        scored_result_type: Any | None = None

        for pair in results:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError(
                    "tell results must contain (Candidate, ScoredResult) 2-tuples"
                )
            candidate, scored = pair
            if not isinstance(candidate, Candidate):
                raise ValueError("tell result candidate must be a Candidate")
            if scored_result_type is None:
                from simulator.optimize.evaluate import ScoredResult as _ScoredResult

                scored_result_type = _ScoredResult
            if not isinstance(scored, scored_result_type):
                raise ValueError("tell result scored value must be a ScoredResult")
            scored_candidate_id = getattr(scored, "candidate_id", None)
            if scored_candidate_id != candidate.id:
                raise ValueError(
                    "ScoredResult.candidate_id must match Candidate.id "
                    f"({scored_candidate_id!r} != {candidate.id!r})"
                )
            if candidate.id not in self._planned_by_id:
                raise ValueError(f"candidate_id was not planned: {candidate.id!r}")
            planned = self._planned_by_id[candidate.id]
            if planned.patch.canonical_json() != candidate.patch.canonical_json():
                raise ValueError(f"candidate patch does not match plan: {candidate.id!r}")
            if dict(planned.metadata) != dict(candidate.metadata):
                raise ValueError(f"candidate metadata does not match plan: {candidate.id!r}")
            if candidate.id in seen:
                raise ValueError(f"duplicate candidate_id in tell batch: {candidate.id!r}")
            if candidate.id in recorded:
                raise ValueError(f"candidate_id already recorded: {candidate.id!r}")
            trial = self._trial_by_candidate_id.get(candidate.id)
            if trial is None:
                raise ValueError(f"candidate trial is unknown: {candidate.id!r}")
            objective_values = self._objective_values(scored)
            constraint_names, constraint_values = _constraint_values(scored)
            seen.add(candidate.id)
            batch.append(
                (
                    candidate,
                    scored,
                    trial,
                    objective_values,
                    constraint_values,
                    constraint_names,
                )
            )

        for candidate, scored, trial, values, constraints, constraint_names in batch:
            trial.set_user_attr(_CANDIDATE_ID_ATTR, candidate.id)
            trial.set_user_attr(_CONSTRAINT_NAMES_ATTR, constraint_names)
            trial.set_user_attr(_CONSTRAINT_VALUES_ATTR, constraints)
            trial.set_user_attr(
                _INFEASIBLE_ATTR,
                not bool(getattr(scored, "feasible", False)),
            )
            self._study.tell(trial, values=values)
            self._result_by_id[candidate.id] = scored
            self._results.append((candidate, scored))
            self._tell_count += 1

    def _objective_values(self, scored: "ScoredResult") -> tuple[float, ...]:
        if (
            not bool(getattr(scored, "feasible", False))
            or getattr(scored, "objectives", None) is None
        ):
            return tuple(
                _bad_objective_value(definition)
                for definition in self._objective_definitions
            )

        mapping = _objective_mapping(scored)
        values: list[float] = []
        for definition in self._objective_definitions:
            if definition.metric not in mapping:
                raise ValueError(f"objective {definition.metric!r} is missing")
            values.append(mapping[definition.metric])
        return tuple(values)


def _require_optuna() -> Any:
    try:
        return importlib.import_module("optuna")
    except ImportError as exc:
        raise OptunaUnavailableError(OPTUNA_REQUIRED_MESSAGE) from exc


def _suggest_value(trial: Any, spec: KnobSpec) -> Any:
    name = ".".join(spec.path)
    if spec.kind == "categorical":
        if not spec.choices:
            raise ValueError(f"{name} categorical knob has no choices")
        return trial.suggest_categorical(name, tuple(spec.choices))

    low, high = _numeric_bounds(spec)
    log = _log_scale(spec)
    if log and low <= 0:
        raise ValueError(f"{name} log-scale knob requires low > 0")
    if spec.kind == "int":
        return int(trial.suggest_int(name, int(low), int(high), log=log))
    if spec.kind == "float":
        return float(trial.suggest_float(name, low, high, log=log))
    raise ValueError(f"{name} has unsupported knob kind {spec.kind!r}")


def _couple_suggested_pressure_defaults(
    schema: RecipeSchema,
    values: dict[KeyPath, Any],
) -> None:
    for po2_path, total_path in schema.PRESSURE_COUPLED_DEFAULT_PAIRS:
        if po2_path not in values or total_path not in values:
            continue
        po2_spec = schema.spec_for(po2_path)
        po2_low, po2_high = _numeric_bounds(po2_spec)
        total = float(values[total_path])
        feasible_high = min(po2_high, total)
        tolerance = max(1e-12, 1e-12 * max(1.0, abs(po2_low), abs(total)))
        if feasible_high + tolerance < po2_low:
            raise ValueError(
                "pressure_default_pair_infeasible_bounds: "
                f"{'.'.join(po2_path)} low {po2_low:.12g} exceeds "
                f"{'.'.join(total_path)} {total:.12g}"
            )
        unit = (float(values[po2_path]) - po2_low) / (po2_high - po2_low)
        values[po2_path] = float(po2_low + unit * (feasible_high - po2_low))


def _numeric_bounds(spec: KnobSpec) -> tuple[float, float]:
    name = ".".join(spec.path)
    if spec.low is None or spec.high is None:
        raise ValueError(f"{name} numeric knob lacks bounds")
    low = float(spec.low)
    high = float(spec.high)
    if not math.isfinite(low) or not math.isfinite(high) or low > high:
        raise ValueError(f"{name} numeric knob has invalid bounds")
    return low, high


def _log_scale(spec: KnobSpec) -> bool:
    return bool(getattr(spec, "log", False) or getattr(spec, "log_scale", False))


def _objective_mapping(scored: "ScoredResult") -> Mapping[str, float]:
    objectives = getattr(scored, "objectives", None)
    if objectives is None:
        raise ValueError("feasible result requires objectives")
    as_mapping = getattr(objectives, "as_mapping", None)
    if as_mapping is None:
        raise ValueError("objective mapping accessor is missing")
    try:
        raw = as_mapping()
    except Exception as exc:
        raise ValueError("objective mapping accessor failed") from exc
    mapping: dict[str, float] = {}
    for metric, value in raw.items():
        metric_name = str(metric)
        try:
            numeric_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"objective {metric_name!r} is not numeric") from exc
        if not math.isfinite(numeric_value):
            raise ValueError(f"objective {metric_name!r} is not finite")
        mapping[metric_name] = numeric_value
    return mapping


def _constraint_values(scored: "ScoredResult") -> tuple[tuple[str, ...], tuple[float, ...]]:
    margins = getattr(scored, "feasibility_margins", {}) or {}
    names: list[str] = []
    values: list[float] = []
    if isinstance(margins, Mapping):
        for name in sorted(margins):
            raw_margin = margins[name]
            margin = getattr(raw_margin, "margin", raw_margin)
            try:
                numeric_margin = float(margin)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"constraint margin {name!r} is not numeric") from exc
            has_feasible_flag = hasattr(raw_margin, "feasible")
            if not has_feasible_flag and not math.isfinite(numeric_margin):
                raise ValueError(f"constraint margin {name!r} is not finite")
            names.append(str(name))
            if has_feasible_flag:
                if bool(getattr(raw_margin, "feasible")):
                    if not math.isfinite(numeric_margin):
                        raise ValueError(f"constraint margin {name!r} is not finite")
                    values.append(0.0)
                elif math.isfinite(numeric_margin):
                    values.append(-numeric_margin if numeric_margin < 0.0 else 1.0)
                else:
                    values.append(1.0)
            else:
                values.append(max(0.0, -numeric_margin))

    feasible = bool(getattr(scored, "feasible", False))
    if not values:
        return ("feasible",), (0.0,) if feasible else (1.0,)
    if not feasible and not any(value > 0.0 for value in values):
        names.append("infeasible")
        values.append(1.0)
    return tuple(names), tuple(values)


def _constraints_for_trial(trial: Any) -> tuple[float, ...]:
    if _CONSTRAINT_VALUES_ATTR not in trial.user_attrs:
        return (0.0,)
    raw = trial.user_attrs[_CONSTRAINT_VALUES_ATTR]
    try:
        values = tuple(float(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("trial constraint values are malformed") from exc
    if not values:
        raise ValueError("trial constraint values are empty")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("trial constraint values must be finite")
    return values


def _bad_objective_value(definition: ObjectiveDefinition) -> float:
    if definition.sense == "maximize":
        return _BAD_MAXIMIZE_VALUE
    if definition.sense == "minimize":
        return _BAD_MINIMIZE_VALUE
    raise ValueError(f"unsupported objective direction {definition.sense!r}")


def _validate_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _validate_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int")
