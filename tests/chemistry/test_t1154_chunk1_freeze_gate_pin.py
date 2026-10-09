"""Pins for the freeze-gate scalar with the gate off and with it on.

The live evaporation flux pin stays in
``test_freeze_gate_default_off_leaves_evaporation_flux_unchanged``
(Na 7.5 kg/h, gate off) and
``test_freeze_gate_enabled_uses_ec_table_zero_mush_full``
(Na 0, 5 and 10 kg/h, gate on). The mass-balance closure pin stays the
nightly ``test_c2a_staged_freeze_gate_on_closes_mass_balance``, which
already runs both flags. Metallothermy still blocks only at an exact zero liquid fraction
(``test_c3_k_shuttle_primary_refuses_no_liquid_before_ellingham``,
``test_c3_na_shuttle_primary_refuses_no_liquid``,
``test_c6_mg_thermite_primary_refuses_no_liquid``) and a partial
fraction does not scale the yield
(``test_c3_na_shuttle_primary_reduces_feo_with_liquid_or_unknown``,
``test_c6_mg_thermite_primary_matches_legacy_stoich``). This module
pins the liquid fraction the extraction sites pass into that provider.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.evaporation import _PARTIAL_MELT_OFFGASSING_COMPONENTS
from simulator.melt_backend.base import EquilibriumResult
from simulator.state import CampaignPhase
from simulator.thermal_train import FiniteCapacity
from tests.chemistry.test_evaporation_freeze_gate import (
    _build_freeze_gate_sim,
    _install_freeze_gate_curve,
)
from tests.chemistry.test_partial_melt_offgassing_diagnostic import (
    _install_eligible_vapour_batch,
)

# Linear between solidus 1000 C (F = 0) and liquidus 1300 C (F = 1).
# 950 C is below the solidus, 1150 C is the midpoint, 1400 C is above.
_CURVE_PATH = ((1000.0, 0.0), (1300.0, 1.0))
# Stub handed to the flux provider. The shadow then multiplies by F and
# applies analytic inventory depletion, which is why the pinned rates sit
# a few parts in 1e6 under the stub.
_SHADOW_STUB_KG_HR = {'Na': 1.0e-6, 'K': 2.0e-6}
_SHADOW_FULL_KG_HR = {
    'K': 1.9999976545067087e-06,
    'Na': 9.999998359604834e-07,
}
_SHADOW_MIDPOINT_KG_HR = {
    'K': 9.99999413626448e-07,
    'Na': 4.999999589901186e-07,
}
_MELT_FRACTION_F = 0.2
_BULK_X = {'Na': 0.01, 'K': 0.02}
_VAPOR_PA = {'Na': 2.0, 'K': 0.5}
_FLUX_STUB_KG_HR = {'Na': 8.0, 'K': 3.0}


def _batch_enrichment(partition_coefficient: float, melt_fraction: float) -> float:
    """C_liquid / C_bulk from C_bulk = F*C_liquid + (1-F)*D*C_liquid."""
    return 1.0 / (
        partition_coefficient
        + melt_fraction * (1.0 - partition_coefficient)
    )


def _install_curve(sim) -> None:
    _install_freeze_gate_curve(sim, path=_CURVE_PATH)


def _shadow_rates(sim, monkeypatch) -> dict[str, float]:
    captured: dict[str, dict[str, float]] = {}

    def policy():
        return (
            FiniteCapacity(1.0),
            SimpleNamespace(
                accumulator_enabled=False,
                relief={
                    'k_relief_kg_hr_Pa': 1.0,
                    'p_open_Pa': 1.0e9,
                    'vessel_rating_Pa': 1.0e12,
                },
            ),
        )

    def dispatch(self, request):
        del self, request
        return SimpleNamespace(
            status='ok',
            diagnostic={'evaporation_flux_kg_hr': dict(_SHADOW_STUB_KG_HR)},
        )

    def solve_shadow(*, flux_kg_hr_at_partials, **kwargs):
        del kwargs
        captured['rates'] = flux_kg_hr_at_partials({})
        return SimpleNamespace(status='recorded')

    monkeypatch.setattr(sim, '_cold_train_capacity_policy', policy)
    monkeypatch.setattr(
        'engines.builtin.evaporation_flux.BuiltinEvaporationFluxProvider.dispatch',
        dispatch,
    )
    monkeypatch.setattr(
        'simulator.capacity_coupling.solve_capacity_shadow',
        solve_shadow,
    )
    # A proved zero liquid skips the batch-flux resolve. The freeze-gate
    # factor is a separate read of the installed curve.
    sim._compute_capacity_coupling_shadow(
        EquilibriumResult(
            liquid_fraction=0.0,
            vapor_pressures_Pa={'Fe': 1.0},
            status='ok',
        )
    )
    return captured['rates']


@pytest.mark.parametrize('enabled', (False, True), ids=('gate_off', 'gate_on'))
def test_capacity_shadow_multiplies_by_the_freeze_gate_factor(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    enabled,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=enabled,
    )
    _install_curve(sim)

    expected = {
        950.0: {} if enabled else _SHADOW_FULL_KG_HR,
        1150.0: _SHADOW_MIDPOINT_KG_HR if enabled else _SHADOW_FULL_KG_HR,
        1400.0: _SHADOW_FULL_KG_HR,
    }
    for temperature_C, rates in expected.items():
        sim.melt.temperature_C = temperature_C
        got = _shadow_rates(sim, monkeypatch)
        assert got.keys() == rates.keys()
        for species, rate in rates.items():
            assert got[species] == pytest.approx(rate, rel=0.0, abs=0.0)
        if enabled and temperature_C == 1150.0:
            for species, full in _SHADOW_FULL_KG_HR.items():
                assert got[species] / full == pytest.approx(0.5, rel=1e-5)


def test_redox_capacity_scales_with_the_curve_whether_or_not_the_gate_is_enabled(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Today's redox site multiplies by the curve and does not read the flag."""

    def full_capacity(**_kwargs):
        return 12.0

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError('cached redox capacity scaling must not dispatch')

    for enabled in (False, True):
        sim = _build_freeze_gate_sim(
            vapor_pressure_data,
            feedstocks_data,
            setpoints_data,
            enabled=enabled,
        )
        _install_curve(sim)
        monkeypatch.setattr(
            sim, '_melt_redox_capacity_mol_per_ln_fO2', full_capacity
        )
        monkeypatch.setattr(sim, '_dispatch_only', fail_dispatch)
        assert sim._freeze_gate_enabled() is enabled
        assert sim._melt_redox_source_capacity_mol_per_ln_fO2(
            fO2_log=-9.0,
            T_K=999.0 + 273.15,
        ) == 0.0
        assert sim._melt_redox_source_capacity_mol_per_ln_fO2(
            fO2_log=-9.0,
            T_K=1150.0 + 273.15,
        ) == pytest.approx(6.0)
        assert sim._last_melt_redox_liquid_fraction_diagnostic[
            'liquid_fraction'
        ] == pytest.approx(0.5)
        assert sim._melt_redox_source_capacity_mol_per_ln_fO2(
            fO2_log=-9.0,
            T_K=1300.0 + 273.15,
        ) == pytest.approx(12.0)


