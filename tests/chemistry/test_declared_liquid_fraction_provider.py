"""The gate's declared physics must not depend on engine discovery."""

from copy import deepcopy
from dataclasses import replace
import builtins
import errno
import multiprocessing.process
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from engines.magemin import MAGEMinShadowProvider
from simulator.chemistry.kernel import normalize_chemistry_kernel_config
from simulator.evaporation import EvaporationFluxRefusal
from simulator.fe_redox import KRESS91_LIQUID_CALIBRATION_MIN_T_C
from simulator.engine_pool import (
    EngineWorkerPool,
    EngineWorkerTimeout,
    WarmEngineWorker,
)
from simulator.melt_backend.liquidus import LiquidusSampleError
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.magemin import MAGEMinBackend
import simulator.melt_backend.magemin as magemin_module
from simulator.melt_backend.magemin import (
    _bootstrap_magemin_subprocess_worker,
    _handle_magemin_subprocess_request,
)
from simulator.chemistry.kernel import ChemistryIntent
from simulator.reduced_real_determinism import (
    GateCurveProviderOutcome,
    PT0DeterminismStore,
    gate_curve_provider_outcome_for_status,
)
from simulator.runner import PyrolysisRun, build_per_hour_summary
from simulator.accounting.run_artifact import build_run_artifact
from simulator.optimize.evalspec import EvalSpec, canonical_evalspec_json
from simulator.optimize.evaluate import _build_eval_inputs
import simulator.optimize.evaluate as evaluate_module
from simulator.config import load_config_bundle
from simulator.optimize.recipe import RecipePatch, RecipeSchema
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim
from tests.test_optimizer_evalspec import _mre_cap_profile


def _sim(provider, vapor_pressure_data, feedstocks_data, setpoints_data):
    setpoints = deepcopy(setpoints_data)
    if provider is not None:
        setpoints['chemistry_kernel']['liquid_fraction_provider'] = provider
    return _build_sim(
        'lunar_mare_low_ti', vapor_pressure_data, feedstocks_data, setpoints,
    )


def _injected_launch_exception(name):
    if name == 'OSError':
        return OSError(errno.EMFILE, 'injected launch failure')
    if name == 'TimeoutExpired':
        return subprocess.TimeoutExpired('/fake/MAGEMin', 1.0)
    if name == 'CalledProcessError':
        return subprocess.CalledProcessError(2, ['/fake/MAGEMin'])
    if name in {'BlockingIOError', 'ConnectionResetError', 'InterruptedError'}:
        number = {
            'BlockingIOError': errno.EAGAIN,
            'ConnectionResetError': errno.ECONNRESET,
            'InterruptedError': errno.EINTR,
        }[name]
        return getattr(builtins, name)(number, 'injected launch failure')
    if name == 'MemoryError':
        return getattr(builtins, name)('injected launch failure')
    if name == 'TypeError':
        return TypeError('injected programming error')
    if name == 'SubprocessError':
        return subprocess.SubprocessError('injected launch failure')
    raise AssertionError(f'unhandled injected exception {name}')


def _bootstrap_magemin_launch_failure_worker(
    binary_path, database, config, exception_name,
):
    backend, ready = _bootstrap_magemin_subprocess_worker(
        binary_path, database, config,
    )
    return (backend, exception_name), ready


def _handle_magemin_launch_failure_request(resource, request, errlog):
    backend, exception_name = resource
    original_run = magemin_module.subprocess.run

    def fail_launch(*_args, **_kwargs):
        raise _injected_launch_exception(exception_name)

    magemin_module.subprocess.run = fail_launch
    try:
        return _handle_magemin_subprocess_request(backend, request, errlog)
    finally:
        magemin_module.subprocess.run = original_run


def _warm_magemin_launch_failure_pool(backend, exception_name):
    def worker_factory(index):
        return WarmEngineWorker(
            name=f'MAGEMin injected launch failure slot {index}',
            bootstrap=_bootstrap_magemin_launch_failure_worker,
            handler=_handle_magemin_launch_failure_request,
            bootstrap_args=(
                str(backend._binary_path),
                backend._database,
                backend._config,
                exception_name,
            ),
            startup_timeout_s=30.0,
            call_timeout_s=30.0,
        )

    return EngineWorkerPool(worker_factory, size=1)


