"""ThermoEngine transport for the AlphaMELTS provider.

This module is a transport selector only.  ThermoEngine stays behind the
existing :class:`AlphaMELTSProvider`; it does not own an intent and it never
emits a ledger transition.
"""

from __future__ import annotations

import faulthandler
import hashlib
import json
import math
import multiprocessing
import os
import pickle
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from simulator.accounting.formulas import resolve_species_formula
from simulator.engine_local_config import (
    cache_version_for,
    setup_thermoengine_dylib_path,
    warn_legacy_once,
)


ActivityConverter = Callable[[float, float, float], float]


class ThermoEngineIsolationError(RuntimeError):
    """Refusal to run native ThermoEngine outside its killable worker."""


_FO2_ECHO_TOLERANCE = 1.0e-3
_FO2_MONOTONIC_EPSILON = 1.0e-7
_FO2_FRACTION_WIDTH_TOLERANCE = 1.0e-10
_DEFAULT_EQUILIBRATE_TIMEOUT_S = 60.0
_DEFAULT_WATCHDOG_GRACE_S = 0.25
_THERMOENGINE_LOG_DIR_ENV = 'REGOLITH_THERMOENGINE_LOG_DIR'
_FE_O_MOLAR_MASS = 71.8444
_FE2_O3_MOLAR_MASS = 159.6882
_FE2O3_ROUNDOFF_WT_TOLERANCE = 1.0e-12


_MODEL_TO_THERMOENGINE = {
    'MELTSv1.0.2': ('1.0.2', 'v1.0'),
    'MELTSv1.1.0': ('1.1.0', 'v1.1'),
    'MELTSv1.2.0': ('1.2.0', 'v1.2'),
    'pMELTS': ('5.6.1', 'pMELTS'),
}


