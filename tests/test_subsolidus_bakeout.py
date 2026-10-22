"""C0 subsolidus bakeout ledger: exact hosts, one mass, no hour-zero credit."""

from __future__ import annotations

import pytest

from simulator.accounting import resolve_species_formula
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend


def _sim(feedstocks):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def _load(feedstock, *, mass_kg=1000.0, additives_kg=None):
    sim = _sim({"feed": feedstock})
    sim.load_batch("feed", mass_kg=mass_kg, additives_kg=additives_kg or {})
    return sim


def test_hour_zero_troilite_stays_mutable_and_is_counted_once():
    sim = _load({
        "label": "troilite host",
        "composition_wt_pct": {
            "SiO2": 90.0,
            "FeS_troilite": 8.0,
            "S": 2.0,
        },
    })
    registry = sim.species_formula_registry
    troilite_kg = sim.inventory.raw_components_kg["FeS_troilite"]
    sulfur_kg = sim.inventory.raw_components_kg["S"]

    salt = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    matte = sim.atom_ledger.kg_by_account("terminal.stage0_sulfide_matte")
    offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    overhead = sim.atom_ledger.kg_by_account("process.overhead_gas")

    assert salt.get("FeS_troilite", 0.0) == pytest.approx(troilite_kg, abs=1e-9)
    assert matte.get("FeS_troilite", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert matte.get("S", 0.0) == pytest.approx(sulfur_kg, abs=1e-9)
    assert offgas.get("S2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert overhead.get("S2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert salt.get("S", 0.0) == pytest.approx(0.0, abs=1e-12)

    assert sim.record.initial_inventory.sulfide_matte_kg["FeS_troilite"] == (
        pytest.approx(troilite_kg, abs=1e-9)
    )
    assert sim.inventory.sulfide_matte_kg["FeS_troilite"] == pytest.approx(
        troilite_kg, abs=1e-9
    )

    troilite_mol = troilite_kg / resolve_species_formula(
        "FeS_troilite", registry
    ).molar_mass_kg_per_mol()
    live_mol = sim.atom_ledger.mol_by_account("process.stage0_salt_feed")
    assert live_mol.get("FeS_troilite", 0.0) == pytest.approx(troilite_mol, abs=1e-9)

    first = sim._make_snapshot()
    second = sim._make_snapshot()
    assert first.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)
    assert second.mass_out_kg == pytest.approx(first.mass_out_kg, abs=1e-12)
    assert second.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_hour_zero_sulfates_stay_mutable_without_offgas_credit():
    sim = _load({
        "label": "sulfate hosts",
        "composition_wt_pct": {
            "SiO2": 90.0,
            "CaSO4": 6.0,
            "MgSO4": 4.0,
        },
    })
    caso4_kg = sim.inventory.raw_components_kg["CaSO4"]
    mgso4_kg = sim.inventory.raw_components_kg["MgSO4"]
    salt = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")

    assert salt.get("CaSO4", 0.0) == pytest.approx(caso4_kg, abs=1e-9)
    assert salt.get("MgSO4", 0.0) == pytest.approx(mgso4_kg, abs=1e-9)
    assert sim.record.initial_inventory.cation_sulfate_feed_kg["CaSO4"] == (
        pytest.approx(caso4_kg, abs=1e-9)
    )
    assert sim.record.initial_inventory.cation_sulfate_feed_kg["MgSO4"] == (
        pytest.approx(mgso4_kg, abs=1e-9)
    )
    assert offgas.get("SO2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert offgas.get("O2", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert melt.get("CaO", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert melt.get("MgO", 0.0) == pytest.approx(0.0, abs=1e-12)

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_limited_carbon_still_gives_feso4_the_post_caso4_remainder():
    """CaSO4 reserves the carbon its old cleanup would have used.

    FeSO4 then sees that reduced remainder and keeps the Fe2O3 branch.
    """
    mass_kg = 1000.0
    feedstock = {
        "label": "shared sulfate carbon",
        "stage0_profile": "mars_carbon_cleanup",
        "stage0_carbon_cleanup": {
            "carbon_reductant_kg_per_tonne": 1.0,
            "reactions": ["sulfate_so3_to_so2_co"],
        },
        "composition_wt_pct": {
            "SiO2": 80.0,
            "CaSO4": 10.0,
            "FeSO4": 10.0,
        },
    }
    sim = _sim({"feed": feedstock})
    registry = sim.species_formula_registry
    caso4_kg = mass_kg * 0.10
    feso4_kg = mass_kg * 0.10
    # Provisional carbon; replaced with the exact shared budget below.
    sim = _sim({"feed": feedstock})
    caso4_mol = caso4_kg / resolve_species_formula(
        "CaSO4", registry
    ).molar_mass_kg_per_mol()
    feso4_mol = feso4_kg / resolve_species_formula(
        "FeSO4", registry
    ).molar_mass_kg_per_mol()
    c_molar = resolve_species_formula("C", registry).molar_mass_kg_per_mol()
    # CaSO4 cleanup would take 1 mol C per mol host. Give it the full host
    # plus half a mol C per mol FeSO4, which is the Fe2O3 stoichiometry.
    carbon_mol = caso4_mol + 0.5 * feso4_mol
    carbon_kg = carbon_mol * c_molar
    feedstock["stage0_carbon_cleanup"]["carbon_reductant_kg_per_tonne"] = (
        carbon_kg * 1000.0 / mass_kg
    )
    sim = _sim({"feed": feedstock})
    sim.load_batch("feed", mass_kg=mass_kg, additives_kg={"C": carbon_kg})

    caso4_kg = sim.inventory.raw_components_kg["CaSO4"]
    feso4_kg = sim.inventory.raw_components_kg["FeSO4"]
    caso4_mol = caso4_kg / resolve_species_formula(
        "CaSO4", registry
    ).molar_mass_kg_per_mol()
    feso4_mol = feso4_kg / resolve_species_formula(
        "FeSO4", registry
    ).molar_mass_kg_per_mol()
    fe2o3_molar = resolve_species_formula(
        "Fe2O3", registry
    ).molar_mass_kg_per_mol()
    so2_molar = resolve_species_formula("SO2", registry).molar_mass_kg_per_mol()
    co_molar = resolve_species_formula("CO", registry).molar_mass_kg_per_mol()

    salt = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    assert salt.get("CaSO4", 0.0) == pytest.approx(caso4_kg, abs=1e-6)
    assert salt.get("FeSO4", 0.0) == pytest.approx(0.0, abs=1e-9)
    assert "FeSO4" not in sim.inventory.cation_sulfate_feed_kg

    melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    # 2 FeSO4 + C -> Fe2O3 + 2 SO2 + CO, so products are half the feed moles.
    assert melt.get("Fe2O3", 0.0) == pytest.approx(
        0.5 * feso4_mol * fe2o3_molar, abs=1e-6
    )
    assert melt.get("CaO", 0.0) == pytest.approx(0.0, abs=1e-9)
    assert offgas.get("SO2", 0.0) == pytest.approx(feso4_mol * so2_molar, abs=1e-6)
    assert offgas.get("CO", 0.0) == pytest.approx(0.5 * feso4_mol * co_molar, abs=1e-6)
    assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(
        caso4_mol * c_molar, abs=1e-6
    )

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)
