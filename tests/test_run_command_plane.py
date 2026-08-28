import threading
from io import BytesIO
from types import SimpleNamespace

import pytest
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Response

import app as app_module
from simulator.backends import BackendUnavailableError
from simulator.melt_backend.base import InternalAnalyticalBackend
from web import events as web_events
from web import routes as web_routes
from web.run_store import RunArtifactStore, persist_run_artifact


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
            "backend": "internal-analytical",
        },
        "per_hour_summary": [{"hour": 1, "campaign": "C0"}],
        "final_state": {"process.cleaned_melt": {"SiO2": 2.0}},
        "final": {},
        "stage_purity_report": {},
        "vapor_pressure_source_report": {
            "vapor_pressure_backend_status": "fallback",
            "authoritative_for_requested_vapor_pressure": False,
        },
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


class _DecisionThenCompleteSession(_PartialSession):
    def __init__(self):
        self.complete = False
        self.auto_applied = False

    def is_complete(self):
        return self.complete

    def pending_decision(self):
        return SimpleNamespace(
            decision_type=SimpleNamespace(name="PATH_AB"),
            options=("recommended",),
            recommendation="recommended",
            context={},
        )

    def resume(self):
        pass


class _Socket:
    def emit(self, *_args, **_kwargs):
        pass

    def sleep(self, _seconds):
        pass

    def start_background_task(self, _target, *_args, **_kwargs):
        return object()


def _single_run_body(
    config=None,
    *,
    target_or_recipe=None,
    name="Command run",
    seed=0,
    fidelity=None,
):
    overrides = dict(config or {})
    feedstock = overrides.pop("feedstock", "lunar_mare_low_ti")
    configured_backend = overrides.pop("backend", None)
    if fidelity is None:
        fidelity = configured_backend or "internal-analytical"
    elif configured_backend is not None:
        raise ValueError("backend and fidelity cannot both be supplied")
    return {
        "single_run": {
            "target_or_recipe": (
                feedstock if target_or_recipe is None else target_or_recipe
            ),
            "l2_overrides": overrides,
            "name": name,
            "seed": seed,
            "fidelity": fidelity,
        },
    }


def _identified_socket_client(app):
    http_client = app.test_client()
    assert http_client.get("/").status_code == 200
    return app_module.socketio.test_client(
        app,
        flask_test_client=http_client,
    )


@pytest.fixture(autouse=True)
def _clean_command_state():
    before = set(web_events._simulations)
    web_events._run_idempotency.clear()
    web_events._socket_client_ids.clear()
    yield
    for sid in set(web_events._simulations) - before:
        web_events._clear_simulation_state(sid)
    web_events._run_idempotency.clear()
    web_events._socket_client_ids.clear()


def test_cancel_route_persists_cancelled_partial_and_terminal_is_idempotent(tmp_path):
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
    assert duplicate.status_code == 200
    assert duplicate.get_json() == {"status": "cancelled"}
    assert store.load(state["run_id"]) == artifact


def test_cancel_by_run_id_cannot_cancel_sid_replacement(tmp_path, monkeypatch):
    store = RunArtifactStore(tmp_path / "runs")
    sid = "aba-cancel"
    first, _ = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    original_cancel = web_events._cancel_simulation_state
    successor = None

    def replace_before_cancel(socketio, target_sid, **kwargs):
        nonlocal successor
        successor, _ = web_events._replace_simulation_state(
            target_sid,
            _PartialSession(),
            speed=0.0,
            ledger_client_id="owner",
            run_store=store,
        )
        return original_cancel(socketio, target_sid, **kwargs)

    monkeypatch.setattr(
        web_events,
        "_cancel_simulation_state",
        replace_before_cancel,
    )

    result = web_events.cancel_run_command(
        _Socket(),
        first["run_id"],
        client_id="owner",
    )

    assert result is None
    assert successor is web_events._simulations[sid]
    assert successor["running"] is True
    assert store.load(successor["run_id"]) is None


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


@pytest.mark.parametrize("owner_mode", ["disconnected_socket", "http"])
def test_viewerless_run_auto_applies_decision_persists_and_reclaims(
    tmp_path,
    monkeypatch,
    owner_mode,
):
    captured_tasks = []
    policies = []

    def capture_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return object()

    monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        web_events,
        "_record_last_recipe_capture",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {})

    def drive_decision(session, _hours, policy):
        policies.append(policy)
        assert session.pending_decision() is not None
        if policy is web_events.DecisionPolicy.OPERATOR:
            return
        session.auto_applied = True
        session.complete = True
        yield SimpleNamespace(
            per_hour_summary={"hour": 1},
            snapshot={},
            backend_error=None,
            campaign_summary=None,
            decision_event=None,
        )

    monkeypatch.setattr(web_events, "drive_session", drive_decision)
    socket = _Socket()
    socket.start_background_task = capture_task
    sid = "viewer-detached-decision"
    store = RunArtifactStore(tmp_path / "runs")
    state, lock = web_events._replace_simulation_state(
        sid,
        _DecisionThenCompleteSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
        initial_state={
            "http_owned": owner_mode == "http",
            "submission_mode": (
                "http" if owner_mode == "http" else "socket"
            ),
            "backend_message": "backend",
            "backend_status": "ok",
            "backend_authoritative": True,
        },
    )
    if owner_mode == "disconnected_socket":
        web_events._socket_client_ids[sid] = "owner"
    run_id = state["run_id"]
    web_events._start_background_loop(
        socket,
        sid,
        run_id,
        lock,
        "backend",
        "ok",
        True,
    )
    target, args, kwargs = captured_tasks[0]
    target(*args, **kwargs)
    if owner_mode == "disconnected_socket":
        assert state["paused"] is True
        assert state["decision_waiting"] is True
        web_events._disconnect_simulation_client(socket, sid)
        target, args, kwargs = next(
            task
            for task in captured_tasks[1:]
            if task[0].__name__ == "run_task"
        )
        target(*args, **kwargs)
        assert state["client_disconnected"] is True

    artifact = store.load(run_id)
    assert artifact["execution_status"] == "ok"
    assert artifact["lifecycle"] == "complete"
    assert state["session"].auto_applied is True
    expected_policies = [web_events.DecisionPolicy.AUTO_APPLY]
    if owner_mode == "disconnected_socket":
        expected_policies.insert(0, web_events.DecisionPolicy.OPERATOR)
    assert policies == expected_policies
    assert sid not in web_events._simulations
    assert sid not in web_events._sim_locks


