import copy
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import app as app_module
from web import events as web_events
from web import routes as web_routes
from simulator.backends import BackendSelectionPolicy, backend_resolution_status
from simulator.condensation import KnudsenRegimeRefusal
from simulator.cost_parameters import default_cost_parameters_block
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.recipe_io import load_recipe_patch, read_recipe_metadata, write_recipe_patch
from simulator.runner import build_per_hour_summary
from simulator.session import drive_auto_apply
from simulator.state import CampaignPhase, DecisionPoint, DecisionType, EvaporationFlux
from web.events import (
    BackendUnavailableError,
    _clear_simulation_state,
    _completion_payload,
    _current_simulation_state,
    _emit_if_current,
    _effective_config_from_setpoints,
    _get_backend,
    _MASS_BALANCE_ERROR_BREACH_PCT,
    _replace_simulation_state,
    _start_payload,
    _sim_locks,
    _simulations,
    _tick_payload,
)
from web.run_store import RunArtifactStore


_DELETE = object()
_REPO_ROOT = Path(__file__).resolve().parents[1]
_RECIPE_DIR = _REPO_ROOT / "data" / "recipes"


def _identified_socket_client(app):
    http_client = app.test_client()
    assert http_client.get("/").status_code == 200
    return app_module.socketio.test_client(
        app,
        flask_test_client=http_client,
    )


def _tag_with_id(source: str, element_id: str) -> str:
    match = re.search(
        r'<(?:input|select)\b(?=[^>]*\bid="' + re.escape(element_id) + r'")[^>]*>',
        source,
    )
    assert match is not None, element_id
    return match.group(0)


def _assert_recipe_defining_marker(source: str, element_id: str) -> None:
    assert "recipe-defining-control" in _tag_with_id(source, element_id)


def _sim_with_mass_balance_snapshot(error_pct, category=None):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {
                    "SiO2": 51.5,
                    "FeO": 13.0,
                    "MgO": 34.0,
                },
                "bulk_additions": {
                    "metallic_FeNi_wt_pct": 15.0,
                },
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")
    snapshot = sim._make_snapshot()
    snapshot.mass_balance_error_pct = error_pct
    if category is not None:
        setattr(snapshot, "mass_balance_error_category", category)
    return sim, snapshot


def _producer_backed_redox_per_hour_summary() -> dict[str, object]:
    root = Path(__file__).resolve().parents[1]
    setpoints = yaml.safe_load((root / "data/setpoints.yaml").read_text())
    setpoints.setdefault("chemistry_kernel", {})["allow_fallback_vapor"] = True
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        setpoints,
        yaml.safe_load((root / "data/feedstocks.yaml").read_text()),
        yaml.safe_load((root / "data/vapor_pressures.yaml").read_text()),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C0)
    snapshot = sim.step()
    return build_per_hour_summary(sim, snapshot, include_fe_redox_split=True)


def _set_nested(mapping, path, value):
    target = mapping
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        target.pop(path[-1], None)
    else:
        target[path[-1]] = value


def _install_recipe_endpoint_state(sid: str) -> None:
    _simulations[sid] = {
        "running": False,
        "paused": False,
        "run_id": "recipe-endpoint-test",
        "recipe_inputs": {
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
            "track": "pyrolysis",
            "runtime_campaign_overrides": {
                "C4": {
                    "pO2_mbar": 0.2,
                    "hold_temp_C": 1600.0,
                    "max_hours": 24.0,
                    "ramp_rate": 10.0,
                }
            },
            "c4_max_temp_C": 1670.0,
            "furnace_max_T_C": 1800.0,
            "c5_enabled": False,
            "mre_target_species": "",
            "mre_max_voltage_V": 0.0,
            "additives_kg": {"Na": 0.0, "K": 0.0, "Mg": 0.0},
            "furnace_material_id": "",
        },
        "setpoints_patch": {},
        "last_recipe_capture": {
            "tick": {
                "hour": 1,
                "campaign": "C4",
                "pO2_mbar": 0.2,
                "p_total_mbar": 10.0,
                "oxygen_kg": 4.5,
                "energy_electrical_plus_evaporation_cumulative_kWh": 12.25,
                "energy_scope": "electrical_plus_known_evaporation_enthalpy",
                "furnace_heat_status": "partial",
                "mass_balance_error_pct": 0.0,
                "process_buckets_kg": {
                    "metal_alloy": {"Fe": 2.0, "Mg": 0.5},
                    "terminal_slag": {"SiO2": 10.0},
                },
            },
            "per_hour_summary": {
                "wall_deposit_cumulative_kg": {
                    "stage_1_to_stage_2": {"SiO": 0.01}
                },
            },
        },
        "last_completion_payload": {
            "oxygen_kg": 4.5,
            "energy_electrical_plus_evaporation_kWh": 12.25,
            "energy_scope": "electrical_plus_known_evaporation_enthalpy",
            "furnace_heat_status": "partial",
            "mass_balance_error_pct": 0.0,
            "terminal_rump_kg": 10.0,
            "products": {"glass": 3.0},
        },
    }
    _sim_locks[sid] = threading.Lock()


def _recipe_metadata(title: str, campaign: str = "C4") -> dict[str, object]:
    return {
        "title": title,
        "feedstock": "lunar_mare_low_ti",
        "campaign": campaign,
        "headline_recipe": {
            "feedstock": "lunar_mare_low_ti",
            "campaign": campaign,
            "temperature_ladder": [],
        },
        "headline_results": {
            "oxygen_kg": 0.0,
            "energy_kWh": 0.0,
            "wall_deposit_kg": 0.0,
        },
    }


def _force_socketio_internal_analytical(monkeypatch) -> list[tuple[object, tuple, dict]]:
    captured_tasks: list[tuple[object, tuple, dict]] = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    return captured_tasks


def test_socketio_ledger_api_is_byte_identical_read_only(monkeypatch):
    _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    before_sids = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        new_sids = set(_simulations) - before_sids
        assert len(new_sids) == 1
        sid = new_sids.pop()
        ledger = _simulations[sid]["session"].simulator.atom_ledger
        before = json.dumps(ledger.close_report(), sort_keys=True).encode()

        response = client.emit(
            "ledger_api",
            {"resource": "account", "account": "process.cleaned_melt"},
            callback=True,
        )

        after = json.dumps(ledger.close_report(), sort_keys=True).encode()
        assert response["account"] == "process.cleaned_melt"
        assert response["run_id"] == _simulations[sid]["run_id"]
        assert after == before
    finally:
        client.disconnect()
        for sid in set(_simulations) - before_sids:
            _clear_simulation_state(sid)


def test_recipe_save_list_load_endpoints_round_trip_without_run(
    tmp_path,
    monkeypatch,
):
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    client = app.test_client()
    sid = "recipe-save-sid"
    _install_recipe_endpoint_state(sid)

    try:
        response = client.post(
            "/recipes/save",
            json={"sid": sid, "title": "<img src=x onerror=alert(1)>"},
        )
        assert response.status_code == 200, response.get_json()
        saved = response.get_json()
        saved_path = tmp_path / f"{saved['name']}.yaml"
        assert saved_path.exists()

        metadata = read_recipe_metadata(saved_path)
        assert metadata["title"] == "<img src=x onerror=alert(1)>"
        assert metadata["headline_recipe"]["feedstock"] == "lunar_mare_low_ti"
        assert metadata["headline_results"]["oxygen_kg"] == pytest.approx(4.5)
        patch = load_recipe_patch(saved_path)
        assert patch["campaigns"]["C4"]["temp_range_C"] == pytest.approx(
            [1600.0, 1670.0]
        )

        output_path = tmp_path / "run.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "simulator.runner",
                "--feedstock",
                "lunar_mare_low_ti",
                "--campaign",
                "C4",
                "--hours",
                "1",
                "--recipe",
                str(saved_path),
                "--allow-fallback-vapor",
                "--started-at-utc",
                "2026-05-15T00:00:00Z",
                "--kernel-commit-sha",
                "recipe-ui-test",
                "--output",
                str(output_path),
            ],
            cwd=_REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert json.loads(output_path.read_text(encoding="utf-8"))["status"] == "ok"

        def reject_run_load(*args, **kwargs):
            raise AssertionError("list must not load or run recipes")

        monkeypatch.setattr(web_routes, "load_recipe_patch", reject_run_load)
        listed = client.get("/recipes")
        assert listed.status_code == 200
        recipes = listed.get_json()
        assert recipes == [{
            "name": saved["name"],
            "title": "<img src=x onerror=alert(1)>",
            "summary": (
                "lunar_mare_low_ti | C4 | O2 4.5 kg | "
                "electrical+evap partial 12.25 kWh | wall deposit 0.01 kg"
            ),
        }]
        monkeypatch.setattr(web_routes, "load_recipe_patch", load_recipe_patch)

        load_sid = "recipe-load-sid"
        _simulations[load_sid] = {"running": False, "run_id": "load-test"}
        _sim_locks[load_sid] = threading.Lock()
        loaded = client.post(
            "/recipes/load",
            json={"sid": load_sid, "name": saved["name"]},
        )
        assert loaded.status_code == 200, loaded.get_json()
        payload = loaded.get_json()
        assert payload["applied_to_session"] is True
        assert payload["controls"]["lever_campaign"] == "C4"
        assert _simulations[load_sid]["setpoints_patch"] == patch
    finally:
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_loaded_recipe_start_applies_restored_runtime_levers(
    tmp_path,
    monkeypatch,
):
    loaded_patch = {
        "campaigns": {
            "C4": {
                "temp_range_C": [1585.0, 1595.0],
                "pO2_mbar_default": 0.1,
                "p_total_mbar_default": 0.1,
            }
        }
    }
    write_recipe_patch(
        tmp_path / "loaded-c4.yaml",
        loaded_patch,
        metadata=_recipe_metadata("Loaded C4", "C4"),
    )
    _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    loaded = app.test_client().post("/recipes/load", json={"name": "loaded-c4"})
    assert loaded.status_code == 200, loaded.get_json()

    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
                "c4_max_temp_C": 1670.0,
                "setpoints_patch": loaded.get_json()["setpoints_patch"],
                "runtime_campaign_overrides": {
                    "C4": {
                        "pO2_mbar": 0.2,
                        "hold_temp_C": 1600.0,
                    }
                },
            },
        )
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]
        assert statuses
        assert statuses[-1]["status"] == "started"

        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        assert state is not None
        sim = state["session"].simulator
        assert state["recipe_inputs"]["runtime_campaign_overrides"] == {
            "C4": {"pO2_mbar": 0.2, "hold_temp_C": 1600.0}
        }
        assert sim.campaign_mgr.overrides["C4"] == {
            "pO2_mbar": 0.2,
            "hold_temp_C": 1600.0,
        }
        assert sim.campaign_mgr.c4_max_temp_C == pytest.approx(1595.0)
        assert sim.setpoints["campaigns"]["C4"]["temp_range_C"] == pytest.approx(
            [1585.0, 1595.0]
        )
    finally:
        client.disconnect()
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_recipe_save_serializes_resolved_staged_ladder(tmp_path):
    staged_patch = load_recipe_patch(
        _RECIPE_DIR / "c2a_staged_temperature_ladder.yaml"
    )
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    client = app.test_client()
    sid = "recipe-staged-save-sid"
    _install_recipe_endpoint_state(sid)
    _simulations[sid]["setpoints_patch"] = {}
    _simulations[sid]["resolved_setpoints_patch"] = copy.deepcopy(staged_patch)
    _simulations[sid]["last_recipe_capture"]["tick"]["campaign"] = "C2A_staged"

    try:
        response = client.post(
            "/recipes/save",
            json={"sid": sid, "title": "Resolved Staged Ladder"},
        )
        assert response.status_code == 200, response.get_json()
        saved_path = tmp_path / f"{response.get_json()['name']}.yaml"
        saved_patch = load_recipe_patch(saved_path)
        assert saved_patch == staged_patch

        stages = saved_patch["campaigns"]["C2A_staged"]["stages"]
        assert [
            (stage.get("target_C"), stage["ramp_rate_C_per_hr"], stage["duration_h"])
            for stage in stages
        ] == [
            (1250, 600, 4),
            (1600, 175, 3),
            (None, 150, 1),
            (1150, 600, 1),
        ]
        ladder = read_recipe_metadata(saved_path)["headline_recipe"][
            "temperature_ladder"
        ]
        assert [entry["stage"] for entry in ladder] == [
            "C2A_staged.alkali_early_fe",
            "C2A_staged.sio_window",
            "C2A_staged.fe_hot_hold",
            "C2A_staged.cool_for_na_shuttle",
        ]
    finally:
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_staged_recipe_save_load_start_is_identity(
    tmp_path,
    monkeypatch,
):
    staged_patch = load_recipe_patch(
        _RECIPE_DIR / "c2a_staged_temperature_ladder.yaml"
    )
    _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    http_client = app.test_client()
    save_sid = "recipe-staged-identity-save-sid"
    _install_recipe_endpoint_state(save_sid)
    _simulations[save_sid]["setpoints_patch"] = {}
    _simulations[save_sid]["resolved_setpoints_patch"] = copy.deepcopy(staged_patch)
    _simulations[save_sid]["last_recipe_capture"]["tick"]["campaign"] = "C2A_staged"

    socket_client = None
    try:
        saved = http_client.post(
            "/recipes/save",
            json={"sid": save_sid, "title": "Staged Identity"},
        )
        assert saved.status_code == 200, saved.get_json()
        saved_patch = load_recipe_patch(tmp_path / f"{saved.get_json()['name']}.yaml")
        loaded = http_client.post(
            "/recipes/load",
            json={"name": saved.get_json()["name"]},
        )
        assert loaded.status_code == 200, loaded.get_json()
        assert loaded.get_json()["setpoints_patch"] == saved_patch

        socket_client = _identified_socket_client(app)
        assert socket_client.is_connected()
        socket_client.get_received()
        before = set(_simulations)
        socket_client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
                "setpoints_patch": loaded.get_json()["setpoints_patch"],
                "runtime_campaign_overrides": {
                    "C4": {"hold_temp_C": 1600.0, "pO2_mbar": 0.2}
                },
            },
        )
        statuses = [
            event["args"][0]
            for event in socket_client.get_received()
            if event["name"] == "simulation_status"
        ]
        assert statuses
        assert statuses[-1]["status"] == "started"
        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        assert state is not None
        assert state["recipe_inputs"]["runtime_campaign_overrides"] == {
            "C4": {"hold_temp_C": 1600.0, "pO2_mbar": 0.2}
        }
        assert state["resolved_setpoints_patch"] == saved_patch
        assert (
            state["session"]
            .simulator
            .setpoints["campaigns"]["C2A_staged"]["stages"]
            == saved_patch["campaigns"]["C2A_staged"]["stages"]
        )
    finally:
        if socket_client is not None:
            socket_client.disconnect()
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_recipe_save_fails_loud_without_last_recipe_capture(tmp_path):
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    client = app.test_client()
    sid = "recipe-no-capture-sid"
    _install_recipe_endpoint_state(sid)
    _simulations[sid].pop("last_recipe_capture")

    try:
        response = client.post(
            "/recipes/save",
            json={"sid": sid, "title": "No Capture Recipe"},
        )

        assert response.status_code == 400
        assert response.mimetype == "application/json"
        assert "completed or running tick" in response.get_json()["error"]
        assert list(tmp_path.glob("*.yaml")) == []
    finally:
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_recipe_save_fails_loud_on_slug_collision(tmp_path):
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    client = app.test_client()
    sid = "recipe-collision-sid"
    _install_recipe_endpoint_state(sid)
    existing = tmp_path / "collision-test.yaml"
    original = "sentinel: do-not-overwrite\n"
    existing.write_text(original, encoding="utf-8")

    try:
        response = client.post(
            "/recipes/save",
            json={"sid": sid, "title": "Collision Test"},
        )

        assert response.status_code == 409
        assert response.mimetype == "application/json"
        assert response.get_json()["error"] == "recipe already exists: collision-test"
        assert existing.read_text(encoding="utf-8") == original
        assert sorted(path.name for path in tmp_path.glob("*.yaml")) == [
            "collision-test.yaml"
        ]
    finally:
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_recipe_xss_title_is_served_as_literal_json_and_safe_slug(tmp_path):
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    client = app.test_client()
    sid = "recipe-xss-title-sid"
    _install_recipe_endpoint_state(sid)
    title = "<img src=x onerror=alert(1)>"

    try:
        saved_response = client.post(
            "/recipes/save",
            json={"sid": sid, "title": title},
        )
        assert saved_response.status_code == 200, saved_response.get_json()
        assert saved_response.mimetype == "application/json"
        saved = saved_response.get_json()

        assert saved["title"] == title
        assert saved["name"] == "img-src-x-onerror-alert-1"
        assert "/" not in saved["name"]
        assert "\\" not in saved["name"]
        assert ".." not in saved["name"]

        saved_path = tmp_path / f"{saved['name']}.yaml"
        assert saved_path.exists()
        assert saved_path.resolve().parent == tmp_path.resolve()
        assert read_recipe_metadata(saved_path)["title"] == title

        listed_response = client.get("/recipes")
        assert listed_response.status_code == 200
        assert listed_response.mimetype == "application/json"
        recipes = listed_response.get_json()
        assert recipes[0]["name"] == saved["name"]
        assert recipes[0]["title"] == title
    finally:
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_recipe_endpoints_fail_loud_on_bad_title_and_metadata(tmp_path):
    app = app_module.create_app()
    app.config["RECIPE_LIBRARY_DIR"] = tmp_path
    client = app.test_client()
    sid = "recipe-bad-title-sid"
    _install_recipe_endpoint_state(sid)

    try:
        bad_title = client.post(
            "/recipes/save",
            json={"sid": sid, "title": " \n "},
        )
        assert bad_title.status_code == 400
        assert "title" in bad_title.get_json()["error"]

        (tmp_path / "bad.yaml").write_text(
            "metadata:\n"
            "  title: 7\n"
            "  headline_recipe: {}\n"
            "  headline_results: {}\n"
            "furnace_max_T_C: 1800\n",
            encoding="utf-8",
        )
        bad_metadata = client.get("/recipes")
        assert bad_metadata.status_code == 500
        assert "metadata.title" in bad_metadata.get_json()["error"]
    finally:
        for active_sid in list(_simulations):
            _clear_simulation_state(active_sid)


