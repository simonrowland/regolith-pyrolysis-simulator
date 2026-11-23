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


# engine_cache_off: patches an engines/melt_backend/openimcc solve dependency the outer double check does not see.
@pytest.mark.engine_cache_off
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


def test_retry_recovery_preserves_step_down_notice(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    sim._register_freeze_gate_liquid_fraction_providers()
    magemin, alpha = sim._chem_registry.gate_provider_order
    calls = []

    def primary(request):
        calls.append('magemin')
        if len(calls) == 1:
            return IntentResult(intent=request.intent, status='not_converged', diagnostic={
                'backend_status_reason': 'engine_worker_timeout',
            })
        return IntentResult(intent=request.intent, status='ok', diagnostic={
            'solidus_T_C': 1000, 'liquidus_T_C': 1300,
        })

    def alternate(request):
        calls.append('alphamelts')
        return IntentResult(intent=request.intent, status='not_converged', diagnostic={
            'backend_status_reason': 'incomplete_cooling_path',
            'backend_diagnostics': {
                'physical_overhead_pressure_bar': .009,
                'condensed_phase_reference_pressure_bar': 1,
                'numerical_path_warning': 'incomplete native path',
            },
        })

    monkeypatch.setattr(magemin, 'dispatch', primary)
    monkeypatch.setattr(alpha, 'dispatch', alternate)
    curve = sim._freeze_gate_curve_from_gate_dispatch([], fO2_log=-2)
    assert calls == ['magemin', 'alphamelts', 'magemin']
    assert curve['source'] == 'gate_liquid_fraction:fallback:magemin-shadow'
    assert (curve['solidus_T_C'], curve['liquidus_T_C']) == (1000, 1300)
    # The realized curve is the final magemin success. The abandoned
    # dispatch, including the non-timeout alphamelts notice, is one record.
    assert 'gate_provider_step_down' not in curve
    assert 'alphamelts_reference_pressure' not in curve
    record = curve['environment_retries']
    assert record['disposition'] == 'environment'
    notice = record['gate_provider_step_down']
    assert notice['requested_provider_order'] == ['magemin-shadow', 'alphamelts-diagnostic']
    assert [p['provider'] for p in notice['failed_providers']] == [
        'magemin-shadow', 'alphamelts-diagnostic',
    ]
    assert 'engine_worker_timeout' in notice['failed_providers'][0]['reason']
    assert notice['failed_providers'][0]['disposition'] == 'environment'
    assert 'disposition' not in notice['failed_providers'][1]
    assert 'incomplete_cooling_path' in notice['failed_providers'][1]['reason']
    assert record['alphamelts_reference_pressure'] == {
        'physical_overhead_pressure_bar': .009,
        'condensed_phase_reference_pressure_bar': 1,
        'numerical_path_warning': 'incomplete native path',
    }


def test_engine_worker_timeout_retries_freeze_gate_dispatch_once(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from simulator.engine_pool import EngineWorkerTimeout
    from simulator.melt_backend.magemin import MAGEMIN_WARM_CALL_TIMEOUT_S
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    calls = []

    def dispatch_only(*_args, **_kwargs):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            raise EngineWorkerTimeout(
                'fake MAGEMin worker',
                MAGEMIN_WARM_CALL_TIMEOUT_S,
                phase='job',
            )
        return IntentResult(
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            status='ok',
            diagnostic={'solidus_T_C': 1000, 'liquidus_T_C': 1300},
        )

    monkeypatch.setattr(sim, '_dispatch_only', dispatch_only)
    curve = sim._freeze_gate_curve_from_gate_dispatch([], fO2_log=-2)

    assert calls == [1, 2]
    assert curve['source'] == 'gate_liquid_fraction'
    assert curve['environment_retries']['disposition'] == 'environment'
    assert curve['environment_retries']['reason'] == 'engine_worker_timeout'


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


def _bind_gate_providers(sim, magemin_dispatch, alpha_dispatch):
    sim._register_freeze_gate_liquid_fraction_providers()
    magemin, alpha = sim._chem_registry.gate_provider_order
    magemin.dispatch = magemin_dispatch
    alpha.dispatch = alpha_dispatch
    return magemin, alpha


def _stripped_gate_bytes(value):
    from simulator.reduced_real_determinism import canonical_json_bytes
    from tests.chemistry.test_condensation_green_pins import deterministic_gate_payload

    return canonical_json_bytes(deterministic_gate_payload(value))


def _run_gate_shape(
    shape, monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
    *,
    shared_cache=None,
    core=False,
):
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    if shared_cache is not None:
        sim._freeze_gate_shared_curve_cache = shared_cache
    calls = []

    def primary(request):
        calls.append('magemin')
        if shape.startswith('recovered') and calls.count('magemin') == 1:
            return IntentResult(
                intent=request.intent, status='not_converged', diagnostic={
                    'backend_status_reason': 'engine_worker_timeout',
                },
            )
        if 'projected' in shape:
            return IntentResult(
                intent=request.intent, status='out_of_domain', diagnostic={
                    'backend_status_reason': 'composition_projected',
                },
            )
        return IntentResult(
            intent=request.intent, status='ok', diagnostic={
                'solidus_T_C': 1000, 'liquidus_T_C': 1300,
            },
        )

    def alternate(request):
        calls.append('alphamelts')
        if shape.startswith('recovered') and calls.count('alphamelts') == 1:
            return IntentResult(
                intent=request.intent, status='not_converged', diagnostic={
                    'backend_status_reason': 'incomplete_cooling_path',
                    'backend_diagnostics': {
                        'physical_overhead_pressure_bar': .009,
                        'condensed_phase_reference_pressure_bar': 1,
                        'numerical_path_warning': 'abandoned path',
                    },
                },
            )
        return IntentResult(
            intent=request.intent, status='ok', diagnostic={
                'solidus_T_C': 1100, 'liquidus_T_C': 1400,
                'backend_diagnostics': {
                    'physical_overhead_pressure_bar': .02,
                    'condensed_phase_reference_pressure_bar': 1,
                },
            },
        )

    magemin, alpha = _bind_gate_providers(sim, primary, alternate)
    monkeypatch.setattr(magemin, 'dispatch', primary)
    monkeypatch.setattr(alpha, 'dispatch', alternate)
    if core:
        record = sim._melt_redox_liquidus_gate_record()
    else:
        record = sim._freeze_gate_curve_record()
    return sim, record, calls


def test_recovered_dispatch_strips_to_the_clean_dispatch(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    """A recovered hour equals the final dispatch once the retry record is dropped."""

    def run(shape):
        return _run_gate_shape(
            shape, monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
        )

    clean_sim, clean_record, clean_calls = run('clean')
    recovered_sim, recovered_record, recovered_calls = run('recovered')
    assert clean_calls == ['magemin']
    assert recovered_calls == ['magemin', 'alphamelts', 'magemin']
    assert 'environment_retries' in recovered_record['curve']
    assert 'environment_retries' not in clean_record['curve']
    assert recovered_record['curve']['environment_retries']['disposition'] == 'environment'
    assert 'incomplete_cooling_path' in recovered_record['curve']['environment_retries'][
        'gate_provider_step_down'
    ]['failed_providers'][1]['reason']
    assert _stripped_gate_bytes(clean_record['curve']) == _stripped_gate_bytes(
        recovered_record['curve'],
    )
    assert _stripped_gate_bytes(clean_record) == _stripped_gate_bytes(recovered_record)
    assert clean_sim._freeze_gate_last_good_curve.get('realized_provider') == (
        recovered_sim._freeze_gate_last_good_curve.get('realized_provider')
    )

    assert clean_sim._freeze_gate_last_good_curve.get('realized_provider') is None

    projected_sim, projected_record, projected_calls = run('projected')
    recovered_projected_sim, recovered_projected_record, recovered_projected_calls = run(
        'recovered-projected',
    )
    assert projected_calls == ['magemin', 'alphamelts']
    assert recovered_projected_calls == ['magemin', 'alphamelts', 'magemin', 'alphamelts']
    projected_curve = projected_record['curve']
    recovered_projected_curve = recovered_projected_record['curve']
    assert projected_curve['source'] == (
        'gate_liquid_fraction:fallback:alphamelts-diagnostic'
    )
    assert 'composition_projected' in projected_curve['gate_provider_step_down'][
        'failed_providers'
    ][0]['reason']
    assert projected_curve['alphamelts_reference_pressure'][
        'physical_overhead_pressure_bar'
    ] == .02
    assert 'environment_retries' not in projected_curve
    abandoned = recovered_projected_curve['environment_retries']
    assert abandoned['disposition'] == 'environment'
    assert abandoned['alphamelts_reference_pressure'][
        'physical_overhead_pressure_bar'
    ] == .009
    assert 'incomplete_cooling_path' in abandoned['gate_provider_step_down'][
        'failed_providers'
    ][1]['reason']
    assert recovered_projected_curve['alphamelts_reference_pressure'][
        'physical_overhead_pressure_bar'
    ] == .02
    assert 'incomplete_cooling_path' not in recovered_projected_curve[
        'gate_provider_step_down'
    ]['failed_providers'][0]['reason']
    assert _stripped_gate_bytes(projected_curve) == _stripped_gate_bytes(
        recovered_projected_curve,
    )
    assert _stripped_gate_bytes(projected_record) == _stripped_gate_bytes(
        recovered_projected_record,
    )
    assert projected_sim._freeze_gate_last_good_curve.get('realized_provider') == (
        'alphamelts-diagnostic'
    )
    assert recovered_projected_sim._freeze_gate_last_good_curve.get(
        'realized_provider',
    ) == 'alphamelts-diagnostic'


def test_store_payload_omits_environment_retries(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data, tmp_path,
):
    """A recovered timeout is not curve identity in the PT-0 store."""

    from simulator.chemistry.kernel import ChemistryIntent
    from simulator.reduced_real_determinism import (
        PT0DeterminismStore,
        _curve_from_payload,
        canonical_json_bytes,
        canonical_replay_key,
        canonical_replay_output_projection,
    )

    def stored(sim, curve):
        key = canonical_replay_key(
            sim,
            artifact='freeze_gate_curve',
            intent=ChemistryIntent.GATE_LIQUID_FRACTION,
            fO2_log=float(sim._current_melt_redox_fO2_log()),
            fe_redox_policy='intrinsic',
            provider_role='fallback',
        )
        payload = canonical_replay_output_projection(
            'freeze_gate_curve', sim, curve,
        )
        return key, payload

    clean_sim, clean_record, _clean_calls = _run_gate_shape(
        'clean', monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
    )
    recovered_sim, recovered_record, _recovered_calls = _run_gate_shape(
        'recovered', monkeypatch,
        vapor_pressure_data, feedstocks_data, setpoints_data,
    )
    clean_curve = clean_record['curve']
    recovered_curve = recovered_record['curve']
    assert recovered_curve['environment_retries']['disposition'] == 'environment'
    clean_key, clean_payload = stored(clean_sim, clean_curve)
    recovered_key, recovered_payload = stored(recovered_sim, recovered_curve)
    assert canonical_json_bytes(clean_key) == canonical_json_bytes(recovered_key)
    assert canonical_json_bytes(clean_payload) == canonical_json_bytes(recovered_payload)
    assert 'environment_retries' not in clean_payload['curve']
    assert 'environment_retries' not in recovered_payload['curve']
    assert 'environment_retries' in recovered_curve
    assert 'environment_retries' not in (
        recovered_sim._freeze_gate_liquid_fraction_cache['curve']
    )

    store = PT0DeterminismStore('capture', db_path=tmp_path / 'pt1.sqlite')
    store._store('freeze_gate_curve', clean_key, clean_payload)
    store._store('freeze_gate_curve', recovered_key, recovered_payload)
    assert len(store.entries) == 1
    stored_curve = next(iter(store.entries.values()))['payload']['curve']
    replayed = _curve_from_payload(stored_curve)
    assert 'environment_retries' not in replayed
    poisoned = {
        **stored_curve,
        'environment_retries': recovered_curve['environment_retries'],
    }
    assert 'environment_retries' not in _curve_from_payload(poisoned)


def test_later_hour_memo_hit_omits_environment_retries(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    """A memo hit does not publish the producing hour's timeout.

    The hour that retried still has environment_retries on its live
    snapshot. The same-sim cache, the shared memo, and the core memo
    each return an identity curve to a later hour.
    """

    from tests.chemistry.test_declared_liquid_fraction_provider import _sim

    shared = {}
    sim, record, calls = _run_gate_shape(
        'recovered', monkeypatch, vapor_pressure_data, feedstocks_data,
        setpoints_data, shared_cache=shared, core=True,
    )
    assert calls == ['magemin', 'alphamelts', 'magemin']
    assert record['curve']['environment_retries']['disposition'] == 'environment'
    assert 'environment_retries' not in (
        sim._freeze_gate_liquid_fraction_cache['curve']
    )
    assert sim._freeze_gate_liquid_fraction_curve_memo
    for stored in sim._freeze_gate_liquid_fraction_curve_memo.values():
        assert 'environment_retries' not in stored['curve']
    assert shared
    for stored in shared.values():
        assert 'environment_retries' not in stored['curve']

    diagnostic = sim._last_melt_redox_liquidus_gate_diagnostic
    assert diagnostic['environment_retries']['disposition'] == 'environment'
    sim._melt_redox_gate_authority_this_tick = record
    sim._melt_redox_gate_authority_tick_hour = int(sim.melt.hour)
    sim._freeze_gate_liquid_fraction_factor()
    assert sim._last_freeze_gate_diagnostic['environment_retries'][
        'disposition'
    ] == 'environment'
    snapshot = sim._make_snapshot()
    assert snapshot.liquid_fraction_gate['redox']['environment_retries'][
        'disposition'
    ] == 'environment'
    assert snapshot.liquid_fraction_gate['evaporation']['environment_retries'][
        'disposition'
    ] == 'environment'

    produced = len(calls)
    sim.melt.hour = int(sim.melt.hour) + 1
    same_sim_hit = sim._freeze_gate_curve_record()
    assert same_sim_hit['disposition'] == 'memo-hit'
    assert 'environment_retries' not in same_sim_hit['curve']
    assert len(calls) == produced

    sim._freeze_gate_liquid_fraction_cache = None
    core_hit = sim._melt_redox_liquidus_gate_record()
    assert core_hit['disposition'] == 'memo-hit'
    assert 'environment_retries' not in core_hit['curve']
    assert 'environment_retries' not in (
        sim._last_melt_redox_liquidus_gate_diagnostic
    )
    assert len(calls) == produced
    later = sim._make_snapshot()
    assert 'environment_retries' not in later.liquid_fraction_gate['redox']

    other = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    other._freeze_gate_shared_curve_cache = shared
    other_calls = []

    def refuse(request):
        other_calls.append(request)
        raise AssertionError('shared memo miss dispatched')

    magemin, alpha = _bind_gate_providers(other, refuse, refuse)
    monkeypatch.setattr(magemin, 'dispatch', refuse)
    monkeypatch.setattr(alpha, 'dispatch', refuse)
    shared_hit = other._freeze_gate_curve_record()
    assert shared_hit['disposition'] == 'memo-hit'
    assert 'environment_retries' not in shared_hit['curve']
    assert other_calls == []


def _clear_gate_memos(sim):
    sim._freeze_gate_liquid_fraction_cache = None
    memo = sim._freeze_gate_liquid_fraction_curve_memo
    if isinstance(memo, dict):
        memo.clear()
    shared = getattr(sim, '_freeze_gate_shared_curve_cache', None)
    if isinstance(shared, dict):
        shared.clear()
    sim._freeze_gate_held_curve_record = None


def _hold_snapshot(sim, record):
    sim._freeze_gate_liquid_fraction_factor(gate_authority=record['curve'])
    return sim._make_snapshot()


def test_held_last_good_omits_prior_environment_retries(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    """A later hold publishes this hour's retry, not an earlier hour's.

    Last-good is kept past the hour, so it stores the identity curve.
    A hold that did not retry has no environment_retries. A hold that
    did retry carries only the record this dispatch abandoned.
    """

    from simulator.melt_backend.magemin import (
        MAGEMinBackend,
        _magemin_bulk_projection_details,
    )

    def recovered():
        sim, record, _calls = _run_gate_shape(
            'recovered', monkeypatch, vapor_pressure_data, feedstocks_data,
            setpoints_data, core=True,
        )
        assert record['curve']['environment_retries']['reason'] == (
            'engine_worker_timeout'
        )
        assert 'environment_retries' not in sim._freeze_gate_last_good_curve['curve']
        _clear_gate_memos(sim)
        sim.melt.hour = int(sim.melt.hour) + 1
        return sim

    # (1) Later miss, no_convergence, hold. This hour did not retry.
    sim = recovered()
    calls = []

    def no_convergence(request):
        calls.append(request)
        return IntentResult(intent=request.intent, status='not_converged', diagnostic={
            'backend_status_reason': 'no_convergence',
        })

    magemin, alpha = _bind_gate_providers(sim, no_convergence, no_convergence)
    monkeypatch.setattr(magemin, 'dispatch', no_convergence)
    monkeypatch.setattr(alpha, 'dispatch', no_convergence)
    record = sim._melt_redox_liquidus_gate_record()
    assert calls
    assert record['disposition'] == 'live'
    assert record['curve']['gate_provider_refusal']['qualification'] == (
        'held_last_good_curve'
    )
    assert record['curve']['gate_provider_refusal']['reason'] == 'no_convergence'
    assert 'environment_retries' not in record['curve']
    assert 'environment_retries' not in sim._last_melt_redox_liquidus_gate_diagnostic
    snapshot = _hold_snapshot(sim, record)
    assert 'environment_retries' not in snapshot.liquid_fraction_gate['redox']
    assert 'environment_retries' not in snapshot.liquid_fraction_gate['evaporation']

    # (2) This hour retried, then held. Only its own abandoned dispatch.
    sim = recovered()

    def retried_then_numerical(request):
        calls.append('magemin')
        if calls.count('magemin') == 1:
            return IntentResult(
                intent=request.intent, status='not_converged', diagnostic={
                    'backend_status_reason': 'aggregate_budget_exceeded',
                },
            )
        return IntentResult(intent=request.intent, status='not_converged', diagnostic={
            'backend_status_reason': 'no_convergence',
        })

    def numerical_alternate(request):
        calls.append('alphamelts')
        return IntentResult(intent=request.intent, status='not_converged', diagnostic={
            'backend_status_reason': 'no_convergence',
        })

    calls = []
    magemin, alpha = _bind_gate_providers(
        sim, retried_then_numerical, numerical_alternate,
    )
    monkeypatch.setattr(magemin, 'dispatch', retried_then_numerical)
    monkeypatch.setattr(alpha, 'dispatch', numerical_alternate)
    record = sim._melt_redox_liquidus_gate_record()
    abandoned = record['curve']['environment_retries']
    assert abandoned['reason'] == 'aggregate_budget_exceeded'
    assert abandoned['hour'] == int(sim.melt.hour)
    assert 'engine_worker_timeout' not in json.dumps(abandoned)
    assert record['curve']['gate_provider_refusal']['qualification'] == (
        'held_last_good_curve'
    )
    snapshot = _hold_snapshot(sim, record)
    assert snapshot.liquid_fraction_gate['redox']['environment_retries']['hour'] == (
        int(sim.melt.hour)
    )
    assert snapshot.liquid_fraction_gate['evaporation']['environment_retries'][
        'reason'
    ] == 'aggregate_budget_exceeded'

    # (3) Stable-refusal hold, with and without a retry on this hour.
    projection = MAGEMinBackend()._build_db_bulk_projection({
        'SiO2': 85.0, 'Al2O3': 10.0, 'CaO': 3.0, 'MnO': 2.0,
    })
    projected = {
        'backend_status_reason': 'composition_projected',
        'input_composition_projection': _magemin_bulk_projection_details(projection),
        'solidus_T_C': 900.0,
        'liquidus_T_C': 1100.0,
    }

    def stable_dispatch(intent, **kwargs):
        return IntentResult(intent=intent, status='out_of_domain', diagnostic=projected)

    sim = recovered()
    monkeypatch.setattr(sim, '_dispatch_only', stable_dispatch)
    record = sim._melt_redox_liquidus_gate_record()
    assert 'composition_projected_over_threshold' in record['curve'][
        'composition_projected_notice'
    ]['reason']
    assert 'environment_retries' not in record['curve']
    assert 'environment_retries' not in sim._last_melt_redox_liquidus_gate_diagnostic
    snapshot = _hold_snapshot(sim, record)
    assert 'environment_retries' not in snapshot.liquid_fraction_gate['redox']
    assert 'environment_retries' not in snapshot.liquid_fraction_gate['evaporation']

    sim = recovered()
    attempt = {'n': 0}

    def retried_then_stable(intent, **kwargs):
        attempt['n'] += 1
        if attempt['n'] == 1:
            return IntentResult(
                intent=intent, status='not_converged', diagnostic={
                    'backend_status_reason': 'aggregate_budget_exceeded',
                },
            )
        return IntentResult(intent=intent, status='out_of_domain', diagnostic=projected)

    monkeypatch.setattr(sim, '_dispatch_only', retried_then_stable)
    record = sim._melt_redox_liquidus_gate_record()
    abandoned = record['curve']['environment_retries']
    assert abandoned['reason'] == 'aggregate_budget_exceeded'
    assert abandoned['hour'] == int(sim.melt.hour)
    assert 'engine_worker_timeout' not in json.dumps(abandoned)
    assert 'composition_projected_over_threshold' in record['curve'][
        'composition_projected_notice'
    ]['reason']
    snapshot = _hold_snapshot(sim, record)
    assert snapshot.liquid_fraction_gate['redox']['environment_retries']['hour'] == (
        int(sim.melt.hour)
    )


def test_engine_worker_timeout_outside_planner_keeps_its_row(
    monkeypatch, vapor_pressure_data, feedstocks_data, setpoints_data,
):
    """A timeout that never reaches the planner still has a visible row."""

    from simulator.engine_pool import EngineWorkerTimeout
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    calls = []

    def dispatch(intent, **kwargs):
        calls.append(intent)
        if len(calls) == 1:
            raise EngineWorkerTimeout('magemin', 15.0, phase='liquidus')
        return IntentResult(intent=intent, status='ok', diagnostic={
            'solidus_T_C': 1000, 'liquidus_T_C': 1300,
        })

    monkeypatch.setattr(sim, '_dispatch_only', dispatch)
    curve = sim._freeze_gate_curve_from_gate_dispatch([], fO2_log=-2)
    assert len(calls) == 2
    assert curve['source'] == 'gate_liquid_fraction'
    assert (curve['solidus_T_C'], curve['liquidus_T_C']) == (1000, 1300)
    assert 'gate_provider_step_down' not in curve
    record = curve['environment_retries']
    assert record['disposition'] == 'environment'
    attempt = record['gate_provider_step_down']['failed_providers'][0]
    assert attempt['provider'] == 'magemin'
    assert attempt['disposition'] == 'environment'
    assert 'engine_worker_timeout' in attempt['reason']
    assert 'gate_provider_step_down' not in attempt['diagnostic']
    assert record['gate_provider_step_down']['realized_provider'] is None
