import json
import subprocess
from pathlib import Path

import pytest

import app as app_module
import web.events as web_events
from simulator.melt_backend.base import StubBackend


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOM_HARNESS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "web_render"
    / "render_simulator_tick_dom.mjs"
)
_SIMULATOR_TICKS_JS = _REPO_ROOT / "web" / "static" / "js" / "simulator-ticks.js"

_RENDER_IDS = [
    "status-hour",
    "status-temp",
    "status-campaign",
    "status-mass",
    "status-atmosphere",
    "energy-cumulative",
    "energy-hour",
    "energy-electrical",
    "energy-evaporation",
    "energy-scope",
    "furnace-heat-status",
    "oxygen-total",
    "mass-error",
    "gt-ramp-actual",
    "gt-ramp-nominal",
    "gt-pipe-sat",
    "gt-turbine-load",
    "gt-o2-stored",
    "gt-o2-vented",
    "gt-vent-rate",
    "debug-inventory-json",
]


class _StopAfterFirstTick(Exception):
    pass


@pytest.fixture()
def producer_backed_operator_tick(monkeypatch):
    """Capture a UI payload through the same socket producer used in runtime."""
    captured_tasks = []

    def force_stub_backend(_backend_name):
        backend = StubBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        return {"captured_task": len(captured_tasks)}

    def stop_after_first_positive_sleep(seconds=0):
        if seconds and seconds > 0:
            raise _StopAfterFirstTick()

    monkeypatch.setattr(web_events, "_safe_log", lambda _message: None)
    monkeypatch.setattr(web_events, "_get_backend", force_stub_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_background_task,
    )
    monkeypatch.setattr(app_module.socketio, "sleep", stop_after_first_positive_sleep)

    app = app_module.create_app()
    html_response = app.test_client().get("/")
    assert html_response.status_code == 200

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
                "speed": 1,
                "track": "pyrolysis",
            },
        )
        statuses = [
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "simulation_status"
        ]
        assert statuses
        assert statuses[-1]["status"] == "started"
        assert captured_tasks

        try:
            captured_tasks[-1]()
        except _StopAfterFirstTick:
            pass

        ticks = [
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "simulation_tick"
        ]
        assert len(ticks) == 1
        payload = ticks[0]
        _assert_producer_tick_baseline(payload)
        return {
            "html": html_response.get_data(as_text=True),
            "payload": payload,
        }
    finally:
        client.disconnect()
        for sid in list(web_events._simulations):
            web_events._clear_simulation_state(sid)


