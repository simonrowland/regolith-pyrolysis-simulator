"""Tests for the MAGEMin melt-backend adapter (simulator/melt_backend/magemin.py).

Distinct from ``tests/test_magemin_shadow_provider.py``, which covers the
``engines/magemin/`` kernel-shadow scaffold. This file defends the
``MeltBackend`` adapter contract -- in particular that the adapter is not
silently ignored if mis-selected as the active melt backend, that it fails
closed when MAGEMin is absent, that the mocked-present path converts the
oxide basis and pressure units correctly, and (skipif-guarded) that the
real MAGEMin binary runs end to end when one is built locally.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import warnings
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import simulator.engine_pool as engine_pool
from simulator.engine_pool import (
    EngineWorkerDied,
    EngineWorkerPoolLifecycleError,
    EngineWorkerRemoteError,
    EngineWorkerTimeout,
    EngineWorkerUnavailable,
)
from engines.alphamelts.domain import AlphaMELTSDomainGate
from engines.domain_reason import OutOfDomainReason
from engines.magemin.domain import MAGEMinDomainGate
import simulator.melt_backend.liquidus as liquidus_module
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import LiquidFractionInvalidError, MeltCompositionError
from simulator.feedstock_composition import FEOT_FROM_FE2O3
from simulator.fe_redox import (
    FE_FEO_BUFFER_STANDARD,
    REDOX_STATE_REFERENCE_T_K,
    RedoxState,
)
from simulator.melt_backend.magemin import (
    COMPOSITION_PROJECTED,
    MAGEMIN_MODE_VECTOR_MASS_DEFICIT,
    MAGEMIN_WARM_CALL_TIMEOUT_S,
    MAGEMIN_WARM_LIQUIDUS_BUDGET_S,
    MAGEMinBackend,
)

# Thread-rendezvous guards throughout this module: these bound a HANG, they do
# not assert a latency. Any test that means to claim "within N seconds" should
# use its own explicit figure and say so, rather than borrowing this one.
_RENDEZVOUS_TIMEOUT_S = 30.0


def _bootstrap_sleeping_magemin_worker():
    return None, 'sleeping-worker-ready'


def _handle_sleeping_magemin_request(_resource, _request, _errlog):
    while True:
        time.sleep(0.05)


def _handle_successful_magemin_request(_resource, _request, _errlog):
    return {'phases': {'liq': {'mass_kg': 1.0}}}


def _handle_reusable_magemin_request(_resource, request, _errlog):
    return request.get('request_id')


class _RegisteredMageminPool:
    def __init__(self):
        self.workers = (types.SimpleNamespace(
            name="MAGEMin subprocess pool slot 0",
        ),)
        self.closed = False
        engine_pool._LIVE_ENGINE_POOLS.add(self)

    def close(self, *, cancel_pending=False, timeout_s=None):
        self.closed = True
        engine_pool._LIVE_ENGINE_POOLS.discard(self)

    def _request_cancel_pending(self):
        return None


@pytest.fixture(scope="module")
def _preexisting_magemin_pool():
    pool = _RegisteredMageminPool()
    yield pool
    pool.close(cancel_pending=True)


@pytest.fixture
def _test_owned_magemin_backend(request):
    backend = MAGEMinBackend()
    backend._subprocess_pool = _RegisteredMageminPool()
    request.addfinalizer(backend.close)
    return backend


def test_adapter_cleanup_owns_only_the_backend_created_by_that_test(
    _preexisting_magemin_pool,
    _test_owned_magemin_backend,
):
    assert _preexisting_magemin_pool.closed is False
    assert _test_owned_magemin_backend._subprocess_pool.closed is False


def test_preexisting_magemin_pool_survives_adapter_test_teardown(
    _preexisting_magemin_pool,
):
    assert _preexisting_magemin_pool.closed is False


def _disable_configured_magemin_path(monkeypatch):
    import simulator.engine_local_config as engine_local_config

    monkeypatch.setattr(
        engine_local_config,
        "configured_magemin_binary_path",
        lambda: None,
    )


def _make_available_magemin(monkeypatch, fake_module):
    """Force a MAGEMinBackend to initialize() successfully with a fake bridge.

    The simulator constructor never calls ``initialize()``; tests that
    want an *available* MAGEMin backend must call it explicitly. We stub
    binary discovery and the Python-bridge import so no real MAGEMin
    install is required.
    """
    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("pymagemin", fake_module),
    )


def _make_fake_magemin_cli_liquidus(monkeypatch):
    calls = []

    def fake_subprocess_run(args, **_kwargs):
        args = list(args)
        temperature_arg = next(
            (arg for arg in args if arg.startswith("--Temp=")),
            None,
        )
        if temperature_arg is None:
            return types.SimpleNamespace(
                returncode=0,
                stdout="",
                stderr="",
            )
        temperature_C = float(
            temperature_arg.split("=", 1)[1]
        )
        calls.append(args)
        liquid_fraction = max(
            0.0,
            min(1.0, (temperature_C - 1000.0) / 200.0),
        )
        stdout = (
            "Phase : liq ol qfm\n"
            f"Mode  : {liquid_fraction:.6f} "
            f"{1.0 - liquid_fraction:.6f} 0.000000\n"
        )
        return types.SimpleNamespace(
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda _explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module

    monkeypatch.setattr(
        magemin_module.subprocess,
        "run",
        fake_subprocess_run,
    )
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"warm_worker": False}) is True
    return backend, calls


def _assert_magemin_cli_absolute_targets(calls, expected_fO2_log):
    assert len(calls) > 10, "expected coarse samples and refinement probes"
    for args in calls:
        assert "--buffer=qfm" in args
        temperature_C = float(
            next(arg.split("=", 1)[1] for arg in args if arg.startswith("--Temp="))
        )
        buffer_n = float(
            next(
                arg.split("=", 1)[1]
                for arg in args
                if arg.startswith("--buffer_n=")
            )
        )
        recovered = MAGEMinBackend._qfm_logfo2_oneill(temperature_C) + buffer_n
        assert recovered == pytest.approx(expected_fO2_log, abs=1.0e-6)


def _assert_magemin_cli_redox_state_targets(calls, state):
    assert len(calls) > 10, "expected coarse samples and refinement probes"
    expected_targets = set()
    for args in calls:
        assert "--buffer=qfm" in args
        temperature_C = float(
            next(arg.split("=", 1)[1] for arg in args if arg.startswith("--Temp="))
        )
        buffer_n = float(
            next(
                arg.split("=", 1)[1]
                for arg in args
                if arg.startswith("--buffer_n=")
            )
        )
        expected = state.log_fO2_at(temperature_C + 273.15)
        expected_targets.add(expected)
        recovered = MAGEMinBackend._qfm_logfo2_oneill(temperature_C) + buffer_n
        assert recovered == pytest.approx(expected, abs=1.0e-6)
    assert len(expected_targets) > 1


def _make_absent_magemin(monkeypatch):
    """Force all MAGEMin availability probes to report absent."""
    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: None),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: (None, None),
    )


class _ImmediateFailurePool:
    def __init__(self, exception):
        self.exception = exception

    def submit(self, *_args, **_kwargs):
        future = Future()
        future.set_exception(self.exception)
        return future

    def _abort_request(self, _future, *, diagnostic_detail=None):
        return False


def _make_pooled_subprocess_backend(exception):
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')
    backend._subprocess_pool = _ImmediateFailurePool(exception)
    return backend


@pytest.mark.parametrize(
    ('database', 'capture_error', 'nonfinite_projection'),
    [
        pytest.param('ig', False, False, id='normal-database'),
        pytest.param('ig' + (' ' * 9000), False, False, id='oversized-database'),
        pytest.param('ig' + (' ' * 9000), True, False, id='capture-error'),
        pytest.param('ig', False, True, id='nonfinite-projection'),
    ],
)
def test_magemin_unfinished_pool_future_is_timed_out_and_aborted(
    monkeypatch, database, capture_error, nonfinite_projection,
):
    class RunningFuture(Future):
        cancel_attempted = False

        def cancel(self):
            self.cancel_attempted = True
            return super().cancel()

    pending = RunningFuture()
    assert pending.set_running_or_notify_cancel()

    class PendingPool:
        def __init__(self):
            self.abort_calls = 0
            self.diagnostic_detail = None

        def submit(self, *_args, **_kwargs):
            return pending

        def _abort_request(self, future, *, diagnostic_detail=None):
            assert future is pending
            assert future.cancel_attempted
            assert future.running()
            self.diagnostic_detail = diagnostic_detail
            self.abort_calls += 1

    pool = PendingPool()
    backend = _make_pooled_subprocess_backend(None)
    backend._subprocess_pool = pool
    backend._database = database
    if capture_error:
        def fail_diagnostic_capture(**_kwargs):
            raise RuntimeError('diagnostic capture failed')

        monkeypatch.setattr(
            backend,
            '_magemin_timeout_diagnostic_detail',
            fail_diagnostic_capture,
        )
    backend._config = {
        'warm_call_timeout_s': 0.05,
        'worker_startup_timeout_s': 0.05,
    }
    projection = backend._build_db_bulk_projection({'SiO2': 60.0, 'MgO': 40.0})
    if nonfinite_projection:
        projection = replace(
            projection,
            vector=(float('nan'), *projection.vector[1:]),
        )
    outcomes = []

    def call():
        try:
            backend._call_magemin(
                projection,
                temperature_C=1200.0,
                pressure_bar=1.0,
                fO2_log=-8.0,
                composition_mol={
                    'SiO2': 0.5,
                    'MgO': 0.25,
                    **{f'species-{index}': 0.01 for index in range(100)},
                },
            )
        except BaseException as exc:  # returned to the asserting thread
            outcomes.append(exc)

    started_at = time.monotonic()
    waiter = threading.Thread(target=call, daemon=True)
    waiter.start()
    waiter.join(timeout=3.0)
    try:
        assert not waiter.is_alive(), (
            'MAGEMin waited forever on its pool future'
        )
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], EngineWorkerTimeout)
        assert outcomes[0].phase == 'job'
        assert outcomes[0].timeout_s == pytest.approx(2.5)
        assert pending.cancel_attempted
        assert pending.running()
        assert pool.abort_calls == 1
        assert pool.diagnostic_detail is not None
        assert len(pool.diagnostic_detail) <= 8192
        diagnostic = json.loads(pool.diagnostic_detail)
        if capture_error:
            assert diagnostic['diagnostic_capture_error'] == 'RuntimeError'
            assert diagnostic['database_truncated'] is True
        else:
            assert diagnostic['argv'][0] == '/fake/MAGEMin'
            if nonfinite_projection:
                assert diagnostic['projected_bulk_vector_wt_pct'][0] == 'nan'
        if not capture_error and len(database) > 8192:
            assert diagnostic['argv_truncated'] is True
            assert diagnostic['database_truncated'] is True
        elif not capture_error:
            assert diagnostic['database'] == database
            assert diagnostic['argv'][2] == '--db=ig'
        if not capture_error:
            assert 'composition_mol_truncated":true' in pool.diagnostic_detail
            assert 'composition_mol' in pool.diagnostic_detail
        assert 'projected_bulk' in pool.diagnostic_detail
        assert 'oxide_names' in pool.diagnostic_detail
        assert 'temperature_C' in pool.diagnostic_detail
        assert 'pressure_bar' in pool.diagnostic_detail
        assert 'fO2_log' in pool.diagnostic_detail
        assert 'database' in pool.diagnostic_detail
        assert 'argv' in pool.diagnostic_detail
        if not capture_error:
            assert 'buffer' in pool.diagnostic_detail
            assert 'buffer_n' in pool.diagnostic_detail
            assert '--Temp=1200.000000' in pool.diagnostic_detail
            assert '--Pres=0.001000' in pool.diagnostic_detail
            assert '--db=ig' in pool.diagnostic_detail
        assert time.monotonic() - started_at < 3.0
    finally:
        if waiter.is_alive():
            pending.set_result({'phases': {'liq': {'mass_kg': 1.0}}})
            waiter.join(timeout=1.0)


@pytest.mark.parametrize(
    'failure_kind',
    [
        pytest.param('capture-error', id='capture-error'),
        pytest.param('log-oserror', id='log-oserror'),
    ],
)
def test_magemin_timeout_kills_and_reaps_a_hung_worker(
    tmp_path,
    monkeypatch,
    failure_kind,
):
    diagnostic_log_path = tmp_path / 'magemin-diagnostics-0.log'
    pool = engine_pool.EngineWorkerPool(
        lambda index: engine_pool.WarmEngineWorker(
            name=f'fake MAGEMin slot {index}',
            bootstrap=_bootstrap_sleeping_magemin_worker,
            handler=_handle_sleeping_magemin_request,
            startup_timeout_s=2.0,
            call_timeout_s=30.0,
            diagnostic_log_path=diagnostic_log_path,
        ),
        size=1,
    )
    worker = pool.workers[0]
    original_call = worker.call

    def call_with_long_worker_wall(request, *, timeout_s=None):
        return original_call(request, timeout_s=30.0)

    worker.call = call_with_long_worker_wall
    backend = _make_pooled_subprocess_backend(None)
    backend._subprocess_pool = pool
    backend._config = {
        'warm_call_timeout_s': 0.05,
        'worker_startup_timeout_s': 2.0,
    }
    if failure_kind == 'capture-error':
        def fail_diagnostic_capture(**_kwargs):
            raise RuntimeError('diagnostic capture failed')

        monkeypatch.setattr(
            backend,
            '_magemin_timeout_diagnostic_detail',
            fail_diagnostic_capture,
        )
    else:
        original_open = Path.open

        def fail_diagnostic_log_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get('mode', 'r')
            if Path(path) == diagnostic_log_path and mode == 'a':
                raise OSError('diagnostic log unavailable')
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'open', fail_diagnostic_log_open)
    projection = backend._build_db_bulk_projection({'SiO2': 60.0, 'MgO': 40.0})
    worker_pid = worker.process.pid
    outcomes = []

    def call():
        try:
            backend._call_magemin(
                projection,
                temperature_C=1200.0,
                pressure_bar=1.0,
                fO2_log=-8.0,
                composition_mol={'SiO2': 0.5, 'MgO': 0.25},
            )
        except BaseException as exc:  # returned to the asserting thread
            outcomes.append(exc)

    started_at = time.monotonic()
    waiter = threading.Thread(target=call, daemon=True)
    waiter.start()
    try:
        waiter.join(timeout=6.0)
        assert not waiter.is_alive(), 'MAGEMin did not bound its pool future'
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], EngineWorkerTimeout)
        assert outcomes[0].phase == 'job'
        assert outcomes[0].timeout_s == pytest.approx(4.45)
        assert outcomes[0].diagnostic_detail
        assert outcomes[0].diagnostic_detail in str(outcomes[0])
        timeout_text = str(outcomes[0])
        for field in (
            'composition_mol',
            'SiO2',
            'MgO',
            'projected_bulk',
            'oxide_names',
            'temperature_C',
            'pressure_bar',
            'fO2_log',
            'buffer',
            'buffer_n',
            'database',
            'argv',
        ):
            assert field in timeout_text
        if failure_kind == 'capture-error':
            assert json.loads(outcomes[0].diagnostic_detail)[
                'diagnostic_capture_error'
            ] == 'RuntimeError'
            diagnostic_log = diagnostic_log_path.read_text(encoding='utf-8')
            assert outcomes[0].diagnostic_detail in diagnostic_log
        else:
            for arg in (
                '--Temp=1200.000000',
                '--Pres=0.001000',
                '--db=ig',
            ):
                assert arg in timeout_text
        assert time.monotonic() - started_at < 5.5
        assert worker.process is None
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
    finally:
        pool.close(cancel_pending=True, timeout_s=3.0)
        waiter.join(timeout=1.0)


@pytest.mark.timeout(20)
def test_magemin_unmodified_worker_timeout_captures_request(tmp_path):
    diagnostic_log_path = tmp_path / 'magemin-diagnostics-0.log'
    pool = engine_pool.EngineWorkerPool(
        lambda index: engine_pool.WarmEngineWorker(
            name=f'fake MAGEMin slot {index}',
            bootstrap=_bootstrap_sleeping_magemin_worker,
            handler=_handle_sleeping_magemin_request,
            startup_timeout_s=2.0,
            call_timeout_s=0.05,
            diagnostic_log_path=diagnostic_log_path,
        ),
        size=1,
    )
    backend = _make_pooled_subprocess_backend(None)
    backend._subprocess_pool = pool
    backend._config = {
        'warm_call_timeout_s': 0.05,
        'worker_startup_timeout_s': 2.0,
    }
    projection = backend._build_db_bulk_projection({'SiO2': 60.0, 'MgO': 40.0})
    worker = pool.workers[0]
    worker_pid = worker.process.pid
    outcomes = []

    def call():
        try:
            backend._call_magemin(
                projection,
                temperature_C=1200.0,
                pressure_bar=1.0,
                fO2_log=-8.0,
                composition_mol={'SiO2': 0.5, 'MgO': 0.25},
            )
        except BaseException as exc:  # returned to the asserting thread
            outcomes.append(exc)

    waiter = threading.Thread(target=call, daemon=True)
    waiter.start()
    try:
        waiter.join(timeout=3.0)
        assert not waiter.is_alive(), 'worker timeout did not complete the future'
        assert len(outcomes) == 1
        timeout = outcomes[0]
        assert isinstance(timeout, EngineWorkerTimeout)
        assert timeout.worker_name == 'fake MAGEMin slot 0'
        assert timeout.timeout_s == pytest.approx(0.05)
        assert timeout.phase == 'job'
        assert timeout.diagnostic_detail in str(timeout)
        diagnostic = json.loads(timeout.diagnostic_detail)
        assert diagnostic['composition_mol']
        assert diagnostic['composition_wt_pct']
        assert diagnostic['projected_bulk_vector_wt_pct']
        assert diagnostic['oxide_names']
        assert diagnostic['temperature_C'] == 1200.0
        assert diagnostic['pressure_bar'] == 1.0
        assert diagnostic['fO2_log'] == -8.0
        assert diagnostic['buffer'] == 'qfm'
        assert diagnostic['database'] == 'ig'
        assert '--Temp=1200.000000' in diagnostic['argv']
        assert len(timeout.diagnostic_detail) <= 8192

        json_records = [
            json.loads(line)
            for line in diagnostic_log_path.read_text(
                encoding='utf-8',
            ).splitlines()
            if line.lstrip().startswith('{')
        ]
        assert json_records == [diagnostic]
        assert worker.process is None
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
    finally:
        pool.close(cancel_pending=True, timeout_s=3.0)
        waiter.join(timeout=1.0)


def test_successful_magemin_worker_call_writes_no_timeout_record(tmp_path):
    diagnostic_log_path = tmp_path / 'magemin-diagnostics-0.log'
    pool = engine_pool.EngineWorkerPool(
        lambda index: engine_pool.WarmEngineWorker(
            name=f'fake MAGEMin slot {index}',
            bootstrap=_bootstrap_sleeping_magemin_worker,
            handler=_handle_successful_magemin_request,
            startup_timeout_s=2.0,
            call_timeout_s=1.0,
            diagnostic_log_path=diagnostic_log_path,
        ),
        size=1,
    )
    backend = _make_pooled_subprocess_backend(None)
    backend._subprocess_pool = pool
    backend._config = {
        'warm_call_timeout_s': 1.0,
        'worker_startup_timeout_s': 2.0,
    }
    projection = backend._build_db_bulk_projection({'SiO2': 60.0, 'MgO': 40.0})
    try:
        assert backend._call_magemin(
            projection,
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-8.0,
            composition_mol={'SiO2': 0.5, 'MgO': 0.25},
        ) == {'phases': {'liq': {'mass_kg': 1.0}}}
        assert diagnostic_log_path.read_text(encoding='utf-8') == ''
    finally:
        pool.close(cancel_pending=True, timeout_s=3.0)


@pytest.mark.timeout(20)
def test_magemin_blocked_send_timeout_reaps_and_restarts_worker_slot():
    pool = engine_pool.EngineWorkerPool(
        lambda index: engine_pool.WarmEngineWorker(
            name=f'fake MAGEMin slot {index}',
            bootstrap=_bootstrap_sleeping_magemin_worker,
            handler=_handle_reusable_magemin_request,
            startup_timeout_s=2.0,
            call_timeout_s=30.0,
        ),
        size=1,
    )
    worker = pool.workers[0]
    connection = worker.connection
    process = worker.process
    worker_pid = process.pid
    send_entered = threading.Event()
    release_send = threading.Event()
    send_unblocked = threading.Event()
    real_send = connection.send
    original_abort = worker._abort_current_call

    def blocked_send(request):
        send_entered.set()
        release_send.wait()
        send_unblocked.set()
        if process.is_alive():
            return real_send(request)
        raise BrokenPipeError('worker exited during blocked send')

    connection.send = blocked_send

    def abort_and_release_send(abort_event=None):
        try:
            return original_abort(abort_event)
        finally:
            release_send.set()

    worker._abort_current_call = abort_and_release_send

    backend = _make_pooled_subprocess_backend(None)
    backend._subprocess_pool = pool
    backend._config = {
        'warm_call_timeout_s': 0.05,
        'worker_startup_timeout_s': 2.0,
    }
    projection = backend._build_db_bulk_projection({'SiO2': 60.0, 'MgO': 40.0})
    submitted = []
    original_submit = pool.submit

    def capture_submit(request, *, timeout_s=None):
        future = original_submit(request, timeout_s=timeout_s)
        submitted.append(future)
        return future

    pool.submit = capture_submit
    caller_finished = threading.Event()
    caller_outcomes = []
    caller_elapsed = []

    def call_magemin():
        started_at = time.monotonic()
        try:
            backend._call_magemin(
                projection,
                temperature_C=1200.0,
                pressure_bar=1.0,
                fO2_log=-8.0,
                composition_mol={'SiO2': 0.5, 'MgO': 0.25},
            )
        except BaseException as exc:
            caller_outcomes.append(exc)
        finally:
            caller_elapsed.append(time.monotonic() - started_at)
            caller_finished.set()

    caller = threading.Thread(target=call_magemin, daemon=True)
    caller.start()
    try:
        assert send_entered.wait(timeout=5.0)
        # The MAGEMin future wait is 4.45 s for these configured budgets;
        # the remaining margin covers the worker kill and reap joins.
        assert caller_finished.wait(timeout=7.0), (
            'caller did not return after aborting the blocked send'
        )
        assert caller_elapsed[0] < 7.0
        assert len(caller_outcomes) == 1
        assert isinstance(caller_outcomes[0], EngineWorkerTimeout)
        assert caller_outcomes[0].timeout_s == pytest.approx(4.45)
        # The consumer publishes the send failure after the caller's abort
        # returns; this is a hang guard, not a latency assertion.
        with pytest.raises(EngineWorkerDied) as send_failure:
            submitted[0].result(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert send_failure.value.phase == 'call'
        assert send_unblocked.is_set()
        assert worker.process is None
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)

        next_future = pool.submit({'request_id': 'slot-reused'}, timeout_s=2.0)
        assert next_future.result(timeout=5.0) == 'slot-reused'
        assert worker.process.pid != worker_pid
    finally:
        release_send.set()
        caller.join(timeout=5.0)
        connection.send = real_send
        worker._abort_current_call = original_abort
        pool.close(cancel_pending=True, timeout_s=3.0)


def _assert_magemin_bulk_drop_projected(
    result,
    expected_components,
    *,
    min_dropped_wt_pct,
    exact_components=False,
    min_dropped_mass_fraction=None,
):
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == COMPOSITION_PROJECTED
    assert result.diagnostics["backend_status"] == "out_of_domain"
    assert not result.phases_present
    projection = result.diagnostics["input_composition_projection"]
    dropped = set(projection["dropped_bulk_components"])
    expected = set(expected_components)
    notice = projection[COMPOSITION_PROJECTED]

    assert projection["status"] == "projected"
    assert projection["reason"] == "input_composition_projected"
    assert projection["magemin_database"] == "ig"
    if exact_components:
        assert dropped == expected
    else:
        assert expected <= dropped
    assert projection["bulk_dropped_wt_pct"] >= min_dropped_wt_pct
    assert notice["dropped_components"] == projection["dropped_bulk_components"]
    assert notice["dropped_mass_fraction"] == projection["dropped_mass_fraction"]
    assert notice["dropped_mass_fraction"] > 0.0
    if min_dropped_mass_fraction is not None:
        assert notice["dropped_mass_fraction"] >= min_dropped_mass_fraction
    assert "dropped_species" not in projection

    warning_text = " ".join(result.warnings)
    assert "dropped components outside documented bulk order" in warning_text
    assert "refused projected composition" in warning_text
    for component in expected_components:
        assert component in warning_text



def test_magemin_defaults_to_subprocess_even_if_pymagemin_importable(
    monkeypatch,
    request,
):
    _disable_configured_magemin_path(monkeypatch)
    fake_module = types.SimpleNamespace(minimize=lambda **kwargs: {})
    monkeypatch.setitem(sys.modules, 'pymagemin', fake_module)
    monkeypatch.setattr(
        MAGEMinBackend,
        '_locate_binary',
        staticmethod(lambda explicit: Path('/fake/MAGEMin')),
    )

    backend = MAGEMinBackend()
    request.addfinalizer(backend.close)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({}) is True

    assert backend._bridge == 'subprocess'
    assert backend._magemin_module is None


def test_magemin_phase_composition_projection_pin_has_no_repair_warning():
    from simulator.melt_backend.base import EquilibriumResult

    backend = MAGEMinBackend()
    result = EquilibriumResult(liquid_fraction=0.5)
    backend._populate_result(
        result,
        {
            'phases': {
                'liq': {
                    'mass_kg': 1.0,
                    'composition_wt_pct': {'SiO2': 50.0, 'MgO': 50.0},
                },
            },
        },
    )

    assert result.phase_compositions == {
        'liq': {'SiO2': 50.0, 'MgO': 50.0},
    }
    assert result.liquid_composition_wt_pct == {'SiO2': 50.0, 'MgO': 50.0}
    assert result.warnings == []


def test_magemin_reinitialize_closes_existing_warm_pool(monkeypatch):
    class FakePool:
        close_calls = 0

        def close(self, *, cancel_pending=False):
            assert cancel_pending is True
            self.close_calls += 1

    existing = FakePool()
    backend = MAGEMinBackend()
    backend._subprocess_pool = existing
    monkeypatch.setattr(
        MAGEMinBackend,
        '_locate_binary',
        staticmethod(lambda _explicit: Path('/fake/MAGEMin')),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        '_import_magemin_bridge',
        lambda self, *, requested: ('subprocess', None),
    )

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({'warm_worker': False}) is True

    assert existing.close_calls == 1
    assert backend._subprocess_pool is None


@pytest.mark.parametrize("caller_tmpdir", [None, "caller-selected-ompi-dir"])
def test_magemin_worker_pool_inherits_ompi_tmpdir_and_cleans_owned_dir(
    monkeypatch, caller_tmpdir
):
    import simulator.melt_backend.magemin as magemin_module

    observed = {}

    class FakePool:
        def __init__(self, _worker_factory, *, size):
            observed["size"] = size
            observed["ompi"] = os.environ.get("OMPI_MCA_orte_tmpdir_base")
            observed["prte"] = os.environ.get("PRTE_MCA_prte_tmpdir_base")

        def close(self, *, cancel_pending=False):
            assert cancel_pending is True

    monkeypatch.setattr(magemin_module, "EngineWorkerPool", FakePool)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda _explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    monkeypatch.delenv("OMPI_MCA_orte_tmpdir_base", raising=False)
    monkeypatch.delenv("PRTE_MCA_prte_tmpdir_base", raising=False)
    if caller_tmpdir is not None:
        monkeypatch.setenv("OMPI_MCA_orte_tmpdir_base", caller_tmpdir)

    backend = MAGEMinBackend()
    assert backend.initialize({}) is True
    assert observed["size"] == 1
    if caller_tmpdir is None:
        assert observed["ompi"] == backend._ompi_tmpdir
        assert observed["prte"] == backend._ompi_tmpdir
        assert backend._ompi_tmpdir is not None
        owned_tmpdir = Path(backend._ompi_tmpdir)
        assert owned_tmpdir.is_dir()
    else:
        assert observed["ompi"] == caller_tmpdir
        assert observed["prte"] == caller_tmpdir
        assert backend._ompi_tmpdir is None
        owned_tmpdir = None
    assert os.environ.get("OMPI_MCA_orte_tmpdir_base") == caller_tmpdir
    assert os.environ.get("PRTE_MCA_prte_tmpdir_base") is None

    backend.close()
    if owned_tmpdir is not None:
        assert not owned_tmpdir.exists()


def test_magemin_runtime_pool_uses_right_sized_warm_timeout(monkeypatch, request):
    import simulator.melt_backend.magemin as magemin_module

    submitted = {}

    class FakeFuture:
        def result(self, timeout=None):
            submitted['result_timeout_s'] = timeout
            return {'phases': {'liq': {'mass_kg': 1.0}}}

    class FakePool:
        def __init__(self, worker_factory, *, size):
            self.workers = (worker_factory(0),)
            self.size = size

        def submit(self, request, *, timeout_s=None):
            submitted.update(request=request, timeout_s=timeout_s)
            return FakeFuture()

        def close(self, *, cancel_pending=False):
            pass

    monkeypatch.setattr(magemin_module, 'EngineWorkerPool', FakePool)
    monkeypatch.setattr(
        MAGEMinBackend,
        '_locate_binary',
        staticmethod(lambda _explicit: Path('/fake/MAGEMin')),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        '_import_magemin_bridge',
        lambda self, *, requested: ('subprocess', None),
    )

    backend = MAGEMinBackend()
    request.addfinalizer(backend.close)
    assert backend.initialize({}) is True
    assert backend._subprocess_pool.size == 1
    assert backend._subprocess_pool.workers[0].call_timeout_s == pytest.approx(
        MAGEMIN_WARM_CALL_TIMEOUT_S
    )

    projection = backend._build_db_bulk_projection({
        'SiO2': 60.0,
        'MgO': 40.0,
    })
    assert backend._call_magemin(projection, 1200.0, 1.0, -8.0)
    assert submitted['timeout_s'] == pytest.approx(MAGEMIN_WARM_CALL_TIMEOUT_S)
    assert submitted['request']['call_timeout_s'] == pytest.approx(
        MAGEMIN_WARM_CALL_TIMEOUT_S + 0.5
    )
    assert submitted['result_timeout_s'] == pytest.approx(
        47.4
    )
    assert backend._call_magemin(
        projection,
        1200.0,
        1.0,
        -8.0,
        call_timeout_s=20.0,
    )
    assert submitted['result_timeout_s'] == pytest.approx(20.0)


@pytest.mark.parametrize(
    ('completion_s', 'should_timeout'),
    [
        pytest.param(45.97, False, id='legal-spawn-startup-solve'),
        pytest.param(47.45, True, id='one-poll-tick-past-envelope'),
    ],
)
def test_magemin_cold_start_envelope_covers_spawn_startup_and_solve(
    completion_s,
    should_timeout,
):
    class ScheduledFuture:
        def __init__(self):
            self.timeout_s = None
            self.cancelled = False

        def result(self, timeout=None):
            self.timeout_s = timeout
            if completion_s > timeout:
                raise FutureTimeoutError()
            return {'phases': {'liq': {'mass_kg': 1.0}}}

        def cancel(self):
            self.cancelled = True
            return False

    class ScheduledPool:
        def __init__(self):
            self.future = ScheduledFuture()
            self.abort_detail = None

        def submit(self, *_args, **_kwargs):
            return self.future

        def _abort_request(self, _future, *, diagnostic_detail=None):
            self.abort_detail = diagnostic_detail
            return False

    pool = ScheduledPool()
    backend = _make_pooled_subprocess_backend(None)
    backend._subprocess_pool = pool
    backend._config = {
        'warm_call_timeout_s': 15.0,
        'worker_startup_timeout_s': 30.0,
    }
    projection = backend._build_db_bulk_projection({'SiO2': 60.0, 'MgO': 40.0})

    if should_timeout:
        with pytest.raises(EngineWorkerTimeout) as caught:
            backend._call_magemin(
                projection,
                temperature_C=1200.0,
                pressure_bar=1.0,
                fO2_log=-8.0,
            )
        assert caught.value.timeout_s == pytest.approx(47.4)
        assert pool.future.cancelled
        assert pool.abort_detail is not None
    else:
        assert backend._call_magemin(
            projection,
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-8.0,
        ) == {'phases': {'liq': {'mass_kg': 1.0}}}
        assert completion_s == pytest.approx(0.99 + 29.99 + 14.99)

    assert pool.future.timeout_s == pytest.approx(47.4)


@pytest.mark.parametrize(
    'startup_timeout_s',
    [
        pytest.param(0.0, id='zero'),
        pytest.param(-0.01, id='negative'),
        pytest.param(float('nan'), id='nan'),
        pytest.param(float('inf'), id='infinity'),
    ],
)
def test_magemin_initialize_rejects_invalid_worker_startup_timeout(
    monkeypatch,
    startup_timeout_s,
):
    monkeypatch.setattr(
        MAGEMinBackend,
        '_locate_binary',
        staticmethod(lambda _explicit: Path('/fake/MAGEMin')),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        '_import_magemin_bridge',
        lambda self, *, requested: ('subprocess', None),
    )
    backend = MAGEMinBackend()

    with pytest.raises(ValueError):
        backend.initialize({
            'worker_startup_timeout_s': startup_timeout_s,
        })


def test_magemin_equilibrate_surfaces_typed_hang_without_disabling_backend():
    timeout = EngineWorkerTimeout(
        'MAGEMin slot 0',
        2.0,
        phase='job',
        failed_slots=(0,),
    )
    backend = _make_pooled_subprocess_backend(timeout)

    with pytest.raises(EngineWorkerTimeout) as raised:
        backend.equilibrate(
            1200.0,
            composition_mol={'SiO2': 1.0, 'MgO': 1.0},
        )

    assert raised.value is timeout
    assert raised.value.worker_name == 'MAGEMin slot 0'
    assert raised.value.timeout_s == 2.0
    assert raised.value.phase == 'job'
    assert raised.value.failed_slots == (0,)
    assert backend.is_available() is True


def test_magemin_worker_exit_returns_typed_not_converged():
    backend = _make_pooled_subprocess_backend(
        EngineWorkerDied(
            'call',
            'MAGEMin subprocess pool slot 0 worker exited without a result',
        )
    )

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'EngineWorkerDied'
    )
    assert 'worker exited without a result' in (
        result.diagnostics['backend_failure_message']
    )
    assert backend.is_available() is True


def test_magemin_pool_unavailable_returns_typed_not_converged():
    backend = _make_pooled_subprocess_backend(
        EngineWorkerUnavailable('MAGEMin subprocess pool slot 0')
    )

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'EngineWorkerUnavailable'
    )
    assert 'worker is unavailable' in (
        result.diagnostics['backend_failure_message']
    )


def test_magemin_pool_lifecycle_error_returns_typed_not_converged():
    backend = _make_pooled_subprocess_backend(
        EngineWorkerPoolLifecycleError(
            'engine worker pool has no live consumer to run this request'
        )
    )

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'EngineWorkerPoolLifecycleError'
    )
    assert 'no live consumer' in result.diagnostics['backend_failure_message']


def test_magemin_in_process_budget_exhaustion_returns_typed_status():
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'pymagemin'
    backend._magemin_module = types.SimpleNamespace(
        minimize=lambda **_kwargs: {'phases': {'liq': {'mass_kg': 1.0}}}
    )

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
        call_timeout_s=0.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'MAGEMinAggregateBudgetExceeded'
    )
    assert 'aggregate liquidus budget exhausted' in (
        result.diagnostics['backend_failure_message']
    )


def test_magemin_subprocess_slot_timeout_returns_typed_not_converged(
    monkeypatch, tmp_path,
):
    from simulator.melt_backend import magemin as magemin_module

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')
    backend._config = {'timeout_s': 0.001}
    lock_path = tmp_path / 'magemin.lock'
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK_DIR', tmp_path
    )
    monkeypatch.setattr(magemin_module, '_MAGEMIN_SUBPROCESS_LOCK', lock_path)
    monkeypatch.setattr(
        magemin_module, '_magemin_subprocess_slot_count', lambda: 1
    )

    def contended_flock(*_args, **_kwargs):
        raise BlockingIOError

    monkeypatch.setattr(magemin_module.fcntl, 'flock', contended_flock)
    monkeypatch.setattr(magemin_module.time, 'sleep', lambda _seconds: None)

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'MAGEMinSubprocessSlotTimeout'
    )
    assert 'slot timed out' in result.diagnostics['backend_failure_message']


@pytest.mark.parametrize(
    ('pooled', 'expected_budget_s'),
    ((True, MAGEMIN_WARM_LIQUIDUS_BUDGET_S),
     (False, liquidus_module.DEFAULT_LIQUIDUS_FINDER_BUDGET_S)),
)
def test_magemin_liquidus_budget_is_tight_only_for_warm_pool(
    monkeypatch,
    pooled,
    expected_budget_s,
):
    import simulator.melt_backend.magemin as magemin_module

    captured = {}

    def fake_finder(_sample_fraction, **kwargs):
        captured.update(kwargs)
        return liquidus_module.LiquidusSolidusResult(status='not_converged')

    monkeypatch.setattr(
        magemin_module,
        'find_liquidus_solidus_by_fraction',
        fake_finder,
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')
    backend._config = {}
    backend._subprocess_pool = object() if pooled else None

    backend.find_liquidus_solidus(
        composition_mol={'SiO2': 1.0, 'MgO': 1.0},
    )
    assert captured['budget_s'] == pytest.approx(expected_budget_s)


def test_magemin_cold_liquidus_budget_retains_bounded_diagnostic_headroom():
    assert liquidus_module.DEFAULT_LIQUIDUS_FINDER_BUDGET_S == pytest.approx(300.0)


# The test pins its OWN coarse grid rather than relying on the backend's
# defaults, so the arithmetic below stays true if those defaults change.
_B299_MIN_T_C = 1000.0
_B299_MAX_T_C = 1200.0
_B299_STEP_C = 50.0
_B299_COARSE_SAMPLES = int((_B299_MAX_T_C - _B299_MIN_T_C) / _B299_STEP_C) + 1


@pytest.mark.parametrize(
    'backend_token',
    ['out_of_domain', 'unavailable', 'not_converged'],
)
@pytest.mark.parametrize(
    'ok_samples_first',
    [0, _B299_COARSE_SAMPLES],
    ids=['refuses_on_first_sample', 'refuses_during_bisection'],
)
def test_magemin_liquidus_preserves_the_backend_refusal_token(
    backend_token,
    ok_samples_first,
):
    """The finder must report WHICH refusal the backend gave (b-299).

    A bare RuntimeError from the sampler falls through to the finder's
    generic library-boundary guard, which mints 'not_converged'.  That
    turns 'the engine was absent' (unavailable) or 'the physics is outside
    the model' (out_of_domain) into an affirmative claim that a solve ran
    and failed to converge.  The AlphaMELTS sampler already raises the
    typed LiquidusSampleError; MAGEMin was the outlier.

    The two parametrisations cover genuinely different finder phases.  The
    finder completes its ENTIRE coarse scan before it brackets, so a
    refusal on an early sample never reaches bracketing or bisection --
    letting the whole coarse grid succeed first is what forces the refusal
    into the bisection phase, and the off-grid assertion below is what
    proves it got there rather than merely claiming so.
    """
    from simulator.melt_backend.base import EquilibriumResult

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')
    backend._config = {}
    backend._subprocess_pool = None

    sampled_at: list[float] = []

    def fake_equilibrate(temperature_C, **_kwargs):
        sampled_at.append(float(temperature_C))
        healthy = len(sampled_at) <= ok_samples_first
        span = _B299_MAX_T_C - _B299_MIN_T_C
        return EquilibriumResult(
            temperature_C=float(temperature_C),
            pressure_bar=1.0e-6,
            fO2_log=-9.0,
            status='ok' if healthy else backend_token,
            liquid_fraction=(
                max(0.0, min(1.0, (float(temperature_C) - _B299_MIN_T_C) / span))
                if healthy else None
            ),
            phase_masses_kg={'liquid': 1.0} if healthy else {},
            warnings=[] if healthy else [f'MAGEMin said {backend_token}'],
        )

    backend.equilibrate = fake_equilibrate

    result = backend.find_liquidus_solidus(
        composition_mol={'SiO2': 1.0, 'MgO': 1.0, 'FeO': 1.0},
        min_T_C=_B299_MIN_T_C,
        max_T_C=_B299_MAX_T_C,
        scan_step_C=_B299_STEP_C,
    )

    # The backend has an early 'unavailable' return for an uninitialised
    # bridge, so a test that never reaches the sampler would pass vacuously
    # for backend_token='unavailable'.  Pin that the sampler actually ran.
    assert len(sampled_at) > ok_samples_first
    refused_at = sampled_at[ok_samples_first]

    if ok_samples_first:
        # Every coarse-grid temperature is an exact multiple of the step
        # above min_T_C; a bisection probe is not.  This is what makes the
        # 'refuses_during_bisection' id honest rather than aspirational.
        offset_steps = (refused_at - _B299_MIN_T_C) / _B299_STEP_C
        assert offset_steps != pytest.approx(round(offset_steps)), (
            f'refusal at {refused_at} C is still on the coarse grid; '
            'this case is not exercising bisection'
        )

    assert result.status == backend_token


def test_magemin_and_alphamelts_reject_exact_major_oxide_boundary():
    boundary = {'SiO2': 50.0, 'MgO': 45.0}

    alpha_valid, _alpha_warnings, alpha_reason = (
        AlphaMELTSDomainGate.validate_with_reason(boundary)
    )
    magemin_valid, _magemin_warnings, magemin_reason = (
        MAGEMinDomainGate.validate_with_reason(boundary)
    )

    assert alpha_valid is False
    assert magemin_valid is False
    assert alpha_reason == OutOfDomainReason.MAJOR_SUM.value
    assert magemin_reason == OutOfDomainReason.MAJOR_SUM.value


def test_magemin_as_active_backend_fails_closed_with_clear_message(monkeypatch):
    # MAGEMin is not wired into any active call site. If someone DOES
    # select it as the active melt backend, equilibrate() populates
    # phase_masses_kg but leaves ledger_transition None -- and core.py's
    # _get_equilibrium rejects exactly that combination. The adapter
    # docstring's "diagnostic" claim must mean "fails closed if
    # mis-selected", not "silently ignored".
    def minimize(**kwargs):
        # A populated post-equilibrium phase assemblage with NO ledger
        # transition -- the exact shape core.py must reject.
        return {
            "phases": {
                "liquid": {"mass_kg": 0.7},
                "olivine": {"mass_kg": 0.3},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True
    assert backend.is_available() is True

    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=1.0)

    with pytest.raises(RuntimeError, match="without an AtomLedger transition"):
        sim.step()


def test_magemin_equilibrate_never_emits_ledger_transition(monkeypatch):
    # Even on a successful library call, the adapter must not fabricate a
    # ledger transition: MAGEMin holds no AtomLedger authority.
    def minimize(**kwargs):
        return {"phases": {"liquid": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.ledger_transition is None
    # phase_masses_kg IS populated -- which is precisely why the result
    # is unsafe to route through _get_equilibrium as the active backend.
    assert result.phase_masses_kg
    assert backend.ledger_account_policies() == ()


def test_magemin_explicit_pymagemin_runtime_failure_retries_subprocess(monkeypatch):
    def minimize(**kwargs):
        raise RuntimeError("pymagemin minimizer crashed")

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"python_bridge": "pymagemin"}) is True
    assert backend._bridge == "pymagemin"

    fallback_calls = []

    def fake_subprocess(**kwargs):
        fallback_calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    monkeypatch.setattr(
        backend,
        "_call_magemin_subprocess",
        fake_subprocess,
    )

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert fallback_calls
    assert result.status == "ok"
    assert result.phase_masses_kg == {"liq": 1.0}
    assert any(
        "pymagemin bridge failed; retried subprocess" in warning
        for warning in result.warnings
    )


def test_magemin_bridge_fallback_subtracts_elapsed_aggregate_budget(monkeypatch):
    clock = {'t': 0.0}

    def minimize(**_kwargs):
        clock['t'] += 39.0
        raise RuntimeError('bridge failed late')

    backend = MAGEMinBackend()
    backend._bridge = 'pymagemin'
    backend._magemin_module = types.SimpleNamespace(minimize=minimize)
    backend._binary_path = Path('/tmp/fake-MAGEMin')
    seen = []

    def fake_subprocess(**kwargs):
        seen.append(kwargs['call_timeout_s'])
        return {'phases': {'liq': {'mass_kg': 1.0}}}

    monkeypatch.setattr(
        'simulator.melt_backend.magemin.time.monotonic',
        lambda: clock['t'],
    )
    monkeypatch.setattr(backend, '_call_magemin_subprocess', fake_subprocess)

    backend._call_magemin(
        bulk_projection=types.SimpleNamespace(
            composition_wt_pct={'SiO2': 50.0, 'MgO': 50.0},
            vector=(50.0, 50.0),
        ),
        temperature_C=1200.0,
        pressure_bar=1000.0,
        fO2_log=-8.0,
        call_timeout_s=40.0,
    )

    assert seen == [pytest.approx(1.0)]


def test_magemin_negative_convergence_evidence_discards_phase_rows(monkeypatch):
    fake_module = types.SimpleNamespace(
        minimize=lambda **_kwargs: {
            'converged': False,
            'phases': {'liq': {'mass_kg': 0.8}, 'ol': {'mass_kg': 0.2}},
        }
    )
    _make_available_magemin(monkeypatch, fake_module)
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({'python_bridge': 'pymagemin'}) is True

    result = backend.equilibrate(
        1200.0,
        composition_mol={'SiO2': 1.0, 'MgO': 1.0},
        pressure_bar=1000.0,
        fO2_log=-8.0,
    )

    assert result.status == 'not_converged'
    assert result.phases_present == []
    assert result.diagnostics['backend_status_reason'] == 'negative_convergence_evidence'


def test_magemin_object_negative_convergence_discards_phase_rows(monkeypatch):
    fake_module = types.SimpleNamespace(
        minimize=lambda **_kwargs: types.SimpleNamespace(
            converged=False,
            phases={'liq': {'mass_kg': 0.8}, 'ol': {'mass_kg': 0.2}},
        )
    )
    _make_available_magemin(monkeypatch, fake_module)
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        assert backend.initialize({'python_bridge': 'pymagemin'}) is True

    result = backend.equilibrate(
        1200.0,
        composition_mol={'SiO2': 1.0, 'MgO': 1.0},
        pressure_bar=1000.0,
        fO2_log=-8.0,
    )

    assert result.status == 'not_converged'
    assert result.phases_present == []
    assert result.diagnostics['backend_status_reason'] == 'negative_convergence_evidence'


@pytest.mark.parametrize(
    'mode_line',
    ['0.8 bad', '0.8 nan', '0.8 -0.2', '0.8 1.2'],
)
def test_magemin_subprocess_parser_rejects_any_invalid_mode_token(mode_line):
    from simulator.melt_backend.magemin import _MAGEMinEngineCallError

    with pytest.raises(_MAGEMinEngineCallError, match='Mode token') as raised:
        MAGEMinBackend._parse_subprocess_stdout(
            f'Phase: liq ol\nMode: {mode_line}\n'
        )
    assert raised.value.exception_type == 'MAGEMinOutputError'


def test_magemin_subprocess_parser_rejects_nonunit_mode_vector():
    from simulator.melt_backend.magemin import _MAGEMinEngineCallError

    with pytest.raises(
        _MAGEMinEngineCallError, match='Mode vector must sum to 1.0'
    ) as raised:
        MAGEMinBackend._parse_subprocess_stdout(
            'Phase: liq ol\nMode: 0.8 0.8\n'
        )
    assert raised.value.exception_type == 'MAGEMinOutputError'


def test_magemin_duplicate_liquid_modes_are_summed_from_captured_output():
    from simulator.melt_backend.base import EquilibriumResult

    stdout = (
        Path(__file__).parent
        / 'fixtures'
        / 'magemin'
        / 'lfdrop-diag-1-1700-phase-mode.stdout'
    ).read_text()
    parsed = MAGEMinBackend._parse_subprocess_stdout(stdout)
    result = EquilibriumResult(liquid_fraction=0.0)
    MAGEMinBackend()._populate_result(result, {'phases': parsed})

    # The captured Mode vector prints 0.71766 liquid and 0.99999 total mass;
    # the production resolver divides these because MAGEMin rounds to 5 places.
    assert result.liquid_fraction == pytest.approx(0.7176672, abs=1e-7)


def test_magemin_plante_mode_deficit_is_typed_out_of_domain(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=(
                'Phase :      cpx       ol       ol       ne      trd\n'
                'Mode  :  0.00000  0.00025  0.00010  0.00047  0.66605\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(
        'simulator.melt_backend.magemin.subprocess.run',
        fake_run,
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')

    result = backend.equilibrate(
        1302.0 - 273.15,
        composition_kg={'K2O': 0.4394, 'SiO2': 0.5606},
        fO2_log=-9.0,
        pressure_bar=1.0e-6,
    )

    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status'] == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        MAGEMIN_MODE_VECTOR_MASS_DEFICIT
    )
    assert result.diagnostics['magemin_mode_vector_raw_sum'] == pytest.approx(
        0.66687
    )
    assert result.diagnostics['magemin_mode_vector_deficit'] == pytest.approx(
        0.33313
    )
    assert result.diagnostics['magemin_mode_vector_input_components'] == {
        'K2O': pytest.approx(43.94),
        'SiO2': pytest.approx(56.06),
    }
    assert result.diagnostics[
        'magemin_mode_vector_unrepresented_input_components'
    ] is None
    assert result.diagnostics[
        'magemin_mode_vector_unrepresented_components_determined'
    ] is False
    assert result.phases_present == []
    assert result.phase_masses_kg == {}
    assert len(calls) == 1
    assert any('mass deficit=0.33313' in warning for warning in result.warnings)


def test_magemin_plante_mode_vector_is_not_renormalized():
    with pytest.raises(RuntimeError, match='Mode vector must sum to 1.0'):
        MAGEMinBackend._parse_subprocess_stdout(
            'Phase : cpx ol ol ne trd\n'
            'Mode  : 0.00000 0.00025 0.00010 0.00047 0.66605\n'
        )


def test_magemin_control_mode_vector_serialization_is_byte_identical():
    parsed = MAGEMinBackend._parse_subprocess_stdout(
        'Phase : liq cpx ol qfm\n'
        'Mode  : 0.72251 0.00001 0.23073 0.04675\n'
    )

    assert repr(parsed) == (
        "{'liq': {'mass_kg': 0.72251}, "
        "'cpx': {'mass_kg': 1e-05}, "
        "'ol': {'mass_kg': 0.23073}}"
    )


def test_magemin_explicit_julia_runtime_failure_retries_subprocess(monkeypatch):
    def single_point_minimization(*args):
        raise RuntimeError("julia minimizer crashed")

    fake_module = types.SimpleNamespace(
        Main=types.SimpleNamespace(
            MAGEMin=types.SimpleNamespace(
                single_point_minimization=single_point_minimization,
            ),
        ),
    )
    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("julia", fake_module),
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"python_bridge": "julia"}) is True
    assert backend._bridge == "julia"

    fallback_calls = []

    def fake_subprocess(**kwargs):
        fallback_calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    monkeypatch.setattr(
        backend,
        "_call_magemin_subprocess",
        fake_subprocess,
    )

    result = backend.equilibrate(
        1600.0,
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert fallback_calls
    assert result.status == "ok"
    assert result.phase_masses_kg == {"liq": 1.0}
    assert any(
        "julia bridge failed; retried subprocess" in warning
        for warning in result.warnings
    )


def test_magemin_liquidus_finder_bisects_fake_bridge(monkeypatch):
    def minimize(**kwargs):
        temperature_C = float(kwargs["T_C"])
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        phases = {}
        if frac > 0.0:
            phases["liq"] = {"mass_kg": frac}
        if frac < 1.0:
            phases["ol"] = {"mass_kg": 1.0 - frac}
        return {"phases": phases}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is True

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == "ok"
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)
    assert result.liquidus_T_K == pytest.approx(result.liquidus_T_C + 273.15)


def test_magemin_liquidus_scan_records_absolute_fo2_per_sample(monkeypatch):
    backend, calls = _make_fake_magemin_cli_liquidus(monkeypatch)

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0, "FeO": 1.0},
        fO2_log=-9.0,
        pressure_bar=1e-6,
        min_T_C=900.0,
        max_T_C=1300.0,
        scan_step_C=50.0,
        tolerance_C=1.0,
    )

    assert result.status == "ok"
    _assert_magemin_cli_absolute_targets(calls, -9.0)


def test_magemin_projected_retry_records_absolute_fo2_per_sample(monkeypatch):
    backend, calls = _make_fake_magemin_cli_liquidus(monkeypatch)

    result = backend.find_liquidus_solidus(
        composition_kg={"SiO2": 50.0, "MgO": 39.0, "P2O5": 10.0, "MnO": 1.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=900.0,
        max_T_C=1300.0,
        scan_step_C=50.0,
        tolerance_C=1.0,
    )

    assert result.status == "out_of_domain"
    assert result.diagnostics["projected_liquidus_status"] == "ok"
    _assert_magemin_cli_absolute_targets(calls, -9.0)


def test_magemin_liquidus_evaluates_redox_state_at_each_sample_temperature(
    monkeypatch,
):
    backend, calls = _make_fake_magemin_cli_liquidus(monkeypatch)
    absolute = RedoxState.absolute(-9.0)

    class RecordingAbsoluteState:
        kind = "absolute"

        def __init__(self):
            self.temperatures_K = []

        def log_fO2_at(self, temperature_K):
            self.temperatures_K.append(temperature_K)
            return absolute.log_fO2_at(temperature_K)

    state = RecordingAbsoluteState()
    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0, "FeO": 1.0},
        fO2_log=-9.0,
        redox_state=state,
        pressure_bar=1e-6,
        min_T_C=900.0,
        max_T_C=1300.0,
        scan_step_C=50.0,
        tolerance_C=1.0,
    )

    assert result.status == "ok"
    _assert_magemin_cli_absolute_targets(calls, -9.0)
    sample_temperatures_K = [
        float(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--Temp=")))
        + 273.15
        for args in calls
    ]
    assert state.temperatures_K == [REDOX_STATE_REFERENCE_T_K, *sample_temperatures_K]


def test_magemin_buffer_offset_is_evaluated_for_each_liquidus_sample(monkeypatch):
    backend, calls = _make_fake_magemin_cli_liquidus(monkeypatch)
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, -0.75)

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0, "FeO": 1.0},
        fO2_log=state.log_fO2_at(REDOX_STATE_REFERENCE_T_K),
        redox_state=state,
        pressure_bar=1e-6,
        min_T_C=900.0,
        max_T_C=1300.0,
        scan_step_C=50.0,
        tolerance_C=1.0,
    )

    assert result.status == "ok"
    _assert_magemin_cli_redox_state_targets(calls, state)


def test_magemin_projected_retry_preserves_buffer_offset_per_sample(monkeypatch):
    backend, calls = _make_fake_magemin_cli_liquidus(monkeypatch)
    state = RedoxState.buffer_offset(FE_FEO_BUFFER_STANDARD, -0.75)

    result = backend.find_liquidus_solidus(
        composition_kg={"SiO2": 50.0, "MgO": 39.0, "P2O5": 10.0, "MnO": 1.0},
        fO2_log=state.log_fO2_at(REDOX_STATE_REFERENCE_T_K),
        redox_state=state,
        pressure_bar=1.0,
        min_T_C=900.0,
        max_T_C=1300.0,
        scan_step_C=50.0,
        tolerance_C=1.0,
    )

    assert result.status == "out_of_domain"
    assert result.diagnostics["projected_liquidus_status"] == "ok"
    _assert_magemin_cli_redox_state_targets(calls, state)


def test_magemin_liquidus_finder_honors_aggregate_budget_config(monkeypatch):
    clock = {"t": 0.0}

    def minimize(**kwargs):
        temperature_C = float(kwargs["T_C"])
        clock["t"] += 0.1
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        phases = {}
        if frac > 0.0:
            phases["liq"] = {"mass_kg": frac}
        if frac < 1.0:
            phases["ol"] = {"mass_kg": 1.0 - frac}
        return {"phases": phases}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)
    monkeypatch.setattr(liquidus_module.time, "monotonic", lambda: clock["t"])

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"liquidus_finder_budget_s": 0.05}) is True

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == "not_converged"
    assert len(result.samples) == 1
    assert any(
        "liquidus finder exceeded aggregate budget 0.05s after 1 calls"
        in warning
        for warning in result.warnings
    )
    assert result.diagnostics["reason"] == "aggregate_budget_exceeded"
    assert result.diagnostics["call_count"] == 1
    assert result.diagnostics["last_T_C"] == pytest.approx(800.0)


def test_magemin_liquidus_finder_rejects_none_budget(monkeypatch):
    def minimize(**kwargs):
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"liquidus_finder_budget_s": None}) is True

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
    )
    assert result.status == "refused"
    assert any("None is rejected" in w for w in result.warnings)
    assert result.diagnostics.get("reason") == "invalid_input"
    assert result.diagnostics.get("budget_reason") == (
        "invalid_liquidus_finder_budget"
    )


@pytest.mark.parametrize(
    'budget',
    (object(), float('nan'), 0.0, -1.0, True, np.bool_(True)),
    ids=('non-numeric', 'non-finite', 'zero', 'negative', 'boolean', 'numpy-boolean'),
)
def test_magemin_liquidus_finder_invalid_budget_is_refused(budget):
    calls = []
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'pymagemin'
    backend._magemin_module = types.SimpleNamespace(
        minimize=lambda **kwargs: calls.append(kwargs),
    )
    backend._config = {'liquidus_finder_budget_s': budget}

    result = backend.find_liquidus_solidus(
        composition_mol={'SiO2': 1.0},
        min_T_C=800.0,
        max_T_C=1000.0,
        scan_step_C=100.0,
    )

    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert calls == []


def test_magemin_subprocess_timeout_clamped_to_remaining_budget(
    monkeypatch, tmp_path
):
    """Per-call subprocess timeout must be min(configured, remaining)."""
    import simulator.melt_backend.magemin as magemin_module

    # 2026-07-25 t-419 load-robustness: isolate the machine-wide K-slot
    # flock into a private namespace. With the shared lock dir, ANY
    # concurrent test's MAGEMin slot traffic could eat into the 1.5 s
    # budget during acquisition, surfacing 'cancelled while waiting for
    # subprocess slot' instead of the clamped-timeout message this test
    # pins — a flake that blamed the box instead of the shared lock.
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK_DIR', tmp_path,
    )
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK',
        tmp_path / 'magemin-subprocess.lock',
    )
    seen_timeouts: list[float] = []

    def fake_run(*args, **kwargs):
        seen_timeouts.append(float(kwargs["timeout"]))
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(
        "simulator.melt_backend.magemin.subprocess.run", fake_run
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/tmp/fake-MAGEMin")
    backend._config = {"timeout_s": 60.0, "database": "ig"}
    backend._database = "ig"
    backend._magemin_module = None

    # Remaining aggregate budget of 1.5s must clamp the 60s default.
    with pytest.raises(RuntimeError, match="timed out after 1.5"):
        backend._call_magemin_subprocess(
            bulk_projection=types.SimpleNamespace(
                vector=[1.0] * 11,
                composition_wt_pct={"SiO2": 50.0, "MgO": 50.0},
                warnings=(),
            ),
            temperature_C=1200.0,
            pressure_kbar=0.001,
            fO2_log=-8.0,
            call_timeout_s=1.5,
        )
    assert len(seen_timeouts) == 1
    assert 0.0 < seen_timeouts[0] <= 1.5


def test_magemin_liquidus_subprocess_path_preserves_budget_diagnostics(
    monkeypatch,
):
    """Subprocess-shaped residual timeout must keep structured budget diagnostics.

    The in-process minimize mock already covers the post-sample budget check.
    The real default bridge is subprocess; residual-clamped timeouts used to
    fall through as generic ``liquidus finder failed`` with empty diagnostics.
    Mock the subprocess boundary (no real engine required).
    """
    clock = {"t": 0.0}
    seen_timeouts: list[float] = []

    def fake_run(*args, **kwargs):
        timeout = float(kwargs["timeout"])
        seen_timeouts.append(timeout)
        # Burn residual wall the way a real residual-clamped hang would.
        clock["t"] += timeout
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=timeout)

    monkeypatch.setattr(
        "simulator.melt_backend.magemin.subprocess.run", fake_run
    )
    monkeypatch.setattr(liquidus_module.time, "monotonic", lambda: clock["t"])

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/tmp/fake-MAGEMin")
    backend._config = {
        "timeout_s": 60.0,
        "database": "ig",
        "liquidus_finder_budget_s": 0.05,
    }
    backend._database = "ig"
    backend._magemin_module = None

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == "not_converged"
    assert seen_timeouts == [pytest.approx(0.05)]
    assert any(
        "liquidus finder exceeded aggregate budget 0.05s after 0 calls"
        in warning
        for warning in result.warnings
    )
    assert result.diagnostics["reason"] == "aggregate_budget_exceeded"
    assert result.diagnostics["call_count"] == 0
    assert result.diagnostics["last_T_C"] == pytest.approx(800.0)
    assert result.diagnostics["budget_s"] == pytest.approx(0.05)
    assert result.diagnostics["elapsed_s"] == pytest.approx(0.05)
    assert not any(
        warning.startswith("liquidus finder failed:")
        for warning in result.warnings
    )


def test_magemin_liquidus_finder_unavailable_without_backend():
    backend = MAGEMinBackend()

    result = backend.find_liquidus_solidus(
        composition_mol={"SiO2": 1.0, "MgO": 1.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "unavailable"
    assert "not initialized" in " ".join(result.warnings)


# ----------------------------------------------------------------------
# Mocked-absent path: no MAGEMin binary, no bridge.
# ----------------------------------------------------------------------


def test_magemin_absent_binary_marks_backend_unavailable(monkeypatch):
    # No binary anywhere -> initialize() returns False and the backend
    # stays unavailable. The simulator can then route around it.
    _make_absent_magemin(monkeypatch)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({}) is False
    assert backend.is_available() is False


def test_magemin_absent_equilibrate_returns_empty_result_with_warning(
    monkeypatch,
):
    # When MAGEMin is unavailable, equilibrate() must NOT raise: it
    # returns an empty EquilibriumResult carrying an explanatory warning,
    # and -- critically for the shadow posture -- no ledger transition.
    _make_absent_magemin(monkeypatch)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})
    assert backend.is_available() is False

    result = backend.equilibrate(
        1500.0,
        composition_mol={"SiO2": 1.0, "MgO": 0.5},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.phases_present == []
    assert result.phase_masses_kg == {}
    assert result.ledger_transition is None
    assert result.warnings
    assert any("not initialized" in w for w in result.warnings)
    assert result.status == "unavailable"


# ----------------------------------------------------------------------
# Mocked-present path: a tiny fake bridge module.
# Verifies oxide-basis projection + pressure_bar -> P_GPa conversion +
# EquilibriumResult population.
# ----------------------------------------------------------------------


def test_magemin_fake_bridge_receives_oxide_wt_pct_basis(monkeypatch):
    # The fake bridge captures what the adapter handed it: the input must
    # be projected onto the MAGEMin database wt% basis and normalized to 100.
    # Non-basis species are a fail-closed out_of_domain path covered below;
    # this bridge-boundary test stays on in-domain oxides.
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    # Mol-native in-domain oxide input.
    result = backend.equilibrate(
        1400.0,
        composition_mol={
            "SiO2": 5.0,
            "Al2O3": 1.0,
            "MgO": 2.0,
            "CaO": 1.5,
        },
        fO2_log=-8.0,
        pressure_bar=5000.0,
    )

    assert result.status == "ok"
    comp = captured["composition"]
    assert set(comp).issubset(
        {
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "Cr2O3", "H2O",
        }
    )
    assert "SiO2" in comp and comp["SiO2"] > 0.0
    # MAGEMin database wt% basis is normalized to 100.
    assert sum(comp.values()) == pytest.approx(100.0, rel=1e-6)


def test_magemin_fake_bridge_receives_pressure_in_gpa(monkeypatch):
    # The binding-spec contract (§4) is pressure in GPa. The adapter must
    # convert pressure_bar -> P_GPa with 1 GPa = 10000 bar before the
    # library boundary, and also expose the kbar form the binary's CLI
    # wants (1 GPa = 10 kbar).
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    # 15000 bar == 1.5 GPa == 15 kbar.
    backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-8.0,
        pressure_bar=15000.0,
    )

    assert captured["P_GPa"] == pytest.approx(1.5)
    assert captured["P_kbar"] == pytest.approx(15.0)
    # Temperature is passed through in both C and K.
    assert captured["T_C"] == pytest.approx(1450.0)
    assert captured["T_K"] == pytest.approx(1450.0 + 273.15)


def test_magemin_bulk_projection_drop_is_composition_projected_refusal(
    monkeypatch,
):
    calls = []

    def minimize(**kwargs):
        calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1400.0,
        composition_kg={"SiO2": 50.0, "MgO": 50.0, "MnO": 1.0},
        fO2_log=-8.0,
        pressure_bar=5000.0,
    )

    assert calls == []
    _assert_magemin_bulk_drop_projected(
        result,
        ("MnO",),
        min_dropped_wt_pct=0.9,
        exact_components=True,
    )


def test_magemin_p2o5_bulk_is_composition_projected_refusal(monkeypatch):
    calls = []

    def minimize(**kwargs):
        calls.append(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1400.0,
        composition_kg={"SiO2": 50.0, "MgO": 40.0, "P2O5": 10.0},
        fO2_log=-8.0,
        pressure_bar=5000.0,
    )

    assert calls == []
    _assert_magemin_bulk_drop_projected(
        result,
        ("P2O5",),
        min_dropped_wt_pct=9.9,
        min_dropped_mass_fraction=0.099,
        exact_components=True,
    )
    notice = result.diagnostics["input_composition_projection"][
        COMPOSITION_PROJECTED
    ]
    assert notice["dropped_components"] == ["P2O5"]
    assert notice["dropped_mass_fraction"] == pytest.approx(0.10)
    assert notice["dropped_component_mass_fractions"]["P2O5"] == pytest.approx(
        0.10
    )


def test_magemin_liquidus_of_projected_bulk_stays_composition_projected():
    """The ig-order drop stays a refusal. The projected bulk's liquidus is
    attached for the freeze gate; it is not reported as status=ok.
    """
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = "subprocess"
    backend._binary_path = Path("/fake/MAGEMin")
    backend._config = {}
    backend._subprocess_pool = None
    calls = []

    def fake_call(*, bulk_projection, temperature_C, **_kwargs):
        composition = dict(bulk_projection.composition_wt_pct)
        calls.append(composition)
        assert "P2O5" not in composition
        assert "MnO" not in composition
        frac = max(0.0, min(1.0, (float(temperature_C) - 1000.0) / 200.0))
        phases = {}
        if frac > 0.0:
            phases["liq"] = {"mass_kg": frac}
        if frac < 1.0:
            phases["ol"] = {"mass_kg": 1.0 - frac}
        return {"phases": phases, "converged": True}

    backend._call_magemin = fake_call
    bulk = {"SiO2": 50.0, "MgO": 39.0, "P2O5": 10.0, "MnO": 1.0}
    result = backend.find_liquidus_solidus(
        composition_kg=bulk,
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=900.0,
        max_T_C=1300.0,
        scan_step_C=50.0,
        tolerance_C=2.0,
    )

    assert calls
    assert result.status == "out_of_domain"
    assert result.status != "ok"
    assert result.solidus_T_C == pytest.approx(1000.0, abs=2.0)
    assert result.liquidus_T_C == pytest.approx(1200.0, abs=2.0)
    assert result.solidus_T_C < result.liquidus_T_C
    notice = result.diagnostics["composition_projected_notice"]
    fractions = {
        row["component"]: row["mass_fraction"]
        for row in notice["dropped_components"]
    }
    assert fractions["P2O5"] == pytest.approx(0.10)
    assert fractions["MnO"] == pytest.approx(0.01)
    assert notice["authority"] == "extrapolated"
    assert notice["reason"] == "composition_projected"

    calls_before = len(calls)
    refused = backend.equilibrate(
        1400.0,
        composition_kg=bulk,
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    assert refused.status == "out_of_domain"
    assert refused.diagnostics["backend_status_reason"] == COMPOSITION_PROJECTED
    assert len(calls) == calls_before


def test_magemin_pressure_conversion_helpers_are_exact():
    # The conversion is load-bearing: a wrong factor is a silent O(10^n)
    # pressure error. Pin both legs.
    assert MAGEMinBackend._pressure_bar_to_GPa(10000.0) == pytest.approx(1.0)
    assert MAGEMinBackend._pressure_bar_to_GPa(0.0) == pytest.approx(0.0)
    assert MAGEMinBackend._pressure_bar_to_GPa(2.5e5) == pytest.approx(25.0)
    assert MAGEMinBackend._GPa_to_kbar(1.0) == pytest.approx(10.0)
    assert MAGEMinBackend._GPa_to_kbar(1.5) == pytest.approx(15.0)


def test_magemin_feot_conversion_uses_iupac_2feo_mass():
    # Current standard atomic weights: Fe 55.845, O 15.999 g/mol.
    feo_molar_mass = (
        MAGEMinBackend._FE_MOLAR_MASS_G_PER_MOL
        + MAGEMinBackend._O_MOLAR_MASS_G_PER_MOL
    )
    fe2o3_molar_mass = (
        2 * MAGEMinBackend._FE_MOLAR_MASS_G_PER_MOL
        + 3 * MAGEMinBackend._O_MOLAR_MASS_G_PER_MOL
    )
    feot_numerator = 2 * feo_molar_mass

    assert feot_numerator == pytest.approx(143.688)
    assert FEOT_FROM_FE2O3 == pytest.approx(
        feot_numerator / fe2o3_molar_mass
    )


def test_magemin_db_bulk_orders_match_installed_binary_help():
    assert MAGEMinBackend._DB_BULK_ORDERS == {
        "ig": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "Cr2O3", "H2O",
        ),
        "igad": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "Cr2O3",
        ),
        "mp": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "MnO", "H2O",
        ),
        "mb": (
            "SiO2", "Al2O3", "CaO", "MgO", "FeOt", "K2O", "Na2O",
            "TiO2", "O", "H2O",
        ),
        "um": ("SiO2", "Al2O3", "MgO", "FeOt", "O", "H2O", "S"),
        "ume": (
            "SiO2", "Al2O3", "MgO", "FeOt", "O", "H2O", "S", "CaO",
            "Na2O",
        ),
        "mtl": ("SiO2", "Al2O3", "CaO", "MgO", "FeOt", "Na2O"),
    }


def test_magemin_db_projection_keeps_mp_mno_and_records_igad_dry_drop():
    backend = MAGEMinBackend()
    composition = {
        "SiO2": 45.0,
        "Al2O3": 10.0,
        "FeO": 12.0,
        "MnO": 0.4,
        "Cr2O3": 0.3,
        "H2O": 2.0,
    }

    mp = backend._build_db_bulk_projection(composition, database="mp")
    assert mp.order == MAGEMinBackend._DB_BULK_ORDERS["mp"]
    assert mp.vector[mp.order.index("MnO")] == pytest.approx(0.4)
    assert "Cr2O3" not in mp.composition_wt_pct
    assert mp.dropped_components == ("Cr2O3",)
    assert mp.merged_components == ("FeO->FeOt+O",)
    assert any("Cr2O3" in warning for warning in mp.warnings)
    assert any("FeO->FeOt+O" in warning for warning in mp.warnings)

    igad = backend._build_db_bulk_projection(composition, database="igad")
    assert "H2O" not in igad.order
    assert "H2O" not in igad.composition_wt_pct
    assert "MnO" not in igad.composition_wt_pct
    assert igad.dropped_components == ("H2O", "MnO")
    assert any("H2O" in warning and "MnO" in warning for warning in igad.warnings)


def test_magemin_db_projection_ume_and_mtl_orders_are_not_ig_shaped():
    backend = MAGEMinBackend()
    composition = {
        "SiO2": 40.0,
        "Al2O3": 5.0,
        "MgO": 30.0,
        "FeO": 10.0,
        "K2O": 4.0,
        "Na2O": 3.0,
        "CaO": 2.0,
        "H2O": 1.0,
        "S": 0.5,
    }

    ume = backend._build_db_bulk_projection(composition, database="ume")
    assert ume.order == MAGEMinBackend._DB_BULK_ORDERS["ume"]
    assert "CaO" in ume.composition_wt_pct
    assert "Na2O" in ume.composition_wt_pct
    assert "S" in ume.composition_wt_pct
    assert "K2O" not in ume.composition_wt_pct

    mtl = backend._build_db_bulk_projection(composition, database="mtl")
    assert mtl.order == MAGEMinBackend._DB_BULK_ORDERS["mtl"]
    assert len(mtl.vector) == 6
    assert "H2O" not in mtl.composition_wt_pct
    assert "S" not in mtl.composition_wt_pct
    assert any("H2O" in warning and "S" in warning for warning in mtl.warnings)


def test_magemin_db_projection_rejects_unknown_bulk_component():
    backend = MAGEMinBackend()

    with pytest.raises(MeltCompositionError, match="unprojectable.*CO2"):
        backend._build_db_bulk_projection({"SiO2": 50.0, "CO2": 1.0})


def test_magemin_subprocess_argument_vector_matches_golden():
    backend = MAGEMinBackend()
    backend._binary_path = Path('/fake/MAGEMin')
    backend._database = 'ig'
    backend._config = {'fO2_buffer': 'nno'}
    projection = backend._build_db_bulk_projection(
        {'SiO2': 60.0, 'MgO': 40.0},
    )

    args, buffer_name, buffer_n, _warnings = (
        backend._build_magemin_subprocess_args(
            bulk_projection=projection,
            temperature_C=1234.5,
            pressure_kbar=10.0,
            fO2_log=-8.0,
        )
    )

    assert args == [
        '/fake/MAGEMin',
        '--Verb=0',
        '--db=ig',
        '--Temp=1234.500000',
        '--Pres=10.000000',
        '--sys_in=wt',
        '--Bulk=60.000000,0.000000,0.000000,40.000000,0.000000,0.000000,0.000000,0.000000,0.000000,0.000000,0.000000',
        '--buffer=nno',
        '--buffer_n=0.000000',
    ]
    assert (buffer_name, buffer_n) == ('nno', 0.0)


def test_magemin_ig_bulk_vector_folds_fe2o3_to_feot():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector({"FeO": 10.0, "Fe2O3": 1.0})
    feot_index = MAGEMinBackend._IG_BULK_ORDER.index("FeOt")
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    expected_feot = 10.0 + FEOT_FROM_FE2O3
    assert vector[feot_index] == pytest.approx(expected_feot)
    assert vector[oxygen_index] == pytest.approx(
        MAGEMinBackend._EXCESS_O_FROM_FE2O3_FACTOR
    )
    assert vector[feot_index] + vector[oxygen_index] == pytest.approx(11.0)


def test_magemin_explicit_fe2o3_does_not_apply_total_iron_o_provision():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector({"FeO": 10.0, "Fe2O3": 1.0})
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    assert vector[oxygen_index] == pytest.approx(
        1.0 * MAGEMinBackend._EXCESS_O_FROM_FE2O3_FACTOR
    )


def test_magemin_ig_bulk_vector_feo_total_iron_provisions_redox_o():
    backend = MAGEMinBackend()
    vector = backend._build_ig_bulk_vector(
        {
            "SiO2": 49.0,
            "Al2O3": 14.0,
            "CaO": 11.0,
            "MgO": 8.0,
            "FeO": 16.5,
        }
    )
    feot_index = MAGEMinBackend._IG_BULK_ORDER.index("FeOt")
    oxygen_index = MAGEMinBackend._IG_BULK_ORDER.index("O")

    feo_excess_o = 16.5 * MAGEMinBackend._EXCESS_O_FROM_FEO_TOTAL_IRON_FACTOR
    assert vector[oxygen_index] == pytest.approx(feo_excess_o)
    assert vector[feot_index] == pytest.approx(16.5 - feo_excess_o)
    assert vector[feot_index] + vector[oxygen_index] == pytest.approx(16.5)
    assert vector[oxygen_index] > 0.0


def test_magemin_fake_bridge_populates_equilibrium_result(monkeypatch):
    # A successful call must populate phases_present, phase_masses_kg and
    # liquid_fraction from the library's phase block -- and still leave
    # ledger_transition None (shadow posture).
    def minimize(**kwargs):
        return {
            "phases": {
                "liq": {"mass_kg": 0.8},
                "ol": {"mass_kg": 0.15},
                "spl": {"mass_kg": 0.05},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1350.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )

    assert set(result.phases_present) == {"liq", "ol", "spl"}
    assert result.phase_masses_kg["liq"] == pytest.approx(0.8)
    # liquid_fraction = liquid mass / total mass.
    assert result.liquid_fraction == pytest.approx(0.8 / 1.0)
    assert result.ledger_transition is None
    assert result.temperature_C == pytest.approx(1350.0)
    assert result.pressure_bar == pytest.approx(2000.0)
    assert result.status == "ok"


def test_magemin_ok_result_with_nonfinite_phase_mass_raises(monkeypatch):
    def minimize(**kwargs):
        return {
            "phases": {
                "liq": {"mass_kg": float("nan")},
                "ol": {"mass_kg": 0.2},
            }
        }

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    with pytest.raises(LiquidFractionInvalidError, match="phase_mass_invalid"):
        backend.equilibrate(
            1350.0,
            composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
            fO2_log=-8.0,
            pressure_bar=2000.0,
        )


def test_magemin_fake_bridge_library_error_returns_warning(monkeypatch):
    # A library-boundary exception must be caught and surfaced as a
    # warning on an otherwise-empty result -- never raised.
    def minimize(**kwargs):
        raise RuntimeError("synthetic MAGEMin failure")

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({'python_bridge': 'pymagemin'})
    backend._binary_path = None

    result = backend.equilibrate(
        1500.0,
        composition_mol={"SiO2": 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.phases_present == []
    assert result.ledger_transition is None
    assert any("synthetic MAGEMin failure" in w for w in result.warnings)
    assert result.status == "not_converged"
    assert result.diagnostics['backend_failure_exception'] == 'RuntimeError'
    assert result.diagnostics['backend_failure_message'] == 'synthetic MAGEMin failure'


def test_magemin_equilibrate_propagates_local_call_helper_errors(monkeypatch):
    fake_module = types.SimpleNamespace(minimize=lambda **_kwargs: {})
    _make_available_magemin(monkeypatch, fake_module)
    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    def broken_local_helper(**_kwargs):
        raise KeyError('local MAGEMin wrapper defect')

    monkeypatch.setattr(backend, '_call_magemin', broken_local_helper)
    with pytest.raises(KeyError, match='local MAGEMin wrapper defect'):
        backend.equilibrate(
            1500.0,
            composition_mol={'SiO2': 1.0},
            fO2_log=-8.0,
            pressure_bar=1.0,
        )


@pytest.mark.parametrize(
    'remote_type',
    (KeyError, TypeError, AttributeError, ValueError, IndexError),
)
def test_magemin_remote_handler_error_propagates_with_remote_traceback(
    remote_type,
):
    backend = _make_pooled_subprocess_backend(
        EngineWorkerRemoteError(
            remote_type.__name__,
            'local MAGEMin parser defect',
            f'Traceback: _parse_subprocess_stdout -> {remote_type.__name__}',
        )
    )

    with pytest.raises(remote_type) as raised:
        backend.equilibrate(
            1500.0,
            composition_mol={'SiO2': 1.0},
            fO2_log=-8.0,
            pressure_bar=1.0,
        )

    assert 'local MAGEMin parser defect' in str(raised.value)
    assert f'_parse_subprocess_stdout -> {remote_type.__name__}' in str(
        raised.value
    )


def test_magemin_remote_builtin_with_multi_argument_constructor_is_preserved():
    backend = _make_pooled_subprocess_backend(
        EngineWorkerRemoteError(
            'UnicodeDecodeError',
            'remote decode failure',
            'Traceback: _parse_subprocess_stdout -> UnicodeDecodeError',
        )
    )

    with pytest.raises(EngineWorkerRemoteError) as raised:
        backend.equilibrate(
            1500.0,
            composition_mol={'SiO2': 1.0},
            fO2_log=-8.0,
            pressure_bar=1.0,
        )

    assert raised.value.exc_name == 'UnicodeDecodeError'
    assert 'remote decode failure' in raised.value.detail
    assert '_parse_subprocess_stdout -> UnicodeDecodeError' in (
        raised.value.remote_traceback
    )


@pytest.mark.parametrize(
    ('override', 'exception_type', 'message'),
    (
        pytest.param(
            {'fO2_log': None},
            'TypeError', 'declared scalar must be numeric', id='none-fo2',
        ),
        pytest.param(
            {'pressure_bar': 'oops'},
            'TypeError', 'declared scalar must be numeric', id='bad-pressure',
        ),
        pytest.param(
            {'temperature_C': 'hot'},
            'TypeError', 'declared scalar must be numeric', id='bad-temperature',
        ),
        pytest.param(
            {'temperature_C': float('nan')},
            'ValueError', 'temperature_C must be finite',
            id='non-finite-temperature',
        ),
        pytest.param(
            {'pressure_bar': float('inf')},
            'ValueError', 'pressure_bar must be finite',
            id='non-finite-pressure',
        ),
        pytest.param(
            {'fO2_log': float('nan')},
            'ValueError', 'fO2_log must be finite',
            id='non-finite-fo2',
        ),
        pytest.param(
            {'call_timeout_s': 'oops'},
            'TypeError',
            'declared scalar must be numeric',
            id='bad-timeout-str',
        ),
        pytest.param(
            {'call_timeout_s': object()},
            'TypeError',
            'declared scalar must be numeric',
            id='bad-timeout-type',
        ),
        pytest.param(
            {'call_timeout_s': float('inf')},
            'ValueError', 'call_timeout_s must be finite',
            id='non-finite-timeout',
        ),
        pytest.param(
            {'temperature_C': True},
            'TypeError', 'declared scalar must be numeric',
            id='boolean-temperature',
        ),
        pytest.param(
            {'fO2_log': np.bool_(False)},
            'TypeError', 'declared scalar must be numeric',
            id='numpy-boolean-fo2',
        ),
    ),
)
def test_magemin_equilibrate_invalid_declared_scalar_is_typed_refusal(
    override, exception_type, message,
):
    calls = []

    def fake_minimize(**kwargs):
        calls.append(kwargs)
        return {'phases': {'liq': {'mass_kg': 1.0}}}

    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'pymagemin'
    backend._magemin_module = types.SimpleNamespace(minimize=fake_minimize)
    params = {
        'temperature_C': 1500.0,
        'composition_mol': {'SiO2': 1.0},
        'pressure_bar': 1.0,
        'fO2_log': -8.0,
    }
    params.update(override)

    result = backend.equilibrate(**params)

    assert result.status == 'refused'
    assert result.diagnostics['reason'] == 'invalid_input'
    assert result.diagnostics['backend_failure_exception'] == exception_type
    assert message in result.diagnostics['backend_failure_message']
    assert any(message in warning for warning in result.warnings)
    if (
        isinstance(override.get('temperature_C'), bool)
        or isinstance(override.get('fO2_log'), np.bool_)
    ):
        assert calls == []


@pytest.mark.parametrize(
    'stdout',
    (
        pytest.param('Phase: liq ol\nMode: 0.8 nan\n', id='nan-token'),
        pytest.param('Phase: liq ol\nMode: 0.8 bad\n', id='non-numeric'),
        pytest.param('Phase: liq ol\nMode: 0.8 0.8\n', id='overfull'),
    ),
)
def test_magemin_equilibrate_invalid_engine_output_is_typed_not_converged(
    monkeypatch, stdout,
):
    monkeypatch.setattr(
        'simulator.melt_backend.magemin.subprocess.run',
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, stdout=stdout, stderr='',
        ),
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        pressure_bar=1.0,
        fO2_log=-8.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'MAGEMinOutputError'
    )
    assert 'MAGEMin Mode' in result.diagnostics['backend_failure_message']


@pytest.mark.parametrize('local_error', (KeyError, TypeError, ValueError))
def test_magemin_equilibrate_parser_local_defect_propagates(
    monkeypatch, local_error,
):
    monkeypatch.setattr(
        'simulator.melt_backend.magemin.subprocess.run',
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, stdout='Phase: liq\nMode: 1.0\n', stderr='',
        ),
    )
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')

    def broken_parser(_stdout):
        raise local_error('local MAGEMin parser defect')

    monkeypatch.setattr(backend, '_parse_subprocess_stdout', broken_parser)
    with pytest.raises(local_error, match='local MAGEMin parser defect'):
        backend.equilibrate(
            1500.0,
            composition_mol={'SiO2': 1.0},
            pressure_bar=1.0,
            fO2_log=-8.0,
        )


def test_magemin_subprocess_parser_value_error_propagates(monkeypatch):
    from simulator.melt_backend import magemin as magemin_module

    backend = MAGEMinBackend()
    backend._binary_path = Path('/fake/MAGEMin')
    projection = backend._build_db_bulk_projection({'SiO2': 100.0})
    monkeypatch.setattr(
        magemin_module.subprocess,
        'run',
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 0, stdout='MAGEMin output', stderr=''
        ),
    )

    def broken_parser(_stdout):
        raise ValueError('local MAGEMin parser defect')

    monkeypatch.setattr(backend, '_parse_subprocess_stdout', broken_parser)
    with pytest.raises(ValueError, match='local MAGEMin parser defect'):
        backend._call_magemin_subprocess(
            bulk_projection=projection,
            temperature_C=1500.0,
            pressure_kbar=0.001,
            fO2_log=-8.0,
        )


def test_magemin_remote_engine_call_error_returns_typed_not_converged():
    from simulator.melt_backend.magemin import _MAGEMinEngineCallError

    backend = _make_pooled_subprocess_backend(
        EngineWorkerRemoteError(
            _MAGEMinEngineCallError.__name__,
            'CalledProcessError: MAGEMin binary exited 9: engine failure',
            'Traceback: subprocess.run -> CalledProcessError',
        )
    )

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'CalledProcessError'
    )
    assert result.diagnostics['backend_failure_message'] == (
        'MAGEMin binary exited 9: engine failure'
    )


def test_magemin_remote_engine_lifecycle_error_returns_typed_not_converged():
    from simulator.melt_backend.magemin import _MAGEMinEngineLifecycleError

    backend = _make_pooled_subprocess_backend(
        EngineWorkerRemoteError(
            _MAGEMinEngineLifecycleError.__name__,
            'MAGEMinSubprocessSlotTimeout: subprocess slot timed out',
            'Traceback: _magemin_subprocess_slot -> timeout',
        )
    )

    result = backend.equilibrate(
        1500.0,
        composition_mol={'SiO2': 1.0},
        fO2_log=-8.0,
        pressure_bar=1.0,
    )

    assert result.status == 'not_converged'
    assert result.diagnostics['backend_failure_exception'] == (
        'MAGEMinSubprocessSlotTimeout'
    )
    assert result.diagnostics['backend_failure_message'] == (
        'subprocess slot timed out'
    )


@pytest.mark.parametrize('bridge', ('pymagemin', 'julia'))
@pytest.mark.parametrize('local_error', (KeyError, TypeError, ValueError))
def test_magemin_bridge_argument_preparation_error_propagates(
    bridge, local_error,
):
    class BrokenProjection:
        @property
        def composition_wt_pct(self):
            raise local_error('local MAGEMin argument validation defect')

    if bridge == 'pymagemin':
        module = types.SimpleNamespace(minimize=lambda **_kwargs: None)
    else:
        module = types.SimpleNamespace(
            Main=types.SimpleNamespace(
                MAGEMin=types.SimpleNamespace(
                    single_point_minimization=lambda *_args: None
                )
            )
        )

    backend = MAGEMinBackend()
    backend._bridge = bridge
    backend._magemin_module = module

    with pytest.raises(
        local_error, match='local MAGEMin argument validation defect'
    ):
        backend._call_magemin(
            BrokenProjection(),
            temperature_C=1500.0,
            pressure_bar=1.0,
            fO2_log=-8.0,
        )


def test_magemin_bridge_import_propagates_non_import_errors(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def broken_import(name, *args, **kwargs):
        if name == 'pymagemin':
            raise KeyError('local import hook defect')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', broken_import)
    with pytest.raises(KeyError, match='local import hook defect'):
        MAGEMinBackend()._import_magemin_bridge(requested='pymagemin')


def test_magemin_julia_bridge_init_propagates_untyped_errors(monkeypatch):
    import sys

    fake_julia = types.ModuleType('julia')
    fake_julia.__path__ = []
    fake_julia.Main = types.SimpleNamespace(
        eval=lambda _source: (_ for _ in ()).throw(KeyError('local Julia hook defect'))
    )
    fake_core = types.ModuleType('julia.core')

    class JuliaError(Exception):
        pass

    class UnsupportedPythonError(Exception):
        pass

    fake_core.JuliaNotFound = RuntimeError
    fake_core.JuliaError = JuliaError
    fake_core.UnsupportedPythonError = UnsupportedPythonError
    monkeypatch.setitem(sys.modules, 'julia', fake_julia)
    monkeypatch.setitem(sys.modules, 'julia.core', fake_core)
    with pytest.raises(KeyError, match='local Julia hook defect'):
        MAGEMinBackend()._import_magemin_bridge(requested='julia')


@pytest.mark.parametrize(
    'failure',
    [
        pytest.param('julia', id='julia-error'),
        pytest.param('unsupported-python', id='unsupported-python'),
        pytest.param('julia-not-found', id='julia-not-found'),
        pytest.param('unsupported-version', id='unsupported-julia-version'),
        pytest.param('os', id='os-error'),
        pytest.param('process', id='process-error'),
        pytest.param('local-runtime', id='local-runtime-error-propagates'),
        pytest.param('package-import', id='package-import-error'),
        pytest.param('package-missing', id='package-module-not-found'),
        pytest.param('core-import', id='core-import-error'),
        pytest.param('core-missing', id='core-module-not-found'),
    ],
)
def test_magemin_julia_runtime_discovery_failures_return_unavailable(
    monkeypatch, failure,
):
    import builtins
    import sys

    fake_julia = types.ModuleType('julia')
    fake_julia.__path__ = []
    fake_core = types.ModuleType('julia.core')

    class JuliaError(Exception):
        pass

    class UnsupportedPythonError(Exception):
        pass

    class JuliaNotFound(RuntimeError):
        def __init__(self, executable, kwargname):
            self.executable = executable
            self.kwargname = kwargname

        def __str__(self):
            return f'Julia executable {self.executable} cannot be found'

    fake_core.JuliaError = JuliaError
    fake_core.JuliaNotFound = JuliaNotFound
    fake_core.UnsupportedPythonError = UnsupportedPythonError
    monkeypatch.setitem(sys.modules, 'julia', fake_julia)
    monkeypatch.setitem(sys.modules, 'julia.core', fake_core)
    real_import = builtins.__import__

    def fail_main_import(name, globals=None, locals=None, fromlist=(), level=0):
        package_hit = name == 'julia' and not fromlist
        core_hit = name == 'julia.core'
        main_hit = name == 'julia' and 'Main' in (fromlist or ())
        if failure == 'package-import' and package_hit:
            raise ImportError('same native initialization failure')
        if failure == 'package-missing' and package_hit:
            raise ModuleNotFoundError('same native initialization failure')
        if failure == 'core-import' and core_hit:
            raise ImportError('same native initialization failure')
        if failure == 'core-missing' and core_hit:
            raise ModuleNotFoundError('same native initialization failure')
        if main_hit:
            if failure == 'julia':
                raise JuliaError('PyCall initialization failed')
            if failure == 'unsupported-python':
                raise UnsupportedPythonError('Python runtime unsupported')
            if failure == 'julia-not-found':
                raise JuliaNotFound('julia', 'runtime')
            if failure == 'unsupported-version':
                raise RuntimeError(
                    'PyJulia does not support Julia < 0.7 anymore'
                )
            if failure == 'os':
                raise FileNotFoundError('Julia executable missing')
            if failure == 'local-runtime':
                raise RuntimeError('local Julia import hook defect')
            if failure == 'process':
                raise subprocess.CalledProcessError(1, ['julia'])
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, '__import__', fail_main_import)
    backend = MAGEMinBackend()
    if failure == 'local-runtime':
        with pytest.raises(RuntimeError, match='local Julia import hook defect'):
            backend._import_magemin_bridge(requested='julia')
        return

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        bridge = backend._import_magemin_bridge(requested='julia')

    expected_class = {
        'julia': 'JuliaError',
        'unsupported-python': 'UnsupportedPythonError',
        'julia-not-found': 'JuliaNotFound',
        'unsupported-version': 'RuntimeError',
        'os': 'FileNotFoundError',
        'process': 'CalledProcessError',
        'package-import': 'ImportError',
        'package-missing': 'ModuleNotFoundError',
        'core-import': 'ImportError',
        'core-missing': 'ModuleNotFoundError',
    }[failure]
    assert bridge == (None, None)
    assert (
        f'julia bridge import failed: {expected_class}:' in backend._last_error
    )
    assert any(
        message in backend._last_error
        for message in (
            'PyCall initialization failed',
            'Python runtime unsupported',
            'Julia executable julia cannot be found',
            'PyJulia does not support Julia < 0.7 anymore',
            'Julia executable missing',
            'returned non-zero exit status',
            'same native initialization failure',
        )
    )


def test_magemin_only_consumes_cleaned_melt_account(monkeypatch):
    # When called with the layered ABC's composition_mol_by_account, the
    # adapter must consume only process.cleaned_melt and warn about every
    # other account it dropped (binding spec §7 -- no metal/salt/sulfide).
    captured = {}

    def minimize(**kwargs):
        captured.update(kwargs)
        return {"phases": {"liq": {"mass_kg": 1.0}}}

    fake_module = types.SimpleNamespace(minimize=minimize)
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1400.0,
        composition_mol_by_account={
            "process.cleaned_melt": {"SiO2": 5.0, "MgO": 3.0, "NaCl": 0.25},
            "process.metal_alloy": {"Fe": 2.0},
            "process.sulfide_matte": {"FeS": 1.0},
        },
        fO2_log=-8.0,
        pressure_bar=1000.0,
    )

    assert captured == {}
    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    dropped_warnings = " ".join(result.warnings)
    assert "process.metal_alloy" in dropped_warnings
    assert "process.sulfide_matte" in dropped_warnings
    assert "dropped_non_basis_melt_mass" in dropped_warnings
    dropped = result.diagnostics["dropped_non_basis_melt_mass_kg_by_species"]
    assert "NaCl" in dropped
    assert dropped["NaCl"] > 0.0
    projection = result.diagnostics["input_composition_projection"]
    assert projection["status"] == "projected"
    assert projection["dropped_species"] == ["NaCl"]
    assert projection["dropped_accounts"] == [
        "process.metal_alloy",
        "process.sulfide_matte",
    ]
    assert projection["dropped_account_species"] == {
        "process.metal_alloy": ["Fe"],
        "process.sulfide_matte": ["FeS"],
    }
    assert projection["renormalization_delta"] > 0.0


# ----------------------------------------------------------------------
# Live smoke test: runs the real MAGEMin binary if one is built locally.
# Skipif-guarded so CI without a built MAGEMin still passes.
# ----------------------------------------------------------------------

# Resolve a real binary at collection time so the guard is a true
# pytest.mark.skipif rather than a runtime branch. MAGEMin v1.9.3 is built
# locally as a sibling clone (../MAGEMin/MAGEMin); _locate_binary also
# checks engines/magemin/{,bin/}MAGEMin and PATH.
_LIVE_MAGEMIN_BINARY = MAGEMinBackend._locate_binary(None)


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_smoke_runs_real_binary(request, monkeypatch):
    # End-to-end against the real MAGEMin binary: a basalt analog at
    # crustal P/T must equilibrate, report a silicate liquid, and -- the
    # invariant that matters -- leave ledger_transition None. MAGEMin is
    # shadow/diagnostic: it never gets AtomLedger authority.
    backend = MAGEMinBackend()
    request.addfinalizer(backend.close)
    monkeypatch.delenv("OMPI_MCA_orte_tmpdir_base", raising=False)
    monkeypatch.delenv("PRTE_MCA_prte_tmpdir_base", raising=False)
    system_tmpdir = Path(tempfile.gettempdir())
    existing_ompi_dirs = set(system_tmpdir.glob("ompi.*"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    # The subprocess bridge is the supported default; the live binary
    # must resolve to it (ctypes is opt-in only, pymagemin/julia rare).
    assert backend._bridge == "subprocess"
    assert backend._ompi_tmpdir is not None
    pool_ompi_tmpdir = Path(backend._ompi_tmpdir)
    assert pool_ompi_tmpdir.is_dir()

    basalt_wt_pct = {
        "SiO2": 49.0,
        "TiO2": 1.5,
        "Al2O3": 14.0,
        "FeO": 10.0,
        "Fe2O3": 1.0,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 2.5,
        "K2O": 0.8,
        "Cr2O3": 0.2,
    }

    # 2000 bar == 0.2 GPa == 2 kbar; well inside the igneous database's
    # crustal calibration. 1200 C is super-liquidus for this analog.
    result = backend.equilibrate(
        1200.0,
        composition_kg=basalt_wt_pct,
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )

    # No library-boundary error. Oxides with no ig endmember (P2O5/MnO/NiO/CoO)
    # are a composition_projected refusal, tested separately; this live smoke
    # stays inside the documented ig bulk order.
    assert result.status == "ok", result.warnings
    assert not any("failed" in w for w in result.warnings), result.warnings
    # MAGEMin reports a phase assemblage including a silicate liquid.
    assert result.phases_present
    assert any(
        name.lower().startswith("liq") for name in result.phases_present
    ), result.phases_present
    assert result.phase_masses_kg
    # At 1200 C this analog is liquid-dominated.
    assert 0.0 < result.liquid_fraction <= 1.0
    # Shadow posture: MAGEMin holds no AtomLedger authority, ever.
    assert result.ledger_transition is None
    assert backend.ledger_account_policies() == ()
    assert set(system_tmpdir.glob("ompi.*")) == existing_ompi_dirs
    backend.close()
    assert not pool_ompi_tmpdir.exists()


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_subliquidus_run_reports_crystalline_phases(request):
    # A second live point below the liquidus: the binary must report
    # crystalline phases alongside (or instead of) the melt, and the
    # liquid fraction must drop relative to the super-liquidus case.
    backend = MAGEMinBackend()
    request.addfinalizer(backend.close)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    peridotite_wt_pct = {
        "SiO2": 45.0,
        "TiO2": 0.2,
        "Al2O3": 4.0,
        "FeO": 8.0,
        "MgO": 38.0,
        "CaO": 3.5,
        "Na2O": 0.3,
        "Cr2O3": 0.4,
    }

    # 1000 C at 10 kbar (1 GPa, 10000 bar) is sub-solidus to
    # low-melt-fraction for a peridotite -- expect crystalline phases.
    result = backend.equilibrate(
        1000.0,
        composition_kg=peridotite_wt_pct,
        fO2_log=-9.0,
        pressure_bar=10000.0,
    )

    assert not any("failed" in w for w in result.warnings), result.warnings
    assert result.phases_present
    crystalline = [
        name
        for name in result.phases_present
        if not name.lower().startswith("liq")
    ]
    assert crystalline, result.phases_present
    assert result.ledger_transition is None


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_liquidus_finder_lunar_mare_low_ti_sane(request):
    """Apollo low-Ti mare basalt sample 12009 begins crystallizing near 1230 C.

    Reference: Walker et al. 1971, Experimental petrology of Apollo 12 basalts,
    part 1, sample 12009. This feedstock is only an Apollo 12/15 low-Ti soil
    analog, so the test checks a sane bracket rather than a forced retune.
    """
    backend = MAGEMinBackend()
    request.addfinalizer(backend.close)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    lunar_mare_low_ti_wt_pct = {
        "SiO2": 44.5,
        "TiO2": 1.5,
        "Al2O3": 13.5,
        "FeO": 16.5,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 0.4,
        "K2O": 0.10,
        "Cr2O3": 0.35,
    }

    result = backend.find_liquidus_solidus(
        composition_kg=lunar_mare_low_ti_wt_pct,
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )

    assert result.status == "ok", result.warnings
    assert 900.0 <= result.solidus_T_C <= 1100.0
    assert 1200.0 <= result.liquidus_T_C <= 1450.0
    assert result.liquidus_T_C >= result.solidus_T_C

    phase_result = backend.equilibrate(
        1300.0,
        composition_kg=lunar_mare_low_ti_wt_pct,
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert phase_result.status == "ok", phase_result.warnings
    assert phase_result.phases_present


# Pure-endmember melting references retained for any future calibrated engine
# (not MAGEMin `ig` acceptance targets). Forsterite 2163 K (Akimoto et al.
# 1981); diopside 1391.5 C and anorthite 1553 C (standard Di-An calibration).
# Former collected xfail nodes always xfailed before any engine work (removed
# 2026-07-29 / re-landed 2026-08-03); suite no longer pays three placeholder
# xfail nodes.


def _run_magemin_gam_o(binary: Path, *, buffer_n: float) -> float:
    """Run the live binary at one ``buffer_n`` and return GAM[O] (mu of the
    oxygen system component, kJ).

    Builds the ``ig`` bulk vector directly with a **nonzero O component** so
    the fO2 buffer actually engages. P3-F showed MAGEMin's qfm buffer is inert
    when ``O=0`` (see
    ``docs-private/research/2026-06-05-p3f/findings.md`` Finding 2). This test
    therefore bypasses the adapter to probe the binary's real redox response.

    GAM is reported in IG component order
    ``SiO2 Al2O3 CaO MgO FeOt K2O Na2O TiO2 O Cr2O3 H2O`` -> O is index 8.
    """
    # Basalt analog (matches the live-smoke test) with O set nonzero.
    bulk = "49,14,11,9,10.899810,0.8,2.5,1.5,1.0,0.2,0"
    completed = subprocess.run(  # noqa: S603 - args are test-built constants
        [
            str(binary),
            "--Verb=2",
            "--db=ig",
            "--Temp=1200.0",
            "--Pres=2.0",
            "--sys_in=wt",
            f"--Bulk={bulk}",
            "--buffer=qfm",
            f"--buffer_n={buffer_n:.6f}",
        ],
        cwd=str(binary.parent),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    match = re.search(r"GAM = \[([^\]]+)\]", completed.stdout)
    assert match, f"no GAM vector in MAGEMin stdout:\n{completed.stdout}"
    gam = [float(x) for x in match.group(1).split(",")]
    # Full IG order has 11 components; the O-component mu is index 8.
    assert len(gam) == 11, gam
    return gam[8]


def _run_magemin_adapter_buffer_probe(
    binary: Path,
    *,
    fO2_log: float,
) -> tuple[float, tuple[str, ...], tuple[float, ...]]:
    """Run the live binary through the production adapter's ig vector path."""
    backend = MAGEMinBackend()
    basalt_wt_pct = {
        "SiO2": 49.0,
        "TiO2": 1.5,
        "Al2O3": 14.0,
        "FeO": 10.0,
        "Fe2O3": 1.0,
        "MgO": 9.0,
        "CaO": 11.0,
        "Na2O": 2.5,
        "K2O": 0.8,
        "Cr2O3": 0.2,
    }
    temperature_C = 1200.0
    pressure_bar = 2000.0
    pressure_kbar = backend._GPa_to_kbar(
        backend._pressure_bar_to_GPa(pressure_bar)
    )
    bulk = backend._build_ig_bulk_vector(basalt_wt_pct)
    buffer_name, buffer_n, _warnings = backend._resolve_buffer(
        temperature_C=temperature_C,
        fO2_log=fO2_log,
    )
    completed = subprocess.run(  # noqa: S603 - args are test-built constants
        [
            str(binary),
            "--Verb=2",
            f"--db={backend._database}",
            f"--Temp={temperature_C:.6f}",
            f"--Pres={pressure_kbar:.6f}",
            "--sys_in=wt",
            "--Bulk=" + ",".join(f"{value:.6f}" for value in bulk),
            f"--buffer={buffer_name}",
            f"--buffer_n={buffer_n:.6f}",
        ],
        cwd=str(binary.parent),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    gam_match = re.search(r"GAM = \[([^\]]+)\]", completed.stdout)
    assert gam_match, f"no GAM vector in MAGEMin stdout:\n{completed.stdout}"
    gam = [float(x) for x in gam_match.group(1).split(",")]
    assert len(gam) == 11, gam

    phase_match = re.search(r"^\s*Phase\s*:\s*(.+)$", completed.stdout, re.M)
    mode_match = re.search(r"^\s*Mode\s*:\s*(.+)$", completed.stdout, re.M)
    assert phase_match and mode_match, (
        f"no Phase/Mode block in MAGEMin stdout:\n{completed.stdout}"
    )
    phases = tuple(phase_match.group(1).split())
    modes = tuple(float(x) for x in mode_match.group(1).split())
    assert len(phases) == len(modes)
    return gam[8], phases, modes


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_buffer_n_sign_and_magnitude_round_trip():
    """P3-F: the live binary must honour ``--buffer_n`` with the correct sign
    AND magnitude, validating ``_resolve_buffer``'s
    ``buffer_n = fO2_log - QFM(T)`` translation against the real MAGEMin.

    The single-point ``ig`` CLI prints no explicit fO2/Fe3+; redox is carried
    by the oxygen component's chemical potential, reported as GAM[O]. We use
    GAM[O] as the non-speculative redox proxy (recon:
    ``docs-private/research/2026-06-05-p3f/findings.md`` Finding 1).

    Two invariants, both anchored to MAGEMin's documented buffer formula
    ``mu_offset(O2) = T_K * 0.019145 * buffer_n`` (0.019145 = R*ln10/1000):
      - SIGN: higher buffer_n => higher (less negative) GAM[O] => more
        oxidizing. So requesting fO2 above QFM (positive buffer_n) is more
        oxidizing, confirming the translation sign.
      - MAGNITUDE: d(GAM[O])/d(buffer_n) == T_K * 0.019145 / 2 (per single O;
        the formula is per O2 = 2 O), so a delta of 4 buffer_n units shifts
        GAM[O] by T_K * 0.019145 * 4 / 2 kJ.
    """
    binary = _LIVE_MAGEMIN_BINARY
    mu_o_reduced = _run_magemin_gam_o(binary, buffer_n=-2.0)
    mu_o_oxidized = _run_magemin_gam_o(binary, buffer_n=2.0)

    # SIGN: oxidizing (higher buffer_n) gives a less-negative oxygen mu.
    assert mu_o_oxidized > mu_o_reduced, (
        f"buffer_n=+2 mu_O={mu_o_oxidized} must exceed "
        f"buffer_n=-2 mu_O={mu_o_reduced} (higher buffer_n = more oxidizing)"
    )

    # MAGNITUDE: anchored to MAGEMin's own buffer formula, not a fitted
    # constant. T = 1200 C = 1473.15 K; delta buffer_n = 4.
    T_K = 1200.0 + 273.15
    expected_delta = T_K * 0.019145 * 4.0 / 2.0
    observed_delta = mu_o_oxidized - mu_o_reduced
    assert observed_delta == pytest.approx(expected_delta, abs=0.5), (
        f"GAM[O] shift {observed_delta:.4f} kJ over buffer_n delta=4 must "
        f"match MAGEMin's buffer formula prediction {expected_delta:.4f} kJ"
    )


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_adapter_path_fO2_changes_shadow_response():
    """Requested fO2 must move the MAGEMin shadow response.

    This intentionally drives the production adapter path
    (``_build_ig_bulk_vector`` + ``_resolve_buffer``). P3-F real-binary probes
    showed MAGEMin's qfm buffer changes GAM[O] only when ig O is nonzero; the
    adapter provisions O from explicit Fe2O3 or from the total-iron-as-FeO
    inventory when no ferric split is reported.
    """
    binary = _LIVE_MAGEMIN_BINARY
    reduced = _run_magemin_adapter_buffer_probe(binary, fO2_log=-12.0)
    oxidized = _run_magemin_adapter_buffer_probe(binary, fO2_log=-4.0)

    gam_o_changed = abs(oxidized[0] - reduced[0]) > 1.0e-6
    assemblage_changed = oxidized[1] != reduced[1]
    modes_changed = oxidized[2] != pytest.approx(reduced[2], abs=1.0e-9)
    # Require the robust, production-parsed signals (phase assemblage / modes) to
    # move, not GAM[O] alone: the subprocess bridge reliably exposes Phase/Mode,
    # whereas GAM[O] is a secondary readout. GAM is kept as a sanity signal.
    assert assemblage_changed or modes_changed, (
        "MAGEMin adapter path must move phase assemblage or modes across widely "
        f"separated fO2_log values; reduced={reduced} oxidized={oxidized} "
        f"(gam_o_changed={gam_o_changed})"
    )


def test_magemin_empty_melt_composition_marks_status_out_of_domain(monkeypatch):
    # A composition with no species in MAGEMin's 14-oxide basis (only
    # native Fe / sulfide / halide) collapses to an empty wt% projection.
    # The adapter labels this 'out_of_domain' -- the engine has nothing
    # valid to act on, not a runtime convergence failure.
    fake_module = types.SimpleNamespace(minimize=lambda **_: {"phases": {}})
    _make_available_magemin(monkeypatch, fake_module)

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({})

    result = backend.equilibrate(
        1600.0,
        composition_mol={"Fe": 1.0, "FeS": 0.5, "NaCl": 0.2},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "out_of_domain"
    assert result.diagnostics["backend_status_reason"] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    assert any("refused projected/dropped non-basis" in w for w in result.warnings)


def test_magemin_subprocess_runs_in_fresh_temp_cwd(monkeypatch, tmp_path):
    """MAGEMin appends _pseudosection_output.txt to its CWD; isolate per call."""
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq\nMode  : 1.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        captured["cwd"] = kwargs.get("cwd")
        return FakeCompleted()

    fake_binary = tmp_path / "MAGEMin"
    fake_binary.write_text("", encoding="utf-8")

    _disable_configured_magemin_path(monkeypatch)
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: fake_binary),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"warm_worker": False}) is True

    result = backend.equilibrate(
        1200.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-8.0,
        pressure_bar=1e-6,
    )

    assert result.status == "ok"
    assert "cwd" in captured, "subprocess.run was not invoked"
    cwd = Path(captured["cwd"])
    assert cwd != fake_binary.parent.resolve()
    # Per-call TemporaryDirectory; path is under the system temp root.
    assert str(cwd).startswith(tempfile.gettempdir())
    assert cwd.name.startswith("tmp")
    binary_arg = Path(captured["args"][0])
    assert binary_arg.is_absolute()
    assert binary_arg == fake_binary.resolve()


