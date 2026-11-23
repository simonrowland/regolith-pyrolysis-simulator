"""Byte pins for the scorer's residual JSONL, summary JSON, and report Markdown.

The fixture is ``tests.battery.test_score._streaming_score_fixture``, the
existing small in-memory scoring set. Engines are not dispatched:
``_compile_residual_with_lineage`` returns those fixture residuals.

Direct path: ``score_store``, ``write_residuals_jsonl``,
``write_headline_summary_json``, ``render_score_report``.
Streamed path: ``_score_store_to_jsonl``,
``_write_headline_summary_from_accumulator_json``,
``_render_score_report_from_payloads_legacy``.
Serialized-row path: ``write_headline_summary_from_payloads_json``,
``render_score_report_from_payloads``. That writer is a separate entry
with its own prose and decimal text, so it has its own pins.

No clock, host, or absolute path is rewritten. The fixture hostname is the
literal ``test`` from the fixture's ``ScoreContext``, not this machine. A
fixed test store stamp keeps the pins independent of corpus regeneration while
covering stamp serialization; live stamp derivation has its own test in
``tests.battery.test_score``.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AdmissionStatus,
    Engine,
    EvidenceClass,
    NoticeKind,
    Phase,
    Quantity,
    Rail,
    ResidualStatus,
)
from simulator.battery.identity import State
from simulator.battery.records import (
    CandidateRequest,
    Species,
    Value,
)
import simulator.battery.score as score_mod
from tests.battery import factories as F
from tests.battery.test_score import _predict, _streaming_score_fixture

HEADING = "# Battery score report (schema v2.1)"
FIXTURE_STORE_STAMP = {
    "experiments": 7001,
    "hard_issues": 1685,
    "kind": "battery_store_stamp",
    "observations": 186648,
    "queue": 124428,
    "revision": "2043bca61",
    "rows_in": 91208,
    "works": 785,
}
_CERTIFIED_COUNT_FIELD = re.compile(r'(?m)^[ \t]*"n_certified":\s*\d+,\n')


def _without_certified_counts(text: str) -> str:
    """Keep prior byte pins focused on report content outside inserted tables."""

    text = _CERTIFIED_COUNT_FIELD.sub("", text)
    census_heading = "## Eligibility conjunct failure census"
    if census_heading in text:
        before, _separator, after = text.partition(census_heading)
        # Coverage is inserted after the eligibility census. Stop there so
        # the pin keeps the coverage block and still drops the census.
        next_headings = (
            "## Coverage",
            "## Measured tier",
            "## Per rail × engine headline",
        )
        next_heading = min(
            (heading for heading in next_headings if heading in after),
            key=after.index,
            default=None,
        )
        if next_heading is None:
            text = before
        else:
            _census, _separator, remainder = after.partition(next_heading)
            text = before + next_heading + remainder
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    removed_columns: set[int] = set()
    for line in lines:
        if not line.startswith("|"):
            removed_columns = set()
            output.append(line)
            continue
        cells = line.split("|")
        labels = {cell.strip(): index for index, cell in enumerate(cells)}
        if "n certified" in labels:
            removed_columns = {
                labels[label]
                for label in (
                    "n certified",
                    "unique observations",
                    "unique source_ids",
                    "flag classes",
                    "top 3 flag classes",
                )
                if label in labels
            }
            if "numeric events" in labels:
                cells[labels["numeric events"]] = " n "
        if removed_columns:
            cells = [cell for index, cell in enumerate(cells) if index not in removed_columns]
        output.append("|".join(cells))
    return "".join(output)


def _expected_fixture_certified_counts() -> tuple[
    dict[tuple[str, str], int], dict[tuple[str, str, str], int]
]:
    from simulator.battery.compilation_tier import (
        _compilation_engines_for_pair,
        is_compilation_evidence,
    )
    from simulator.battery.score import is_compilation_source

    context, engine, fixture_residuals, _candidates = _streaming_score_fixture()
    def reaches_score_engine(residual) -> bool:
        observation = context.observations[residual.reference]
        origin = context.origins.get(residual.reference)
        if not (
            is_compilation_evidence(observation)
            or is_compilation_source(observation.source_id, origin)
        ):
            return True
        identity = observation.identity
        return engine in _compilation_engines_for_pair((engine,), identity)

    certified = {
        residual.reference
        for residual in score_mod._measured_residuals(
            tuple(fixture_residuals.values()),
            context,
            include_engine_out_of_band=True,
        )
        if residual.numeric is not None and residual.rail is not None
    }
    numeric = [
        residual
        for residual in fixture_residuals.values()
        if residual.numeric is not None
        and residual.rail is not None
        and reaches_score_engine(residual)
    ]
    aggregate_counts = {
        (rail.value, engine.value): sum(
            residual.rail is rail and residual.reference in certified
            for residual in numeric
        )
        for rail in Rail
    }
    source_keys = {
        (
            residual.rail.value,
            engine.value,
            context.observations[residual.reference].source_id
            or residual.reference,
        )
        for residual in numeric
    }
    source_counts = {
        key: sum(
            (
                residual.rail.value,
                engine.value,
                context.observations[residual.reference].source_id
                or residual.reference,
            )
            == key
            and residual.reference in certified
            for residual in numeric
        )
        for key in source_keys
    }
    return aggregate_counts, source_counts


def _summary_certified_counts(
    text: str,
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int]]:
    payload = json.loads(text)
    records = payload.get("records")
    assert isinstance(records, list)
    all_numeric = [
        record
        for record in records
        if isinstance(record, dict) and record.get("tier") == "all_numeric"
    ]
    assert all_numeric, "summary omitted all-numeric records"
    aggregate_counts: dict[tuple[str, str], int] = {}
    source_counts: dict[tuple[str, str, str], int] = {}
    for record in all_numeric:
        assert "n_certified" in record, (
            "all-numeric summary schema omitted certified count"
        )
        aggregate_key = (record["rail"], record["engine"])
        aggregate_counts[aggregate_key] = record["n_certified"]
        sources = record.get("sources")
        assert isinstance(sources, list), "all-numeric summary schema omitted sources"
        for source in sources:
            assert "n_certified" in source, (
                "source summary schema omitted certified count"
            )
            source_key = (*aggregate_key, source["source_id"])
            source_counts[source_key] = source["n_certified"]
    return aggregate_counts, source_counts


def _report_certified_counts(
    text: str,
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int]]:
    section = text.split("## All-numeric tier", 1)[1].split("\n## ", 1)[0]
    lines = section.splitlines()
    header_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("|") and "n certified" in line
    )
    table_lines = []
    for line in lines[header_index:]:
        if not line.startswith("|"):
            break
        table_lines.append(line)
    header = [cell.strip() for cell in table_lines[0].split("|")[1:-1]]
    assert "n certified" in header, (
        "all-numeric report schema omitted certified column"
    )
    rail_column = header.index("rail")
    engine_column = header.index("engine")
    certified_column = header.index("n certified")
    sources_column = header.index("sources")
    aggregate_counts: dict[tuple[str, str], int] = {}
    source_counts: dict[tuple[str, str, str], int] = {}
    source_pattern = re.compile(r"([^(),]+)\(n=\d+,cert=(\d+),flag=\d+\)")
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        aggregate_key = (cells[rail_column], cells[engine_column])
        aggregate_counts[aggregate_key] = int(cells[certified_column])
        for source_id, count in source_pattern.findall(cells[sources_column]):
            source_counts[(*aggregate_key, source_id.strip())] = int(count)
    assert aggregate_counts, "all-numeric report omitted aggregate rows"
    return aggregate_counts, source_counts


def _assert_certified_counts_match_fixture(
    actual: tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int]],
    expected: tuple[dict[tuple[str, str], int], dict[tuple[str, str, str], int]],
) -> None:
    assert actual[0] == expected[0], (
        "aggregate certified counts differ from fixture membership"
    )
    assert actual[1] == expected[1], (
        "source certified counts differ from fixture membership"
    )


# Route pin: gas ΔfG and entropy rows routed away from Internal Analytical are no longer emitted.
PIN_RESIDUAL_JSONL = """\
{"experiments":7001,"hard_issues":1685,"kind":"battery_store_stamp","observations":186648,"queue":124428,"revision":"2043bca61","rows_in":91208,"works":785}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-measured-refusal::internal-analytical","notices":[],"rail":"vapour","reference":"fixture-measured-refusal","refusal":{"check_refs":[],"detail":{"reason":"fixture_unavailable"},"reason":"unsupported"},"score_eligible":false,"source_relation":"unknown","status":"refused"}
{"candidate":"fixture-vapour-candidate","candidate_observation":{"admission":{"decided_by":{"date":"2026-09-13","evidence":{"page":1,"table":"I"},"worker":"test"},"reason":"canonical","status":"admitted"},"derivation":{"inputs":["raw-1"],"output_unit":"Pa","parameters":[],"references":[],"relation":"model"},"derived_from":["raw-1"],"evidence":{"class":{"tag":"value","value":"model_derived"},"model":"gibbs-duhem"},"experiment_id":"exp-1","identity":{"composition":{"reason":"pure-component p_sat","tag":"not_applicable"},"exposure":{"reason":"pure-component p_sat","tag":"not_applicable"},"fO2_Pa":{"reason":"pure-component p_sat","tag":"not_applicable"},"formation_elements":{"reason":"pure-component p_sat","tag":"not_applicable"},"per":{"reason":"pure-component p_sat","tag":"not_applicable"},"quantity":{"tag":"value","value":"p_sat"},"reaction":{"reason":"pure-component p_sat","tag":"not_applicable"},"reference_state":{"reason":"pure-component p_sat","tag":"not_applicable"},"reservoir":{"tag":"value","value":{"charge":{"tag":"value","value":0},"formula":"Na","phase":{"tag":"value","value":"l"}}},"sample_mass_kg":{"reason":"pure-component p_sat","tag":"not_applicable"},"species":{"charge":{"tag":"value","value":0},"formula":"Na","phase":{"tag":"value","value":"g"}},"standard_pressure_Pa":{"reason":"pure-component p_sat","tag":"not_applicable"},"subtype":{"reason":"pure-component p_sat","tag":"not_applicable"},"sweep_gas":{"reason":"pure-component p_sat","tag":"not_applicable"},"temperature_K":{"tag":"value","value":"1156"},"total_pressure_Pa":{"reason":"pure-component p_sat","tag":"not_applicable"},"wall":{"reason":"pure-component p_sat","tag":"not_applicable"}},"locator":{"page":1,"table":"I"},"notices":[],"observation_id":"fixture-vapour-candidate","point_conditions":{"temperature_K":{"locator":{"page":1,"table":"I"},"state":{"tag":"value","value":"1156"}}},"read_from":"pdf-1","source_id":"work-1","uncertainty":{"kind":"none"},"value":{"approximate":false,"kind":"point","point":"0.6"}},"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-measured-vapour::internal-analytical","notices":[],"numeric":{"decision_band":{"band_basis":"derived_scatter","rule":"measured fixture band","unit":"dimensionless","value":"1"},"operation":"dex","unit":"dimensionless","value":"0.5"},"rail":"vapour","reference":"fixture-measured-vapour","score_eligible":true,"source_relation":"unknown","status":"match"}
"""

# Route pin: routed-away compilation engine pairs are summarized, removing their refusal strata.
PIN_SUMMARY_JSON = """\
{
  "assumed_band": {
    "label": "assumed band — literature method precision; confidence not stated",
    "records": [],
    "refusal_counts": {}
  },
  "capability_matrix": {
    "cross_product_total": 2,
    "engines": [
      "internal-analytical"
    ],
    "n_candidate_observations": 2,
    "n_outside_census_not_emitted": 0,
    "n_quantity_unknown": 0,
    "quantities_no_engine_predicts": [],
    "rows": [
      {
        "covered": true,
        "engine": "internal-analytical",
        "n_observations": 2,
        "quantity": "p_sat",
        "rail": "vapour"
      }
    ],
    "uncovered_pair_total": 0
  },
  "coverage": {
    "callable_observations": 2,
    "coverage": "0.500",
    "n_records": 2,
    "n_refused_records": 1,
    "n_scored_observations": 1,
    "n_scored_records": 1
  },
  "kind": "battery_headline_summary",
  "records": [
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "SiO_evolution",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "alkali_shuttle",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "melt_activity",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "pyrolysis_yield",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "redox",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "residue_composition",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {
        "unflagged": 1
      },
      "iqr_dex": null,
      "median_abs_dex": "0.5",
      "median_dex": "0.5",
      "metric_strata": [
        {
          "iqr": null,
          "label": "dex",
          "median": "0.5",
          "median_abs": "0.5",
          "n": 1,
          "operation": "dex",
          "quantity": "quantity_unknown",
          "rms": "0.5",
          "unit": "dimensionless"
        }
      ],
      "n": 1,
      "n_dex": 1,
      "n_flagged": 0,
      "n_inside_band": 1,
      "n_no_band": 0,
      "n_score_eligible": 1,
      "rail": "vapour",
      "rms_dex": "0.5",
      "sources": [
        {
          "flag_class_counts": {},
          "n_flagged": 0,
          "n_numeric": 1,
          "source_id": "work-1"
        }
      ],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "wall_deposition",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "SiO_evolution",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "alkali_shuttle",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "melt_activity",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "pyrolysis_yield",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "redox",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "residue_composition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": "1",
      "data_scatter_ratio": "0.5",
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": 1.0,
      "match_rate_label": "band membership",
      "median_abs_dex": "0.5",
      "median_dex": "0.5",
      "n": 1,
      "n_candidates": 2,
      "n_eligible_references": 2,
      "n_inside_band": 1,
      "n_match": 1,
      "n_no_band": 0,
      "n_refused": 1,
      "n_score_eligible": 1,
      "n_scored": 1,
      "rail": "vapour",
      "rms_dex": "0.5",
      "rms_over_band": "0.5",
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "wall_deposition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "SiO_evolution",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "alkali_shuttle",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "melt_activity",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "pyrolysis_yield",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "redox",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "residue_composition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "vapour",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_eligible_references": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "wall_deposition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    }
  ],
  "schema_version": "v1",
  "store": {
    "experiments": 7001,
    "hard_issues": 1685,
    "kind": "battery_store_stamp",
    "observations": 186648,
    "queue": 124428,
    "revision": "2043bca61",
    "rows_in": 91208,
    "works": 785
  }
}
"""

# Route pin: routed-away compilation engine pairs now appear once in the direct report summary.
PIN_REPORT = """\
# Battery score report (schema v2.1)

