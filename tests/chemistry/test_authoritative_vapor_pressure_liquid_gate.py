from __future__ import annotations

import types

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentResult
from simulator.core import PyrolysisSimulator
from simulator.evaporation import EvaporationMixin
from simulator.melt_backend.base import EquilibriumResult

# DESIGN-REV5 §1.2 / §7.3 U4 / VR-11 / a9a46cf: empty or incomplete batch is a
# typed outcome (resolve error / refuse silent-zero), never a missing method
# that fail-opens before the vapour-batch gate.


def _attach_vapour_batch_resolve(sim) -> None:
    """Bind VR-11 batch resolve on harness SimpleNamespaces.

    Without ``build_vapour_batch``, resolve records a typed
    ``vapour_batch_builder_missing`` error and returns None — empty flux map,
    no live-map fallthrough.
    """

    melt = getattr(sim, 'melt', None)
    if melt is not None and not hasattr(melt, 'p_total_mbar'):
        melt.p_total_mbar = 0.0
    sim._resolve_evaporation_vapour_batch = types.MethodType(
        EvaporationMixin._resolve_evaporation_vapour_batch,
        sim,
    )


def _sim_with_vapor_dispatch(
    vapor_pressures: dict[str, float],
    vapor_sources: dict[str, str] | None = None,
):
    calls = []
    source_by_species = vapor_sources or {
        species: 'builtin_authoritative'
        for species in vapor_pressures
    }

    def _dispatch_only(intent, **kwargs):
        calls.append((intent, kwargs))
        return IntentResult(
            intent=ChemistryIntent.VAPOR_PRESSURE,
            status='ok',
            diagnostic={
                'vapor_pressures_Pa': dict(vapor_pressures),
                'vapor_pressures_source': dict(source_by_species),
            },
        )

    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0, p_total_mbar=0.0),
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
    _attach_vapour_batch_resolve(sim)
    return sim, calls


def test_authoritative_vapor_pressure_no_liquid_gate_zeroes_evaporation():
    # DESIGN-REV5 §1.2 / VR-11: no_liquid_phase remains authorized physical zero
    # after batch resolve; incomplete batch must not AttributeError fail-open.
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
    overlay = sim._last_vapour_batch_flux_overlay
    assert overlay['batch_present'] is False
    assert overlay['selection_source'] == 'typed_failure_resolution_error'
    assert overlay['detail']['reason'] == 'vapour_batch_builder_missing'


def test_active_liquid_empty_vapor_pressures_fail_loud():
    # DESIGN-REV5 §1.2 rule 5 / a9a46cf: active melt + empty batch map refuses
    # silent-zero (typed RuntimeError; tighter than pre-cutover empty-map pin).
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0, p_total_mbar=0.0),
    )
    _attach_vapour_batch_resolve(sim)
    result = EquilibriumResult(
        temperature_C=1600.0,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r'empty vapour_batch_flux_pressures_Pa.*refusing silent-zero'
        ),
    ):
        PyrolysisSimulator._calculate_evaporation(sim, result)
    assert sim._last_vapour_batch_flux_overlay['selection_source'] == (
        'typed_failure_resolution_error'
    )


def test_kernel_ok_empty_allows_active_liquid_zero_evaporation():
    # DESIGN-REV5 §1.2 / VR-11 / a9a46cf: kernel_ok_empty still authorizes
    # physical-zero flux, but only *after* typed batch resolve — never via a
    # missing-resolve AttributeError fail-open (the closed hazard).
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
    overlay = sim._last_vapour_batch_flux_overlay
    assert overlay['batch_present'] is False
    assert overlay['selection_source'] == 'typed_failure_resolution_error'
    assert overlay['detail']['reason'] == 'vapour_batch_builder_missing'
    assert overlay['shadow_outcome'] == 'resolution_error'


def test_no_volatile_species_allows_active_liquid_zero_evaporation():
    # DESIGN-REV5 §1.2 / VR-11: authorized zero_reason after typed batch resolve.
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0, p_total_mbar=0.0),
    )
    _attach_vapour_batch_resolve(sim)
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
    assert sim._last_vapour_batch_flux_overlay['selection_source'] == (
        'typed_failure_resolution_error'
    )


def test_subthreshold_empty_vapor_pressures_remain_physical_zero():
    # DESIGN-REV5 §1.2 / VR-11: sub-1050 C empty batch remains physical zero.
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=500.0, p_total_mbar=0.0),
    )
    _attach_vapour_batch_resolve(sim)
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


def test_authoritative_vapor_pressure_invalid_liquid_fraction_still_fails_loud():
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5})
    result = types.SimpleNamespace(
        liquid_fraction=float('nan'),
        vapor_pressures_Pa={'Na': 3.0},
        vapor_pressures_source={'Na': 'backend_pre_kernel'},
    )

    with pytest.raises(RuntimeError, match='liquid_fraction_invalid'):
        PyrolysisSimulator._refresh_vapor_pressures_from_kernel(sim, result)

    assert calls == []


def test_empty_vapor_pressure_invalid_liquid_fraction_preserves_false_gate():
    # DESIGN-REV5 §1.2 / a9a46cf: empty batch refuse message is batch-keyed;
    # regime divergence diagnostic still recorded before the typed raise.
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0, p_total_mbar=0.0),
    )
    _attach_vapour_batch_resolve(sim)
    result = types.SimpleNamespace(
        liquid_fraction=float('nan'),
        vapor_pressures_Pa={},
        diagnostics={},
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r'empty vapour_batch_flux_pressures_Pa.*refusing silent-zero'
        ),
    ):
        PyrolysisSimulator._calculate_evaporation(sim, result)

    divergence = sim._last_evaporation_flux_diagnostic[
        'melt_regime_predicate_divergences'
    ][0]
    assert divergence['site'] == (
        'evaporation.empty_vapor_pressure.liquid_fraction'
    )
    assert divergence['effective_regime'] == 'partial'
    assert divergence['liquid_fraction_invalid'] == 'non_finite'
    assert sim._last_vapour_batch_flux_overlay['selection_source'] == (
        'typed_failure_resolution_error'
    )


def test_empty_vapor_pressure_string_zero_preserves_legacy_false_gate():
    # DESIGN-REV5 §1.2 / a9a46cf: string "0" is not authorized liquid-zero;
    # empty batch refuses silent-zero (typed), no regime diagnostic side path.
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=1600.0, p_total_mbar=0.0),
    )
    _attach_vapour_batch_resolve(sim)
    result = types.SimpleNamespace(
        liquid_fraction="0",
        vapor_pressures_Pa={},
        diagnostics={},
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r'empty vapour_batch_flux_pressures_Pa.*refusing silent-zero'
        ),
    ):
        PyrolysisSimulator._calculate_evaporation(sim, result)

    assert not hasattr(sim, '_last_evaporation_flux_diagnostic')
    assert sim._last_vapour_batch_flux_overlay['selection_source'] == (
        'typed_failure_resolution_error'
    )


def test_kernel_refresh_preserves_per_species_source_labels():
    source = (
        'vaporock_backsolved_curve_fit:'
        'backsolved_vaporock_curve_fit'
    )
    sim, calls = _sim_with_vapor_dispatch({'Na': 12.5}, {'Na': source})
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
    assert result.vapor_pressures_source == {'Na': source}
    assert sim._last_vapor_pressure_diagnostic['vapor_pressures_source'] == {
        'Na': source,
    }


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
