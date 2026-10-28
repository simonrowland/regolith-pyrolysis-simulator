"""Pin melt-resident account aggregation before consolidating its readers."""

from types import SimpleNamespace

import pytest

from simulator.accounting.ledger import AtomLedger
from simulator.core import PyrolysisSimulator
from simulator.state import MeltState


def test_melt_resident_projection_includes_spent_oxide_and_retains_nonoxide():
    sim = PyrolysisSimulator.__new__(PyrolysisSimulator)
    sim.atom_ledger = AtomLedger()
    sim.atom_ledger.load_external(
        'process.cleaned_melt', {'FeO': 3.0, 'Fe': 0.2}, source='fixture',
        material_origin='feedstock')
    sim.atom_ledger.load_external(
        'process.spent_reductant_residue', {'Na2O': 1.0}, source='fixture',
        material_origin='reagent')
    sim.inventory = SimpleNamespace(melt_oxide_kg={})
    sim.melt = MeltState()

    sim._project_cleaned_melt_from_atom_ledger()

    assert sim.melt.composition_kg == pytest.approx(
        {'FeO': 3.0, 'Fe': 0.2, 'Na2O': 1.0}, abs=1e-15)
    assert sim.melt.total_mass_kg == pytest.approx(4.2, abs=1e-15)
    assert sim._melt_oxide_wt_pct() == pytest.approx(
        {'FeO': 75.0, 'Na2O': 25.0}, abs=1e-14)
