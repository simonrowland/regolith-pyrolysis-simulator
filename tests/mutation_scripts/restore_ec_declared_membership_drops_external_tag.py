"""Prove the declared-failure tuple still owns the PetThermoTools tag.

Removing the tag lets a genuine equilibrate_MELTS failure escape EC
sampling after the liquidus bounds have already succeeded.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'engines/alphamelts/thermoengine.py'
TARGET = """    ThermoEngineNotConvergedError,
    EngineWorkerTimeout,
    _PetThermoToolsExternalCallFailure,
    _PetThermoToolsUnavailable,
)
"""
MUTATION = """    ThermoEngineNotConvergedError,
    EngineWorkerTimeout,
    _PetThermoToolsUnavailable,
)
"""
TEST = (
    'tests/test_alphamelts_provider.py::'
    'test_provider_ec_sampling_projects_tagged_engine_failure'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify the declared-failure tuple')
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
        print('external-tag membership mutation was not rejected')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
