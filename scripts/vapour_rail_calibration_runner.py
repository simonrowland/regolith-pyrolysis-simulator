#!/usr/bin/env python3
"""VR-10 warm-pool VapoRock calibration runner + progressive-validation report.

Offline / golden-neutral. Hard requirements (DESIGN-REV5 §5.5, owner warm-pool
ruling):

* VapoRock executes **only** through the VR-5 warm pool
  (``warm_worker=True``).
* No VapoRock result cache, calibration cache, or new cache-key dimension.
* Raw cells land in a SQLite research store under docs-private (gitignored
  ``*.sqlite``); runtime never reads it.
* Only a reviewed ``data/vapour_rail_calibration.yaml`` sidecar may enter
  runtime data — this CLI can emit a **draft** sidecar for review; promotion
  is a separate human approval step.

Usage examples::

    # Dry corpus / report only (no VapoRock process):
    python scripts/vapour_rail_calibration_runner.py --report-only \\
        --output-dir /tmp/vr10-report

    # Full warm-pool campaign (requires vaporock install):
    python scripts/vapour_rail_calibration_runner.py \\
        --calibration-id vr10-2026-07-31 \\
        --store docs-private/research/vapour-rail-calibration/run.sqlite \\
        --write-draft-sidecar /tmp/vapour_rail_calibration.DRAFT.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.vapour_rail.calibration import (  # noqa: E402
    DEFAULT_CALIBRATION_SPECIES,
    DEFAULT_EPSILON_J,
    DEFAULT_P_FLOOR_PA,
    DEFAULT_RESEARCH_STORE_DIR,
    DEFAULT_SIDECAR_PATH,
    CalibrationRunnerError,
    build_calibration_cells,
    build_progressive_validation_report,
    build_sidecar_document,
    default_holdout_plan,
    open_warm_vaporock_backend,
    run_calibration_campaign,
    temperature_grid_K,
    write_sidecar,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--calibration-id",
        default=None,
        help="stable calibration ID (default: auto timestamped vr10-...)",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        help="SQLite research store path (offline only; never runtime)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="directory for JSON/Markdown progressive-validation reports",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="build planned corpus + progressive report without calling VapoRock",
    )
    parser.add_argument(
        "--write-draft-sidecar",
        type=Path,
        default=None,
        help="write a draft reviewed-sidecar YAML for human approval",
    )
    parser.add_argument(
        "--epsilon-j",
        type=float,
        default=DEFAULT_EPSILON_J,
        help="owner relative flux error budget ε_J (default: %(default)s)",
    )
    parser.add_argument(
        "--p-floor-pa",
        type=float,
        default=DEFAULT_P_FLOOR_PA,
        help="censored sub-floor pressure in Pa (default: %(default)s)",
    )
    parser.add_argument(
        "--warm-pool-size",
        type=int,
        default=1,
        help="VR-5 warm pool size (default: 1)",
    )
    parser.add_argument(
        "--species",
        nargs="+",
        default=list(DEFAULT_CALIBRATION_SPECIES),
        help="species subset (must be in frozen family table)",
    )
    parser.add_argument(
        "--max-cells",
        type=int,
        default=None,
        help="optional cap on cells for smoke runs",
    )
    return parser.parse_args(argv)


def _default_calibration_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"vr10-{stamp}"


def _write_reports(report_dict: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "progressive_validation_report.json"
    md_path = output_dir / "progressive_validation_report.md"
    json_path.write_text(json.dumps(report_dict, indent=2, sort_keys=True) + "\n")

    lines = [
        f"# Progressive validation report — `{report_dict['calibration_id']}`",
        "",
        f"- authority: `{report_dict.get('authority')}`",
        f"- certifies: `{report_dict.get('certifies')}`",
        "",
        "## Domain",
        "",
        "```json",
        json.dumps(report_dict.get("domain"), indent=2),
        "```",
        "",
        "## Per-row pending / validated state",
        "",
    ]
    for row in report_dict.get("per_row_state") or []:
        lines.append(
            f"- **{row['species']}**: `{row['validation_status']}` "
            f"(family={row['family_id']}, cap={row['parameter_cap']}, "
            f"blockers={row.get('flip_blockers')})"
        )
    lines.extend(
        [
            "",
            "## Remaining pending set",
            "",
            f"- calibration candidates still pending: "
            f"{sum(1 for r in report_dict.get('per_row_state') or [] if r.get('validation_status') == 'pending_validation')}",
            f"- rail pending summary entries: "
            f"{len(report_dict.get('remaining_pending') or [])}",
            "",
            "## Source-selectable / refused fractions",
            "",
            "```json",
            json.dumps(report_dict.get("source_selection_fractions"), indent=2),
            "```",
            "",
            "## Downstream error budget",
            "",
            "```json",
            json.dumps(report_dict.get("error_budget"), indent=2),
            "```",
            "",
            "## Boundary statistics",
            "",
            f"- n_records: {len(report_dict.get('boundary_statistics') or [])}",
            "",
            "## Cell counts",
            "",
            "```json",
            json.dumps(report_dict.get("cell_counts"), indent=2),
            "```",
            "",
            "## Notes",
            "",
        ]
    )
    for note in report_dict.get("notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    md_path.write_text("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    calibration_id = args.calibration_id or _default_calibration_id()
    cells = build_calibration_cells()
    if args.max_cells is not None:
        if args.max_cells < 1:
            raise SystemExit("--max-cells must be >= 1")
        cells = cells[: args.max_cells]

    output_dir = args.output_dir or (
        DEFAULT_RESEARCH_STORE_DIR / calibration_id / "report"
    )

    if args.report_only:
        report = build_progressive_validation_report(
            calibration_id=calibration_id,
            cells=cells,
            epsilon_J=float(args.epsilon_j),
        )
        report_dict = report.as_dict()
        _write_reports(report_dict, output_dir)
        print(f"wrote report-only artifacts under {output_dir}")
        if args.write_draft_sidecar is not None:
            doc = build_sidecar_document(
                calibration_id=calibration_id,
                raw_store_digest=None,
                raw_store_path=None,
                report=report,
                approval="draft_unreviewed",
            )
            write_sidecar(args.write_draft_sidecar, doc)
            print(f"wrote draft sidecar {args.write_draft_sidecar}")
        print(
            json.dumps(
                {
                    "calibration_id": calibration_id,
                    "planned_cells": len(cells),
                    "temperatures_K": list(temperature_grid_K()),
                    "holdout": report.holdout,
                    "runtime_sidecar": str(DEFAULT_SIDECAR_PATH),
                },
                indent=2,
            )
        )
        return 0

    store_path = args.store or (
        DEFAULT_RESEARCH_STORE_DIR / calibration_id / "cells.sqlite"
    )
    backend = None
    try:
        backend = open_warm_vaporock_backend(
            warm_pool_size=int(args.warm_pool_size)
        )
        report = run_calibration_campaign(
            store_path=store_path,
            calibration_id=calibration_id,
            backend=backend,
            cells=cells,
            species=tuple(args.species),
            p_floor_Pa=float(args.p_floor_pa),
            epsilon_J=float(args.epsilon_j),
            close_backend=False,
        )
    except CalibrationRunnerError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        if backend is not None:
            backend.close()

    report_dict = report.as_dict()
    _write_reports(report_dict, output_dir)
    print(f"wrote research store {store_path}")
    print(f"wrote report artifacts under {output_dir}")

    if args.write_draft_sidecar is not None:
        # Digest is stored in the research DB; re-open is offline-only.
        from simulator.vapour_rail.calibration import CalibrationResearchStore

        with CalibrationResearchStore(store_path) as store:
            digest = store.get_meta("raw_store_digest")
        doc = build_sidecar_document(
            calibration_id=calibration_id,
            raw_store_digest=digest,
            raw_store_path=str(store_path),
            report=report,
            approval="draft_unreviewed",
        )
        write_sidecar(args.write_draft_sidecar, doc)
        print(f"wrote draft sidecar {args.write_draft_sidecar}")

    print(
        json.dumps(
            {
                "calibration_id": calibration_id,
                "store": str(store_path),
                "evaluated_cells": report.cell_counts.get("evaluated_cells"),
                "ok": report.cell_counts.get("ok"),
                "refused": report.cell_counts.get("refused"),
                "holdout": report.holdout,
                "plan": {
                    "default_holdout_family": default_holdout_plan().held_out_formulation_family,
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
