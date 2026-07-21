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
_SOCKET_HARNESS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "web_render"
    / "render_simulator_socket_dom.mjs"
)
_STATUS_STRIP_HARNESS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "web_render"
    / "render_simulator_status_strip_dom.mjs"
)
_SIMULATOR_CHARTS_JS = _REPO_ROOT / "web" / "static" / "js" / "simulator-charts.js"
_SIMULATOR_TICKS_JS = _REPO_ROOT / "web" / "static" / "js" / "simulator-ticks.js"
_SIMULATOR_ADVISORY_JS = (
    _REPO_ROOT / "web" / "static" / "js" / "simulator-advisory.js"
)
_SIMULATOR_SOCKET_JS = _REPO_ROOT / "web" / "static" / "js" / "simulator-socket.js"
_SIMULATOR_DECISIONS_JS = (
    _REPO_ROOT / "web" / "static" / "js" / "simulator-decisions.js"
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
    "ceramic-rump-state",
    "ceramic-rump-content",
    "industrial-glass-state",
    "industrial-glass-content",
    "vapor-pressure-authority-state",
    "vapor-pressure-authority-content",
    "thermal-train-headline-state",
    "thermal-train-headline",
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
    http_client = app.test_client()
    html_response = http_client.get("/")
    assert html_response.status_code == 200

    client = app_module.socketio.test_client(
        app,
        flask_test_client=http_client,
    )
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
    # The scope token is spaced out for reading, exactly as `atmosphere` is
    # rendered "Hard vacuum" rather than "hard_vacuum" above. Displaying the raw
    # identifier put a ledger variable name on screen and, as one unbreakable
    # 42-character word, widened the whole page past the viewport at 1440px.
    # Nothing is lost: the exact token stays on the element's title.
    assert rendered["text"]["energy-scope"] == (
        "Electrical plus known evaporation enthalpy"
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


def test_unavailable_product_story_never_renders_as_ok():
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)

    rendered = _render_advisory_dom(
        html=html,
        event="simulation_complete",
        payload={
            "product_story": None,
            "product_story_status": "unavailable",
            "products": {"Fe": 1.0},
        },
    )

    assert rendered["text"]["product-ledger-state"] == "unavailable"
    assert "ProductsFe: 1 kg" in rendered["text"]["product-ledger-content"]

    empty_fallback = _render_advisory_dom(
        html=html,
        event="simulation_complete",
        payload={
            "product_story": None,
            "product_story_status": "unavailable",
        },
    )
    assert empty_fallback["text"]["product-ledger-state"] == "unavailable"
    assert empty_fallback["text"]["product-ledger-content"] == "n/a"


def test_started_status_clears_completion_bound_advisory_panels():
    html = app_module.create_app().test_client().get("/").get_data(as_text=True)
    rendered = _render_advisory_sequence(
        html=html,
        events=[
            {
                "event": "simulation_complete",
                "payload": {
                    "run_id": "run-a",
                    "product_story_status": "ok",
                    "products": {"Fe": 1.0},
                    "ceramic_rump_panel": {
                        "status": "match",
                        "match": {"label": "ceramic A"},
                    },
                    "vapor_pressure_authority_panel": {
                        "status": "authoritative",
                        "message": "source A",
                    },
                    "knudsen_regime_diagnostic": {
                        "status": "warning",
                        "regime": "transitional",
                    },
                },
            },
            {
                "event": "simulation_status",
                "payload": {"status": "started", "run_id": "run-b"},
            },
        ],
    )

    for panel in [
        "product-ledger",
        "ceramic-rump",
        "industrial-glass",
        "vapor-pressure-authority",
        "knudsen-regime",
    ]:
        assert rendered["text"][f"{panel}-state"] == "n/a"
        assert rendered["text"][f"{panel}-content"] == "n/a"


def test_evaporation_flux_chart_admits_species_that_appear_later(
    producer_backed_operator_tick,
):
    first = {**producer_backed_operator_tick["payload"], "evap_species": {"Fe": 0.1}}
    second = {
        **producer_backed_operator_tick["payload"],
        "hour": 2,
        "evap_species": {"Fe": 0.2, "SiO": 0.05},
    }

    rendered = _render_tick_sequence(
        html=producer_backed_operator_tick["html"],
        payloads=[first, second],
    )
    additions = [
        call
        for call in rendered["plotlyCalls"]
        if call["method"] == "addTraces" and call["target"] == "chart-massflow"
    ]

    assert additions == [
        {
            "method": "addTraces",
            "target": "chart-massflow",
            "traceCount": 1,
            "traceNames": ["SiO"],
        }
    ]


def test_disconnect_reconnect_resets_controls_and_decision_modal():
    rendered = _render_socket_lifecycle()

    assert rendered["afterDisconnect"] == {
        "startDisabled": True,
        "pauseDisabled": True,
        "resumeDisabled": True,
        "decisionModalPresent": False,
    }
    assert rendered["afterReconnect"] == {
        "startDisabled": False,
        "pauseDisabled": True,
        "resumeDisabled": True,
        "decisionModalPresent": False,
    }


def _minimal_tick_payload(**overrides):
    """Bare tick with the numeric fields the tick DOM path always reads."""
    payload = {
        "hour": 70,
        "temperature_C": 1459.0,
        "campaign": "C3_NA",
        "melt_mass_kg": 949.0,
        "atmosphere": "HARD_VACUUM",
        "composition_wt_pct": {},
        "pot_composition": {},
        "evap_species": {},
        "condensation": {},
        "energy_electrical_plus_evaporation_cumulative_kWh": 1.0,
        "energy_electrical_plus_evaporation_kWh": 0.1,
        "energy_electrical_kWh": 0.1,
        "energy_evaporation_thermal_kWh": 0.0,
        "energy_scope": "electrical_plus_known_evaporation_enthalpy",
        "furnace_heat_status": "partial",
        "oxygen_kg": 0.0,
        "mass_balance_error_pct": 0.0,
        "mass_balance_error_breached": False,
        "backend_status": "ok",
        "backend_authoritative": False,
        "backend_message": "Internal analytical backend",
        "backend_fallback_active": False,
        "O2_stored_kg": 0.0,
        "O2_vented_cumulative_kg": 0.0,
        "O2_vented_kg_hr": 0.0,
        "turbine_shaft_power_kW": 0.0,
        "actual_ramp_rate": 50.0,
        "nominal_ramp_rate": 50.0,
        "transport_saturation_pct": 0.0,
        "turbine_utilization_pct": 0.0,
        "ramp_throttled": False,
    }
    payload.update(overrides)
    return payload


def _decision_gate_status_sequence():
    """Reproduce F1: started (full backend) → decision_applied (no backend) → tick."""
    return [
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "backend_active": "InternalAnalyticalBackend",
                "backend_status": "ok",
                "backend_authoritative": False,
                "backend_message": "Internal analytical backend",
            },
        },
        {
            "event": "simulation_tick",
            "payload": _minimal_tick_payload(hour=70, temperature_C=1459.0),
        },
        {
            "event": "simulation_status",
            "payload": {
                "status": "decision_applied",
                "choice": "A",
            },
        },
        {
            "event": "simulation_tick",
            "payload": _minimal_tick_payload(
                hour=71,
                temperature_C=1509.0,
                campaign="C4",
                melt_mass_kg=949.0,
                # Tick carries status/auth but not always backend_active —
                # the pre-fix path invented "unknown" for the missing half.
                backend_status="ok",
                backend_authoritative=False,
                backend_message="Internal analytical backend",
            ),
        },
    ]


