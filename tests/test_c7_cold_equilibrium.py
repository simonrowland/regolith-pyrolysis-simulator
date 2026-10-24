"""A proved cold closure must not repeat an unchanged condensed solve."""
import pytest

from simulator.melt_backend.base import EquilibriumResult
from tests.magemin_test_support import close_test_owned_magemin_backend
from tests.test_c7_closure_routing import simulation

pytestmark = [pytest.mark.serial, pytest.mark.xdist_group('serial')]


def test_cold_step_queries_condensed_backend_once(monkeypatch):
    sim = simulation('lunar_mare_low_ti', 'C0')
    calls = []

    def condensed(**kwargs):
        calls.append(kwargs)
        return EquilibriumResult(
            temperature_C=kwargs['temperature_C'], pressure_bar=kwargs['pressure_bar'],
            fO2_log=kwargs['fO2_log'], liquid_fraction=0.0, status='ok',
        )

    monkeypatch.setattr(sim.backend, 'is_available', lambda: True)
    monkeypatch.setattr(sim.backend, 'equilibrate', condensed)
    try:
        sim.step()
        assert len(calls) == 1
        assert sim._last_surface_oxygen_diagnostic['oxygen_flux_mol_m2_s'] == 0.0
        assert not sim._last_surface_oxygen_diagnostic['source_terms_mol_m2_s']
    finally:
        close_test_owned_magemin_backend(sim)
