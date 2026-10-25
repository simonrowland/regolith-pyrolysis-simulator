#!/usr/bin/env python3
"""Regenerate canonical runner goldens from their complete scenario inputs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_runner_smoke import FIXTURES_DIR, SCENARIOS, SCENARIO_FLUX_LEDGER_PINS, _run_scenario, _flux_ledger_pins
from tests.binding_admission_fixtures import isolate_engine_local_config


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report pins and check fixtures without writing")
    args = parser.parse_args()
    from simulator import engine_local_config

    source_path = engine_local_config.config_path()
    with tempfile.TemporaryDirectory(
        prefix="runner-golden-engine-config-"
    ) as directory:
        with isolate_engine_local_config(
            Path(directory) / "engines",
            source_path=source_path,
        ):
            generated = [
                (scenario, *_run_scenario(scenario, capture=True)) for scenario in SCENARIOS
            ]
    failures = [
        (scenario["name"], payload.get("status"), payload.get("reason", ""))
        for scenario, payload, capture in generated
        if payload.get("status") != "ok"
    ]
    if failures:
        details = "; ".join(
            f"{name}: status={status!r} reason={reason!r}"
            for name, status, reason in failures
        )
        raise RuntimeError(f"runner golden regeneration refused: {details}")

    for scenario, payload, capture in generated:
        output = FIXTURES_DIR / scenario["fixture"]
        print(json.dumps({"scenario": scenario["name"], **_flux_ledger_pins(capture)}))
        if args.check:
            assert _flux_ledger_pins(capture) == SCENARIO_FLUX_LEDGER_PINS[scenario["name"]]
            assert output.read_text(encoding="utf-8") == json.dumps(payload, indent=2, allow_nan=False) + "\n"
        else:
            _atomic_write_json(output, payload)
        print(output.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
