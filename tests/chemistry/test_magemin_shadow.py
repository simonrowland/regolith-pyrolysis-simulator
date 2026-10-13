"""Kernel-level parity tests for the MAGEMin shadow provider.

Goal #9 ``MAGEMIN-SHADOW-PARITY`` checklist:

1. ``engines/magemin/provider.py`` registers as shadow provider for
   SILICATE_LIQUIDUS + SILICATE_EQUILIBRIUM.
2. Kernel planner routes the request to the authoritative AND the
   shadow provider; only authoritative's result becomes the
   LedgerTransition.
3. Parity test:
       |T_liquidus_authoritative - T_liquidus_shadow| <= 50 K
       |mode_pct_diff| <= 2 wt% per phase
   Disagreement raises parity warning to trace, not a test failure.

These tests use a stand-in authoritative provider (so the suite stays
green without alphaMELTS installed) and a controllable MAGEMin shadow
backend so we can drive both the "agreement" and "disagreement" paths
deterministically.  The real-engine parity case is gated by an
``importorskip`` -- the per-engine adapters land their own integration
tests that exercise the binary.
"""

from __future__ import annotations

import gc
import multiprocessing
import os
import signal
import subprocess
import sys
import threading
import textwrap
from pathlib import Path

import pytest

from engines.magemin import (
    MAGEMinParityComparator,
    MAGEMinShadowDiagnostics,
    MAGEMinShadowProvider,
)
from simulator.accounting.ledger import AtomLedger
from simulator.chemistry.kernel import (
    CapabilityProfile,
    ChemistryIntent,
    ChemistryKernel,
    ChemistryProvider,
    ControlAudit,
    IntentRequest,
    IntentResult,
    IntentResultStatusError,
    KernelError,
    ProviderAccountView,
    ProviderRegistry,
)
from simulator.chemistry.kernel.planner import Planner
from simulator.engine_pool import EngineWorkerPool, WarmEngineWorker


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubAuthoritativeProvider(ChemistryProvider):
    """Stand-in authoritative provider that returns canned diagnostics.

    Tests construct it with the liquidus / mode payload they want to see
    on the authoritative result, so the parity comparison is fully
    deterministic and does not depend on AlphaMELTS being installed.
    """

    PROVIDER_ID = 'stub-authoritative-silicate'
    DECLARED_ACCOUNT = 'process.cleaned_melt'

    def __init__(
        self,
        *,
        liquidus_T_K: float = 1700.0,
        phase_modes_wt_pct: dict | None = None,
        status: str = 'ok',
    ) -> None:
        self._liquidus_T_K = float(liquidus_T_K)
        self._phase_modes_wt_pct = dict(phase_modes_wt_pct or {'liquid': 100.0})
        self._status = status
        self.call_count = 0

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id=self.PROVIDER_ID,
            intents=frozenset({
                ChemistryIntent.SILICATE_LIQUIDUS,
                ChemistryIntent.SILICATE_EQUILIBRIUM,
            }),
            is_authoritative_for=frozenset({
                ChemistryIntent.SILICATE_LIQUIDUS,
                ChemistryIntent.SILICATE_EQUILIBRIUM,
            }),
            declared_accounts=frozenset({self.DECLARED_ACCOUNT}),
        )

    def dispatch(self, request: IntentRequest) -> IntentResult:
        self.call_count += 1
        diagnostic = {
            'liquidus_T_K': self._liquidus_T_K,
            'liquidus_T_C': self._liquidus_T_K - 273.15,
            'phase_modes_wt_pct': dict(self._phase_modes_wt_pct),
            'phases_present': tuple(sorted(self._phase_modes_wt_pct)),
            'engine_version': 'stub-authoritative-1.0',
        }
        return IntentResult(
            intent=request.intent,
            status=self._status,
            transition=None,  # diagnostic; goal #8 already binds this
            control_audit=ControlAudit(
                requested={
                    'temperature_C': request.temperature_C,
                    'pressure_bar': request.pressure_bar,
                    'fO2_log': request.fO2_log,
                },
                applied={
                    'temperature_C': request.temperature_C,
                    'pressure_bar': request.pressure_bar,
                    'fO2_log': request.fO2_log,
                },
            ),
            diagnostic=diagnostic,
            warnings=(),
        )


class _FakeMAGEMinBackend:
    """Minimal MAGEMinBackend stand-in.

    The provider's :meth:`_run_backend` calls ``equilibrate`` with
    keyword arguments matching the real adapter; this fake records the
    call and returns a canned :class:`EquilibriumResult`-shaped object.
    Avoids depending on a built MAGEMin binary in the test env.
    """

    name = 'magemin'

    def __init__(
        self,
        *,
        equilibrium,
        is_available: bool = True,
        engine_version: str = 'fake-magemin',
        bridge: str = 'subprocess',
    ) -> None:
        self._equilibrium = equilibrium
        self._is_available = bool(is_available)
        self._engine_version = engine_version
        self._bridge = bridge
        self.calls: list[dict] = []
        self.initialize_configs: list[dict] = []

    def is_available(self) -> bool:
        return self._is_available

    def get_engine_version(self) -> str:
        return self._engine_version

    def initialize(self, config: dict) -> bool:
        self.initialize_configs.append(dict(config or {}))
        return self._is_available

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        return self._equilibrium


