"""Continuation contracts; native bracket evidence is t-1560 step 1."""
from types import SimpleNamespace
import json
from pathlib import Path
import subprocess

import pytest

from engines.alphamelts import AlphaMELTSProvider
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.config import normalize_chemistry_kernel_config
from simulator.chemistry.kernel.dto import ProviderAccountView
from simulator.melt_backend.alphamelts import AlphaMELTSBackend
from simulator.melt_backend.alphamelts import (
    _alphamelts_backend_failure_error, ALPHAMELTS_REASON_CALC_MODE_MISMATCH,
)
from simulator.melt_backend.liquidus import EquilibriumCrystallizationPathResult
from simulator.chemistry.kernel import IntentResult
from simulator.accounting.formulas import resolve_species_formula
from tests.chemistry.conftest import vapor_pressure_data, feedstocks_data, setpoints_data


def test_alphamelts_is_a_declared_gate_provider():
    assert normalize_chemistry_kernel_config({
        'liquid_fraction_provider': 'alphamelts',
    })['liquid_fraction_provider'] == 'alphamelts'


def _tables(temperature, fraction, *, enthalpy='-100', liquid=True):
    system = ('index Pressure Temperature mass F H S fO2(absolute)\n'
              f'1 1 {temperature} 100 {fraction} {enthalpy} 10 -2\n')
    phases = f'index 1 Pressure 1 Temperature {temperature} SiO2 MgO\n'
    if liquid:
        phases += f'liquid1 {100 * fraction} -100 10 40 1 0 50 50\n'
    if fraction < 1:
        phases += f'olivine1 {100 * (1-fraction)} -100 10 40 1 Mg2SiO4 50 50\n'
    return system, phases


@pytest.mark.parametrize('temperature,fraction,enthalpy,liquid', [
    (950, 0, 'nan', False), (950, 0, '-100', True),
    (960, .1, '-100', False),
])
def test_cooling_row_refuses_nan_or_inconsistent_phases(
    temperature, fraction, enthalpy, liquid,
):
    backend = AlphaMELTSBackend()
    system, phases = _tables(temperature, fraction, enthalpy=enthalpy, liquid=liquid)
    with pytest.raises(ValueError):
        backend._parse_gate_cooling_row(system, phases, temperature_C=temperature,
                                       pressure_bar=1, fO2_log=-2)


def test_cooling_row_keeps_a_checked_zero():
    system, phases = _tables(950, 0, liquid=False)
    point = AlphaMELTSBackend()._parse_gate_cooling_row(
        system, phases, temperature_C=950, pressure_bar=1, fO2_log=-2,
    )
    assert point.temperature_C == 950
    assert point.liquid_fraction == 0


@pytest.mark.parametrize('native_fraction,phase_fraction', [
    (0.0, 0.0), (0.333333, 1 / 3), (1.0, 1.0),
])
def test_cooling_row_native_fraction_numeric_pin(native_fraction, phase_fraction):
    system, phases = _tables(1200, phase_fraction, liquid=phase_fraction > 0)
    system = system.replace(f'100 {phase_fraction} ', f'100 {native_fraction} ')
    point = AlphaMELTSBackend()._parse_gate_cooling_row(
        system, phases, temperature_C=1200, pressure_bar=1, fO2_log=-2,
    )
    # Preserve native table rounding even when phase masses imply more digits.
    assert point.liquid_fraction.hex() == native_fraction.hex()


def test_cooling_row_requires_system_and_finite_phase_thermodynamics():
    backend = AlphaMELTSBackend()
    with pytest.raises(ValueError, match='system header'):
        backend._parse_gate_cooling_row('', '', temperature_C=950, pressure_bar=1, fO2_log=-2)
    system, phases = _tables(950, 0, liquid=False)
    phases = phases.replace('olivine1 100 -100 10', 'olivine1 100 nan 10')
    with pytest.raises(ValueError, match='phase thermodynamics'):
        backend._parse_gate_cooling_row(system, phases, temperature_C=950, pressure_bar=1, fO2_log=-2)


def test_cooling_row_rejects_an_adjacent_printed_fo2_value():
    system, phases = _tables(950, 0, liquid=False)
    system = system.replace('10 -2\n', '10 -2.001\n')
    with pytest.raises(ValueError, match='fO2'):
        AlphaMELTSBackend()._parse_gate_cooling_row(
            system, phases, temperature_C=950, pressure_bar=1, fO2_log=-2,
        )


