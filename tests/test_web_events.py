from types import SimpleNamespace

import pytest

import app as app_module
from simulator.backends import BackendSelectionPolicy, backend_resolution_status
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.session import drive_auto_apply
from simulator.state import EvaporationFlux
from web.events import (
    BackendUnavailableError,
    _clear_simulation_state,
    _completion_payload,
    _current_simulation_state,
    _emit_if_current,
    _get_backend,
    _replace_simulation_state,
    _start_payload,
    _sim_locks,
    _simulations,
    _tick_payload,
)


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
        assert set(material) == {"id", "display_name", "max_service_T_C"}


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