Generated only. Pins are an independent baseline and are never
re-centred from these residuals. Refusals are diagnostics, never hidden.
The measured tier keeps numeric rows visible; its headline reports
n, n inside band, RMS dex, signed and absolute median dex, band width,
RMS/band, and score_eligible separately. The compilation tier is beside
it and is never added to it. Match rate is banded rows only.

Hostname: `test`.

This report measured store `2043bca61`: 91208 rows in, 186648 observations, 785 works, 7001 experiments, queue 124428, 1685 hard issues.

Engines: internal-analytical.

## Live candidate census

Comparison candidates are measured rows admitted or pending.
Admitted is split from pending. Counts are observations, not
residuals, so they are not multiplied by the engine set.
A vapour candidate is only `p_sat`, `p_partial`, or `p_reference`.

| rail | candidates | admitted | pending | points |
|---|---:|---:|---:|---:|
| vapour | 2 | 2 | 0 | 2 |
| melt_activity | 0 | 0 | 0 | 0 |
| thermochemistry | 0 | 0 | 0 | 0 |
| SiO_evolution | 0 | 0 | 0 | 0 |
| pyrolysis_yield | 0 | 0 | 0 | 0 |
| wall_deposition | 0 | 0 | 0 | 0 |
| redox | 0 | 0 | 0 | 0 |
| alkali_shuttle | 0 | 0 | 0 | 0 |
| residue_composition | 0 | 0 | 0 | 0 |

