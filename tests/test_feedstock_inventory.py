from pathlib import Path

import pytest
import yaml

from simulator.core import PyrolysisSimulator
from simulator.accounting import AccountingError
from simulator.melt_backend.base import StubBackend
from simulator.state import CampaignPhase


def _sim(feedstocks):
    backend = StubBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def test_builtin_feedstocks_initially_conserve_batch_mass():
    data_path = Path(__file__).parent.parent / "data" / "feedstocks.yaml"
    feedstocks = yaml.safe_load(data_path.read_text())

    for key in feedstocks:
        sim = _sim(feedstocks)
        required_c = 0.0
        if PyrolysisSimulator._uses_mars_carbon_cleanup(feedstocks[key]):
            required_c = PyrolysisSimulator._carbon_reductant_required_kg(
                feedstocks[key], 1000.0)
        additives = {"C": required_c} if required_c > 0.0 else None
        sim.load_batch(key, mass_kg=1000.0, additives_kg=additives)
        snapshot = sim._make_snapshot()

        assert snapshot.mass_balance_error_pct == pytest.approx(0.0), key


def test_load_batch_preserves_non_melt_feedstock_inventory():
    sim = _sim(
        {
            "mixed": {
                "label": "Mixed raw regolith",
                "composition_wt_pct": {
                    "SiO2": 50.0,
                    "FeO": 10.0,
                    "Fe2O3": 2.0,
                    "H2O": 5.0,
                    "C": 2.0,
                    "S": 3.0,
                    "SO3": 4.0,
                    "Cl": 1.0,
                    "ClO4": 0.5,
                    "Fe": 6.0,
                    "Ni": 1.0,
                    "NiO": 1.2,
                    "ZrO2": 0.3,
                    "REE_oxides": 0.2,
                },
                "non_oxide_components": {
                    "S_wt_pct": [1.0, 3.0],
                },
                "bulk_additions": {
                    "metallic_FeNi_wt_pct": [10.0, 20.0],
                    "FeS_troilite_wt_pct": [5.0, 6.0],
                    "C_wt_pct": [0.1, 0.5],
                },
            }
        }
    )

    sim.load_batch("mixed", mass_kg=1000.0)

    assert sim.melt.composition_kg["SiO2"] == pytest.approx(458.715596)
    assert sim.melt.composition_kg["FeO"] == pytest.approx(91.743119)
    assert sim.melt.composition_kg["Fe2O3"] == pytest.approx(18.348624)
    assert sim.melt.composition_kg["NiO"] == pytest.approx(11.009174)
    assert "H2O" not in sim.melt.composition_kg
    assert "Fe" not in sim.melt.composition_kg
    assert "SO3" not in sim.melt.composition_kg

    inv = sim.inventory
    assert inv.stage0_profile == "bulk_preservation"
    assert inv.raw_components_kg["H2O"] == pytest.approx(45.871560)
    assert inv.raw_components_kg["C"] == pytest.approx(21.100917)
    assert inv.raw_components_kg["S"] == pytest.approx(45.871560)
    assert inv.raw_components_kg["metallic_FeNi"] == pytest.approx(137.614679)
    assert inv.raw_components_kg["FeS_troilite"] == pytest.approx(50.458716)

    assert "SiO2" not in inv.residual_components_kg
    assert "Fe2O3" not in inv.residual_components_kg
    assert "H2O" not in inv.residual_components_kg
    assert "NiO" not in inv.residual_components_kg
    assert "ZrO2" not in inv.residual_components_kg
    assert "REE_oxides" not in inv.residual_components_kg
    assert inv.terminal_slag_components_kg["ZrO2"] == pytest.approx(2.752294)
    assert inv.terminal_slag_components_kg["REE_oxides"] == pytest.approx(1.834862)

    assert inv.stage0_products_kg["H2O"] == pytest.approx(45.871560)
    assert "Fe" not in inv.stage0_products_kg
    assert "Ni" not in inv.stage0_products_kg
    assert "metallic_FeNi" not in inv.stage0_products_kg
    assert "ZrO2" not in inv.stage0_products_kg
    assert "REE_oxides" not in inv.stage0_products_kg
    assert inv.gas_volatiles_kg["C"] == pytest.approx(21.100917)
    assert inv.salt_phase_kg["SO3"] == pytest.approx(36.697248)
    assert inv.salt_phase_kg["Cl"] == pytest.approx(9.174312)
    assert inv.salt_phase_kg["ClO4"] == pytest.approx(4.587156)
    assert inv.sulfide_matte_kg["S"] == pytest.approx(45.871560)
    assert inv.sulfide_matte_kg["FeS_troilite"] == pytest.approx(50.458716)
    assert inv.metal_alloy_kg["Fe"] == pytest.approx(55.045872)
    assert inv.metal_alloy_kg["Ni"] == pytest.approx(9.174312)
    assert inv.metal_alloy_kg["metallic_FeNi"] == pytest.approx(137.614679)
    assert inv.drain_tap_kg["metallic_FeNi"] == pytest.approx(137.614679)

    ledger = sim.product_ledger()
    assert ledger["Fe"] == pytest.approx(55.045872)
    assert ledger["Ni"] == pytest.approx(9.174312)
    assert ledger["metallic_FeNi"] == pytest.approx(137.614679)
    assert ledger["H2O"] == pytest.approx(45.871560)
    assert "ZrO2" not in ledger

    sim.melt.campaign = CampaignPhase.COMPLETE
    sim._finalize_record()
    assert sim.record.completed is True
    assert sim.record.products_kg["Fe"] == pytest.approx(55.045872)
    assert sim.record.products_kg["metallic_FeNi"] == pytest.approx(137.614679)


