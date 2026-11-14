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

from simulator.battery.enums import Quantity, Rail
from simulator.battery.identity import State
from simulator.battery.records import CandidateRequest, Value
import simulator.battery.score as score_mod
from tests.battery.test_score import _streaming_score_fixture

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
    """Keep output byte pins independent of the changing certified-row count."""

    lines = _CERTIFIED_COUNT_FIELD.sub("", text).splitlines(keepends=True)
    output: list[str] = []
    certified_column: int | None = None
    for line in lines:
        if not line.startswith("|"):
            certified_column = None
            output.append(line)
            continue
        cells = line.split("|")
        if certified_column is None:
            certified_column = next(
                (
                    index
                    for index, cell in enumerate(cells)
                    if cell.strip() == "n certified"
                ),
                None,
            )
        if certified_column is not None:
            del cells[certified_column]
        output.append("|".join(cells))
    return "".join(output)


def _expected_fixture_certified_counts() -> tuple[
    dict[tuple[str, str], int], dict[tuple[str, str, str], int]
]:
    context, engine, fixture_residuals, _candidates = _streaming_score_fixture()
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
        if residual.numeric is not None and residual.rail is not None
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


PIN_RESIDUAL_JSONL = """\
{"experiments":7001,"hard_issues":1685,"kind":"battery_store_stamp","observations":186648,"queue":124428,"revision":"2043bca61","rows_in":91208,"works":785}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-00::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"0"},"rail":"thermochemistry","reference":"fixture-derived-00","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-01::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"0"},"rail":"thermochemistry","reference":"fixture-derived-01","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-02::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"0"},"rail":"thermochemistry","reference":"fixture-derived-02","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-03::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"1"},"rail":"thermochemistry","reference":"fixture-derived-03","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-04::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"1"},"rail":"thermochemistry","reference":"fixture-derived-04","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-05::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"2"},"rail":"thermochemistry","reference":"fixture-derived-05","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-06::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"2"},"rail":"thermochemistry","reference":"fixture-derived-06","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-07::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"3"},"rail":"thermochemistry","reference":"fixture-derived-07","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-08::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"4"},"rail":"thermochemistry","reference":"fixture-derived-08","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-derived-09::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"50"},"rail":"thermochemistry","reference":"fixture-derived-09","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-no-band::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"7"},"rail":"thermochemistry","reference":"fixture-no-band","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-non-dex-dimensionless-band::internal-analytical","notices":[],"numeric":{"decision_band":{"band_basis":"derived_scatter","rule":"printed_1sigma: source-printed uncertainty","unit":"dimensionless","value":"0.231"},"operation":"absolute","unit":"dimensionless","value":"3"},"rail":"thermochemistry","reference":"fixture-non-dex-dimensionless-band","score_eligible":false,"source_relation":"unknown","status":"match"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-printed::internal-analytical","notices":[],"numeric":{"decision_band":{"band_basis":"derived_scatter","rule":"printed_1sigma: source-printed uncertainty","unit":"kJ_per_declared_mol_basis","value":"2"},"operation":"absolute","unit":"kJ_per_declared_mol_basis","value":"1"},"rail":"thermochemistry","reference":"fixture-printed","score_eligible":false,"source_relation":"unknown","status":"match"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-scaled-max-first::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"J_per_declared_mol_basis_per_K","value":"0.24"},"rail":"thermochemistry","reference":"fixture-scaled-max-first","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-scaled-max-second::internal-analytical","notices":[],"numeric":{"operation":"absolute","unit":"J_per_declared_mol_basis_per_K","value":"-0.24"},"rail":"thermochemistry","reference":"fixture-scaled-max-second","score_eligible":false,"source_relation":"unknown","status":"no_band"}
{"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-measured-refusal::internal-analytical","notices":[],"rail":"vapour","reference":"fixture-measured-refusal","refusal":{"check_refs":[],"detail":{"reason":"fixture_unavailable"},"reason":"unsupported"},"score_eligible":false,"source_relation":"unknown","status":"refused"}
{"candidate":"fixture-vapour-candidate","candidate_observation":{"admission":{"decided_by":{"date":"2026-09-13","evidence":{"page":1,"table":"I"},"worker":"test"},"reason":"canonical","status":"admitted"},"derivation":{"inputs":["raw-1"],"output_unit":"Pa","parameters":[],"references":[],"relation":"model"},"derived_from":["raw-1"],"evidence":{"class":{"tag":"value","value":"model_derived"},"model":"gibbs-duhem"},"experiment_id":"exp-1","identity":{"composition":{"reason":"pure-component p_sat","tag":"not_applicable"},"exposure":{"reason":"pure-component p_sat","tag":"not_applicable"},"fO2_Pa":{"reason":"pure-component p_sat","tag":"not_applicable"},"formation_elements":{"reason":"pure-component p_sat","tag":"not_applicable"},"per":{"reason":"pure-component p_sat","tag":"not_applicable"},"quantity":{"tag":"value","value":"p_sat"},"reaction":{"reason":"pure-component p_sat","tag":"not_applicable"},"reference_state":{"reason":"pure-component p_sat","tag":"not_applicable"},"reservoir":{"tag":"value","value":{"charge":{"tag":"value","value":0},"formula":"Na","phase":{"tag":"value","value":"l"}}},"sample_mass_kg":{"reason":"pure-component p_sat","tag":"not_applicable"},"species":{"charge":{"tag":"value","value":0},"formula":"Na","phase":{"tag":"value","value":"g"}},"standard_pressure_Pa":{"reason":"pure-component p_sat","tag":"not_applicable"},"subtype":{"reason":"pure-component p_sat","tag":"not_applicable"},"sweep_gas":{"reason":"pure-component p_sat","tag":"not_applicable"},"temperature_K":{"tag":"value","value":"1156"},"total_pressure_Pa":{"reason":"pure-component p_sat","tag":"not_applicable"},"wall":{"reason":"pure-component p_sat","tag":"not_applicable"}},"locator":{"page":1,"table":"I"},"notices":[],"observation_id":"fixture-vapour-candidate","point_conditions":{"temperature_K":{"locator":{"page":1,"table":"I"},"state":{"tag":"value","value":"1156"}}},"read_from":"pdf-1","source_id":"work-1","uncertainty":{"kind":"none"},"value":{"approximate":false,"kind":"point","point":"0.6"}},"exclusions":[],"execution":{"state":"not_probed"},"key":"fixture::fixture-measured-vapour::internal-analytical","notices":[],"numeric":{"decision_band":{"band_basis":"derived_scatter","rule":"measured fixture band","unit":"dimensionless","value":"1"},"operation":"dex","unit":"dimensionless","value":"0.5"},"rail":"vapour","reference":"fixture-measured-vapour","score_eligible":true,"source_relation":"unknown","status":"match"}
"""

