"""Prove health-return failures cannot use a bare re-raise."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/thermoengine.py'
NARROW = 'if classified is exc:\n                raise classified'
BARE = 'if classified is exc:\n                raise'
TESTS = (
    'tests/test_alphamelts_backend.py::test_initialize_ood_smoke_closes_and_raises_typed_refusal',
    'tests/test_alphamelts_backend.py::test_initialize_smoke_timeout_raises_typed_timeout_not_import_error',
    'tests/test_thermoengine_failure_disposition.py::test_initialize_health_return_preserves_solver_failure_reason',
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(NARROW) != 1:
        raise SystemExit('expected one explicit ThermoEngine health failure raise')
    SOURCE.write_text(source.replace(NARROW, BARE, 1))
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
                *TESTS,
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
    if result.returncode == 0 or 'No active exception to reraise' not in output:
        raise SystemExit('bare re-raise mutation did not trigger the regression')
    print('PASS: bare re-raise breaks the health-result regressions')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
