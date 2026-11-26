#!/usr/bin/env python3
"""Prune PT-1 reduced-real rows with missing or superseded payload codecs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.reduced_real_determinism import (  # noqa: E402
    _prune_pt1_payload_codec_rows,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="PT-1 reduced-real cache database")
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument(
        "--apply",
        action="store_true",
        help="delete matched rows and vacuum; the default is a dry run",
    )
    flags.add_argument(
        "--dry-run",
        action="store_true",
        help="print counts and delete nothing; this is the default",
    )
    args = parser.parse_args(argv)
    try:
        result = _prune_pt1_payload_codec_rows(args.file, apply=args.apply)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    output = {
        "command": "prune",
        "file": str(args.file.expanduser()),
        **result,
    }
    print(json.dumps(output, sort_keys=True))
    vacuum_error = result["vacuum_error"]
    if vacuum_error is not None:
        deleted = sum(result["deleted_by_artifact"].values())
        print(
            f"deleted {deleted}, vacuum failed: {vacuum_error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
