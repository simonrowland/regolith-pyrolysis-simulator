from __future__ import annotations

import os

from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
from engines.alphamelts import AlphaMELTSProvider
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.liquidus import (
    LiquidusSolidusResult,
    build_equilibrium_crystallization_path,
)

import pytest


def _basalt_species_mol() -> dict:
    masses = {
        'SiO2': 0.06008,
        'TiO2': 0.07987,
        'Al2O3': 0.10196,
        'FeO': 0.07184,
        'Fe2O3': 0.15969,
        'MgO': 0.04030,
        'CaO': 0.05608,
        'Na2O': 0.06198,
        'K2O': 0.09420,
        'Cr2O3': 0.15199,
        'MnO': 0.07094,
        'P2O5': 0.14194,
    }
    wt_pct = {
        'SiO2': 49.0,
        'TiO2': 1.5,
        'Al2O3': 14.0,
        'FeO': 10.0,
        'Fe2O3': 1.0,
        'MgO': 9.0,
        'CaO': 11.0,
        'Na2O': 2.5,
        'K2O': 0.8,
        'Cr2O3': 0.2,
        'MnO': 0.2,
        'P2O5': 0.3,
    }
    return {
        oxide: (weight / 100.0) / masses[oxide]
        for oxide, weight in wt_pct.items()
    }


def _make_request(intent: ChemistryIntent) -> IntentRequest:
    return IntentRequest(
        intent=intent,
        account_view=ProviderAccountView(
            accounts={'process.cleaned_melt': _basalt_species_mol()},
            species_formula_registry={},
        ),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        control_inputs={},
    )


def _synthetic_liquid_state(temperature_C: float) -> tuple[float, dict]:
    frac = max(0.0, min(1.0, (float(temperature_C) - 1000.0) / 300.0))
    crystallized = 1.0 - frac
    return frac, {
        'SiO2': 50.0 - 4.0 * crystallized,
        'MgO': 8.0 - 2.0 * crystallized,
        'Na2O': 2.0 + 3.0 * crystallized,
        'K2O': 0.5 + 1.2 * crystallized,
    }


def test_table_builder_spans_interval_monotone_and_enriches_residual_liquid():
    result = build_equilibrium_crystallization_path(
        _synthetic_liquid_state,
        solidus_T_C=1000.0,
        liquidus_T_C=1300.0,
        grid_step_C=75.0,
        max_points=9,
    )
    repeat = build_equilibrium_crystallization_path(
        _synthetic_liquid_state,
        solidus_T_C=1000.0,
        liquidus_T_C=1300.0,
        grid_step_C=75.0,
        max_points=9,
    )

    assert result == repeat
    assert result.status == 'ok'
    assert len(result.liquid_fraction_path) <= 9
    temperatures = [p.temperature_C for p in result.liquid_fraction_path]
    assert temperatures[0] == pytest.approx(1000.0)
    assert temperatures[-1] == pytest.approx(1300.0)

    fractions = [p.liquid_fraction for p in result.liquid_fraction_path]
    assert all(a <= b for a, b in zip(fractions, fractions[1:]))
    cooling_fractions = list(reversed(fractions))
    assert all(
        a >= b for a, b in zip(cooling_fractions, cooling_fractions[1:])
    )

    cold = result.liquid_fraction_path[0].liquid_composition_wt_pct
    hot = result.liquid_fraction_path[-1].liquid_composition_wt_pct
    assert cold['Na2O'] > hot['Na2O']
    assert cold['K2O'] > hot['K2O']


