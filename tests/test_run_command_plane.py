import threading
from types import SimpleNamespace

import pytest

import app as app_module
from simulator.backends import BackendUnavailableError
from simulator.melt_backend.base import InternalAnalyticalBackend
from web import events as web_events
from web.run_store import RunArtifactStore


def _runner_document(status: str = "ok") -> dict[str, object]:
    return {
        "schema_version": "1.4.0",
        "status": status,
        "reason": "",
        "error_message": "",
        "run_metadata": {
            "started_at_utc": "2026-07-15T12:00:00Z",
            "feedstock_id": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
            "backend": "stub",
        },
        "per_hour_summary": [{"hour": 1, "campaign": "C0"}],
        "final_state": {"process.cleaned_melt": {"SiO2": 2.0}},
        "final": {},
        "stage_purity_report": {},
        "vapor_pressure_source_report": {"status": "ok"},
    }


class _PartialSession:
    simulator = SimpleNamespace(_poisoned_hour=None)

    def is_complete(self):
        return False

    def result_document(self):
        return _runner_document()


class _CompleteSession(_PartialSession):
    def is_complete(self):
        return True


class _Socket:
    def emit(self, *_args, **_kwargs):
        pass


@pytest.fixture(autouse=True)
def _clean_command_state():
    before = set(web_events._simulations)
    web_events._run_idempotency.clear()
    yield
    for sid in set(web_events._simulations) - before:
        web_events._clear_simulation_state(sid)
    web_events._run_idempotency.clear()


def test_cancel_route_persists_cancelled_partial_and_terminal_is_409(tmp_path):
    app = app_module.create_app()
    store = RunArtifactStore(tmp_path / "runs")
    sid = "cancel-route"
    state, _ = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )

    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["ledger_client_id"] = "owner"
    response = client.post(f"/api/runs/{state['run_id']}/cancel")

    assert response.status_code == 200
    assert response.get_json() == {
        "cancelled": True,
        "run_id": state["run_id"],
        "status": "cancelled",
    }
    artifact = store.load(state["run_id"])
    assert artifact["lifecycle"] == "cancelled"
    assert artifact["execution_status"] == "partial"

    duplicate = client.post(f"/api/runs/{state['run_id']}/cancel")
    assert duplicate.status_code == 409
    assert duplicate.get_json() == {
        "error": "run is already terminal",
        "error_type": "run_not_active",
    }


def test_cancel_complete_boundary_keeps_honest_ok_execution_status(tmp_path):
    store = RunArtifactStore(tmp_path / "runs")
    state, _ = web_events._replace_simulation_state(
        "cancel-complete",
        _CompleteSession(),
        speed=0.0,
        run_store=store,
    )

    web_events._cancel_simulation_state(
        _Socket(),
        "cancel-complete",
        reason="cancelled_by_client",
    )

    artifact = store.load(state["run_id"])
    assert artifact["lifecycle"] == "cancelled"
    assert artifact["execution_status"] == "ok"