def _provider_lifecycle_worker_bootstrap():
    return None, 'ready'


def _provider_lifecycle_worker_handler(_resource, request, _errlog):
    return request


def _provider_lifecycle_worker(_index):
    return WarmEngineWorker(
        name='magemin-provider-lifecycle-test',
        bootstrap=_provider_lifecycle_worker_bootstrap,
        handler=_provider_lifecycle_worker_handler,
        startup_timeout_s=3.0,
        call_timeout_s=1.0,
    )


def _provider_callback_worker_handler(_resource, request, _errlog):
    # Keep the request in flight long enough for the parent to attach a
    # Future callback before the pool's consumer thread completes it.
    import time

    time.sleep(0.2)
    return request


def _provider_callback_worker(_index):
    return WarmEngineWorker(
        name='magemin-provider-callback-test',
        bootstrap=_provider_lifecycle_worker_bootstrap,
        handler=_provider_callback_worker_handler,
        startup_timeout_s=3.0,
        call_timeout_s=1.0,
    )


class _PooledFakeMAGEMinBackend(_FakeMAGEMinBackend):
    """Fake result backend with the real warm-pool child lifecycle."""

    def __init__(self):
        super().__init__(equilibrium=_FakeEquilibriumResult())
        self.pool = None
        self.pool_child_pid = None
        self.close_calls = 0

    def initialize(self, config: dict) -> bool:
        available = super().initialize(config)
        self.pool = EngineWorkerPool(_provider_lifecycle_worker, size=1)
        self.pool_child_pid = self.pool.workers[0].process.pid
        return available

    def close(self):
        if self.pool is not None:
            self.close_calls += 1
            self.pool.close(cancel_pending=True)
            self.pool = None


def _run_provider_finalizer_probe(scenario: str, *, consumer_callback=False):
    """Run GC/lock reentry probes in a process group with a hard timeout."""
    repo_root = Path(__file__).resolve().parents[2]
    chemistry_tests = Path(__file__).resolve().parent
    consumer_setup = (
        'test_module._provider_lifecycle_worker = '
        'test_module._provider_callback_worker\n'
        if consumer_callback else ''
    )
    setup = textwrap.dedent(
        f'''
        import atexit
        import gc
        import multiprocessing
        import os
        import sys
        import threading

        gc.disable()
        sys.path.insert(0, {str(chemistry_tests)!r})
        import test_magemin_shadow as test_module
        from simulator import engine_pool
        from simulator.engine_pool import EngineWorkerPool
        from simulator.melt_backend import magemin as magemin_module
        '''
    )
    setup += consumer_setup
    setup += textwrap.dedent(
        '''
        backend = test_module._PooledFakeMAGEMinBackend()
        magemin_module.MAGEMinBackend = lambda: backend
        provider = test_module.MAGEMinShadowProvider()
        assert provider._ensure_backend() is backend
        pool = backend.pool
        child_pid = backend.pool_child_pid
        provider._cycle = provider

        def child_is_alive(pid):
            return any(
                child.pid == pid and child.is_alive()
                for child in multiprocessing.active_children()
            )

        def cleanup_workers():
            for worker in pool.workers:
                worker.close()

        atexit.register(cleanup_workers)
        '''
    )
    script = setup + '\n' + textwrap.dedent(scenario)
    process = subprocess.Popen(
        [sys.executable, '-c', script],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=8.0)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        pytest.fail(
            'provider finalizer probe deadlocked past 8 seconds\n'
            f'stdout: {stdout}\nstderr: {stderr}'
        )
    assert process.returncode == 0, (
        f'provider finalizer probe exited {process.returncode}\n'
        f'stdout: {stdout}\nstderr: {stderr}'
    )


def _run_provider_after_fork_probe():
    """Probe automatic collection during the real threading fork hook."""
    repo_root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        f'''
        import gc
        import os
        import sys
        import threading
        import weakref

        from engines.magemin import provider as provider_module
        from simulator.melt_backend import magemin as magemin_module

        gc.disable()
        closed = threading.Event()

        class Backend:
            def initialize(self, _config):
                return True

            def close(self):
                closed.set()

        backend = Backend()
        magemin_module.MAGEMinBackend = lambda: backend
        provider = provider_module.MAGEMinShadowProvider()
        assert provider._ensure_backend() is backend
        provider.cycle = provider
        provider_ref = weakref.ref(provider)
        del provider

        def arm_collection_inside_after_fork(frame, event, _arg):
            if (
                frame.f_code.co_name == '_after_fork'
                and event == 'line'
                and threading._active_limbo_lock._is_owned()
            ):
                sys.settrace(None)
                gc.set_threshold(gc.get_count()[0], 1_000_000, 1_000_000)
                gc.enable()
            return arm_collection_inside_after_fork

        sys.settrace(arm_collection_inside_after_fork)
        child_pid = os.fork()
        if child_pid:
            sys.settrace(None)
            _, child_status = os.waitpid(child_pid, 0)
            gc.enable()
            gc.collect()
            if not closed.wait(2.0):
                raise SystemExit('parent close did not drain')
            raise SystemExit(os.waitstatus_to_exitcode(child_status))

        sys.settrace(None)
        provider_collected = provider_ref() is None
        # In this child the finalizer ran before its first _ensure_backend.
        # That call must start a fresh reaper and drain the inherited queue.
        trigger = provider_module.MAGEMinShadowProvider()
        trigger._ensure_backend()
        queued_close_ran = closed.wait(2.0)
        os.write(
            1,
            f'provider_collected={{provider_collected}} '
            f'queued_close_ran={{queued_close_ran}}\\n'.encode(),
        )
        os._exit(0 if provider_collected and queued_close_ran else 1)
        '''
    )
    process = subprocess.Popen(
        [sys.executable, '-c', script],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=8.0)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        pytest.fail(
            'provider after-fork probe exceeded 8 seconds\n'
            f'stdout: {stdout}\nstderr: {stderr}'
        )
    assert process.returncode == 0, (
        f'provider after-fork probe exited {process.returncode}\n'
        f'stdout: {stdout}\nstderr: {stderr}'
    )
    assert 'provider_collected=True queued_close_ran=True' in stdout
    assert 'KeyError' not in stderr
    assert 'Exception ignored in thread' not in stderr


