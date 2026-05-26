import math
import time
import types
from pathlib import Path

import pytest
import yaml

from simulator.core import CampaignPhase, PyrolysisSimulator
from simulator.melt_backend.alphamelts import (
    AlphaMELTSBackend,
    activity_from_chem_potential,
)
from simulator.melt_backend.base import EquilibriumResult
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


def test_alphamelts_subprocess_liquidus_finder_is_unavailable():
    backend = AlphaMELTSBackend()
    backend._mode = 'subprocess'

    result = backend.find_liquidus_solidus(
        composition_kg={'SiO2': 50.0, 'Al2O3': 15.0, 'MgO': 15.0, 'CaO': 20.0},
        fO2_log=-9.0,
        pressure_bar=1.0,
    )

    assert result.status == 'unavailable'
    assert any('python_api mode' in warning for warning in result.warnings)


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


def test_alphamelts_initialize_prefers_thermoengine_when_available(monkeypatch):
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

    assert backend.initialize({}) is True
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
        table['Na']['antoine']['A']
        - table['Na']['antoine']['B'] / (T_K + table['Na']['antoine']['C'])
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


def test_alphamelts_stdout_parser_fails_without_stable_assemblage():
    backend = AlphaMELTSBackend()

    with pytest.raises(RuntimeError, match="stable assemblage"):
        backend._parse_single_point_stdout(
            "Error in SILMIN file input procedure.",
            temperature_C=1200.0,
            pressure_bar=1.0,
            fO2_log=-9.0,
            total_input_kg=1000.0,
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
