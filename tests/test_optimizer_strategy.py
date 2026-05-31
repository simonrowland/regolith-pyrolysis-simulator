from __future__ import annotations

import pickle
import subprocess
import sys
from pathlib import Path

import pytest

from simulator.optimize import Candidate, RandomStrategy, Strategy
from simulator.optimize.doe import DEPENDENCY_FREE_LHC_SAMPLER, active_sampler_name
from simulator.optimize.evaluate import FailureCategory, ScoredResult
from simulator.optimize.recipe import (
    KnobSpec,
    RecipePatch,
    RecipeSchema,
)


class FakeStrategy:
    name = "fake"
    seed = 7

    def ask(self, n: int) -> list[Candidate]:
        return [
            Candidate(
                id=f"fake-{self.seed}-{index:06d}",
                patch=RecipePatch({}).validated(RecipeSchema()),
            )
            for index in range(n)
        ]

    def tell(self, results: list[tuple[Candidate, ScoredResult]]) -> None:
        for candidate, scored in results:
            assert scored.candidate_id == candidate.id


def _fake_result(candidate: Candidate) -> ScoredResult:
    return ScoredResult(
        candidate_id=candidate.id,
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
    )


def _canonical_candidates(candidates: list[Candidate]) -> tuple[tuple[str, str], ...]:
    return tuple((candidate.id, candidate.patch.canonical_json()) for candidate in candidates)


def test_protocol_implementable_at_runtime() -> None:
    strategy = FakeStrategy()

    assert isinstance(strategy, Strategy)
    candidates = strategy.ask(2)
    results = [(candidate, _fake_result(candidate)) for candidate in candidates]
    strategy.tell(results)


def test_random_strategy_ask_returns_valid_unique_candidates() -> None:
    schema = RecipeSchema()
    candidates = RandomStrategy(schema, seed=13).ask(8)

    assert len(candidates) == 8
    assert len({candidate.id for candidate in candidates}) == 8
    for candidate in candidates:
        validated = candidate.patch.validated(schema)
        assert validated.canonical_json() == candidate.patch.canonical_json()
        assert all(not schema.is_forbidden(path) for path in candidate.patch.values)


def test_random_strategy_deterministic_by_schema_seed_and_n() -> None:
    schema = RecipeSchema()

    first = RandomStrategy(schema, seed=22).ask(6)
    second = RandomStrategy(schema, seed=22).ask(6)
    different = RandomStrategy(schema, seed=23).ask(6)

    assert _canonical_candidates(first) == _canonical_candidates(second)
    assert _canonical_candidates(first) != _canonical_candidates(different)


def test_random_strategy_ask_is_chunk_invariant_for_default_sampler() -> None:
    schema = RecipeSchema()

    chunked_strategy = RandomStrategy(schema, seed=27)
    chunked = chunked_strategy.ask(2) + chunked_strategy.ask(1)
    one_shot = RandomStrategy(schema, seed=27).ask(3)

    assert _canonical_candidates(chunked) == _canonical_candidates(one_shot)


def test_random_strategy_rejects_non_streaming_sampler() -> None:
    with pytest.raises(ValueError, match="chunk-invariant"):
        RandomStrategy(
            RecipeSchema(), seed=27, sampler_name=DEPENDENCY_FREE_LHC_SAMPLER
        )


def test_random_strategy_sampler_name_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="unsupported DOE sampler"):
        RandomStrategy(RecipeSchema(), seed=27, sampler_name="")
    with pytest.raises(ValueError, match="unsupported DOE sampler"):
        RandomStrategy(RecipeSchema(), seed=27, sampler_name="not-a-sampler")

    strategy = RandomStrategy(RecipeSchema(), seed=27, sampler_name=None)

    assert strategy.sampler_name == active_sampler_name()


def test_random_strategy_tell_round_trip_records_candidate_linkage() -> None:
    strategy = RandomStrategy(RecipeSchema(), seed=31)
    candidates = strategy.ask(3)
    results = [(candidate, _fake_result(candidate)) for candidate in candidates]

    strategy.tell(results)

    assert strategy.tell_count == 3
    assert all(scored.candidate_id == candidate.id for candidate, scored in strategy.results)


def test_random_strategy_tell_rejects_candidate_id_mismatch() -> None:
    strategy = RandomStrategy(RecipeSchema(), seed=31)
    candidate = strategy.ask(1)[0]
    bad_result = ScoredResult(
        candidate_id="other",
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
    )

    with pytest.raises(ValueError, match="candidate_id"):
        strategy.tell([(candidate, bad_result)])


def test_random_strategy_tell_rejects_mismatched_batch_atomically() -> None:
    strategy = RandomStrategy(RecipeSchema(), seed=31)
    candidates = strategy.ask(2)
    bad_result = ScoredResult(
        candidate_id="other",
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
    )

    with pytest.raises(ValueError, match="candidate_id"):
        strategy.tell([(candidates[0], _fake_result(candidates[0])), (candidates[1], bad_result)])

    assert strategy.tell_count == 0
    assert strategy.results == ()