def test_recipe_ui_uses_text_content_for_loaded_titles() -> None:
    controls = (
        _REPO_ROOT / "web/static/js/simulator-controls.js"
    ).read_text(encoding="utf-8")
    assert "option.textContent" in controls
    assert "titleEl.textContent" in controls
    assert "summaryEl.textContent" in controls
    assert "innerHTML" not in controls
    assert "payload.setpoints_patch = loadedRecipePatch" in controls
    assert (
        "payload.runtime_campaign_overrides = buildRuntimeCampaignOverrides()"
        in controls
    )
    assert "clearLoadedRecipeForManualEdit" in controls


def test_loaded_recipe_dom_controls_reach_start_payload() -> None:
    controls_path = _REPO_ROOT / "web/static/js/simulator-controls.js"
    harness = r"""
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
function functionSource(name) {
    const start = source.indexOf(`function ${name}(`);
    if (start < 0) throw new Error(`missing function ${name}`);
    const open = source.indexOf('{', start);
    let depth = 0;
    for (let i = open; i < source.length; i += 1) {
        if (source[i] === '{') depth += 1;
        if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
    }
    throw new Error(`unterminated function ${name}`);
}
const elements = Object.fromEntries([
    'lever-campaign', 'lever-po2-mbar', 'lever-pn2-mbar',
    'lever-stage-temp', 'lever-stage-duration', 'lever-stage-ramp',
    'c4-max-temp', 'feedstock-select', 'batch-mass', 'engine-select',
    'furnace-material', 'add-na', 'add-k', 'add-mg', 'add-ca', 'add-c',
].map(id => [id, {id, value: '', dataset: {}}]));
const leverFields = {
    'lever-po2-mbar': 'pO2_mbar',
    'lever-pn2-mbar': 'p_total_mbar',
    'lever-stage-temp': 'hold_temp_C',
    'lever-stage-duration': 'max_hours',
    'lever-stage-ramp': 'ramp_rate',
};
for (const [id, field] of Object.entries(leverFields)) elements[id].dataset.field = field;
const context = {
    document: {
        getElementById: id => elements[id] || null,
        querySelectorAll: selector => selector === '.recipe-lever[data-field]'
            ? Object.keys(leverFields).map(id => elements[id]) : [],
        querySelector: () => null,
    },
    updateMreFields: () => {},
    updateKnudsenIndicator: () => {},
    updateLeverWarning: () => {},
    console,
};
vm.createContext(context);
vm.runInContext([
    functionSource('selectedLeverCampaign'),
    functionSource('buildRuntimeCampaignOverrides'),
    functionSource('setRadioValue'),
    functionSource('applyLoadedRecipeControls'),
    functionSource('applyLoadedRecipeStartIdentity'),
    'let loadedRecipePatch = null;',
].join('\n'), context);
vm.runInContext(`
applyLoadedRecipeControls({
    lever_campaign: 'C4', pO2_mbar: 0.12, p_total_mbar: 0.12,
    stage_temp_C: 1600, stage_duration_h: 7.5, stage_ramp_C_per_h: 22
});
loadedRecipePatch = {campaigns: {C4: {temp_range_C: [1600, 1660]}}};
const payload = {};
applyLoadedRecipeStartIdentity(payload);
console.log(JSON.stringify(payload));
`, context);
"""
    completed = subprocess.run(
        ["node", "-", str(controls_path)],
        input=harness,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "setpoints_patch": {"campaigns": {"C4": {"temp_range_C": [1600, 1660]}}},
        "runtime_campaign_overrides": {
            "C4": {
                "pO2_mbar": 0.12,
                "p_total_mbar": 0.12,
                "hold_temp_C": 1600,
                "max_hours": 7.5,
                "ramp_rate": 22,
            }
        },
    }


def test_recipe_ui_renders_per_hour_redox_summary_payload_keys() -> None:
    controls = (
        _REPO_ROOT / "web/static/js/simulator-controls.js"
    ).read_text(encoding="utf-8")
    simulator_template = (
        _REPO_ROOT / "web/templates/simulator.html"
    ).read_text(encoding="utf-8")

    assert "renderRedoxSummary(lastRecipeSummary)" in controls
    redox_markup = simulator_template.split('<div id="redox-card"', 1)[1].split(
        "<!-- Gas Train Status -->",
        1,
    )[0]
    assert "Melt Fe Redox Diagnostics" in redox_markup
    assert "Diagnostic ferric/ferrous speciation" in redox_markup
    assert "CERTIFIED" not in redox_markup
    for payload_key in (
        "fe_redox_split",
        "fO2_log",
        "fe3_over_sigma_fe",
        "ferric_frac",
        "ferrous_frac",
        "fe2o3_over_feo_molar",
        "native_fe_frac",
        "native_fe_saturation_event",
        "native_fe_event_status",
        "native_fe_partition",
        "source",
        "stage_3_capture",
        "Fe_kg",
        "total_kg",
        "Fe_wt_pct",
        "redox_source_breakdown",
        "net_mol_o2_equiv",
        "delta_log10_fO2",
        "ferric_divergence",
        "source_campaign",
        "source_hour",
        "source_campaign_hour",
        # M3-L3 P2: the source-label readout must distinguish applied from
        # skipped terms — rendering the TOTAL terms dict shows skipped terms
        # as if they happened. Pin the applied/skipped split + skipped tag.
        "applied_terms_mol_o2_equiv_by_label",
        "skipped_terms_mol_o2_equiv_by_label",
        " (skipped)",
    ):
        assert payload_key in controls

    assert "nativeFeEventStatus(redox.native_fe_saturation_event)" in controls
    assert "nativeFePartitionText(redox.native_fe_partition)" in controls

    expected_redox_ids = (
        "redox-fo2-log",
        "redox-ferric-frac",
        "redox-ferrous-frac",
        "redox-fe3-sigma-fe",
        "redox-fe2o3-feo-molar",
        "redox-native-fe-frac",
        "redox-native-fe-status",
        "redox-native-fe-partition",
        "redox-stage3-fe",
        "redox-stage3-total",
        "redox-stage3-fe-wt",
        "redox-source-net",
        "redox-source-delta-log10",
        "redox-divergence-status",
        "redox-source-labels",
        "redox-split-source",
        "redox-source-context",
    )
    redox_template_ids = tuple(
        element_id
        for element_id in re.findall(r'id="(redox-[^"]+)"', redox_markup)
        if element_id != "redox-card"
    )
    set_redox_ids = tuple(
        re.findall(r"setRedoxText\(\s*'([^']+)'", controls)
    )
    assert redox_template_ids == expected_redox_ids
    assert set_redox_ids == expected_redox_ids


