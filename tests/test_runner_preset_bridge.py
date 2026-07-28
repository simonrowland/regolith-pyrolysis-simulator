from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from simulator import runner as runner_module
from simulator.diagnostic_helpers.vacuum_pyrolysis import (
    COMPARISON_SCHEMA_VERSION,
)
from simulator.runner import PyrolysisRun
from tests.test_runner_smoke import _assert_schema_shape
from tests.mre_reproduction_fixtures import synthetic_voltage_documents


REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_bridge_preset(
    tmp_path: Path,
    *,
    feedstock_id: str,
    scale: str = "gram_lab",
    exposed_melt_area_m2: float | None = None,
    comparison_sidecar_path: Path | None = None,
) -> Path:
    preset_path = tmp_path / "bridge_preset.yaml"
    exposed_area_line = (
        ""
        if exposed_melt_area_m2 is None
        else f"\n                exposed_melt_area_m2: {exposed_melt_area_m2}"
    )
    comparison_block = ""
    if comparison_sidecar_path is not None:
        comparison_block = textwrap.dedent(
            f"""
            paper_id: bridge_comparison_paper
            paper_citation_id: bridge_comparison_source
            measurement_id: bridge_comparison_measurement
            measurement_selectors:
              - observable_id: bridge_final_o2_mass
                kind: final_o2_mass_kg
                species: O2
                units: kg
                evidence_scope: source_side_bridge_test
                certification:
                  status: assumed-input
                  blocked_by:
                    - synthetic_bridge_recipe
            comparison_contract:
              observation_sidecar_path: {json.dumps(str(comparison_sidecar_path))}
            """
        )
    preset_text = textwrap.dedent(
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
              scale: {scale}
              equipment_sizing: lab_fixed_geometry
              sample:
                mass_g: 2.0{exposed_area_line}
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
    if comparison_block:
        preset_text = preset_text.replace(
            "preset_kind: faithful_with_remediation_twin\n",
            "preset_kind: faithful_with_remediation_twin\n"
            + comparison_block.strip()
            + "\n",
            1,
        )
    preset_path.write_text(
        preset_text + "\n",
        encoding="utf-8",
    )
    return preset_path


def _runtime_geometry(*, scale: str, exposed_melt_area_m2: float) -> dict:
    return {
        "id": "runtime_area_geometry",
        "scale": scale,
        "equipment_sizing": "lab_fixed_geometry",
        "sample": {
            "mass_g": 2.0,
            "exposed_melt_area_m2": exposed_melt_area_m2,
        },
        "surfaces": [
            {
                "id": "witness",
                "role": "condenser",
                "area_m2": 0.001,
                "temperature_C": 25.0,
                "view_factor_from_melt": 0.25,
                "line_of_sight_to_melt": False,
                "source_class": "assumption_with_sensitivity_marker",
                "sensitivity_marker": "runtime_area_surface_sweep",
                "extraction_note": "synthetic surface for runtime area tests",
            }
        ],
    }


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


def _write_mre_bridge_preset(tmp_path: Path) -> tuple[Path, Path]:
    preset, observations = synthetic_voltage_documents()
    preset["sampling"]["max_interval_min"]["value"] = 60.0
    sidecar_path = tmp_path / "mre-observations.yaml"
    preset_path = tmp_path / "mre-preset.yaml"
    preset["comparison_contract"]["observation_sidecar_path"] = str(sidecar_path)
    sidecar_path.write_text(
        yaml.safe_dump(observations, sort_keys=False),
        encoding="utf-8",
    )
    preset_path.write_text(
        yaml.safe_dump(preset, sort_keys=False),
        encoding="utf-8",
    )
    return preset_path, sidecar_path


def test_gram_lab_exposed_area_sets_runtime_melt_area() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=0,
        mass_kg=0.002,
        setpoints_patch={
            "lab_geometry": _runtime_geometry(
                scale="gram_lab",
                exposed_melt_area_m2=0.000123,
            ),
        },
    )
    session = run._start_session()

    assert session.simulator.melt.melt_surface_area_m2 == pytest.approx(0.2)
    bridge = run._lab_area_bridge()
    run._apply_lab_area_bridge(session.simulator, bridge)

    assert bridge == {
        "effective_exposed_area_m2": 0.000123,
        "area_basis": "gram_lab_exposed_melt",
    }
    assert session.simulator.melt.melt_surface_area_m2 == pytest.approx(0.000123)