class _RaisingMAGEMinBackend(_FakeMAGEMinBackend):
    def __init__(self):
        super().__init__(
            equilibrium=_FakeEquilibriumResult(),
            engine_version='fake-magemin-raising',
        )

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError('synthetic MAGEMin adapter failure')


class _FakeEquilibriumResult:
    """Duck-typed substitute for ``simulator.melt_backend.base.EquilibriumResult``."""

    def __init__(
        self,
        *,
        liquidus_T_K: float | None = None,
        phase_masses_kg: dict | None = None,
        liquid_composition_wt_pct: dict | None = None,
        status: str = 'ok',
        warnings: tuple = (),
    ) -> None:
        self.liquidus_T_K = liquidus_T_K
        self.phase_masses_kg = dict(phase_masses_kg or {})
        self.phases_present = tuple(self.phase_masses_kg)
        self.liquid_composition_wt_pct = dict(liquid_composition_wt_pct or {})
        total = sum(float(m) for m in self.phase_masses_kg.values())
        self.liquid_fraction = (
            float(self.phase_masses_kg.get('liquid', 0.0))
            / total if total > 0 else None
        )
        self.status = status
        self.warnings = tuple(warnings)


def _basalt_species_mol() -> dict:
    """Basalt composition (mol per oxide) that passes the MAGEMin domain gate."""
    masses = {
        'SiO2': 0.06008,
        'TiO2': 0.07987,
        'Al2O3': 0.10196,
        'FeO': 0.07184,
        'Fe2O3': 0.15969,
        'MgO': 0.04030,
        'CaO': 0.05608,
        'Na2O': 0.06198,
        'K2O': 0.09420,
        'Cr2O3': 0.15199,
        'MnO': 0.07094,
        'P2O5': 0.14194,
        'NiO': 0.07469,
        'CoO': 0.07493,
    }
    wt_pct = {
        'SiO2': 49.0, 'TiO2': 1.5, 'Al2O3': 14.0, 'FeO': 10.0, 'Fe2O3': 1.0,
        'MgO': 9.0, 'CaO': 11.0, 'Na2O': 2.5, 'K2O': 0.8, 'Cr2O3': 0.2,
        'MnO': 0.2, 'P2O5': 0.3, 'NiO': 0.02, 'CoO': 0.01,
    }
    return {oxide: (pct / 100.0) / masses[oxide] for oxide, pct in wt_pct.items()}


def _make_request(
    intent: ChemistryIntent,
    *,
    composition_mol: dict | None = None,
    temperature_C: float = 1400.0,
    pressure_bar: float = 1.0,
    fO2_log: float = -9.0,
) -> IntentRequest:
    accounts = {
        MAGEMinShadowProvider.DECLARED_ACCOUNT:
        dict(composition_mol or _basalt_species_mol()),
    }
    view = ProviderAccountView(
        accounts=accounts,
        species_formula_registry={},
    )
    return IntentRequest(
        intent=intent,
        account_view=view,
        temperature_C=temperature_C,
        pressure_bar=pressure_bar,
        fO2_log=fO2_log,
        control_inputs={},
    )


def test_provider_closes_warm_pool_when_provider_lifetime_ends(monkeypatch):
    from simulator.melt_backend import magemin as magemin_module

    backends = []

    def make_backend():
        backend = _PooledFakeMAGEMinBackend()
        backends.append(backend)
        return backend

    monkeypatch.setattr(magemin_module, 'MAGEMinBackend', make_backend)
    initial_children = {
        child.pid
        for child in multiprocessing.active_children()
        if child.pid is not None and child.is_alive()
    }

    try:
        for _ in range(3):
            provider = MAGEMinShadowProvider()
            result = provider.dispatch(
                _make_request(ChemistryIntent.SILICATE_EQUILIBRIUM)
            )
            assert result.status == 'ok'
            assert backends[-1].pool_child_pid in {
                child.pid for child in multiprocessing.active_children()
            }
            pool = backends[-1].pool
            child_pid = backends[-1].pool_child_pid
            del provider
            gc.collect()
            assert pool._close_complete.wait(timeout=5.0), (
                'provider finalizer did not close its pool within 5 seconds'
            )
            assert child_pid not in {
                child.pid for child in multiprocessing.active_children()
            }

        live_children = {
            child.pid
            for child in multiprocessing.active_children()
            if child.pid is not None and child.is_alive()
        }
        assert not (live_children - initial_children)
        assert len(backends) == 3
        assert all(backend.close_calls == 1 for backend in backends)
    finally:
        for backend in backends:
            backend.close()


