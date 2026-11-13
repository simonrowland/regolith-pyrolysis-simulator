"""Mixed executed objective sets rank on one declared axis.

A retained-metric list is not part of the sort key. Study leaderboards and
staged beam selection both compare maximize-native scores, one slot per
declared objective. A withheld objective is an absent slot. It is not a
metric name, not a dropped slot, and not a zero score.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.optimize.evaluate import RunReference, ScoredResult
from simulator.optimize.objective import (
    ObjectiveComputationError,
    ObjectiveDefinition,
    ObjectiveValue,
    ObjectiveVector,
    aligned_objective_rank_slots,
    candidate_rank_key,
    declared_objective_definitions,
    objective_definitions,
    scoring_profile_for_executed_spec,
)
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.strategy.protocol import Candidate
from simulator.optimize.strategy.staged import _rank_stage_results
from simulator.optimize.study import StudyRecord, _rank_key

_ROWS = {
    "duration_h": {
        "metric": "duration_h",
        "sense": "minimize",
        "units": "h",
        "weight": 0.34,
        "rationale": "throughput",
    },
    "pure_silica_glass_kg": {
        "metric": "pure_silica_glass_kg",
        "sense": "maximize",
        "units": "kg",
        "weight": 0.33,
        "rationale": "glass",
    },
    "oxygen_kg": {
        "metric": "oxygen_kg",
        "sense": "maximize",
        "units": "kg",
        "weight": 0.33,
        "rationale": "oxidizer",
    },
}


def _profile(order: tuple[str, ...]) -> dict:
    return {
        "objectives": [_ROWS[metric] for metric in order],
        "run": {
            "campaign": "C0",
            "hours": 1,
            "track": "pyrolysis",
            "c5_enabled": False,
        },
    }


def _spec(c5_enabled: bool, track: str = "pyrolysis"):
    return SimpleNamespace(campaign="C0", track=track, c5_enabled=c5_enabled)


def _furnace_summary() -> dict:
    return {
        "furnace_amortization_status": "available",
        "furnace_amortization_batch_cost_equivalents": 0.0,
    }


def _study_record(
    candidate_id: str,
    *,
    c5_enabled: bool,
    values: dict[str, float],
    track: str = "pyrolysis",
) -> StudyRecord:
    return StudyRecord(
        candidate_id=candidate_id,
        patch=RecipePatch({}),
        feasible=True,
        status="ok",
        objectives=values,
        feasibility_margins={},
        cache_key=f"cache-{candidate_id}",
        product_summary=_furnace_summary(),
        eval_spec=_spec(c5_enabled, track),
    )


def _scored(
    candidate_id: str,
    *,
    c5_enabled: bool,
    values: dict[str, float],
    senses: dict[str, str],
    track: str = "pyrolysis",
) -> tuple[Candidate, ScoredResult]:
    candidate = Candidate(id=candidate_id, patch=RecipePatch({}))
    scored = ScoredResult(
        candidate_id=candidate_id,
        eval_spec=_spec(c5_enabled, track),
        cache_key=f"cache-{candidate_id}",
        feasible=True,
        failure_category=None,
        objectives=ObjectiveVector(
            tuple(
                ObjectiveValue(metric=metric, sense=senses[metric], value=value)
                for metric, value in values.items()
            )
        ),
        run_reference=RunReference(status="ok", product_summary=_furnace_summary()),
    )
    return candidate, scored


def _winner(path: str, profile: dict, records, scored_pairs) -> str:
    definitions = declared_objective_definitions(profile)
    if path == "study":
        ranked = sorted(
            records,
            key=lambda row: _rank_key(row, definitions, profile=profile),
        )
        return ranked[0].candidate_id
    ranked = _rank_stage_results(
        scored_pairs,
        definitions,
        beam_width=len(scored_pairs),
        profile=profile,
    )
    return ranked[0][1].id


@pytest.mark.parametrize("path", ("study", "staged"))
def test_duration_first_mixed_sets_rank_the_better_duration(path: str) -> None:
    """C5-on keeps only duration. C5-off also keeps glass. Duration decides.

    The old key puts the retained names in front of the scores. The shorter
    name tuple then compares a score slot with the longer tuple's glass
    string and raises TypeError. The better duration must win.
    """

    profile = _profile(("duration_h", "pure_silica_glass_kg"))
    off_metrics = tuple(
        item.metric
        for item in objective_definitions(
            scoring_profile_for_executed_spec(profile, _spec(False))
        )
    )
    on_metrics = tuple(
        item.metric
        for item in objective_definitions(
            scoring_profile_for_executed_spec(profile, _spec(True))
        )
    )
    assert off_metrics == ("duration_h", "pure_silica_glass_kg")
    assert on_metrics == ("duration_h",)
    senses = {"duration_h": "minimize", "pure_silica_glass_kg": "maximize"}
    records = [
        _study_record(
            "c5-on",
            c5_enabled=True,
            values={"duration_h": 12.0},
        ),
        _study_record(
            "c5-off",
            c5_enabled=False,
            values={"duration_h": 4.0, "pure_silica_glass_kg": 1.0},
        ),
    ]
    scored_pairs = [
        _scored("c5-on", c5_enabled=True, values={"duration_h": 12.0}, senses=senses),
        _scored(
            "c5-off",
            c5_enabled=False,
            values={"duration_h": 4.0, "pure_silica_glass_kg": 1.0},
            senses=senses,
        ),
    ]
    assert _winner(path, profile, records, scored_pairs) == "c5-off"
    _assert_keys_are_score_slots(path, profile, records, scored_pairs)


@pytest.mark.parametrize("path", ("study", "staged"))
def test_glass_first_mixed_sets_do_not_rank_by_metric_spelling(path: str) -> None:
    """Alphabetical retained names must not outrank a finite earlier objective.

    C5-on's retained list starts with duration_h, which sorts before
    pure_silica_glass_kg. C5-on also has the better duration. Glass is the
    first declared objective, and only C5-off retains it, so C5-off wins.
    """

    profile = _profile(("pure_silica_glass_kg", "duration_h"))
    off_metrics = tuple(
        item.metric
        for item in objective_definitions(
            scoring_profile_for_executed_spec(profile, _spec(False))
        )
    )
    on_metrics = tuple(
        item.metric
        for item in objective_definitions(
            scoring_profile_for_executed_spec(profile, _spec(True))
        )
    )
    assert off_metrics == ("pure_silica_glass_kg", "duration_h")
    assert on_metrics == ("duration_h",)
    senses = {"duration_h": "minimize", "pure_silica_glass_kg": "maximize"}
    records = [
        _study_record(
            "c5-on",
            c5_enabled=True,
            values={"duration_h": 1.0},
        ),
        _study_record(
            "c5-off",
            c5_enabled=False,
            values={"pure_silica_glass_kg": 2.0, "duration_h": 20.0},
        ),
    ]
    scored_pairs = [
        _scored("c5-on", c5_enabled=True, values={"duration_h": 1.0}, senses=senses),
        _scored(
            "c5-off",
            c5_enabled=False,
            values={"pure_silica_glass_kg": 2.0, "duration_h": 20.0},
            senses=senses,
        ),
    ]
    assert _winner(path, profile, records, scored_pairs) == "c5-off"
    _assert_keys_are_score_slots(path, profile, records, scored_pairs)


_DECLARED = ("duration_h", "pure_silica_glass_kg", "oxygen_kg")


def _subsets(metrics: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    found: list[tuple[str, ...]] = []
    for mask in range(1, 1 << len(metrics)):
        found.append(
            tuple(
                metric
                for index, metric in enumerate(metrics)
                if mask & (1 << index)
            )
        )
    return tuple(found)


def _native_scores(retained: tuple[str, ...], scale: float) -> tuple[float, ...]:
    return tuple(scale * (index + 1) for index in range(len(retained)))


def _expected_leader(
    declared: tuple[str, ...],
    left: tuple[str, ...],
    left_scores: tuple[float, ...],
    right: tuple[str, ...],
    right_scores: tuple[float, ...],
) -> str:
    """Earliest declared metric whose presence or score differs.

    A missing metric loses to any finite score. A higher maximize-native
    score wins. This does not call the rank key.
    """

    left_by = dict(zip(left, left_scores, strict=True))
    right_by = dict(zip(right, right_scores, strict=True))
    for metric in declared:
        left_has = metric in left_by
        right_has = metric in right_by
        if not left_has and not right_has:
            continue
        if not left_has:
            return "right"
        if not right_has:
            return "left"
        if left_by[metric] != right_by[metric]:
            return "left" if left_by[metric] > right_by[metric] else "right"
    return "tie"


def _assert_keys_are_score_slots(path, profile, records, scored_pairs) -> None:
    definitions = declared_objective_definitions(profile)
    declared = tuple(item.metric for item in definitions)
    if path == "study":
        keys = [_rank_key(row, definitions, profile=profile) for row in records]
    else:
        keys = [
            item[0]
            for item in _rank_stage_results(
                scored_pairs,
                definitions,
                beam_width=len(scored_pairs),
                profile=profile,
            )
        ]
    for key in keys:
        slots = key[:-2] if path == "study" else key[1:-2]
        assert len(slots) == len(declared)
        assert all(
            isinstance(slot, tuple)
            and len(slot) == 2
            and isinstance(slot[0], int)
            and isinstance(slot[1], float)
            for slot in slots
        )
        assert not any(isinstance(item, str) for item in slots)


@pytest.mark.parametrize("left", _subsets(_DECLARED))
@pytest.mark.parametrize("right", _subsets(_DECLARED))
def test_every_retained_subset_ranks_on_the_declared_axis(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> None:
    """Every non-empty retained subset of the declared axis, both ways.

    The alphabetically earlier retained tuple is given the worse scores, so
    a name sort would pick it whenever the names differ. The declared axis
    decides instead.
    """

    if left == right:
        left_scale, right_scale = 10.0, 1.0
    elif left < right:
        left_scale, right_scale = 1.0, 10.0
    else:
        left_scale, right_scale = 10.0, 1.0
    left_scores = _native_scores(left, left_scale)
    right_scores = _native_scores(right, right_scale)
    leader = _expected_leader(_DECLARED, left, left_scores, right, right_scores)
    left_key = candidate_rank_key(
        _DECLARED,
        left,
        left_scores,
        cache_key="left",
        candidate_id="left",
    )
    right_key = candidate_rank_key(
        _DECLARED,
        right,
        right_scores,
        cache_key="right",
        candidate_id="right",
    )
    assert left_key[:-2] == aligned_objective_rank_slots(_DECLARED, left, left_scores)
    assert right_key[:-2] == aligned_objective_rank_slots(
        _DECLARED, right, right_scores
    )
    assert len(left_key) == len(right_key)
    if leader == "tie":
        assert left_key[:-2] == right_key[:-2]
    elif leader == "left":
        assert left_key < right_key
    else:
        assert right_key < left_key
    if left != right and leader != "tie":
        alphabetical = "left" if left < right else "right"
        if alphabetical != leader:
            assert (left_key < right_key) is (leader == "left")


def test_rank_axis_refuses_off_axis_duplicates_and_empty_declarations() -> None:
    assert aligned_objective_rank_slots((), (), ()) == ()
    assert aligned_objective_rank_slots(("duration_h",), ("duration_h",), (None,)) == (
        (1, 0.0),
    )
    finite = aligned_objective_rank_slots(("duration_h",), ("duration_h",), (0.0,))
    absent = aligned_objective_rank_slots(("duration_h",), (), ())
    assert finite < absent
    assert absent == ((1, 0.0),)
    with pytest.raises(ObjectiveComputationError, match="non-finite"):
        aligned_objective_rank_slots(("duration_h",), ("duration_h",), (float("nan"),))
    with pytest.raises(ObjectiveComputationError, match="must be unique"):
        aligned_objective_rank_slots(("duration_h", "duration_h"), (), ())
    with pytest.raises(ObjectiveComputationError, match="do not match"):
        aligned_objective_rank_slots(("duration_h",), ("duration_h",), ())
    with pytest.raises(ObjectiveComputationError, match="no declared rank axis"):
        aligned_objective_rank_slots((), ("duration_h",), (1.0,))
    with pytest.raises(
        ObjectiveComputationError, match="not on the declared rank axis"
    ):
        aligned_objective_rank_slots(("duration_h",), ("oxygen_kg",), (1.0,))


def _native(definition: ObjectiveDefinition, raw: float) -> float:
    return raw if definition.sense == "maximize" else -raw


@pytest.mark.parametrize("path", ("study", "staged"))
def test_track_and_c5_grid_uses_the_same_axis(path: str) -> None:
    """Every track and c5 flag this ticket distinguishes, ranked against the others."""

    profile = _profile(_DECLARED)
    declared_defs = declared_objective_definitions(profile)
    declared = tuple(item.metric for item in declared_defs)
    sense = {item.metric: item.sense for item in declared_defs}
    built = []
    for track in ("pyrolysis", "mre_baseline"):
        for c5_enabled in (False, True):
            spec = _spec(c5_enabled, track)
            retained_defs = objective_definitions(
                scoring_profile_for_executed_spec(profile, spec)
            )
            retained = tuple(item.metric for item in retained_defs)
            raw = {
                "duration_h": 12.0 if c5_enabled else 4.0,
                "pure_silica_glass_kg": 2.0,
                "oxygen_kg": 1.5,
            }
            values = {metric: raw[metric] for metric in retained}
            candidate_id = f"{track}-c5-{int(c5_enabled)}"
            record = _study_record(
                candidate_id,
                c5_enabled=c5_enabled,
                values=values,
                track=track,
            )
            scored = _scored(
                candidate_id,
                c5_enabled=c5_enabled,
                values=values,
                senses=sense,
                track=track,
            )
            native = {
                item.metric: _native(item, values[item.metric])
                for item in retained_defs
            }
            built.append((candidate_id, record, scored, retained, native))
    for left in range(len(built)):
        for right in range(left + 1, len(built)):
            left_id, left_record, left_scored, left_retained, left_native = built[left]
            right_id, right_record, right_scored, right_retained, right_native = built[right]
            leader = _expected_leader(
                declared,
                left_retained,
                tuple(left_native[metric] for metric in left_retained),
                right_retained,
                tuple(right_native[metric] for metric in right_retained),
            )
            winner = _winner(
                path,
                profile,
                [left_record, right_record],
                [left_scored, right_scored],
            )
            if leader == "left":
                assert winner == left_id
            elif leader == "right":
                assert winner == right_id
            else:
                assert winner in {left_id, right_id}
            _assert_keys_are_score_slots(
                path,
                profile,
                [left_record, right_record],
                [left_scored, right_scored],
            )
