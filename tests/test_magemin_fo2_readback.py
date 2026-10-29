"""Absolute fO2 must be certified by native oxygen potential, not argv algebra."""

from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from simulator.engine_local_config import configured_magemin_binary_path
from simulator.melt_backend.magemin import MAGEMinBackend
import simulator.melt_backend.magemin as magemin_module
from tests.chemistry.conftest import vapor_pressure_data, feedstocks_data, setpoints_data


# t-1561's archived h97 C5 *projected* dispatch, not its earlier bulk snapshot.
_C5 = dict(zip(MAGEMinBackend._IG_BULK_ORDER, (
    55.26659765380637, 16.925820590782514, 13.791407875375269,
    11.278768891141643, 0.0010413653423760171, 0.057574531497528336,
    0.000004786816995217007, 1.8803600403714642, 0.00013047955667813124,
    0.42734694006419893, 0.0,
)))
_BASALT = dict(zip(MAGEMinBackend._IG_BULK_ORDER, (
    49, 14, 11, 9, 11, 0.8, 2.5, 1.5, 2, 0.2, 0,
)))
_FERROUS = {
    'SiO2': 49.0,
    'Al2O3': 14.0,
    'CaO': 11.0,
    'MgO': 9.0,
    'FeO': 11.0,
    'K2O': 0.8,
    'Na2O': 2.5,
    'TiO2': 1.5,
    'Cr2O3': 0.2,
}


@pytest.fixture
def live_backend(request):
    binary = configured_magemin_binary_path() or MAGEMinBackend._locate_binary(None)
    if binary is None:
        pytest.skip('compiled MAGEMin unavailable')
    backend = MAGEMinBackend()
    request.addfinalizer(backend.close)
    assert backend.initialize({'binary_path': str(binary), 'warm_worker': False})
    return backend


def _solve(backend, bulk, requested, temperature_C=1500.0):
    return backend.equilibrate(
        temperature_C, composition_kg={'SiO2': 1.0},
        projected_oxide_wt_pct=bulk, fO2_log=requested, pressure_bar=0.05,
    )


@pytest.mark.parametrize('temperature_K', [1200.0, 1723.0, 2273.0])
@pytest.mark.parametrize('pressure_bar', [1.0, 1e-6])
def test_live_absolute_target_uses_independent_native_readback(
    live_backend, monkeypatch, temperature_K, pressure_bar,
):
    native_reads = []
    run = magemin_module.subprocess.run

    def record(*args, **kwargs):
        completed = run(*args, **kwargs)
        path = Path(kwargs['cwd']) / 'output' / '_matlab_output.txt'
        if path.exists():
            match = re.search(r'^log10\(fO2\)\s+(\S+)', path.read_text(), re.M)
            if match:
                native_reads.append(float(match[1]))
        return completed

    monkeypatch.setattr(magemin_module.subprocess, 'run', record)
    result = live_backend.equilibrate(
        temperature_K - 273.15, composition_kg={'SiO2': 1.0},
        projected_oxide_wt_pct=_BASALT, fO2_log=-9.0, pressure_bar=pressure_bar,
    )
    assert native_reads, 'every solve must request native output'
    assert result.diagnostics['solved_fO2_log'] == native_reads[-1]
    assert native_reads[-1] == pytest.approx(-9.0, abs=1e-5)
    assert result.diagnostics['authoritative_for_requested_conditions'] is True
    assert result.diagnostics['fO2_clamped'] is False
    assert result.temperature_C == temperature_K - 273.15
    if temperature_K > 1420:
        assert result.diagnostics['qfm_reference_notice']['kind'] == 'out_of_certified_band'
        assert any('out_of_certified_band' in warning for warning in result.warnings)


