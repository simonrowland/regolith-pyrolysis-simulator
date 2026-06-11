"""Optuna NSGA-II ask/tell optimizer strategy."""

from __future__ import annotations

import importlib
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from simulator.optimize.objective import ObjectiveDefinition, objective_definitions
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.strategy.bayesian import (
    _CANDIDATE_ID_ATTR,
    _CONSTRAINT_NAMES_ATTR,
    _CONSTRAINT_VALUES_ATTR,
    _INFEASIBLE_ATTR,
    _bad_objective_value,
    _constraint_values,
    _constraints_for_trial,
    _couple_suggested_pressure_defaults,
    _objective_mapping,
    _suggest_value,
)
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

OPTUNA_NSGA2_REQUIRED_MESSAGE = (
    "optuna is required for OptunaNSGA2Strategy; install the [optimize] extra"
)


class OptunaNSGA2UnavailableError(ImportError):
    """Raised when OptunaNSGA2Strategy is used without the optional dependency."""


class OptunaNSGA2Strategy:
    """Constrained multi-objective Optuna NSGA-II strategy over RecipeSchema knobs."""

    name = "optuna-nsga2"

    def __init__(
        self,
        schema: RecipeSchema | None = None,
        *,
        seed: int,
        objective_profile: Mapping[str, Any] | None = None,
        profile: Mapping[str, Any] | None = None,
    ) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative int")
        if profile is not None and objective_profile is not None:
            raise ValueError("pass either objective_profile or profile, not both")
        active_profile = objective_profile if objective_profile is not None else profile
        if active_profile is None:
            raise ValueError("objective_profile is required")

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
        sampler = optuna.samplers.NSGAIISampler(
            seed=self.seed,
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
                id=f"nsga2-{self.seed}-{trial.number:06d}",
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
                raise ValueError(
                    f"candidate patch does not match plan: {candidate.id!r}"
                )
            if dict(planned.metadata) != dict(candidate.metadata):
                raise ValueError(
                    f"candidate metadata does not match plan: {candidate.id!r}"
                )
            if candidate.id in seen:
                raise ValueError(
                    f"duplicate candidate_id in tell batch: {candidate.id!r}"
                )
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
        raise OptunaNSGA2UnavailableError(OPTUNA_NSGA2_REQUIRED_MESSAGE) from exc
