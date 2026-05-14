import time
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

    with pytest.raises(RuntimeError, match='AlphaMELTS Python equilibrium failed'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_kg={'SiO2': 1.0},
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

    with pytest.raises(ValueError, match='process.cleaned_melt'):
        backend.equilibrate(
            temperature_C=1600.0,
            composition_mol_by_account={
                'process.cleaned_melt': {'SiO2': 1.0},
                'process.metal_phase': {'Fe': 0.25},
                'process.overhead_gas': {'O2': 0.5},
            },
            fO2_log=-9.0,
            pressure_bar=1e-6,
        )


def test_alphamelts_parser_keeps_valid_melts_mineral_phases(tmp_path):
    backend = AlphaMELTSBackend()
    table = tmp_path / 'phase_tbl.txt'
    table.write_text(
        'Temperature 1600\n'
        'Phase T_C P_bar Mass_g Moles\n'
        'liquid 900.0\n'
        'plagioclase 1600 1e-6 50.0 0.2\n'
        'ilmenite 1600 1e-6 10.0 0.05\n'
    )

    result = backend._parse_melts_output(
        str(tmp_path), T_C=1600.0, P_bar=1e-6, fO2_log=-9.0)

    assert result.phase_masses_kg['plagioclase'] == pytest.approx(0.05)
    assert result.phase_masses_kg['ilmenite'] == pytest.approx(0.01)


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
