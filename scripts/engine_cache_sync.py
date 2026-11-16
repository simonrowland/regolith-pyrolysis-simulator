#!/usr/bin/env python3
"""Snapshot live caches, accumulate a master, and seed new box-local files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator import engine_cache as cache  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "seed"):
        command = commands.add_parser(name, help="copy a HOT/COLD pair to new files")
        command.add_argument("source", type=Path)
        command.add_argument("destination", type=Path)
    merge = commands.add_parser("merge", help="accumulate new rows, keeping the master's values")
    merge.add_argument("master", type=Path)
    merge.add_argument("inputs", type=Path, nargs="+")
    merge.add_argument("--strict", action="store_true", help="exit 2 after merging if values conflicted")
    merge.add_argument("--vacuum", action="store_true", help="reclaim unused HOT/COLD pages after merge")
    stats = commands.add_parser("stats", help="report allocated HOT/COLD bytes per row")
    stats.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command in {"snapshot", "seed"}:
            result = cache.snapshot_cache(args.source, args.destination)
        elif args.command == "merge":
            result = cache.merge_cache_files(args.master, args.inputs, vacuum=args.vacuum)
        else:
            result = cache.engine_cache_stats(args.file.expanduser())
    except (OSError, sqlite3.Error, ValueError, cache.EngineCacheSchemaMismatch) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 2 if args.command == "merge" and args.strict and result["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
