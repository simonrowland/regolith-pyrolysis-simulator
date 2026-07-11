import json
import subprocess
from pathlib import Path

import pytest

import app as app_module
import web.events as web_events
from simulator.melt_backend.base import InternalAnalyticalBackend


_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOM_HARNESS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "web_render"
    / "render_simulator_tick_dom.mjs"
)
_ADVISORY_HARNESS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "web_render"
    / "render_simulator_advisory_dom.mjs"
)
_SIMULATOR_TICKS_JS = _REPO_ROOT / "web" / "static" / "js" / "simulator-ticks.js"
_SIMULATOR_ADVISORY_JS = (
    _REPO_ROOT / "web" / "static" / "js" / "simulator-advisory.js"
)

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

_ADVISORY_IDS = [
    "product-ledger-state",
    "product-ledger-content",
    "overlap-evaporation-state",
    "overlap-evaporation-content",
    "knudsen-regime-state",
    "knudsen-regime-content",
]


class _StopAfterFirstTick(Exception):
    pass


@pytest.fixture()
def producer_backed_operator_tick(monkeypatch):
    """Capture a UI payload through the same socket producer used in runtime."""
    captured_tasks = []

    def force_internal_analytical_backend(_backend_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_background_task(target, *args, **kwargs):
        captured_tasks.append(target)
        return {"captured_task": len(captured_tasks)}

    def stop_after_first_positive_sleep(seconds=0):
        if seconds and seconds > 0:
            raise _StopAfterFirstTick()

    monkeypatch.setattr(web_events, "_safe_log", lambda _message: None)
    monkeypatch.setattr(web_events, "_get_backend", force_internal_analytical_backend)
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


def test_completion_payload_renders_product_ledger_and_knudsen_diagnostic():
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)
    payload = {
        "products": {"Fe": 12.345, "glass": 4.0},
        "oxygen_kg": 2.5,
        "oxygen_stored_kg": 2.0,
        "oxygen_vented_kg": 0.5,
        "mass_in_kg": 1000.0,
        "mass_out_kg": 999.999,
        "terminal_rump_kg": 80.0,
        "terminal_rump_by_class": {"refractory_ceramic_rump": 80.0},
        "terminal_rump_by_species": {"CaO": 10.0},
        "terminal_residual_buckets": {
            "process.cleaned_melt": {
                "kg_by_species": {"SiO2": 1.2},
                "total_kg": 1.2,
            },
        },
        "process_inventory_spent_reductant": {
            "class_total_kg": 0.75,
            "account": "process.spent_reductant_residue",
            "disposition": "process_inventory_spent_reductant",
            "kg_by_species": {"Na2O": 0.75},
        },
        "knudsen_regime_diagnostic": {
            "status": "warning",
            "reason": "transitional_knudsen_transport",
            "regime": "transitional",
            "knudsen_number": 0.000345,
            "mean_free_path_m": 0.000041,
            "overhead_pressure_mbar": 10.0,
            "gas_temperature_C": 1500.0,
            "carrier_gas": "N2",
            "segments": [
                {
                    "name": "stage_1_to_stage_2",
                    "knudsen_number": 0.000345,
                    "regime": "viscous",
                    "characteristic_length_m": 0.12,
                    "regime_factor": 1.0,
                }
            ],
            "warnings": ["surface deposition uncertainty"],
        },
    }

    rendered = _render_advisory_dom(
        html=html,
        event="simulation_complete",
        payload=payload,
    )

    product = rendered["text"]["product-ledger-content"]
    assert rendered["text"]["product-ledger-state"] == "ok"
    assert "ProductsFe: 12.345 kg" in product
    assert "glass: 4 kg" in product
    assert "Terminal rump by classrefractory ceramic rump: 80 kg" in product
    assert "SiO2 1.2 kg" in product
    assert "process.spent_reductant_residue" in product
    assert "Na2O 0.75 kg" in product

    knudsen = rendered["text"]["knudsen-regime-content"]
    assert rendered["text"]["knudsen-regime-state"] == "warning"
    assert "Completion diagnostic" in knudsen
    assert "Regime: transitional" in knudsen
    assert "Kn: 3.45e-4" in knudsen
    assert "stage_1_to_stage_2: Kn 3.45e-4; regime viscous" in knudsen


