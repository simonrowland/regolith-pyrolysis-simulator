import io
import json
import sys
import types

import pytest

from simulator.accounting import AccountingError, AtomLedger, LedgerTransition
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
        'control_fO2': False,
    }


def test_factsage_backend_imports_without_chemapp():
    assert FactSAGEBackend is not None


def test_factsage_initialize_returns_false_without_chemapp_or_datafile():
    backend = FactSAGEBackend()

    assert backend.initialize({}) is False
    assert backend.is_available() is False


def test_configured_unavailable_factsage_backend_fail_closes():
    backend = FactSAGEBackend()
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

    with pytest.raises(RuntimeError, match='FactSAGEBackend is unavailable'):
        sim._get_equilibrium()


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
    assert result.ledger_transition is not None


def test_factsage_phase_species_transition_conserves_noop_melt():
    backend = FactSAGEBackend()
    result = EquilibriumResult(
        phase_species_mol={'LIQUID': {'SiO2': 1.0, 'FeO': 2.0}},
        phase_species_kg={'LIQUID': {
            'SiO2': MOLAR_MASS['SiO2'] / 1000.0,
            'FeO': 2.0 * MOLAR_MASS['FeO'] / 1000.0,
        }},
    )

    transition = backend._ledger_transition_from_result(
        {'SiO2': 1.0, 'FeO': 2.0}, result)

    assert transition is not None
    transition.validate_conservation()


def test_factsage_unclassified_phase_fails_closed():
    backend = FactSAGEBackend()
    result = EquilibriumResult(
        phase_species_mol={
            'SPINEL': {'FeO': 1.0},
            'UNKNOWN_SOLID': {'SiO2': 2.0},
        },
    )

    with pytest.raises(ValueError, match='phase_map'):
        backend._ledger_transition_from_result(
            {'FeO': 1.0, 'SiO2': 2.0}, result)


def test_factsage_explicit_solid_phase_stays_in_cleaned_melt():
    backend = FactSAGEBackend()
    backend._build_phase_roles({'SPINEL': 'solid', 'UNKNOWN_SOLID': 'solid'})
    result = EquilibriumResult(
        phase_species_mol={
            'SPINEL': {'FeO': 1.0},
            'UNKNOWN_SOLID': {'SiO2': 2.0},
        },
    )

    transition = backend._ledger_transition_from_result(
        {'FeO': 1.0, 'SiO2': 2.0}, result)

    assert transition is not None
    transition.validate_conservation()
    assert {
        lot.account for lot in transition.credits
    } == {'process.cleaned_melt'}


def test_factsage_invalid_phase_role_fails_initialization():
    backend = FactSAGEBackend()

    with pytest.raises(ValueError, match='phase role'):
        backend._build_phase_roles({'unknown_role': ['SPINEL']})


def test_factsage_gas_and_metal_phase_accounts_are_process_side():
    backend = FactSAGEBackend()

    assert backend._ledger_account_for_phase_species(
        'GAS', 'O2') == 'process.overhead_gas'
    assert backend._ledger_account_for_phase_species(
        'GAS', 'Na') == 'process.overhead_gas'
    assert backend._ledger_account_for_phase_species(
        'METAL', 'Fe') == 'process.metal_phase'


def test_factsage_non_o2_gas_phase_credits_overhead_gas():
    backend = FactSAGEBackend()
    result = EquilibriumResult(
        phase_species_mol={'GAS': {'Na': 1.0}},
    )

    transition = backend._ledger_transition_from_result({'Na': 1.0}, result)

    assert transition is not None
    transition.validate_conservation()
    assert {lot.account for lot in transition.credits} == {
        'process.overhead_gas'
    }
    assert 'process.condensation_train' not in {
        lot.account for lot in transition.credits
    }


def test_factsage_transition_debits_account_scoped_inputs_when_provided():
    backend = FactSAGEBackend()
    result = EquilibriumResult(
        phase_species_mol={
            'LIQUID': {'SiO2': 1.0},
            'METAL': {'Fe': 1.0},
            'GAS': {'O2': 0.5},
        },
    )

    transition = backend._ledger_transition_from_result(
        {'SiO2': 100.0},
        result,
        input_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0},
            'process.metal_phase': {'Fe': 1.0},
            'process.overhead_gas': {'O2': 0.5},
        },
    )

    assert transition is not None
    transition.validate_conservation()
    assert {lot.account for lot in transition.debits} == {
        'process.cleaned_melt',
        'process.metal_phase',
        'process.overhead_gas',
    }
    cleaned_melt_debit = next(
        lot for lot in transition.debits
        if lot.account == 'process.cleaned_melt')
    assert cleaned_melt_debit.meta['species_mol'] == {'SiO2': 1.0}


