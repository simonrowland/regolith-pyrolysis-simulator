#!/usr/bin/env python3
"""Merge per-node grind cache DBs into one consolidated reduced-real cache.

Uses the shared cache-source merge path: each source row is verified before
either store is migrated; stale destination rows are replaced, and only current
rows with different payload bytes are collisions. Nodes that ran disjoint
feedstocks should produce no collisions.

Usage: merge_grind_cache.py <target.db> <source1.db> [<source2.db> ...]
"""
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.seed_reduced_real_cache import (  # noqa: E402
    payload_count,
    validate_cache_source_rows,
)
from simulator.reduced_real_determinism import (  # noqa: E402
    _copy_pt1_equilibrium_rows,
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
        # Validate source content before the shared helper migrates the target.
        admitted_key_hashes = {
            str(row["key_hash"])
            for _source, rows in source_rows
            for row in rows
        }
        result = _copy_pt1_equilibrium_rows(
            sources,
            target,
            require_capture_epoch_match=False,
            key_hashes=admitted_key_hashes,
        )
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    total_inserted = int(result["inserted_rows"])
    source_by_path = {source.resolve(): rows for source, rows in source_rows}
    for source_result in result["sources"]:
        src = Path(source_result["source"])
        admitted_rows = source_by_path[src.resolve()]
        source_count = payload_count(src)
        print(
            f"merged {src.name}: source_rows={source_count} "
            f"superseded_rows={source_count - len(admitted_rows)} "
            f"inserted={source_result['inserted_rows']}"
        )
    final = payload_count(target)
    print(f"TARGET {target}: payload_rows={final} total_inserted={total_inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
