import pytest

import app as app_module
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.session import drive_auto_apply
from web.events import (
    BackendUnavailableError,
    _clear_simulation_state,
    _completion_payload,
    _current_simulation_state,
    _emit_if_current,
    _get_backend,
    _replace_simulation_state,
    _sim_locks,
    _simulations,
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
