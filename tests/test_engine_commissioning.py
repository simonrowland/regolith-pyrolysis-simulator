"""Project-owned engine commissioning table (t-894)."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from engines.alphamelts import AlphaMELTSProvider
from engines.alphamelts.domain import (
    DEFAULT_SILICATE_NETWORK_BAND_WT_PCT,
    DEFAULT_SIO2_MAX_WT_PCT,
    DEFAULT_SIO2_MIN_WT_PCT,
    _SIO2_CRASH_FLOOR_WT_PCT,
)
import engines.engine_commissioning as engine_commissioning_module
from engines.engine_commissioning import (
    PUBLISHED_AUTHORITY_OUTSIDE,
    PUBLISHED_SIO2_CERTIFIED_WT_PCT,
    PUBLISHED_SIO2_CRASH_FLOOR_WT_PCT,
    PUBLISHED_TEMPERATURE_CERTIFIED_K,
    EngineCommissioningError,
    assess_engine_commissioning,
    engine_commissioning,
    load_engine_commissioning,
    parse_engine_commissioning_file,
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
from simulator.chemistry.kernel import ChemistryIntent, IntentRequest
from simulator.chemistry.kernel.dto import ProviderAccountView
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


def test_binary_pot_battery_describes_notice_and_run_tmin() -> None:
    """D04 / grok P2-1: helper text matches notice+run, not the old T-min refusal."""
    text = Path(
        'simulator/diagnostic_helpers/binary_pot_battery.py'
    ).read_text(encoding='utf-8')
    assert '800 C inline in _equilibrate_subprocess' not in text
    assert 'notice+run' in text


def test_commissioning_yaml_comments_match_notice_and_run() -> None:
    """C14 / G-P3-2: comments describe notice+run and floor-as-metadata."""
    text = Path('data/engine_commissioning.yaml').read_text(encoding='utf-8')
    assert 'notice + authority=extrapolated' in text
    assert 'evidence metadata, not a pre-run refusal' in text
    assert 'used the same window as a pre-equilibrate' not in text


def test_melt_envelope_t_max_reads_commissioning_snapshot() -> None:
    """C12 / G-P1-4: envelope T max has one source — the commissioning table."""
    envelope_path = Path('simulator/melt_backend/melt_envelope.py')
    source = envelope_path.read_text(encoding='utf-8')
    assert '"T_calib_max_K": 1700.0' not in source
    assert 'engine_commissioning' in source
    assert MELT_ENVELOPE_CONSTANTS['MELTS-v1.0']['T_calib_max_K'] == (
        engine_commissioning('alphamelts').temperature_K.certified.maximum
    )


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
    assert ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C == 800.0
    assert (
        alphamelts.temperature_K.certified.minimum
        == ALPHAMELTS_SUBPROCESS_MIN_TEMPERATURE_C + CELSIUS_TO_KELVIN_OFFSET
    )
    assert MELT_ENVELOPE_CONSTANTS['MELTS-v1.0']['T_calib_max_K'] == (
        engine_commissioning('alphamelts').temperature_K.certified.maximum
    )
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


def test_runtime_snapshot_cannot_be_mutated() -> None:
    """C06 / F6: one frozen process snapshot; mutation cannot change assessment."""
    table = load_engine_commissioning()
    original = table.engines['alphamelts']
    with pytest.raises(TypeError):
        table.engines['alphamelts'] = table.engines['thermoengine']
    first = assess_engine_commissioning(
        'alphamelts', sio2_wt_pct=50.0, temperature_K=1500.0
    )
    second = assess_engine_commissioning(
        'alphamelts', sio2_wt_pct=50.0, temperature_K=1500.0
    )
    assert table.engines['alphamelts'] is original
    assert first.spec is second.spec is original
    assert not hasattr(engine_commissioning_module, 'clear_engine_commissioning_cache')


def test_bad_key_is_rejected(tmp_path: Path) -> None:
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['not_a_field'] = 1
    path = _write_table(tmp_path / 'bad-key.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='unknown key'):
        parse_engine_commissioning_file(path)


def test_authority_outside_certified_is_rejected(tmp_path: Path) -> None:
    """C03 / F3: only extrapolated may leave the certified band."""
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['authority_outside'] = 'certified'
    path = _write_table(tmp_path / 'authority-certified.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='authority_outside'):
        parse_engine_commissioning_file(path)


def test_authority_outside_bridge_is_rejected(tmp_path: Path) -> None:
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['authority_outside'] = 'bridge'
    path = _write_table(tmp_path / 'authority-bridge.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='authority_outside'):
        parse_engine_commissioning_file(path)


def test_boolean_bound_is_rejected(tmp_path: Path) -> None:
    """C05 / F5: [True, 80] is not a numeric certified interval."""
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['sio2_wt_pct']['certified'] = [True, 80]
    path = _write_table(tmp_path / 'bool-bound.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='finite float'):
        parse_engine_commissioning_file(path)


def test_non_integer_schema_version_is_rejected(tmp_path: Path) -> None:
    """C05 / F5: schema_version 1.9 is not an int."""
    payload = _valid_table_payload()
    payload['schema_version'] = 1.9
    path = _write_table(tmp_path / 'schema-1.9.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='schema_version'):
        parse_engine_commissioning_file(path)


def test_inverted_band_is_rejected(tmp_path: Path) -> None:
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['sio2_wt_pct']['certified'] = [80.0, 30.0]
    path = _write_table(tmp_path / 'inverted.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='inverted') as excinfo:
        parse_engine_commissioning_file(path)
    assert 'crash floor' not in str(excinfo.value)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), 'not-a-number'])
def test_nonfinite_bound_is_rejected(tmp_path: Path, value) -> None:
    """C11 / G-P1-3: NaN, inf, and junk are not finite floats."""
    payload = _valid_table_payload()
    payload['engines']['alphamelts']['sio2_wt_pct']['certified'] = [value, 80.0]
    path = _write_table(tmp_path / 'nonfinite.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='finite float'):
        parse_engine_commissioning_file(path)


def test_unknown_engine_is_rejected(tmp_path: Path) -> None:
    payload = _valid_table_payload()
    payload['engines']['not_an_engine'] = dict(payload['engines']['alphamelts'])
    path = _write_table(tmp_path / 'unknown-engine.yaml', payload)
    with pytest.raises(EngineCommissioningError, match='unknown engine'):
        parse_engine_commissioning_file(path)


def test_missing_table_is_typed_error(tmp_path: Path) -> None:
    missing = tmp_path / 'no-such-commissioning.yaml'
    with pytest.raises(EngineCommissioningError, match='not found'):
        parse_engine_commissioning_file(missing)


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
    """C01: SiO2-free pots follow the ordinary SiO2=0 basis gate, not a floor."""
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

    assert not calls, 'SiO2-free pots must not launch'
    assert result.status == 'out_of_domain'
    assert result.diagnostics.get('backend_status_reason') == 'silicate_window'
    assert result.diagnostics.get('backend_failure_reason_code') != (
        'sio2_below_crash_floor'
    )
    assert result.diagnostics.get('backend_failure_category') != 'engine_crash'


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


def test_provider_band_only_high_silica_reaches_backend_and_notices(
    monkeypatch,
) -> None:
    """C02 / F2: SiO2=85 wt% is uncertified, not a provider veto."""
    backend = AlphaMELTSBackend()
    backend.stage0_subprocess_required = True
    transport_calls = _install_alphamelts_transport_spy(monkeypatch, backend)
    original_equilibrate = backend.equilibrate
    backend_calls: list = []
    backend_results: list = []

    def wrapped_equilibrate(*args, **kwargs):
        result = original_equilibrate(*args, **kwargs)
        backend_calls.append(kwargs)
        backend_results.append(result)
        return result

    monkeypatch.setattr(backend, 'equilibrate', wrapped_equilibrate)
    provider = AlphaMELTSProvider(backend=backend)
    masses = {'SiO2': 0.06008, 'FeO': 0.07184, 'MgO': 0.04030}
    wt = {'SiO2': 85.0, 'FeO': 9.0, 'MgO': 6.0}
    composition_mol = {
        oxide: (wt_pct / 100.0) / masses[oxide]
        for oxide, wt_pct in wt.items()
    }
    result = provider.dispatch(
        IntentRequest(
            intent=ChemistryIntent.SILICATE_EQUILIBRIUM,
            account_view=ProviderAccountView(
                accounts={'process.cleaned_melt': composition_mol},
                species_formula_registry={},
            ),
            temperature_C=1400.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            control_inputs={},
        )
    )

    assert backend_calls, (
        'band-only SiO2=85 wt% must reach the backend, not veto in the provider'
    )
    assert transport_calls, 'backend must still launch the engine'
    eq = backend_results[0]
    assert eq.diagnostics.get('authority') == 'extrapolated'
    assert eq.diagnostics['commissioning_notice']['kind'] == 'engine_commissioning'
    assert result.status != 'out_of_domain' or (
        (result.diagnostic or {}).get('backend_status_reason')
        != 'silicate_window'
    )


def test_liquidus_search_aggregates_commissioning_notice(monkeypatch) -> None:
    """C04 / F4: liquidus diagnostics keep structured extrapolation evidence."""
    backend = ThermoEngineBackend()
    calls = _install_thermoengine_transport_spy(monkeypatch, backend)

    def fake_equilibrate(**kwargs):
        calls.append(kwargs)
        temperature_C = float(kwargs['temperature_C'])
        frac = max(0.0, min(1.0, (temperature_C - 1200.0) / 400.0))
        masses = {}
        if frac > 0.0:
            masses['liquid'] = frac
        if frac < 1.0:
            masses['solid'] = 1.0 - frac
        return ThermoEnginePayload(
            phases_present=tuple(masses),
            phase_masses_kg=masses,
            liquid_fraction=frac,
            liquid_composition_wt_pct=dict(kwargs['comp_wt']),
            activity_coefficients={'SiO2': 0.4},
            solved_fO2_log=kwargs['fO2_log'],
        )

    backend._thermoengine_transport.equilibrate = fake_equilibrate
    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 85.0, 'FeO': 9.0, 'MgO': 6.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=1000.0,
        max_T_C=1800.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )

    assert calls, 'liquidus search must evaluate engine samples'
    assert result.status == 'ok'
    assert result.diagnostics.get('authority') == 'extrapolated'
    notice = result.diagnostics['commissioning_notice']
    assert notice['kind'] == 'engine_commissioning'
    assert 'certified_band' in result.diagnostics
    evaluated = notice['evaluated_temperature_C']
    assert evaluated[0] <= 1000.0
    assert evaluated[-1] >= 1600.0


def test_commissioning_notice_is_call_local(monkeypatch) -> None:
    """C07 / F7: a later early refusal must not inherit the prior notice."""
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._engine_version = 'test'
    called: list = []
    monkeypatch.setattr(backend, '_equilibrate_prepared', _spy_prepared(called))

    first = backend.equilibrate(
        temperature_C=2200.0,
        composition_kg=_in_band_pot(),
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )
    assert first.diagnostics.get('commissioning_notice')

    second = backend.equilibrate(
        temperature_C=1400.0,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0, 'MgO': 1.0, 'Al2O3': 1.0},
            'oxygen_mre_anode_stored': {'O2': 0.1},
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
        subprocess_run_mode='isothermal',
    )
    assert second.status == 'out_of_domain'
    assert 'commissioning_notice' not in (second.diagnostics or {})
    assert (second.diagnostics or {}).get('authority') is None


def test_thermoengine_in_band_ok_omits_crash_template_keys(monkeypatch) -> None:
    """C08 / F8: successful in-band results must not inherit crash-template fields."""
    backend = ThermoEngineBackend()
    _install_thermoengine_transport_spy(monkeypatch, backend)
    result = backend.equilibrate(
        temperature_C=1400.0,
        composition_kg={'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    assert result.status == 'ok'
    diagnostics = result.diagnostics or {}
    assert 'commissioning_notice' not in diagnostics
    for key in (
        'backend_status',
        'backend_failure_reason_code',
        'backend_failure_category',
        'backend_failure_message',
        'out_of_domain_crash_point',
    ):
        assert key not in diagnostics, key


def test_discarded_commissioning_call_and_crash_floor_alias_are_gone() -> None:
    """C10: no unused engine_commissioning() call or pre-run floor alias."""
    import simulator.melt_backend.alphamelts as alphamelts_module

    source = inspect.getsource(ThermoEngineBackend._equilibrate_prepared)
    assert 'engine_commissioning(' not in source
    assert not hasattr(AlphaMELTSBackend, '_crash_floor_result')
    assert not hasattr(alphamelts_module, 'ALPHAMELTS_REASON_SIO2_CRASH_FLOOR')


def _reviewer_ptt_payload(temperature_C: float) -> dict:
    """Codex R1 fake: 100 g, fO2=-9, liquid/olivine from clip((T-1200)/400,0,1)."""
    frac = max(0.0, min(1.0, (float(temperature_C) - 1200.0) / 400.0))
    liquid_mass = 100.0 * frac
    olivine_mass = 100.0 - liquid_mass
    payload: dict = {
        'Conditions': {
            'mass': 100.0,
            'fO2_log': -9.0,
            'P_bar': 1.0,
        },
    }
    if liquid_mass > 0.0:
        payload['liquid1'] = {'SiO2': 50.0}
        payload['liquid1_prop'] = {'mass': liquid_mass}
    if olivine_mass > 0.0:
        payload['olivine1'] = {'SiO2': 40.0}
        payload['olivine1_prop'] = {'mass': olivine_mass}
    return payload


def _install_python_api_transport_spy(monkeypatch, backend):
    """Fake `_run_petthermotools_isolated`; keep real entry points and parser."""
    calls: list = []
    backend._mode = 'python_api'
    backend._engine_version = 'test'
    backend._vaporock_available = False
    backend._pet_payload_preloaded = True
    backend._pet_melts = object()
    backend._pet_module = SimpleNamespace(
        findLiq_MELTS=lambda **_kwargs: 1600.0,
        isothermal_decompression=object(),
    )
    backend._redox_buffer = 'QFM'
    backend._fo2_offset = 0.0

    def fake_isolated(operation, *, args=(), kwargs=None):
        kwargs = dict(kwargs or {})
        calls.append({'operation': operation, 'kwargs': kwargs})
        if operation in ('findLiq_MELTS', 'findLiq'):
            return 1600.0
        temperature_C = float(kwargs.get('T_C', 1400.0))
        payload = _reviewer_ptt_payload(temperature_C)
        if operation == 'isothermal_decompression':
            return {0: payload}
        return payload

    monkeypatch.setattr(backend, '_run_petthermotools_isolated', fake_isolated)
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *args, **kwargs: {},
    )
    return calls


def _python_api_path_result(
    backend,
    path: str,
    composition_kg: dict,
    temperature_C: float,
    *,
    min_T_C: float = 1000.0,
    max_T_C: float = 1800.0,
):
    composition = {
        'composition_kg': composition_kg,
        'fO2_log': -9.0,
    }
    if path == 'equilibrate':
        return backend.equilibrate(
            temperature_C, pressure_bar=1.0, **composition
        )
    if path == 'find_liquidus_solidus':
        return backend.find_liquidus_solidus(
            min_T_C=min_T_C,
            max_T_C=max_T_C,
            scan_step_C=100.0,
            tolerance_C=2.0,
            pressure_bar=1.0,
            **composition,
        )
    if path == 'decompression_path':
        results = backend.decompression_path(
            temperature_C, 1.0, 1.0, 1.0, **composition
        )
        assert results, 'decompression_path must return at least one result'
        return results[0]
    raise AssertionError(f'unknown path {path!r}')


def _assert_structured_commissioning(result) -> None:
    diagnostics = result.diagnostics or {}
    assert diagnostics.get('authority') == 'extrapolated'
    assert diagnostics['certified_band']['sio2_wt_pct'] == [30.0, 80.0]
    assert diagnostics['certified_band']['temperature_K'] == [1073.15, 1700.0]
    notice = diagnostics['commissioning_notice']
    assert notice['kind'] == 'engine_commissioning'
    assert notice['authority'] == 'extrapolated'


def _assert_no_structured_commissioning(result) -> None:
    diagnostics = result.diagnostics or {}
    assert 'commissioning_notice' not in diagnostics
    assert diagnostics.get('authority') is None
    assert 'certified_band' not in diagnostics


@pytest.mark.parametrize(
    'path',
    ['equilibrate', 'find_liquidus_solidus', 'decompression_path'],
)
def test_python_api_high_silica_returns_structured_commissioning(
    monkeypatch, path,
) -> None:
    """D01 / Codex R1: 85/9/6 wt% python_api success still carries the notice."""
    backend = AlphaMELTSBackend()
    calls = _install_python_api_transport_spy(monkeypatch, backend)
    result = _python_api_path_result(
        backend, path, {'SiO2': 85.0, 'FeO': 9.0, 'MgO': 6.0}, 1400.0,
    )
    assert calls, f'{path} must call the PetThermoTools transport'
    assert result.status == 'ok'
    _assert_structured_commissioning(result)


@pytest.mark.parametrize(
    'path',
    ['equilibrate', 'decompression_path'],
)
def test_python_api_in_band_omits_structured_commissioning(
    monkeypatch, path,
) -> None:
    """D01 in-band control: 50/30/20 wt% at 1400 C carries none of the three fields."""
    backend = AlphaMELTSBackend()
    calls = _install_python_api_transport_spy(monkeypatch, backend)
    result = _python_api_path_result(
        backend, path, {'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0}, 1400.0,
    )
    assert calls, f'{path} must call the PetThermoTools transport'
    assert result.status == 'ok'
    _assert_no_structured_commissioning(result)


def test_python_api_in_band_liquidus_omits_structured_commissioning(
    monkeypatch,
) -> None:
    """In-band composition and in-band evaluated T: liquidus carries no notice."""
    backend = AlphaMELTSBackend()
    calls = _install_python_api_transport_spy(monkeypatch, backend)
    backend._pet_module.findLiq_MELTS = lambda **_kwargs: 1300.0

    def fake_isolated(operation, *, args=(), kwargs=None):
        kwargs = dict(kwargs or {})
        calls.append({'operation': operation, 'kwargs': kwargs})
        if operation in ('findLiq_MELTS', 'findLiq'):
            return 1300.0
        temperature_C = float(kwargs.get('T_C', 1300.0))
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        payload = {
            'Conditions': {
                'mass': 100.0,
                'fO2_log': -9.0,
                'P_bar': 1.0,
            },
            'liquid1': {'SiO2': 50.0},
            'liquid1_prop': {'mass': 100.0 * frac if frac > 0.0 else 0.0},
        }
        if frac < 1.0:
            payload['olivine1'] = {'SiO2': 40.0}
            payload['olivine1_prop'] = {'mass': 100.0 * (1.0 - frac)}
        if payload['liquid1_prop']['mass'] <= 0.0:
            payload.pop('liquid1')
            payload.pop('liquid1_prop')
        return payload

    monkeypatch.setattr(backend, '_run_petthermotools_isolated', fake_isolated)
    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=1000.0,
        max_T_C=1400.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )
    assert calls, 'in-band liquidus must still evaluate samples'
    assert result.status == 'ok'
    _assert_no_structured_commissioning(result)


def test_python_api_native_finder_hot_result_is_assessed(monkeypatch) -> None:
    """E01 / Codex R1: exposed findLiq=1600 C is assessed even if the scan stays in band."""
    backend = AlphaMELTSBackend()
    calls = _install_python_api_transport_spy(monkeypatch, backend)
    backend._pet_module.findLiq_MELTS = lambda **_kwargs: 1600.0

    def fake_isolated(operation, *, args=(), kwargs=None):
        kwargs = dict(kwargs or {})
        calls.append({'operation': operation, 'kwargs': kwargs})
        if operation in ('findLiq_MELTS', 'findLiq'):
            return 1600.0
        temperature_C = float(kwargs.get('T_C', 1300.0))
        frac = max(0.0, min(1.0, (temperature_C - 1000.0) / 300.0))
        payload = {
            'Conditions': {
                'mass': 100.0,
                'fO2_log': -9.0,
                'P_bar': 1.0,
            },
            'liquid1': {'SiO2': 50.0},
            'liquid1_prop': {'mass': 100.0 * frac if frac > 0.0 else 0.0},
        }
        if frac < 1.0:
            payload['olivine1'] = {'SiO2': 40.0}
            payload['olivine1_prop'] = {'mass': 100.0 * (1.0 - frac)}
        if payload['liquid1_prop']['mass'] <= 0.0:
            payload.pop('liquid1')
            payload.pop('liquid1_prop')
        return payload

    monkeypatch.setattr(backend, '_run_petthermotools_isolated', fake_isolated)
    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=1000.0,
        max_T_C=1400.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )
    assert calls, 'python_api liquidus must still call the PetThermoTools transport'
    assert result.status == 'ok'
    assert result.liquidus_T_C == pytest.approx(1300.0)
    _assert_structured_commissioning(result)
    extent = (result.diagnostics or {}).get('commissioning_notice', {}).get(
        'evaluated_temperature_C'
    )
    assert extent, 'native finder T must enter the assessed temperature extent'
    assert min(extent) <= 1600.0 <= max(extent)


def test_python_api_hot_in_band_composition_notices(monkeypatch) -> None:
    """D01 / grok P1-1: python_api equilibrate at 2200 C, in-band composition."""
    backend = AlphaMELTSBackend()
    calls = _install_python_api_transport_spy(monkeypatch, backend)
    result = backend.equilibrate(
        2200.0,
        composition_kg={'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    assert calls, 'python_api equilibrate must still call the transport'
    assert result.status == 'ok'
    _assert_structured_commissioning(result)


def test_native_liquidus_notices_at_returned_temperature(monkeypatch) -> None:
    """D02 / Codex R2: native finder classifies the returned liquidus, not the seed."""
    backend = AlphaMELTSBackend()
    calls = _install_alphamelts_transport_spy(
        monkeypatch, backend, temperature_C=1600.0,
    )
    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=1000.0,
        max_T_C=1800.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )
    assert len(calls) == 1, (
        f'native finder must be one transport call; got {len(calls)}'
    )
    assert result.status == 'ok'
    assert result.liquidus_T_C == pytest.approx(1600.0)
    assert result.liquidus_T_K == pytest.approx(1873.15)
    _assert_structured_commissioning(result)


def test_native_liquidus_in_band_omits_notice(monkeypatch) -> None:
    """Returned in-band liquidus carries none of the three commissioning fields."""
    backend = AlphaMELTSBackend()
    calls = _install_alphamelts_transport_spy(
        monkeypatch, backend, temperature_C=1400.0,
    )
    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=1000.0,
        max_T_C=1800.0,
        scan_step_C=100.0,
        tolerance_C=2.0,
    )
    assert len(calls) == 1
    assert result.status == 'ok'
    assert result.liquidus_T_C == pytest.approx(1400.0)
    _assert_no_structured_commissioning(result)


@pytest.mark.parametrize(
    'composition_kg,temperature_C',
    [
        ({'SiO2': 50.0, 'FeO': 30.0, 'MgO': 20.0}, 2200.0),
        ({'SiO2': 85.0, 'FeO': 9.0, 'MgO': 6.0}, 1400.0),
    ],
    ids=['hot_in_band', 'high_silica'],
)
def test_python_api_decompression_notices_out_of_band(
    monkeypatch, composition_kg, temperature_C,
) -> None:
    """D01 / grok P1-1: FakePTT decompression at 2200 C and at SiO2=85 wt%."""
    backend = AlphaMELTSBackend()
    calls = _install_python_api_transport_spy(monkeypatch, backend)
    results = backend.decompression_path(
        temperature_C,
        1.0,
        1.0,
        1.0,
        composition_kg=composition_kg,
        fO2_log=-9.0,
    )
    assert calls, 'decompression_path must call isothermal_decompression'
    assert results
    assert results[0].status == 'ok'
    _assert_structured_commissioning(results[0])


def _load_studio_diff_module():
    path = Path('scripts/diff_commissioning_studio_cells.py')
    spec = importlib.util.spec_from_file_location(
        'diff_commissioning_studio_cells', path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _studio_cell(**overrides):
    cell = {
        'pot_id': 'feo_mgo_sio2_30_20_50',
        'engine': 'alphamelts',
        'temperature_K': 1700.0,
        'po2': {'mode': 'engine_default', 'po2_bar': None},
        'arm': 'headline',
        'status': 'ok',
        'refusal_reason': None,
        'engine_reason': None,
        'engine_status': 'ok',
        'melt_activities': {'SiO2': 0.4},
        'gas_partial_pressures_Pa': {'Fe': 1.0},
        'liquid_fraction': 1.0,
        'notices': [],
        'authority': None,
    }
    cell.update(overrides)
    return cell


def test_studio_diff_classifies_allowed_and_other_verdicts() -> None:
    """D03: per-cell table includes identical cells and names OTHER."""
    diff = _load_studio_diff_module()
    identical = _studio_cell()
    reclassified_base = _studio_cell(
        status='refusal',
        refusal_reason='gate_refused_in_adapter',
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
        engine_status='out_of_domain',
    )
    reclassified_tip = _studio_cell(
        status='ok',
        authority='extrapolated',
        notices=[{'kind': 'engine_commissioning', 'authority': 'extrapolated'}],
    )
    crash_base = _studio_cell(
        status='refusal',
        refusal_reason='engine_crash',
        engine_status='engine_crash',
        engine_reason='subprocess_died',
        melt_activities={},
        gas_partial_pressures_Pa={},
        liquid_fraction=None,
    )
    crash_tip = dict(crash_base)
    crash_tip['engine_annotation'] = 'sio2_below_observed_crash_floor'
    other_tip = _studio_cell(liquid_fraction=0.5)

    assert diff.classify_cell(identical, identical) == 'identical'
    assert diff.classify_cell(reclassified_base, reclassified_tip) == (
        'reclassified_refusal_to_notice'
    )
    assert diff.classify_cell(crash_base, crash_tip) == 'crash_annotation'
    assert diff.classify_cell(identical, other_tip) == 'OTHER'
    assert diff.classify_cell(identical, None) == 'OTHER'

    report = diff.diff_captures(
        {
            'hostname': 'Mac-Studio-256-1.local',
            'revision_id': 'base',
            'run_timestamp': 't0',
            'cells': [
                identical,
                {**reclassified_base, 'pot_id': 'high_silica'},
                {**crash_base, 'pot_id': 'crash'},
            ],
        },
        {
            'hostname': 'Mac-Studio-256-1.local',
            'revision_id': 'tip',
            'run_timestamp': 't1',
            'cells': [
                identical,
                {**reclassified_tip, 'pot_id': 'high_silica'},
                {**crash_tip, 'pot_id': 'crash'},
            ],
        },
    )
    assert report['n_cells'] == 3
    assert report['n_identical'] == 1
    assert report['n_reclassified_refusal_to_notice'] == 1
    assert report['n_crash_annotation'] == 1
    assert report['n_OTHER'] == 0
    assert {row['verdict'] for row in report['cells']} == {
        'identical',
        'reclassified_refusal_to_notice',
        'crash_annotation',
    }


def test_studio_diff_timeout_is_not_subprocess_death() -> None:
    """E02 / Codex R2: engine_timeout is not crash evidence."""
    diff = _load_studio_diff_module()
    base = _studio_cell(
        status='refusal',
        refusal_reason='engine_timeout',
        engine_reason='timeout',
        liquid_fraction=None,
    )
    tip = dict(base, engine_reason='sio2_below_observed_crash_floor')
    assert diff.classify_cell(base, tip) == 'OTHER'