def test_recipe_defining_controls_share_loaded_recipe_clear_marker() -> None:
    simulator_template = (
        _REPO_ROOT / "web/templates/simulator.html"
    ).read_text(encoding="utf-8")
    disclosure_template = (
        _REPO_ROOT / "web/templates/partials/disclosure.html"
    ).read_text(encoding="utf-8")

    for element_id in (
        "engine-select",
        "feedstock-select",
        "batch-mass",
        "mre-enabled",
        "mre-preset",
        "lever-campaign",
        "lever-po2-mbar",
        "lever-stage-temp",
        "lever-stage-duration",
        "lever-stage-ramp",
        "c4-max-temp",
        "furnace-material",
        "add-na",
        "add-k",
        "add-mg",
        "add-ca",
        "add-c",
    ):
        _assert_recipe_defining_marker(simulator_template, element_id)

    assert 'class="ctrl-param recipe-defining-control"' in disclosure_template
    assert 'class="campaign-ctrl recipe-defining-control"' in disclosure_template


def test_loaded_recipe_manual_edit_contract_clears_before_start_payload() -> None:
    controls = (
        _REPO_ROOT / "web/static/js/simulator-controls.js"
    ).read_text(encoding="utf-8")

    assert "e.target?.closest?.('.recipe-defining-control')" in controls
    assert "document.addEventListener('input', handleRecipeDefiningControlEdit)" in controls
    assert "handleRecipeDefiningControlEdit(e);" in controls
    assert "#additive-controls" not in controls
    assert "payload.setpoints_patch = loadedRecipePatch" in controls
    assert (
        "payload.runtime_campaign_overrides = buildRuntimeCampaignOverrides()"
        in controls
    )
    assert "c5_enabled: mrePayload.c5_enabled" in controls
    assert "mre_target_species: mrePayload.mre_target_species" in controls
    assert "mre_max_voltage_V: mrePayload.mre_max_voltage_V" in controls
    assert "if (furnaceMaterialId) payload.furnace_material_id" in controls
    for element_id in ("add-na", "add-k", "add-mg", "add-ca", "add-c"):
        assert f"document.getElementById('{element_id}').value" in controls


def test_launcher_defaults_to_localhost_and_debug_off(monkeypatch):
    call = {}

    monkeypatch.delenv("REGOLITH_HOST", raising=False)
    monkeypatch.delenv("REGOLITH_PORT", raising=False)
    monkeypatch.delenv("REGOLITH_FLASK_DEBUG", raising=False)
    monkeypatch.setattr(app_module, "create_app", lambda: object())

    def fake_run(app, **kwargs):
        call.update(kwargs)

    monkeypatch.setattr(app_module.socketio, "run", fake_run)

    app_module.main()

    assert call["host"] == "127.0.0.1"
    assert call["port"] == 3000
    assert call["debug"] is False
    assert call["allow_unsafe_werkzeug"] is True


def test_launcher_does_not_allow_unsafe_werkzeug_on_public_host(monkeypatch):
    call = {}

    monkeypatch.setenv("REGOLITH_HOST", "0.0.0.0")
    monkeypatch.delenv("REGOLITH_ALLOW_UNSAFE_WERKZEUG", raising=False)
    monkeypatch.delenv("REGOLITH_FLASK_DEBUG", raising=False)
    monkeypatch.setattr(app_module, "create_app", lambda: object())

    def fake_run(app, **kwargs):
        call.update(kwargs)

    monkeypatch.setattr(app_module.socketio, "run", fake_run)

    app_module.main()

    assert call["host"] == "0.0.0.0"
    assert call["debug"] is False
    assert call["allow_unsafe_werkzeug"] is False


def test_launcher_rejects_legacy_unsafe_env(monkeypatch):
    monkeypatch.setenv("REGOLITH_ALLOW_UNSAFE_WERKZEUG", "1")

    with pytest.raises(SystemExit, match="no longer supported"):
        app_module.main()


def test_launcher_rejects_public_debug_host(monkeypatch):
    monkeypatch.setenv("REGOLITH_HOST", "0.0.0.0")
    monkeypatch.setenv("REGOLITH_FLASK_DEBUG", "1")

    with pytest.raises(RuntimeError, match="loopback host"):
        app_module.main()


def test_launcher_rejects_invalid_port(monkeypatch):
    monkeypatch.setenv("REGOLITH_PORT", "abc")

    with pytest.raises(SystemExit, match="REGOLITH_PORT"):
        app_module.main()


def test_launcher_rejects_out_of_range_port(monkeypatch):
    monkeypatch.setenv("REGOLITH_PORT", "70000")

    with pytest.raises(SystemExit, match="1..65535"):
        app_module.main()


@pytest.mark.parametrize("host", ["[127.0.0.1", "127.0.0.1]", "[]localhost"])
def test_loopback_detection_rejects_malformed_brackets(host):
    assert app_module._is_loopback_host(host) is False


def test_loopback_detection_accepts_bracketed_ipv6_loopback():
    assert app_module._is_loopback_host("[::1]") is True


def test_alphamelts_backend_selection_fails_closed(monkeypatch):
    class UnavailableAlphaMELTS:
        def initialize(self, config):
            return False

    monkeypatch.setattr("web.events.AlphaMELTSBackend",
                        UnavailableAlphaMELTS)

    with pytest.raises(BackendUnavailableError,
                       match="AlphaMELTS unavailable"):
        _get_backend("alphamelts")


def test_web_backend_path_uses_shared_resolve_backend(monkeypatch):
    calls = []

    def fake_resolve_backend(backend_name, policy, **kwargs):
        calls.append((backend_name, policy, kwargs))
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    monkeypatch.setattr("web.events.resolve_backend", fake_resolve_backend)

    backend = _get_backend("internal-analytical")

    assert isinstance(backend, InternalAnalyticalBackend)
    assert len(calls) == 1
    assert calls[0][0] == "internal-analytical"
    assert calls[0][1] is BackendSelectionPolicy.WEB_AUTODETECT
    assert calls[0][2]["unavailable_error_cls"] is BackendUnavailableError


def test_web_start_payload_exposes_backend_status():
    expected_backend = backend_resolution_status(InternalAnalyticalBackend()).as_payload()
    payload = _start_payload(
        sim=object(),
        feedstock_key="lunar_mare_low_ti",
        mass_kg=1000.0,
        backend_requested="internal-analytical",
        backend_active="InternalAnalyticalBackend",
        backend_status="unavailable",
        backend_authoritative=False,
        backend_message="Using built-in fallback",
        backend_payload=expected_backend,
        c5_enabled=True,
        mre_target_species="SiO2",
        mre_max_voltage_V=1.45,
    )

    for key, value in expected_backend.items():
        assert payload[key] == value
    assert payload["backend_status"] == "unavailable"
    assert payload["backend_authoritative"] is False
    assert payload["c5_enabled"] is True
    assert payload["mre_target_species"] == "SiO2"
    assert payload["mre_max_voltage_V"] == pytest.approx(1.45)


def test_web_start_event_carries_mre_fields_into_session(monkeypatch):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
                "c5_enabled": True,
                "mre_target_species": "SiO2",
                "mre_max_voltage_V": 1.45,
                "runtime_campaign_overrides": {
                    "C4": {
                        "pO2_mbar": 0.2,
                        "hold_temp_C": 1600,
                        "max_hours": 24,
                        "ramp_rate": 10,
                    }
                },
            },
        )
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]
        assert statuses
        started = statuses[-1]
        assert started["c5_enabled"] is True
        assert started["mre_target_species"] == "SiO2"
        assert started["mre_max_voltage_V"] == pytest.approx(1.45)
        assert started["backend_status"] == "unavailable"
        assert started["backend_active"] == "InternalAnalyticalBackend"
        assert started["backend_message"] == "Using built-in fallback"
        # v0.6.0 t-172 flip: the emitted display token is now
        # internal-analytical (legacy 'stub' accepted on input only).
        assert started["backend_status_message"] == (
            "internal-analytical backend selected; "
            "no authoritative melt result available"
        )

        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        assert state is not None
        sim = state["session"].simulator
        assert sim.melt.c5_enabled is True
        assert sim.melt.mre_target_species == "SiO2"
        assert sim.melt.mre_max_voltage_V == pytest.approx(1.45)
        assert sim.campaign_mgr.overrides["C4"]["pO2_mbar"] == pytest.approx(0.2)
    finally:
        client.disconnect()
        for sid in list(_simulations):
            _clear_simulation_state(sid)


def test_furnace_material_catalog_endpoint_returns_enabled_only():
    app = app_module.create_app()
    response = app.test_client().get("/api/furnace-material-catalog")

    assert response.status_code == 200
    materials = response.get_json()["materials"]
    material_ids = {material["id"] for material in materials}
    assert "dense_alumina_continuous" in material_ids
    assert "fused_silica" in material_ids
    assert "sintered_regolith" in material_ids
    assert "graphite_inert" not in material_ids
    base_material_keys = {
        "id",
        "display_name",
        "max_service_T_C",
        "grounding",
        "service_rating_T_C",
        "requested_ceiling_T_C",
        "effective_applied_ceiling_T_C",
    }
    for material in materials:
        if material["grounding"].get("tier") == "proxy-sintering":
            assert set(material) == base_material_keys | {"service_rating_qualifier"}
        else:
            assert set(material) == base_material_keys

    zirconia = next(
        material
        for material in materials
        if material["id"] == "zirconia_ysz"
    )
    assert zirconia["service_rating_T_C"] == pytest.approx(2200)
    assert zirconia["requested_ceiling_T_C"] == pytest.approx(1800)
    assert zirconia["effective_applied_ceiling_T_C"] == pytest.approx(1800)
    fused_silica = next(
        material
        for material in materials
        if material["id"] == "fused_silica"
    )
    assert fused_silica["service_rating_T_C"] == pytest.approx(1200)
    assert fused_silica["requested_ceiling_T_C"] == pytest.approx(1800)
    assert fused_silica["effective_applied_ceiling_T_C"] == pytest.approx(1200)
    sintered_regolith = next(
        material
        for material in materials
        if material["id"] == "sintered_regolith"
    )
    assert sintered_regolith["max_service_T_C"] == pytest.approx(1200)
    assert sintered_regolith["service_rating_T_C"] == pytest.approx(1200)
    assert sintered_regolith["requested_ceiling_T_C"] == pytest.approx(1800)
    assert sintered_regolith["effective_applied_ceiling_T_C"] == pytest.approx(1200)
    assert sintered_regolith["grounding"]["tier"] == "proxy-sintering"
    assert sintered_regolith["grounding"]["source"] == "Warren et al. 2022 (arXiv:2205.06855)"
    assert sintered_regolith["service_rating_qualifier"] == {
        "tier": "proxy-sintering",
        "source": "Warren et al. 2022 (arXiv:2205.06855)",
        "caveat": sintered_regolith["grounding"]["caveat"],
    }
    assert "not a certified refractory hot-face" in (
        sintered_regolith["service_rating_qualifier"]["caveat"]
    )


def test_c4_operator_presets_render_from_server_setpoints(monkeypatch):
    original_load_yaml = web_routes._load_yaml
    setpoints = copy.deepcopy(original_load_yaml("setpoints.yaml"))
    c4 = setpoints["campaigns"]["C4"]
    c4["temp_range_C"] = [1500, 1715]
    c4["pO2_mbar_default"] = 0.42
    c4["p_total_mbar_default"] = 7.5
    c4["max_hold_hr"] = 12
    setpoints["furnace"]["induction_stirring"][
        "rate_acceleration_factor"
    ] = [3, 9]

    def fake_load_yaml(filename):
        if filename == "setpoints.yaml":
            return setpoints
        return original_load_yaml(filename)

    monkeypatch.setattr(web_routes, "_load_yaml", fake_load_yaml)
    app = app_module.create_app()

    client = app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="lever-po2-mbar"' in html
    assert 'value="0.42"' in html
    assert 'id="c4-max-temp"' in html
    assert 'value="1715"' in html
    assert "Setpoint stir: 3-9x" in html

    disclosure = client.get("/partials/disclosure/C4")
    assert disclosure.status_code == 200
    disclosure_html = disclosure.get_data(as_text=True)
    assert 'value="1715"' in disclosure_html
    assert "Setpoint default (0.42)" in disclosure_html
    assert "Setpoint default (3-9x)" in disclosure_html
    assert 'data-field="max_hours"\n                           value="12"' in disclosure_html