def test_provider_finalizer_does_not_deadlock_registry_lock():
    _run_provider_finalizer_probe(
        '''
        dummy_pool = object.__new__(EngineWorkerPool)
        registry_locked_during_gc = []

        def observe_collection(phase, _info):
            if phase == 'start':
                acquired = engine_pool._ENGINE_POOL_REGISTRY_LOCK.acquire(
                    blocking=False
                )
                if acquired:
                    engine_pool._ENGINE_POOL_REGISTRY_LOCK.release()
                else:
                    registry_locked_during_gc.append(True)

        gc.callbacks.append(observe_collection)
        del provider
        current_count = gc.get_count()[0]
        gc.set_threshold(current_count, 1000000, 1000000)
        gc.enable()
        engine_pool._register_engine_pool(dummy_pool)
        engine_pool._discard_engine_pool(dummy_pool)
        gc.callbacks.remove(observe_collection)
        assert registry_locked_during_gc, 'GC did not run under registry lock'
        assert pool._close_complete.wait(timeout=5.0)
        assert not child_is_alive(child_pid), 'provider child survived close'
        '''
    )


def test_provider_finalizer_does_not_deadlock_pool_lifecycle_lock():
    _run_provider_finalizer_probe(
        '''
        lifecycle_locked_during_gc = []

        def observe_collection(phase, _info):
            if phase == 'start':
                acquired = pool._lifecycle_lock.acquire(blocking=False)
                if acquired:
                    pool._lifecycle_lock.release()
                else:
                    lifecycle_locked_during_gc.append(True)

        gc.callbacks.append(observe_collection)
        del provider
        pool._lifecycle_lock.acquire()
        try:
            gc.collect()
        finally:
            pool._lifecycle_lock.release()
        gc.enable()
        gc.callbacks.remove(observe_collection)
        assert lifecycle_locked_during_gc, 'GC did not run under lifecycle lock'
        assert pool._close_complete.wait(timeout=5.0)
        assert not child_is_alive(child_pid), 'provider child survived close'
        '''
    )


def test_provider_finalizer_reaps_when_collection_runs_on_consumer_thread():
    _run_provider_finalizer_probe(
        '''
        collection_on_consumer = []
        unraisable = []
        collection_done = threading.Event()
        sys.unraisablehook = lambda error: unraisable.append(error)

        def observe_collection(phase, _info):
            if phase == 'start':
                collection_on_consumer.append(
                    threading.current_thread() is pool._threads[0]
                )

        gc.callbacks.append(observe_collection)
        provider_box = [provider]

        def collect_provider(_future):
            provider_box.clear()
            gc.enable()
            gc.collect()
            collection_done.set()

        future = pool.submit('consumer-thread', timeout_s=1.0)
        future.add_done_callback(collect_provider)
        del provider
        try:
            assert future.result(timeout=5.0) == 'consumer-thread'
            assert collection_done.wait(timeout=2.0)
            assert collection_on_consumer and any(collection_on_consumer)
            assert pool._close_complete.wait(timeout=5.0)
            assert not unraisable, f'unraisable finalizer errors: {unraisable!r}'
            assert not pool.workers[0]._pending_reap
            assert not child_is_alive(child_pid), 'provider child survived close'
        finally:
            gc.callbacks.remove(observe_collection)
            cleanup_workers()
        ''',
        consumer_callback=True,
    )


def test_provider_finalizer_queues_close_across_automatic_gc_during_fork():
    _run_provider_after_fork_probe()


def test_provider_close_reaper_continues_after_close_raises(monkeypatch, caplog):
    from engines.magemin import provider as provider_module
    from simulator.melt_backend import magemin as magemin_module

    backend = _FakeMAGEMinBackend(equilibrium=_FakeEquilibriumResult())
    backend.close = lambda: None
    monkeypatch.setattr(magemin_module, 'MAGEMinBackend', lambda: backend)
    caplog.set_level('ERROR', logger=provider_module.__name__)

    provider = MAGEMinShadowProvider()
    assert provider._ensure_backend() is backend

    close_order = []
    next_close_ran = threading.Event()

    def raising_close():
        close_order.append('raising')
        raise RuntimeError('synthetic close failure')

    def next_close():
        close_order.append('next')
        next_close_ran.set()

    provider_module._CLOSE_QUEUE.put(raising_close)
    provider_module._CLOSE_QUEUE.put(next_close)

    assert next_close_ran.wait(timeout=2.0)
    assert close_order == ['raising', 'next']
    assert 'MAGEMin provider backend close failed' in caplog.text


# ---------------------------------------------------------------------------
# Checklist #1: provider registers as a shadow
# ---------------------------------------------------------------------------