### No headline rail

These quantities are not vapour candidates and not SiO_evolution
candidates. The reason is the refusal, not a borrowed rail.

| reason | candidates | admitted | pending | points |
|---|---:|---:|---:|---:|
| (none) | 0 | 0 | 0 | 0 |

## All-numeric tier

Co-equal descriptive headline (A) over every priced residual, certified and flagged. Every rail × engine cell is printed, n = 0 included. Dex statistics use DEX residuals only; every other metric stays in its own quantity / operation / unit stratum below and is never labelled dex. n certified is the measured (B) membership decision, consumed rather than recomputed. Does not change the measured/certified table.

| rail | engine | n | n certified | n flagged | n dex | median dex | IQR dex | flag classes | sources |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| SiO_evolution | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| alkali_shuttle | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| melt_activity | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| pyrolysis_yield | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| redox | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| residue_composition | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| thermochemistry | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| vapour | internal-analytical | 1 | 1 | 0 | 1 | 0.5 | — | unflagged=1 | work-1(n=1,cert=1,flag=0) |
| wall_deposition | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |

### All-numeric metric strata

| rail | engine | quantity | metric | n | median | IQR |
|---|---|---|---|---:|---:|---:|
| vapour | internal-analytical | quantity_unknown | dex | 1 | 0.5 | — |

## Coverage

Coverage is observations with a numeric residual from at least one
scoring engine, divided by callable comparison candidates. Callable
means the quantity is in at least one scoring engine's declared
capability set. A quantity no declared engine predicts is a
capability-matrix cell, not a refusal, and is outside this ratio.
The candidate population is the live candidate census, not the
residual ledger.

| scored observations | callable observations | coverage |
|---|---:|---:|
| 1 | 2 | 0.500 |

Per-record diagnostic (residual rows, not observations): n_records=2 n_scored=1 n_refused=1.

## Measured tier

Numeric measured rows stay visible. score_eligible and inside-band counts are reported separately. Compilation rows are not in this table.

| rail | engine | population / source relation | n candidates | n refused | n scored | n score eligible | n inside band | RMS dex | median dex | median abs dex | band width dex | RMS/band | n no band | match rate |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SiO_evolution | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| alkali_shuttle | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| melt_activity | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| pyrolysis_yield | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| redox | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| residue_composition | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| thermochemistry | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| vapour | internal-analytical | — | 2 | 1 | 1 | 1 | 1 | 0.5 | 0.5 | 0.5 | 1 | 0.5 | 0 | 1.000 |
| wall_deposition | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |

## Flagged strata

These numeric diagnostics carry an explicit source or apparatus flag. Flagged rows do not derive measurement bands or enter unflagged headlines. Cell-material-inferred residuals are still judged against the band; other flag kinds retain their existing scoring rules. A row carrying both flags appears in both strata.

| stratum | rail | engine | policy_id | policy status | n | median dex | RMS dex |
|---|---|---|---|---|---:|---:|---:|
| (none) | — | — | — | — | 0 | — | — |

## Condition hull per rail × engine

Min–max of the stored (T, P, composition) of the numeric rows behind each per rail × engine statistic: the measured and compilation headlines (the n scored column) and each flagged stratum (its n). T and total pressure are the reference row's own (a compilation cell's printed T); composition is the row's stated composition per amount basis. Nothing is filled in: bracketed counts are rows carrying a value. Total P is the stored total pressure only; a table's standard-state pressure (standard_pressure_Pa) is not a total pressure and is not shown. A compilation row pools every compilation table on that rail × engine, so its T span is not one table's window. Six significant figures. Presentation only; no residual or statistic reads this table.

| tier / stratum | rail | engine | n | T K (n) | total P Pa (n) | composition (n) |
|---|---|---|---:|---|---|---|
| measured | vapour | internal-analytical | 1 | 1156 (1) | — (0) | — (0) |

## Compilation tier

Compilation comparisons: assessed tables and quoted rows stored
under a compilation. Not part of the measured tier and not added
to it. Same-source residuals measure implementation fidelity, not
independent physics; the engine coefficients resolve to this
compilation (JANAF-4th refit versus JANAF; NASA CEA thermo.inp
versus the Glenn coefficient database). Pending admission is
unchanged. Printed uncertainty is the reference observation's
uncertainty (often none on a grid). Otherwise, exact-alignment
source scatter sets a 95% ticket line; membership is tested around
zero: |residual| ≤ ticket line.

| compilation | rail | engine | relation | quantity | unit | n | signed median residual | MAD | bias-to-scatter ratio | max |residual| | median abs residual | RMS residual | band value | band kind | band source n | no-band reason | in-band | out-of-band | n no band | n same-source |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| (none) | (none) | (none) | (none) | (none) | (none) | 0 | — | — | — | — | — | — | — | — | — | — | — | 0 | 0 | 0 | 0 |

| engine | comparisons | refused | numeric | same-source | independent | match same-source | match independent |
|---|---:|---:|---:|---:|---:|---:|---:|
| (none) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

| compilation | quantity | reachable | predicted | refused | median abs residual | uncertainty |
|---|---|---:|---:|---:|---:|---|
| (none) |  | 0 | 0 | 0 | — |  |


### Unrouted compilation engine pairs

Each row summarizes compilation cells skipped before engine scoring.

| engine | quantity | phase | skipped cells |
|---|---|---|---:|
| internal-analytical | S | g | 2 |
| internal-analytical | delta_fG | g | 13 |

## Refusal census

Measured tier only. Compilation refusals are in the compilation table.

| reason | n |
|---|---:|
| `unsupported:fixture_unavailable` | 1 |

Capability-matrix total (candidate observations × engines in this report): 2. Uncovered pairs not emitted as residuals: 0. Diagnostic and duplicate pairs outside the candidate census that this report does not emit: 0.

## Capability matrix

Engine × quantity × rail for comparison candidates. A cell this
engine does not cover is not a residual. The uncovered pair total
is the cross-product records this report's engine list does not
emit. Quantities no declared engine predicts are listed with their
observation count. Residue quantities are outside this table; a
bound residue adapter still emits a residual.