def test_c4_disclosure_option_lists_are_server_sourced_byte_identical():
    app = app_module.create_app()
    client = app.test_client()

    response = client.get("/partials/disclosure/C4")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    option_tags = re.findall(r"<option[^>]*>[^<]*</option>", html)
    assert option_tags[0:6] == [
        '<option value="0.2" selected>Setpoint default (0.2)</option>',
        '<option value="0.001">Hard vacuum (0.001)</option>',
        '<option value="0.2">Low (0.2)</option>',
        '<option value="1.0">Medium (1.0)</option>',
        '<option value="5.0">High (5.0)</option>',
        '<option value="50.0">MRE backpressure (50)</option>',
    ]
    assert option_tags[6:11] == [
        '<option value="6" selected>Setpoint default (4-8x)</option>',
        '<option value="8">High (8&times;)</option>',
        '<option value="6">Medium (6&times;)</option>',
        '<option value="4">Low (4&times;)</option>',
        '<option value="1">Off (1&times;)</option>',
    ]


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (
            ("campaigns", "C4", "temp_range_C"),
            _DELETE,
            "campaigns.C4.temp_range_C",
        ),
        (
            ("campaigns", "C4", "pO2_mbar_default"),
            "bad",
            "campaigns.C4.pO2_mbar_default",
        ),
        (
            ("campaigns", "C4", "max_hold_hr"),
            _DELETE,
            "campaigns.C4.max_hold_hr",
        ),
        (
            ("furnace", "induction_stirring", "rate_acceleration_factor"),
            ["bad", 8],
            "furnace.induction_stirring.rate_acceleration_factor",
        ),
    ],
)
def test_c4_operator_presets_fail_loud_without_magic_defaults(
    path,
    value,
    message,
):
    setpoints = copy.deepcopy(web_routes._load_yaml("setpoints.yaml"))
    _set_nested(setpoints, path, value)

    with pytest.raises(ValueError, match=message):
        web_routes._c4_operator_preset_payload(setpoints)


def test_furnace_material_dropdown_hydrates_honest_labels_at_source():
    controls = (
        _REPO_ROOT / "web/static/js/simulator-controls.js"
    ).read_text()
    ticks = (_REPO_ROOT / "web/static/js/simulator-ticks.js").read_text()

    assert "furnaceMaterialOptionText(material)" in controls
    assert "proxy cap (sintering-based, uncertified)" in controls
    assert "`service ${service} C`" in controls
    assert "effective_applied_ceiling_T_C" in controls
    assert "`${material.display_name} (${material.max_service_T_C} C)`" not in controls
    assert "hydrateHonestFurnaceMaterialLabels" not in ticks
    assert "MutationObserver" not in ticks
    assert ".catch(() => {})" not in ticks


def test_c4_start_payload_uses_rendered_setpoint_not_literal_1670():
    controls = (
        _REPO_ROOT / "web/static/js/simulator-controls.js"
    ).read_text()

    assert "c4_max_temp_C: selectedC4MaxTempC()" in controls
    assert "|| 1670" not in controls


@pytest.mark.parametrize(
    ("payload_extra", "expected_cap"),
    [
        ({"furnace_material_id": "dense_alumina_continuous"}, 1700.0),
        # Cap-preserving: a material whose max (2200) exceeds the 1800 default
        # must resolve to min(1800, 2200) = 1800, never raising the ceiling.
        ({"furnace_material_id": "zirconia_ysz"}, 1800.0),
        ({}, 1800.0),
        ({"furnace_material_id": ""}, 1800.0),
    ],
)
def test_web_start_event_resolves_furnace_material_cap(
    monkeypatch,
    payload_extra,
    expected_cap,
):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
        "track": "pyrolysis",
    }
    payload.update(payload_extra)

    try:
        client.emit("start_simulation", payload)
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]
        assert statuses
        assert statuses[-1]["status"] == "started"

        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        assert state is not None
        assert (
            state["session"].simulator.campaign_mgr.furnace_max_T_C
            == pytest.approx(expected_cap)
        )
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_web_start_event_defaults_c4_temp_from_setpoints(monkeypatch):
    original_load_yaml = web_events._load_yaml
    setpoints = copy.deepcopy(original_load_yaml("setpoints.yaml"))
    setpoints["campaigns"]["C4"]["temp_range_C"] = [1500, 1715]
    captured_tasks = []

    def fake_load_yaml(filename):
        if filename == "setpoints.yaml":
            return copy.deepcopy(setpoints)
        return original_load_yaml(filename)

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr(web_events, "_load_yaml", fake_load_yaml)
    monkeypatch.setattr(web_events, "_get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
        "track": "pyrolysis",
    }

    try:
        client.emit("start_simulation", payload)
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]
        assert statuses
        assert statuses[-1]["status"] == "started"

        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        assert state is not None
        sim = state["session"].simulator
        assert sim.c4_max_temp_C == pytest.approx(1715)
        assert sim.campaign_mgr.c4_max_temp_C == pytest.approx(1715)
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_loaded_recipe_start_applies_patch_and_runtime_overrides(monkeypatch):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr(web_events, "_get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
                "setpoints_patch": {
                    "campaigns": {
                        "C4": {
                            "temp_range_C": [1600.0, 1660.0],
                            "pO2_mbar_default": 0.12,
                            "p_total_mbar_default": 0.12,
                        }
                    }
                },
                "runtime_campaign_overrides": {
                    "C4": {"max_hours": 7.5, "ramp_rate": 22.0}
                },
            },
        )
        statuses = [
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "simulation_status"
        ]
        assert statuses[-1]["status"] == "started"

        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        assert state is not None
        assert state["setpoints_patch"]["campaigns"]["C4"]["temp_range_C"] == [
            1600.0,
            1660.0,
        ]
        assert state["recipe_inputs"]["runtime_campaign_overrides"] == {
            "C4": {"max_hours": 7.5, "ramp_rate": 22.0}
        }
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


@pytest.mark.parametrize(
    ("material_id", "message"),
    [
        ("graphite_inert", "not selectable"),
        ("unknown_material", "unknown furnace material"),
    ],
)
def test_web_start_event_rejects_unselectable_furnace_material_before_session(
    monkeypatch,
    material_id,
    message,
):
    backend_called = False

    def fail_if_backend_resolves(_backend_name):
        nonlocal backend_called
        backend_called = True
        raise AssertionError("backend resolution should not run")

    monkeypatch.setattr("web.events._get_backend", fail_if_backend_resolves)
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
                "furnace_material_id": material_id,
            },
        )
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]

        assert statuses
        assert statuses[-1]["status"] == "error"
        assert message in statuses[-1]["message"]
        assert set(_simulations) == before
        assert backend_called is False
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_web_start_event_applies_furnace_material_after_recipe_patch(monkeypatch):
    monkeypatch.setattr("web.events._get_backend", lambda _backend_name: InternalAnalyticalBackend())
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
                "furnace_material_id": "fused_silica",
                "setpoints_patch": {"furnace_max_T_C": 1300},
            },
        )
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]

        assert statuses
        assert statuses[-1]["status"] == "started"
        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        assert state is not None
        sim = state["session"].simulator
        assert sim.campaign_mgr.furnace_max_T_C == pytest.approx(1200)
        assert state["recipe_inputs"]["furnace_material_id"] == "fused_silica"
        assert state["recipe_inputs"]["furnace_max_T_C"] == pytest.approx(1200)
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


@pytest.mark.parametrize(
    ("override", "message_field"),
    [
        ({"mass_kg": "abc"}, "mass_kg"),
        ({"mass_kg": -1}, "mass_kg"),
        ({"mass_kg": "nan"}, "mass_kg"),
        ({"mass_kg": "inf"}, "mass_kg"),
        ({"speed": "abc"}, "speed"),
        ({"speed": "inf"}, "speed"),
        (
            {
                "runtime_campaign_overrides": {
                    "C2A": {"pO2_mbar": "bad", "ramp_rate": 10}
                }
            },
            "runtime_campaign_overrides.C2A.pO2_mbar",
        ),
        (
            {"runtime_campaign_overrides": {"C2A": {"pO2_mbar": -1}}},
            "runtime_campaign_overrides.C2A.pO2_mbar",
        ),
        (
            {"runtime_campaign_overrides": {"NOT_A_CAMPAIGN": {"x": 1}}},
            "unknown runtime_campaign_overrides campaign",
        ),
        (
            {"runtime_campaign_overrides": {"C2A": {"not_a_field": 1}}},
            "unknown runtime_campaign_overrides",
        ),
        ({"c4_max_temp_C": "nan"}, "c4_max_temp_C"),
        ({"c5_enabled": {"unexpected": True}}, "c5_enabled"),
        ({"c5_enabled": True, "mre_max_voltage_V": "abc"}, "mre_max_voltage_V"),
        ({"additives": {"Na": "abc"}}, "additives.Na"),
        ({"additives": {"Na": -1}}, "additives.Na"),
        ({"additives": []}, "additives"),
    ],
)
def test_web_start_event_rejects_invalid_numeric_payload_before_session(
    monkeypatch,
    override,
    message_field,
):
    backend_called = False

    def fail_if_backend_resolves(_backend_name):
        nonlocal backend_called
        backend_called = True
        raise AssertionError("backend resolution should not run")

    monkeypatch.setattr("web.events._get_backend", fail_if_backend_resolves)
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
        "track": "pyrolysis",
    }
    payload.update(override)

    try:
        client.emit("start_simulation", payload)
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]

        assert statuses
        assert statuses[-1]["status"] == "error"
        assert message_field in statuses[-1]["message"]
        assert set(_simulations) == before
        assert backend_called is False
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


@pytest.mark.parametrize(
    ("bad_payload", "message"),
    [
        (None, "make_decision payload must be an object"),
        ([], "make_decision payload must be an object"),
        ("bad-payload", "make_decision payload must be an object"),
        ({}, "make_decision choice is required"),
        ({"choice": "   "}, "make_decision choice is required"),
    ],
)
def test_make_decision_rejects_bad_payload(monkeypatch, bad_payload, message):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        client.get_received()

        client.emit("make_decision", bad_payload)
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]

        assert statuses
        assert statuses[-1]["status"] == "error"
        assert (
            message in statuses[-1]["message"]
        )
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_make_decision_rejects_choice_not_in_pending_options(monkeypatch):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        client.get_received()
        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        sid = new_sids.pop()
        state, _ = _current_simulation_state(sid)
        session = state["session"]
        decision = DecisionPoint(
            DecisionType.PATH_AB,
            options=["A", "A_staged", "B"],
            recommendation="A_staged",
            context="choose path",
        )
        session.simulator.pending_decision = decision
        session.simulator.paused_for_decision = True

        client.emit("make_decision", {"choice": "   C2B-by-whitespace-class   "})
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]

        assert statuses
        assert statuses[-1]["status"] == "error"
        assert "is not one of" in statuses[-1]["message"]
        assert session.simulator.pending_decision is decision
        assert session.simulator.record.decisions == []
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


