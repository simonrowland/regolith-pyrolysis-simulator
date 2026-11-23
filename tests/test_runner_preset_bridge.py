from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import math
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from simulator import runner as runner_module
from simulator import condensation as condensation_module
from simulator import lab_geometry as lab_geometry_module
from simulator.battery.default_policy import resolve
from simulator.core import PyrolysisSimulator
from simulator.diagnostic_helpers.vacuum_pyrolysis import (
    COMPARISON_SCHEMA_VERSION,
)
from simulator.lab_geometry import (
    GAS_FILM_DISK,
    GAS_FILM_SPHERE,
    LAB_GEOMETRY_SCALE,
    LabGeometryError,
    resolve_lab_gas_film_geometry,
)
from simulator.thermal_budget import declared_benchtop_lengths_m
from simulator.runner import PyrolysisRun
from simulator.session import SimSession
from tests.test_runner_smoke import _assert_schema_shape
from tests.mre_reproduction_fixtures import synthetic_voltage_documents


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_every_shipped_vacuum_pyrolysis_preset_loads() -> None:
    preset_paths = sorted(
        (REPO_ROOT / "data" / "presets" / "vacuum_pyrolysis").glob("*.yaml")
    )

    assert preset_paths
    for preset_path in preset_paths:
        runner_module._load_preset_run_spec(preset_path, "faithful")


def test_robinot_faithful_loaded_steps_bracket_power_trace_and_complete() -> None:
    preset_path = (
        REPO_ROOT
        / "data"
        / "presets"
        / "vacuum_pyrolysis"
        / "robinot_2026.yaml"
    )
    preset = runner_module._load_preset_run_spec(preset_path, "faithful")

    assert preset.process_dt_s == 60.0
    assert preset.step_count == 59
    sample = preset.lab_geometry["sample"]
    assert sample["exposed_melt_area_m2"] == pytest.approx(
        0.00013273228961416876
    )
    assert sample["exposed_melt_area_basis"] == (
        "assumed_pellet_top_face_from_reported_diameter"
    )
    assert preset.provenance["notice"]["kind"] == "deck_declared_assumption"
    assert preset.provenance["notice"]["ruling_ref"] == "t-1892_2026-11-22"
    assert preset.provenance["notice"]["derivation"] == (
        "pi*(D/2)^2 from the reported D=13 mm"
    )
    assert preset.provenance["notice"]["reason"] == (
        "liquid_footprint_not_printed"
    )
    driver = preset.lab_schedule[runner_module.FURNACE_POWER_DRIVER_KEY]
    assert driver["exposed_area_m2"] is None
    resolved_film = resolve_lab_gas_film_geometry(preset.lab_geometry)
    assert resolved_film is not None
    assert driver["evaporating_area_m2"] == pytest.approx(resolved_film.area_m2)
    assert driver["radiating_face_set"] == "hot_upper"
    trace = driver["trace"]
    fields = trace["point_fields"]
    last_time_s = float(trace["points"][-1][fields.index("time_min")]) * 60.0
    step_end_samples_s = [
        index * preset.process_dt_s
        for index in range(1, preset.step_count + 1)
    ]
    assert all(sample_s <= last_time_s for sample_s in step_end_samples_s)
    assert step_end_samples_s[-1] == 59.0 * 60.0

    run = PyrolysisRun(
        feedstock_id=preset.feedstock_id,
        campaign="C0",
        hours=preset.hours,
        mass_kg=preset.mass_kg,
        process_dt_s=preset.process_dt_s,
        lab_schedule=copy.deepcopy(preset.lab_schedule),
        setpoints_patch={"lab_geometry": copy.deepcopy(preset.lab_geometry)},
        allow_fallback_vapor=True,
        allow_unmeasured_alpha_fallback=True,
    )
    session = run._start_session()
    first_step = session.advance()

    assert first_step.per_hour_summary["t_s"] == 60.0
    notice = run._lab_area_bridge()["notice"]
    assert session.simulator.melt.melt_surface_area_m2 == pytest.approx(
        0.00013273228961416876
    )
    assert session.simulator.campaign_mgr._power_temperature_for_step[
        "time_s"
    ] == 60.0
    assert notice["kind"] == "deck_declared_assumption"
    assert notice["field"] == "exposed_melt_area_m2"
    assert notice["value"] == pytest.approx(0.00013273228961416876)
    assert notice["unit"] == "m2"
    assert notice["basis"] == "assumed_pellet_top_face_from_reported_diameter"
    assert notice["ruling_ref"] == "t-1892_2026-11-22"
    assert notice["derivation"] == "pi*(D/2)^2 from the reported D=13 mm"
    assert notice["reason"] == "liquid_footprint_not_printed"
    assert "policy" not in notice
    assert "policy_class" not in notice


