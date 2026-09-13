#!/usr/bin/env python3
"""Per-cell Studio commissioning diff (D03).

Compare two full binary-pot engine-arm captures (base vs tip) and emit a
table with one row per cell, including identical cells. Verdicts:

- identical
- reclassified_refusal_to_notice
- crash_annotation
- OTHER  (any OTHER is a stop)

Compared fields: status, refusal_reason, engine_reason, melt_activities,
gas_partial_pressures_Pa, liquid_fraction.

Captures may be a raw battery report or a wrapper with revision_id,
run_timestamp, hostname, and report=.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping


VERDICT_IDENTICAL = "identical"
VERDICT_RECLASSIFIED = "reclassified_refusal_to_notice"
VERDICT_CRASH_ANNOTATION = "crash_annotation"
VERDICT_OTHER = "OTHER"

COMPARE_FIELDS = (
    "status",
    "refusal_reason",
    "engine_reason",
    "melt_activities",
    "gas_partial_pressures_Pa",
    "liquid_fraction",
)

CRASH_REFUSALS = frozenset(
    {"engine_crash", "engine_timeout", "subprocess_died"}
)
CRASH_ANNOTATION_REASON = "sio2_below_observed_crash_floor"
REFUSAL_STATUSES = frozenset({"refusal", "out_of_domain"})
OK_STATUSES = frozenset({"ok"})
_FLOAT_ABS = 1.0e-12
_FLOAT_REL = 1.0e-12


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_capture(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if isinstance(payload.get("report"), Mapping) and "cells" in payload["report"]:
        report = dict(payload["report"])
        revision_id = payload.get("revision_id") or report.get("revision_id")
        run_timestamp = (
            payload.get("run_timestamp")
            or report.get("generated_at")
        )
        hostname = payload.get("hostname") or report.get("hostname")
    else:
        report = payload
        revision_id = payload.get("revision_id")
        run_timestamp = payload.get("generated_at") or payload.get("run_timestamp")
        hostname = payload.get("hostname")
    cells = list(report.get("cells") or [])
    return {
        "revision_id": revision_id,
        "run_timestamp": run_timestamp,
        "hostname": hostname,
        "report": report,
        "cells": cells,
    }


def wrap_capture(
    report: Mapping[str, Any],
    *,
    revision_id: str,
    run_timestamp: str | None = None,
    hostname: str | None = None,
) -> dict[str, Any]:
    return {
        "revision_id": str(revision_id),
        "run_timestamp": str(
            run_timestamp or report.get("generated_at") or ""
        ),
        "hostname": str(hostname or report.get("hostname") or ""),
        "n_cells": len(list(report.get("cells") or [])),
        "report": dict(report),
    }


def cell_identity(cell: Mapping[str, Any]) -> tuple[Any, ...]:
    po2 = cell.get("po2") or {}
    if not isinstance(po2, Mapping):
        po2 = {}
    po2_bar = po2.get("po2_bar")
    return (
        str(cell.get("pot_id") or ""),
        str(cell.get("engine") or ""),
        float(cell.get("temperature_K") or 0.0),
        str(po2.get("mode") or ""),
        None if po2_bar is None else float(po2_bar),
        str(cell.get("arm") or "headline"),
    )


def identity_label(key: tuple[Any, ...]) -> str:
    pot, engine, temperature_K, po2_mode, po2_bar, arm = key
    po2 = "default" if po2_bar is None else f"{po2_bar:g}"
    return (
        f"{pot}|{engine}|T={temperature_K:g}|po2={po2_mode}:{po2}|arm={arm}"
    )


def _floats_equal(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        left_f = float(left)
        right_f = float(right)
    except (TypeError, ValueError):
        return left == right
    if math.isnan(left_f) and math.isnan(right_f):
        return True
    return math.isclose(
        left_f, right_f, rel_tol=_FLOAT_REL, abs_tol=_FLOAT_ABS
    )


def _mapping_floats_equal(
    left: Mapping[str, Any] | None,
    right: Mapping[str, Any] | None,
) -> bool:
    left_map = dict(left or {})
    right_map = dict(right or {})
    if set(left_map) != set(right_map):
        return False
    return all(
        _floats_equal(left_map[key], right_map[key]) for key in left_map
    )


def _field_equal(field: str, left: Any, right: Any) -> bool:
    if field in {"melt_activities", "gas_partial_pressures_Pa"}:
        return _mapping_floats_equal(left, right)
    if field == "liquid_fraction":
        return _floats_equal(left, right)
    return left == right


def _snapshot(cell: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if cell is None:
        return None
    return {field: cell.get(field) for field in COMPARE_FIELDS}


def _is_crash(cell: Mapping[str, Any]) -> bool:
    reason = cell.get("refusal_reason")
    engine_status = cell.get("engine_status")
    engine_reason = str(cell.get("engine_reason") or "")
    return (
        reason in CRASH_REFUSALS
        or engine_status in CRASH_REFUSALS
        or engine_reason == CRASH_ANNOTATION_REASON
        or "subprocess_died" in engine_reason
    )


def _has_notice(cell: Mapping[str, Any]) -> bool:
    if cell.get("authority") == "extrapolated":
        return True
    notices = cell.get("notices") or []
    for notice in notices:
        if not isinstance(notice, Mapping):
            continue
        if notice.get("kind") in {
            "engine_commissioning",
            "melts_domain_gate",
        }:
            return True
        if notice.get("authority") == "extrapolated":
            return True
        if notice.get("run_anyway") is True:
            return True
    return False


def classify_cell(
    base: Mapping[str, Any] | None,
    tip: Mapping[str, Any] | None,
) -> str:
    if base is None or tip is None:
        return VERDICT_OTHER
    equal = {
        field: _field_equal(field, base.get(field), tip.get(field))
        for field in COMPARE_FIELDS
    }
    if all(equal.values()):
        return VERDICT_IDENTICAL
    numeric_equal = all(
        equal[field]
        for field in (
            "melt_activities",
            "gas_partial_pressures_Pa",
            "liquid_fraction",
        )
    )
    if (
        numeric_equal
        and equal["status"]
        and equal["refusal_reason"]
        and _is_crash(base)
        and _is_crash(tip)
        and base.get("engine_reason") != CRASH_ANNOTATION_REASON
        and tip.get("engine_reason") == CRASH_ANNOTATION_REASON
    ):
        return VERDICT_CRASH_ANNOTATION
    if (
        base.get("status") in REFUSAL_STATUSES
        and tip.get("status") in OK_STATUSES
        and not _is_crash(base)
        and _has_notice(tip)
    ):
        return VERDICT_RECLASSIFIED
    return VERDICT_OTHER


def diff_captures(
    base_capture: Mapping[str, Any],
    tip_capture: Mapping[str, Any],
) -> dict[str, Any]:
    base_cells = {
        cell_identity(cell): cell for cell in base_capture["cells"]
    }
    tip_cells = {
        cell_identity(cell): cell for cell in tip_capture["cells"]
    }
    keys = sorted(set(base_cells) | set(tip_cells))
    cells = []
    counts = {
        VERDICT_IDENTICAL: 0,
        VERDICT_RECLASSIFIED: 0,
        VERDICT_CRASH_ANNOTATION: 0,
        VERDICT_OTHER: 0,
    }
    for key in keys:
        base = base_cells.get(key)
        tip = tip_cells.get(key)
        verdict = classify_cell(base, tip)
        counts[verdict] += 1
        cells.append(
            {
                "identity": identity_label(key),
                "pot_id": key[0],
                "engine": key[1],
                "temperature_K": key[2],
                "po2_mode": key[3],
                "po2_bar": key[4],
                "arm": key[5],
                "verdict": verdict,
                "base": _snapshot(base),
                "tip": _snapshot(tip),
            }
        )
    other = [row for row in cells if row["verdict"] == VERDICT_OTHER]
    return {
        "hostname_base": base_capture.get("hostname"),
        "hostname_tip": tip_capture.get("hostname"),
        "revision_id_base": base_capture.get("revision_id"),
        "revision_id_tip": tip_capture.get("revision_id"),
        "run_timestamp_base": base_capture.get("run_timestamp"),
        "run_timestamp_tip": tip_capture.get("run_timestamp"),
        "n_base_cells": len(base_cells),
        "n_tip_cells": len(tip_cells),
        "n_cells": len(cells),
        "n_identical": counts[VERDICT_IDENTICAL],
        "n_reclassified_refusal_to_notice": counts[VERDICT_RECLASSIFIED],
        "n_crash_annotation": counts[VERDICT_CRASH_ANNOTATION],
        "n_OTHER": counts[VERDICT_OTHER],
        "other_identities": [row["identity"] for row in other],
        "cells": cells,
    }


def render_markdown(diff: Mapping[str, Any]) -> str:
    lines = [
        "# Commissioning Studio 1 cell diff",
        "",
        f"- hostname base: `{diff.get('hostname_base')}`",
        f"- hostname tip: `{diff.get('hostname_tip')}`",
        f"- base SHA: `{diff.get('revision_id_base')}`",
        f"- tip SHA: `{diff.get('revision_id_tip')}`",
        f"- base timestamp: `{diff.get('run_timestamp_base')}`",
        f"- tip timestamp: `{diff.get('run_timestamp_tip')}`",
        f"- base cells: {diff.get('n_base_cells')}",
        f"- tip cells: {diff.get('n_tip_cells')}",
        f"- table rows: {diff.get('n_cells')}",
        f"- identical: {diff.get('n_identical')}",
        f"- refusal→notice: {diff.get('n_reclassified_refusal_to_notice')}",
        f"- crash-floor annotation: {diff.get('n_crash_annotation')}",
        f"- OTHER: {diff.get('n_OTHER')}",
        "",
        "Compared fields: status, refusal_reason, engine_reason, "
        "melt_activities, gas_partial_pressures_Pa, liquid_fraction.",
        "",
        "| identity | verdict | base status | tip status | base refusal | tip refusal |",
        "|---|---|---|---|---|---|",
    ]
    for row in diff.get("cells") or []:
        base = row.get("base") or {}
        tip = row.get("tip") or {}
        lines.append(
            "| `{id}` | {verdict} | {bs} | {ts} | {br} | {tr} |".format(
                id=row["identity"],
                verdict=row["verdict"],
                bs=base.get("status"),
                ts=tip.get("status"),
                br=base.get("refusal_reason"),
                tr=tip.get("refusal_reason"),
            )
        )
    others = diff.get("other_identities") or []
    lines.extend(["", "## OTHER cells", ""])
    if others:
        lines.extend(f"- `{identity}`" for identity in others)
    else:
        lines.append("(none)")
    lines.append("")
    return "\n".join(lines) + "\n"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--tip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="optional markdown table path (default: output with .md)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    diff = diff_captures(load_capture(args.base), load_capture(args.tip))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(diff, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path = args.markdown
    if markdown_path is None:
        markdown_path = args.output.with_suffix(".md")
    markdown_path.write_text(render_markdown(diff), encoding="utf-8")
    print(f"hostname_base={diff['hostname_base']}")
    print(f"hostname_tip={diff['hostname_tip']}")
    print(f"n_cells={diff['n_cells']}")
    print(f"n_identical={diff['n_identical']}")
    print(f"n_reclassified_refusal_to_notice={diff['n_reclassified_refusal_to_notice']}")
    print(f"n_crash_annotation={diff['n_crash_annotation']}")
    print(f"n_OTHER={diff['n_OTHER']}")
    print(f"json={args.output}")
    print(f"markdown={markdown_path}")
    if diff["n_OTHER"]:
        print("OTHER: " + ", ".join(diff["other_identities"]))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