def test_live_ferrous_only_bulk_uses_separate_buffer_oxygen_reservoir(live_backend):
    feedstock_projection = live_backend._build_db_bulk_projection(_FERROUS)
    assert feedstock_projection.composition_wt_pct['FeOt'] == 11.0
    assert feedstock_projection.composition_wt_pct.get('O', 0.0) == 0.0

    result = live_backend.equilibrate(
        1200.0,
        composition_kg={'SiO2': 1.0},
        projected_oxide_wt_pct=_FERROUS,
        fO2_log=-9.0,
        pressure_bar=0.05,
    )

    diagnostics = result.diagnostics
    assert diagnostics['buffer_phase_present'] is True
    assert diagnostics['solved_fO2_log'] == pytest.approx(
        -9.0, abs=magemin_module._MAGEMIN_FO2_TOL_DEX,
    )
    assert diagnostics['authoritative_for_requested_conditions'] is True
    assert diagnostics['magemin_feedstock_oxygen_wt_pct'] == 0.0
    assert diagnostics['magemin_buffer_oxygen_reservoir_wt_pct'] == pytest.approx(
        magemin_module._MAGEMIN_BUFFER_O_RESERVOIR_WT_PCT,
    )
    assert diagnostics['magemin_cli_oxygen_wt_pct'] == pytest.approx(
        magemin_module._MAGEMIN_BUFFER_O_RESERVOIR_WT_PCT,
    )


def test_live_buffer_reservoir_size_does_not_move_gate_or_silicate_assemblage(
    live_backend, monkeypatch,
):
    base_reservoir = magemin_module._MAGEMIN_BUFFER_O_RESERVOIR_WT_PCT
    run = live_backend._call_magemin_subprocess
    signatures = []
    observed_calls = []

    def record(**kwargs):
        raw = run(**kwargs)
        observed_calls.append(raw['operating_point_diagnostics'])
        return raw

    monkeypatch.setattr(live_backend, '_call_magemin_subprocess', record)
    for scale in (0.5, 1.0, 2.0):
        observed_calls.clear()
        reservoir_wt_pct = base_reservoir * scale
        monkeypatch.setattr(
            magemin_module,
            '_MAGEMIN_BUFFER_O_RESERVOIR_WT_PCT',
            reservoir_wt_pct,
        )
        gate = live_backend.find_liquidus_solidus(
            composition_kg={'SiO2': 1.0},
            projected_oxide_wt_pct=_FERROUS,
            fO2_log=-9.0,
            pressure_bar=0.05,
            min_T_C=400.0,
            max_T_C=2200.0,
            scan_step_C=50.0,
            tolerance_C=2.0,
        )
        assert gate.solidus_T_C is not None, gate.warnings
        assert gate.liquidus_T_C is not None, gate.warnings

        at_solidus = live_backend.equilibrate(
            gate.solidus_T_C,
            composition_kg={'SiO2': 1.0},
            projected_oxide_wt_pct=_FERROUS,
            fO2_log=-9.0,
            pressure_bar=0.05,
        )
        signatures.append((
            gate.solidus_T_C,
            gate.liquidus_T_C,
            tuple(sorted(at_solidus.phases_present)),
        ))
        assert observed_calls
        assert all(call['buffer_phase_present'] for call in observed_calls)
        assert all(
            call['solved_fO2_log'] == pytest.approx(
                -9.0, abs=magemin_module._MAGEMIN_FO2_TOL_DEX,
            )
            for call in observed_calls
        )
        assert all(
            call['magemin_buffer_oxygen_reservoir_wt_pct']
            == pytest.approx(reservoir_wt_pct)
            for call in observed_calls
        )

    reference = signatures[1]
    for solidus, liquidus, phases in signatures:
        assert solidus == pytest.approx(reference[0], abs=2.0)
        assert liquidus == pytest.approx(reference[1], abs=2.0)
        assert phases == reference[2]