def test_random_strategy_tell_rejects_duplicate_candidate_ids_atomically() -> None:
    strategy = RandomStrategy(RecipeSchema(), seed=31)
    candidate = strategy.ask(1)[0]

    with pytest.raises(ValueError, match="duplicate candidate_id"):
        strategy.tell([(candidate, _fake_result(candidate)), (candidate, _fake_result(candidate))])

    assert strategy.tell_count == 0
    assert strategy.results == ()

    strategy.tell([(candidate, _fake_result(candidate))])
    before = strategy.results

    with pytest.raises(ValueError, match="already recorded"):
        strategy.tell([(candidate, _fake_result(candidate))])

    assert strategy.tell_count == 1
    assert strategy.results == before


def test_candidate_pickle_hash_and_deep_frozen_metadata() -> None:
    candidate = Candidate(
        id="candidate-1",
        patch=RecipePatch({}).validated(RecipeSchema()),
        metadata={
            "nested": {"values": [1, 2]},
            "labels": {"beta", "alpha"},
        },
    )

    loaded = pickle.loads(pickle.dumps(candidate))

    assert loaded == candidate
    assert loaded.metadata["nested"]["values"] == candidate.metadata["nested"]["values"]
    assert loaded.metadata["labels"] == ("alpha", "beta")
    assert hash(loaded) == hash(candidate)
    assert loaded in {candidate}
    with pytest.raises(TypeError):
        loaded.metadata["new"] = "blocked"
    with pytest.raises(TypeError):
        loaded.metadata["nested"]["new"] = "blocked"


def test_candidate_metadata_nested_mutables_are_frozen() -> None:
    candidate = Candidate(
        id="candidate-2",
        patch=RecipePatch({}).validated(RecipeSchema()),
        metadata={"nested": {"items": [{"value": 1}]}},
    )

    assert candidate.metadata["nested"]["items"][0]["value"] == 1
    with pytest.raises(TypeError):
        candidate.metadata["nested"]["items"][0]["value"] = 2


def test_random_strategy_clean_import_does_not_load_evaluate_or_pool() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    code = """
import importlib
import sys

importlib.import_module("simulator.optimize.strategy.random_strategy")
forbidden = {
    "simulator.optimize.evaluate",
    "simulator.optimize.pool",
}
loaded = sorted(name for name in forbidden if name in sys.modules)
if loaded:
    raise SystemExit(f"forbidden imports loaded: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_random_strategy_ask_edge_cases() -> None:
    strategy = RandomStrategy(RecipeSchema(), seed=41)

    assert strategy.ask(0) == []
    for bad_n in (-1, True, "x"):
        with pytest.raises(ValueError, match="non-negative int"):
            strategy.ask(bad_n)  # type: ignore[arg-type]

    candidates = strategy.ask(1) + strategy.ask(2) + strategy.ask(3)

    assert [candidate.id for candidate in candidates] == [
        "random-41-000000",
        "random-41-000001",
        "random-41-000002",
        "random-41-000003",
        "random-41-000004",
        "random-41-000005",
    ]
    assert len({candidate.id for candidate in candidates}) == len(candidates)


def test_random_strategy_large_sequence_id_format_is_unambiguous() -> None:
    strategy = RandomStrategy(RecipeSchema(), seed=41)

    ids = [strategy._candidate_id(999_999), strategy._candidate_id(1_000_000)]

    assert ids == ["random-41-999999", "random-41-1000000"]
    assert len(set(ids)) == 2


def test_random_strategy_tell_empty_and_malformed_pairs() -> None:
    strategy = RandomStrategy(RecipeSchema(), seed=43)
    candidate = strategy.ask(1)[0]

    strategy.tell([])

    assert strategy.tell_count == 0
    assert strategy.results == ()
    with pytest.raises(ValueError, match="2"):
        strategy.tell([(candidate, _fake_result(candidate), "extra")])  # type: ignore[list-item]
    assert strategy.tell_count == 0
    assert strategy.results == ()


def test_random_strategy_tell_does_not_perturb_later_ask() -> None:
    schema = RecipeSchema()
    baseline = RandomStrategy(schema, seed=47).ask(3)
    strategy = RandomStrategy(schema, seed=47)
    first = strategy.ask(1)

    strategy.tell([(first[0], _fake_result(first[0]))])
    later = strategy.ask(2)

    assert _canonical_candidates(first + later) == _canonical_candidates(baseline)


def test_random_strategy_seed_namespaces_are_disjoint() -> None:
    first_ids = {candidate.id for candidate in RandomStrategy(RecipeSchema(), seed=1).ask(2)}
    second_ids = {candidate.id for candidate in RandomStrategy(RecipeSchema(), seed=2).ask(2)}

    assert first_ids.isdisjoint(second_ids)


def test_random_strategy_rejects_forbidden_allowlist_paths() -> None:
    schema = RecipeSchema(
        allowlist=(
            KnobSpec(
                path=("campaigns", "C2A_staged", "products", "oxygen_kg"),
                kind="float",
                low=0.0,
                high=1.0,
                bounds_source="test",
            ),
        )
    )

    with pytest.raises(ValueError, match="forbidden"):
        RandomStrategy(schema, seed=53).ask(1)
