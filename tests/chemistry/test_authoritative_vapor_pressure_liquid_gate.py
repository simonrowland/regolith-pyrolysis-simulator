from __future__ import annotations

import types

import pytest

from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel.dto import IntentResult
from simulator.core import PyrolysisSimulator
from simulator.evaporation import EvaporationFluxRefusal
from simulator.melt_backend.base import EquilibriumResult
from simulator.vapour_rail.batch import (
    FLUX_ACTIVATION_EPOCH_PRE_RG,
    FluxEligible,
    FluxActivationContext,
    PressureValue,
    VapourAnswer,
    VapourBatch,
)
from simulator.vapour_rail.request import (
    RequestRule,
    VapourResolveState,
    resolve_vapour_batch,
)


def _resolved_liquid_gate_batch(*, parent_inventory_mol: float):
    rule = RequestRule(
        species_id='K',
        source_account='process.cleaned_melt',
        parent_species_ids=frozenset({'K2O'}),
        required_source_atoms=frozenset({'K', 'O'}),
        solve_group_id='test:K',
        applicability_predicate='applicable',
        request_rule_kind='source_inventory_present',
        origin='catalog',
        formula_id='K',
        has_pressure_evaluator=False,
        has_alpha=False,
        has_route=False,
    )
    ledger = {
        'process.cleaned_melt': (
            {'K2O': parent_inventory_mol} if parent_inventory_mol > 0.0 else {}
        )
    }
    return resolve_vapour_batch(
        rules=(rule,),
        ledger_snapshot=ledger,
        state=VapourResolveState(temperature_K=1873.15),
        catalog_species={},
        flux_activation_context=FluxActivationContext(
            epoch=FLUX_ACTIVATION_EPOCH_PRE_RG
        ),
    )


def _with_empty_vapour_batch(sim):
    sim.setpoints = {}
    sim.vapor_pressures = {}
    sim._last_vapour_batch_resolve_error = {}
    empty_batch = _resolved_liquid_gate_batch(parent_inventory_mol=0.0)
    sim._resolve_evaporation_vapour_batch = (
        lambda equilibrium, temperature_K, effective_pressure_source: empty_batch
    )
    return sim


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
    return _with_empty_vapour_batch(sim), calls


def test_authoritative_vapor_pressure_no_liquid_gate_zeroes_evaporation():
    sim, calls = _sim_with_vapor_dispatch({'Na': 10.0})
    sim._last_vapour_batch = object()
    sim._last_vapour_batch_report = {'stale': True}
    sim._last_vapour_batch_flux_overlay = {'stale': True}
    sim._last_vapour_batch_resolve_error = {'stale': True}
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
    assert sim._last_vapour_batch is None
    assert sim._last_vapour_batch_report is None
    assert sim._last_vapour_batch_flux_overlay == {}
    assert sim._last_vapour_batch_resolve_error == {}


@pytest.mark.parametrize('temperature_C', [0.0, 1000.0, 1600.0])
def test_active_liquid_empty_vapor_pressures_fail_loud(temperature_C):
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=temperature_C),
        setpoints={},
        vapor_pressures={},
        _last_vapor_pressure_diagnostic={
            'vapor_pressure_zero_reason': 'kernel_ok_empty',
        },
        _last_vapour_batch_resolve_error={},
    )
    refused_batch = _resolved_liquid_gate_batch(parent_inventory_mol=1.0)
    sim._resolve_evaporation_vapour_batch = (
        lambda equilibrium, temperature_K, effective_pressure_source: refused_batch
    )
    result = EquilibriumResult(
        temperature_C=temperature_C,
        pressure_bar=1e-6,
        liquid_fraction=1.0,
        vapor_pressures_Pa={},
        status='ok',
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        PyrolysisSimulator._calculate_evaporation(sim, result)
    assert exc_info.value.reason == 'vapour_batch_no_debiting_pressure_outcome'
    assert refused_batch.requested_species_ids == frozenset({'K'})
    assert refused_batch.channel('K').is_refused


def test_kernel_ok_empty_with_proven_empty_request_is_physical_zero():
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
    assert sim._last_vapour_batch.requested_species_ids == frozenset()
    assert sim._last_evaporation_flux_diagnostic['reason'] == (
        'no_volatile_species_or_positive_parent_activity'
    )


def test_no_volatile_species_allows_active_liquid_zero_evaporation():
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=1600.0))
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
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=500.0))
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


def test_pre_rg_subthreshold_effective_source_zero_requires_answered_batch():
    sim = types.SimpleNamespace(
        melt=types.SimpleNamespace(temperature_C=500.0),
        setpoints={},
        vapor_pressures={},
        _last_vapor_pressure_diagnostic={
            'vapor_pressure_zero_reason': 'kernel_ok_empty',
        },
        _last_vapour_batch_resolve_error={},
    )
    pressure = PressureValue(pa=1.0)
    answered_batch = VapourBatch(
        requested_species_ids=frozenset({'K'}),
        channels_by_species={
            'K': VapourAnswer(
                species_id='K',
                pressure=pressure,
                selected_runtime_pressure=pressure,
                flux=FluxEligible(alpha_ref='test:K'),
                source_label='test',
                formula_id='K',
                source_account='process.cleaned_melt',
                solve_group_id='test:K',
                state_fingerprint='test',
                validation_status='validated',
            )
        },
        flux_active_species_ids=frozenset(),
    )
    sim._resolve_evaporation_vapour_batch = (
        lambda equilibrium, temperature_K, effective_pressure_source: answered_batch
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
    assert sim._last_vapour_batch_flux_overlay[
        'effective_pressure_zero_reason'
    ] == 'pre_rg_backend_below_effective_pressure_threshold'


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
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=1600.0))
    )
    result = types.SimpleNamespace(
        liquid_fraction=float('nan'),
        vapor_pressures_Pa={},
        diagnostics={},
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        PyrolysisSimulator._calculate_evaporation(sim, result)
    assert exc_info.value.reason == 'vapour_batch_no_debiting_pressure_outcome'

    divergence = sim._last_evaporation_flux_diagnostic[
        'melt_regime_predicate_divergences'
    ][0]
    assert divergence['site'] == (
        'evaporation.empty_vapor_pressure.liquid_fraction'
    )
    assert divergence['effective_regime'] == 'partial'
    assert divergence['liquid_fraction_invalid'] == 'non_finite'


def test_empty_vapor_pressure_string_zero_preserves_legacy_false_gate():
    sim = _with_empty_vapour_batch(
        types.SimpleNamespace(melt=types.SimpleNamespace(temperature_C=1600.0))
    )
    result = types.SimpleNamespace(
        liquid_fraction="0",
        vapor_pressures_Pa={},
        diagnostics={},
    )

    with pytest.raises(EvaporationFluxRefusal) as exc_info:
        PyrolysisSimulator._calculate_evaporation(sim, result)
    assert exc_info.value.reason == 'vapour_batch_no_debiting_pressure_outcome'

    assert sim._last_evaporation_flux_diagnostic['reason'] == (
        'vapour_batch_no_debiting_pressure_outcome'
    )
    assert (
        'melt_regime_predicate_divergences'
        not in sim._last_evaporation_flux_diagnostic
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