def test_http_headless_run_has_no_lifetime_cutoff_and_slots_refuse_typed_429(
    tmp_path,
    monkeypatch,
):
    captured_tasks = []
    clock = {"now": 100.0}

    def capture_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return object()

    def force_backend(_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    monkeypatch.setattr(web_events.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(web_events, "_MAX_ACTIVE_RUNS", 1)
    monkeypatch.setattr(web_events, "_get_backend", force_backend)
    monkeypatch.setattr(web_events, "_completion_payload", lambda _sim: {})
    monkeypatch.setattr(
        web_events,
        "_full_runner_payload",
        lambda *_args, **_kwargs: _runner_document(),
    )
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        capture_task,
    )
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    first_client = app.test_client()
    second_client = app.test_client()
    assert first_client.get("/").status_code == 200
    assert second_client.get("/").status_code == 200
    park_input = {
        "single_run": {
            "target_or_recipe": "lunar_mare_low_ti",
            "l2_overrides": {
                "speed": 3600,
                "runtime_campaign_overrides": {
                    "C0": {
                        "max_hours": 1e308,
                        "min_hold_hr": 1e308,
                    },
                },
            },
            "name": "park",
            "seed": 0,
            "fidelity": "internal-analytical",
        },
    }

    submitted = first_client.post("/api/runs", json=park_input)
    assert submitted.status_code == 201, submitted.get_json()
    run_id = submitted.get_json()["run_id"]
    sid = next(
        sid
        for sid, state in web_events._simulations.items()
        if state.get("run_id") == run_id
    )
    state = web_events._simulations[sid]
    assert state["speed"] == 3600.0
    assert state["submission_mode"] == "http"
    assert "viewerless_reclaim_deadline_monotonic" not in state
    assert "viewerless_reclaim_scheduled" not in state
    assert [task[0].__name__ for task in captured_tasks] == ["run_task"]

    capacity_blocked = second_client.post("/api/runs", json=park_input)
    assert capacity_blocked.status_code == 429
    assert capacity_blocked.get_json()["error_type"] == (
        "global_run_capacity_exhausted"
    )

    clock["now"] = 401.0
    assert state["running"] is True
    assert RunArtifactStore(tmp_path / "runs").load(run_id) is None
    monkeypatch.setattr(state["session"], "is_complete", lambda: True)
    run_task, args, kwargs = captured_tasks[0]
    run_task(*args, **kwargs)

    artifact = RunArtifactStore(tmp_path / "runs").load(run_id)
    assert artifact["lifecycle"] == "complete"
    assert artifact["execution_status"] == "ok"
    assert sid not in web_events._simulations
    assert sid not in web_events._sim_locks

    retried = second_client.post("/api/runs", json=park_input)
    assert retried.status_code == 201, retried.get_json()


def test_detached_socket_run_reclaims_at_deadline(tmp_path, monkeypatch):
    captured_tasks = []
    clock = {"now": 100.0}

    def capture_task(target, *args, **kwargs):
        captured_tasks.append((target, args, kwargs))
        return object()

    monkeypatch.setattr(web_events.time, "monotonic", lambda: clock["now"])
    socket = _Socket()
    socket.start_background_task = capture_task
    sid = "detached-socket-reclaim"
    store = RunArtifactStore(tmp_path / "runs")
    state, _ = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
        initial_state={"submission_mode": "socket"},
    )
    web_events._socket_client_ids[sid] = "owner"

    web_events._disconnect_simulation_client(socket, sid)

    assert state["client_disconnected"] is True
    assert state["viewerless_reclaim_deadline_monotonic"] == 400.0
    assert state["viewerless_reclaim_scheduled"] is True
    assert [task[0].__name__ for task in captured_tasks] == ["reclaim_task"]

    clock["now"] = 401.0
    reclaim_task, args, kwargs = captured_tasks[0]
    reclaim_task(*args, **kwargs)

    artifact = store.load(state["run_id"])
    assert artifact["lifecycle"] == "cancelled"
    assert artifact["execution_status"] == "partial"
    assert artifact["failure"]["reason"] == "viewerless_reclaimed"
    assert sid not in web_events._simulations
    assert sid not in web_events._sim_locks


def test_start_racing_disconnect_cannot_publish_orphan(tmp_path, monkeypatch):
    sid = "disconnect-start-race"
    client_id = "owner"
    ready_to_publish = threading.Event()
    release_start = threading.Event()
    result = []
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    handler = web_events._registered_start_handler
    web_events._socket_client_ids[sid] = client_id

    def blocking_backend(_name):
        ready_to_publish.set()
        assert release_start.wait(timeout=2.0)
        return backend

    monkeypatch.setitem(handler.__globals__, "_get_backend", blocking_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        lambda *_args, **_kwargs: None,
    )
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
    }

    def start():
        with app.app_context():
            result.append(handler(payload, sid=sid))

    starter = threading.Thread(target=start)
    starter.start()
    assert ready_to_publish.wait(timeout=2.0)
    web_events._disconnect_simulation_client(_Socket(), sid)
    release_start.set()
    starter.join(timeout=10.0)

    assert not starter.is_alive()
    assert result == [None]
    assert sid not in web_events._simulations
    assert sid not in web_events._socket_client_ids