def test_runtime_backend_failure_fail_closes():
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

    with pytest.raises(RuntimeError, match='Na2O not mapped'):
        sim._get_equilibrium()
    with pytest.raises(RuntimeError, match='Na2O not mapped'):
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
    with pytest.raises(RuntimeError, match='temporary backend outage'):
        sim._get_equilibrium()
    backend.fail = False
    sim.load_batch("oxide")

    sim._get_equilibrium()

    assert backend.calls == 2
    assert sim._last_backend_error == ''


def test_factsage_converts_units_and_uses_component_map(fake_chemapp,
                                                        factsage_config):
    factsage_config = dict(factsage_config)
    factsage_config['control_fO2'] = False
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
    assert not any(call[0] == 'set_eq_AC_pc' for call in fake_chemapp.calls)
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
    run_idx = next(
        idx for idx, call in enumerate(fake_chemapp.calls)
        if call[0] == 'calculate_eq')
    assert incoming_idx < run_idx


def test_factsage_rejects_controlled_fo2_without_ledger_reservoir(
    fake_chemapp, factsage_config
):
    config = dict(factsage_config)
    config['control_fO2'] = True
    backend = FactSAGEBackend()
    assert backend.initialize(config) is True

    with pytest.raises(RuntimeError, match='control_fO2'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 2.0, 'FeO': 1.0},
            fO2_log=-8.5,
            pressure_bar=2e-6,
        )


def test_factsage_requires_explicit_fo2_control_choice(
    fake_chemapp, factsage_config
):
    config = dict(factsage_config)
    config.pop('control_fO2')
    backend = FactSAGEBackend()
    assert backend.initialize(config) is True

    with pytest.raises(RuntimeError, match='explicitly declare control_fO2'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 2.0, 'FeO': 1.0},
            fO2_log=-8.5,
            pressure_bar=2e-6,
        )


def test_factsage_fo2_buffer_debits_finite_credit_line():
    backend = FactSAGEBackend()
    backend._config = {
        'control_fO2': True,
        'fo2_buffer_credit_limit_mol': 1.0,
    }
    result = EquilibriumResult(
        phase_species_mol={'LIQUID': {'Fe2O3': 1.0}},
    )
    transition = backend._ledger_transition_from_result(
        {'FeO': 2.0}, result)
    assert transition is not None
    transition.validate_conservation()

    ledger = AtomLedger(account_policies=backend.ledger_account_policies())
    ledger.load_external_mol('process.cleaned_melt', {'FeO': 2.0})
    ledger.apply(transition)

    assert ledger.kg_by_account('reservoir.fo2_buffer')['O2'] == pytest.approx(
        -0.5 * MOLAR_MASS['O2'] / 1000.0)


def test_factsage_fo2_buffer_credit_limit_is_enforced():
    backend = FactSAGEBackend()
    backend._config = {
        'control_fO2': True,
        'fo2_buffer_credit_limit_mol': 0.25,
    }
    result = EquilibriumResult(
        phase_species_mol={'LIQUID': {'Fe2O3': 1.0}},
    )
    transition = backend._ledger_transition_from_result(
        {'FeO': 2.0}, result)

    ledger = AtomLedger(account_policies=backend.ledger_account_policies())
    ledger.load_external_mol('process.cleaned_melt', {'FeO': 2.0})
    with pytest.raises(AccountingError, match='exceeded .* credit'):
        ledger.apply(transition)


def test_factsage_fo2_buffer_credits_released_oxygen():
    backend = FactSAGEBackend()
    backend._config = {
        'control_fO2': True,
        'fo2_buffer_credit_limit_mol': 1.0,
    }
    result = EquilibriumResult(
        phase_species_mol={'LIQUID': {'FeO': 2.0}},
    )
    transition = backend._ledger_transition_from_result(
        {'Fe2O3': 1.0}, result)
    assert transition is not None
    transition.validate_conservation()

    ledger = AtomLedger(account_policies=backend.ledger_account_policies())
    ledger.load_external_mol('process.cleaned_melt', {'Fe2O3': 1.0})
    ledger.apply(transition)

    assert ledger.kg_by_account('reservoir.fo2_buffer')['O2'] == pytest.approx(
        0.5 * MOLAR_MASS['O2'] / 1000.0)


def test_factsage_rejects_open_oxygen_without_ledger_reservoir(
    fake_chemapp, factsage_config
):
    config = dict(factsage_config)
    config['open_oxygen_mol'] = 1.0
    backend = FactSAGEBackend()
    assert backend.initialize(config) is True

    with pytest.raises(RuntimeError, match='open_oxygen_mol'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 2.0, 'FeO': 1.0},
            fO2_log=-8.5,
            pressure_bar=2e-6,
        )


