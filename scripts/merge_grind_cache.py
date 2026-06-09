#!/usr/bin/env python3
"""Merge per-node grind cache DBs into one consolidated reduced-real cache.

Reuses the audited, collision-detecting `_merge_cache_shard` helper from the
populate script: each source row is verified against the target; a key_hash that
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

from scripts.populate_reduced_real_cache import _merge_cache_shard  # noqa: E402
from simulator.reduced_real_determinism import (  # noqa: E402
    PT1PersistentEquilibriumStore,
)


def _payload_count(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT count(*) FROM reduced_real_equilibrium_payloads"
        ).fetchone()[0]
    finally:
        con.close()


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    target = Path(argv[0]).expanduser()
    sources = [Path(p).expanduser() for p in argv[1:]]
    if target.exists():
        print(f"refusing to overwrite existing target: {target}")
        return 2
    # Create the canonical schema on the (new) target.
    PT1PersistentEquilibriumStore(target)
    total_inserted = 0
    for src in sources:
        src_rows = _payload_count(src)
        result = _merge_cache_shard(src, target)
        ins = int(result.get("inserted_rows", 0))
        total_inserted += ins
        print(f"merged {src.name}: source_rows={src_rows} inserted={ins}")
    final = _payload_count(target)
    print(f"TARGET {target}: payload_rows={final} total_inserted={total_inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