PIN_SUMMARY_JSON = """\
{
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
      "flag_class_counts": {
        "unflagged": 15
      },
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [
        {
          "iqr": "0.24000",
          "label": "absolute (J_per_declared_mol_basis_per_K)",
          "median": "0.000",
          "median_abs": "0.240",
          "n": 2,
          "operation": "absolute",
          "quantity": "quantity_unknown",
          "rms": "0.240",
          "unit": "J_per_declared_mol_basis_per_K"
        },
        {
          "iqr": null,
          "label": "absolute (dimensionless)",
          "median": "3",
          "median_abs": "3",
          "n": 1,
          "operation": "absolute",
          "quantity": "quantity_unknown",
          "rms": "3",
          "unit": "dimensionless"
        },
        {
          "iqr": "2.50",
          "label": "absolute (kJ_per_declared_mol_basis)",
          "median": "1.5",
          "median_abs": "1.5",
          "n": 12,
          "operation": "absolute",
          "quantity": "quantity_unknown",
          "rms": "14.67707963685782644757773530",
          "unit": "kJ_per_declared_mol_basis"
        }
      ],
      "n": 15,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 2,
      "n_no_band": 13,
      "n_score_eligible": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "sources": [
        {
          "flag_class_counts": {},
          "n_flagged": 0,
          "n_numeric": 14,
          "source_id": "nist-janaf-4th"
        },
        {
          "flag_class_counts": {},
          "n_flagged": 0,
          "n_numeric": 1,
          "source_id": "other-compilation"
        }
      ],
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
      "decision_strata": [
        {
          "band_derived_n": null,
          "band_kind": "no_band",
          "band_value": [],
          "bias_to_scatter_ratio": null,
          "engine": "internal-analytical",
          "family": "compilations-janaf",
          "mad": "0.240",
          "match_count": 0,
          "max_abs_residual": "0.240",
          "mismatch_count": 0,
          "n": 2,
          "no_band_count": 2,
          "no_band_reason": null,
          "quantity": "S",
          "relation": "unknown",
          "signed_median_residual": "0.000",
          "tail_in_count": null,
          "tail_out_count": null,
          "unit": "J_per_declared_mol_basis_per_K"
        },
        {
          "band_derived_n": null,
          "band_kind": "printed_1sigma",
          "band_value": [
            "0.231",
            "2"
          ],
          "bias_to_scatter_ratio": "1",
          "engine": "internal-analytical",
          "family": "compilations-janaf",
          "mad": "1.5",
          "match_count": 2,
          "max_abs_residual": "50",
          "mismatch_count": 0,
          "n": 12,
          "no_band_count": 10,
          "no_band_reason": null,
          "quantity": "delta_fG",
          "relation": "unknown",
          "signed_median_residual": "1.5",
          "tail_in_count": null,
          "tail_out_count": null,
          "unit": [
            "dimensionless",
            "kJ_per_declared_mol_basis"
          ]
        },
        {
          "band_derived_n": null,
          "band_kind": "no_band",
          "band_value": [],
          "bias_to_scatter_ratio": null,
          "engine": "internal-analytical",
          "family": "compilations-other",
          "mad": "0",
          "match_count": 0,
          "max_abs_residual": "7",
          "mismatch_count": 0,
          "n": 1,
          "no_band_count": 1,
          "no_band_reason": null,
          "quantity": "delta_fG",
          "relation": "unknown",
          "signed_median_residual": "7",
          "tail_in_count": null,
          "tail_out_count": null,
          "unit": "kJ_per_declared_mol_basis"
        }
      ],
      "engine": "internal-analytical",
      "match_rate": 1.0,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 15,
      "n_candidates": 15,
      "n_eligible_references": 15,
      "n_inside_band": 2,
      "n_match": 2,
      "n_no_band": 13,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 15,
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
| thermochemistry | internal-analytical | 15 | 0 | 0 | 0 | — | — | unflagged=15 | nist-janaf-4th(n=14,cert=0,flag=0), other-compilation(n=1,cert=0,flag=0) |
| vapour | internal-analytical | 1 | 1 | 0 | 1 | 0.5 | — | unflagged=1 | work-1(n=1,cert=1,flag=0) |
| wall_deposition | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |

### All-numeric metric strata

| rail | engine | quantity | metric | n | median | IQR |
|---|---|---|---|---:|---:|---:|
| thermochemistry | internal-analytical | quantity_unknown | absolute (J_per_declared_mol_basis_per_K) | 2 | 0.000 | 0.24000 |
| thermochemistry | internal-analytical | quantity_unknown | absolute (dimensionless) | 1 | 3 | — |
| thermochemistry | internal-analytical | quantity_unknown | absolute (kJ_per_declared_mol_basis) | 12 | 1.5 | 2.50 |
| vapour | internal-analytical | quantity_unknown | dex | 1 | 0.5 | — |

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

| stratum | rail | engine | n | median dex | RMS dex |
|---|---|---|---:|---:|---:|
| (none) | — | — | 0 | — | — |

## Condition hull per rail × engine

Min–max of the stored (T, P, composition) of the numeric rows behind each per rail × engine statistic: the measured and compilation headlines (the n scored column) and each flagged stratum (its n). T and total pressure are the reference row's own (a compilation cell's printed T); composition is the row's stated composition per amount basis. Nothing is filled in: bracketed counts are rows carrying a value. Total P is the stored total pressure only; a table's standard-state pressure (standard_pressure_Pa) is not a total pressure and is not shown. A compilation row pools every compilation table on that rail × engine, so its T span is not one table's window. Six significant figures. Presentation only; no residual or statistic reads this table.

| tier / stratum | rail | engine | n | T K (n) | total P Pa (n) | composition (n) |
|---|---|---|---:|---|---|---|
| measured | vapour | internal-analytical | 1 | 1156 (1) | — (0) | — (0) |
| compilation | thermochemistry | internal-analytical | 15 | 298.15 (15) | — (0) | — (0) |

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
| compilations-janaf | thermochemistry | internal-analytical | unknown | S | J_per_declared_mol_basis_per_K | 2 | 0.000 | 0.240 | — | 0.240 | 0.240 | 0.240 | — | no_band | — | — | no_band in-band=0 | no_band out-of-band=0 | 2 | 0 |
| compilations-janaf | thermochemistry | internal-analytical | unknown | delta_fG | dimensionless | 1 | 3 | 0 | — | 3 | 3 | 3 | 0.231 | printed_1sigma | — | — | printed_1sigma in-band=1 | printed_1sigma out-of-band=0 | 0 | 0 |
| compilations-janaf | thermochemistry | internal-analytical | unknown | delta_fG | kJ/mol | 11 | 1 | 1 | 1 | 50 | 1 | 15.18372334262760944930524278 | 2 | no_band,printed_1sigma | — | — | no_band in-band=0; printed_1sigma in-band=1 | no_band out-of-band=0; printed_1sigma out-of-band=0 | 10 | 0 |
| compilations-other | thermochemistry | internal-analytical | unknown | delta_fG | kJ/mol | 1 | 7 | 0 | — | 7 | 7 | 7 | — | no_band | — | — | no_band in-band=0 | no_band out-of-band=0 | 1 | 0 |

| engine | comparisons | refused | numeric | same-source | independent | match same-source | match independent |
|---|---:|---:|---:|---:|---:|---:|---:|
| internal-analytical | 15 | 0 | 15 | 0 | 0 | 0 | 0 |

| compilation | quantity | reachable | predicted | refused | median abs residual | uncertainty |
|---|---|---:|---:|---:|---:|---|
| `compilations-janaf` | `S` | 2 | 2 | 0 | 0.240 | none |
| `compilations-janaf` | `delta_fG` | 12 | 12 | 0 | 1.5 | none |
| `compilations-other` | `delta_fG` | 1 | 1 | 0 | 7 | none |


## Refusal census

Measured tier only. Compilation refusals are in the compilation table.

| reason | n |
|---|---:|
| `unsupported:fixture_unavailable` | 1 |

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

Diagnostic residuals in this file: 15.

## Pin failures

None.

## status_diff vs old scorers

No mapped outcome changes.

All mapped legacy keys have a v2.1 comparison slot.
"""