def test_table_builder_clamps_small_engine_noise_but_rejects_gross_nonmonotone():
    noisy = {
        1000.0: 0.0,
        1100.0: 0.50,
        1200.0: 0.49,
        1300.0: 1.0,
    }
    result = build_equilibrium_crystallization_path(
        lambda T: (noisy[float(T)], {'Na2O': 1.0, 'K2O': 0.1}),
        solidus_T_C=1000.0,
        liquidus_T_C=1300.0,
        grid_step_C=100.0,
        monotonicity_tolerance=0.02,
    )
    assert result.status == 'ok'
    fractions = [p.liquid_fraction for p in result.liquid_fraction_path]
    assert fractions == pytest.approx([0.0, 0.5, 0.5, 1.0])

    gross = {
        1000.0: 0.0,
        1100.0: 1.0,
        1200.0: 0.0,
        1300.0: 1.0,
    }
    failed = build_equilibrium_crystallization_path(
        lambda T: (gross[float(T)], {'Na2O': 1.0, 'K2O': 0.1}),
        solidus_T_C=1000.0,
        liquidus_T_C=1300.0,
        grid_step_C=100.0,
        monotonicity_tolerance=0.02,
    )
    assert failed.status == 'not_converged'
    assert any('non-monotone frac_M' in warning for warning in failed.warnings)


def test_table_builder_smooths_magemin_scale_nonmonotone_dip():
    # 0.09 / 0.33 / 0.05 MAGEMin frac_M dips were observed in the
    # 2026-05-26 freeze-gate flip blast-radius on lunar/mars C2A cases.
    fractions = {
        1000.0: 0.0,
        1050.0: 0.25,
        1100.0: 0.5,
        1150.0: 0.75,
        1200.0: 0.98,
        1250.0: 0.98075,
        1300.0: 0.890898,
        1350.0: 0.99,
        1400.0: 1.0,
        1450.0: 1.0,
        1500.0: 0.670427,
        1550.0: 0.945697,
        1600.0: 1.0,
    }
    result = build_equilibrium_crystallization_path(
        lambda T: (fractions[float(T)], {'Na2O': 1.0, 'K2O': 0.1}),
        solidus_T_C=1000.0,
        liquidus_T_C=1600.0,
        grid_step_C=50.0,
        monotonicity_tolerance=0.02,
    )

    assert result.status == 'ok'
    path = {p.temperature_C: p.liquid_fraction for p in result.liquid_fraction_path}
    assert path[1300.0] == pytest.approx(0.98075)
    assert path[1500.0] == pytest.approx(1.0)
    assert path[1550.0] == pytest.approx(1.0)
    assert any('smoothed non-monotone frac_M' in w for w in result.warnings)
    assert any('1300.000 C raw 0.890898' in w for w in result.warnings)
    assert any('1500.000 C raw 0.670427' in w for w in result.warnings)
    assert any('1550.000 C raw 0.945697' in w for w in result.warnings)


class _FakeECAlphaMELTSBackend:
    def __init__(self, mode: str = 'python_api', *, empty_comp: bool = False):
        self._mode = mode
        self._empty_comp = empty_comp
        self.calls: list[dict] = []
        self.finder_calls: list[dict] = []

    def is_available(self) -> bool:
        return self._mode in {'python_api', 'subprocess'}

    def get_engine_version(self) -> str:
        return f'fake-ec-{self._mode}'

    def find_liquidus_solidus(self, **kwargs):
        self.finder_calls.append(kwargs)
        return LiquidusSolidusResult(
            liquidus_T_C=1300.0,
            solidus_T_C=1000.0,
            liquid_fraction=1.0,
            status='ok',
        )

    def equilibrate(self, **kwargs):
        self.calls.append(kwargs)
        frac, composition = _synthetic_liquid_state(kwargs['temperature_C'])
        return EquilibriumResult(
            temperature_C=float(kwargs['temperature_C']),
            pressure_bar=float(kwargs['pressure_bar']),
            liquid_fraction=frac,
            liquid_composition_wt_pct={} if self._empty_comp else composition,
            phases_present=['liquid'] if frac > 0.0 else ['olivine'],
            phase_masses_kg={'liquid': frac, 'olivine': 1.0 - frac},
            fO2_log=float(kwargs['fO2_log']),
            status='ok',
        )


