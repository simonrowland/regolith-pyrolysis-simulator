"""Measure concurrent warm MAGEMin liquidus scans on the lunar-mare gate bulk.

Run both requested concurrency levels from the repository root on macOS:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
        /opt/homebrew/bin/timeout -s TERM -k 10 610 \
        python3 scripts/diagnose_magemin_spinel_hang.py --workers 8 16

The Python coordinator has a 600 s deadline across both runs. It starts N
separate scan processes together; each scan uses the production warm-worker
MAGEMin subprocess bridge and the liquidus finder. Native-call instrumentation
is installed only in this diagnostic process tree and records slot wait,
binary wall time, and child CPU time without changing the backend API.
"""

from __future__ import annotations

import argparse
from collections import defaultdict, deque
from contextlib import contextmanager
import contextvars
import faulthandler
import json
import multiprocessing
import os
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Iterator, Mapping

from simulator.melt_backend import magemin as magemin_module
from simulator.melt_backend.magemin import MAGEMinBackend


F_O2_LOG = -9.0
GATE_PRESSURE_BAR = 1e-6
MIN_T_C = 400.0
MAX_T_C = 2800.0  # 49 grid points at the configured 50 C step.
SCAN_STEP_C = 50.0
SAMPLE_STACK_AFTER_S = 30.0
OUTER_CAP_S = 600.0

# Exact projected IG bulk from tests/fixtures/runner/
# lunar_mare_low_ti_C0_24h.json; MnO and P2O5 are excluded by the production
# IG projection, so pass this already-projected gate bulk explicitly.
PROJECTED_BULK_WT_PCT = {
    'SiO2': 45.805946411267385,
    'Al2O3': 13.896187404322582,
    'CaO': 11.322819366465607,
    'MgO': 9.26412461393047,
    'FeOt': 16.984162807410257,
    'K2O': 0.10285938860300292,
    'Na2O': 0.41044099160813513,
    'TiO2': 1.5440208220639342,
    'O': 0.00036276439294850816,
    'Cr2O3': 0.3602714676473288,
}

_CURRENT_SLOT: contextvars.ContextVar[dict[str, Any] | None] = (
    contextvars.ContextVar('magemin_probe_current_slot', default=None)
)
_CURRENT_TEMPERATURE: contextvars.ContextVar[float | None] = (
    contextvars.ContextVar('magemin_probe_current_temperature', default=None)
)
_ORIGINAL_SLOT = magemin_module._magemin_subprocess_slot
_ORIGINAL_MAGEMIN_CALL = MAGEMinBackend._call_magemin_subprocess
_ORIGINAL_RUN = subprocess.run


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(dict(payload), sort_keys=True, default=str), flush=True)


def _append_event(filename: str, payload: Mapping[str, Any]) -> None:
    output_dir = os.environ.get('MAGEMIN_PROBE_OUTPUT_DIR')
    if not output_dir:
        return
    event = dict(payload)
    event.setdefault('pid', os.getpid())
    event.setdefault('scan_id', os.environ.get('MAGEMIN_PROBE_SCAN_ID'))
    encoded = (json.dumps(event, sort_keys=True, default=str) + '\n').encode()
    path = Path(output_dir) / filename
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def _native_call_temperature(args: Any) -> float | None:
    if isinstance(args, (str, bytes)):
        return None
    for arg in args:
        text = str(arg)
        if text.startswith('--Temp='):
            try:
                return float(text.partition('=')[2])
            except ValueError:
                return None
    return None


def _timed_magemin_call(self: Any, *args: Any, **kwargs: Any) -> Any:
    temperature = kwargs.get('temperature_C')
    token = _CURRENT_TEMPERATURE.set(
        None if temperature is None else float(temperature)
    )
    try:
        return _ORIGINAL_MAGEMIN_CALL(self, *args, **kwargs)
    finally:
        _CURRENT_TEMPERATURE.reset(token)


