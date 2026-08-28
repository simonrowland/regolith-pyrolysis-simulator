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
_STATUS_STRIP_HARNESS = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "web_render"
    / "render_simulator_status_strip_dom.mjs"
)
_SIMULATOR_TICKS_JS = _REPO_ROOT / "web" / "static" / "js" / "simulator-ticks.js"
_SIMULATOR_CHARTS_JS = (
    _REPO_ROOT / "web" / "static" / "js" / "simulator-charts.js"
)
_SIMULATOR_ADVISORY_JS = (
    _REPO_ROOT / "web" / "static" / "js" / "simulator-advisory.js"
)
_SIMULATOR_SOCKET_JS = _REPO_ROOT / "web" / "static" / "js" / "simulator-socket.js"
_SIMULATOR_DECISIONS_JS = (
    _REPO_ROOT / "web" / "static" / "js" / "simulator-decisions.js"
)
_SIMULATOR_CONTROLS_JS = (
    _REPO_ROOT / "web" / "static" / "js" / "simulator-controls.js"
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
                "backend": "internal-analytical",
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


@pytest.fixture()
def producer_backed_submillimbar_pressure_tick(monkeypatch, request):
    original_tick_payload = web_events._tick_payload

    def tick_payload_with_submillimbar_pressure(**kwargs):
        kwargs["snapshot"].overhead.pressure_mbar = 0.0004
        return original_tick_payload(**kwargs)

    monkeypatch.setattr(
        web_events,
        "_tick_payload",
        tick_payload_with_submillimbar_pressure,
    )
    return request.getfixturevalue("producer_backed_operator_tick")


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


def _minimal_tick_payload(**overrides):
    """Bare tick with the numeric fields the tick DOM path always reads."""
    payload = {
        "run_id": "run-1",
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
    """Reproduce F1/F2 through real controls, socket, decisions, and tick handlers."""
    return [
        {"event": "start_click", "payload": {}},
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "run_id": "run-1",
                "lifecycle_generation": 1,
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
                "run_id": "run-1",
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
    """b-088: live ticks restore Running without erasing backend authority."""
    rendered = _render_status_strip(sequence=_decision_gate_status_sequence())
    final = rendered["final"]

    after_decision = [
        step for step in rendered["steps"] if step["event"] == "simulation_status"
    ][-1]
    assert after_decision["statusText"] == "decision_applied"
    # decision_applied has no backend fields — badge must keep prior knowledge.
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
    """Named harness mutations restore both pre-fix failure symptoms."""
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

    assert after_decision["backendText"] == "Backend: unknown / unknown"
    assert final["statusText"] == "decision_applied"
    assert final["hourText"] == "Hour: 71"
    assert final["backendText"] == "Backend: unknown / unknown"

    # Control: the identical sequence without mutations stays fixed.
    fixed = _render_status_strip(sequence=sequence)
    assert fixed["final"]["statusText"] == "Running"
    assert "unknown" not in fixed["final"]["backendText"].lower()


def test_mid_run_reconnect_does_not_claim_ready_over_live_telemetry():
    """F3 property: a live run reconnects to restored, never idle Ready."""
    sequence = [
        {"event": "start_click", "payload": {}},
        {
            "event": "simulation_tick",
            "payload": _minimal_tick_payload(
                hour=42,
                backend_active="InternalAnalyticalBackend",
                backend_status="ok",
                backend_authoritative=False,
            ),
        },
        {"event": "disconnect", "payload": "transport close"},
        {"event": "connect", "payload": {}},
    ]
    rendered = _render_status_strip(sequence=sequence)
    final = rendered["final"]
    assert final["statusText"] == "Connection restored"
    assert final["hourText"] == "Hour: 42"
    assert final["statusText"] != "Ready"
    assert final["lifecycle"]["phase"] == "running"
    assert final["lifecycle"]["activeRunId"] == "run-1"
    assert len(rendered["startPayloads"]) == 1


def test_fresh_run_reconnect_before_first_tick_does_not_claim_ready():
    """F3 property: starting is an explicit reconnectable phase."""
    sequence = [
        {"event": "start_click", "payload": {}},
        {"event": "disconnect", "payload": "transport close"},
        {"event": "connect", "payload": {}},
    ]
    rendered = _render_status_strip(sequence=sequence)
    assert rendered["final"]["statusText"] == "Connection restored"
    assert rendered["final"]["statusText"] != "Ready"
    assert rendered["final"]["lifecycle"]["phase"] == "starting"
    assert rendered["final"]["lifecycle"]["generation"] == 1
    assert len(rendered["startPayloads"]) == 1
    assert rendered["startPayloads"][0]["lifecycle_generation"] == 1


def test_complete_status_survives_failed_reconnect_then_success():
    """F4 property: complete owns the strip across failed and live reconnects."""
    sequence = [
        {"event": "start_click", "payload": {}},
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "run_id": "run-1",
                "lifecycle_generation": 1,
            },
        },
        {
            "event": "simulation_tick",
            "payload": _minimal_tick_payload(hour=6),
        },
        {"event": "simulation_complete", "payload": {"run_id": "run-1"}},
        {"event": "connect_error", "payload": {"message": "still offline"}},
        {"event": "connect", "payload": {}},
    ]
    rendered = _render_status_strip(sequence=sequence)
    after_error = [
        step for step in rendered["steps"] if step["event"] == "connect_error"
    ][-1]
    assert after_error["statusText"] == "Complete"
    assert rendered["final"]["statusText"] == "Complete"
    assert rendered["final"]["lifecycle"]["phase"] == "terminal-complete"


@pytest.mark.parametrize(
    ("terminal_payload", "expected"),
    [
        (
            {"status": "refused", "message": "typed terminal refusal"},
            "refused — typed terminal refusal",
        ),
        (
            {
                "status": "error",
                "reason": "terminal_run_failed",
                "message": "terminal failure",
            },
            "error — terminal failure",
        ),
    ],
)
def test_terminal_status_taxonomy_is_sticky_across_reconnect(
    terminal_payload,
    expected,
):
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "lifecycle_generation": 1,
                    "backend_active": "InternalAnalyticalBackend",
                    "backend_status": "ok",
                },
            },
            {
                "event": "simulation_status",
                "payload": {**terminal_payload, "run_id": "run-1"},
            },
            {"event": "disconnect", "payload": "transport close"},
            {"event": "connect", "payload": {}},
        ]
    )
    assert rendered["final"]["statusText"] == expected
    assert rendered["final"]["lifecycle"]["phase"].startswith("terminal-")


