"""Ask ordinal is the only sampling order, for every strategy and sink.

INVARIANT: exact objective ties and published Pareto order follow the order
``ask()`` emitted the candidate. ``trial_number``, the spelling of
``candidate_id``, ``cache_key``, and completion order do not reorder a
record that carries ``ask_ordinal``. Staged search asks seeds before Sobol
children, and the Sobol id sorts first lexicographically; the ordinal keeps
the seed first.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from simulator.optimize import study
from simulator.optimize.evalspec import cache_key
from simulator.optimize.evaluate import ScoredResult
from simulator.optimize.objective import (
    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
    ObjectiveValue,
    ObjectiveVector,
)
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.optimize.strategy import (
    MorrisScreenStrategy,
    OptunaNSGA2Strategy,
    OptunaTPEStrategy,
    RandomStrategy,
    StagedStrategy,
    WarmStartSeed,
)
from tests.test_optimizer_study import FEEDSTOCK, PROFILE, _margin, _run_reference, _spec


_STRATEGY_NAMES = (
    "random",
    "morris",
    "tpe",
    "nsga2",
    "staged_unseeded",
    "staged_seeded",
)
_SINKS = (
    "select_pareto",
    "pareto_payload",
    "study_result",
    "replay_result",
    "write_artifacts",
    "aborted_cache",
)
_ASKED: dict[str, list[Any]] = {}


def _stage_profile() -> dict[str, Any]:
    return {
        **PROFILE,
        "staged": {"beam_width": 1, "children_per_parent": 2},
    }


def _ask(strategy_name: str) -> list[Any]:
    schema = RecipeSchema()
    if strategy_name == "random":
        return RandomStrategy(schema, seed=7).ask(3)
    if strategy_name == "morris":
        return MorrisScreenStrategy(schema, seed=7, num_trajectories=1).ask(3)
    if strategy_name == "tpe":
        return OptunaTPEStrategy(
            schema,
            seed=7,
            objective_profile=PROFILE,
            budget=3,
        ).ask(3)
    if strategy_name == "nsga2":
        return OptunaNSGA2Strategy(
            schema,
            seed=7,
            objective_profile=PROFILE,
        ).ask(3)
    if strategy_name == "staged_unseeded":
        return StagedStrategy(
            schema,
            seed=7,
            objective_profile=_stage_profile(),
        ).ask(3)
    if strategy_name == "staged_seeded":
        seed = WarmStartSeed(
            "study-c0-seed",
            RecipePatch.from_nested(
                {"campaigns": {"C0": {"temp_range_C": [900, 950]}}}
            ).validated(schema),
            "seed_recipe",
        )
        return StagedStrategy(
            schema,
            seed=7,
            objective_profile=_stage_profile(),
            stage0_seed_candidates=(seed,),
        ).ask(3)
    raise AssertionError(f"unknown strategy {strategy_name}")


def _asked(strategy_name: str) -> list[Any]:
    try:
        batch = _ASKED.get(strategy_name)
        if batch is None:
            batch = _ask(strategy_name)
            _ASKED[strategy_name] = batch
    except ImportError as exc:
        pytest.skip(str(exc))
    assert len(batch) >= 2, strategy_name
    return batch


def _tied_records(strategy_name: str) -> tuple[tuple[study.StudyRecord, ...], list[str]]:
    """Identical scores. Trial numbers run opposite the ask order."""

    candidates = _asked(strategy_name)
    count = len(candidates)
    records = []
    for index, candidate in enumerate(candidates):
        records.append(
            study.StudyRecord(
                candidate_id=candidate.id,
                patch=candidate.patch,
                feasible=True,
                status="ok",
                objectives={
                    "oxygen_kg": 10.0,
                    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC: 5.0,
                },
                feasibility_margins={},
                cache_key=f"cache-{count - index:06d}",
                product_summary={
                    "furnace_amortization_status": "available",
                    "furnace_amortization_batch_cost_equivalents": 0.0,
                },
                search_provenance={
                    "ask_ordinal": index + 1,
                    "trial_number": count - index,
                },
            )
        )
    ask_ids = [candidate.id for candidate in candidates]
    return tuple(records), ask_ids


def _published_ids(
    sink: str,
    records: tuple[study.StudyRecord, ...],
    *,
    out: Path,
) -> list[str]:
    definitions = study.objective_definitions(PROFILE)
    if sink == "select_pareto":
        return [
            record.candidate_id
            for record in study._select_pareto(records, definitions).published
        ]
    if sink == "pareto_payload":
        payload = study._pareto_payload(
            PROFILE,
            FEEDSTOCK,
            "internal-analytical",
            definitions,
            records,
            None,
            RecipeSchema(),
        )
        return [row["candidate_id"] for row in payload["pareto"]]
    if sink == "study_result":
        result = study.StudyResult(
            out_dir=out,
            store_path=out / "cache.sqlite",
            artifacts={},
            records=records,
            leaderboard=records,
            pareto=records,
            winner=None,
        )
        return [record.candidate_id for record in result.pareto]
    if sink == "replay_result":
        replay = study.StudyReplayResult(
            run_dir=out,
            manifest={},
            strategy=RandomStrategy(RecipeSchema(), seed=0),
            records=records,
            leaderboard=records,
            pareto=records,
        )
        return [record.candidate_id for record in replay.pareto]
    if sink == "write_artifacts":
        out.mkdir(parents=True, exist_ok=True)
        study._write_artifacts(
            out,
            profile=PROFILE,
            feedstock=FEEDSTOCK,
            fidelity="internal-analytical",
            definitions=definitions,
            pareto=records,
            leaderboard=records,
            winner=None,
            schema=RecipeSchema(),
            failure_counts={},
            strategy_name="fixed",
        )
        payload = json.loads((out / "pareto.json").read_text(encoding="utf-8"))
        return [row["candidate_id"] for row in payload["pareto"]]
    if sink == "aborted_cache":
        out.mkdir(parents=True, exist_ok=True)
        wrote = study._write_aborted_artifacts_from_cache(
            out,
            profile=PROFILE,
            feedstock=FEEDSTOCK,
            fidelity="internal-analytical",
            definitions=definitions,
            fallback_records=records,
            schema=RecipeSchema(),
            failure_counts={},
            strategy_name="fixed",
        )
        assert wrote is True
        payload = json.loads((out / "pareto.json").read_text(encoding="utf-8"))
        return [row["candidate_id"] for row in payload["pareto"]]
    raise AssertionError(f"unknown sink {sink}")


def _arrivals(
    records: tuple[study.StudyRecord, ...],
) -> tuple[tuple[study.StudyRecord, ...], ...]:
    """Every distinct completion order.

    ``StudyRecord`` holds dicts, so it cannot be a set member. Identity is
    the candidate-id sequence.
    """

    ordered: list[tuple[study.StudyRecord, ...]] = [
        tuple(reversed(records)),
        tuple(sorted(records, key=lambda row: row.candidate_id)),
        tuple(sorted(records, key=lambda row: row.search_provenance["trial_number"])),
    ]
    if len(records) <= 3:
        ordered.extend(itertools.permutations(records))
    unique: list[tuple[study.StudyRecord, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for arrival in ordered:
        key = tuple(record.candidate_id for record in arrival)
        if key in seen:
            continue
        seen.add(key)
        unique.append(arrival)
    return tuple(unique)


@pytest.mark.parametrize("strategy_name", _STRATEGY_NAMES)
@pytest.mark.parametrize("sink", _SINKS)
def test_ask_ordinal_orders_every_strategy_and_sink(
    strategy_name: str,
    sink: str,
    tmp_path: Path,
) -> None:
    """Tied candidates publish in ask order on every sink.

    Trial numbers and cache keys run backwards. Staged seeds are the id
    shape whose lexical order is the reverse of the ask. Every arrival,
    including that lexical order, must still publish the ask order.
    """

    records, ask_ids = _tied_records(strategy_name)
    if strategy_name == "staged_seeded":
        assert "-seed-s000-" in ask_ids[0]
        assert "-p000-c" in ask_ids[1]
        assert sorted(ask_ids) != ask_ids
    definitions = study.objective_definitions(PROFILE)
    for index, arrival in enumerate(_arrivals(records)):
        ids = _published_ids(sink, arrival, out=tmp_path / f"{strategy_name}-{sink}-{index}")
        assert ids == ask_ids
        selection = study._select_pareto(arrival, definitions)
        assert selection.winner is not None
        assert selection.winner.candidate_id == ask_ids[0]
        assert [record.candidate_id for record in selection.ranked] == ask_ids


def _tied_evaluator(
    patch: RecipePatch,
    feedstock: str,
    fidelity: str,
    *,
    profile: Mapping[str, Any],
    candidate_id: str | None = None,
    **kwargs: Any,
) -> ScoredResult:
    spec = _spec(patch, feedstock, fidelity, profile, kwargs.get("constraints"))
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", 10.0, "kg", ordinal=0),
                ObjectiveValue(
                    ENERGY_ELECTRICAL_PLUS_EVAPORATION_METRIC,
                    "minimize",
                    5.0,
                    "kWh",
                    ordinal=1,
                ),
            )
        ),
        feasibility_margins={"delivered_stream_purity": _margin()},
        run_reference=_run_reference(
            status="ok",
            trace={
                "backend_status": "ok",
                "backend_authoritative": True,
                "snapshots": [{"mass_balance_error_pct": 0.0}],
            },
            product_summary={
                "oxygen_kg": 10.0,
                "mass_closure": {
                    "status": "closed",
                    "mass_balance_error_pct": 0.0,
                },
            },
            backend_status="ok",
            backend_authoritative=True,
        ),
    )


def _ask_ids(result: study.StudyResult) -> list[str]:
    ordered = sorted(
        result.records,
        key=lambda record: record.search_provenance["ask_ordinal"],
    )
    return [record.candidate_id for record in ordered]


def test_seeded_staged_tie_follows_ask_order_not_completion_or_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A seeded stage-0 tie keeps the seed, which was asked before its Sobol child.

    The Sobol id sorts first. Reversing evaluation completion, journal
    replay, two-phase certification, and a cache rebuild must all keep the
    ask order. Serial evaluation is the same winner.
    """

    run_kwargs = dict(
        feedstock=FEEDSTOCK,
        strategy="staged",
        fidelity="internal-analytical",
        parallel=2,
        budget=2,
        seed=7,
        evaluator=_tied_evaluator,
    )
    reference = study.run(PROFILE, out_dir=tmp_path / "reference", **run_kwargs)
    serial = study.run(
        PROFILE,
        out_dir=tmp_path / "serial",
        **{**run_kwargs, "parallel": 1},
    )
    original = study._evaluate_candidates

    def reversed_completion(*args: Any, **kwargs: Any):
        completed, prefix_evals, pool = original(*args, **kwargs)
        return tuple(reversed(completed)), prefix_evals, pool

    monkeypatch.setattr(study, "_evaluate_candidates", reversed_completion)
    reversed_result = study.run(PROFILE, out_dir=tmp_path / "reversed", **run_kwargs)
    monkeypatch.setattr(study, "_evaluate_candidates", original)
    certified = study.run(
        PROFILE,
        out_dir=tmp_path / "certified",
        two_phase_certify={"enabled": True, "top_k": 2},
        **run_kwargs,
    )
    replay = study.replay_study(tmp_path / "reference")

    ask_ids = _ask_ids(reference)
    assert len(ask_ids) == 2
    assert "-seed-s000-" in ask_ids[0]
    assert "-p000-c" in ask_ids[1]
    assert ask_ids[1] < ask_ids[0]
    for result in (reference, serial, reversed_result, certified):
        assert result.winner is not None
        assert result.winner.candidate_id == ask_ids[0]
        assert [record.candidate_id for record in result.pareto] == ask_ids
        assert [record.candidate_id for record in result.records] == ask_ids
        payload = json.loads((result.out_dir / "pareto.json").read_text(encoding="utf-8"))
        assert [row["candidate_id"] for row in payload["pareto"]] == ask_ids
        assert payload["winner_candidate_id"] == ask_ids[0]
    assert [record.candidate_id for record in replay.pareto] == ask_ids
    assert [record.candidate_id for record in replay.records] == ask_ids
    assert (tmp_path / "reference" / "pareto.json").read_bytes() == (
        tmp_path / "reversed" / "pareto.json"
    ).read_bytes()

    definitions = study.objective_definitions(PROFILE)
    rebuilt = study._records_from_cache_sqlite(
        tmp_path / "reversed",
        RecipeSchema(),
        profile=PROFILE,
        feedstock=FEEDSTOCK,
        fidelity="internal-analytical",
    )
    assert sorted(record.search_provenance["ask_ordinal"] for record in rebuilt) == [1, 2]
    selection = study._select_pareto(rebuilt, definitions)
    assert selection.winner is not None
    assert selection.winner.candidate_id == ask_ids[0]
    assert [record.candidate_id for record in selection.published] == ask_ids
    assert [record.search_provenance["ask_ordinal"] for record in selection.published] == [
        1,
        2,
    ]
