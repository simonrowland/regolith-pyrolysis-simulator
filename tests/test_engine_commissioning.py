"""Project-owned engine commissioning table (t-894)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.alphamelts.domain import (
    DEFAULT_SILICATE_NETWORK_BAND_WT_PCT,
    DEFAULT_SIO2_MAX_WT_PCT,
    DEFAULT_SIO2_MIN_WT_PCT,
    _SIO2_CRASH_FLOOR_WT_PCT,
)
from engines.engine_commissioning import (
    PUBLISHED_AUTHORITY_OUTSIDE,
    PUBLISHED_SIO2_CERTIFIED_WT_PCT,
    PUBLISHED_SIO2_CRASH_FLOOR_WT_PCT,
    PUBLISHED_TEMPERATURE_CERTIFIED_K,
    EngineCommissioningError,
    assess_engine_commissioning,
    engine_commissioning,
    load_engine_commissioning,
)
from engines.alphamelts.thermoengine import ThermoEnginePayload
from simulator.melt_backend.alphamelts import (
    ALPHAMELTS_REASON_SUBPROCESS_DIED,
    ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C,
    AlphaMELTSBackend,
)
from simulator.melt_backend.base import EquilibriumResult
from simulator.melt_backend.melt_envelope import MELT_ENVELOPE_CONSTANTS
from simulator.melt_backend.thermoengine import ThermoEngineBackend
from simulator.physical_constants import CELSIUS_TO_KELVIN_OFFSET


def _in_band_pot() -> dict[str, float]:
    return {
        'SiO2': 49.0,
        'Al2O3': 15.0,
        'FeO': 10.0,
        'Fe2O3': 1.0,
        'MgO': 10.0,
        'CaO': 10.0,
        'Na2O': 5.0,
    }


def _out_of_band_high_silica() -> dict[str, float]:
    return {'SiO2': 85.0, 'MgO': 15.0}


def _valid_table_payload() -> dict:
    engine = {
        'sio2_wt_pct': {
            'certified': [30.0, 80.0],
            'observed_crash_floor_wt_pct': 34.0,
        },
        'temperature_K': {'certified': [1073.15, 1700.0]},
        'authority_outside': 'extrapolated',
        'source': {
            'kind': 'adapter_default',
            'ref': 'test',
            'note': 'unit-test table',
        },
    }
    return {
        'schema_version': 1,
        'engines': {
            'alphamelts': dict(engine),
            'thermoengine': dict(engine),
        },
    }


def _write_table(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload), encoding='utf-8')
    return path


def _spy_prepared(called: list):
    def spy(**kwargs):
        called.append(kwargs)
        return EquilibriumResult(
            temperature_C=kwargs['temperature_C'],
            status='ok',
            liquid_fraction=1.0,
            phases_present=['liquid'],
            diagnostics=dict(kwargs.get('crash_diagnostics') or {}),
            warnings=list(kwargs.get('warnings') or []),
        )

    return spy


def test_table_loads_and_pins_published_defaults() -> None:
    """Pin table defaults to the adapter/domain constants they replace.

    Derivation (t-894): these were hardcoded at
    engines/alphamelts/domain.py DEFAULT_SIO2_MIN_WT_PCT = 30.0,
    DEFAULT_SIO2_MAX_WT_PCT = 80.0 (DEFAULT_SILICATE_NETWORK_BAND_WT_PCT),
    _SIO2_CRASH_FLOOR_WT_PCT = 34.0; simulator/melt_backend/alphamelts.py
    ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C = 800.0 (1073.15 K); and
    simulator/melt_backend/melt_envelope.py
    MELT_ENVELOPE_CONSTANTS['MELTS-v1.0']['T_calib_max_K'] = 1700.0.
    """
    table = load_engine_commissioning()
    assert table.schema_version == 1
    for name in ('alphamelts', 'thermoengine'):
        spec = table.engines[name]
        assert spec.sio2_wt_pct.certified.as_tuple() == (
            PUBLISHED_SIO2_CERTIFIED_WT_PCT
        )
        assert spec.temperature_K.certified.as_tuple() == (
            PUBLISHED_TEMPERATURE_CERTIFIED_K
        )
        assert spec.authority_outside == PUBLISHED_AUTHORITY_OUTSIDE
        assert spec.source.kind == 'adapter_default'

    alphamelts = table.engines['alphamelts']
    thermoengine = table.engines['thermoengine']
    assert (
        alphamelts.sio2_wt_pct.observed_crash_floor_wt_pct
        == PUBLISHED_SIO2_CRASH_FLOOR_WT_PCT
    )
    assert thermoengine.sio2_wt_pct.observed_crash_floor_wt_pct is None

    assert DEFAULT_SILICATE_NETWORK_BAND_WT_PCT == PUBLISHED_SIO2_CERTIFIED_WT_PCT
    assert DEFAULT_SIO2_MIN_WT_PCT == 30.0
    assert DEFAULT_SIO2_MAX_WT_PCT == 80.0
    assert _SIO2_CRASH_FLOOR_WT_PCT == 34.0
    assert (
        ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C + CELSIUS_TO_KELVIN_OFFSET
        == pytest.approx(1073.15)
    )
    assert MELT_ENVELOPE_CONSTANTS['MELTS-v1.0']['T_calib_max_K'] == 1700.0
    alphamelts = engine_commissioning('alphamelts')
    assert alphamelts.sio2_wt_pct.certified.as_tuple() == (
        DEFAULT_SILICATE_NETWORK_BAND_WT_PCT
    )
    assert (
        alphamelts.sio2_wt_pct.observed_crash_floor_wt_pct
        == _SIO2_CRASH_FLOOR_WT_PCT
    )
    assert alphamelts.temperature_K.certified.minimum == pytest.approx(
        ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C + CELSIUS_TO_KELVIN_OFFSET
    )


def test_bad_key_is_rejected(tmp_path: Path) -> None:
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['not_a_field'] = 1
    path = _write_table(tmp_path / 'bad-key.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='unknown key'):
        load_engine_commissioning(path)


def test_inverted_band_is_rejected(tmp_path: Path) -> None:
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['sio2_wt_pct']['certified'] = [80.0, 30.0]
    path = _write_table(tmp_path / 'inverted.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='inverted'):
        load_engine_commissioning(path)


def test_in_band_pot_has_no_notice_and_calls_engine(monkeypatch) -> None:
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._engine_version = 'test'
    called: list = []
    monkeypatch.setattr(backend, '_equilibrate_prepared', _spy_prepared(called))

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=_in_band_pot(),
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert called, 'in-band pot must call the engine'
    assert 'commissioning_notice' not in result.diagnostics
    assert result.diagnostics.get('authority') is None


def test_out_of_band_pot_notices_and_still_calls_engine(monkeypatch) -> None:
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._engine_version = 'test'
    called: list = []
    monkeypatch.setattr(backend, '_equilibrate_prepared', _spy_prepared(called))

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=_out_of_band_high_silica(),
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert called, 'out-of-band pot must still call the engine'
    notice = result.diagnostics['commissioning_notice']
    assert notice['kind'] == 'engine_commissioning'
    assert result.diagnostics['authority'] == 'extrapolated'
    assert result.diagnostics['certified_band']['sio2_wt_pct'] == [30.0, 80.0]
    assert result.diagnostics['certified_band']['temperature_K'] == [
        1073.15,
        1700.0,
    ]
    assert any('CommissioningNotice' in warning for warning in result.warnings)


def test_thermoengine_out_of_band_notices_and_still_calls_engine(
    monkeypatch,
) -> None:
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._engine_version = 'test'
    called: list = []
    monkeypatch.setattr(backend, '_equilibrate_prepared', _spy_prepared(called))

    result = backend.equilibrate(
        temperature_C=2200.0,
        composition_kg=_in_band_pot(),
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert called, 'thermoengine must run outside the certified T band'
    assert result.diagnostics['authority'] == 'extrapolated'
    assert result.diagnostics['certified_band']['temperature_K'] == [
        1073.15,
        1700.0,
    ]
    assert result.diagnostics['commissioning_notice']['reason'] == (
        'temperature_range'
    )


def test_assess_in_band_has_no_notice() -> None:
    assessment = assess_engine_commissioning(
        'alphamelts',
        sio2_wt_pct=50.0,
        temperature_K=1500.0,
    )
    assert assessment.below_crash_floor is False
    assert assessment.outside_certified is False
    assert assessment.notice is None


def test_assess_out_of_band_notice_shape() -> None:
    assessment = assess_engine_commissioning(
        'alphamelts',
        sio2_wt_pct=90.0,
        temperature_K=2400.0,
    )
    assert assessment.below_crash_floor is False
    assert assessment.outside_certified is True
    assert assessment.notice is not None
    assert assessment.notice['authority'] == 'extrapolated'
    assert 'certified_band' in assessment.notice
    assert 'silicate_network_band' in assessment.failed_constraints
    assert 'temperature_range' in assessment.failed_constraints


def _system_main_fixture(temperature_C: float, fO2_log: float = -9.0) -> str:
    return (
        "System Thermodynamic Data:\n"
        "index Pressure Temperature mass F phi H S V Cp dVdP*10^6 "
        "dVdT*10^6 fO2(absolute) fO2-9.0) rhol rhos viscosity aH2O chisqr\n"
        f"1 1.00 {temperature_C:.6f} 100.0 1 1 -1 1 1 1 0 0 "
        f"{fO2_log:.6f} 0 2.638918 0 1.409 n/a n/a\n"
    )


def _install_alphamelts_transport_spy(monkeypatch, backend, *, temperature_C=1400.0):
    """Spy the real subprocess launch boundary and emit an all-liquid parse."""
    calls: list = []
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    backend._engine_version = 'test'

    def fake_run(args, **kwargs):
        argv = list(args[0] if args and isinstance(args[0], (list, tuple)) else args)
        if argv and argv[-1] == '--version':
            return SimpleNamespace(returncode=0, stdout='alphaMELTS fake\n', stderr='')
        calls.append({'args': argv, 'kwargs': kwargs})
        cwd = Path(kwargs['cwd'])
        oxides = 'SiO2 FeO MgO'
        (cwd / 'System_main_tbl.txt').write_text(
            _system_main_fixture(temperature_C=temperature_C)
        )
        (cwd / 'Phase_main_tbl.txt').write_text(
            f'index 1 Pressure 1.00 Temperature {temperature_C:.2f} {oxides}\n'
            f'liquid1 100.0 -1059377.1 268.91 34.56 143.47 1.409 30 42 28\n'
        )
        (cwd / 'Solid_comp_tbl.txt').write_text(
            f'index Pressure Temperature mass {oxides}\n'
            f'1 1.00 {temperature_C:.2f} 0.0 ---\n'
        )
        (cwd / 'Bulk_comp_tbl.txt').write_text(
            f'index Pressure Temperature mass {oxides}\n'
            f'1 1.00 {temperature_C:.2f} 100.0 30 42 28\n'
        )
        (cwd / 'Liquid_comp_tbl.txt').write_text(
            f'index Pressure Temperature mass {oxides}\n'
            f'1 1.00 {temperature_C:.2f} 100.0 30 42 28\n'
        )
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '<> Stable liquid assemblage achieved.\n'
                f'Initial alphaMELTS calculation at: P 1.000000 (bars), '
                f'T {temperature_C:.6f} (C)\n'
                'liquid: SiO2 FeO MgO\n'
                '100.0 g 30 42 28\n'
                'Melt fraction = 1.0\n'
            ),
            stderr='',
        )

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts._run_alphamelts_subprocess',
        fake_run,
    )
    monkeypatch.setattr(
        backend,
        '_builtin_vapor_projection_for_subprocess',
        lambda _eq: ({}, {}, {'vapor_pressure_zero_reason': 'test_stub'}),
    )
    return calls


def _install_thermoengine_transport_spy(monkeypatch, backend):
    """Spy ThermoEngine's transport.equilibrate boundary with an all-liquid payload."""
    calls: list = []
    backend._mode = 'thermoengine'
    backend._engine_version = 'test'
    backend._vaporock_available = False

    class FakeTransport:
        def equilibrate(self, **kwargs):
            calls.append(kwargs)
            return ThermoEnginePayload(
                phases_present=('liquid',),
                phase_masses_kg={'liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(kwargs['comp_wt']),
                activity_coefficients={'SiO2': 0.4},
                solved_fO2_log=kwargs['fO2_log'],
            )

        def close(self):
            return None

    backend._thermoengine_transport = FakeTransport()
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *args, **kwargs: {},
    )
    return calls


