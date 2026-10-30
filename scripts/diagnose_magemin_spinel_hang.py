"""Probe MAGEMin on the in-basis lunar mare bulk used by the gate.

Run from the repository root on macOS with:

    PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
        /opt/homebrew/bin/timeout -s TERM -k 5 180 \
        python3 scripts/diagnose_magemin_spinel_hang.py

The projected bulk is copied from the production lunar-mare gate fixture. The
40 C point checks the historical ThermoEngine trigger; 900-950 C samples bracket
the fixture's held 918.75 C solidus, and 1200 C is a hot control. Each binary
call has a 25 s hard timeout. Each returned result must include MAGEMin's
parsed solver status, which proves the binary ran instead of a projection
refusal. A live process and Python stacks are sampled on slow calls.
"""

from __future__ import annotations

import faulthandler
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from simulator.melt_backend.magemin import MAGEMinBackend


CALL_TIMEOUT_S = 25.0
CALL_OVERRUN_ALLOWANCE_S = 5.0
SAMPLE_AFTER_S = 5.0
F_O2_LOG = -9.0
GATE_PRESSURE_BAR = 1e-6

# Exact projected IG bulk from tests/fixtures/runner/
# lunar_mare_low_ti_C0_24h.json; MnO and P2O5 were excluded by the production
# IG projection, so pass this folded bulk through the adapter's explicit
# projected_oxide_wt_pct path to exercise the binary.
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

