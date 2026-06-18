"""Group-wise Morris elementary-effects screening strategy.

Recipe allowlist paths are collapsed into physics-coupled groups before the
Morris design is built:

* ``thermo``: temperature, oxygen-pressure, total-pressure, and hold-T knobs.
* ``schedule``: ramp-rate, dwell-duration, and endpoint-hold knobs.
* ``chemistry``: alkali shuttle, bakeout-pO2, recovery-duration, and optional
  harvest knobs.
* ``residual``: any allowlist path not matched above; never dropped.

Safety/constraint knobs are paths whose leaf is a default fallback
(``*_default`` or ``default_*``) or that sit under an ``endpoint`` branch. The
schema forbids direct safety-ceiling mutation, so these optimizer-facing
fallback/endpoint knobs are the screen's never-prune constraint surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from simulator.optimize.doe import _map_unit_row, _map_unit_value
from simulator.optimize.recipe import KeyPath, KnobSpec, RecipePatch, RecipeSchema
from simulator.optimize.strategy.protocol import Candidate

if TYPE_CHECKING:
    from simulator.optimize.evaluate import ScoredResult


_GROUP_ORDER = ("thermo", "schedule", "chemistry")


@dataclass(frozen=True)
class MorrisGroup:
    name: str
    paths: tuple[KeyPath, ...]
    never_prune: bool = False


@dataclass(frozen=True)
class ObjectiveImportance:
    mu_star: float
    sigma: float
    effects: tuple[float, ...]


@dataclass(frozen=True)
class GroupImportance:
    name: str
    paths: tuple[KeyPath, ...]
    never_prune: bool
    objectives: Mapping[str, ObjectiveImportance]
    aggregate_mu_star: float
    aggregate_sigma: float
    recommendation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "objectives", MappingProxyType(dict(self.objectives)))


@dataclass(frozen=True)
class MorrisScreenResult:
    groups: tuple[GroupImportance, ...]
    completed_trajectories: int
    prune_threshold: float


class MorrisScreenStrategy:
    """Deterministic group-wise Morris screen over RecipeSchema knobs."""

    name = "morris-screen"

    def __init__(
        self,
        schema: RecipeSchema | None = None,
        *,
        seed: int,
        num_trajectories: int = 4,
        num_levels: int = 4,
        prune_threshold: float = 0.0,
    ) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative int")
        if (
            isinstance(num_trajectories, bool)
            or not isinstance(num_trajectories, int)
            or num_trajectories <= 0
        ):
            raise ValueError("num_trajectories must be a positive int")
        if (
            isinstance(num_levels, bool)
            or not isinstance(num_levels, int)
            or num_levels < 2
            or num_levels % 2 != 0
        ):
            raise ValueError("num_levels must be an even int >= 2")
        if not math.isfinite(float(prune_threshold)) or prune_threshold < 0:
            raise ValueError("prune_threshold must be a non-negative finite float")

        self.schema = schema or RecipeSchema()
        self._seed = seed
        self.num_trajectories = num_trajectories
        self.num_levels = num_levels
        self.prune_threshold = float(prune_threshold)
        self._specs = tuple(self.schema.search_allowlist)
        if any(self.schema.is_forbidden(spec.path) for spec in self._specs):
            raise ValueError("RecipeSchema search_allowlist contains forbidden paths")
        self._spec_by_path = {spec.path: spec for spec in self._specs}
        self._groups = _build_groups(self._specs)
        self._plan = self._build_plan()
        self._planned_by_id = {candidate.id: candidate for candidate in self._plan}
        self._cursor = 0
        self._tell_count = 0
        self._results: list[tuple[Candidate, ScoredResult]] = []
        self._result_by_id: dict[str, ScoredResult] = {}
        self._effects: dict[tuple[int, str, str], float] = {}
        self._computed_trajectories: set[int] = set()

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def groups(self) -> tuple[MorrisGroup, ...]:
        return self._groups

    @property
    def plan_length(self) -> int:
        return len(self._plan)

    @property
    def tell_count(self) -> int:
        return self._tell_count

    @property
    def results(self) -> tuple[tuple[Candidate, "ScoredResult"], ...]:
        return tuple(self._results)

    def ask(self, n: int) -> list[Candidate]:
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError("n must be a non-negative int")
        if n == 0 or self._cursor >= len(self._plan):
            return []
        end = min(self._cursor + n, len(self._plan))
        candidates = list(self._plan[self._cursor : end])
        self._cursor = end
        return candidates

    def tell(self, results: Sequence[tuple[Candidate, "ScoredResult"]]) -> None:
        batch: list[tuple[Candidate, "ScoredResult"]] = []
        seen: set[str] = set()
        recorded = set(self._result_by_id)

        for pair in results:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError(
                    "tell results must contain (Candidate, ScoredResult) 2-tuples"
                )
            candidate, scored = pair
            if not isinstance(candidate, Candidate):
                raise ValueError("tell result candidate must be a Candidate")
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
            seen.add(candidate.id)
            batch.append((candidate, scored))

        proposed_result_by_id = dict(self._result_by_id)
        for candidate, scored in batch:
            proposed_result_by_id[candidate.id] = scored
        proposed_effects = dict(self._effects)
        proposed_computed = set(self._computed_trajectories)
        for trajectory in sorted(
            {
                int(self._planned_by_id[candidate.id].metadata["trajectory"])
                for candidate, _ in batch
            }
        ):
            if trajectory in proposed_computed:
                continue
            effects = self._trajectory_effects(trajectory, proposed_result_by_id)
            if effects is None:
                continue
            proposed_effects.update(effects)
            proposed_computed.add(trajectory)

        self._results.extend(batch)
        self._tell_count += len(batch)
        self._result_by_id = proposed_result_by_id
        self._effects = proposed_effects
        self._computed_trajectories = proposed_computed

    def screen_result(self) -> MorrisScreenResult:
        if len(self._computed_trajectories) != self.num_trajectories:
            raise ValueError("Morris screen requires all trajectories to be complete")
        if not self._effects:
            raise ValueError("Morris screen has no objective effects")

        rows: list[GroupImportance] = []
        metric_names = sorted({metric for _, _, metric in self._effects})
        if not metric_names:
            raise ValueError("Morris screen has no objective metrics")
        objective_by_group: dict[str, dict[str, ObjectiveImportance]] = {}
        for group in self._groups:
            objective_rows: dict[str, ObjectiveImportance] = {}
            for metric in metric_names:
                values = tuple(
                    self._effects[(trajectory, group.name, metric)]
                    for trajectory in sorted(self._computed_trajectories)
                    if (trajectory, group.name, metric) in self._effects
                )
                if len(values) != self.num_trajectories:
                    raise ValueError(
                        "Morris screen has incomplete objective effects "
                        f"for group={group.name!r} metric={metric!r}"
                    )
                mu_star = _mean(abs(value) for value in values)
                sigma = _sigma(values)
                objective_rows[metric] = ObjectiveImportance(mu_star, sigma, values)
            objective_by_group[group.name] = objective_rows

        if len(metric_names) == 1:
            metric = metric_names[0]
            aggregate_mu_by_group = {
                group_name: objective_rows[metric].mu_star
                for group_name, objective_rows in objective_by_group.items()
            }
            aggregate_sigma_by_group = {
                group_name: objective_rows[metric].sigma
                for group_name, objective_rows in objective_by_group.items()
            }
        else:
            aggregate_mu_by_group = _rank_scores(objective_by_group, "mu_star")
            aggregate_sigma_by_group = _rank_scores(objective_by_group, "sigma")
        for group in self._groups:
            objective_rows = objective_by_group[group.name]
            aggregate_mu_star = aggregate_mu_by_group[group.name]
            aggregate_sigma = aggregate_sigma_by_group[group.name]
            keep = group.never_prune or any(
                row.mu_star > self.prune_threshold for row in objective_rows.values()
            )
            rows.append(
                GroupImportance(
                    name=group.name,
                    paths=group.paths,
                    never_prune=group.never_prune,
                    objectives=objective_rows,
                    aggregate_mu_star=aggregate_mu_star,
                    aggregate_sigma=aggregate_sigma,
                    recommendation="keep" if keep else "prune",
                )
            )

        return MorrisScreenResult(
            groups=tuple(
                sorted(rows, key=lambda row: (-row.aggregate_mu_star, row.name))
            ),
            completed_trajectories=len(self._computed_trajectories),
            prune_threshold=self.prune_threshold,
        )

    def _build_plan(self) -> tuple[Candidate, ...]:
        plan: list[Candidate] = []
        group_names = tuple(group.name for group in self._groups)
        step_size = 1.0 / (self.num_levels - 1)
        delta = _morris_delta(self.num_levels)

        for trajectory in range(self.num_trajectories):
            rng = random.Random(_trajectory_seed(self.seed, trajectory))
            group_units: dict[str, float] = {}
            deltas: dict[str, float] = {}
            for group_name in group_names:
                direction = 1 if rng.randrange(2) == 0 else -1
                low_index = 0 if direction > 0 else self.num_levels // 2
                high_index = (
                    (self.num_levels - 2) // 2
                    if direction > 0
                    else self.num_levels - 1
                )
                level_index = rng.randint(low_index, high_index)
                group_units[group_name] = level_index * step_size
                deltas[group_name] = direction * delta

            order = list(group_names)
            rng.shuffle(order)
            current = dict(group_units)
            plan.append(self._candidate(trajectory, 0, current, None, 0.0))
            for step, group_name in enumerate(order, start=1):
                current = dict(current)
                current[group_name] += deltas[group_name]
                plan.append(
                    self._candidate(
                        trajectory, step, current, group_name, deltas[group_name]
                    )
                )
        return tuple(plan)

    def _candidate(
        self,
        trajectory: int,
        step: int,
        group_units: Mapping[str, float],
        moved_group: str | None,
        group_delta: float,
    ) -> Candidate:
        row = tuple(group_units[_group_for_path(spec.path)] for spec in self._specs)
        values = _map_unit_row(self.schema, self._specs, row, _map_unit_value)
        patch = RecipePatch(values).validated(self.schema)
        return Candidate(
            id=f"{self.name}-{self.seed}-t{trajectory:04d}-s{step:03d}",
            patch=patch,
            metadata={
                "strategy": self.name,
                "seed": self.seed,
                "trajectory": trajectory,
                "step": step,
                "moved_group": moved_group,
                "group_delta": group_delta,
                "group_units": dict(sorted(group_units.items())),
            },
        )

    def _trajectory_effects(
        self,
        trajectory: int,
        result_by_id: Mapping[str, "ScoredResult"],
    ) -> dict[tuple[int, str, str], float] | None:
        ids = [
            f"{self.name}-{self.seed}-t{trajectory:04d}-s{step:03d}"
            for step in range(len(self._groups) + 1)
        ]
        if any(candidate_id not in result_by_id for candidate_id in ids):
            return None
        mappings = [_objective_mapping(result_by_id[candidate_id]) for candidate_id in ids]
        if any(mapping is None for mapping in mappings):
            return None

        first = mappings[0] or {}
        metric_names = set(first)
        if not metric_names:
            raise ValueError("Morris trajectory has no objective metrics")
        for step, mapping in enumerate(mappings[1:], start=1):
            current_names = set(mapping or {})
            if current_names != metric_names:
                raise ValueError(
                    "Morris trajectory objective metrics must match at every step "
                    f"for trajectory={trajectory}: step 0 has {sorted(metric_names)!r}, "
                    f"step {step} has {sorted(current_names)!r}"
                )

        effects: dict[tuple[int, str, str], float] = {}
        for step in range(1, len(ids)):
            candidate = self._planned_by_id[ids[step]]
            group_name = candidate.metadata["moved_group"]
            delta = float(candidate.metadata["group_delta"])
            previous = mappings[step - 1] or {}
            current = mappings[step] or {}
            for metric in sorted(metric_names):
                effect = (current[metric] - previous[metric]) / delta
                effects[(trajectory, str(group_name), metric)] = effect
        return effects


def _build_groups(specs: Sequence[KnobSpec]) -> tuple[MorrisGroup, ...]:
    by_group: dict[str, list[KeyPath]] = {name: [] for name in _GROUP_ORDER}
    by_group["residual"] = []
    for spec in specs:
        by_group[_group_for_path(spec.path)].append(spec.path)

    groups = tuple(
        MorrisGroup(
            name=name,
            paths=tuple(paths),
            never_prune=any(_is_never_prune_path(path) for path in paths),
        )
        for name, paths in by_group.items()
        if paths
    )
    _assert_partition(tuple(spec.path for spec in specs), groups)
    return groups


def _group_for_path(path: KeyPath) -> str:
    dotted = ".".join(path)
    leaf = path[-1]
    if (
        "na_shuttle_stage" in path
        or "pO2_bakeout_mbar" in leaf
        or leaf in {"duration_after_pathA_h", "duration_after_pathB_h_per_phase"}
        or "optional_Ca_harvest" in path
    ):
        return "chemistry"
    if (
        "dT_dt" in dotted
        or "ramp_rate" in leaf
        or leaf == "duration_h"
        or "endpoint" in path
        or "hold_time" in leaf
    ):
        return "schedule"
    if (
        leaf == "temp_range_C"
        or leaf == "default_hold_T_C"
        or "pO2_" in leaf
        or "p_total_" in leaf
    ):
        return "thermo"
    return "residual"


def _is_never_prune_path(path: KeyPath) -> bool:
    leaf = path[-1]
    return leaf.endswith("_default") or leaf.startswith("default_") or "endpoint" in path


def _assert_partition(paths: tuple[KeyPath, ...], groups: tuple[MorrisGroup, ...]) -> None:
    allowlist = set(paths)
    seen: set[KeyPath] = set()
    for group in groups:
        overlap = seen & set(group.paths)
        if overlap:
            raise AssertionError(f"Morris groups overlap: {sorted(overlap)!r}")
        seen.update(group.paths)
    if seen != allowlist:
        missing = sorted(allowlist - seen)
        extra = sorted(seen - allowlist)
        raise AssertionError(f"Morris groups must partition allowlist: {missing=} {extra=}")


def _trajectory_seed(seed: int, trajectory: int) -> int:
    digest = hashlib.sha256(f"{seed}:{trajectory}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _morris_delta(num_levels: int) -> float:
    return num_levels / (2.0 * (num_levels - 1))


def _rank_scores(
    objective_by_group: Mapping[str, Mapping[str, ObjectiveImportance]],
    attribute: str,
) -> dict[str, float]:
    metric_names = sorted(
        {metric for objective_rows in objective_by_group.values() for metric in objective_rows}
    )
    scores = {group_name: [] for group_name in objective_by_group}
    for metric in metric_names:
        values = {
            group_name: float(getattr(objective_rows[metric], attribute))
            for group_name, objective_rows in objective_by_group.items()
        }
        distinct = sorted(set(values.values()))
        if distinct == [0.0]:
            for group_name in scores:
                scores[group_name].append(0.0)
            continue
        if len(distinct) == 1:
            for group_name in scores:
                scores[group_name].append(1.0)
            continue
        denom = len(distinct) - 1
        rank_by_value = {value: index / denom for index, value in enumerate(distinct)}
        for group_name, value in values.items():
            scores[group_name].append(rank_by_value[value])
    return {group_name: _mean(group_scores) for group_name, group_scores in scores.items()}


def _objective_mapping(scored: "ScoredResult") -> Mapping[str, float] | None:
    objectives = getattr(scored, "objectives", None)
    if objectives is None:
        return None
    as_mapping = getattr(objectives, "as_mapping", None)
    if as_mapping is None:
        return None
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



def _mean(values: Any) -> float:
    collected = tuple(values)
    if not collected:
        return 0.0
    return sum(float(value) for value in collected) / len(collected)


def _sigma(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