def test_provider_registers_as_shadow_for_silicate_intents():
    """Silicate intents stay shadow-only in the provider capability profile.

    Defence in depth: even ``register(shadow=False)`` raises if the
    provider does not declare itself authoritative for the silicate intent.
    """
    registry = ProviderRegistry()
    shadow = MAGEMinShadowProvider()
    registry.register(
        shadow,
        [ChemistryIntent.SILICATE_LIQUIDUS, ChemistryIntent.SILICATE_EQUILIBRIUM],
        shadow=True,
    )
    assert MAGEMinShadowProvider() not in (
        registry.authoritative_for(ChemistryIntent.SILICATE_LIQUIDUS),
    )
    shadows = registry.shadows_for(ChemistryIntent.SILICATE_LIQUIDUS)
    assert len(shadows) == 1
    assert shadows[0].capability_profile().provider_id == 'magemin-shadow'


def test_registry_rejects_promoting_magemin_to_authoritative():
    """Goal-spec forbidden: 'Granting MAGEMin ledger authority.'

    The kernel registry enforces this -- ``CapabilityProfile.
    is_authoritative_for`` excludes the full silicate-state intents, so
    the ``register(shadow=False)`` path raises for those intents.
    """
    registry = ProviderRegistry()
    shadow = MAGEMinShadowProvider()
    with pytest.raises(KernelError, match="cannot be authoritative"):
        registry.register(
            shadow,
            [ChemistryIntent.SILICATE_LIQUIDUS],
            shadow=False,
        )


# ---------------------------------------------------------------------------
# Checklist #2: planner runs authoritative AND shadow
# ---------------------------------------------------------------------------


def test_planner_runs_authoritative_and_shadow():
    """Both providers must run on every dispatch (goal spec acceptance gate)."""

    auth = _StubAuthoritativeProvider()
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    fake_backend = _FakeMAGEMinBackend(equilibrium=fake_equilibrium)
    shadow = MAGEMinShadowProvider(backend=fake_backend)

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)

    planner = Planner(registry)
    result = planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    assert auth.call_count == 1
    assert len(fake_backend.calls) == 1, 'MAGEMin shadow was not dispatched'
    # Authoritative result is what the planner returns.
    assert result.diagnostic.get('engine_version') == 'stub-authoritative-1.0'


def test_planner_returns_authoritative_result_only():
    """Goal spec: 'only authoritative's result becomes the LedgerTransition.'"""

    auth = _StubAuthoritativeProvider()
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    result = planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    # The planner returns the authoritative result, not the shadow's.
    # Authority diagnostic exposes the stub engine_version key; the
    # MAGEMin diagnostic exposes 'fake-magemin'. We must see the auth
    # one.
    assert result.diagnostic.get('engine_version') == 'stub-authoritative-1.0'
    # Shadow result is captured in trace, separately.
    shadow_records = [
        entry for entry in planner.shadow_trace
        if entry.get('event') == 'shadow_dispatch'
    ]
    assert len(shadow_records) == 1
    assert shadow_records[0]['provider_id'] == 'magemin-shadow'


def test_shadow_backend_initialization_pins_subprocess_bridge():
    fake_backend = _FakeMAGEMinBackend(
        equilibrium=_FakeEquilibriumResult(liquidus_T_K=1700.0),
        is_available=False,
    )
    shadow = MAGEMinShadowProvider(backend=fake_backend)
    shadow._backend_initialised = False

    result = shadow.dispatch(
        _make_request(ChemistryIntent.SILICATE_EQUILIBRIUM))

    assert result.status == 'unavailable'
    assert fake_backend.initialize_configs == [
        {'python_bridge': 'subprocess'},
    ]


def test_shadow_backend_exception_surfaces_warning():
    fake_backend = _RaisingMAGEMinBackend()
    shadow = MAGEMinShadowProvider(backend=fake_backend)

    result = shadow.dispatch(
        _make_request(ChemistryIntent.SILICATE_EQUILIBRIUM)
    )

    assert fake_backend.calls
    assert result.status == 'not_converged'
    assert result.diagnostic['backend_status'] == 'not_converged'
    assert result.diagnostic['backend_warnings'] == (
        'MAGEMin equilibrate raised: synthetic MAGEMin adapter failure',
    )
    assert result.warnings == (
        'MAGEMin equilibrate raised: synthetic MAGEMin adapter failure',
    )


def test_projected_field_repair_warning_reaches_shadow_caller():
    fake_backend = _FakeMAGEMinBackend(
        equilibrium=_FakeEquilibriumResult(
            phase_masses_kg={'liquid': 1.0, 'olivine': 0.0},
            liquid_composition_wt_pct={
                'SiO2': 50.0,
                'MgO': 50.0,
                'FeO': 0.0,
                'CaO': -1.0,
                'Na2O': float('nan'),
            },
        ),
    )
    shadow = MAGEMinShadowProvider(backend=fake_backend)

    result = shadow.dispatch(
        _make_request(ChemistryIntent.SILICATE_EQUILIBRIUM)
    )

    assert result.status == 'ok'
    assert len(result.warnings) == 2
    assert result.warnings == result.diagnostic['backend_warnings']
    assert any(
        'nonfinite' in warning and "'Na2O'" in warning
        for warning in result.warnings
    )
    assert result.diagnostic['phase_masses_kg'] == {'liquid': 1.0}
    assert result.diagnostic['liquid_composition_wt_pct'] == {
        'SiO2': 50.0,
        'MgO': 50.0,
        'FeO': 0.0,
        'CaO': -1.0,
    }


