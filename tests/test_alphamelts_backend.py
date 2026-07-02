import math
import subprocess
import time
import types
import warnings
from pathlib import Path

import pytest
import yaml

from engines.builtin.vapor_pressure import (
    HighUncertaintyVaporPressureFallbackWarning,
)
from engines.domain_reason import OutOfDomainReason
from engines.alphamelts import AlphaMELTSProvider
from engines.alphamelts.domain import AlphaMELTSDomainGate
import engines.alphamelts.provider as alphamelts_provider_module
from engines.alphamelts.parser import diagnostics_to_equilibrium
from engines.alphamelts.result import LiquidusDiagnostics
from simulator.core import CampaignPhase, PyrolysisSimulator
from simulator.melt_backend.alphamelts import (
    ALPHAMELTS_REASON_MISSING_BINARY,
    ALPHAMELTS_REASON_NONZERO_EXIT,
    ALPHAMELTS_REASON_NO_CONVERGENCE,
    ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT,
    ALPHAMELTS_REASON_SUBPROCESS_DIED,
    ALPHAMELTS_REASON_TIMEOUT,
    AlphaMELTSBackend,
    activity_from_chem_potential,
)
from simulator.melt_backend.base import (
    EquilibriumResult,
    LiquidFractionInvalidError,
)
from engines.alphamelts.thermoengine import ThermoEngineTransport
from engines.magemin.parity import MAGEMinParityComparator


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _load_data(filename):
    with open(DATA_DIR / filename) as f:
        return yaml.safe_load(f) or {}


def test_alphamelts_python_failures_mark_backend_unavailable():
    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'

    with pytest.raises(ImportError, match='not preloaded'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={
                'SiO2': 50.0,
                'Al2O3': 15.0,
                'FeO': 10.0,
                'MgO': 10.0,
                'CaO': 10.0,
                'Na2O': 5.0,
            },
            fO2_log=-9.0,
            pressure_bar=1e-6,
        )

    assert backend.is_available() is False


def test_alphamelts_python_liquidus_finder_uses_findliq_gate():
    class FakeFinderBackend(AlphaMELTSBackend):
        def __init__(self):
            super().__init__()
            self._mode = 'python_api'

        def _find_petthermotools_liquidus_C(self, comp_wt, *, pressure_bar, seed_T_C):
            return 1300.0, ()

        def equilibrate(self, temperature_C, **kwargs):
            frac = max(0.0, min(1.0, (float(temperature_C) - 1000.0) / 300.0))
            return EquilibriumResult(
                temperature_C=float(temperature_C),
                pressure_bar=float(kwargs.get('pressure_bar', 1e-6)),
                liquid_fraction=frac,
                phases_present=['liq'] if frac > 0.0 else ['ol'],
                phase_masses_kg={'liq': frac, 'ol': 1.0 - frac},
                status='ok',
            )

    backend = FakeFinderBackend()
    result = backend.find_liquidus_solidus(
        composition_kg={
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
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'ok'
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)


@pytest.mark.parametrize(
    ('phase_masses_kg', 'liquid_fraction', 'message'),
    [
        ({'liquid': math.inf, 'olivine': 1.0}, None, 'phase_mass_invalid'),
        ({'liquid': 0.8, 'olivine': 0.2}, math.nan, 'liquid_fraction_invalid'),
        ({}, None, 'liquid_fraction_missing'),
        ({'liquid': 0.8, 'olivine': 0.2}, 0.1, 'liquid_fraction_mismatch'),
    ],
)
def test_alphamelts_ok_result_requires_finite_phase_mass_fraction(
    phase_masses_kg,
    liquid_fraction,
    message,
):
    backend = AlphaMELTSBackend()

    with pytest.raises(LiquidFractionInvalidError, match=message):
        backend._emit_equilibrium_result(
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            phases_present=list(phase_masses_kg),
            phase_masses_kg=phase_masses_kg,
            liquid_fraction=liquid_fraction,
            status='ok',
        )


def test_alphamelts_subprocess_liquidus_finder_uses_fraction_samples():
    class FakeFinderBackend(AlphaMELTSBackend):
        def __init__(self):
            super().__init__()
            self._mode = 'subprocess'

        def equilibrate(self, temperature_C, **kwargs):
            frac = max(0.0, min(1.0, (float(temperature_C) - 1000.0) / 300.0))
            return EquilibriumResult(
                temperature_C=float(temperature_C),
                pressure_bar=float(kwargs.get('pressure_bar', 1e-6)),
                liquid_fraction=frac,
                phases_present=['liq'] if frac > 0.0 else ['ol'],
                phase_masses_kg={'liq': frac, 'ol': 1.0 - frac},
                status='ok',
            )

    backend = FakeFinderBackend()

    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 50.0, 'Al2O3': 15.0, 'MgO': 15.0, 'CaO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
        min_T_C=800.0,
        max_T_C=1500.0,
        scan_step_C=100.0,
        tolerance_C=1.0,
    )

    assert result.status == 'ok'
    assert result.solidus_T_C == pytest.approx(1000.0, abs=1.0)
    assert result.liquidus_T_C == pytest.approx(1300.0, abs=1.0)


def _melts_domain_composition() -> dict[str, float]:
    return {
        'SiO2': 50.0,
        'Al2O3': 15.0,
        'FeO': 10.0,
        'MgO': 10.0,
        'CaO': 10.0,
        'Na2O': 5.0,
    }


def _clamped_success_diagnostics() -> LiquidusDiagnostics:
    return LiquidusDiagnostics(
        phases_present=('liq',),
        phase_masses_kg={'liq': 1.0},
        liquid_fraction=1.0,
        fO2_log=-9.0,
        backend_status='ok',
        backend_diagnostics={
            'operating_point_clamped': True,
            'operating_point_transport': 'subprocess',
            'temperature_clamped': True,
            'pressure_clamped': True,
            'requested_temperature_C': 650.0,
            'requested_pressure_bar': 1.0e-6,
            'solved_temperature_C': 800.0,
            'solved_pressure_bar': 1.0,
            'authoritative_for_requested_conditions': False,
            'authoritative_for_solved_conditions': True,
        },
    )


def test_diagnostics_to_equilibrium_clamped_success_is_requested_point_ood():
    result = diagnostics_to_equilibrium(
        _clamped_success_diagnostics(),
        {
            'temperature_C': 650.0,
            'pressure_bar': 1.0e-6,
            'fO2_log': -9.0,
        },
    )

    assert result.status == 'out_of_domain'
    assert result.temperature_C == pytest.approx(800.0)
    assert result.pressure_bar == pytest.approx(1.0)
    assert result.diagnostics['backend_status'] == 'out_of_domain'
    assert (
        result.diagnostics['backend_status_reason']
        == 'clamped_operating_point'
    )
    assert result.diagnostics['requested_temperature_C'] == pytest.approx(650.0)
    assert result.diagnostics['requested_pressure_bar'] == pytest.approx(1.0e-6)
    assert result.diagnostics['authoritative_for_requested_conditions'] is False


