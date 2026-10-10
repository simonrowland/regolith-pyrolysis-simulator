#!/usr/bin/env python3
"""Restore the old liquidus gate catch and prove the KeyError pin detects it."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / 'simulator' / 'core.py'
TEST = (
    'tests/chemistry/test_evaporation_freeze_gate.py::'
    'test_redox_gate_propagates_keyerror_from_curve_notice_recording'
)
NARROW_HANDLER = """            except EvaporationFluxRefusal:
                raise
            except liquidus_provider_failures as exc:
                # These are the declared liquidus-provider failures:
                # ProviderUnavailableError, LiquidusSampleError, the typed
                # ThermoEngine failures, and EngineWorkerTimeout. They retain
                # their classified floor fallback. EvaporationFluxRefusal is
                # a composition-projected refusal; MeltCompositionError,
                # IntentResultStatusError, and programming errors propagate.
"""
OLD_HANDLER = """            except MeltCompositionError:
                raise
            except IntentResultStatusError:
                raise
            except Exception as exc:  # noqa: BLE001 - optional liquidus engines
                if 'composition_projected' in str(exc):
                    raise
"""


def main() -> int:
    original = CORE.read_bytes()
    old_text = NARROW_HANDLER.encode()
    if original.count(old_text) != 1:
        raise SystemExit('could not identify exactly one narrow gate handler')

    try:
        CORE.write_bytes(original.replace(old_text, OLD_HANDLER.encode(), 1))
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                TEST,
                '-q',
                '-n',
                '0',
                '--timeout=300',
                '--tb=short',
            ],
            cwd=ROOT,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        print(result.stdout, end='')
        if result.returncode == 0 or 'DID NOT RAISE' not in result.stdout:
            raise SystemExit('broad-except mutation did not trip the regression')
        print('mutation detected: the broad catch swallowed the injected KeyError')
        return 0
    finally:
        CORE.write_bytes(original)


if __name__ == '__main__':
    raise SystemExit(main())