@pytest.mark.parametrize(
    ("bad_speed", "message"),
    [
        (-1, "speed must be >= 0"),
        (1e12, "speed must be <= 3600"),
    ],
)
def test_adjust_speed_rejects_out_of_bounds_without_mutating_state(
    monkeypatch,
    bad_speed,
    message,
):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 1.0,
                "track": "pyrolysis",
            },
        )
        client.get_received()
        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        sid = new_sids.pop()
        state, _ = _current_simulation_state(sid)
        assert state is not None

        assert state["speed"] == pytest.approx(1.0)

        client.emit("adjust_parameter", {"param": "speed", "value": bad_speed})
        received = client.get_received()
        statuses = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_status"
        ]

        assert statuses
        assert statuses[-1]["status"] == "error"
        assert message in statuses[-1]["message"]
        assert state["speed"] == pytest.approx(1.0)
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_adjust_po2_rolls_back_when_post_mutation_validation_fails(monkeypatch):
    _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    client = _identified_socket_client(app)
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        client.get_received()
        sid = (set(_simulations) - before).pop()
        state, _ = _current_simulation_state(sid)
        melt = state["session"].simulator.melt
        snapshot = (melt.pO2_mbar, melt.p_total_mbar, melt.atmosphere)

        def reject_pressure_state():
            raise ValueError("pressure validation failed")

        monkeypatch.setattr(melt, "validate_melt_pressures", reject_pressure_state)
        client.emit("adjust_parameter", {"param": "pO2_mbar", "value": 1.5})
        statuses = [
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "simulation_status"
        ]

        assert (melt.pO2_mbar, melt.p_total_mbar, melt.atmosphere) == snapshot
        assert statuses[-1]["status"] == "error"
        assert "pressure validation failed" in statuses[-1]["message"]
        assert all(item["status"] != "parameter_adjusted" for item in statuses)
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_adjust_campaign_override_rolls_back_after_validation_failure(monkeypatch):
    _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    client = _identified_socket_client(app)
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        client.get_received()
        sid = (set(_simulations) - before).pop()
        state, _ = _current_simulation_state(sid)
        sim = state["session"].simulator
        melt = sim.melt
        campaign_name = melt.campaign.name
        melt_snapshot = (melt.pO2_mbar, melt.p_total_mbar, melt.atmosphere)
        overrides_snapshot = copy.deepcopy(sim.campaign_mgr.overrides)

        def reject_pressure_state():
            raise ValueError("pressure validation failed")

        monkeypatch.setattr(melt, "validate_melt_pressures", reject_pressure_state)
        client.emit(
            "adjust_parameter",
            {
                "param": "campaign_override",
                "campaign": campaign_name,
                "field": "pO2_mbar",
                "value": 1.5,
            },
        )
        statuses = [
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "simulation_status"
        ]

        assert (melt.pO2_mbar, melt.p_total_mbar, melt.atmosphere) == melt_snapshot
        assert sim.campaign_mgr.overrides == overrides_snapshot
        assert statuses[-1]["status"] == "error"
        assert "pressure validation failed" in statuses[-1]["message"]
        assert all(item["status"] != "parameter_adjusted" for item in statuses)
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"param": ""}, "unsupported parameter adjustment"),
        ({"param": "not_supported", "value": 1}, "unsupported parameter adjustment"),
        (
            {"param": "campaign_override", "campaign": "C2A", "value": 1},
            "requires campaign and field",
        ),
        (
            {"param": "campaign_override", "field": "pO2_mbar", "value": 1},
            "requires campaign and field",
        ),
    ],
)
def test_adjust_parameter_rejects_unknown_or_incomplete_noops(
    monkeypatch,
    payload,
    message,
):
    _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    client = _identified_socket_client(app)
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        client.get_received()
        client.emit("adjust_parameter", payload)
        statuses = [
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "simulation_status"
        ]

        assert statuses[-1]["status"] == "error"
        assert message in statuses[-1]["message"]
        assert all(item["status"] != "parameter_adjusted" for item in statuses)
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_replacing_simulation_state_stops_prior_run():
    sid = "test-replace"
    try:
        first, first_lock = _replace_simulation_state(
            sid, object(), speed=0.0)
        second, second_lock = _replace_simulation_state(
            sid, object(), speed=0.0)

        assert first["running"] is False
        assert second["running"] is True
        assert first["run_id"] != second["run_id"]
        assert _simulations[sid] is second
        assert _sim_locks[sid] is second_lock
        assert first_lock is not second_lock
    finally:
        _clear_simulation_state(sid)


def test_socket_run_is_fully_initialized_when_published(tmp_path, monkeypatch):
    _force_socketio_internal_analytical(monkeypatch)
    store = RunArtifactStore(tmp_path / "runs")
    monkeypatch.setattr(web_events, "get_run_store", lambda: store)
    original_replace = web_events._replace_simulation_state
    published = []

    def inspect_publication(*args, **kwargs):
        state, lock = original_replace(*args, **kwargs)
        required = {
            "backend_message",
            "backend_status",
            "backend_authoritative",
            "recipe_inputs",
            "setpoints_patch",
            "resolved_setpoints_patch",
        }
        assert required <= state.keys()
        published.append(state)
        return state, lock

    monkeypatch.setattr(
        web_events,
        "_replace_simulation_state",
        inspect_publication,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    try:
        result = client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
            },
            callback=True,
        )
        assert result["status"] == "started"
        assert len(published) == 1
    finally:
        client.disconnect()


def test_http_identified_socket_binds_client_identity_before_arbitration(
    tmp_path,
    monkeypatch,
):
    _force_socketio_internal_analytical(monkeypatch)
    store = RunArtifactStore(tmp_path / "runs")
    monkeypatch.setattr(web_events, "get_run_store", lambda: store)
    app = app_module.create_app()
    client = _identified_socket_client(app)
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
    }
    try:
        first = client.emit("start_simulation", payload, callback=True)
        first_state = next(
            state
            for state in _simulations.values()
            if state.get("run_id") == first["run_id"]
        )
        client_id = first_state.get("ledger_client_id")
        assert isinstance(client_id, str) and client_id
        assert client_id in web_events._socket_client_ids.values()

        second = client.emit("start_simulation", payload, callback=True)
        owned = [
            state
            for state in _simulations.values()
            if state.get("ledger_client_id") == client_id
        ]
        assert len(owned) == 1
        assert owned[0]["run_id"] == second["run_id"]
        assert store.load(first["run_id"])["lifecycle"] == "cancelled"
    finally:
        client.disconnect()


def test_stale_run_id_cannot_emit_after_restart():
    sid = "test-stale-run"

    class Recorder:
        def __init__(self):
            self.emitted = []

        def emit(self, event, payload, room=None):
            self.emitted.append((event, payload, room))

    try:
        first, _ = _replace_simulation_state(sid, object(), speed=0.0)
        second, _ = _replace_simulation_state(sid, object(), speed=0.0)
        recorder = Recorder()

        state, lock = _current_simulation_state(sid, first["run_id"])
        assert state is None
        assert lock is None
        assert _emit_if_current(
            recorder, sid, first["run_id"], "simulation_tick", {"stale": True}
        ) is False
        assert recorder.emitted == []

        assert _emit_if_current(
            recorder, sid, second["run_id"], "simulation_tick", {"fresh": True}
        ) is True
        assert recorder.emitted == [
            (
                "simulation_tick",
                {"fresh": True, "run_id": second["run_id"]},
                sid,
            )
        ]
    finally:
        _clear_simulation_state(sid)


def test_restart_waits_for_current_emit_and_payload_identifies_run():
    sid = "test-emit-restart-order"
    emit_entered = threading.Event()
    release_emit = threading.Event()
    replacement_done = threading.Event()
    emitted = []

    class BlockingRecorder:
        def emit(self, event, payload, room=None):
            emitted.append((event, payload, room))
            emit_entered.set()
            assert release_emit.wait(timeout=2.0)

    try:
        first, _ = _replace_simulation_state(sid, object(), speed=0.0)
        emitter = threading.Thread(
            target=_emit_if_current,
            args=(
                BlockingRecorder(),
                sid,
                first["run_id"],
                "simulation_tick",
                {"hour": 1},
            ),
        )
        emitter.start()
        assert emit_entered.wait(timeout=2.0)

        def replace():
            _replace_simulation_state(sid, object(), speed=0.0)
            replacement_done.set()

        replacer = threading.Thread(target=replace)
        replacer.start()
        assert not replacement_done.wait(timeout=0.05)
        release_emit.set()
        emitter.join(timeout=2.0)
        replacer.join(timeout=2.0)

        assert replacement_done.is_set()
        assert emitted == [
            (
                "simulation_tick",
                {"hour": 1, "run_id": first["run_id"]},
                sid,
            )
        ]
    finally:
        release_emit.set()
        _clear_simulation_state(sid)


def test_loop_rechecks_pause_after_acquiring_run_lock(monkeypatch):
    sid = "test-pause-after-lock"
    state, _ = _replace_simulation_state(
        sid,
        SimpleNamespace(simulator=object(), is_complete=lambda: False),
        speed=0.0,
    )
    drive_calls = []

    class PauseOnEnter:
        def __enter__(self):
            state["paused"] = True

        def __exit__(self, *_args):
            return False

    class StopPausedLoop(Exception):
        pass

    class Socket:
        def start_background_task(self, target):
            self.target = target
            return object()

        def sleep(self, _seconds):
            raise StopPausedLoop

    socket = Socket()
    monkeypatch.setattr(
        web_events,
        "drive_session",
        lambda *_args, **_kwargs: drive_calls.append(True),
    )
    try:
        web_events._start_background_loop(
            socket,
            sid,
            state["run_id"],
            PauseOnEnter(),
            "backend",
            "ok",
            True,
        )
        with pytest.raises(StopPausedLoop):
            socket.target()
        assert drive_calls == []
    finally:
        _clear_simulation_state(sid)


def _terminal_runner_document(status: str) -> dict[str, object]:
    return {
        "schema_version": "1.4.0",
        "status": status,
        "reason": "terminal reason" if status != "ok" else "",
        "error_message": "terminal failure" if status != "ok" else "",
        "run_metadata": {
            "started_at_utc": "2026-07-15T12:00:00Z",
            "feedstock_id": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
            "backend": "stub",
        },
        "per_hour_summary": [
            {"hour": 1, "campaign": "C0", "T_C": 900.0, "mass_balance_pct": 0.0}
        ],
        "final_state": {"process.cleaned_melt": {"SiO2": 2.0}},
        "final": {"wall_deposit_by_species_kg": {"SiO": 0.25}},
        "stage_purity_report": {"stage_1": {"verdict": "PURE"}},
        "vapor_pressure_source_report": {
            "vapor_pressure_backend_status": "fallback",
            "authoritative_for_requested_vapor_pressure": False,
        },
    }


def test_run_loop_captures_detached_mol_ledger_at_hour_boundary(monkeypatch):
    sid = "test-hourly-ledger-capture"
    captured_payloads = []

    class Ledger:
        balances = {"process.cleaned_melt": {"SiO2": 1.25}}

        def mol_by_account(self):
            return self.balances

    ledger = Ledger()
    sim = SimpleNamespace(atom_ledger=ledger, _poisoned_hour=None)

    class Session:
        completed = False
        simulator = sim

        def is_complete(self):
            return self.completed

        def result_document(self):
            return _terminal_runner_document("ok")

    session = Session()

    class Socket:
        def start_background_task(self, target):
            self.target = target
            return object()

        def emit(self, event, _payload, room=None):
            if event == "simulation_tick":
                ledger.balances["process.cleaned_melt"]["SiO2"] = 99.0

        def sleep(self, _seconds):
            pass

    socket = Socket()
    state, lock = _replace_simulation_state(sid, session, speed=0.0)
    state["run_store"] = object()
    step_result = SimpleNamespace(
        snapshot=object(),
        backend_error="",
        per_hour_summary={"hour": 1},
        campaign_summary=None,
        decision_event=None,
    )

    def drive_one(*_args, **_kwargs):
        session.completed = True
        return iter([step_result])

    def capture_artifact(payload, _run_id, *, store):
        captured_payloads.append(copy.deepcopy(payload))
        return {"execution_status": "ok"}

    monkeypatch.setattr(web_events, "drive_session", drive_one)
    monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {})
    monkeypatch.setattr(web_events, "persist_run_artifact", capture_artifact)

    try:
        web_events._start_background_loop(
            socket,
            sid,
            state["run_id"],
            lock,
            "backend",
            "ok",
            True,
        )
        socket.target()

        assert captured_payloads[0]["per_hour_ledger"] == {
            "1": {"process.cleaned_melt": {"SiO2": 1.25}}
        }
    finally:
        _clear_simulation_state(sid)


@pytest.mark.parametrize(
    ("terminal_path", "terminal_event"),
    [
        ("complete", "simulation_complete"),
        ("refused", "simulation_status"),
        ("failed", "simulation_status"),
        ("c6_refused", "simulation_status"),
    ],
)
def test_terminal_state_finishes_before_terminal_emit(
    monkeypatch,
    terminal_path,
    terminal_event,
):
    sid = f"test-terminal-order-{terminal_path}"
    session = SimpleNamespace(
        simulator=SimpleNamespace(_poisoned_hour=None),
        is_complete=lambda: terminal_path == "complete",
    )
    state, lock = _replace_simulation_state(sid, session, speed=0.0)
    observed = []

    class Socket:
        def start_background_task(self, target):
            self.target = target
            return object()

        def emit(self, event, payload, room=None):
            if event == terminal_event:
                observed.append((state["running"], state["paused"], payload))

    socket = Socket()

    def persist_terminal(*_args, status, **_kwargs):
        state["artifact_persisted"] = True
        return {"execution_status": status}

    if terminal_path == "refused":
        def drive(*_args, **_kwargs):
            raise KnudsenRegimeRefusal({"reason": "binding refusal"})
    elif terminal_path == "failed":
        def drive(*_args, **_kwargs):
            raise RuntimeError("terminal failure")
    else:
        campaign_summary = None
        if terminal_path == "c6_refused":
            campaign_summary = {
                "c6_refusal_diagnostic": {
                    "status": "refused",
                    "diagnostic": {"reason_refused": "c6 refused"},
                }
            }

        def drive(*_args, **_kwargs):
            return iter([
                SimpleNamespace(
                    snapshot=object(),
                    backend_error="",
                    per_hour_summary={"hour": 1},
                    campaign_summary=campaign_summary,
                    decision_event=None,
                )
            ])

    monkeypatch.setattr(web_events, "_persist_terminal", persist_terminal)
    monkeypatch.setattr(web_events, "drive_session", drive)
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {})
    monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})

    try:
        web_events._start_background_loop(
            socket,
            sid,
            state["run_id"],
            lock,
            "backend",
            "ok",
            True,
        )
        socket.target()

        assert len(observed) == 1
        assert observed[0][:2] == (False, False)
    finally:
        _clear_simulation_state(sid)