def test_inventory_is_visible_on_record_and_snapshot():
    sim = _sim(
        {
            "volatile": {
                "label": "Volatile-bearing",
                "composition_wt_pct": {
                    "SiO2": 40.0,
                    "MgO": 20.0,
                    "H2O": 10.0,
                },
            }
        }
    )

    sim.load_batch("volatile", mass_kg=1000.0)
    snapshot = sim._make_snapshot()

    assert "H2O" not in sim.record.initial_inventory.residual_components_kg
    assert "H2O" not in snapshot.inventory.residual_components_kg
    assert snapshot.inventory.melt_oxide_kg["SiO2"] == pytest.approx(571.428571)
    assert snapshot.inventory.stage0_products_kg["H2O"] == pytest.approx(142.857143)


def test_oxide_only_melt_behavior_is_unchanged():
    sim = _sim(
        {
            "basalt": {
                "label": "Basalt",
                "composition_wt_pct": {
                    "SiO2": 44.5,
                    "TiO2": 1.5,
                    "Al2O3": 13.5,
                    "FeO": 16.5,
                    "MgO": 9.0,
                    "CaO": 11.0,
                    "Na2O": 0.4,
                    "K2O": 0.1,
                    "Cr2O3": 0.35,
                    "MnO": 0.2,
                    "P2O5": 0.1,
                },
            }
        }
    )

    sim.load_batch("basalt", mass_kg=1000.0)
    snapshot = sim._make_snapshot()

    assert sim.melt.total_mass_kg == pytest.approx(1000.0)
    assert "unassigned_feedstock_residue" not in sim.inventory.residual_components_kg
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0)
    assert sim.melt.composition_wt_pct()["SiO2"] == pytest.approx(
        44.5 / 97.15 * 100.0)


def test_carbonaceous_stage0_uses_anhydrous_silicate_handoff():
    sim = _sim(
        {
            "ci": {
                "label": "CI",
                "composition_wt_pct": {
                    "SiO2": 25.0,
                    "FeO": 25.0,
                    "MgO": 18.5,
                    "Al2O3": 2.25,
                    "CaO": 1.75,
                    "Na2O": 0.75,
                    "NiO": 1.5,
                    "H2O": 15.0,
                    "C": 3.5,
                    "S": 4.0,
                },
                "stage0_profile": "carbonaceous_degas_cleanup",
                "stage0_temp_range_C": [20.0, 1050.0],
                "anhydrous_silicate_after_degassing": {
                    "mass_per_tonne_kg": [650, 800],
                    "composition_wt_pct": {
                        "SiO2": 36.0,
                        "FeO": 30.0,
                        "MgO": 24.0,
                        "Al2O3": 2.5,
                        "CaO": 2.25,
                        "NiO": 2.0,
                    },
                },
                "key_products": {
                    "H2O_kg_per_tonne": [100, 170],
                    "Fe_Ni_alloy_kg_per_tonne": [75, 120],
                    "S_kg_per_tonne": [25, 40],
                    "hydrocarbons_kg_per_tonne": [15, 30],
                },
            }
        }
    )

    sim.load_batch("ci", mass_kg=1000.0)
    inv = sim.inventory

    assert inv.stage0_profile == "carbonaceous_degas_cleanup"
    assert inv.cleaned_melt_source == "anhydrous_silicate_after_degassing"
    assert inv.stage0_temp_range_C == (20.0, 1050.0)
    assert sim.melt.composition_kg["SiO2"] == pytest.approx(286.004663)
    assert sim.melt.composition_kg["FeO"] == pytest.approx(238.337219)
    assert "H2O" not in sim.melt.composition_kg
    assert "C" not in sim.melt.composition_kg
    assert "S" not in sim.melt.composition_kg
    assert sim.melt.composition_kg["NiO"] == pytest.approx(15.889148)

    assert inv.gas_volatiles_kg["H2O"] == pytest.approx(154.241645)
    assert inv.gas_volatiles_kg["C"] == pytest.approx(35.989717)
    assert "hydrocarbons" not in inv.gas_volatiles_kg
    assert inv.sulfide_matte_kg["S"] == pytest.approx(41.131105)
    assert "Fe_Ni_alloy" not in inv.metal_alloy_kg
    assert "Fe_Ni_alloy" not in inv.drain_tap_kg
    assert "Fe_Ni_alloy" not in inv.stage0_products_kg
    assert "cleaned_melt_NiO" not in inv.residual_components_kg