def test_disconnect_does_not_invoke_cancel_path(tmp_path, monkeypatch):
    sid = "disconnect-no-cancel"
    state, _ = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=RunArtifactStore(tmp_path / "runs"),
    )
    state["paused"] = True
    web_events._socket_client_ids[sid] = "owner"
    monkeypatch.setattr(
        web_events,
        "_cancel_simulation_state",
        lambda *_args, **_kwargs: pytest.fail("disconnect must not cancel"),
    )

    web_events._disconnect_simulation_client(_Socket(), sid)

    assert web_events._simulations[sid] is state
    assert state["running"] is True
    assert state["client_disconnected"] is True
    assert state["paused"] is False
    assert sid not in web_events._socket_client_ids


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
    client = _identified_socket_client(app)
    payload = _single_run_body({
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
    })
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


def test_replacement_launch_failure_reports_persisted_prior_cancellation(
    tmp_path,
    monkeypatch,
):
    def force_backend(_name):
        backend = InternalAnalyticalBackend()
        backend.initialize({})
        return backend

    launch_count = 0

    def launch_then_fail(_target, *_args, **_kwargs):
        nonlocal launch_count
        launch_count += 1
        if launch_count == 2:
            raise RuntimeError("task launch failed")
        return object()

    monkeypatch.setattr(web_events, "_get_backend", force_backend)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        launch_then_fail,
    )
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["ledger_client_id"] = "replacement-owner"
    payload = _single_run_body({
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
    })

    first = client.post("/api/runs", json=payload)
    assert first.status_code == 201
    prior_run_id = first.get_json()["run_id"]
    prior_state = next(
        state
        for state in web_events._simulations.values()
        if state.get("run_id") == prior_run_id
    )
    prior_state["session"] = _PartialSession()

    replacement = client.post(
        "/api/runs",
        json=_single_run_body({
            "backend": "internal-analytical",
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 2000,
            "speed": 0,
        }),
    )

    assert replacement.status_code == 500
    body = replacement.get_json()
    assert body["error_type"] == "run_launch_failed_after_replacement"
    assert body["prior_run_id"] == prior_run_id
    assert body["prior_run_cancelled"] is True
    assert (
        f"prior run {prior_run_id} was cancelled and persisted"
        in body["error"]
    )
    artifact = RunArtifactStore(tmp_path / "runs").load(prior_run_id)
    assert artifact["lifecycle"] == "cancelled"
    assert not any(
        state.get("ledger_client_id") == "replacement-owner"
        for state in web_events._simulations.values()
    )