def test_kernel_dispatch_does_not_commit_shadow_transitions():
    """A shadow may not write the ledger; the kernel's writer-purity
    invariant binds this and is enforced by transition=None on
    the shadow side. Belt-and-braces: ledger is unchanged across a
    silicate dispatch.
    """
    ledger = AtomLedger()
    ledger.load_external('process.cleaned_melt', {'SiO2': 5.0}, material_origin="feedstock")
    before_mol = ledger.mol_by_account()

    auth = _StubAuthoritativeProvider()
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    kernel = ChemistryKernel(
        ledger=ledger,
        registry=registry,
        species_formula_registry={},
    )

    kernel.dispatch(
        ChemistryIntent.SILICATE_LIQUIDUS,
        temperature_C=1400.0,
        pressure_bar=1.0,
        declared_accounts=frozenset({'process.cleaned_melt'}),
    )

    after_mol = ledger.mol_by_account()
    assert before_mol == after_mol


# ---------------------------------------------------------------------------
# Checklist #3: parity warning landing in the trace
# ---------------------------------------------------------------------------


def test_aligned_results_produce_no_parity_warning():
    """Within tolerance: shadow record present, no parity_warning."""

    # Authoritative + shadow report the same liquidus and modes.
    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 100.0},
    )
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,  # exact match
        phase_masses_kg={'liquid': 1.0},  # 100 wt% liquid
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)
    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == [], (
        f'expected no parity warnings; got {parity_warnings}'
    )


def test_disagreement_records_parity_warning_in_trace_not_failure():
    """Goal-spec acceptance gate: disagreement raises a parity warning
    to the trace, NOT a test failure.

    The shadow liquidus differs by 100 K (twice the 50 K tolerance);
    parity warning MUST land in the trace and the dispatch MUST return
    normally with the authoritative result.
    """
    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 100.0},
    )
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1600.0,  # 100 K below -- twice tolerance
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    # No raise: a parity disagreement is never an exception.
    result = planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))
    assert result.status == 'ok'

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert len(parity_warnings) == 1, (
        f'expected exactly 1 parity warning, got {parity_warnings}'
    )
    warning = parity_warnings[0]
    assert warning['provider_id'] == 'magemin-shadow'
    assert warning['intent'] == ChemistryIntent.SILICATE_LIQUIDUS.value
    assert warning['agreement'] is False
    # Both numbers retained verbatim -- no silent averaging.
    assert warning['liquidus_T_delta_K'] == pytest.approx(100.0)
    assert warning['authoritative_liquidus_T_K'] == pytest.approx(1700.0)
    assert warning['shadow_liquidus_T_K'] == pytest.approx(1600.0)
    # The comparator's per-disagreement warning text comes through.
    assert any('liquidus delta' in w for w in warning['warnings'])


def test_modal_disagreement_records_parity_warning():
    """Modal disagreement above 2 wt% per phase raises a parity warning."""

    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 80.0, 'olivine': 20.0},
    )
    # Shadow reports liquid:65 / olivine:35 -- 15 wt% disagreement.
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 0.65, 'olivine': 0.35},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert len(parity_warnings) == 1
    warning = parity_warnings[0]
    assert warning['mode_pct_max_delta'] == pytest.approx(15.0)
    assert any('modal disagreement' in w for w in warning['warnings'])


def test_parity_within_tolerance_does_not_fire():
    """Within +/-50 K and +/-2 wt%: no parity warning."""

    auth = _StubAuthoritativeProvider(
        liquidus_T_K=1700.0,
        phase_modes_wt_pct={'liquid': 80.0, 'olivine': 20.0},
    )
    # Shadow:
    # liquidus 1670 K (delta = 30 K, within 50 K tolerance)
    # modes: liquid 79 / olivine 21 (delta = 1 wt%, within 2 wt%)
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1670.0,
        phase_masses_kg={'liquid': 0.79, 'olivine': 0.21},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)
    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == []


def test_nonfinite_liquidus_forces_parity_disagreement():
    comparator = MAGEMinParityComparator()

    report = comparator.compare(
        {
            'liquidus_T_K': 1700.0,
            'phase_modes_wt_pct': {'liquid': 100.0},
        },
        {
            'liquidus_T_K': float('nan'),
            'phase_modes_wt_pct': {'liquid': 100.0},
        },
    )

    assert report.agreement is False
    assert report.liquidus_T_delta_K is None
    assert any('finite liquidus T' in warning for warning in report.warnings)


def test_shadow_unavailable_skips_parity_check():
    """If the shadow returns ``unavailable`` no parity event is emitted.

    Goal spec implication: a missing engine is not a parity failure --
    the trace just records the unavailable shadow_dispatch and moves
    on. Otherwise environments without a MAGEMin binary would spam
    spurious parity warnings.
    """
    auth = _StubAuthoritativeProvider(liquidus_T_K=1700.0)
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(
            equilibrium=_FakeEquilibriumResult(status='unavailable'),
            is_available=False,
        ),
    )

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)
    planner.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == []
    shadow_dispatches = [
        e for e in planner.shadow_trace if e.get('event') == 'shadow_dispatch'
    ]
    # The shadow still ran and its 'unavailable' result is recorded.
    assert len(shadow_dispatches) == 1
    assert shadow_dispatches[0]['result'].status == 'unavailable'


