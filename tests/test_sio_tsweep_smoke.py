import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


FEEDSTOCKS = ("lunar_mare_low_ti", "mars_basalt")
EXPECTED_COLUMNS = (
    "cell_id",
    "T_low_C",
    "T_hold_C",
    "ramp_C_per_hr",
    "sio_yield_pct_of_feedstock",
    "terminal_offgas_escape_pct",
    "stage3_silica_kg",
    "mass_balance_err_pct",
)
EXPECTED_WALL_COLUMNS = (
    "cell_id",
    "feedstock_id",
    "pO2_mode",
    "pO2_mbar",
    "liner_temperature_C",
    "overhead_pressure_mbar",
    "knudsen_number",
    "regime_factor",
    "sio_wall_deposit_kg",
    "total_wall_deposit_kg",
    "stage3_silica_kg",
    "sio_evolved_kg",
    "sio_yield_pct_of_feedstock",
    "mass_balance_err_pct",
    "closure_error_pct",
)
MASS_BALANCE_LIMIT_PCT = 5.0e-12


def _run_tsweep(tmp_path: Path, feedstock: str, label: str, *grid_args: str):
    output_dir = tmp_path / feedstock / label
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner.sio_tsweep",
            "--feedstock",
            feedstock,
            "--output-dir",
            str(output_dir),
            # Pending t-194 grounded Cr/Mn alphas; alpha=1.0 prototype fallback.
            "--allow-unmeasured-alpha-fallback",
            *grid_args,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return output_dir


def _read_index(output_dir: Path):
    with (output_dir / "index.csv").open(newline="") as f:
        return list(csv.DictReader(f))


# 2026-07-23 re-measure (post contention-robust MAGEMin walls, 54df34f): the
# grid params now pay for honest MAGEMin answers instead of fail-fasting into
# drifted fallback curves — measured 4448 s (mars) / 4011 s (lunar) at -n0.
# That volume cannot live in the default gate (CI-duration mandate), so the
# grid pair is DEMOTED to the gate-window/nightly lane: opt in with
# REGOLITH_RUN_TSWEEP_GRID=1 (same pattern as REGOLITH_RUN_ENGINE_DETERMINISM).
# In-gate SiO tsweep coverage continues via the single-cell + wall-sweep tests
# below. Ceiling = 4448 s measured x ~1.2 headroom = 5400 s for the opt-in
# window run. Long-term fix is a call-volume cut (t-414), not a bigger ceiling.
@pytest.mark.skipif(
    os.environ.get("REGOLITH_RUN_TSWEEP_GRID") != "1",
    reason=(
        "gate-window/nightly lane: honest grid runtime ~4000-4450 s per "
        "feedstock (2026-07-23 measurement); opt in with "
        "REGOLITH_RUN_TSWEEP_GRID=1"
    ),
)
@pytest.mark.xdist_group("magemin_fullrun_c")
@pytest.mark.timeout(5400)
@pytest.mark.parametrize("feedstock", FEEDSTOCKS)
def test_sio_tsweep_cli_smoke_2x2x2_grid(tmp_path, feedstock):
    output_dir = _run_tsweep(
        tmp_path,
        feedstock,
        "grid",
        "--t-low-grid",
        "1050,1100",
        "--t-hold-grid",
        "1400,1500",
        "--ramp-grid",
        "5,10",
    )

    rows = _read_index(output_dir)
    assert len(rows) == 8
    assert tuple(rows[0]) == EXPECTED_COLUMNS
    assert len(list(output_dir.glob("*.json"))) == 8
    for row in rows:
        assert float(row["mass_balance_err_pct"]) <= MASS_BALANCE_LIMIT_PCT


# t-385 (2026-07-21): sio_tsweep native CLI family measured 293.7 s at -n0;
# ceiling >= 1.2x headroom over measured n0 (family serialized on one gateway). xdist_group pins the MAGEMin
# full-run family to one gateway.
# 2026-07-23 re-measure (post 54df34f honest walls): 343 s (mars) / 329 s
# (lunar) at -n0. Ceiling = 343 x ~1.75 = 600 s, absorbing cross-chain
# MAGEMin K-slot waits without hiding a 2x regression.
# Nightly (2026-08-02 CI tiering): live MAGEMin SiO tsweep (~50 s junit).
@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_c")
@pytest.mark.timeout(600)
@pytest.mark.parametrize("feedstock", FEEDSTOCKS)
def test_sio_tsweep_single_cell_deterministic(tmp_path, feedstock):
    metrics = []
    for index in range(3):
        output_dir = _run_tsweep(
            tmp_path,
            feedstock,
            f"deterministic-{index}",
            "--t-low-grid",
            "1050",
            "--t-hold-grid",
            "1400",
            "--ramp-grid",
            "5",
        )
        cell_path = output_dir / "tl1050_th1400_r5.json"
        cell_doc = json.loads(cell_path.read_text())
        metrics.append(cell_doc["metrics"])
        assert cell_doc["diagnostics"]["mass_balance_error_pct"] <= (
            MASS_BALANCE_LIMIT_PCT
        )

    assert metrics[1] == metrics[0]
    assert metrics[2] == metrics[0]