def test_tick_snapshot_is_built_before_concurrent_mutation(monkeypatch):
    sid = "test-tick-snapshot-lock-order"
    sim = SimpleNamespace(live_pressure=1.0, atom_ledger=None)
    session = SimpleNamespace(simulator=sim, is_complete=lambda: False)
    state, _ = _replace_simulation_state(sid, session, speed=0.0)
    payload_started = threading.Event()
    mutation_attempted = threading.Event()
    mutation_done = threading.Event()
    captured_ticks = []

    class TrackingLock:
        def __init__(self):
            self._lock = threading.RLock()
            self.owner = None
            self.depth = 0

        def __enter__(self):
            if threading.current_thread().name == "tick-mutator":
                mutation_attempted.set()
            self._lock.acquire()
            self.owner = threading.get_ident()
            self.depth += 1
            return self

        def __exit__(self, *_args):
            self.depth -= 1
            if self.depth == 0:
                self.owner = None
            self._lock.release()

    run_lock = TrackingLock()
    with web_events._simulations_guard:
        web_events._sim_locks[sid] = run_lock

    class Socket:
        def start_background_task(self, target):
            self.target = target
            return object()

    socket = Socket()
    step = SimpleNamespace(
        snapshot=object(),
        backend_error="",
        per_hour_summary={"hour": 1},
        campaign_summary=None,
        decision_event=None,
    )
    monkeypatch.setattr(
        web_events,
        "drive_session",
        lambda *_args, **_kwargs: iter([step]),
    )

    def build_tick(**_kwargs):
        payload_started.set()
        assert mutation_attempted.wait(2)
        assert run_lock.owner == threading.get_ident()
        return {"live_pressure": sim.live_pressure}

    def emit_after_mutation(_socketio, _sid, _run_id, event, payload):
        if event == "simulation_tick":
            assert mutation_done.wait(2)
            captured_ticks.append(copy.deepcopy(payload))
            state["running"] = False
            return False
        return True

    monkeypatch.setattr(web_events, "_tick_payload", build_tick)
    monkeypatch.setattr(web_events, "_emit_if_current", emit_after_mutation)

    def mutate():
        assert payload_started.wait(2)
        with run_lock:
            sim.live_pressure = 2.0
            mutation_done.set()

    mutator = threading.Thread(target=mutate, name="tick-mutator")
    mutator.start()
    try:
        web_events._start_background_loop(
            socket,
            sid,
            state["run_id"],
            run_lock,
            "backend",
            "ok",
            True,
        )
        socket.target()
        mutator.join(2)

        assert mutation_done.is_set()
        assert sim.live_pressure == 2.0
        assert captured_ticks == [{"live_pressure": 1.0}]
        assert state["last_recipe_capture"]["tick"] == {"live_pressure": 1.0}
    finally:
        mutator.join(2)
        _clear_simulation_state(sid)


@pytest.mark.parametrize("outcome", ["ok", "refused", "failed"])
def test_terminal_outcomes_persist_full_runner_document(tmp_path, monkeypatch, outcome):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    logged = []
    monkeypatch.setattr(web_events, "_safe_log", logged.append)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    before = set(_simulations)

    class Session:
        simulator = SimpleNamespace(_poisoned_hour=None)

        def is_complete(self):
            return outcome == "ok"

        def result_document(self):
            recorded_outcome = "failed" if outcome == "ok" else "ok"
            return _terminal_runner_document(recorded_outcome)

    if outcome == "ok":
        monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {"done": True})
    elif outcome == "refused":
        monkeypatch.setattr(
            web_events,
            "drive_session",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KnudsenRegimeRefusal({"reason": "binding refusal"})
            ),
        )
    else:
        monkeypatch.setattr(
            web_events,
            "drive_session",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        sid = (set(_simulations) - before).pop()
        state = _simulations[sid]
        state["session"] = Session()
        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        store = RunArtifactStore(tmp_path / "runs")
        artifact = store.load(state["run_id"])
        assert artifact is not None
        assert artifact["execution_status"] == outcome
        assert artifact["terminal"]["final_state"] == {
            "process.cleaned_melt": {"SiO2": 2.0}
        }
        assert artifact["terminal"]["final"] == {
            "wall_deposit_by_species_kg": {"SiO": 0.25}
        }
        if outcome != "ok":
            assert artifact["failure"]["error_message"]
        assert any(
            f"observed={outcome!r}; using observed outcome" in message
            for message in logged
        )
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


@pytest.mark.parametrize(
    ("submitted_patch", "expected_patch", "expected_pins"),
    [
        (None, {}, []),
        (
            {"campaigns": {"C4": {"temp_range_C": [1600.0, 1660.0]}}},
            {"campaigns": {"C4": {"temp_range_C": [1600.0, 1660.0]}}},
            ["campaigns.C4.temp_range_C"],
        ),
    ],
)
def test_real_web_session_uses_canonical_runner_projector(
    tmp_path,
    monkeypatch,
    submitted_patch,
    expected_patch,
    expected_pins,
):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    before = set(_simulations)

    try:
        submission = {
            "backend": "internal-analytical",
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000,
            "speed": 0,
            "track": "pyrolysis",
        }
        if submitted_patch is not None:
            submission["setpoints_patch"] = submitted_patch
        client.emit(
            "start_simulation",
            submission,
        )
        sid = (set(_simulations) - before).pop()
        state = _simulations[sid]
        session = state["session"]
        session.advance()
        session.is_complete = lambda: True
        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        artifact = RunArtifactStore(tmp_path / "runs").load(state["run_id"])
        assert artifact is not None
        assert artifact["execution_status"] == "ok"
        assert len(artifact["timesteps"]) == 1
        assert artifact["terminal"]["final_state"]
        assert artifact["terminal"]["final"]
        run_metadata = artifact["terminal"]["run_metadata"]
        assert run_metadata["backend"] == "internal-analytical"
        assert run_metadata["cost_rollup_diagnostic"]
        assert artifact["header"]["recipe_snapshot"] == {
            "setpoints_patch": expected_patch,
            "pins": expected_pins,
            "recipe_schema_version": "recipe-schema-v1",
        }
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_real_web_session_failure_persists_non_sparse_terminal(tmp_path, monkeypatch):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        sid = (set(_simulations) - before).pop()
        state = _simulations[sid]
        monkeypatch.setattr(
            web_events,
            "drive_session",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("terminal boom")
            ),
        )
        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        artifact = RunArtifactStore(tmp_path / "runs").load(state["run_id"])
        assert artifact is not None
        assert artifact["execution_status"] == "failed"
        assert artifact["failure"]["error_message"] == "terminal boom"
        assert artifact["terminal"]["final_state"]
        assert artifact["terminal"]["final"]
        assert artifact["terminal"]["run_metadata"]["cost_rollup_diagnostic"]
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_web_run_payload_captures_effective_config_sources(monkeypatch):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    captured_payloads = []

    def capture_persist(runner_payload, _run_id, *, store):
        assert store is not None
        captured_payloads.append(copy.deepcopy(runner_payload))
        return {"execution_status": "ok"}

    monkeypatch.setattr(web_events, "persist_run_artifact", capture_persist)
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {})
    app = app_module.create_app()
    client = _identified_socket_client(app)
    before = set(_simulations)
    cost_parameters = default_cost_parameters_block()
    cost_parameters["parameters"]["electricity_cost_per_kWh"]["value"] = 12.0
    cost_parameters["parameters"]["solar_heat_cost_per_kWh"]["value"] = 0.07

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
                "setpoints_patch": {
                    "campaigns": {"C4": {"temp_range_C": [1600.0, 1660.0]}}
                },
                "cost_parameters": cost_parameters,
            },
        )
        sid = (set(_simulations) - before).pop()
        state = _simulations[sid]
        state["session"] = SimpleNamespace(
            simulator=SimpleNamespace(_poisoned_hour=None),
            is_complete=lambda: True,
            result_document=lambda: _terminal_runner_document("ok"),
        )
        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        assert len(captured_payloads) == 1
        effective_config = captured_payloads[0]["effective_config"]
        assert effective_config["campaigns.C4.temp_range_C"] == {
            "value": [1600.0, 1660.0],
            "source": "override",
        }
        assert effective_config["campaigns.C4.pO2_mbar_default"]["source"] == (
            "default"
        )
        assert "campaigns.C0.pO2_mbar" not in effective_config
        assert (
            "completion_contracts.gated_steps.C0.contracts"
            not in effective_config
        )
        assert "mass_kg" not in effective_config
        captured_costs = captured_payloads[0]["cost_parameters"]["parameters"]
        assert captured_costs["electricity_cost_per_kWh"]["value"] == 12.0
        assert captured_costs["solar_heat_cost_per_kWh"]["value"] == 0.07
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_empty_setpoint_resolution_omits_effective_config():
    payload = {}
    effective_config = _effective_config_from_setpoints({}, override_paths=set())
    if effective_config:
        payload["effective_config"] = effective_config

    assert "effective_config" not in payload


@pytest.mark.parametrize("terminal_path", ["ok", "refused", "failed", "c6_refused"])
def test_persist_failure_visible_on_all_terminal_paths(
    tmp_path,
    monkeypatch,
    terminal_path,
):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    before = set(_simulations)

    session = SimpleNamespace(
        simulator=SimpleNamespace(_poisoned_hour=None),
        is_complete=lambda: terminal_path == "ok",
        result_document=lambda: _terminal_runner_document(
            "ok" if terminal_path == "ok" else "refused"
        ),
    )
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {"done": True})
    if terminal_path == "refused":
        monkeypatch.setattr(
            web_events,
            "drive_session",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                KnudsenRegimeRefusal({"reason": "binding refusal"})
            ),
        )
    elif terminal_path == "failed":
        monkeypatch.setattr(
            web_events,
            "drive_session",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("terminal boom")
            ),
        )
    elif terminal_path == "c6_refused":
        monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})
        monkeypatch.setattr(
            web_events,
            "drive_session",
            lambda *_args, **_kwargs: iter([
                SimpleNamespace(
                    snapshot=object(),
                    backend_error="",
                    per_hour_summary={},
                    campaign_summary={
                        "c6_refusal_diagnostic": {
                            "status": "refused",
                            "diagnostic": {"reason_refused": "c6 refused"},
                        }
                    },
                    decision_event=None,
                )
            ]),
        )
    monkeypatch.setattr(
        web_events,
        "persist_run_artifact",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        sid = (set(_simulations) - before).pop()
        state = _simulations[sid]
        state["session"] = session
        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        events = client.get_received()
        names = [event["name"] for event in events]
        assert "simulation_complete" not in names
        assert "simulation_persistence_failed" in names
        assert "persistence_retry" not in state
        statuses = [
            event["args"][0]
            for event in events
            if event["name"] == "simulation_status"
        ]
        assert statuses[-1]["status"] == "error"
        assert statuses[-1]["reason"] == "persistence_failed"
        assert state["running"] is False
        assert state["paused"] is False
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_save_ok_completion_emit_failure_is_not_persistence_failure(
    tmp_path,
    monkeypatch,
):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    before = set(_simulations)
    original_emit_if_current = web_events._emit_if_current

    def fail_completion_emit(socketio, sid, run_id, event, payload):
        if event == "simulation_complete":
            raise RuntimeError("completion transport failed")
        return original_emit_if_current(socketio, sid, run_id, event, payload)

    monkeypatch.setattr(web_events, "_emit_if_current", fail_completion_emit)
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {"done": True})

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        sid = (set(_simulations) - before).pop()
        state = _simulations[sid]
        state["session"] = SimpleNamespace(
            simulator=SimpleNamespace(_poisoned_hour=None),
            is_complete=lambda: True,
            result_document=lambda: _terminal_runner_document("ok"),
        )
        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        events = client.get_received()
        names = [event["name"] for event in events]
        statuses = [
            event["args"][0]
            for event in events
            if event["name"] == "simulation_status"
        ]
        assert "simulation_complete" not in names
        assert "simulation_persistence_failed" not in names
        assert statuses[-1]["reason"] == "completion_emit_failed"
        assert state["artifact_persisted"] is True
        assert RunArtifactStore(tmp_path / "runs").load(state["run_id"]) is not None
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_c6_refusal_emit_failure_still_cleans_run_state(tmp_path, monkeypatch):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    logged = []
    monkeypatch.setattr(web_events, "_safe_log", logged.append)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    before = set(_simulations)
    original_emit_if_current = web_events._emit_if_current

    def fail_refusal_emit(socketio, sid, run_id, event, payload):
        if event == "simulation_status" and payload.get("status") == "refused":
            raise RuntimeError("refusal transport failed")
        return original_emit_if_current(socketio, sid, run_id, event, payload)

    monkeypatch.setattr(web_events, "_emit_if_current", fail_refusal_emit)
    monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        web_events,
        "drive_session",
        lambda *_args, **_kwargs: iter([
            SimpleNamespace(
                snapshot=object(),
                backend_error="",
                per_hour_summary={},
                campaign_summary={
                    "c6_refusal_diagnostic": {
                        "status": "refused",
                        "diagnostic": {"reason_refused": "c6 refused"},
                    }
                },
                decision_event=None,
            )
        ]),
    )

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        sid = (set(_simulations) - before).pop()
        state = _simulations[sid]
        state["session"] = SimpleNamespace(
            simulator=SimpleNamespace(_poisoned_hour=None),
            is_complete=lambda: False,
            result_document=lambda: _terminal_runner_document("refused"),
        )
        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        assert any(
            "Simulation status emission failed: refusal transport failed" in message
            for message in logged
        )
        assert state["artifact_persisted"] is True
        assert state["running"] is False
        assert state["paused"] is False
        assert RunArtifactStore(tmp_path / "runs").load(state["run_id"]) is not None
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


