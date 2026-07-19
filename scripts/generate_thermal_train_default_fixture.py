#!/usr/bin/env python3
"""Generate the read-only Phase-2 default thermal-train artifact."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulator.accounting.queries import AccountingQueries
from simulator.backends import BackendSelectionPolicy
from simulator.session import SimSession, SimSessionConfig


DATA = ROOT / "data"
OUTPUT = DATA / "fixtures" / "thermal_train" / "default-v2.json"


def _load(name: str) -> dict:
    return yaml.safe_load((DATA / name).read_text(encoding="utf-8")) or {}


def main() -> int:
    setpoints = _load("setpoints.yaml")
    # SC-67's prototype opt-in is local to fixture generation.  Runtime and
    # checked-in setpoints remain fail-closed; provenance below records this.
    setpoints["chemistry_kernel"]["allow_unmeasured_alpha_fallback"] = True
    config = SimSessionConfig(
        feedstock_id="lunar_mare_low_ti",
        feedstocks=_load("feedstocks.yaml"),
        setpoints=setpoints,
        vapor_pressures=_load("vapor_pressures.yaml"),
        campaign="C3_NA",
        backend_name="stub",
        backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
        # C3_NA ramps from 25 C at 50 C/hr; 33 snapshots include three
        # plateau hours in the 1500 C evaporation window.
        hours=33,
        mass_kg=1000.0,
        additives_kg={"Na": 12.0},
        track="pyrolysis",
        c5_enabled=False,
    )
    session = SimSession().start(config)
    for _ in range(config.hours):
        session.advance()
    report = AccountingQueries(session.simulator).thermal_train_report()
    artifact = {
        "artifact_schema_version": "thermal-train-default-artifact-v2",
        "artifact_id": "thermal-train-default-v2",
        "config": {
            "feedstock_id": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
            "track": "pyrolysis",
            "campaign": "C3_NA",
            "c3_shuttle_enabled": True,
            "c3_shuttle_recipe": {"Na_kg": 12.0, "K_kg": 0.0},
            "c5_enabled": False,
            "hours": config.hours,
        },
        "provenance": {
            "generator": "scripts/generate_thermal_train_default_fixture.py",
            "command": "MPLCONFIGDIR=/tmp/mpl .venv/bin/python scripts/generate_thermal_train_default_fixture.py",
            "thermal_train_report_schema_version": report["schema_version"],
            "allow_unmeasured_alpha_fallback": True,
            "fallback_scope": "fixture_generation_only",
            "backend_name": config.backend_name,
            "backend_policy": config.backend_policy.value,
            "backend_evidence_class": "internal-analytical",
            "reason": "SC-67 unmeasured evaporation-alpha refusal on current HEAD",
        },
        "thermal_train_report": report,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(artifact, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
