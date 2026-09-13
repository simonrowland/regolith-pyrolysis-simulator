"""Project-owned engine commissioning table (t-894)."""

from __future__ import annotations

from pathlib import Path

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
from simulator.melt_backend.alphamelts import (
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


def _below_crash_floor() -> dict[str, float]:
    return {'SiO2': 20.0, 'FeO': 40.0, 'MgO': 40.0}


def _valid_table_payload() -> dict:
    engine = {
        'sio2_wt_pct': {'certified': [30.0, 80.0], 'crash_floor': 34.0},
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
        assert spec.sio2_wt_pct.crash_floor == PUBLISHED_SIO2_CRASH_FLOOR_WT_PCT
        assert spec.temperature_K.certified.as_tuple() == (
            PUBLISHED_TEMPERATURE_CERTIFIED_K
        )
        assert spec.authority_outside == PUBLISHED_AUTHORITY_OUTSIDE
        assert spec.source.kind == 'adapter_default'

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
    assert alphamelts.sio2_wt_pct.crash_floor == _SIO2_CRASH_FLOOR_WT_PCT
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


def test_certified_outside_crash_floor_is_rejected(tmp_path: Path) -> None:
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['sio2_wt_pct']['certified'] = [20.0, 25.0]
    payload['engines']['alphamelts']['sio2_wt_pct']['crash_floor'] = 34.0
    path = _write_table(tmp_path / 'outside-floor.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='outside crash floor'):
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


def test_crash_floor_refuses_without_calling_engine(monkeypatch) -> None:
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._engine_version = 'test'
    called: list = []
    monkeypatch.setattr(backend, '_equilibrate_prepared', _spy_prepared(called))

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=_below_crash_floor(),
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )

    assert not called, 'crash floor must not call the engine'
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'
    assert result.diagnostics['backend_failure_reason_code'] == (
        'sio2_below_crash_floor'
    )
    assert result.status == 'out_of_domain'


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


def test_thermoengine_crash_floor_refuses_without_calling_engine(
    monkeypatch,
) -> None:
    backend = ThermoEngineBackend()
    backend._mode = 'thermoengine'
    backend._engine_version = 'test'
    called: list = []
    monkeypatch.setattr(backend, '_equilibrate_prepared', _spy_prepared(called))

    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg=_below_crash_floor(),
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert not called, 'thermoengine crash floor must not call the engine'
    assert result.diagnostics['backend_failure_category'] == 'engine_crash'


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
