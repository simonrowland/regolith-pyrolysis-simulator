#!/usr/bin/env python3
"""Run a VapoRock score pass and print its score-summary cache counters."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path.cwd()
    score_script = root / "scripts" / "battery_score.py"
    if not score_script.is_file():
        raise SystemExit(f"run this probe from the seat root; missing {score_script}")

    with tempfile.TemporaryDirectory(prefix="regolith-engine-cache-not-stored-") as temp:
        cache_path = Path(temp) / "engine-cache.sqlite"
        subprocess.run(
            [
                sys.executable,
                str(score_script),
                "--engines", "vaporock",
                "--workers", "2",
                "--engine-cache", str(cache_path),
                "--root", str(root),
            ],
            cwd=root,
            check=True,
        )

    from scripts.battery_score import (
        _scoring_filter_record,
        _scoring_output_directory,
    )
    from simulator.battery.score import engines_from_names

    filters = _scoring_filter_record(
        engines=engines_from_names(["vaporock"]),
        rail=None,
        work_id=None,
        limit=None,
        species_formulas=(),
    )
    summary_path = (
        _scoring_output_directory(root, filters, subset=True) / "score-summary.json"
    )
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    engine_cache = payload.get("engine_cache")
    by_engine = (
        engine_cache.get("by_engine") if isinstance(engine_cache, dict) else None
    )
    counts = by_engine.get("vaporock") if isinstance(by_engine, dict) else None
    print(
        json.dumps(
            {
                "summary": str(summary_path),
                "summary_keys": sorted(payload),
                "engine_cache_keys": (
                    sorted(engine_cache) if isinstance(engine_cache, dict) else None
                ),
                "vaporock_cache_counters": counts,
                "acceptance": payload.get("acceptance"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