Matrix total (candidate observations × engines): 2. Uncovered pairs: 0. Quantity unknown, not a matrix cell: 0.

| engine | quantity | rail | n observations | covered |
|---|---|---|---:|---|
| internal-analytical | p_sat | vapour | 2 | yes |

### Quantities no engine predicts

| quantity | rail | n observations |
|---|---|---:|
| (none) | — | 0 |

## Admission

score_eligible requires canonical admission admitted. Pending rows
stay in the comparison set as diagnostics (flagged and priced when
numeric, never the empirical headline). The admission rule is unchanged.

| count | n |
|---|---:|
| comparison candidates pending | 0 |
| comparison candidates admitted | 2 |
| residuals with admission_admitted exclusion | 0 |
| residuals that die on admission alone | 0 |
| unique observations that die on admission alone | 0 |

## Notice backlog (certification projects)

| notice kind | n |
|---|---:|
| (none) | 0 |

## Internal-consistency diagnostics

species_rail_differential_ledger and gibbs_battery_residual_ledger
are internal-consistency instruments, not scoring ledgers. They do
not enter either headline tier. Compilation rows are scored in the
compilation tier above, still COMPILATION_ASSESSED, and never added
to the measured tier. Do not validate an engine against a compilation it consumes.

Diagnostic residuals in this file: 0.

## Pin failures

None.

## status_diff vs old scorers

No mapped outcome changes.

All mapped legacy keys have a v2.1 comparison slot.
"""

# Route pin: routed-away compilation engine pairs are summarized in payload output, removing their refusal strata.
PIN_PAYLOAD_SUMMARY_JSON = """\
{
  "assumed_band": {
    "label": "assumed band — literature method precision; confidence not stated",
    "records": [],
    "refusal_counts": {}
  },
  "capability_matrix": {
    "cross_product_total": 2,
    "engines": [
      "internal-analytical"
    ],
    "n_candidate_observations": 2,
    "n_outside_census_not_emitted": 0,
    "n_quantity_unknown": 0,
    "quantities_no_engine_predicts": [],
    "rows": [
      {
        "covered": true,
        "engine": "internal-analytical",
        "n_observations": 2,
        "quantity": "p_sat",
        "rail": "vapour"
      }
    ],
    "uncovered_pair_total": 0
  },
  "coverage": {
    "callable_observations": 2,
    "coverage": "0.500",
    "n_records": 2,
    "n_refused_records": 1,
    "n_scored_observations": 1,
    "n_scored_records": 1
  },
  "kind": "battery_headline_summary",
  "records": [
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "SiO_evolution",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "alkali_shuttle",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "melt_activity",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "pyrolysis_yield",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "redox",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "residue_composition",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {
        "unflagged": 1
      },
      "iqr_dex": null,
      "median_abs_dex": "0.5",
      "median_dex": "0.5",
      "metric_strata": [
        {
          "iqr": null,
          "label": "dex",
          "median": "0.5",
          "median_abs": "0.5",
          "n": 1,
          "operation": "dex",
          "quantity": "quantity_unknown",
          "rms": "0.5",
          "unit": "dimensionless"
        }
      ],
      "n": 1,
      "n_dex": 1,
      "n_flagged": 0,
      "n_inside_band": 1,
      "n_no_band": 0,
      "n_score_eligible": 1,
      "rail": "vapour",
      "rms_dex": "0.5",
      "sources": [
        {
          "flag_class_counts": {},
          "n_flagged": 0,
          "n_numeric": 1,
          "source_id": "work-1"
        }
      ],
      "tier": "all_numeric"
    },
    {
      "engine": "internal-analytical",
      "flag_class_counts": {},
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [],
      "n": 0,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 0,
      "n_no_band": 0,
      "n_score_eligible": 0,
      "rail": "wall_deposition",
      "rms_dex": null,
      "sources": [],
      "tier": "all_numeric"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "SiO_evolution",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "alkali_shuttle",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "melt_activity",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "pyrolysis_yield",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "redox",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "residue_composition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": "1",
      "data_scatter_ratio": "0.5",
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": 1.0,
      "match_rate_label": "band membership",
      "median_abs_dex": "0.5",
      "median_dex": "0.5",
      "n": 1,
      "n_candidates": 2,
      "n_inside_band": 1,
      "n_match": 1,
      "n_no_band": 0,
      "n_refused": 1,
      "n_score_eligible": 1,
      "n_scored": 1,
      "rail": "vapour",
      "rms_dex": "0.5",
      "rms_over_band": "0.5",
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "wall_deposition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "measured"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "SiO_evolution",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "alkali_shuttle",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "melt_activity",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "pyrolysis_yield",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "redox",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "residue_composition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "vapour",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    },
    {
      "band_width_dex": null,
      "data_scatter_ratio": null,
      "decision_strata": [],
      "engine": "internal-analytical",
      "match_rate": null,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 0,
      "n_candidates": 0,
      "n_inside_band": 0,
      "n_match": 0,
      "n_no_band": 0,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 0,
      "rail": "wall_deposition",
      "rms_dex": null,
      "rms_over_band": null,
      "tier": "compilation"
    }
  ],
  "schema_version": "v1",
  "store": {
    "experiments": 7001,
    "hard_issues": 1685,
    "kind": "battery_store_stamp",
    "observations": 186648,
    "queue": 124428,
    "revision": "2043bca61",
    "rows_in": 91208,
    "works": 785
  }
}
"""

# Payload reports count routed-away pairs only for references present in their rows.
PIN_PAYLOAD_REPORT = """\
# Battery score report (schema v2.1)

Generated only. Pins are an independent baseline and are never
re-centred from these residuals. Refusals are diagnostics, never hidden.
Headline accuracy per rail reports numeric n, inside-band n, RMS dex,
median dex, band width, and RMS/band. Measured and compilation tiers
are separate and never summed; match rate is secondary.

Hostname: `test`.

This report measured store `2043bca61`: 91208 rows in, 186648 observations, 785 works, 7001 experiments, queue 124428, 1685 hard issues.

Engines: internal-analytical.

## All-numeric tier

Co-equal descriptive headline (A) over every priced residual, certified and flagged. Every rail × engine cell is printed, n = 0 included. Dex statistics use DEX residuals only; every other metric stays in its own quantity / operation / unit stratum below and is never labelled dex. n certified is the measured (B) membership decision, consumed rather than recomputed. Does not change the measured/certified table.

| rail | engine | n | n certified | n flagged | n dex | median dex | IQR dex | flag classes | sources |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| SiO_evolution | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| alkali_shuttle | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| melt_activity | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| pyrolysis_yield | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| redox | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| residue_composition | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| thermochemistry | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |
| vapour | internal-analytical | 1 | 1 | 0 | 1 | 0.5 | — | unflagged=1 | work-1(n=1,cert=1,flag=0) |
| wall_deposition | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |

### All-numeric metric strata

| rail | engine | quantity | metric | n | median | IQR |
|---|---|---|---|---:|---:|---:|
| vapour | internal-analytical | quantity_unknown | dex | 1 | 0.5 | — |

## Coverage

Coverage is observations with a numeric residual from at least one
scoring engine, divided by callable comparison candidates. Callable
means the quantity is in at least one scoring engine's declared
capability set. A quantity no declared engine predicts is a
capability-matrix cell, not a refusal, and is outside this ratio.
The candidate population is the live candidate census, not the
residual ledger.

