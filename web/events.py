"""SocketIO event handlers for the simulator interface."""

import copy
from collections.abc import Mapping
import math
import threading
import uuid
from pathlib import Path

import yaml
try:
    from flask import request
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
from simulator.condensation import KnudsenRegimeRefusal, stage_purity_report
from simulator.furnace_materials import resolve_furnace_max_T_C
from simulator.melt_backend.base import StubBackend
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.recipe_io import RecipeIOError, normalize_recipe_patch
from simulator.runner import RunnerError, _deep_merge_setpoints
from simulator.session import (
    DecisionPolicy,
    SimSession,
    SimSessionConfig,
    drive_session,
    normalize_mre_policy,
)
from simulator.state import MOLAR_MASS
# Goal #18 ``JSON-RUNNER-HARNESS``: the SocketIO stream and the CLI
# runner share ONE per-hour summary builder.  ``SimSession.advance()``
# owns that runner-format summary and returns it in ``StepResult``; this
# adapter only emits it alongside the legacy ``simulation_tick`` payload.
from web.feedstock_data import load_visible_feedstocks
from web.advisory import (
    active_wall_species_from_flue,
    ceramic_rump_payload,
    oxide_wt_pct_from_kg,
    wall_advisory_payload,
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


def apply_loaded_recipe_patch_to_state(
    sid: str,
    patch: Mapping[str, object],
) -> bool:
    state, lock = _current_simulation_state(sid)
    if state is None:
        return False
    normalized = normalize_recipe_patch(
        patch,
        source="recipes/load setpoints_patch",
    )
    if lock is None:
        state["loaded_setpoints_patch"] = normalized
        state["setpoints_patch"] = normalized
        return True
    with lock:
        state["loaded_setpoints_patch"] = normalized
        state["setpoints_patch"] = normalized
    return True


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
    * ``'vaporock'`` / ``'magemin'`` — explicitly **refused**.  Both
      adapters are not wired into a multi-intent dispatcher yet; selecting
      either as the active ``MeltBackend`` would fail closed inside
      ``simulator/core.py::_get_equilibrium`` (their populated
      ``phase_masses_kg`` + ``ledger_transition=None`` returns trip the
      "backend returned post-equilibrium phase material without an
      AtomLedger transition" reject).  Promotion is blocked on
      ``\\goal CHEMISTRY-KERNEL-CARVE-OUT``.
    * ``'internal-analytical'`` (legacy alias ``'stub'``) — deterministic
      ``StubBackend`` selection. Both names fold onto the stable ``stub``
      serialization token via ``canonical_backend_name``.
    * ``'auto'`` / unset — autodetect chain: probe
      AlphaMELTS first, falling back to ``StubBackend`` as the
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
        stub_backend_cls=StubBackend,
    )


def _backend_name_for_session(backend) -> str:
    """Map the web-selected backend instance to SimSession strict name."""
    backend_type = type(backend).__name__
    if isinstance(backend, AlphaMELTSBackend) or backend_type == 'AlphaMELTSBackend':
        return 'alphamelts'
    return 'stub'


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


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
            clean_fields[field_key] = _coerce_bounded_float(
                field_value,
                field=f'runtime_campaign_overrides.{campaign_name}.{field_key}',
            )
        if clean_fields:
            overrides[campaign_name] = clean_fields
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


def _start_background_loop(
    socketio,
    sid: str,
    run_id: str,
    run_lock,
    backend_message: str,
    backend_status: str,
    backend_authoritative: bool,
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
                    _record_last_recipe_capture(
                        sid,
                        run_id,
                        completion_payload=completion_payload,
                    )
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
                except Exception as exc:
                    _safe_log(f'Simulation loop failed: {exc}')
                    error_payload = {
                        'status': 'error',
                        'message': str(exc),
                        'backend_status': backend_status,
                        'backend_authoritative': backend_authoritative,
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
        if data is None:
            data = {}
        elif not isinstance(data, Mapping):
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': 'start_simulation payload must be an object',
            }, room=sid)
            return

        feedstock_key = data.get('feedstock', 'lunar_mare_low_ti')
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
            if setpoints_patch:
                runtime_campaign_overrides: dict[str, dict[str, float]] = {}
            else:
                runtime_campaign_overrides = _coerce_runtime_campaign_overrides(
                    data.get('runtime_campaign_overrides')
                )
        except InputValidationError as exc:
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': str(exc),
            }, room=sid)
            return
        # Default is 'auto' (AlphaMELTS-preferred autodetect per
        # \goal BACKEND-DEFAULT-SWITCH), not 'stub'.  Explicit UI choices
        # ('alphamelts', 'stub') are still honoured.
        backend_name = data.get('backend', 'auto')
        track = data.get('track', 'pyrolysis')

        # Load data files
        feedstocks = load_visible_feedstocks()
        setpoints = dict(_load_yaml('setpoints.yaml'))
        vapor_pressures = _load_yaml('vapor_pressures.yaml')
        materials = _load_yaml('materials.yaml')
        furnace_material_id = str(data.get('furnace_material_id') or '').strip()
        if furnace_material_id:
            try:
                setpoints['furnace_max_T_C'] = resolve_furnace_max_T_C(
                    furnace_material_id,
                    requested_cap=setpoints.get('furnace_max_T_C'),
                )
            except ValueError as exc:
                socketio.emit('simulation_status', {
                    'status': 'error',
                    'message': str(exc),
                }, room=sid)
                return
        if setpoints_patch:
            try:
                setpoints = _deep_merge_setpoints(setpoints, setpoints_patch)
                c4_max_temp = _c4_setpoint_ceiling_T_C(setpoints)
            except RunnerError as exc:
                socketio.emit('simulation_status', {
                    'status': 'error',
                    'message': str(exc),
                }, room=sid)
                return
            except InputValidationError as exc:
                socketio.emit('simulation_status', {
                    'status': 'error',
                    'message': str(exc),
                }, room=sid)
                return

        try:
            backend = _get_backend(backend_name)
        except BackendUnavailableError as exc:
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': str(exc),
                'backend_status': 'unavailable',
                'backend_authoritative': False,
            }, room=sid)
            return
        backend_type = type(backend).__name__
        resolution_status = backend_resolution_status(backend)
        backend_message = ''
        if isinstance(backend, StubBackend):
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
            socketio.emit('simulation_status', {
                'status': 'error',
                'message': str(e),
                'backend_status': resolution_status.backend_status,
                'backend_authoritative': resolution_status.authoritative,
            }, room=sid)
            return
        sim = session.simulator

        state, run_lock = _replace_simulation_state(sid, session, speed)
        run_id = state['run_id']
        state['backend_message'] = backend_message
        state['backend_status'] = resolution_status.backend_status
        state['backend_authoritative'] = resolution_status.authoritative
        state['recipe_inputs'] = _recipe_inputs_payload(
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
        )
        state['setpoints_patch'] = copy.deepcopy(setpoints_patch)
        state['resolved_setpoints_patch'] = _resolved_recipe_patch_for_session(
            setpoints_patch=setpoints_patch,
            setpoints=setpoints,
            runtime_campaign_overrides=runtime_campaign_overrides,
        )

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
        _start_background_loop(
            socketio,
            sid,
            run_id,
            run_lock,
            backend_message,
            resolution_status.backend_status,
            resolution_status.authoritative,
        )

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
        if not state:
            return

        param = data.get('param', '')
        value = data.get('value')

        if param == 'speed':
            try:
                speed = _coerce_bounded_float(
                    value,
                    field='speed',
                    minimum=0.0,
                    maximum=_MAX_SIM_SPEED_SECONDS,
                )
            except InputValidationError as exc:
                _emit_if_current(
                    socketio,
                    sid,
                    state['run_id'],
                    'simulation_status',
                    {'status': 'error', 'message': str(exc)},
                )
                return
            if lock:
                with lock:
                    state['speed'] = speed
            else:
                state['speed'] = speed
            value = speed
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