@pytest.mark.parametrize('requested', [-8.0, -12.0])
def test_live_c5_buffer_reservoir_honours_requested_fO2(live_backend, requested):
    result = _solve(live_backend, _C5, requested)
    assert result.status == 'ok'
    assert result.diagnostics['buffer_phase_present'] is True
    assert result.diagnostics['solved_fO2_log'] == pytest.approx(
        requested, abs=magemin_module._MAGEMIN_FO2_TOL_DEX,
    )
    assert result.diagnostics['authoritative_for_requested_conditions'] is True
    assert result.diagnostics['magemin_cli_oxygen_wt_pct'] == pytest.approx(
        result.diagnostics['magemin_feedstock_oxygen_wt_pct']
        + result.diagnostics['magemin_buffer_oxygen_reservoir_wt_pct'],
    )


def test_live_liquid_control_is_corrected_to_requested_native_value(live_backend):
    result = _solve(live_backend, _C5, -2.0)
    assert result.diagnostics['solved_fO2_log'] == pytest.approx(
        -2.0, abs=magemin_module._MAGEMIN_FO2_TOL_DEX,
    )
    assert result.diagnostics['authoritative_for_requested_conditions'] is True
    assert result.status == 'ok'


@pytest.mark.parametrize('native', [None, 'nan', '-inf', '-8.5'])
def test_subprocess_readback_cannot_self_confirm(monkeypatch, native):
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = 'subprocess'
    backend._binary_path = Path('/fake/MAGEMin')

    def run(_args, *, cwd, **kwargs):
        output = Path(cwd) / 'output'
        output.mkdir(exist_ok=True)
        (output / '_matlab_output.txt').write_text(
            '' if native is None else f'log10(fO2) {native}\n'
        )
        return SimpleNamespace(returncode=0, stderr='', stdout=(
            'Status : 0 [success]\nPhase : liq qfm\nMode : 0.99 0.01\n'
        ))

    monkeypatch.setattr(magemin_module.subprocess, 'run', run)
    result = _solve(backend, _BASALT, -9.0, 1449.85)
    assert result.diagnostics['authoritative_for_requested_conditions'] is False
    if native == '-8.5':
        assert result.diagnostics['solved_fO2_log'] == -8.5
        assert result.status == 'out_of_domain'
        assert result.diagnostics['fO2_control_notice']['delta_log10'] == 0.5
    else:
        assert result.status == 'not_converged'
        assert result.diagnostics['solved_fO2_log'] is None


@pytest.mark.parametrize('bridge', ['pymagemin', 'julia'])
@pytest.mark.parametrize('native', [None, float('-inf'), -8.5, -9.0])
def test_optional_bridge_uses_native_struct_fields(bridge, native):
    backend = MAGEMinBackend()
    backend._available = True
    backend._bridge = bridge
    raw = dict(fO2=native, ph=['liq', 'qfm'], phases={'liq': {'mass_kg': 1.0}})
    if bridge == 'pymagemin':
        backend._magemin_module = SimpleNamespace(minimize=lambda **kwargs: raw)
    else:
        backend._magemin_module = SimpleNamespace(Main=SimpleNamespace(
            MAGEMin=SimpleNamespace(single_point_minimization=lambda *args: SimpleNamespace(**raw)),
        ))
    result = _solve(backend, _BASALT, -9.0)
    assert result.diagnostics['authoritative_for_requested_conditions'] is (native == -9.0)
    assert result.status == ('ok' if native == -9.0 else (
        'out_of_domain' if native == -8.5 else 'not_converged'
    ))


