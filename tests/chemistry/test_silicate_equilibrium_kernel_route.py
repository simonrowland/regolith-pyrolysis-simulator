from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import EquilibriumResult


def _load_data_yaml(name):
    return yaml.safe_load(
        (Path(__file__).parent.parent.parent / "data" / name).read_text())


class AlphaMELTSBackend:
    def __init__(self) -> None:
        self._mode = 'python_api'
        self._available = True
        self.calls = []
        self.last_result = None

    def initialize(self, config):
        return True

    def is_available(self):
        return self._available

    def get_engine_version(self):
        return 'fake-alphamelts-kernel-route'

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        result = EquilibriumResult(
            temperature_C=float(kwargs['temperature_C']),
            pressure_bar=float(kwargs['pressure_bar']),
            phases_present=['liquid', 'olivine'],
            phase_masses_kg={'liquid': 0.875, 'olivine': 0.125},
            liquid_fraction=0.875,
            liquid_composition_wt_pct={
                'SiO2': 44.0,
                'TiO2': 2.0,
                'Al2O3': 15.0,
                'FeO': 12.0,
                'MgO': 10.0,
                'CaO': 12.0,
                'Na2O': 3.0,
                'K2O': 2.0,
            },
            activity_coefficients={'SiO2': 0.93, 'FeO': 1.07},
            fO2_log=float(kwargs['fO2_log']),
            warnings=['AlphaMELTS liquidus_C=1285.0'],
            status='ok',
        )
        self.last_result = result
        return result


def _build_sim(backend):
    feedstocks = _load_data_yaml("feedstocks.yaml")
    setpoints = _load_data_yaml("setpoints.yaml")
    vapor_pressures = _load_data_yaml("vapor_pressures.yaml")
    setpoints = dict(setpoints)
    kernel_config = dict(setpoints.get("chemistry_kernel", {}) or {})
    kernel_config["allow_fallback_vapor"] = True
    setpoints["chemistry_kernel"] = kernel_config

    sim = PyrolysisSimulator(backend, setpoints, feedstocks, vapor_pressures)
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.melt.temperature_C = 25.0
    return sim


def test_lunar_silicate_equilibrium_uses_kernel_and_matches_legacy_result():
    backend = AlphaMELTSBackend()
    sim = _build_sim(backend)

    before_mol = sim.atom_ledger.mol_by_account()
    physical_pressure_bar = sim.melt.p_total_mbar / 1000.0
    result = sim._get_equilibrium()

    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert 'composition_mol_by_account' in call
    assert 'composition_mol' not in call
    assert physical_pressure_bar < 1.0
    assert call['pressure_bar'] == pytest.approx(physical_pressure_bar)
    assert sim.atom_ledger.mol_by_account() == before_mol

    legacy = backend.last_result
    assert legacy is not None
    assert result.temperature_C == pytest.approx(legacy.temperature_C)
    assert result.pressure_bar == pytest.approx(legacy.pressure_bar)
    assert result.phases_present == legacy.phases_present
    assert result.phase_masses_kg == pytest.approx(legacy.phase_masses_kg)
    assert result.liquid_fraction == pytest.approx(legacy.liquid_fraction)
    assert result.liquid_composition_wt_pct == pytest.approx(
        legacy.liquid_composition_wt_pct)
    assert result.activity_coefficients == pytest.approx(
        legacy.activity_coefficients)
    assert result.fO2_log == pytest.approx(legacy.fO2_log)
    assert result.ledger_transition is None

    diagnostic = getattr(result, 'alphamelts_diagnostics')
    assert 'physical_overhead_pressure_bar' not in diagnostic[
        'backend_diagnostics'
    ]
    assert 'condensed_phase_reference_pressure_bar' not in diagnostic[
        'backend_diagnostics'
    ]
    assert diagnostic['liquidus_T_K'] == pytest.approx(1558.15)
    assert diagnostic['phase_masses_kg'] == pytest.approx(
        legacy.phase_masses_kg)

    trace = sim._chem_kernel.planner.shadow_trace
    shadow_dispatches = [
        event for event in trace
        if event.get('event') == 'shadow_dispatch'
    ]
    assert shadow_dispatches
    assert all(
        event.get('intent') == 'silicate_equilibrium'
        for event in shadow_dispatches
    )
    assert all(
        event.get('intent') != 'silicate_liquidus'
        for event in trace
    )