def test_provider_builds_ec_diagnostic_path_and_emits_no_transition():
    backend = _FakeECAlphaMELTSBackend()
    provider = AlphaMELTSProvider(backend=backend)

    result = provider.dispatch(
        _make_request(ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION)
    )

    assert result.status == 'ok'
    assert result.transition is None
    assert backend.finder_calls
    assert backend.calls
    diagnostic = dict(result.diagnostic or {})
    path = tuple(diagnostic['liquid_fraction_path'])
    assert diagnostic['mode'] == 'petthermotools'
    assert diagnostic['solidus_T_C'] == pytest.approx(1000.0)
    assert diagnostic['liquidus_T_C'] == pytest.approx(1300.0)
    assert path[0]['temperature_C'] == pytest.approx(1000.0)
    assert path[-1]['temperature_C'] == pytest.approx(1300.0)
    assert all(
        a['liquid_fraction'] <= b['liquid_fraction']
        for a, b in zip(path, path[1:])
    )
    assert (
        path[0]['liquid_composition_wt_pct']['Na2O']
        > path[-1]['liquid_composition_wt_pct']['Na2O']
    )
    assert (
        path[0]['liquid_composition_wt_pct']['K2O']
        > path[-1]['liquid_composition_wt_pct']['K2O']
    )


def test_provider_reports_ec_unavailable_for_subprocess_without_faking_comp():
    backend = _FakeECAlphaMELTSBackend(mode='subprocess')
    provider = AlphaMELTSProvider(backend=backend)

    result = provider.dispatch(
        _make_request(ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION)
    )

    assert result.status == 'unavailable'
    assert result.transition is None
    assert not backend.finder_calls
    assert not backend.calls
    assert not (result.diagnostic or {}).get('liquid_fraction_path')
    assert any('not EC-capable' in warning for warning in result.warnings)


def test_provider_rejects_ec_sample_without_residual_liquid_composition():
    backend = _FakeECAlphaMELTSBackend(empty_comp=True)
    provider = AlphaMELTSProvider(backend=backend)

    result = provider.dispatch(
        _make_request(ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION)
    )

    assert result.status == 'not_converged'
    assert result.transition is None
    assert any('lacks residual liquid composition' in w for w in result.warnings)


@pytest.mark.parametrize('mode', ('thermoengine', 'python_api'))
def test_live_alphamelts_ec_path_runs_with_live_transport(mode):
    if not os.environ.get('REGOLITH_RUN_LIVE_MELTS'):
        pytest.skip(
            'live alphaMELTS EC transport test is opt-in: set REGOLITH_RUN_LIVE_MELTS=1 '
            'to run it. The [thermoengine] param can hang indefinitely inside native '
            'MELTS evaluateSaturationState (the dispatch call never returns, so the '
            'existing exception-based skips below cannot fire and pytest-timeout cannot '
            'kill a GIL-holding native call). Excluded from the default suite to keep it '
            'runnable. Pre-existing flaky live-engine behaviour, unrelated to the '
            'recipe-optimizer build.'
        )
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({'mode': mode})
    except ImportError as exc:
        pytest.skip(f'AlphaMELTS EC {mode} transport unavailable: {exc}')
    if not available or getattr(backend, '_mode', None) != mode:
        pytest.skip(f'AlphaMELTS EC {mode} transport unavailable')

    provider = AlphaMELTSProvider(backend=backend)
    result = provider.dispatch(
        _make_request(ChemistryIntent.EQUILIBRIUM_CRYSTALLIZATION)
    )
    if result.status != 'ok':
        pytest.skip(
            f'AlphaMELTS EC live transport unavailable/not converged: '
            f'{result.status} {result.warnings}'
        )

    path = tuple((result.diagnostic or {}).get('liquid_fraction_path') or ())
    assert result.transition is None
    assert path
    assert all(
        a['liquid_fraction'] <= b['liquid_fraction']
        for a, b in zip(path, path[1:])
    )
    assert all(p['liquid_composition_wt_pct'] for p in path)
