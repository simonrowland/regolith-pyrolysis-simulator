from __future__ import annotations

import pytest
from types import SimpleNamespace

from simulator.battery.score import SCORE_ELIGIBLE_CONJUNCTS
from simulator.battery.score_report import (
    _all_numeric_markdown_lines,
    _eligibility_failure_census_lines,
)
from simulator.battery.score_report_data import (
    EligibilityFailureCensus,
    eligibility_failure_census,
)


def _rows_with_known_failures() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for conjunct_index, conjunct in enumerate(SCORE_ELIGIBLE_CONJUNCTS, start=1):
        for _ in range(conjunct_index):
            rows.append(
                {
                    "reference": f"obs-{conjunct_index}",
                    "rail": "vapour",
                    "candidate_request": {"engine": "engine-a"},
                    "numeric": {"value": "1"},
                    "exclusions": [conjunct],
                }
            )
    rows.append(
        {
            "reference": "eligible-observation",
            "rail": "vapour",
            "candidate_request": {"engine": "engine-a"},
            "numeric": {"value": "0"},
            "exclusions": [],
        }
    )
    return rows


def _observations(rows: list[dict[str, object]]) -> dict[str, object]:
    references = {str(row["reference"]) for row in rows}
    return {
        reference: SimpleNamespace(
            observation_id=reference,
            source_id=(
                "source-eligible"
                if reference == "eligible-observation"
                else f"source-{(int(reference.removeprefix('obs-')) + 1) // 2}"
            ),
        )
        for reference in references
    }


def _counts(
    rows: list[dict[str, object]], observations: dict[str, object]
) -> dict[str, tuple[int, int, int]]:
    census = eligibility_failure_census(
        rows,
        conjunct_names=SCORE_ELIGIBLE_CONJUNCTS,
        observations=observations,  # type: ignore[arg-type]
    )
    return {
        conjunct: (events, observations, first_events)
        for _rail, _engine, conjunct, events, observations, first_events in census.rows
    }


def test_eligibility_census_counts_events_observations_and_first_failures() -> None:
    rows = _rows_with_known_failures()
    observations = _observations(rows)
    counts = _counts(rows, observations)

    for expected_events, conjunct in enumerate(SCORE_ELIGIBLE_CONJUNCTS, start=1):
        assert counts[conjunct] == (expected_events, 1, expected_events)

    numeric_events = len(rows)
    eligible_events = sum(not row["exclusions"] for row in rows)
    assert sum(value[2] for value in counts.values()) == numeric_events - eligible_events
    census = eligibility_failure_census(
        rows,
        conjunct_names=SCORE_ELIGIBLE_CONJUNCTS,
        observations=observations,  # type: ignore[arg-type]
    )
    assert census.engine_counts == (("vapour", "engine-a", 92, 14, 8),)


def test_eligibility_census_detects_swapped_conjunct_predicates() -> None:
    rows = _rows_with_known_failures()
    observations = _observations(rows)
    expected = _counts(rows, observations)
    left, right = SCORE_ELIGIBLE_CONJUNCTS[:2]
    mutated = [dict(row) for row in rows]
    for row in mutated:
        exclusions = row["exclusions"]
        if exclusions == [left]:
            row["exclusions"] = [right]
        elif exclusions == [right]:
            row["exclusions"] = [left]

    actual = _counts(mutated, observations)
    with pytest.raises(AssertionError):
        assert actual == expected

    assert actual[left] == expected[right]
    assert actual[right] == expected[left]


def test_score_report_places_unique_and_certified_counts_in_one_table() -> None:
    lines = _all_numeric_markdown_lines(
        [
            {
                "rail": "vapour",
                "engine": "engine-a",
                "n": 5,
                "n_certified": 2,
                "n_flagged": 3,
                "n_dex": 5,
                "median_dex": "0",
                "iqr_dex": "1",
                "metric_strata": [],
                "flag_class_counts": {
                    "unflagged": 2,
                    "apparatus": 3,
                    "catalogue": 2,
                    "calibration": 1,
                },
                "sources": [],
            }
        ],
        (("vapour", "engine-a", 5, 4, 3),),
    )

    report = "\n".join(lines)
    assert "| rail | engine | numeric events | unique observations | unique source_ids | " in report
    assert "| IQR dex | top 3 flag classes | sources |" in report
    assert "| vapour | engine-a | 5 | 4 | 3 | 2 | 3 | 5 | 0 | 1 | " in report
    assert "apparatus=3, catalogue=2, calibration=1" in report
    assert "unflagged=2" not in report

    census_lines = _eligibility_failure_census_lines(
        EligibilityFailureCensus(
            (("vapour", "engine-a", "admission_admitted", 2, 1, 1),),
            (("vapour", "engine-a", 5, 4, 3),),
        )
    )
    assert "first failing events" in census_lines[4]
    assert (
        "| vapour | engine-a | admission_admitted | 1 | admission_admitted | 2 | 1 |"
        in census_lines
    )
