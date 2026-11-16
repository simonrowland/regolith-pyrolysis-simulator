#!/usr/bin/env python3
"""Snapshot, accumulate and redistribute the engine cache without replacing rows."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator import engine_cache as cache  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="back up a live WAL cache to a new file")
    snapshot.add_argument("live", type=Path)
    snapshot.add_argument("out", type=Path)
    merge = commands.add_parser("merge", help="union snapshots into a canonical cache")
    merge.add_argument("--into", required=True, type=Path)
    merge.add_argument("snapshots", nargs="+", type=Path)
    seed = commands.add_parser("seed", help="add canonical rows to a live cache without replacing it")
    seed.add_argument("--from", dest="source", required=True, type=Path)
    seed.add_argument("--into", required=True, type=Path)
    stats = commands.add_parser("stats", help="show row counts, main-file bytes and bytes per row")
    stats.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = cache.snapshot_cache(args.live.expanduser(), args.out.expanduser())
        elif args.command == "stats":
            path = args.file.expanduser()
            result = cache.engine_cache_stats(path)
        else:
            inputs = args.snapshots if args.command == "merge" else [args.source]
            result = cache.merge_cache_files(args.into.expanduser(), [path.expanduser() for path in inputs])
    except (OSError, sqlite3.Error, ValueError, cache.EngineCacheSchemaMismatch) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