def test_magemin_subprocess_launches_are_serialized_across_callers(
    monkeypatch, tmp_path
):
    """With REGOLITH_MAGEMIN_SLOTS=1 launches stay strictly serialized.

    2026-07-22 t-385: the machine-wide launch gate became a bounded K-slot
    semaphore (each call runs in a private tmpdir; the exclusive lock was a
    CPU bound, not correctness). K=1 must reproduce the old serialization
    exactly; the bounded-K contract is pinned by the companion test below.
    """
    import simulator.melt_backend.magemin as magemin_module

    monkeypatch.setenv('REGOLITH_MAGEMIN_SLOTS', '1')
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK',
        tmp_path / 'isolated-magemin.lock',
    )
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK_DIR', tmp_path,
    )

    first_entered = threading.Event()
    release_first = threading.Event()
    state_lock = threading.Lock()
    state = {'active': 0, 'calls': 0, 'max_active': 0}

    class FakeCompleted:
        returncode = 0
        stderr = ''
        stdout = 'Phase : liq\nMode  : 1.000\n'

    def fake_subprocess_run(_args, **_kwargs):
        with state_lock:
            state['active'] += 1
            state['calls'] += 1
            state['max_active'] = max(state['max_active'], state['active'])
            call_number = state['calls']
        try:
            if call_number == 1:
                first_entered.set()
                assert release_first.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
            return FakeCompleted()
        finally:
            with state_lock:
                state['active'] -= 1

    fake_binary = tmp_path / 'MAGEMin'
    fake_binary.write_text('', encoding='utf-8')
    monkeypatch.setattr(magemin_module.subprocess, 'run', fake_subprocess_run)

    backend = MAGEMinBackend()
    backend._binary_path = fake_binary
    backend._database = 'ig'
    backend._config = {'timeout_s': 1.0}
    projection = backend._build_db_bulk_projection(
        {'SiO2': 60.0, 'MgO': 40.0}, database='ig'
    )
    errors = []

    def solve():
        try:
            backend._call_magemin_subprocess(
                bulk_projection=projection,
                temperature_C=1200.0,
                pressure_kbar=0.001,
                fO2_log=-8.0,
            )
        except BaseException as exc:  # surfaced below from the test threads
            errors.append(exc)

    first = threading.Thread(target=solve)
    second = threading.Thread(target=solve)
    first.start()
    assert first_entered.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
    second.start()
    time.sleep(0.05)
    assert state['calls'] == 1
    release_first.set()
    first.join(timeout=_RENDEZVOUS_TIMEOUT_S)
    second.join(timeout=_RENDEZVOUS_TIMEOUT_S)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert state == {'active': 0, 'calls': 2, 'max_active': 1}