@pytest.mark.parametrize('enabled', (False, True), ids=('gate_off', 'gate_on'))
def test_nak_diagnostic_keeps_the_batch_enrichment_while_flux_follows_the_gate(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    enabled,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=enabled,
    )
    sim.melt.temperature_C = 1150.0
    _install_curve(sim)
    _install_eligible_vapour_batch(sim)
    sim._last_vapor_pressure_diagnostic = {
        'vapor_pressure_numerator_provenance': {
            'Na': {'melt_oxide_X_single_cation': _BULK_X['Na']},
            'K': {'melt_oxide_X_single_cation': _BULK_X['K']},
        },
    }

    def fake_dispatch(intent, *args, **kwargs):
        from simulator.chemistry.kernel import ChemistryIntent

        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': dict(_FLUX_STUB_KG_HR)},
            )
        if intent is ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM:
            return SimpleNamespace(status='ok', diagnostic={})
        raise AssertionError(f'unexpected dispatch: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    flux = sim._calculate_evaporation(
        EquilibriumResult(
            temperature_C=1150.0,
            pressure_bar=1e-8,
            liquid_fraction=_MELT_FRACTION_F,
            vapor_pressures_Pa=dict(_VAPOR_PA),
            diagnostics={'solidus_T_C': 1000.0, 'liquidus_T_C': 1300.0},
        )
    )

    gate_factor = 0.5 if enabled else 1.0
    for species, stub in _FLUX_STUB_KG_HR.items():
        assert flux.species_kg_hr[species] == pytest.approx(stub * gate_factor)

    diagnostic = sim._last_partial_melt_offgassing_diagnostic
    assert diagnostic['status'] == 'UNCERTIFIED_PARAMETERIZED_ESTIMATE'
    assert diagnostic['melt_fraction_F'] == pytest.approx(_MELT_FRACTION_F)
    assert diagnostic['golden_authoritative'] is False
    for species, component in (('Na', 'NaO0.5'), ('K', 'KO0.5')):
        partition = _PARTIAL_MELT_OFFGASSING_COMPONENTS[species]
        assert partition['partition_coefficient'] == pytest.approx(
            0.10 if species == 'Na' else 0.05
        )
        enrichment = _batch_enrichment(
            float(partition['partition_coefficient']),
            _MELT_FRACTION_F,
        )
        detail = diagnostic['component_details'][component]
        assert detail['liquid_composition_source'] == (
            'analytical_batch_partition_fallback'
        )
        assert detail['enrichment_factor'] == pytest.approx(enrichment)
        assert detail['p_partial_liquid_diagnostic'] == pytest.approx(
            _VAPOR_PA[species] * enrichment
        )
        assert detail['bulk_single_cation_mole_fraction'] == pytest.approx(
            _BULK_X[species]
        )


