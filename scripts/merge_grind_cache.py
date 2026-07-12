#!/usr/bin/env python3
"""Merge per-node grind cache DBs into one consolidated reduced-real cache.

Uses the shared, collision-detecting cache-source merge path: each source row is
verified before the target is opened; a key_hash that
exists with a DIFFERING payload aborts loudly (PT-1 cache collision), identical
rows are idempotently skipped. Nodes that ran disjoint feedstocks should produce
no collisions.

Usage: merge_grind_cache.py <target.db> <source1.db> [<source2.db> ...]
"""
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.seed_reduced_real_cache import (  # noqa: E402
    merge_cache_source,
    payload_count,
    validate_cache_source_rows,
    validate_merge_plan,
)
from simulator.reduced_real_determinism import (  # noqa: E402
    PT1PersistentEquilibriumStore,
)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    target = Path(argv[0]).expanduser()
    sources = [Path(p).expanduser() for p in argv[1:]]
    try:
        source_rows = []
        for src in sources:
            rows = validate_cache_source_rows(src)
            source_rows.append((src, rows))
        validate_merge_plan(target, source_rows)
        # Create or validate the canonical target schema only after all sources
        # pass. Existing partial targets are resumable via idempotent row merge.
        PT1PersistentEquilibriumStore(target)
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    total_inserted = 0
    for src, rows in source_rows:
        try:
            result = merge_cache_source(src, target, validated_rows=rows)
        except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        inserted = int(result.get("inserted_rows", 0))
        total_inserted += inserted
        print(f"merged {src.name}: source_rows={len(rows)} inserted={inserted}")
    final = payload_count(target)
    print(f"TARGET {target}: payload_rows={final} total_inserted={total_inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