def test_alphamelts_subprocess_clamped_pt_reports_solved_conditions(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    seen = {}

    def fake_run(*args, **kwargs):
        seen['input_melts'] = (
            Path(kwargs['cwd']) / 'input.melts'
        ).read_text()
        return types.SimpleNamespace(
            returncode=0,
            stdout='<> Stable synthetic assemblage achieved.',
            stderr='',
        )

    def fake_parse(
        output,
        *,
        temperature_C,
        pressure_bar,
        fO2_log,
        total_input_kg,
        warnings=None,
        diagnostics=None,
        success_diagnostics=None,
    ):
        result_diagnostics = (
            success_diagnostics if success_diagnostics is not None
            else diagnostics
        )
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            phases_present=['liquid'],
            phase_masses_kg={'liquid': 1.0},
            liquid_fraction=1.0,
            warnings=list(warnings or []),
            status='ok',
            diagnostics=dict(result_diagnostics or {}),
        )

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        fake_run,
    )
    monkeypatch.setattr(backend, '_parse_single_point_stdout', fake_parse)

    result = backend.equilibrate(
        temperature_C=650.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert 'Initial Temperature: 800.0' in seen['input_melts']
    assert 'Initial Pressure: 1.0' in seen['input_melts']
    assert result.temperature_C == pytest.approx(800.0)
    assert result.pressure_bar == pytest.approx(1.0)
    assert result.diagnostics['operating_point_clamped'] is True
    assert result.diagnostics['temperature_clamped'] is True
    assert result.diagnostics['pressure_clamped'] is True
    assert result.diagnostics['requested_temperature_C'] == pytest.approx(650.0)
    assert result.diagnostics['requested_pressure_bar'] == pytest.approx(1e-6)
    assert result.diagnostics['solved_temperature_C'] == pytest.approx(800.0)
    assert result.diagnostics['solved_pressure_bar'] == pytest.approx(1.0)
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status'] == 'out_of_domain'
    assert (
        result.diagnostics['backend_status_reason']
        == 'clamped_operating_point'
    )
    assert (
        result.diagnostics['authoritative_for_requested_conditions']
        is False
    )
    assert any('clamped operating point' in warning for warning in result.warnings)


def test_alphamelts_python_api_clamped_pressure_reports_solved_condition(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'python_api'
    seen = {}

    class FakePetThermoTools:
        def equilibrate_MELTS(self, **kwargs):
            seen.update(kwargs)
            return {'ok': True}

    def fake_parse(
        results,
        *,
        temperature_C,
        pressure_bar,
        fO2_log,
        comp_wt,
        warnings=None,
        diagnostics=None,
    ):
        return EquilibriumResult(
            temperature_C=temperature_C,
            pressure_bar=pressure_bar,
            fO2_log=fO2_log,
            phases_present=['liquid'],
            phase_masses_kg={'liquid': 1.0},
            liquid_fraction=1.0,
            warnings=list(warnings or []),
            status='ok',
            diagnostics=dict(diagnostics or {}),
        )

    monkeypatch.setattr(
        backend,
        '_require_petthermotools_runtime',
        lambda: FakePetThermoTools(),
    )
    monkeypatch.setattr(backend, '_parse_petthermotools_result', fake_parse)
    monkeypatch.setattr(
        backend,
        '_activities_times_antoine_or_fail',
        lambda *args, **kwargs: {},
    )

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1e-9,
    )

    assert seen['P_bar'] == pytest.approx(1e-6)
    assert result.temperature_C == pytest.approx(1600.0)
    assert result.pressure_bar == pytest.approx(1e-6)
    assert result.diagnostics['operating_point_clamped'] is True
    assert result.diagnostics['temperature_clamped'] is False
    assert result.diagnostics['pressure_clamped'] is True
    assert result.diagnostics['requested_pressure_bar'] == pytest.approx(1e-9)
    assert result.diagnostics['solved_pressure_bar'] == pytest.approx(1e-6)
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status'] == 'out_of_domain'
    assert (
        result.diagnostics['backend_status_reason']
        == 'clamped_operating_point'
    )
    assert (
        result.diagnostics['authoritative_for_requested_conditions']
        is False
    )
    assert any('clamped operating point' in warning for warning in result.warnings)


def test_alphamelts_subprocess_signal_exit_is_out_of_domain_without_mode_flip(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return types.SimpleNamespace(
                returncode=-6,
                stdout='... liquidus search stopped mid-stream',
                stderr='',
            )
        return types.SimpleNamespace(returncode=0, stdout='stable', stderr='')

    def fake_parse(*args, **kwargs):
        return EquilibriumResult(
            temperature_C=kwargs['temperature_C'],
            pressure_bar=kwargs['pressure_bar'],
            fO2_log=kwargs['fO2_log'],
            phases_present=['liquid'],
            phase_masses_kg={'liquid': 1.0},
            liquid_fraction=1.0,
            status='ok',
        )

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)
    monkeypatch.setattr(backend, '_parse_single_point_stdout', fake_parse)

    first = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    second = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert first.status == 'out_of_domain'
    assert any('SIGABRT' in warning for warning in first.warnings)
    assert (
        first.diagnostics.get('backend_status_reason')
        == ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert 'subprocess exited' in (
        first.diagnostics.get('backend_status_reason_message') or ''
    )
    assert backend._mode == 'subprocess'
    assert second.status == 'ok'
    assert len(calls) == 2


def test_alphamelts_subprocess_timeout_stays_loud_without_mode_flip(monkeypatch):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')
    seen = {}

    def fake_run(*args, **kwargs):
        seen['timeout'] = kwargs['timeout']
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs['timeout'])

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)

    with pytest.raises(RuntimeError, match='timed out') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_TIMEOUT
    )
    assert 'timed out' in getattr(
        excinfo.value,
        'backend_status_reason_message',
    )
    assert seen['timeout'] == 20.0
    assert backend._mode == 'subprocess'


def test_alphamelts_subprocess_uses_configured_timeout(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )
    assert backend.initialize({'mode': 'subprocess', 'timeout_s': 37.5}) is True
    seen = {}

    def fake_run(*args, **kwargs):
        seen['timeout'] = kwargs['timeout']
        return types.SimpleNamespace(returncode=-6, stdout='', stderr='')

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg=_melts_domain_composition(),
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.status == 'out_of_domain'
    assert (
        result.diagnostics.get('backend_status_reason')
        == ALPHAMELTS_REASON_SUBPROCESS_DIED
    )
    assert seen['timeout'] == 37.5
    assert backend._mode == 'subprocess'


def test_alphamelts_subprocess_missing_binary_is_loud_and_disables_mode(monkeypatch):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')

    def fake_run(*args, **kwargs):
        raise FileNotFoundError('missing binary')

    monkeypatch.setattr('simulator.melt_backend.alphamelts.subprocess.run', fake_run)

    with pytest.raises(RuntimeError, match='binary not found') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_MISSING_BINARY
    )
    assert 'binary' in getattr(
        excinfo.value,
        'backend_status_reason_message',
    )
    assert backend._mode is None


def test_alphamelts_subprocess_unconfigured_binary_reports_missing_binary():
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = None
    backend._engine_path = None

    with pytest.raises(RuntimeError, match='not configured') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_MISSING_BINARY
    )


def test_alphamelts_subprocess_positive_exit_stays_loud_without_mode_flip(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=2,
            stdout='',
            stderr='wrapper error',
        ),
    )

    with pytest.raises(RuntimeError, match='returncode 2') as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_NONZERO_EXIT
    )
    assert backend._mode == 'subprocess'


def test_alphamelts_subprocess_exit_zero_without_assemblage_stays_loud(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'
    backend._binary_path = Path('/tmp/fake-alphamelts')

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.subprocess.run',
        lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0,
            stdout='successful run but changed format',
            stderr='',
        ),
    )

    with pytest.raises(
        RuntimeError,
        match='no parseable phase assemblage',
    ) as excinfo:
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg=_melts_domain_composition(),
            fO2_log=-9.0,
            pressure_bar=1.0,
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT
    )

    assert backend._mode == 'subprocess'


