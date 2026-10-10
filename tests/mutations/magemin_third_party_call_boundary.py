"""Prove local MAGEMin call arguments stay outside engine-failure tagging."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
TEST = (
    'tests/test_magemin_backend.py::'
    'test_magemin_bridge_argument_preparation_error_propagates'
)
ORIGINAL = '''                call_kwargs = {
                    'composition': bulk_projection.composition_wt_pct,
                    'T_C': temperature_C,
                    'T_K': temperature_K,
                    'P_GPa': pressure_GPa,
                    'P_kbar': pressure_kbar,
                    'log_fO2': fO2_log,
                    'database': self._database,
                }
                try:
                    return fn(**call_kwargs)'''
MUTATION = '''                try:
                    call_kwargs = {
                        'composition': bulk_projection.composition_wt_pct,
                        'T_C': temperature_C,
                        'T_K': temperature_K,
                        'P_GPa': pressure_GPa,
                        'P_kbar': pressure_kbar,
                        'log_fO2': fO2_log,
                        'database': self._database,
                    }
                    return fn(**call_kwargs)'''


def main() -> int:
    source = SOURCE.read_text()
    if source.count(ORIGINAL) != 1:
        raise SystemExit('expected one external PyMAGEMin call boundary')
    SOURCE.write_text(source.replace(ORIGINAL, MUTATION, 1))
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
        or 'bridge_argument_preparation_error_propagates' not in output
    ):
        raise SystemExit(
            'argument-boundary mutation did not break the regression'
        )
    print(
        'PASS: moving local argument preparation into the external catch '
        'breaks the regression'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