@contextmanager
def _timed_subprocess_slot(timeout_s: float) -> Iterator[None]:
    started = time.perf_counter()
    state: dict[str, Any] = {
        'temperature_C': _CURRENT_TEMPERATURE.get(),
    }
    token = _CURRENT_SLOT.set(state)
    try:
        try:
            with _ORIGINAL_SLOT(timeout_s):
                state['slot_wait_s'] = time.perf_counter() - started
                state['slot_acquired'] = True
                yield
        except BaseException as exc:
            state.setdefault('slot_wait_s', time.perf_counter() - started)
            state['slot_acquired'] = False
            state['slot_error'] = type(exc).__name__
            raise
    finally:
        state.setdefault('slot_wait_s', time.perf_counter() - started)
        state['slot_held_s'] = (
            max(0.0, time.perf_counter() - started - state['slot_wait_s'])
            if state.get('slot_acquired') else 0.0
        )
        _append_event(
            f"native-{os.environ.get('MAGEMIN_PROBE_SCAN_ID', 'unknown')}.jsonl",
            state,
        )
        _CURRENT_SLOT.reset(token)


def _timed_subprocess_run(args: Any, *positional: Any, **keywords: Any) -> Any:
    binary = os.environ.get('MAGEMIN_PROBE_BINARY')
    is_magemin = bool(
        binary
        and not isinstance(args, (str, bytes))
        and args
        and str(args[0]) == binary
    )
    if not is_magemin:
        return _ORIGINAL_RUN(args, *positional, **keywords)

    state = _CURRENT_SLOT.get()
    if state is None:
        state = {}
    state['temperature_C'] = _native_call_temperature(args)
    user_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    started = time.perf_counter()
    try:
        completed = _ORIGINAL_RUN(args, *positional, **keywords)
        state['binary_status'] = 'returned'
        state['binary_returncode'] = completed.returncode
        return completed
    except BaseException as exc:
        state['binary_status'] = type(exc).__name__
        raise
    finally:
        user_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        state['solve_wall_s'] = max(0.0, time.perf_counter() - started)
        state['solver_cpu_user_s'] = max(
            0.0, user_after.ru_utime - user_before.ru_utime
        )
        state['solver_cpu_system_s'] = max(
            0.0, user_after.ru_stime - user_before.ru_stime
        )


# WarmEngineWorker uses spawn. The diagnostic-only patches are repeated when
# its child re-imports this script as __mp_main__, before the MAGEMin handler
# executes. They are never installed in the simulator source.
magemin_module._magemin_subprocess_slot = _timed_subprocess_slot
MAGEMinBackend._call_magemin_subprocess = _timed_magemin_call
subprocess.run = _timed_subprocess_run


def _write_scan_event(scan_id: str, payload: Mapping[str, Any]) -> None:
    _append_event(f'scan-{scan_id}.jsonl', payload)