PIN_PAYLOAD_SUMMARY_JSON = """\
{
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
      "flag_class_counts": {
        "unflagged": 15
      },
      "iqr_dex": null,
      "median_abs_dex": null,
      "median_dex": null,
      "metric_strata": [
        {
          "iqr": "0.2400",
          "label": "absolute (J_per_declared_mol_basis_per_K)",
          "median": "0.00",
          "median_abs": "0.24",
          "n": 2,
          "operation": "absolute",
          "quantity": "quantity_unknown",
          "rms": "0.24",
          "unit": "J_per_declared_mol_basis_per_K"
        },
        {
          "iqr": null,
          "label": "absolute (dimensionless)",
          "median": "3",
          "median_abs": "3",
          "n": 1,
          "operation": "absolute",
          "quantity": "quantity_unknown",
          "rms": "3",
          "unit": "dimensionless"
        },
        {
          "iqr": "2.50",
          "label": "absolute (kJ_per_declared_mol_basis)",
          "median": "1.5",
          "median_abs": "1.5",
          "n": 12,
          "operation": "absolute",
          "quantity": "quantity_unknown",
          "rms": "14.67707963685782644757773530",
          "unit": "kJ_per_declared_mol_basis"
        }
      ],
      "n": 15,
      "n_dex": 0,
      "n_flagged": 0,
      "n_inside_band": 2,
      "n_no_band": 13,
      "n_score_eligible": 0,
      "rail": "thermochemistry",
      "rms_dex": null,
      "sources": [
        {
          "flag_class_counts": {},
          "n_flagged": 0,
          "n_numeric": 14,
          "source_id": "nist-janaf-4th"
        },
        {
          "flag_class_counts": {},
          "n_flagged": 0,
          "n_numeric": 1,
          "source_id": "other-compilation"
        }
      ],
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
      "decision_strata": [
        {
          "band_derived_n": null,
          "band_kind": "no_band",
          "band_value": [],
          "bias_to_scatter_ratio": null,
          "engine": "internal-analytical",
          "family": "compilations-janaf",
          "mad": "0.24",
          "match_count": 0,
          "max_abs_residual": "0.24",
          "mismatch_count": 0,
          "n": 2,
          "no_band_count": 2,
          "no_band_reason": null,
          "quantity": "S",
          "relation": "unknown",
          "signed_median_residual": "0.00",
          "tail_in_count": null,
          "tail_out_count": null,
          "unit": "J_per_declared_mol_basis_per_K"
        },
        {
          "band_derived_n": null,
          "band_kind": "printed_1sigma",
          "band_value": [
            "0.231",
            "2"
          ],
          "bias_to_scatter_ratio": "1",
          "engine": "internal-analytical",
          "family": "compilations-janaf",
          "mad": "1.5",
          "match_count": 2,
          "max_abs_residual": "50",
          "mismatch_count": 0,
          "n": 12,
          "no_band_count": 10,
          "no_band_reason": null,
          "quantity": "delta_fG",
          "relation": "unknown",
          "signed_median_residual": "1.5",
          "tail_in_count": null,
          "tail_out_count": null,
          "unit": [
            "dimensionless",
            "kJ_per_declared_mol_basis"
          ]
        },
        {
          "band_derived_n": null,
          "band_kind": "no_band",
          "band_value": [],
          "bias_to_scatter_ratio": null,
          "engine": "internal-analytical",
          "family": "compilations-other",
          "mad": "0",
          "match_count": 0,
          "max_abs_residual": "7",
          "mismatch_count": 0,
          "n": 1,
          "no_band_count": 1,
          "no_band_reason": null,
          "quantity": "delta_fG",
          "relation": "unknown",
          "signed_median_residual": "7",
          "tail_in_count": null,
          "tail_out_count": null,
          "unit": "kJ_per_declared_mol_basis"
        }
      ],
      "engine": "internal-analytical",
      "match_rate": 1.0,
      "match_rate_label": "band membership",
      "median_abs_dex": null,
      "median_dex": null,
      "n": 15,
      "n_candidates": 15,
      "n_inside_band": 2,
      "n_match": 2,
      "n_no_band": 13,
      "n_refused": 0,
      "n_score_eligible": 0,
      "n_scored": 15,
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
| thermochemistry | internal-analytical | 15 | 0 | 0 | 0 | — | — | unflagged=15 | nist-janaf-4th(n=14,cert=0,flag=0), other-compilation(n=1,cert=0,flag=0) |
| vapour | internal-analytical | 1 | 1 | 0 | 1 | 0.5 | — | unflagged=1 | work-1(n=1,cert=1,flag=0) |
| wall_deposition | internal-analytical | 0 | 0 | 0 | 0 | — | — | — | — |

### All-numeric metric strata

| rail | engine | quantity | metric | n | median | IQR |
|---|---|---|---|---:|---:|---:|
| thermochemistry | internal-analytical | quantity_unknown | absolute (J_per_declared_mol_basis_per_K) | 2 | 0.00 | 0.2400 |
| thermochemistry | internal-analytical | quantity_unknown | absolute (dimensionless) | 1 | 3 | — |
| thermochemistry | internal-analytical | quantity_unknown | absolute (kJ_per_declared_mol_basis) | 12 | 1.5 | 2.50 |
| vapour | internal-analytical | quantity_unknown | dex | 1 | 0.5 | — |

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

| stratum | rail | engine | n | median dex | RMS dex |
|---|---|---|---:|---:|---:|
| (none) | — | — | 0 | — | — |

## Condition hull per rail × engine

Min–max of the stored (T, P, composition) of the numeric rows behind each per rail × engine statistic: the measured and compilation headlines (the n scored column) and each flagged stratum (its n). T and total pressure are the reference row's own (a compilation cell's printed T); composition is the row's stated composition per amount basis. Nothing is filled in: bracketed counts are rows carrying a value. Total P is the stored total pressure only; a table's standard-state pressure (standard_pressure_Pa) is not a total pressure and is not shown. A compilation row pools every compilation table on that rail × engine, so its T span is not one table's window. Six significant figures. Presentation only; no residual or statistic reads this table.

| tier / stratum | rail | engine | n | T K (n) | total P Pa (n) | composition (n) |
|---|---|---|---:|---|---|---|
| measured | vapour | internal-analytical | 1 | 1156 (1) | — (0) | — (0) |
| compilation | thermochemistry | internal-analytical | 15 | 298.15 (15) | — (0) | — (0) |

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
| compilations-janaf | thermochemistry | internal-analytical | unknown | S | J_per_declared_mol_basis_per_K | 2 | 0.00 | 0.24 | — | 0.24 | 0.24 | 0.24 | — | no_band | — | — | no_band in-band=0 | no_band out-of-band=0 | 2 | 0 |
| compilations-janaf | thermochemistry | internal-analytical | unknown | delta_fG | dimensionless | 1 | 3 | 0 | — | 3 | 3 | 3 | 0.231 | printed_1sigma | — | — | printed_1sigma in-band=1 | printed_1sigma out-of-band=0 | 0 | 0 |
| compilations-janaf | thermochemistry | internal-analytical | unknown | delta_fG | kJ/mol | 11 | 1 | 1 | 1 | 50 | 1 | 15.18372334262760944930524278 | 2 | no_band,printed_1sigma | — | — | no_band in-band=0; printed_1sigma in-band=1 | no_band out-of-band=0; printed_1sigma out-of-band=0 | 10 | 0 |
| compilations-other | thermochemistry | internal-analytical | unknown | delta_fG | kJ/mol | 1 | 7 | 0 | — | 7 | 7 | 7 | — | no_band | — | — | no_band in-band=0 | no_band out-of-band=0 | 1 | 0 |

| engine | comparisons | refused | numeric | same-source | independent | match same-source | match independent |
|---|---:|---:|---:|---:|---:|---:|---:|
| internal-analytical | 15 | 0 | 15 | 0 | 0 | 0 | 0 |

| compilation | quantity | reachable | predicted | refused | median abs residual | uncertainty |
|---|---|---:|---:|---:|---:|---|
| `compilations-janaf` | `S` | 2 | 2 | 0 | 0.24 | none |
| `compilations-janaf` | `delta_fG` | 12 | 12 | 0 | 1.5 | none |
| `compilations-other` | `delta_fG` | 1 | 1 | 0 | 7 | none |


## Refusal census

Measured tier only. Compilation refusals are in the compilation table.

| reason | n |
|---|---:|
| `unsupported:fixture_unavailable` | 1 |

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
    residuals, candidates = score_mod.score_store(context, engines=engines)
    direct_jsonl_path = tmp_path / "direct-residuals.jsonl"
    score_mod.write_residuals_jsonl(
        residuals, candidates, direct_jsonl_path, root=root
    )
    streamed_path = tmp_path / "residuals.jsonl"
    _written, metadata = score_mod._score_store_to_jsonl(
        context, streamed_path, root=root, engines=engines
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
    assert got["direct_jsonl"] == got["streamed_jsonl"]
    assert _without_certified_counts(got["direct_jsonl"]) == PIN_RESIDUAL_JSONL
    assert got["direct_summary"] == got["streamed_summary"]
    assert _without_certified_counts(got["direct_summary"]) == PIN_SUMMARY_JSON
    assert got["direct_report"] == got["streamed_report"]
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
