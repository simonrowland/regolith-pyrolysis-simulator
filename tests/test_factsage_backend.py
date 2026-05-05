import io
import json
import sys
import types

import pytest

from simulator.accounting import AccountingError, AtomLedger
from simulator.melt_backend.base import EquilibriumResult, StubBackend
from simulator.melt_backend.factsage import FactSAGEBackend
from simulator.melt_backend.factsage_config import load_factsage_config
from simulator.melt_backend.factsage_doctor import run_doctor
from simulator.melt_backend.installer import EngineInstaller
from simulator.core import PyrolysisSimulator
from simulator.state import MOLAR_MASS
from web.events import _get_backend


class _State:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture
def fake_chemapp(monkeypatch):
    module = types.ModuleType('fake_chemapp')
    module.calls = []

    class ThermochemicalSystem:
        @classmethod
        def load(cls, file_path):
            module.calls.append(('load', file_path))

    class Units:
        @classmethod
        def set(cls, **kwargs):
            module.calls.append(('units.set', kwargs))

    class EquilibriumCalculation:
        @classmethod
        def set_eq_T(cls, value):
            module.calls.append(('set_eq_T', value))

        @classmethod
        def set_eq_P(cls, value):
            module.calls.append(('set_eq_P', value))

        @classmethod
        def set_eq_AC_pc(cls, phase, constituent, value):
            module.calls.append(('set_eq_AC_pc', phase, constituent, value))

        @classmethod
        def set_IA_cfs(cls, names, values):
            module.calls.append(('set_IA_cfs', list(names), list(values)))

        @classmethod
        def calculate_eq(cls, return_result=False):
            module.calls.append(('calculate_eq', return_result))
            return module.result

    module.ThermochemicalSystem = ThermochemicalSystem
    module.Units = Units
    module.EquilibriumCalculation = EquilibriumCalculation
    module.PressureUnit = types.SimpleNamespace(bar='bar')
    module.TemperatureUnit = types.SimpleNamespace(K='K')
    module.AmountUnit = types.SimpleNamespace(kg='kg', mol='mol')
    module.EnergyUnit = types.SimpleNamespace(J='J')
    sio2_mol = 2.0 / (MOLAR_MASS['SiO2'] / 1000.0)
    feo_mol = 1.0 / (MOLAR_MASS['FeO'] / 1000.0)
    na_mol = 0.01 / (MOLAR_MASS['Na'] / 1000.0)
    module.result = _State(
        phs={
            'LIQUID': _State(
                A=sio2_mol + feo_mol,
                pcs={
                    'SIO2_L': _State(A=sio2_mol, AC=0.42),
                    'FEO_L': _State(A=feo_mol, AC=0.21),
                },
            ),
            'GAS': _State(
                A=na_mol,
                pcs={
                    'NA_G': _State(A=na_mol, AC=1.2e-4),
                },
            ),
        },
        units={'P': 'bar', 'T': 'K', 'A': 'mol'},
    )

    monkeypatch.setitem(sys.modules, 'fake_chemapp', module)
    return module


@pytest.fixture
def factsage_config(tmp_path):
    datafile = tmp_path / 'mock-factsage.cst'
    datafile.write_text('fake data file marker\n')
    return {
        'chemapp_module': 'fake_chemapp',
        'datafile_path': str(datafile),
        'component_map': {
            'SiO2': 'SIO2_L',
            'FeO': 'FEO_L',
            'Fe2O3': 'FE2O3_L',
            'Na2O': 'NA2O_L',
        },
        'species_map': {
            'Na': 'NA_G',
            'K': 'K_G',
            'Fe': 'FE_G',
            'Mg': 'MG_G',
            'Ca': 'CA_G',
            'SiO': 'SIO_G',
        },
    }


def test_factsage_backend_imports_without_chemapp():
    assert FactSAGEBackend is not None


def test_factsage_initialize_returns_false_without_chemapp_or_datafile():
    backend = FactSAGEBackend()

    assert backend.initialize({}) is False
    assert backend.is_available() is False


def test_get_backend_factsage_falls_back_to_stub(monkeypatch):
    monkeypatch.delenv('FACTSAGE_CONFIG', raising=False)

    backend = _get_backend('factsage')

    assert isinstance(backend, StubBackend)


