#!/usr/bin/env python3
"""Freshly migrate and score the full battery store as a smoke check."""

from __future__ import annotations

import multiprocessing
from collections import Counter
from pathlib import Path

from simulator.battery.enums import Engine
from simulator.battery.migrate import migrate
from simulator.battery.score import load_score_context, score_store


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    migrated = migrate(root, write=True)
    context = load_score_context(root)
    residuals, candidates = score_store(
        context,
        engines=(Engine.INTERNAL_ANALYTICAL,),
        include_diagnostics=False,
    )
    banded = sum(
        residual.numeric is not None
        and residual.numeric.decision_band is not None
        for residual in residuals
    )
    statuses = Counter(residual.status.value for residual in residuals)
    print(
        f"fresh_migration_observations={len(migrated.observations)} "
        f"engine={Engine.INTERNAL_ANALYTICAL.value} "
        "include_diagnostics=False "
        f"scored_rows={len(residuals)} candidates={len(candidates)} "
        f"rows_with_band={banded} statuses={dict(sorted(statuses.items()))}"
    )
    print("expected_band_changes_vs_8ab765717=0")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