def _assert_subhour_preset_refuses(preset_path: Path) -> None:
    try:
        runner_module._load_preset_run_spec(preset_path, "faithful")
    except runner_module.PresetRunnerError as exc:
        message = str(exc)
        assert message.startswith("malformed_preset:")
        assert "duration_h=0.25" in message
        assert "1-hour simulator timestep" in message
    else:
        raise AssertionError("sub-hour preset was accepted by the hourly runner")


def test_quarter_hour_preset_refuses_with_hourly_timestep_reason(
    tmp_path: Path,
) -> None:
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
        duration_h=0.25,
    )

    _assert_subhour_preset_refuses(preset_path)


def test_quarter_hour_refusal_detects_lossy_guard_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
        duration_h=0.25,
    )
    monkeypatch.setattr(
        runner_module.math,
        "isclose",
        lambda *_args, **_kwargs: True,
    )

    with pytest.raises(AssertionError, match="sub-hour preset was accepted"):
        _assert_subhour_preset_refuses(preset_path)


def _write_bridge_preset(
    tmp_path: Path,
    *,
    feedstock_id: str,
    duration_h: float = 1.0,
    scale: str = "gram_lab",
    exposed_melt_area_m2: float | None = None,
    comparison_sidecar_path: Path | None = None,
) -> Path:
    preset_path = tmp_path / "bridge_preset.yaml"
    duration_literal = repr(float(duration_h))
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
              duration_h: {duration_literal}
              interpolation: piecewise_linear
              interpolation_source_class: assumption_with_sensitivity_marker
              interpolation_citation_id: bridge_test
              interpolation_extraction_note: bridge test schedule
              furnace_ceiling_C: 1600.0
              melt_temperature_C:
                - {{t_h: 0.0, value: 25.0, unit: C}}
                - {{t_h: {duration_literal}, value: 1500.0, unit: C}}
              chamber_pressure_mbar:
                - {{t_h: 0.0, value: 13.0, unit: mbar}}
                - {{t_h: {duration_literal}, value: 13.0, unit: mbar}}
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
                  - {{t_h: {duration_literal}, value: 300.0, unit: C}}
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
                duration_h: {duration_literal}
                mitigation: none
              remediation:
                feedstock_id: {feedstock_id}
                schedule_id: bridge_schedule
                geometry_id: bridge_geometry
                duration_h: {duration_literal}
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
        timeout=300,
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


def _vacuum_preset(name: str) -> dict:
    path = REPO_ROOT / "data" / "presets" / "vacuum_pyrolysis" / name
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _gram_lab_run(preset: dict, *, campaign: str) -> PyrolysisRun:
    sample = preset["lab_geometry"]["sample"]
    return PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign=campaign,
        hours=0,
        mass_kg=float(sample["mass_g"]) / 1000.0,
        setpoints_patch={"lab_geometry": preset["lab_geometry"]},
        lab_schedule=preset["lab_schedule"],
    )