def _warm_magemin_respawn_pool(backend):
    def worker_factory(index):
        return WarmEngineWorker(
            name=f'MAGEMin respawn failure slot {index}',
            bootstrap=_bootstrap_magemin_subprocess_worker,
            handler=_handle_magemin_subprocess_request,
            bootstrap_args=(
                str(backend._binary_path), backend._database, backend._config,
            ),
            startup_timeout_s=30.0,
            call_timeout_s=30.0,
        )

    return EngineWorkerPool(worker_factory, size=1)


@pytest.mark.parametrize('exception_name', [
    'OSError', 'BlockingIOError', 'MemoryError', 'SubprocessError',
])
def test_magemin_warm_worker_respawn_environment_errors_are_typed_refusals(
    exception_name, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    backend = MAGEMinBackend()
    assert backend.initialize({'warm_worker': False})
    backend._subprocess_pool = _warm_magemin_respawn_pool(backend)
    backend._subprocess_pool.workers[0].close()
    provider._backend = backend
    provider._backend_initialised = True
    attempts = []

    def fail_start(_process):
        attempts.append(True)
        raise _injected_launch_exception(exception_name)

    before = sim.atom_ledger.mol_by_account()
    try:
        with patch.object(multiprocessing.process.BaseProcess, 'start', fail_start):
            with pytest.raises(EvaporationFluxRefusal) as error:
                sim._melt_redox_liquidus_gate_curve()

        diagnostic = error.value.diagnostic['provider_diagnostic'][
            'backend_diagnostics'
        ]['projected_liquidus_diagnostics']
        assert diagnostic['backend_failure_exception'] == exception_name
        assert diagnostic['backend_failure_disposition'] == 'environment'
        assert error.value.diagnostic['reason'] == 'liquid_fraction_provider_failed'
        assert attempts
        assert sim.atom_ledger.mol_by_account() == before
    finally:
        try:
            backend.close()
        except EngineWorkerTimeout:
            # The injected Process.start failure leaves the pool's normal
            # shutdown path to report its own hard timeout; the test records
            # the gate outcome above, as the review probe does.
            pass


def test_magemin_warm_worker_respawn_programming_error_propagates(
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    backend = MAGEMinBackend()
    assert backend.initialize({'warm_worker': False})
    backend._subprocess_pool = _warm_magemin_respawn_pool(backend)
    backend._subprocess_pool.workers[0].close()
    provider._backend = backend
    provider._backend_initialised = True

    def fail_start(_process):
        raise TypeError('injected programming error')

    try:
        with patch.object(multiprocessing.process.BaseProcess, 'start', fail_start):
            with pytest.raises(TypeError, match='injected programming error'):
                sim._melt_redox_liquidus_gate_curve()
    finally:
        try:
            backend.close()
        except EngineWorkerTimeout:
            pass


@pytest.mark.parametrize('value', ['auto', 'alphamelts', '', None, True, 1])
def test_gate_provider_rejects_undeclared_values(value):
    with pytest.raises((TypeError, ValueError), match='liquid_fraction_provider'):
        normalize_chemistry_kernel_config({'liquid_fraction_provider': value})


@pytest.mark.parametrize('provider', [None, 'magemin'])
def test_missing_declared_engine_refuses_before_first_hour(
    monkeypatch, provider, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    monkeypatch.setattr(MAGEMinShadowProvider, '_ensure_backend', lambda _: None)
    sim = _sim(provider, vapor_pressure_data, feedstocks_data, setpoints_data)
    before = sim.atom_ledger.mol_by_account()
    with pytest.raises(EvaporationFluxRefusal) as error:
        sim.start_campaign(CampaignPhase.C2A_STAGED)
    assert error.value.diagnostic['reason'] == 'missing_input'
    assert error.value.diagnostic['provider'] == 'magemin'
    assert sim.melt.hour == 0
    assert sim.atom_ledger.mol_by_account() == before
    assert sim.record.snapshots == []


def test_analytical_gate_never_probes_magemin_and_flags_each_hour(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    def forbidden(*args, **kwargs):
        pytest.fail('analytical gate called MAGEMin')

    monkeypatch.setattr(MAGEMinShadowProvider, '_ensure_backend', forbidden)
    sim = _sim('analytical', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim.start_campaign(CampaignPhase.C2A_STAGED)
    curve = sim._freeze_gate_curve()
    floor = KRESS91_LIQUID_CALIBRATION_MIN_T_C
    assert sim._interpolate_freeze_gate_curve(curve, floor - 1.0) == 0.0
    assert sim._interpolate_freeze_gate_curve(curve, floor + 1.0) == 1.0
    assert sim.record.liquid_fraction_provider == 'analytical'
    for _ in range(2):
        snapshot = sim.step()
        diagnostic = snapshot.liquid_fraction_gate
        assert diagnostic['provider'] == 'analytical'
        assert diagnostic['qualification'] == 'analytical_kress91_floor_prediction'
        assert build_per_hour_summary(sim, snapshot)['oxygen_reservoir'][
            'liquid_fraction_gate'
        ] == diagnostic


@pytest.mark.parametrize('status', ['unavailable', 'not_converged'])
def test_declared_magemin_environment_failure_never_uses_another_liquidus(
    monkeypatch, status, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    monkeypatch.setattr(sim, '_dispatch_only', lambda *a, **kw: SimpleNamespace(
        status=status, diagnostic={'backend_status': status,
                                   'backend_status_reason': 'missing_binary'},
    ))
    calls = []
    sim.backend.find_liquidus_solidus = lambda **kw: calls.append(kw)
    monkeypatch.setattr(
        sim, '_composition_projected_freeze_gate_curve',
        lambda *a, **kw: pytest.fail('numerical failure used projected bounds'),
    )
    with pytest.raises(EvaporationFluxRefusal) as error:
        sim._freeze_gate_curve()
    assert error.value.diagnostic['provider'] == 'magemin'
    assert calls == []


def test_runner_reports_typed_startup_missing_input(monkeypatch):
    monkeypatch.setattr(MAGEMinShadowProvider, '_ensure_backend', lambda _: None)
    result = PyrolysisRun(
        feedstock_id='lunar_mare_low_ti', campaign='C2A_staged', hours=1,
    ).run()
    assert result['status'] == 'refused'
    artifact = build_run_artifact(result, run_id='missing-gate')
    assert artifact['timesteps'] == []
    metadata = artifact['terminal']['run_metadata']
    assert metadata['liquid_fraction_provider'] == 'magemin'
    assert metadata['refusal_diagnostic']['reason'] == 'missing_input'


@pytest.mark.parametrize('reason', ['engine_not_converged', 'engine_worker_timeout'])
@pytest.mark.parametrize('status', ['unavailable', 'not_converged', 'out_of_domain'])
def test_projected_solve_status_survives_actual_adapter_wrapping(
    monkeypatch, status, reason, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    store = PT0DeterminismStore('capture')
    sim.configure_pt0_determinism_store(store)
    shared_cache = {}
    sim._freeze_gate_shared_curve_cache = shared_cache
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    backend = MAGEMinBackend()
    assert backend.initialize({})
    provider._backend = backend
    provider._backend_initialised = True
    original_equilibrate = backend.equilibrate
    original_dispatch = sim._dispatch_only
    injected = []
    diagnostics = []

    def sample(temperature_C, *args, **kwargs):
        if kwargs.get('projected_oxide_wt_pct') is not None:
            injected.append(temperature_C)
            return EquilibriumResult(
                temperature_C=temperature_C, status=status,
                warnings=['injected projected-bulk solve refusal'],
                diagnostics={'backend_status_reason': (
                    'silicate_window' if status == 'out_of_domain' else reason
                )},
            )
        return original_equilibrate(temperature_C, *args, **kwargs)

    def dispatch(*args, **kwargs):
        result = original_dispatch(*args, **kwargs)
        if args[0] is ChemistryIntent.GATE_LIQUID_FRACTION:
            diagnostics.append(result.diagnostic)
        return result

    monkeypatch.setattr(backend, 'equilibrate', sample)
    monkeypatch.setattr(sim, '_dispatch_only', dispatch)
    before = sim.atom_ledger.mol_by_account()
    try:
        if status == 'out_of_domain':
            curve = sim._melt_redox_liquidus_gate_curve()
            assert curve['source'] == 'kress91_liquid_calibration_floor'
            assert curve['composition_projected_notice']['authority'] == 'extrapolated'
            assert sim._freeze_gate_provider_outcome is GateCurveProviderOutcome.STABLE_REFUSAL
        else:
            for hour in range(2):
                sim.melt.hour = hour
                curve = sim._melt_redox_liquidus_gate_curve()
                notice = curve['gate_provider_refusal']
                assert curve['source'] == 'kress91_liquid_calibration_floor'
                assert notice['authority'] == 'extrapolated (lower-rung)'
                assert notice['provider_diagnostic'] == diagnostics[-1]
                if reason == 'engine_worker_timeout':
                    assert notice['backend_failure_disposition'] == 'environment'
                    assert sim._freeze_gate_provider_outcome is GateCurveProviderOutcome.FAILED
                else:
                    assert notice['outcome'] == 'numerical_failure'
                assert sim._freeze_gate_liquid_fraction_cache is None
                assert sim._freeze_gate_liquid_fraction_curve_memo == {}
                assert shared_cache == {}
                assert store.capture_sequence == []
        assert injected
        assert diagnostics[-1]['backend_status'] == 'out_of_domain'
        assert diagnostics[-1]['backend_diagnostics']['projected_liquidus_status'] == status
        assert diagnostics[-1]['backend_diagnostics']['projected_liquidus_diagnostics'][
            'backend_status_reason'
        ] == ('silicate_window' if status == 'out_of_domain' else reason)
        assert 'injected projected-bulk solve refusal' in diagnostics[-1]['backend_warnings']
        assert sim.atom_ledger.mol_by_account() == before
    finally:
        backend.close()


@pytest.mark.parametrize(('error_type', 'error_number'), [
    (BlockingIOError, errno.EAGAIN),
    (ConnectionResetError, errno.ECONNRESET),
    (FileNotFoundError, errno.ENOENT),
    (OSError, errno.EMFILE),
])
def test_actual_magemin_launch_oserror_is_typed_environment_refusal(
    monkeypatch, error_type, error_number,
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    backend = MAGEMinBackend()
    assert backend.initialize({})
    backend._subprocess_pool = None
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')
    provider._backend = backend
    provider._backend_initialised = True
    calls = []
    diagnostics = []
    original_dispatch = sim._dispatch_only

    def fail_launch(*args, **kwargs):
        calls.append((args, kwargs))
        raise error_type(error_number, 'injected process launch failure')

    def dispatch(*args, **kwargs):
        result = original_dispatch(*args, **kwargs)
        if args[0] is ChemistryIntent.GATE_LIQUID_FRACTION:
            diagnostics.append(result.diagnostic)
        return result

    monkeypatch.setattr('simulator.melt_backend.magemin.subprocess.run', fail_launch)
    monkeypatch.setattr(sim, '_dispatch_only', dispatch)
    before = sim.atom_ledger.mol_by_account()
    try:
        with pytest.raises(EvaporationFluxRefusal) as error:
            sim._melt_redox_liquidus_gate_curve()

        assert error.value.diagnostic['reason'] == 'liquid_fraction_provider_failed'
        assert error.value.diagnostic['outcome'] is GateCurveProviderOutcome.FAILED
        assert diagnostics
        projected = diagnostics[-1]['backend_diagnostics'][
            'projected_liquidus_diagnostics'
        ]
        assert projected['backend_failure_exception'] == error_type.__name__
        assert projected['backend_failure_disposition'] == 'environment'
        assert calls
        assert sim.atom_ledger.mol_by_account() == before
    finally:
        backend.close()


@pytest.mark.parametrize('mode', ['cold', 'bridge_retry', 'warm_pool'])
@pytest.mark.parametrize('exception_name', [
    'BlockingIOError', 'ConnectionResetError', 'InterruptedError',
    'MemoryError', 'SubprocessError', 'TimeoutExpired', 'CalledProcessError',
])
def test_magemin_launch_environment_errors_keep_type_in_every_execution_mode(
    monkeypatch, mode, exception_name,
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    backend = MAGEMinBackend()
    assert backend.initialize({'warm_worker': False})
    if mode == 'warm_pool':
        backend._subprocess_pool = _warm_magemin_launch_failure_pool(
            backend, exception_name,
        )
    elif mode == 'bridge_retry':
        backend._bridge = 'pymagemin'

        def fail_bridge(**_kwargs):
            raise RuntimeError('injected optional bridge failure')

        backend._magemin_module = SimpleNamespace(minimize=fail_bridge)

        def fail_launch(*_args, **_kwargs):
            raise _injected_launch_exception(exception_name)

        monkeypatch.setattr(magemin_module.subprocess, 'run', fail_launch)
    else:
        def fail_launch(*_args, **_kwargs):
            raise _injected_launch_exception(exception_name)

        monkeypatch.setattr(magemin_module.subprocess, 'run', fail_launch)
    provider._backend = backend
    provider._backend_initialised = True
    before = sim.atom_ledger.mol_by_account()
    try:
        if exception_name == 'TimeoutExpired':
            curve = sim._melt_redox_liquidus_gate_curve()
            failure = curve['gate_provider_refusal']
            assert curve['source'] == 'kress91_liquid_calibration_floor'
            assert failure['authority'] == 'extrapolated (lower-rung)'
            assert failure['backend_failure_disposition'] == 'environment'
        else:
            with pytest.raises(EvaporationFluxRefusal) as error:
                sim._melt_redox_liquidus_gate_curve()
            failure = error.value.diagnostic
            assert failure['reason'] == 'liquid_fraction_provider_failed'
        assert failure['outcome'] is GateCurveProviderOutcome.FAILED
        projected = failure['provider_diagnostic'][
            'backend_diagnostics'
        ]['projected_liquidus_diagnostics']
        assert projected['backend_failure_exception'] == exception_name
        assert projected['backend_failure_disposition'] == 'environment'
        if mode == 'warm_pool':
            assert backend._subprocess_pool is not None
        assert sim.atom_ledger.mol_by_account() == before
    finally:
        backend.close()


@pytest.mark.parametrize('mode', ['cold', 'bridge_retry', 'warm_pool'])
def test_magemin_launch_programming_errors_still_propagate(
    monkeypatch, mode, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    provider = sim._chem_registry.fallback_for(ChemistryIntent.GATE_LIQUID_FRACTION)
    backend = MAGEMinBackend()
    assert backend.initialize({'warm_worker': False})
    if mode == 'warm_pool':
        backend._subprocess_pool = _warm_magemin_launch_failure_pool(
            backend, 'TypeError',
        )
    elif mode == 'bridge_retry':
        backend._bridge = 'pymagemin'

        def fail_bridge(**_kwargs):
            raise RuntimeError('injected optional bridge failure')

        backend._magemin_module = SimpleNamespace(minimize=fail_bridge)

        def fail_launch(*_args, **_kwargs):
            raise TypeError('injected programming error')

        monkeypatch.setattr(magemin_module.subprocess, 'run', fail_launch)
    else:
        def fail_launch(*_args, **_kwargs):
            raise TypeError('injected programming error')

        monkeypatch.setattr(magemin_module.subprocess, 'run', fail_launch)
    provider._backend = backend
    provider._backend_initialised = True
    try:
        with pytest.raises(TypeError, match='injected programming error'):
            sim._melt_redox_liquidus_gate_curve()
    finally:
        backend.close()


@pytest.mark.parametrize('outer_status', ['ok', 'out_of_domain'])
@pytest.mark.parametrize('nested, expected', [
    ({'backend_status': 'unavailable'}, 'failed'),
    ({'backend_status': 'not_converged'}, 'numerical_failure'),
    ({'backend_status_reason': 'engine_worker_timeout'}, 'failed'),
    ({'projected_liquidus_status': 'unavailable'}, 'numerical_failure'),
    ({'projected_liquidus_status': 'not_converged'}, 'numerical_failure'),
    ({'projected_liquidus_status': 'out_of_domain',
      'projected_liquidus_diagnostics': {'reason': 'engine_worker_timeout'}}, 'failed'),
    ({'projected_liquidus_status': 'not_converged',
      'projected_liquidus_diagnostics': {'reason': 'engine_crash'}}, 'failed'),
    ({'projected_liquidus_status': 'unavailable',
      'projected_liquidus_diagnostics': {'reason': 'missing_binary'}}, 'failed'),
    *[({'projected_liquidus_status': 'not_converged',
        'projected_liquidus_diagnostics': {'backend_failure_exception': exception}}, 'failed')
      for exception in ('EngineWorkerDied', 'EngineWorkerUnavailable',
                        'MAGEMinProcessExit', 'FileNotFoundError', 'TimeoutExpired',
                        'MAGEMinAggregateBudgetExceeded')],
    *[({'projected_liquidus_status': 'not_converged',
        'projected_liquidus_diagnostics': {'reason': reason}}, 'failed')
      for reason in ('invalid_input', 'missing_input')],
])
def test_wrapped_diagnostic_distinguishes_numerical_and_environment_failure(
    outer_status, nested, expected,
):
    assert gate_curve_provider_outcome_for_status(outer_status, {
        'backend_status_reason': 'composition_projected',
        'backend_diagnostics': nested,
    }).value == expected


@pytest.mark.parametrize('mode', ['reason', 'exception', 'backend_exception', 'aggregate_budget'])
def test_timeout_does_not_retry_same_declared_gate(
    monkeypatch, mode, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    calls = []

    def dispatch(intent, **kwargs):
        calls.append((intent, kwargs))
        if mode == 'exception':
            raise EngineWorkerTimeout('magemin', 15.0, phase='liquidus')
        return SimpleNamespace(status='out_of_domain', diagnostic={
            'backend_status_reason': 'composition_projected',
            'backend_diagnostics': {
                'projected_liquidus_status': 'not_converged',
                'projected_liquidus_diagnostics': (
                    {'backend_failure_exception': 'TimeoutExpired'}
                    if mode == 'backend_exception' else (
                        {'reason': 'aggregate_budget_exceeded', 'budget_s': 60.0,
                         'call_count': 26, 'last_T_C': 1700.0}
                        if mode == 'aggregate_budget' else {'reason': 'engine_worker_timeout'}
                    )
                ),
            },
        })

    monkeypatch.setattr(sim, '_dispatch_only', dispatch)
    curve = sim._freeze_gate_curve()
    notice = curve['gate_provider_refusal']
    assert curve['source'] == 'kress91_liquid_calibration_floor'
    assert notice['provider'] == 'magemin'
    assert notice['backend_failure_disposition'] == 'environment'
    assert notice['authority'] == 'extrapolated (lower-rung)'
    assert sim._freeze_gate_liquid_fraction_cache is None
    assert sim._freeze_gate_liquid_fraction_curve_memo == {}
    assert len(calls) == 1


@pytest.mark.parametrize('failure', [
    LiquidusSampleError('not_converged', (), {'reason': 'aggregate_budget_exceeded'}),
    EngineWorkerTimeout('magemin', 15.0, phase='liquidus'),
])
def test_first_call_gate_exception_uses_flagged_calibration_floor(
    monkeypatch, failure, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(sim, '_dispatch_only', fail)
    before = sim.atom_ledger.mol_by_account()
    floor = KRESS91_LIQUID_CALIBRATION_MIN_T_C
    assert sim._melt_redox_liquid_fraction_factor(floor + 1.0 + 273.15) == 1.0
    notice = sim._melt_redox_liquidus_gate_curve()['gate_provider_refusal']
    assert notice['authority'] == 'extrapolated (lower-rung)'
    assert sim.atom_ledger.mol_by_account() == before


def test_gate_provider_is_a_run_identity_input():
    args = dict(
        recipe_id='gate', feedstock_recipe_digest='', feedstock_id='lunar_mare_low_ti',
        profile_id='gate', fidelity='test', code_version='test', data_digests={},
    )
    default = canonical_evalspec_json(EvalSpec(**args))
    magemin = canonical_evalspec_json(EvalSpec(
        **args, chemistry_kernel={'liquid_fraction_provider': 'magemin'},
    ))
    analytical = canonical_evalspec_json(EvalSpec(
        **args, chemistry_kernel={'liquid_fraction_provider': 'analytical'},
    ))
    assert default == magemin
    assert analytical != magemin


def test_optimizer_preserves_declared_gate_in_config_and_identity():
    profile = _mre_cap_profile(chemistry_kernel={'liquid_fraction_provider': 'analytical'})
    spec, config = _build_eval_inputs(
        RecipePatch({}), 'lunar_mare_low_ti', 'internal-analytical', profile, RecipeSchema(),
    )
    assert config.setpoints['chemistry_kernel']['liquid_fraction_provider'] == 'analytical'
    assert spec.chemistry_kernel['liquid_fraction_provider'] == 'analytical'


def test_optimizer_resolves_gate_declared_in_loaded_setpoints(monkeypatch):
    bundle = load_config_bundle()
    setpoints = deepcopy(bundle.setpoints)
    setpoints['chemistry_kernel']['liquid_fraction_provider'] = 'analytical'
    monkeypatch.setattr(
        evaluate_module, 'load_config_bundle',
        lambda *a, **kw: replace(bundle, setpoints=setpoints),
    )
    spec, config = _build_eval_inputs(
        RecipePatch({}), 'lunar_mare_low_ti', 'internal-analytical',
        _mre_cap_profile(), RecipeSchema(),
    )
    assert config.setpoints['chemistry_kernel']['liquid_fraction_provider'] == 'analytical'
    assert spec.chemistry_kernel['liquid_fraction_provider'] == 'analytical'


@pytest.mark.parametrize('failure', ['numerical', 'projected_numerical', 'aggregate_timeout', 'warm_timeout'])
@pytest.mark.parametrize('prior_good', [False, True])
def test_declared_gate_holds_or_uses_first_call_floor_and_retries_next_hour(
    monkeypatch, failure, prior_good,
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from tests.binding_admission_fixtures import register_synthetic_gate_curve_producer

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    register_synthetic_gate_curve_producer(sim)
    shared = {}
    sim._freeze_gate_shared_curve_cache = shared
    phase = {'good': prior_good, 'liquidus': 1300.0}
    calls = []
    composition = {'SiO2': 50.0, 'MgO': 50.0}
    monkeypatch.setattr(sim.melt, 'composition_wt_pct', lambda: dict(composition))

    def dispatch(intent, **kwargs):
        assert intent is ChemistryIntent.GATE_LIQUID_FRACTION
        calls.append(sim.melt.hour)
        if phase['good']:
            return SimpleNamespace(status='ok', diagnostic={
                'solidus_T_C': 1000.0, 'liquidus_T_C': phase['liquidus'],
                'kernel_fallback_used': 'magemin-shadow',
            }, warnings=())
        if failure == 'warm_timeout':
            raise EngineWorkerTimeout('magemin', 15.0, phase='liquidus')
        if failure == 'numerical':
            return SimpleNamespace(status='not_converged', diagnostic={
                'backend_status_reason': 'no_convergence',
            }, warnings=('non-monotone frac_M(T)',))
        return SimpleNamespace(status='out_of_domain', diagnostic={
            'backend_status_reason': 'composition_projected',
            'backend_diagnostics': {
                'projected_liquidus_status': 'not_converged',
                'projected_liquidus_diagnostics': {
                    'reason': ('aggregate_budget_exceeded'
                               if failure == 'aggregate_timeout' else 'no_convergence'),
                },
            },
        }, warnings=('injected declared-provider failure',))

    monkeypatch.setattr(sim, '_dispatch_only', dispatch)
    sim.melt.hour = 7
    sim.melt.temperature_C = 1500.0
    timed_out = failure.endswith('timeout')
    if not prior_good:
        store = PT0DeterminismStore('capture')
        sim.configure_pt0_determinism_store(store)
        curve = sim._melt_redox_liquidus_gate_curve()
        expected = sim._freeze_gate_kress_floor_curve(None, [])
        assert {k: v for k, v in curve.items() if k != 'gate_provider_refusal'} == expected
        notice = curve['gate_provider_refusal']
        assert notice['authority'] == 'extrapolated (lower-rung)'
        assert notice['qualification'] == 'first_call_kress91_floor_prediction'
        assert notice['failure_hour'] == 7
        assert notice['failure_temperature_C'] == 1500.0
        assert notice['status'] == ('unavailable' if failure == 'warm_timeout' else (
            'not_converged' if failure == 'numerical' else 'out_of_domain'
        ))
        assert notice['provider_diagnostic']
        assert 'calibration-floor step' in notice['detail']
        assert 'not a fresh solve or a melting measurement' in notice['detail']
        if timed_out:
            assert notice['backend_failure_disposition'] == 'environment'
        else:
            assert notice['outcome'] == 'numerical_failure'
            assert notice['warnings']
        assert sim._freeze_gate_last_good_curve is None
        assert sim._freeze_gate_liquid_fraction_cache is None
        assert sim._freeze_gate_liquid_fraction_curve_memo == {}
        assert shared == {}
        assert store.capture_sequence == []
        assert sim._make_snapshot().liquid_fraction_gate['redox']['gate_provider_refusal'] == notice
        call_count = len(calls)
        assert sim._melt_redox_liquidus_gate_curve() == curve
        assert len(calls) == call_count
        sim.melt.hour = 8
        again = sim._melt_redox_liquidus_gate_curve()
        assert again['gate_provider_refusal']['failure_hour'] == 8
        assert len(calls) == call_count + 1
        assert sim._freeze_gate_liquid_fraction_curve_memo == {}
        assert shared == {}
        assert store.capture_sequence == []
        sim.melt.hour = 9
        phase.update(good=True, liquidus=1500.0)
        recovered = sim._melt_redox_liquidus_gate_curve()
        assert recovered['liquidus_T_C'] == 1500.0
        assert 'gate_provider_refusal' not in recovered
        assert sim._freeze_gate_last_good_curve['computed_hour'] == 9
        return

    good = sim._melt_redox_liquidus_gate_curve()
    memo_keys = set(sim._freeze_gate_liquid_fraction_curve_memo)
    shared_keys = set(shared)
    assert memo_keys and shared_keys
    phase['good'] = False
    sim.melt.hour = 8
    sim.melt.p_total_mbar += 100.0
    composition.update(SiO2=40.0, MgO=60.0)
    store = PT0DeterminismStore('capture')
    sim.configure_pt0_determinism_store(store)
    held = sim._melt_redox_liquidus_gate_curve()
    notice = held['gate_provider_refusal']
    assert held['source'] == good['source']
    assert held['liquidus_T_C'] == good['liquidus_T_C']
    assert notice['provider'] == 'magemin'
    assert notice['authority'] == 'extrapolated (held)'
    assert notice['failure_hour'] == 8
    assert notice['failure_temperature_C'] == 1500.0
    assert notice['computed_hour'] == 7
    assert notice['composition_distance_wt_pct'] == 20.0
    assert notice['provider_diagnostic']
    if timed_out:
        assert notice['backend_failure_disposition'] == 'environment'
    else:
        assert notice['warnings']
        assert notice['outcome'] == 'numerical_failure'
    assert set(sim._freeze_gate_liquid_fraction_curve_memo) == memo_keys
    assert set(shared) == shared_keys
    assert store.capture_sequence == []
    assert sim._freeze_gate_liquid_fraction_cache is None or (
        'gate_provider_refusal' not in sim._freeze_gate_liquid_fraction_cache['curve']
    )
    assert sim._make_snapshot().liquid_fraction_gate['redox']['gate_provider_refusal'] == notice
    call_count = len(calls)
    assert sim._melt_redox_liquidus_gate_curve() == held
    assert len(calls) == call_count

    sim.melt.hour = 9
    sim.melt.p_total_mbar -= 100.0
    again = sim._melt_redox_liquidus_gate_curve()
    assert len(calls) == call_count + 1
    assert again['gate_provider_refusal']['failure_hour'] == 9
    assert again['gate_provider_refusal']['computed_hour'] == 7
    assert set(sim._freeze_gate_liquid_fraction_curve_memo) <= memo_keys
    assert set(shared) == shared_keys
    assert store.capture_sequence == []

    sim.melt.hour = 10
    phase.update(good=True, liquidus=1500.0)
    recovered = sim._melt_redox_liquidus_gate_curve()
    assert recovered['liquidus_T_C'] == 1500.0
    assert 'gate_provider_refusal' not in recovered
    sim.melt.hour = 11
    sim.melt.p_total_mbar += 100.0
    phase['good'] = False
    held = sim._melt_redox_liquidus_gate_curve()
    assert held['liquidus_T_C'] == 1500.0
    assert held['gate_provider_refusal']['computed_hour'] == 10