@pytest.mark.parametrize('reason', [
    'missing_input', 'invalid_input', 'invalid_fo2_log',
    'invalid_pressure_bar', 'non_finite_composition',
])
def test_terminal_input_outcome_pin_before_owner_reuse(reason):
    from simulator.reduced_real_determinism import gate_curve_provider_outcome_for_status

    assert gate_curve_provider_outcome_for_status('ok', {'reason': reason}).value == 'failed'


def test_exit_zero_without_rows_is_a_failed_provider(monkeypatch):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._timeout_s = 20
    monkeypatch.setattr(backend, '_composition_for_liquidus_finder', lambda **_: {'SiO2': 50, 'MgO': 50})
    monkeypatch.setattr('simulator.melt_backend.alphamelts._run_alphamelts_subprocess',
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, '', ''))
    result = backend.gate_cooling_path(
        pressure_bar=1, fO2_log=-2, composition_mol_by_account={}, species_formula_registry={},
    )
    assert result.status == 'not_converged'
    assert result.diagnostics['reason'] == 'incomplete_cooling_path'
    assert result.solidus_T_C is None
    assert not result.liquid_fraction_path


@pytest.mark.parametrize('contract_failure', [False, True])
def test_subprocess_gate_uses_reference_pressure(contract_failure):
    calls = []

    def cooling_path(**kw):
        calls.append(kw)
        if contract_failure:
            raise _alphamelts_backend_failure_error(
                ALPHAMELTS_REASON_CALC_MODE_MISMATCH, 'injected ambient mode mismatch',
            )
        return EquilibriumCrystallizationPathResult(
            status='not_converged', diagnostics={'reason': 'incomplete_cooling_path'},
            warnings=('stopped at 1170 C',),
        )

    backend = SimpleNamespace(
        _mode='subprocess', is_available=lambda: True,
        get_engine_version=lambda: 'test',
        gate_cooling_path=cooling_path,
    )
    request = IntentRequest(
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        account_view=ProviderAccountView(
            accounts={'process.cleaned_melt': {'SiO2': 1, 'MgO': 1, 'FeO': .1}},
            species_formula_registry={},
        ),
        temperature_C=1400, pressure_bar=.009, fO2_log=-2,
        control_inputs={},
    )
    result = AlphaMELTSProvider(backend).dispatch(request)
    assert result.status == ('unavailable' if contract_failure else 'not_converged')
    assert calls[0]['pressure_bar'] == 1
    assert result.diagnostic['backend_diagnostics'][
        'physical_overhead_pressure_bar'] == .009
    assert result.diagnostic['backend_diagnostics'][
        'condensed_phase_reference_pressure_bar'] == 1
    assert not result.diagnostic['liquid_fraction_path']
    assert result.diagnostic['solidus_T_C'] is None
    if contract_failure:
        assert result.diagnostic['backend_status_reason'] == ALPHAMELTS_REASON_CALC_MODE_MISMATCH


@pytest.mark.parametrize('bulk,pressure', [('s-type', .009), ('c5', .05)])
def test_live_cooling_bounds_or_incomplete_refusal(bulk, pressure):
    backend = AlphaMELTSBackend()
    if not backend.initialize({'mode': 'subprocess'}):
        pytest.skip('live alphaMELTS subprocess unavailable')
    wt = json.loads((Path(__file__).parent / 'fixtures/alphamelts_gate' / f'{bulk}.json').read_text())
    mol = {oxide: value / resolve_species_formula(oxide, {}).molar_mass_g_mol
           for oxide, value in wt.items()}
    request = IntentRequest(
        intent=ChemistryIntent.GATE_LIQUID_FRACTION,
        account_view=ProviderAccountView(
            accounts={'process.cleaned_melt': mol}, species_formula_registry={},
        ),
        temperature_C=1400, pressure_bar=pressure, fO2_log=-2, control_inputs={},
    )
    try:
        result = AlphaMELTSProvider(backend).dispatch(request)
    finally:
        backend.close()
    diagnostic = result.diagnostic
    details = diagnostic['backend_diagnostics']
    assert details['physical_overhead_pressure_bar'] == pressure
    assert details['condensed_phase_reference_pressure_bar'] == 1
    if bulk == 's-type':
        assert result.status == 'ok', result
        assert details['solidus_bracket_C'] == [950, 960]
        # Current native continuation output: numeric identity pin before
        # moving boundary selection into its shared liquidus owner.
        assert (diagnostic['solidus_T_C'], diagnostic['liquidus_T_C']) == (950.0, 1630.0)
        assert 950 <= diagnostic['solidus_T_C'] <= 960
        assert diagnostic['liquid_fraction_path'][0]['liquid_fraction'] == 0
    else:
        assert result.status == 'not_converged', result
        assert details['last_completed_temperature_C'] == 1170
        assert details['reason'] == 'incomplete_cooling_path'
        assert diagnostic['solidus_T_C'] is None
        assert not diagnostic['liquid_fraction_path']


