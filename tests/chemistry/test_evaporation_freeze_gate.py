"""Regression tests for the default-off evaporation freeze gate."""

from __future__ import annotations

from collections import Counter
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
from simulator.core import PoisonedHourError
from simulator.fe_redox import kress91_ln_fO2_temperature_delta
from simulator.melt_backend.base import EquilibriumResult
from simulator.state import CampaignPhase, EvaporationFlux
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


def _configure_tick_authority_retry_sim(
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
    sim.melt.temperature_C = 1600.0
    sim.melt.target_temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim.melt.oxygen_reservoir.reference_T_K = 1500.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas',
        {'O2': 10_000.0},
        source='test hour authority retry oxygen',
    )
    return sim


def _freeze_gate_key_for_current_state(sim) -> tuple:
    pressure_bar = float(sim.melt.p_total_mbar) / 1000.0
    fO2_log = float(sim._current_melt_redox_fO2_log())
    redox_key_fO2_log = sim._freeze_gate_redox_key_fO2_log(fO2_log=fO2_log)
    return sim._freeze_gate_cache_key(
        pressure_bar=pressure_bar,
        fO2_log=redox_key_fO2_log,
    )


def _install_freeze_gate_curve(
    sim,
    *,
    path: tuple[tuple[float, float], ...] | None = None,
) -> None:
    curve = {
        'source': 'test',
        'solidus_T_C': 1000.0,
        'liquidus_T_C': 1300.0,
    }
    if path is not None:
        curve['path'] = path
    sim._freeze_gate_liquid_fraction_cache = {
        'key': _freeze_gate_key_for_current_state(sim),
        'curve': curve,
    }


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


def test_kernel_liquidus_aggregate_budget_exhaustion_production_default_no_authority(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """allow_parametric=False is the production default: budget exhaustion
    must not earn a curve, cache entry, or ledger-authority path.
    """
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=True,
    )
    sim._freeze_gate_liquid_fraction_cache = None
    transitions_before = len(sim.atom_ledger.transitions)
    mol_before = sim.atom_ledger.mol_by_account()

    def fake_dispatch(intent, *args, **kwargs):
        assert intent is ChemistryIntent.SILICATE_LIQUIDUS
        return SimpleNamespace(
            status='not_converged',
            diagnostic={
                'backend_status': 'not_converged',
                'reason': 'aggregate_budget_exceeded',
                'elapsed_s': 0.12,
                'call_count': 3,
                'last_T_C': 900.0,
                'budget_s': 0.1,
            },
            warnings=(
                'liquidus finder exceeded aggregate budget 0.1s after 3 calls',
            ),
        )

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    reasons: list[str] = []

    # Production call site omits allow_parametric (default False).
    curve = sim._freeze_gate_curve_from_kernel_liquidus(
        reasons,
        fO2_log=-9.0,
    )

    assert curve is None
    assert any(
        'kernel liquidus unavailable: status=not_converged' in r
        for r in reasons
    )
    # No freeze-gate curve authority and no cache population on this helper.
    assert sim._freeze_gate_liquid_fraction_cache is None
    # No ledger mutation from a non-ok diagnostic liquidus path.
    assert len(sim.atom_ledger.transitions) == transitions_before
    assert sim.atom_ledger.mol_by_account() == mol_before


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
        {'test_redox_step': capacity * math.log(10.0) * 1.25},
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
    _install_freeze_gate_curve(sim)

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError('redox liquid guard must not dispatch')

    monkeypatch.setattr(sim, '_dispatch_only', fail_dispatch)

    assert sim._melt_redox_temperature_shift_is_liquid(999.0 + 273.15) is False
    assert sim._melt_redox_temperature_shift_is_liquid(1299.0 + 273.15) is False
    assert sim._melt_redox_temperature_shift_is_liquid(1300.0 + 273.15) is True
    assert sim._last_melt_regime_diagnostic[
        'redox_temperature_shift_threshold_T_C'
    ] == 1300.0


