"""Optuna TPE ask/tell optimizer strategy."""

from __future__ import annotations

import importlib
import logging
import math
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from simulator.optimize.doe import _condition_pressure_pair_values
from simulator.optimize.objective import (
    ObjectiveDefinition,
    canonical_objective_mapping,
    objective_definitions,
)
from simulator.optimize.recipe import KeyPath, KnobSpec, RecipePatch, RecipeSchema
from simulator.optimize.strategy.protocol import Candidate, WarmStartSeed

if TYPE_CHECKING:
    from simulator.optimize.evaluate import ScoredResult

TellBatchRow = tuple[
    Candidate,
    "ScoredResult",
    Any,
    tuple[float, ...] | None,
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
_UNSCOREABLE_OBJECTIVES_ATTR = "regolith_unscoreable_objectives"
_BAD_MAXIMIZE_VALUE = -1.0e30
_BAD_MINIMIZE_VALUE = 1.0e30
_NONFINITE_INFEASIBLE_CONSTRAINT_VIOLATION = 1.0e30
_LOGGER = logging.getLogger(__name__)


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
        warm_start_seeds: Sequence[WarmStartSeed] | None = None,
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
        self._specs = tuple(self.schema.search_allowlist)
        if any(self.schema.is_forbidden(spec.path) for spec in self._specs):
            raise ValueError("RecipeSchema search_allowlist contains forbidden paths")

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
        enqueued_seeds: list[WarmStartSeed] = []
        rejected_seed_ids: list[str] = []
        for seed_candidate in tuple(warm_start_seeds or ()):
            try:
                params = _optuna_params_from_seed(seed_candidate, self._specs, self.schema)
            except ValueError as exc:
                _LOGGER.warning(
                    "optuna_warm_start_seed_dropped seed_id=%s reason=%s",
                    seed_candidate.id,
                    exc,
                )
                rejected_seed_ids.append(seed_candidate.id)
                continue
            self._study.enqueue_trial(params)
            enqueued_seeds.append(seed_candidate)
        self._warm_start_seeds = tuple(enqueued_seeds)
        self._warm_start_rejected_seed_ids = tuple(rejected_seed_ids)
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
    def warm_start_rejected_seed_count(self) -> int:
        return len(self._warm_start_rejected_seed_ids)

    @property
    def warm_start_rejected_seed_ids(self) -> tuple[str, ...]:
        return self._warm_start_rejected_seed_ids

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
            enqueued_seed = (
                self._warm_start_seeds[trial.number]
                if trial.number < len(self._warm_start_seeds)
                else None
            )
            values = {
                spec.path: _suggest_value(trial, spec)
                for spec in self._specs
                if not self.schema.is_forbidden(spec.path)
            }
            raw_values = dict(values)
            _couple_suggested_pressure_defaults(self.schema, values)
            _sync_conditioned_trial_params(trial, values, raw_values)
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
                    "proposal_source": (
                        "optuna_enqueued" if enqueued_seed is not None else "optuna_model"
                    ),
                    "seed_lineage": enqueued_seed is not None,
                    **(
                        {
                            "seed_id": enqueued_seed.id,
                            "seed_origin": enqueued_seed.origin,
                        }
                        if enqueued_seed is not None
                        else {}
                    ),
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
            if values is None:
                trial.set_user_attr(_UNSCOREABLE_OBJECTIVES_ATTR, True)
                self._study.tell(trial, state=_failed_trial_state())
            else:
                self._study.tell(trial, values=values)
            self._result_by_id[candidate.id] = scored
            self._results.append((candidate, scored))
            self._tell_count += 1

    def _objective_values(self, scored: "ScoredResult") -> tuple[float, ...] | None:
        return _objective_values_for_definitions(scored, self._objective_definitions)


def _optuna_params_from_seed(
    seed: WarmStartSeed,
    specs: Sequence[KnobSpec],
    schema: RecipeSchema,
) -> dict[str, Any]:
    patch = seed.patch.validated(schema)
    missing = [spec.path for spec in specs if spec.path not in patch.values]
    if missing:
        missing_names = ", ".join(".".join(path) for path in missing[:5])
        if len(missing) > 5:
            missing_names += ", ..."
        raise ValueError(
            f"warm-start seed {seed.id!r} is incomplete for Optuna enqueue: "
            f"{missing_names}"
        )
    return {
        ".".join(spec.path): patch.values[spec.path]
        for spec in specs
        if not schema.is_forbidden(spec.path)
    }


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
    _condition_pressure_pair_values(schema, tuple(schema.search_allowlist), values)


def _sync_conditioned_trial_params(
    trial: Any,
    values: Mapping[KeyPath, Any],
    raw_values: Mapping[KeyPath, Any],
) -> None:
    distributions = getattr(trial, "distributions", {})
    for path, value in values.items():
        if raw_values.get(path) == value:
            continue
        name = ".".join(path)
        if name not in distributions:
            continue
        distribution = distributions[name]
        try:
            internal_value = distribution.to_internal_repr(value)
        except Exception as exc:
            raise ValueError(f"conditioned trial param {name!r} is invalid") from exc
        # Optuna's public Trial.params is read-only after suggest(); update the
        # running trial storage so future sampler observations use evaluated coords.
        trial.storage.set_trial_param(trial._trial_id, name, internal_value, distribution)
    if hasattr(trial, "_cached_frozen_trial"):
        trial._cached_frozen_trial = trial.storage.get_trial(trial._trial_id)


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


def _objective_mapping(scored: "ScoredResult") -> Mapping[str, float | None]:
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
    mapping: dict[str, float | None] = {}
    for metric, value in raw.items():
        metric_name = str(metric)
        if value is None:
            mapping[metric_name] = None
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"objective {metric_name!r} is not numeric") from exc
        if not math.isfinite(numeric_value):
            raise ValueError(f"objective {metric_name!r} is not finite")
        mapping[metric_name] = numeric_value
    return canonical_objective_mapping(mapping)


