"""Admission output pin captured before sharing the round-trip policy."""
import pytest

from simulator.pumping_cost import process_pressure_state


@pytest.mark.parametrize('achieved,status', [
    (1499.9999999999998, 'ok'), (1500.0000000000005, 'ok'),
    (1500.000001, 'infeasible'),
])
def test_process_pressure_roundtrip_admission_pin(achieved, status):
    state = process_pressure_state(
        requested_pressure_pa=1500.0, ambient_pressure_pa=600.0,
        chamber_mol_per_s=1.0, chamber_temperature_K=1800.0,
        pumping={}, transport_pressure_pa=achieved,
    )
    assert state['status'] == status
    assert state['achieved_pressure_pa'].hex() == max(1500.0, achieved).hex()
