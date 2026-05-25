"""Regression tests for the default-off evaporation freeze gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from simulator.chemistry.kernel import ChemistryIntent, ProviderUnavailableError
from simulator.melt_backend.base import EquilibriumResult
from tests.chemistry.conftest import _build_sim


def _set_freeze_gate(setpoints_data: dict, *, enabled: bool) -> dict:
    setpoints = dict(setpoints_data)
    gate = dict(setpoints.get('freeze_gate', {}) or {})
    gate['enabled'] = enabled
    setpoints['freeze_gate'] = gate
    return setpoints


def _equilibrium() -> EquilibriumResult:
    return EquilibriumResult(vapor_pressures_Pa={'Na': 1.0}, status='ok')


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
                diagnostic={'evaporation_flux_kg_hr': {'Na': 7.5}},
            )
        raise AssertionError(f'unexpected liquidus dispatch: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    flux = sim._calculate_evaporation(_equilibrium())

    assert sim._freeze_gate_enabled() is False
    assert calls == [ChemistryIntent.EVAPORATION_FLUX]
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
    ec_calls = 0

    def fake_dispatch(intent, *args, **kwargs):
        nonlocal ec_calls
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        if intent is ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION:
            ec_calls += 1
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
        raise AssertionError(f'unexpected dispatch: {intent}')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    rates = []
    for temperature_C in (950.0, 1150.0, 1400.0):
        sim.melt.temperature_C = temperature_C
        flux = sim._calculate_evaporation(_equilibrium())
        rates.append(flux.species_kg_hr.get('Na', 0.0))

    assert rates == pytest.approx([0.0, 5.0, 10.0])
    assert rates == sorted(rates)
    assert ec_calls == 1
    assert sim._last_freeze_gate_diagnostic['source'] == (
        'equilibrium_crystallization'
    )


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
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        if intent is ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION:
            raise ProviderUnavailableError('EC provider absent')
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

    def fake_dispatch(intent, *args, **kwargs):
        if intent is ChemistryIntent.EVAPORATION_FLUX:
            return SimpleNamespace(
                diagnostic={'evaporation_flux_kg_hr': {'Na': 10.0}},
            )
        raise ProviderUnavailableError(f'{intent.value} provider absent')

    monkeypatch.setattr(sim, '_dispatch_only', fake_dispatch)

    with pytest.raises(RuntimeError, match='freeze_gate.enabled requires'):
        sim._calculate_evaporation(_equilibrium())