def test_factsage_default_vapor_species_cover_engine_metals():
    backend = FactSAGEBackend()
    assert {'Al', 'Ti', 'Cr', 'Mn', 'Si'} <= set(backend.get_vapor_species())


def test_backend_phase_species_without_ledger_transition_fails_closed():
    class PhaseSpeciesBackend:
        def __init__(self):
            self.calls = 0

        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            self.calls += 1
            return EquilibriumResult(
                phase_species_mol={'LIQUID': {'FeO': 1.0}},
                phase_species_kg={'LIQUID': {'FeO': MOLAR_MASS['FeO'] / 1000.0}},
            )

    backend = PhaseSpeciesBackend()
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

    with pytest.raises(RuntimeError, match='AtomLedger transition'):
        sim._get_equilibrium()

    assert backend.calls == 1
    assert sim._backend_failed is True
    assert 'AtomLedger transition' in sim._last_backend_error


def test_backend_overhead_o2_stays_in_process_headspace_until_gas_tick():
    class OxygenBackend:
        def __init__(self):
            self.transition = None

        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            return EquilibriumResult(ledger_transition=self.transition)

    backend = OxygenBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("feo", mass_kg=1.0)
    backend.transition = LedgerTransition(
        name='factsage_equilibrium_phase_update',
        debits=(sim.atom_ledger.debit_mol(
            'process.cleaned_melt', {'FeO': 1.0}),),
        credits=(
            sim.atom_ledger.credit_mol('process.cleaned_melt', {'Fe': 1.0}),
            sim.atom_ledger.credit_mol('process.overhead_gas', {'O2': 0.5}),
        ),
    )

    sim._get_equilibrium()

    expected_o2_kg = 0.5 * MOLAR_MASS['O2'] / 1000.0
    assert sim.atom_ledger.kg_by_account(
        'process.overhead_gas')['O2'] == pytest.approx(expected_o2_kg)
    assert sim.atom_ledger.kg_by_account(
        'terminal.oxygen_melt_offgas_stored').get(
            'O2', 0.0) == pytest.approx(0.0)
    # The turbine-feed authority is the ledger holdup, which sees exactly
    # the backend O2 credit and nothing else (no separate per-tick tally).
    assert sim._ledger_o2_kg(
        'process.overhead_gas') == pytest.approx(expected_o2_kg)


def test_backend_transition_name_must_match_declared_contract():
    class UnexpectedNameBackend:
        def __init__(self):
            self.transition = None

        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            return EquilibriumResult(ledger_transition=self.transition)

    backend = UnexpectedNameBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("feo", mass_kg=1.0)
    backend.transition = LedgerTransition(
        name='unexpected_backend_move',
        debits=(sim.atom_ledger.debit_mol(
            'process.cleaned_melt', {'FeO': 1.0}),),
        credits=(
            sim.atom_ledger.credit_mol('process.cleaned_melt', {'Fe': 1.0}),
            sim.atom_ledger.credit_mol('process.overhead_gas', {'O2': 0.5}),
        ),
    )

    with pytest.raises(AccountingError, match='transition name'):
        sim._get_equilibrium()


def test_backend_input_includes_active_process_accounts():
    class RecordingBackend:
        def __init__(self):
            self.kwargs = None

        def is_available(self):
            return True

        def equilibrate(
            self, *, composition_mol_by_account=None, **kwargs
        ):
            self.kwargs = kwargs
            self.kwargs['composition_mol_by_account'] = (
                composition_mol_by_account)
            return EquilibriumResult()

    backend = RecordingBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("feo", mass_kg=1.0)
    sim.atom_ledger.load_external_mol('process.metal_phase', {'Fe': 0.25})
    sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': 0.125})

    sim._get_equilibrium()

    assert backend.kwargs['composition_mol_by_account'][
        'process.cleaned_melt']['FeO'] > 0.0
    assert backend.kwargs['composition_mol_by_account'][
        'process.metal_phase']['Fe'] == pytest.approx(0.25)
    assert backend.kwargs['composition_mol_by_account'][
        'process.overhead_gas']['O2'] == pytest.approx(0.125)
    assert backend.kwargs['composition_mol']['Fe'] == pytest.approx(0.25)
    assert backend.kwargs['composition_mol']['O2'] == pytest.approx(0.125)


def test_kwargs_only_backend_rejects_active_metal_and_gas_inputs():
    class KwargsOnlyBackend:
        def __init__(self):
            self.calls = 0

        def is_available(self):
            return True

        def equilibrate(self, **kwargs):
            self.calls += 1
            return EquilibriumResult()

    backend = KwargsOnlyBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("feo", mass_kg=1.0)
    sim.atom_ledger.load_external_mol('process.metal_phase', {'Fe': 0.25})
    sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': 0.125})

    with pytest.raises(AccountingError, match='composition_mol_by_account'):
        sim._get_equilibrium()
    assert backend.calls == 0