def test_load_factsage_config_from_env_file(monkeypatch, tmp_path):
    config_path = tmp_path / 'factsage.local.json'
    config_path.write_text(json.dumps({
        'chemapp_module': 'fake_chemapp',
        'datafile_path': 'local-data.cst',
        'component_map': {'SiO2': 'SIO2_L'},
    }))
    monkeypatch.setenv('FACTSAGE_CONFIG', str(config_path))

    config = load_factsage_config()

    assert config['chemapp_module'] == 'fake_chemapp'
    assert config['datafile_path'] == 'local-data.cst'
    assert config['component_map'] == {'SiO2': 'SIO2_L'}


def test_factsage_initialize_loads_configured_datafile(fake_chemapp,
                                                       factsage_config):
    backend = FactSAGEBackend()

    assert backend.initialize(factsage_config) is True
    assert backend.is_available() is True
    assert ('load', factsage_config['datafile_path']) in fake_chemapp.calls


def test_factsage_defaults_to_silicate_melt_only_capability(
    fake_chemapp, factsage_config
):
    backend = FactSAGEBackend()

    assert backend.initialize(factsage_config) is True

    assert backend.capabilities() == {
        'silicate_melt': True,
        'gas_volatiles': False,
        'salt_phase': False,
        'sulfide_matte': False,
        'metal_alloy': False,
    }
    assert backend.capability_summary() == 'silicate melt only'


def test_factsage_configured_capabilities_are_reported(
    fake_chemapp, factsage_config
):
    config = dict(factsage_config)
    config['capabilities'] = {
        'silicate_melt': True,
        'gas_volatiles': True,
        'metal_alloy': True,
    }
    backend = FactSAGEBackend()

    assert backend.initialize(config) is True

    assert backend.capabilities()['gas_volatiles'] is True
    assert backend.capabilities()['metal_alloy'] is True
    assert backend.capabilities()['salt_phase'] is False
    assert backend.capability_summary() == (
        'silicate melt, gas volatiles, metal alloy')


def test_factsage_unknown_capability_fails_before_chemapp_import():
    backend = FactSAGEBackend()

    assert backend.initialize({'capabilities': ['whole_regolith']}) is False

    assert backend.is_available() is False
    assert 'unknown backend capability: whole_regolith' in backend.last_error


def test_factsage_doctor_smoke_with_fake_chemapp(fake_chemapp, factsage_config,
                                                 tmp_path):
    config_path = tmp_path / 'factsage.local.json'
    config_path.write_text(json.dumps(factsage_config))
    stream = io.StringIO()

    code = run_doctor(str(config_path), stream=stream)

    assert code == 0
    output = stream.getvalue()
    assert '[ ok ] backend initialize' in output
    assert '[ ok ] smoke equilibrium' in output
    assert '[info] capabilities: silicate melt only' in output


def test_factsage_doctor_reports_configured_capabilities(
    fake_chemapp, factsage_config, tmp_path
):
    config = dict(factsage_config)
    config['capability_profile'] = ['silicate_melt', 'salt_phase']
    config_path = tmp_path / 'factsage.local.json'
    config_path.write_text(json.dumps(config))
    stream = io.StringIO()

    code = run_doctor(str(config_path), stream=stream)

    assert code == 0
    assert '[info] capabilities: silicate melt, salt phase' in (
        stream.getvalue())


def test_factsage_doctor_accepts_backend_config_aliases(
    monkeypatch, fake_chemapp, factsage_config, tmp_path
):
    monkeypatch.setitem(sys.modules, 'ChemApp', fake_chemapp)
    config = dict(factsage_config)
    config.pop('chemapp_module')
    config['data_file'] = config.pop('datafile_path')
    config_path = tmp_path / 'factsage.local.json'
    config_path.write_text(json.dumps(config))
    stream = io.StringIO()

    code = run_doctor(str(config_path), stream=stream)

    assert code == 0
    assert '[ ok ] ChemApp import: ChemApp' in stream.getvalue()


def test_installer_distinguishes_chemapp_import_from_usable_factsage(
    monkeypatch, fake_chemapp
):
    monkeypatch.setitem(sys.modules, 'ChemApp', fake_chemapp)
    monkeypatch.delenv('FACTSAGE_CONFIG', raising=False)

    status = EngineInstaller().check_status()

    assert status['ChemApp_module'] is True
    assert status['FactSAGE'] is False