def test_nonterminal_error_does_not_end_active_run():
    """Recoverable command errors must not become sticky terminal labels."""
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "lifecycle_generation": 1,
                },
            },
            {
                "event": "simulation_status",
                "payload": {
                    "status": "error",
                    "message": "unsupported parameter adjustment",
                    "run_id": "run-1",
                },
            },
            {"event": "disconnect", "payload": "transport close"},
            {"event": "connect", "payload": {}},
        ]
    )
    assert rendered["final"]["statusText"] == "Connection restored"
    assert rendered["final"]["lifecycle"]["phase"] == "running"


def test_rejected_duplicate_start_does_not_freeze_advancing_prior_run():
    """A rejected replacement returns to the identified prior run."""
    sequence = [
        {"event": "start_click", "payload": {}},
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "run_id": "run-1",
                "lifecycle_generation": 1,
            },
        },
        {
            "event": "simulation_tick",
            "payload": _minimal_tick_payload(hour=9),
        },
        {"event": "start_click", "payload": {}},
        {
            "event": "simulation_status",
            "payload": {
                "status": "error",
                "message": "invalid replacement request",
                "error_type": "invalid_backend",
                "lifecycle_generation": 2,
            },
        },
        {
            "event": "simulation_tick",
            "payload": _minimal_tick_payload(hour=10),
        },
    ]
    rendered = _render_status_strip(sequence=sequence)
    assert rendered["steps"][-2]["statusText"] == (
        "error — invalid replacement request"
    )
    assert rendered["final"]["statusText"] == "Running"
    assert rendered["final"]["hourText"] == "Hour: 10"
    assert rendered["final"]["lifecycle"]["phase"] == "running"
    assert rendered["final"]["lifecycle"]["activeRunId"] == "run-1"
    assert len(rendered["startPayloads"]) == 2
    assert [
        payload["lifecycle_generation"]
        for payload in rendered["startPayloads"]
    ] == [1, 2]


def test_duplicate_terminal_events_keep_first_terminal_label():
    """Later terminal-looking events cannot desynchronise cached and shown state."""
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_tick",
                "payload": _minimal_tick_payload(hour=6),
            },
            {"event": "simulation_complete", "payload": {"run_id": "run-1"}},
            {"event": "simulation_complete", "payload": {"run_id": "run-1"}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "error",
                    "reason": "late_terminal_error",
                    "message": "late terminal error",
                    "run_id": "run-1",
                },
            },
            {"event": "connect_error", "payload": {"message": "offline"}},
            {"event": "connect", "payload": {}},
        ]
    )
    assert all(
        step["statusText"] == "Complete"
        for step in rendered["steps"][2:]
    )


def test_status_strip_harness_loads_production_lifecycle_module_order():
    """F5 property: the proof executes decisions before controls, as production does."""
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "lifecycle_generation": 1,
                },
            },
            {"event": "simulation_complete", "payload": {"run_id": "run-1"}},
        ]
    )
    assert rendered["loadedModules"] == [
        "simulator-socket.js",
        "simulator-charts.js",
        "simulator-ticks.js",
        "simulator-advisory.js",
        "simulator-decisions.js",
        "simulator-controls.js",
    ]
    assert rendered["final"]["statusText"] == "Complete"


