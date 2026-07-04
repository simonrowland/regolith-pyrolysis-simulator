from __future__ import annotations

import types

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentResult
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import EquilibriumResult


def _sim_with_vapor_dispatch(vapor_pressures: dict[str, float]):
    calls = []

    def _dispatch_only(intent, **kwargs):
        calls.append((intent, kwargs))
        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status='ok',
            diagnostic={
                'vapor_pressures_Pa': dict(vapor_pressures),
                'vapor_pressures_source': {
                    species: 'builtin_authoritative'
                    for species in vapor_pressures
                },
            },
        )

    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0),
        _allow_fallback_vapor=False,
        _commanded_pO2_bar=lambda: 1e-9,
        # #94 LIVE-PO2-SWEEP: kernel refresh now reads the shared vapor
        # transport-pO2 snapshot helper instead of commanded pO2 directly.
        _vapor_pressure_dispatch_pO2_bar=lambda: 1e-9,
        _compute_intrinsic_melt_fO2=lambda: -9.0,
        _dispatch_only=_dispatch_only,
        _kernel_vapor_pressure_source=(
            PyrolysisSimulator._kernel_vapor_pressure_source
        ),
        _vapor_pressure_values_agree=(
            PyrolysisSimulator._vapor_pressure_values_agree
        ),
    )
    return sim, calls


def test_authoritative_vapor_pressure_no_liquid_gate_zeroes_evaporation():
    sim, calls = _sim_with_vapor_dispatch({'Na': 10.0})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        phases_present=['olivine'],
        phase_masses_kg={'olivine': 1.0},
        liquid_fraction=0.0,
        vapor_pressures_Pa={'Na': 10.0},
        vapor_pressures_source={'Na': 'backend_spurious'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)
    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert calls == []
    assert result.vapor_pressures_Pa == {}
    assert result.vapor_pressures_source == {}
    assert sim._last_vapor_pressure_diagnostic['vapor_pressure_zero_reason'] == (
        'no_liquid_phase'
    )
    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0


def test_active_liquid_empty_vapor_pressures_fail_loud():
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0),
    )
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
    )

    with pytest.raises(RuntimeError, match='empty vapor_pressures_Pa'):
        PyrolysisSimulator._calculate_evaporation(sim, result)


def test_kernel_ok_empty_allows_active_liquid_zero_evaporation():
    sim, calls = _sim_with_vapor_dispatch({})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        phases_present=['liq'],
        phase_masses_kg={'liq': 1.0},
        liquid_fraction=1.0,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
        status='ok',
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)
    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {}
    assert sim._last_vapor_pressure_diagnostic['vapor_pressure_zero_reason'] == (
        'kernel_ok_empty'
    )
    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0


def test_no_volatile_species_allows_active_liquid_zero_evaporation():
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0),
    )
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
        diagnostics={'vapor_pressure_zero_reason': 'no_volatile_species'},
    )

    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0


def test_subthreshold_empty_vapor_pressures_remain_physical_zero():
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=500.0),
    )
    result = EquilibriumResult(
        temperature_C=500.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
    )

    flux = PyrolysisSimulator._calculate_evaporation(sim, result)

    assert flux.species_kg_hr == {}
    assert flux.total_kg_hr == 0.0


def test_authoritative_vapor_pressure_liquid_present_dispatch_unchanged():
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        phases_present=['liq', 'olivine'],
        phase_masses_kg={'liq': 0.25, 'olivine': 0.75},
        liquid_fraction=0.25,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {'Na': pytest.approx(12.5)}
    assert result.vapor_pressures_source == {'Na': 'builtin_authoritative'}


def test_authoritative_vapor_pressure_vapor_only_none_does_not_zero_gate():
    sim, calls = _sim_with_vapor_dispatch({'Na': 8.0})
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=None,
        phase_assemblage_available=False,
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
    )

    PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert [call[0] for call in calls] == [ChemistryIntent.VAPOR_PRESSURE]
    assert result.vapor_pressures_Pa == {'Na': pytest.approx(8.0)}
