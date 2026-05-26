"""SocketIO event handlers for the simulator interface."""

import threading
import uuid
from pathlib import Path

import yaml
from flask import request

from simulator.backends import (
    BackendSelectionPolicy,
    BackendUnavailableError,
    emit_web_engine_selection_log,
    resolve_backend,
)
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.factsage import FactSAGEBackend
from simulator.melt_backend.factsage_config import (
    FactSAGEConfigError,
    load_factsage_config,
)
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    drive_session,
)
# Goal #18 ``JSON-RUNNER-HARNESS``: the SocketIO stream and the CLI
# runner share ONE per-hour summary builder.  ``SimSession.advance()``
# owns that runner-format summary and returns it in ``StepResult``; this
# adapter only emits it alongside the legacy ``simulation_tick`` payload.
from web.feedstock_data import load_visible_feedstocks


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


def _safe_log(message: str) -> None:
    try:
        print(message)
    except OSError:
        pass


def _replace_simulation_state(
    sid: str,
    session,
    speed: float,
) -> tuple[dict, threading.Lock]:
    """Install one active simulation for a client and stop any prior run."""
    with _simulations_guard:
        previous = _simulations.get(sid)
        if previous is not None:
            previous['running'] = False
        run_lock = threading.Lock()
        state = {
            'session': session,
            'running': True,
            'paused': False,
            'speed': speed,
            'run_id': uuid.uuid4().hex,
        }
        _simulations[sid] = state
        _sim_locks[sid] = run_lock
        return state, run_lock


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


def _emit_if_current(socketio, sid: str, run_id: str, event: str, payload) -> bool:
    with _simulations_guard:
        state = _simulations.get(sid)
        if (
            state is None
            or state.get('run_id') != run_id
            or not state['running']
        ):
            return False
    socketio.emit(event, payload, room=sid)
    return True


def _clear_simulation_state(sid: str) -> None:
    """Stop and remove any active simulation for a client."""
    with _simulations_guard:
        state = _simulations.pop(sid, None)
        if state is not None:
            state['running'] = False
        _sim_locks.pop(sid, None)


