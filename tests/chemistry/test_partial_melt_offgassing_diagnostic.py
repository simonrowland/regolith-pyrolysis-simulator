"""Diagnostic-only partial-melt offgassing instrumentation."""

from types import SimpleNamespace

import pytest

from engines.builtin.vapor_pressure import BuiltinVaporPressureProvider
from simulator.chemistry.kernel import ChemistryIntent
from simulator.chemistry.kernel import IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.melt_backend.base import EquilibriumResult
from simulator.runner import build_per_hour_summary
from simulator.vapour_rail.batch import (
    FluxEligible,
    PressureValue,
    VapourAnswer,
    VapourBatch,
)
from tests.chemistry.conftest import _build_sim


def _sim(vapor_pressure_data, feedstocks_data, setpoints_data):
    sim = _build_sim(
        'lunar_mare_low_ti',
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.temperature_C = 1150.0
    return sim


def _install_flux_dispatch(monkeypatch, sim, rates):
    def fake_dispatch_only(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': dict(rates)},
            )
        raise AssertionError(f'unexpected intent: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch_only)


def _install_eligible_vapour_batch(sim):
    """Typed VR-11 seam: equilibrium Pa become flux-eligible batch answers."""

    sim._last_vapour_batch_resolve_error = {}

    def _resolve(equilibrium, *, temperature_K, effective_pressure_source):
        del temperature_K
        channels = {
            species_id: VapourAnswer(
                species_id=species_id,
                pressure=PressureValue(pa=float(pressure_pa)),
                selected_runtime_pressure=PressureValue(pa=float(pressure_pa)),
                flux=FluxEligible(alpha_ref=f"alpha:{species_id}"),
                source_label="test_partial_melt_passthrough",
                formula_id=species_id,
                source_account="process.cleaned_melt",
                solve_group_id=f"test:{species_id}",
                state_fingerprint="state:test",
                validation_status="pending_validation",
            )
            for species_id, pressure_pa in equilibrium.vapor_pressures_Pa.items()
        }
        return VapourBatch(
            requested_species_ids=frozenset(channels),
            channels_by_species=channels,
            flux_active_species_ids=frozenset(channels),
        )

    sim._resolve_evaporation_vapour_batch = _resolve
    return sim


def test_partial_melt_offgassing_diagnostic_warns_on_partition_fallback(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _sim(vapor_pressure_data, feedstocks_data, setpoints_data)
    _install_flux_dispatch(monkeypatch, sim, {'Na': 0.01, 'K': 0.002})
    _install_eligible_vapour_batch(sim)

    equilibrium = EquilibriumResult(
        temperature_C=1150.0,
        pressure_bar=1e-8,
        liquid_fraction=0.2,
        vapor_pressures_Pa={'Na': 1.0, 'K': 0.25},
        diagnostics={'solidus_T_C': 1000.0, 'liquidus_T_C': 1300.0},
    )

    flux = sim._calculate_evaporation(equilibrium)

    assert flux.species_kg_hr == {'Na': 0.01, 'K': 0.002}
    diagnostic = sim._last_partial_melt_offgassing_diagnostic
    assert diagnostic['status'] == 'UNCERTIFIED_PARAMETERIZED_ESTIMATE'
    assert diagnostic['melt_regime'] == 'partial'
    assert diagnostic['melt_fraction_F'] == pytest.approx(0.2)
    assert diagnostic['golden_authoritative'] is False
    assert diagnostic['warnings']
    assert diagnostic['component_details']['NaO0.5'][
        'liquid_composition_source'
    ] == 'analytical_batch_partition_fallback'
    assert diagnostic['p_ratio_partial_over_bulk']['Na'] > 1.0
    assert diagnostic['p_ratio_partial_over_bulk']['K'] > 1.0


def test_partial_melt_offgassing_diagnostic_uses_phase_engine_liquid_comp(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _sim(vapor_pressure_data, feedstocks_data, setpoints_data)
    _install_flux_dispatch(monkeypatch, sim, {'Na': 0.01, 'K': 0.002})
    _install_eligible_vapour_batch(sim)

    equilibrium = EquilibriumResult(
        temperature_C=1150.0,
        pressure_bar=1e-8,
        liquid_fraction=0.3,
        liquid_composition_wt_pct={
            'SiO2': 80.0,
            'Al2O3': 17.0,
            'Na2O': 2.0,
            'K2O': 1.0,
        },
        vapor_pressures_Pa={'Na': 1.0, 'K': 0.25},
        diagnostics={'solidus_T_C': 1000.0, 'liquidus_T_C': 1300.0},
    )

    sim._last_vapor_pressure_diagnostic = {
        'vapor_pressure_numerator_provenance': {
            'Na': {'melt_oxide_X_single_cation': 0.01},
            'K': {'melt_oxide_X_single_cation': 0.002},
        },
    }

    sim._calculate_evaporation(equilibrium)

    diagnostic = sim._last_partial_melt_offgassing_diagnostic
    assert diagnostic['status'] == 'ENGINE_DERIVED'
    assert diagnostic['liquid_composition_source'] == (
        'phase_engine:equilibrium.liquid_composition_wt_pct'
    )
    assert not diagnostic['warnings']
    assert diagnostic['component_details']['NaO0.5'][
        'liquid_composition_source'
    ] == 'phase_engine:equilibrium.liquid_composition_wt_pct'
    assert diagnostic['p_ratio_partial_over_bulk']['Na'] > 1.0
    assert diagnostic['p_ratio_partial_over_bulk']['K'] > 1.0


def test_partial_melt_offgassing_diagnostic_stays_out_of_runner_summary(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _sim(vapor_pressure_data, feedstocks_data, setpoints_data)
    _install_flux_dispatch(monkeypatch, sim, {'Na': 0.01})

    request = IntentRequest(
        intent=ChemistryIntent.VAPOR_PRESSURE,
        account_view=ProviderAccountView(
            accounts={
                'process.cleaned_melt': sim.atom_ledger.mol_by_account(
                    'process.cleaned_melt'
                )
            },
            species_formula_registry=sim.species_formula_registry,
        ),
        temperature_C=1150.0,
        pressure_bar=1.0,
        fO2_log=0.0,
        control_inputs={'pO2_bar': 1.0, 'intrinsic_fO2_log': 0.0},
    )
    provider_result = BuiltinVaporPressureProvider(vapor_pressure_data).dispatch(
        request
    )
    assert provider_result.status == 'ok'
    vapor_diagnostic = dict(provider_result.diagnostic)
    fe_provenance = vapor_diagnostic[
        'vapor_pressure_numerator_provenance'
    ]['Fe']
    assert fe_provenance['activity_standard_state_conversion'][
        'conversion_performed'
    ] is True
    sim._last_vapor_pressure_diagnostic = vapor_diagnostic

    equilibrium = EquilibriumResult(
        temperature_C=1150.0,
        pressure_bar=1e-8,
        liquid_fraction=0.2,
        activity_coefficients=vapor_diagnostic['activities'],
        vapor_pressures_Pa=vapor_diagnostic['vapor_pressures_Pa'],
        diagnostics={'solidus_T_C': 1000.0, 'liquidus_T_C': 1300.0},
    )

    sim._calculate_evaporation(equilibrium)
    snapshot = sim._make_snapshot()
    snapshot.partial_melt_offgassing_diagnostic = dict(
        sim._last_partial_melt_offgassing_diagnostic
    )

    summary = build_per_hour_summary(sim, snapshot)

    assert snapshot.partial_melt_offgassing_diagnostic
    assert 'partial_melt_offgassing_diagnostic' not in summary
