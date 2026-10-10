"""Prove local liquidus sampler defects escape both sampling boundaries."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/liquidus.py'
FINDER_TARGET = '    raw = sample_fraction(*args, **kwargs)\n'
FINDER_MUTATION = '''    try:
        raw = sample_fraction(*args, **kwargs)
    except Exception as exc:
        raise LiquidusSampleError(
            'not_converged',
            (f'{type(exc).__name__}: {exc}',),
            {'exception_class': type(exc).__name__},
        ) from exc
'''
EC_TARGET = '''            raw_fraction, raw_composition = sample_liquid_state(
                float(temperature_C)
            )'''
EC_MUTATION = '''            try:
                raw_fraction, raw_composition = sample_liquid_state(
                    float(temperature_C)
                )
            except Exception as exc:
                raise LiquidusSampleError(
                    'not_converged',
                    (f'{type(exc).__name__}: {exc}',),
                    {'exception_class': type(exc).__name__},
                ) from exc'''
TESTS = (
    'tests/test_alphamelts_backend.py::'
    'test_alphamelts_liquidus_propagates_local_result_processing_error',
    'tests/test_liquidus_finder.py::'
    'test_ec_path_propagates_local_sample_callback_error',
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(FINDER_TARGET) != 1 or original.count(EC_TARGET) != 1:
        raise RuntimeError('could not identify both liquidus sampling calls')
    mutated = original.replace(FINDER_TARGET, FINDER_MUTATION, 1)
    mutated = mutated.replace(EC_TARGET, EC_MUTATION, 1)
    try:
        SOURCE.write_text(mutated)
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
                *TESTS,
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
    if (
        result.returncode == 0
        or any(test not in output for test in TESTS)
        or 'DID NOT RAISE' not in output
    ):
        print('mutation did not make both local-sampler regressions fail')
        print('\n'.join(output.splitlines()[-20:]))
        return 1
    print('mutation rejected by both local-sampler regression tests')
    print('\n'.join(output.splitlines()[-20:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