def test_submit_id_header_is_client_scoped_and_payload_bound(monkeypatch):
    app = app_module.create_app()
    calls = []

    def fake_start(payload, **kwargs):
        calls.append((payload, kwargs))
        return {"run_id": "run-1", "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    client = app.test_client()
    payload = _single_run_body({
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "cost_parameters": {"schema_version": "optimize-costs-v1"},
        "cost_parameters_recipe_name": "http-tab-recipe",
    })
    headers = {"Submit-Id": "retry-token"}

    first = client.post("/api/runs", json=payload, headers=headers)
    replay = client.post("/api/runs", json=payload, headers=headers)
    conflict = client.post(
        "/api/runs",
        json=_single_run_body({
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 2000,
            "cost_parameters": {"schema_version": "optimize-costs-v1"},
            "cost_parameters_recipe_name": "http-tab-recipe",
        }),
        headers=headers,
    )

    assert first.status_code == 201
    assert first.get_json()["idempotent_replay"] is False
    assert replay.status_code == 200
    assert replay.get_json()["idempotent_replay"] is True
    assert len(calls) == 1
    assert calls[0][0]["cost_parameters"] == (
        payload["single_run"]["l2_overrides"]["cost_parameters"]
    )
    assert calls[0][0]["cost_parameters_recipe_name"] == "http-tab-recipe"
    assert conflict.status_code == 409
    assert conflict.get_json()["error_type"] == "idempotency_conflict"

    other_client = app.test_client()
    other = other_client.post("/api/runs", json=payload, headers=headers)
    assert other.status_code == 201
    assert len(calls) == 2


@pytest.mark.parametrize("submit_id", ["   ", "x" * 257])
def test_submit_id_header_validation_is_typed_400(submit_id):
    response = app_module.create_app().test_client().post(
        "/api/runs",
        json=_single_run_body(),
        headers={"Submit-Id": submit_id},
    )

    assert response.status_code == 400
    assert response.get_json()["error_type"] == "invalid_submit_id"


def test_typed_single_run_maps_through_canonical_command_payload(monkeypatch):
    calls = []
    app = app_module.create_app()

    def fake_start(payload, **kwargs):
        calls.append((payload, kwargs))
        return {"run_id": "typed-run", "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    request_body = {
        "single_run": {
            "target_or_recipe": {
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
            },
            "l2_overrides": {
                "setpoints_patch": {
                    "campaigns": {"C4": {"temp_range_C": [1600, 1650]}}
                },
            },
            "name": "Typed run",
            "seed": 7,
            "fidelity": "internal-analytical",
        },
    }

    client = app.test_client()
    headers = {"Submit-Id": "typed-token"}
    first = client.post("/api/runs", json=request_body, headers=headers)
    replay = client.post("/api/runs", json=request_body, headers=headers)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.get_json()["run_id"] == replay.get_json()["run_id"] == "typed-run"
    assert len(calls) == 1
    command_payload, kwargs = calls[0]
    assert command_payload == {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "setpoints_patch": {
            "campaigns": {"C4": {"temp_range_C": [1600, 1650]}}
        },
    }
    assert kwargs["single_run_context"] == {
        "target_or_recipe": {
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000,
        },
        "l2_overrides": {
            "setpoints_patch": {
                "campaigns": {"C4": {"temp_range_C": [1600, 1650]}}
            },
        },
        "name": "Typed run",
        "seed": 7,
        "fidelity": "internal-analytical",
        "parent_run_id": None,
    }


@pytest.mark.parametrize(
    "single_run",
    [
        {},
        {
            "target_or_recipe": "lunar_mare_low_ti",
            "l2_overrides": {},
            "name": "",
            "seed": 0,
            "fidelity": "internal-analytical",
        },
        {
            "target_or_recipe": "lunar_mare_low_ti",
            "l2_overrides": {},
            "name": "bad seed",
            "seed": True,
            "fidelity": "internal-analytical",
        },
    ],
)
def test_typed_single_run_validation_is_typed_400(single_run):
    response = app_module.create_app().test_client().post(
        "/api/runs",
        json={"single_run": single_run},
    )

    assert response.status_code == 400
    assert response.get_json()["error_type"] == "invalid_single_run"


@pytest.mark.parametrize(
    ("body", "error_type"),
    [
        ({"feedstock": "lunar_mare_low_ti"}, "invalid_submission_type"),
        (
            {**_single_run_body(), "client_token": "legacy-body-token"},
            "invalid_single_run",
        ),
        (
            {
                "single_run": {
                    **_single_run_body()["single_run"],
                    "not_a_real_single_run_field": 7,
                },
            },
            "invalid_single_run",
        ),
        (
            _single_run_body(target_or_recipe={}),
            "invalid_single_run",
        ),
        (
            _single_run_body(
                target_or_recipe={
                    "feedstock": "lunar_mare_low_ti",
                    "not_a_real_target_field": 7,
                },
            ),
            "invalid_single_run",
        ),
        (
            _single_run_body({"not_a_real_override": 7}),
            "invalid_single_run",
        ),
        (
            _single_run_body(
                {"mass_kg": 2000},
                target_or_recipe={
                    "feedstock": "lunar_mare_low_ti",
                    "mass_kg": 1000,
                },
            ),
            "invalid_single_run",
        ),
    ],
)
def test_submit_refuses_flat_ambiguous_or_unknown_typed_fields(body, error_type):
    response = app_module.create_app().test_client().post("/api/runs", json=body)

    assert response.status_code == 400
    assert response.get_json()["error_type"] == error_type


def test_failed_tokenized_launch_is_not_cached_and_retry_can_start(monkeypatch):
    calls = []

    def fail_then_start(payload, **kwargs):
        calls.append((payload, kwargs))
        if len(calls) == 1:
            raise RuntimeError("launch failed")
        return {"run_id": "run-retry", "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fail_then_start)
    payload = _single_run_body({"mass_kg": 1000})

    with pytest.raises(RuntimeError, match="launch failed"):
        web_events.submit_run_command(
            _Socket(),
            payload,
            client_id="same-client",
            submit_id="retry-after-failure",
        )
    assert web_events._run_idempotency == {}

    retry = web_events.submit_run_command(
        _Socket(),
        payload,
        client_id="same-client",
        submit_id="retry-after-failure",
    )
    replay = web_events.submit_run_command(
        _Socket(),
        payload,
        client_id="same-client",
        submit_id="retry-after-failure",
    )

    assert retry["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
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
            _single_run_body({"mass_kg": 1000}),
            client_id="same-client",
            submit_id="same-token",
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
        _single_run_body({"mass_kg": 1000}),
        client_id="same-client",
        submit_id="first",
    )
    second = web_events.submit_run_command(
        _Socket(),
        _single_run_body({"mass_kg": 2000}),
        client_id="same-client",
        submit_id="second",
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


@pytest.mark.parametrize("first_transport", ["socket", "http"])
def test_cross_transport_submit_replaces_prior_run_and_keeps_ledger_unique(
    tmp_path,
    monkeypatch,
    first_transport,
):
    monkeypatch.setattr(web_events, "_MAX_ACTIVE_RUNS", 1)
    store = RunArtifactStore(tmp_path / "runs")
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    monkeypatch.setattr(web_events, "_get_backend", lambda _name: backend)
    monkeypatch.setattr(web_events, "get_run_store", lambda: store)
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        lambda _target, *_args, **_kwargs: None,
    )
    app = app_module.create_app()
    http_client = app.test_client()
    assert http_client.get("/").status_code == 200
    with http_client.session_transaction() as browser_session:
        client_id = browser_session["ledger_client_id"]
    socket_client = app_module.socketio.test_client(
        app,
        flask_test_client=http_client,
    )
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
        "track": "pyrolysis",
    }

    try:
        if first_transport == "socket":
            first = socket_client.emit(
                "start_simulation",
                payload,
                callback=True,
            )
            prior_run_id = first["run_id"]
            response = http_client.post(
                "/api/runs",
                json=_single_run_body(payload),
                headers={"Submit-Id": "http-replacement"},
            )
            assert response.status_code == 201
            replacement_run_id = response.get_json()["run_id"]
        else:
            response = http_client.post(
                "/api/runs",
                json=_single_run_body(payload),
                headers={"Submit-Id": "http-first"},
            )
            assert response.status_code == 201
            prior_run_id = response.get_json()["run_id"]
            replacement = socket_client.emit(
                "start_simulation",
                payload,
                callback=True,
            )
            replacement_run_id = replacement["run_id"]

        prior_artifact = store.load(prior_run_id)
        assert prior_artifact["lifecycle"] == "cancelled"
        assert prior_artifact["execution_status"] == "partial"
        owned = [
            state
            for state in web_events._simulations.values()
            if state.get("ledger_client_id") == client_id
        ]
        assert len(owned) == 1
        assert owned[0]["run_id"] == replacement_run_id
        ledger_response = http_client.get("/api/ledger/snapshot")
        assert ledger_response.status_code == 200
    finally:
        socket_client.disconnect()


def test_socket_before_http_identity_does_not_mint_competing_client_id(
    monkeypatch,
):
    app = app_module.create_app()
    http_client = app.test_client()
    socket_client = app_module.socketio.test_client(
        app,
        flask_test_client=http_client,
    )

    try:
        assert socket_client.is_connected()
        statuses = [
            event["args"][0]
            for event in socket_client.get_received()
            if event["name"] == "simulation_status"
        ]
        assert statuses == [{
            "status": "error",
            "message": "browser identity must be established over HTTP first",
            "error_type": "client_identity_required",
        }]
        assert web_events._socket_client_ids == {}

        monkeypatch.setattr(
            web_events,
            "validate_run_draft",
            lambda _payload, *, client_id: {
                "status": "valid",
                "client_id": client_id,
            },
        )
        response = http_client.post("/api/runs/draft", json={})

        assert response.status_code == 200
        http_client_id = response.get_json()["client_id"]
        assert http_client_id
        assert http_client_id not in web_events._socket_client_ids.values()
    finally:
        socket_client.disconnect()


@pytest.mark.parametrize("replacement_transport", ["http", "socket"])
def test_replacement_persist_failure_is_typed_and_keeps_honest_state(
    tmp_path,
    monkeypatch,
    replacement_transport,
):
    store = RunArtifactStore(tmp_path / "runs")
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    monkeypatch.setattr(web_events, "_get_backend", lambda _name: backend)
    monkeypatch.setattr(web_events, "get_run_store", lambda: store)
    launched = []
    monkeypatch.setattr(
        app_module.socketio,
        "start_background_task",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )
    emitted = []
    monkeypatch.setattr(
        app_module.socketio,
        "emit",
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )
    app = app_module.create_app()
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
    }
    typed_payload = _single_run_body(payload)
    token_headers = {"Submit-Id": "original-run"}
    client = app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["ledger_client_id"] = "owner"
    initial = client.post(
        "/api/runs",
        json=typed_payload,
        headers=token_headers,
    )
    assert initial.status_code == 201
    prior_run_id = initial.get_json()["run_id"]
    prior_sid, prior = next(
        (sid, state)
        for sid, state in web_events._simulations.items()
        if state.get("run_id") == prior_run_id
    )
    monkeypatch.setattr(store, "save", lambda *_args, **_kwargs: False)

    if replacement_transport == "http":
        response = client.post("/api/runs", json=typed_payload)
        assert response.status_code == 500
        assert response.get_json()["error_type"] == "run_replacement_failed"
    else:
        result = web_events._registered_start_handler(
            payload,
            sid="socket-replacement",
            ledger_client_id="owner",
        )
        assert result is None
        assert emitted[-1][1]["error_type"] == "run_replacement_failed"

    assert web_events._simulations[prior_sid] is prior
    assert prior["running"] is False
    assert prior.get("artifact_persisted") is not True
    assert store.load(prior["run_id"]) is None
    assert "socket-replacement" not in web_events._simulations

    replay = client.post(
        "/api/runs",
        json=typed_payload,
        headers=token_headers,
    )
    assert replay.status_code == 200
    assert replay.get_json() == {
        "idempotent_replay": True,
        "message": "Run was cancelled but its report was not saved",
        "reason": "persistence_failed",
        "run_id": prior_run_id,
        "status": "error",
    }
    assert [args[0].__name__ for args, _kwargs in launched] == ["run_task"]

    monkeypatch.setattr(web_events, "_MAX_RUN_IDEMPOTENCY_ENTRIES", 1)
    fresh_launches = []

    def fake_start(_payload, **_kwargs):
        fresh_launches.append(True)
        return {"run_id": "fresh-run", "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    fresh = web_events.submit_run_command(
        _Socket(),
        _single_run_body({"mass_kg": 1000}),
        client_id="fresh-owner",
        submit_id="fresh-token",
    )
    assert fresh["run_id"] == "fresh-run"
    assert fresh_launches == [True]
    assert list(web_events._run_idempotency) == [
        ("fresh-owner", "fresh-token")
    ]


