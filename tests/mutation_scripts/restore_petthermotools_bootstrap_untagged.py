"""Prove warm bootstrap import failures must retain their external origin."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/alphamelts.py'
TARGET = '''        module = _petthermotools_external_call(
            lambda: importlib.import_module('PetThermoTools')
        )'''
MUTATION = "        module = importlib.import_module('PetThermoTools')"
TEST = (
    'tests/test_alphamelts_backend.py::'
    'test_petthermotools_warm_bootstrap_import_failure_is_typed'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify the warm fallback import')
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
    if result.returncode == 0 or 'EngineWorkerRemoteError' not in output:
        print('bootstrap-import mutation was not rejected for the expected reason')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
