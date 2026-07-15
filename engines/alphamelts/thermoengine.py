"""ThermoEngine transport for the AlphaMELTS provider.

This module is a transport selector only.  ThermoEngine stays behind the
existing :class:`AlphaMELTSProvider`; it does not own an intent and it never
emits a ledger transition.
"""

from __future__ import annotations

import faulthandler
import math
import multiprocessing
import pickle
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from simulator.accounting.formulas import resolve_species_formula
from simulator.engine_local_config import (
    cache_version_for,
    setup_thermoengine_dylib_path,
    warn_legacy_once,
)


ActivityConverter = Callable[[float, float, float], float]


_FO2_ECHO_TOLERANCE = 1.0e-3
_FO2_MONOTONIC_EPSILON = 1.0e-7
_FO2_FRACTION_WIDTH_TOLERANCE = 1.0e-10
_DEFAULT_EQUILIBRATE_TIMEOUT_S = 60.0
_FE_O_MOLAR_MASS = 71.8444
_FE2_O3_MOLAR_MASS = 159.6882


_MODEL_TO_THERMOENGINE = {
    'MELTSv1.0.2': ('1.0.2', 'v1.0'),
    'MELTSv1.1.0': ('1.1.0', 'v1.1'),
    'MELTSv1.2.0': ('1.2.0', 'v1.2'),
    'pMELTS': ('5.6.1', 'pMELTS'),
}


def _run_thermoengine_worker(connection: Any, model_name: str,
                             activity_converter: ActivityConverter,
                             equilibrate_timeout_s: float) -> None:
    """Own all native ThermoEngine state inside a killable worker."""
    faulthandler.enable()
    try:
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
            faulthandler.dump_traceback_later(
                equilibrate_timeout_s, exit=False)
            try:
                connection.send((
                    'ok', transport._equilibrate_in_process(**kwargs)))
            except BaseException as exc:
                connection.send((
                    'error', type(exc).__name__, str(exc),
                    traceback.format_exc(),
                ))
            finally:
                faulthandler.cancel_dump_traceback_later()
    except BaseException as exc:  # pragma: no cover - native/bootstrap faults
        connection.send((
            'error', type(exc).__name__, str(exc), traceback.format_exc(),
        ))
    finally:
        connection.close()


@dataclass(frozen=True)
class ThermoEnginePayload:
    """Transport payload ready for ``AlphaMELTSBackend`` emission."""

    phases_present: tuple[str, ...] = ()
    phase_masses_kg: Mapping[str, float] = field(default_factory=dict)
    liquid_fraction: float = 0.0
    liquid_composition_wt_pct: Mapping[str, float] = field(default_factory=dict)
    activity_coefficients: Mapping[str, float] = field(default_factory=dict)
    fe_redox_split: Mapping[str, float] = field(default_factory=dict)
    solved_fO2_log: Optional[float] = None
    phase_universe_size: int = 0
    fO2_solve_count: int = 0
    warnings: tuple[str, ...] = ()


class ThermoEngineTransport:
    """ENKI ThermoEngine MELTS transport for one AlphaMELTS backend."""

    def __init__(
        self,
        *,
        model_name: str = 'MELTSv1.0.2',
        activity_converter: ActivityConverter,
        equilibrate_timeout_s: float = _DEFAULT_EQUILIBRATE_TIMEOUT_S,
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
                self._equilibrate_timeout_s,
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

    def close(self) -> None:
        """Idempotently stop the native worker and close both pipe ends."""
        process = self._worker_process
        connection = self._worker_connection
        self._worker_process = None
        self._worker_connection = None
        if connection is not None:
            try:
                if process is not None and process.is_alive():
                    connection.send(None)
            except (BrokenPipeError, EOFError, OSError):
                pass
            finally:
                connection.close()
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
            # Direct injection keeps pure-Python unit tests lightweight. Real
            # initialized transports always use the isolated worker above.
            return self._equilibrate_in_process(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                comp_wt=comp_wt,
                fO2_log=fO2_log,
                warnings=warnings,
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
                self.close()
                raise TimeoutError(
                    'ThermoEngine equilibrium exceeded hard timeout of '
                    f'{self._equilibrate_timeout_s:g}s'
                )
            message = connection.recv()
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
            liquid_fraction=liquid_fraction,
            liquid_composition_wt_pct=liquid_comp,
            activity_coefficients=activities,
            fe_redox_split=fe_redox_split,
            solved_fO2_log=solved_fO2_log,
            phase_universe_size=len(melts.get_phase_names()),
            fO2_solve_count=fO2_solve_count,
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
        initial_fraction = 2.0 * fe2o3_moles / total_fe_moles
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


def equilibrate_via_thermoengine(
    backend: Any,
    *,
    temperature_C: float,
    pressure_bar: float,
    fO2_log: float,
    composition_mol_by_account: Mapping[str, Mapping[str, float]],
    species_formula_registry: Mapping[str, Any],
) -> Any:
    """Run AlphaMELTS through the ThermoEngine transport mode."""
    mode = getattr(backend, '_mode', None)
    if mode != 'thermoengine':
        raise RuntimeError(
            'equilibrate_via_thermoengine requires backend._mode == '
            f'"thermoengine"; got {mode!r}. Provider must dispatch another '
            'transport instead.'
        )
    return backend.equilibrate(
        temperature_C=float(temperature_C),
        pressure_bar=float(pressure_bar),
        fO2_log=float(fO2_log),
        composition_mol_by_account=composition_mol_by_account,
        species_formula_registry=species_formula_registry,
    )


def thermoengine_available(backend: Any) -> bool:
    """True when the backend has initialized the ThermoEngine path."""
    return getattr(backend, '_mode', None) == 'thermoengine'


__all__ = (
    'ThermoEnginePayload',
    'ThermoEngineTransport',
    'equilibrate_via_thermoengine',
    'thermoengine_available',
)