@pytest.mark.parametrize('second_readback', [-9.0, None])
def test_corrective_resolve_shares_budget_and_requires_fresh_output(
    monkeypatch, second_readback,
):
    backend = MAGEMinBackend()
    backend._binary_path = Path('/fake/MAGEMin')
    clock = [0.0]
    timeouts = []

    def run(_args, *, cwd, timeout, **kwargs):
        timeouts.append(timeout)
        clock[0] += 0.4
        output = Path(cwd) / 'output'
        output.mkdir(exist_ok=True)
        readback = -9.05 if len(timeouts) == 1 else second_readback
        if readback is not None:
            (output / '_matlab_output.txt').write_text(f'log10(fO2) {readback}\n')
        return SimpleNamespace(returncode=0, stderr='', stdout=(
            'Phase : liq qfm\nMode : 0.99 0.01\n'
        ))

    monkeypatch.setattr(magemin_module.subprocess, 'run', run)
    monkeypatch.setattr(magemin_module.time, 'monotonic', lambda: clock[0])
    raw = backend._call_magemin_subprocess(
        bulk_projection=backend._build_db_bulk_projection(_BASALT),
        temperature_C=1449.85, pressure_kbar=0.001, fO2_log=-9.0,
        call_timeout_s=1.0,
    )
    assert timeouts == pytest.approx([1.0, 0.6])
    diagnostics = raw['operating_point_diagnostics']
    assert diagnostics['solved_fO2_log'] == second_readback
    assert diagnostics['authoritative_for_requested_conditions'] is (second_readback is not None)


@pytest.mark.parametrize('prior_good', [False, True])
@pytest.mark.parametrize('requested,native,phases,modes', [
    (-8.0, None, None, None),
    (-12.0, None, None, None),
    (-9.0, '-inf', 'liq', '0.9'),
    (-9.0, '-8.5', 'liq qfm', '0.89 0.01'),
])
def test_control_failure_reaches_existing_gate_hold_or_floor(
    live_backend, monkeypatch, prior_good, requested, native, phases, modes,
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim
    from tests.binding_admission_fixtures import register_synthetic_gate_curve_producer
    from engines.magemin.provider import MAGEMinShadowProvider

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    register_synthetic_gate_curve_producer(sim)
    if native is not None:
        def run(_args, *, cwd, **kwargs):
            output = Path(cwd) / 'output'
            output.mkdir(exist_ok=True)
            (output / '_matlab_output.txt').write_text(f'log10(fO2) {native}\n')
            return SimpleNamespace(returncode=0, stderr='', stdout=(
                f'Status : 0 [success]\nPhase : {phases}\nMode : {modes}\n'
            ))

        monkeypatch.setattr(magemin_module.subprocess, 'run', run)
    failed = live_backend.find_liquidus_solidus(
        projected_oxide_wt_pct=_C5, fO2_log=requested, pressure_bar=0.05,
        min_T_C=1500.0, max_T_C=1550.0, scan_step_C=50.0,
    )
    good = prior_good
    projected = MAGEMinShadowProvider._project_equilibrium(
        failed, mode='subprocess', engine_version='test',
    ).as_diagnostic()

    def dispatch(*_args, **_kwargs):
        if good:
            return SimpleNamespace(status='ok', diagnostic={
                'solidus_T_C': 1000.0, 'liquidus_T_C': 1300.0,
            }, warnings=())
        return SimpleNamespace(status=failed.status, diagnostic=projected,
                               warnings=tuple(failed.warnings))

    monkeypatch.setattr(sim, '_dispatch_only', dispatch)
    if prior_good:
        assert sim._melt_redox_liquidus_gate_curve()['source'] == 'gate_liquid_fraction'
        good = False
        sim.melt.hour += 1
        sim.melt.p_total_mbar += 100.0
    curve = sim._melt_redox_liquidus_gate_curve()
    notice = curve['gate_provider_refusal']
    assert notice['outcome'] == 'numerical_failure'
    assert notice['qualification'] == (
        'held_last_good_curve' if prior_good else 'first_call_kress91_floor_prediction'
    )
    if native is not None:
        diagnostics = failed.diagnostics
        assert diagnostics['backend_status_reason'] == 'engine_not_converged'
        assert diagnostics['magemin_mode_vector_mass_deficit'] is True
        assert diagnostics['magemin_mode_vector_deficit'] == pytest.approx(0.1)
        assert diagnostics['authoritative_for_requested_conditions'] is False
        assert diagnostics['solved_fO2_log'] == (None if native == '-inf' else -8.5)
        assert any('mass deficit=' in warning for warning in failed.warnings)
