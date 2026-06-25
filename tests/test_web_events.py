import copy
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as app_module
from web import events as web_events
from web import routes as web_routes
from simulator.backends import BackendSelectionPolicy, backend_resolution_status
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.recipe_io import load_recipe_patch, read_recipe_metadata
from simulator.session import drive_auto_apply
from simulator.state import DecisionPoint, DecisionType, EvaporationFlux
from web.events import (
    BackendUnavailableError,
    _clear_simulation_state,
    _completion_payload,
    _current_simulation_state,
    _emit_if_current,
    _get_backend,
    _MASS_BALANCE_ERROR_BREACH_PCT,
    _replace_simulation_state,
    _start_payload,
    _sim_locks,
    _simulations,
    _tick_payload,
)


_DELETE = object()
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _sim_with_mass_balance_snapshot(error_pct, category=None):
    backend = StubBackend()
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
                "energy_cumulative_kWh": 12.25,
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
            "energy_kWh": 12.25,
            "mass_balance_error_pct": 0.0,
            "terminal_rump_kg": 10.0,
            "products": {"glass": 3.0},
        },
    }
    _sim_locks[sid] = threading.Lock()


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
                "energy 12.25 kWh | wall deposit 0.01 kg"
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
        backend = StubBackend()
        backend.initialize({})
        return backend

    monkeypatch.setattr("web.events.resolve_backend", fake_resolve_backend)

    backend = _get_backend("stub")

    assert isinstance(backend, StubBackend)
    assert len(calls) == 1
    assert calls[0][0] == "stub"
    assert calls[0][1] is BackendSelectionPolicy.WEB_AUTODETECT
    assert calls[0][2]["unavailable_error_cls"] is BackendUnavailableError


def test_web_start_payload_exposes_backend_status():
    expected_backend = backend_resolution_status(StubBackend()).as_payload()
    payload = _start_payload(
        sim=object(),
        feedstock_key="lunar_mare_low_ti",
        mass_kg=1000.0,
        backend_requested="stub",
        backend_active="StubBackend",
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

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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
    assert "fused_silica" not in material_ids
    for material in materials:
        assert set(material) == {
            "id",
            "display_name",
            "max_service_T_C",
            "service_rating_T_C",
            "requested_ceiling_T_C",
            "effective_applied_ceiling_T_C",
        }

    zirconia = next(
        material
        for material in materials
        if material["id"] == "zirconia_ysz"
    )
    assert zirconia["service_rating_T_C"] == pytest.approx(2200)
    assert zirconia["requested_ceiling_T_C"] == pytest.approx(1800)
    assert zirconia["effective_applied_ceiling_T_C"] == pytest.approx(1800)


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
    assert "service ${service} C; applied ${applied} C" in controls
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

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    payload = {
        "backend": "stub",
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

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr(web_events, "_load_yaml", fake_load_yaml)
    monkeypatch.setattr(web_events, "_get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    payload = {
        "backend": "stub",
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


@pytest.mark.parametrize(
    ("material_id", "message"),
    [
        ("fused_silica", "not selectable"),
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
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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
        ({"c4_max_temp_C": "nan"}, "c4_max_temp_C"),
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
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)
    payload = {
        "backend": "stub",
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

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()
    before = set(_simulations)

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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
            ("simulation_tick", {"fresh": True}, sid)
        ]
    finally:
        _clear_simulation_state(sid)


def test_completion_payload_exposes_final_mass_reconciliation():
    backend = StubBackend()
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
    backend = StubBackend()
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
        backend_status="stub",
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
        backend_status="stub",
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
        backend_status="stub",
        backend_authoritative=False,
    )

    assert payload["mass_balance_error_pct"] == pytest.approx(4.99e-12)
    assert payload["mass_balance_error_category"] == "zero_input_basis_breach"
    assert payload["mass_balance_error_breached"] is True


def test_completion_payload_exposes_mass_balance_category_when_pct_none(
    monkeypatch,
):
    backend = StubBackend()
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

    def force_stub_backend(_backend_name):
        backend = StubBackend()
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

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr("web.events.drive_session", one_tick_drive)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        run_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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


def test_simulation_tick_exposes_mass_balance_category_when_pct_none(
    monkeypatch,
):
    captured_tasks = []
    drive_calls = {"count": 0}

    def force_stub_backend(_backend_name):
        backend = StubBackend()
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

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr("web.events.drive_session", one_tick_drive)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        run_background_task,
    )
    app = app_module.create_app()
    client = app_module.socketio.test_client(app)
    assert client.is_connected()
    client.get_received()

    try:
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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


class RaisingCleanedMeltLedger:
    def kg_by_account(self, account):
        assert account == "process.cleaned_melt"
        raise RuntimeError("cleaned melt unavailable")


@pytest.mark.parametrize("ledger", [None, RaisingCleanedMeltLedger()])
def test_tick_omits_pot_composition_when_cleaned_melt_ledger_unavailable(
    ledger,
):
    backend = StubBackend()
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
        backend_status="stub",
        backend_authoritative=False,
    )

    assert payload["pot_composition"] == {}
    assert payload["pot_composition_wt_pct"] == {}


def test_web_pause_resume_is_result_neutral(monkeypatch):
    captured_tasks = []

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        return {"captured_task": len(captured_tasks)}

    monkeypatch.setattr("web.events._get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    app = app_module.create_app()

    def start_web_session():
        before = set(_simulations)
        client = app_module.socketio.test_client(app)
        assert client.is_connected()
        client.get_received()
        client.emit(
            "start_simulation",
            {
                "backend": "stub",
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
