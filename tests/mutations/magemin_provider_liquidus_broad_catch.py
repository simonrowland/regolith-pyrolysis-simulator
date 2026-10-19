"""Prove the MAGEMin liquidus regression rejects a restored broad catch."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from _pytest_assertion_outcome import pytest_assertion_failed


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'engines/magemin/provider.py'
TEST = ROOT / 'tests/test_magemin_shadow_provider.py'
TEST_NODE = f'{TEST.relative_to(ROOT)}::test_liquidus_finder_keyerror_propagates'
CALL = '''        return finder(
            pressure_bar=float(request.pressure_bar),
            fO2_log=(float(fO2_log) if fO2_log is not None else -9.0),
            redox_state=redox_state,
            composition_mol_by_account=composition_mol_by_account,
            species_formula_registry=species_registry,
        )
'''
BROAD = '''        try:
            return finder(
                pressure_bar=float(request.pressure_bar),
                fO2_log=(float(fO2_log) if fO2_log is not None else -9.0),
                redox_state=redox_state,
                composition_mol_by_account=composition_mol_by_account,
                species_formula_registry=species_registry,
            )
        except Exception as exc:  # noqa: BLE001 - mutation restores old handler
            from simulator.melt_backend.liquidus import LiquidusSolidusResult

            return LiquidusSolidusResult(
                status='not_converged',
                warnings=(f'MAGEMin liquidus finder failed: {exc}',),
            )
'''


def main() -> int:
    source = SOURCE.read_text()
    if source.count(CALL) != 1:
        raise SystemExit('expected one direct MAGEMin liquidus finder call')

    try:
        SOURCE.write_text(source.replace(CALL, BROAD, 1))
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT)
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-q',
                '-n',
                '0',
                '-p',
                'no:cacheprovider',
                '--basetemp=/private/tmp/claude-501/bugs1019-w2-00-pyt',
                TEST_NODE,
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
    if not pytest_assertion_failed(
        returncode=result.returncode,
        output=output,
        node_id=TEST_NODE,
        expected_reason="DID NOT RAISE <class 'KeyError'>",
    ):
        raise SystemExit(
            'mutation was not killed by the focused assertion failure'
        )
    print('PASS: restored broad catch makes the focused regression test red')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
