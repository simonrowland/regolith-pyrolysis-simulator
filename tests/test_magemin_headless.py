"""MAGEMin launch isolation and captured numeric output contracts."""

from pathlib import Path

from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.magemin import MAGEMinBackend


def test_captured_phase_assemblage_numeric_pin():
    stdout = (
        Path(__file__).parent / 'fixtures' / 'magemin'
        / 'lfdrop-diag-1-1700-phase-mode.stdout'
    ).read_text()
    phases = MAGEMinBackend._parse_subprocess_stdout(stdout)
    result = EquilibriumResult(liquid_fraction=0.0)
    MAGEMinBackend()._populate_result(result, {'phases': phases})

    # Pin the captured output before changing launch policy; no physics changes.
    assert result.phases_present == ['liq', 'fsp', 'crst', 'sph']
    assert result.liquid_fraction.hex() == '0x1.6f72127a6f8f9p-1'
    assert {key: value.hex() for key, value in result.phase_masses_kg.items()} == {
        'liq': '0x1.6f7121ab4b72cp-1',
        'fsp': '0x1.aee631f8a0903p-3',
        'crst': '0x1.43fe5c91d14e4p-5',
        'sph': '0x1.0941c8216c615p-5',
    }
