"""Prove pool lifecycle refusals have a distinct typed error."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/engine_pool.py'
TYPED_RAISE = 'raise EngineWorkerPoolLifecycleError('
PLAIN_RAISE = 'raise RuntimeError('
TESTS = (
    'tests/test_engine_worker.py::'
    'test_submit_with_no_live_consumer_refuses_instead_of_hanging_forever',
    'tests/test_engine_worker.py::'
    'test_submit_racing_close_is_completed_or_rejected_without_orphan',
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(TYPED_RAISE) != 2:
        raise SystemExit('expected the closed and no-consumer lifecycle raises')
    SOURCE.write_text(source.replace(TYPED_RAISE, PLAIN_RAISE))
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
    if result.returncode == 0 or 'EngineWorkerPoolLifecycleError' not in output:
        raise SystemExit('untyped lifecycle mutation did not break the regressions')
    print('PASS: generic RuntimeError pool refusals break lifecycle regressions')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