def test_observed_terminal_without_refusal_diagnostic_clears_recorded_value():
    recorded = _terminal_runner_document("refused")
    recorded["refusal_diagnostic"] = {"reason": "stale refusal"}
    session = SimpleNamespace(result_document=lambda: recorded)

    payload = web_events._full_runner_payload(session, status="ok")

    assert "refusal_diagnostic" not in payload


def test_reduced_terminal_payload_preserves_available_submission_provenance():
    projector = SimpleNamespace(
        setpoints_patch={},
        run_metadata_overrides={"started_at_utc": "2026-07-15T12:00:00Z"},
    )
    session = SimpleNamespace(
        _config=SimpleNamespace(
            feedstock_id="lunar_mare_low_ti",
            mass_kg=1000.0,
            backend_name="stub",
            track="pyrolysis",
        ),
        simulator=SimpleNamespace(melt=SimpleNamespace(hour=3)),
        per_hour_summaries=lambda: [],
    )

    payload = web_events._available_runner_payload(
        session,
        projector=projector,
        status="failed",
        reason="runner_projection_failed",
        error_message="projection failed",
        refusal_diagnostic=None,
    )

    assert payload["run_metadata"]["started_at_utc"] == "2026-07-15T12:00:00Z"
    assert payload["recipe_snapshot"] == {
        "setpoints_patch": {},
        "pins": [],
        "recipe_schema_version": "recipe-schema-v1",
    }


@pytest.mark.parametrize("failure_site", ["completion", "tick", "emit"])
def test_loop_projection_and_emit_failures_stop_current_run(
    monkeypatch,
    failure_site,
):
    sid = f"test-loop-failure-{failure_site}"
    emitted = []

    class Socket:
        def start_background_task(self, target):
            self.target = target
            return object()

        def emit(self, event, payload, room=None):
            if failure_site == "emit" and event == "simulation_tick":
                self.failed_tick = True
                raise RuntimeError("tick emit failed")
            emitted.append((event, payload, room))

    socket = Socket()
    sim = SimpleNamespace(_poisoned_hour=None)
    session = SimpleNamespace(
        simulator=sim,
        is_complete=lambda: failure_site == "completion",
    )
    state, lock = _replace_simulation_state(sid, session, speed=0.0)
    step_result = SimpleNamespace(
        snapshot=object(),
        backend_error="",
        per_hour_summary={},
        campaign_summary=None,
        decision_event=None,
    )
    monkeypatch.setattr(
        web_events,
        "drive_session",
        lambda *_args, **_kwargs: iter([step_result]),
    )
    if failure_site == "completion":
        monkeypatch.setattr(
            web_events,
            "_completion_payload",
            lambda _sim: (_ for _ in ()).throw(RuntimeError("completion failed")),
        )
    elif failure_site == "tick":
        monkeypatch.setattr(
            web_events,
            "_tick_payload",
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("tick failed")),
        )
    else:
        monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})

    try:
        web_events._start_background_loop(
            socket,
            sid,
            state["run_id"],
            lock,
            "backend",
            "ok",
            True,
        )
        socket.target()
        assert state["running"] is False
        assert state["paused"] is False
        statuses = [payload for event, payload, _room in emitted if event == "simulation_status"]
        assert statuses[-1]["status"] == "error"
        assert statuses[-1]["run_id"] == state["run_id"]
    finally:
        _clear_simulation_state(sid)


def test_completion_payload_exposes_final_mass_reconciliation():
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {
                    "SiO2": 51.5,
                    "FeO": 13.0,
                    "MgO": 34.0,
                },
                "bulk_additions": {
                    "metallic_FeNi_wt_pct": 15.0,
                },
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")

    payload = _completion_payload(sim)

    assert payload["mass_in_kg"] == pytest.approx(1000.0)
    assert payload["mass_out_kg"] == pytest.approx(1000.0)
    assert payload["mass_balance_error_pct"] == pytest.approx(0.0)
    assert payload["stage0_mass_balance_delta_kg"] == pytest.approx(0.0)
    assert "residual_inventory_kg" in payload


def test_web_payloads_preserve_full_precision_mass_balance_error(
    monkeypatch,
):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {
                    "SiO2": 51.5,
                    "FeO": 13.0,
                    "MgO": 34.0,
                },
                "bulk_additions": {
                    "metallic_FeNi_wt_pct": 15.0,
                },
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")
    snapshot = sim._make_snapshot()
    snapshot.mass_balance_error_pct = 6.25e-12
    monkeypatch.setattr(sim, "_make_snapshot", lambda: snapshot)

    tick_payload = _tick_payload(
        sim=sim,
        snapshot=snapshot,
        backend_message="",
        backend_status="internal-analytical",
        backend_authoritative=False,
    )
    completion_payload = _completion_payload(sim)

    assert tick_payload["mass_balance_error_pct"] == pytest.approx(6.25e-12)
    assert tick_payload["mass_balance_error_breached"] is True
    assert completion_payload["mass_balance_error_pct"] == pytest.approx(6.25e-12)
    assert completion_payload["mass_balance_error_breached"] is True


def test_web_mass_balance_threshold_matches_kernel_abort_invariant():
    from simulator.optimize.evaluate import MASS_BALANCE_ABORT_PCT

    runner_source = (_REPO_ROOT / "simulator/runner.py").read_text()
    state_source = (_REPO_ROOT / "simulator/state.py").read_text()

    assert _MASS_BALANCE_ERROR_BREACH_PCT == pytest.approx(
        MASS_BALANCE_ABORT_PCT
    )
    assert MASS_BALANCE_ABORT_PCT == pytest.approx(5e-12)
    assert "<= 5e-12" in runner_source
    assert "≤5e-12" in state_source


@pytest.mark.parametrize(
    ("error_pct", "expected_breached"),
    [
        (4.99e-12, False),
        (5.01e-12, True),
    ],
)
def test_web_mass_balance_breach_numeric_boundary(
    error_pct,
    expected_breached,
):
    sim, snapshot = _sim_with_mass_balance_snapshot(error_pct)

    payload = _tick_payload(
        sim=sim,
        snapshot=snapshot,
        backend_message="",
        backend_status="internal-analytical",
        backend_authoritative=False,
    )

    assert payload["mass_balance_error_pct"] == pytest.approx(error_pct)
    assert payload["mass_balance_error_category"] is None
    assert payload["mass_balance_error_breached"] is expected_breached


def test_web_mass_balance_category_breaches_with_small_numeric_error():
    sim, snapshot = _sim_with_mass_balance_snapshot(
        4.99e-12,
        category="zero_input_basis_breach",
    )

    payload = _tick_payload(
        sim=sim,
        snapshot=snapshot,
        backend_message="",
        backend_status="internal-analytical",
        backend_authoritative=False,
    )

    assert payload["mass_balance_error_pct"] == pytest.approx(4.99e-12)
    assert payload["mass_balance_error_category"] == "zero_input_basis_breach"
    assert payload["mass_balance_error_breached"] is True


@pytest.mark.parametrize("error_pct", [float("nan"), float("inf"), float("-inf")])
def test_web_mass_balance_non_finite_error_fails_closed(error_pct):
    sim, snapshot = _sim_with_mass_balance_snapshot(error_pct)

    payload = _tick_payload(
        sim=sim,
        snapshot=snapshot,
        backend_message="",
        backend_status="internal-analytical",
        backend_authoritative=False,
    )

    assert payload["mass_balance_error_pct"] is None
    assert (
        payload["mass_balance_error_category"]
        == "non_finite_mass_balance_error"
    )
    assert payload["mass_balance_error_breached"] is True
    json.dumps(payload, allow_nan=False)


def test_completion_payload_exposes_mass_balance_category_when_pct_none(
    monkeypatch,
):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {
                    "SiO2": 51.5,
                    "FeO": 13.0,
                    "MgO": 34.0,
                },
                "bulk_additions": {
                    "metallic_FeNi_wt_pct": 15.0,
                },
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")
    snapshot = sim._make_snapshot()
    snapshot.mass_balance_error_pct = None
    setattr(
        snapshot,
        "mass_balance_error_category",
        "zero_input_basis_breach",
    )
    monkeypatch.setattr(sim, "_make_snapshot", lambda: snapshot)

    payload = _completion_payload(sim)

    assert payload["mass_balance_error_pct"] is None
    assert payload["mass_balance_error_category"] == "zero_input_basis_breach"
    assert payload["mass_balance_error_breached"] is True


def test_simulation_tick_exposes_live_pot_and_flue_composition(monkeypatch):
    captured_tasks = []
    drive_calls = {"count": 0}

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def run_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        target()
        return {"captured_task": len(captured_tasks)}

    def one_tick_drive(session, *args, **kwargs):
        drive_calls["count"] += 1
        if drive_calls["count"] > 1:
            return iter(())
        snapshot = session.simulator._make_snapshot()
        snapshot.hour = 1
        snapshot.evap_flux = EvaporationFlux(
            species_kg_hr={"Na": 1.25, "SiO": 0.5},
        )
        snapshot.evap_flux.update_totals()
        snapshot.melt_offgas_O2_mol_hr = 2.0
        return iter([
            SimpleNamespace(
                snapshot=snapshot,
                backend_error="",
                per_hour_summary={"hour": 1},
                campaign_summary=None,
                decision_event=None,
            )
        ])

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr("web.events.drive_session", one_tick_drive)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        run_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        received = client.get_received()
        ticks = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_tick"
        ]

        assert len(ticks) == 1
        tick = ticks[0]
        assert tick["mass_balance_error_pct"] == pytest.approx(0.0)
        assert tick["pot_composition"]["SiO2"] > 0
        assert tick["pot_composition_units"] == "kg"
        assert tick["pot_composition_wt_pct"]["SiO2"] > 0
        assert tick["flue_composition"]["Na"] == pytest.approx(1.25)
        assert tick["flue_composition"]["SiO"] == pytest.approx(0.5)
        assert tick["flue_composition"]["O2"] == pytest.approx(
            2.0 * 31.998 / 1000.0
        )
        assert tick["flue_composition_units"] == "kg/hr"
    finally:
        client.disconnect()
        for sid in list(_simulations):
            _clear_simulation_state(sid)