| scored observations | callable observations | coverage |
|---|---:|---:|
| 1 | 2 | 0.500 |

Per-record diagnostic (residual rows, not observations): n_records=2 n_scored=1 n_refused=1.

## Measured tier

Numeric measured rows stay visible. score_eligible and inside-band counts are reported separately.

| rail | engine | population / source relation | n candidates | n refused | n scored | n score eligible | n inside band | RMS dex | median dex | median abs dex | band width dex | RMS/band | n no band | match rate |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| SiO_evolution | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| alkali_shuttle | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| melt_activity | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| pyrolysis_yield | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| redox | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| residue_composition | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| thermochemistry | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |
| vapour | internal-analytical | — | 2 | 1 | 1 | 1 | 1 | 0.5 | 0.5 | 0.5 | 1 | 0.5 | 0 | 1.000 |
| wall_deposition | internal-analytical | — | 0 | 0 | 0 | 0 | 0 | — | — | — | — | — | 0 | — |

## Flagged strata

Flagged numeric diagnostics are excluded from the measured headline, bands, and band statistics.

| stratum | rail | engine | policy_id | policy status | n | median dex | RMS dex |
|---|---|---|---|---|---:|---:|---:|
| (none) | — | — | — | — | 0 | — | — |

## Condition hull per rail × engine

Min–max of the stored (T, P, composition) of the numeric rows behind each per rail × engine statistic: the measured and compilation headlines (the n scored column) and each flagged stratum (its n). T and total pressure are the reference row's own (a compilation cell's printed T); composition is the row's stated composition per amount basis. Nothing is filled in: bracketed counts are rows carrying a value. Total P is the stored total pressure only; a table's standard-state pressure (standard_pressure_Pa) is not a total pressure and is not shown. A compilation row pools every compilation table on that rail × engine, so its T span is not one table's window. Six significant figures. Presentation only; no residual or statistic reads this table.

| tier / stratum | rail | engine | n | T K (n) | total P Pa (n) | composition (n) |
|---|---|---|---:|---|---|---|
| measured | vapour | internal-analytical | 1 | 1156 (1) | — (0) | — (0) |

## Compilation tier

Compilation comparisons: assessed tables and quoted rows stored
under a compilation. Not part of the measured tier and not added
to it. Same-source residuals measure implementation fidelity, not
independent physics; the engine coefficients resolve to this
compilation (JANAF-4th refit versus JANAF; NASA CEA thermo.inp
versus the Glenn coefficient database). Pending admission is
unchanged. Printed uncertainty is the reference observation's
uncertainty (often none on a grid). Otherwise, exact-alignment
source scatter sets a 95% ticket line; membership is tested around
zero: |residual| ≤ ticket line.

| compilation | rail | engine | relation | quantity | unit | n | signed median residual | MAD | bias-to-scatter ratio | max |residual| | median abs residual | RMS residual | band value | band kind | band source n | no-band reason | in-band | out-of-band | n no band | n same-source |
|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|
| (none) | (none) | (none) | (none) | (none) | (none) | 0 | — | — | — | — | — | — | — | — | — | — | — | 0 | 0 | 0 | 0 |

| engine | comparisons | refused | numeric | same-source | independent | match same-source | match independent |
|---|---:|---:|---:|---:|---:|---:|---:|
| (none) | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

| compilation | quantity | reachable | predicted | refused | median abs residual | uncertainty |
|---|---|---:|---:|---:|---:|---|
| (none) |  | 0 | 0 | 0 | — |  |


## Refusal census

Measured tier only. Compilation refusals are in the compilation table.

| reason | n |
|---|---:|
| `unsupported:fixture_unavailable` | 1 |

Capability-matrix total (candidate observations × engines in this report): 2. Uncovered pairs not emitted as residuals: 0. Diagnostic and duplicate pairs outside the candidate census that this report does not emit: 0.

## Capability matrix

Engine × quantity × rail for comparison candidates. A cell this
engine does not cover is not a residual. The uncovered pair total
is the cross-product records this report's engine list does not
emit. Quantities no declared engine predicts are listed with their
observation count. Residue quantities are outside this table; a
bound residue adapter still emits a residual.

Matrix total (candidate observations × engines): 2. Uncovered pairs: 0. Quantity unknown, not a matrix cell: 0.

| engine | quantity | rail | n observations | covered |
|---|---|---|---:|---|
| internal-analytical | p_sat | vapour | 2 | yes |

### Quantities no engine predicts

| quantity | rail | n observations |
|---|---|---:|
| (none) | — | 0 |

## Admission

score_eligible requires canonical admission admitted. Pending rows
stay in the comparison set as diagnostics (flagged and priced when
numeric, never the empirical headline). The admission rule is unchanged.

| count | n |
|---|---:|
| residuals with admission_admitted exclusion | 0 |
| residuals that die on admission alone | 0 |
| unique observations that die on admission alone | 0 |

## Notice backlog (certification projects)

| notice kind | n |
|---|---:|
| (none) | 0 |

## Internal-consistency / compilation diagnostics

species_rail_differential_ledger and gibbs_battery_residual_ledger
are internal-consistency instruments, not scoring ledgers. Compilation
observations are engine reference inputs. Neither population enters
the empirical headline. Do not validate an engine against a compilation it consumes.

Diagnostic residuals in this file: 0.

## Pin failures

None.

## status_diff vs old scorers

No mapped outcome changes.

All mapped legacy keys have a v2.1 comparison slot.
"""

PIN_ASSUMED_BAND_SECTION = """\
## Assumed band

assumed band — literature method precision; confidence not stated

Separate diagnostics; existing headline counts and bands are unchanged.

| rail | engine | quantity | metric | unit | class | evidence | n | observations | inside band | eligible | citation |
|---|---|---|---|---|---|---|---:|---:|---:|---:|---|
| (none) | — | — | — | — | — | — | 0 | 0 | 0 | 0 | — |

Applicability / conversion refusals:

None.
"""
PIN_REPORT += "\n" + PIN_ASSUMED_BAND_SECTION
PIN_PAYLOAD_REPORT += "\n" + PIN_ASSUMED_BAND_SECTION

PIN_ELEMENT_RETENTION_SECTION = """\
## Element retention diagnostics

Observed loss is 1 − FM; printed uncertainty is shown without rescaling.
Loss bins are reporting diagnostics and do not affect residual weights.

| observation | engine | status | FM observed | observed loss | printed uncertainty | residual (dex) |
|---|---|---|---:|---:|---|---:|
| fixture-element-retention | internal-analytical | match | 0.995 | 0.005 | not printed | 0.5 |

### Observed loss bins