def test_simulation_tick_renders_overlap_evaporation_diagnostic():
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)
    payload = {
        "overlap_evaporation": {
            "campaign": "C2A",
            "campaign_hour": 3,
            "temperature_C": 1550.0,
            "completion_target_species": ["Fe"],
            "endpoint_species_monitored": ["Fe"],
            "off_target_total_kg_hr": 0.012,
            "off_target_evaporation": {
                "SiO": {
                    "rate_kg_hr": 0.012,
                    "designated_stage_number": 3,
                    "future_campaign_stage_targets": ["C4"],
                    "listed_in_endpoint_watch": False,
                    "gates_completion": False,
                },
            },
        },
    }

    rendered = _render_advisory_dom(
        html=html,
        event="simulation_tick",
        payload=payload,
    )

    content = rendered["text"]["overlap-evaporation-content"]
    assert rendered["text"]["overlap-evaporation-state"] == "warning"
    assert "Campaign: C2A" in content
    assert "Off-target total: 0.012 kg/hr" in content
    assert "SiO: rate 0.012 kg/hr; stage 3" in content
    assert "endpoint watch false" in content
    assert "gates completion false" in content


def test_refusal_status_renders_structured_knudsen_diagnostic():
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)
    payload = {
        "status": "refused",
        "message": "knudsen regime refused",
        "knudsen_regime_diagnostic": {
            "status": "refused",
            "reason": "free_molecular_transport_refused",
            "regime": "free_molecular",
            "knudsen_number": None,
            "mean_free_path_m": None,
            "overhead_pressure_mbar": 0.0,
            "gas_temperature_C": 1500.0,
            "carrier_gas": "N2",
            "segments": [
                {
                    "name": "default_pipe",
                    "knudsen_number": 12.0,
                    "regime": "free_molecular",
                    "characteristic_length_m": 0.12,
                    "regime_factor": 0.0,
                }
            ],
        },
    }

    rendered = _render_advisory_dom(
        html=html,
        event="simulation_status",
        payload=payload,
    )

    content = rendered["text"]["knudsen-regime-content"]
    assert rendered["text"]["knudsen-regime-state"] == "refused"
    assert "Refusal diagnostic" in content
    assert "Regime: free_molecular" in content
    assert "Reason: free_molecular_transport_refused" in content
    assert "default_pipe: Kn 12; regime free_molecular" in content


def test_per_hour_summary_renders_kn_and_regime():
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)
    payload = {
        "hour": 7,
        "campaign": "C2A",
        "Kn": 0.000345,
        "regime": "viscous",
        "transport_formula_id": "mean_free_path_v1",
    }

    rendered = _render_advisory_dom(
        html=html,
        event="per_hour_summary",
        payload=payload,
    )

    content = rendered["text"]["knudsen-regime-content"]
    assert rendered["text"]["knudsen-regime-state"] == "viscous"
    assert "Per-hour transport" in content
    assert "Hour: 7" in content
    assert "Kn: 3.45e-4" in content
    assert "Regime: viscous" in content
    assert "Formula: mean_free_path_v1" in content


def test_new_advisory_panels_render_empty_payloads_as_na():
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)

    complete = _render_advisory_dom(html=html, event="simulation_complete", payload={})
    assert complete["text"]["product-ledger-state"] == "n/a"
    assert complete["text"]["product-ledger-content"] == "n/a"
    assert complete["text"]["knudsen-regime-state"] == "n/a"
    assert complete["text"]["knudsen-regime-content"] == "n/a"

    tick = _render_advisory_dom(html=html, event="simulation_tick", payload={})
    assert tick["text"]["overlap-evaporation-state"] == "n/a"
    assert tick["text"]["overlap-evaporation-content"] == "n/a"


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


def _render_advisory_dom(*, html, event, payload):
    completed = subprocess.run(
        [
            "node",
            str(_ADVISORY_HARNESS),
        ],
        input=json.dumps(
            {
                "html": html,
                "event": event,
                "payload": payload,
                "script_path": str(_SIMULATOR_ADVISORY_JS),
                "ids": _ADVISORY_IDS,
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
