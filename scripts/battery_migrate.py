#!/usr/bin/env python3
"""Lift the empirical battery into schema v2.1 records.

Writes works, extract-v2 / observations-v2 siblings, the alias registry,
migration-queue.yaml, and migration-report.md. Does not rewrite extract
sources, generate residuals, or touch pins.

Completed writes exit 0 even when the report contains known hard issues.
Use --check-store to verify that regenerating from the current inputs would
produce byte-identical derived-store files without changing the worktree.

Landing a store change is four steps:

1. .venv/bin/python scripts/battery_migrate.py
2. .venv/bin/python data/literature/build_index.py --write-store-summary
3. .venv/bin/python scripts/battery_migrate.py --check-store
4. the battery gate

``python -m simulator.battery.migrate`` is the same CLI.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