def test_web_failure_status_and_cleanup_survive_poison_enrichment_failure(
    monkeypatch,
    tmp_path,
):
    captured_tasks = _force_socketio_internal_analytical(monkeypatch)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        client.get_received()
        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        sid = new_sids.pop()
        state, _ = _current_simulation_state(sid)
        assert state is not None

        class RaisingPoisonSim:
            @property
            def _poisoned_hour(self):
                raise LookupError("poison metadata unavailable")

        class HostileSession:
            simulator = RaisingPoisonSim()

            def is_complete(self):
                return False

        state["session"] = HostileSession()

        def fail_drive_session(*_args, **_kwargs):
            raise RuntimeError("primary abort")

        monkeypatch.setattr(web_events, "drive_session", fail_drive_session)

        target, args, kwargs = captured_tasks.pop()
        target(*args, **kwargs)

        statuses = [
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "simulation_status"
        ]
        assert statuses == [
            {
                "status": "error",
                "message": "primary abort",
                "backend_status": "unavailable",
                "backend_authoritative": False,
                "backend_message": "Using built-in fallback",
                "run_id": state["run_id"],
            }
        ]
        assert state["running"] is False
        assert state["paused"] is False
    finally:
        client.disconnect()
        for sid in list(_simulations):
            _clear_simulation_state(sid)


def test_per_hour_summary_redox_fields_reach_socket_and_recipe_capture_live_path(
    monkeypatch,
):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    original_emit = web_events._emit_if_current

    def emit_and_stop_after_summary(socketio, sid, run_id, event, payload):
        emitted = original_emit(socketio, sid, run_id, event, payload)
        if event == "per_hour_summary":
            state, _ = _current_simulation_state(sid, run_id)
            if state is not None:
                state["running"] = False
        return emitted

    def run_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        target()
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr(web_events, "_get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(web_events, "_emit_if_current", emit_and_stop_after_summary)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        run_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        received = client.get_received()
        summaries = [
            event["args"][0]
            for event in received
            if event["name"] == "per_hour_summary"
        ]
        assert len(summaries) == 1
        summary = summaries[0]
        redox = summary["fe_redox_split"]
        for key in (
            "fO2_log",
            "ferric_frac",
            "ferrous_frac",
            "fe3_over_sigma_fe",
            "fe2o3_over_feo_molar",
            "native_fe_frac",
            "native_fe_saturation_event",
            "diagnostic_only",
            "source",
        ):
            assert key in redox
        assert redox["diagnostic_only"] is True
        event = redox["native_fe_saturation_event"]
        assert event["native_fe_event"]
        assert event["native_fe_event_status"]

        stage_3 = summary["stage_3_capture"]
        for key in ("Fe_kg", "total_kg", "Fe_wt_pct"):
            assert key in stage_3

        breakdown = summary["redox_source_breakdown"]
        for key in (
            "net_mol_o2_equiv",
            "delta_log10_fO2",
            "ferric_divergence",
            "source_campaign",
            "source_hour",
            "source_campaign_hour",
        ):
            assert key in breakdown

        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        capture = state["last_recipe_capture"]["per_hour_summary"]
        assert capture["fe_redox_split"]["fO2_log"] == pytest.approx(
            redox["fO2_log"]
        )
        assert capture["fe_redox_split"]["native_fe_saturation_event"] == event
        assert capture["stage_3_capture"]["Fe_wt_pct"] == pytest.approx(
            stage_3["Fe_wt_pct"]
        )
        assert capture["redox_source_breakdown"]["ferric_divergence"]["status"] == (
            breakdown["ferric_divergence"]["status"]
        )
    finally:
        client.disconnect()
        for sid in list(_simulations):
            _clear_simulation_state(sid)


def test_optional_native_fe_nested_redox_payloads_reach_socket_and_recipe_capture(
    monkeypatch,
):
    captured_tasks = []
    drive_calls = {"count": 0}
    redox_summary = copy.deepcopy(_producer_backed_redox_per_hour_summary())
    redox_summary["fe_redox_split"]["native_fe_saturation_event"] = {
        "native_fe_event": "native_fe_partitioned_saturation",
        "native_fe_event_reason": "native_fe_saturation_split_applied",
        "native_fe_event_status": "ok",
    }
    redox_summary["fe_redox_split"]["native_fe_partition"] = {
        "native_fe_pool_mol": 12.5,
        "native_fe_tap_mol": 12.0,
        "native_fe_vapor_mol": 0.5,
        "native_fe_vapor_escape_fraction_of_pool": 0.04,
    }

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def run_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        target()
        return {"captured_task": len(captured_tasks)}

    def one_tick_drive(session, *args, **kwargs):
        drive_calls["count"] += 1
        if drive_calls["count"] > 1:
            return iter(())
        snapshot = session.simulator._make_snapshot()
        snapshot.hour = 1
        return iter([
            SimpleNamespace(
                snapshot=snapshot,
                backend_error="",
                per_hour_summary=copy.deepcopy(redox_summary),
                campaign_summary=None,
                decision_event=None,
            )
        ])

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr("web.events.drive_session", one_tick_drive)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        run_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        received = client.get_received()
        summaries = [
            event["args"][0]
            for event in received
            if event["name"] == "per_hour_summary"
        ]
        assert len(summaries) == 1
        redox = summaries[0]["fe_redox_split"]
        assert redox["native_fe_saturation_event"]["native_fe_event"] == (
            "native_fe_partitioned_saturation"
        )
        assert redox["native_fe_saturation_event"]["native_fe_event_status"] == "ok"
        assert redox["native_fe_partition"]["native_fe_pool_mol"] == pytest.approx(
            12.5
        )
        assert redox["native_fe_partition"]["native_fe_tap_mol"] == pytest.approx(
            12.0
        )
        assert redox["native_fe_partition"]["native_fe_vapor_mol"] == pytest.approx(
            0.5
        )

        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        state, _ = _current_simulation_state(new_sids.pop())
        capture_redox = state["last_recipe_capture"]["per_hour_summary"][
            "fe_redox_split"
        ]
        assert capture_redox["native_fe_saturation_event"] == (
            redox["native_fe_saturation_event"]
        )
        assert capture_redox["native_fe_partition"]["native_fe_vapor_mol"] == (
            pytest.approx(redox["native_fe_partition"]["native_fe_vapor_mol"])
        )
    finally:
        client.disconnect()
        for sid in list(_simulations):
            _clear_simulation_state(sid)


def test_simulation_tick_exposes_mass_balance_category_when_pct_none(
    monkeypatch,
):
    captured_tasks = []
    drive_calls = {"count": 0}

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def run_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        target()
        return {"captured_task": len(captured_tasks)}

    def one_tick_drive(session, *args, **kwargs):
        drive_calls["count"] += 1
        if drive_calls["count"] > 1:
            return iter(())
        snapshot = session.simulator._make_snapshot()
        snapshot.hour = 1
        snapshot.mass_balance_error_pct = None
        setattr(
            snapshot,
            "mass_balance_error_category",
            "zero_input_basis_breach",
        )
        return iter([
            SimpleNamespace(
                snapshot=snapshot,
                backend_error="",
                per_hour_summary={"hour": 1},
                campaign_summary=None,
                decision_event=None,
            )
        ])

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr("web.events.drive_session", one_tick_drive)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        run_background_task,
    )
    app = app_module.create_app()
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        received = client.get_received()
        ticks = [
            event["args"][0]
            for event in received
            if event["name"] == "simulation_tick"
        ]

        assert len(ticks) == 1
        tick = ticks[0]
        assert tick["mass_balance_error_pct"] is None
        assert tick["mass_balance_error_category"] == "zero_input_basis_breach"
    finally:
        client.disconnect()
        for sid in list(_simulations):
            _clear_simulation_state(sid)


def test_socketio_reports_binding_c6_refusal_after_retaining_run_data(
    monkeypatch,
    tmp_path,
):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = _identified_socket_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    events = []
    try:
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "ci_carbonaceous_chondrite",
                "mass_kg": 1000,
                "additives": {"C": 30.0},
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        events.extend(client.get_received())
        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        sid = new_sids.pop()

        for _ in range(10):
            captured_tasks[-1]()
            events.extend(client.get_received())
            state = _simulations[sid]
            if not state["running"]:
                break
            decision = state["session"].pending_decision()
            assert decision is not None
            client.emit("make_decision", {"choice": decision.recommendation})
            events.extend(client.get_received())
        else:
            raise AssertionError("Socket.IO run did not reach the C6 refusal")

        names = [event["name"] for event in events]
        statuses = [
            event["args"][0]
            for event in events
            if event["name"] == "simulation_status"
        ]
        refusal = next(
            status for status in statuses if status.get("status") == "refused"
        )
        refusal_event_index = next(
            index
            for index, event in enumerate(events)
            if (
                event["name"] == "simulation_status"
                and event["args"][0].get("status") == "refused"
            )
        )

        # C6 cold-hold (1450 -> 1400 C, wave-09) reaches the binding CI
        # refusal one ramp-hour earlier: 42 hours (was 43 at the 1450 recipe;
        # controller-verified pre-existing vs the token flip).
        assert names.count("simulation_tick") == 42
        assert names.count("per_hour_summary") == 42
        assert "campaign_complete_summary" in names
        assert "simulation_complete" not in names
        assert max(
            index
            for index, name in enumerate(names)
            if name in {"simulation_tick", "per_hour_summary"}
        ) < refusal_event_index
        assert set(refusal) == {
            "status",
            "reason",
            "message",
            "c6_refusal_diagnostic",
            "backend_status",
            "backend_authoritative",
            "backend_message",
            "run_id",
        }
        assert refusal["run_id"] == _simulations[sid]["run_id"]
        assert refusal["reason"] == (
            "c6_joint_thermodynamic_liquid_fraction_window_empty"
        )
        assert refusal["message"] == refusal["reason"]
        assert refusal["c6_refusal_diagnostic"]["campaign"] == "C6"
        assert (
            refusal["c6_refusal_diagnostic"]["diagnostic"]["reason_refused"]
            == refusal["reason"]
        )
        artifact = RunArtifactStore(tmp_path / "runs").load(refusal["run_id"])
        assert artifact is not None
        assert artifact["execution_status"] == "refused"
        assert len(artifact["timesteps"]) == 42
        assert artifact["failure"] == {
            "reason": refusal["reason"],
            "error_message": refusal["reason"],
        }
        assert artifact["terminal"]["final_state"]
        assert artifact["terminal"]["final"]
    finally:
        client.disconnect()
        for sid in set(_simulations) - before:
            _clear_simulation_state(sid)


class RaisingCleanedMeltLedger:
    def kg_by_account(self, account):
        assert account == "process.cleaned_melt"
        raise RuntimeError("cleaned melt unavailable")


@pytest.mark.parametrize("ledger", [None, RaisingCleanedMeltLedger()])
def test_tick_omits_pot_composition_when_cleaned_melt_ledger_unavailable(
    ledger,
):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {
                    "SiO2": 51.5,
                    "FeO": 13.0,
                    "MgO": 34.0,
                },
            },
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("s_type")
    snapshot = sim._make_snapshot()
    assert snapshot.inventory.melt_oxide_kg["SiO2"] > 0
    sim.atom_ledger = ledger

    payload = _tick_payload(
        sim=sim,
        snapshot=snapshot,
        backend_message="",
        backend_status="internal-analytical",
        backend_authoritative=False,
    )

    assert payload["pot_composition"] == {}
    assert payload["pot_composition_wt_pct"] == {}


def test_web_pause_resume_is_result_neutral(monkeypatch, tmp_path):
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_internal_analytical_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")

    def start_web_session():
        before = set(_simulations)
        client = _identified_socket_client(app)
        assert client.is_connected()
        client.get_received()
        client.emit(
            "start_simulation",
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "speed": 0,
                "track": "pyrolysis",
            },
        )
        client.get_received()
        new_sids = set(_simulations) - before
        assert len(new_sids) == 1
        sid = new_sids.pop()
        state, _ = _current_simulation_state(sid)
        assert state is not None
        return client, sid, state

    clients = []
    try:
        paused_client, paused_sid, paused_state = start_web_session()
        clients.append(paused_client)
        paused_client.emit("pause_simulation")
        paused_client.emit("resume_simulation")
        paused_client.get_received()
        assert paused_state["paused"] is False

        unpaused_client, unpaused_sid, unpaused_state = start_web_session()
        clients.append(unpaused_client)

        paused_results = [
            result.per_hour_summary
            for result in drive_auto_apply(paused_state["session"], 3)
        ]
        unpaused_results = [
            result.per_hour_summary
            for result in drive_auto_apply(unpaused_state["session"], 3)
        ]

        assert paused_results == unpaused_results
        assert (
            paused_state["session"].simulator.product_ledger()
            == unpaused_state["session"].simulator.product_ledger()
        )
    finally:
        for client in clients:
            client.disconnect()
        for sid in list(_simulations):
            _clear_simulation_state(sid)
