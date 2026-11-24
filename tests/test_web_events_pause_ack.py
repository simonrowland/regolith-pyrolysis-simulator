"""Pause command acknowledgement stays responsive during a simulation step."""

import threading
from types import SimpleNamespace

import app as app_module
import pytest
from web import events as web_events
from web.events import _clear_simulation_state, _replace_simulation_state


@pytest.fixture
def active_run():
    app = app_module.create_app()
    http_client = app.test_client()
    assert http_client.get("/").status_code == 200
    client = app_module.socketio.test_client(app, flask_test_client=http_client)
    client.get_received()
    sid = app_module.socketio.server.manager.sid_from_eio_sid(client.eio_sid, "/")
    state, run_lock = _replace_simulation_state(
        sid,
        SimpleNamespace(pause=lambda: None),
        speed=0.0,
    )
    try:
        yield client, sid, state, run_lock
    finally:
        if client.is_connected():
            client.disconnect()
        _clear_simulation_state(sid)


def _assert_acknowledged_after_boundary(client, sid, state):
    assert web_events._emit_pending_pause_ack(
        app_module.socketio,
        sid,
        state["run_id"],
    ) is True
    statuses = [
        event["args"][0]
        for event in client.get_received()
        if event["name"] == "simulation_status"
    ]
    assert statuses == [{"status": "paused", "run_id": state["run_id"]}]


def test_pause_ack_does_not_wait_for_in_flight_step_lock(active_run):
    client, sid, state, run_lock = active_run
    completed = threading.Event()
    failures = []

    def pause_from_socket():
        try:
            client.emit("pause_simulation")
        except Exception as exc:  # noqa: BLE001 -- propagate worker failure
            failures.append(exc)
        finally:
            completed.set()

    run_lock.acquire()
    lock_released = False
    worker = threading.Thread(target=pause_from_socket)
    worker.start()
    try:
        assert completed.wait(timeout=1.0), (
            "pause acknowledgement waited for the in-flight simulation step"
        )
        assert not failures
        assert state["paused"] is True
        assert state["pause_ack_pending"] is True
        assert client.get_received() == []
        run_lock.release()
        lock_released = True
        _assert_acknowledged_after_boundary(client, sid, state)
    finally:
        if not lock_released:
            run_lock.release()
        worker.join(timeout=2.0)


def test_missing_pause_ack_mutation_fails_handshake_contract(active_run, monkeypatch):
    client, sid, state, _run_lock = active_run
    state["pause_ack_pending"] = True
    monkeypatch.setattr(
        web_events,
        "_emit_pending_pause_ack",
        lambda *_args: False,
    )

    with pytest.raises(AssertionError, match="is True"):
        _assert_acknowledged_after_boundary(client, sid, state)
