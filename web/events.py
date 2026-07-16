"""SocketIO event handlers for the simulator interface."""

import copy
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
import threading
import uuid
from pathlib import Path

import yaml
try:
    from flask import request, session as flask_session
except ModuleNotFoundError:
    class _MissingFlaskRequest:
        sid = None

        def __getattr__(self, name):
            raise RuntimeError("Flask is required for SocketIO request context")

    request = _MissingFlaskRequest()

from simulator.backends import (
    BackendSelectionPolicy,
    BackendUnavailableError,
    backend_resolution_status,
    emit_web_engine_selection_log,
    resolve_backend,
)
from simulator.accounting.ledger_api import LedgerAPI
from simulator.accounting.run_artifact import build_run_artifact
from simulator.backend_names import ANALYTICAL_BACKEND_SERIALIZATION_TOKEN
from simulator.campaigns import CampaignManager
from simulator.condensation import KnudsenRegimeRefusal, stage_purity_report
from simulator.cost_parameters import (
    normalize_cost_parameters,
)
from simulator.core import PoisonedHourError
from simulator.furnace_materials import resolve_furnace_max_T_C
from simulator.melt_backend.base import InternalAnalyticalBackend
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.optimize.recipe import recipe_schema_version
from simulator.recipe_io import RecipeIOError, normalize_recipe_patch
from simulator.run_executor import RunExecutor
from simulator.runner import PyrolysisRun, RunnerError, _deep_merge_setpoints
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    drive_session,
    normalize_mre_policy,
)
from simulator.state import MOLAR_MASS
from simulator.trace import PhysicsTrace
# Goal #18 ``JSON-RUNNER-HARNESS``: the SocketIO stream and the CLI
# runner share ONE per-hour summary builder.  ``SimSession.advance()``
# owns that runner-format summary and returns it in ``StepResult``; this
# adapter only emits it alongside the legacy ``simulation_tick`` payload.
from web.feedstock_data import load_visible_feedstocks
from web.advisory import (
    active_wall_species_from_flue,
    ceramic_rump_payload,
    oxide_wt_pct_from_kg,
    vapor_pressure_authority_payload,
    wall_advisory_payload,
)
from web.run_store import get_run_store, persist_run_artifact


DATA_DIR = Path(__file__).parent.parent / 'data'


