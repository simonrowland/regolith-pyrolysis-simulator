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
# PIN_RECORD_KEYS and PIN_SUMMARY_SHA256 re-recorded on
# review/empirical-ror-batch2 c9fd86c9c (still before the hull): its b-693
# dual headline puts the all_numeric records first in summary["records"].
PIN_MELT_STATS = ("0.3162277660168379331998893544", "0.10", "0.20")
PIN_VAPOUR_STATS = (2, "0.1118033988749894848204586834", "-0.05")
PIN_RECORD_KEYS = [
    "engine", "flag_class_counts", "iqr_dex", "median_abs_dex", "median_dex",
    "metric_strata", "n", "n_certified", "n_dex", "n_flagged",
    "n_inside_band", "n_no_band", "n_score_eligible", "rail", "rms_dex",
    "sources", "tier",
]
PIN_SUMMARY_SHA256 = "4d69e72df3f0746eee27eae585d0a1142eb8b2d9bad5003ed190da1ea9478b49"
PIN_HEADLINE_LINES = (
    "| melt_activity | internal-analytical | 4 | 1 | 3 | 3 | 2 | "
    "0.3162277660168379331998893544 | 0.10 | 0.20 | 0.3 | "
    "1.054092553389459777332964515 | 0 | 0.667 |",
    "| vapour | internal-analytical | 2 | 0 | 2 | 2 | 2 | "
    "0.1118033988749894848204586834 | -0.05 | 0.10 | 0.3 | "
    "0.3726779962499649494015289447 | 0 | 1.000 |",
)