def test_invalid_http_submit_does_not_destroy_active_run(tmp_path):
    app_module.create_app()
    store = RunArtifactStore(tmp_path / "runs")
    state, _ = web_events._replace_simulation_state(
        "http:owner:active",
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    state["http_owned"] = True

    with pytest.raises(web_events.RunCommandError, match="mass_kg must be numeric"):
        web_events.submit_run_command(
            _Socket(),
            _single_run_body({"mass_kg": "bad"}),
            client_id="owner",
            submit_id="invalid",
        )

    assert web_events._simulations["http:owner:active"] is state
    assert state["running"] is True
    assert state.get("artifact_persisted") is not True
    assert store.load(state["run_id"]) is None


def test_cancel_already_persisted_http_run_releases_state_and_lock():
    sid = "http:owner:already-persisted"
    state, _ = web_events._replace_simulation_state(
        sid,
        _CompleteSession(),
        speed=0.0,
        ledger_client_id="owner",
    )
    state["http_owned"] = True
    state["artifact_persisted"] = True

    result = web_events._cancel_simulation_state(
        _Socket(),
        sid,
        reason="cancelled_by_client",
    )

    assert result == {
        "run_id": state["run_id"],
        "status": "terminal",
        "cancelled": False,
    }
    assert sid not in web_events._simulations
    assert sid not in web_events._sim_locks


def test_http_natural_completion_with_zero_viewers_persists_and_reclaims_capacity(
    tmp_path,
    monkeypatch,
):
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
    release_transitions = []
    finish_terminal_state = web_events._finish_terminal_state

    def count_release(finish_sid, finish_run_id, **kwargs):
        before, _ = web_events._current_simulation_state(finish_sid, finish_run_id)
        was_running = bool(before and before.get("running"))
        finish_terminal_state(finish_sid, finish_run_id, **kwargs)
        after, _ = web_events._current_simulation_state(finish_sid, finish_run_id)
        if was_running and (after is None or not after.get("running")):
            release_transitions.append(finish_run_id)

    monkeypatch.setattr(web_events, "_finish_terminal_state", count_release)
    web_events._start_background_loop(
        socket,
        sid,
        state["run_id"],
        lock,
        "backend",
        "ok",
        True,
    )

    socket.target()
    socket.target()

    artifact = store.load(state["run_id"])
    assert artifact["execution_status"] == "ok"
    assert artifact["lifecycle"] == "complete"
    assert release_transitions == [state["run_id"]]
    assert sid not in web_events._simulations
    assert sid not in web_events._sim_locks
    monkeypatch.setattr(web_events, "_MAX_ACTIVE_RUNS", 1)
    web_events._ensure_global_run_capacity(None)


def test_crashed_background_worker_persists_failure_and_releases_capacity(
    tmp_path,
    monkeypatch,
):
    sid = "http:owner:worker-crash"
    store = RunArtifactStore(tmp_path / "runs")
    state, lock = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    state["http_owned"] = True
    state["paused"] = True

    class CrashingSocket(_Socket):
        def start_background_task(self, target):
            self.target = target
            return object()

        def sleep(self, _seconds):
            raise RuntimeError("synthetic worker crash")

    socket = CrashingSocket()
    web_events._start_background_loop(
        socket,
        sid,
        state["run_id"],
        lock,
        "backend",
        "ok",
        True,
    )
    monkeypatch.setattr(web_events, "_MAX_ACTIVE_RUNS", 1)
    with pytest.raises(web_events.RunCommandError) as saturated:
        web_events._ensure_global_run_capacity(None)
    assert saturated.value.error_type == "global_run_capacity_exhausted"

    with pytest.raises(RuntimeError, match="synthetic worker crash"):
        socket.target()

    artifact = store.load(state["run_id"])
    assert artifact["execution_status"] == "failed"
    assert artifact["lifecycle"] == "complete"
    assert artifact["failure"] == {
        "reason": "background_worker_crash:RuntimeError",
        "error_message": "synthetic worker crash",
    }
    assert artifact["timesteps"] == [
        {"hour": 1, "summary": {"hour": 1, "campaign": "C0"}}
    ]
    assert sid not in web_events._simulations
    assert sid not in web_events._sim_locks
    web_events._ensure_global_run_capacity(None)


def test_c6_campaign_refusal_does_not_persist_terminal(tmp_path, monkeypatch):
    sid = "http:owner:c6-campaign-refusal"
    store = RunArtifactStore(tmp_path / "runs")
    state, run_lock = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
        run_store=store,
    )
    state["http_owned"] = True
    step = SimpleNamespace(
        per_hour_summary={"hour": 1},
        snapshot={},
        backend_error=None,
        campaign_summary={
            "c6_refusal_diagnostic": {
                "status": "refused",
                "diagnostic": {"reason_refused": "no_window"},
            }
        },
        decision_event=None,
    )

    def drive_once(*_args, **_kwargs):
        state["running"] = False
        yield step

    monkeypatch.setattr(web_events, "drive_session", drive_once)
    monkeypatch.setattr(web_events, "_tick_payload", lambda **_kwargs: {})
    monkeypatch.setattr(
        web_events,
        "_record_last_recipe_capture",
        lambda *_args, **_kwargs: None,
    )
    persist_statuses = []
    monkeypatch.setattr(
        web_events,
        "_persist_terminal",
        lambda *_args, status, **_kwargs: persist_statuses.append(status),
    )

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
        "ok",
        True,
    )
    socket.target()

    assert persist_statuses == []
    assert store.load(state["run_id"]) is None