def test_magemin_subprocess_slots_bound_concurrency(monkeypatch, tmp_path):
    """K slots admit K concurrent launches; the K+1th blocks until release.

    Uses an ISOLATED lock namespace (tmp_path): the production slot files
    are contended by real engine calls when the suite runs the split
    chains, which broke the barrier under load (gate-1 flake).
    """
    import simulator.melt_backend.magemin as magemin_module

    monkeypatch.setenv('REGOLITH_MAGEMIN_SLOTS', '3')
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK',
        tmp_path / 'isolated-magemin.lock',
    )
    monkeypatch.setattr(
        magemin_module, '_MAGEMIN_SUBPROCESS_LOCK_DIR', tmp_path,
    )
    held = []
    waited = []
    # 4 parties: the 3 slot holders + the main thread (a 3-party barrier
    # left main waiting on an unfillable second cycle — gate-1 self-bug).
    barrier = threading.Barrier(4, timeout=20.0)

    def hold(index):
        with magemin_module._magemin_subprocess_slot(5.0):
            held.append(index)
            barrier.wait()
            time.sleep(0.6)

    def fourth():
        started = time.monotonic()
        with magemin_module._magemin_subprocess_slot(5.0):
            waited.append(time.monotonic() - started)

    holders = [
        threading.Thread(target=hold, args=(index,)) for index in range(3)
    ]
    for thread in holders:
        thread.start()
    barrier.wait()
    late = threading.Thread(target=fourth)
    late.start()
    for thread in holders:
        thread.join(timeout=5.0)
    late.join(timeout=5.0)

    assert sorted(held) == [0, 1, 2]
    assert waited and waited[0] > 0.3


