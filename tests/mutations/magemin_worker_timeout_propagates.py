"""Prove a MAGEMin worker timeout stays a raised control-plane signal."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
PRESERVED = '''                except EngineWorkerTimeout:
                    raise
                except (
                    EngineWorkerExitedError,
                    EngineWorkerPoolLifecycleError,
                    EngineWorkerUnavailable,
                ) as exc:'''
WRAPPED = '''                except (
                    EngineWorkerExitedError,
                    EngineWorkerPoolLifecycleError,
                    EngineWorkerTimeout,
                    EngineWorkerUnavailable,
                ) as exc:'''
TEST = (
    'tests/test_magemin_backend.py::'
    'test_magemin_equilibrate_surfaces_typed_hang_without_disabling_backend'
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(PRESERVED) != 1:
        raise SystemExit('expected one MAGEMin worker-timeout re-raise')
    SOURCE.write_text(source.replace(PRESERVED, WRAPPED, 1))
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT)
    try:
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-q',
                '-n',
                '0',
                '--timeout=300',
                TEST,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        SOURCE.write_text(source)

    output = result.stdout + result.stderr
    failed = [
        line for line in output.splitlines()
        if line.startswith('FAILED ') and (
            'test_magemin_equilibrate_surfaces_typed_hang_without_disabling_backend'
            in line
        )
    ]
    if result.returncode != 1 or not failed:
        raise SystemExit(
            'wrapping EngineWorkerTimeout did not fail the hang guard\n'
            + output[-2000:]
        )
    print('PASS: wrapping EngineWorkerTimeout fails the raised-timeout guard')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