def test_failure_terminal_persist_excludes_concurrent_cancel(monkeypatch):
    sid = "http:owner:failure-race"
    state, run_lock = web_events._replace_simulation_state(
        sid,
        _PartialSession(),
        speed=0.0,
        ledger_client_id="owner",
    )
    state["http_owned"] = True
    step = SimpleNamespace(
        per_hour_summary={"hour": 1},
        snapshot={},
        backend_error=None,
        campaign_summary=None,
        decision_event=None,
    )
    monkeypatch.setattr(
        web_events,
        "drive_session",
        lambda *_args, **_kwargs: iter([step]),
    )
    monkeypatch.setattr(
        web_events,
        "_tick_payload",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("tick failed")),
    )
    persist_entered = threading.Event()
    release_persist = threading.Event()
    cancel_started = threading.Event()
    cancel_finished = threading.Event()
    persist_statuses = []
    cancel_results = []
    thread_errors = []

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
            persist_entered.set()
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
        "ok",
        True,
    )

    def run_loop():
        try:
            socket.target()
        except Exception as exc:  # pragma: no cover - assertion captures regression
            thread_errors.append(exc)

    def cancel():
        cancel_started.set()
        try:
            cancel_results.append(web_events._cancel_simulation_state(
                socket,
                sid,
                reason="replaced_by_new_run",
            ))
        except Exception as exc:  # pragma: no cover - assertion captures regression
            thread_errors.append(exc)
        finally:
            cancel_finished.set()

    loop_thread = threading.Thread(target=run_loop, name="failure-loop")
    loop_thread.start()
    assert persist_entered.wait(2)
    cancel_thread = threading.Thread(target=cancel, name="cancel-failure")
    cancel_thread.start()
    assert cancel_started.wait(2)
    assert cancel_finished.wait(0.05) is False
    assert persist_statuses == ["failed"]

    release_persist.set()
    loop_thread.join(2)
    cancel_thread.join(2)
    assert loop_thread.is_alive() is False
    assert cancel_thread.is_alive() is False
    assert thread_errors == []
    assert persist_statuses == ["failed"]
    assert cancel_results[0] is None or cancel_results[0] == {
        "run_id": state["run_id"],
        "status": "terminal",
        "cancelled": False,
    }


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
            _single_run_body({"mass_kg": 1000}),
            client_id="owner",
            submit_id=token,
        )

    assert list(web_events._run_idempotency) == [
        ("owner", "middle"),
        ("owner", "newest"),
    ]
    replay = web_events.submit_run_command(
        _Socket(),
        _single_run_body({"mass_kg": 1000}),
        client_id="owner",
        submit_id="middle",
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
            _single_run_body({"mass_kg": 1000}),
            client_id=client_id,
            submit_id=token,
        )

    with pytest.raises(web_events.RunCommandError) as exc_info:
        web_events.submit_run_command(
            _Socket(),
            _single_run_body({"mass_kg": 1000}),
            client_id="client-c",
            submit_id="token-c",
        )

    assert exc_info.value.error_type == "idempotency_capacity_exhausted"
    assert exc_info.value.status_code == 503
    assert list(web_events._run_idempotency) == [
        ("client-a", "token-a"),
        ("client-b", "token-b"),
    ]
    replay = web_events.submit_run_command(
        _Socket(),
        _single_run_body({"mass_kg": 1000}),
        client_id="client-a",
        submit_id="token-a",
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
        json=_single_run_body({"mass_kg": 1000}),
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
    ("body", "error_type"),
    [
        (
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "bogus": True,
            },
            "invalid_submission_type",
        ),
        (
            {
                "single_run": {
                    **_single_run_body({"mass_kg": 1000})["single_run"],
                    "bogus": True,
                },
            },
            "invalid_single_run",
        ),
        (
            {
                "backend": "internal-analytical",
                "feedstock": "lunar_mare_low_ti",
                "mass_kg": 1000,
                "client_token": "legacy-token",
            },
            "invalid_submission_type",
        ),
    ],
)
def test_draft_refuses_fail_open_flat_or_unknown_payloads(body, error_type):
    app = app_module.create_app()

    response = app.test_client().post("/api/runs/draft", json=body)

    assert response.status_code == 400
    assert response.get_json()["error_type"] == error_type