# t-385 (2026-07-21): sio_tsweep native CLI family measured 293.7 s at -n0;
# ceiling >= 1.2x headroom over measured n0 (family serialized on one gateway). xdist_group pins the MAGEMin
# full-run family to one gateway.
# 2026-07-23 re-measure (post 54df34f honest walls): 1098 s at -n0 — the old
# 1200 s ceiling left only 9 % headroom (it red under in-gate chain load).
# Ceiling = 1098 x ~1.35 = 1500 s.
# Nightly (2026-08-02 CI tiering): wall sweep CLI smoke (~23 s junit).
@pytest.mark.nightly
@pytest.mark.xdist_group("magemin_fullrun_c")
@pytest.mark.timeout(1500)
def test_sio_wall_sweep_cli_smoke(tmp_path):
    output_dir = tmp_path / "wall-sweep"
    summary_path = tmp_path / "wall-summary.json"
    report_path = tmp_path / "wall-report.md"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "simulator.runner.sio_wall_sweep",
            "--feedstocks",
            "lunar_mare_low_ti",
            "--wall-t-grid",
            "1100,1500",
            "--pO2-modes",
            "no_suppress,o2_1mbar",
            # Pending t-194 grounded Cr/Mn alphas; alpha=1.0 prototype fallback.
            "--allow-unmeasured-alpha-fallback",
            "--output-dir",
            str(output_dir),
            "--summary-output",
            str(summary_path),
            "--report-output",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    rows = _read_index(output_dir)
    assert len(rows) == 4
    assert tuple(rows[0]) == EXPECTED_WALL_COLUMNS
    assert summary_path.exists()
    assert "SiO Wall-Deposit Sweep" in report_path.read_text()
    for row in rows:
        assert float(row["mass_balance_err_pct"]) <= MASS_BALANCE_LIMIT_PCT

    summary = json.loads(summary_path.read_text())
    guard = summary["evolved_invariant_guard"]
    assert guard["relative_tolerance"] == pytest.approx(1.0e-6)
    assert guard["pO2_mode_allowed_to_differ"] is True
    assert guard["checks"]["lunar_mare_low_ti:no_suppress"]["passed"] is True
    assert guard["checks"]["lunar_mare_low_ti:o2_1mbar"]["passed"] is True
    thresholds = summary["thresholds"]
    assert thresholds["lunar_mare_low_ti:no_suppress"]["basis"] == "sio_wall_deposit_kg"
    # 0.5.3 Phase A1 (2026-05-28): finite-headspace default-on flip.
    # 0.5.3 Phase A chunk-review P2 fix (codex 2026-05-28): the wall-sweep
    # CLI's "o2_1mbar" mode now switches the atmosphere to CONTROLLED_O2
    # so the commanded-pO2 floor at `_commanded_pO2_bar` actively
    # suppresses SiO via the 1/sqrt(pO2) Ellingham factor. The
    # P1-A HKL mass-flux fix (2026-06-04): corrected SiO flux is low enough
    # that this coarse 1100/1500 C smoke grid hits the threshold floor in
    # both modes. Keep the ordering non-worse; evolved-kg below still proves
    # pO2 suppression is live.
    assert (
        thresholds["lunar_mare_low_ti:o2_1mbar"]["threshold_liner_temperature_C"]
        <= thresholds["lunar_mare_low_ti:no_suppress"]["threshold_liner_temperature_C"]
    )
    evolved_by_mode = {
        row["pO2_mode"]: float(row["sio_evolved_kg"])
        for row in rows
        if row["liner_temperature_C"] == "1100.0"
    }
    # 0.5.3 Phase A chunk-review P2 fix: pO2 suppression IS LIVE again
    # via the CONTROLLED_O2 atmosphere switch in
    # `_apply_sio_wall_sweep_controls`. The "1 mbar pO2 glass / clean-
    # alkali mode" operator lever is once again a meaningful physics
    # surface — `o2_1mbar` SiO evolved must be strictly less than
    # `no_suppress` (the 1/sqrt(1.0 mbar) = 1/sqrt(0.001 bar) ≈ 31.6
    # suppression factor applied to the SiO partial — actual ratio
    # depends on background equilibrium pO2 and melt composition).
    assert evolved_by_mode["o2_1mbar"] < evolved_by_mode["no_suppress"]
