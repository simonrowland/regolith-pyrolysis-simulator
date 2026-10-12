"""Prove an external oxide-conversion failure stays typed through sampling.

Removing the native-call wrapper makes the production regression escape as an
untagged RuntimeError instead of returning a not_converged liquidus result.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'engines/alphamelts/thermoengine.py'
TARGET = """        mol = _thermoengine_native_call(
            lambda: self._chem.wt_to_mol_oxide(wt)
        )
"""
MUTATION = """        mol = self._chem.wt_to_mol_oxide(wt)
"""
TEST = (
    'tests/test_alphamelts_backend.py::'
    'test_echo_conversion_remote_failure_reaches_liquidus_as_not_converged'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify the native oxide conversion wrapper')
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
        print('oxide conversion unwrap mutation was not rejected')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