def test_decision_applied_does_not_stick_or_clobber_backend_badge():
    """b-088: after a decision gate, live ticks must re-assert Running and
    the badge must keep the backend it already knows (never unknown/unknown).
    """
    rendered = _render_status_strip(sequence=_decision_gate_status_sequence())
    final = rendered["final"]

    after_decision = [
        step for step in rendered["steps"] if step["event"] == "simulation_status"
    ][-1]
    assert after_decision["statusText"] == "decision_applied"
    # decision_applied has no backend fields — badge must keep prior knowledge
    assert after_decision["backendText"] == (
        "Backend: InternalAnalyticalBackend / ok"
    )
    assert "unknown" not in after_decision["backendText"].lower()
    assert "backend-badge-internal-analytical" in after_decision["backendClass"]

    assert final["statusText"] == "Running"
    assert final["hourText"] == "Hour: 71"
    assert final["backendText"] == "Backend: InternalAnalyticalBackend / ok"
    assert "unknown / unknown" not in final["backendText"]
    assert "backend-badge-internal-analytical" in final["backendClass"]
    assert final["backendTitle"] == "Internal analytical backend"


def test_status_strip_mutations_reproduce_b088_failure_mode():
    """Falsifiable proof: named mutations restore the F1 failure symptoms.

    Mutations
    ---------
    * ``mutate_badge_clobber`` — pre-fix ``updateBackendBadge`` that defaults
      missing fields to ``'unknown'`` (the decision_applied clobber).
    * ``mutate_no_tick_recovery`` — drop ``noteLiveSimulationTick`` so sticky
      ``decision_applied`` is never cleared by live ticks.

    Apply → observe failure → (mutations are harness-local; source stays fixed).
    """
    sequence = _decision_gate_status_sequence()

    clobbered = _render_status_strip(
        sequence=sequence,
        mutate_badge_clobber=True,
        mutate_no_tick_recovery=True,
    )
    final = clobbered["final"]
    after_decision = [
        step
        for step in clobbered["steps"]
        if step["event"] == "simulation_status"
        and step["statusText"] == "decision_applied"
    ][-1]

    assert after_decision["backendText"] == "Backend: unknown / unknown", (
        "badge-clobber mutation must invent unknown/unknown on decision_applied"
    )
    assert final["statusText"] == "decision_applied", (
        "no-tick-recovery mutation must leave status stuck on decision_applied"
    )
    assert final["hourText"] == "Hour: 71", (
        "telemetry still advances under the stuck status (F1 signature)"
    )

    # Control: same sequence without mutations stays fixed.
    fixed = _render_status_strip(sequence=sequence)
    assert fixed["final"]["statusText"] == "Running"
    assert "unknown" not in fixed["final"]["backendText"].lower()


