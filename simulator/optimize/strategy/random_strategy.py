"""Random optimizer strategy baseline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from simulator.optimize.doe import (
    SAMPLER_NAMES,
    STREAMING_SAMPLER_NAMES,
    active_sampler_name,
    sample_recipe_candidate_at_index,
)
from simulator.optimize.recipe import RecipeSchema, conditional_context_metadata
from simulator.optimize.strategy.protocol import Candidate

if TYPE_CHECKING:
    from simulator.optimize.evaluate import ScoredResult


class RandomStrategy:
    """Pure exploration baseline backed by the DOE recipe sampler."""

    name = "random"

    def __init__(
        self,
        schema: RecipeSchema | None = None,
        *,
        seed: int,
        sampler_name: str | None = None,
    ) -> None:
        if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
            raise ValueError("seed must be a non-negative int")
        self.schema = schema or RecipeSchema()
        self._seed = seed
        self.sampler_name = active_sampler_name() if sampler_name is None else sampler_name
        if self.sampler_name not in SAMPLER_NAMES:
            raise ValueError(f"unsupported DOE sampler {self.sampler_name!r}")
        if self.sampler_name not in STREAMING_SAMPLER_NAMES:
            raise ValueError(
                f"DOE sampler {self.sampler_name!r} is not chunk-invariant for ask()"
            )
        self._asked = 0
        self._tell_count = 0
        self._results: list[tuple[Candidate, ScoredResult]] = []

    @property
    def seed(self) -> int:
        return self._seed

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

        start = self._asked
        end = start + n
        candidates = []
        for sequence in range(start, end):
            sampled = sample_recipe_candidate_at_index(
                    self.schema,
                    index=sequence,
                    seed=self.seed,
                    sampler_name=self.sampler_name,
                )
            candidates.append(Candidate(
                id=self._candidate_id(sequence),
                patch=sampled.patch,
                metadata={
                    "strategy": self.name,
                    "seed": self.seed,
                    "sequence": sequence,
                    "sampler_name": self.sampler_name,
                    "proposal_source": "sobol",
                    **conditional_context_metadata(sampled.conditional_context),
                },
            ))
        self._asked = end
        return candidates

    def tell(self, results: Sequence[tuple[Candidate, "ScoredResult"]]) -> None:
        batch: list[tuple[Candidate, "ScoredResult"]] = []
        seen: set[str] = set()
        recorded = {candidate.id for candidate, _ in self._results}

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
            if candidate.id in seen:
                raise ValueError(f"duplicate candidate_id in tell batch: {candidate.id!r}")
            if candidate.id in recorded:
                raise ValueError(f"candidate_id already recorded: {candidate.id!r}")
            seen.add(candidate.id)
            batch.append((candidate, scored))

        self._results.extend(batch)
        self._tell_count += len(batch)

    def _candidate_id(self, sequence: int) -> str:
        return f"{self.name}-{self.seed}-{sequence:06d}"