def _run_scan(
    scan_id: str,
    start_event: Any,
    output_dir: str,
) -> None:
    os.environ['MAGEMIN_PROBE_OUTPUT_DIR'] = output_dir
    os.environ['MAGEMIN_PROBE_SCAN_ID'] = scan_id
    stack_path = Path(output_dir) / f'{scan_id}-python-stacks.txt'
    stack_stream = stack_path.open('a', encoding='utf-8', buffering=1)
    faulthandler.register(signal.SIGUSR1, file=stack_stream, all_threads=True)

    backend = MAGEMinBackend()
    sample_index = 0
    try:
        binary_path = backend._resolve_binary_path()
        if binary_path is None:
            _write_scan_event(scan_id, {
                'event': 'backend-unavailable',
                'reason': 'MAGEMin binary was not found',
            })
            return
        resolved_binary = str(binary_path.resolve())
        os.environ['MAGEMIN_PROBE_BINARY'] = resolved_binary
        initialized = backend.initialize({
            'database': 'ig',
            'python_bridge': 'subprocess',
            'warm_worker': True,
            'warm_pool_size': 1,
        })
        if not initialized:
            _write_scan_event(scan_id, {
                'event': 'backend-unavailable',
                'warnings': backend._warnings,
                'last_error': backend._last_error,
            })
            return

        workers = (
            backend._subprocess_pool.workers
            if backend._subprocess_pool is not None else ()
        )
        worker_pid = (
            getattr(workers[0].process, 'pid', None) if workers else None
        )
        _write_scan_event(scan_id, {
            'event': 'scan-ready',
            'binary': resolved_binary,
            'warm_worker': True,
            'engine_worker_pid': worker_pid,
            'diagnostic_signal': signal.SIGUSR1,
        })

        original_equilibrate = backend.equilibrate

        def timed_equilibrate(temperature_C: float, *args: Any, **kwargs: Any):
            nonlocal sample_index
            sample_index += 1
            index = sample_index
            started_wall = time.time()
            started = time.perf_counter()
            started_cpu = time.process_time()
            _write_scan_event(scan_id, {
                'event': 'sample-start',
                'sample_index': index,
                'temperature_C': float(temperature_C),
                'started_wall': started_wall,
            })
            try:
                result = original_equilibrate(
                    temperature_C, *args, **kwargs
                )
            except BaseException as exc:
                _write_scan_event(scan_id, {
                    'event': 'sample-result',
                    'sample_index': index,
                    'temperature_C': float(temperature_C),
                    'wall_s': time.perf_counter() - started,
                    'caller_cpu_s': time.process_time() - started_cpu,
                    'status': type(exc).__name__,
                    'error': str(exc),
                })
                raise
            _write_scan_event(scan_id, {
                'event': 'sample-result',
                'sample_index': index,
                'temperature_C': float(temperature_C),
                'wall_s': time.perf_counter() - started,
                'caller_cpu_s': time.process_time() - started_cpu,
                'status': result.status,
                'solver_status': (result.diagnostics or {}).get(
                    'magemin_solver_status'
                ),
                'diagnostics': result.diagnostics,
            })
            return result

        backend.equilibrate = timed_equilibrate
        if not start_event.wait(timeout=90.0):
            raise TimeoutError('coordinator did not release scan start')

        result = backend.find_liquidus_solidus(
            projected_oxide_wt_pct=dict(PROJECTED_BULK_WT_PCT),
            fO2_log=F_O2_LOG,
            pressure_bar=GATE_PRESSURE_BAR,
            min_T_C=MIN_T_C,
            max_T_C=MAX_T_C,
            scan_step_C=SCAN_STEP_C,
        )
        _write_scan_event(scan_id, {
            'event': 'scan-result',
            'status': result.status,
            'sample_count': sample_index,
            'grid_points': int((MAX_T_C - MIN_T_C) / SCAN_STEP_C) + 1,
            'solidus_T_C': result.solidus_T_C,
            'liquidus_T_C': result.liquidus_T_C,
            'diagnostics': result.diagnostics,
            'warnings': result.warnings,
        })
    except BaseException as exc:
        _write_scan_event(scan_id, {
            'event': 'scan-error',
            'type': type(exc).__name__,
            'message': str(exc),
        })
    finally:
        backend.close()
        try:
            faulthandler.unregister(signal.SIGUSR1)
        except (RuntimeError, OSError):
            pass
        stack_stream.close()


def _read_events(path: Path, offset: int) -> tuple[int, list[dict[str, Any]]]:
    if not path.exists():
        return offset, []
    events = []
    with path.open('r', encoding='utf-8') as stream:
        stream.seek(offset)
        while line := stream.readline():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return stream.tell(), events