def test_duplicate_pending_starts_accept_only_latest_generation():
    sequence = [
        {"event": "start_click", "payload": {}},
        {"event": "start_click", "payload": {}},
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "run_id": "run-stale",
                "lifecycle_generation": 1,
                "backend_active": "StaleBackend",
                "backend_status": "stale",
            },
        },
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "run_id": "run-current",
                "lifecycle_generation": 2,
                "backend_active": "CurrentBackend",
                "backend_status": "ok",
            },
        },
    ]
    rendered = _render_status_strip(sequence=sequence)
    assert rendered["steps"][-2]["lifecycle"]["phase"] == "starting"
    assert rendered["final"]["lifecycle"]["phase"] == "running"
    assert rendered["final"]["lifecycle"]["activeRunId"] == "run-current"
    assert rendered["final"]["backendText"] == "Backend: CurrentBackend / ok"
    assert [
        payload["lifecycle_generation"]
        for payload in rendered["startPayloads"]
    ] == [1, 2]


def test_stale_start_error_after_current_generation_is_ignored():
    sequence = [
        {"event": "start_click", "payload": {}},
        {"event": "start_click", "payload": {}},
        {
            "event": "simulation_status",
            "payload": {
                "status": "started",
                "run_id": "run-current",
                "lifecycle_generation": 2,
                "backend_active": "CurrentBackend",
                "backend_status": "ok",
            },
        },
        {
            "event": "simulation_status",
            "payload": {
                "status": "error",
                "message": "stale generation rejection",
                "error_type": "invalid_backend",
                "lifecycle_generation": 1,
            },
        },
    ]
    rendered = _render_status_strip(sequence=sequence)
    accepted = rendered["steps"][-2]
    stale = rendered["steps"][-1]
    assert stale["statusText"] == accepted["statusText"]
    assert stale["backendText"] == "Backend: CurrentBackend / ok"
    assert stale["lifecycle"]["phase"] == "running"
    assert stale["lifecycle"]["activeRunId"] == "run-current"
    assert stale["startDisabled"] == accepted["startDisabled"]
    assert stale["pauseDisabled"] == accepted["pauseDisabled"]
    assert stale["resumeDisabled"] == accepted["resumeDisabled"]


def test_replacement_launch_failure_after_cancellation_is_terminal():
    error_text = (
        "error — New run launch failed after prior run run-1 was cancelled"
    )
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "lifecycle_generation": 1,
                    "backend_active": "InternalAnalyticalBackend",
                    "backend_status": "ok",
                },
            },
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "error",
                    "message": (
                        "New run launch failed after prior run run-1 was cancelled"
                    ),
                    "error_type": "run_launch_failed_after_replacement",
                    "lifecycle_generation": 2,
                    "prior_run_id": "run-1",
                    "prior_run_cancelled": True,
                },
            },
            {
                "event": "simulation_tick",
                "payload": _minimal_tick_payload(run_id="run-1", hour=10),
            },
            {"event": "disconnect", "payload": "transport close"},
            {"event": "connect", "payload": {}},
        ]
    )
    assert rendered["steps"][3]["statusText"] == error_text
    assert rendered["final"]["statusText"] == error_text
    assert rendered["final"]["lifecycle"]["phase"] == "terminal-error"
    assert rendered["final"]["lifecycle"]["retiredRunId"] == "run-1"
    assert rendered["final"]["backendText"] == "Backend: —"


def test_prior_run_terminal_after_replacement_start_cannot_capture_new_run():
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "lifecycle_generation": 1,
                },
            },
            {"event": "start_click", "payload": {}},
            {"event": "simulation_complete", "payload": {"run_id": "run-1"}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-2",
                    "lifecycle_generation": 2,
                },
            },
            {
                "event": "simulation_tick",
                "payload": _minimal_tick_payload(run_id="run-2", hour=10),
            },
        ]
    )
    assert rendered["steps"][3]["statusText"] == "Running"
    assert rendered["final"]["statusText"] == "Running"
    assert rendered["final"]["hourText"] == "Hour: 10"
    assert rendered["final"]["lifecycle"]["activeRunId"] == "run-2"


def test_terminal_then_new_run_clears_prior_terminal_authority():
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "lifecycle_generation": 1,
                },
            },
            {"event": "simulation_complete", "payload": {"run_id": "run-1"}},
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-2",
                    "lifecycle_generation": 2,
                },
            },
            {
                "event": "simulation_tick",
                "payload": _minimal_tick_payload(run_id="run-2", hour=1),
            },
        ]
    )
    assert rendered["steps"][2]["statusText"] == "Complete"
    assert rendered["steps"][3]["statusText"] == "Running"
    assert rendered["final"]["lifecycle"]["phase"] == "running"
    assert rendered["final"]["lifecycle"]["activeRunId"] == "run-2"