def test_missing_exposed_area_uses_sample_geometry_instead_of_crucible_sizer() -> None:
    for name in ("sesko_2024.yaml",):
        preset = _vacuum_preset(name)
        assert preset["lab_geometry"]["sample"].get("exposed_melt_area_m2") in (
            None,
            "",
        )
        run = _gram_lab_run(preset, campaign="C0")
        session = SimSession()
        session.start(run._session_config())
        sim = session.simulator
        resolved = resolve_lab_gas_film_geometry(preset["lab_geometry"])
        assert resolved is not None
        crucible_area_m2 = sim._equipment.crucible.melt_surface_area_m2
        assert crucible_area_m2 > 0.0
        assert sim.melt.melt_surface_area_m2 == pytest.approx(resolved.area_m2)
        assert sim.melt.melt_surface_area_m2 != pytest.approx(crucible_area_m2)


def test_gram_lab_without_sample_mass_refuses_before_area_default(
    tmp_path: Path,
) -> None:
    preset_path = _write_bridge_preset(
        tmp_path,
        feedstock_id="lunar_mare_low_ti",
    )
    preset = yaml.safe_load(preset_path.read_text(encoding="utf-8"))
    del preset["lab_geometry"]["sample"]["mass_g"]
    preset_path.write_text(yaml.safe_dump(preset, sort_keys=False), encoding="utf-8")

    with pytest.raises(
        runner_module.PresetRunnerError,
        match="lab_geometry.sample.mass_g is required",
    ):
        runner_module._load_preset_run_spec(preset_path, "faithful")


def test_null_exposed_melt_area_uses_lab_resolver_with_flag() -> None:
    preset = _vacuum_preset("sesko_2024.yaml")
    sample = preset["lab_geometry"]["sample"]
    assert sample.get("exposed_melt_area_m2") in (None, "")
    run = _gram_lab_run(preset, campaign="C0")
    assert run._lab_area_bridge() == {}
    session = run._start_session()
    sim = session.simulator
    resolved = resolve_lab_gas_film_geometry(preset["lab_geometry"])
    assert resolved is not None
    assert sim.melt.melt_surface_area_m2 == pytest.approx(resolved.area_m2)
    assert sim._dp007_area_default is not None

    robinot = _vacuum_preset("robinot_2026.yaml")
    printed = copy.deepcopy(robinot)
    printed["lab_geometry"]["sample"]["exposed_melt_area_m2"] = 0.000123
    printed_run = _gram_lab_run(printed, campaign="C2A")
    printed_session = printed_run._start_session()
    printed_run._apply_lab_area_bridge(
        printed_session.simulator,
        printed_run._lab_area_bridge(),
    )
    printed_session.simulator._get_turbine_spec()
    printed_melt_m2 = printed_session.simulator.melt.melt_surface_area_m2
    sized_face_m2 = (
        printed_session.simulator._equipment.crucible.melt_surface_area_m2
    )
    assert printed_melt_m2 == pytest.approx(0.000123)
    assert printed_melt_m2 != pytest.approx(sized_face_m2)

    pomeroy = _vacuum_preset("pomeroy_cardiff_2006.yaml")
    assert pomeroy["lab_geometry"]["sample"]["exposed_melt_area_m2"] == (
        pytest.approx(0.000314)
    )
    pomeroy_run = _gram_lab_run(pomeroy, campaign="C2A")
    pomeroy_session = pomeroy_run._start_session()
    pomeroy_run._apply_lab_area_bridge(
        pomeroy_session.simulator,
        pomeroy_run._lab_area_bridge(),
    )
    pomeroy_session.simulator._get_turbine_spec()
    pomeroy_melt_m2 = pomeroy_session.simulator.melt.melt_surface_area_m2
    assert pomeroy_melt_m2 == pytest.approx(0.000314)
    assert pomeroy_melt_m2 != pytest.approx(
        pomeroy_session.simulator._equipment.crucible.melt_surface_area_m2
    )


