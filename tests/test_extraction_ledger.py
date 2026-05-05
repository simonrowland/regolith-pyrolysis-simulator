import pytest

from simulator.core import PyrolysisSimulator
from simulator.electrolysis import ElectrolysisModel
from simulator.melt_backend.base import StubBackend
from simulator.state import (
    MOLAR_MASS,
    OXIDE_TO_METAL,
    CampaignPhase,
    MeltState,
)


def _sim(feedstocks):
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def test_mre_reduction_records_atom_ledger_transition():
    sim = _sim(
        {
            "feo": {
                "label": "FeO",
                "composition_wt_pct": {"FeO": 100.0},
            }
        }
    )
    sim.load_batch("feo", mass_kg=1000.0)

    feo_removed_kg = 1.0
    feo_removed_mol = feo_removed_kg / (MOLAR_MASS["FeO"] / 1000.0)
    fe_kg = feo_removed_kg * MOLAR_MASS["Fe"] / MOLAR_MASS["FeO"]
    o2_kg = feo_removed_kg * MOLAR_MASS["O"] / MOLAR_MASS["FeO"]

    class FixedElectrolysis:
        def step_hour(self, **_kwargs):
            return {
                "oxides_reduced_kg": {"FeO": feo_removed_kg},
                "oxides_reduced_mol": {"FeO": feo_removed_mol},
                "metals_produced_kg": {"Fe": fe_kg},
                "metals_produced_mol": {"Fe": feo_removed_mol},
                "O2_produced_kg": o2_kg,
                "O2_produced_mol": feo_removed_mol / 2.0,
                "energy_kWh": 1.25,
            }

    sim._electrolysis_model = FixedElectrolysis()
    sim.melt.campaign = CampaignPhase.C5
    sim._mre_voltage_sequence = [{"voltage": 0.6, "min_hold_hours": 1}]
    sim._mre_voltage_step_idx = 0
    sim._mre_hold_hours = 0
    sim._mre_effective_current_A = 100.0

    assert sim._step_mre() == pytest.approx(o2_kg)

    sim.atom_ledger.assert_balanced()
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")[
        "FeO"
    ] == pytest.approx(999.0)
    assert sim.atom_ledger.kg_by_account("process.condensation_train")[
        "Fe"
    ] == pytest.approx(fe_kg)
    assert sim.atom_ledger.kg_by_account("terminal.oxygen_mre_anode_stored")[
        "O2"
    ] == pytest.approx(o2_kg)
    assert sim.train.stages[1].collected_kg["Fe"] == pytest.approx(fe_kg)
    assert sim._oxygen_stored_kg() == pytest.approx(o2_kg)


def test_condensed_species_projection_does_not_double_count_across_stages():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"FeO": 100.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"Fe": 2.0},
        source="test condensed Fe",
    )
    sim.train.stages[2].collected_kg["Fe"] = 1.0

    sim._project_condensed_species(1, "Fe")

    assert sim.train.stages[1].collected_kg["Fe"] == pytest.approx(2.0)
    assert "Fe" not in sim.train.stages[2].collected_kg
    assert sim.train.total_by_species()["Fe"] == pytest.approx(2.0)


def test_electrolysis_accumulates_shared_metal_products():
    melt = MeltState()
    melt.composition_kg = {"FeO": 10.0, "Fe2O3": 10.0}
    melt.update_total_mass()

    result = ElectrolysisModel().step_hour(
        melt_state=melt,
        voltage_V=5.0,
        current_A=1.0e9,
        T_C=1600.0,
    )

    expected_fe_kg = 0.0
    for oxide in ("FeO", "Fe2O3"):
        metal, n_metal, _n_oxygen = OXIDE_TO_METAL[oxide]
        expected_fe_kg += (
            result["oxides_reduced_kg"][oxide]
            * n_metal
            * MOLAR_MASS[metal]
            / MOLAR_MASS[oxide]
        )

    assert result["metals_produced_kg"]["Fe"] == pytest.approx(expected_fe_kg)
    assert result["O2_produced_mol"] > 0.0


def test_ferric_oxide_reduces_after_wustite_in_mre_sequence():
    melt = MeltState()
    melt.composition_kg = {"FeO": 10.0, "Fe2O3": 10.0}
    melt.update_total_mass()

    sequence = ElectrolysisModel().get_reduction_sequence(melt, T_C=1600.0)
    order = [oxide for oxide, _voltage in sequence]

    assert order.index("FeO") < order.index("Fe2O3")


def test_k_shuttle_draws_from_process_reagent_inventory():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"FeO": 50.0, "SiO2": 50.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={"K": 9.0})
    sim._init_shuttle_inventory(CampaignPhase.C3_K)

    assert sim.atom_ledger.kg_by_account("reservoir.reagent.K").get(
        "K", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "K"
    ] == pytest.approx(9.0)

    sim._shuttle_inject_K()

    sim.atom_ledger.assert_balanced()
    assert sim._shuttle_injected_this_hr > 0.0
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "K"
    ] == pytest.approx(9.0 - sim._shuttle_injected_this_hr)
    assert sim.shuttle_K_inventory_kg == pytest.approx(
        sim.atom_ledger.kg_by_account("process.reagent_inventory")["K"]
    )
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")[
        "K2O"
    ] > 0.0
    assert sim.atom_ledger.kg_by_account("process.condensation_train")[
        "Fe"
    ] == pytest.approx(sim.train.stages[1].collected_kg["Fe"])


def test_recovered_condensate_transfers_once_to_reagent_inventory():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"SiO2": 100.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.train.stages[3].collected_kg["K"] = 2.0

    sim._init_shuttle_inventory(CampaignPhase.C3_K)

    assert sim.train.stages[3].collected_kg["K"] == pytest.approx(2.0)
    assert sim.shuttle_K_inventory_kg == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.condensation_train").get(
        "K", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory").get(
        "K", 0.0
    ) == pytest.approx(0.0)

    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"K": 2.0},
        source="test recovered K condensate",
    )
    assert sim._transfer_condensed_species("K") == pytest.approx(2.0)

    assert sim._transfer_condensed_species("K") == pytest.approx(0.0)
    assert sim.train.stages[3].collected_kg["K"] == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "K"
    ] == pytest.approx(2.0)


def test_mg_thermite_debits_process_reagent_inventory():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {"Al2O3": 80.0, "SiO2": 20.0},
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={"Mg": 12.0})
    sim._init_thermite_inventory()

    sim._step_thermite()

    sim.atom_ledger.assert_balanced()
    assert sim._thermite_Mg_consumed_this_hr > 0.0
    assert sim.atom_ledger.kg_by_account("reservoir.reagent.Mg").get(
        "Mg", 0.0
    ) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account("process.reagent_inventory")[
        "Mg"
    ] == pytest.approx(12.0 - sim._thermite_Mg_consumed_this_hr)
    assert sim.thermite_Mg_inventory_kg == pytest.approx(
        sim.atom_ledger.kg_by_account("process.reagent_inventory")["Mg"]
    )
    assert sim.atom_ledger.kg_by_account("process.cleaned_melt")["MgO"] > 0.0
    assert sim.train.stages[1].collected_kg["Al"] > 0.0
