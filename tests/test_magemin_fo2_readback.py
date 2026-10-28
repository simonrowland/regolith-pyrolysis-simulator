"""Absolute fO2 must be certified by native oxygen potential, not argv algebra."""

from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from simulator.engine_local_config import configured_magemin_binary_path
from simulator.melt_backend.magemin import MAGEMinBackend
import simulator.melt_backend.magemin as magemin_module


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


@pytest.mark.parametrize('requested', [-8.0, -12.0])
def test_live_c5_buffer_loss_is_numerical_failure(live_backend, requested):
    result = _solve(live_backend, _C5, requested)
    assert result.status == 'not_converged'
    assert result.diagnostics['buffer_phase_present'] is False
    assert result.diagnostics['solved_fO2_log'] is None
    assert result.diagnostics['authoritative_for_requested_conditions'] is False
    assert result.diagnostics['backend_failure_disposition'] == 'numerical'


def test_live_liquid_control_keeps_finite_native_value(live_backend):
    result = _solve(live_backend, _C5, -2.0)
    assert result.diagnostics['solved_fO2_log'] == pytest.approx(-5.467, abs=0.01)
    assert result.diagnostics['authoritative_for_requested_conditions'] is False
    notice = result.diagnostics['fO2_control_notice']
    assert notice['requested_fO2_log'] == -2.0
    assert notice['native_fO2_log'] == result.diagnostics['solved_fO2_log']
    assert notice['delta_log10'] == pytest.approx(notice['native_fO2_log'] + 2.0)
    assert result.status != 'ok'


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


@pytest.mark.parametrize('prior_good', [False, True])
@pytest.mark.parametrize('requested', [-8.0, -12.0])
def test_live_buffer_loss_reaches_existing_gate_hold_or_floor(
    live_backend, monkeypatch, prior_good, requested,
    vapor_pressure_data, feedstocks_data, setpoints_data,
):
    from tests.chemistry.test_declared_liquid_fraction_provider import _sim
    from tests.binding_admission_fixtures import register_synthetic_gate_curve_producer

    sim = _sim('magemin', vapor_pressure_data, feedstocks_data, setpoints_data)
    register_synthetic_gate_curve_producer(sim)
    if prior_good:
        sim._remember_freeze_gate_provider_curve({
            'source': 'gate_liquid_fraction', 'solidus_C': 1000.0,
            'liquidus_C': 1300.0, 'model': 'linear',
        })
    failed = _solve(live_backend, _C5, requested)

    def dispatch(*_args, **_kwargs):
        return SimpleNamespace(status=failed.status, diagnostic=failed.diagnostics,
                               warnings=tuple(failed.warnings))

    monkeypatch.setattr(sim, '_dispatch_only', dispatch)
    curve = sim._melt_redox_liquidus_gate_curve()
    notice = curve['gate_provider_refusal']
    assert notice['outcome'] == 'numerical_failure'
    assert notice['qualification'] == (
        'held_last_good_curve' if prior_good else 'first_call_kress91_floor_prediction'
    )