def test_source_run_draft_is_pure_and_child_persists_lineage(
    tmp_path,
    monkeypatch,
):
    store = RunArtifactStore(tmp_path / "runs")
    source_document = _runner_document()
    source_document["run_metadata"]["seed"] = 11
    source_document["run_metadata"]["single_run"] = {
        "target_or_recipe": {
            "feedstock": "lunar_mare_low_ti",
            "mass_kg": 1000.0,
        },
        "l2_overrides": {
            "setpoints_patch": {
                "campaigns": {"C4": {"temp_range_C": [1600.0, 1650.0]}}
            },
        },
        "name": "Source run",
        "seed": 11,
        "fidelity": "internal-analytical",
        "parent_run_id": None,
    }
    persist_run_artifact(
        source_document,
        "source-run",
        name="Source run",
        store=store,
    )
    source_path = tmp_path / "runs" / "source-run.json"
    source_before = source_path.read_bytes()
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    client = app.test_client()

    draft_response = client.post(
        "/api/runs/draft",
        json={"source_run_id": "source-run"},
    )

    assert draft_response.status_code == 200
    draft = draft_response.get_json()["draft"]
    single_run = draft["single_run"]
    assert single_run["target_or_recipe"] == {
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000.0,
    }
    assert single_run["l2_overrides"]["setpoints_patch"] == {
        "campaigns": {"C4": {"temp_range_C": [1600.0, 1650.0]}}
    }
    assert single_run["name"] == "Source run"
    assert single_run["seed"] == 11
    assert single_run["fidelity"] == "internal-analytical"
    assert single_run["parent_run_id"] == "source-run"
    single_run["name"] = "Derived run"
    command_payload, context = web_events._typed_single_run_payload(draft)
    assert command_payload["setpoints_patch"] == {
        "campaigns": {"C4": {"temp_range_C": [1600.0, 1650.0]}}
    }
    assert context["parent_run_id"] == "source-run"

    def fake_start(
        _payload,
        *,
        sid,
        ledger_client_id,
        single_run_context,
        **_kwargs,
    ):
        state, _ = web_events._replace_simulation_state(
            sid,
            _PartialSession(),
            speed=0.0,
            ledger_client_id=ledger_client_id,
            run_store=store,
            initial_state={
                "http_owned": True,
                "single_run": single_run_context,
            },
        )
        return {"run_id": state["run_id"], "status": "started"}

    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    submission = client.post("/api/runs", json=draft)
    assert submission.status_code == 201, submission.get_json()
    child_run_id = submission.get_json()["run_id"]
    cancelled = client.post(f"/api/runs/{child_run_id}/cancel")
    assert cancelled.status_code == 200

    child = store.load(child_run_id)
    assert child["header"]["name"] == "Derived run"
    assert child["header"]["seed"] == 11
    assert child["terminal"]["run_metadata"]["single_run"]["parent_run_id"] == (
        "source-run"
    )
    assert next(
        row for row in store.list_runs() if row["run_id"] == child_run_id
    )["parent_run_id"] == "source-run"
    assert source_path.read_bytes() == source_before


