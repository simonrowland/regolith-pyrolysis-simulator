"""Regression tests for the default-off evaporation freeze gate."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel import (
    CapabilityProfile,
    ChemistryIntent,
    ChemistryProvider,
    IntentResult,
    ProviderUnavailableError,
)
from simulator.fe_redox import kress91_ln_fO2_temperature_delta
from simulator.melt_backend.base import EquilibriumResult
from simulator.state import CampaignPhase
from tests.chemistry.conftest import _build_sim

_CLEANED_MELT_ACCOUNT = 'process.cleaned_melt'


class _FakeMAGEMinGateFallbackProvider(ChemistryProvider):
    name = 'magemin-shadow'

    def __init__(self, *, status: str = 'ok') -> None:
        self.status = status
        self.requests = []

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id='magemin-shadow',
            intents=frozenset({
                ChemistryIntent.SILICATE_LIQUIDUS,
                ChemistryIntent.SILICATE_EQUILIBRIUM,
                ChemistryIntent.GATE_LIQUID_FRACTION,
            }),
            is_authoritative_for=frozenset({
                ChemistryIntent.GATE_LIQUID_FRACTION,
            }),
            declared_accounts=frozenset({_CLEANED_MELT_ACCOUNT}),
        )

    def dispatch(self, request) -> IntentResult:
        self.requests.append(request)
        assert request.intent is ChemistryIntent.GATE_LIQUID_FRACTION
        assert _CLEANED_MELT_ACCOUNT in request.account_view.accounts
        assert request.account_view.accounts[_CLEANED_MELT_ACCOUNT]
        if self.status != 'ok':
            return IntentResult(
                intent=ChemistryIntent.GATE_LIQUID_FRACTION,
                status=self.status,
                transition=None,
                diagnostic={'backend_status': self.status},
                warnings=('MAGEMin unavailable in test',),
            )
        return IntentResult(
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            status='ok',
            transition=None,
            diagnostic={
                'backend_status': 'ok',
                'solidus_T_C': 1000.0,
                'liquidus_T_C': 1300.0,
            },
        )


class _UnavailableGateProvider(ChemistryProvider):
    name = 'alphamelts-gate-unavailable'

    def __init__(self) -> None:
        self.requests = []

    def capability_profile(self) -> CapabilityProfile:
        return CapabilityProfile(
            provider_id='alphamelts-gate-unavailable',
            intents=frozenset({ChemistryIntent.GATE_LIQUID_FRACTION}),
            is_authoritative_for=frozenset({
                ChemistryIntent.GATE_LIQUID_FRACTION,
            }),
            declared_accounts=frozenset({_CLEANED_MELT_ACCOUNT}),
        )

    def dispatch(self, request) -> IntentResult:
        self.requests.append(request)
        raise ProviderUnavailableError('AlphaMELTS gate unavailable in test')


def _set_freeze_gate(setpoints_data: dict, *, enabled: bool) -> dict:
    setpoints = dict(setpoints_data)
    gate = dict(setpoints.get('freeze_gate', {}) or {})
    gate['enabled'] = enabled
    setpoints['freeze_gate'] = gate
    return setpoints


def _equilibrium() -> EquilibriumResult:
    return EquilibriumResult(
        liquid_fraction=1.0,
        vapor_pressures_Pa={'Na': 1.0},
        status='ok',
    )


def _build_freeze_gate_sim(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    *,
    enabled: bool,
):
    return _build_sim(
        'lunar_mare_low_ti',
        vapor_pressure_data,
        feedstocks_data,
        _set_freeze_gate(setpoints_data, enabled=enabled),
    )


# 0.5.3 Phase A1 (2026-05-28): under finite-headspace default-on, the
# _calculate_evaporation call chain reads _commanded_pO2_bar →
# _overhead_gas_equilibrium_diagnostic → _dispatch_only(OVERHEAD_GAS_EQUILIBRIUM).
# The freeze-gate tests below monkeypatch _dispatch_only with mock
# evaporation/gate/liquidus stubs; they now also need to absorb the
# OVERHEAD_GAS_EQUILIBRIUM diagnostic dispatch (empty diagnostic is fine —
# _commanded_pO2_bar falls back to the vacuum floor in HARD_VACUUM, which
# matches the test sims' default atmosphere). The mass-balance / freeze-gate
# behavior under test is unchanged by the empty pO2 diagnostic; this is a
# pure test-plumbing patch, NOT a freeze-gate physics shift.
_OVERHEAD_GAS_EQUILIBRIUM_STUB = SimpleNamespace(
    status='ok',
    diagnostic={},
)


def test_freeze_gate_default_off_leaves_evaporation_flux_unchanged(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=False,
    )
    sim.melt.temperature_C = 900.0
    calls = []

    def fake_dispatch(intent, *args, **kwargs):
        calls.append(intent)
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 7.5}},
            )
        if intent is ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM:
            # 0.5.3 Phase A1: finite-headspace default-on routes pO2 through
            # the diagnostic. Empty diagnostic → vacuum-floor pO2; the
            # freeze-gate physics under test is unaffected.
            return _OVERHEAD_GAS_EQUILIBRIUM_STUB
        raise AssertionError(f'unexpected liquidus dispatch: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    flux = sim._calculate_evaporation(_equilibrium())

    assert sim._freeze_gate_enabled() is False
    # 0.5.3 Phase A1: under finite-headspace default-on, the dispatch list
    # additionally includes OVERHEAD_GAS_EQUILIBRIUM (called from
    # _commanded_pO2_bar → _overhead_gas_equilibrium_diagnostic). The
    # EVAPORATION_FLUX call still happens once. Filter to the
    # gate-relevant intent to keep the freeze-gate-default-off
    # assertion: no liquidus/gate calls fire when the gate is OFF.
    assert ChemistryIntent.EVAPORATION_FLUX in calls
    assert ChemistryIntent.GATE_LIQUID_FRACTION not in calls
    assert ChemistryIntent.SILICATE_LIQUIDUS not in calls
    assert flux.species_kg_hr['Na'] == pytest.approx(7.5)


def test_freeze_gate_enabled_uses_ec_table_zero_mush_full(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    gate_calls = 0

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal gate_calls
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            gate_calls += 1
            return SimpleNamespace(
                status='ok',
                diagnostic={
                    'backend_status': 'ok',
                    'solidus_T_C': 1000.0,
                    'liquidus_T_C': 1300.0,
                    'liquid_fraction_path': (
                        {'temperature_C': 1000.0, 'liquid_fraction': 0.0},
                        {'temperature_C': 1150.0, 'liquid_fraction': 0.5},
                        {'temperature_C': 1300.0, 'liquid_fraction': 1.0},
                    ),
                },
            )
        if intent is ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM:
            # 0.5.3 Phase A1: finite-headspace default-on routes pO2
            # through the diagnostic; empty stub → vacuum-floor pO2.
            return _OVERHEAD_GAS_EQUILIBRIUM_STUB
        raise AssertionError(f'unexpected dispatch: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    rates = []
    factors = []
    for temperature_C in (950.0, 1150.0, 1400.0):
        sim.melt.temperature_C = temperature_C
        flux = sim._calculate_evaporation(_equilibrium())
        rates.append(flux.species_kg_hr.get('Na', 0.0))
        factors.append(sim._last_freeze_gate_diagnostic['liquid_fraction'])

    assert rates == pytest.approx([0.0, 5.0, 10.0])
    assert factors == pytest.approx([0.0, 0.5, 1.0])
    assert rates == sorted(rates)
    assert gate_calls == 1
    assert sim._freeze_gate_cache_rebuild_count == 1
    assert sim._last_freeze_gate_diagnostic['source'] == (
        'gate_liquid_fraction'
    )


def test_freeze_gate_dispatches_intrinsic_fo2_to_gate_and_kernel_liquidus(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim.melt.temperature_C = 1150.0
    sim.backend.find_liquidus_solidus = None
    expected_fO2 = sim._compute_intrinsic_melt_fO2()
    captured = []

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            captured.append((intent, kwargs))
            return SimpleNamespace(
                status='unavailable',
                diagnostic={'backend_status': 'unavailable'},
            )
        if intent is ChemistryIntent.SILICATE_LIQUIDUS:
            captured.append((intent, kwargs))
            return SimpleNamespace(
                status='ok',
                diagnostic={
                    'backend_status': 'ok',
                    'solidus_T_C': 1000.0,
                    'liquidus_T_C': 1300.0,
                },
            )
        if intent is ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM:
            # 0.5.3 Phase A1: finite-headspace default-on routes pO2
            # through the diagnostic; empty stub → vacuum-floor pO2.
            return _OVERHEAD_GAS_EQUILIBRIUM_STUB
        raise AssertionError(f'unexpected dispatch: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    flux = sim._calculate_evaporation(_equilibrium())

    assert flux.species_kg_hr['Na'] == pytest.approx(5.0)
    assert [intent for intent, _ in captured] == [
        ChemistryIntent.GATE_LIQUID_FRACTION,
        ChemistryIntent.SILICATE_LIQUIDUS,
    ]
    for _, kwargs in captured:
        assert kwargs['fO2_log'] == expected_fO2
        assert kwargs['fe_redox_policy'] == 'intrinsic'


def test_freeze_gate_enabled_falls_back_to_liquidus_samples(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim.melt.temperature_C = 1150.0

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            return SimpleNamespace(
                status='unavailable',
                diagnostic={'backend_status': 'unavailable'},
            )
        if intent is ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM:
            # 0.5.3 Phase A1: finite-headspace default-on routes pO2
            # through the diagnostic; empty stub → vacuum-floor pO2.
            return _OVERHEAD_GAS_EQUILIBRIUM_STUB
        raise AssertionError(f'unexpected dispatch: {intent}')

    def fake_liquidus_finder(**kwargs):
        assert 'process.cleaned_melt' in kwargs['composition_mol_by_account']
        return SimpleNamespace(
            status='ok',
            solidus_T_C=1000.0,
            liquidus_T_C=1300.0,
            samples=(
                SimpleNamespace(temperature_C=1000.0, frac_M=0.0),
                SimpleNamespace(temperature_C=1150.0, frac_M=0.4),
                SimpleNamespace(temperature_C=1300.0, frac_M=1.0),
            ),
        )

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    sim.backend.find_liquidus_solidus = fake_liquidus_finder

    flux = sim._calculate_evaporation(_equilibrium())

    assert flux.species_kg_hr['Na'] == pytest.approx(4.0)
    assert sim._last_freeze_gate_diagnostic['source'] == (
        'liquidus_solidus:backend'
    )


def test_freeze_gate_enabled_no_engine_freeze_stops(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim.melt.temperature_C = 1150.0
    sim.backend.find_liquidus_solidus = None

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        if intent is ChemistryIntent.OVERHEAD_GAS_EQUILIBRIUM:
            # 0.5.3 Phase A1: finite-headspace default-on routes pO2
            # through the diagnostic. Stub it so the freeze-gate
            # ProviderUnavailableError path can be exercised cleanly —
            # the test is asserting the gate-engine-absent behavior,
            # not the overhead-gas-equilibrium-absent behavior.
            return _OVERHEAD_GAS_EQUILIBRIUM_STUB
        raise ProviderUnavailableError(f'{intent.value} provider absent')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    with pytest.raises(RuntimeError, match='freeze_gate.enabled requires') as exc:
        sim._calculate_evaporation(_equilibrium())
    assert 'gate_liquid_fraction provider absent' in str(exc.value)
    assert 'no liquidus engine produced usable solidus/liquidus bounds' in str(
        exc.value
    )


def test_freeze_gate_enabled_reaches_magemin_gate_fallback(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    authoritative = _UnavailableGateProvider()
    fallback = _FakeMAGEMinGateFallbackProvider()
    sim._chem_registry._authoritative[
        ChemistryIntent.GATE_LIQUID_FRACTION
    ] = authoritative
    sim._chem_registry._fallback[
        ChemistryIntent.GATE_LIQUID_FRACTION
    ] = fallback
    monkeypatch.setattr(
        sim,
        '_register_freeze_gate_liquid_fraction_providers',
        lambda: None,
    )
    original_dispatch = sim._dispatch_only

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        return original_dispatch(intent, *args, **kwargs)

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    rates = []
    for temperature_C in (950.0, 1150.0, 1400.0):
        sim.melt.temperature_C = temperature_C
        flux = sim._calculate_evaporation(_equilibrium())
        rates.append(flux.species_kg_hr.get('Na', 0.0))

    assert rates == pytest.approx([0.0, 5.0, 10.0])
    assert len(authoritative.requests) == 1
    assert len(fallback.requests) == 1
    assert fallback.requests[0].intent is ChemistryIntent.GATE_LIQUID_FRACTION
    assert fallback.requests[0].account_view.accounts[_CLEANED_MELT_ACCOUNT]
    assert fallback.requests[0].pressure_bar == pytest.approx(
        float(sim.melt.p_total_mbar) / 1000.0,
    )
    assert sim._last_freeze_gate_diagnostic['source'] == (
        'gate_liquid_fraction:fallback:magemin-shadow'
    )


def test_freeze_gate_cache_key_constant_across_isochemical_ramp(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = 1425.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    gate_calls = 0

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal gate_calls
        assert intent is ChemistryIntent.GATE_LIQUID_FRACTION
        gate_calls += 1
        return SimpleNamespace(
            status='ok',
            diagnostic={
                'backend_status': 'ok',
                'solidus_T_C': 1000.0,
                'liquidus_T_C': 1300.0,
            },
        )

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    cache_keys = []
    for temperature_C in (1425.0, 1500.0, 1600.0, 1750.0):
        sim.melt.temperature_C = temperature_C
        sim._re_reference_melt_fO2_to_temperature(temperature_C + 273.15)
        sim._freeze_gate_curve()
        cache_keys.append(sim._freeze_gate_liquid_fraction_cache['key'])

    assert len(set(cache_keys)) == 1
    assert sim._freeze_gate_cache_rebuild_count == 1
    assert gate_calls == 1


def test_freeze_gate_cache_rekeys_after_real_redox_source_term(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    temperature_K = 1500.0 + 273.15
    sim.melt.temperature_C = 1500.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = temperature_K
    sim._sync_oxygen_reservoir_mirror()
    gate_calls = 0

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal gate_calls
        assert intent is ChemistryIntent.GATE_LIQUID_FRACTION
        gate_calls += 1
        return SimpleNamespace(
            status='ok',
            diagnostic={
                'backend_status': 'ok',
                'solidus_T_C': 1000.0,
                'liquidus_T_C': 1300.0,
            },
        )

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    sim._freeze_gate_curve()
    baseline_key = sim._freeze_gate_liquid_fraction_cache['key']
    capacity = sim._melt_redox_capacity_mol_per_ln_fO2(
        fO2_log=sim._current_melt_redox_fO2_log(),
        T_K=temperature_K,
    )
    sim._apply_oxygen_reservoir_redox_source_terms(
        {'test_redox_step': capacity * math.log(10.0) * 0.25},
        temperature_K=temperature_K,
    )
    sim._freeze_gate_curve()

    assert sim._freeze_gate_liquid_fraction_cache['key'] != baseline_key
    assert sim._freeze_gate_cache_rebuild_count == 2
    assert gate_calls == 2


def test_redox_liquid_guard_uses_cached_bounds_without_dispatch(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim._freeze_gate_liquid_fraction_cache = {
        'key': ('test',),
        'curve': {
            'source': 'test',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1300.0,
        },
    }

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError('redox liquid guard must not dispatch')

    monkeypatch.setattr(sim, '_dispatch_only', fail_dispatch)

    assert sim._melt_redox_temperature_shift_is_liquid(999.0 + 273.15) is False
    assert sim._melt_redox_temperature_shift_is_liquid(1001.0 + 273.15) is True


def test_freeze_gate_enabled_without_cached_curve_defers_first_reference_seed(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim.melt.temperature_C = 1450.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -9.0
    sim.melt.oxygen_reservoir.reference_T_K = None
    sim._sync_oxygen_reservoir_mirror()

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError('first redox seed deferral must not dispatch')

    monkeypatch.setattr(sim, '_dispatch_only', fail_dispatch)

    sim._re_reference_melt_fO2_to_temperature(1450.0 + 273.15)

    assert sim.melt.oxygen_reservoir.reference_T_K is None
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(-9.0)


def test_freeze_gate_pre_curve_window_builds_curve_on_first_staged_tick(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim.start_campaign(CampaignPhase.C2A)
    sim.melt.temperature_C = sim.campaign_mgr.furnace_max_T_C
    original_dispatch = sim._dispatch_only
    gate_calls = 0

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal gate_calls
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 0.01}},
            )
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            gate_calls += 1
            return SimpleNamespace(
                status='ok',
                diagnostic={
                    'backend_status': 'ok',
                    'solidus_T_C': 1000.0,
                    'liquidus_T_C': 1300.0,
                },
            )
        return original_dispatch(intent, *args, **kwargs)

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    monkeypatch.setattr(sim, '_get_equilibrium', lambda: _equilibrium())

    ticks_to_curve = 0
    while getattr(sim, '_freeze_gate_liquid_fraction_cache', None) is None:
        assert ticks_to_curve < 3
        sim.step()
        ticks_to_curve += 1

    assert ticks_to_curve == 1
    assert gate_calls == 1


def test_temperature_rereference_noop_skips_liquid_guard(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    temperature_K = 1500.0 + 273.15
    sim.melt.temperature_C = 1500.0
    sim.melt.oxygen_reservoir.reference_T_K = temperature_K

    def fail_guard(_temperature_K):
        raise AssertionError('constant-T re-reference should be a no-op')

    monkeypatch.setattr(sim, '_melt_redox_temperature_shift_is_liquid', fail_guard)

    sim._re_reference_melt_fO2_to_temperature(temperature_K)


def test_freeze_gate_enabled_quench_hysteresis_uses_last_liquid_reference(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim._freeze_gate_liquid_fraction_cache = {
        'key': ('test',),
        'curve': {
            'source': 'test',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1300.0,
        },
    }

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError('quenched redox guard must not dispatch')

    monkeypatch.setattr(sim, '_dispatch_only', fail_dispatch)

    original_T_K = 1500.0 + 273.15
    remelt_T_K = 1650.0 + 273.15
    original_fO2 = -9.0
    sim.melt.temperature_C = 1500.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = original_fO2
    sim.melt.oxygen_reservoir.reference_T_K = original_T_K
    sim._sync_oxygen_reservoir_mirror()

    sim.melt.temperature_C = 900.0
    sim._re_reference_melt_fO2_to_temperature(900.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        original_fO2,
    )
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(original_T_K)

    sim.melt.temperature_C = 1650.0
    sim._re_reference_melt_fO2_to_temperature(remelt_T_K)
    expected_hot = (
        original_fO2
        + kress91_ln_fO2_temperature_delta(original_T_K, remelt_T_K)
        / math.log(10.0)
    )
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        expected_hot,
        abs=1.0e-9,
    )
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(remelt_T_K)

    sim.melt.temperature_C = 1500.0
    sim._re_reference_melt_fO2_to_temperature(original_T_K)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        original_fO2,
        abs=1.0e-9,
    )
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(original_T_K)


def test_freeze_gate_cache_quantization_holds_super_liquidus_ticks(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim.start_campaign(CampaignPhase.C2A)
    # Start AT the continuous target so the melt holds a stable super-liquidus
    # plateau across the 10 steps — that is what makes the freeze-gate liquid-
    # fraction cache quantize (hold) instead of rebuilding every tick. The C2A
    # continuous target is now furnace_max_T_C (S2a1b); pin the start there
    # (was hardcoded 1600, which only plateaued under the old 1600 cap).
    sim.melt.temperature_C = sim.campaign_mgr.furnace_max_T_C
    original_dispatch = sim._dispatch_only
    gate_calls = 0

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal gate_calls
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 0.01}},
            )
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            gate_calls += 1
            return SimpleNamespace(
                status='ok',
                diagnostic={
                    'backend_status': 'ok',
                    'solidus_T_C': 1000.0,
                    'liquidus_T_C': 1300.0,
                    'liquid_fraction_path': (
                        {'temperature_C': 1000.0, 'liquid_fraction': 0.0},
                        {'temperature_C': 1150.0, 'liquid_fraction': 0.5},
                        {'temperature_C': 1300.0, 'liquid_fraction': 1.0},
                    ),
                },
            )
        return original_dispatch(intent, *args, **kwargs)

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    monkeypatch.setattr(sim, '_get_equilibrium', lambda: _equilibrium())

    factors = []
    balance_errors = []
    cache_ids = []
    for _ in range(10):
        snapshot = sim.step()
        factors.append(sim._last_freeze_gate_diagnostic['liquid_fraction'])
        balance_errors.append(abs(snapshot.mass_balance_error_pct))
        cache_ids.append(id(sim._freeze_gate_liquid_fraction_cache))

    cache_rebuild_count = sim._freeze_gate_cache_rebuild_count
    # 2026-07-02 SSO-R ch1d: freeze-gate fO2 key is T-invariant along
    # isochemical ramps, so quantization returns to the original <=2 bound.
    assert cache_rebuild_count <= 2
    assert gate_calls == cache_rebuild_count
    assert len(set(cache_ids)) <= 2
    assert factors == [1.0] * 10
    assert max(balance_errors) <= 5e-12


def test_freeze_gate_cache_key_includes_pressure_and_fo2(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )

    baseline = sim._freeze_gate_cache_key(pressure_bar=1.00, fO2_log=-9.0)
    same = sim._freeze_gate_cache_key(pressure_bar=1.004, fO2_log=-9.04)
    different_pressure = sim._freeze_gate_cache_key(
        pressure_bar=1.02,
        fO2_log=-9.0,
    )
    different_fO2 = sim._freeze_gate_cache_key(
        pressure_bar=1.00,
        fO2_log=-8.9,
    )

    assert same == baseline
    assert different_pressure != baseline
    assert different_fO2 != baseline
