#!/usr/bin/env python3
"""Regenerate the species-rail differential ledger and the JSON/MD report.

Internal-consistency instrument: residuals are the result. No pass/fail
threshold, no coefficient retune, no scoring_eligible rows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.diagnostic_helpers.gibbs_battery import (  # noqa: E402
    LEDGER_PATH as GIBBS_PILOT_LEDGER_PATH,
)
from simulator.diagnostic_helpers.species_rail_differential import (  # noqa: E402
    LEDGER_PATH,
    REPORT_DIR,
    run_harness,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=LEDGER_PATH,
        help="species-rail differential ledger path (default: %(default)s)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPORT_DIR,
        help="directory for report.json and report.md (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.ledger.resolve() == GIBBS_PILOT_LEDGER_PATH.resolve():
        raise SystemExit("refusing to overwrite the gibbs-battery pilot ledger")
    before = GIBBS_PILOT_LEDGER_PATH.read_bytes()
    report = run_harness(ledger_path=args.ledger, report_dir=args.output_dir)
    after = GIBBS_PILOT_LEDGER_PATH.read_bytes()
    if after != before:
        raise SystemExit("gibbs-battery pilot ledger changed; abort")
    counts = report["counts"]
    n_scored = sum(
        row["n"]
        for row in counts
        if row["status"] in {"match", "mismatch"}
        and row["channel"] in {"nasa_cea_9", "ellingham"}
    )
    n_refused = sum(row["n"] for row in counts if row["status"] == "typed-refusal")
    print(f"points_scored={n_scored}")
    print(f"points_refused={n_refused}")
    print(f"top20={len(report['top20_major_residual'])}")
    print(f"self_check_failures={len(report['table_self_check_failures'])}")
    print(f"ledger={args.ledger}")
    print(f"json={args.output_dir / 'report.json'}")
    print(f"markdown={args.output_dir / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