def test_alias_fidelity_is_canonical_in_persisted_typed_provenance(
    tmp_path,
    monkeypatch,
):
    store = RunArtifactStore(tmp_path / "runs")
    command_payloads = []

    def fake_start(
        payload,
        *,
        sid,
        ledger_client_id,
        single_run_context,
        **_kwargs,
    ):
        command_payloads.append(payload)
        state, _ = web_events._replace_simulation_state(
            sid,
            _PartialSession(),
            speed=0.0,
            ledger_client_id=ledger_client_id,
            run_store=store,
            initial_state={
                "http_owned": True,
                "single_run": single_run_context,
            },
        )
        return {"run_id": state["run_id"], "status": "started"}

    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")
    monkeypatch.setattr(web_events, "_registered_start_handler", fake_start)
    client = app.test_client()

    submission = client.post(
        "/api/runs",
        json=_single_run_body({"mass_kg": 1000}, fidelity="stub"),
    )
    assert submission.status_code == 201
    run_id = submission.get_json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200

    artifact = store.load(run_id)
    assert command_payloads[0]["backend"] == "internal-analytical"
    assert artifact["header"]["engine_identity"]["name"] == "internal-analytical"
    assert artifact["header"]["engine_identity"]["backend_wire_token"] == (
        "internal-analytical"
    )
    assert artifact["terminal"]["run_metadata"]["backend"] == "internal-analytical"
    assert (
        artifact["terminal"]["run_metadata"]["single_run"]["fidelity"]
        == "internal-analytical"
    )

    def strings(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
        elif isinstance(value, str):
            yield value

    # ★ THE LEGACY SPELLING HERE IS LOAD-BEARING, in both places: this test
    # feeds the ALIAS in (above) and asserts the alias does NOT survive into
    # the persisted artifact, while the canonical assertions above require it
    # to be there. Renaming either one inverts the test -- a sweep did exactly
    # that, leaving it asserting the canonical token was absent while five
    # lines up demanding it be present.
    assert "stub" not in set(strings(artifact))


def test_source_run_draft_unknown_is_typed_404(tmp_path):
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")

    response = app.test_client().post(
        "/api/runs/draft",
        json={"source_run_id": "missing"},
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "source run not found",
        "error_type": "run_not_found",
    }


def test_typed_child_submit_unknown_parent_is_typed_404(tmp_path):
    app = app_module.create_app()
    app.config["RUN_ARTIFACT_DIR"] = str(tmp_path / "runs")

    response = app.test_client().post(
        "/api/runs",
        json={
            "single_run": {
                "target_or_recipe": "lunar_mare_low_ti",
                "l2_overrides": {},
                "name": "Orphan child",
                "seed": 0,
                "fidelity": "internal-analytical",
                "parent_run_id": "missing",
            },
        },
    )

    assert response.status_code == 404
    assert response.get_json()["error_type"] == "run_not_found"


def test_draft_validation_returns_typed_503_when_capacity_is_saturated(
    monkeypatch,
):
    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(web_events, "_draft_validation_slots", slots)
    try:
        response = app_module.create_app().test_client().post(
            "/api/runs/draft",
            json=_single_run_body({"mass_kg": 1000}),
        )
    finally:
        slots.release()

    assert response.status_code == 503
    assert response.get_json() == {
        "error": "run draft validation capacity is exhausted",
        "error_type": "draft_validation_capacity_exhausted",
    }


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


def test_run_command_route_rejects_dns_rebinding_host_with_typed_403():
    response = app_module.create_app().test_client().post(
        "/api/runs/draft",
        json={},
        headers={
            "Host": "attacker.example:3000",
            "Origin": "http://localhost:3000",
        },
    )

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "request Host does not match the configured server bind",
        "error_type": "untrusted_request_host",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/runs",
        "/api/runs/draft",
        "/api/runs/unknown-run/cancel",
        "/api/runs/unknown-run/meta",
    ],
)
def test_run_command_routes_reject_bodies_over_one_mib(path):
    body = b'{"padding":"' + (
        b"x" * web_routes.RUN_COMMAND_BODY_CAP_BYTES
    ) + b'"}'

    response = app_module.create_app().test_client().open(
        path,
        method="PATCH" if path.endswith("/meta") else "POST",
        data=body,
        content_type="application/json",
    )

    assert response.status_code == 413
    assert response.get_json() == {
        "error": "run command body exceeds 1 MiB cap",
        "error_type": "run_command_too_large",
    }


def test_run_command_route_rejects_chunked_body_over_one_mib():
    body = b'{"padding":"' + (
        b"x" * web_routes.RUN_COMMAND_BODY_CAP_BYTES
    ) + b'"}'
    builder = EnvironBuilder(
        path="/api/runs",
        method="POST",
        input_stream=BytesIO(body),
        content_type="application/json",
    )
    environ = builder.get_environ()
    environ.pop("CONTENT_LENGTH", None)
    environ["HTTP_TRANSFER_ENCODING"] = "chunked"
    environ["wsgi.input_terminated"] = True

    response = Response.from_app(app_module.create_app(), environ)

    assert response.status_code == 413
    assert response.get_json() == {
        "error": "run command body exceeds 1 MiB cap",
        "error_type": "run_command_too_large",
    }


@pytest.mark.parametrize("transport", ["http", "socket"])
def test_global_active_run_cap_is_shared_by_http_and_socket(
    tmp_path,
    monkeypatch,
    transport,
):
    monkeypatch.setattr(web_events, "_MAX_ACTIVE_RUNS", 1)
    store = RunArtifactStore(tmp_path / "runs")
    active, _ = web_events._replace_simulation_state(
        "active-owner-sid",
        _PartialSession(),
        speed=0.0,
        ledger_client_id="active-owner",
        run_store=store,
    )
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    monkeypatch.setattr(web_events, "_get_backend", lambda _name: backend)
    monkeypatch.setattr(web_events, "get_run_store", lambda: store)
    emitted = []
    monkeypatch.setattr(
        app_module.socketio,
        "emit",
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )
    app = app_module.create_app()
    payload = {
        "backend": "internal-analytical",
        "feedstock": "lunar_mare_low_ti",
        "mass_kg": 1000,
        "speed": 0,
    }

    if transport == "http":
        response = app.test_client().post(
            "/api/runs",
            json=_single_run_body(payload),
        )
        assert response.status_code == 429
        assert response.get_json()["error_type"] == "global_run_capacity_exhausted"
    else:
        result = web_events._registered_start_handler(
            payload,
            sid="saturated-socket",
            ledger_client_id="socket-owner",
        )
        assert result is None
        assert emitted[-1][1]["error_type"] == "global_run_capacity_exhausted"

    assert web_events._simulations["active-owner-sid"] is active
    assert active["running"] is True


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/runs", _single_run_body({"mass_kg": "not-a-number"})),
        (
            "/api/runs/draft",
            _single_run_body({"mass_kg": "not-a-number"}),
        ),
    ],
)
def test_command_routes_share_socket_input_validation(path, body):
    response = app_module.create_app().test_client().post(
        path,
        json=body,
    )

    assert response.status_code == 400
    assert response.get_json()["error_type"] == "invalid_run_input"


def test_submit_rejects_compound_c5_enabled_with_typed_400():
    response = app_module.create_app().test_client().post(
        "/api/runs",
        json=_single_run_body({"c5_enabled": {"unexpected": True}}),
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "c5_enabled must be a boolean",
        "error_type": "invalid_run_input",
        "message": "c5_enabled must be a boolean",
        "status": "error",
    }


def test_http_command_error_preserves_structured_socket_diagnostics(monkeypatch):
    def unavailable(_name):
        raise BackendUnavailableError("configured backend is unavailable")

    monkeypatch.setattr(web_events, "_get_backend", unavailable)
    response = app_module.create_app().test_client().post(
        "/api/runs",
        json=_single_run_body({"backend": "missing"}),
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
