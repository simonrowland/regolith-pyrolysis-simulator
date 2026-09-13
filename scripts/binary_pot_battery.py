#!/usr/bin/env python3
"""Run the binary-pot engine-arm battery.

Every melt engine is resolved through resolve_backend (VapoRock via the
VR-5 warm pool) and called with equilibrate() on the same small oxide-pair
pots. Residuals are the result; there is no pass/fail gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.diagnostic_helpers.binary_pot_battery import (  # noqa: E402
    BATTERY_ENGINE_NAMES,
    DEFAULT_POTS_PATH,
    REPORT_DIR,
    load_cells_from_report,
    load_engine_blocks_from_report,
    probe_battery_engines,
    recompute_residuals_from_report,
    run_engine_arm,
    write_reports,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORT_DIR,
        help="directory for binary-pot-engine-arm.json and .md (default: %(default)s)",
    )
    parser.add_argument(
        "--pots",
        type=Path,
        default=DEFAULT_POTS_PATH,
        help="binary pot catalog (default: %(default)s)",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="resolve engines, print availability, and exit without equilibrate()",
    )
    parser.add_argument(
        "--progress-log",
        type=Path,
        default=None,
        help="append one line per cell (and START/DONE) for detached polling",
    )
    parser.add_argument(
        "--from-json",
        type=Path,
        default=None,
        help="re-score an existing engine-arm JSON (no engine re-run)",
    )
    parser.add_argument(
        "--engines",
        default=None,
        help="comma-separated engine names (default: all battery engines, including IMCC)",
    )
    parser.add_argument(
        "--qualification",
        action="store_true",
        help=(
            "MELTS QUALIFICATION mode: record domain-gate verdict as "
            "authority=extrapolated and run anyway in an isolated subprocess"
        ),
    )
    parser.add_argument(
        "--include-scoring-pots",
        action="store_true",
        help="also run the 30 Kambayashi/Ohara scoring pots at their extract T",
    )
    parser.add_argument(
        "--reuse-cells",
        type=Path,
        action="append",
        default=None,
        help="reuse cells from an existing engine-arm/scoring JSON (repeatable)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    engine_names = BATTERY_ENGINE_NAMES
    if args.engines:
        engine_names = tuple(
            name.strip() for name in str(args.engines).split(",") if name.strip()
        )
    if args.probe_only:
        handles = probe_battery_engines(engine_names)
        payload = {
            name: {
                "available": handle.available,
                "unavailable_reason": handle.unavailable_reason,
                "takes_fo2": handle.takes_fo2,
                "identity": dict(handle.identity),
            }
            for name, handle in handles.items()
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.from_json is not None:
        source = Path(args.from_json)
        report = recompute_residuals_from_report(
            json.loads(source.read_text(encoding="utf-8"))
        )
    else:
        reuse_cells = []
        reuse_engine_blocks: dict = {}
        for path in args.reuse_cells or []:
            reuse_cells.extend(load_cells_from_report(path))
            reuse_engine_blocks.update(load_engine_blocks_from_report(path))
        report = run_engine_arm(
            pots_path=args.pots,
            progress_log=args.progress_log,
            engine_names=engine_names,
            qualification=bool(args.qualification),
            include_scoring_pots=bool(args.include_scoring_pots),
            reuse_cells=reuse_cells or None,
            reuse_engine_blocks=reuse_engine_blocks or None,
        )
    json_path, markdown_path = write_reports(report, args.output_dir)
    print(f"hostname={report['hostname']}")
    print(f"n_cells={report['n_cells']}")
    print(f"n_ok={report['n_ok']}")
    print(f"n_refused={report['n_refused']}")
    print(f"n_matched_residuals={report['n_matched_residuals']}")
    print(f"n_floor_refusals={report.get('n_floor_refusals', 0)}")
    print(f"json={json_path}")
    print(f"markdown={markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
