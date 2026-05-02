"""SocketIO event handlers for the simulator interface."""

import threading
import time
from pathlib import Path

import yaml
from flask import request

from simulator.core import PyrolysisSimulator, CampaignPhase
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.alphamelts import AlphaMELTSBackend


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


def _get_backend(backend_name: str):
    """Create and initialize the requested melt backend."""
    if backend_name == 'alphamelts':
        backend = AlphaMELTSBackend()
        if backend.initialize({}):
            return backend
    # Fallback to stub
    backend = StubBackend()
    backend.initialize({})
    return backend


def register_events(socketio):
    """Register all SocketIO events for the simulator UI."""

    @socketio.on('connect')
    def handle_connect():
        print(f"Client connected: {request.sid}")

    @socketio.on('disconnect')
    def handle_disconnect():
        sid = request.sid
        print(f"Client disconnected: {sid}")
        # Clean up simulation state
        _simulations.pop(sid, None)
        _sim_locks.pop(sid, None)

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

        # Create simulator
        backend = _get_backend(backend_name)
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

        # Store state
        _simulations[sid] = {
            'sim': sim,
            'running': True,
            'paused': False,
            'speed': speed,
        }
        _sim_locks[sid] = threading.Lock()

        socketio.emit('simulation_status', {
            'status': 'started',
            'feedstock': feedstock_key,
            'mass_kg': mass_kg,
        }, room=sid)

        # Start background loop
        def run_loop():
            while True:
                state = _simulations.get(sid)
                if state is None or not state['running']:
                    break
                if state['paused']:
                    time.sleep(0.1)
                    continue

                with _sim_locks[sid]:
                    sim = state['sim']
                    if sim.is_complete():
                        socketio.emit('simulation_complete', {
                            'total_hours': sim.melt.hour,
                            'energy_kWh': sim.energy_cumulative_kWh,
                            'oxygen_kg': sim.oxygen_cumulative_kg,
                            'products': {k: round(v, 2)
                                         for k, v in sim.train.total_by_species().items()},
                        }, room=sid)
                        state['running'] = False
                        break

                    snapshot = sim.step()

                # Emit tick to client
                tick_data = {
                    'hour': snapshot.hour,
                    'campaign': snapshot.campaign.name,
                    'temperature_C': round(snapshot.temperature_C, 1),
                    'melt_mass_kg': round(snapshot.melt_mass_kg, 1),
                    'composition_wt_pct': {
                        k: round(v, 2) for k, v in snapshot.composition_wt_pct.items()
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
                    # Gas train feedback
                    'ramp_throttled': snapshot.ramp_throttled,
                    'nominal_ramp_rate': round(snapshot.nominal_ramp_rate_C_hr, 2),
                    'actual_ramp_rate': round(snapshot.actual_ramp_rate_C_hr, 2),
                    'throttle_reason': snapshot.throttle_reason,
                    'O2_vented_kg_hr': round(snapshot.O2_vented_kg_hr, 4),
                    'O2_vented_cumulative_kg': round(snapshot.O2_vented_cumulative_kg, 2),
                    'O2_stored_kg': round(snapshot.O2_stored_kg, 2),
                    'turbine_limited': snapshot.overhead.turbine_limited,
                    'turbine_utilization_pct': round(snapshot.overhead.turbine_utilization_pct, 1),
                    'transport_saturation_pct': round(snapshot.overhead.transport_saturation_pct, 1),
                    'turbine_shaft_power_kW': round(snapshot.turbine_shaft_power_kW, 4),
                    # Alkali shuttle (C3)
                    'shuttle_phase': snapshot.shuttle_phase,
                    'shuttle_injected_kg_hr': round(snapshot.shuttle_injected_kg_hr, 3),
                    'shuttle_reduced_kg_hr': round(snapshot.shuttle_reduced_kg_hr, 3),
                    'shuttle_metal_produced_kg_hr': round(snapshot.shuttle_metal_produced_kg_hr, 3),
                    'shuttle_K_inventory_kg': round(snapshot.shuttle_K_inventory_kg, 2),
                    'shuttle_Na_inventory_kg': round(snapshot.shuttle_Na_inventory_kg, 2),
                    'shuttle_cycle': snapshot.shuttle_cycle,
                    # MRE electrolysis state
                    'mre_voltage_V': round(snapshot.mre_voltage_V, 3),
                    'mre_current_A': round(snapshot.mre_current_A, 1),
                    'mre_metals_kg_hr': {k: round(v, 4) for k, v in snapshot.mre_metals_kg_hr.items()},
                    'mre_energy_kWh': round(snapshot.energy.mre_kWh, 4),
                }
                socketio.emit('simulation_tick', tick_data, room=sid)

                # Check for campaign completion summary
                if sim._last_campaign_summary is not None:
                    socketio.emit('campaign_complete_summary',
                                  sim._last_campaign_summary, room=sid)
                    sim._last_campaign_summary = None

                # Check for decision points
                if sim.paused_for_decision and sim.pending_decision:
                    d = sim.pending_decision
                    socketio.emit('decision_required', {
                        'type': d.decision_type.name,
                        'options': d.options,
                        'recommendation': d.recommendation,
                        'context': d.context,
                    }, room=sid)
                    state['paused'] = True

                # Pace the simulation
                spd = state.get('speed', 1.0)
                if spd > 0:
                    time.sleep(spd)

        thread = threading.Thread(target=run_loop, daemon=True)
        thread.start()

    @socketio.on('pause_simulation')
    def handle_pause():
        sid = request.sid
        state = _simulations.get(sid)
        if state:
            state['paused'] = True
            socketio.emit('simulation_status', {
                'status': 'paused',
            }, room=sid)

    @socketio.on('resume_simulation')
    def handle_resume():
        sid = request.sid
        state = _simulations.get(sid)
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
        state = _simulations.get(sid)
        if state and _sim_locks.get(sid):
            with _sim_locks[sid]:
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
        state = _simulations.get(sid)
        if not state:
            return

        param = data.get('param', '')
        value = data.get('value')

        if param == 'speed':
            state['speed'] = float(value)
        elif param == 'stir_factor' and _sim_locks.get(sid):
            with _sim_locks[sid]:
                state['sim'].melt.stir_factor = float(value)
        elif param == 'pO2_mbar' and _sim_locks.get(sid):
            with _sim_locks[sid]:
                state['sim'].melt.pO2_mbar = float(value)
        elif param == 'c4_max_temp' and _sim_locks.get(sid):
            with _sim_locks[sid]:
                state['sim'].c4_max_temp_C = float(value)
                state['sim'].campaign_mgr.c4_max_temp_C = float(value)
        elif param == 'campaign_override' and _sim_locks.get(sid):
            # data = {param: 'campaign_override', campaign: 'C2A',
            #         field: 'ramp_rate', value: 10.0}
            campaign_name = data.get('campaign', '')
            field_name = data.get('field', '')
            field_value = data.get('value')
            if campaign_name and field_name:
                with _sim_locks[sid]:
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
