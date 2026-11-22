#!/usr/bin/env python3
"""Snapshot live caches, accumulate a master, and seed new box-local files.

expire and prune are manual. They print counts and delete nothing unless
``--apply`` is set. Nothing in the cache read or write path calls them.
"""

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


def _add_apply_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--apply",
        action="store_true",
        help="delete the matched rows and vacuum. The default is a dry run",
    )
    command.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="print counts and delete nothing. This is the default",
    )


def _apply(parser: argparse.ArgumentParser, args: argparse.Namespace) -> bool:
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run cannot be combined")
    return bool(args.apply)


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
    expire = commands.add_parser(
        "expire",
        help="delete one engine's rows whose recorded inputs include a named species",
    )
    expire.add_argument("file", type=Path)
    expire.add_argument("--engine", required=True)
    choice = expire.add_mutually_exclusive_group(required=True)
    choice.add_argument("--species", help="comma-separated species names")
    choice.add_argument(
        "--all",
        action="store_true",
        dest="all_rows",
        help="delete every row of --engine, including rows with no recorded species",
    )
    _add_apply_flags(expire)
    prune = commands.add_parser(
        "prune",
        help="delete rows whose chemistry version is not the current one",
    )
    prune.add_argument("file", type=Path)
    _add_apply_flags(prune)
    migrate = commands.add_parser(
        "migrate",
        help="convert a schema-3 HOT/COLD pair in place without deleting values",
    )
    migrate.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command in {"snapshot", "seed"}:
            result = cache.snapshot_cache(args.source, args.destination)
        elif args.command == "merge":
            result = cache.merge_cache_files(args.master, args.inputs, vacuum=args.vacuum)
        elif args.command == "expire":
            species = None
            if args.species is not None:
                species = tuple(
                    part.strip() for part in args.species.split(",") if part.strip()
                )
            result = cache.expire_cache_file(
                args.file,
                engine=args.engine,
                species=species,
                all_rows=bool(args.all_rows),
                apply=_apply(parser, args),
            )
        elif args.command == "prune":
            result = cache.prune_cache_file(args.file, apply=_apply(parser, args))
        elif args.command == "migrate":
            result = cache.migrate_cache_file(args.file)
        else:
            result = cache.engine_cache_stats(args.file.expanduser())
    except (OSError, sqlite3.Error, ValueError, cache.EngineCacheSchemaMismatch) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 2 if args.command == "merge" and args.strict and result["conflicts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