def test_configured_unavailable_alphamelts_backend_fail_closes():
    backend = AlphaMELTSBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide", mass_kg=1.0)

    with pytest.raises(RuntimeError, match='AlphaMELTSBackend is unavailable'):
        sim._get_equilibrium()


def test_alphamelts_vapor_species_are_canonical_formula_names():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True

    species = set(backend.get_vapor_species())

    assert 'SiO2' in species
    assert 'Fe2O3' in species
    assert 'SiO2_gas' not in species
    assert 'Fe2O3_gas' not in species


def test_alphamelts_rejects_metal_and_gas_account_inputs():
    backend = AlphaMELTSBackend()

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0},
            'process.metal_phase': {'Fe': 0.25},
            'process.overhead_gas': {'O2': 0.5},
        },
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert result.phases_present == []
    assert result.warnings == [
        "DomainGate rejected: unsupported ledger accounts present: "
        "process.metal_phase=['Fe'], process.overhead_gas=['O2']"
    ]
    assert result.status == 'out_of_domain'
    assert (
        result.diagnostics.get('backend_status_reason')
        == OutOfDomainReason.FORBIDDEN_SPECIES.value
    )


def test_alphamelts_capabilities_surface_engine_version(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.importlib.metadata.version',
        lambda name: '0.test',
    )

    assert backend.capabilities()['engine_version'] == 'petthermotools 0.test'


def test_alphamelts_initialize_requires_petthermotools_payload(monkeypatch):
    backend = AlphaMELTSBackend()
    fake_ptt = types.SimpleNamespace(__version__='0.test')

    def fake_import(name):
        if name in {'petthermotools', 'PetThermoTools'}:
            return fake_ptt
        if name == 'meltsdynamic':
            raise ImportError('no meltsdynamic')
        raise AssertionError(name)

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.importlib.import_module',
        fake_import,
    )

    with pytest.raises(ImportError, match='PetThermoTools Python path unavailable'):
        backend.initialize({'mode': 'python_api'})


def test_alphamelts_require_petthermotools_does_not_use_subprocess(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )

    def fake_import(name):
        if name in {'petthermotools', 'PetThermoTools'}:
            return types.SimpleNamespace(__version__='0.test')
        if name == 'meltsdynamic':
            raise ImportError('no meltsdynamic')
        raise AssertionError(name)

    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.importlib.import_module',
        fake_import,
    )

    with pytest.raises(ImportError, match='PetThermoTools Python path unavailable'):
        backend.initialize({'require_petthermotools': True})
    assert backend._mode is None