def test_tick_for_new_run_clears_terminal_with_stale_run_identity():
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "lifecycle_generation": 1,
                },
            },
            {"event": "simulation_complete", "payload": {"run_id": "run-1"}},
            {
                "event": "simulation_tick",
                "payload": _minimal_tick_payload(run_id="run-2", hour=10),
            },
        ]
    )
    assert rendered["steps"][-2]["statusText"] == "Complete"
    assert rendered["final"]["statusText"] == "Running"
    assert rendered["final"]["hourText"] == "Hour: 10"
    assert rendered["final"]["lifecycle"]["activeRunId"] == "run-2"


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
    # 2026-08-05 MC-1 trace wiring d1b4f5d: Stage-0 routes 1.2285257819218 kg
    # of lunar trace passengers before this rounded operator payload is emitted.
    assert payload["melt_mass_kg"] == pytest.approx(998.8)
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
                "charts_script_path": str(_SIMULATOR_CHARTS_JS),
                "ticks_script_path": str(_SIMULATOR_TICKS_JS),
                "advisory_script_path": str(_SIMULATOR_ADVISORY_JS),
                "decisions_script_path": str(_SIMULATOR_DECISIONS_JS),
                "controls_script_path": str(_SIMULATOR_CONTROLS_JS),
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


def test_submillimbar_pressure_survives_socket_emitter_and_dom(
    producer_backed_submillimbar_pressure_tick,
):
    payload = producer_backed_submillimbar_pressure_tick["payload"]
    rendered = _render_tick_dom(
        html=producer_backed_submillimbar_pressure_tick["html"],
        payload=payload,
    )
    pressure_calls = [
        call
        for call in rendered["plotlyCalls"]
        if call["method"] == "extendTraces" and call["target"] == "chart-pressure"
    ]

    assert type(payload["pressure_mbar"]) is float
    assert payload["pressure_mbar"] == 0.0004
    assert pressure_calls
    # This chunk (b-090) owns only the producer/socket boundary: the client
    # must RECEIVE the un-destroyed sub-0.0005 value (payload asserts above)
    # and consume it without error (pressure_calls non-empty). The chart-level
    # contract (un-floored y, hover text) belongs to the b-086 zero/floor
    # renderer fix — assert it there when fix/zerofloor integrates, not here.


@pytest.mark.parametrize(
    ("terminal_payload", "label"),
    [
        (
            {
                "status": "refused",
                "run_id": "run-1",
                "reason": "viscous_p_bulk_transport_out_of_domain",
                "message": "transport model out of domain",
            },
            "lawful refusal",
        ),
        (
            {
                "status": "error",
                "run_id": "run-1",
                "reason": "terminal_run_failed",
                "message": "boom",
            },
            "error",
        ),
    ],
)
def test_any_terminal_outcome_hands_the_controls_back(terminal_payload, label):
    """After ANY terminal outcome the operator must be able to start again.

    The re-enable was gated on `data.status === 'error'`, so a run that CRASHED
    returned the controls while a run that lawfully REFUSED left #btn-start
    disabled forever -- reload-the-page or nothing. That is inverted: a refusal
    is the model declining to extrapolate (a result), an error is a fault, and
    only the fault recovered.

    It is also how a correct fail-close came to be reported as a stall: the e2e
    harness found terminal-refused with Start greyed out and the ledger showing
    n/a, which is indistinguishable from a dead app.

    The `started` step asserts the button is STILL disabled mid-run, so this
    cannot pass vacuously on a button that was never disabled -- which is
    exactly how my first hand-check of this fix fooled me.
    """
    rendered = _render_status_strip(
        sequence=[
            {"event": "start_click", "payload": {}},
            {
                "event": "simulation_status",
                "payload": {
                    "status": "started",
                    "run_id": "run-1",
                    "backend_status": "ok",
                },
            },
            {"event": "simulation_status", "payload": terminal_payload},
        ],
    )
    steps = rendered["steps"]
    started_step = [
        s for s in steps
        if s["event"] == "simulation_status"
    ][0]
    assert started_step["startDisabled"] is True, (
        "vacuity guard: Start must be disabled while the run is live, "
        f"otherwise this test proves nothing; got {started_step!r}"
    )
    assert rendered["final"]["startDisabled"] is False, (
        f"after a terminal {label} the operator must be able to start again; "
        f"Start is still disabled: {rendered['final']!r}"
    )
    assert rendered["final"]["pauseDisabled"] is True
    assert rendered["final"]["resumeDisabled"] is True