| observed loss | n rows |
|---|---:|
| negative (FM > 1) | 0 |
| 0–1% | 1 |
| >1–10% | 0 |
| >10–50% | 0 |
| >50% | 0 |
"""

EMPTY_LEDGER_SENTENCES = (
    "No engine residuals were regenerated for this store revision.",
    "Pins were not compared; no residuals ledger was regenerated for this store revision.",
    "status_diff was not run; no residuals ledger was regenerated for this store revision.",
)
PIN_FAILURE_TRUNCATION_ROW = "| … | 1 more | | | |"


def _score_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    residual_delta: Decimal | None = None,
    include_retention: bool = False,
    pin_failures: tuple[dict[str, str], ...] = (),
) -> dict[str, str]:
    context, engine, fixture_residuals, fixture_candidates = _streaming_score_fixture()
    if include_retention:
        reference = context.observations["fixture-measured-vapour"]
        retention_id = "fixture-element-retention"
        retention = replace(
            reference,
            observation_id=retention_id,
            identity=replace(
                reference.identity,
                quantity=Quantity.ELEMENT_RETENTION_RATIO,
                subtype=State.of("concentration_retention"),
            ),
            value=Value.point_of(Decimal("0.995")),
        )
        context = replace(
            context,
            observations={**context.observations, retention_id: retention},
        )
        fixture_residuals[retention_id] = replace(
            fixture_residuals["fixture-measured-vapour"],
            key=f"fixture::{retention_id}::{engine.value}",
            reference=retention_id,
            rail=Rail.VAPOUR,
            candidate=None,
            candidate_request=CandidateRequest(
                retention.experiment_id,
                Quantity.ELEMENT_RETENTION_RATIO,
                engine,
                engine.value,
            ),
        )
    if residual_delta is not None:
        key = "fixture-measured-vapour"
        row = fixture_residuals[key]
        assert row.numeric is not None
        fixture_residuals[key] = replace(
            row,
            numeric=replace(row.numeric, value=row.numeric.value + residual_delta),
        )

    def compile_stub(reference, engine, **_kwargs):
        residual = fixture_residuals[reference.observation_id]
        candidate = fixture_candidates.get(residual.candidate or "")
        return residual, candidate

    monkeypatch.setattr(score_mod, "_compile_residual_with_lineage", compile_stub)
    monkeypatch.setattr(
        score_mod,
        "derive_store_stamp",
        lambda _root=None: dict(FIXTURE_STORE_STAMP),
    )
    engines = (engine,)
    root = score_mod.REPO_ROOT
    # Element retention is outside every declared set, so the production
    # gate does not emit it. This pin formats a residual the caller asked
    # to score. An injected predictor is that gate's exception.
    injected_predict = None
    if include_retention:
        def injected_predict(*_args, **_kwargs):
            raise AssertionError("compile stub owns this score")
    residuals, candidates = score_mod.score_store(
        context, engines=engines, predict=injected_predict
    )
    direct_jsonl_path = tmp_path / "direct-residuals.jsonl"
    score_mod.write_residuals_jsonl(
        residuals, candidates, direct_jsonl_path, root=root
    )
    streamed_path = tmp_path / "residuals.jsonl"
    _written, metadata = score_mod._score_store_to_jsonl(
        context,
        streamed_path,
        root=root,
        engines=engines,
        predict=injected_predict,
    )
    partial = streamed_path.with_name(streamed_path.name + ".partial")
    direct_summary = tmp_path / "direct-summary.json"
    streamed_summary = tmp_path / "streamed-summary.json"
    score_mod.write_headline_summary_json(
        residuals, direct_summary, context=context, engines=engines, root=root
    )
    stamp = score_mod.derive_store_stamp(root)
    assert metadata.aggregate is not None
    score_mod._write_headline_summary_from_accumulator_json(
        metadata.aggregate, streamed_summary, store_stamp=stamp
    )
    direct_report = score_mod.render_score_report(
        residuals,
        context=context,
        engines=engines,
        pin_failures=pin_failures,
        root=root,
    )
    streamed_report = score_mod._render_score_report_from_payloads_legacy(
        score_mod._ResidualJsonlRows(partial, metadata=metadata),
        context=context,
        engines=engines,
        pin_failures=pin_failures,
        store_stamp=stamp,
        _aggregate=metadata.aggregate,
    )
    payload_rows = score_mod._ResidualJsonlRows(partial)
    payload_summary = tmp_path / "payload-summary.json"
    score_mod.write_headline_summary_from_payloads_json(
        payload_rows,
        payload_summary,
        engines=engines,
        observations=context.observations,
        origins=context.origins,
        store_stamp=stamp,
    )
    payload_report = score_mod.render_score_report_from_payloads(
        payload_rows,
        engines=engines,
        hostname=context.hostname,
        pin_failures=pin_failures,
        store_stamp=stamp,
        observations=context.observations,
        origins=context.origins,
    )
    return {
        "direct_jsonl": direct_jsonl_path.read_text(encoding="utf-8"),
        "streamed_jsonl": partial.read_text(encoding="utf-8"),
        "direct_summary": direct_summary.read_text(encoding="utf-8"),
        "streamed_summary": streamed_summary.read_text(encoding="utf-8"),
        "direct_report": direct_report,
        "streamed_report": streamed_report,
        "payload_summary": payload_summary.read_text(encoding="utf-8"),
        "payload_report": payload_report,
    }


def test_predictor_sentinel_and_replacement_keyword_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from simulator.battery.enums import Engine, Fo2Mode

    context, _engine, _fixture_residuals, _fixture_candidates = (
        _streaming_score_fixture()
    )
    reference = context.observations["fixture-measured-vapour"]
    experiment = context.experiments[reference.experiment_id]
    handles: dict[str, object] = {}
    bench = object()
    siblings = {"SiO2": Decimal("1")}
    prediction = _predict(Decimal("5"), reference.identity)
    calls: list[tuple[object, ...]] = []

    original_predictor = score_mod.predict_with_engine
    assert score_mod._BUILTIN_SCORE_PREDICTOR is original_predictor

    def builtin_predictor(
        engine,
        observation,
        *,
        handles,
        experiment,
        bench,
        fo2_mode,
        phase_siblings,
        phase_sibling_refusal,
        isolated,
    ):
        calls.append(
            (
                engine,
                observation,
                handles,
                experiment,
                bench,
                fo2_mode,
                phase_siblings,
                phase_sibling_refusal,
                isolated,
            )
        )
        return prediction

    monkeypatch.setattr(score_mod, "predict_with_engine", builtin_predictor)
    monkeypatch.setattr(score_mod, "_BUILTIN_SCORE_PREDICTOR", builtin_predictor)
    produced = score_mod._score_predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        reference,
        handles=handles,
        experiment=experiment,
        bench=bench,
        fo2_mode=Fo2Mode.SHARED_LADDER,
        phase_siblings=siblings,
        phase_sibling_refusal="fixture refusal",
    )

    assert produced is prediction
    assert calls == [
        (
            Engine.INTERNAL_ANALYTICAL,
            reference,
            handles,
            experiment,
            bench,
            Fo2Mode.SHARED_LADDER,
            siblings,
            "fixture refusal",
            True,
        )
    ]

    calls.clear()

    def replacement_predictor(engine, observation, *, handles, experiment):
        calls.append((engine, observation, handles, experiment))
        return prediction

    monkeypatch.setattr(score_mod, "_BUILTIN_SCORE_PREDICTOR", original_predictor)
    monkeypatch.setattr(score_mod, "predict_with_engine", replacement_predictor)
    produced = score_mod._score_predict_with_engine(
        Engine.INTERNAL_ANALYTICAL,
        reference,
        handles=handles,
        experiment=experiment,
        bench=bench,
        fo2_mode=Fo2Mode.SHARED_LADDER,
        phase_siblings=siblings,
        phase_sibling_refusal="fixture refusal",
    )

    assert produced is prediction
    assert calls == [(Engine.INTERNAL_ANALYTICAL, reference, handles, experiment)]


def test_score_store_calls_injected_predictor_once_and_pins_its_result() -> None:
    context, engine, _fixture_residuals, _fixture_candidates = (
        _streaming_score_fixture()
    )
    reference = context.observations["fixture-measured-vapour"]
    context = replace(context, observations={reference.observation_id: reference})
    experiment = context.experiments[reference.experiment_id]
    calls: list[tuple[object, ...]] = []

    def predict(engine, observation, *, handles, experiment):
        calls.append((engine, observation, handles, experiment))
        return _predict(Decimal("5"), observation.identity)

    residuals, _candidates = score_mod.score_store(
        context,
        engines=(engine,),
        include_diagnostics=False,
        predict=predict,
    )

    assert calls == [(engine, reference, {}, experiment)]
    assert len(residuals) == 1
    numeric = residuals[0].numeric
    assert numeric is not None
    assert numeric.value == Decimal("1")


def test_direct_and_streamed_score_outputs_match_byte_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct and streamed writers share one pin; the serialized-row writer has its own."""

    got = _score_outputs(tmp_path, monkeypatch)
    expected_counts = _expected_fixture_certified_counts()
    for key in ("direct_summary", "streamed_summary", "payload_summary"):
        _assert_certified_counts_match_fixture(
            _summary_certified_counts(got[key]), expected_counts
        )
    for key in ("direct_report", "streamed_report", "payload_report"):
        _assert_certified_counts_match_fixture(
            _report_certified_counts(got[key]), expected_counts
        )
    assert got["direct_jsonl"].encode("utf-8") == got["streamed_jsonl"].encode(
        "utf-8"
    )
    assert _without_certified_counts(got["direct_jsonl"]) == PIN_RESIDUAL_JSONL
    assert got["direct_summary"].encode("utf-8") == got["streamed_summary"].encode(
        "utf-8"
    )
    assert _without_certified_counts(got["direct_summary"]) == PIN_SUMMARY_JSON
    assert got["direct_report"].encode("utf-8") == got["streamed_report"].encode(
        "utf-8"
    )
    assert _without_certified_counts(got["direct_report"]) == _without_certified_counts(
        PIN_REPORT
    )
    assert _without_certified_counts(got["payload_summary"]) == PIN_PAYLOAD_SUMMARY_JSON
    assert _without_certified_counts(got["payload_report"]) == _without_certified_counts(
        PIN_PAYLOAD_REPORT
    )
    assert (
        _without_certified_counts(got["direct_jsonl"]).encode("utf-8")
        == PIN_RESIDUAL_JSONL.encode("utf-8")
    )
    assert (
        _without_certified_counts(got["direct_report"]).encode("utf-8")
        == _without_certified_counts(PIN_REPORT).encode("utf-8")
    )