PROBES = (
    ('historical_low_temperature', 40.0, 0.01),
    ('below_solidus', 900.0, GATE_PRESSURE_BAR),
    ('near_solidus', 918.75, GATE_PRESSURE_BAR),
    ('above_solidus', 950.0, GATE_PRESSURE_BAR),
    ('hot_control', 1200.0, GATE_PRESSURE_BAR),
)


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def _magemin_pids(binary_path: Path) -> list[int]:
    try:
        result = subprocess.run(
            ['/bin/ps', '-Ao', 'pid=,command='],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _emit({'event': 'process-list-error', 'error': str(exc)})
        return []

    if result.returncode != 0:
        _emit({
            'event': 'process-list-error',
            'returncode': result.returncode,
            'stderr': result.stderr.strip(),
        })
        return []

    resolved = str(binary_path.resolve())
    pids: list[int] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or resolved not in fields[1]:
            continue
        try:
            pids.append(int(fields[0]))
        except ValueError:
            continue
    return pids


def _sample_live_binary(
    binary_path: Path,
    output_dir: Path,
    probe_name: str,
) -> None:
    pids = _magemin_pids(binary_path)
    sample_tool = shutil.which('sample')
    if not pids:
        _emit({
            'event': 'native-sample-skipped',
            'probe': probe_name,
            'reason': 'no MAGEMin subprocess is visible',
        })
        return
    if sample_tool is None:
        _emit({
            'event': 'native-sample-skipped',
            'probe': probe_name,
            'reason': 'macOS sample command is unavailable',
            'pids': pids,
        })
        return

    for pid in pids:
        sample_path = output_dir / f'{probe_name}-{pid}.sample.txt'
        try:
            result = subprocess.run(
                [sample_tool, str(pid), '3', '1', '-file', str(sample_path)],
                capture_output=True,
                text=True,
                timeout=8.0,
                check=False,
            )
            _emit({
                'event': 'native-sample',
                'probe': probe_name,
                'pid': pid,
                'returncode': result.returncode,
                'path': str(sample_path),
                'stderr': result.stderr.strip(),
            })
        except (OSError, subprocess.SubprocessError) as exc:
            _emit({
                'event': 'native-sample-error',
                'probe': probe_name,
                'pid': pid,
                'error': str(exc),
            })


def _run_probe(
    backend: MAGEMinBackend,
    binary_path: Path,
    output_dir: Path,
    *,
    name: str,
    temperature_C: float,
    pressure_bar: float,
) -> tuple[bool, bool]:
    outcome: dict[str, Any] = {}
    finished = threading.Event()
    started = time.monotonic()

    def call_backend() -> None:
        try:
            result = backend.equilibrate(
                temperature_C,
                projected_oxide_wt_pct=dict(PROJECTED_BULK_WT_PCT),
                fO2_log=F_O2_LOG,
                pressure_bar=pressure_bar,
                call_timeout_s=CALL_TIMEOUT_S,
                native_activity_formula='MgO',
            )
            outcome['result'] = {
                'status': result.status,
                'solver_status': result.diagnostics.get(
                    'magemin_solver_status'
                ),
                'liquid_fraction': result.liquid_fraction,
                'phases_present': result.phases_present,
                'phase_masses_kg': result.phase_masses_kg,
                'warnings': result.warnings,
                'diagnostics': result.diagnostics,
            }
        except Exception as exc:  # report the exact adapter boundary failure
            outcome['error'] = {
                'type': type(exc).__name__,
                'message': str(exc),
            }
        finally:
            finished.set()

    thread = threading.Thread(
        target=call_backend,
        name=f'magemin-diagnostic-{name}',
        daemon=True,
    )
    thread.start()

    sampled = False
    hard_deadline = started + CALL_TIMEOUT_S + CALL_OVERRUN_ALLOWANCE_S
    while not finished.wait(0.25):
        elapsed = time.monotonic() - started
        if not sampled and elapsed >= SAMPLE_AFTER_S:
            _sample_live_binary(binary_path, output_dir, name)
            stack_path = output_dir / f'{name}-python-threads.txt'
            with stack_path.open('w', encoding='utf-8') as stream:
                faulthandler.dump_traceback(file=stream, all_threads=True)
            _emit({
                'event': 'python-thread-stack',
                'probe': name,
                'path': str(stack_path),
            })
            sampled = True
        if time.monotonic() >= hard_deadline:
            _emit({
                'event': 'probe-overrun',
                'probe': name,
                'elapsed_s': time.monotonic() - started,
                'thread_alive': thread.is_alive(),
            })
            return False, False

    elapsed_s = time.monotonic() - started
    returned_result = outcome.get('result')
    solver_status = (
        returned_result.get('solver_status')
        if isinstance(returned_result, dict)
        else None
    )
    assertion_failures = []
    if elapsed_s <= 0.0:
        assertion_failures.append('elapsed_s must be positive')
    if not isinstance(returned_result, dict):
        assertion_failures.append('MAGEMin returned no solver result')
    else:
        diagnostics = returned_result.get('diagnostics') or {}
        if diagnostics.get('backend_status_reason') == 'composition_projected':
            assertion_failures.append(
                'backend refused composition_projected without calling MAGEMin'
            )
        if solver_status is None:
            assertion_failures.append('MAGEMin solver status is missing')
    _emit({
        'event': 'probe-result',
        'probe': name,
        'temperature_C': temperature_C,
        'pressure_bar': pressure_bar,
        'fO2_log': F_O2_LOG,
        'elapsed_s': elapsed_s,
        'binary_called': solver_status is not None,
        'solver_status': solver_status,
        'backend_status': (
            returned_result.get('status')
            if isinstance(returned_result, dict)
            else None
        ),
        'assertion_failures': assertion_failures,
        **outcome,
    })
    # Keep probing after a returned error; stop only when the adapter misses
    # its outer deadline and may still own a live MAGEMin process.
    return True, not assertion_failures


def main() -> int:
    backend = MAGEMinBackend()
    output_dir = Path(tempfile.mkdtemp(prefix='magemin-spinel-probe-'))
    try:
        if not backend.initialize({
            'database': 'ig',
            'python_bridge': 'subprocess',
            'warm_worker': False,
            'timeout_s': CALL_TIMEOUT_S,
        }):
            _emit({
                'event': 'backend-unavailable',
                'warnings': backend._warnings,
                'last_error': backend._last_error,
            })
            return 2

        binary_path = backend._binary_path
        if binary_path is None:
            _emit({'event': 'backend-unavailable', 'reason': 'no binary path'})
            return 2
        _emit({
            'event': 'probe-start',
            'binary': str(binary_path.resolve()),
            'database': 'ig',
            'output_dir': str(output_dir),
            'call_timeout_s': CALL_TIMEOUT_S,
            'bulk_wt_pct': PROJECTED_BULK_WT_PCT,
            'gate_pressure_bar': GATE_PRESSURE_BAR,
        })

        all_returned = True
        all_binary_calls_asserted = True
        for name, temperature_C, pressure_bar in PROBES:
            returned, assertions_passed = _run_probe(
                backend,
                binary_path,
                output_dir,
                name=name,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
            )
            all_returned = all_returned and returned
            all_binary_calls_asserted = (
                all_binary_calls_asserted and assertions_passed
            )
            if not returned:
                break
        _emit({
            'event': 'probe-finished',
            'all_calls_returned': all_returned,
            'all_binary_calls_asserted': all_binary_calls_asserted,
        })
        return 0 if all_returned and all_binary_calls_asserted else 1
    finally:
        backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
