import threading
from types import SimpleNamespace

import pytest

import app as app_module
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


def test_cancel_unknown_returns_typed_404():
    response = app_module.create_app().test_client().post(
        "/api/runs/unknown-run/cancel"
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "run not found",
        "error_type": "run_not_found",
    }