def test_retained_nasa_liquid_thermo_rows_keep_numeric_output_and_bands(
    tmp_path: Path,
) -> None:
    """All output writers retain real NASA-9 liquid Cp score rows and bands."""

    work = F.work()
    experiment = F.tabulation_experiment(work_id=work.work_id)
    identity = replace(
        F.o2_identity(Decimal("298.15")),
        quantity=Quantity.CP,
        species=Species("H2O", Phase.L),
        reaction=State.not_applicable("pure standard-state thermo"),
        formation_elements=State.not_applicable("not a formation quantity"),
    )
    observations = tuple(
        F.observation(
            f"output-pin-h2o-liquid-cp-{index}",
            experiment.experiment_id,
            identity,
            Decimal(value),
            evidence=EvidenceClass.COMPILATION_ASSESSED,
            admission=AdmissionStatus.PENDING,
            source_id=f"output-pin-source-{index}",
        )
        for index, value in enumerate(("75", "75.3", "76"))
    )
    context = score_mod.ScoreContext(
        works={work.work_id: work},
        experiments={experiment.experiment_id: experiment},
        observations={row.observation_id: row for row in observations},
        origins={
            row.observation_id: f"compilations-janaf/{row.observation_id}.yaml"
            for row in observations
        },
        extract_review={row.source_id: None for row in observations},
        hostname="test",
    )
    bands, _band_records, _no_band_reasons = score_mod._source_band_catalog(
        context, {}
    )
    assert len(bands) == 1
    expected_band = next(iter(bands.values()))
    engines = (Engine.NASA_CEA_9,)
    residuals, candidates = score_mod.score_store(context, engines=engines)
    expected_values = {
        observations[0].observation_id: Decimal("0.35055745293062"),
        observations[1].observation_id: Decimal("0.05055745293062"),
        observations[2].observation_id: Decimal("-0.64944254706938"),
    }
    by_reference = {row.reference: row for row in residuals}
    assert set(by_reference) == set(expected_values)
    for reference, expected_value in expected_values.items():
        residual = by_reference[reference]
        assert residual.numeric is not None
        assert residual.numeric.value == expected_value
        assert residual.numeric.unit == "J_per_declared_mol_basis_per_K"
        assert residual.numeric.decision_band is not None
        assert residual.numeric.decision_band.rule.startswith("derived_scatter:")
        assert residual.status is ResidualStatus.MATCH
        assert score_mod.flagged_strata(residual.notices) == ()

    root = score_mod.REPO_ROOT
    direct_jsonl = tmp_path / "retained-direct.jsonl"
    score_mod.write_residuals_jsonl(residuals, candidates, direct_jsonl, root=root)
    streamed_jsonl = tmp_path / "retained-streamed.jsonl"
    written, metadata = score_mod._score_store_to_jsonl(
        context, streamed_jsonl, root=root, engines=engines
    )
    partial = streamed_jsonl.with_name(streamed_jsonl.name + ".partial")
    assert written == 3
    assert partial.read_bytes() == direct_jsonl.read_bytes()
    jsonl_rows = [
        json.loads(line)
        for line in partial.read_text(encoding="utf-8").splitlines()
    ]
    numeric_rows = {
        row["reference"]: row
        for row in jsonl_rows
        if row.get("reference") in expected_values
    }
    assert set(numeric_rows) == set(expected_values)
    for reference, expected_value in expected_values.items():
        row = numeric_rows[reference]
        numeric = row["numeric"]
        assert numeric["value"] == str(expected_value)
        assert numeric["unit"] == "J_per_declared_mol_basis_per_K"
        assert numeric["operation"] == "absolute"
        assert numeric["decision_band"]["band_basis"] == "derived_scatter"
        assert numeric["decision_band"]["unit"] == "J_per_declared_mol_basis_per_K"
        assert numeric["decision_band"]["value"] == str(expected_band.value)
        assert row["status"] == ResidualStatus.MATCH.value

    stamp = score_mod.derive_store_stamp(root)
    direct_summary = tmp_path / "retained-direct-summary.json"
    streamed_summary = tmp_path / "retained-streamed-summary.json"
    score_mod.write_headline_summary_json(
        residuals, direct_summary, context=context, engines=engines, root=root
    )
    assert metadata.aggregate is not None
    score_mod._write_headline_summary_from_accumulator_json(
        metadata.aggregate, streamed_summary, store_stamp=stamp
    )
    assert direct_summary.read_bytes() == streamed_summary.read_bytes()
    direct_report = score_mod.render_score_report(
        residuals, context=context, engines=engines, root=root
    )
    streamed_report = score_mod._render_score_report_from_payloads_legacy(
        score_mod._ResidualJsonlRows(partial, metadata=metadata),
        context=context,
        engines=engines,
        store_stamp=stamp,
        _aggregate=metadata.aggregate,
    )
    assert streamed_report == direct_report
    payload_rows = score_mod._ResidualJsonlRows(partial)
    payload_summary = tmp_path / "retained-payload-summary.json"
    score_mod.write_headline_summary_from_payloads_json(
        payload_rows,
        payload_summary,
        engines=engines,
        observations=context.observations,
        origins=context.origins,
        store_stamp=stamp,
    )
    payload_report = score_mod.render_score_report_from_payloads(
        payload_rows,
        engines=engines,
        hostname=context.hostname,
        store_stamp=stamp,
        observations=context.observations,
        origins=context.origins,
    )
    for output in (
        direct_report,
        streamed_report,
        payload_report,
    ):
        assert "0.05055745293062" in output
        assert "nasa_cea_9" in output
        assert "derived_scatter" in output
        assert str(expected_band.value) in output
    payload_summary_doc = json.loads(payload_summary.read_text(encoding="utf-8"))
    direct_summary_doc = json.loads(direct_summary.read_text(encoding="utf-8"))
    for summary in (payload_summary_doc, direct_summary_doc):
        thermo_records = [
            record
            for record in summary["records"]
            if record.get("tier") == "all_numeric"
            and record.get("rail") == Rail.THERMOCHEMISTRY.value
            and record.get("engine") == Engine.NASA_CEA_9.value
        ]
        assert thermo_records
        assert sum(record["n"] for record in thermo_records) == 3


