"""Pause command acknowledgement stays responsive during a simulation step."""

import threading
from types import SimpleNamespace

import app as app_module
import pytest
from web import events as web_events
from web.events import _clear_simulation_state, _replace_simulation_state


@pytest.fixture
def active_run(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "pause-ack-test-secret")
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


@pytest.fixture
def live_worker(monkeypatch):
    monkeypatch.setenv("FLASK_SECRET_KEY", "pause-ack-test-secret")
    app = app_module.create_app()
    http_client = app.test_client()
    assert http_client.get("/").status_code == 200
    socketio = app_module.socketio
    client = socketio.test_client(app, flask_test_client=http_client)
    client.get_received()
    sid = socketio.server.manager.sid_from_eio_sid(client.eio_sid, "/")
    session = SimpleNamespace(
        simulator=object(),
        is_complete=lambda: False,
        pause=lambda: None,
        resume=lambda: None,
        pending_decision=lambda: None,
    )
    state, run_lock = _replace_simulation_state(sid, session, speed=1.0)
    tasks = []
    steps = []
    monkeypatch.setattr(socketio, "start_background_task", tasks.append)
    monkeypatch.setattr(socketio, "sleep", lambda _seconds: state.update(running=False))
    monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        web_events, "_record_last_recipe_capture", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        web_events,
        "_per_tick_backend_resolution",
        lambda *_args, **_kwargs: ("ok", "", True),
    )
    step = SimpleNamespace(
        per_hour_summary={},
        snapshot=None,
        backend_error=None,
        campaign_summary=None,
        decision_event=None,
    )

    def drive(*_args):
        steps.append(None)
        return iter((step,))

    monkeypatch.setattr(web_events, "drive_session", drive)
    web_events._start_background_loop(
        socketio, sid, state["run_id"], run_lock, "backend", "ok", True
    )
    try:
        yield client, sid, state, socketio, tasks[0], steps
    finally:
        if client.is_connected():
            client.disconnect()
        _clear_simulation_state(sid)


def _status_events(client):
    return [
        event["args"][0]["status"]
        for event in client.get_received()
        if event["name"] == "simulation_status"
    ]


def _request_pause_within_bound(client):
    completed = threading.Event()
    failures = []

    def pause_from_socket():
        try:
            client.emit("pause_simulation")
        except Exception as exc:  # noqa: BLE001 -- propagate worker failure
            failures.append(exc)
        finally:
            completed.set()

    worker = threading.Thread(target=pause_from_socket)
    worker.start()
    assert completed.wait(timeout=1.0), "pause command did not return within one second"
    worker.join(timeout=1.0)
    assert not failures


def _restore_deferred_pause_handler(monkeypatch, socketio, sid, state):
    def old_pause_handler(*_args):
        with web_events._simulations_guard:
            current = web_events._simulations.get(sid)
            if current is not state or not current.get("running"):
                return
            current["session"].pause()
            current["paused"] = True
            generation = int(current.get("pause_ack_generation", 0)) + 1
            current["pause_ack_generation"] = generation
            current["pause_ack_pending"] = True
            current["pause_ack_pending_generation"] = generation

    monkeypatch.setitem(
        socketio.server.handlers["/"], "pause_simulation", old_pause_handler
    )


def test_pause_during_inter_step_sleep_acks_before_another_step(
    live_worker, monkeypatch
):
    client, _sid, state, socketio, task, steps = live_worker
    observed_during_sleep = []

    def pause_during_sleep(_seconds):
        if not state["paused"]:
            _request_pause_within_bound(client)
            observed_during_sleep.extend(_status_events(client))
        else:
            state["running"] = False

    monkeypatch.setattr(socketio, "sleep", pause_during_sleep)
    task()

    assert observed_during_sleep == ["paused"]
    assert len(steps) == 1


def test_old_deferred_sleep_ack_mutation_fails(live_worker, monkeypatch):
    client, _sid, state, socketio, task, steps = live_worker
    observed_during_sleep = []
    _restore_deferred_pause_handler(monkeypatch, socketio, _sid, state)

    def pause_during_sleep(_seconds):
        if not state["paused"]:
            _request_pause_within_bound(client)
            observed_during_sleep.extend(_status_events(client))
        else:
            state["running"] = False

    monkeypatch.setattr(socketio, "sleep", pause_during_sleep)
    with pytest.raises(AssertionError, match=r"\['paused'\]"):
        task()
        assert observed_during_sleep == ["paused"]
    assert len(steps) == 1


def _pause_then_resume_before_worker_check(client, task, monkeypatch):
    original_emit = web_events._emit_if_current

    def emit_then_commands(socketio, sid, run_id, event, payload):
        emitted = original_emit(socketio, sid, run_id, event, payload)
        if event == "per_hour_summary":
            client.emit("pause_simulation")
            client.emit("resume_simulation", {"payload_version": 4})
        return emitted

    monkeypatch.setattr(web_events, "_emit_if_current", emit_then_commands)
    task()


def test_resume_clears_pending_ack_before_worker_check(live_worker, monkeypatch):
    client, _sid, state, _socketio, task, _steps = live_worker
    _pause_then_resume_before_worker_check(client, task, monkeypatch)

    statuses = _status_events(client)
    assert statuses[-1] == "resumed"
    assert "paused" not in statuses[statuses.index("resumed") + 1 :]
    assert state["pause_ack_pending"] is False


def test_old_resume_pending_flag_mutation_fails(live_worker, monkeypatch):
    client, _sid, state, _socketio, task, _steps = live_worker
    original_emit = web_events._emit_if_current

    def old_pending_ack(socketio, sid, run_id):
        with web_events._simulations_guard:
            current = web_events._simulations.get(sid)
            if current is None or current.get("run_id") != run_id:
                return False
            if not current.get("running") or not current.pop(
                "pause_ack_pending", False
            ):
                return False
            socketio.emit(
                "simulation_status",
                {"status": "paused", "run_id": run_id},
                room=sid,
            )
            return True

    monkeypatch.setattr(web_events, "_emit_pending_pause_ack", old_pending_ack)

    def resume_without_invalidating(socketio, sid, run_id, event, payload):
        emitted = original_emit(socketio, sid, run_id, event, payload)
        if event == "per_hour_summary":
            client.emit("pause_simulation")
            client.emit("resume_simulation", {"payload_version": 4})
            state["paused"] = False  # old resume left the queued acknowledgement intact
            state["pause_ack_pending"] = True
        return emitted

    monkeypatch.setattr(web_events, "_emit_if_current", resume_without_invalidating)
    with pytest.raises(AssertionError, match="stale paused acknowledgement"):
        task()
        statuses = _status_events(client)
        assert not (
            "resumed" in statuses
            and "paused" in statuses[statuses.index("resumed") + 1 :]
        ), "stale paused acknowledgement"
