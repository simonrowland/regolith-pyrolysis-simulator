"""Tests for the non-interactive SimSession CLI harness."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
_MPLCONFIG_TMP = tempfile.TemporaryDirectory(prefix="regolith-session-cli-mpl-")
_MPLCONFIG_READY = False
PER_HOUR_KEYS = {
    "hour",
    "campaign",
    "T_C",
    "P_total_bar",
    "pO2_bar",
    "mass_balance_pct",
    "O2_yield_kg_cumulative",
    "metal_yields_kg",
    "condensation_train_kg",
    "vapor_species_kg_hr",
    "wall_deposit_delta_kg",
    "wall_deposit_cumulative_kg",
    "Kn",
    "regime",
    "transport_formula_id",
}


def _run_session(script: str, *, strict: bool = False) -> subprocess.CompletedProcess:
    global _MPLCONFIG_READY
    cmd = [
        sys.executable,
        "-m",
        "simulator",
        "session",
        "--script=-",
    ]
    if strict:
        cmd.append("--strict")
    env = os.environ.copy()
    env["MPLCONFIGDIR"] = _MPLCONFIG_TMP.name
    if not _MPLCONFIG_READY:
        subprocess.run(
            [sys.executable, "-c", "import matplotlib.font_manager"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=ROOT,
            env=env,
            check=False,
        )
        _MPLCONFIG_READY = True
    return subprocess.run(
        cmd,
        input=script,
        text=True,
        capture_output=True,
        cwd=ROOT,
        env=env,
        check=False,
    )


def _frames(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines() if line]


def test_session_script_exercises_every_verb_as_ndjson():
    result = _run_session(
        """
        # Comments and blank lines do not emit frames.

        start --feedstock=lunar_mare_low_ti --campaign=C0 --backend=stub --setpoint=C0.max_hours=1 --setpoint=C0B.max_hours=1
        snapshot
        advance 10
        decide A
        adjust pO2_mbar 1.0
        adjust campaign_override C2A stir_factor 1.5
        pause
        resume
        quit
        """
    )

    assert result.returncode == 0, result.stderr
    frames = _frames(result.stdout)
    assert [frame["seq"] for frame in frames] == list(range(1, 10))
    assert [frame["frame_type"] for frame in frames] == [
        "start",
        "snapshot",
        "decision_required",
        "decide",
        "adjust",
        "adjust",
        "pause",
        "resume",
        "quit",
    ]
    assert all(frame["ok"] for frame in frames)
    assert frames[0]["backend"] == "stub"
    assert frames[0]["backend_active"] == "StubBackend"
    assert set(frames[1]["snapshot"]) == PER_HOUR_KEYS
    assert frames[2]["decision"]["recommendation"] == "A"
    assert frames[2]["steps"]
    assert set(frames[2]["steps"][0]) == PER_HOUR_KEYS
    assert frames[3]["choice"] == "A"
    assert frames[5]["campaign"] == "C2A"
    assert frames[5]["field"] == "stir_factor"
    assert result.stderr == ""


def test_session_script_is_byte_deterministic():
    script = """
    start --feedstock=lunar_mare_low_ti --campaign=C0 --backend=stub
    advance 2
    snapshot
    quit
    """

    first = _run_session(script)
    second = _run_session(script)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_session_bad_out_of_state_verb_emits_error_and_continues():
    result = _run_session(
        """
        decide A
        start --feedstock=lunar_mare_low_ti --backend=stub
        quit
        """
    )

    frames = _frames(result.stdout)
    assert result.returncode == 0
    assert frames[0]["ok"] is False
    assert frames[0]["frame_type"] == "error"
    assert frames[0]["cmd"] == "decide A"
    assert frames[1]["ok"] is True
    assert "session: decide A:" in result.stderr


def test_session_strict_exits_nonzero_on_first_error():
    result = _run_session(
        """
        decide A
        start --feedstock=lunar_mare_low_ti --backend=stub
        """,
        strict=True,
    )

    frames = _frames(result.stdout)
    assert result.returncode == 1
    assert len(frames) == 1
    assert frames[0]["ok"] is False
    assert "session: decide A:" in result.stderr


def test_advance_n_stops_on_first_control_frame():
    result = _run_session(
        """
        start --feedstock=lunar_mare_low_ti --backend=stub --setpoint=C0.max_hours=1 --setpoint=C0B.max_hours=1
        advance 10
        snapshot
        quit
        """
    )

    frames = _frames(result.stdout)
    assert result.returncode == 0, result.stderr
    assert frames[1]["frame_type"] == "decision_required"
    assert len(frames[1]["steps"]) == 3
    assert frames[1]["steps"][-1]["hour"] == 3
    assert frames[2]["frame_type"] == "snapshot"


def test_adjust_variadic_campaign_override_and_scalar_forms_parse():
    result = _run_session(
        """
        start feedstock=lunar_mare_low_ti campaign=C2A backend=stub
        adjust campaign_override C2A stir_factor 1.5
        adjust pO2_mbar 1.0
        snapshot
        quit
        """
    )

    frames = _frames(result.stdout)
    assert result.returncode == 0, result.stderr
    assert frames[1] == {
        "campaign": "C2A",
        "cmd": "adjust campaign_override C2A stir_factor 1.5",
        "field": "stir_factor",
        "frame_type": "adjust",
        "ok": True,
        "param": "campaign_override",
        "seq": 2,
        "value": 1.5,
    }
    assert frames[2]["param"] == "pO2_mbar"
    # pO2_bar is honestly overhead-derived (0.5.3 Phase C P1 fix in
    # build_per_hour_summary): it reads snapshot.overhead.composition['O2'], NOT
    # the commanded melt.pO2_mbar intent. A freshly-started C2A session has not
    # advanced, so no O2 sits in the overhead yet -> pO2_bar == 0.0 regardless of
    # the `adjust pO2_mbar 1.0` operator intent. Asserting 0.0 (not the legacy
    # 0.001) guards against regressing to the old melt.pO2_mbar-painted-onto-a-
    # vacuum-floor behavior that the Phase C fix deliberately removed.
    assert frames[3]["snapshot"]["pO2_bar"] == 0.0


def test_run_subcommand_matches_runner_byte_for_byte(tmp_path: Path):
    simulator_output = tmp_path / "simulator-run.json"
    runner_output = tmp_path / "runner.json"
    common = [
        "--feedstock=lunar_mare_low_ti",
        "--campaign=C0",
        "--hours=2",
        "--started-at-utc=2026-05-20T00:00:00Z",
        "--kernel-commit-sha=test-sha",
    ]

    simulator_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator",
            "run",
            *common,
            f"--output={simulator_output}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    runner_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner",
            *common,
            f"--output={runner_output}",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert simulator_result.returncode == 0, simulator_result.stderr
    assert runner_result.returncode == 0, runner_result.stderr
    assert simulator_output.read_bytes() == runner_output.read_bytes()