def _objective_values_for_definitions(
    scored: "ScoredResult",
    definitions: Sequence[ObjectiveDefinition],
) -> tuple[float, ...] | None:
    if not bool(getattr(scored, "feasible", False)):
        return tuple(_bad_objective_value(definition) for definition in definitions)
    if getattr(scored, "objectives", None) is None:
        return None

    mapping = _objective_mapping(scored)
    values: list[float] = []
    for definition in definitions:
        if definition.metric not in mapping:
            return None
        value = mapping[definition.metric]
        if value is None:
            return None
        values.append(value)
    return tuple(values)


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
            if math.isnan(numeric_margin):
                raise ValueError(f"constraint margin {name!r} is not finite")
            names.append(str(name))
            status_payload = getattr(raw_margin, "status_payload", {}) or {}
            continuous = (
                isinstance(status_payload, Mapping)
                and status_payload.get("constraint_mode") == "continuous"
            )
            if continuous:
                values.append(
                    _constraint_violation_from_margin(
                        numeric_margin,
                        infeasible_flag=False,
                    )
                )
            elif has_feasible_flag:
                if bool(getattr(raw_margin, "feasible")):
                    values.append(0.0)
                else:
                    values.append(
                        _constraint_violation_from_margin(
                            numeric_margin,
                            infeasible_flag=True,
                        )
                    )
            else:
                values.append(
                    _constraint_violation_from_margin(
                        numeric_margin,
                        infeasible_flag=False,
                    )
                )

    feasible = bool(getattr(scored, "feasible", False))
    if not values:
        return ("feasible",), (0.0,) if feasible else (1.0,)
    if not feasible and not any(value > 0.0 for value in values):
        names.append("infeasible")
        values.append(1.0)
    return tuple(names), tuple(values)


def _constraint_violation_from_margin(
    margin: float,
    *,
    infeasible_flag: bool,
) -> float:
    if math.isinf(margin):
        if margin > 0.0 and not infeasible_flag:
            return 0.0
        return _NONFINITE_INFEASIBLE_CONSTRAINT_VIOLATION
    if margin < 0.0:
        return -margin
    return 1.0 if infeasible_flag else 0.0


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


def _failed_trial_state() -> Any:
    return _require_optuna().trial.TrialState.FAIL


def _validate_non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _validate_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive int")