def test_non_gram_lab_exposed_area_leaves_runtime_melt_area_default() -> None:
    run = PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C2A",
        hours=0,
        mass_kg=0.002,
        setpoints_patch={
            "lab_geometry": _runtime_geometry(
                scale="industrial_pot",
                exposed_melt_area_m2=0.000123,
            ),
        },
    )
    session = run._start_session()

    bridge = run._lab_area_bridge()
    run._apply_lab_area_bridge(session.simulator, bridge)

    assert bridge == {}
    assert session.simulator.melt.melt_surface_area_m2 == pytest.approx(0.2)


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
    assert payload["status"] == "ok"
    assert payload["reason"] == ""
    assert payload["error_message"] == ""
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
    assert row["mass_balance_pct"] == pytest.approx(0.0)
    assert row["P_total_bar"] == pytest.approx(13.0e-3)
    assert row["pO2_bar"] == pytest.approx(
        enforcement[0]["achieved_mbar"] * 1.0e-3)


def test_preset_bridge_compare_mode_writes_json_and_markdown(tmp_path: Path):
    sidecar_path = tmp_path / "observations.yaml"
    sidecar_path.write_text(
        textwrap.dedent(
            """
            schema_version: vacuum_pyrolysis_measurements.v1
            measurements:
              bridge_comparison_measurement:
                paper_citation:
                  citation_id: bridge_comparison_source
                comparison_points:
                  - observable_id: bridge_final_o2_mass
                    coordinate: {time_h: 1.0}
                    expected_value: 0.0
                    uncertainty: {kind: absolute, value: 0.0}
                    units: kg
                    status: reported
                    source_locator: {table: synthetic_bridge}
                qualitative_comparison_observations: []
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
        comparison_sidecar_path=sidecar_path,
    )

    returncode, payload = _run_preset_cli(
        tmp_path,
        preset_path,
        "--compare",
    )

    assert returncode == 0, payload
    _assert_schema_shape(payload)
    assert "comparison" not in payload

    comparison_path = tmp_path / "runner-output.comparison.json"
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    assert comparison["schema_version"] == COMPARISON_SCHEMA_VERSION == 2
    assert set(comparison) == {
        "schema_version",
        "domain",
        "preset_kind",
        "execution_scope",
        "paper_id",
        "case_id",
        "measurement_id",
        "sidecar_path",
        "markdown_path",
        "digests",
        "records",
        "qualitative_observations",
        "unsupported_observables",
    }
    assert comparison["domain"] == "vacuum_pyrolysis"
    assert comparison["preset_kind"] == "faithful_with_remediation_twin"
    assert comparison["execution_scope"] == "vacuum_pyrolysis"
    assert comparison["paper_id"] == "bridge_comparison_paper"
    assert comparison["case_id"] == "faithful"
    assert json.loads(json.dumps(comparison, allow_nan=False)) == comparison
    assert comparison["measurement_id"] == "bridge_comparison_measurement"
    assert comparison["sidecar_path"] == str(sidecar_path)
    assert comparison["markdown_path"] == str(
        tmp_path / "runner-output.comparison.md"
    )
    assert set(comparison["digests"]) == {
        "recipe_sha256",
        "source_sha256",
        "result_sha256",
    }
    assert all(len(value) == 64 for value in comparison["digests"].values())
    assert len(comparison["records"]) == 1
    record = comparison["records"][0]
    assert record["status"] == "assumed-input"
    assert record["recipe_digest"] == comparison["digests"]["recipe_sha256"]
    assert record["observation_digest"] == comparison["digests"]["source_sha256"]
    assert record["runtime_digest"] == comparison["digests"]["result_sha256"]

    markdown = (tmp_path / "runner-output.comparison.md").read_text()
    assert "| case | observable | species |" in markdown
    assert "bridge_final_o2_mass" in markdown
    assert "## Content digests" in markdown
    assert f"Versioned comparison artifact: `{comparison_path}`" in markdown


def test_mre_preset_bridge_exact_shape_and_domain_artifacts(tmp_path: Path):
    preset_path, sidecar_path = _write_mre_bridge_preset(tmp_path)

    returncode, payload = _run_preset_cli(
        tmp_path,
        preset_path,
        "--leg",
        "one_hour",
        "--compare",
    )

    assert returncode == 0, payload
    assert set(payload) == {
        "status",
        "reason",
        "error_message",
        "run_metadata",
        "mre_reproduction",
    }
    reproduction = payload["mre_reproduction"]
    assert set(reproduction) == {
        "schema_version",
        "execution_origin",
        "case_id",
        "controls_digest",
        "temperature_C",
        "gas_boundary",
        "intervals",
        "cumulative",
    }
    assert reproduction["case_id"] == "one_hour"
    assert {row["applied_current_A"] for row in reproduction["intervals"]} == {0.5}

    comparison_path = tmp_path / "runner-output.comparison.json"
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    assert comparison["schema_version"] == 2
    assert comparison["domain"] == "mre"
    assert comparison["case_id"] == "one_hour"
    assert comparison["sidecar_path"] == str(sidecar_path)
    assert set(comparison["digests"]) == {
        "recipe_sha256",
        "source_sha256",
        "result_sha256",
        "controls_sha256",
    }
    markdown = (tmp_path / "runner-output.comparison.md").read_text(
        encoding="utf-8"
    )
    assert "# MRE literature comparison: yu_2025_hollow_anode / one_hour" in markdown
    assert "exterior-RGA collected O2" in markdown


@pytest.mark.parametrize(
    ("args", "reason"),
    (
        (("--campaign", "C5"), "mre_reproduction_campaign_conflict"),
        (("--track", "mre_baseline"), "mre_reproduction_campaign_conflict"),
    ),
)
def test_mre_preset_bridge_refuses_plant_campaign_surfaces(
    tmp_path: Path,
    args: tuple[str, ...],
    reason: str,
):
    preset_path, _ = _write_mre_bridge_preset(tmp_path)

    returncode, payload = _run_preset_cli(
        tmp_path,
        preset_path,
        "--leg",
        "one_hour",
        *args,
    )

    assert returncode == 1
    assert reason in payload["reason"] or reason in payload["error_message"]


def test_preset_fast_tier_policy_rejects_internal_analytical_backend() -> None:
    with pytest.raises(
        runner_module.RunnerError,
        match="forbids internal-analytical execution",
    ):
        PyrolysisRun(
            feedstock_id="lunar_mare_low_ti",
            backend_name="internal-analytical",
            run_metadata_overrides={
                runner_module.PRESET_PROVENANCE_METADATA_KEY: {
                    "comparison_contract": {
                        "fast_tier_policy": (
                            "cached_real_only_no_internal_analytical"
                        )
                    }
                }
            },
        )


def test_preset_bridge_mass_balance_breach_marks_status_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
    )
    preset = runner_module._load_preset_run_spec(preset_path, "faithful")
    breached_pct = runner_module.RUNNER_MASS_BALANCE_LIMIT_PCT * 2.0
    original_execute = runner_module.RunExecutor.execute

    def execute_with_breach(self, config, *, worker_runtime=None):
        execution = original_execute(
            self,
            config,
            worker_runtime=worker_runtime,
        )
        assert execution.snapshots
        execution.snapshots[-1].mass_balance_error_pct = breached_pct
        per_hour = list(execution.per_hour)
        assert per_hour
        per_hour[-1] = {**per_hour[-1], "mass_balance_pct": breached_pct}
        return replace(execution, per_hour=tuple(per_hour))

    monkeypatch.setattr(
        runner_module.RunExecutor,
        "execute",
        execute_with_breach,
    )

    payload = PyrolysisRun(
        feedstock_id=preset.feedstock_id,
        campaign="C0",
        hours=preset.hours,
        mass_kg=preset.mass_kg,
        setpoints_patch={"lab_geometry": copy.deepcopy(preset.lab_geometry)},
        lab_schedule=copy.deepcopy(preset.lab_schedule),
        run_metadata_overrides={
            runner_module.PRESET_PROVENANCE_METADATA_KEY: dict(
                preset.provenance
            ),
        },
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    ).run()

    assert payload["status"] == "failed"
    assert payload["reason"] == "mass_balance_closure_breach"
    assert payload["error_message"] == (
        "mass_balance_closure_breach: "
        f"{breached_pct:.12g}% > "
        f"{runner_module.RUNNER_MASS_BALANCE_LIMIT_PCT:.12g}%"
    )
    assert payload["run_metadata"]["preset"]["leg"] == "faithful"


def test_preset_bridge_records_exposed_area_result_scope(tmp_path: Path):
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
        exposed_melt_area_m2=0.000123,
    )

    returncode, payload = _run_preset_cli(tmp_path, preset_path)

    # 2026-07-21 B1 re-baseline: on pre-vapor-package physics this
    # lab-geometry run breached mass-balance closure, and that rc=1 breach
    # was the test vehicle. On compose-0.6.3 the run closes exactly (worst
    # per-hour mass_balance_pct 0.0 <= the 5e-12 % runner limit, verified
    # from the executable), so the subject under test — the exposed-area
    # result scope — is asserted on the success path. The runner still
    # enforces the closure gate itself: a breach would surface as rc=1.
    assert returncode == 0, payload
    assert payload["status"] == "ok"
    metadata = payload["run_metadata"]
    assert metadata["effective_exposed_area_m2"] == pytest.approx(0.000123)
    assert metadata["area_basis"] == "gram_lab_exposed_melt"
    preset = metadata["preset"]
    assert preset["geometry_digest"] == "bridge_geometry_digest"
    assert preset["effective_exposed_area_m2"] == pytest.approx(0.000123)
    assert preset["area_basis"] == "gram_lab_exposed_melt"


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
