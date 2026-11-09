"""Hash-seed stability for serialized same-hour closure diagnostics."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_pressure_held_closure_diagnostics_ignore_python_hash_seed():
    script = textwrap.dedent(
        """\
        import json
        from tests.chemistry.conftest import _load_yaml
        from tests.chemistry.test_evaporation_series_resistance_flux import (
            _pressure_held_sim,
        )

        sim = _pressure_held_sim(
            _load_yaml("vapor_pressures.yaml"),
            _load_yaml("feedstocks.yaml"),
            _load_yaml("setpoints.yaml"),
        )
        sim._calculate_evaporation(sim._get_equilibrium())
        diagnostic = sim._last_evaporation_flux_diagnostic
        print(json.dumps({
            "closure": diagnostic["same_hour_headspace_closure"],
            "pressure_held_sweep_bounds": diagnostic[
                "pressure_held_sweep_bounds"
            ],
        }, separators=(",", ":"), allow_nan=False))
        """
    )
    repo_root = Path(__file__).resolve().parents[2]
    serializations = []
    for seed in ("1", "2"):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = seed
        process = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            env=env,
            capture_output=True,
            check=False,
            timeout=900,
        )
        assert process.returncode == 0, process.stderr.decode("utf-8")
        serializations.append(process.stdout)

    assert serializations[0] == serializations[1]