def test_account_unaware_backend_rejects_active_metal_and_gas_inputs():
    class AccountUnawareBackend:
        def __init__(self):
            self.calls = 0

        def is_available(self):
            return True

        def equilibrate(
            self, temperature_C, composition_mol, fO2_log=-9.0,
            pressure_bar=1e-6, species_formula_registry=None
        ):
            self.calls += 1
            return EquilibriumResult()

    backend = AccountUnawareBackend()
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        },
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("feo", mass_kg=1.0)
    sim.atom_ledger.load_external_mol('process.metal_phase', {'Fe': 0.25})
    sim.atom_ledger.load_external_mol('process.overhead_gas', {'O2': 0.125})

    with pytest.raises(AccountingError, match='composition_mol_by_account'):
        sim._get_equilibrium()
    assert backend.calls == 0


def test_factsage_accepts_mol_amount_unit(fake_chemapp, factsage_config):
    config = dict(factsage_config)
    config['amount_unit'] = 'mol'
    backend = FactSAGEBackend()

    assert backend.initialize(config) is True
    assert backend.is_available() is True


def test_factsage_control_fo2_fails_closed_before_chemapp_call(
    fake_chemapp, factsage_config
):
    delattr(fake_chemapp.EquilibriumCalculation, 'set_eq_AC_pc')
    factsage_config = dict(factsage_config)
    factsage_config['control_fO2'] = True
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    with pytest.raises(RuntimeError, match='control_fO2'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 2.0, 'FeO': 1.0},
            fO2_log=-8.5,
            pressure_bar=2e-6,
        )

    assert backend.is_available() is False


def test_factsage_control_fo2_requires_gas_oxygen_phase_before_ac_call(
    fake_chemapp, factsage_config
):
    config = dict(factsage_config)
    config.update({
        'control_fO2': True,
        'fo2_buffer_credit_limit_mol': 1.0,
        'oxygen_phase': 'LIQUID',
    })
    backend = FactSAGEBackend()
    assert backend.initialize(config) is True

    with pytest.raises(RuntimeError, match='oxygen_phase.*phase role gas'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 2.0, 'FeO': 1.0},
            fO2_log=-8.5,
            pressure_bar=2e-6,
        )

    assert not any(call[0] == 'set_eq_AC_pc' for call in fake_chemapp.calls)
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


def test_factsage_account_scoped_inputs_use_component_and_species_maps(
    fake_chemapp, factsage_config
):
    factsage_config['species_map']['Fe'] = 'FE_METAL'
    factsage_config['species_map']['O2'] = 'O2_G'
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    backend.equilibrate(
        temperature_C=1600.0,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 2.0},
            'process.metal_phase': {'Fe': 1.25},
            'process.overhead_gas': {'O2': 0.5},
        },
        fO2_log=-8.5,
        pressure_bar=2e-6,
    )

    ia_call = next(call for call in fake_chemapp.calls if call[0] == 'set_IA_cfs')
    incoming = dict(zip(ia_call[1], ia_call[2]))
    assert incoming['SIO2_L'] == pytest.approx(2.0)
    assert incoming['FE_METAL'] == pytest.approx(1.25)
    assert incoming['O2_G'] == pytest.approx(0.5)


def test_factsage_account_species_inputs_are_zeroed_between_calls(
    fake_chemapp, factsage_config
):
    factsage_config['species_map']['Fe'] = 'FE_METAL'
    factsage_config['species_map']['O2'] = 'O2_G'
    backend = FactSAGEBackend()
    assert backend.initialize(factsage_config) is True

    backend.equilibrate(
        temperature_C=1600.0,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 2.0},
            'process.metal_phase': {'Fe': 1.25},
            'process.overhead_gas': {'O2': 0.5},
        },
        fO2_log=-8.5,
        pressure_bar=2e-6,
    )
    fake_chemapp.calls.clear()

    backend.equilibrate(
        temperature_C=1600.0,
        composition_mol_by_account={
            'process.cleaned_melt': {'SiO2': 1.0},
        },
        fO2_log=-8.5,
        pressure_bar=2e-6,
    )

    ia_call = next(call for call in fake_chemapp.calls if call[0] == 'set_IA_cfs')
    incoming = dict(zip(ia_call[1], ia_call[2]))
    assert incoming['SIO2_L'] == pytest.approx(1.0)
    assert incoming['FE_METAL'] == pytest.approx(0.0)
    assert incoming['O2_G'] == pytest.approx(0.0)


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


def test_simulator_fail_closes_after_configured_backend_failure(
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

    with pytest.raises(RuntimeError, match='Na2O'):
        sim._get_equilibrium()

    assert backend.is_available() is False
    assert 'Na2O' in sim._last_backend_error
