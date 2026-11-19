from __future__ import annotations

import pytest
from decimal import Decimal
from types import SimpleNamespace

from simulator.battery.score import (
    SCORE_ELIGIBLE_CONJUNCTS,
    EligibleConjuncts,
    ResidualStatus,
    _engine_from_payload,
)
from simulator.battery.score_report import (
    _all_numeric_markdown_lines,
    _eligibility_failure_census_lines,
    format_source_relation_diagnostics,
    render_payload_score_report_markdown,
)
from simulator.battery.score_report_data import (
    EligibilityFailureCensus,
    EligibleSourceRelationCount,
    eligibility_failure_census,
    eligible_source_relation_breakdown,
    notice_backlog_data,
    suspected_calibration_overlaps,
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
        engine_for_row=_engine_from_payload,
    )
    return {
        conjunct: (events, observations, first_events)
        for _rail, _engine, conjunct, events, observations, first_events in census.rows
    }


def test_openimcc_vocabulary_mismatch_is_visible_in_report() -> None:
    from simulator.battery.source_lineage import (
        OPENIMCC_INDEX_VOCABULARY_MISMATCH_PREFIX,
        OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX,
        OPENIMCC_LINEAGE_GAP_PREFIX,
        OPENIMCC_REVIEW_GAMMA_ROW_WITHHELD_PREFIX,
    )

    reason = (
        OPENIMCC_INDEX_VOCABULARY_MISMATCH_PREFIX
        + "manifest=3645e2c2bb;current=56a27a9249"
    )
    withheld_reason = (
        f"{OPENIMCC_REVIEW_GAMMA_ROW_WITHHELD_PREFIX}"
        "fragment=cu;candidate_rows=CuO0.5-1573K,CuO0.5-1923K"
    )
    unverified_reason = (
        f"{OPENIMCC_INDEX_VOCABULARY_UNVERIFIED_PREFIX}"
        "reason=current_commit_unavailable;manifest=3645e2c2bb"
    )
    lineage_gap_reason = (
        f"{OPENIMCC_LINEAGE_GAP_PREFIX}"
        "missing_work_id=darken-gurry-1946-iron-oxygen-ii"
    )
    notice_counts = notice_backlog_data(
        [
            {
                "notices": [
                    {"kind": "engine_model_caveat", "reason": reason},
                    {"kind": "engine_model_caveat", "reason": withheld_reason},
                    {"kind": "engine_model_caveat", "reason": unverified_reason},
                    {"kind": "engine_model_caveat", "reason": lineage_gap_reason},
                ]
            }
        ]
    )
    census = EligibilityFailureCensus((), ())

    report = render_payload_score_report_markdown(
        hostname="test",
        studio_hostname=None,
        store_stamp=None,
        mismatch_warning=None,
        engines=(),
        observations_present=False,
        all_numeric_records=(),
        eligibility_census=census,
        measured_headline_rows=(),
        e15_lines=(),
        flagged_rows=(),
        condition_lines=None,
        compilation_lines=(),
        refusal_counts={},
        admit={
            "residuals_with_admission_exclusion": 0,
            "residuals_admission_alone": 0,
            "unique_obs_admission_alone": 0,
        },
        notice_counts=notice_counts,
        diagnostic_count=0,
        pin_failures=(),
        status_diff=(),
        unmapped_legacy_keys=(),
        circularity_warning="",
    )

    assert notice_counts == {
        f"engine_model_caveat:{reason}": 1,
        f"engine_model_caveat:{withheld_reason}": 1,
        f"engine_model_caveat:{unverified_reason}": 1,
        f"engine_model_caveat:{lineage_gap_reason}": 1,
    }
    assert f"`engine_model_caveat:{reason}` | 1" in report
    assert f"`engine_model_caveat:{withheld_reason}` | 1" in report
    assert f"`engine_model_caveat:{unverified_reason}` | 1" in report
    assert f"`engine_model_caveat:{lineage_gap_reason}` | 1" in report


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
        engine_for_row=_engine_from_payload,
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


