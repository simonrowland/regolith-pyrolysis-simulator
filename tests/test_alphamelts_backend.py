import time
import types
from pathlib import Path

import pytest
import yaml

from simulator.core import CampaignPhase, PyrolysisSimulator
from simulator.melt_backend.alphamelts import AlphaMELTSBackend


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
    assert result.activity_coefficients == {'Na': 2.0}
    assert result.ledger_transition is None


def test_activities_times_antoine_computes_gamma_x_ppure_from_yaml():
    backend = AlphaMELTSBackend()

    pressures = backend._activities_times_antoine(
        1600.0,
        {'Na': 2.0, 'K': 1.0, 'unknown': 10.0},
        {'Na2O': 5.0, 'K2O': 1.0, 'SiO2': 94.0},
    )

    assert set(pressures) == {'Na', 'K'}
    assert pressures['Na'] > 0.0
    assert pressures['K'] > 0.0


def test_activities_times_antoine_returns_empty_without_parent_oxide_activity():
    backend = AlphaMELTSBackend()

    assert backend._activities_times_antoine(
        1600.0,
        {'Na': 2.0},
        {'SiO2': 100.0},
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
    assert result.phase_masses_kg == {}
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
    if not backend.initialize({}):
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
    if not backend.initialize({}):
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
    # An AlphaMELTSBackend with no python_api or subprocess mode reaches
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