def test_factsage_equilibrate_returns_equilibrium_result(fake_chemapp,
                                                         factsage_config):
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 2.0, 'FeO': 1.0},
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert isinstance(result, EquilibriumResult)
    assert result.temperature_C == 1600.0
    assert result.pressure_bar == 1e-6
    assert result.fO2_log == -9.0
    assert result.phases_present == ['LIQUID', 'GAS']
    assert result.phase_masses_kg['LIQUID'] == pytest.approx(3.0)
    assert result.phase_species_mol['LIQUID']['SiO2'] == pytest.approx(
        2.0 / (MOLAR_MASS['SiO2'] / 1000.0))
    assert result.liquid_fraction == pytest.approx(3.0 / 3.01)
    assert result.liquid_composition_wt_pct['SiO2'] == pytest.approx(
        2.0 / 3.0 * 100.0)
    assert result.vapor_pressures_Pa['Na'] == pytest.approx(12.0)


def test_runtime_backend_failure_sets_fallback_error():
    class FailingBackend:
        def __init__(self):
            self.calls = 0

        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            self.calls += 1
            raise RuntimeError('Na2O not mapped')

    backend = FailingBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 50.0, "Na2O": 5.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide")

    sim._get_equilibrium()
    sim._get_equilibrium()

    assert sim._last_backend_error == 'Na2O not mapped'
    assert backend.calls == 1


def test_programming_errors_from_backend_are_not_stubbed():
    class BuggyBackend:
        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            raise KeyError('bad adapter key')

    sim = PyrolysisSimulator(
        BuggyBackend(),
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 50.0, "Na2O": 5.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide")

    with pytest.raises(KeyError, match='bad adapter key'):
        sim._get_equilibrium()


def test_accounting_errors_from_backend_are_not_stubbed():
    class AccountingFailureBackend:
        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            raise AccountingError("species 'XYZ' not in catalog")

    sim = PyrolysisSimulator(
        AccountingFailureBackend(),
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 50.0, "Na2O": 5.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide")

    with pytest.raises(AccountingError, match='XYZ'):
        sim._get_equilibrium()
    assert sim._last_backend_error == ''


def test_factsage_adapter_programming_errors_are_not_rebranded(
    fake_chemapp, factsage_config
):
    def boom(return_result=False):
        raise AttributeError('fake adapter bug')

    fake_chemapp.EquilibriumCalculation.calculate_eq = staticmethod(boom)
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 50.0, "FeO": 5.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide")

    with pytest.raises(AttributeError, match='fake adapter bug'):
        sim._get_equilibrium()
    assert sim._last_backend_error == ''


def test_backend_failure_flag_resets_when_loading_next_batch():
    class FailsOnceBackend:
        def __init__(self):
            self.calls = 0
            self.fail = True

        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            self.calls += 1
            if self.fail:
                raise RuntimeError('temporary backend outage')
            return EquilibriumResult()

    backend = FailsOnceBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 50.0, "FeO": 5.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("oxide")
    sim._get_equilibrium()
    backend.fail = False
    sim.load_batch("oxide")

    sim._get_equilibrium()

    assert backend.calls == 2
    assert sim._last_backend_error == ''


def test_factsage_converts_units_and_uses_component_map(fake_chemapp,
                                                        factsage_config):
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 2.0, 'FeO': 1.0},
        fO2_log=-8.5,
        pressure_bar=2e-6,
    )

    assert ('set_eq_T', pytest.approx(1873.15)) in fake_chemapp.calls
    assert ('set_eq_P', pytest.approx(2e-6)) in fake_chemapp.calls
    assert ('set_eq_AC_pc', 'GAS', 'O2', pytest.approx(10.0 ** -8.5)) in (
        fake_chemapp.calls)
    ia_call = next(call for call in fake_chemapp.calls if call[0] == 'set_IA_cfs')
    incoming = dict(zip(ia_call[1], ia_call[2]))
    assert incoming['SIO2_L'] == pytest.approx(
        2.0 / (MOLAR_MASS['SiO2'] / 1000.0))
    assert incoming['FEO_L'] == pytest.approx(
        1.0 / (MOLAR_MASS['FeO'] / 1000.0))
    assert incoming['FE2O3_L'] == pytest.approx(0.0)
    assert incoming['NA2O_L'] == pytest.approx(0.0)
    incoming_idx = next(
        idx for idx, call in enumerate(fake_chemapp.calls)
        if call[0] == 'set_IA_cfs')
    fo2_idx = next(
        idx for idx, call in enumerate(fake_chemapp.calls)
        if call[0] == 'set_eq_AC_pc')
    run_idx = next(
        idx for idx, call in enumerate(fake_chemapp.calls)
        if call[0] == 'calculate_eq')
    assert incoming_idx < fo2_idx < run_idx