def thermoengine_diagnostic_log_path(
    log_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Return the aggregate, append-only ThermoEngine diagnostic log path."""
    configured_dir = log_dir or os.environ.get(_THERMOENGINE_LOG_DIR_ENV)
    directory = Path(configured_dir) if configured_dir else Path(
        tempfile.gettempdir(), 'regolith-pyrolysis-simulator')
    return directory / 'thermoengine-diagnostics.log'


def _register_worker_fault_handler(
    error_log_path: str | os.PathLike[str],
    diagnostic_signal: int,
) -> Any:
    path = Path(error_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    errlog = path.open('a', encoding='utf-8', buffering=1)
    try:
        faulthandler.register(
            diagnostic_signal,
            file=errlog,
            all_threads=True,
        )
    except BaseException:
        errlog.close()
        raise
    return errlog


def _append_solve_input_line(
    errlog: Any,
    *,
    worker_id: int,
    temperature_C: float,
    pressure_bar: float,
    comp_wt: Mapping[str, float],
    fO2_log: Optional[float],
) -> None:
    normalized_comp = {
        str(oxide): float(value)
        for oxide, value in sorted(comp_wt.items())
    }
    encoded_comp = json.dumps(
        normalized_comp,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    comp_hash = hashlib.sha256(encoded_comp).hexdigest()[:16]
    timestamp = datetime.now(timezone.utc).isoformat(
        timespec='milliseconds').replace('+00:00', 'Z')
    fO2_value = 'intrinsic' if fO2_log is None else f'{float(fO2_log):g}'
    errlog.write(
        f'worker_id={worker_id} | comp_sha256={comp_hash} | '
        f'T_C={float(temperature_C):g} | P_bar={float(pressure_bar):g} | '
        f'fO2_log={fO2_value} | timestamp={timestamp}\n'
    )
    errlog.flush()


def _run_thermoengine_worker(connection: Any, model_name: str,
                             activity_converter: ActivityConverter,
                             error_log_path: str,
                             diagnostic_signal: int) -> None:
    """Own all native ThermoEngine state inside a killable worker."""
    faulthandler.enable()
    errlog = None
    try:
        errlog = _register_worker_fault_handler(
            error_log_path,
            diagnostic_signal,
        )
        transport = ThermoEngineTransport(
            model_name=model_name,
            activity_converter=activity_converter,
        )
        transport._initialize_in_process()
        connection.send(('ready', transport.engine_version))
        while True:
            try:
                kwargs = connection.recv()
            except EOFError:
                break
            if kwargs is None:
                break
            _append_solve_input_line(
                errlog,
                worker_id=os.getpid(),
                temperature_C=kwargs['temperature_C'],
                pressure_bar=kwargs['pressure_bar'],
                comp_wt=kwargs['comp_wt'],
                fO2_log=kwargs['fO2_log'],
            )
            try:
                connection.send((
                    'ok', transport._equilibrate_in_process(**kwargs)))
            except BaseException as exc:
                connection.send((
                    'error', type(exc).__name__, str(exc),
                    traceback.format_exc(),
                ))
    except BaseException as exc:  # pragma: no cover - native/bootstrap faults
        connection.send((
            'error', type(exc).__name__, str(exc), traceback.format_exc(),
        ))
    finally:
        if errlog is not None:
            try:
                faulthandler.unregister(diagnostic_signal)
            finally:
                errlog.close()
        connection.close()


@dataclass(frozen=True)
class ThermoEnginePayload:
    """Transport payload ready for ``AlphaMELTSBackend`` emission."""

    phases_present: tuple[str, ...] = ()
    phase_masses_kg: Mapping[str, float] = field(default_factory=dict)
    phase_compositions: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    phase_thermo: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    chem_potentials: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    phase_affinities: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    thermodynamic_basis: Mapping[str, Any] = field(default_factory=dict)
    liquid_density_kg_m3: Optional[float] = None
    system_enthalpy: Optional[float] = None
    system_entropy: Optional[float] = None
    system_volume: Optional[float] = None
    system_heat_capacity_Cp: Optional[float] = None
    system_dVdP_m3_bar: Optional[float] = None
    system_dVdT_m3_K: Optional[float] = None
    liquid_fraction: float = 0.0
    liquid_composition_wt_pct: Mapping[str, float] = field(default_factory=dict)
    activity_coefficients: Mapping[str, float] = field(default_factory=dict)
    fe_redox_split: Mapping[str, float] = field(default_factory=dict)
    solved_fO2_log: Optional[float] = None
    phase_universe_size: int = 0
    fO2_solve_count: int = 0
    solver_status: Optional[str] = None
    solver_converged: Optional[bool] = None
    solver_iterations: Optional[int] = None
    warnings: tuple[str, ...] = ()


class ThermoEngineTransport:
    """ENKI ThermoEngine MELTS transport for one AlphaMELTS backend."""

    def __init__(
        self,
        *,
        model_name: str = 'MELTSv1.0.2',
        activity_converter: ActivityConverter,
        equilibrate_timeout_s: float = _DEFAULT_EQUILIBRATE_TIMEOUT_S,
        diagnostic_log_dir: str | os.PathLike[str] | None = None,
        watchdog_grace_s: float = _DEFAULT_WATCHDOG_GRACE_S,
        diagnostic_signal: int = signal.SIGUSR1,
    ) -> None:
        self._model_name = str(model_name or 'MELTSv1.0.2')
        if self._model_name not in _MODEL_TO_THERMOENGINE:
            known = ', '.join(sorted(_MODEL_TO_THERMOENGINE))
            raise ValueError(
                f'unknown ThermoEngine MELTS model {self._model_name!r}; '
                f'expected one of: {known}'
            )
        self._activity_converter = activity_converter
        self._equilibrate_timeout_s = max(
            1.0, float(equilibrate_timeout_s))
        self._diagnostic_log_path = thermoengine_diagnostic_log_path(
            diagnostic_log_dir)
        self._watchdog_grace_s = max(0.0, float(watchdog_grace_s))
        self._diagnostic_signal = int(diagnostic_signal)
        self._thermoengine = None
        self._equilibrate = None
        self._model = None
        self._chem = None
        self._database = None
        self._liq_phase = None
        self._melts_version = '1.0.2'
        self._liq_model = 'v1.0'
        self.engine_version = 'thermoengine unavailable'
        self._health_cache: dict[str, tuple[bool, str, float]] = {}
        self._worker_process = None
        self._worker_connection = None

    def initialize(self) -> bool:
        self.close()
        setup_thermoengine_dylib_path()
        try:
            pickle.dumps(self._activity_converter)
        except (AttributeError, pickle.PickleError, TypeError) as exc:
            raise TypeError(
                'ThermoEngine activity_converter must be pickleable for the '
                'spawned native-solver worker'
            ) from exc
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=_run_thermoengine_worker,
            args=(
                child,
                self._model_name,
                self._activity_converter,
                str(self._diagnostic_log_path),
                self._diagnostic_signal,
            ),
            daemon=True,
        )
        process.start()
        child.close()
        try:
            if not parent.poll(30.0):
                raise TimeoutError(
                    'ThermoEngine initialization exceeded hard timeout of 30s'
                )
            message = parent.recv()
        except (EOFError, OSError, TimeoutError):
            parent.close()
            if process.is_alive():
                process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
            raise
        if message[0] != 'ready':
            if process.is_alive():
                process.terminate()
            process.join(timeout=1.0)
            parent.close()
            raise RuntimeError(
                f'ThermoEngine initialization failed: {message[2]}\n'
                f'{message[3]}'
            )
        self.engine_version = str(message[1])
        self._worker_process = process
        self._worker_connection = parent
        return True

    @property
    def diagnostic_log_path(self) -> Path:
        """Well-known append log harvested after a worker timeout."""
        return self._diagnostic_log_path

    def _dump_then_kill_worker(self) -> None:
        """Request a native traceback, allow it to flush, then hard-kill."""
        process = self._worker_process
        connection = self._worker_connection
        self._worker_process = None
        self._worker_connection = None
        try:
            if process is not None and process.is_alive():
                try:
                    os.kill(process.pid, self._diagnostic_signal)
                except OSError:
                    pass
                time.sleep(self._watchdog_grace_s)
                if process.is_alive():
                    process.kill()
                process.join(timeout=1.0)
        finally:
            if connection is not None:
                connection.close()

    def close(self) -> None:
        """Idempotently stop the native worker and close both pipe ends."""
        process = self._worker_process
        connection = self._worker_connection
        self._worker_process = None
        self._worker_connection = None
        try:
            if connection is not None:
                try:
                    if process is not None and process.is_alive():
                        connection.send(None)
                except (BrokenPipeError, EOFError, OSError):
                    pass
                finally:
                    connection.close()
        finally:
            if process is not None:
                process.join(timeout=1.0)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1.0)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1.0)

    def _initialize_in_process(self) -> bool:
        setup_thermoengine_dylib_path()
        import thermoengine
        from thermoengine import chem, equilibrate, model

        melts_version, liq_model = _MODEL_TO_THERMOENGINE[self._model_name]
        database = model.Database(
            database='Berman',
            liq_mod=liq_model,
            calib=True,
        )
        liq_phase = database.get_phase('Liq')
        # Construct once at initialization so missing Objective-C/C payloads
        # fail before the adapter advertises thermoengine mode.
        equilibrate.MELTSmodel(version=melts_version)

        self._thermoengine = thermoengine
        self._equilibrate = equilibrate
        self._model = model
        self._chem = chem
        self._database = database
        self._liq_phase = liq_phase
        self._melts_version = melts_version
        self._liq_model = liq_model
        config_version = cache_version_for('thermoengine')
        if config_version is not None:
            self.engine_version = config_version
        else:
            module_path = getattr(thermoengine, '__file__', 'unknown')
            self.engine_version = (
                f'thermoengine MELTS {melts_version} '
                f'(liq_mod {liq_model}; {module_path})'
            )
            warn_legacy_once(
                'thermoengine',
                'engines.local.toml absent; using legacy ThermoEngine '
                'path-based identity for cache comparison',
            )
        return True

    def clear_health_cache(self) -> None:
        self._health_cache.clear()

    def health_check(
        self,
        *,
        timeout_s: float = 8.0,
        failure_cache_ttl_s: float = 30.0,
        force_refresh: bool = False,
    ) -> tuple[bool, str]:
        timeout = max(1.0, float(timeout_s))
        cache_key = f'{self._model_name}:{timeout:.3f}'
        cached = self._health_cache.get(cache_key)
        now = time.monotonic()
        if cached is not None and not force_refresh:
            ok, message, cached_at = cached
            ttl = max(0.0, float(failure_cache_ttl_s))
            if ok or (now - cached_at) < ttl:
                return ok, message

        target_fO2_log = -9.0
        code = f"""
from engines.alphamelts.thermoengine import ThermoEngineTransport

def activity_from_mu(_mu, _mu0, _temperature_K):
    return 1.0

transport = ThermoEngineTransport(
    model_name={self._model_name!r},
    activity_converter=activity_from_mu,
)
transport._initialize_in_process()
payload = transport._equilibrate_in_process(
    temperature_C=1200.0,
    pressure_bar=1.0,
    fO2_log={target_fO2_log!r},
    comp_wt={{
        'SiO2': 49.0,
        'TiO2': 1.5,
        'Al2O3': 14.0,
        'FeO': 10.0,
        'Fe2O3': 1.0,
        'MgO': 9.0,
        'CaO': 11.0,
        'Na2O': 2.5,
        'K2O': 0.8,
        'Cr2O3': 0.2,
        'MnO': 0.2,
        'P2O5': 0.3,
    }},
)
if (
    payload.solved_fO2_log is None
    or abs(float(payload.solved_fO2_log) - {target_fO2_log!r}) >= 1.0e-3
):
    raise RuntimeError(
        'ThermoEngine smoke equilibrium did not solve at requested absolute fO2: '
        f'requested={{{target_fO2_log!r}:g}}, '
        f'solved={{payload.solved_fO2_log!r}}'
    )
positive_phase_mass_kg = sum(
    float(value)
    for value in payload.phase_masses_kg.values()
    if float(value) > 0.0
)
if not payload.phases_present or positive_phase_mass_kg <= 0.0:
    raise RuntimeError(
        'ThermoEngine smoke equilibrium returned no positive phase masses'
    )
print('ok')
"""
        try:
            result = subprocess.run(
                [sys.executable, '-c', code],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            health = (
                False,
                f'ThermoEngine smoke equilibrium timed out after '
                f'{timeout:.1f}s',
            )
        except OSError as exc:
            health = (False, f'ThermoEngine smoke equilibrium failed: {exc}')
        else:
            if result.returncode == 0:
                health = (True, 'ThermoEngine smoke equilibrium completed')
            else:
                detail = (result.stderr or result.stdout or '').strip()
                if detail:
                    detail = detail.splitlines()[-1]
                health = (
                    False,
                    'ThermoEngine smoke equilibrium failed'
                    + (f': {detail}' if detail else ''),
                )
        self._health_cache[cache_key] = (health[0], health[1], now)
        return health

    def equilibrate(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        comp_wt: Mapping[str, float],
        fO2_log: Optional[float] = None,
        warnings: tuple[str, ...] = (),
    ) -> ThermoEnginePayload:
        """Equilibrate with a hard deadline around all native solve calls."""
        if self._worker_process is None:
            raise ThermoEngineIsolationError(
                'ThermoEngine equilibrium requires an isolated worker; '
                'in-process native equilibrium is forbidden'
            )
        process = self._worker_process
        connection = self._worker_connection
        if connection is None or not process.is_alive():
            raise RuntimeError('ThermoEngine equilibrium worker is not alive')
        kwargs = {
            'temperature_C': temperature_C,
            'pressure_bar': pressure_bar,
            'comp_wt': dict(comp_wt),
            'fO2_log': fO2_log,
            'warnings': tuple(warnings),
        }
        try:
            connection.send(kwargs)
            if not connection.poll(self._equilibrate_timeout_s):
                self._dump_then_kill_worker()
                raise TimeoutError(
                    'ThermoEngine equilibrium exceeded hard timeout of '
                    f'{self._equilibrate_timeout_s:g}s'
                )
            message = connection.recv()
        except TimeoutError:
            raise
        except (BrokenPipeError, EOFError, OSError) as exc:
            self.close()
            raise RuntimeError(
                'ThermoEngine equilibrium worker exited without a result'
            ) from exc
        if message[0] == 'ok':
            return message[1]
        _tag, exc_name, detail, child_traceback = message
        exception_type = {
            'ImportError': ImportError,
            'TimeoutError': TimeoutError,
            'ValueError': ValueError,
        }.get(exc_name, RuntimeError)
        raise exception_type(
            f'ThermoEngine equilibrium failed: {detail}\n{child_traceback}'
        )

    def _equilibrate_in_process(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        comp_wt: Mapping[str, float],
        fO2_log: Optional[float] = None,
        warnings: tuple[str, ...] = (),
    ) -> ThermoEnginePayload:
        if self._equilibrate is None or self._liq_phase is None:
            raise ImportError('ThermoEngine transport not initialized')

        melts = self._equilibrate.MELTSmodel(version=self._melts_version)
        oxide_names = tuple(str(name) for name in melts.get_oxide_names())
        bulk_wt = {
            oxide: float(comp_wt.get(oxide, 0.0) or 0.0)
            for oxide in oxide_names
            if float(comp_wt.get(oxide, 0.0) or 0.0) > 0.0
        }
        if not bulk_wt:
            raise ValueError('ThermoEngine composition has no MELTS oxides')

        pressure_mpa = max(float(pressure_bar) / 10.0, 1.0e-7)
        pressure_bar = pressure_mpa * 10.0
        solved_fO2_log: Optional[float] = None
        fO2_solve_count = 1
        if fO2_log is None:
            melts.set_bulk_composition(bulk_wt)
            runs = melts.equilibrate_tp(
                float(temperature_C),
                pressure_mpa,
                initialize=True,
            )
        else:
            melts, runs, solved_fO2_log, fO2_solve_count = self._solve_imposed_fO2(
                temperature_C=float(temperature_C),
                pressure_bar=float(pressure_bar),
                pressure_mpa=pressure_mpa,
                bulk_wt=bulk_wt,
                target_fO2_log=float(fO2_log),
            )
        if not runs:
            return ThermoEnginePayload(
                warnings=tuple(warnings) + (
                    'ThermoEngine returned no equilibration result',
                ),
            )

        status, _T_C, _P_MPa, root = runs[0]
        status_text = str(status)
        if 'success' not in status_text.lower():
            return ThermoEnginePayload(
                warnings=tuple(warnings) + (
                    f'ThermoEngine equilibrium status: {status_text}',
                ),
            )

        phases = tuple(str(phase) for phase in melts.get_list_of_phases_in_assemblage(root))
        if fO2_log is None and self._select_liquid_phase(phases) is not None:
            solved_fO2_log = self._echo_log_fO2(
                melts,
                root,
                temperature_C=float(temperature_C),
                pressure_bar=float(pressure_bar),
            )
        phase_masses_kg = {
            phase: float(melts.get_mass_of_phase(root, phase)) / 1000.0
            for phase in phases
            if float(melts.get_mass_of_phase(root, phase)) > 0.0
        }
        total_mass_kg = sum(phase_masses_kg.values())
        liquid_phase = self._select_liquid_phase(phases)
        liquid_mass_kg = phase_masses_kg.get(liquid_phase or '', 0.0)
        liquid_fraction = (
            max(0.0, min(1.0, liquid_mass_kg / total_mass_kg))
            if total_mass_kg > 0.0 else 0.0
        )
        phase_compositions = {
            phase: self._strict_finite_mapping(
                melts.get_composition_of_phase(root, phase, 'oxide_wt'),
                context=f'ThermoEngine {phase} phase composition',
            )
            for phase in phases
        }
        property_names = {
            'gibbs_free_energy_J': 'GibbsFreeEnergy',
            'enthalpy_J': 'Enthalpy',
            'entropy_J_K': 'Entropy',
            'volume_m3': 'Volume',
            'heat_capacity_J_K': 'HeatCapacity',
            'density_kg_m3': 'Density',
            'dVdP_m3_bar': 'DvDp',
            'dVdT_m3_K': 'DvDt',
        }
        phase_thermo: dict[str, dict[str, Any]] = {}
        for phase in phases:
            values = {
                name: self._strict_finite_float(
                    melts.get_property_of_phase(root, phase, property_name),
                    context=f'ThermoEngine {phase} {property_name}',
                )
                for name, property_name in property_names.items()
            }
            # ThermoEngine documents phase Volume in J/bar. Since
            # 1 J / 1 bar = 1e-5 m3, convert here; alphaMELTS table volumes
            # take their separate cm3 -> m3 path before both engines emit the
            # same ``volume_m3`` key.
            values['volume_m3'] *= 1.0e-5
            # ThermoEngine exposes Volume in J/bar, DvDp in J/bar^2,
            # and DvDt in J/(bar K). Since 1 J/bar = 1e-5 m3, the same
            # conversion produces m3/bar and m3/K derivatives.
            values['dVdP_m3_bar'] *= 1.0e-5
            values['dVdT_m3_K'] *= 1.0e-5
            values['density_kg_m3'] *= 1000.0
            values['reference_mass_kg'] = phase_masses_kg[phase]
            values['reference_basis'] = 'thermoengine_solver_phase_amount'
            phase_thermo[phase] = values
        chem_potentials: dict[str, dict[str, Any]] = {}
        for phase in phases:
            raw_mu = self._strict_finite_mapping(
                melts.get_thermo_properties_of_phase_components(
                    root, phase, mode='mu'
                ),
                context=f'ThermoEngine {phase} chemical potentials',
            )
            components = dict(raw_mu)
            source_basis = 'chemical_potential_J_mol'
            conversion: dict[str, Any] = {}
            if len(raw_mu) == 1 and phase in raw_mu:
                formula_payload = melts.get_composition_of_phase(
                    root, phase, 'component'
                )
                formula = str(dict(formula_payload or {}).get('formula') or '')
                if not formula:
                    raise ValueError(
                        f'ThermoEngine pure phase {phase!r} lacks a formula '
                        'needed to convert specific Gibbs energy to J/mol'
                    )
                molar_mass_g_mol = (
                    resolve_species_formula(formula, None)
                    .molar_mass_kg_per_mol() * 1000.0
                )
                # ThermoEngine mode='mu' returns solution endmember chemical
                # potentials in J/mol, but a pure phase as G/mass in J/g.
                # Multiplying J/g by formula molar mass g/mol gives J/mol, so
                # every emitted component below has one chemical-potential
                # reference basis instead of a phase-dependent bare float.
                components = {
                    phase: float(raw_mu[phase]) * molar_mass_g_mol,
                }
                source_basis = 'specific_gibbs_energy_J_g'
                conversion = {
                    'formula': formula,
                    'molar_mass_g_mol': molar_mass_g_mol,
                }
            chem_potentials[phase] = {
                'basis': 'chemical_potential',
                'units': 'J/mol',
                'source_basis': source_basis,
                'components': components,
                **conversion,
            }
        raw_affinities = melts.get_dictionary_of_affinities(root, sort=False)
        if not isinstance(raw_affinities, Mapping):
            raise ValueError(
                'ThermoEngine phase affinities must be a mapping; '
                f'got {type(raw_affinities).__name__}'
            )
        phase_affinities: dict[str, dict[str, Any]] = {}
        for phase, raw_value in raw_affinities.items():
            if not isinstance(raw_value, (list, tuple)) or len(raw_value) != 2:
                raise ValueError(
                    f'ThermoEngine phase affinity {phase!r} must be '
                    f'(affinity, composition); got {raw_value!r}'
                )
            affinity, composition = raw_value
            affinity_value = self._strict_finite_float(
                affinity,
                context=f'ThermoEngine {phase} phase affinity',
            )
            sentinel = affinity_value == 999999.0
            phase_affinities[str(phase)] = {
                # get_dictionary_of_affinities contains phases absent from the
                # assemblage and rewrites a native zero affinity to 999999.
                # Restore that zero and label its state; never expose the
                # sentinel as a physical delta-G value. The companion string is
                # a phase formula, not mole fractions.
                'affinity_J': 0.0 if sentinel else affinity_value,
                'state': 'zero_affinity_sentinel' if sentinel else 'undersaturated',
                'phase_scope': 'not_in_equilibrium_assemblage',
                'composition_formula': str(composition),
            }
        system_enthalpy = sum(
            float(values['enthalpy_J']) for values in phase_thermo.values()
        )
        system_entropy = sum(
            float(values['entropy_J_K']) for values in phase_thermo.values()
        )
        system_volume = sum(
            float(values['volume_m3']) for values in phase_thermo.values()
        )
        system_heat_capacity = sum(
            float(values['heat_capacity_J_K'])
            for values in phase_thermo.values()
        )
        system_dVdP = sum(
            float(values['dVdP_m3_bar']) for values in phase_thermo.values()
        )
        system_dVdT = sum(
            float(values['dVdT_m3_K']) for values in phase_thermo.values()
        )
        thermodynamic_basis = {
            'reference_basis': 'thermoengine_solver_system_amount',
            'reference_mass_kg': total_mass_kg,
            'system_enthalpy': {'units': 'J'},
            'system_entropy': {'units': 'J/K'},
            'system_volume': {'units': 'm3', 'source_units': 'J/bar'},
            'system_heat_capacity_Cp': {'units': 'J/K'},
            'system_dVdP_m3_bar': {'units': 'm3/bar'},
            'system_dVdT_m3_K': {'units': 'm3/K'},
        }
        # Autoreview r4 P2 (2026-05-27): only emit a liquid composition
        # / activities / Fe-redox split when ThermoEngine actually
        # reports a liquid phase.  The prior code fell back to the
        # bulk-oxide composition whenever ``liquid_comp`` was empty,
        # which (a) fabricated a liquid composition for subsolidus or
        # fully crystallized assemblages, then (b) derived activities
        # and Fe redox from that fabrication, breaking the
        # liquid_fraction-driven freeze-gate diagnostic (callers could
        # not distinguish a real liquid from a fabricated one).  Now
        # the bulk-fallback only fires when a liquid phase IS reported
        # but the composition API returned an incomplete payload, and
        # the situation surfaces as a warning so it is auditable.
        extra_warnings: tuple[str, ...] = ()
        if liquid_phase:
            liquid_comp = self._finite_mapping(
                melts.get_composition_of_phase(root, liquid_phase, 'oxide_wt'))
            if not liquid_comp:
                liquid_comp = dict(bulk_wt)
                extra_warnings = (
                    'ThermoEngine reported liquid phase '
                    f'{liquid_phase!r} but composition_of_phase returned '
                    'an empty payload; falling back to bulk composition.',
                )
            liquid_components = self._finite_mapping(
                melts.get_composition_of_phase(root, liquid_phase, 'component'))
        else:
            # No liquid phase: leave composition + activities + Fe redox
            # empty.  Subsolidus / fully crystallized states surface as
            # ``liquid_fraction=0`` with an empty ``liquid_composition_wt_pct``,
            # which is exactly what downstream consumers (freeze-gate,
            # evaporation flux gate) need to refuse evaporation cleanly.
            liquid_comp = {}
            liquid_components = {}

        if liquid_phase and liquid_comp:
            activities = self._activities_from_chemical_potentials(
                temperature_C=float(temperature_C),
                pressure_bar=float(pressure_bar),
                component_mole_fraction=liquid_components,
                comp_wt=liquid_comp,
            )
            fe_redox_split = self._fe_redox_split(liquid_comp)
        else:
            activities = {}
            fe_redox_split = {}

        return ThermoEnginePayload(
            phases_present=phases,
            phase_masses_kg=phase_masses_kg,
            phase_compositions=phase_compositions,
            phase_thermo=phase_thermo,
            chem_potentials=chem_potentials,
            phase_affinities=phase_affinities,
            thermodynamic_basis=thermodynamic_basis,
            liquid_density_kg_m3=(
                None
                if liquid_phase is None
                else float(phase_thermo[liquid_phase]['density_kg_m3'])
            ),
            system_enthalpy=system_enthalpy,
            system_entropy=system_entropy,
            system_volume=system_volume,
            system_heat_capacity_Cp=system_heat_capacity,
            system_dVdP_m3_bar=system_dVdP,
            system_dVdT_m3_K=system_dVdT,
            liquid_fraction=liquid_fraction,
            liquid_composition_wt_pct=liquid_comp,
            activity_coefficients=activities,
            fe_redox_split=fe_redox_split,
            solved_fO2_log=solved_fO2_log,
            phase_universe_size=len(melts.get_phase_names()),
            fO2_solve_count=fO2_solve_count,
            solver_status=status_text,
            solver_converged=True,
            # MELTSmodel returns convergence status but exposes no iteration
            # count in its public result or XML tree.
            solver_iterations=None,
            warnings=tuple(warnings) + extra_warnings + (
                f'ThermoEngine status: {status_text}',
            ),
        )

    def _solve_imposed_fO2(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        pressure_mpa: float,
        bulk_wt: Mapping[str, float],
        target_fO2_log: float,
    ) -> tuple[Any, Any, float, int]:
        if not math.isfinite(target_fO2_log):
            raise ValueError('ThermoEngine absolute fO2 target must be finite')
        feo_moles = float(bulk_wt.get('FeO', 0.0) or 0.0) / _FE_O_MOLAR_MASS
        fe2o3_moles = (
            float(bulk_wt.get('Fe2O3', 0.0) or 0.0) / _FE2_O3_MOLAR_MASS
        )
        total_fe_moles = feo_moles + 2.0 * fe2o3_moles
        if total_fe_moles <= 0.0:
            raise ValueError(
                'ThermoEngine cannot impose absolute fO2 without FeO/Fe2O3'
            )
        from simulator.fe_redox import (
            kress91_split,
            melt_mol_fractions_for_kress91,
        )

        seed = kress91_split(
            fO2_log=target_fO2_log,
            mol_fractions=melt_mol_fractions_for_kress91(bulk_wt),
            T_K=temperature_C + 273.15,
            pressure_bar=pressure_bar,
        )
        initial_fraction = float(seed['fe3'])
        if not math.isfinite(initial_fraction) or not 0.0 < initial_fraction < 1.0:
            raise ValueError(
                'ThermoEngine Kress91 fO2 seed must have a positive finite '
                'ferric fraction below one'
            )
        solve_count = 0

        def evaluate(ferric_fraction: float) -> tuple[Any, Any, float]:
            nonlocal solve_count
            candidate = dict(bulk_wt)
            candidate['Fe2O3'] = (
                total_fe_moles * ferric_fraction / 2.0 * _FE2_O3_MOLAR_MASS
            )
            candidate['FeO'] = (
                total_fe_moles * (1.0 - ferric_fraction) * _FE_O_MOLAR_MASS
            )
            model = self._equilibrate.MELTSmodel(version=self._melts_version)
            model.set_bulk_composition(candidate)
            result = model.equilibrate_tp(
                temperature_C,
                pressure_mpa,
                initialize=True,
            )
            solve_count += 1
            if not result:
                raise RuntimeError('ThermoEngine returned no equilibration result')
            status, _T_C, _P_MPa, root = result[0]
            if 'success' not in str(status).lower():
                raise RuntimeError(f'ThermoEngine equilibrium status: {status}')
            solved = self._echo_log_fO2(
                model,
                root,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
            )
            return model, result, solved

        def require_unique(
            point: tuple[float, float, Any, Any],
            lower: tuple[float, float, Any, Any] | None,
            upper: tuple[float, float, Any, Any] | None,
        ) -> tuple[Any, Any, float, int]:
            probes = [point]
            for bound in (lower, upper):
                if bound is None:
                    continue
                distance = abs(point[0] - bound[0])
                direction = -1.0 if bound[0] < point[0] else 1.0
                for scale in (0.5, 0.1, 0.01, 0.001):
                    probe_fraction = point[0] + direction * distance * scale
                    if abs(probe_fraction - point[0]) <= (
                        _FO2_FRACTION_WIDTH_TOLERANCE
                    ):
                        continue
                    probe_model, probe_result, probe_solved = evaluate(
                        probe_fraction)
                    probes.append((
                        probe_fraction,
                        probe_solved,
                        probe_model,
                        probe_result,
                    ))
            self._validate_fO2_order(probes)
            return point[2], point[3], point[1], solve_count

        initial_model, initial_result, initial_solved = evaluate(initial_fraction)
        initial_point = (
            initial_fraction,
            initial_solved,
            initial_model,
            initial_result,
        )
        sampled = [initial_point]
        local_fractions = (
            max(1.0e-8, 0.5 * initial_fraction),
            min(
                0.99999999,
                initial_fraction + 0.1 * (1.0 - initial_fraction),
            ),
        )
        for sample_fraction in local_fractions:
            if any(
                abs(sample_fraction - point[0])
                <= _FO2_FRACTION_WIDTH_TOLERANCE
                for point in sampled
            ):
                continue
            sample_model, sample_result, sample_solved = evaluate(sample_fraction)
            sampled.append((
                sample_fraction,
                sample_solved,
                sample_model,
                sample_result,
            ))
        sampled.sort(key=lambda item: item[0])
        self._validate_fO2_order(sampled)
        if abs(initial_solved - target_fO2_log) < _FO2_ECHO_TOLERANCE:
            initial_index = sampled.index(initial_point)
            return require_unique(
                initial_point,
                sampled[initial_index - 1] if initial_index > 0 else None,
                (
                    sampled[initial_index + 1]
                    if initial_index + 1 < len(sampled)
                    else None
                ),
            )
        expansion_fractions = (
            (
                0.1 * initial_fraction,
                0.01 * initial_fraction,
                1.0e-8,
            )
            if target_fO2_log < initial_solved
            else (
                initial_fraction + 0.5 * (1.0 - initial_fraction),
                initial_fraction + 0.9 * (1.0 - initial_fraction),
                initial_fraction + 0.99 * (1.0 - initial_fraction),
                0.99999999,
            )
        )
        for sample_fraction in expansion_fractions:
            if sampled[0][1] <= target_fO2_log <= sampled[-1][1]:
                break
            if any(
                abs(sample_fraction - point[0])
                <= _FO2_FRACTION_WIDTH_TOLERANCE
                for point in sampled
            ):
                continue
            sample_model, sample_result, sample_solved = evaluate(sample_fraction)
            sampled.append((
                sample_fraction,
                sample_solved,
                sample_model,
                sample_result,
            ))
            sampled.sort(key=lambda item: item[0])
            self._validate_fO2_order(sampled)
        if not sampled[0][1] <= target_fO2_log <= sampled[-1][1]:
            raise ValueError(
                'ThermoEngine absolute fO2 target is outside the attainable '
                f'Fe-redox bracket: requested={target_fO2_log:g}'
            )
        for index, point in enumerate(sampled):
            if abs(point[1] - target_fO2_log) < _FO2_ECHO_TOLERANCE:
                return require_unique(
                    point,
                    sampled[index - 1] if index > 0 else None,
                    sampled[index + 1] if index + 1 < len(sampled) else None,
                )
        left, right = next(
            (lower, upper)
            for lower, upper in zip(sampled, sampled[1:])
            if lower[1] <= target_fO2_log <= upper[1]
        )

        for _iteration in range(48):
            echo_width = right[1] - left[1]
            if echo_width <= 2.0 * _FO2_ECHO_TOLERANCE:
                closest = min(
                    (left, right),
                    key=lambda item: abs(item[1] - target_fO2_log),
                )
                if abs(closest[1] - target_fO2_log) < _FO2_ECHO_TOLERANCE:
                    return closest[2], closest[3], closest[1], solve_count
                break
            if right[0] - left[0] <= _FO2_FRACTION_WIDTH_TOLERANCE:
                raise RuntimeError(
                    'ThermoEngine fO2 bracket collapsed in ferric-fraction '
                    'space before its fO2 width converged'
                )
            fraction = 0.5 * (left[0] + right[0])
            model, result, solved = evaluate(fraction)
            point = (fraction, solved, model, result)
            self._validate_fO2_order((left, point, right))
            if abs(solved - target_fO2_log) < _FO2_ECHO_TOLERANCE:
                return require_unique(point, left, right)
            if solved < target_fO2_log:
                left = point
            else:
                right = point
        raise RuntimeError(
            'ThermoEngine failed to impose absolute fO2 within tolerance: '
            f'requested={target_fO2_log:g}, '
            f'bracket={left[1]:g}..{right[1]:g}, '
            f'tolerance={_FO2_ECHO_TOLERANCE:g}'
        )

    @staticmethod
    def _validate_fO2_order(points: Any) -> None:
        ordered = sorted(points, key=lambda item: item[0])
        for lower, upper in zip(ordered, ordered[1:]):
            delta = float(upper[1]) - float(lower[1])
            if delta <= _FO2_MONOTONIC_EPSILON:
                raise ValueError(
                    'ThermoEngine non-monotonic/buffered fO2 region: '
                    f'ferric fractions {lower[0]:.8g}..{upper[0]:.8g} '
                    f'echo {lower[1]:.8g}..{upper[1]:.8g}'
                )

    def _echo_log_fO2(
        self,
        melts: Any,
        root: Any,
        *,
        temperature_C: float,
        pressure_bar: float,
    ) -> float:
        if self._database is None or self._chem is None:
            raise ImportError('ThermoEngine redox model not initialized')
        phases = tuple(
            str(name) for name in melts.get_list_of_phases_in_assemblage(root)
        )
        liquid_phase = self._select_liquid_phase(phases)
        if liquid_phase is None:
            raise ValueError(
                'ThermoEngine cannot echo imposed fO2 without a liquid phase'
            )
        liquid = self._finite_mapping(
            melts.get_composition_of_phase(root, liquid_phase, 'oxide_wt')
        )
        liquid_fe2o3 = float(liquid.get('Fe2O3', 0.0) or 0.0)
        if liquid_fe2o3 < -_FE2O3_ROUNDOFF_WT_TOLERANCE:
            raise ValueError(
                'ThermoEngine liquid Fe2O3 is physically negative beyond the '
                f'{_FE2O3_ROUNDOFF_WT_TOLERANCE:g} wt% roundoff tolerance'
            )
        if liquid_fe2o3 < 0.0:
            # MELTS normalization can leave ~1e-14 wt% negative Fe2O3.  A
            # 1e-12 wt% tolerance is two orders larger than that observed
            # residue yet eleven orders below a 0.1 wt% reportable oxide.
            # Clamp only to the physical zero-ferric boundary; never invent a
            # positive ferric inventory to make Kress91 logarithms finite.
            liquid['Fe2O3'] = 0.0
            liquid_fe2o3 = 0.0
        if liquid_fe2o3 == 0.0:
            raise ValueError(
                'ThermoEngine liquid Fe2O3 is at the zero-ferric limiting '
                'state; finite Kress91 fO2 echo is undefined'
            )
        import numpy as np

        wt = np.array([
            float(liquid.get(str(oxide), 0.0) or 0.0)
            for oxide in self._chem.OXIDE_ORDER
        ])
        mol = self._chem.wt_to_mol_oxide(wt)
        solved = float(np.asarray(self._database.redox_state(
            np.array([temperature_C + 273.15]),
            np.array([pressure_bar]),
            oxide_comp={'Liq': mol},
            phase_of_interest='Liq',
            method='Kress91',
        )).reshape(-1)[0])
        if not math.isfinite(solved):
            raise ValueError('ThermoEngine returned a non-finite fO2 echo')
        return solved

    def _activities_from_chemical_potentials(
        self,
        *,
        temperature_C: float,
        pressure_bar: float,
        component_mole_fraction: Mapping[str, float],
        comp_wt: Mapping[str, float],
    ) -> dict[str, float]:
        liq_phase = self._liq_phase
        if liq_phase is None:
            return {}

        endmember_names = tuple(str(name) for name in liq_phase.endmember_names)
        mol = [
            float(component_mole_fraction.get(name, 0.0) or 0.0)
            for name in endmember_names
        ]
        if sum(mol) <= 0.0:
            mol = self._endmember_moles_from_wt(comp_wt, endmember_names)
        total = sum(value for value in mol if value > 0.0)
        if total <= 0.0:
            return {}
        mol = [max(0.0, value) / total for value in mol]

        import numpy as np

        T_K = float(temperature_C) + 273.15
        P_bar = max(float(pressure_bar), 1.0e-6)
        mu_values = np.asarray(
            liq_phase.chem_potential(T_K, P_bar, mol=[mol]),
            dtype=float,
        ).reshape(-1)
        activities: dict[str, float] = {}
        for idx, name in enumerate(endmember_names):
            if idx >= len(mu_values):
                continue
            pure = [0.0 for _ in endmember_names]
            pure[idx] = 1.0
            mu0_values = np.asarray(
                liq_phase.gibbs_energy(T_K, P_bar, mol=[pure]),
                dtype=float,
            ).reshape(-1)
            if len(mu0_values) == 0:
                continue
            try:
                activity = self._activity_converter(
                    float(mu_values[idx]),
                    float(mu0_values[0]),
                    T_K,
                )
            except (OverflowError, ValueError):
                continue
            if activity > 0.0 and math.isfinite(activity):
                activities[name] = activity
        return activities

    def _endmember_moles_from_wt(
        self,
        comp_wt: Mapping[str, float],
        endmember_names: tuple[str, ...],
    ) -> list[float]:
        mol: list[float] = []
        for name in endmember_names:
            wt = float(comp_wt.get(name, 0.0) or 0.0)
            if wt <= 0.0:
                mol.append(0.0)
                continue
            try:
                molar_mass_g_per_mol = (
                    resolve_species_formula(
                        name,
                        None,
                    ).molar_mass_kg_per_mol()
                    * 1000.0
                )
            except Exception:
                mol.append(0.0)
                continue
            mol.append(wt / molar_mass_g_per_mol if molar_mass_g_per_mol > 0.0 else 0.0)
        return mol

    def _fe_redox_split(self, liquid_comp: Mapping[str, float]) -> dict[str, float]:
        feo_wt = float(liquid_comp.get('FeO', 0.0) or 0.0)
        fe2o3_wt = float(liquid_comp.get('Fe2O3', 0.0) or 0.0)
        if fe2o3_wt < -_FE2O3_ROUNDOFF_WT_TOLERANCE:
            raise ValueError(
                'ThermoEngine liquid Fe2O3 is physically negative beyond the '
                f'{_FE2O3_ROUNDOFF_WT_TOLERANCE:g} wt% roundoff tolerance'
            )
        if fe2o3_wt < 0.0:
            fe2o3_wt = 0.0
        feo_mol = self._oxide_mol('FeO', feo_wt)
        fe2o3_mol = self._oxide_mol('Fe2O3', fe2o3_wt)
        total_fe_mol = feo_mol + 2.0 * fe2o3_mol
        split = {
            'FeO_wt_pct': feo_wt,
            'Fe2O3_wt_pct': fe2o3_wt,
        }
        if total_fe_mol > 0.0:
            split['Fe3Fet_Liq'] = (2.0 * fe2o3_mol) / total_fe_mol
        return split

    def _oxide_mol(self, oxide: str, wt: float) -> float:
        if wt <= 0.0:
            return 0.0
        try:
            molar_mass_g_per_mol = (
                resolve_species_formula(oxide, None).molar_mass_kg_per_mol()
                * 1000.0
            )
        except Exception:
            return 0.0
        return wt / molar_mass_g_per_mol if molar_mass_g_per_mol > 0.0 else 0.0

    def _select_liquid_phase(self, phases: tuple[str, ...]) -> Optional[str]:
        for phase in phases:
            if phase.lower() == 'liquid':
                return phase
        for phase in phases:
            if phase.lower().startswith('liquid'):
                return phase
        return None

    def _finite_mapping(self, values: Mapping[str, Any]) -> dict[str, float]:
        result: dict[str, float] = {}
        for key, value in dict(values or {}).items():
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                result[str(key)] = number
        return result

    def _strict_finite_mapping(
        self,
        values: Mapping[str, Any],
        *,
        context: str,
    ) -> dict[str, float]:
        if not isinstance(values, Mapping):
            raise ValueError(
                f'{context} must be a mapping; got {type(values).__name__}'
            )
        return {
            str(key): self._strict_finite_float(
                value,
                context=f'{context} {key!r}',
            )
            for key, value in values.items()
        }

    @staticmethod
    def _strict_finite_float(value: Any, *, context: str) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{context} is not numeric: {value!r}') from exc
        if not math.isfinite(result):
            raise ValueError(f'{context} is not finite: {value!r}')
        return result


def equilibrate_via_thermoengine(
    backend: Any,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float | None,
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
    species_formula_registry: Mapping[str, Any],
) -> Any:
    """Run a standalone ThermoEngine backend."""
    backend_name = getattr(backend, 'backend_name', None)
    if backend_name != 'thermoengine':
        raise RuntimeError(
            'equilibrate_via_thermoengine requires backend_name == '
            f'"thermoengine"; got {backend_name!r}. Provider must dispatch another '
            'transport instead.'
        )
    return backend.equilibrate(
        temperature_C=float(temperature_C),
        pressure_bar=float(pressure_bar),
        fO2_log=None if fO2_log is None else float(fO2_log),
        composition_mol_by_account=composition_mol_by_account,
        species_formula_registry=species_formula_registry,
    )


def thermoengine_available(backend: Any) -> bool:
    """True when the backend has initialized the ThermoEngine path."""
    return (
        getattr(backend, 'backend_name', None) == 'thermoengine'
        and bool(backend.is_available())
    )


__all__ = (
    'ThermoEnginePayload',
    'ThermoEngineTransport',
    'equilibrate_via_thermoengine',
    'thermoengine_diagnostic_log_path',
    'thermoengine_available',
)
