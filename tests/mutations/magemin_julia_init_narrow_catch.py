"""Prove PyJulia initialization failures are caught at the Main import."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
NARROW = '''                    except (
                        JuliaError,
                        JuliaNotFound,
                        UnsupportedPythonError,
                        OSError,
                        subprocess.SubprocessError,
                    ) as exc:'''
IMPORT_ONLY = '                    except ImportError as exc:'
TEST = (
    'tests/test_magemin_backend.py::'
    'test_magemin_julia_runtime_discovery_failures_return_unavailable'
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(NARROW) != 1:
        raise SystemExit('expected one PyJulia runtime initialization catch')
    SOURCE.write_text(source.replace(NARROW, IMPORT_ONLY, 1))
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
    if result.returncode == 0 or 'julia_runtime_discovery_failures_return_unavailable' not in output:
        raise SystemExit('ImportError-only mutation did not break the regression')
    print('PASS: ImportError-only catch leaves PyJulia initialization failures uncaught')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
