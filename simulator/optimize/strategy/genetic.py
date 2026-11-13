"""Optuna NSGA-II ask/tell optimizer strategy."""

from __future__ import annotations

import importlib
import logging
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from simulator.optimize.objective import ObjectiveDefinition, objective_definitions
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.strategy.bayesian import (
    _couple_suggested_pressure_defaults,
    _constraints_for_trial,
    _objective_values_for_definitions,
    _optuna_params_from_seed,
    _suggest_value,
    _sync_conditioned_trial_params,
    _tell_optuna_strategy,
)
from simulator.optimize.strategy.protocol import Candidate, WarmStartSeed

if TYPE_CHECKING:
    from simulator.optimize.evaluate import ScoredResult

OPTUNA_NSGA2_REQUIRED_MESSAGE = (
    "optuna is required for OptunaNSGA2Strategy; install the [optimize] extra"
)
_LOGGER = logging.getLogger(__name__)


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
        warm_start_seeds: Sequence[WarmStartSeed] | None = None,
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
        self._specs = tuple(self.schema.search_allowlist)
        if any(self.schema.is_forbidden(spec.path) for spec in self._specs):
            raise ValueError("RecipeSchema search_allowlist contains forbidden paths")

        optuna = _require_optuna()
        sampler = optuna.samplers.NSGAIISampler(
            seed=self.seed,
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
                id=f"nsga2-{self.seed}-{trial.number:06d}",
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
        _tell_optuna_strategy(self, results)

    def _objective_values(self, scored: "ScoredResult") -> tuple[float, ...] | None:
        return _objective_values_for_definitions(scored, self._objective_definitions)


def _require_optuna() -> Any:
    try:
        return importlib.import_module("optuna")
    except ImportError as exc:
        raise OptunaNSGA2UnavailableError(OPTUNA_NSGA2_REQUIRED_MESSAGE) from exc
