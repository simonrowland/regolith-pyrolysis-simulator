"""Prove the finder regression test rejects a restored broad catch."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/liquidus.py'
TARGET = '    except LiquidusSampleError as exc:\n        return LiquidusSolidusResult('
MUTATION = (
    '    except Exception as exc:  # restored broad error-to-status catch\n'
    '        exc = _external_sample_error(exc)\n'
    '        return LiquidusSolidusResult('
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify the liquidus finder catch')
    try:
        SOURCE.write_text(original.replace(TARGET, MUTATION, 1))
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-n',
                '0',
                'tests/test_liquidus_finder.py::'
                'test_finder_propagates_internal_monotone_error',
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
    if result.returncode == 0:
        print('mutation survived; regression test did not reject broad catch')
        return 1
    print('mutation rejected by test_finder_propagates_internal_monotone_error')
    print('\n'.join(result.stdout.splitlines()[-12:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
