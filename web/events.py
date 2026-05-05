"""SocketIO event handlers for the simulator interface."""

import threading
import time
import uuid
from pathlib import Path

import yaml
from flask import request

from simulator.core import PyrolysisSimulator, CampaignPhase
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.factsage import FactSAGEBackend
from simulator.melt_backend.factsage_config import (
    FactSAGEConfigError,
    load_factsage_config,
)


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


def _replace_simulation_state(sid: str, sim, speed: float) -> tuple[dict, threading.Lock]:
    """Install one active simulation for a client and stop any prior run."""
    with _simulations_guard:
        previous = _simulations.get(sid)
        if previous is not None:
            previous['running'] = False
        run_lock = threading.Lock()
        state = {
            'sim': sim,
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
    """Create and initialize the requested melt backend."""
    if backend_name == 'alphamelts':
        backend = AlphaMELTSBackend()
        if backend.initialize({}):
            return backend
    elif backend_name == 'factsage':
        backend = FactSAGEBackend()
        if backend.initialize(_factsage_config()):
            return backend
    # Fallback to stub
    backend = StubBackend()
    backend.initialize({})
    return backend


def _factsage_config():
    """Load optional FactSAGE config from FACTSAGE_CONFIG."""
    try:
        return load_factsage_config()
    except FactSAGEConfigError as exc:
        print(f'FactSAGE config error: {exc}')
        return {}


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


def _tick_payload(*, sim, snapshot, backend_message: str):
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
        'backend_error': getattr(sim, '_last_backend_error', ''),
        'backend_fallback_active': bool(
            getattr(sim, '_last_backend_error', '')),
        'backend_message': (
            'Built-in fallback active: '
            f'{sim._last_backend_error}'
            if getattr(sim, '_last_backend_error', '')
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
    }


def register_events(socketio):
    """Register all SocketIO events for the simulator UI."""

    @socketio.on('connect')
    def handle_connect():
        print(f"Client connected: {request.sid}")

    @socketio.on('disconnect')
    def handle_disconnect():
        sid = request.sid
        print(f"Client disconnected: {sid}")
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
        backend_name = data.get('backend', 'stub')
        track = data.get('track', 'pyrolysis')
        speed = float(data.get('speed', 1.0))

        # Load data files
        feedstocks = _load_yaml('feedstocks.yaml')
        setpoints = _load_yaml('setpoints.yaml')
        vapor_pressures = _load_yaml('vapor_pressures.yaml')

        backend = _get_backend(backend_name)
        backend_type = type(backend).__name__
        backend_message = ''
        if backend_name == 'factsage' and isinstance(backend, StubBackend):
            backend_message = 'FactSAGE unavailable; using built-in fallback'
        elif backend_name == 'factsage':
            backend_message = (
                'FactSAGE/ChemApp export active: '
                f'{backend.capability_summary()}')
        elif backend_name == 'alphamelts' and isinstance(backend, StubBackend):
            backend_message = 'AlphaMELTS unavailable; using built-in fallback'
        else:
            backend_message = f'Using {backend_type}'

        # Create simulator
        sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)

        # User-configurable parameters
        c4_max_temp = float(data.get('c4_max_temp_C', 1670))
        sim.c4_max_temp_C = c4_max_temp

        # Additives from inventory (Na, K, Mg, Ca, C)
        raw_additives = data.get('additives', {})
        additives_kg = {k: float(v) for k, v in raw_additives.items()
                        if float(v) > 0}

        try:
            sim.load_batch(feedstock_key, mass_kg,
                           additives_kg=additives_kg)
        except ValueError as e:
            socketio.emit('simulation_status', {
                'status': 'error', 'message': str(e),
            }, room=sid)
            return

        # Start first campaign
        if track == 'mre_baseline':
            sim.start_campaign(CampaignPhase.C0)
            sim.record.track = 'mre_baseline'
        else:
            sim.start_campaign(CampaignPhase.C0)

        state, run_lock = _replace_simulation_state(sid, sim, speed)
        run_id = state['run_id']

        socketio.emit('simulation_status', _start_payload(
            sim=sim,
            feedstock_key=feedstock_key,
            mass_kg=mass_kg,
            backend_requested=backend_name,
            backend_active=backend_type,
            backend_message=backend_message,
        ), room=sid)

        # Start background loop
        def run_loop():
            while True:
                state, _ = _current_simulation_state(sid, run_id)
                if (
                    state is None
                    or not state['running']
                ):
                    break
                if state['paused']:
                    time.sleep(0.1)
                    continue

                with run_lock:
                    state, _ = _current_simulation_state(sid, run_id)
                    if (
                        state is None
                        or not state['running']
                    ):
                        break
                    sim = state['sim']
                    if sim.is_complete():
                        with _simulations_guard:
                            current = _simulations.get(sid)
                            if (
                                current is None
                                or current.get('run_id') != run_id
                                or not current['running']
                            ):
                                break
                            socketio.emit(
                                'simulation_complete',
                                _completion_payload(sim),
                                room=sid)
                            current['running'] = False
                        break

                    snapshot = sim.step()

                tick_data = _tick_payload(
                    sim=sim,
                    snapshot=snapshot,
                    backend_message=backend_message,
                )
                if not _emit_if_current(
                    socketio, sid, run_id, 'simulation_tick', tick_data
                ):
                    break

                # Check for campaign completion summary
                if sim._last_campaign_summary is not None:
                    if not _emit_if_current(
                        socketio,
                        sid,
                        run_id,
                        'campaign_complete_summary',
                        sim._last_campaign_summary,
                    ):
                        break
                    sim._last_campaign_summary = None

                # Check for decision points
                if sim.paused_for_decision and sim.pending_decision:
                    d = sim.pending_decision
                    decision_payload = {
                        'type': d.decision_type.name,
                        'options': d.options,
                        'recommendation': d.recommendation,
                        'context': d.context,
                    }
                    with _simulations_guard:
                        current = _simulations.get(sid)
                        if (
                            current is None
                            or current.get('run_id') != run_id
                            or not current['running']
                        ):
                            break
                        socketio.emit(
                            'decision_required', decision_payload, room=sid)
                        current['paused'] = True

                # Pace the simulation
                spd = state.get('speed', 1.0)
                if spd > 0:
                    time.sleep(spd)

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()
        with _simulations_guard:
            current = _simulations.get(sid)
            if current is state and current.get('run_id') == run_id:
                current['thread'] = thread

    @socketio.on('pause_simulation')
    def handle_pause():
        sid = request.sid
        state, _ = _current_simulation_state(sid)
        if state:
            state['paused'] = True
            socketio.emit('simulation_status', {
                'status': 'paused',
            }, room=sid)

    @socketio.on('resume_simulation')
    def handle_resume():
        sid = request.sid
        state, _ = _current_simulation_state(sid)
        if state:
            state['paused'] = False
            socketio.emit('simulation_status', {
                'status': 'resumed',
            }, room=sid)

    @socketio.on('make_decision')
    def handle_decision(data):
        """
        Player makes a process decision.

        data = {'choice': 'A'}  or  {'choice': 'two'}
        """
        sid = request.sid
        state, lock = _current_simulation_state(sid)
        if state and lock:
            with lock:
                sim = state['sim']
                if sim.pending_decision:
                    choice = data.get('choice', '')
                    sim.apply_decision(
                        sim.pending_decision.decision_type, choice)
            # Resume after decision
            state['paused'] = False
            socketio.emit('simulation_status', {
                'status': 'decision_applied',
                'choice': data.get('choice'),
            }, room=sid)

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
                state['sim'].melt.stir_factor = float(value)
        elif param == 'pO2_mbar' and lock:
            with lock:
                state['sim'].melt.pO2_mbar = float(value)
        elif param == 'c4_max_temp' and lock:
            with lock:
                state['sim'].c4_max_temp_C = float(value)
                state['sim'].campaign_mgr.c4_max_temp_C = float(value)
        elif param == 'campaign_override' and lock:
            # data = {param: 'campaign_override', campaign: 'C2A',
            #         field: 'ramp_rate', value: 10.0}
            campaign_name = data.get('campaign', '')
            field_name = data.get('field', '')
            field_value = data.get('value')
            if campaign_name and field_name:
                with lock:
                    mgr = state['sim'].campaign_mgr
                    if campaign_name not in mgr.overrides:
                        mgr.overrides[campaign_name] = {}
                    mgr.overrides[campaign_name][field_name] = float(field_value)
                    # Apply stir_factor immediately if currently in that campaign
                    if (field_name == 'stir_factor'
                            and state['sim'].melt.campaign.name == campaign_name):
                        state['sim'].melt.stir_factor = float(field_value)
                    # Apply pO₂ immediately if currently in that campaign
                    if (field_name == 'pO2_mbar'
                            and state['sim'].melt.campaign.name == campaign_name):
                        state['sim'].melt.pO2_mbar = float(field_value)

        socketio.emit('simulation_status', {
            'status': 'parameter_adjusted',
            'param': param,
            'value': value,
        }, room=sid)