def test_alphamelts_provider_production_equilibrium_skips_thermoengine(monkeypatch):
    provider = AlphaMELTSProvider(backend=types.SimpleNamespace())
    request = types.SimpleNamespace(
        account_view=types.SimpleNamespace(species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    monkeypatch.setattr(alphamelts_provider_module, 'thermoengine_available', lambda _backend: True)
    monkeypatch.setattr(alphamelts_provider_module, 'python_api_available', lambda _backend: False)
    monkeypatch.setattr(alphamelts_provider_module, 'subprocess_available', lambda _backend: False)

    def fail_thermoengine(*args, **kwargs):
        raise AssertionError('production equilibrium must not call in-process ThermoEngine')

    monkeypatch.setattr(
        alphamelts_provider_module,
        'equilibrate_via_thermoengine',
        fail_thermoengine,
    )

    mode, equilibrium = provider._run_backend(
        request,
        composition_mol_by_account={'process.cleaned_melt': {'SiO2': 1.0}},
    )

    assert mode == 'unavailable'
    assert equilibrium is None


def test_alphamelts_provider_liquidus_skips_thermoengine(monkeypatch):
    def fail_liquidus(*args, **kwargs):
        raise AssertionError('liquidus must not call in-process ThermoEngine')

    provider = AlphaMELTSProvider(
        backend=types.SimpleNamespace(find_liquidus_solidus=fail_liquidus)
    )
    request = types.SimpleNamespace(
        account_view=types.SimpleNamespace(species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    monkeypatch.setattr(
        alphamelts_provider_module,
        'thermoengine_available',
        lambda _backend: True,
    )
    monkeypatch.setattr(
        alphamelts_provider_module,
        'python_api_available',
        lambda _backend: False,
    )
    monkeypatch.setattr(
        alphamelts_provider_module,
        'subprocess_available',
        lambda _backend: False,
    )

    mode, result = provider._run_liquidus_finder(
        request,
        composition_mol_by_account={'process.cleaned_melt': {'SiO2': 1.0}},
    )

    assert mode == 'unavailable'
    assert result.status == 'unavailable'


def test_alphamelts_provider_ec_skips_thermoengine(monkeypatch):
    def fail_transport(*args, **kwargs):
        raise AssertionError('EC must not call in-process ThermoEngine')

    provider = AlphaMELTSProvider(
        backend=types.SimpleNamespace(find_liquidus_solidus=fail_transport)
    )
    request = types.SimpleNamespace(
        account_view=types.SimpleNamespace(species_formula_registry={}),
        temperature_C=1400.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    monkeypatch.setattr(
        alphamelts_provider_module,
        'thermoengine_available',
        lambda _backend: True,
    )
    monkeypatch.setattr(
        alphamelts_provider_module,
        'python_api_available',
        lambda _backend: False,
    )
    monkeypatch.setattr(
        alphamelts_provider_module,
        'subprocess_available',
        lambda _backend: False,
    )
    monkeypatch.setattr(
        alphamelts_provider_module,
        'equilibrate_via_thermoengine',
        fail_transport,
    )

    mode, result = provider._run_equilibrium_crystallization_path(
        request,
        composition_mol_by_account={'process.cleaned_melt': {'SiO2': 1.0}},
    )

    assert mode == 'unavailable'
    assert result.status == 'unavailable'


def test_alphamelts_domain_gate_contract_is_composition_only():
    doc = AlphaMELTSDomainGate.__doc__ or ''

    assert 'T / P bounds' not in doc
    assert 'Composition-only gate' in doc


def test_alphamelts_initialize_defaults_to_subprocess_when_binary_available(
    monkeypatch,
):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )

    assert backend.initialize({}) is True
    assert backend._mode == 'subprocess'


def test_alphamelts_initialize_explicit_thermoengine_when_available(monkeypatch):
    class FakeThermoEngineTransport:
        engine_version = 'thermoengine fake'

        def __init__(self, *, model_name, activity_converter):
            self.model_name = model_name
            self.activity_converter = activity_converter

        def initialize(self):
            return True

    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        'simulator.melt_backend.alphamelts.ThermoEngineTransport',
        FakeThermoEngineTransport,
    )

    assert backend.initialize({'mode': 'thermoengine'}) is True
    assert backend._mode == 'thermoengine'
    assert backend.get_engine_version() == 'thermoengine fake'


def test_alphamelts_configured_subprocess_skips_thermoengine(monkeypatch):
    backend = AlphaMELTSBackend()
    monkeypatch.setattr(
        backend,
        '_find_project_binary',
        lambda _engine_root: Path('/tmp/fake-alphamelts'),
    )

    assert backend.initialize({'mode': 'subprocess'}) is True
    assert backend._mode == 'subprocess'


def test_alphamelts_config_top_level_subprocess_overrides_nested_thermoengine():
    backend = AlphaMELTSBackend()

    config = backend._alphamelts_config({
        'mode': 'subprocess',
        'python_bridge': 'subprocess',
        'alphamelts': {
            'mode': 'thermoengine',
            'python_bridge': 'pymagemin',
        },
    })

    assert config['mode'] == 'subprocess'
    assert config['python_bridge'] == 'subprocess'


def test_normalize_composition_to_melts_basis_drops_and_renormalizes():
    backend = AlphaMELTSBackend()

    comp = backend._normalize_composition_to_melts_basis({
        'SiO2': 45.0,
        'Al2O3': 15.0,
        'FeO': 10.0,
        'MgO': 10.0,
        'CaO': 10.0,
        'Na2O': 5.0,
        'H2O': 5.0,
    })

    assert pytest.approx(sum(comp.values())) == 100.0
    assert comp['SiO2'] == pytest.approx(47.3684210526)
    assert backend._last_normalization_warnings == [
        'Dropped non-MELTS component H2O'
    ]


def test_normalize_composition_refuses_feo_total_without_policy():
    backend = AlphaMELTSBackend()

    with pytest.raises(ValueError, match='FeO_total requires explicit redox policy'):
        backend._normalize_composition_to_melts_basis({
            'SiO2': 50.0,
            'FeO_total': 10.0,
        })


def test_normalize_composition_splits_feo_total_with_explicit_ratio():
    backend = AlphaMELTSBackend()
    backend._fe3fet_ratio = 0.2

    comp = backend._normalize_composition_to_melts_basis({
        'SiO2': 50.0,
        'Al2O3': 20.0,
        'FeO_total': 10.0,
        'MgO': 20.0,
    })

    assert pytest.approx(sum(comp.values())) == 100.0
    assert comp['FeO'] > comp['Fe2O3'] > 0.0



def test_alphamelts_domain_gate_rejects_unrecognized_oxide_like_species():
    backend = AlphaMELTSBackend()

    result = backend._domain_gate(
        {'SiO2': 50.0, 'MgO': 49.0, 'XeO': 1.0},
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    assert result is not None
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        OutOfDomainReason.FORBIDDEN_SPECIES.value
    )
    assert 'unrecognised species outside MELTS basis: XeO' in result.warnings[0]


def test_alphamelts_domain_gate_rejects_exact_major_oxide_boundary():
    backend = AlphaMELTSBackend()

    result = backend._domain_gate(
        {'SiO2': 50.0, 'MgO': 45.0},
        temperature_C=1500.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
    )

    assert result is not None
    assert result.status == 'out_of_domain'
    assert result.diagnostics['backend_status_reason'] == (
        OutOfDomainReason.MAJOR_SUM.value
    )
    assert result.warnings == [
        'DomainGate rejected: major oxide sum 95.000 wt% <= 95'
    ]


def test_domain_gate_rejects_non_silicate_or_non_oxide_inputs():
    backend = AlphaMELTSBackend()

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 90.0, 'Fe': 10.0},
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert result.phases_present == []
    assert result.warnings == [
        'DomainGate rejected: SiO2 90.000 wt% outside [30, 80]; '
        'major oxide sum 90.000 wt% <= 95; non-oxide species present: Fe'
    ]
    assert result.status == 'out_of_domain'


def test_petthermotools_result_parser_uses_verified_schema():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        'liquid1_prop': {'mass': 80.0, 'Na_activity': 2.0},
        'olivine1': {'SiO2': 40.0, 'MgO': 50.0},
        'olivine1_prop': {'mass': 20.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
    )

    assert result.phases_present == ['liquid1', 'olivine1']
    assert result.phase_masses_kg == {
        'liquid1': pytest.approx(0.08),
        'olivine1': pytest.approx(0.02),
    }
    assert result.liquid_fraction == pytest.approx(0.8)
    assert result.liquid_composition_wt_pct['SiO2'] == pytest.approx(50.0)
    assert result.activity_coefficients == {}
    assert result.warnings == [
        'PetThermoTools chemical potentials absent; '
        'activity-scaled Antoine fallback skipped'
    ]
    assert result.ledger_transition is None


@pytest.mark.parametrize(
    ('mu', 'mu0', 'temperature_K', 'expected'),
    [
        (-1000.0, -1000.0, 1800.0, 1.0),
        (-900.0, -1000.0, 1800.0, math.exp(100.0 / (8.31446261815324 * 1800.0))),
        (-1100.0, -1000.0, 1800.0, math.exp(-100.0 / (8.31446261815324 * 1800.0))),
    ],
)
def test_activity_from_chem_potential_matches_vaporock_convention(
    mu,
    mu0,
    temperature_K,
    expected,
):
    assert activity_from_chem_potential(mu, mu0, temperature_K) == pytest.approx(
        expected
    )


def test_activity_from_chem_potential_is_monotone_in_mu():
    low = activity_from_chem_potential(-1100.0, -1000.0, 1800.0)
    equal = activity_from_chem_potential(-1000.0, -1000.0, 1800.0)
    high = activity_from_chem_potential(-900.0, -1000.0, 1800.0)
    assert low < equal < high


def test_petthermotools_result_parser_converts_mu_to_activity():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
        'liquid1_prop': {'mass': 80.0},
        'olivine1': {'SiO2': 40.0, 'MgO': 50.0},
        'olivine1_prop': {'mass': 20.0},
        'chemical_potentials': {'Na': -900.0, 'K': -1050.0},
        'pure_chemical_potentials': {'Na': -1000.0, 'K': -1000.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1526.85,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0},
    )

    assert result.activity_coefficients == pytest.approx({
        'Na': activity_from_chem_potential(-900.0, -1000.0, 1800.0),
        'K': activity_from_chem_potential(-1050.0, -1000.0, 1800.0),
    })


def test_thermoengine_activity_extractor_uses_mu_minus_mu0():
    class FakeLiquidPhase:
        endmember_names = ['Na']

        def chem_potential(self, T_K, P_bar, mol=None):
            assert mol == [[1.0]]
            return [[-900.0]]

        def gibbs_energy(self, T_K, P_bar, mol=None):
            assert mol == [[1.0]]
            return [-1000.0]

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    transport._liq_phase = FakeLiquidPhase()

    activities = transport._activities_from_chemical_potentials(
        temperature_C=1526.85,
        pressure_bar=1.0,
        component_mole_fraction={'Na': 1.0},
        comp_wt={},
    )

    assert activities == pytest.approx({
        'Na': activity_from_chem_potential(-900.0, -1000.0, 1800.0),
    })


def test_thermoengine_public_equilibrate_runs_in_process(monkeypatch):
    class FakeMelts:
        bulk_wt: dict[str, float] | None = None

        def get_oxide_names(self):
            return ('SiO2', 'Al2O3')

        def set_bulk_composition(self, bulk_wt):
            self.bulk_wt = dict(bulk_wt)

        def equilibrate_tp(self, temperature_C, pressure_mpa, *, initialize):
            assert temperature_C == 1200.0
            assert pressure_mpa == pytest.approx(0.1)
            assert initialize is True
            return [('success', temperature_C, pressure_mpa, 'root')]

        def get_list_of_phases_in_assemblage(self, root):
            assert root == 'root'
            return ('Spinel',)

        def get_mass_of_phase(self, root, phase):
            assert root == 'root'
            assert phase == 'Spinel'
            return 1000.0

    class FakeEquilibrate:
        def __init__(self):
            self.melts = FakeMelts()
            self.version = None

        def MELTSmodel(self, *, version):
            self.version = version
            return self.melts

    transport = ThermoEngineTransport(
        activity_converter=activity_from_chem_potential,
    )
    fake_equilibrate = FakeEquilibrate()
    transport._equilibrate = fake_equilibrate
    transport._liq_phase = object()

    def fail_run(*_args, **_kwargs):
        raise AssertionError('public equilibrate must not spawn subprocess')

    monkeypatch.setattr('engines.alphamelts.thermoengine.subprocess.run', fail_run)

    result = transport.equilibrate(
        temperature_C=1200.0,
        pressure_bar=1.0,
        comp_wt={'SiO2': 50.0, 'Al2O3': 0.0},
        warnings=('input-warning',),
    )

    assert fake_equilibrate.version == '1.0.2'
    assert fake_equilibrate.melts.bulk_wt == {'SiO2': 50.0}
    assert result.phases_present == ('Spinel',)
    assert result.phase_masses_kg == {'Spinel': pytest.approx(1.0)}
    assert result.liquid_fraction == 0.0
    assert result.liquid_composition_wt_pct == {}


def test_thermoengine_transport_equilibrates_live_when_installed():
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({'mode': 'thermoengine'})
    except ImportError as exc:
        pytest.skip(f'ThermoEngine transport unavailable: {exc}')
    if not available:
        pytest.skip('ThermoEngine transport unavailable')

    result = backend.equilibrate(
        temperature_C=1200.0,
        composition_kg={
            'SiO2': 490.0,
            'TiO2': 15.0,
            'Al2O3': 140.0,
            'FeO': 100.0,
            'Fe2O3': 10.0,
            'MgO': 90.0,
            'CaO': 110.0,
            'Na2O': 25.0,
            'K2O': 8.0,
            'Cr2O3': 2.0,
            'MnO': 2.0,
            'P2O5': 3.0,
        },
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.status == 'ok'
    assert backend._mode == 'thermoengine'
    assert result.ledger_transition is None
    assert result.phases_present
    assert result.phase_masses_kg
    assert 0.0 <= result.liquid_fraction <= 1.0
    assert result.activity_coefficients
    assert 'SiO2' in result.activity_coefficients
    assert result.activity_coefficients['SiO2'] > 0.0
    assert result.fe_redox_split['FeO_wt_pct'] > 0.0
    assert result.fe_redox_split['Fe2O3_wt_pct'] > 0.0


def test_thermoengine_transport_shadow_parity_against_subprocess_when_available():
    thermo = AlphaMELTSBackend()
    try:
        thermo_ok = thermo.initialize({'mode': 'thermoengine'})
    except ImportError as exc:
        pytest.skip(f'ThermoEngine transport unavailable: {exc}')
    if not thermo_ok:
        pytest.skip('ThermoEngine transport unavailable')

    subprocess_backend = AlphaMELTSBackend()
    try:
        subprocess_ok = subprocess_backend.initialize({'mode': 'subprocess'})
    except ImportError as exc:
        pytest.skip(f'AlphaMELTS subprocess transport unavailable: {exc}')
    if not subprocess_ok:
        pytest.skip('AlphaMELTS subprocess transport unavailable')

    composition_kg = {
        'SiO2': 490.0,
        'TiO2': 15.0,
        'Al2O3': 140.0,
        'FeO': 100.0,
        'Fe2O3': 10.0,
        'MgO': 90.0,
        'CaO': 110.0,
        'Na2O': 25.0,
        'K2O': 8.0,
        'Cr2O3': 2.0,
        'MnO': 2.0,
        'P2O5': 3.0,
    }
    thermo_result = thermo.equilibrate(
        temperature_C=1200.0,
        composition_kg=composition_kg,
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    subprocess_result = subprocess_backend.equilibrate(
        temperature_C=1200.0,
        composition_kg=composition_kg,
        fO2_log=-9.0,
        pressure_bar=1.0,
    )
    if not subprocess_result.phase_masses_kg:
        pytest.skip('AlphaMELTS subprocess did not report modal phase masses')

    def canonical_modes(result):
        return {
            'phase_masses_kg': {
                str(phase).lower(): mass
                for phase, mass in result.phase_masses_kg.items()
            },
        }

    report = MAGEMinParityComparator().compare(
        canonical_modes(thermo_result),
        canonical_modes(subprocess_result),
    )
    assert report.agreement, report.warnings


def test_activities_times_antoine_computes_activity_times_ppure_from_yaml():
    backend = AlphaMELTSBackend()

    pressures = backend._activities_times_antoine(
        1600.0,
        {'Na': 2.0, 'K': 1.0, 'unknown': 10.0},
        {'SiO2': 100.0},
    )
    table = _load_data('vapor_pressures.yaml')['metals']
    T_K = 1600.0 + 273.15
    expected_na = 2.0 * 10.0 ** (
        table['Na']['pure_component_antoine']['A']
        - table['Na']['pure_component_antoine']['B']
        / (T_K + table['Na']['pure_component_antoine']['C'])
    )

    assert set(pressures) == {'Na', 'K'}
    assert pressures['Na'] == pytest.approx(expected_na)
    assert pressures['K'] > 0.0


def test_activities_times_antoine_maps_thermoengine_liquid_activity_keys():
    backend = AlphaMELTSBackend()

    pressures = backend._activities_times_antoine(
        1600.0,
        {
            'SiO2': 0.4,
            'Na2O': 0.2,
            'KAlSi3O8': 0.1,
            'CaSiO3': 0.3,
            'Mg2SiO4': 0.5,
            'Al2O3': 0.6,
        },
        {'SiO2': 45.0, 'Na2O': 4.0, 'K2O': 1.0},
    )

    assert {'Na', 'K', 'Si', 'SiO', 'Ca', 'Mg', 'Al'} <= set(pressures)
    assert all(pressures[species] > 0.0 for species in (
        'Na', 'K', 'Si', 'SiO', 'Ca', 'Mg', 'Al',
    ))


def test_activities_times_antoine_returns_empty_without_species_activity():
    backend = AlphaMELTSBackend()

    assert backend._activities_times_antoine(
        1600.0,
        {},
        {'Na2O': 100.0},
    ) == {}


def test_activities_times_antoine_warns_once_for_pseudo_curvefit():
    backend = AlphaMELTSBackend()
    backend._vapor_pressure_table = {
        'K': {
            'fit_target': 'pseudo_psat_backsolved_from_vaporock',
            'residual_dex': 1.4,
            'confidence_tier': 'low',
            'antoine': {'A': 5.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with pytest.warns(
        HighUncertaintyVaporPressureFallbackWarning,
        match=(
            'HIGH-UNCERTAINTY WARNING: K vapor pressure uses a backsolved '
            'VapoRock fallback \\(curve-fit\\), NOT first-principles; '
            'residual_dex=1.4; confidence_tier=low; '
            'builtin remains authoritative; VapoRock is diagnostic-only'
        ),
    ):
        first = backend._activities_times_antoine(
            1600.0,
            {'K2O': 0.5},
            {'K2O': 1.0},
        )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        second = backend._activities_times_antoine(
            1600.0,
            {'K2O': 0.5},
            {'K2O': 1.0},
        )

    assert caught == []
    assert first == second
    assert first['K'] == pytest.approx(5.0e4)
    assert backend._antoine_vapor_pressure_source_by_species(
        'alphamelts_python_api',
        first,
    ) == {'K': 'alphamelts_python_api:backsolved_vaporock_curve_fit'}


def test_activities_times_antoine_uncertified_pure_component_is_silent():
    backend = AlphaMELTSBackend()
    backend._vapor_pressure_table = {
        'K': {
            'fit_target': 'pure_component_psat',
            'residual_dex': 0.01,
            'confidence_tier': 'high',
            'antoine': {'A': 4.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        pressures = backend._activities_times_antoine(
            1600.0,
            {'K2O': 0.5},
            {'K2O': 1.0},
        )

    assert caught == []
    assert pressures == {'K': pytest.approx(5.0e3)}
    assert backend._antoine_vapor_pressure_source_by_species(
        'alphamelts_python_api',
        pressures,
    ) == {'K': 'alphamelts_python_api:legacy_pure_component_estimate'}


def test_real_vaporock_path_does_not_warn_for_pseudo_fallback_rows():
    class RealVapoRock:
        def is_available(self):
            return True

        def equilibrate(self, **_kwargs):
            return EquilibriumResult(
                status='ok',
                liquid_fraction=1.0,
                vapor_pressures_Pa={'K': 12.0},
                warnings=[],
            )

    backend = AlphaMELTSBackend()
    backend._vaporock_helper = RealVapoRock()
    backend._vapor_pressure_table = {
        'K': {
            'fit_target': 'pseudo_psat_backsolved_from_vaporock',
            'residual_dex': 1.4,
            'confidence_tier': 'low',
            'antoine': {'A': 5.0, 'B': 0.0, 'C': 0.0},
        },
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'K2O': 1.0},
            liquid_fraction=1.0,
            fO2_log=-9.0,
            pressure_bar=1.0,
            activities={'K2O': 0.5},
        )

    assert caught == []
    assert pressures == {'K': 12.0}
    assert source == 'vaporock'


def test_vaporock_empty_and_antoine_empty_fails_loud_for_volatile_melt():
    class EmptyVapoRock:
        def is_available(self):
            return True

        def equilibrate(self, **_kwargs):
            return EquilibriumResult(
                status='not_converged',
                vapor_pressures_Pa={},
                warnings=['forced empty VapoRock result'],
            )

    backend = AlphaMELTSBackend()
    backend._vaporock_helper = EmptyVapoRock()

    with pytest.warns(UserWarning, match='using activity x Antoine fallback rows'):
        with pytest.raises(
            RuntimeError,
            match='volatile-bearing melt.*silently zero evaporation flux',
        ):
            backend._vapor_pressures_via_vaporock_or_antoine(
                T_C=1600.0,
                solved_melt_wt_pct={'SiO2': 95.0, 'Na2O': 5.0},
                liquid_fraction=1.0,
                fO2_log=-9.0,
                pressure_bar=1.0,
                activities={},
            )


def test_vaporock_empty_volatile_free_melt_returns_physical_zero():
    class EmptyVapoRock:
        def is_available(self):
            return True

        def equilibrate(self, **_kwargs):
            return EquilibriumResult(
                status='not_converged',
                vapor_pressures_Pa={},
                warnings=['forced empty VapoRock result'],
            )

    backend = AlphaMELTSBackend()
    backend._vaporock_helper = EmptyVapoRock()

    with pytest.warns(UserWarning, match='using activity x Antoine fallback rows'):
        pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'P2O5': 100.0},
            liquid_fraction=1.0,
            fO2_log=-9.0,
            pressure_bar=1.0,
            activities={},
        )

    assert pressures == {}
    assert source == 'no_volatile_species'


def test_decompression_path_calls_verified_petthermotools_api():
    backend = AlphaMELTSBackend()
    calls = []

    def fake_decompression(**kwargs):
        calls.append(kwargs)
        return {
            0: {
                'Conditions': {'mass': 100.0},
                'liquid1': {'SiO2': 50.0},
                'liquid1_prop': {'mass': 100.0},
            },
            1: {
                'Conditions': {'mass': 100.0},
                'liquid1': {'SiO2': 49.0},
                'liquid1_prop': {'mass': 95.0},
                'olivine1': {'SiO2': 40.0},
                'olivine1_prop': {'mass': 5.0},
            },
        }

    backend._mode = 'python_api'
    backend._pet_module = types.SimpleNamespace(
        isothermal_decompression=fake_decompression)
    backend._pet_payload_preloaded = True
    backend._pet_melts = object()
    backend._redox_buffer = 'QFM'
    backend._fo2_offset = -1.5

    results = backend.decompression_path(
        1200.0,
        1000.0,
        1.0,
        100.0,
        composition_kg={
            'SiO2': 50.0,
            'Al2O3': 15.0,
            'FeO': 10.0,
            'MgO': 10.0,
            'CaO': 10.0,
            'Na2O': 5.0,
        },
    )

    assert len(results) == 2
    assert calls[0]['T_C'] == 1200.0
    assert calls[0]['P_start_bar'] == 1000.0
    assert calls[0]['P_end_bar'] == 1.0
    assert calls[0]['dp_bar'] == 100.0
    assert calls[0]['fO2_buffer'] == 'QFM'
    assert calls[0]['fO2_offset'] == -1.5
    assert calls[0]['bulk']['FeOt_Liq'] == pytest.approx(10.0)


def test_alphamelts_stdout_parser_solid_only_reports_zero_liquid_fraction():
    backend = AlphaMELTSBackend()
    output = """
<> Stable solid assemblage achieved.
olivine: 90.3451 g, composition (Ca0.01Mg0.80Fe''0.20Mn0.00Co0.00Ni0.00)2SiO4
"""

    result = backend._parse_single_point_stdout(
        output,
        temperature_C=1100.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        total_input_kg=1000.0,
    )

    assert result.liquid_fraction == pytest.approx(0.0)
    assert result.phases_present == ["olivine"]
    assert "liquid" not in result.phases_present
    assert result.status == "ok"


def test_alphamelts_stdout_parser_reports_liquid_fraction_without_ledger_transition():
    backend = AlphaMELTSBackend()
    output = """
<> Stable liquid solid assemblage achieved.
<> Found the liquidus at T = 1220.31 (C).
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid:    SiO2 TiO2 Al2O3 Fe2O3 Cr2O3   FeO  MnO  MgO  NiO  CoO   CaO Na2O  K2O P2O5  H2O
90.3451 g 46.49 2.21 16.60  0.00  0.00 11.71 0.00 7.54 0.00 0.00 11.02 3.32 1.11 0.00 0.00
Activity of H2O = 0  Melt fraction = 0.921889
olivine: 7.654887 g, composition (Ca0.01Mg0.80Fe''0.20Mn0.00Co0.00Ni0.00)2SiO4
"""

    result = backend._parse_single_point_stdout(
        output,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        total_input_kg=1000.0,
    )

    assert result.liquid_fraction == pytest.approx(0.921889)
    assert result.liquid_composition_wt_pct["SiO2"] == pytest.approx(46.49)
    assert result.phases_present == ["liquid", "olivine"]
    assert result.phase_masses_kg == {
        "liquid": pytest.approx(0.0903451),
        "olivine": pytest.approx(0.007654887),
    }
    assert result.warnings == ["AlphaMELTS liquidus_C=1220.310"]
    assert result.ledger_transition is None
    assert result.status == 'ok'


def test_alphamelts_stdout_parser_accepts_parseable_rows_without_stable_banner():
    backend = AlphaMELTSBackend()
    output = """
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid:    SiO2 TiO2 Al2O3 Fe2O3 Cr2O3   FeO  MnO  MgO  NiO  CoO   CaO Na2O  K2O P2O5  H2O
90.3451 g 46.49 2.21 16.60  0.00  0.00 11.71 0.00 7.54 0.00 0.00 11.02 3.32 1.11 0.00 0.00
Activity of H2O = 0  Melt fraction = 0.921889
olivine: 7.654887 g, composition (Ca0.01Mg0.80Fe''0.20Mn0.00Co0.00Ni0.00)2SiO4
"""

    result = backend._parse_single_point_stdout(
        output,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        total_input_kg=1000.0,
    )

    assert result.status == "ok"
    assert result.phases_present == ["liquid", "olivine"]
    assert result.liquid_fraction == pytest.approx(0.921889)
    assert result.warnings == [
        "AlphaMELTS stable assemblage banner absent; accepted parseable phase rows"
    ]


def test_alphamelts_stdout_parser_classifies_no_phase_convergence_failure():
    backend = AlphaMELTSBackend()
    output = """
<> Found the liquidus at T = 1249.41 (C).
...Checking saturation state of potential solids.
...Adding the solid phase olivine to the assemblage.
...Projecting equality constraints.
...Minimizing the thermodynamic potential.
...Quadratic convergence failure. Aborting.
Initial calculation failed (1.000000 bars, 1249.414062 C)!
"""

    result = backend._parse_single_point_stdout(
        output,
        temperature_C=1250.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        total_input_kg=1000.0,
    )

    assert result.status == "out_of_domain"
    assert result.phases_present == []
    assert result.warnings == [
        "AlphaMELTS liquidus_C=1249.410",
        "AlphaMELTS subprocess failed before phase rows: "
        "Quadratic convergence failure. Aborting.",
    ]
    assert (
        result.diagnostics.get("backend_status_reason")
        == ALPHAMELTS_REASON_NO_CONVERGENCE
    )
    assert 'no convergence' in (
        result.diagnostics.get("backend_status_reason_message") or ''
    )


def test_alphamelts_stdout_parser_classifies_initial_calculation_failed():
    # Isolate the second no-phase classifier branch ('Initial calculation
    # failed') -- the combined-fixture test above is dominated by the
    # 'Quadratic convergence failure' branch, so this asserts the
    # Initial-calculation path also maps to NO_CONVERGENCE.
    backend = AlphaMELTSBackend()
    output = """
<> Found the liquidus at T = 1249.41 (C).
...Checking saturation state of potential solids.
...Projecting equality constraints.
Initial calculation failed (1.000000 bars, 1249.414062 C)!
"""

    result = backend._parse_single_point_stdout(
        output,
        temperature_C=1250.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        total_input_kg=1000.0,
    )

    assert result.status == "out_of_domain"
    assert result.phases_present == []
    assert (
        "AlphaMELTS subprocess failed before phase rows: "
        "Initial calculation failed."
    ) in result.warnings
    assert (
        result.diagnostics.get("backend_status_reason")
        == ALPHAMELTS_REASON_NO_CONVERGENCE
    )
    assert 'no convergence' in (
        result.diagnostics.get("backend_status_reason_message") or ''
    )


def test_alphamelts_stdout_parser_fails_without_stable_assemblage():
    backend = AlphaMELTSBackend()

    with pytest.raises(
        RuntimeError,
        match="parseable phase assemblage",
    ) as excinfo:
        backend._parse_single_point_stdout(
            "Error in SILMIN file input procedure.",
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            total_input_kg=1000.0,
        )

    assert (
        getattr(excinfo.value, 'backend_status_reason')
        == ALPHAMELTS_REASON_PARSE_EMPTY_OUTPUT
    )


def test_project_local_alphamelts_reports_liquidus_when_installed():
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({'mode': 'subprocess'})
    except ImportError as exc:
        pytest.skip(f"project-local alphaMELTS app is not installed: {exc}")
    if not available:
        pytest.skip("project-local alphaMELTS app is not installed")

    result = backend.equilibrate(
        temperature_C=1200.0,
        composition_kg={
            "SiO2": 450.0,
            "Al2O3": 150.0,
            "FeO": 120.0,
            "MgO": 100.0,
            "CaO": 100.0,
            "Na2O": 30.0,
            "K2O": 10.0,
            "TiO2": 20.0,
        },
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert result.liquid_fraction == pytest.approx(1.0)
    assert any(message.startswith("AlphaMELTS liquidus_C=")
               for message in result.warnings)
    assert result.ledger_transition is None


def test_project_local_alphamelts_cold_c0_step_returns_when_installed():
    backend = AlphaMELTSBackend()
    try:
        available = backend.initialize({'mode': 'subprocess'})
    except ImportError as exc:
        pytest.skip(f"project-local alphaMELTS app is not installed: {exc}")
    if not available:
        pytest.skip("project-local alphaMELTS app is not installed")

    sim = PyrolysisSimulator(
        backend,
        _load_data("setpoints.yaml"),
        _load_data("feedstocks.yaml"),
        _load_data("vapor_pressures.yaml"),
    )
    sim.load_batch("lunar_mare_low_ti", mass_kg=1000.0)
    sim.start_campaign(CampaignPhase.C0)

    started = time.monotonic()
    snapshot = sim.step()
    elapsed_s = time.monotonic() - started

    assert snapshot.hour == 1
    assert elapsed_s < 5.0


def test_no_mode_marks_status_unavailable():
    # An AlphaMELTSBackend with no thermoengine, python_api, or subprocess mode reaches
    # the explicit "no engine present" return path in equilibrate(); the
    # result is labelled 'unavailable'.
    backend = AlphaMELTSBackend()
    assert backend._mode is None  # default state

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={
            'SiO2': 50.0, 'Al2O3': 15.0, 'FeO': 10.0,
            'MgO': 10.0, 'CaO': 10.0, 'Na2O': 5.0,
        },
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert result.status == 'unavailable'


def test_petthermotools_result_parser_marks_status_ok():
    # _parse_petthermotools_result is the success path of the python_api
    # mode -- a parsed PetThermoTools result must be labelled 'ok'.
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'SiO2': 50.0},
        'liquid1_prop': {'mass': 100.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0},
    )

    assert result.status == 'ok'
    assert result.liquid_fraction == pytest.approx(1.0)
    assert result.liquid_composition_wt_pct == {'SiO2': 50.0}


def test_petthermotools_parser_preserves_empty_liquid_composition():
    backend = AlphaMELTSBackend()
    results = ({
        'Conditions': {'mass': 100.0},
        'liquid1': {'not_an_oxide': 50.0},
        'liquid1_prop': {'mass': 25.0},
        'olivine1': {'MgO': 75.0},
        'olivine1_prop': {'mass': 75.0},
    }, {})

    result = backend._parse_petthermotools_result(
        results,
        temperature_C=1200.0,
        pressure_bar=1.0,
        fO2_log=-9.0,
        comp_wt={'SiO2': 50.0, 'Na2O': 5.0},
    )

    assert result.status == 'ok'
    assert result.liquid_fraction == pytest.approx(0.25)
    assert result.liquid_composition_wt_pct == {}


# ----------------------------------------------------------------------
# VapoRock vapor-bridge: alphaMELTS delegates to the real VapoRockBackend
# helper for vapor pressures (no re-implemented stub). Tests below mock
# the helper's EquilibriumResult so they do not need a live alphaMELTS or
# the upstream library; the live numbers are validated separately in the
# vaporock backend test + the manual physics-sanity check.
# ----------------------------------------------------------------------


def _vaporock_helper_returning(pressures, status='ok'):
    """A stand-in VapoRockBackend whose equilibrate() returns a known result."""
    class _Helper:
        def __init__(self):
            self.calls = []

        def is_available(self):
            return True

        def equilibrate(self, *, temperature_C, composition_kg,
                        fO2_log, pressure_bar):
            self.calls.append({
                'temperature_C': temperature_C,
                'composition_kg': dict(composition_kg),
                'fO2_log': fO2_log,
                'pressure_bar': pressure_bar,
            })
            return EquilibriumResult(
                temperature_C=temperature_C,
                pressure_bar=pressure_bar,
                fO2_log=fO2_log,
                status=status,
                liquid_fraction=1.0 if status == 'ok' else None,
                vapor_pressures_Pa=dict(pressures),
            )
    return _Helper()


def test_vapor_bridge_happy_path_labels_source_vaporock_and_passes_solved_comp():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 11.5, 'SiO': 0.4, 'Fe': 1.57})
    backend._vaporock_helper = helper

    solved = {'SiO2': 45.0, 'FeO': 18.0, 'Na2O': 0.4}
    pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
        T_C=1600.0,
        solved_melt_wt_pct=solved,
        liquid_fraction=1.0,
        fO2_log=-7.96,
        pressure_bar=1e-6,
        activities={'Na2O': 0.1},
    )

    # vapor_pressures_Pa is ALREADY Pa -> returned verbatim, no 1e5 rescale.
    assert source == 'vaporock'
    assert pressures == {'Na': 11.5, 'SiO': 0.4, 'Fe': 1.57}
    # The SOLVED equilibrium liquid was fed to VapoRock, not the fallback.
    assert helper.calls[0]['composition_kg'] == solved
    # Absolute fO2_log forwarded unchanged (no buffer/offset conversion).
    assert helper.calls[0]['fO2_log'] == pytest.approx(-7.96)


def test_vapor_bridge_no_liquid_phase_returns_physical_zero_without_helper():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 5.0})
    backend._vaporock_helper = helper

    pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
        T_C=1600.0,
        solved_melt_wt_pct={},
        liquid_fraction=0.0,
        fO2_log=-8.0,
        pressure_bar=1e-6,
        activities={},
    )

    assert source == 'no_liquid_phase'
    assert pressures == {}
    assert helper.calls == []

    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.melt = types.SimpleNamespace(temperature_C=1600.0)
    flux = sim._calculate_evaporation(EquilibriumResult(
        status='ok',
        liquid_fraction=0.0,
        vapor_pressures_Pa=pressures,
    ))
    assert flux.species_kg_hr == {}