def test_anhydrous_silicate_handoff_requires_loud_stage0_profile():
    sim = _sim(
        {
            "ci": {
                "label": "CI",
                "composition_wt_pct": {"SiO2": 50.0, "H2O": 50.0},
                "stage0_temp_range_C": [20.0, 1050.0],
                "anhydrous_silicate_after_degassing": {
                    "composition_wt_pct": {"SiO2": 100.0},
                },
            }
        }
    )

    with pytest.raises(ValueError, match="stage0_profile"):
        sim.load_batch("ci", mass_kg=1000.0)


def test_mars_carbon_cleanup_routes_products_and_keeps_melt_oxide_only():
    sim = _sim(
        {
            "mars": {
                "label": "Mars sulfate-rich",
                "stage0_profile": "mars_carbon_cleanup",
                "environment": {
                    "surface_pressure_mbar": 6,
                    "atmosphere": "96% CO2",
                },
                "composition_wt_pct": {
                    "SiO2": 45.0,
                    "TiO2": 1.0,
                    "Al2O3": 10.0,
                    "FeO": 17.5,
                    "MgO": 8.5,
                    "CaO": 7.0,
                    "Na2O": 2.75,
                    "K2O": 0.5,
                    "SO3": 11.5,
                    "Cl": 0.85,
                    "P2O5": 0.85,
                },
                "bonus_products": {
                    "sulfuric_acid_feedstock_kg_per_tonne": [30, 50],
                    "O2_extra_kg_per_tonne": [20, 40],
                },
                "process_notes": (
                    "Extended carbon pre-reduction (30-60 kg C/t); "
                    "Boudouard self-cleaning."
                ),
            }
        }
    )

    sim.load_batch("mars", mass_kg=1000.0, additives_kg={"C": 45.0})
    inv = sim.inventory

    assert inv.stage0_profile == "mars_carbon_cleanup"
    assert inv.stage0_temp_range_C == (20.0, 1050.0)
    assert inv.carbon_reductant_required_kg == pytest.approx(45.0)
    assert sim.atom_ledger.kg_by_account(
        "reservoir.reagent.C").get("C", 0.0) == pytest.approx(0.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.offgas")["C"] == pytest.approx(45.0)
    assert "unspent_C_reagent" not in sim.product_ledger()
    assert sim.melt.ambient_pressure_mbar == pytest.approx(6.0)
    assert sim.melt.ambient_atmosphere == "96% CO2"
    assert "SO3" not in sim.melt.composition_kg
    assert "Cl" not in sim.melt.composition_kg
    assert inv.salt_phase_kg["SO3"] == pytest.approx(109.056425)
    assert inv.salt_phase_kg["Cl"] == pytest.approx(8.060692)
    assert "sulfuric_acid_feedstock" not in inv.salt_phase_kg
    assert "O2_extra" not in inv.gas_volatiles_kg
    assert "SO3" not in inv.residual_components_kg
    assert "Cl" not in inv.residual_components_kg


def test_mars_carbon_cleanup_requires_carbon_additive():
    sim = _sim(
        {
            "mars": {
                "label": "Mars sulfate-rich",
                "stage0_profile": "mars_carbon_cleanup",
                "composition_wt_pct": {
                    "SiO2": 45.0,
                    "FeO": 17.5,
                    "SO3": 11.5,
                },
                "process_notes": "carbon pre-reduction (30-60 kg C/t)",
            }
        }
    )

    with pytest.raises(AccountingError, match="requires .* kg C"):
        sim.load_batch("mars", mass_kg=1000.0)


def test_co2_atmosphere_without_carbon_intent_does_not_select_carbon_cleanup():
    sim = _sim(
        {
            "co2_sulfate": {
                "label": "CO2 sulfate",
                "environment": {"atmosphere": "96% CO2"},
                "composition_wt_pct": {
                    "SiO2": 50.0,
                    "FeO": 40.0,
                    "SO3": 5.0,
                    "ClO4": 1.0,
                },
                "process_notes": "CO2 carrier gas only.",
            }
        }
    )

    sim.load_batch("co2_sulfate", mass_kg=1000.0)

    assert sim.inventory.stage0_profile == "bulk_preservation"
    assert sim.inventory.carbon_reductant_required_kg == pytest.approx(0.0)


def test_declared_stage0_product_requires_matching_source_species():
    sim = _sim(
        {
            "bad_declared_product": {
                "label": "Bad declared product",
                "composition_wt_pct": {"SiO2": 99.0, "Fe": 1.0},
                "key_products": {"Ni_kg_per_tonne": 50.0},
            }
        }
    )

    with pytest.raises(ValueError, match="declared Stage 0"):
        sim.load_batch("bad_declared_product", mass_kg=1000.0)


@pytest.mark.parametrize(
    ("notes", "expected_kg"),
    [
        ("carbon pre-reduction (30-60 kg C/t)", 45.0),
        ("carbon pre-reduction (30–60 kg C/t)", 45.0),
        ("carbon pre-reduction 30 to 60 kg C/t", 45.0),
        ("carbon pre-reduction 45 kg C/t", 45.0),
    ],
)
def test_carbon_reductant_parser_accepts_common_range_formats(
    notes, expected_kg
):
    feedstock = {"process_notes": notes}

    assert PyrolysisSimulator._carbon_reductant_required_kg(
        feedstock, 1000.0) == pytest.approx(expected_kg)


def test_bulk_additions_share_batch_mass_without_stage0_balance_plug():
    sim = _sim(
        {
            "s_type": {
                "label": "S type",
                "composition_wt_pct": {
                    "SiO2": 51.5,
                    "Al2O3": 3.0,
                    "FeO": 13.0,
                    "MgO": 34.0,
                    "CaO": 2.0,
                    "Na2O": 1.0,
                    "K2O": 0.1,
                    "Cr2O3": 0.45,
                    "MnO": 0.3,
                },
                "bulk_additions": {
                    "metallic_FeNi_wt_pct": 15.0,
                    "FeS_troilite_wt_pct": 5.5,
                    "C_wt_pct": 0.3,
                },
            }
        }
    )

    sim.load_batch("s_type", mass_kg=1000.0)
    snapshot = sim._make_snapshot()

    assert sim.inventory.metal_alloy_kg["metallic_FeNi"] == pytest.approx(118.906064)
    assert sim.inventory.stage0_products_kg["C"] == pytest.approx(2.378121)
    assert sim.inventory.sulfide_matte_kg["FeS_troilite"] == pytest.approx(43.598890)
    assert sim.inventory.stage0_mass_balance_delta_kg == pytest.approx(0.0)
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0)