def test_cancel_is_scoped_to_owning_browser_session(tmp_path):
    app = app_module.create_app()
    store = RunArtifactStore(tmp_path / "runs")
    state, _ = web_events._replace_simulation_state(
        "owned-run",
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    intruder = app.test_client()
    with intruder.session_transaction() as browser_session:
        browser_session["ledger_client_id"] = "intruder"

    response = intruder.post(f"/api/runs/{state['run_id']}/cancel")

    assert response.status_code == 404
    assert state["running"] is True
    assert store.load(state["run_id"]) is None


def test_disconnect_persists_orphaned_run_as_cancelled_partial(tmp_path, monkeypatch):
    captured_tasks = []

    def force_backend(_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    def capture_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return object()

    monkeypatch.setattr(web_events, "_get_backend", force_backend)
    monkeypatch.setattr(app_module.socketio, "start_background_task", capture_task)
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    before = set(web_events._simulations)
    client = app_module.socketio.test_client(app)
    client.emit(
        "start_simulation",
        {
            "backend": "internal-analytical",
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000,
            "speed": 0,
        },
    )
    sid = (set(web_events._simulations) - before).pop()
    state = web_events._simulations[sid]
    state["session"] = _PartialSession()
    run_id = state["run_id"]

    client.disconnect()

    assert sid not in web_events._simulations
    artifact = RunArtifactStore(tmp_path / "runs").load(run_id)
    assert artifact["lifecycle"] == "cancelled"
    assert artifact["execution_status"] == "partial"


def test_socket_restart_persists_displaced_run(tmp_path, monkeypatch):
    def force_backend(_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    monkeypatch.setattr(web_events, "_get_backend", force_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        lambda target, *args, **kwargs: object(),
    )
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = app_module.socketio.test_client(app)
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
    }
    client.emit("start_simulation", payload)
    sid = next(reversed(web_events._simulations))
    first_state = web_events._simulations[sid]
    first_state["session"] = _PartialSession()
    first_run_id = first_state["run_id"]

    client.emit("start_simulation", payload)

    artifact = RunArtifactStore(tmp_path / "runs").load(first_run_id)
    assert artifact["lifecycle"] == "cancelled"
    assert artifact["execution_status"] == "partial"
    client.disconnect()


def test_submit_idempotency_is_client_scoped_and_payload_bound(monkeypatch):
    app = app_module.create_app()
    calls = []

    def fake_start(payload, **kwargs):
        calls.append((payload, kwargs))
        return {"run_id": "run-1", "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    client = app.test_client()
    payload = {
        "client_token": "retry-token",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
    }

    first = client.post("/api/runs", json=payload)
    replay = client.post("/api/runs", json=payload)
    conflict = client.post("/api/runs", json={**payload, "mass_kg": 2000})

    assert first.status_code == 201
    assert first.get_json()["idempotent_replay"] is False
    assert replay.status_code == 200
    assert replay.get_json()["idempotent_replay"] is True
    assert len(calls) == 1
    assert conflict.status_code == 409
    assert conflict.get_json()["error_type"] == "idempotency_conflict"

    other_client = app.test_client()
    other = other_client.post("/api/runs", json=payload)
    assert other.status_code == 201
    assert len(calls) == 2


def test_concurrent_idempotent_submits_launch_once(monkeypatch):
    barrier = threading.Barrier(3)
    calls = []
    results = []

    def fake_start(payload, **kwargs):
        calls.append((payload, kwargs))
        return {"run_id": "run-concurrent", "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)

    def submit():
        barrier.wait()
        results.append(web_events.submit_run_command(
            _Socket(),
            {"client_token": "same-token", "mass_kg": 1000},
            client_id="same-client",
        ))

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert len(calls) == 1
    assert sorted(result["idempotent_replay"] for result in results) == [False, True]


def test_second_http_submit_replaces_prior_run_and_keeps_ledger_unique(
    tmp_path,
    monkeypatch,
):
    store = RunArtifactStore(tmp_path / "runs")

    def fake_start(
        _payload,
        *,
        sid,
        ledger_client_id,
        replace_sid=None,
        **_kwargs,
    ):
        if replace_sid is not None:
            web_events._cancel_simulation_state(
                _Socket(),
                replace_sid,
                reason="replaced_by_new_run",
            )
        state, _ = web_events._replace_simulation_state(
            sid,
            _PartialSession(),
            speed=0.0,
            ledger_client_id=ledger_client_id,
            run_store=store,
        )
        state["http_owned"] = True
        return {"run_id": state["run_id"], "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    first = web_events.submit_run_command(
        _Socket(),
        {"client_token": "first", "mass_kg": 1000},
        client_id="same-client",
    )
    second = web_events.submit_run_command(
        _Socket(),
        {"client_token": "second", "mass_kg": 2000},
        client_id="same-client",
    )

    first_artifact = store.load(first["run_id"])
    assert first_artifact["lifecycle"] == "cancelled"
    assert first_artifact["execution_status"] == "partial"
    owned = [
        sid
        for sid, state in web_events._simulations.items()
        if state.get("ledger_client_id") == "same-client"
    ]
    assert len(owned) == 1
    assert web_events._simulations[owned[0]]["run_id"] == second["run_id"]
    monkeypatch.setattr(
        web_events,
        "read_ledger_api",
        lambda sid, _resource, **_params: {"sid": sid},
    )
    assert web_events.read_ledger_api_for_client("same-client", "snapshot") == {
        "sid": owned[0]
    }


def test_invalid_http_submit_does_not_destroy_active_run(tmp_path, monkeypatch):
    store = RunArtifactStore(tmp_path / "runs")
    state, _ = web_events._replace_simulation_state(
        "http:owner:active",
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    state["http_owned"] = True

    def reject_invalid(_payload, **_kwargs):
        raise web_events.RunCommandError(
            "mass_kg must be numeric",
            error_type="invalid_run_input",
        )

    monkeypatch.setattr(web_events, "_registered_start_handler", reject_invalid)

    with pytest.raises(web_events.RunCommandError, match="mass_kg must be numeric"):
        web_events.submit_run_command(
            _Socket(),
            {"client_token": "invalid", "mass_kg": "bad"},
            client_id="owner",
        )

    assert web_events._simulations["http:owner:active"] is state
    assert state["running"] is True
    assert state.get("artifact_persisted") is not True
    assert store.load(state["run_id"]) is None


def test_http_terminal_run_releases_session_state(tmp_path, monkeypatch):
    sid = "http:owner:terminal"
    store = RunArtifactStore(tmp_path / "runs")
    state, lock = web_events._replace_simulation_state(
        sid,
        _CompleteSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    state["http_owned"] = True

    class CapturingSocket(_Socket):
        def start_background_task(self, target):
            self.target = target
            return object()

    socket = CapturingSocket()
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {})
    web_events._start_background_loop(
        socket,
        sid,
        state["run_id"],
        lock,
        "backend",
        "available",
        True,
    )

    socket.target()

    assert store.load(state["run_id"]) is not None
    assert sid not in web_events._simulations
    assert sid not in web_events._sim_locks


def test_c6_terminal_persist_excludes_concurrent_cancel(tmp_path, monkeypatch):
    sid = "http:owner:c6-race"
    store = RunArtifactStore(tmp_path / "runs")
    state, _ = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    state["http_owned"] = True

    cancel_attempted = threading.Event()
    cancel_acquired = threading.Event()
    first_persist_entered = threading.Event()
    release_persist = threading.Event()
    inner_lock = threading.RLock()

    class TrackingLock:
        def __enter__(self):
            if threading.current_thread().name == "cancel-c6":
                cancel_attempted.set()
            inner_lock.acquire()
            if threading.current_thread().name == "cancel-c6":
                cancel_acquired.set()
            return self

        def __exit__(self, *_args):
            inner_lock.release()

    run_lock = TrackingLock()
    with web_events._simulations_guard:
        web_events._sim_locks[sid] = run_lock

    c6_refusal = {
        "status": "refused",
        "reason": "no_window",
        "diagnostic": {"reason_refused": "no_window"},
    }
    step = SimpleNamespace(
        per_hour_summary={"hour": 1},
        snapshot={},
        backend_error=None,
        campaign_summary={"c6_refusal_diagnostic": c6_refusal},
        decision_event=None,
    )
    monkeypatch.setattr(
        web_events,
        "drive_session",
        lambda *_args, **_kwargs: iter([step]),
    )
    monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        web_events,
        "_record_last_recipe_capture",
        lambda *_args, **_kwargs: None,
    )
    persist_statuses = []

    def blocking_persist(
        _socketio,
        persist_sid,
        run_id,
        _session,
        *,
        status,
        **_kwargs,
    ):
        persist_statuses.append(status)
        if len(persist_statuses) == 1:
            first_persist_entered.set()
            assert release_persist.wait(2)
        with web_events._simulations_guard:
            current = web_events._simulations.get(persist_sid)
            if current is not None and current.get("run_id") == run_id:
                current["artifact_persisted"] = True
        return {"execution_status": status}

    monkeypatch.setattr(web_events, "_persist_terminal", blocking_persist)

    class CapturingSocket(_Socket):
        def start_background_task(self, target):
            self.target = target
            return object()

    socket = CapturingSocket()
    web_events._start_background_loop(
        socket,
        sid,
        state["run_id"],
        run_lock,
        "backend",
        "available",
        True,
    )
    loop_thread = threading.Thread(target=socket.target, name="c6-loop")
    loop_thread.start()
    assert first_persist_entered.wait(2)

    cancel_thread = threading.Thread(
        target=lambda: web_events._cancel_simulation_state(
            socket,
            sid,
            reason="replaced_by_new_run",
        ),
        name="cancel-c6",
    )
    cancel_thread.start()
    assert cancel_attempted.wait(2)
    assert cancel_acquired.wait(0.05) is False
    assert persist_statuses == ["refused"]

    release_persist.set()
    loop_thread.join(2)
    cancel_thread.join(2)
    assert loop_thread.is_alive() is False
    assert cancel_thread.is_alive() is False
    assert persist_statuses == ["refused"]


def test_idempotency_entries_evict_oldest_at_fixed_bound(monkeypatch):
    calls = []

    def fake_start(_payload, **_kwargs):
        run_id = f"run-{len(calls)}"
        calls.append(run_id)
        return {"run_id": run_id, "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    monkeypatch.setattr(web_events, "_MAX_RUN_IDEMPOTENCY_ENTRIES", 2)
    for token in ("oldest", "middle", "newest"):
        web_events.submit_run_command(
            _Socket(),
            {"client_token": token, "mass_kg": 1000},
            client_id="owner",
        )

    assert list(web_events._run_idempotency) == [
        ("owner", "middle"),
        ("owner", "newest"),
    ]
    replay = web_events.submit_run_command(
        _Socket(),
        {"client_token": "middle", "mass_kg": 1000},
        client_id="owner",
    )
    assert replay["idempotent_replay"] is True
    assert len(calls) == 3


def test_active_idempotency_tokens_are_never_evicted(monkeypatch):
    calls = []

    def fake_start(_payload, *, sid, ledger_client_id, **_kwargs):
        state, _ = web_events._replace_simulation_state(
            sid,
            _PartialSession(),
            speed=0.0,
            ledger_client_id=ledger_client_id,
        )
        state["http_owned"] = True
        calls.append(state["run_id"])
        return {"run_id": state["run_id"], "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    monkeypatch.setattr(web_events, "_MAX_RUN_IDEMPOTENCY_ENTRIES", 2)
    for client_id, token in (("client-a", "token-a"), ("client-b", "token-b")):
        web_events.submit_run_command(
            _Socket(),
            {"client_token": token, "mass_kg": 1000},
            client_id=client_id,
        )

    with pytest.raises(web_events.RunCommandError) as exc_info:
        web_events.submit_run_command(
            _Socket(),
            {"client_token": "token-c", "mass_kg": 1000},
            client_id="client-c",
        )

    assert exc_info.value.error_type == "idempotency_capacity_exhausted"
    assert exc_info.value.status_code == 503
    assert list(web_events._run_idempotency) == [
        ("client-a", "token-a"),
        ("client-b", "token-b"),
    ]
    replay = web_events.submit_run_command(
        _Socket(),
        {"client_token": "token-a", "mass_kg": 1000},
        client_id="client-a",
    )
    assert replay["idempotent_replay"] is True
    assert len(calls) == 2


def test_draft_is_stateless_validate_and_echo(monkeypatch):
    def force_backend(_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    monkeypatch.setattr(web_events, "_get_backend", force_backend)
    app = app_module.create_app()
    before = dict(web_events._simulations)
    response = app.test_client().post(
        "/api/runs/draft",
        json={
            "backend": "internal-analytical",
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000,
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "valid",
        "validated_inputs": {
            "backend": "internal-analytical",
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
        },
    }
    assert web_events._simulations == before


@pytest.mark.parametrize(
    ("path", "body", "error_type"),
    [
        ("/api/runs", [], "invalid_run_request"),
        ("/api/runs/draft", "not-an-object", "invalid_run_request"),
    ],
)
def test_command_routes_return_typed_json_errors(path, body, error_type):
    response = app_module.create_app().test_client().post(path, json=body)

    assert response.status_code == 400
    assert response.get_json()["error_type"] == error_type
    assert response.get_json()["error"]


@pytest.mark.parametrize("path", ["/api/runs", "/api/runs/draft"])
def test_command_routes_share_socket_input_validation(path):
    response = app_module.create_app().test_client().post(
        path,
        json={"mass_kg": "not-a-number"},
    )

    assert response.status_code == 400
    assert response.get_json()["error_type"] == "invalid_run_input"


def test_http_command_error_preserves_structured_socket_diagnostics(monkeypatch):
    def unavailable(_name):
        raise BackendUnavailableError("configured backend is unavailable")

    monkeypatch.setattr(web_events, "_get_backend", unavailable)
    response = app_module.create_app().test_client().post(
        "/api/runs",
        json={"backend": "missing"},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "backend_authoritative": False,
        "backend_status": "unavailable",
        "error": "configured backend is unavailable",
        "error_type": "backend_unavailable",
        "message": "configured backend is unavailable",
        "status": "error",
    }


def test_cancel_unknown_returns_typed_404():
    response = app_module.create_app().test_client().post(
        "/api/runs/unknown-run/cancel"
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "run not found",
        "error_type": "run_not_found",
    }
