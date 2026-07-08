#!/usr/bin/env python3
"""Run the time-series validation lake harness and write the report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.diagnostic_helpers.timeseries_validation_lake import (
    DEFAULT_DATA_ROOT,
    load_catalog,
    render_markdown_report,
    validate_lake,
)


DEFAULT_REPORT = (
    Path("docs-private")
    / "research"
    / "2026-07-05-timeseries-datasets"
    / "validation-report.md"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=DEFAULT_DATA_ROOT,
        help="canonical validation-data/timeseries root",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="markdown report path",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="optional JSON report path",
    )
    args = parser.parse_args()

    reports = validate_lake(args.data_root)
    catalog = load_catalog(args.data_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_markdown_report(reports, catalog=catalog))

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([report.as_dict() for report in reports], indent=2, sort_keys=True)
            + "\n"
        )

    validated = [report for report in reports if report.status == "validated"]
    print(
        "REPORT "
        f"datasets={len(reports)} "
        f"validated={len(validated)} "
        f"report={args.report}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
