"""t-1110: the score report prints the (T, P, composition) hull of the rows
behind each per-rail x engine statistic.

Presentation only. The pin below was committed before the hull existed: it
fixes the headline statistics and the measured headline table for a small
mixed fixture, so the hull change is proven not to move any statistic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    EvidenceClass,
    MetricOperation,
    Rail,
    ResidualStatus,
    SourceRelation,
)
from simulator.battery.records import (
    Composition,
    DecisionBand,
    Execution,
    ExecutionState,
    Residual,
    ResidualNumeric,
    ResidualRefusal,
)
from simulator.battery.enums import RefusalReason
from simulator.battery.score import (
    ScoreContext,
    _render_score_report_from_payloads_legacy,
    _ResidualRowsPayloadView,
    _ScorePayloadAccumulator,
)
from tests.battery import factories as F

ENGINE = Engine.INTERNAL_ANALYTICAL
STAMP = {
    "kind": "battery_store_stamp",
    "revision": "aaa111ccc",
    "rows_in": 10,
    "observations": 20,
    "works": 2,
    "experiments": 3,
    "queue": 4,
    "hard_issues": 5,
}


def _melt(cao: str, sio2: str) -> Composition:
    return Composition(
        basis="ordered_complete_mole_inventory",
        components=(("CaO", Decimal(cao)), ("SiO2", Decimal(sio2))),
        amount_basis=AmountBasis.MOLE_FRACTION,
    )


def _dex(value: str, band: str = "0.3") -> ResidualNumeric:
    return ResidualNumeric(
        MetricOperation.DEX,
        "dimensionless",
        Decimal(value),
        DecisionBand(Decimal(band), "dimensionless", "fixture band"),
    )


def _residual(reference: str, rail: Rail, status: ResidualStatus, numeric=None, refusal=None):
    return Residual(
        key=f"fixture::{reference}::{ENGINE.value}",
        reference=reference,
        execution=Execution(state=ExecutionState.PRODUCED),
        rail=rail,
        status=status,
        source_relation=SourceRelation.INDEPENDENT,
        score_eligible=status in {ResidualStatus.MATCH, ResidualStatus.MISMATCH},
        exclusions=(),
        notices=(),
        candidate=None,
        numeric=numeric,
        refusal=refusal,
    )


def hull_fixture() -> tuple[ScoreContext, tuple[Residual, ...]]:
    work = F.work()
    exp = F.tabulation_experiment(work_id=work.work_id)
    rows = []
    residuals = []
    melt_points = (
        ("act-1", "1773", "100000", "0.40", "0.60", "0.10", ResidualStatus.MATCH),
        ("act-2", "1873", "100000", "0.55", "0.45", "-0.20", ResidualStatus.MATCH),
        ("act-3", "1923", "50000", "0.30", "0.70", "0.50", ResidualStatus.MISMATCH),
    )
    for oid, t_k, p_pa, cao, sio2, dex, status in melt_points:
        obs = F.observation(
            oid,
            exp.experiment_id,
            F.activity_identity(
                formula="CaO",
                component_basis="CaO",
                T_K=Decimal(t_k),
                total_P=Decimal(p_pa),
                composition=_melt(cao, sio2),
            ),
            Decimal("0.1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        )
        rows.append(obs)
        residuals.append(_residual(oid, Rail.MELT_ACTIVITY, status, numeric=_dex(dex)))
    # Refused melt row: counted as a candidate, not behind any statistic.
    refused = F.observation(
        "act-refused",
        exp.experiment_id,
        F.activity_identity(
            formula="CaO",
            component_basis="CaO",
            T_K=Decimal("2500"),
            total_P=Decimal("1"),
            composition=_melt("0.99", "0.01"),
        ),
        Decimal("0.1"),
        evidence=EvidenceClass.MEASURED_DIRECT,
        source_id=work.work_id,
    )
    rows.append(refused)
    residuals.append(
        _residual(
            "act-refused",
            Rail.MELT_ACTIVITY,
            ResidualStatus.REFUSED,
            refusal=ResidualRefusal(RefusalReason.UNSUPPORTED, {"reason": "fixture"}),
        )
    )
    # Vapour rows: p_sat carries T only (no total pressure, no composition).
    for oid, t_k, dex in (("na-1", "1100", "0.05"), ("na-2", "1250", "-0.15")):
        obs = F.observation(
            oid,
            exp.experiment_id,
            F.psat_identity("Na", T_K=Decimal(t_k)),
            Decimal("1"),
            evidence=EvidenceClass.MEASURED_DIRECT,
            source_id=work.work_id,
        )
        rows.append(obs)
        residuals.append(_residual(oid, Rail.VAPOUR, ResidualStatus.MATCH, numeric=_dex(dex)))
    # Compilation series cells: the printed T is the cell, not the parent.
    parent = F.observation(
        "janaf-o2",
        exp.experiment_id,
        replace(F.o2_identity(), temperature_K=None),
        Decimal("0"),
    )
    rows.append(parent)
    for cell, value in (("janaf-o2#t1500v-3.2", "0.4"), ("janaf-o2#t2000v-5.1", "-0.6")):
        residuals.append(
            _residual(
                cell,
                Rail.THERMOCHEMISTRY,
                ResidualStatus.NO_BAND,
                numeric=ResidualNumeric(
                    MetricOperation.ABSOLUTE,
                    "kJ_per_declared_mol_basis",
                    Decimal(value),
                    None,
                ),
            )
        )
    context = ScoreContext(
        works={work.work_id: work},
        experiments={exp.experiment_id: exp},
        observations={o.observation_id: o for o in rows},
        extract_review={work.work_id: None, "": None, "janaf-4th": None},
        hostname="test",
    )
    return context, tuple(residuals)


def _aggregate(context, residuals):
    return _ScorePayloadAccumulator.from_rows(
        _ResidualRowsPayloadView(residuals, context), context=context, engines=(ENGINE,)
    )


def _report(context, residuals) -> str:
    return _render_score_report_from_payloads_legacy(
        _ResidualRowsPayloadView(residuals, context),
        context=context,
        engines=(ENGINE,),
        store_stamp=STAMP,
    )


def test_pin_headline_statistics_unchanged_by_hull() -> None:
    """Pin committed BEFORE the hull: statistics and the summary payload."""

    context, residuals = hull_fixture()
    summary = _aggregate(context, residuals).summary_payload(STAMP)
    blob = json.dumps(summary, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    melt = next(
        r for r in summary["records"]
        if r["tier"] == "measured" and r["rail"] == "melt_activity"
    )
    vapour = next(
        r for r in summary["records"]
        if r["tier"] == "measured" and r["rail"] == "vapour"
    )
    assert (melt["n_candidates"], melt["n_refused"], melt["n"], melt["n_inside_band"]) == (4, 1, 3, 2)
    assert (melt["rms_dex"], melt["median_dex"], melt["median_abs_dex"]) == PIN_MELT_STATS
    assert (vapour["n"], vapour["rms_dex"], vapour["median_dex"]) == PIN_VAPOUR_STATS
    assert sorted(summary["records"][0]) == PIN_RECORD_KEYS
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == PIN_SUMMARY_SHA256


def test_pin_measured_headline_table_unchanged_by_hull() -> None:
    context, residuals = hull_fixture()
    report = _report(context, residuals)
    for line in PIN_HEADLINE_LINES:
        assert line in report.splitlines()


# Values recorded on origin/work-v064-green e702b2551 before the hull change.
PIN_MELT_STATS = ("0.3162277660168379331998893544", "0.10", "0.20")
PIN_VAPOUR_STATS = (2, "0.1118033988749894848204586834", "-0.05")
PIN_RECORD_KEYS = [
    "band_width_dex", "data_scatter_ratio", "decision_strata", "engine",
    "match_rate", "match_rate_label", "median_abs_dex", "median_dex", "n",
    "n_candidates", "n_eligible_references", "n_inside_band", "n_match",
    "n_no_band", "n_refused", "n_score_eligible", "n_scored", "rail",
    "rms_dex", "rms_over_band", "tier",
]
PIN_SUMMARY_SHA256 = "89a26432e54a73ca322b5bfe6834c305f3ab0bcd4e2a9dfe97b6c02776a04ed4"
PIN_HEADLINE_LINES = (
    "| melt_activity | internal-analytical | 4 | 1 | 3 | 3 | 2 | "
    "0.3162277660168379331998893544 | 0.10 | 0.20 | 0.3 | "
    "1.054092553389459777332964515 | 0 | 0.667 |",
    "| vapour | internal-analytical | 2 | 0 | 2 | 2 | 2 | "
    "0.1118033988749894848204586834 | -0.05 | 0.10 | 0.3 | "
    "0.3726779962499649494015289447 | 0 | 1.000 |",
)


# --- t-1110 hull (added after the pin) ---------------------------------------

HULL_MELT = (
    "| measured | melt_activity | internal-analytical | 3 | 1773–1923 (3) | "
    "50000–100000 (3) | mole_fraction: CaO 0.3–0.55, SiO2 0.45–0.7 (3) |"
)
HULL_VAPOUR = (
    "| measured | vapour | internal-analytical | 2 | 1100–1250 (2) | — (0) | — (0) |"
)
HULL_COMPILATION = (
    "| compilation | thermochemistry | internal-analytical | 2 | 1500–2000 (2) | "
    "— (0) | — (0) |"
)


def _hull_section(report: str) -> list[str]:
    lines = report.splitlines()
    start = lines.index("## Condition hull per rail × engine")
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
    return [line for line in lines[start:end] if line.startswith("| ") and "---" not in line]


def test_streamed_report_prints_condition_hull_per_rail_and_engine() -> None:
    context, residuals = hull_fixture()
    rows = _hull_section(_report(context, residuals))
    assert rows[0].startswith("| tier / stratum | rail | engine | n |")
    # Refused act-refused (T 2500 K, P 1 Pa, CaO 0.99) is not behind a statistic.
    assert rows[1:] == [HULL_MELT, HULL_VAPOUR, HULL_COMPILATION]


def test_report_only_render_prints_the_same_hull_as_the_streamed_report() -> None:
    from simulator.battery.score import render_score_report_from_payloads, residual_to_plain

    context, residuals = hull_fixture()
    payloads = []
    for residual in residuals:
        payload = residual_to_plain(residual)
        if residual.numeric is not None:
            payload["numeric"]["value"] = str(residual.numeric.value)
        payloads.append(payload)
    report = render_score_report_from_payloads(
        payloads,
        engines=(ENGINE,),
        hostname="test",
        store_stamp=STAMP,
        observations=context.observations,
        origins=context.origins,
    )
    # --report-only must print the compilation-tier hull too (ROR-t1110 P1):
    # the two writers' hull sections are the same, row for row.
    assert _hull_section(report)[1:] == [HULL_MELT, HULL_VAPOUR, HULL_COMPILATION]
    assert _hull_section(report) == _hull_section(_report(context, residuals))


def test_hull_counts_missing_conditions_and_never_imputes() -> None:
    from simulator.battery.score import _ConditionHull

    hull = _ConditionHull()
    hull.add("missing-row", None)
    context, _ = hull_fixture()
    hull.add("na-1", context.observations["na-1"])
    assert (hull.n, hull.n_t, hull.n_p, hull.n_composition) == (2, 1, 0, 0)
    assert hull.cells() == ("1100 (1)", "— (0)", "— (0)")
    hull.add("act-1", context.observations["act-1"])
    hull.add("act-3", context.observations["act-3"])
    assert hull.cells() == (
        "1100–1923 (3)",
        "50000–100000 (2)",
        "mole_fraction: CaO 0.3–0.4, SiO2 0.6–0.7 (2)",
    )


def test_flagged_stratum_gets_its_own_hull_and_leaves_headline_alone() -> None:
    from simulator.battery.enums import NoticeKind, Quantity
    from simulator.battery.records import Notice
    from simulator.battery.score import (
        FLAGGED_STRATUM_CELL_MATERIAL_INFERRED,
        render_score_report_from_payloads,
        residual_to_plain,
    )

    context, residuals = hull_fixture()
    flag = Notice(
        kind=NoticeKind.CELL_MATERIAL_INFERRED,
        affected_quantities=(Quantity.ACTIVITY,),
        reason="fixture cell material inferred",
        origin="fixture",
    )
    flagged = tuple(
        replace(r, notices=(flag,)) if r.reference == "act-3" else r for r in residuals
    )
    stratum = (
        f"| flagged:{FLAGGED_STRATUM_CELL_MATERIAL_INFERRED} | melt_activity | "
        "internal-analytical | 1 | 1923 (1) | 50000 (1) | "
        "mole_fraction: CaO 0.3, SiO2 0.7 (1) |"
    )
    streamed = _hull_section(_report(context, flagged))
    assert stratum in streamed
    # The flagged row leaves the measured headline, so it leaves its hull too.
    assert (
        "| measured | melt_activity | internal-analytical | 2 | 1773–1873 (2) | "
        "100000 (2) | mole_fraction: CaO 0.4–0.55, SiO2 0.45–0.6 (2) |"
    ) in streamed
    payloads = []
    for residual in flagged:
        payload = residual_to_plain(residual)
        if residual.numeric is not None:
            payload["numeric"]["value"] = str(residual.numeric.value)
        payloads.append(payload)
    report_only = _hull_section(
        render_score_report_from_payloads(
            payloads,
            engines=(ENGINE,),
            hostname="test",
            store_stamp=STAMP,
            observations=context.observations,
            origins=context.origins,
        )
    )
    assert stratum in report_only
    assert report_only == streamed


def test_compilation_point_temperature_reads_only_printed_cell_ids() -> None:
    from simulator.battery.compilation_tier import (
        compilation_point_id,
        compilation_point_temperature,
    )

    cell = compilation_point_id("janaf-o2", Decimal("1500.50"), Decimal("-3.2"), 1)
    assert compilation_point_temperature(cell) == Decimal("1500.5")
    assert compilation_point_temperature("janaf-o2") is None
    assert compilation_point_temperature("series-row#3") is None


def test_hull_is_presentation_only_no_residual_reads_it() -> None:
    """The hull is written to the report only; the summary JSON is unchanged."""

    context, residuals = hull_fixture()
    aggregate = _aggregate(context, residuals)
    assert aggregate.headline_hulls  # collected
    blob = json.dumps(aggregate.summary_payload(STAMP), sort_keys=True)
    assert "hull" not in blob


def test_hull_text_states_total_pressure_and_compilation_pooling() -> None:
    # ROR-t1110 P2: a JANAF/USGS table stores standard_pressure_Pa (1 bar), not
    # a total pressure, so its total P cell is "— (0)"; and the compilation
    # row pools every table on the rail x engine. The section says both.
    context, residuals = hull_fixture()
    report = _report(context, residuals)
    assert "standard-state pressure (standard_pressure_Pa) is not a total pressure" in report
    assert "A compilation row pools every compilation table on that rail × engine" in report
