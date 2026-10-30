"""Compare MAGEMin at the historical ThermoEngine spinel-hang conditions.

Run from the repository root on macOS with:

    PYTHONDONTWRITEBYTECODE=1 /opt/homebrew/bin/timeout -s TERM -k 5 180 \
        python3 scripts/diagnose_magemin_spinel_hang.py

The bulk and low-temperature point come from
docs-private/research/2026-07-14-thermoengine-spinel-hang/findings.md. The
paired 1200 C / 1 bar point is its reported successful spinel-bearing control.
Each MAGEMin call has a 60 s hard timeout. A live binary is sampled once after
8 s so a slow native call leaves a stack artifact before the timeout kills it.
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


CALL_TIMEOUT_S = 60.0
CALL_OVERRUN_ALLOWANCE_S = 15.0
SAMPLE_AFTER_S = 8.0
F_O2_LOG = -9.0

# These values are the documented historical bulk as kg on a 100 kg basis;
# their ratios are the reported wt-% composition.
COMPOSITION_KG = {
    'SiO2': 44.5,
    'TiO2': 1.5,
    'Al2O3': 13.5,
    'FeO': 16.5,
    'MgO': 9.0,
    'CaO': 11.0,
    'Na2O': 0.4,
    'K2O': 0.1,
    'Cr2O3': 0.35,
    'MnO': 0.2,
    'P2O5': 0.1,
}

PROBES = (
    ('historical_low_temperature', 40.0, 0.01),
    ('reported_spinel_control', 1200.0, 1.0),
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
                [sample_tool, str(pid), '5', '1', '-file', str(sample_path)],
                capture_output=True,
                text=True,
                timeout=12.0,
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
) -> bool:
    outcome: dict[str, Any] = {}
    finished = threading.Event()
    started = time.monotonic()

    def call_backend() -> None:
        try:
            result = backend.equilibrate(
                temperature_C,
                composition_kg=dict(COMPOSITION_KG),
                fO2_log=F_O2_LOG,
                pressure_bar=pressure_bar,
                call_timeout_s=CALL_TIMEOUT_S,
            )
            outcome['result'] = {
                'status': result.status,
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
            return False

    _emit({
        'event': 'probe-result',
        'probe': name,
        'temperature_C': temperature_C,
        'pressure_bar': pressure_bar,
        'fO2_log': F_O2_LOG,
        'elapsed_s': time.monotonic() - started,
        **outcome,
    })
    # A typed backend error is still a returned call and useful evidence.
    # Only the outer deadline means the adapter itself failed to return.
    return True


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
            'bulk_kg': COMPOSITION_KG,
        })

        all_returned = True
        for name, temperature_C, pressure_bar in PROBES:
            returned = _run_probe(
                backend,
                binary_path,
                output_dir,
                name=name,
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
            )
            all_returned = all_returned and returned
            if not returned:
                break
        _emit({'event': 'probe-finished', 'all_calls_returned': all_returned})
        return 0 if all_returned else 1
    finally:
        backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