def test_redox_liquid_gate_builds_outer_curve_when_cache_missing_default_off(
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

    curve = sim._melt_redox_liquidus_gate_curve()

    assert curve['source'] == 'gate_liquid_fraction'
    assert gate_calls == 1
    assert sim._freeze_gate_cache_rebuild_count == 1
    assert sim._freeze_gate_enabled() is False
    assert sim._melt_redox_temperature_shift_is_liquid(1300.0 + 273.15) is True
    assert gate_calls == 1
    sim._freeze_gate_liquid_fraction_cache = {
        'key': ('stale-active-slot',),
        'curve': {
            'source': 'stale',
            'solidus_T_C': 900.0,
            'liquidus_T_C': 1100.0,
        },
    }
    memo_curve = sim._melt_redox_liquidus_gate_curve()

    assert memo_curve['source'] == 'gate_liquid_fraction'
    assert gate_calls == 1


def test_redox_liquid_gate_rebuilds_stale_cache_on_key_mismatch(
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

    first_curve = sim._melt_redox_liquidus_gate_curve()
    baseline_key = sim._freeze_gate_liquid_fraction_cache['key']
    sim.atom_ledger.load_external_mol(
        _CLEANED_MELT_ACCOUNT,
        {'Fe2O3': 1_000.0},
        source='test ferric oxide cache-key perturbation',
    )
    rebuilt_curve = sim._melt_redox_liquidus_gate_curve()

    assert first_curve['source'] == 'gate_liquid_fraction'
    assert rebuilt_curve['source'] == 'gate_liquid_fraction'
    assert sim._freeze_gate_liquid_fraction_cache['key'] != baseline_key
    assert sim._freeze_gate_cache_rebuild_count == 2
    assert gate_calls == 2


def test_redox_liquid_gate_in_progress_refuses_reentrant_curve_request(
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
    inner_curves = []
    inner_factors = []
    inner_diagnostics = []

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal gate_calls
        assert intent is ChemistryIntent.GATE_LIQUID_FRACTION
        gate_calls += 1
        assert sim._freeze_gate_liquid_fraction_cache['status'] == 'computing'
        inner_factors.append(
            sim._melt_redox_liquid_fraction_factor(1500.0 + 273.15)
        )
        inner_curves.append(sim._melt_redox_liquidus_gate_curve())
        inner_diagnostics.append(
            dict(sim._last_melt_redox_liquidus_gate_diagnostic)
        )
        return SimpleNamespace(
            status='ok',
            diagnostic={
                'backend_status': 'ok',
                'solidus_T_C': 1000.0,
                'liquidus_T_C': 1300.0,
            },
        )

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    curve = sim._freeze_gate_curve()

    assert curve['source'] == 'gate_liquid_fraction'
    assert gate_calls == 1
    assert sim._freeze_gate_cache_rebuild_count == 1
    assert sim._freeze_gate_curve_in_progress is False
    assert 'status' not in sim._freeze_gate_liquid_fraction_cache
    assert inner_factors == [0.0]
    assert inner_curves == [None]
    assert inner_diagnostics == [
        {
            'status': 'unavailable',
            'source': 'none:liquidus_gate_in_progress',
        }
    ]


@pytest.mark.parametrize(
    ('provider_status', 'expected_source'),
    (
        ('unavailable', 'none:liquidus_unavailable'),
        ('not_converged', 'none:liquidus_not_converged'),
    ),
    ids=('provider-unavailable', 'provider-not-converged'),
)
def test_redox_liquidus_failure_uses_kress_floor_above_1200_default_off(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    provider_status,
    expected_source,
):
    sim = _build_freeze_gate_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        enabled=False,
    )
    sim.melt.temperature_C = 1500.0
    sim.backend.find_liquidus_solidus = None

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            if provider_status == 'unavailable':
                raise ProviderUnavailableError('gate provider unavailable in test')
            return SimpleNamespace(
                status='not_converged',
                diagnostic={'backend_status': 'not_converged'},
            )
        if intent is ChemistryIntent.SILICATE_LIQUIDUS:
            raise ProviderUnavailableError('kernel liquidus unavailable in test')
        raise AssertionError(f'unexpected dispatch: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    T_K = sim.melt.temperature_C + 273.15
    fO2_log = sim._current_melt_redox_fO2_log()
    assert sim._melt_redox_temperature_shift_is_liquid(T_K) is True
    full_capacity = sim._melt_redox_capacity_mol_per_ln_fO2(
        fO2_log=fO2_log,
        T_K=T_K,
    )

    effective_capacity = sim._melt_redox_source_capacity_mol_per_ln_fO2(
        fO2_log=fO2_log,
        T_K=T_K,
    )

    assert sim._freeze_gate_enabled() is False
    assert full_capacity > 0.0
    assert effective_capacity == pytest.approx(full_capacity)
    diagnostic = sim._last_melt_redox_liquid_fraction_diagnostic
    assert diagnostic['status'] == 'liquidus_unavailable_floor_fallback'
    assert diagnostic['source'] == expected_source
    assert diagnostic['liquidus_status'] == provider_status
    assert diagnostic['liquid_fraction'] == 1.0
    assert diagnostic['floor_T_C'] == 1200.0
    assert diagnostic['reason']
    assert sim._melt_redox_liquid_fraction_factor(1200.0 + 273.15) == 0.0


def test_redox_source_capacity_scales_with_continuous_liquid_fraction(
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
    _install_freeze_gate_curve(
        sim,
        path=(
            (1000.0, 0.0),
            (1300.0, 1.0),
        ),
    )

    def full_capacity(**_kwargs):
        return 12.0

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError('cached redox capacity scaling must not dispatch')

    monkeypatch.setattr(sim, '_melt_redox_capacity_mol_per_ln_fO2', full_capacity)
    monkeypatch.setattr(sim, '_dispatch_only', fail_dispatch)

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


def test_passive_exchange_refuses_zero_liquid_capacity(
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
    sim.melt.temperature_C = 900.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim.melt.oxygen_reservoir.reference_T_K = 1500.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    _install_freeze_gate_curve(
        sim,
        path=((1000.0, 0.0), (1300.0, 1.0)),
    )
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas',
        {'O2': 10_000.0},
        source='test frozen passive exchange oxygen',
    )
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    before_reference_T_K = sim.melt.oxygen_reservoir.reference_T_K
    before_overhead_o2 = sim.atom_ledger.mol_by_account('process.overhead_gas')[
        'O2'
    ]
    before_transition_count = len(sim.atom_ledger.transitions)

    reservoir = sim._apply_oxygen_reservoir_exchange()

    assert reservoir.exchange_direction == 'none:no_melt_redox_capacity'
    assert reservoir.melt_redox_capacity_mol_per_ln_fO2 == pytest.approx(0.0)
    assert reservoir.melt_intrinsic_fO2_log == pytest.approx(before_fO2)
    assert reservoir.reference_T_K == pytest.approx(before_reference_T_K)
    assert sim.atom_ledger.mol_by_account('process.overhead_gas')['O2'] == (
        pytest.approx(before_overhead_o2)
    )
    assert len(sim.atom_ledger.transitions) == before_transition_count


def test_redox_reference_seed_builds_liquidus_curve_before_first_seed(
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

    sim._re_reference_melt_fO2_to_temperature(1450.0 + 273.15)

    assert gate_calls == 1
    assert sim.melt.oxygen_reservoir.reference_T_K == pytest.approx(1450.0 + 273.15)
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(-9.0)
    assert sim._last_melt_redox_liquidus_gate_diagnostic['status'] == 'ok'


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
                # 2026-07-02 ch2c: a nonzero stubbed flux now drives REAL committed
                # evaporation transitions -> evaporative redox source terms -> fO2
                # movement -> honest cache re-keys. These tests probe cache/gate
                # behavior on a QUIESCENT plateau, so the stub flux must be zero.
                diagnostic={'evaporation_flux_kg_hr': {'Na': 0.0}},
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
    assert gate_calls == sim._freeze_gate_cache_rebuild_count
    assert 1 <= gate_calls <= 5


def test_freeze_gate_pre_curve_tick_builds_curve_before_native_split(
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
    sim.melt.target_temperature_C = sim.campaign_mgr.furnace_max_T_C
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -10.0
    sim.melt.oxygen_reservoir.reference_T_K = None
    sim._sync_oxygen_reservoir_mirror()
    original_dispatch = sim._dispatch_only

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 0.0}},
            )
        if intent is ChemistryIntent.GATE_LIQUID_FRACTION:
            return SimpleNamespace(
                status='ok',
                diagnostic={
                    'backend_status': 'ok',
                    'solidus_T_C': 1000.0,
                    'liquidus_T_C': 1300.0,
                },
            )
        return original_dispatch(intent, *args, **kwargs)

    respeciation_results: list[dict] = []
    native_results: list[dict] = []
    original_respeciation = sim._apply_fe_redox_respeciation
    original_native_split = sim._apply_native_fe_saturation_split

    def record_respeciation(**kwargs):
        result = original_respeciation(**kwargs)
        respeciation_results.append(dict(result))
        return result

    def record_native_split(*, sample_time_h=None):
        result = original_native_split(sample_time_h=sample_time_h)
        native_results.append(dict(result))
        return result

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    monkeypatch.setattr(sim, '_get_equilibrium', lambda: _equilibrium())
    monkeypatch.setattr(sim, '_apply_fe_redox_respeciation', record_respeciation)
    monkeypatch.setattr(sim, '_apply_native_fe_saturation_split', record_native_split)

    snapshot = sim.step()

    assert respeciation_results
    assert all(
        result.get('respeciation_status') != 'skipped_solid'
        for result in respeciation_results
    )
    assert native_results[0]['native_fe_event'] == 'native_fe_partitioned_saturation'
    assert native_results[0]['native_fe_partition']['native_fe_pool_mol'] > 0.0
    event = snapshot.fe_redox_split['native_fe_saturation_event']
    assert event['native_fe_event'] == 'native_fe_partitioned_saturation'
    assert snapshot.fe_redox_split['native_fe_partition'][
        'native_fe_pool_mol'
    ] > 0.0


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

    def fail_dispatch(*_args, **_kwargs):
        raise AssertionError('quenched redox guard must not dispatch')

    original_T_K = 1500.0 + 273.15
    remelt_T_K = 1650.0 + 273.15
    original_fO2 = -9.0
    sim.melt.temperature_C = 1500.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = original_fO2
    sim.melt.oxygen_reservoir.reference_T_K = original_T_K
    sim._sync_oxygen_reservoir_mirror()
    _install_freeze_gate_curve(sim)
    monkeypatch.setattr(sim, '_dispatch_only', fail_dispatch)

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
                # 2026-07-02 ch2c: a nonzero stubbed flux now drives REAL committed
                # evaporation transitions -> evaporative redox source terms -> fO2
                # movement -> honest cache re-keys. These tests probe cache/gate
                # behavior on a QUIESCENT plateau, so the stub flux must be zero.
                diagnostic={'evaporation_flux_kg_hr': {'Na': 0.0}},
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
    # The first hot tick can materialize several distinct redox/cache bins; the
    # quiescent plateau must then hold the cache rather than churn every tick.
    assert 1 <= cache_rebuild_count <= 5
    assert gate_calls == cache_rebuild_count
    assert len(set(cache_ids)) <= cache_rebuild_count
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
        fO2_log=-7.9,
    )

    assert same == baseline
    assert different_pressure != baseline
    assert different_fO2 != baseline


def test_freeze_gate_redox_key_ignores_degenerate_reference_temperature(
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

    assert sim._freeze_gate_redox_key_fO2_log(
        fO2_log=-9.0,
        reference_T_K=0.2021010676400634,
    ) == pytest.approx(-9.0)
    assert sim._freeze_gate_redox_key_fO2_log(
        fO2_log=-9.0,
        reference_T_K=273.15,
    ) == pytest.approx(-9.0)
    assert sim._freeze_gate_redox_key_fO2_log(
        fO2_log=127898.56895386701,
        reference_T_K=None,
    ) == pytest.approx(30.0)
    assert sim._freeze_gate_liquidus_fO2_log(-127898.56895386701) == pytest.approx(
        -30.0,
    )


def test_freeze_gate_cache_rebuilds_after_integrated_fe2o3_respeciation(
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
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim.melt.oxygen_reservoir.reference_T_K = 1600.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas',
        {'O2': 10_000.0},
        source='test explicit oxygen for integrated Fe redox respeciation',
    )
    gate_calls = 0
    original_dispatch = sim._dispatch_only

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal gate_calls
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

    first_curve = sim._melt_redox_liquidus_gate_curve()
    baseline_key = sim._freeze_gate_liquid_fraction_cache['key']
    before_fe2o3_mol = sim.atom_ledger.mol_by_account(
        _CLEANED_MELT_ACCOUNT
    ).get('Fe2O3', 0.0)

    respeciation = sim._apply_fe_redox_respeciation()
    after_fe2o3_mol = sim.atom_ledger.mol_by_account(
        _CLEANED_MELT_ACCOUNT
    ).get('Fe2O3', 0.0)
    rebuilt_curve = sim._melt_redox_liquidus_gate_curve()
    ferric_key = sim._freeze_gate_liquid_fraction_cache['key']

    assert first_curve['source'] == 'gate_liquid_fraction'
    assert rebuilt_curve['source'] == 'gate_liquid_fraction'
    assert respeciation['status'] == 'ok'
    assert respeciation['respeciation_status'] == 'ok'
    assert after_fe2o3_mol > before_fe2o3_mol
    assert ferric_key != baseline_key
    composition_key = dict(ferric_key[3])
    assert 'Fe2O3' in composition_key
    assert gate_calls == 2
    assert sim._freeze_gate_cache_rebuild_count == 2
    transition = sim.atom_ledger.transitions[-1]
    assert transition.name == 'fe_redox_respeciation'
    assert all(
        lot.meta['melt_redox_gate_authority']['kind'] == 'real'
        for lot in (*transition.debits, *transition.credits)
    )
    assert all(
        lot.meta['melt_redox_gate_authority']['fallback_status']
        == 'not_engaged'
        for lot in (*transition.debits, *transition.credits)
    )


def test_redox_operation_holds_failed_authority_until_recovery_next_operation(
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
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim.melt.oxygen_reservoir.reference_T_K = 1500.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas',
        {'O2': 10_000.0},
        source='test redox authority snapshot recovery oxygen',
    )
    curve_calls = 0

    def fail_then_recover_curve():
        nonlocal curve_calls
        curve_calls += 1
        if curve_calls == 1:
            raise ProviderUnavailableError('transient liquidus lookup failure')
        return {
            'source': 'test_recovered_real_curve',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1700.0,
        }

    monkeypatch.setattr(sim, '_freeze_gate_curve', fail_then_recover_curve)

    fallback_operation = sim._apply_fe_redox_respeciation()
    fallback_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    fallback_authority = dict(
        sim._last_melt_redox_liquidus_gate_diagnostic
    )
    expected_fO2 = -3.0 + (
        kress91_ln_fO2_temperature_delta(
            1500.0 + 273.15,
            1600.0 + 273.15,
        )
        / math.log(10.0)
    )

    assert curve_calls == 1
    assert fallback_fO2 == pytest.approx(expected_fO2)
    assert fallback_operation['respeciation_status'] == 'ok'
    assert fallback_operation['transition_name'] == 'fe_redox_respeciation'
    assert fallback_authority['status'] == 'liquidus_unavailable_floor_fallback'

    recovered_operation = sim._apply_fe_redox_respeciation()

    assert curve_calls == 2
    assert recovered_operation['respeciation_status'] == 'skipped_solid'
    assert sim._last_melt_redox_liquidus_gate_diagnostic == {
        'status': 'ok',
        'source': 'test_recovered_real_curve',
        'solidus_T_C': 1000.0,
        'liquidus_T_C': 1700.0,
    }
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == pytest.approx(
        fallback_fO2
    )


def test_full_tick_pins_failed_authority_and_poisoned_retry(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    recovered_curve = {
        'source': 'test_recovered_real_curve',
        'solidus_T_C': 1000.0,
        'liquidus_T_C': 1700.0,
        'path': ((1000.0, 0.0), (1700.0, 1.0)),
    }

    per_operation_sim = _configure_tick_authority_retry_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    per_operation_calls = 0

    def per_operation_fail_then_recover():
        nonlocal per_operation_calls
        per_operation_calls += 1
        if per_operation_calls == 1:
            raise ProviderUnavailableError('transient tick liquidus failure')
        return dict(recovered_curve)

    monkeypatch.setattr(
        per_operation_sim,
        '_freeze_gate_curve',
        per_operation_fail_then_recover,
    )
    per_operation_sim._apply_fe_redox_respeciation()
    mixed_transition = per_operation_sim.atom_ledger.transitions[-1]
    mixed_liquid_fraction = (
        per_operation_sim._freeze_gate_liquid_fraction_factor()
    )

    assert per_operation_calls == 2
    assert mixed_transition.name == 'fe_redox_respeciation'
    # K-01 complete Kress91 re-reference lowers the target fO2 shift in this
    # mixed liquidus gate probe; the liquid-fraction authority remains pinned.
    assert per_operation_sim._transition_species_mol(
        mixed_transition,
        side='debits',
        account=_CLEANED_MELT_ACCOUNT,
        species='FeO',
    ) == pytest.approx(694.169, rel=2.0e-3)
    assert per_operation_sim._transition_species_mol(
        mixed_transition,
        side='credits',
        account=_CLEANED_MELT_ACCOUNT,
        species='Fe2O3',
    ) == pytest.approx(347.085, rel=2.0e-3)
    assert per_operation_sim._transition_species_mol(
        mixed_transition,
        side='debits',
        account='process.overhead_gas',
        species='O2',
    ) == pytest.approx(173.542, rel=2.0e-3)
    assert (
        per_operation_sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log + 3.0
    ) == pytest.approx(0.733213, rel=2.0e-3)
    assert mixed_liquid_fraction == pytest.approx(6.0 / 7.0)

    tick_sim = _configure_tick_authority_retry_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    tick_curve_calls = 0
    original_dispatch = tick_sim._dispatch_only
    original_resolve = tick_sim._resolved_melt_redox_gate_authority
    resolved_authorities = []

    def tick_fail_then_recover():
        nonlocal tick_curve_calls
        tick_curve_calls += 1
        if tick_curve_calls == 1:
            raise ProviderUnavailableError('transient tick liquidus failure')
        return dict(recovered_curve)

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 0.0}},
            )
        return original_dispatch(intent, *args, **kwargs)

    def record_resolved_authority(*args, **kwargs):
        authority = original_resolve(*args, **kwargs)
        resolved_authorities.append(authority)
        return authority

    monkeypatch.setattr(tick_sim, '_freeze_gate_curve', tick_fail_then_recover)
    monkeypatch.setattr(tick_sim, '_dispatch_only', fake_dispatch)
    monkeypatch.setattr(
        tick_sim,
        '_resolved_melt_redox_gate_authority',
        record_resolved_authority,
    )
    monkeypatch.setattr(tick_sim, '_get_equilibrium', lambda: _equilibrium())
    monkeypatch.setattr(tick_sim, '_update_temperature', lambda: None)
    monkeypatch.setattr(tick_sim, '_apply_oxygen_reservoir_exchange', lambda: None)

    transition_count_before_attempt = len(tick_sim.atom_ledger.transitions)
    with pytest.raises(
        RuntimeError,
        match='transient tick liquidus failure',
    ) as first_error:
        tick_sim.step()

    assert type(first_error.value) is RuntimeError
    failed_hour_authority = tick_sim._melt_redox_gate_authority_this_tick
    assert tick_curve_calls == 1
    assert len(resolved_authorities) >= 6
    assert all(
        authority is tick_sim._melt_redox_gate_authority_this_tick
        for authority in resolved_authorities
    )
    first_tick_redox = [
        transition
        for transition in tick_sim.atom_ledger.transitions
        if transition.name in {
            'fe_redox_respeciation',
            'native_fe_saturation_split',
        }
    ]
    assert first_tick_redox
    assert all(
        lot.meta['melt_redox_gate_authority']['kind'] == 'fallback'
        for transition in first_tick_redox
        for lot in (*transition.debits, *transition.credits)
    )
    balances_after_abort = tick_sim.atom_ledger.mol_by_account()
    transitions_after_abort = tick_sim.atom_ledger.transitions

    with pytest.raises(PoisonedHourError) as retry_error:
        tick_sim.step()

    assert type(retry_error.value) is PoisonedHourError
    assert tick_curve_calls == 1
    assert tick_sim._melt_redox_gate_authority_this_tick is failed_hour_authority
    assert retry_error.value.state.hour == 0
    assert retry_error.value.state.committed_transition_count == (
        len(transitions_after_abort) - transition_count_before_attempt
    )
    assert tick_sim.atom_ledger.mol_by_account() == balances_after_abort
    assert tick_sim.atom_ledger.transitions == transitions_after_abort


def test_abort_before_any_commit_allows_same_hour_retry_with_pinned_authority(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _configure_tick_authority_retry_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    pinned_curve = {
        'source': 'test_recovered_real_curve',
        'solidus_T_C': 1000.0,
        'liquidus_T_C': 1700.0,
        'path': ((1000.0, 0.0), (1700.0, 1.0)),
    }
    curve_calls = 0
    exchange_calls = 0
    original_dispatch = sim._dispatch_only
    original_resolve = sim._resolved_melt_redox_gate_authority
    resolved_authorities = []

    def resolve_curve():
        nonlocal curve_calls
        curve_calls += 1
        return pinned_curve

    def abort_once_before_commit():
        nonlocal exchange_calls
        exchange_calls += 1
        if exchange_calls == 1:
            raise RuntimeError('pre-commit retry probe')

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                status='ok',
                diagnostic={'evaporation_flux_kg_hr': {'Na': 0.0}},
            )
        return original_dispatch(intent, *args, **kwargs)

    def record_resolved_authority(*args, **kwargs):
        authority = original_resolve(*args, **kwargs)
        resolved_authorities.append(authority)
        return authority

    monkeypatch.setattr(sim, '_freeze_gate_curve', resolve_curve)
    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)
    monkeypatch.setattr(
        sim,
        '_resolved_melt_redox_gate_authority',
        record_resolved_authority,
    )
    monkeypatch.setattr(sim, '_get_equilibrium', lambda: _equilibrium())
    monkeypatch.setattr(sim, '_update_temperature', lambda: None)
    monkeypatch.setattr(
        sim,
        '_apply_oxygen_reservoir_exchange',
        abort_once_before_commit,
    )

    transition_count_before_attempt = len(sim.atom_ledger.transitions)
    with pytest.raises(RuntimeError, match='pre-commit retry probe') as first_error:
        sim.step()

    assert type(first_error.value) is RuntimeError
    failed_authority = sim._melt_redox_gate_authority_this_tick
    assert failed_authority == pinned_curve
    assert curve_calls == 1
    assert sim._poisoned_hour is None
    assert len(sim.atom_ledger.transitions) == transition_count_before_attempt

    snapshot = sim.step()

    assert snapshot.hour == 1
    assert exchange_calls == 2
    assert curve_calls == 1
    assert resolved_authorities
    assert all(authority is failed_authority for authority in resolved_authorities)
    assert sim._melt_redox_gate_authority_tick_hour is None
    assert sim._poisoned_hour is None