def test_eligibility_census_resolves_series_cells_to_their_parent_source() -> None:
    rows = [
        {
            "reference": f"parent#t{temperature}v1",
            "rail": "thermochemistry",
            "candidate": "engine:nasa_cea_9:parent",
            "key": f"parent::h::thermochemistry::nasa_cea_9",
            "numeric": {"value": "1"},
            "exclusions": [],
        }
        for temperature in (1000, 1100)
    ]
    observations = {
        "parent": SimpleNamespace(observation_id="parent", source_id="source-parent")
    }

    census = eligibility_failure_census(
        rows,
        conjunct_names=SCORE_ELIGIBLE_CONJUNCTS,
        observations=observations,  # type: ignore[arg-type]
        engine_for_row=_engine_from_payload,
    )

    # Synthetic cells resolve to and count as their one stored parent observation.
    assert census.engine_counts == (("thermochemistry", "nasa_cea_9", 2, 1, 1),)


@pytest.mark.parametrize(
    ("row", "expected_engine"),
    [
        (
            {
                "candidate_request": {"engine": "vaporock"},
                "key": "obs::p_sat::vapour::vaporock::fo2_mode=fixed",
            },
            "vaporock",
        ),
        (
            {
                "candidate": "engine:vaporock:obs",
                "key": "obs::p_sat::vapour::vaporock::fo2_mode=shared_ladder",
            },
            "vaporock",
        ),
        (
            {"key": "obs::p_sat::vapour::vaporock::fo2_mode=fixed"},
            "vaporock",
        ),
    ],
)
def test_eligibility_census_uses_score_engine_resolution(
    row: dict[str, object], expected_engine: str
) -> None:
    residual = {
        **row,
        "reference": "obs",
        "rail": "vapour",
        "numeric": {"value": "1"},
        "exclusions": [SCORE_ELIGIBLE_CONJUNCTS[0]],
    }

    census = eligibility_failure_census(
        [residual],
        conjunct_names=SCORE_ELIGIBLE_CONJUNCTS,
        engine_for_row=_engine_from_payload,
    )

    assert census.engine_counts == (("vapour", expected_engine, 1, 1, None),)


def _eligible_conjuncts(**overrides: object) -> EligibleConjuncts:
    values: dict[str, object] = {
        "status": ResidualStatus.MATCH,
        "finite_numeric_point_endpoints": True,
        "valid_metric_domain": True,
        "reference_measured_evidence": True,
        "admission_admitted": True,
        "extract_review_permits_use": True,
        "validity_gates_pass": True,
        "identity_equal": True,
        "source_relation_not_same_input": True,
        "candidate_engine_prediction_authority_allowed": True,
        "no_blocking_qualification": True,
        "selected_independent_lineage_level": True,
        "not_flagged_stratum": True,
    }
    values.update(overrides)
    return EligibleConjuncts(**values)  # type: ignore[arg-type]


def test_eligibility_census_preserves_real_first_failure_precedence() -> None:
    all_failed = _eligible_conjuncts(
        status=ResidualStatus.REFUSED,
        **{name: False for name in SCORE_ELIGIBLE_CONJUNCTS[1:]},
    )
    two_failed = _eligible_conjuncts(
        admission_admitted=False,
        identity_equal=False,
    )
    rows = [
        {
            "reference": "all-failed",
            "rail": "vapour",
            "candidate_request": {"engine": "engine-a"},
            "numeric": {"value": "1"},
            "exclusions": list(reversed(all_failed.exclusions())),
        },
        {
            "reference": "two-failed",
            "rail": "vapour",
            "candidate_request": {"engine": "engine-a"},
            "numeric": {"value": "1"},
            "exclusions": list(reversed(two_failed.exclusions())),
        },
    ]

    census = eligibility_failure_census(
        rows,
        conjunct_names=SCORE_ELIGIBLE_CONJUNCTS,
        engine_for_row=_engine_from_payload,
    )
    failures = {
        conjunct: (events, first_events)
        for _rail, _engine, conjunct, events, _observations, first_events in census.rows
    }

    assert failures["status_match_or_mismatch"] == (1, 1)
    assert failures["finite_numeric_point_endpoints"] == (1, 0)
    assert failures["admission_admitted"] == (2, 1)
    assert failures["identity_equal"] == (2, 0)
    assert sum(first for _events, first in failures.values()) == len(rows)


