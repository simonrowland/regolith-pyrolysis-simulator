#!/usr/bin/env python3
"""Lift the empirical battery into schema v2.1 records.

Writes works, extract-v2 / observations-v2 siblings, the alias registry,
migration-queue.yaml, and migration-report.md. Does not rewrite extract
sources, generate residuals, or touch pins.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import migrate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the lift without writing outputs",
    )
    args = parser.parse_args(argv)
    result = migrate(args.root, write=not args.dry_run, validate=True)
    hard = 0 if result.validation is None else len(result.validation.hard_issues)
    print(
        f"rows_in={sum(c.rows_in for c in result.source_counts.values())} "
        f"observations={len(result.observations)} works={len(result.works)} "
        f"experiments={len(result.experiments)} queue={len(result.queue)} "
        f"hard_issues={hard}"
    )
    return 0 if hard == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
