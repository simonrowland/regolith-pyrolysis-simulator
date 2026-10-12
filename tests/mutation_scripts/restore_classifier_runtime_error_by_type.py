"""Prove a bare RuntimeError is not classified as not_converged."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'engines/alphamelts/thermoengine.py'
TARGET = '''    if isinstance(exc, _PetThermoToolsExternalCallFailure):
        return ThermoEngineFailureDisposition(
            status='not_converged',
            reason_code='not_converged',
            exception=exc,
        )'''
MUTATION = '''    if type(exc) is RuntimeError or isinstance(
        exc, _PetThermoToolsExternalCallFailure
    ):
        return ThermoEngineFailureDisposition(
            status='not_converged',
            reason_code='not_converged',
            exception=exc,
        )'''
TEST = (
    'tests/test_alphamelts_backend.py::'
    'test_alphamelts_liquidus_propagates_sampler_wrapper_runtime_error'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError(
            'could not identify the origin-tagged failure projection: '
            f'found {original.count(TARGET)}'
        )
    try:
        SOURCE.write_text(original.replace(TARGET, MUTATION, 1))
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-vv',
                '-n',
                '0',
                '--timeout=300',
                TEST,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        SOURCE.write_text(original)
    output = result.stdout + result.stderr
    if result.returncode != 1 or 'FAILED' not in output:
        print('runtime-error type mutation was not rejected')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