def _overlap_row(
    reference: str,
    value: str,
    band: str | None,
    *,
    engine: str = "alphamelts",
    eligible: bool = True,
    relation: str = "unknown",
    unit: str = "dimensionless",
    band_unit: str | None = None,
) -> dict[str, object]:
    numeric: dict[str, object] = {"value": value, "unit": unit}
    if band is not None:
        numeric["decision_band"] = {
            "value": band,
            "unit": unit if band_unit is None else band_unit,
            "rule": "test",
        }
    return {
        "reference": reference,
        "score_eligible": eligible,
        "source_relation": relation,
        "candidate_request": {"engine": engine},
        "numeric": numeric,
    }


def test_eligible_rows_break_down_by_source_relation() -> None:
    rows = [
        _overlap_row("ref-a", "0", "1", relation="independent"),
        _overlap_row("ref-b", "0", "1", relation="unknown"),
        _overlap_row("ref-c", "0", "1", relation="unknown", eligible=False),
        _overlap_row("ref-d", "0", "1", relation="same_input", eligible=False),
        _overlap_row(
            "ref-e", "0", "1", engine="openimcc", relation="unknown"
        ),
    ]

    counts = eligible_source_relation_breakdown(
        rows,
        engine_for_row=_engine_from_payload,
    )

    assert counts == (
        EligibleSourceRelationCount("alphamelts", 1, 1),
        EligibleSourceRelationCount("openimcc", 0, 1),
    )
    section = format_source_relation_diagnostics(counts, ())
    assert "## Eligible rows by source relation" in section
    assert "| alphamelts | 1 | 1 |" in section
    assert "| openimcc | 0 | 1 |" in section
    assert "## Suspected calibration overlap" in section
    assert section.endswith("None.")


def test_suspected_calibration_overlap_flags_only_the_tight_pair() -> None:
    """Diagnostic only: a perfect fit is flagged and a normal fit is not."""

    observations = {
        reference: SimpleNamespace(source_id=reference.removeprefix("ref-"))
        for reference in (
            "ref-tight",
            "ref-normal",
            "ref-short",
            "ref-gap",
            "ref-noband",
            "ref-units",
            "ref-edge",
            "ref-eight",
            "ref-seven",
            "ref-ineligible",
        )
    }
    rows: list[dict[str, object]] = []
    rows.extend(_overlap_row("ref-tight", "0", "1") for _ in range(10))
    rows.extend(_overlap_row("ref-normal", "0.5", "1") for _ in range(10))
    rows.extend(_overlap_row("ref-short", "0", "1") for _ in range(9))
    rows.extend(_overlap_row("ref-gap", "0", "1") for _ in range(9))
    rows.append(_overlap_row("ref-gap", "0", None))
    rows.extend(_overlap_row("ref-noband", "0", None) for _ in range(10))
    rows.extend(
        _overlap_row("ref-units", "0", "1", band_unit="other") for _ in range(10)
    )
    rows.extend(_overlap_row("ref-edge", "0.1", "1") for _ in range(10))
    rows.extend(_overlap_row("ref-eight", "0", "1") for _ in range(8))
    rows.extend(_overlap_row("ref-eight", "0.5", "1") for _ in range(2))
    rows.extend(_overlap_row("ref-seven", "0", "1") for _ in range(7))
    rows.extend(_overlap_row("ref-seven", "0.5", "1") for _ in range(3))
    rows.extend(
        _overlap_row("ref-ineligible", "0", "1", eligible=False) for _ in range(10)
    )
    eligible_before = [row["score_eligible"] for row in rows]

    overlaps = suspected_calibration_overlaps(
        rows,
        observations,  # type: ignore[arg-type]
        engine_for_row=_engine_from_payload,
    )

    assert [row["score_eligible"] for row in rows] == eligible_before
    assert {(row.source_id, row.engine) for row in overlaps} == {
        ("tight", "alphamelts"),
        ("eight", "alphamelts"),
    }
    tight = next(row for row in overlaps if row.source_id == "tight")
    assert tight.n == 10
    assert tight.median_abs_residual == Decimal(0)
    assert tight.median_band_half_width == Decimal(1)
    assert tight.fraction_inside_tight_band == Decimal(1)
    eight = next(row for row in overlaps if row.source_id == "eight")
    assert eight.fraction_inside_tight_band == Decimal("0.8")