@pytest.mark.skipif(
    _LIVE_MAGEMIN_BINARY is None,
    reason="No compiled MAGEMin binary found (build per pyproject.toml [magemin])",
)
def test_magemin_live_subprocess_does_not_append_dump_in_engine_tree(request):
    """Equilibrium calls must not grow _pseudosection_output.txt in-tree."""
    binary = _LIVE_MAGEMIN_BINARY.resolve()
    dump_path = binary.parent / "_pseudosection_output.txt"
    size_before = dump_path.stat().st_size if dump_path.exists() else 0

    backend = MAGEMinBackend()
    request.addfinalizer(backend.close)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        available = backend.initialize({"binary_path": str(binary)})
    if not available:
        pytest.skip("MAGEMin binary present but backend failed to initialize")

    result = backend.equilibrate(
        1200.0,
        composition_kg={"SiO2": 49.0, "MgO": 9.0, "Al2O3": 14.0, "CaO": 11.0},
        fO2_log=-8.0,
        pressure_bar=2000.0,
    )
    assert result.status == "ok", result.warnings

    size_after = dump_path.stat().st_size if dump_path.exists() else 0
    assert size_after == size_before


def test_magemin_subprocess_fo2_log_substitution_recorded(monkeypatch):
    # MAGEMin's CLI only accepts a named buffer plus a numeric `buffer_n`
    # offset (see ``MAGEMin/examples/MAGEMin_C_single_point_with_buffer.jl``),
    # so the adapter must translate the caller's absolute log10(fO2) into
    # `--buffer=qfm --buffer_n=<delta>` using the O'Neill (1987) QFM
    # calibration. The previous "silently substitute qfm and ignore the
    # absolute value" path made a Mars reducing campaign at fO2_log=-12
    # land at QFM (~ -6 at 1450 C in the O'Neill fit) -- a multi-decade
    # error -- without any warning to the caller. This test pins the
    # honest translation: the binary receives the offset that reproduces
    # the requested absolute fO2, and the EquilibriumResult.warnings
    # surfaces the substitution so a downstream consumer cannot miss it.
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = (
            "Phase : liq qfm\n"
            "Mode  : 1.000 0.000\n"
        )

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        return FakeCompleted()

    # Force the subprocess bridge directly: stub the binary discovery so
    # initialize() picks up the subprocess path without needing a real
    # MAGEMin install.
    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        assert backend.initialize({"warm_worker": False}) is True
    assert backend._bridge == "subprocess"

    # Mars-reducing analog: T = 1450 C, fO2_log = -12 (well below QFM).
    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0, "FeO": 1.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    # The substitution result is OK (the subprocess ran), but the
    # warnings record the absolute -> buffer-offset translation in
    # detail so a downstream consumer cannot miss it.
    assert "args" in captured, "subprocess.run was not invoked"
    args = captured["args"]
    buffer_args = [a for a in args if a.startswith("--buffer")]
    # Both --buffer and --buffer_n must be passed so the absolute fO2
    # is honoured rather than silently snapped to QFM.
    assert any(a == "--buffer=qfm" for a in buffer_args), buffer_args
    buffer_n_args = [a for a in buffer_args if a.startswith("--buffer_n=")]
    assert len(buffer_n_args) == 1, buffer_args
    buffer_n = float(buffer_n_args[0].split("=", 1)[1])
    # Independent O'Neill QFM value at 1723.15 K and its expected offset.
    qfm_at_1450C = -5.957329890026985
    expected_offset = -6.042670109973015
    assert buffer_n == pytest.approx(expected_offset, abs=0.05), (
        f"buffer_n={buffer_n} should approximate {expected_offset}"
    )
    # Once the offset round-trips through QFM(T) we recover the
    # requested absolute fO2_log within calibration accuracy.
    recovered_fo2_log = buffer_n + qfm_at_1450C
    assert recovered_fo2_log == pytest.approx(-12.0, abs=0.05)
    # The warning chain must explicitly name the substitution so the
    # caller knows their absolute fO2 was translated, not ignored.
    substitution_warnings = [
        w for w in result.warnings if "fO2_log" in w and "qfm" in w
    ]
    assert substitution_warnings, result.warnings
    assert any("-12.0" in w for w in substitution_warnings), substitution_warnings


