#!/usr/bin/env python3
"""Regenerate canonical runner goldens from their complete scenario inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_runner_smoke import FIXTURES_DIR, SCENARIOS, _run_scenario


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    generated = [(scenario, _run_scenario(scenario)) for scenario in SCENARIOS]
    failures = [
        (scenario["name"], payload.get("status"), payload.get("reason", ""))
        for scenario, payload in generated
        if payload.get("status") != "ok"
    ]
    if failures:
        details = "; ".join(
            f"{name}: status={status!r} reason={reason!r}"
            for name, status, reason in failures
        )
        raise RuntimeError(f"runner golden regeneration refused: {details}")

    for scenario, payload in generated:
        output = FIXTURES_DIR / scenario["fixture"]
        _atomic_write_json(output, payload)
        print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
