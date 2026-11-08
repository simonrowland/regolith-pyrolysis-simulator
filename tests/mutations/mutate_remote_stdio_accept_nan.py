"""Prove the remote scalar test rejects a non-finite acceptance mutant."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/engine_pool.py'
TEST = 'tests/test_remote_stdio_engine_worker.py'
GUARD = """    if not finite:
        raise _RemoteEngineProtocolFailure(
            'non_finite_value', 'produced scalar must be finite',
        )
"""
MUTANT = """    if False:
        raise _RemoteEngineProtocolFailure(
            'non_finite_value', 'produced scalar must be finite',
        )
"""


def main() -> int:
    original = SOURCE.read_text(encoding='utf-8')
    if original.count(GUARD) != 1:
        raise SystemExit('expected one finite-scalar validation guard')
    SOURCE.write_text(original.replace(GUARD, MUTANT, 1), encoding='utf-8')
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-q',
                '-n',
                '0',
                '-p',
                'no:cacheprovider',
                TEST,
                '-k',
                'nan',
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        SOURCE.write_text(original, encoding='utf-8')
    if result.returncode == 0 or '1 failed' not in result.stdout:
        raise SystemExit('non-finite scalar mutant survived its regression')
    print('PASS: removing the finite-scalar guard fails the NaN regression')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