def test_magemin_subprocess_unknown_buffer_falls_back_with_warning(monkeypatch):
    # An unrecognised fO2_buffer config still drives the subprocess bridge,
    # but the adapter MUST surface the substitution as a warning rather
    # than silently swapping in 'qfm' (which would hide the requested fO2
    # mismatch from the caller). The previous _resolve_buffer routed the
    # warning into self._warnings, never reaching EquilibriumResult.
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq\nMode  : 1.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        return FakeCompleted()

    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({
            "fO2_buffer": "qfm-2",  # invalid: includes offset
            "warm_worker": False,
        })

    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    assert any("qfm-2" in w and "qfm" in w for w in result.warnings), (
        result.warnings
    )


def test_magemin_configured_named_buffer_marks_requested_fo2_out_of_domain(
    monkeypatch,
):
    captured: dict = {}

    class FakeCompleted:
        returncode = 0
        stderr = ""
        stdout = "Phase : liq mw\nMode  : 1.000 0.000\n"

    def fake_subprocess_run(args, **kwargs):
        captured["args"] = list(args)
        return FakeCompleted()

    monkeypatch.setattr(
        MAGEMinBackend,
        "_locate_binary",
        staticmethod(lambda explicit: Path("/fake/MAGEMin")),
    )
    monkeypatch.setattr(
        MAGEMinBackend,
        "_import_magemin_bridge",
        lambda self, *, requested: ("subprocess", None),
    )
    import simulator.melt_backend.magemin as magemin_module
    monkeypatch.setattr(
        magemin_module.subprocess, "run", fake_subprocess_run
    )

    backend = MAGEMinBackend()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        backend.initialize({"fO2_buffer": "mw", "warm_worker": False})

    result = backend.equilibrate(
        1450.0,
        composition_mol={"SiO2": 5.0, "MgO": 3.0},
        fO2_log=-12.0,
        pressure_bar=1e-6,
    )

    assert any(arg == "--buffer=mw" for arg in captured["args"])
    assert any(arg == "--buffer_n=0.000000" for arg in captured["args"])
    assert result.status == "out_of_domain"
    assert result.fO2_log == -12.0
    assert result.diagnostics["operating_point_clamped"] is True
    assert result.diagnostics["fO2_clamped"] is True
    assert result.diagnostics["requested_fO2_log"] == -12.0
    assert result.diagnostics["solved_fO2_log"] is None
    assert result.diagnostics["applied_fO2_buffer"] == "mw"
    assert result.diagnostics["authoritative_for_requested_conditions"] is False


