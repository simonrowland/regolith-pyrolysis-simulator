"""Prove known-invalid MAGEMin output stays a typed engine failure."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
TYPED = '''def _raise_magemin_output_invalid(
    message: str,
    cause: Optional[BaseException] = None,
) -> NoReturn:
    """Known-invalid MAGEMin output, not a defect in the parser."""
    if cause is None:
        raise _MAGEMinEngineCallError('MAGEMinOutputError', message)
    raise _MAGEMinEngineCallError('MAGEMinOutputError', message) from cause'''
PLAIN = '''def _raise_magemin_output_invalid(
    message: str,
    cause: Optional[BaseException] = None,
) -> NoReturn:
    """Known-invalid MAGEMin output, not a defect in the parser."""
    raise RuntimeError(message)'''
TEST = (
    'tests/test_magemin_backend.py::'
    'test_magemin_equilibrate_invalid_engine_output_is_typed_not_converged'
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(TYPED) != 1:
        raise SystemExit('expected one typed MAGEMin output-invalid raise')
    SOURCE.write_text(source.replace(TYPED, PLAIN, 1))
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
            'test_magemin_equilibrate_invalid_engine_output_is_typed_not_converged'
            in line
        )
    ]
    if result.returncode != 1 or not failed:
        raise SystemExit(
            'plain RuntimeError output validation did not fail the regression\n'
            + output[-2000:]
        )
    print('PASS: plain output RuntimeError fails the typed-status regression')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
