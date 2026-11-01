#!/usr/bin/env python3
"""Validate a migrated battery store and write its deterministic record.

Exit codes:
  0: the record was written and contains no forbidden hard-issue shapes.
  1: the record was written and contains one or more forbidden shapes.
  2: command usage, store loading, or record writing failed.

The JSON contains only store-derived content. Run metadata is printed to stdout.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.store_validation import validate_migrated_store  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="migrated repository root (defaults to this checkout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    started = time.perf_counter()
    cpu_started = time.process_time()
    try:
        record = validate_migrated_store(args.root)
        head = subprocess.run(
            ["git", "-C", str(args.root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        output_path = args.root / "data" / "battery" / "store-validation.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                record,
                ensure_ascii=False,
                allow_nan=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"battery store validation failed: {exc}", file=sys.stderr)
        return 2

    print(f"HEAD sha: {head}")
    print(f"validated at UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    print(f"wall seconds: {time.perf_counter() - started:.3f}")
    print(f"process CPU seconds: {time.process_time() - cpu_started:.3f}")
    print(f"wrote {output_path}")
    print(f"forbidden shape count: {record['forbidden_shape_count']}")
    return 0 if record["forbidden_shape_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