def test_magemin_resolve_buffer_qfm_calibration_at_1450C():
    # Pin the O'Neill 1987 calibration math: at T = 1450 C (T_K = 1723.15),
    # logfo2_QFM should be ~-5.96. The conversion is load-bearing for the
    # A5 honest-substitution path; a wrong constant would silently shift
    # every Mars reducing fO2 by ~6 decades.
    qfm_at_1450C = MAGEMinBackend._qfm_logfo2_oneill(1450.0)
    assert qfm_at_1450C == -5.957329890026985
    assert -7.0 < qfm_at_1450C < -5.0, qfm_at_1450C


def test_magemin_qfm_buffer_offsets_are_bit_pinned():
    backend = MAGEMinBackend()
    expected_hex = {
        1000.0: "-0x1.cf0c806fa67b0p-1",
        1200.0: "-0x1.c9adf82faff0cp+1",
        1450.0: "-0x1.82bb1b69b673ep+2",
        1600.0: "-0x1.cd3c4b84b9f52p+2",
    }

    for temperature_C, pinned_hex in expected_hex.items():
        _, offset, _ = backend._resolve_buffer(
            temperature_C=temperature_C,
            fO2_log=-12.0,
        )
        assert offset.hex() == pinned_hex


def test_magemin_qfm_nonpositive_kelvin_behavior_is_bit_pinned():
    assert MAGEMinBackend._qfm_logfo2_oneill(-200.0).hex() == "-0x1.4dddf57582f36p+8"
    assert MAGEMinBackend._qfm_logfo2_oneill(-274.0).hex() == "0x1.cc9cac45df6b9p+14"
    with pytest.raises(ZeroDivisionError):
        MAGEMinBackend._qfm_logfo2_oneill(-273.15)


def test_oneill_qfm_coefficient_has_one_production_owner():
    repo_root = Path(__file__).resolve().parents[1]
    tracked = subprocess.run(
        ["git", "ls-files", "--", "simulator", "engines"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    coefficient = re.compile(r"(?<!\d)25_?050(?:\.0)?(?!\d)")
    occurrences = []

    for relative_path in tracked:
        if not relative_path.endswith(".py"):
            continue
        source_path = repo_root / relative_path
        for line in source_path.read_text(encoding="utf-8").splitlines():
            if coefficient.search(line):
                occurrences.append(relative_path)

    assert occurrences == ["simulator/chemistry/qfm_oneill.py"]
