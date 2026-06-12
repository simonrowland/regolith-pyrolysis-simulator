from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_bridge_preset(tmp_path: Path, *, feedstock_id: str) -> Path:
    preset_path = tmp_path / "bridge_preset.yaml"
    preset_path.write_text(
        textwrap.dedent(
            f"""
            schema_version: vacuum_pyrolysis_preset.v1
            preset_kind: faithful_with_remediation_twin
            lab_schedule:
              id: bridge_schedule
              duration_h: 1.0
              interpolation: piecewise_linear
              interpolation_source_class: assumption_with_sensitivity_marker
              interpolation_citation_id: bridge_test
              interpolation_extraction_note: bridge test schedule
              furnace_ceiling_C: 1600.0
              melt_temperature_C:
                - {{t_h: 0.0, value: 25.0, unit: C}}
                - {{t_h: 1.0, value: 1500.0, unit: C}}
              chamber_pressure_mbar:
                - {{t_h: 0.0, value: 13.0, unit: mbar}}
                - {{t_h: 1.0, value: 13.0, unit: mbar}}
              gas_boundary:
                background_gas:
                  species: Ar
                  mole_fraction: 1.0
                  source_class: assumption_with_sensitivity_marker
                  citation_id: bridge_test
                  digest: bridge_argon_boundary
                imposed_flow:
                  value: 0.3
                  unit: NL_min
                  source_class: assumption_with_sensitivity_marker
                  citation_id: bridge_test
                  digest: bridge_flow_boundary
                pressure_control:
                  mode: flow_through_with_pump
                  source_class: assumption_with_sensitivity_marker
                  citation_id: bridge_test
                  digest: bridge_pressure_control
              surface_temperature_C:
                witness:
                  - {{t_h: 0.0, value: 25.0, unit: C}}
                  - {{t_h: 1.0, value: 300.0, unit: C}}
            lab_geometry:
              id: bridge_geometry
              scale: gram_lab
              equipment_sizing: lab_fixed_geometry
              sample:
                mass_g: 2.0
              surfaces:
                - id: witness
                  role: condenser
                  area_m2: 0.001
                  view_factor_from_melt: 0.25
                  line_of_sight_to_melt: false
                  temperature_profile: witness
                  source_class: assumption_with_sensitivity_marker
                  sensitivity_marker: bridge_witness_surface_sweep
                  extraction_note: bridge test declared surface
            pair:
              faithful:
                feedstock_id: {feedstock_id}
                schedule_id: bridge_schedule
                geometry_id: bridge_geometry
                duration_h: 1.0
                mitigation: none
              remediation:
                feedstock_id: {feedstock_id}
                schedule_id: bridge_schedule
                geometry_id: bridge_geometry
                duration_h: 1.0
                mitigation:
                  pO2_cover:
                    enabled: true
                    setpoint_mbar: 1.0e-4
                    p_total_mbar: 13.0
                    effective_pO2_achieved_mbar: 1.0e-4
                    limited_by_total_pressure: false
                  alkali_shuttle_deconfliction:
                    enabled: false
            digests:
              schedule_digest: bridge_schedule_digest
              gas_boundary_digest: bridge_gas_boundary_digest
              geometry_digest: bridge_geometry_digest
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return preset_path


def _run_preset_cli(tmp_path: Path, preset_path: Path, *extra_args: str) -> tuple[int, dict]:
    output_path = tmp_path / "runner-output.json"
    cmd = [
        sys.executable,
        "-m",
        "simulator.runner",
        "--preset",
        str(preset_path),
        "--output",
        str(output_path),
        "--started-at-utc",
        "2026-06-12T00:00:00Z",
        "--kernel-commit-sha",
        "preset-bridge-test",
        "--allow-fallback-vapor",
        "--allow-unmeasured-alpha-fallback",
        *extra_args,
    ]
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert output_path.exists(), completed.stderr
    return completed.returncode, json.loads(output_path.read_text())


def test_preset_bridge_cli_maps_leg_and_records_provenance(tmp_path: Path):
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
    )

    returncode, payload = _run_preset_cli(
        tmp_path,
        preset_path,
        "--leg",
        "remediation",
    )

    assert returncode == 0, payload
    assert payload["status"] in {"ok", "partial"}
    metadata = payload["run_metadata"]
    preset = metadata["preset"]
    assert preset == {
        "path": str(preset_path),
        "leg": "remediation",
        "digest": "sha256:" + hashlib.sha256(preset_path.read_bytes()).hexdigest(),
        "schema_version": "vacuum_pyrolysis_preset.v1",
        "preset_kind": "faithful_with_remediation_twin",
        "schedule_digest": "bridge_schedule_digest",
        "gas_boundary_digest": "bridge_gas_boundary_digest",
        "geometry_digest": "bridge_geometry_digest",
        "feedstock_id": "lunar_mare_low_ti",
        "duration_h": 1.0,
        "sample_mass_g": 2.0,
        "mass_kg": 0.002,
        "schedule_id": "bridge_schedule",
        "geometry_id": "bridge_geometry",
    }
    assert metadata["feedstock_id"] == "lunar_mare_low_ti"
    assert metadata["mass_kg"] == pytest.approx(0.002)
    assert metadata["hours_requested"] == 1

    enforcement = payload["pO2_enforcement_by_hour"]
    assert enforcement
    assert enforcement[0]["schedule_id"] == "bridge_schedule"
    assert enforcement[0]["p_total_mbar"] == pytest.approx(13.0)
    assert enforcement[0]["setpoint_mbar"] == pytest.approx(1.0e-4)
    assert enforcement[0]["achieved_mbar"] == pytest.approx(1.0e-4)
    assert enforcement[0]["limited_by_total_pressure"] is False
    row = payload["per_hour_summary"][-1]
    assert row["P_total_bar"] == pytest.approx(13.0e-3)
    assert row["pO2_bar"] == pytest.approx(
        enforcement[0]["achieved_mbar"] * 1.0e-3)


def test_preset_bridge_missing_feedstock_uses_existing_named_refusal(tmp_path: Path):
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="bridge_missing_feedstock",
    )

    returncode, payload = _run_preset_cli(tmp_path, preset_path)

    assert returncode == 1
    assert payload["status"] == "failed"
    assert payload["run_metadata"]["preset"]["leg"] == "faithful"
    assert payload["error_message"].startswith(
        "RunnerError: unknown feedstock 'bridge_missing_feedstock'; "
        "expected one of "
    )


def test_preset_bridge_unknown_leg_is_named_error(tmp_path: Path):
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
    )

    returncode, payload = _run_preset_cli(
        tmp_path,
        preset_path,
        "--leg",
        "calibrated",
    )

    assert returncode == 1
    assert payload["status"] == "failed"
    assert payload["run_metadata"]["preset"]["leg"] == "calibrated"
    assert "RunnerError: unknown_preset_leg: 'calibrated'" in payload["error_message"]


def test_preset_bridge_malformed_preset_is_named_error(tmp_path: Path):
    preset_path = tmp_path / "malformed.yaml"
    preset_path.write_text("schema_version: [\n", encoding="utf-8")

    returncode, payload = _run_preset_cli(tmp_path, preset_path)

    assert returncode == 1
    assert payload["status"] == "failed"
    assert payload["run_metadata"]["preset"]["path"] == str(preset_path)
    assert payload["error_message"].startswith("RunnerError: malformed_preset:")
