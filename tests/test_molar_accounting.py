import importlib

import pytest

from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import StubBackend
from simulator.state import MOLAR_MASS, OXIDE_TO_METAL


def _required_attr(module_name, attr_name):
    module = importlib.import_module(module_name)
    assert hasattr(module, attr_name), (
        f"{module_name}.{attr_name} is required by the molar accounting "
        "contract"
    )
    return getattr(module, attr_name)


def _sim(feedstocks):
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


@pytest.mark.parametrize(
    ("species", "kg", "expected_atom_moles"),
    [
        ("SiO2", 60.08, {"Si": 1000.0, "O": 2000.0}),
        ("FeO", 71.84, {"Fe": 1000.0, "O": 1000.0}),
    ],
)
def test_species_formula_table_round_trips_kg_to_atoms_to_kg(
    species, kg, expected_atom_moles
):
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    lot = MaterialLot(
        account="process.cleaned_melt",
        species_kg={species: kg},
        source="formula roundtrip test",
    )

    assert lot.species_moles[species] == pytest.approx(1000.0)
    assert lot.atom_moles == pytest.approx(expected_atom_moles)
    assert lot.kg_total == pytest.approx(kg)


def test_atom_ledger_stores_moles_and_projects_kg():
    AtomLedger = _required_attr("simulator.accounting", "AtomLedger")

    ledger = AtomLedger()
    ledger.load_external_mol(
        "process.cleaned_melt",
        {"FeO": 1000.0},
        source="mol-native feed",
    )

    assert ledger.mol_by_account("process.cleaned_melt")["FeO"] == pytest.approx(1000.0)
    assert ledger.kg_by_account("process.cleaned_melt")["FeO"] == pytest.approx(71.84)


@pytest.mark.parametrize("oxide", ["FeO", "Fe2O3"])
def test_reducible_oxide_transitions_conserve_elements(oxide):
    AtomLedger = _required_attr("simulator.accounting", "AtomLedger")
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    LedgerTransition = _required_attr("simulator.accounting", "LedgerTransition")
    metal, metal_atoms, oxygen_atoms = OXIDE_TO_METAL[oxide]
    oxide_mol = 1000.0
    oxide_kg = oxide_mol * MOLAR_MASS[oxide] / 1000.0
    product_kg = {
        metal: oxide_mol * metal_atoms * MOLAR_MASS[metal] / 1000.0,
        "O2": oxide_mol * oxygen_atoms / 2.0 * MOLAR_MASS["O2"] / 1000.0,
    }

    ledger = AtomLedger()
    ledger.load_external(
        "process.cleaned_melt", {oxide: oxide_kg}, source="unit-test feed"
    )
    transition = LedgerTransition(
        name=f"reduce_{oxide}",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                {oxide: oxide_kg},
                source="unit-test reduction",
            ),
        ),
        credits=(
            MaterialLot(
                    "terminal.drain_tap_material",
                    {metal: product_kg[metal]},
                    source="unit-test reduction",
                ),
            MaterialLot(
                "terminal.oxygen_melt_offgas_stored",
                {"O2": product_kg["O2"]},
                source="unit-test reduction",
            ),
        ),
        reason=f"{oxide} reducible oxide transition",
    )
    ledger.transfer(
        transition.name,
        transition.debits,
        transition.credits,
        reason=transition.reason,
    )

    ledger.assert_balanced()
    assert ledger.kg_by_account("process.cleaned_melt").get(
        oxide, 0.0
    ) == pytest.approx(0.0)
    assert ledger.kg_by_account("terminal.drain_tap_material")[
        metal
    ] == pytest.approx(product_kg[metal])
    assert ledger.kg_by_account("terminal.oxygen_melt_offgas_stored")[
        "O2"
    ] == pytest.approx(product_kg["O2"])


def test_atom_tolerance_is_tighter_than_mass_tolerance():
    AtomLedger = _required_attr("simulator.accounting", "AtomLedger")
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    LedgerTransition = _required_attr("simulator.accounting", "LedgerTransition")
    UnbalancedTransitionError = _required_attr(
        "simulator.accounting", "UnbalancedTransitionError"
    )

    ledger = AtomLedger()
    ledger.load_external(
        "process.cleaned_melt", {"FeO": 71.84}, source="unit-test feed"
    )
    transition = LedgerTransition(
        name="bad_atom_drift_under_mass_tolerance",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                {"FeO": 71.84},
                source="unit-test reduction",
            ),
        ),
        credits=(
            MaterialLot(
                "terminal.drain_tap_material",
                {"Fe": 55.855},
                source="unit-test reduction",
            ),
            MaterialLot(
                "terminal.oxygen_melt_offgas_stored",
                {"O2": 16.0},
                source="unit-test reduction",
            ),
        ),
        reason="mass drift is below 20 g but atom drift is not",
    )

    with pytest.raises(UnbalancedTransitionError, match="atoms"):
        ledger.transfer(
            transition.name,
            transition.debits,
            transition.credits,
            reason=transition.reason,
        )


def test_load_batch_creates_atom_ledger_and_melt_projection():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide ledger contract",
                "composition_wt_pct": {"SiO2": 60.0, "FeO": 40.0},
            }
        }
    )

    sim.load_batch("oxide", mass_kg=1000.0)

    assert hasattr(sim, "atom_ledger"), (
        "PyrolysisSimulator.load_batch must create sim.atom_ledger"
    )
    ledger_melt_kg = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    assert ledger_melt_kg["SiO2"] == pytest.approx(600.0)
    assert ledger_melt_kg["FeO"] == pytest.approx(400.0)
    assert sim.melt.composition_kg["SiO2"] == pytest.approx(600.0)
    assert sim.melt.composition_kg["FeO"] == pytest.approx(400.0)

    snapshot = sim._make_snapshot()
    assert snapshot.melt_mass_kg == pytest.approx(1000.0)
    assert snapshot.composition_wt_pct["SiO2"] == pytest.approx(60.0)
    assert snapshot.composition_wt_pct["FeO"] == pytest.approx(40.0)


def test_snapshot_mass_balance_uses_explicit_flow_accounts():
    MaterialLot = _required_attr("simulator.accounting", "MaterialLot")
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide ledger contract",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)

    sim.atom_ledger.transfer(
        "move_to_unclassified_process_account",
        debits=(
            MaterialLot(
                "process.cleaned_melt",
                {"SiO2": 1.0},
                source="unit-test unclassified debit",
            ),
        ),
        credits=(
            MaterialLot(
                "process.unclassified_debug",
                {"SiO2": 1.0},
                source="unit-test unclassified credit",
            ),
        ),
        reason="balanced ledger move to an unreported flow account",
    )
    sim._project_cleaned_melt_from_atom_ledger()

    snapshot = sim._make_snapshot()

    assert sim._ledger_total_mass_kg() == pytest.approx(1000.0)
    assert snapshot.mass_out_kg == pytest.approx(999.0)
    assert snapshot.mass_balance_error_pct == pytest.approx(0.1)


def test_stage0_bonus_products_require_explicit_credit_or_source():
    sim = _sim(
        {
            "bad_bonus": {
                "label": "Bad hidden credit",
                "composition_wt_pct": {"SiO2": 100.0},
                "bonus_products": {"H2O_kg_per_tonne": 5.0},
            }
        }
    )

    with pytest.raises(ValueError, match="stage 0|Stage 0|source|credit"):
        sim.load_batch("bad_bonus", mass_kg=1000.0)
