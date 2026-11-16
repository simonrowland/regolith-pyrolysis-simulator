#!/usr/bin/env python3
"""Snapshot, accumulate and redistribute the engine cache without replacing rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from contextlib import ExitStack, contextmanager
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator import engine_cache as cache  # noqa: E402


@contextmanager
def _read_cache(path: Path):
    # immutable=1 would ignore committed rows still in a live writer's WAL.
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=cache._TIMEOUT_MS / 1000)
    try:
        connection.execute("BEGIN")
        result = connection.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise ValueError(f"integrity_check failed for {path}: {result}")
        connection.execute(
            f"SELECT key, value, engine, chem_version, source_box, row_seq "
            f"FROM {cache._TABLE} LIMIT 0"
        )
        yield connection
    finally:
        connection.close()


def _stats(path: Path, connection: sqlite3.Connection) -> dict:
    groups = [
        dict(zip(("engine", "chem_version", "source_box", "rows"), row))
        for row in connection.execute(
            f"SELECT engine, chem_version, source_box, COUNT(*) FROM {cache._TABLE} "
            "GROUP BY engine, chem_version, source_box ORDER BY engine, chem_version, source_box"
        )
    ]
    by_engine: Counter = Counter()
    for group in groups:
        by_engine[group["engine"]] += group["rows"]
    count = sum(by_engine.values())
    size = path.stat().st_size
    return {
        "file": str(path), "rows": count, "groups": groups,
        "by_engine": dict(by_engine), "file_bytes": size,
        "bytes_per_row": size / count if count else None,
    }


def _snapshot(live: Path, out: Path) -> dict:
    with _read_cache(live) as source:
        # Exclusive creation also refuses aliases of the live file and races.
        with out.open("xb"):
            pass
        destination = sqlite3.connect(out)
        try:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode=DELETE")
        finally:
            destination.close()
    with _read_cache(out) as connection:
        result = _stats(out, connection)
    result["sha256"] = cache._file_sha256(out)
    return result


def _union(into: Path, inputs: list[Path]) -> dict:
    by_engine: dict[str, dict[str, int]] = {}
    by_box: dict[str, dict[str, int]] = {}
    report_path = Path(str(into) + ".conflicts.jsonl")
    with ExitStack() as stack:
        # Validate every input before even creating a new destination or report.
        sources = [stack.enter_context(_read_cache(path)) for path in inputs]
        destination = cache._connect(into, readonly=False)
        assert destination is not None
        report = stack.enter_context(report_path.open("a", encoding="utf-8"))
        destination.execute("BEGIN IMMEDIATE")
        try:
            for source in sources:
                for key, value, engine, version, box, seq in source.execute(
                    f"SELECT key, value, engine, chem_version, source_box, row_seq FROM {cache._TABLE}"
                ):
                    inserted, conflict = cache._insert_cache_row_if_absent(
                        destination, key, value, engine, version, box, seq,
                    )
                    for dimension, name in ((by_engine, engine), (by_box, box)):
                        counts = dimension.setdefault(name, dict.fromkeys(
                            ("read", "inserted", "already_present", "conflicts"), 0,
                        ))
                        counts["read"] += 1
                        counts["inserted"] += int(inserted)
                        counts["already_present"] += int(not inserted and not conflict)
                        counts["conflicts"] += int(conflict)
                    if conflict:
                        kept_value, kept_box = destination.execute(
                            f"SELECT value, source_box FROM {cache._TABLE} WHERE key=?", (key,),
                        ).fetchone()
                        report.write(json.dumps({
                            "key": key.hex(), "engine": engine, "chem_version": version,
                            "source_boxes": [kept_box, box],
                            "value_sha256": [hashlib.sha256(kept_value).hexdigest(), hashlib.sha256(value).hexdigest()],
                        }, sort_keys=True) + "\n")
            report.flush()
            destination.commit()
        except Exception:
            destination.rollback()
            raise
    return {
        "file": str(into), "by_engine": by_engine, "by_source_box": by_box,
        "conflict_report": str(report_path),
    }


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
            result = _snapshot(args.live.expanduser(), args.out.expanduser())
        elif args.command == "stats":
            path = args.file.expanduser()
            with _read_cache(path) as connection:
                result = _stats(path, connection)
        else:
            inputs = args.snapshots if args.command == "merge" else [args.source]
            result = _union(args.into.expanduser(), [path.expanduser() for path in inputs])
    except (OSError, sqlite3.Error, ValueError, cache.EngineCacheSchemaMismatch) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