@pytest.mark.parametrize(
    'composition',
    [
        {'SiO2': 30.0, 'FeO': 42.0, 'MgO': 28.0},
        {'SiO2': 33.0, 'FeO': 40.2, 'MgO': 26.8},
    ],
    ids=['sio2_30', 'sio2_33'],
)
@pytest.mark.parametrize('engine', ['alphamelts', 'thermoengine'])
def test_in_band_sliver_below_observed_floor_still_calls_transport(
    monkeypatch, composition, engine,
) -> None:
    """C01 / F1: 30–34 wt% SiO2 is inside the certified band; the engine runs.

    Codex review probe: 30/42/28 and 33/40.2/26.8 wt%, 1400 C, both engines.
    Base: 1 transport call / ok / liquid_fraction 1.0. Tip must match.
    """
    if engine == 'alphamelts':
        backend = AlphaMELTSBackend()
        calls = _install_alphamelts_transport_spy(monkeypatch, backend)
    else:
        backend = ThermoEngineBackend()
        calls = _install_thermoengine_transport_spy(monkeypatch, backend)

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=composition,
        fO2_log=-9.0,
        pressure_bar=1.0,
        **({'subprocess_run_mode': 'isothermal'} if engine == 'alphamelts' else {}),
    )

    assert len(calls) == 1, (
        f'{engine} must launch once for in-band SiO2={composition["SiO2"]} wt%; '
        f'got {len(calls)} calls, status={result.status!r}, '
        f'reason={result.diagnostics.get("backend_failure_reason_code")!r}'
    )
    assert result.status == 'ok'
    assert result.liquid_fraction == pytest.approx(1.0)
    assert result.diagnostics.get('backend_failure_reason_code') != (
        'sio2_below_crash_floor'
    )
    assert 'commissioning_notice' not in result.diagnostics


