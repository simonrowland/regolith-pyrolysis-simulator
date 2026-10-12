"""Prove the MAGEMin local-error regression test rejects the old catch."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
TEST = ROOT / 'tests/test_magemin_backend.py'
NARROW = 'except _MAGEMinEngineCallError as exc:'
BROAD = 'except Exception as exc:  # noqa: BLE001 - library-boundary catch'


def main() -> int:
    source = SOURCE.read_text()
    if source.count(NARROW) != 1:
        raise SystemExit('expected one MAGEMin equilibrium boundary catch')
    original = source
    SOURCE.write_text(source.replace(NARROW, BROAD, 1))
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT)
    try:
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-n',
                '0',
                str(TEST),
                '-k',
                'equilibrate_propagates_local_call_helper_errors',
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        SOURCE.write_text(original)

    if result.returncode == 0:
        raise SystemExit('mutation survived; expected the focused regression test to fail')
    if 'test_magemin_equilibrate_propagates_local_call_helper_errors' not in (
        result.stdout + result.stderr
    ):
        raise SystemExit('pytest failed for an unrelated reason')
    print('PASS: restored broad catch makes the focused regression test red')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