def _get_backend(backend_name: str):
    """
    Create and initialize the active melt backend.

    Eligibility policy (\\goal BACKEND-DEFAULT-SWITCH):

    * ``'alphamelts'`` — strict probe; raise ``BackendUnavailableError`` if
      PetThermoTools or the alphaMELTS binary is not reachable.
    * ``'factsage'`` — strict probe under the configured strict-config gate
      (a real ``FACTSAGE_CONFIG`` JSON + ChemApp + ``.cst`` data file); on
      gate failure, fall back to ``StubBackend`` so the simulator stays
      diagnostic-without-strict-config (existing semantics).
    * ``'vaporock'`` / ``'magemin'`` — explicitly **refused**.  Both
      adapters are not wired into a multi-intent dispatcher yet; selecting
      either as the active ``MeltBackend`` would fail closed inside
      ``simulator/core.py::_get_equilibrium`` (their populated
      ``phase_masses_kg`` + ``ledger_transition=None`` returns trip the
      "backend returned post-equilibrium phase material without an
      AtomLedger transition" reject).  Promotion is blocked on
      ``\\goal CHEMISTRY-KERNEL-CARVE-OUT``.
    * ``'auto'`` / ``'stub'`` / unknown — autodetect chain: probe
      AlphaMELTS first, then FactSAGE-with-strict-config, falling back to
      ``StubBackend`` as the always-available primary fallback.  No silent
      cross-backend fallback at runtime: if the selected primary throws
      inside ``_get_equilibrium`` after selection, ``core.py``'s
      fail-closed path handles it without re-routing here.
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
        factsage_backend_cls=FactSAGEBackend,
        stub_backend_cls=StubBackend,
        factsage_config_loader=_factsage_config,
        factsage_config_error_cls=FactSAGEConfigError,
    )


def _factsage_config():
    """Load optional FactSAGE config from FACTSAGE_CONFIG."""
    try:
        return load_factsage_config()
    except FactSAGEConfigError as exc:
        _safe_log(f'FactSAGE config error: {exc}')
        return {}


def _backend_name_for_session(backend) -> str:
    """Map the web-selected backend instance to SimSession strict name."""
    backend_type = type(backend).__name__
    if isinstance(backend, AlphaMELTSBackend) or backend_type == 'AlphaMELTSBackend':
        return 'alphamelts'
    if isinstance(backend, FactSAGEBackend) or backend_type == 'FactSAGEBackend':
        return 'factsage'
    return 'stub'


def _decision_payload(decision):
    return {
        'type': decision.decision_type.name,
        'options': list(decision.options),
        'recommendation': decision.recommendation,
        'context': decision.context,
    }


def _start_payload(
    *,
    sim,
    feedstock_key: str,
    mass_kg: float,
    backend_requested: str,
    backend_active: str,
    backend_message: str,
):
    """Build the public start status payload."""
    return {
        'status': 'started',
        'feedstock': feedstock_key,
        'mass_kg': mass_kg,
        'backend_requested': backend_requested,
        'backend_active': backend_active,
        'backend_message': backend_message,
    }


def _tick_payload(*, sim, snapshot, backend_message: str, backend_error: str = ''):
    """Build the public per-tick payload."""
    return {
        'hour': snapshot.hour,
        'campaign': snapshot.campaign.name,
        'temperature_C': round(snapshot.temperature_C, 1),
        'melt_mass_kg': round(snapshot.melt_mass_kg, 1),
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
        'evap_total_kg_hr': round(snapshot.evap_flux.total_kg_hr, 4),
        'evap_species': {
            k: round(v, 4) for k, v in snapshot.evap_flux.species_kg_hr.items()
        },
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
        'energy_kWh': round(snapshot.energy.total_kWh, 4),
        'energy_cumulative_kWh': round(snapshot.energy_cumulative_kWh, 2),
        'oxygen_kg': round(snapshot.oxygen_produced_kg, 2),
        'mass_balance_error_pct': round(snapshot.mass_balance_error_pct, 3),
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
    return {
        'total_hours': sim.melt.hour,
        'energy_kWh': sim.energy_cumulative_kWh,
        'oxygen_kg': sim._oxygen_total_kg(),
        'oxygen_stored_kg': sim._oxygen_stored_kg(),
        'oxygen_vented_kg': sim._oxygen_vented_kg(),
        'mass_in_kg': round(final_snapshot.mass_in_kg, 3),
        'mass_out_kg': round(final_snapshot.mass_out_kg, 3),
        'mass_balance_error_pct': round(
            final_snapshot.mass_balance_error_pct, 6),
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
        'terminal_rump_by_species': sim._terminal_rump_by_species(),
        'terminal_rump_by_class': sim._terminal_rump_by_class(),
    }


def _start_background_loop(
    socketio,
    sid: str,
    run_id: str,
    run_lock,
    backend_message: str,
):
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
            with run_lock:
                state, _ = _current_simulation_state(sid, run_id)
                if (
                    state is None
                    or not state['running']
                ):
                    break
                session = state['session']
                sim = session.simulator
                if session.is_complete():
                    completion_payload = _completion_payload(sim)
                    if not _emit_if_current(
                        socketio,
                        sid,
                        run_id,
                        'simulation_complete',
                        completion_payload,
                    ):
                        break
                    with _simulations_guard:
                        current = _simulations.get(sid)
                        if (
                            current is not None
                            and current.get('run_id') == run_id
                        ):
                            current['running'] = False
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
                except Exception as exc:
                    _safe_log(f'Simulation loop failed: {exc}')
                    error_payload = {
                        'status': 'error',
                        'message': str(exc),
                        'backend_message': backend_message,
                    }
                    if not _emit_if_current(
                        socketio,
                        sid,
                        run_id,
                        'simulation_status',
                        error_payload,
                    ):
                        break
                    with _simulations_guard:
                        current = _simulations.get(sid)
                        if (
                            current is not None
                            and current.get('run_id') == run_id
                        ):
                            current['running'] = False
                            current['paused'] = False
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
            tick_data = _tick_payload(
                sim=sim,
                snapshot=step_result.snapshot,
                backend_message=backend_message,
                backend_error=step_result.backend_error,
            )
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

    @socketio.on('connect')
    def handle_connect():
        _safe_log(f"Client connected: {request.sid}")

    @socketio.on('disconnect')
    def handle_disconnect():
        sid = request.sid
        _safe_log(f"Client disconnected: {sid}")
        _clear_simulation_state(sid)

    @socketio.on('start_simulation')
    def handle_start(data):
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
        sid = request.sid

        feedstock_key = data.get('feedstock', 'lunar_mare_low_ti')
        mass_kg = float(data.get('mass_kg', 1000))
        # Default is 'auto' (AlphaMELTS-preferred autodetect per
        # \goal BACKEND-DEFAULT-SWITCH), not 'stub'.  Explicit UI choices
        # ('alphamelts', 'factsage', 'stub') are still honoured.
        backend_name = data.get('backend', 'auto')
        track = data.get('track', 'pyrolysis')
        speed = float(data.get('speed', 1.0))

        # Load data files
        feedstocks = load_visible_feedstocks()
        setpoints = _load_yaml('setpoints.yaml')
        vapor_pressures = _load_yaml('vapor_pressures.yaml')

        try:
            backend = _get_backend(backend_name)
        except BackendUnavailableError as exc:
            socketio.emit('simulation_status', {
                'status': 'error', 'message': str(exc),
            }, room=sid)
            return
        backend_type = type(backend).__name__
        backend_message = ''
        if isinstance(backend, StubBackend):
            if backend_name == 'factsage':
                backend_message = (
                    'FactSAGE unavailable; using built-in fallback')
            else:
                backend_message = 'Using built-in fallback'
        elif backend_name == 'factsage' or backend_type == 'FactSAGEBackend':
            backend_message = (
                'FactSAGE/ChemApp export active: '
                f'{backend.capability_summary()}')
        else:
            backend_message = f'Using {backend_type}'

        # User-configurable parameters
        c4_max_temp = float(data.get('c4_max_temp_C', 1670))

        # Additives from inventory (Na, K, Mg, Ca, C)
        raw_additives = data.get('additives', {})
        additives_kg = {k: float(v) for k, v in raw_additives.items()
                        if float(v) > 0}

        session = SimSession()
        try:
            session.start(
                SimSessionConfig(
                    feedstock_id=feedstock_key,
                    feedstocks=feedstocks,
                    setpoints=setpoints,
                    vapor_pressures=vapor_pressures,
                    backend_name=_backend_name_for_session(backend),
                    backend_policy=BackendSelectionPolicy.RUNNER_STRICT,
                    mass_kg=mass_kg,
                    additives_kg=additives_kg,
                    track=track,
                    c4_max_temp=c4_max_temp,
                    unavailable_error_cls=BackendUnavailableError,
                )
            )
        except BackendUnavailableError as e:
            socketio.emit('simulation_status', {
                'status': 'error', 'message': str(e),
            }, room=sid)
            return
        sim = session.simulator

        state, run_lock = _replace_simulation_state(sid, session, speed)
        run_id = state['run_id']
        state['backend_message'] = backend_message

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
                backend_message=backend_message,
            ),
        )
        _start_background_loop(socketio, sid, run_id, run_lock, backend_message)

    @socketio.on('pause_simulation')
    def handle_pause():
        sid = request.sid
        state, _ = _current_simulation_state(sid)
        if state:
            state['session'].pause()
            state['paused'] = True
            _emit_if_current(
                socketio,
                sid,
                state['run_id'],
                'simulation_status',
                {'status': 'paused'},
            )

    @socketio.on('resume_simulation')
    def handle_resume():
        sid = request.sid
        state, _ = _current_simulation_state(sid)
        if state:
            state['session'].resume()
            state['paused'] = False
            _emit_if_current(
                socketio,
                sid,
                state['run_id'],
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
        state, lock = _current_simulation_state(sid)
        if state and lock:
            resume_loop = False
            with lock:
                session = state['session']
                if session.pending_decision():
                    choice = data.get('choice', '')
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
                    'choice': data.get('choice'),
                },
            )
            if resume_loop:
                _start_background_loop(
                    socketio,
                    sid,
                    state['run_id'],
                    lock,
                    state.get('backend_message', ''),
                )

    @socketio.on('adjust_parameter')
    def handle_parameter_change(data):
        """
        Live parameter adjustment mid-run.

        data = {'param': 'speed', 'value': 0.5}
        """
        sid = request.sid
        state, lock = _current_simulation_state(sid)
        if not state:
            return

        param = data.get('param', '')
        value = data.get('value')

        if param == 'speed':
            state['speed'] = float(value)
        elif param == 'stir_factor' and lock:
            with lock:
                state['session'].adjust('stir_factor', value)
        elif param == 'pO2_mbar' and lock:
            with lock:
                state['session'].adjust('pO2_mbar', value)
        elif param == 'c4_max_temp' and lock:
            with lock:
                state['session'].adjust('c4_max_temp', value)
        elif param == 'campaign_override' and lock:
            # data = {param: 'campaign_override', campaign: 'C2A',
            #         field: 'ramp_rate', value: 10.0}
            campaign_name = data.get('campaign', '')
            field_name = data.get('field', '')
            field_value = data.get('value')
            if campaign_name and field_name:
                with lock:
                    state['session'].adjust(
                        'campaign_override',
                        field_value,
                        campaign=campaign_name,
                        field=field_name,
                    )

        _emit_if_current(
            socketio,
            sid,
            state['run_id'],
            'simulation_status',
            {
                'status': 'parameter_adjusted',
                'param': param,
                'value': value,
            },
        )
