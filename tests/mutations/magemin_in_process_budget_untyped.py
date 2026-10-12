"""Prove an in-process aggregate budget expiry stays typed."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
TYPED = '''                raise _MAGEMinEngineLifecycleError(
                    'MAGEMinAggregateBudgetExceeded',
'''
MARKER = '# Even in-process bridges cannot be cancelled mid-call'
PLAIN = '''                raise RuntimeError(
                    'MAGEMin call cancelled: aggregate liquidus budget exhausted'
                )'''
TEST = (
    'tests/test_magemin_backend.py::'
    'test_magemin_in_process_budget_exhaustion_returns_typed_status'
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(MARKER) != 1:
        raise SystemExit('expected the in-process budget guard')
    start = source.index(TYPED, source.index(MARKER))
    end = source.index('\n                )', start) + len('\n                )')
    mutated = source[:start] + PLAIN + source[end:]
    SOURCE.write_text(mutated)
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
    if (
        result.returncode == 0
        or 'MAGEMin call cancelled: aggregate liquidus budget exhausted'
        not in output
    ):
        raise SystemExit('untyped budget mutation did not break the regression')
    print('PASS: generic RuntimeError budget expiry breaks typed status regression')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