@pytest.mark.parametrize('alpha_ok,prior_good', [
    (True, False), (False, False), (False, True), (False, 'alphamelts'), (None, False),
])
def test_gate_order_steps_down_before_hold_or_floor(
    monkeypatch, alpha_ok, prior_good, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    magemin, alpha = sim._chem_registry.gate_provider_order
    calls = []

    def dispatch(provider, status, diagnostic):
        def run(request):
            calls.append(provider)
            return IntentResult(intent=request.intent, status=status, diagnostic=diagnostic)
        return run

    monkeypatch.setattr(magemin, 'dispatch', dispatch('magemin', 'not_converged', {
        'backend_status_reason': 'no_convergence',
    }))
    monkeypatch.setattr(alpha, 'dispatch', dispatch('alphamelts', (
        'ok' if alpha_ok else 'unavailable' if alpha_ok is None else 'not_converged'
    ), {
        'solidus_T_C': 950 if alpha_ok else None,
        'liquidus_T_C': 1630 if alpha_ok else None,
        'backend_status_reason': None if alpha_ok else 'incomplete_cooling_path',
        'backend_diagnostics': {'physical_overhead_pressure_bar': .009,
                                'condensed_phase_reference_pressure_bar': 1},
    }))
    if prior_good:
        sim._remember_freeze_gate_provider_curve({
            'source': 'gate_liquid_fraction:fallback:' + (
                'alphamelts-diagnostic' if prior_good == 'alphamelts' else 'magemin-shadow'
            ),
            'solidus_T_C': 1000, 'liquidus_T_C': 1300,
            **({'gate_provider_step_down': {'realized_provider': 'alphamelts-diagnostic'}}
               if prior_good == 'alphamelts' else {}),
            **({'alphamelts_reference_pressure': {
                'physical_overhead_pressure_bar': .05,
                'condensed_phase_reference_pressure_bar': 1,
                'solidus_bracket_C': [1000, 1010],
                'numerical_path_warning': 'original solved path warning',
            }} if prior_good == 'alphamelts' else {}),
        })
    curve = sim._freeze_gate_curve_from_gate_dispatch([], fO2_log=-2)
    assert calls == ['magemin', 'alphamelts']
    notice = curve['gate_provider_step_down']
    assert notice['failed_providers'][0]['provider'] == 'magemin-shadow'
    assert 'no_convergence' in notice['failed_providers'][0]['reason']
    if not prior_good:
        assert curve['alphamelts_reference_pressure']['physical_overhead_pressure_bar'] == .009
    if alpha_ok:
        from simulator.reduced_real_determinism import _is_cacheable_gate_curve

        assert curve['source'] == 'gate_liquid_fraction:fallback:alphamelts-diagnostic'
        assert curve['solidus_T_C'] == 950
        assert notice['realized_provider'] == 'alphamelts-diagnostic'
        assert not _is_cacheable_gate_curve(curve)
    else:
        assert len(notice['failed_providers']) == 2
        assert 'incomplete_cooling_path' in notice['failed_providers'][1]['reason']
        expected = 'held_last_good_curve' if prior_good else 'first_call_kress91_floor_prediction'
        assert curve['gate_provider_refusal']['qualification'] == expected
        assert curve['solidus_T_C'] == (1000 if prior_good else 1200)
        assert curve['gate_provider_refusal']['status'] == 'not_converged'
        if prior_good == 'alphamelts':
            assert notice['realized_provider'] == 'alphamelts-diagnostic'
            solved = curve['alphamelts_reference_pressure']
            assert solved['physical_overhead_pressure_bar'] == .05
            assert solved['solidus_bracket_C'] == [1000, 1010]
            assert solved['numerical_path_warning'] == 'original solved path warning'


def test_magemin_success_does_not_call_alpha(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    magemin, alpha = sim._chem_registry.gate_provider_order
    monkeypatch.setattr(magemin, 'dispatch', lambda request: IntentResult(
        intent=request.intent, status='ok', diagnostic={'solidus_T_C': 1000, 'liquidus_T_C': 1300},
    ))
    monkeypatch.setattr(alpha, 'dispatch', lambda _: pytest.fail('MAGEMin success stepped down'))
    curve = sim._freeze_gate_curve_from_gate_dispatch([], fO2_log=-2)
    assert curve['source'] == 'gate_liquid_fraction:fallback:magemin-shadow'
    assert curve['solidus_T_C'] == 1000
    assert curve['liquidus_T_C'] == 1300
    assert 'gate_provider_step_down' not in curve
    assert alpha._backend is None