def _ps_processes() -> dict[int, tuple[int, str]]:
    try:
        result = _ORIGINAL_RUN(
            ['/bin/ps', '-Ao', 'pid=,ppid=,command='],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    processes = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        try:
            pid, ppid = int(fields[0]), int(fields[1])
        except ValueError:
            continue
        processes[pid] = (ppid, fields[2])
    return processes


def _descendants(processes: Mapping[int, tuple[int, str]], parent: int) -> set[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for pid, (ppid, _command) in processes.items():
        children[ppid].append(pid)
    found: set[int] = set()
    pending = list(children.get(parent, ()))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found.add(pid)
        pending.extend(children.get(pid, ()))
    return found


def _sample_slow_scan(
    scan_id: str,
    scan_pid: int,
    worker_pid: int | None,
    binary_path: str,
    sample_index: int,
    output_dir: Path,
) -> None:
    try:
        os.kill(scan_pid, signal.SIGUSR1)
    except OSError:
        pass
    if worker_pid is not None:
        try:
            os.kill(worker_pid, signal.SIGUSR1)
        except OSError:
            pass
    _emit({
        'event': 'python-stack-sample-requested',
        'scan_id': scan_id,
        'sample_index': sample_index,
        'scan_stack_path': str(output_dir / f'{scan_id}-python-stacks.txt'),
        'worker_pid': worker_pid,
        'worker_diagnostic_log': str(
            Path(tempfile.gettempdir())
            / 'regolith-pyrolysis-simulator'
            / 'magemin-diagnostics-0.log'
        ),
    })

    sample_tool = shutil.which('sample')
    processes = _ps_processes()
    descendants = _descendants(processes, scan_pid)
    binary_pids = [
        pid for pid in descendants
        if pid in processes and binary_path in processes[pid][1]
    ]
    if sample_tool is None or not binary_pids:
        _emit({
            'event': 'slow-call-stack-sample-skipped',
            'scan_id': scan_id,
            'sample_index': sample_index,
            'binary_pids': binary_pids,
            'reason': (
                'sample command unavailable' if sample_tool is None
                else 'no MAGEMin process descended from this scan'
            ),
        })
        return
    for pid in binary_pids:
        sample_path = output_dir / f'{scan_id}-sample-{sample_index}-{pid}.txt'
        try:
            result = _ORIGINAL_RUN(
                [sample_tool, str(pid), '3', '1', '-file', str(sample_path)],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            _emit({
                'event': 'slow-call-stack-sample',
                'scan_id': scan_id,
                'sample_index': sample_index,
                'pid': pid,
                'returncode': result.returncode,
                'path': str(sample_path),
                'stderr': result.stderr.strip(),
            })
        except (OSError, subprocess.SubprocessError) as exc:
            _emit({
                'event': 'slow-call-stack-sample-error',
                'scan_id': scan_id,
                'sample_index': sample_index,
                'pid': pid,
                'error': str(exc),
            })


def _collect_scan_results(output_dir: Path, scan_ids: list[str]) -> None:
    for scan_id in scan_ids:
        scan_events = []
        path = output_dir / f'scan-{scan_id}.jsonl'
        _, new_events = _read_events(path, 0)
        scan_events.extend(new_events)
        native_path = output_dir / f'native-{scan_id}.jsonl'
        _, native_events = _read_events(native_path, 0)
        native_by_temperature: dict[float, deque[dict[str, Any]]] = defaultdict(deque)
        for event in native_events:
            temperature = event.get('temperature_C')
            if temperature is not None:
                native_by_temperature[round(float(temperature), 6)].append(event)

        _emit({
            'event': 'scan-summary',
            'scan_id': scan_id,
            'scan': next(
                (event for event in scan_events
                 if event.get('event') in {'scan-result', 'scan-error', 'backend-unavailable'}),
                {'status': 'no-result-event'},
            ),
        })
        sample_results = [
            event for event in scan_events
            if event.get('event') == 'sample-result'
        ]
        for sample in sample_results:
            temperature = sample.get('temperature_C')
            temperature_key = (
                round(float(temperature), 6) if temperature is not None else None
            )
            native = (
                native_by_temperature[temperature_key].popleft()
                if temperature_key is not None
                and native_by_temperature.get(temperature_key)
                else {}
            )
            cpu_s = float(native.get('solver_cpu_user_s', 0.0)) + float(
                native.get('solver_cpu_system_s', 0.0)
            )
            solve_wall_s = native.get('solve_wall_s')
            slot_wait_s = native.get('slot_wait_s')
            wall_s = float(sample.get('wall_s', 0.0))
            transport_pool_s = (
                max(
                    0.0,
                    wall_s - float(slot_wait_s or 0.0)
                    - float(solve_wall_s or 0.0),
                )
                if slot_wait_s is not None else None
            )
            _emit({
                'event': 'sample-summary',
                'scan_id': scan_id,
                'sample_index': sample.get('sample_index'),
                'temperature_C': temperature,
                'status': sample.get('status'),
                'solver_status': sample.get('solver_status'),
                'wall_s': round(wall_s, 3),
                'caller_cpu_s': round(float(sample.get('caller_cpu_s', 0.0)), 3),
                'slot_wait_s': (
                    round(float(slot_wait_s), 3) if slot_wait_s is not None else None
                ),
                'solve_wall_s': (
                    round(float(solve_wall_s), 3) if solve_wall_s is not None else None
                ),
                'solver_cpu_s': round(cpu_s, 3) if solve_wall_s is not None else None,
                'transport_pool_s': (
                    round(transport_pool_s, 3)
                    if transport_pool_s is not None else None
                ),
                'binary_status': native.get('binary_status'),
                'binary_returncode': native.get('binary_returncode'),
                'slot_error': native.get('slot_error'),
            })
        for native in native_events:
            if native.get('temperature_C') is None:
                _emit({'event': 'native-call-without-temperature', **native})


def _run_concurrency(
    context: Any,
    concurrency: int,
    output_dir: Path,
    deadline: float,
) -> tuple[bool, list[str]]:
    run_id = f'n{concurrency}-{int(time.time())}'
    scan_ids = [f'{run_id}-scan{index:02d}' for index in range(concurrency)]
    start_event = context.Event()
    processes = []
    for scan_id in scan_ids:
        process = context.Process(
            target=_run_scan,
            args=(scan_id, start_event, str(output_dir)),
            name=f'magemin-gate-{scan_id}',
        )
        process.start()
        processes.append(process)

    offsets = {scan_id: 0 for scan_id in scan_ids}
    ready: set[str] = set()
    worker_pids: dict[str, int | None] = {}
    active: dict[str, dict[str, Any]] = {}
    sampled: set[tuple[str, int]] = set()
    released = False
    run_ok = True
    ready_deadline = min(deadline, time.monotonic() + 120.0)

    while True:
        now = time.monotonic()
        for scan_id in scan_ids:
            event_path = output_dir / f'scan-{scan_id}.jsonl'
            offset, events = _read_events(event_path, offsets[scan_id])
            offsets[scan_id] = offset
            for event in events:
                kind = event.get('event')
                if kind == 'scan-ready':
                    ready.add(scan_id)
                    worker_pids[scan_id] = event.get('engine_worker_pid')
                elif kind == 'backend-unavailable':
                    ready.add(scan_id)
                    run_ok = False
                elif kind == 'sample-start':
                    active[scan_id] = event
                elif kind == 'sample-result':
                    active.pop(scan_id, None)
                elif kind == 'scan-error':
                    ready.add(scan_id)
                    run_ok = False
        if not any(process.is_alive() for process in processes):
            break
        if not released and len(ready) == concurrency:
            start_event.set()
            released = True
            _emit({
                'event': 'concurrent-scans-released',
                'concurrency': concurrency,
                'scan_ids': scan_ids,
            })
        elif not released and now >= ready_deadline:
            start_event.set()
            released = True
            run_ok = False
            _emit({
                'event': 'scan-startup-deadline',
                'concurrency': concurrency,
                'ready_count': len(ready),
            })

        for scan_id, sample in list(active.items()):
            sample_index = int(sample['sample_index'])
            key = (scan_id, sample_index)
            if (
                key not in sampled
                and time.time() - float(sample['started_wall']) >= SAMPLE_STACK_AFTER_S
            ):
                process_index = scan_ids.index(scan_id)
                scan_pid = processes[process_index].pid
                if scan_pid is not None:
                    _sample_slow_scan(
                        scan_id,
                        scan_pid,
                        worker_pids.get(scan_id),
                        str(_resolved_binary_in_process(processes, scan_id, output_dir)),
                        sample_index,
                        output_dir,
                    )
                sampled.add(key)
        if now >= deadline:
            run_ok = False
            _emit({
                'event': 'outer-deadline',
                'concurrency': concurrency,
                'active_samples': active,
            })
            break
        time.sleep(0.2)

    if any(process.is_alive() for process in processes):
        # Engine workers start a private process group. Stop the specific
        # group for each scan before terminating its coordinator process.
        for scan_id, worker_pid in worker_pids.items():
            if worker_pid is None:
                continue
            try:
                os.killpg(int(worker_pid), signal.SIGTERM)
            except OSError:
                pass
        for process in processes:
            if process.is_alive():
                process.terminate()
        for process in processes:
            process.join(timeout=2.0)
            if process.is_alive() and process.pid is not None:
                try:
                    os.kill(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.join(timeout=1.0)

    for process in processes:
        if process.exitcode not in (0, None):
            run_ok = False
    _collect_scan_results(output_dir, scan_ids)
    return run_ok, scan_ids


def _resolved_binary_in_process(
    processes: list[Any], scan_id: str, output_dir: Path,
) -> str:
    del processes
    _, events = _read_events(output_dir / f'scan-{scan_id}.jsonl', 0)
    for event in events:
        if event.get('event') == 'scan-ready' and event.get('binary'):
            return str(event['binary'])
    return ''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--workers', nargs='+', type=int, default=[8, 16],
        help='sequential scan-process concurrency levels (default: 8 16)',
    )
    parser.add_argument(
        '--outer-cap-s', type=float, default=OUTER_CAP_S,
        help='hard deadline across all requested runs (default: 600)',
    )
    args = parser.parse_args()
    if any(count <= 0 for count in args.workers):
        parser.error('--workers values must be positive')
    if args.outer_cap_s <= 0:
        parser.error('--outer-cap-s must be positive')

    output_dir = Path(tempfile.mkdtemp(prefix='magemin-gate-load-probe-'))
    os.environ['MAGEMIN_PROBE_OUTPUT_DIR'] = str(output_dir)
    _emit({
        'event': 'probe-start',
        'output_dir': str(output_dir),
        'worker_counts': args.workers,
        'outer_cap_s': args.outer_cap_s,
        'warm_worker': True,
        'warm_pool_size_per_scan': 1,
        'machine_subprocess_slots': magemin_module._magemin_subprocess_slot_count(),
        'warm_call_timeout_s': magemin_module.MAGEMIN_WARM_CALL_TIMEOUT_S,
        'warm_finder_budget_s': magemin_module.MAGEMIN_WARM_LIQUIDUS_BUDGET_S,
        'grid': {
            'min_T_C': MIN_T_C,
            'max_T_C': MAX_T_C,
            'scan_step_C': SCAN_STEP_C,
            'initial_points': int((MAX_T_C - MIN_T_C) / SCAN_STEP_C) + 1,
            'bisection': True,
        },
    })

    context = multiprocessing.get_context('spawn')
    deadline = time.monotonic() + args.outer_cap_s
    all_ok = True
    for concurrency in args.workers:
        if time.monotonic() >= deadline:
            all_ok = False
            _emit({'event': 'outer-deadline-before-run', 'concurrency': concurrency})
            break
        ok, _scan_ids = _run_concurrency(
            context, concurrency, output_dir, deadline
        )
        all_ok = all_ok and ok
    _emit({
        'event': 'probe-finished',
        'all_runs_completed': all_ok,
        'elapsed_s': args.outer_cap_s - max(0.0, deadline - time.monotonic()),
        'output_dir': str(output_dir),
    })
    return 0 if all_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