def test_corrupt_certified_aggregates_miss_fixture_assertions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = _score_outputs(tmp_path, monkeypatch)
    payload = json.loads(got["direct_summary"])
    for record in payload["records"]:
        if record.get("tier") != "all_numeric":
            continue
        record["n_certified"] = 999
        for source in record["sources"]:
            source["n_certified"] = 999

    expected_counts = _expected_fixture_certified_counts()
    with pytest.raises(AssertionError, match="certified counts differ"):
        _assert_certified_counts_match_fixture(
            _summary_certified_counts(json.dumps(payload)), expected_counts
        )


def test_changed_residual_value_misses_the_output_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One changed residual numeric value makes every pinned writer miss."""

    got = _score_outputs(tmp_path, monkeypatch, residual_delta=Decimal("1"))
    assert got["direct_jsonl"] == got["streamed_jsonl"]
    assert _without_certified_counts(got["direct_jsonl"]) != PIN_RESIDUAL_JSONL
    assert got["direct_summary"] == got["streamed_summary"]
    assert _without_certified_counts(got["direct_summary"]) != PIN_SUMMARY_JSON
    assert got["direct_report"] == got["streamed_report"]
    assert _without_certified_counts(got["direct_report"]) != _without_certified_counts(
        PIN_REPORT
    )
    assert _without_certified_counts(got["payload_summary"]) != PIN_PAYLOAD_SUMMARY_JSON
    assert got["payload_summary"] != PIN_PAYLOAD_SUMMARY_JSON
    assert _without_certified_counts(got["payload_report"]) != _without_certified_counts(PIN_PAYLOAD_REPORT)


def test_element_retention_report_aggregation_is_byte_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = _score_outputs(tmp_path, monkeypatch, include_retention=True)
    direct = got["direct_report"].split("## Element retention diagnostics", 1)[1]
    payload = got["payload_report"].split("## Element retention diagnostics", 1)[1]
    expected = PIN_ELEMENT_RETENTION_SECTION.split(
        "## Element retention diagnostics", 1
    )[1]
    assert direct == payload == expected


def test_empty_ledger_sentences_are_pinned_on_live_and_streamed_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, engine, _fixture_residuals, _fixture_candidates = _streaming_score_fixture()
    stamp = score_mod.derive_store_stamp(score_mod.REPO_ROOT)
    direct = score_mod.render_score_report(
        (), context=context, engines=(engine,), root=score_mod.REPO_ROOT
    )
    streamed = score_mod._render_score_report_from_payloads_legacy(
        (), context=context, engines=(engine,), store_stamp=stamp
    )
    assert direct == streamed
    assert all(sentence in direct for sentence in EMPTY_LEDGER_SENTENCES)


def test_pin_failure_truncation_row_is_pinned_on_all_report_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = tuple(
        {
            "key": f"fixture-pin-{index:02d}",
            "reason": "outside_pin_band",
            "live": "1",
            "centre": "0",
            "pin_band": "0.1",
        }
        for index in range(51)
    )
    got = _score_outputs(tmp_path, monkeypatch, pin_failures=failures)
    for key in ("direct_report", "streamed_report", "payload_report"):
        assert PIN_FAILURE_TRUNCATION_ROW in got[key]


def test_changed_report_heading_misses_the_output_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import simulator.battery.score_report as report_mod

    monkeypatch.setattr(report_mod, "REPORT_HEADING", HEADING + " changed")
    got = _score_outputs(tmp_path, monkeypatch)
    assert _without_certified_counts(got["direct_report"]) != _without_certified_counts(
        PIN_REPORT
    )
    assert _without_certified_counts(got["streamed_report"]) != _without_certified_counts(
        PIN_REPORT
    )
    assert _without_certified_counts(got["payload_report"]) != _without_certified_counts(
        PIN_PAYLOAD_REPORT
    )


def test_unknown_candidate_request_quantity_is_serialized_as_null() -> None:
    residual = F.residual(
        "unknown-quantity::none::nasa-cea-9",
        "unknown-quantity",
        quantity=Quantity.P_SAT,
    )
    residual = replace(
        residual,
        candidate_request=CandidateRequest(
            "exp-1", None, Engine.NASA_CEA_9, "cea"
        ),
    )

    plain = score_mod.residual_to_plain(residual)
    assert plain["candidate_request"]["quantity"] is None
    assert "candidate_observation" not in plain
    payload = json.loads(score_mod.dumps_residual_line(residual))

    assert payload["candidate_request"] == {
        "channel": "cea",
        "engine": Engine.NASA_CEA_9.value,
        "experiment_id": "exp-1",
        "quantity": None,
    }


@pytest.mark.parametrize("string_version", [False, True])
def test_transitional_missing_pressure_notice_is_pinned(string_version: bool) -> None:
    notice = score_mod._assumption_notice(
        Quantity.P_PARTIAL,
        "observation does not carry total_pressure_Pa",
        1e-6,
    )
    assert notice.kind is NoticeKind.METHOD_IMPLIED
    assert notice.policy is not None
    assert type(notice.policy.version) is int
    expected_version = "1" if string_version else 1
    if string_version:
        notice = replace(notice, policy=replace(notice.policy, version=expected_version))
    residual = replace(
        F.residual(
            "assumed-pressure::partial",
            "assumed-pressure",
            quantity=Quantity.P_PARTIAL,
        ),
        notices=(notice,),
    )
    payload = json.loads(score_mod.dumps_residual_line(residual))

    assert payload["notices"][0]["kind"] == NoticeKind.METHOD_IMPLIED.value
    assert payload["notices"][0]["policy"] == {
        "id": "DP-101",
        "version": expected_version,
        "status": "rejected",
        "value": "1e-6",
        "units": "bar",
    }
    from simulator.battery.migrate import _notice_from_plain

    decoded = _notice_from_plain(payload["notices"][0])
    assert decoded == notice
    assert decoded.policy is not None
    assert type(decoded.policy.version) is type(expected_version)

    payload["notices"][0]["policy"]["version"] = True
    with pytest.raises(TypeError, match="notice policy version"):
        _notice_from_plain(payload["notices"][0])