def test_vapor_bridge_liquid_present_empty_solved_comp_fails_loud():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 5.0})
    backend._vaporock_helper = helper

    with pytest.raises(
        RuntimeError,
        match='missing_solved_liquid_composition.*bulk-composition',
    ):
        backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={},
            liquid_fraction=0.25,
            fO2_log=-8.0,
            pressure_bar=1e-6,
            activities={},
        )

    assert helper.calls == []


def test_vapor_bridge_helper_unavailable_uses_explicit_antoine_fallback_nonempty():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True

    class _Down:
        def is_available(self):
            return False

        def equilibrate(self, **_kw):  # pragma: no cover - must not be called
            raise AssertionError('unavailable helper must not be called')

    backend._vaporock_helper = _Down()

    # Real activities so the Antoine fallback emits a non-empty dict.
    pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
        T_C=1600.0,
        solved_melt_wt_pct={'SiO2': 45.0, 'Na2O': 4.0, 'K2O': 1.0},
        liquid_fraction=1.0,
        fO2_log=-8.0,
        pressure_bar=1e-6,
        activities={'Na2O': 0.2, 'SiO2': 0.4},
    )

    assert source["Na"] == (
        "antoine_fallback_from_vaporock:backsolved_vaporock_curve_fit"
    )
    assert source["SiO"] == (
        "antoine_fallback_from_vaporock:backsolved_vaporock_curve_fit"
    )
    # FAIL-LOUD: the fallback is a real Antoine dict, NOT a silent {} that
    # would zero the evaporation flux.
    assert pressures != {}
    assert 'Na' in pressures and pressures['Na'] > 0.0