def test_factsage_accepts_mol_amount_unit(fake_chemapp, factsage_config):
    config = dict(factsage_config)
    config['amount_unit'] = 'mol'
    backend = FactSAGEBackend()

    assert backend.initialize(config) is True
    assert backend.is_available() is True


def test_factsage_fails_closed_without_fo2_control(fake_chemapp,
                                                   factsage_config):
    delattr(fake_chemapp.EquilibriumCalculation, 'set_eq_AC_pc')
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    with pytest.raises(RuntimeError, match='fO2|set_eq_AC_pc'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 2.0, 'FeO': 1.0},
            fO2_log=-8.5,
            pressure_bar=2e-6,
        )

    assert backend.is_available() is False


def test_factsage_can_explicitly_disable_fo2_control(fake_chemapp,
                                                     factsage_config):
    delattr(fake_chemapp.EquilibriumCalculation, 'set_eq_AC_pc')
    config = dict(factsage_config)
    config['control_fO2'] = False
    backend = FactSAGEBackend()
    assert backend.initialize(config) is True

    backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 2.0, 'FeO': 1.0},
        fO2_log=-8.5,
        pressure_bar=2e-6,
    )

    assert backend.is_available() is True


def test_factsage_accepts_ferric_oxide_component_map(fake_chemapp,
                                                     factsage_config):
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 2.0, 'FeO': 1.0, 'Fe2O3': 0.5},
        fO2_log=-8.5,
        pressure_bar=2e-6,
    )

    ia_call = next(call for call in fake_chemapp.calls if call[0] == 'set_IA_cfs')
    incoming = dict(zip(ia_call[1], ia_call[2]))
    assert incoming['SIO2_L'] == pytest.approx(
        2.0 / (MOLAR_MASS['SiO2'] / 1000.0))
    assert incoming['FEO_L'] == pytest.approx(
        1.0 / (MOLAR_MASS['FeO'] / 1000.0))
    assert incoming['FE2O3_L'] == pytest.approx(
        0.5 / (MOLAR_MASS['Fe2O3'] / 1000.0))


def test_factsage_missing_vapor_species_are_omitted_and_warned(
    fake_chemapp, factsage_config
):
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    result = backend.equilibrate(
        temperature_C=1600.0,
        composition_kg={'SiO2': 2.0, 'FeO': 1.0},
        fO2_log=-9.0,
        pressure_bar=1e-6,
    )

    assert result.vapor_pressures_Pa == {'Na': pytest.approx(12.0)}
    assert 'K' not in result.vapor_pressures_Pa
    assert any('K' in warning for warning in backend.warnings)


def test_factsage_component_map_mismatch_raises_and_marks_unavailable(
    fake_chemapp, factsage_config
):
    config = dict(factsage_config)
    config['component_map'] = {'SiO2': 'SIO2_L', 'Na2O': None}
    backend = FactSAGEBackend()
    assert backend.initialize(config) is True

    with pytest.raises(RuntimeError, match='Na2O'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 2.0, 'Na2O': 1.0},
            fO2_log=-9.0,
            pressure_bar=1e-6,
        )

    assert backend.is_available() is False
    assert any('Na2O' in warning for warning in backend.warnings)


def test_simulator_uses_stub_equilibrium_after_backend_failure(
    fake_chemapp, factsage_config
):
    config = dict(factsage_config)
    config['component_map'] = {'SiO2': 'SIO2_L', 'Na2O': None}
    backend = FactSAGEBackend()
    assert backend.initialize(config) is True

    sim = PyrolysisSimulator(
        backend,
        {'campaigns': {}},
        {},
        {
            'metals': {
                'Na': {
                    'parent_oxide': 'Na2O',
                    'molar_mass_g_mol': 22.99,
                    'antoine': {'A': 10.866, 'B': 5688, 'C': 0},
                },
            },
            'oxide_vapors': {},
        },
    )
    sim.melt.temperature_C = 1600.0
    sim.atom_ledger = AtomLedger(registry=sim.species_formula_registry)
    sim.atom_ledger.load_external(
        'process.cleaned_melt',
        {'SiO2': 2.0, 'Na2O': 1.0},
        source='test backend failure composition',
    )
    sim._project_cleaned_melt_from_atom_ledger()

    result = sim._get_equilibrium()

    assert result.vapor_pressures_Pa['Na'] > 0.0
    assert backend.is_available() is False
    assert 'Na2O' in sim._last_backend_error