def test_c5_partial_commit_poison_refuses_same_hour_replay(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _configure_tick_authority_retry_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = 'CaO'
    sim.melt.mre_max_voltage_V = 2.5
    sim.campaign_mgr.c5_enabled = True
    sim.start_campaign(CampaignPhase.C5)
    sim.melt.temperature_C = 1600.0
    sim.melt.target_temperature_C = 1600.0
    retry_oxygen_load = sim.atom_ledger.external_loads[-1]
    sim.inventory.stage0_external_inputs_kg['test_hour_authority_retry_oxygen'] = (
        retry_oxygen_load.total_mass_kg(sim.atom_ledger.registry)
    )
    curve_calls = 0

    def fail_to_floor_then_recover():
        nonlocal curve_calls
        curve_calls += 1
        if curve_calls == 1:
            raise ProviderUnavailableError('C5 partial-commit authority probe')
        return {
            'source': 'test_recovered_real_curve',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1700.0,
            'path': ((1000.0, 0.0), (1700.0, 1.0)),
        }

    monkeypatch.setattr(sim, '_freeze_gate_curve', fail_to_floor_then_recover)
    monkeypatch.setattr(sim, '_update_temperature', lambda: None)
    monkeypatch.setattr(sim, '_apply_oxygen_reservoir_exchange', lambda: None)
    monkeypatch.setattr(
        sim,
        '_get_equilibrium',
        lambda: (_ for _ in ()).throw(RuntimeError('post-MRE probe abort')),
    )
    sim._establish_melt_redox_gate_authority_for_current_hour()
    sim._apply_fe_redox_respeciation()

    balances_before = sim.atom_ledger.mol_by_account()
    transitions_before = Counter(
        transition.name for transition in sim.atom_ledger.transitions
    )
    with pytest.raises(RuntimeError, match='post-MRE probe abort') as first_error:
        sim.step()

    assert type(first_error.value) is RuntimeError
    balances_after_abort = sim.atom_ledger.mol_by_account()
    transitions_after_abort = sim.atom_ledger.transitions
    new_transitions = (
        Counter(transition.name for transition in transitions_after_abort)
        - transitions_before
    )
    stored_o2_after_abort = sim.atom_ledger.mol_by_account(
        'terminal.oxygen_mre_anode_stored'
    ).get('O2', 0.0)

    assert balances_after_abort != balances_before
    assert new_transitions['mre_electrolysis_reduction'] == 1
    assert new_transitions['fe_redox_respeciation'] == 1
    assert stored_o2_after_abort > 0.0
    assert sim.melt.hour == 0
    assert sim.energy_electrical_plus_evaporation_cumulative_kWh == 0.0
    assert sim._poisoned_hour is not None
    assert sim._poisoned_hour.hour == 0
    assert sim._poisoned_hour.committed_transition_count == sum(
        new_transitions.values()
    )
    assert sim._poisoned_hour.aborting_exception_summary == (
        'RuntimeError: post-MRE probe abort'
    )
    assert abs(sim._make_snapshot().mass_balance_error_pct) < 5.0e-12

    with pytest.raises(PoisonedHourError) as retry_error:
        sim.step()

    assert type(retry_error.value) is PoisonedHourError
    assert retry_error.value.state is sim._poisoned_hour
    assert 'fresh simulator or reload the batch' in str(retry_error.value)
    assert curve_calls == 1
    assert sim.atom_ledger.mol_by_account() == balances_after_abort
    assert sim.atom_ledger.transitions == transitions_after_abort
    assert sim.atom_ledger.mol_by_account(
        'terminal.oxygen_mre_anode_stored'
    ).get('O2', 0.0) == stored_o2_after_abort


def test_mre_zero_transition_refusal_does_not_advance_rung_state(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        'lunar_mare_low_ti',
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    sim.start_campaign(CampaignPhase.C5)
    sim.melt.c5_enabled = True
    sim.melt.mre_target_species = 'SiO2'
    sim.melt.mre_max_voltage_V = 1.5
    sim.melt.temperature_C = 1600.0
    sim._mre_hold_hours = 2
    sim._mre_voltage_step_idx = 0
    sim._mre_rung_ever_effective = True
    sim._mre_effective_current_A = 0.0
    sim.melt.mre_declared_rung_V = 0.75
    sim._mre_uncertified_yield = {'previous': {'kg': 1.0}}
    sim._mre_ellingham_ladder_diagnostic = {'schema': 'previous'}

    def refused_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.ELECTROLYSIS_STEP:
            return IntentResult(
                intent=intent,
                status='refused',
                transition=None,
                diagnostic={'reason_refused': 'test_zero_transition_refusal'},
            )
        return IntentResult(intent=intent, status='ok', transition=None)

    monkeypatch.setattr(sim, '_dispatch_only', refused_dispatch)

    with pytest.raises(RuntimeError, match='test_zero_transition_refusal'):
        sim._step_mre()

    assert sim._mre_hold_hours == 2
    assert sim._mre_voltage_step_idx == 0
    assert sim._mre_rung_ever_effective is True
    assert sim.melt.mre_declared_rung_V == pytest.approx(0.75)
    assert sim._mre_uncertified_yield == {'previous': {'kg': 1.0}}
    assert sim._mre_ellingham_ladder_diagnostic == {'schema': 'previous'}
    assert sim._poisoned_hour is None


def test_shuttle_bakeout_cycle_counter_waits_for_successful_hour(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    sim = _build_sim(
        'lunar_mare_low_ti',
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
        additives_kg={'K': 1.0},
    )
    sim.start_campaign(CampaignPhase.C3_K)
    sim.melt.campaign_hour = 3
    sim.shuttle_cycle_K = 0
    monkeypatch.setattr(sim, '_update_temperature', lambda: None)
    monkeypatch.setattr(
        sim,
        '_apply_oxygen_reservoir_exchange',
        lambda: sim.melt.oxygen_reservoir,
    )
    monkeypatch.setattr(sim, '_apply_fe_redox_respeciation', lambda *a, **k: None)
    monkeypatch.setattr(sim, '_apply_native_fe_saturation_split', lambda *a, **k: None)
    monkeypatch.setattr(
        sim,
        '_get_equilibrium',
        lambda: (_ for _ in ()).throw(RuntimeError('post-bakeout abort')),
    )

    with pytest.raises(RuntimeError, match='post-bakeout abort'):
        sim.step()

    assert sim.shuttle_cycle_K == 0
    assert sim._pending_shuttle_bakeout_cycle_increment == ''
    assert sim._make_snapshot().shuttle_cycle == 0
    assert sim._poisoned_hour is None


def test_partial_commit_poison_survives_hostile_exception_formatting(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    class HostileAbort(BaseException):
        def __str__(self):
            raise RuntimeError('hostile exception __str__')

        def __repr__(self):
            raise RuntimeError('hostile exception __repr__')

        def __getattribute__(self, name):
            if name in {'__traceback__', '__context__', '__cause__'}:
                return super().__getattribute__(name)
            raise RuntimeError('hostile exception attribute access')

    sim = _configure_tick_authority_retry_sim(
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    hostile_abort = HostileAbort()

    def commit_then_abort():
        sim.atom_ledger.record(
            'hostile_exception_poison_probe',
            debits=(),
            credits=(),
        )
        raise hostile_abort

    monkeypatch.setattr(sim, '_step_one_hour', commit_then_abort)
    transition_count_before = len(sim.atom_ledger.transitions)

    first_error = None
    try:
        sim.step()
    except BaseException as exc:
        first_error = exc

    assert type(first_error) is HostileAbort
    assert first_error is hostile_abort
    assert len(sim.atom_ledger.transitions) == transition_count_before + 1
    assert sim._poisoned_hour is not None
    assert sim._poisoned_hour.hour == sim.melt.hour
    assert sim._poisoned_hour.committed_transition_count == 1
    transitions_after_abort = sim.atom_ledger.transitions

    with pytest.raises(PoisonedHourError):
        sim.step()

    assert sim.atom_ledger.transitions == transitions_after_abort


def test_aborted_respeciation_preserves_fo2_reference_state(
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
    sim.melt.temperature_C = 1600.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim.melt.oxygen_reservoir.reference_T_K = 1500.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas',
        {'O2': 10_000.0},
        source='test aborted respeciation oxygen',
    )
    monkeypatch.setattr(
        sim,
        '_freeze_gate_curve',
        lambda: {
            'source': 'test_real_curve',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1300.0,
        },
    )
    original_dispatch = sim._dispatch_only

    def abort_respeciation(intent, *args, **kwargs):
        if intent is ChemistryIntent.FE_REDOX_RESPECIATION:
            raise ProviderUnavailableError('test respeciation dispatch abort')
        return original_dispatch(intent, *args, **kwargs)

    monkeypatch.setattr(sim, '_dispatch_only', abort_respeciation)
    before_fO2 = sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log
    before_reference_T_K = sim.melt.oxygen_reservoir.reference_T_K
    before_transition_count = len(sim.atom_ledger.transitions)

    with pytest.raises(
        ProviderUnavailableError,
        match='test respeciation dispatch abort',
    ):
        sim._apply_fe_redox_respeciation()

    assert len(sim.atom_ledger.transitions) == before_transition_count
    assert sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log == before_fO2
    assert sim.melt.oxygen_reservoir.reference_T_K == before_reference_T_K


def test_source_terms_and_bubbler_share_failed_tick_authority(
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
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim.melt.oxygen_reservoir.reference_T_K = 1600.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    curve_calls = 0

    def fail_then_recover_curve():
        nonlocal curve_calls
        curve_calls += 1
        if curve_calls == 1:
            raise ProviderUnavailableError('source/bubbler transient failure')
        return {
            'source': 'test_recovered_real_curve',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1700.0,
            'path': ((1000.0, 0.0), (1700.0, 1.0)),
        }

    monkeypatch.setattr(sim, '_freeze_gate_curve', fail_then_recover_curve)
    monkeypatch.setattr(
        sim.campaign_mgr,
        'o2_bubbler_controls',
        lambda _campaign: {
            'o2_bubbler_kg_per_hr': 1.0,
            'o2_bubbler_eta_absorb_default': 0.75,
            'o2_bubbler_target_fO2_log': -2.0,
        },
    )
    authority = sim._establish_melt_redox_gate_authority_for_current_hour()

    source_reservoir = sim._apply_oxygen_reservoir_redox_source_terms(
        {'redox_source:test_tick_pin': 1.0},
    )
    bubbler = sim._apply_o2_bubbler()

    assert curve_calls == 1
    assert source_reservoir.melt_redox_capacity_mol_per_ln_fO2 > 0.0
    assert bubbler['reason'] == 'applied'
    assert sim._last_melt_redox_liquid_fraction_diagnostic['status'] == (
        'liquidus_unavailable_floor_fallback'
    )
    passthrough_transition = next(
        transition
        for transition in reversed(sim.atom_ledger.transitions)
        if transition.name == 'oxygen_bubbler_passthrough'
    )
    passthrough_transition.validate_conservation(sim.atom_ledger.registry)
    assert all(
        lot.source == 'melt_redox_gate_authority:fallback'
        for lot in (
            *passthrough_transition.debits,
            *passthrough_transition.credits,
        )
    )
    assert all(
        lot.meta['melt_redox_gate_authority']['kind'] == 'fallback'
        for lot in (
            *passthrough_transition.debits,
            *passthrough_transition.credits,
        )
    )

    sim._clear_melt_redox_gate_authority_for_completed_hour(sim.melt.hour)
    recovered = sim._resolved_melt_redox_gate_authority()

    assert curve_calls == 2
    assert recovered['source'] == 'test_recovered_real_curve'


def test_invalid_redox_gate_curve_does_not_poison_valid_retry(
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
    curve_calls = 0

    def invalid_then_valid_curve():
        nonlocal curve_calls
        curve_calls += 1
        if curve_calls == 1:
            return {
                'source': 'test_invalid_curve',
                'solidus_T_C': 1800.0,
                'liquidus_T_C': 1700.0,
            }
        return {
            'source': 'test_recovered_curve',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1700.0,
        }

    monkeypatch.setattr(sim, '_freeze_gate_curve', invalid_then_valid_curve)
    key = _freeze_gate_key_for_current_state(sim)

    invalid = sim._melt_redox_liquidus_gate_curve()

    assert invalid.liquidus_status == 'invalid'
    assert key not in sim._freeze_gate_liquid_fraction_curve_memo
    assert sim._freeze_gate_liquid_fraction_cache is None

    valid = sim._melt_redox_liquidus_gate_curve()

    assert curve_calls == 2
    assert valid == {
        'source': 'test_recovered_curve',
        'solidus_T_C': 1000.0,
        'liquidus_T_C': 1700.0,
    }
    assert sim._freeze_gate_liquid_fraction_curve_memo[key] == valid


def test_fallback_authorized_redox_transition_is_balanced_and_provenanced(
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
    sim.melt.temperature_C = 1600.0
    sim.melt.p_total_mbar = 10.0
    sim.melt.oxygen_reservoir.melt_intrinsic_fO2_log = -3.0
    sim.melt.oxygen_reservoir.reference_T_K = 1500.0 + 273.15
    sim._sync_oxygen_reservoir_mirror()
    sim.atom_ledger.load_external_mol(
        'process.overhead_gas',
        {'O2': 10_000.0},
        source='test fallback transition oxygen provenance',
    )

    def unavailable_curve():
        raise ProviderUnavailableError('fallback transition test failure')

    monkeypatch.setattr(sim, '_freeze_gate_curve', unavailable_curve)
    transition_count = len(sim.atom_ledger.transitions)

    diagnostic = sim._apply_fe_redox_respeciation()

    assert len(sim.atom_ledger.transitions) == transition_count + 1
    transition = sim.atom_ledger.transitions[-1]
    transition.validate_conservation(sim.atom_ledger.registry)
    assert transition.name == 'fe_redox_respeciation'
    assert transition.reason == 'fe_redox_respeciation'
    assert diagnostic['transition_name'] == transition.name
    assert diagnostic['gate_authority']['kind'] == 'fallback'
    assert all(
        lot.source == 'melt_redox_gate_authority:fallback'
        for lot in (*transition.debits, *transition.credits)
    )
    assert all(
        lot.meta['melt_redox_gate_authority']['fallback_status']
        == 'liquidus_unavailable_floor_fallback'
        for lot in (*transition.debits, *transition.credits)
    )
    fallback_record = sim._melt_redox_liquidus_gate_fallback_diagnostics[-1]
    assert fallback_record['status'] == 'liquidus_unavailable_floor_fallback'
    assert fallback_record['source'] == (
        'none:liquidus_unavailable'
    )


def test_fallback_diagnostic_log_survives_recovered_last_value(
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
    curve_calls = 0

    def fail_then_recover_curve():
        nonlocal curve_calls
        curve_calls += 1
        if curve_calls == 1:
            raise ProviderUnavailableError('durable diagnostic test failure')
        return {
            'source': 'test_recovered_curve',
            'solidus_T_C': 1000.0,
            'liquidus_T_C': 1700.0,
        }

    monkeypatch.setattr(sim, '_freeze_gate_curve', fail_then_recover_curve)

    sim._melt_redox_liquidus_gate_curve()
    fallback_record = dict(
        sim._melt_redox_liquidus_gate_fallback_diagnostics[0]
    )
    sim._melt_redox_liquidus_gate_curve()

    assert sim._last_melt_redox_liquidus_gate_diagnostic['status'] == 'ok'
    assert sim._melt_redox_liquidus_gate_fallback_count == 1
    assert list(sim._melt_redox_liquidus_gate_fallback_diagnostics) == [
        fallback_record
    ]
    assert fallback_record['status'] == 'liquidus_unavailable_floor_fallback'
    assert fallback_record['source'] == 'none:liquidus_unavailable'


def test_fallback_log_is_bounded_and_snapshot_serializes_hourly_summary(
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

    def unavailable_curve():
        raise ProviderUnavailableError('bounded fallback history failure')

    monkeypatch.setattr(sim, '_freeze_gate_curve', unavailable_curve)

    for _ in range(300):
        sim._melt_redox_liquidus_gate_curve()

    snapshot = sim._make_snapshot()
    summary = snapshot.oxygen_reservoir['melt_redox_gate_fallback']

    assert sim._melt_redox_liquidus_gate_fallback_count == 300
    assert len(sim._melt_redox_liquidus_gate_fallback_diagnostics) == 256
    assert len(sim._melt_redox_liquidus_gate_fallback_hourly) == 1
    assert summary['engaged'] is True
    assert summary['total_count'] == 300
    assert summary['history_maxlen'] == 256
    assert len(summary['recent']) == 256
    assert summary['recent_hourly'] == [{
        'campaign': sim.melt.campaign.name,
        'hour': int(sim.melt.hour),
        'campaign_hour': int(sim.melt.campaign_hour),
        'count': 300,
    }]