def test_vapor_bridge_empty_vaporock_result_falls_back_to_antoine_with_label():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True
    # Helper available but returns out_of_domain + empty pressures.
    backend._vaporock_helper = _vaporock_helper_returning(
        {}, status='out_of_domain')

    with pytest.warns(UserWarning):
        pressures, source = backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'SiO2': 45.0, 'Na2O': 4.0},
            liquid_fraction=1.0,
            fO2_log=-8.0,
            pressure_bar=1e-6,
            activities={'Na2O': 0.2},
        )

    assert source["Na"] == (
        "antoine_fallback_from_vaporock:backsolved_vaporock_curve_fit"
    )
    assert pressures != {}  # not a silent zero


def test_vapor_bridge_reraises_library_exception_as_labelled_runtime_error():
    backend = AlphaMELTSBackend()
    backend._vaporock_available = True

    class _Boom:
        def is_available(self):
            return True

        def equilibrate(self, **_kw):
            raise ValueError('upstream blew up')

    backend._vaporock_helper = _Boom()

    with pytest.raises(RuntimeError, match='VapoRock vapor bridge failed'):
        backend._vapor_pressures_via_vaporock_or_antoine(
            T_C=1600.0,
            solved_melt_wt_pct={'SiO2': 45.0},
            liquid_fraction=1.0,
            fO2_log=-8.0,
            pressure_bar=1e-6,
            activities={},
        )