def test_c0_start_installs_printed_area_before_the_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    robinot = _vacuum_preset("robinot_2026.yaml")
    printed = copy.deepcopy(robinot)
    printed["lab_geometry"]["sample"]["exposed_melt_area_m2"] = 0.000123
    forecast_areas: list[float] = []
    predict = PyrolysisSimulator._predict_c0_exit

    def capture_area(simulator, *args, **kwargs):
        forecast_areas.append(simulator.melt.melt_surface_area_m2)
        return predict(simulator, *args, **kwargs)

    monkeypatch.setattr(PyrolysisSimulator, "_predict_c0_exit", capture_area)
    run = _gram_lab_run(printed, campaign="C0")
    session = SimSession()
    session.start(run._session_config())
    assert forecast_areas == [pytest.approx(0.000123)]
    melt_m2 = session.simulator.melt.melt_surface_area_m2
    sized_m2 = session.simulator._equipment.crucible.melt_surface_area_m2
    assert melt_m2 == pytest.approx(0.000123)
    assert melt_m2 != pytest.approx(sized_m2)
    omitted = copy.deepcopy(printed)
    omitted["lab_geometry"]["sample"]["exposed_melt_area_m2"] = None
    omitted_session = SimSession()
    omitted_session.start(_gram_lab_run(omitted, campaign="C0")._session_config())
    omitted_film = resolve_lab_gas_film_geometry(omitted["lab_geometry"])
    assert omitted_film is not None
    assert omitted_session.simulator.melt.melt_surface_area_m2 == pytest.approx(
        omitted_film.area_m2
    )
    assert forecast_areas == [
        pytest.approx(0.000123),
        pytest.approx(omitted_film.area_m2),
    ]
    omitted_sim = omitted_session.simulator
    assert omitted_sim.melt.melt_surface_area_m2 == pytest.approx(omitted_film.area_m2)
    assert omitted_sim._dp007_area_default is not None
    assert len(forecast_areas) == 2
    assert forecast_areas[0] == pytest.approx(0.000123)


def test_dp007_area_notice_is_attached_to_run_metadata() -> None:
    preset = _vacuum_preset("sesko_2024.yaml")
    sample_mass_kg = preset["lab_geometry"]["sample"]["mass_g"] / 1000.0
    expected = resolve("DP-007", sample_mass_kg=sample_mass_kg)
    film = resolve_lab_gas_film_geometry(preset["lab_geometry"])
    assert film is not None
    run = _gram_lab_run(preset, campaign="C0")
    session = run._start_session()
    output = run._run_session(session)

    notices = output["run_metadata"]["runtime_notices"]
    assert len(notices) == 1
    assert notices[0]["kind"] == "knob_defaulted"
    assert notices[0]["reason"] == "exposed_melt_area_not_printed"
    assert notices[0]["policy"] == {
        "id": "DP-007",
        "version": 1,
        "status": "canonical",
        "value": expected.payload["policy"]["value"],
        "units": "m²",
    }
    assert float(notices[0]["policy"]["value"]) == pytest.approx(film.area_m2)
    assert float(notices[0]["policy"]["value"]) == pytest.approx(
        math.pi * film.length_m**2 / 4.0
    )
    assert session.simulator.melt.melt_surface_area_m2 == pytest.approx(
        film.area_m2
    )

    robinot = _vacuum_preset("robinot_2026.yaml")
    declared_area_m2 = robinot["lab_geometry"]["sample"]["exposed_melt_area_m2"]
    run = _gram_lab_run(robinot, campaign="C0")
    session = run._start_session()
    output = run._run_session(session)
    assert session.simulator.melt.melt_surface_area_m2 == pytest.approx(declared_area_m2)
    assert all(
        notice.get("policy", {}).get("id") != "DP-007"
        for notice in output["run_metadata"].get("runtime_notices", ())
    )