def test_shadow_out_of_domain_skips_parity_check():
    """Out-of-domain compositions: shadow result recorded, no parity warning."""

    auth = _StubAuthoritativeProvider(liquidus_T_K=1700.0)
    shadow = MAGEMinShadowProvider()  # lazy adapter, never reached

    registry = ProviderRegistry()
    registry.register(auth, [ChemistryIntent.SILICATE_LIQUIDUS])
    registry.register(shadow, [ChemistryIntent.SILICATE_LIQUIDUS], shadow=True)
    planner = Planner(registry)

    # Empty composition -> MAGEMinDomainGate rejects -> shadow returns
    # status='out_of_domain'. No parity check.
    accounts = {MAGEMinShadowProvider.DECLARED_ACCOUNT: {}}
    view = ProviderAccountView(
        accounts=accounts,
        species_formula_registry={},
    )
    request = IntentRequest(
        intent=ChemistryIntent.SILICATE_LIQUIDUS,
        account_view=view,
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=None,
        control_inputs={},
    )
    planner.dispatch(request)

    parity_warnings = [
        e for e in planner.shadow_trace if e.get('event') == 'parity_warning'
    ]
    assert parity_warnings == []
    shadow_dispatches = [
        e for e in planner.shadow_trace if e.get('event') == 'shadow_dispatch'
    ]
    assert len(shadow_dispatches) == 1
    assert shadow_dispatches[0]['result'].status == 'out_of_domain'


# ---------------------------------------------------------------------------
# Checklist invariants: MAGEMin never emits a LedgerTransition
# ---------------------------------------------------------------------------


def test_shadow_dispatch_never_returns_a_transition():
    """The shadow provider's IntentResult.transition is always None."""

    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )
    request = _make_request(ChemistryIntent.SILICATE_LIQUIDUS)
    result = shadow.dispatch(request)
    assert result.transition is None


def test_unsupported_intent_returns_unsupported_status():
    """Defence in depth: bypassing the registry must not produce silent garbage."""

    shadow = MAGEMinShadowProvider()
    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={}, species_formula_registry={},
        ),
        temperature_C=1400.0,
        pressure_bar=1.0,
    )
    result = shadow.dispatch(request)
    assert result.status == 'unsupported'
    assert result.transition is None


def test_shadow_diagnostic_shape_serialisable():
    """``IntentResult.diagnostic`` MUST be a plain mapping with the
    keys the parity comparator looks up.

    Goal spec acceptance: parity warnings visible in the debug UI.
    The UI projects ``shadow_trace`` to JSON; a dataclass or
    MappingProxy on the diagnostic would break that path.
    """
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 0.8, 'olivine': 0.2},
        liquid_composition_wt_pct={'SiO2': 50.0},
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )
    request = _make_request(ChemistryIntent.SILICATE_LIQUIDUS)
    result = shadow.dispatch(request)

    diag = result.diagnostic
    # Mapping-shaped; the kernel froze it via MappingProxyType.
    assert diag['liquidus_T_K'] == pytest.approx(1700.0)
    assert diag['phase_modes_wt_pct']['liquid'] == pytest.approx(80.0)
    assert diag['phase_modes_wt_pct']['olivine'] == pytest.approx(20.0)
    assert diag['engine_version'] == 'fake-magemin'


# ---------------------------------------------------------------------------
# Optional real-engine probe (skipped without a MAGEMin binary)
# ---------------------------------------------------------------------------


def test_provider_with_real_adapter_smoke():
    """When the MAGEMin binary is present, the provider returns OK or
    a clean unavailable status without raising.

    Acts as a real-engine smoke probe; skipped automatically when the
    binary is not located. Disagreement -> parity warning -> still
    not a test failure. The kernel-level parity tolerance is unchanged.
    """
    from simulator.melt_backend.magemin import MAGEMinBackend

    backend = MAGEMinBackend()
    initialised = backend.initialize({})
    if not initialised:
        pytest.skip(
            'MAGEMin binary not located; real-engine parity probe skipped',
        )

    shadow = MAGEMinShadowProvider(backend=backend)
    request = _make_request(
        ChemistryIntent.SILICATE_LIQUIDUS,
        temperature_C=1400.0,
        pressure_bar=1000.0,  # 1 kbar; MAGEMin's CLI expects kbar via the
                              # adapter's pressure_kbar conversion.
    )
    result = shadow.dispatch(request)
    assert result.transition is None
    assert result.status in (
        'ok', 'not_converged', 'out_of_domain', 'unavailable',
    )


# ---------------------------------------------------------------------------
# Status vocabulary: the provider must not invent 'ok'
#
# Milestone coherence audit 2026-08-26, P1. The provider used to keep a
# private four-token allowlist and map every other adapter status to 'ok'.
# That is a fail-open at the worst possible seam: MAGEMinShadowProvider is
# the registered fallback for the authoritative GATE_LIQUID_FRACTION intent,
# and the freeze-gate consumer prefers IntentResult.status over the
# diagnostic's backend status when deciding whether to accept solidus /
# liquidus bounds. A failed MAGEMin result carrying stale finite bounds could
# therefore set the liquid-fraction multiplier applied to authoritative
# evaporation rates -- silently halving a real rate instead of refusing.
#
# 590b1cda gave the status vocabulary a single validated owner
# (IntentResult / INTENT_RESULT_STATUSES). AlphaMELTS was converted to pass
# its adapter status straight through; MAGEMin was missed. These two tests
# pin the conversion.
# ---------------------------------------------------------------------------


