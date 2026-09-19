#!/usr/bin/env python3
"""Lift the empirical battery into schema v2.1 records.

Writes works, extract-v2 / observations-v2 siblings, the alias registry,
migration-queue.yaml, and migration-report.md. Does not rewrite extract
sources, generate residuals, or touch pins.

The process exits 1 whenever hard_issues is non-zero, including on a
healthy run against the long-standing baseline. Assert the printed
hard_issues count against that baseline, not the exit status.

Landing a store change is three steps:

1. .venv/bin/python scripts/battery_migrate.py
2. .venv/bin/python data/literature/build_index.py --write-store-summary
3. the battery gate

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