def test_mid_run_reconnect_does_not_claim_ready_over_live_telemetry():
    """Shared failure class with reconnect P0: status strip must not assert
    Ready while last-tick readouts are still on the strip.
    """
    sequence = [
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "backend_active": "InternalAnalyticalBackend",
                "backend_status": "ok",
                "backend_authoritative": False,
            },
        },
        {
            "event": "simulation_tick",
            "payload": _minimal_tick_payload(hour=42),
        },
        {"event": "disconnect", "payload": "transport close"},
        {"event": "connect", "payload": {}},
    ]
    rendered = _render_status_strip(sequence=sequence)
    final = rendered["final"]
    assert final["statusText"] == "Connection restored"
    assert final["hourText"] == "Hour: 42"
    assert final["statusText"] != "Ready"


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


def _render_tick_sequence(*, html, payloads):
    completed = subprocess.run(
        ["node", str(_DOM_HARNESS)],
        input=json.dumps(
            {
                "html": html,
                "payloads": payloads,
                "script_path": str(_SIMULATOR_TICKS_JS),
                "chart_script_path": str(_SIMULATOR_CHARTS_JS),
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


def _render_advisory_sequence(*, html, events):
    completed = subprocess.run(
        ["node", str(_ADVISORY_HARNESS)],
        input=json.dumps(
            {
                "html": html,
                "events": events,
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


def _render_socket_lifecycle():
    completed = subprocess.run(
        ["node", str(_SOCKET_HARNESS)],
        input=json.dumps(
            {
                "socket_script_path": str(_SIMULATOR_SOCKET_JS),
                "decisions_script_path": str(_SIMULATOR_DECISIONS_JS),
            }
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _render_status_strip(
    *,
    sequence,
    mutate_badge_clobber=False,
    mutate_no_tick_recovery=False,
):
    completed = subprocess.run(
        ["node", str(_STATUS_STRIP_HARNESS)],
        input=json.dumps(
            {
                "socket_script_path": str(_SIMULATOR_SOCKET_JS),
                "ticks_script_path": str(_SIMULATOR_TICKS_JS),
                "sequence": sequence,
                "mutate_badge_clobber": mutate_badge_clobber,
                "mutate_no_tick_recovery": mutate_no_tick_recovery,
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