class _StatuslessEquilibriumResult:
    """An adapter result that never declares a status at all.

    Deliberately NOT a subclass of :class:`_FakeEquilibriumResult`, whose
    ``status`` defaults to ``'ok'`` -- inheriting it would hand the test the
    very attribute it exists to prove is absent.
    """

    def __init__(self) -> None:
        self.liquidus_T_K = 1700.0
        self.phase_masses_kg = {'liquid': 1.0}
        self.phases_present = ('liquid',)
        self.liquid_composition_wt_pct = {}
        self.liquid_fraction = 1.0
        self.warnings = ()


def test_unknown_adapter_status_is_refused_at_the_dto_boundary():
    """An unrecognised adapter status must raise, not become 'ok'.

    FALSIFIABILITY: this goes red the moment the provider reintroduces any
    local status mapping, because a mapped token constructs an IntentResult
    successfully and no exception escapes. Against the pre-fix provider the
    dispatch returned status='ok' and this test fails on the missing raise --
    it is not a test that passes either way.

    'failed' is used because it is a plausible adapter token that is NOT in
    INTENT_RESULT_STATUSES; the point is the handling of tokens the kernel
    does not recognise, not this token in particular.
    """
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 0.5, 'olivine': 0.5},
        status='failed',
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )
    request = _make_request(ChemistryIntent.SILICATE_LIQUIDUS)

    with pytest.raises(IntentResultStatusError) as excinfo:
        shadow.dispatch(request)

    # The error must name the offending token, so an operator reading the
    # traceback learns what the engine actually said rather than only that
    # something was wrong.
    assert excinfo.value.status == 'failed'
    assert 'failed' in str(excinfo.value)


def test_absent_adapter_status_reports_unavailable_not_ok():
    """A result that declares no status is an absent answer, not a good one.

    Same fail-open class as the allowlist, one layer down: the projection
    used to coerce a missing or empty status to 'ok'. Silence must not read
    as affirmation.

    FALSIFIABILITY: goes red if the fallback returns to 'ok' (the assertion
    below fails on the token) or if the projection starts raising on a
    statusless object (the dispatch raises before the assertion). Against the
    pre-fix provider this returned 'ok' and the test fails.
    """
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=_StatuslessEquilibriumResult()),
    )
    result = shadow.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    assert result.status == 'unavailable'
    assert result.diagnostic['backend_status'] == 'unavailable'
    # Writer purity is unchanged by this fix and is worth re-asserting here,
    # because a status change is exactly the kind of edit that could tempt
    # someone to start populating a transition.
    assert result.transition is None


def test_in_vocabulary_adapter_statuses_pass_through_unchanged():
    """The four statuses the adapter actually emits reach the DTO intact.

    Guards the other half of the fix: passing the token through must not
    have narrowed what a healthy MAGEMin run can report. Every
    EquilibriumResult / LiquidusSolidusResult construction in the tree uses
    one of these four, so this is the live vocabulary, not a hypothetical.
    """
    for token in ('ok', 'not_converged', 'out_of_domain', 'unavailable'):
        fake_equilibrium = _FakeEquilibriumResult(
            liquidus_T_K=1700.0,
            phase_masses_kg={'liquid': 1.0},
            status=token,
        )
        shadow = MAGEMinShadowProvider(
            backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
        )
        result = shadow.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))
        assert result.status == token, (
            f'adapter status {token!r} was rewritten to {result.status!r}'
        )


@pytest.mark.parametrize("declared", [None, ""])
def test_explicit_none_or_empty_status_reports_unavailable_not_ok(declared):
    """An explicitly-declared empty status is silence too, and must refuse.

    The statusless-object test above covers a MISSING attribute. This covers
    the other half of the same hunk: an attribute that exists and says
    nothing. Both are the absence of an answer and both must land on
    'unavailable' rather than 'ok'.

    Called out by review as unpinned: the projection's ``or 'unavailable'``
    handles None and '' as well as the missing case, but only the missing
    case had a test, so a change to ``getattr(equilibrium, 'status', 'unavailable')``
    -- which drops the falsy handling while looking equivalent -- would have
    kept every existing test green.

    FALSIFIABILITY: goes red if the falsy branch is removed (None/'' would
    then reach IntentResult and raise, or coerce to the string 'None').
    """
    fake_equilibrium = _FakeEquilibriumResult(
        liquidus_T_K=1700.0,
        phase_masses_kg={'liquid': 1.0},
        status=declared,
    )
    shadow = MAGEMinShadowProvider(
        backend=_FakeMAGEMinBackend(equilibrium=fake_equilibrium),
    )
    result = shadow.dispatch(_make_request(ChemistryIntent.SILICATE_LIQUIDUS))

    assert result.status == 'unavailable'
    assert result.diagnostic['backend_status'] == 'unavailable'
