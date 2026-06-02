"""Forward staged optimizer strategy with prefix-replay contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from simulator.optimize.doe import (
    SAMPLER_NAMES,
    STREAMING_SAMPLER_NAMES,
    active_sampler_name,
    sample_recipe_patch_at_index,
)
from simulator.optimize.evalspec import EvalSpec, PrefixEvalSpec
from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize.objective import (
    ObjectiveDefinition,
    objective_definitions,
    objective_scores,
    pareto_front,
)
from simulator.optimize.recipe import KeyPath, KnobSpec, RecipePatch, RecipeSchema
from simulator.optimize.strategy.protocol import Candidate


class StagedStrategyError(RuntimeError):
    """Raised when staged strategy configuration asks for unsupported work."""


class StagedReplayViolation(StagedStrategyError):
    """Raised when cached prefix replay differs from a fresh prefix run."""


class StagedDuplicateCacheKey(StagedStrategyError):
    """Raised when beam candidates produce duplicate cache keys."""


class StagedBeamStateError(StagedStrategyError):
    """Raised when staged beam state is corrupt or empty."""


@dataclass(frozen=True)
class _BeamNode:
    patch: RecipePatch
    stage_ids: tuple[str, ...]
    recipe_ids: tuple[str, ...]
    parent_id: str | None
    score_key: tuple[Any, ...] = ()
    cache_key: str | None = None


@dataclass(frozen=True)
class _ArchiveMember:
    candidate: Candidate
    scored: ScoredResult
    node: _BeamNode


def make_prefix_eval_spec(
    base_spec: EvalSpec,
    *,
    prefix_stage_ids: Sequence[str],
    prefix_recipe_ids: Sequence[str] = (),
    topology_id: str = "PATH_AB",
) -> PrefixEvalSpec:
    recipe_ids = tuple(prefix_recipe_ids)
    stage_ids = tuple(prefix_stage_ids)
    if not recipe_ids:
        recipe_ids = (base_spec.recipe_id,) * len(stage_ids)
    return PrefixEvalSpec(
        recipe_id=base_spec.recipe_id,
        feedstock_recipe_digest=base_spec.feedstock_recipe_digest,
        feedstock_id=base_spec.feedstock_id,
        profile_id=base_spec.profile_id,
        fidelity=base_spec.fidelity,
        code_version=base_spec.code_version,
        data_digests=base_spec.data_digests,
        campaign=base_spec.campaign,
        hours=base_spec.hours,
        mass_kg=base_spec.mass_kg,
        additives_kg=base_spec.additives_kg,
        track=base_spec.track,
        backend_name=base_spec.backend_name,
        runtime_campaign_overrides=base_spec.runtime_campaign_overrides,
        chemistry_kernel=base_spec.chemistry_kernel,
        prefix_stage_ids=stage_ids,
        prefix_recipe_ids=recipe_ids,
        topology_id=topology_id,
    )


def assert_prefix_replay_equal(replayed: ScoredResult, fresh: ScoredResult) -> None:
    replayed_view = _prefix_result_view(replayed)
    fresh_view = _prefix_result_view(fresh)
    if replayed_view != fresh_view:
        raise StagedReplayViolation(
            f"cached prefix replay differs from fresh prefix: {replayed_view!r} != {fresh_view!r}"
        )


class StagedStrategy:
    """Forward staged beam search over campaign-scoped recipe knobs."""

    name = "staged"

    def __init__(
        self,
        schema: RecipeSchema | None = None,
        *,
        seed: int,
        objective_profile: Mapping[str, Any] | None = None,
        beam_width: int | None = None,
        children_per_parent: int | None = None,
        stage_allowlist: Sequence[str] | None = None,
        sampler_name: str | None = None,
    ) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative int")
        self.schema = schema or RecipeSchema()
        self.objective_profile = MappingProxyType(dict(objective_profile or {}))
        options = _staged_options(self.objective_profile)
        _reject_out_of_scope(options)
        self._seed = seed
        self.sampler_name = active_sampler_name() if sampler_name is None else sampler_name
        if self.sampler_name not in SAMPLER_NAMES:
            raise ValueError(f"unsupported DOE sampler {self.sampler_name!r}")
        if self.sampler_name not in STREAMING_SAMPLER_NAMES:
            raise ValueError(
                f"DOE sampler {self.sampler_name!r} is not chunk-invariant for ask()"
            )
        self.beam_width = _positive_int(
            beam_width if beam_width is not None else options.get("beam_width", 2),
            "beam_width",
        )
        self.children_per_parent = _positive_int(
            children_per_parent
            if children_per_parent is not None
            else options.get("children_per_parent", max(2, self.beam_width)),
            "children_per_parent",
        )
        self.max_backward_passes = _backward_pass_limit(options)
        self.backward_passes_completed = 0
        self._backward_done = self.max_backward_passes <= 0
        self._backward_reference_signature: tuple[str, ...] | None = None
        configured_allowlist = (
            stage_allowlist if stage_allowlist is not None else options.get("allowlist")
        )
        self._stages = _stage_specs(self.schema, configured_allowlist)
        if not self._stages:
            raise StagedBeamStateError("staged strategy requires at least one stage")
        self._definitions = objective_definitions(self.objective_profile)
        self._stage_index = 0
        self._frontier = (
            _BeamNode(
                patch=RecipePatch({}),
                stage_ids=(),
                recipe_ids=(),
                parent_id=None,
            ),
        )
        self._pending: list[Candidate] = []
        self._expected_stage_ids: set[str] = set()
        self._stage_results: dict[str, tuple[Candidate, ScoredResult]] = {}
        self._asked_by_id: dict[str, Candidate] = {}
        self._results: list[tuple[Candidate, ScoredResult]] = []
        self._archive: tuple[_ArchiveMember, ...] = ()
        self._mode = "forward"
        self._tell_count = 0
        self._build_stage_candidates()

    @property
    def seed(self) -> int:
        return self._seed

    @property
    def tell_count(self) -> int:
        return self._tell_count

    @property
    def results(self) -> tuple[tuple[Candidate, ScoredResult], ...]:
        return tuple(self._results)

    @property
    def stage_ids(self) -> tuple[str, ...]:
        return tuple(stage_id for stage_id, _ in self._stages)

    def ask(self, n: int) -> list[Candidate]:
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError("n must be a non-negative int")
        if n == 0:
            return []
        if not self._pending and self._stage_index >= len(self._stages):
            return []
        batch = self._pending[:n]
        self._pending = self._pending[n:]
        return batch

    def tell(self, results: Sequence[tuple[Candidate, ScoredResult]]) -> None:
        batch: list[tuple[Candidate, ScoredResult]] = []
        seen: set[str] = set()
        recorded = {candidate.id for candidate, _ in self._results}
        for pair in results:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("tell results must contain (Candidate, ScoredResult) 2-tuples")
            candidate, scored = pair
            if not isinstance(candidate, Candidate):
                raise ValueError("tell result candidate must be a Candidate")
            if candidate.id not in self._asked_by_id:
                raise StagedBeamStateError(f"unknown staged candidate_id: {candidate.id!r}")
            scored_candidate_id = getattr(scored, "candidate_id", None)
            if scored_candidate_id != candidate.id:
                raise ValueError(
                    "ScoredResult.candidate_id must match Candidate.id "
                    f"({scored_candidate_id!r} != {candidate.id!r})"
                )
            if candidate.id in seen:
                raise ValueError(f"duplicate candidate_id in tell batch: {candidate.id!r}")
            if candidate.id in recorded:
                raise ValueError(f"candidate_id already recorded: {candidate.id!r}")
            seen.add(candidate.id)
            light_scored = _strip_trace(scored)
            _assert_not_parent_duplicate(candidate, light_scored)
            batch.append((candidate, light_scored))

        for candidate, scored in batch:
            self._stage_results[candidate.id] = (candidate, scored)
            self._results.append((candidate, scored))
        self._tell_count += len(batch)
        self._advance_completed_stage()

    def run_backward_pass(self) -> bool:
        if self._backward_done:
            return False
        if self._pending or self._expected_stage_ids:
            return False
        if self._stage_index < len(self._stages):
            return False
        if not self._archive:
            self._backward_done = True
            return False
        if self.backward_passes_completed >= self.max_backward_passes:
            self._backward_done = True
            return False

        candidates = self._build_backward_candidates()
        if not candidates:
            self._backward_done = True
            return False
        self._backward_reference_signature = self._archive_signature()
        self._mode = "backward"
        self._pending = candidates
        self._expected_stage_ids = {candidate.id for candidate in candidates}
        self._stage_results = {}
        self.backward_passes_completed += 1
        return True

    def joint_refine(self) -> None:
        raise StagedStrategyError("joint-refine not implemented until O-P5b3")

    def enumerate_c6_topology(self) -> None:
        raise StagedStrategyError("C6 topology not implemented until O-P5b3")

    def _build_stage_candidates(self) -> None:
        if self._stage_index >= len(self._stages):
            self._pending = []
            self._expected_stage_ids = set()
            self._stage_results = {}
            return
        stage_id, specs = self._stages[self._stage_index]
        if not specs:
            raise StagedBeamStateError(f"stage {stage_id!r} has no knobs")
        stage_schema = RecipeSchema(
            allowlist=specs,
            recipe_schema_version=self.schema.recipe_schema_version,
            allowlist_version=self.schema.allowlist_version,
        )
        candidates: list[Candidate] = []
        expected: set[str] = set()
        for parent_index, parent in enumerate(self._frontier):
            for child_index in range(self.children_per_parent):
                sample_index = (
                    self._stage_index * 1_000_000
                    + parent_index * self.children_per_parent
                    + child_index
                )
                stage_patch = sample_recipe_patch_at_index(
                    stage_schema,
                    index=sample_index,
                    seed=self.seed,
                    sampler_name=self.sampler_name,
                )
                patch = _merge_patches(parent.patch, stage_patch).validated(self.schema)
                candidate_id = (
                    f"{self.name}-{self.seed}-{self._stage_index:02d}-"
                    f"{stage_id}-p{parent_index:03d}-c{child_index:06d}"
                )
                metadata = {
                    "strategy": self.name,
                    "seed": self.seed,
                    "stage_index": self._stage_index,
                    "stage_id": stage_id,
                    "stage_ids": (*parent.stage_ids, stage_id),
                    "prefix_depth": len(parent.stage_ids),
                    "prefix_stage_ids": parent.stage_ids,
                    "prefix_recipe_ids": parent.recipe_ids,
                    "prefix_patch_values": _metadata_patch_values(parent.patch),
                    "stage_patch_values": _metadata_patch_values(stage_patch),
                    "topology": {"id": "PATH_AB"},
                    "parent_candidate_id": parent.parent_id,
                    "parent_cache_key": parent.cache_key,
                    "parent_rank": parent_index,
                    "child_index": child_index,
                }
                candidate = Candidate(id=candidate_id, patch=patch, metadata=metadata)
                candidates.append(candidate)
                expected.add(candidate_id)
                self._asked_by_id[candidate_id] = candidate
        self._pending = candidates
        self._expected_stage_ids = expected
        self._stage_results = {}

    def _advance_completed_stage(self) -> None:
        if self._pending:
            return
        if not self._expected_stage_ids:
            return
        if set(self._stage_results) != self._expected_stage_ids:
            return
        if self._mode == "backward":
            self._advance_completed_backward_pass()
            return
        ranked = _rank_stage_results(
            self._stage_results.values(),
            self._definitions,
            beam_width=self.beam_width,
        )
        if not ranked:
            raise StagedBeamStateError("staged beam produced no ranked candidates")
        next_frontier: list[_BeamNode] = []
        for score_key, candidate, scored in ranked:
            next_frontier.append(_node_from_candidate(candidate, scored, score_key, self.schema))
        self._frontier = tuple(next_frontier)
        if self._stage_index == len(self._stages) - 1:
            self._archive = _pareto_archive(
                _archive_members_from_results(
                    self._stage_results.values(),
                    self._definitions,
                    self.schema,
                ),
                self._definitions,
            )
            if self._archive:
                self._frontier = tuple(member.node for member in self._archive)
        self._stage_index += 1
        self._build_stage_candidates()

    def _advance_completed_backward_pass(self) -> None:
        ranked = _rank_stage_results(
            self._stage_results.values(),
            self._definitions,
            beam_width=len(self._stage_results),
        )
        children = tuple(
            _ArchiveMember(
                candidate=candidate,
                scored=scored,
                node=_node_from_candidate(candidate, scored, score_key, self.schema),
            )
            for score_key, candidate, scored in ranked
            if scored.feasible and scored.objectives is not None
        )
        self._archive = _pareto_archive((*self._archive, *children), self._definitions)
        self._frontier = tuple(member.node for member in self._archive)
        self._expected_stage_ids = set()
        self._stage_results = {}
        self._mode = "forward"
        if (
            self._backward_reference_signature is not None
            and self._archive_signature() == self._backward_reference_signature
        ):
            self._backward_done = True
        self._backward_reference_signature = None
        if self.backward_passes_completed >= self.max_backward_passes:
            self._backward_done = True

    def _build_backward_candidates(self) -> list[Candidate]:
        if not self._archive:
            return []
        target_index = len(self._stages) - 1 - (
            self.backward_passes_completed % len(self._stages)
        )
        target_stage_id, target_specs = self._stages[target_index]
        if not target_specs:
            raise StagedBeamStateError(f"stage {target_stage_id!r} has no knobs")
        stage_schema = RecipeSchema(
            allowlist=target_specs,
            recipe_schema_version=self.schema.recipe_schema_version,
            allowlist_version=self.schema.allowlist_version,
        )
        candidates: list[Candidate] = []
        backward_stage_id = f"backward-{target_stage_id}"
        for parent_index, member in enumerate(self._archive):
            parent = member.node
            prefix_patch = _patch_for_stage_range(
                parent.patch,
                self._stages,
                stop=target_index,
            )
            suffix_patch = _patch_for_stage_range(
                parent.patch,
                self._stages,
                start=target_index + 1,
            )
            for child_index in range(self.children_per_parent):
                sample_index = (
                    10_000_000
                    + self.backward_passes_completed * 1_000_000
                    + target_index * 100_000
                    + parent_index * self.children_per_parent
                    + child_index
                )
                stage_patch = sample_recipe_patch_at_index(
                    stage_schema,
                    index=sample_index,
                    seed=self.seed,
                    sampler_name=self.sampler_name,
                )
                replay_patch = _combine_patches(stage_patch, suffix_patch)
                patch = _combine_patches(prefix_patch, replay_patch).validated(self.schema)
                recipe_id = patch.recipe_id(self.schema)
                candidate_id = (
                    f"{self.name}-{self.seed}-backward-"
                    f"{self.backward_passes_completed:02d}-{target_stage_id}-"
                    f"p{parent_index:03d}-c{child_index:06d}"
                )
                prefix_recipe_ids = parent.recipe_ids[:target_index]
                metadata = {
                    "strategy": self.name,
                    "seed": self.seed,
                    "stage_index": target_index,
                    "stage_id": backward_stage_id,
                    "stage_ids": (*parent.stage_ids, backward_stage_id),
                    "recipe_ids": (*parent.recipe_ids, recipe_id),
                    "prefix_depth": target_index,
                    "prefix_stage_ids": tuple(
                        stage_id for stage_id, _ in self._stages[:target_index]
                    ),
                    "prefix_recipe_ids": prefix_recipe_ids,
                    "prefix_patch_values": _metadata_patch_values(prefix_patch),
                    "stage_patch_values": _metadata_patch_values(replay_patch),
                    "topology": {"id": "PATH_AB"},
                    "parent_candidate_id": parent.parent_id,
                    "parent_cache_key": parent.cache_key,
                    "parent_rank": parent_index,
                    "child_index": child_index,
                    "backward_pass": self.backward_passes_completed,
                    "backward_target_stage_id": target_stage_id,
                }
                candidate = Candidate(id=candidate_id, patch=patch, metadata=metadata)
                candidates.append(candidate)
                self._asked_by_id[candidate_id] = candidate
        return candidates

    def _archive_signature(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                member.scored.cache_key
                for member in self._archive
                if isinstance(member.scored.cache_key, str)
            )
        )


def _rank_stage_results(
    results: Sequence[tuple[Candidate, ScoredResult]],
    definitions: Sequence[ObjectiveDefinition],
    *,
    beam_width: int,
) -> tuple[tuple[tuple[Any, ...], Candidate, ScoredResult], ...]:
    seen_keys: dict[str, str] = {}
    ranked: list[tuple[tuple[Any, ...], Candidate, ScoredResult]] = []
    for candidate, scored in results:
        key = scored.cache_key
        if not isinstance(key, str) or not key:
            raise StagedBeamStateError(f"staged candidate {candidate.id!r} missing cache_key")
        prior = seen_keys.get(key)
        if prior is not None:
            raise StagedDuplicateCacheKey(
                f"duplicate staged cache_key {key!r}: {prior!r} and {candidate.id!r}"
            )
        seen_keys[key] = candidate.id
        score_key = _score_key(candidate, scored, definitions)
        ranked.append((score_key, candidate, scored))
    ranked.sort(key=lambda item: item[0])
    return tuple(ranked[:beam_width])


def _archive_members_from_results(
    results: Sequence[tuple[Candidate, ScoredResult]],
    definitions: Sequence[ObjectiveDefinition],
    schema: RecipeSchema,
) -> tuple[_ArchiveMember, ...]:
    members: list[_ArchiveMember] = []
    for candidate, scored in results:
        if not scored.feasible or scored.objectives is None:
            continue
        score_key = _score_key(candidate, scored, definitions)
        members.append(
            _ArchiveMember(
                candidate=candidate,
                scored=scored,
                node=_node_from_candidate(candidate, scored, score_key, schema),
            )
        )
    return tuple(members)


def _pareto_archive(
    members: Sequence[_ArchiveMember],
    definitions: Sequence[ObjectiveDefinition],
) -> tuple[_ArchiveMember, ...]:
    feasible = tuple(
        member
        for member in members
        if member.scored.feasible and member.scored.objectives is not None
    )
    front = pareto_front(
        feasible,
        definitions,
        objective_getter=lambda member: member.scored.objectives,
    )
    seen_keys: dict[str, str] = {}
    for member in front:
        key = member.scored.cache_key
        if not isinstance(key, str) or not key:
            raise StagedBeamStateError(
                f"staged archive candidate {member.candidate.id!r} missing cache_key"
            )
        prior = seen_keys.get(key)
        if prior is not None and prior != member.candidate.id:
            raise StagedDuplicateCacheKey(
                f"duplicate staged archive cache_key {key!r}: "
                f"{prior!r} and {member.candidate.id!r}"
            )
        seen_keys[key] = member.candidate.id
    return tuple(
        sorted(front, key=lambda member: _score_key(member.candidate, member.scored, definitions))
    )


def _node_from_candidate(
    candidate: Candidate,
    scored: ScoredResult,
    score_key: tuple[Any, ...],
    schema: RecipeSchema,
) -> _BeamNode:
    return _BeamNode(
        patch=candidate.patch,
        stage_ids=_string_tuple_metadata(candidate, "stage_ids"),
        recipe_ids=_recipe_ids_from_metadata(candidate, schema),
        parent_id=candidate.id,
        score_key=score_key,
        cache_key=scored.cache_key,
    )


def _recipe_ids_from_metadata(candidate: Candidate, schema: RecipeSchema) -> tuple[str, ...]:
    raw = candidate.metadata.get("recipe_ids")
    if raw is not None:
        if not isinstance(raw, Sequence) or isinstance(raw, str):
            raise StagedBeamStateError("staged recipe_ids metadata must be a sequence")
        values = tuple(raw)
        if not all(isinstance(value, str) for value in values):
            raise StagedBeamStateError("staged recipe_ids metadata must contain strings")
        return values
    return (
        *_string_tuple_metadata(candidate, "prefix_recipe_ids"),
        candidate.patch.recipe_id(schema),
    )


def _score_key(
    candidate: Candidate,
    scored: ScoredResult,
    definitions: Sequence[ObjectiveDefinition],
) -> tuple[Any, ...]:
    if scored.feasible and scored.objectives is not None:
        scores = objective_scores(scored.objectives, definitions)
        return (0, *(-score for score in scores), scored.cache_key or "", candidate.id)
    return (1, scored.cache_key or "", candidate.id)


def _stage_specs(
    schema: RecipeSchema,
    configured_allowlist: Sequence[str] | None,
) -> tuple[tuple[str, tuple[KnobSpec, ...]], ...]:
    stage_filter: tuple[str, ...] | None
    if configured_allowlist is None:
        stage_filter = None
    else:
        if isinstance(configured_allowlist, str):
            raise StagedBeamStateError("staged allowlist must be a sequence of stage ids")
        stage_filter = tuple(configured_allowlist)
        if not all(isinstance(stage_id, str) for stage_id in stage_filter):
            raise StagedBeamStateError("staged allowlist must contain stage id strings")
    grouped: dict[str, list[KnobSpec]] = {}
    order: list[str] = []
    for spec in schema.allowlist:
        if schema.is_forbidden(spec.path):
            continue
        stage_id = _stage_id_for_path(spec.path)
        if stage_filter is not None and stage_id not in stage_filter:
            continue
        if stage_id not in grouped:
            grouped[stage_id] = []
            order.append(stage_id)
        grouped[stage_id].append(spec)
    if stage_filter is not None:
        order = [stage_id for stage_id in stage_filter if stage_id in grouped]
    return tuple((stage_id, tuple(grouped[stage_id])) for stage_id in order)


def _stage_id_for_path(path: KeyPath) -> str:
    if len(path) >= 2 and path[0] == "campaigns":
        return path[1]
    return path[0] if path else "global"


def _staged_options(profile: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("staged", "staged_strategy"):
        value = profile.get(key)
        if isinstance(value, Mapping):
            return MappingProxyType(dict(value))
    return MappingProxyType({})


def _reject_out_of_scope(options: Mapping[str, Any]) -> None:
    if options.get("joint_refine"):
        raise StagedStrategyError("joint-refine not implemented until O-P5b3")
    topology = str(options.get("topology", options.get("topology_id", "PATH_AB"))).upper()
    if topology in {"C6", "C6_TOPOLOGY"} or options.get("enable_c6_topology"):
        raise StagedStrategyError("C6 topology not implemented until O-P5b3")


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive int")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative int")
    return value


def _backward_pass_limit(options: Mapping[str, Any]) -> int:
    if "max_backward_passes" in options:
        return _non_negative_int(options["max_backward_passes"], "max_backward_passes")
    if "backward_passes" in options:
        return _non_negative_int(options["backward_passes"], "backward_passes")
    if options.get("backward") or options.get("block_coordinate_ascent"):
        return 1
    return 0


def _merge_patches(prefix: RecipePatch, stage_patch: RecipePatch) -> RecipePatch:
    values = dict(prefix.values)
    overlap = set(values).intersection(stage_patch.values)
    if overlap:
        joined = ", ".join(".".join(path) for path in sorted(overlap))
        raise StagedBeamStateError(f"stage patch overlaps prefix patch: {joined}")
    values.update(stage_patch.values)
    return RecipePatch(values)


def _combine_patches(*patches: RecipePatch) -> RecipePatch:
    combined = RecipePatch({})
    for patch in patches:
        combined = _merge_patches(combined, patch)
    return combined


def _patch_for_stage_range(
    patch: RecipePatch,
    stages: Sequence[tuple[str, tuple[KnobSpec, ...]]],
    *,
    start: int = 0,
    stop: int | None = None,
) -> RecipePatch:
    selected = {
        spec.path
        for _, specs in stages[start:stop]
        for spec in specs
    }
    return RecipePatch(
        {
            path: value
            for path, value in patch.values.items()
            if path in selected
        }
    )


def _string_tuple_metadata(candidate: Candidate, key: str) -> tuple[str, ...]:
    raw = candidate.metadata.get(key, ())
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise StagedBeamStateError(f"staged {key} metadata must be a sequence")
    values = tuple(raw)
    if not all(isinstance(value, str) for value in values):
        raise StagedBeamStateError(f"staged {key} metadata must contain strings")
    return values


def _assert_not_parent_duplicate(candidate: Candidate, scored: ScoredResult) -> None:
    parent_key = candidate.metadata.get("parent_cache_key")
    if parent_key is None:
        return
    key = scored.cache_key
    if isinstance(parent_key, str) and isinstance(key, str) and key == parent_key:
        raise StagedDuplicateCacheKey(
            f"staged child {candidate.id!r} duplicated parent cache_key {key!r}"
        )


def _metadata_patch_values(patch: RecipePatch) -> Mapping[str, Any]:
    return MappingProxyType({".".join(path): value for path, value in sorted(patch.values.items())})


def _strip_trace(scored: ScoredResult) -> ScoredResult:
    reference = scored.run_reference
    if reference is None or reference.trace is None:
        return scored
    return replace(
        scored,
        run_reference=RunReference(
            status=reference.status,
            error_message=reference.error_message,
            reason=reference.reason,
            trace=None,
            product_summary=reference.product_summary,
        ),
    )


def _prefix_result_view(scored: ScoredResult) -> Mapping[str, Any]:
    return {
        "cache_key": scored.cache_key,
        "failure_category": scored.failure_category.value if scored.failure_category else None,
        "failing_gates": tuple(scored.failing_gates),
        "feasible": bool(scored.feasible),
        "margins": {
            key: _margin_view(value)
            for key, value in sorted(scored.feasibility_margins.items())
        },
        "notes": tuple(scored.notes),
        "objectives": _objectives_view(scored),
        "run_reference": _run_reference_view(scored.run_reference),
    }


def _objectives_view(scored: ScoredResult) -> tuple[tuple[Any, ...], ...]:
    if scored.objectives is None:
        return ()
    return tuple(
        (
            value.metric,
            value.sense,
            float(value.value),
            value.units,
            value.ordinal,
        )
        for value in scored.objectives.values
    )


def _margin_view(margin: Any) -> tuple[Any, ...]:
    threshold = margin.threshold
    return (
        margin.gate,
        bool(margin.feasible),
        float(margin.margin),
        threshold.id,
        float(threshold.value),
        threshold.units,
        threshold.source,
        threshold.source_ref,
        float(threshold.tolerance),
        float(margin.observed),
        margin.detail,
    )


def _run_reference_view(reference: RunReference | None) -> Mapping[str, Any] | None:
    if reference is None:
        return None
    return {
        "error_message": reference.error_message,
        "product_summary": reference.product_summary,
        "reason": reference.reason,
        "status": reference.status,
    }


__all__ = [
    "StagedBeamStateError",
    "StagedDuplicateCacheKey",
    "StagedReplayViolation",
    "StagedStrategy",
    "StagedStrategyError",
    "assert_prefix_replay_equal",
    "make_prefix_eval_spec",
]