@pytest.mark.parametrize(
    "raw_area",
    [
        0,
        -1,
        False,
        True,
        "nope",
        pytest.param(10**400, id="overflow-int"),
        pytest.param(b"0.000123", id="binary-scalar"),
    ],
)
def test_invalid_exposed_area_refuses_before_sized_face(raw_area) -> None:
    robinot = copy.deepcopy(_vacuum_preset("robinot_2026.yaml"))
    robinot["lab_geometry"]["sample"]["exposed_melt_area_m2"] = raw_area
    session = SimSession()
    with pytest.raises(LabGeometryError) as caught:
        session.start(_gram_lab_run(robinot, campaign="C0")._session_config())
    assert caught.value.code == "invalid_lab_geometry_positive_value"
    # _session_config rejects invalid geometry before SimSession.start runs.
    assert session._sim is None


def test_numeric_string_exposed_area_stays_admitted() -> None:
    robinot = copy.deepcopy(_vacuum_preset("robinot_2026.yaml"))
    robinot["lab_geometry"]["sample"]["exposed_melt_area_m2"] = "0.000123"
    session = _gram_lab_run(robinot, campaign="C2A")._start_session()
    session.simulator._get_turbine_spec()
    melt_m2 = session.simulator.melt.melt_surface_area_m2
    assert melt_m2 == pytest.approx(0.000123)
    assert melt_m2 != pytest.approx(
        session.simulator._equipment.crucible.melt_surface_area_m2
    )


def test_bridge_and_runtime_share_resolved_lab_film_geometry() -> None:
    robinot = _vacuum_preset("robinot_2026.yaml")
    resolved = resolve_lab_gas_film_geometry(robinot["lab_geometry"])
    assert resolved is not None
    bridge = condensation_module.gram_lab_exposed_melt_area_bridge(
        robinot["lab_geometry"]
    )
    assert bridge["effective_exposed_area_m2"] == pytest.approx(resolved.area_m2)
    run = _gram_lab_run(robinot, campaign="C2A")
    session = run._start_session()
    session.simulator._get_turbine_spec()
    melt_m2 = session.simulator.melt.melt_surface_area_m2
    assert melt_m2 == pytest.approx(resolved.area_m2)
    assert melt_m2 != pytest.approx(
        session.simulator._equipment.crucible.melt_surface_area_m2
    )


def test_robinot_lab_film_uses_pellet_diameter_and_keeps_declared_area() -> None:
    robinot = _vacuum_preset("robinot_2026.yaml")
    resolved = resolve_lab_gas_film_geometry(robinot["lab_geometry"])

    assert resolved is not None
    assert resolved.length_m == pytest.approx(0.013)
    assert resolved.length_m != pytest.approx(0.12)
    assert resolved.shape == GAS_FILM_SPHERE
    assert resolved.area_m2 == pytest.approx(0.00013273228961416876)
    assert "gas_film_length_declared" in resolved.flags
    assert "gas_film_shape:sphere_equivalent" in resolved.flags


def test_benchtop_declared_diameter_delegates_to_lab_geometry_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = {"pellet_diameter_mm": "13"}
    calls: list[object] = []

    def declared_owner(received):
        calls.append(received)
        return 0.013

    monkeypatch.setattr(
        lab_geometry_module,
        "declared_pellet_diameter_m",
        declared_owner,
    )

    lengths = declared_benchtop_lengths_m(sample)

    assert calls == [sample]
    assert lengths["pellet_diameter_m"] == pytest.approx(0.013)


def test_crucible_inner_diameter_selects_disk_shape_and_length() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {"crucible_inner_diameter_mm": 20.0},
    }

    resolved = resolve_lab_gas_film_geometry(geometry)

    assert resolved is not None
    assert resolved.shape == GAS_FILM_DISK
    assert resolved.length_m == pytest.approx(0.02)
    assert resolved.length_method == "declared:crucible_inner_diameter_mm"
    assert resolved.area_m2 == pytest.approx(math.pi * 0.02**2 / 4.0)