def test_unspent_additives_are_accounted_until_consumed():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {
                    "SiO2": 50.0,
                    "FeO": 50.0,
                },
            }
        }
    )

    sim.load_batch("oxide", mass_kg=1000.0, additives_kg={"K": 10.0})
    snapshot = sim._make_snapshot()

    assert snapshot.mass_balance_error_pct == pytest.approx(0.0)
    assert sim.product_ledger()["unspent_K_reagent"] == pytest.approx(10.0)


def test_recovered_reagent_transfer_debits_condenser_product():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {
                    "SiO2": 100.0,
                },
            }
        }
    )

    sim.load_batch("oxide", mass_kg=1000.0)
    sim.train.stages[3].collected_kg["K"] = 2.0
    before = sim._make_snapshot()

    sim._init_shuttle_inventory(CampaignPhase.C3_K)
    after = sim._make_snapshot()

    assert before.mass_balance_error_pct == pytest.approx(0.0)
    assert after.mass_balance_error_pct == pytest.approx(0.0)
    assert sim.train.stages[3].collected_kg["K"] == pytest.approx(2.0)
    assert sim.shuttle_K_inventory_kg == pytest.approx(0.0)
    assert "unspent_K_reagent" not in sim.product_ledger()

    sim.atom_ledger.load_external(
        "process.condensation_train",
        {"K": 2.0},
        source="test recovered K condensate",
    )
    assert sim._transfer_condensed_species("K") == pytest.approx(2.0)
    assert sim.train.stages[3].collected_kg["K"] == pytest.approx(0.0)
    assert sim.shuttle_K_inventory_kg == pytest.approx(2.0)
    assert sim.product_ledger()["unspent_K_reagent"] == pytest.approx(2.0)