def test_thermoengine_callsite_wires_vaporock_source_and_solved_liquid(monkeypatch):
    from engines.alphamelts.thermoengine import ThermoEnginePayload

    solved_liquid = {'SiO2': 44.0, 'FeO': 17.0, 'Na2O': 0.5}

    class FakeTransport:
        def equilibrate(self, *, temperature_C, pressure_bar, comp_wt, warnings):
            return ThermoEnginePayload(
                phases_present=('liquid',),
                phase_masses_kg={'liquid': 1.0},
                liquid_fraction=1.0,
                liquid_composition_wt_pct=dict(solved_liquid),
                activity_coefficients={'Na2O': 0.1},
                fe_redox_split={},
            )

    backend = AlphaMELTSBackend()
    backend._mode = 'thermoengine'
    backend._thermoengine_transport = FakeTransport()
    backend._vaporock_available = True
    helper = _vaporock_helper_returning({'Na': 9.9, 'SiO': 0.3})
    backend._vaporock_helper = helper

    eq = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 45.0, 'FeO': 18.0, 'MgO': 9.0,
                        'CaO': 11.0, 'Al2O3': 12.0, 'Na2O': 4.0, 'K2O': 1.0},
        fO2_log=-7.96,
        pressure_bar=1e-6,
    )

    assert eq.vapor_pressures_Pa == {'Na': 9.9, 'SiO': 0.3}
    assert set(eq.vapor_pressures_source.values()) == {'vaporock'}
    # The post-equilibrium SOLVED liquid (not the pre-equilibrium input)
    # is what reached the VapoRock helper.
    assert helper.calls[0]['composition_kg'] == solved_liquid


def test_dead_uppercase_vaporock_stub_is_gone_from_source():
    import simulator.melt_backend.alphamelts as mod

    src = Path(mod.__file__).read_text()
    # The never-worked stub: an uppercase-module import statement and the
    # nonexistent calc_vapor entry point + the old method name.
    assert 'import VapoRock\n' not in src
    assert 'VapoRock.calc_vapor' not in src
    assert 'calc_vapor' not in src
    assert '_get_vaporock_pressures' not in src