def test_printed_crucible_diameter_owns_area_before_a_printed_area() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {
            "mass_g": 10.0,
            "crucible_inner_diameter_mm": 20.0,
            "exposed_melt_area_m2": 0.000123,
        },
    }

    resolved = resolve_lab_gas_film_geometry(geometry)

    assert resolved is not None
    assert resolved.length_m == pytest.approx(0.02)
    assert resolved.area_m2 == pytest.approx(math.pi * resolved.length_m**2 / 4.0)


def test_crucible_mass_uses_hemisphere_length_and_coherent_disk_area() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {"mass_g": 10.0, "crucible_material": "alumina"},
    }
    bridge = condensation_module.gram_lab_exposed_melt_area_bridge(geometry)
    film = resolve_lab_gas_film_geometry(geometry)

    assert film is not None
    assert film.shape == GAS_FILM_DISK
    assert film.length_method == "derived:hemispherical_pool_from_mass"
    assert "gas_film_length_derived" in film.flags
    from simulator.equipment import EquipmentDesigner

    assert math.pi * film.length_m**3 / 12.0 == pytest.approx(
        0.010 / EquipmentDesigner.MELT_DENSITY_KG_M3
    )
    assert bridge["effective_exposed_area_m2"] == pytest.approx(
        math.pi * film.length_m**2 / 4.0
    )


def test_sesko_deck_holder_resolves_hemispherical_melt_pool():
    sesko = _vacuum_preset("sesko_2024.yaml")
    from simulator.condensation import _stirring_enhanced_sherwood

    film = resolve_lab_gas_film_geometry(sesko["lab_geometry"])

    assert film is not None
    assert film.shape == GAS_FILM_DISK
    assert film.length_m == pytest.approx(0.02419, rel=5e-4)
    assert film.area_m2 == pytest.approx(4.594e-4, rel=5e-4)
    assert film.length_method == "derived:hemispherical_pool_from_mass"
    assert _stirring_enhanced_sherwood(
        radial_stir_factor=0.0,
        gas_film_shape=film.shape,
    ) == pytest.approx(8.0 / math.pi)


def test_free_body_mass_uses_sphere_length_and_coherent_sphere_area() -> None:
    geometry = {"scale": "gram_lab", "sample": {"mass_g": 3.38}}
    bridge = condensation_module.gram_lab_exposed_melt_area_bridge(geometry)
    film = resolve_lab_gas_film_geometry(geometry)

    assert film is not None
    assert film.shape == GAS_FILM_SPHERE
    assert film.length_method == "derived:sphere_from_mass"
    from simulator.equipment import EquipmentDesigner

    assert math.pi * film.length_m**3 / 6.0 == pytest.approx(
        0.00338 / EquipmentDesigner.MELT_DENSITY_KG_M3
    )
    assert bridge["effective_exposed_area_m2"] == pytest.approx(
        math.pi * film.length_m**2
    )


def test_printed_area_sets_its_shape_equivalent_film_length() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {
            "exposed_melt_area_m2": 0.000314,
            "crucible_material": "alumina",
        },
    }
    resolved = resolve_lab_gas_film_geometry(geometry)

    assert resolved is not None
    assert resolved.length_m == pytest.approx(
        math.sqrt(4.0 * 0.000314 / math.pi)
    )
    assert resolved.area_m2 == pytest.approx(0.000314)
    assert resolved.length_method == "derived:printed_exposed_area"
    assert "gas_film_length_derived" in resolved.flags
    assert resolved.shape == GAS_FILM_DISK


def test_free_body_printed_area_uses_sphere_equivalent_diameter() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {"exposed_melt_area_m2": 0.000314},
    }

    resolved = resolve_lab_gas_film_geometry(geometry)

    assert resolved is not None
    assert resolved.shape == GAS_FILM_SPHERE
    assert resolved.length_m == pytest.approx(math.sqrt(0.000314 / math.pi))
    assert resolved.area_m2 == pytest.approx(0.000314)
    assert resolved.length_method == "derived:printed_exposed_area"