def test_oxygen_is_not_duplicated_in_product_ledger():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {
                    "SiO2": 50.0,
                    "FeO": 50.0,
                },
            }
        }
    )

    sim.load_batch("oxide", mass_kg=1000.0)
    with pytest.raises(AccountingError):
        sim._debit_vented_oxygen(0.1)

    sim.atom_ledger.load_external(
        "terminal.oxygen_melt_offgas_stored",
        {"O2": 5.0},
        source="test stored oxygen",
    )
    sim.train.stages[1].collected_kg["O2"] = 1.5
    sim.train.stages[4].collected_kg["O2"] = 3.5
    sim._debit_vented_oxygen(3.0)
    snapshot = sim._make_snapshot()
    sim.melt.campaign = CampaignPhase.COMPLETE
    sim._finalize_record()

    assert "O2" not in sim.record.products_kg
    assert snapshot.oxygen_produced_kg == pytest.approx(5.0)
    assert snapshot.O2_stored_kg == pytest.approx(2.0)
    assert snapshot.O2_vented_cumulative_kg == pytest.approx(3.0)
    assert snapshot.melt_offgas_O2_stored_kg == pytest.approx(2.0)
    assert snapshot.melt_offgas_O2_vented_kg == pytest.approx(3.0)
    assert snapshot.mre_anode_O2_stored_kg == pytest.approx(0.0)
    assert snapshot.condensation_totals["O2"] == pytest.approx(2.0)
    assert sim.record.oxygen_total_kg == pytest.approx(5.0)
    assert sim.record.oxygen_stored_kg == pytest.approx(2.0)
    assert sim.record.oxygen_vented_kg == pytest.approx(3.0)
    assert (
        sim.record.oxygen_stored_kg + sim.record.oxygen_vented_kg
        == pytest.approx(sim.record.oxygen_total_kg))
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_stored")["O2"] == pytest.approx(2.0)
    assert sim.atom_ledger.kg_by_account(
        "terminal.oxygen_melt_offgas_vented_to_vacuum")["O2"] == pytest.approx(3.0)
    assert sum(
        stage.collected_kg.get("O2", 0.0)
        for stage in sim.train.stages
    ) == pytest.approx(0.0)


def test_mre_anode_o2_snapshot_bin_is_separate_from_melt_offgas():
    sim = _sim(
        {
            "oxide": {
                "label": "Oxide",
                "composition_wt_pct": {
                    "SiO2": 50.0,
                    "FeO": 50.0,
                },
            }
        }
    )
    sim.load_batch("oxide", mass_kg=1000.0)
    sim.atom_ledger.load_external(
        "terminal.oxygen_mre_anode_stored",
        {"O2": 4.0},
        source="test MRE anode oxygen",
    )

    snapshot = sim._make_snapshot()

    assert snapshot.oxygen_produced_kg == pytest.approx(4.0)
    assert snapshot.O2_stored_kg == pytest.approx(4.0)
    assert snapshot.melt_offgas_O2_stored_kg == pytest.approx(0.0)
    assert snapshot.melt_offgas_O2_vented_kg == pytest.approx(0.0)
    assert snapshot.mre_anode_O2_stored_kg == pytest.approx(4.0)
    assert "O2" not in snapshot.condensation_totals


def test_composition_ranges_do_not_create_stage0_products():
    sim = _sim(
        {
            "ceres": {
                "label": "Ceres",
                "composition_wt_pct": {
                    "SiO2": 35.0,
                    "FeO": 24.0,
                    "MgO": 23.0,
                    "H2O": 20.0,
                },
                "composition_ranges": {
                    "H2O_ice_structural_kg_per_tonne": [120, 280],
                },
                "bonus_products": {
                    "H2O_kg_per_tonne": [120, 250],
                },
            }
        }
    )

    sim.load_batch("ceres", mass_kg=1000.0)

    assert "H2O_ice_structural" not in sim.inventory.stage0_products_kg
    assert sim.inventory.gas_volatiles_kg["H2O"] == pytest.approx(196.078431)


def test_m_type_phosphorus_routes_to_drain_tap():
    sim = _sim(
        {
            "m_type": {
                "label": "M type metal",
                "composition_wt_pct": {
                    "Fe": 90.0,
                    "Ni": 7.5,
                    "Co": 0.4,
                    "S": 1.25,
                    "P": 0.2,
                },
            }
        }
    )

    sim.load_batch("m_type", mass_kg=1000.0)

    assert sim.inventory.drain_tap_kg["P"] == pytest.approx(2.013085)
    assert "P" not in sim.inventory.residual_components_kg
