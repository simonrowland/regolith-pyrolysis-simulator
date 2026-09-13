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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.probe_only:
        handles = probe_battery_engines(BATTERY_ENGINE_NAMES)
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
        report = run_engine_arm(
            pots_path=args.pots, progress_log=args.progress_log
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
