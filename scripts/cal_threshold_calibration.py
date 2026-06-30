#!/usr/bin/env python3
"""CAL harness for SG-3 vapor yield-threshold calibration.

Runs instrumented, golden-neutral simulator cases and writes calibration
artifacts. The default backend is AlphaMELTS; stub runs require an explicit
--allow-stub opt-in and are marked non-authoritative.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.backend_names import canonical_backend_name

DEFAULT_OUTPUT_DIR = (
    REPO_ROOT / "docs-private" / "research" / "2026-06-03-cal-calibration"
)
DEFAULT_REVIEW_DIR = (
    REPO_ROOT / "docs-private" / "reviews" / "2026-06-03-cal-calibration"
)
DEFAULT_FEEDSTOCKS = ("lunar_mare_low_ti",)
OPTIONAL_FEEDSTOCKS = ("mars_perchlorate_rich", "ci_carbonaceous_chondrite")
DEFAULT_CAMPAIGNS = ("C2A_continuous", "C2B", "C4")
CAMPAIGN_TARGETS = {
    # Must match setpoints.yaml campaigns.*.target_species / completion contracts.
    "C2A_continuous": ("Na", "K", "Fe", "CrO2", "SiO"),
    "C2B": ("Fe",),
    "C4": ("Mg",),
}
_WORKER_FAILURE_STOP_REASONS = frozenset({
    "timeout",
    "error",
    "invalid_json",
    "max_hours",
})


@dataclass(frozen=True)
class CaseSpec:
    feedstock: str
    campaign: str


def _capture_tail(value: Any, *, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    return text[-limit:]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return None
    return value


def _float_or_none(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _worker_payload(args: argparse.Namespace) -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT))

    # --feedstock/--campaign register with action="append" (dest=feedstocks/
    # campaigns), so a worker subprocess receives them as single-element lists.
    # Resolve the scalar the worker body expects, and fail loud if the contract
    # (exactly one feedstock + one campaign per worker) is violated.
    if not args.feedstocks or len(args.feedstocks) != 1:
        raise SystemExit("--worker requires exactly one --feedstock")
    if not args.campaigns or len(args.campaigns) != 1:
        raise SystemExit("--worker requires exactly one --campaign")
    args.feedstock = args.feedstocks[0]
    args.campaign = args.campaigns[0]

    from simulator.backends import BackendSelectionPolicy
    from simulator.config import load_config_bundle
    from simulator.session import SimSession, SimSessionConfig

    cfg = load_config_bundle()
    session = SimSession().start(
        SimSessionConfig(
            feedstock_id=args.feedstock,
            feedstocks=cfg.feedstocks,
            setpoints=cfg.setpoints,
            vapor_pressures=cfg.vapor_pressures,
            campaign=args.campaign,
            backend_name=args.backend,
            backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
            mass_kg=args.mass_kg,
        )
    )
    backend = session.simulator.backend
    backend_info = {
        "name": type(backend).__name__,
        "capabilities": _json_safe(backend.capabilities()),
    }
    rows: list[dict[str, Any]] = []
    case_started = time.time()
    stop_reason = "max_hours"
    start_campaign = session.simulator.melt.campaign.name

    for step_index in range(1, args.max_hours + 1):
        step_started = time.time()
        result = session.advance()
        sim = session.simulator
        diag = getattr(sim, "_last_extraction_completeness_diagnostic", {}) or {}
        values = diag.get("completeness_by_target_species", {}) or {}
        detail = diag.get("detail_by_target_species", {}) or {}
        target_set = tuple(
            diag.get("target_species", ())
        ) or CAMPAIGN_TARGETS.get(args.campaign, ())

        for target in target_set:
            d = detail.get(target, {}) or {}
            rows.append(
                {
                    "feedstock": args.feedstock,
                    "campaign": args.campaign,
                    "backend": backend_info["name"],
                    "hour_index": step_index,
                    "sim_hour": sim.melt.hour,
                    "campaign_hour": sim.melt.campaign_hour,
                    "temperature_C": sim.melt.temperature_C,
                    "target": target,
                    "completeness": _float_or_none(values.get(target)),
                    "aggregate_completeness": _float_or_none(
                        diag.get("aggregate_completeness_fraction")
                    ),
                    "liquid_fraction": _float_or_none(diag.get("liquid_fraction")),
                    "product_target_equiv_mol": _float_or_none(
                        d.get("product_target_equiv_mol")
                    ),
                    "residual_target_equiv_mol": _float_or_none(
                        d.get("residual_target_equiv_mol")
                    ),
                    "denominator_target_equiv_mol": _float_or_none(
                        d.get("denominator_target_equiv_mol")
                    ),
                    "wall_deposit_target_equiv_mol": _float_or_none(
                        d.get("wall_deposit_target_equiv_mol")
                    ),
                    "reagent_target_equiv_mol": _float_or_none(
                        d.get("reagent_target_equiv_mol")
                    ),
                    "gross_product_target_equiv_mol": _float_or_none(
                        d.get("gross_product_target_equiv_mol")
                    ),
                    "contract_id": d.get("contract_id", ""),
                    "reason": d.get("reason", ""),
                    "aggregate_status": diag.get("aggregate_status", ""),
                    "aggregate_reason": diag.get("aggregate_reason", ""),
                    "aggregate_worst_target": diag.get(
                        "aggregate_worst_target_species", ""
                    ),
                    "would_be_cap_advance": diag.get("would_be_cap_advance"),
                    "would_be_hard_floor_advance": diag.get(
                        "would_be_hard_floor_advance"
                    ),
                    "backend_error": result.backend_error,
                    "step_elapsed_s": round(time.time() - step_started, 3),
                }
            )

        if diag.get("would_be_cap_advance") is True:
            stop_reason = "current_endpoint_cap"
            break
        if diag.get("would_be_hard_floor_advance") is True:
            stop_reason = "current_endpoint_hard_floor"
            break
        if session.pending_decision() is not None:
            stop_reason = "pending_decision"
            break
        if sim.melt.campaign.name != start_campaign:
            stop_reason = f"campaign_changed:{sim.melt.campaign.name}"
            break

    return {
        "case": {"feedstock": args.feedstock, "campaign": args.campaign},
        "backend": backend_info,
        "stop_reason": stop_reason,
        "elapsed_s": round(time.time() - case_started, 3),
        "rows": rows,
    }


def _run_worker_case(
    case: CaseSpec,
    *,
    backend: str,
    max_hours: int,
    per_case_timeout_s: int,
    mass_kg: float,
) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--feedstock",
        case.feedstock,
        "--campaign",
        case.campaign,
        "--backend",
        backend,
        "--max-hours",
        str(max_hours),
        "--mass-kg",
        str(mass_kg),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    started = time.time()
    try:
        completed = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=per_case_timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "case": {"feedstock": case.feedstock, "campaign": case.campaign},
            "backend": {"name": backend},
            "stop_reason": "timeout",
            "elapsed_s": round(time.time() - started, 3),
            "rows": [],
            "error": f"timeout after {per_case_timeout_s}s",
            "stdout_tail": _capture_tail(exc.stdout, limit=1000),
            "stderr_tail": _capture_tail(exc.stderr, limit=1000),
        }
    if completed.returncode != 0:
        return {
            "case": {"feedstock": case.feedstock, "campaign": case.campaign},
            "backend": {"name": backend},
            "stop_reason": "error",
            "elapsed_s": round(time.time() - started, 3),
            "rows": [],
            "error": f"worker exited {completed.returncode}",
            "stdout_tail": completed.stdout[-1000:],
            "stderr_tail": completed.stderr[-2000:],
        }
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {
            "case": {"feedstock": case.feedstock, "campaign": case.campaign},
            "backend": {"name": backend},
            "stop_reason": "invalid_json",
            "elapsed_s": round(time.time() - started, 3),
            "rows": [],
            "error": str(exc),
            "stdout_tail": completed.stdout[-2000:],
            "stderr_tail": completed.stderr[-2000:],
        }


def _series_by_key(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    series: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("completeness") is None:
            continue
        key = (row["feedstock"], row["campaign"], row["target"])
        series[key].append(row)
    for values in series.values():
        values.sort(key=lambda r: (r["campaign_hour"], r["hour_index"]))
    return series


def _analyze_series(values: list[dict[str, Any]]) -> dict[str, Any]:
    points = [
        (float(row["campaign_hour"]), float(row["completeness"]))
        for row in values
        if row.get("completeness") is not None
    ]
    if not points:
        return {"status": "no_data"}
    c_values = [c for _, c in points]
    c_max = max(c_values)
    endpoint = c_values[-1]
    if len(points) < 3 or c_max <= 0:
        threshold = max(0.0, min(endpoint, c_max - 1.0e-6))
        return {
            "status": "insufficient_curve",
            "points": len(points),
            "c_max": c_max,
            "endpoint_completeness": endpoint,
            "knee_hour": points[-1][0],
            "knee_completeness": endpoint,
            "proposed_threshold": threshold,
            "notes": "fewer than 3 numeric points; knee is provisional",
        }

    slopes: list[float] = []
    for (h0, c0), (h1, c1) in zip(points, points[1:]):
        dh = max(h1 - h0, 1.0e-9)
        slopes.append(max(0.0, (c1 - c0) / dh))
    peak_slope = max(slopes) if slopes else 0.0
    collapse_slope = max(1.0e-8, peak_slope * 0.15)
    knee_idx = len(points) - 1
    for idx in range(1, len(points) - 1):
        _, c = points[idx]
        future = slopes[idx : min(len(slopes), idx + 3)]
        future_avg = sum(future) / len(future) if future else 0.0
        if c >= 0.85 * c_max and future_avg <= collapse_slope:
            knee_idx = idx
            break
    else:
        for idx, (_, c) in enumerate(points):
            if c >= 0.95 * c_max:
                knee_idx = idx
                break

    knee_hour, knee_c = points[knee_idx]
    margin = max(0.0025, 0.01 * max(c_max, 1.0e-9))
    threshold = max(0.0, min(knee_c, c_max - margin))
    threshold = min(threshold, 0.999999)
    if threshold >= c_max:
        threshold = max(0.0, c_max - 1.0e-6)
    return {
        "status": "ok",
        "points": len(points),
        "c_max": c_max,
        "endpoint_completeness": endpoint,
        "knee_hour": knee_hour,
        "knee_completeness": knee_c,
        "proposed_threshold": threshold,
        "peak_slope_per_hr": peak_slope,
        "collapse_slope_per_hr": collapse_slope,
    }


def _delta_direction(threshold: float | None, endpoint: float | None) -> str:
    if threshold is None or endpoint is None:
        return "unknown"
    eps = 0.005
    if threshold < endpoint - eps:
        return "EARLIER -> yield DOWN (no-regression risk)"
    if threshold > endpoint + eps:
        return "LATER -> yield UP"
    return "~neutral"


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for case in cases for row in case.get("rows", [])]
    analysis_by_key = {
        "|".join(key): _analyze_series(values)
        for key, values in _series_by_key(rows).items()
    }

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for key, analysis in analysis_by_key.items():
        feedstock, campaign, target = key.split("|", 2)
        grouped[(campaign, target)].append({"feedstock": feedstock, **analysis})

    global_calls = {}
    for (campaign, target), items in grouped.items():
        thresholds = [
            item["proposed_threshold"]
            for item in items
            if item.get("proposed_threshold") is not None
        ]
        if len(thresholds) < 2:
            call = "provisional_per_feedstock_only"
            spread = None
        else:
            spread = max(thresholds) - min(thresholds)
            call = "global_candidate" if spread <= 0.05 else "per_feedstock"
        global_calls[f"{campaign}|{target}"] = {
            "call": call,
            "threshold_spread": spread,
            "feedstock_count": len(items),
            "thresholds": thresholds,
        }

    gm_matrix = []
    for key, analysis in analysis_by_key.items():
        feedstock, campaign, target = key.split("|", 2)
        threshold = analysis.get("proposed_threshold")
        endpoint = analysis.get("endpoint_completeness")
        gm_matrix.append(
            {
                "feedstock": feedstock,
                "campaign": campaign,
                "target": target,
                "endpoint_completeness": endpoint,
                "proposed_threshold": threshold,
                "direction": _delta_direction(threshold, endpoint),
                "no_regression_risk": (
                    threshold is not None
                    and endpoint is not None
                    and threshold < endpoint - 0.005
                ),
            }
        )

    return {
        "analysis_by_feedstock_campaign_target": analysis_by_key,
        "global_vs_per_feedstock": global_calls,
        "gm_delta_matrix": gm_matrix,
        "row_count": len(rows),
        "case_count": len(cases),
    }


def _expected_feedstock_campaign_targets(
    feedstocks: tuple[str, ...],
    campaigns: tuple[str, ...],
) -> tuple[tuple[str, str, str], ...]:
    keys: list[tuple[str, str, str]] = []
    for feedstock in feedstocks:
        for campaign in campaigns:
            for target in CAMPAIGN_TARGETS.get(campaign, ()):
                keys.append((feedstock, campaign, target))
    return tuple(keys)


def _is_real_backend_calibration_blocked(
    cases: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    backend: str,
    feedstocks: tuple[str, ...],
    campaigns: tuple[str, ...],
) -> bool:
    """True when a real-backend CAL run must not emit authoritative thresholds."""

    if backend == "stub":
        return False
    if summary.get("row_count", 0) == 0:
        return True
    for case in cases:
        if case.get("stop_reason") in _WORKER_FAILURE_STOP_REASONS:
            return True
    analysis = summary.get("analysis_by_feedstock_campaign_target", {})
    for feedstock, campaign, target in _expected_feedstock_campaign_targets(
        feedstocks,
        campaigns,
    ):
        entry = analysis.get(f"{feedstock}|{campaign}|{target}", {"status": "no_data"})
        if entry.get("status") != "ok":
            return True
    return False


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    fieldnames = [
        "feedstock",
        "campaign",
        "backend",
        "hour_index",
        "sim_hour",
        "campaign_hour",
        "temperature_C",
        "target",
        "completeness",
        "aggregate_completeness",
        "liquid_fraction",
        "product_target_equiv_mol",
        "residual_target_equiv_mol",
        "denominator_target_equiv_mol",
        "wall_deposit_target_equiv_mol",
        "reagent_target_equiv_mol",
        "gross_product_target_equiv_mol",
        "contract_id",
        "reason",
        "aggregate_status",
        "aggregate_reason",
        "aggregate_worst_target",
        "would_be_cap_advance",
        "would_be_hard_floor_advance",
        "backend_error",
        "step_elapsed_s",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def _markdown_findings(
    *,
    metadata: dict[str, Any],
    cases: list[dict[str, Any]],
    summary: dict[str, Any],
    blocked: bool,
) -> str:
    lines = [
        "# CAL Vapor Threshold Calibration",
        "",
        f"- Backend requested: `{metadata['backend']}`",
        f"- Backend fidelity: `{metadata['backend_fidelity']}`",
        f"- Feedstocks requested: {', '.join(metadata['feedstocks'])}",
        f"- Campaigns requested: {', '.join(metadata['campaigns'])}",
        f"- Rows collected: {summary['row_count']}",
        f"- Status: {'BLOCKED' if blocked else 'COMPLETE'}",
        "",
        "## Case Outcomes",
        "",
        "| feedstock | campaign | stop reason | elapsed s | rows |",
        "|---|---:|---:|---:|---:|",
    ]
    for case in cases:
        c = case.get("case", {})
        lines.append(
            f"| {c.get('feedstock')} | {c.get('campaign')} | "
            f"{case.get('stop_reason')} | {case.get('elapsed_s')} | "
            f"{len(case.get('rows', []))} |"
        )
    lines += ["", "## Per-Target Knee / Cmax / Threshold", ""]
    if not summary["analysis_by_feedstock_campaign_target"]:
        lines.append(
            "No numeric real-backend curves completed. Do not set thresholds from this run."
        )
    else:
        lines += [
            "| feedstock | campaign | target | knee hr | knee C | Cmax | threshold | status |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
        for key, item in sorted(
            summary["analysis_by_feedstock_campaign_target"].items()
        ):
            feedstock, campaign, target = key.split("|", 2)
            lines.append(
                f"| {feedstock} | {campaign} | {target} | "
                f"{_fmt(item.get('knee_hour'))} | {_fmt(item.get('knee_completeness'))} | "
                f"{_fmt(item.get('c_max'))} | {_fmt(item.get('proposed_threshold'))} | "
                f"{item.get('status')} |"
            )
    lines += ["", "## Global vs Per-Feedstock", ""]
    if not summary["global_vs_per_feedstock"]:
        lines.append("No variance call; no completed curves.")
    else:
        for key, item in sorted(summary["global_vs_per_feedstock"].items()):
            lines.append(
                f"- `{key}`: {item['call']} "
                f"(feedstocks={item['feedstock_count']}, spread={_fmt(item['threshold_spread'])})"
            )
    lines += ["", "## Today's Endpoint vs Proposed Default", ""]
    if not summary["gm_delta_matrix"]:
        lines.append("No matrix; endpoint completeness unavailable.")
    else:
        lines += [
            "| feedstock | campaign | target | endpoint C | threshold | predicted GM delta | flag |",
            "|---|---|---:|---:|---:|---|---|",
        ]
        for row in sorted(
            summary["gm_delta_matrix"],
            key=lambda r: (r["feedstock"], r["campaign"], r["target"]),
        ):
            flag = "NO-REGRESSION RISK" if row["no_regression_risk"] else ""
            lines.append(
                f"| {row['feedstock']} | {row['campaign']} | {row['target']} | "
                f"{_fmt(row['endpoint_completeness'])} | {_fmt(row['proposed_threshold'])} | "
                f"{row['direction']} | {flag} |"
            )
    lines += [
        "",
        "## Caveats",
        "",
        "- CAL is golden-neutral: this script writes analysis artifacts only.",
        "- Stub backend results are non-authoritative and must not seed thresholds.",
        "- AlphaMELTS is the active real backend available in this checkout; MAGEMin is not an active runner backend here.",
    ]
    if blocked:
        lines.append(
            "- Real-backend runtime exceeded the configured cap before useful curves completed; rerun with a longer per-case timeout or narrower campaign/feedstock set."
        )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _worker_result(
    *,
    metadata: dict[str, Any],
    summary: dict[str, Any],
    cases: list[dict[str, Any]],
    blocked: bool,
) -> str:
    lines = [
        "# CAL Calibration Worker Result",
        "",
        f"Backend: `{metadata['backend']}`; fidelity: `{metadata['backend_fidelity']}`.",
        f"Coverage: {summary['case_count']} cases attempted; {summary['row_count']} per-target rows collected.",
    ]
    if summary["analysis_by_feedstock_campaign_target"]:
        lines.append("")
        lines.append("Proposed thresholds:")
        for key, item in sorted(summary["analysis_by_feedstock_campaign_target"].items()):
            lines.append(
                f"- `{key}` threshold {_fmt(item.get('proposed_threshold'))}; "
                f"Cmax {_fmt(item.get('c_max'))}; endpoint {_fmt(item.get('endpoint_completeness'))}"
            )
        lines.append("")
        lines.append("GM-delta directions:")
        for row in sorted(
            summary["gm_delta_matrix"],
            key=lambda r: (r["feedstock"], r["campaign"], r["target"]),
        ):
            flag = " [NO-REGRESSION RISK]" if row["no_regression_risk"] else ""
            lines.append(
                f"- `{row['feedstock']}|{row['campaign']}|{row['target']}`: {row['direction']}{flag}"
            )
    else:
        lines.append("")
        lines.append(
            "No real-backend numeric curve completed; no threshold is proposed."
        )
    stops = ", ".join(
        f"{c.get('case', {}).get('feedstock')}:{c.get('case', {}).get('campaign')}={c.get('stop_reason')}"
        for c in cases
    )
    lines += [
        "",
        f"Case stops: {stops or 'none'}.",
        "Caveat: AlphaMELTS smoke exceeded the cap in this environment; stub data intentionally not used for threshold proposals.",
        (
            f"BLOCKED: real-backend runtime prohibited calibration artifact completion; "
            f"see {DEFAULT_REVIEW_DIR.relative_to(REPO_ROOT) / 'worker-result.md'}"
            if blocked
            else f"COMPLETE: {DEFAULT_REVIEW_DIR.relative_to(REPO_ROOT) / 'worker-result.md'}"
        ),
    ]
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="alphamelts", type=canonical_backend_name)
    parser.add_argument("--allow-stub", action="store_true")
    parser.add_argument("--feedstock", action="append", dest="feedstocks")
    parser.add_argument("--include-optional-feedstocks", action="store_true")
    parser.add_argument("--campaign", action="append", dest="campaigns")
    parser.add_argument("--max-hours", type=int, default=30)
    parser.add_argument("--per-case-timeout-s", type=int, default=900)
    parser.add_argument("--mass-kg", type=float, default=1000.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW_DIR)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.backend == "stub" and not args.allow_stub and not args.worker:
        raise SystemExit("stub backend requires --allow-stub; not authoritative for CAL")

    if args.worker:
        payload = _worker_payload(args)
        print(json.dumps(_json_safe(payload), sort_keys=True))
        return 0

    feedstocks = tuple(args.feedstocks or DEFAULT_FEEDSTOCKS)
    if args.include_optional_feedstocks:
        feedstocks = tuple(dict.fromkeys((*feedstocks, *OPTIONAL_FEEDSTOCKS)))
    campaigns = tuple(args.campaigns or DEFAULT_CAMPAIGNS)
    cases = [CaseSpec(feedstock, campaign) for feedstock in feedstocks for campaign in campaigns]

    results = [
        _run_worker_case(
            case,
            backend=args.backend,
            max_hours=args.max_hours,
            per_case_timeout_s=args.per_case_timeout_s,
            mass_kg=args.mass_kg,
        )
        for case in cases
    ]
    rows = [row for result in results for row in result.get("rows", [])]
    backend_fidelity = (
        "real-active-melt-backend" if args.backend != "stub" else "stub-non-authoritative"
    )
    metadata = {
        "backend": args.backend,
        "backend_fidelity": backend_fidelity,
        "feedstocks": feedstocks,
        "campaigns": campaigns,
        "max_hours": args.max_hours,
        "per_case_timeout_s": args.per_case_timeout_s,
        "generated_at_unix": time.time(),
    }
    summary = _summarize(results)
    blocked = _is_real_backend_calibration_blocked(
        results,
        summary,
        backend=args.backend,
        feedstocks=feedstocks,
        campaigns=campaigns,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.review_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(rows, args.output_dir / "raw_curves.csv")
    (args.output_dir / "raw_curves.json").write_text(
        json.dumps(_json_safe({"metadata": metadata, "cases": results}), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "analysis.json").write_text(
        json.dumps(_json_safe(summary), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "findings.md").write_text(
        _markdown_findings(
            metadata=metadata,
            cases=results,
            summary=summary,
            blocked=blocked,
        ),
        encoding="utf-8",
    )
    (args.review_dir / "worker-result.md").write_text(
        _worker_result(
            metadata=metadata,
            summary=summary,
            cases=results,
            blocked=blocked,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "blocked": blocked,
                "rows": summary["row_count"],
                "cases": summary["case_count"],
                "output_dir": str(args.output_dir),
                "review": str(args.review_dir / "worker-result.md"),
            },
            sort_keys=True,
        )
    )
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