def test_wire_loop_support_uses_free_sphere_limit() -> None:
    geometry = {
        "scale": "gram_lab",
        "sample": {"mass_g": 3.38, "support_material": "Pt wire loop"},
    }

    resolved = resolve_lab_gas_film_geometry(geometry)

    assert resolved is not None
    assert resolved.shape == GAS_FILM_SPHERE
    assert resolved.length_method == "derived:sphere_from_mass"
    assert resolved.area_m2 == pytest.approx(math.pi * resolved.length_m**2)


def test_missing_lab_dimensions_use_dp113_with_shape_and_area_flags() -> None:
    geometry = {"scale": "gram_lab", "sample": {"crucible_material": "alumina"}}
    resolved = resolve_lab_gas_film_geometry(geometry)
    policy = yaml.safe_load(
        (REPO_ROOT / "data" / "battery" / "default_policy.yaml").read_text()
    )["policies"][0]

    assert resolved is not None
    assert resolved.length_m == pytest.approx(policy["value"])
    assert resolved.shape == GAS_FILM_DISK
    assert "gas_film_length_assumed" in resolved.flags
    assert resolved.area_m2 == pytest.approx(math.pi * resolved.length_m**2 / 4.0)
    assert policy["id"] == "DP-113"
    assert policy["status"] == "proposed"
    silent = resolve_lab_gas_film_geometry({"scale": "gram_lab", "sample": {}})
    assert silent is not None
    assert silent.length_m == pytest.approx(policy["value"])
    assert silent.shape == GAS_FILM_SPHERE
    assert "gas_film_shape:sphere_equivalent" in silent.flags
    assert (
        "gas_film_shape_assumed:sphere_equivalent:apparatus_unspecified"
        in silent.flags
    )
    assert silent.area_m2 == pytest.approx(math.pi * silent.length_m**2)


def test_exposed_area_scale_token_is_lab_geometry_scale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert LAB_GEOMETRY_SCALE == "gram_lab"
    robinot = _vacuum_preset("robinot_2026.yaml")
    monkeypatch.setattr(
        lab_geometry_module,
        "LAB_GEOMETRY_SCALE",
        "not_the_gram_lab_token",
    )
    session = _gram_lab_run(robinot, campaign="C2A")._start_session()
    session.simulator._get_turbine_spec()
    melt_m2 = session.simulator.melt.melt_surface_area_m2
    sized_m2 = session.simulator._equipment.crucible.melt_surface_area_m2
    assert melt_m2 == pytest.approx(sized_m2)
    assert melt_m2 != pytest.approx(0.2)


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
        exposed_melt_area_m2=0.000123,
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
        "effective_exposed_area_m2": 0.000123,
        "area_basis": "gram_lab_exposed_melt",
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
        exposed_melt_area_m2=0.000123,
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
        exposed_melt_area_m2=0.000123,
    )
    preset = runner_module._load_preset_run_spec(preset_path, "faithful")
    breached_pct = runner_module.RUNNER_MASS_BALANCE_LIMIT_PCT * 2.0
    original_execute_session = runner_module.RunExecutor.execute_session

    def execute_session_with_breach(self, session, **kwargs):
        execution = original_execute_session(self, session, **kwargs)
        assert execution.snapshots
        execution.snapshots[-1].mass_balance_error_pct = breached_pct
        per_hour = list(execution.per_hour)
        assert per_hour
        per_hour[-1] = {**per_hour[-1], "mass_balance_pct": breached_pct}
        return replace(execution, per_hour=tuple(per_hour))

    monkeypatch.setattr(
        runner_module.RunExecutor,
        "execute_session",
        execute_session_with_breach,
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