def test_sio2_free_pot_is_not_a_crash_floor_refusal(monkeypatch) -> None:
    """C01: SiO2-free pots follow the ordinary SiO2=0 path, not a new floor."""
    composition = {'CaO': 50.0, 'Al2O3': 50.0}
    backend = AlphaMELTSBackend()
    calls = _install_alphamelts_transport_spy(monkeypatch, backend)

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=composition,
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert result.diagnostics.get('backend_failure_reason_code') != (
        'sio2_below_crash_floor'
    )
    assert result.diagnostics.get('backend_failure_category') != 'engine_crash'
    assert len(calls) == 1
    assert result.diagnostics.get('authority') == 'extrapolated'


def test_alphamelts_subprocess_death_below_observed_floor_annotates_engine_reason(
    monkeypatch,
) -> None:
    """C01: only an actual subprocess death below the observed floor is annotated."""
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    calls: list = []

    def fake_run(args, **kwargs):
        argv = list(args[0] if args and isinstance(args[0], (list, tuple)) else args)
        if argv and argv[-1] == '--version':
            return SimpleNamespace(returncode=0, stdout='alphaMELTS fake\n', stderr='')
        calls.append(argv)
        return SimpleNamespace(returncode=-6, stdout='', stderr='SIGABRT')

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts._run_alphamelts_subprocess',
        fake_run,
    )

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg={'SiO2': 30.0, 'FeO': 42.0, 'MgO': 28.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert calls, 'subprocess must actually launch before a crash annotation'
    assert result.diagnostics.get('backend_failure_reason_code') == (
        ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert result.diagnostics.get('backend_failure_category') == 'engine_crash'
    assert result.diagnostics.get('engine_reason') == (
        'sio2_below_observed_crash_floor'
    )