@pytest.mark.parametrize('enabled', (False, True), ids=('gate_off', 'gate_on'))
def test_shuttle_and_thermite_receive_the_gate_liquid_fraction(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    enabled,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=enabled,
    )
    _install_curve(sim)
    monkeypatch.setattr(sim, '_transfer_condensed_species', lambda species: 0.0)
    monkeypatch.setattr(sim, '_top_up_c3_alkali_credit', lambda species: 0.0)
    seen: list[tuple[str, float | None]] = []

    def record_k(*, liquid_fraction=None):
        seen.append(('K', liquid_fraction))

    def record_na(*, target_stage, liquid_fraction=None):
        seen.append((str(target_stage), liquid_fraction))

    monkeypatch.setattr(sim, '_shuttle_inject_K', record_k)
    monkeypatch.setattr(sim, '_shuttle_inject_Na', record_na)
    sim.melt.campaign = CampaignPhase.C3_K
    sim.melt.campaign_hour = 1

    expected_factor = {
        950.0: 0.0 if enabled else None,
        1150.0: 0.5 if enabled else None,
        1400.0: 1.0 if enabled else None,
    }
    for temperature_C, factor in expected_factor.items():
        seen.clear()
        sim.melt.temperature_C = temperature_C
        sim._step_shuttle()
        assert seen == [('K', factor), ('feo_cleanup', factor)]

    captured: list[float | None] = []

    def capture_dispatch(intent, *args, **kwargs):
        del intent, args
        captured.append(kwargs['control_inputs']['liquid_fraction'])
        return SimpleNamespace(status='ok', diagnostic={}, transition=None)

    monkeypatch.setattr(sim, '_dispatch_only', capture_dispatch)
    # Replace the nested campaign dict. The sim's setpoints are a shallow
    # copy of the module fixture, so mutating C6 in place would leak.
    campaigns = dict(sim.setpoints.get('campaigns') or {})
    c6 = dict(campaigns.get('C6') or {})
    c6['static_window_by_feedstock'] = {}
    campaigns['C6'] = c6
    sim.setpoints['campaigns'] = campaigns
    sim.thermite_Mg_inventory_kg = 5.0
    for temperature_C, factor in expected_factor.items():
        captured.clear()
        sim.melt.temperature_C = temperature_C
        sim._step_thermite()
        assert captured == [factor]