def _load_yaml(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


# Active simulations keyed by session ID
_simulations: dict = {}
_sim_locks: dict = {}
_simulations_guard = threading.Lock()
_run_command_lock = threading.RLock()
_run_idempotency_guard = threading.Lock()
_socket_client_ids: dict[str, str] = {}
_loaded_recipe_cost_parameters: dict[str, dict[str, object]] = {}
_run_idempotency: dict[tuple[str, str], tuple[str, dict[str, object]]] = {}
_MAX_RUN_IDEMPOTENCY_ENTRIES = 1024
_MAX_ACTIVE_RUNS = 4
_draft_validation_slots = threading.BoundedSemaphore(1)
_registered_start_handler = None
_registered_socketio = None
_O2_KG_PER_MOL = MOLAR_MASS['O2'] / 1000.0
_MAX_WEB_MASS_KG = 1_000_000_000.0
_MAX_SIM_SPEED_SECONDS = 3600.0
_MAX_C4_TEMP_C = 5000.0
_MAX_MRE_VOLTAGE_V = 100.0
_MAX_ADDITIVE_KG = 1_000_000_000.0
_MASS_BALANCE_ERROR_BREACH_PCT = 5e-12


class InputValidationError(ValueError):
    pass


class RecipeStateError(ValueError):
    pass


class RunCommandError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        status_code: int = 400,
        payload: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code
        self.payload = dict(payload or {})

    def response_payload(self) -> dict[str, object]:
        payload = copy.deepcopy(self.payload)
        payload['error'] = str(self)
        payload['error_type'] = self.error_type
        return payload


class _RunReplacementError(RuntimeError):
    pass


def _safe_log(message: str) -> None:
    try:
        print(message)
    except OSError:
        pass


def _replace_simulation_state(
    sid: str,
    session,
    speed: float,
    *,
    ledger_client_id: str | None = None,
    run_store=None,
    runner_projector=None,
    initial_state: Mapping[str, object] | None = None,
) -> tuple[dict, threading.Lock]:
    """Install one active simulation for a client and stop any prior run."""
    while True:
        with _simulations_guard:
            previous = _simulations.get(sid)
            previous_lock = _sim_locks.get(sid)
        if previous_lock is not None:
            previous_lock.acquire()
        try:
            with _simulations_guard:
                if _simulations.get(sid) is not previous:
                    continue
                if previous is not None:
                    previous['running'] = False
                run_lock = threading.RLock()
                state = {
                    'session': session,
                    'running': True,
                    'paused': False,
                    'speed': speed,
                    'run_id': uuid.uuid4().hex,
                    'per_hour_ledger': {},
                    'ledger_client_id': ledger_client_id,
                    'run_store': run_store,
                    'runner_projector': runner_projector,
                }
                if initial_state:
                    state.update(initial_state)
                _simulations[sid] = state
                _sim_locks[sid] = run_lock
                return state, run_lock
        finally:
            if previous_lock is not None:
                previous_lock.release()


def _current_simulation_state(
    sid: str,
    run_id: str | None = None,
) -> tuple[dict | None, object | None]:
    """Return the current simulation state/lock, optionally scoped to one run."""
    with _simulations_guard:
        state = _simulations.get(sid)
        if state is None:
            return None, None
        if run_id is not None and state.get('run_id') != run_id:
            return None, None
        return state, _sim_locks.get(sid)


def read_ledger_api(
    sid: str,
    resource: str,
    *,
    include_run_id: bool = False,
    **params,
):
    """Read one L1 ledger resource from an active session under its run lock."""
    state, lock = _current_simulation_state(sid)
    if state is None:
        raise LookupError("no active simulation")
    run_id = state.get('run_id')

    def read():
        api = LedgerAPI(state['session'].simulator)
        if resource == 'accounts':
            return api.accounts()
        if resource == 'account':
            units = str(params.get('units', 'kg'))
            pattern = params.get('pattern')
            if pattern:
                return api.account_pattern(str(pattern), units=units)
            return api.account(str(params.get('account', '')), units=units)
        if resource == 'view':
            return api.view(str(params.get('view', '')))
        if resource == 'snapshot':
            return api.snapshot()
        raise ValueError("unknown ledger resource")

    def response():
        payload = read()
        if include_run_id and isinstance(payload, Mapping):
            payload = dict(payload)
            payload['run_id'] = run_id
        return payload

    if lock is None:
        return response()
    with lock:
        current, _ = _current_simulation_state(sid, run_id)
        if current is not state:
            raise LookupError("simulation run changed")
        return response()


def read_ledger_api_for_client(client_id: str, resource: str, **params):
    """Resolve an active run owned by one signed Flask browser session."""
    with _run_command_lock:
        with _simulations_guard:
            matches = [
                sid for sid, state in _simulations.items()
                if state.get('ledger_client_id') == client_id
            ]
        if len(matches) != 1:
            raise LookupError("no unique active simulation for this browser session")
        return read_ledger_api(matches[0], resource, **params)


def _emit_if_current(socketio, sid: str, run_id: str, event: str, payload) -> bool:
    with _simulations_guard:
        state = _simulations.get(sid)
        run_lock = _sim_locks.get(sid)
        if state is None or run_lock is None or state.get('run_id') != run_id:
            return False
    with run_lock:
        with _simulations_guard:
            state = _simulations.get(sid)
            if (
                state is None
                or state.get('run_id') != run_id
                or (
                    not state['running']
                    and not state.get('terminal_emission_pending')
                )
            ):
                return False
        emitted_payload = payload
        if isinstance(payload, Mapping):
            emitted_payload = dict(payload)
            emitted_payload['run_id'] = run_id
        socketio.emit(event, emitted_payload, room=sid)
        return True


def recipe_save_context(sid: str) -> dict[str, object]:
    state, lock = _current_simulation_state(sid)
    if state is None:
        raise RecipeStateError("recipe save requires an active web session")

    def snapshot_context() -> dict[str, object]:
        capture = state.get("last_recipe_capture")
        if not capture:
            raise RecipeStateError("recipe save requires a completed or running tick")
        return {
            "recipe_inputs": copy.deepcopy(state.get("recipe_inputs") or {}),
            "setpoints_patch": copy.deepcopy(state.get("setpoints_patch") or {}),
            "resolved_setpoints_patch": copy.deepcopy(
                state.get("resolved_setpoints_patch") or {}
            ),
            "last_recipe_capture": copy.deepcopy(capture),
            "last_completion_payload": copy.deepcopy(
                state.get("last_completion_payload") or {}
            ),
        }

    if lock is None:
        return snapshot_context()
    with lock:
        return snapshot_context()


def _cost_stash_key_for_sid(sid: str) -> str:
    """Client-bound key for staged recipe cost identity.

    Prefer ledger_client_id so a reconnect or control edit does not orphan
    the operator's loaded-recipe prices. Fall back to sid only when no client
    identity is known (unit tests / pre-connect paths).
    """
    client_id = _socket_client_ids.get(sid)
    if isinstance(client_id, str) and client_id:
        return client_id
    state = _simulations.get(sid)
    if isinstance(state, Mapping):
        state_client = state.get("ledger_client_id")
        if isinstance(state_client, str) and state_client:
            return state_client
    return sid


def _store_loaded_recipe_cost_pending(
    sid: str,
    pending: Mapping[str, object],
) -> None:
    stash_key = _cost_stash_key_for_sid(sid)
    with _simulations_guard:
        if _simulations.get(sid) is not None or sid in _socket_client_ids:
            _loaded_recipe_cost_parameters[stash_key] = copy.deepcopy(dict(pending))
            # Drop any legacy sid-keyed entry so a prior r3 stash cannot linger.
            if stash_key != sid:
                _loaded_recipe_cost_parameters.pop(sid, None)


def apply_loaded_recipe_patch_to_state(
    sid: str,
    patch: Mapping[str, object],
    *,
    cost_parameters: Mapping[str, object] | None = None,
    recipe_name: str | None = None,
    recipe_title: str | None = None,
) -> bool:
    normalized = normalize_recipe_patch(
        patch,
        source="recipes/load setpoints_patch",
    )
    pending: dict[str, object] = {
        "setpoints_patch": copy.deepcopy(normalized),
        "loaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if isinstance(recipe_name, str) and recipe_name.strip():
        pending["recipe_name"] = recipe_name.strip()
    if isinstance(recipe_title, str) and recipe_title.strip():
        pending["recipe_title"] = recipe_title.strip()
    if isinstance(cost_parameters, Mapping):
        pending["cost_parameters"] = copy.deepcopy(dict(cost_parameters))
    # Always replace any prior staged cost identity for this client — including
    # a no-cost recipe load, which clears the previous recipe's prices.
    _store_loaded_recipe_cost_pending(sid, pending)
    with _simulations_guard:
        state = _simulations.get(sid)
        lock = _sim_locks.get(sid)
    if state is None:
        # Stash may still have been written for a connected socket; live session
        # patch apply requires an existing simulation state.
        return False
    active_run = False
    cost_staged_for_next_run = False
    active_run_id = None
    if lock is None:
        state["loaded_setpoints_patch"] = normalized
        state["setpoints_patch"] = normalized
        active_run = bool(state.get("running"))
        cost_staged_for_next_run = active_run and isinstance(
            cost_parameters, Mapping
        )
        active_run_id = state.get("run_id")
        if not active_run:
            if isinstance(cost_parameters, Mapping):
                state["cost_parameters"] = copy.deepcopy(dict(cost_parameters))
            else:
                state.pop("cost_parameters", None)
    else:
        with lock:
            state["loaded_setpoints_patch"] = normalized
            state["setpoints_patch"] = normalized
            active_run = bool(state.get("running"))
            cost_staged_for_next_run = active_run and isinstance(
                cost_parameters, Mapping
            )
            active_run_id = state.get("run_id")
            if not active_run:
                if isinstance(cost_parameters, Mapping):
                    state["cost_parameters"] = copy.deepcopy(dict(cost_parameters))
                else:
                    state.pop("cost_parameters", None)
    if cost_staged_for_next_run and _registered_socketio is not None:
        stage_payload: dict[str, object] = {
            "status": "recipe_cost_parameters_staged",
            "notice_type": "cost_parameters_next_submission",
            "message": (
                "Loaded recipe cost parameters are staged for the next submission; "
                "the active run cost identity is unchanged."
            ),
            "run_id": active_run_id,
        }
        if "recipe_name" in pending:
            stage_payload["recipe_name"] = pending["recipe_name"]
        if "loaded_at" in pending:
            stage_payload["loaded_at"] = pending["loaded_at"]
        _registered_socketio.emit(
            "simulation_status",
            stage_payload,
            room=sid,
        )
    return True


def _discard_loaded_recipe_cost_parameters(
    sid: str,
) -> dict[str, object] | None:
    """Remove staged recipe cost identity without emitting a notice."""
    stash_key = _cost_stash_key_for_sid(sid)
    with _simulations_guard:
        removed = _loaded_recipe_cost_parameters.pop(stash_key, None)
        if stash_key != sid:
            legacy = _loaded_recipe_cost_parameters.pop(sid, None)
            if removed is None:
                removed = legacy
    if not isinstance(removed, Mapping):
        return None
    return dict(removed)


def clear_loaded_recipe_cost_parameters(sid: str) -> bool:
    """Explicitly clear this client's staged recipe cost identity.

    Visible operator action: next submission will not inherit loaded-recipe
    prices unless a new recipe is loaded.
    """
    removed = _discard_loaded_recipe_cost_parameters(sid)
    if removed is None:
        return False
    if _registered_socketio is not None:
        clear_payload: dict[str, object] = {
            "status": "recipe_cost_parameters_cleared",
            "notice_type": "cost_parameters_cleared",
            "message": (
                "Staged recipe cost parameters were cleared; the next submission "
                "will not inherit a loaded-recipe cost identity."
            ),
        }
        recipe_name = removed.get("recipe_name")
        if isinstance(recipe_name, str) and recipe_name:
            clear_payload["recipe_name"] = recipe_name
        loaded_at = removed.get("loaded_at")
        if isinstance(loaded_at, str) and loaded_at:
            clear_payload["loaded_at"] = loaded_at
        _registered_socketio.emit(
            "simulation_status",
            clear_payload,
            room=sid,
        )
    return True


def _loaded_recipe_cost_parameters_for_start(
    sid: str,
    setpoints_patch: Mapping[str, object] | None = None,
    *,
    consume: bool = True,
) -> tuple[bool, dict[str, object] | None, dict[str, object] | None]:
    """Resolve staged recipe cost identity for a submission.

    Bound to the client (ledger_client_id), not to an exact setpoints match —
    control edits must not silently drop the operator's loaded-recipe prices.

    Returns (found, cost_parameters_or_None, source_meta_or_None). found=True
    means a staged entry existed (and was consumed when consume=True). A found
    entry may still carry no cost_parameters when the loaded recipe had none.
    setpoints_patch is accepted for call-site compatibility and ignored.
    """
    del setpoints_patch  # no longer gates application; retained for callers
    stash_key = _cost_stash_key_for_sid(sid)
    with _simulations_guard:
        if consume:
            pending = _loaded_recipe_cost_parameters.pop(stash_key, None)
            if stash_key != sid:
                legacy = _loaded_recipe_cost_parameters.pop(sid, None)
                if pending is None:
                    pending = legacy
        else:
            pending = _loaded_recipe_cost_parameters.get(stash_key)
            if pending is None and stash_key != sid:
                pending = _loaded_recipe_cost_parameters.get(sid)
    if not isinstance(pending, Mapping):
        return False, None, None
    meta: dict[str, object] = {}
    for key in ("recipe_name", "recipe_title", "loaded_at"):
        value = pending.get(key)
        if isinstance(value, str) and value:
            meta[key] = value
    cost_parameters = pending.get("cost_parameters")
    if not isinstance(cost_parameters, Mapping):
        return True, None, meta or None
    return True, copy.deepcopy(dict(cost_parameters)), meta or None


def _cost_identity_applied_notice(
    cost_source_meta: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(cost_source_meta, Mapping) or not cost_source_meta:
        return None
    recipe_name = cost_source_meta.get("recipe_name")
    recipe_title = cost_source_meta.get("recipe_title")
    loaded_at = cost_source_meta.get("loaded_at")
    display_name = None
    if isinstance(recipe_name, str) and recipe_name:
        display_name = recipe_name
    elif isinstance(recipe_title, str) and recipe_title:
        display_name = recipe_title
    if display_name is not None and isinstance(loaded_at, str) and loaded_at:
        message = (
            f"Applying cost parameters from recipe {display_name} "
            f"loaded at {loaded_at}."
        )
    elif display_name is not None:
        message = f"Applying cost parameters from recipe {display_name}."
    elif isinstance(loaded_at, str) and loaded_at:
        message = (
            "Applying cost parameters from a previously loaded recipe "
            f"at {loaded_at}."
        )
    else:
        message = (
            "Applying cost parameters staged from a previously loaded recipe."
        )
    notice: dict[str, object] = {
        "status": "recipe_cost_parameters_applied",
        "notice_type": "cost_parameters_from_loaded_recipe",
        "message": message,
    }
    if display_name is not None:
        notice["recipe_name"] = display_name
    if isinstance(loaded_at, str) and loaded_at:
        notice["loaded_at"] = loaded_at
    return notice


def _record_last_recipe_capture(
    sid: str,
    run_id: str,
    *,
    tick_data: Mapping[str, object] | None = None,
    per_hour_summary: Mapping[str, object] | None = None,
    completion_payload: Mapping[str, object] | None = None,
) -> None:
    with _simulations_guard:
        state = _simulations.get(sid)
        if state is None or state.get("run_id") != run_id:
            return
        if tick_data is not None:
            state["last_recipe_capture"] = {
                "tick": copy.deepcopy(dict(tick_data)),
                "per_hour_summary": copy.deepcopy(dict(per_hour_summary or {})),
            }
        if completion_payload is not None:
            state["last_completion_payload"] = copy.deepcopy(
                dict(completion_payload)
            )


def _recipe_inputs_payload(
    *,
    feedstock_key: str,
    mass_kg: float,
    track: str,
    runtime_campaign_overrides: Mapping[str, Mapping[str, object]],
    c4_max_temp: float,
    furnace_max_T_C: object,
    c5_enabled: bool,
    mre_target_species: str,
    mre_max_voltage_V: float,
    additives_kg: Mapping[str, float],
    furnace_material_id: str,
) -> dict[str, object]:
    return {
        "feedstock": feedstock_key,
        "mass_kg": mass_kg,
        "track": track,
        "runtime_campaign_overrides": copy.deepcopy(
            dict(runtime_campaign_overrides)
        ),
        "c4_max_temp_C": c4_max_temp,
        "furnace_max_T_C": furnace_max_T_C,
        "c5_enabled": c5_enabled,
        "mre_target_species": mre_target_species,
        "mre_max_voltage_V": mre_max_voltage_V,
        "additives_kg": copy.deepcopy(dict(additives_kg)),
        "furnace_material_id": furnace_material_id,
    }


def _resolved_recipe_patch_for_session(
    *,
    setpoints_patch: Mapping[str, object],
    setpoints: Mapping[str, object],
    runtime_campaign_overrides: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    if setpoints_patch:
        return copy.deepcopy(dict(setpoints_patch))

    campaigns = setpoints.get("campaigns") if isinstance(setpoints, Mapping) else {}
    if not isinstance(campaigns, Mapping):
        return {}
    if "C2A_staged" not in runtime_campaign_overrides:
        return {}

    staged = campaigns.get("C2A_staged")
    if not isinstance(staged, Mapping):
        return {}
    candidate = {"campaigns": {"C2A_staged": copy.deepcopy(dict(staged))}}
    try:
        return normalize_recipe_patch(
            candidate,
            source="resolved C2A_staged session setpoints_patch",
        )
    except RecipeIOError:
        return {}


def _clear_simulation_state(sid: str, run_id: str | None = None) -> bool:
    """Stop and remove the current simulation, optionally by exact identity."""
    with _simulations_guard:
        state = _simulations.get(sid)
        if state is None or (
            run_id is not None and state.get('run_id') != run_id
        ):
            return False
        _simulations.pop(sid, None)
        if state is not None:
            state['running'] = False
        _sim_locks.pop(sid, None)
        return True


def _finish_terminal_state(
    sid: str,
    run_id: str,
    *,
    idempotency_result: Mapping[str, object] | None = None,
    defer_cleanup: bool = False,
) -> None:
    """Stop a terminal run and release synthetic HTTP session state."""
    terminal_result = None
    if idempotency_result is not None:
        terminal_result = {'run_id': run_id, **dict(idempotency_result)}
    with _run_idempotency_guard:
        with _simulations_guard:
            state = _simulations.get(sid)
            if state is None or state.get('run_id') != run_id:
                return
            state['running'] = False
            state['paused'] = False
            if defer_cleanup:
                state['terminal_emission_pending'] = True
            else:
                state.pop('terminal_emission_pending', None)
            if terminal_result is not None:
                state['idempotency_terminal_result'] = terminal_result
            if (
                not defer_cleanup
                and state.get('http_owned')
                and state.get('artifact_persisted')
            ):
                _simulations.pop(sid, None)
                _sim_locks.pop(sid, None)
        if terminal_result is not None:
            for key, (payload, result) in list(_run_idempotency.items()):
                if result.get('run_id') == run_id:
                    _run_idempotency[key] = (payload, dict(terminal_result))


def _persist_terminal(
    socketio,
    sid: str,
    run_id: str,
    session,
    *,
    status: str,
    lifecycle: str = 'complete',
    reason: str = '',
    error_message: str = '',
    refusal_diagnostic: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    state, _ = _current_simulation_state(sid, run_id)
    projector = state.get('runner_projector') if state is not None else None
    try:
        try:
            runner_payload = _full_runner_payload(
                session,
                projector=projector,
                status=status,
                reason=reason,
                error_message=error_message,
                refusal_diagnostic=refusal_diagnostic,
            )
        except Exception as projection_exc:  # noqa: BLE001
            _safe_log(f'Full runner projection unavailable: {projection_exc}')
            fallback_status = 'failed' if status == 'ok' else status
            runner_payload = _available_runner_payload(
                session,
                projector=projector,
                status=fallback_status,
                reason=(reason or 'runner_projection_failed'),
                error_message=(
                    error_message
                    or f'Runner projection failed: {projection_exc}'
                ),
                refusal_diagnostic=refusal_diagnostic,
            )
        effective_config = state.get('effective_config') if state is not None else None
        if effective_config:
            runner_payload['effective_config'] = copy.deepcopy(effective_config)
        cost_parameters = state.get('cost_parameters') if state is not None else None
        if isinstance(cost_parameters, Mapping):
            runner_payload['cost_parameters'] = copy.deepcopy(cost_parameters)
        per_hour_ledger = state.get('per_hour_ledger') if state is not None else None
        if per_hour_ledger:
            runner_payload['per_hour_ledger'] = copy.deepcopy(per_hour_ledger)
        run_store = state.get('run_store') if state is not None else None
        if run_store is None:
            raise RuntimeError('run artifact store is unavailable')
        if lifecycle == 'complete':
            artifact = persist_run_artifact(
                runner_payload,
                run_id,
                store=run_store,
            )
        else:
            artifact = build_run_artifact(
                runner_payload,
                run_id=run_id,
                lifecycle=lifecycle,
            )
            if not run_store.save(run_id, artifact):
                raise RuntimeError(f'run artifact {run_id!r} already exists')
    except Exception as exc:  # noqa: BLE001 -- durability is client-visible
        _safe_log(f'Run artifact persistence failed: {exc}')
        _finish_terminal_state(sid, run_id, defer_cleanup=True)
        try:
            _emit_if_current(
                socketio,
                sid,
                run_id,
                'simulation_persistence_failed',
                {
                    'status': 'persistence_failed',
                    'message': str(exc),
                },
            )
        except Exception as emit_exc:  # noqa: BLE001 -- log lost visibility
            _safe_log(f'Persistence failure emission failed: {emit_exc}')
        return None
    with _simulations_guard:
        current = _simulations.get(sid)
        if current is not None and current.get('run_id') == run_id:
            current['artifact_persisted'] = True
    return artifact


def _cancel_simulation_state(
    socketio,
    sid: str,
    *,
    reason: str,
    run_id: str | None = None,
) -> dict[str, object] | None:
    state, run_lock = _current_simulation_state(sid, run_id)
    if state is None or run_lock is None:
        return None
    target_run_id = str(state['run_id'])
    with run_lock:
        current, _ = _current_simulation_state(sid, target_run_id)
        if current is not state:
            return None
        if state.get('artifact_persisted'):
            _finish_terminal_state(sid, target_run_id)
            return {
                'run_id': target_run_id,
                'status': 'terminal',
                'cancelled': False,
            }
        execution_status = (
            'ok' if state['session'].is_complete() else 'partial'
        )
        artifact = _persist_terminal(
            socketio,
            sid,
            target_run_id,
            state['session'],
            status=execution_status,
            lifecycle='cancelled',
            reason=reason,
            error_message='',
        )
        if artifact is None:
            _finish_terminal_state(
                sid,
                target_run_id,
                idempotency_result={
                    'status': 'error',
                    'reason': 'persistence_failed',
                    'message': 'Run was cancelled but its report was not saved',
                },
            )
            raise RuntimeError('cancelled run artifact could not be persisted')
        _finish_terminal_state(sid, target_run_id)
        return {
            'run_id': target_run_id,
            'status': 'cancelled',
            'cancelled': True,
        }


def cancel_run_command(
    socketio,
    run_id: str,
    *,
    client_id: str,
) -> dict[str, object] | None:
    with _simulations_guard:
        sid = next(
            (
                candidate_sid
                for candidate_sid, state in _simulations.items()
                if (
                    state.get('run_id') == run_id
                    and state.get('ledger_client_id') == client_id
                )
            ),
            None,
        )
    if sid is None:
        return None
    return _cancel_simulation_state(
        socketio,
        sid,
        reason='cancelled_by_client',
        run_id=run_id,
    )


def _disconnect_simulation_client(socketio, sid: str) -> None:
    """Cancel only the run observed by this disconnect under client arbitration."""
    with _run_command_lock:
        client_id = _socket_client_ids.get(sid)
        state, _ = _current_simulation_state(sid)
        run_id = str(state['run_id']) if state is not None else None
        try:
            if run_id is not None:
                _cancel_simulation_state(
                    socketio,
                    sid,
                    reason='client_disconnected',
                    run_id=run_id,
                )
        except RuntimeError as exc:
            _safe_log(f'Disconnected run retained after persistence failure: {exc}')
        else:
            if run_id is not None:
                _clear_simulation_state(sid, run_id=run_id)
        finally:
            if _socket_client_ids.get(sid) == client_id:
                _socket_client_ids.pop(sid, None)
            # Client-bound staged costs survive disconnect/reconnect until
            # consumed, replaced by another recipe load, or explicitly cleared.
            # Drop only a legacy sid-keyed entry for this dead socket.
            with _simulations_guard:
                _loaded_recipe_cost_parameters.pop(sid, None)


def _ensure_global_run_capacity(replacement_sid: str | None) -> None:
    with _simulations_guard:
        active_sids = {
            candidate_sid
            for candidate_sid, state in _simulations.items()
            if state.get('running') and not state.get('artifact_persisted')
        }
    active_sids.discard(replacement_sid)
    if len(active_sids) >= _MAX_ACTIVE_RUNS:
        raise RunCommandError(
            'global active-run capacity is exhausted',
            error_type='global_run_capacity_exhausted',
            status_code=503,
        )


def _idempotency_entry_is_terminal(result: Mapping[str, object]) -> bool:
    if result.get('reason') == 'persistence_failed':
        return True
    run_id = result.get('run_id')
    with _simulations_guard:
        state = next(
            (
                candidate
                for candidate in _simulations.values()
                if candidate.get('run_id') == run_id
            ),
            None,
        )
        return state is None or bool(state.get('artifact_persisted'))


def _make_idempotency_capacity() -> None:
    with _run_idempotency_guard:
        while len(_run_idempotency) >= _MAX_RUN_IDEMPOTENCY_ENTRIES:
            terminal_key = next(
                (
                    key
                    for key, (_, result) in _run_idempotency.items()
                    if _idempotency_entry_is_terminal(result)
                ),
                None,
            )
            if terminal_key is None:
                # Launch-once records for nonterminal runs are never evicted. Reject
                # new tokenized work until a terminal record becomes evictable.
                raise RunCommandError(
                    'idempotency capacity is occupied by active runs',
                    error_type='idempotency_capacity_exhausted',
                    status_code=503,
                )
            _run_idempotency.pop(terminal_key)


def submit_run_command(
    socketio,
    payload: Mapping[str, object],
    *,
    client_id: str,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise RunCommandError(
            'run request body must be a JSON object',
            error_type='invalid_run_request',
        )
    request_payload = dict(payload)
    raw_token = request_payload.pop('client_token', None)
    token = ''
    if raw_token is not None:
        if not isinstance(raw_token, str) or not raw_token.strip():
            raise RunCommandError(
                'client_token must be a non-empty string',
                error_type='invalid_client_token',
            )
        token = raw_token.strip()
        if len(token) > 256:
            raise RunCommandError(
                'client_token must be at most 256 characters',
                error_type='invalid_client_token',
            )
    canonical_payload = json.dumps(
        request_payload,
        sort_keys=True,
        separators=(',', ':'),
    )
    token_key = (client_id, token)
    with _run_command_lock:
        if token:
            with _run_idempotency_guard:
                existing = _run_idempotency.get(token_key)
                if existing is not None:
                    existing = (existing[0], dict(existing[1]))
            if existing is not None:
                existing_payload, existing_result = existing
                if existing_payload != canonical_payload:
                    raise RunCommandError(
                        'client_token was already used with a different request',
                        error_type='idempotency_conflict',
                        status_code=409,
                    )
                return {**existing_result, 'idempotent_replay': True}
            _make_idempotency_capacity()
        handler = _registered_start_handler
        if handler is None:
            raise RuntimeError('run command handler is unavailable')
        with _simulations_guard:
            previous_sid = next(
                (
                    candidate_sid
                    for candidate_sid, state in _simulations.items()
                    if (
                        state.get('ledger_client_id') == client_id
                    )
                ),
                None,
            )
        sid = f'http:{client_id}:{uuid.uuid4().hex}'
        result = handler(
            request_payload,
            sid=sid,
            ledger_client_id=client_id,
            command_mode=True,
            replace_sid=previous_sid,
        )
        response_result = dict(result)
        if token:
            cached_result = dict(response_result)
            run_id = cached_result.get('run_id')
            with _run_idempotency_guard:
                with _simulations_guard:
                    state = next(
                        (
                            candidate
                            for candidate in _simulations.values()
                            if candidate.get('run_id') == run_id
                        ),
                        None,
                    )
                    if state is not None:
                        cached_result = dict(
                            state.get('idempotency_terminal_result')
                            or cached_result
                        )
                _run_idempotency[token_key] = (
                    canonical_payload,
                    cached_result,
                )
            response_result = cached_result
        return {**response_result, 'idempotent_replay': False}


def validate_run_draft(
    payload: Mapping[str, object],
    *,
    client_id: str,
) -> dict[str, object]:
    if not isinstance(payload, Mapping):
        raise RunCommandError(
            'run draft body must be a JSON object',
            error_type='invalid_run_request',
        )
    request_payload = dict(payload)
    request_payload.pop('client_token', None)
    handler = _registered_start_handler
    if handler is None:
        raise RuntimeError('run command handler is unavailable')
    if not _draft_validation_slots.acquire(blocking=False):
        raise RunCommandError(
            'run draft validation capacity is exhausted',
            error_type='draft_validation_capacity_exhausted',
            status_code=503,
        )
    try:
        return handler(
            request_payload,
            sid=f'draft:{client_id}:{uuid.uuid4().hex}',
            ledger_client_id=client_id,
            command_mode=True,
            draft_mode=True,
        )
    finally:
        _draft_validation_slots.release()


def _get_backend(backend_name: str):
    """
    Create and initialize the active melt backend.

    Eligibility policy (\\goal BACKEND-DEFAULT-SWITCH):

    * ``'alphamelts'`` — strict probe; raise ``BackendUnavailableError`` if
      PetThermoTools or the alphaMELTS binary is not reachable.
    * ``'vaporock'`` / ``'magemin'`` — explicitly **refused**.  Both
      adapters are not wired into a multi-intent dispatcher yet; selecting
      either as the active ``MeltBackend`` would fail closed inside
      ``simulator/core.py::_get_equilibrium`` (their populated
      ``phase_masses_kg`` + ``ledger_transition=None`` returns trip the
      "backend returned post-equilibrium phase material without an
      AtomLedger transition" reject).  Promotion is blocked on
      ``\\goal CHEMISTRY-KERNEL-CARVE-OUT``.
    * ``'internal-analytical'`` — deterministic
      ``InternalAnalyticalBackend`` selection. Legacy spellings are accepted
      on input and folded onto the 0.6 serialization token.
    * ``'auto'`` / unset — autodetect chain: probe
      AlphaMELTS first, falling back to ``InternalAnalyticalBackend`` as the
      always-available primary fallback.  No silent cross-backend
      fallback at runtime: if the selected primary throws inside
      ``_get_equilibrium`` after selection, ``core.py``'s fail-closed
      path handles it without re-routing here.
    * unknown explicit names — raise ``BackendUnavailableError`` instead
      of silently coercing to ``auto``.
    """
    return resolve_backend(
        backend_name,
        BackendSelectionPolicy.WEB_AUTODETECT,
        unavailable_error_cls=BackendUnavailableError,
        log_selection=lambda backend: emit_web_engine_selection_log(
            backend, _safe_log
        ),
        log_message=_safe_log,
        alphamelts_backend_cls=AlphaMELTSBackend,
        internal_analytical_backend_cls=InternalAnalyticalBackend,
    )


def _backend_name_for_session(backend) -> str:
    """Map the web-selected backend instance to SimSession strict name."""
    backend_type = type(backend).__name__
    if isinstance(backend, AlphaMELTSBackend) or backend_type == 'AlphaMELTSBackend':
        return 'alphamelts'
    return ANALYTICAL_BACKEND_SERIALIZATION_TOKEN


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    raise InputValidationError('c5_enabled must be a boolean')


def _coerce_bounded_float(
    value,
    *,
    field: str,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if value is None or value == '':
        if default is None:
            raise InputValidationError(f'{field} is required')
        value = default
    if isinstance(value, bool):
        raise InputValidationError(f'{field} must be numeric')
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise InputValidationError(f'{field} must be numeric') from None
    if not math.isfinite(numeric):
        raise InputValidationError(f'{field} must be finite')
    if minimum is not None:
        below = numeric <= minimum if exclusive_minimum else numeric < minimum
        if below:
            operator = '>' if exclusive_minimum else '>='
            raise InputValidationError(f'{field} must be {operator} {minimum:g}')
    if maximum is not None and numeric > maximum:
        raise InputValidationError(f'{field} must be <= {maximum:g}')
    return numeric


def _required_setpoint_float(value, *, field: str) -> float:
    if isinstance(value, bool):
        raise InputValidationError(f'setpoints.yaml {field} must be numeric')
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise InputValidationError(
            f'setpoints.yaml {field} must be numeric'
        ) from None
    if not math.isfinite(numeric):
        raise InputValidationError(f'setpoints.yaml {field} must be finite')
    return numeric


def _c4_setpoint_ceiling_T_C(
    setpoints: Mapping[str, object] | None = None,
) -> float:
    if setpoints is None:
        setpoints = _load_yaml('setpoints.yaml')
    if not isinstance(setpoints, Mapping):
        raise InputValidationError('setpoints.yaml must be a mapping')
    campaigns = setpoints.get('campaigns')
    if not isinstance(campaigns, Mapping):
        raise InputValidationError('setpoints.yaml campaigns must be a mapping')
    c4 = campaigns.get('C4')
    if not isinstance(c4, Mapping):
        raise InputValidationError('setpoints.yaml campaigns.C4 must be a mapping')
    temp_range = c4.get('temp_range_C')
    if not isinstance(temp_range, (list, tuple)) or len(temp_range) < 2:
        raise InputValidationError(
            'setpoints.yaml campaigns.C4.temp_range_C must contain at least two values'
        )
    return max(
        _required_setpoint_float(
            item,
            field=f'campaigns.C4.temp_range_C[{index}]',
        )
        for index, item in enumerate(temp_range)
    )


def _coerce_additives_kg(value) -> dict[str, float]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InputValidationError('additives must be an object')
    additives_kg: dict[str, float] = {}
    for key, raw_amount in value.items():
        amount = _coerce_bounded_float(
            raw_amount,
            field=f'additives.{key}',
            minimum=0.0,
            maximum=_MAX_ADDITIVE_KG,
        )
        if amount > 0.0:
            additives_kg[str(key)] = amount
    return additives_kg


def _coerce_runtime_campaign_overrides(value) -> dict[str, dict[str, float]]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InputValidationError('runtime_campaign_overrides must be an object')
    overrides: dict[str, dict[str, float]] = {}
    for campaign, fields in value.items():
        campaign_name = str(campaign).strip()
        if not campaign_name:
            raise InputValidationError(
                'runtime_campaign_overrides campaign names must be non-empty'
            )
        if not isinstance(fields, Mapping):
            raise InputValidationError(
                f'runtime_campaign_overrides.{campaign_name} must be an object'
            )
        clean_fields: dict[str, float] = {}
        for field_name, field_value in fields.items():
            field_key = str(field_name).strip()
            if not field_key:
                raise InputValidationError(
                    f'runtime_campaign_overrides.{campaign_name} '
                    'field names must be non-empty'
                )
            clean_value = _coerce_bounded_float(
                field_value,
                field=f'runtime_campaign_overrides.{campaign_name}.{field_key}',
            )
            if field_key == 'pO2_mbar' and clean_value < 0.0:
                raise InputValidationError(
                    f'runtime_campaign_overrides.{campaign_name}.{field_key} '
                    'must be >= 0'
                )
            clean_fields[field_key] = clean_value
        if clean_fields:
            overrides[campaign_name] = clean_fields
    try:
        CampaignManager.validate_runtime_campaign_overrides(overrides)
    except ValueError as exc:
        raise InputValidationError(str(exc)) from exc
    return overrides


def _decision_payload(decision):
    return {
        'type': decision.decision_type.name,
        'options': list(decision.options),
        'recommendation': decision.recommendation,
        'context': decision.context,
    }


def _rounded_positive_species(values, digits: int) -> dict[str, float]:
    species = {}
    for key, value in dict(values or {}).items():
        amount = float(value or 0.0)
        if amount > 1e-12:
            species[str(key)] = round(amount, digits)
    return dict(sorted(species.items()))


def _wt_pct_from_kg(species_kg: dict[str, float]) -> dict[str, float]:
    total_kg = sum(float(value or 0.0) for value in species_kg.values())
    if total_kg <= 0.0:
        return {}
    return {
        species: round(float(kg) / total_kg * 100.0, 2)
        for species, kg in sorted(species_kg.items())
        if float(kg or 0.0) > 1e-12
    }


def _pot_composition_kg(sim, snapshot) -> dict[str, float]:
    ledger = getattr(sim, 'atom_ledger', None)
    if ledger is None:
        return {}
    try:
        return _rounded_positive_species(
            ledger.kg_by_account('process.cleaned_melt'), 4)
    except Exception as exc:
        _safe_log(f'Unable to read cleaned-melt ledger for web tick: {exc}')
        return {}


def _flue_composition_kg_hr(snapshot) -> dict[str, float]:
    flue = dict(getattr(snapshot.evap_flux, 'species_kg_hr', {}) or {})
    o2_kg_hr = max(
        0.0,
        float(getattr(snapshot, 'melt_offgas_O2_mol_hr', 0.0) or 0.0)
        * _O2_KG_PER_MOL,
    )
    if o2_kg_hr > 1e-12:
        flue['O2'] = flue.get('O2', 0.0) + o2_kg_hr
    return _rounded_positive_species(flue, 6)


def _mass_balance_error_fields(snapshot) -> dict[str, object]:
    error_pct = getattr(snapshot, 'mass_balance_error_pct', None)
    category = getattr(snapshot, 'mass_balance_error_category', None)
    if error_pct is None:
        return {
            'mass_balance_error_pct': None,
            'mass_balance_error_category': category or 'undefined',
            'mass_balance_error_breached': bool(category),
        }
    error_pct = float(error_pct)
    if not math.isfinite(error_pct):
        return {
            'mass_balance_error_pct': None,
            'mass_balance_error_category': (
                category or 'non_finite_mass_balance_error'
            ),
            'mass_balance_error_breached': True,
        }
    return {
        'mass_balance_error_pct': error_pct,
        'mass_balance_error_category': category,
        'mass_balance_error_breached': (
            bool(category) or abs(error_pct) > _MASS_BALANCE_ERROR_BREACH_PCT
        ),
    }


def _start_payload(
    *,
    sim,
    feedstock_key: str,
    mass_kg: float,
    backend_requested: str,
    backend_active: str,
    backend_status: str,
    backend_authoritative: bool,
    backend_message: str,
    backend_payload: Mapping[str, object] | None = None,
    c5_enabled: bool = False,
    mre_target_species: str = '',
    mre_max_voltage_V: float = 0.0,
):
    """Build the public start status payload."""
    payload = {
        'status': 'started',
        'feedstock': feedstock_key,
        'mass_kg': mass_kg,
        'backend_message': backend_message,
        'c5_enabled': bool(c5_enabled),
        'mre_target_species': str(mre_target_species or ''),
        'mre_max_voltage_V': float(mre_max_voltage_V or 0.0),
    }
    if backend_payload is None:
        backend_payload = {
            'backend_requested': backend_requested,
            'backend_active': backend_active,
            'backend_status': backend_status,
            'backend_authoritative': backend_authoritative,
        }
    payload.update(dict(backend_payload))
    return payload


def _tick_payload(
    *,
    sim,
    snapshot,
    backend_message: str,
    backend_status: str,
    backend_authoritative: bool,
    backend_error: str = '',
):
    """Build the public per-tick payload."""
    pot_composition = _pot_composition_kg(sim, snapshot)
    flue_composition = _flue_composition_kg_hr(snapshot)
    active_wall_species = active_wall_species_from_flue(flue_composition)
    return {
        'hour': snapshot.hour,
        'campaign': snapshot.campaign.name,
        'temperature_C': round(snapshot.temperature_C, 1),
        'melt_mass_kg': round(snapshot.melt_mass_kg, 1),
        'pot_composition': pot_composition,
        'pot_composition_units': 'kg',
        'pot_composition_wt_pct': _wt_pct_from_kg(pot_composition),
        'composition_wt_pct': {
            k: round(v, 2) for k, v in snapshot.composition_wt_pct.items()
        },
        'raw_inventory_kg': {
            k: round(v, 3)
            for k, v in snapshot.inventory.raw_components_kg.items()
        },
        'residual_inventory_kg': {
            k: round(v, 3)
            for k, v in snapshot.inventory.residual_components_kg.items()
        },
        'stage0_products_kg': {
            k: round(v, 3)
            for k, v in snapshot.inventory.stage0_products_kg.items()
        },
        'drain_tap_kg': {
            k: round(v, 3)
            for k, v in snapshot.inventory.drain_tap_kg.items()
        },
        'stage0_profile': snapshot.inventory.stage0_profile,
        'stage0_temp_range_C': [
            round(v, 1)
            for v in snapshot.inventory.stage0_temp_range_C
        ],
        'cleaned_melt_source': snapshot.inventory.cleaned_melt_source,
        'carbon_reductant_required_kg': round(
            snapshot.inventory.carbon_reductant_required_kg, 3),
        'stage0_mass_balance_delta_kg': round(
            snapshot.inventory.stage0_mass_balance_delta_kg, 3),
        'backend_status': backend_status,
        'backend_authoritative': backend_authoritative,
        'backend_error': backend_error,
        'backend_fallback_active': bool(backend_error),
        'backend_message': (
            'Built-in fallback active: '
            f'{backend_error}'
            if backend_error
            else backend_message),
        'process_buckets_kg': {
            'gas_volatiles': {
                k: round(v, 3)
                for k, v in snapshot.inventory.gas_volatiles_kg.items()
            },
            'salt_phase': {
                k: round(v, 3)
                for k, v in snapshot.inventory.salt_phase_kg.items()
            },
            'chloride_salt_phase': {
                k: round(v, 3)
                for k, v in snapshot.inventory.chloride_salt_phase_kg.items()
            },
            'sulfide_matte': {
                k: round(v, 3)
                for k, v in snapshot.inventory.sulfide_matte_kg.items()
            },
            'metal_alloy': {
                k: round(v, 3)
                for k, v in snapshot.inventory.metal_alloy_kg.items()
            },
            'terminal_slag': {
                k: round(v, 3)
                for k, v in (
                    snapshot.inventory
                    .terminal_slag_components_kg.items())
            },
        },
        'process_bucket_metadata': {
            'chloride_salt_phase': {
                'disposition': 'separated_chloride_salt_fouling_risk',
            },
        },
        'evap_total_kg_hr': round(snapshot.evap_flux.total_kg_hr, 4),
        'evap_species': {
            k: round(v, 4) for k, v in snapshot.evap_flux.species_kg_hr.items()
        },
        'flue_composition': flue_composition,
        'flue_composition_units': 'kg/hr',
        'flue_partial_pressure_mbar': _rounded_positive_species(
            getattr(snapshot.overhead, 'composition', {}) or {}, 6),
        'flue_composition_note': (
            '' if flue_composition else
            'No gas-offtake species flow reported for this tick; '
            'partial pressures are surfaced separately when available.'
        ),
        'wall_risk_panel': wall_advisory_payload(
            active_wall_species,
            pO2_mbar=max(sim.melt.pO2_mbar, 0.0),
            # CO2/N2/Ar are all generic buffer gas; the non-O2 overhead
            # pressure is what sets the transport (Knudsen) regime.
            p_buffer_mbar=max(sim.melt.p_total_mbar - sim.melt.pO2_mbar, 0.0),
        ),
        'vapor_pressure_authority_panel': vapor_pressure_authority_payload(
            getattr(sim, '_last_backend_diagnostics', {}) or {}
        ),
        'overlap_evaporation': (
            getattr(sim, '_last_overlap_evaporation_diagnostic', {}) or {}
        ),
        'pressure_mbar': round(snapshot.overhead.pressure_mbar, 3),
        'atmosphere': sim.melt.atmosphere.name,
        'p_total_mbar': round(sim.melt.p_total_mbar, 3),
        'pO2_mbar': round(sim.melt.pO2_mbar, 6),
        'ambient_pressure_mbar': round(
            sim.melt.ambient_pressure_mbar, 3),
        'ambient_atmosphere': sim.melt.ambient_atmosphere,
        'condensation': {
            k: round(v, 3) for k, v in snapshot.condensation_totals.items()
        },
        'stage_purity_report': stage_purity_report(sim.train),
        'energy_electrical_plus_evaporation_kWh': round(
            snapshot.energy.electrical_plus_evaporation_kWh, 4),
        'energy_electrical_kWh': round(snapshot.energy.electrical_total_kWh, 4),
        'energy_evaporation_thermal_kWh': round(
            snapshot.energy.evaporation_thermal_kWh, 4),
        'energy_scope': snapshot.energy.energy_scope,
        'furnace_heat_status': snapshot.energy.furnace_heat_status,
        'energy_latent_kWh': round(snapshot.energy.latent_kWh, 4),
        'energy_dissociation_kWh': round(snapshot.energy.dissociation_kWh, 4),
        'energy_evaporation_breakdown_kWh': {
            key: round(value, 4)
            for key, value in snapshot.energy.evaporation_breakdown_kWh.items()
        },
        'energy_electrical_plus_evaporation_cumulative_kWh': round(
            snapshot.energy_electrical_plus_evaporation_cumulative_kWh, 2),
        'energy_cumulative_breakdown_kWh': {
            key: round(value, 4)
            for key, value in snapshot.energy_cumulative_breakdown_kWh.items()
        },
        'oxygen_kg': round(snapshot.oxygen_produced_kg, 2),
        **_mass_balance_error_fields(snapshot),
        'ramp_throttled': snapshot.ramp_throttled,
        'nominal_ramp_rate': round(snapshot.nominal_ramp_rate_C_hr, 2),
        'actual_ramp_rate': round(snapshot.actual_ramp_rate_C_hr, 2),
        'throttle_reason': snapshot.throttle_reason,
        'O2_vented_kg_hr': round(snapshot.O2_vented_kg_hr, 4),
        'O2_vented_mol_hr': round(snapshot.O2_vented_mol_hr, 4),
        'O2_vented_cumulative_kg': round(
            snapshot.O2_vented_cumulative_kg, 2),
        'O2_stored_kg': round(snapshot.O2_stored_kg, 2),
        'stage0_O2_stored_kg': round(snapshot.stage0_O2_stored_kg, 2),
        'melt_offgas_O2_stored_kg': round(
            snapshot.melt_offgas_O2_stored_kg, 2),
        'melt_offgas_O2_vented_kg': round(
            snapshot.melt_offgas_O2_vented_kg, 2),
        'mre_anode_O2_stored_kg': round(snapshot.mre_anode_O2_stored_kg, 2),
        'melt_offgas_O2_mol_hr': round(snapshot.melt_offgas_O2_mol_hr, 4),
        'mre_anode_O2_mol_hr': round(snapshot.mre_anode_O2_mol_hr, 4),
        'turbine_limited': snapshot.overhead.turbine_limited,
        'turbine_utilization_pct': round(
            snapshot.overhead.turbine_utilization_pct, 1),
        'transport_saturation_pct': round(
            snapshot.overhead.transport_saturation_pct, 1),
        'turbine_shaft_power_kW': round(snapshot.turbine_shaft_power_kW, 4),
        'shuttle_phase': snapshot.shuttle_phase,
        'shuttle_injected_kg_hr': round(snapshot.shuttle_injected_kg_hr, 3),
        'shuttle_reduced_kg_hr': round(snapshot.shuttle_reduced_kg_hr, 3),
        'shuttle_metal_produced_kg_hr': round(
            snapshot.shuttle_metal_produced_kg_hr, 3),
        'shuttle_K_inventory_kg': round(snapshot.shuttle_K_inventory_kg, 2),
        'shuttle_Na_inventory_kg': round(snapshot.shuttle_Na_inventory_kg, 2),
        'shuttle_cycle': snapshot.shuttle_cycle,
        'mre_voltage_V': round(snapshot.mre_voltage_V, 3),
        'mre_current_A': round(snapshot.mre_current_A, 1),
        'mre_metals_kg_hr': {
            k: round(v, 4) for k, v in snapshot.mre_metals_kg_hr.items()
        },
        'mre_energy_kWh': round(snapshot.energy.mre_kWh, 4),
    }


def _completion_payload(sim):
    final_snapshot = sim._make_snapshot()
    terminal_rump_by_species = sim._terminal_rump_by_species()
    spent_reductant_by_species = sim._spent_reductant_residue_by_species()
    terminal_rump_composition_wt_pct = oxide_wt_pct_from_kg(
        terminal_rump_by_species
    )
    return {
        'total_hours': sim.melt.hour,
        'energy_electrical_plus_evaporation_kWh': (
            sim.energy_electrical_plus_evaporation_cumulative_kWh
        ),
        'energy_scope': 'electrical_plus_known_evaporation_enthalpy',
        'furnace_heat_status': 'partial',
        'energy_breakdown_kWh': dict(
            getattr(sim, 'energy_cumulative_breakdown_kWh', {}) or {}),
        'oxygen_kg': sim._oxygen_total_kg(),
        'oxygen_stored_kg': sim._oxygen_stored_kg(),
        'oxygen_vented_kg': sim._oxygen_vented_kg(),
        'mass_in_kg': round(final_snapshot.mass_in_kg, 3),
        'mass_out_kg': round(final_snapshot.mass_out_kg, 3),
        **_mass_balance_error_fields(final_snapshot),
        'residual_inventory_kg': {
            k: round(v, 3)
            for k, v in (
                final_snapshot.inventory.residual_components_kg.items())
        },
        'stage0_mass_balance_delta_kg': round(
            final_snapshot.inventory.stage0_mass_balance_delta_kg, 3),
        'products': {k: round(v, 2)
                     for k, v in sim.product_ledger().items()},
        'terminal_slag_kg': round(sim._terminal_slag_kg(), 2),
        'terminal_rump_kg': sim._terminal_slag_kg(),
        'terminal_rump_by_species': terminal_rump_by_species,
        'process_inventory_spent_reductant': {
            'kg_by_species': spent_reductant_by_species,
            'class_total_kg': sum(spent_reductant_by_species.values()),
            'account': 'process.spent_reductant_residue',
            'disposition': 'process_inventory_spent_reductant',
        },
        'terminal_residual_buckets': sim._terminal_residual_buckets(),
        'terminal_rump_composition_wt_pct': terminal_rump_composition_wt_pct,
        'terminal_rump_by_class': sim._terminal_rump_by_class(),
        'ceramic_rump_panel': ceramic_rump_payload(
            terminal_rump_composition_wt_pct
        ),
        'vapor_pressure_authority_panel': vapor_pressure_authority_payload(
            getattr(sim, '_last_backend_diagnostics', {}) or {}
        ),
        'stage_purity_report': stage_purity_report(sim.train),
        'knudsen_regime_diagnostic': _knudsen_regime_diagnostic_from_sim(sim),
    }


def _knudsen_regime_diagnostic_from_sim(sim):
    condensation_model = getattr(sim, '_condensation_model', None)
    if condensation_model is None:
        return {}
    diagnostic = getattr(
        condensation_model, 'last_knudsen_regime_diagnostic', {}) or {}
    return dict(diagnostic) if isinstance(diagnostic, dict) else {}


def _mapping_leaf_paths(
    value: Mapping[str, object],
    prefix: tuple[str, ...] = (),
) -> list[str]:
    paths: list[str] = []
    for key, child in value.items():
        child_path = (*prefix, str(key))
        if isinstance(child, Mapping):
            paths.extend(_mapping_leaf_paths(child, child_path))
        else:
            paths.append('.'.join(child_path))
    return paths


def _effective_config_from_setpoints(
    setpoints: Mapping[str, object],
    *,
    override_paths: set[str],
) -> dict[str, dict[str, object]]:
    effective_config: dict[str, dict[str, object]] = {}

    def contains_absent(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, Mapping):
            return any(contains_absent(child) for child in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_absent(child) for child in value)
        return False

    def capture(value: object, prefix: tuple[str, ...]) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                capture(child, (*prefix, str(key)))
            return
        if not prefix or contains_absent(value):
            return
        path = '.'.join(prefix)
        effective_config[path] = {
            'value': copy.deepcopy(value),
            'source': 'override' if path in override_paths else 'default',
        }

    capture(setpoints, ())
    return effective_config


def _recipe_snapshot_from_projector(projector) -> dict[str, object] | None:
    if projector is None:
        return None
    setpoints_patch = getattr(projector, 'setpoints_patch', None)
    if not isinstance(setpoints_patch, Mapping):
        return None
    return {
        'setpoints_patch': copy.deepcopy(dict(setpoints_patch)),
        'pins': sorted(_mapping_leaf_paths(setpoints_patch)),
        'recipe_schema_version': recipe_schema_version,
    }


def _full_runner_payload(
    session,
    *,
    projector=None,
    status: str,
    reason: str = '',
    error_message: str = '',
    refusal_diagnostic: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Project a web-driven session through the canonical runner envelope."""
    result_document = getattr(session, 'result_document', None)
    try:
        recorded = result_document() if callable(result_document) else None
    except RuntimeError:
        recorded = None
    if isinstance(recorded, Mapping):
        payload = copy.deepcopy(dict(recorded))
        recorded_status = payload.get('status')
        if recorded_status != status:
            _safe_log(
                'Recorded terminal outcome conflicts with observed outcome: '
                f'recorded={recorded_status!r}, observed={status!r}; '
                'using observed outcome'
            )
        payload['status'] = status
        payload['reason'] = reason
        payload['error_message'] = error_message
        if refusal_diagnostic is not None:
            payload['refusal_diagnostic'] = copy.deepcopy(
                dict(refusal_diagnostic)
            )
        else:
            payload.pop('refusal_diagnostic', None)
        recipe_snapshot = _recipe_snapshot_from_projector(projector)
        if recipe_snapshot is not None:
            payload['recipe_snapshot'] = recipe_snapshot
        return payload

    sim = session.simulator
    config = session._config
    if config is None:
        raise RuntimeError('web session has no runner configuration')
    execution = RunExecutor().execute_session(session, hours=0)
    execution = replace(
        execution,
        snapshots=tuple(getattr(sim.record, 'snapshots', ()) or ()),
        trace=PhysicsTrace.from_simulator(sim),
        per_hour=tuple(session.per_hour_summaries()),
        operator_decisions=tuple(session.operator_decisions()),
        status=status,
        reason=reason,
        error_message=error_message,
        refusal_diagnostic=dict(refusal_diagnostic or {}),
    )
    if projector is None:
        raise RuntimeError('web session has no canonical runner projector')
    active_projector = copy.copy(projector)
    active_projector.hours = max(
        int(config.hours),
        int(getattr(sim.melt, 'hour', 0)),
    )
    payload = active_projector._build_output(execution)
    recipe_snapshot = _recipe_snapshot_from_projector(active_projector)
    if recipe_snapshot is not None:
        payload['recipe_snapshot'] = recipe_snapshot
    return payload


def _available_runner_payload(
    session,
    *,
    projector=None,
    status: str,
    reason: str,
    error_message: str,
    refusal_diagnostic: Mapping[str, object] | None,
) -> dict[str, object]:
    """Return an honest reduced envelope when canonical projection is unavailable."""
    config = getattr(session, '_config', None)
    sim = getattr(session, 'simulator', None)
    metadata: dict[str, object] = {}
    if config is not None:
        metadata = {
            'feedstock_id': config.feedstock_id,
            'mass_kg': config.mass_kg,
            'backend': config.backend_name,
            'track': config.track,
        }
    metadata_overrides = getattr(projector, 'run_metadata_overrides', None)
    if isinstance(metadata_overrides, Mapping):
        started_at_utc = metadata_overrides.get('started_at_utc')
        if started_at_utc:
            metadata['started_at_utc'] = copy.deepcopy(started_at_utc)
    melt = getattr(sim, 'melt', None) if sim is not None else None
    if melt is not None:
        metadata['hours_completed'] = int(getattr(melt, 'hour', 0))
    if refusal_diagnostic:
        metadata['refusal_diagnostic'] = copy.deepcopy(
            dict(refusal_diagnostic)
        )
    summaries_builder = getattr(session, 'per_hour_summaries', None)
    summaries = summaries_builder() if callable(summaries_builder) else []
    payload = {
        'schema_version': 'web-reduced-terminal-v1',
        'run_metadata': metadata,
        'per_hour_summary': copy.deepcopy(list(summaries)),
        'status': status,
        'reason': reason,
        'error_message': error_message,
    }
    recipe_snapshot = _recipe_snapshot_from_projector(projector)
    if recipe_snapshot is not None:
        payload['recipe_snapshot'] = recipe_snapshot
    return payload


def _start_background_loop(
    socketio,
    sid: str,
    run_id: str,
    run_lock,
    backend_message: str,
    backend_status: str,
    backend_authoritative: bool,
):
    def persist_terminal(
        session,
        *,
        status: str,
        reason: str = '',
        error_message: str = '',
        refusal_diagnostic: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        return _persist_terminal(
            socketio,
            sid,
            run_id,
            session,
            status=status,
            reason=reason,
            error_message=error_message,
            refusal_diagnostic=refusal_diagnostic,
        )

    def stop_with_status(payload: Mapping[str, object]) -> None:
        _finish_terminal_state(
            sid,
            run_id,
            idempotency_result=(
                payload
                if payload.get('reason') == 'persistence_failed'
                else None
            ),
            defer_cleanup=True,
        )
        try:
            _emit_if_current(
                socketio,
                sid,
                run_id,
                'simulation_status',
                payload,
            )
        except Exception as exc:  # noqa: BLE001 -- cleanup must still run
            _safe_log(f'Simulation status emission failed: {exc}')
        finally:
            _finish_terminal_state(
                sid,
                run_id,
                idempotency_result=(
                    payload
                    if payload.get('reason') == 'persistence_failed'
                    else None
                ),
            )

    def stop_for_failure(exc: Exception, session, sim) -> None:
        _safe_log(f'Simulation loop failed: {exc}')
        message = str(exc)
        unenriched_message = message
        poisoned = None
        try:
            poisoned = getattr(sim, '_poisoned_hour', None)
            if poisoned is not None:
                poisoned_error = PoisonedHourError(poisoned)
                poisoned_detail = f'PoisonedHourError: {poisoned_error}'
                if message == str(poisoned_error):
                    message = poisoned_detail
                elif message != poisoned_detail:
                    message = f'{message}; {poisoned_detail}'
        except Exception:  # noqa: BLE001 -- best-effort enrichment
            message = unenriched_message
            poisoned = None
        error_payload = {
            'status': 'error',
            'message': message,
            'backend_status': backend_status,
            'backend_authoritative': backend_authoritative,
            'backend_message': backend_message,
        }
        if poisoned is not None:
            error_payload['reason'] = 'poisoned_hour'
        with run_lock:
            current, _ = _current_simulation_state(sid, run_id)
            if (
                current is None
                or not current['running']
                or current.get('artifact_persisted')
            ):
                return
            artifact = persist_terminal(
                session,
                status='failed',
                reason=str(error_payload.get('reason') or ''),
                error_message=message,
            )
            if artifact is None:
                stop_with_status({
                    'status': 'error',
                    'reason': 'persistence_failed',
                    'message': 'Run failed but its report was not saved',
                })
                return
            stop_with_status(error_payload)

    def run_loop():
        while True:
            state, _ = _current_simulation_state(sid, run_id)
            if (
                state is None
                or not state['running']
            ):
                break
            if state['paused']:
                socketio.sleep(0.1)
                continue

            step_result = None
            decision_payload = None
            tick_data = None
            with run_lock:
                state, _ = _current_simulation_state(sid, run_id)
                if (
                    state is None
                    or not state['running']
                ):
                    break
                if state['paused']:
                    continue
                session = state['session']
                sim = session.simulator
                if session.is_complete():
                    try:
                        completion_payload = _completion_payload(sim)
                        _record_last_recipe_capture(
                            sid,
                            run_id,
                            completion_payload=completion_payload,
                        )
                        artifact = persist_terminal(session, status='ok')
                        if artifact is None:
                            stop_with_status({
                                'status': 'error',
                                'reason': 'persistence_failed',
                                'message': 'Run completed but its report was not saved',
                            })
                            break
                        if artifact.get('execution_status') != 'ok':
                            stop_with_status({
                                'status': 'error',
                                'reason': 'terminal_run_failed',
                                'message': 'Run finished with a failed terminal result',
                            })
                            break
                    except Exception as exc:  # noqa: BLE001 -- loop boundary
                        stop_for_failure(exc, session, sim)
                        break
                    try:
                        _finish_terminal_state(
                            sid,
                            run_id,
                            defer_cleanup=True,
                        )
                        emitted = _emit_if_current(
                            socketio,
                            sid,
                            run_id,
                            'simulation_complete',
                            completion_payload,
                        )
                    except Exception as exc:  # noqa: BLE001 -- transport boundary
                        _safe_log(f'Simulation completion emission failed: {exc}')
                        stop_with_status({
                            'status': 'error',
                            'reason': 'completion_emit_failed',
                            'message': str(exc),
                        })
                        break
                    if not emitted:
                        _finish_terminal_state(sid, run_id)
                        break
                    _finish_terminal_state(sid, run_id)
                    break

                try:
                    for step_result in drive_session(
                        session,
                        1,
                        DecisionPolicy.OPERATOR,
                    ):
                        break
                    if step_result is None:
                        decision = session.pending_decision()
                        if decision is not None:
                            decision_payload = _decision_payload(decision)
                    elif isinstance(step_result.per_hour_summary, Mapping):
                        hour = step_result.per_hour_summary.get('hour')
                        ledger = getattr(sim, 'atom_ledger', None)
                        mol_by_account = getattr(ledger, 'mol_by_account', None)
                        if hour is not None and callable(mol_by_account):
                            state['per_hour_ledger'][str(hour)] = copy.deepcopy(
                                mol_by_account()
                            )
                    if step_result is not None:
                        tick_data = _tick_payload(
                            sim=sim,
                            snapshot=step_result.snapshot,
                            backend_message=backend_message,
                            backend_status=backend_status,
                            backend_authoritative=backend_authoritative,
                            backend_error=step_result.backend_error,
                        )
                        _record_last_recipe_capture(
                            sid,
                            run_id,
                            tick_data=tick_data,
                            per_hour_summary=step_result.per_hour_summary,
                        )
                except KnudsenRegimeRefusal as exc:
                    _safe_log(f'Simulation refused: {exc.reason}')
                    error_payload = {
                        'status': 'refused',
                        'reason': exc.reason,
                        'message': exc.reason,
                        'knudsen_regime_diagnostic': dict(exc.diagnostic),
                        'backend_status': backend_status,
                        'backend_authoritative': backend_authoritative,
                        'backend_message': backend_message,
                    }
                    artifact = persist_terminal(
                        session,
                        status='refused',
                        reason=exc.reason,
                        error_message=exc.reason,
                        refusal_diagnostic=exc.diagnostic,
                    )
                    if artifact is None:
                        stop_with_status({
                            'status': 'error',
                            'reason': 'persistence_failed',
                            'message': 'Run was refused but its report was not saved',
                        })
                        break
                    stop_with_status(error_payload)
                    break
                except Exception as exc:
                    stop_for_failure(exc, session, sim)
                    break

            if step_result is None:
                if decision_payload is None:
                    break
                with _simulations_guard:
                    current = _simulations.get(sid)
                    if (
                        current is None
                        or current.get('run_id') != run_id
                        or not current['running']
                    ):
                        break
                    current['paused'] = True
                _emit_if_current(
                    socketio, sid, run_id, 'decision_required', decision_payload
                )
                break

            sim = session.simulator
            try:
                if not _emit_if_current(
                    socketio, sid, run_id, 'simulation_tick', tick_data
                ):
                    break

                if not _emit_if_current(
                    socketio,
                    sid,
                    run_id,
                    'per_hour_summary',
                    step_result.per_hour_summary,
                ):
                    break

                if step_result.campaign_summary is not None:
                    if not _emit_if_current(
                        socketio,
                        sid,
                        run_id,
                        'campaign_complete_summary',
                        step_result.campaign_summary,
                    ):
                        break
            except Exception as exc:  # noqa: BLE001 -- loop boundary
                stop_for_failure(exc, session, sim)
                break

            campaign_summary = step_result.campaign_summary
            c6_refusal = (
                campaign_summary.get('c6_refusal_diagnostic')
                if isinstance(campaign_summary, Mapping)
                else None
            )
            if (
                isinstance(c6_refusal, Mapping)
                and c6_refusal.get('status') == 'refused'
            ):
                diagnostic = c6_refusal.get('diagnostic')
                reason = (
                    diagnostic.get('reason_refused')
                    if isinstance(diagnostic, Mapping)
                    else c6_refusal.get('reason')
                )
                reason = str(reason or 'c6_mg_thermite_refused')
                refusal_payload = {
                    'status': 'refused',
                    'reason': reason,
                    'message': reason,
                    'c6_refusal_diagnostic': dict(c6_refusal),
                    'backend_status': backend_status,
                    'backend_authoritative': backend_authoritative,
                    'backend_message': backend_message,
                }
                with run_lock:
                    current, _ = _current_simulation_state(sid, run_id)
                    if (
                        current is None
                        or not current['running']
                        or current.get('artifact_persisted')
                    ):
                        break
                    artifact = persist_terminal(
                        session,
                        status='refused',
                        reason=reason,
                        error_message=reason,
                        refusal_diagnostic=c6_refusal,
                    )
                    if artifact is None:
                        stop_with_status({
                            'status': 'error',
                            'reason': 'persistence_failed',
                            'message': 'Run was refused but its report was not saved',
                        })
                        break
                    stop_with_status(refusal_payload)
                break

            if step_result.decision_event is not None:
                with _simulations_guard:
                    current = _simulations.get(sid)
                    if (
                        current is None
                        or current.get('run_id') != run_id
                        or not current['running']
                    ):
                        break
                    current['paused'] = True
                _emit_if_current(
                    socketio,
                    sid,
                    run_id,
                    'decision_required',
                    step_result.decision_event,
                )
                break

            spd = state.get('speed', 1.0)
            if spd > 0:
                socketio.sleep(spd)

    thread = socketio.start_background_task(run_loop)
    with _simulations_guard:
        current = _simulations.get(sid)
        if current is not None and current.get('run_id') == run_id:
            current['thread'] = thread


def register_events(socketio):
    """Register all SocketIO events for the simulator UI."""
    global _registered_socketio, _registered_start_handler
    _registered_socketio = socketio

    @socketio.on('connect')
    def handle_connect():
        with _run_command_lock:
            client_id = flask_session.get('ledger_client_id')
            if not isinstance(client_id, str) or not client_id:
                socketio.emit('simulation_status', {
                    'status': 'error',
                    'message': 'browser identity must be established over HTTP first',
                    'error_type': 'client_identity_required',
                }, room=request.sid)
                return
            _socket_client_ids[request.sid] = client_id
        _safe_log(f"Client connected: {request.sid}")

    @socketio.on('ledger_api')
    def handle_ledger_api(data=None):
        """Serve the caller's active simulation through the generic ledger API."""
        try:
            params = dict(data or {})
            resource = str(params.pop('resource', 'snapshot'))
            return read_ledger_api(
                request.sid,
                resource,
                include_run_id=True,
                **params,
            )
        except (KeyError, LookupError, TypeError, ValueError) as exc:
            return {"error": str(exc)}

    @socketio.on('disconnect')
    def handle_disconnect():
        sid = request.sid
        _safe_log(f"Client disconnected: {sid}")
        _disconnect_simulation_client(socketio, sid)

    @socketio.on('start_simulation')
    def handle_start(
        data,
        *,
        sid: str | None = None,
        ledger_client_id: str | None = None,
        command_mode: bool = False,
        draft_mode: bool = False,
        replace_sid: str | None = None,
    ):
        """
        Start a new simulation run.

        data = {
            'feedstock': 'lunar_mare_low_ti',
            'mass_kg': 1000,
            'backend': 'alphamelts',
            'track': 'pyrolysis',
            'speed': 1.0,           # seconds per simulation hour
        }
        """
        sid = sid or request.sid
        socket_bound = ledger_client_id is None

        def reject(
            payload: dict[str, object],
            error_type: str,
            status_code: int = 400,
        ):
            if command_mode:
                raise RunCommandError(
                    str(payload['message']),
                    error_type=error_type,
                    status_code=status_code,
                    payload=payload,
                )
            socket_payload = dict(payload)
            socket_payload['error_type'] = error_type
            socketio.emit('simulation_status', socket_payload, room=sid)
            return None

        with _run_command_lock:
            resolved_ledger_client_id = ledger_client_id
            if socket_bound:
                resolved_ledger_client_id = _socket_client_ids.get(sid)
                if resolved_ledger_client_id is None:
                    return reject({
                        'status': 'error',
                        'message': 'browser identity must be established over HTTP first',
                    }, 'client_identity_required', 409)

        if data is None:
            data = {}
        elif not isinstance(data, Mapping):
            return reject({
                'status': 'error',
                'message': 'start_simulation payload must be an object',
            }, 'invalid_run_request')

        feedstock_key = data.get('feedstock', 'lunar_mare_low_ti')
        cost_parameters = None
        cost_source_meta: dict[str, object] | None = None
        try:
            mass_kg = _coerce_bounded_float(
                data.get('mass_kg'),
                field='mass_kg',
                default=1000.0,
                minimum=0.0,
                maximum=_MAX_WEB_MASS_KG,
                exclusive_minimum=True,
            )
            speed = _coerce_bounded_float(
                data.get('speed'),
                field='speed',
                default=1.0,
                minimum=0.0,
                maximum=_MAX_SIM_SPEED_SECONDS,
            )
            raw_c4_max_temp = data.get('c4_max_temp_C')
            c4_max_temp = _coerce_bounded_float(
                raw_c4_max_temp,
                field='c4_max_temp_C',
                default=(
                    _c4_setpoint_ceiling_T_C()
                    if raw_c4_max_temp is None or raw_c4_max_temp == ''
                    else None
                ),
                minimum=0.0,
                maximum=_MAX_C4_TEMP_C,
                exclusive_minimum=True,
            )
            c5_enabled = _coerce_bool(data.get('c5_enabled', False))
            mre_target_species = str(data.get('mre_target_species') or '').strip()
            mre_max_voltage_V = _coerce_bounded_float(
                data.get('mre_max_voltage_V'),
                field='mre_max_voltage_V',
                default=0.0,
                minimum=0.0,
                maximum=_MAX_MRE_VOLTAGE_V,
            )
            additives_kg = _coerce_additives_kg(data.get('additives', {}))
            raw_setpoints_patch = data.get('setpoints_patch')
            if raw_setpoints_patch in (None, {}, ''):
                setpoints_patch: dict[str, object] = {}
            else:
                if not isinstance(raw_setpoints_patch, Mapping):
                    raise InputValidationError(
                        'setpoints_patch must be an object'
                    )
                try:
                    setpoints_patch = normalize_recipe_patch(
                        raw_setpoints_patch,
                        source='start_simulation.setpoints_patch',
                    )
                except RecipeIOError as exc:
                    raise InputValidationError(str(exc)) from exc
            runtime_campaign_overrides = _coerce_runtime_campaign_overrides(
                data.get('runtime_campaign_overrides')
            )
            raw_cost_parameters = data.get('cost_parameters')
            if raw_cost_parameters is None:
                _, cost_parameters, cost_source_meta = (
                    _loaded_recipe_cost_parameters_for_start(
                        sid,
                        setpoints_patch,
                        consume=not draft_mode,
                    )
                )
            elif not isinstance(raw_cost_parameters, Mapping):
                raise InputValidationError('cost_parameters must be an object')
            else:
                try:
                    cost_parameters = normalize_cost_parameters(
                        raw_cost_parameters,
                        source='start_simulation.cost_parameters',
                        defaults_applied=False,
                    )
                except (TypeError, ValueError) as exc:
                    raise InputValidationError(str(exc)) from exc
                # Explicit submission supersedes any staged recipe identity
                # without a "cleared" notice (operator already supplied prices).
                if not draft_mode:
                    _discard_loaded_recipe_cost_parameters(sid)
        except InputValidationError as exc:
            return reject({
                'status': 'error',
                'message': str(exc),
            }, 'invalid_run_input')
        # Default is 'auto' (AlphaMELTS-preferred autodetect per
        # \goal BACKEND-DEFAULT-SWITCH). Explicit UI choices are still honoured.
        backend_name = data.get('backend', 'auto')
        track = data.get('track', 'pyrolysis')

        # Load data files
        feedstocks = load_visible_feedstocks()
        setpoints = dict(_load_yaml('setpoints.yaml'))
        vapor_pressures = _load_yaml('vapor_pressures.yaml')
        materials = _load_yaml('materials.yaml')
        furnace_material_id = str(data.get('furnace_material_id') or '').strip()
        if setpoints_patch:
            try:
                setpoints = _deep_merge_setpoints(setpoints, setpoints_patch)
                c4_max_temp = _c4_setpoint_ceiling_T_C(setpoints)
            except RunnerError as exc:
                return reject({
                    'status': 'error',
                    'message': str(exc),
                }, 'invalid_run_input')
            except InputValidationError as exc:
                return reject({
                    'status': 'error',
                    'message': str(exc),
                }, 'invalid_run_input')
        if furnace_material_id:
            try:
                setpoints['furnace_max_T_C'] = resolve_furnace_max_T_C(
                    furnace_material_id,
                    requested_cap=setpoints.get('furnace_max_T_C'),
                )
            except ValueError as exc:
                return reject({
                    'status': 'error',
                    'message': str(exc),
                }, 'invalid_run_input')

        override_paths = set(_mapping_leaf_paths(setpoints_patch))
        if furnace_material_id:
            override_paths.add('furnace_max_T_C')
        effective_config = _effective_config_from_setpoints(
            setpoints,
            override_paths=override_paths,
        )

        try:
            backend = _get_backend(backend_name)
        except BackendUnavailableError as exc:
            return reject({
                'status': 'error',
                'message': str(exc),
                'backend_status': 'unavailable',
                'backend_authoritative': False,
            }, 'backend_unavailable')
        if socket_bound:
            with _run_command_lock:
                if _socket_client_ids.get(sid) != resolved_ledger_client_id:
                    return reject({
                        'status': 'error',
                        'message': 'client disconnected before run launch',
                    }, 'client_disconnected', 409)
        resolution_status = backend_resolution_status(backend)
        backend_type = resolution_status.active_backend
        backend_message = ''
        if isinstance(backend, InternalAnalyticalBackend):
            backend_message = 'Using built-in fallback'
        else:
            backend_message = f'Using {backend_type}'

        # User-configurable parameters
        c5_enabled, mre_target_species, mre_max_voltage_V = normalize_mre_policy(
            c5_enabled,
            mre_target_species,
            mre_max_voltage_V,
        )
        session = SimSession()
        try:
            session.start(
                SimSessionConfig(
                    feedstock_id=feedstock_key,
                    feedstocks=feedstocks,
                    setpoints=setpoints,
                    vapor_pressures=vapor_pressures,
                    materials=materials,
                    backend_name=_backend_name_for_session(backend),
                    backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
                    mass_kg=mass_kg,
                    additives_kg=additives_kg,
                    runtime_campaign_overrides=runtime_campaign_overrides,
                    track=track,
                    c4_max_temp=c4_max_temp,
                    c5_enabled=c5_enabled,
                    mre_target_species=mre_target_species,
                    mre_max_voltage_V=mre_max_voltage_V,
                    unavailable_error_cls=BackendUnavailableError,
                )
            )
        except BackendUnavailableError as e:
            return reject({
                'status': 'error',
                'message': str(e),
                'backend_status': resolution_status.backend_status,
                'backend_authoritative': resolution_status.authoritative,
            }, 'backend_unavailable')
        except (TypeError, ValueError) as exc:
            return reject({
                'status': 'error',
                'message': str(exc),
            }, 'invalid_run_input')
        sim = session.simulator

        if draft_mode:
            normalized_values = {
                'feedstock': str(feedstock_key),
                'mass_kg': mass_kg,
                'backend': str(backend_name),
                'track': str(track),
                'speed': speed,
                'c4_max_temp_C': c4_max_temp,
                'c5_enabled': c5_enabled,
                'mre_target_species': mre_target_species,
                'mre_max_voltage_V': mre_max_voltage_V,
                'additives': additives_kg,
                'setpoints_patch': copy.deepcopy(setpoints_patch),
                'runtime_campaign_overrides': copy.deepcopy(
                    runtime_campaign_overrides
                ),
                'furnace_material_id': furnace_material_id,
                'cost_parameters': copy.deepcopy(cost_parameters),
            }
            validated_inputs = {
                key: value
                for key, value in normalized_values.items()
                if key in data
            }
            return {
                'status': 'valid',
                'validated_inputs': validated_inputs,
            }

        run_store = get_run_store()
        runner_projector = PyrolysisRun(
            feedstock_id=session._config.feedstock_id,
            campaign=session._config.campaign,
            hours=session._config.hours,
            additives_kg=dict(session._config.additives_kg),
            mass_kg=float(session._config.mass_kg),
            backend_name=session._config.backend_name,
            setpoints_patch=copy.deepcopy(setpoints_patch),
            runtime_campaign_overrides=(
                session._config.runtime_campaign_overrides
            ),
            track=session._config.track,
            c5_enabled=session._config.c5_enabled,
            mre_target_species=session._config.mre_target_species,
            mre_max_voltage_V=session._config.mre_max_voltage_V,
            run_metadata_overrides={
                'started_at_utc': datetime.now(timezone.utc).strftime(
                    '%Y-%m-%dT%H:%M:%SZ'
                ),
            },
        )

        initial_state = {
            'http_owned': command_mode,
            'backend_message': backend_message,
            'backend_status': resolution_status.backend_status,
            'backend_authoritative': resolution_status.authoritative,
            'recipe_inputs': _recipe_inputs_payload(
                feedstock_key=str(feedstock_key),
                mass_kg=mass_kg,
                track=str(track),
                runtime_campaign_overrides=runtime_campaign_overrides,
                c4_max_temp=c4_max_temp,
                furnace_max_T_C=setpoints.get('furnace_max_T_C'),
                c5_enabled=c5_enabled,
                mre_target_species=mre_target_species,
                mre_max_voltage_V=mre_max_voltage_V,
                additives_kg=additives_kg,
                furnace_material_id=furnace_material_id,
            ),
            'setpoints_patch': copy.deepcopy(setpoints_patch),
            'resolved_setpoints_patch': _resolved_recipe_patch_for_session(
                setpoints_patch=setpoints_patch,
                setpoints=setpoints,
                runtime_campaign_overrides=runtime_campaign_overrides,
            ),
        }
        if isinstance(cost_parameters, Mapping):
            initial_state['cost_parameters'] = copy.deepcopy(cost_parameters)
        if effective_config:
            initial_state['effective_config'] = effective_config
        cancelled_prior_run_id = None
        published_run_id = None
        try:
            with _run_command_lock:
                if (
                    socket_bound
                    and _socket_client_ids.get(sid) != resolved_ledger_client_id
                ):
                    raise RunCommandError(
                        'client disconnected before run launch',
                        error_type='client_disconnected',
                        status_code=409,
                    )
                with _simulations_guard:
                    client_replacement_sid = next(
                        (
                            candidate_sid
                            for candidate_sid, candidate_state in _simulations.items()
                            if (
                                resolved_ledger_client_id is not None
                                and candidate_state.get('ledger_client_id')
                                == resolved_ledger_client_id
                            )
                        ),
                        None,
                    )
                replacement_sid = (
                    client_replacement_sid
                    or replace_sid
                    or (sid if not command_mode else None)
                )
                _ensure_global_run_capacity(replacement_sid)
                if replacement_sid is not None:
                    replacement_state, _ = _current_simulation_state(
                        replacement_sid
                    )
                    replacement_run_id = (
                        str(replacement_state['run_id'])
                        if replacement_state is not None
                        else None
                    )
                    try:
                        cancellation = _cancel_simulation_state(
                            socketio,
                            replacement_sid,
                            reason='replaced_by_new_run',
                            run_id=replacement_run_id,
                        )
                    except RuntimeError as exc:
                        raise _RunReplacementError(str(exc)) from exc
                    if cancellation is not None and cancellation.get('cancelled'):
                        cancelled_prior_run_id = str(cancellation['run_id'])
                        # Terminal cancellation is an immutable first write, so rollback
                        # is impossible; any later launch failure reports this run ID.
                    if replacement_sid != sid and cancellation is not None:
                        _clear_simulation_state(
                            replacement_sid,
                            run_id=replacement_run_id,
                        )

                state, run_lock = _replace_simulation_state(
                    sid,
                    session,
                    speed,
                    ledger_client_id=resolved_ledger_client_id,
                    run_store=run_store,
                    runner_projector=runner_projector,
                    initial_state=initial_state,
                )
            run_id = state['run_id']
            published_run_id = run_id

            _emit_if_current(
                socketio,
                sid,
                run_id,
                'simulation_status',
                _start_payload(
                    sim=sim,
                    feedstock_key=feedstock_key,
                    mass_kg=mass_kg,
                    backend_requested=backend_name,
                    backend_active=backend_type,
                    backend_status=resolution_status.backend_status,
                    backend_authoritative=resolution_status.authoritative,
                    backend_message=backend_message,
                    backend_payload=resolution_status.as_payload(),
                    c5_enabled=c5_enabled,
                    mre_target_species=mre_target_species,
                    mre_max_voltage_V=mre_max_voltage_V,
                ),
            )
            if isinstance(cost_parameters, Mapping):
                cost_apply_notice = _cost_identity_applied_notice(
                    cost_source_meta
                )
                if cost_apply_notice is not None:
                    _emit_if_current(
                        socketio,
                        sid,
                        run_id,
                        'simulation_status',
                        cost_apply_notice,
                    )
            _start_background_loop(
                socketio,
                sid,
                run_id,
                run_lock,
                backend_message,
                resolution_status.backend_status,
                resolution_status.authoritative,
            )
        except RunCommandError as exc:
            return reject(
                {'status': 'error', 'message': str(exc)},
                exc.error_type,
                exc.status_code,
            )
        except _RunReplacementError as exc:
            return reject({
                'status': 'error',
                'message': str(exc),
            }, 'run_replacement_failed', 500)
        except Exception as exc:  # noqa: BLE001 -- typed launch boundary
            if published_run_id is not None:
                _clear_simulation_state(sid, run_id=published_run_id)
            if cancelled_prior_run_id is not None:
                return reject({
                    'status': 'error',
                    'message': (
                        f'New run launch failed after prior run '
                        f'{cancelled_prior_run_id} was cancelled and persisted: {exc}'
                    ),
                    'prior_run_id': cancelled_prior_run_id,
                    'prior_run_cancelled': True,
                }, 'run_launch_failed_after_replacement', 500)
            return reject({
                'status': 'error',
                'message': f'Run launch failed: {exc}',
            }, 'run_launch_failed', 500)
        return {'run_id': run_id, 'status': 'started'}

    _registered_start_handler = handle_start

    @socketio.on('clear_staged_cost_parameters')
    def handle_clear_staged_cost_parameters(_data=None):
        """Operator-visible clear of staged loaded-recipe cost identity."""
        sid = request.sid
        cleared = clear_loaded_recipe_cost_parameters(sid)
        if not cleared and _registered_socketio is not None:
            socketio.emit(
                'simulation_status',
                {
                    'status': 'recipe_cost_parameters_cleared',
                    'notice_type': 'cost_parameters_cleared',
                    'message': (
                        'No staged recipe cost parameters were present to clear.'
                    ),
                    'already_clear': True,
                },
                room=sid,
            )
        return {'cleared': cleared}

    @socketio.on('pause_simulation')
    def handle_pause():
        sid = request.sid
        state, lock = _current_simulation_state(sid)
        if state and lock:
            run_id = state['run_id']
            with lock:
                current, _ = _current_simulation_state(sid, run_id)
                if current is not state or not state['running']:
                    return
                state['session'].pause()
                state['paused'] = True
                _emit_if_current(
                    socketio,
                    sid,
                    run_id,
                    'simulation_status',
                    {'status': 'paused'},
                )

    @socketio.on('resume_simulation')
    def handle_resume():
        sid = request.sid
        state, lock = _current_simulation_state(sid)
        if state and lock:
            run_id = state['run_id']
            with lock:
                current, _ = _current_simulation_state(sid, run_id)
                if current is not state or not state['running']:
                    return
                state['session'].resume()
                state['paused'] = False
                _emit_if_current(
                    socketio,
                    sid,
                    run_id,
                    'simulation_status',
                    {'status': 'resumed'},
                )

    @socketio.on('make_decision')
    def handle_decision(data):
        """
        Player makes a process decision.

        data = {'choice': 'A'}  or  {'choice': 'two'}
        """
        sid = request.sid
        if not isinstance(data, Mapping):
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': 'make_decision payload must be an object',
            }, room=sid)
            return
        raw_choice = data.get('choice')
        if not isinstance(raw_choice, str):
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': 'make_decision choice is required',
            }, room=sid)
            return
        choice = raw_choice.strip()
        if choice == '':
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': 'make_decision choice is required',
            }, room=sid)
            return
        state, lock = _current_simulation_state(sid)
        if state and lock:
            resume_loop = False
            with lock:
                current, _ = _current_simulation_state(sid, state['run_id'])
                if current is not state or not state['running']:
                    return
                session = state['session']
                decision = session.pending_decision()
                if decision is None:
                    socketio.emit('simulation_status', {
                        'status': 'error',
                        'message': 'make_decision no decision is pending',
                    }, room=sid)
                    return
                valid_choices = {str(option) for option in decision.options}
                if choice not in valid_choices:
                    socketio.emit('simulation_status', {
                        'status': 'error',
                        'message': (
                            f"make_decision choice {choice!r} is not one of "
                            f"{sorted(valid_choices)!r}"
                        ),
                    }, room=sid)
                    return
                session.decide(choice)
                resume_loop = True
                session.resume()
                state['paused'] = False
                _emit_if_current(
                    socketio,
                    sid,
                    state['run_id'],
                    'simulation_status',
                    {
                        'status': 'decision_applied',
                        'choice': choice,
                    },
                )
            if resume_loop:
                _start_background_loop(
                    socketio,
                    sid,
                    state['run_id'],
                    lock,
                    state.get('backend_message', ''),
                    state.get('backend_status', 'unavailable'),
                    bool(state.get('backend_authoritative', False)),
                )

    @socketio.on('adjust_parameter')
    def handle_parameter_change(data):
        """
        Live parameter adjustment mid-run.

        data = {'param': 'speed', 'value': 0.5}
        """
        sid = request.sid
        if data is None:
            data = {}
        elif not isinstance(data, Mapping):
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': 'adjust_parameter payload must be an object',
            }, room=sid)
            return
        state, lock = _current_simulation_state(sid)
        if not state or not lock:
            return

        param = str(data.get('param', '') or '').strip()
        value = data.get('value')

        def reject_adjustment(message: str) -> None:
            _emit_if_current(
                socketio,
                sid,
                state['run_id'],
                'simulation_status',
                {'status': 'error', 'message': message},
            )

        supported = {
            'speed',
            'stir_factor',
            'pO2_mbar',
            'c4_max_temp',
            'campaign_override',
        }
        if param not in supported:
            reject_adjustment(f'unsupported parameter adjustment {param!r}')
            return
        if param == 'campaign_override':
            campaign_name = str(data.get('campaign', '') or '').strip()
            field_name = str(data.get('field', '') or '').strip()
            if not campaign_name or not field_name:
                reject_adjustment(
                    'campaign_override requires campaign and field'
                )
                return
        else:
            campaign_name = ''
            field_name = ''

        run_id = state['run_id']
        with lock:
            current, _ = _current_simulation_state(sid, run_id)
            if current is not state or not state['running']:
                return
            melt_snapshot = None
            campaign_overrides_snapshot = None
            try:
                if param == 'speed':
                    speed = _coerce_bounded_float(
                        value,
                        field='speed',
                        minimum=0.0,
                        maximum=_MAX_SIM_SPEED_SECONDS,
                    )
                    state['speed'] = speed
                    value = speed
                elif param == 'stir_factor':
                    state['session'].adjust('stir_factor', value)
                elif param == 'pO2_mbar':
                    pO2 = _coerce_bounded_float(
                        value,
                        field='pO2_mbar',
                        minimum=0.0,
                    )
                    melt = state['session'].simulator.melt
                    melt_snapshot = {
                        'pO2_mbar': melt.pO2_mbar,
                        'p_total_mbar': melt.p_total_mbar,
                        'atmosphere': melt.atmosphere,
                    }
                    state['session'].adjust('pO2_mbar', pO2)
                    value = pO2
                elif param == 'c4_max_temp':
                    c4_max_temp = _coerce_bounded_float(
                        value,
                        field='c4_max_temp',
                        minimum=0.0,
                        maximum=_MAX_C4_TEMP_C,
                    )
                    state['session'].adjust('c4_max_temp', c4_max_temp)
                    value = c4_max_temp
                else:
                    sim = state['session'].simulator
                    campaign_overrides_snapshot = copy.deepcopy(
                        sim.campaign_mgr.overrides
                    )
                    melt = sim.melt
                    if melt.campaign.name == campaign_name:
                        melt_snapshot = {
                            'pO2_mbar': melt.pO2_mbar,
                            'p_total_mbar': melt.p_total_mbar,
                            'atmosphere': melt.atmosphere,
                            'stir_state': copy.deepcopy(melt.stir_state),
                        }
                    CampaignManager.validate_runtime_campaign_overrides(
                        {campaign_name: {field_name: value}}
                    )
                    state['session'].adjust(
                        'campaign_override',
                        value,
                        campaign=campaign_name,
                        field=field_name,
                    )
            except (InputValidationError, TypeError, ValueError) as exc:
                sim = state['session'].simulator
                if campaign_overrides_snapshot is not None:
                    sim.campaign_mgr.overrides = campaign_overrides_snapshot
                if melt_snapshot is not None:
                    melt = sim.melt
                    for attr, snapshot_value in melt_snapshot.items():
                        setattr(melt, attr, snapshot_value)
                reject_adjustment(str(exc))
                return

            _emit_if_current(
                socketio,
                sid,
                run_id,
                'simulation_status',
                {
                    'status': 'parameter_adjusted',
                    'param': param,
                    'value': value,
                },
            )