def test_simulation_tick_payload_renders_operator_dom_readouts(
    producer_backed_operator_tick,
):
    payload = producer_backed_operator_tick["payload"]
    rendered = _render_tick_dom(
        html=producer_backed_operator_tick["html"],
        payload=payload,
    )

    assert rendered["text"]["status-hour"] == f"Hour: {payload['hour']}"
    assert (
        rendered["text"]["status-temp"]
        == f"T: {payload['temperature_C']:.0f} \u00b0C"
    )
    assert rendered["text"]["status-campaign"] == payload["campaign"]
    assert rendered["text"]["status-mass"] == (
        f"Melt: {payload['melt_mass_kg']:.0f} kg"
    )
    assert rendered["text"]["status-atmosphere"] == "Atmosphere: Hard vacuum"
    assert rendered["text"]["energy-cumulative"] == (
        f"{payload['energy_electrical_plus_evaporation_cumulative_kWh']:.1f} kWh"
    )
    assert rendered["text"]["energy-hour"] == (
        f"{payload['energy_electrical_plus_evaporation_kWh']:.3f} kWh"
    )
    assert rendered["text"]["energy-electrical"] == (
        f"{payload['energy_electrical_kWh']:.3f} kWh"
    )
    assert rendered["text"]["energy-evaporation"] == (
        f"{payload['energy_evaporation_thermal_kWh']:.3f} kWh"
    )
    assert rendered["text"]["energy-scope"] == (
        "electrical_plus_known_evaporation_enthalpy"
    )
    assert rendered["text"]["furnace-heat-status"] == (
        "partial; feed sensible, fusion, radiation, full furnace heat omitted"
    )
    assert "energy_kWh" not in payload
    assert "energy_solar_thermal_kWh" not in payload
    assert rendered["text"]["oxygen-total"] == f"{payload['oxygen_kg']:.2f} kg"
    assert rendered["text"]["mass-error"] == (
        f"{_js_number_text(payload['mass_balance_error_pct'])}%"
    )
    assert rendered["dataset"]["mass-error"]["breached"] == (
        "true" if payload["mass_balance_error_breached"] else "false"
    )
    assert rendered["text"]["gt-ramp-actual"] == (
        f"{payload['actual_ramp_rate']:.1f}"
    )
    assert rendered["text"]["gt-ramp-nominal"] == (
        f"(nominal: {payload['nominal_ramp_rate']:.1f})"
    )
    assert rendered["text"]["gt-pipe-sat"] == (
        f"{payload['transport_saturation_pct']:.0f}"
    )
    assert rendered["text"]["gt-turbine-load"] == (
        f"{payload['turbine_utilization_pct']:.0f}"
    )
    assert rendered["text"]["gt-o2-stored"] == f"{payload['O2_stored_kg']:.1f}"
    assert rendered["text"]["gt-o2-vented"] == (
        f"{payload['O2_vented_cumulative_kg']:.1f}"
    )
    assert rendered["text"]["gt-vent-rate"] == (
        f"({payload['O2_vented_kg_hr']:.3f} kg/hr)"
    )

    debug_payload = _debug_inventory_payload(rendered["text"]["debug-inventory-json"])
    assert debug_payload["run"]["hour"] == payload["hour"]
    assert debug_payload["run"]["campaign"] == payload["campaign"]
    assert debug_payload["run"]["temperature_C"] == payload["temperature_C"]
    assert (
        debug_payload["process_inventory_kg"]["pot_composition"]
        == payload["pot_composition"]
    )
    assert debug_payload["backend"]["fallback_active"] == bool(
        payload["backend_fallback_active"]
    )
    assert _plotly_targets(rendered) >= {
        "chart-temperature",
        "chart-pressure",
        "chart-composition",
        "chart-pot-composition",
        "chart-absolute",
        "chart-o2-budget",
        "chart-melt-inventory",
    }


def _assert_producer_tick_baseline(payload):
    assert payload["hour"] == 1
    assert payload["campaign"] == "C0"
    assert payload["temperature_C"] == pytest.approx(75.0)
    assert payload["melt_mass_kg"] == pytest.approx(1000.0)
    assert payload["mass_balance_error_pct"] == pytest.approx(0.0)
    assert payload["mass_balance_error_breached"] is False
    assert payload["pot_composition_units"] == "kg"
    assert payload["pot_composition"]["SiO2"] > 0
    assert payload["atmosphere"] == "HARD_VACUUM"
    assert payload["actual_ramp_rate"] == pytest.approx(50.0)
    assert payload["nominal_ramp_rate"] == pytest.approx(50.0)
    assert "backend_fallback_active" in payload
    assert "backend_message" in payload


def _render_tick_dom(*, html, payload):
    completed = subprocess.run(
        [
            "node",
            str(_DOM_HARNESS),
        ],
        input=json.dumps(
            {
                "html": html,
                "payload": payload,
                "script_path": str(_SIMULATOR_TICKS_JS),
                "ids": _RENDER_IDS,
            }
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _debug_inventory_payload(text):
    prefix = "/* debug_inventory\n"
    suffix = "\n*/"
    assert text.startswith(prefix)
    assert text.endswith(suffix)
    return json.loads(text[len(prefix): -len(suffix)])


def _js_number_text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _plotly_targets(rendered):
    return {
        call["target"]
        for call in rendered["plotlyCalls"]
        if isinstance(call.get("target"), str)
    }
