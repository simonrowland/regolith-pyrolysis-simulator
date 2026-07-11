"""Stage-0 cation routing — stoichiometry-grounded regression tests.

Guards audit rows #2b/#4/#8: carbonate MCO3 -> MO(melt)+CO2(offgas) and
cation-bearing sulfate S->SO2 with cation credited to melt (not deleted to
salt bucket).  Assertions use published stoichiometry, not simulator parity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.builtin.foulant_disposition import chi_decomp, load_foulant_registry
from simulator.accounting import resolve_species_formula
from simulator.core import PyrolysisSimulator
from simulator.melt_backend.base import InternalAnalyticalBackend

REPO_ROOT = Path(__file__).resolve().parents[1]
FOULANT_THERMO = REPO_ROOT / "data" / "foulant_thermo.yaml"


def _sim(feedstocks):
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    return PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        feedstocks,
        {"metals": {}, "oxide_vapors": {}},
    )


def _carbonate_decomposition_extent(
    species: str,
    species_mol: float,
    melt_sio2_kg: float,
    temp_c: float,
    registry,
) -> float:
    if species_mol <= 0.0:
        return 0.0
    foulant_registry = load_foulant_registry(FOULANT_THERMO)
    thermal_extent = chi_decomp(species, temp_c, 0.0, 0.0, foulant_registry).extent
    thermal_extent = max(0.0, min(1.0, thermal_extent))
    if species != "Na2CO3":
        return thermal_extent
    sio2_mol = melt_sio2_kg / resolve_species_formula(
        "SiO2", registry).molar_mass_kg_per_mol()
    return thermal_extent * min(1.0, sio2_mol / species_mol)


def _expected_carbonate_decomposition(
    species: str,
    feed_kg: float,
    registry,
    *,
    melt_sio2_kg: float = 0.0,
    temp_c: float = 1050.0,
):
    """Ground-truth MCO3 -> MO + CO2, with Na2CO3 NIST/SiO2 gate."""
    if species == "carbonate_salts":
        components = (
            ("MgCO3", 1.0),
            ("CaCO3", 1.0),
            ("Na2CO3", 1.0),
        )
        component_molar = [
            (
                comp_id,
                moles,
                resolve_species_formula(comp_id, registry).molar_mass_kg_per_mol(),
            )
            for comp_id, moles in components
        ]
        total_group_mass = sum(
            moles * molar_mass for _, moles, molar_mass in component_molar
        )
        oxide_kg: dict[str, float] = {}
        co2_kg = 0.0
        for comp_id, moles, molar_mass in component_molar:
            comp_kg = feed_kg * (moles * molar_mass / total_group_mass)
            comp_formula = resolve_species_formula(comp_id, registry)
            comp_mol = comp_kg / comp_formula.molar_mass_kg_per_mol()
            extent = _carbonate_decomposition_extent(
                comp_id, comp_mol, melt_sio2_kg, temp_c, registry
            )
            atoms = comp_formula.atom_moles(comp_mol * extent)
            co2_kg += atoms.get("C", 0.0) * resolve_species_formula(
                "CO2", registry
            ).molar_mass_kg_per_mol()
            for metal, oxide, stoich in (
                ("Mg", "MgO", 1.0),
                ("Ca", "CaO", 1.0),
                ("Na", "Na2O", 2.0),
            ):
                metal_mol = atoms.get(metal, 0.0)
                if metal_mol <= 0.0:
                    continue
                oxide_mol = metal_mol / stoich
                oxide_kg[oxide] = oxide_kg.get(oxide, 0.0) + (
                    oxide_mol
                    * resolve_species_formula(oxide, registry).molar_mass_kg_per_mol()
                )
        return oxide_kg, co2_kg

    formula = resolve_species_formula(species, registry)
    species_mol = feed_kg / formula.molar_mass_kg_per_mol()
    extent = _carbonate_decomposition_extent(
        species, species_mol, melt_sio2_kg, temp_c, registry
    )
    atoms = formula.atom_moles(species_mol * extent)
    co2_kg = atoms.get("C", 0.0) * resolve_species_formula(
        "CO2", registry
    ).molar_mass_kg_per_mol()
    oxide_kg = {}
    for metal, oxide, stoich in (
        ("Mg", "MgO", 1.0),
        ("Ca", "CaO", 1.0),
        ("Fe", "FeO", 1.0),
        ("Na", "Na2O", 2.0),
        ("K", "K2O", 2.0),
    ):
        metal_mol = atoms.get(metal, 0.0)
        if metal_mol <= 0.0:
            continue
        oxide_mol = metal_mol / stoich
        oxide_kg[oxide] = (
            oxide_mol
            * resolve_species_formula(oxide, registry).molar_mass_kg_per_mol()
        )
    return oxide_kg, co2_kg


def _expected_caso4_sulfide_carbon_cleanup(feed_kg: float, registry):
    """Ground-truth CaSO4 + 4C -> CaS + 4CO."""
    feed_formula = resolve_species_formula("CaSO4", registry)
    extent_mol = feed_kg / feed_formula.molar_mass_kg_per_mol()
    molar = {
        species: resolve_species_formula(species, registry).molar_mass_kg_per_mol()
        for species in ("CO", "CaS", "C")
    }
    return {
        "CO": 4.0 * extent_mol * molar["CO"],
        "CaS": extent_mol * molar["CaS"],
        "C": 4.0 * extent_mol * molar["C"],
        "extent_mol": extent_mol,
    }


def _expected_caso4_carbon_cleanup(feed_kg: float, registry):
    """Ground-truth CaSO4 + C -> CaO + SO2 + CO."""
    feed_formula = resolve_species_formula("CaSO4", registry)
    extent_mol = feed_kg / feed_formula.molar_mass_kg_per_mol()
    molar = {
        species: resolve_species_formula(species, registry).molar_mass_kg_per_mol()
        for species in ("SO2", "CO", "CaO", "C")
    }
    return {
        "SO2": extent_mol * molar["SO2"],
        "CO": extent_mol * molar["CO"],
        "CaO": extent_mol * molar["CaO"],
        "C": extent_mol * molar["C"],
    }


def _expected_feso4_carbon_cleanup(feed_kg: float, registry):
    """Ground-truth 2 FeSO4 + C -> Fe2O3 + 2 SO2 + CO."""
    feed_formula = resolve_species_formula("FeSO4", registry)
    feed_mol = feed_kg / feed_formula.molar_mass_kg_per_mol()
    molar = {
        species: resolve_species_formula(species, registry).molar_mass_kg_per_mol()
        for species in ("SO2", "CO", "Fe2O3", "C")
    }
    return {
        "SO2": feed_mol * molar["SO2"],
        "CO": 0.5 * feed_mol * molar["CO"],
        "Fe2O3": 0.5 * feed_mol * molar["Fe2O3"],
        "C": 0.5 * feed_mol * molar["C"],
        "extent_mol": feed_mol,
    }


def _expected_feso4_sulfide_carbon_cleanup(feed_kg: float, registry):
    """Ground-truth FeSO4 + 4C -> FeS + 4CO."""
    feed_formula = resolve_species_formula("FeSO4", registry)
    extent_mol = feed_kg / feed_formula.molar_mass_kg_per_mol()
    molar = {
        species: resolve_species_formula(species, registry).molar_mass_kg_per_mol()
        for species in ("CO", "FeS", "C")
    }
    return {
        "CO": 4.0 * extent_mol * molar["CO"],
        "FeS": extent_mol * molar["FeS"],
        "C": 4.0 * extent_mol * molar["C"],
        "extent_mol": extent_mol,
    }


@pytest.fixture
def ceres_carbonate_feedstock():
    return {
        "label": "Ceres carbonate routing test",
        "composition_wt_pct": {
            "SiO2": 70.0,
            "FeO": 12.0,
            "MgO": 8.0,
            "carbonate_salts": 10.0,
        },
        "stage0_profile": "carbonaceous_degas_cleanup",
        "stage0_temp_range_C": [20, 1050],
        "anhydrous_silicate_after_degassing": {
            "mass_per_tonne_kg": 700.0,
            "composition_wt_pct": {
                "SiO2": 40.0,
                "FeO": 30.0,
                "MgO": 24.0,
                "Al2O3": 2.5,
                "CaO": 2.0,
                "NiO": 1.5,
            },
        },
    }


@pytest.fixture
def mars_caso4_sulfide_feedstock():
    return {
        "label": "Mars CaSO4 sulfide routing test",
        "stage0_profile": "mars_carbon_cleanup",
        "stage0_carbon_cleanup": {
            "carbon_reductant_kg_per_tonne": 18.0,
            "cation_sulfate_product": "sulfide",
            "reactions": [
                "sulfate_so3_to_so2_co",
            ],
        },
        "composition_wt_pct": {
            "SiO2": 85.0,
            "FeO": 5.0,
            "MgO": 5.0,
            "CaSO4": 5.0,
        },
    }


@pytest.fixture
def mars_caso4_feedstock():
    return {
        "label": "Mars CaSO4 routing test",
        "stage0_profile": "mars_carbon_cleanup",
        "stage0_carbon_cleanup": {
            "carbon_reductant_kg_per_tonne": 60.0,
            "reactions": [
                "sulfate_so3_to_so2_co",
                "co2_boudouard_to_co",
            ],
        },
        "environment": {
            "atmosphere": "96% CO2",
        },
        "composition_wt_pct": {
            "SiO2": 40.0,
            "FeO": 15.0,
            "MgO": 8.0,
            "CaO": 5.0,
            "CaSO4": 5.0,
            "SO3": 2.0,
        },
    }


@pytest.fixture
def mars_feso4_feedstock():
    return {
        "label": "Mars FeSO4 routing test",
        "stage0_profile": "mars_carbon_cleanup",
        "stage0_carbon_cleanup": {
            "carbon_reductant_kg_per_tonne": 60.0,
            "reactions": [
                "sulfate_so3_to_so2_co",
            ],
        },
        "composition_wt_pct": {
            "SiO2": 95.0,
            "FeSO4": 5.0,
        },
    }


@pytest.fixture
def mars_feso4_sulfide_feedstock(mars_feso4_feedstock):
    feedstock = dict(mars_feso4_feedstock)
    feedstock["stage0_carbon_cleanup"] = dict(feedstock["stage0_carbon_cleanup"])
    feedstock["stage0_carbon_cleanup"]["cation_sulfate_product"] = "sulfide"
    return feedstock


def test_carbonate_salts_decompose_to_melt_oxides_and_co2_offgas(
    ceres_carbonate_feedstock,
):
    sim = _sim({"ceres_test": ceres_carbonate_feedstock})
    mass_kg = 1000.0
    sim.load_batch("ceres_test", mass_kg=mass_kg)

    carbonate_kg = sim.inventory.raw_components_kg["carbonate_salts"]
    registry = sim.species_formula_registry
    expected_oxides, expected_co2 = _expected_carbonate_decomposition(
        "carbonate_salts",
        carbonate_kg,
        registry,
        melt_sio2_kg=sim.inventory.melt_oxide_kg.get("SiO2", 0.0),
        temp_c=sim.inventory.stage0_temp_range_C[1],
    )

    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("CO2", 0.0) == pytest.approx(expected_co2, rel=0, abs=1e-9)

    ledger_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    for oxide, expected_kg in expected_oxides.items():
        assert ledger_melt.get(oxide, 0.0) >= expected_kg - 1e-9

    assert "carbonate_salts" not in sim.inventory.salt_phase_kg
    salt_ledger = sim.atom_ledger.kg_by_account("terminal.stage0_salt_phase")
    assert "carbonate_salts" not in salt_ledger

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_na2co3_without_sio2_stays_salt_phase_not_calcined():
    feedstock = {
        "label": "Na2CO3 no SiO2 gate test",
        "composition_wt_pct": {
            "FeO": 95.0,
            "Na2CO3": 5.0,
        },
        "stage0_temp_range_C": [20.0, 1050.0],
    }
    sim = _sim({"na2co3_no_sio2": feedstock})
    sim.load_batch("na2co3_no_sio2", mass_kg=1000.0)

    assert sim.inventory.salt_phase_kg["Na2CO3"] == pytest.approx(50.0, abs=1e-9)
    assert sim.inventory.melt_oxide_kg.get("Na2O", 0.0) == pytest.approx(0.0, abs=1e-12)

    ledger_salt = sim.atom_ledger.kg_by_account("terminal.stage0_salt_phase")
    assert ledger_salt["Na2CO3"] == pytest.approx(50.0, abs=1e-9)
    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("CO2", 0.0) == pytest.approx(0.0, abs=1e-12)

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_na2co3_below_nist_silicate_displacement_onset_stays_salt_phase():
    feedstock = {
        "label": "Na2CO3 low temperature gate test",
        "stage0_profile": "carbonaceous_degas_cleanup",
        "composition_wt_pct": {
            "FeO": 95.0,
            "Na2CO3": 5.0,
        },
        "anhydrous_silicate_after_degassing": {
            "mass_per_tonne_kg": 950.0,
            "composition_wt_pct": {
                "SiO2": 100.0,
            },
        },
        "stage0_temp_range_C": [20.0, 21.0],
    }
    sim = _sim({"na2co3_low_t": feedstock})
    sim.load_batch("na2co3_low_t", mass_kg=1000.0)

    assert sim.inventory.salt_phase_kg["Na2CO3"] > 49.99
    assert sim.inventory.melt_oxide_kg.get("Na2O", 0.0) < 0.01

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_caso4_carbothermal_routes_cation_to_melt_not_salt_bucket(
    mars_caso4_feedstock,
):
    sim = _sim({"mars_caso4": mars_caso4_feedstock})
    mass_kg = 1000.0
    required_c = PyrolysisSimulator._carbon_reductant_required_kg(
        mars_caso4_feedstock, mass_kg)
    sim.load_batch("mars_caso4", mass_kg=mass_kg, additives_kg={"C": required_c})

    caso4_kg = sim.inventory.raw_components_kg["CaSO4"]
    registry = sim.species_formula_registry
    expected = _expected_caso4_carbon_cleanup(caso4_kg, registry)

    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("SO2", 0.0) >= expected["SO2"] - 1e-9
    assert ledger_offgas.get("CO", 0.0) >= expected["CO"] - 1e-9

    ledger_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    baseline_cao = mars_caso4_feedstock["composition_wt_pct"]["CaO"] / 100.0 * mass_kg
    assert ledger_melt.get("CaO", 0.0) >= baseline_cao + expected["CaO"] - 1e-9

    assert "CaSO4" not in sim.inventory.salt_phase_kg
    salt_ledger = sim.atom_ledger.kg_by_account("terminal.stage0_salt_phase")
    assert "CaSO4" not in salt_ledger

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_feso4_carbothermal_oxide_route_balances_fe2o3_half_stoich(
    mars_feso4_feedstock,
):
    mass_kg = 1000.0
    feedstock = dict(mars_feso4_feedstock)
    feedstock["stage0_carbon_cleanup"] = dict(feedstock["stage0_carbon_cleanup"])
    feso4_feed_kg = feedstock["composition_wt_pct"]["FeSO4"] / 100.0 * mass_kg

    sim = _sim({"mars_feso4": feedstock})
    registry = sim.species_formula_registry
    expected = _expected_feso4_carbon_cleanup(feso4_feed_kg, registry)
    exact_c_kg = expected["C"]
    feedstock["stage0_carbon_cleanup"]["carbon_reductant_kg_per_tonne"] = (
        exact_c_kg * 1000.0 / mass_kg
    )

    sim = _sim({"mars_feso4": feedstock})
    sim.load_batch("mars_feso4", mass_kg=mass_kg, additives_kg={"C": exact_c_kg})

    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("SO2", 0.0) == pytest.approx(expected["SO2"], rel=0, abs=1e-9)
    assert ledger_offgas.get("CO", 0.0) == pytest.approx(expected["CO"], rel=0, abs=1e-9)

    ledger_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    assert ledger_melt.get("Fe2O3", 0.0) == pytest.approx(expected["Fe2O3"], rel=0, abs=1e-9)
    assert "FeSO4" not in sim.inventory.salt_phase_kg

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_feso4_sulfide_carbothermal_route_balances_fes_and_four_co(
    mars_feso4_sulfide_feedstock,
):
    mass_kg = 1000.0
    feedstock = dict(mars_feso4_sulfide_feedstock)
    feedstock["stage0_carbon_cleanup"] = dict(feedstock["stage0_carbon_cleanup"])
    feso4_feed_kg = feedstock["composition_wt_pct"]["FeSO4"] / 100.0 * mass_kg

    sim = _sim({"mars_feso4_sulfide": feedstock})
    registry = sim.species_formula_registry
    expected = _expected_feso4_sulfide_carbon_cleanup(feso4_feed_kg, registry)
    exact_c_kg = expected["C"]
    feedstock["stage0_carbon_cleanup"]["carbon_reductant_kg_per_tonne"] = (
        exact_c_kg * 1000.0 / mass_kg
    )

    sim = _sim({"mars_feso4_sulfide": feedstock})
    sim.load_batch(
        "mars_feso4_sulfide", mass_kg=mass_kg, additives_kg={"C": exact_c_kg})

    ledger_matte = sim.atom_ledger.kg_by_account("terminal.stage0_sulfide_matte")
    assert ledger_matte.get("FeS", 0.0) == pytest.approx(expected["FeS"], rel=0, abs=1e-9)

    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("CO", 0.0) == pytest.approx(expected["CO"], rel=0, abs=1e-9)

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_caso4_sulfide_carbothermal_routes_s_to_matte_and_four_co_per_sulfate(
    mars_caso4_sulfide_feedstock,
):
    mass_kg = 1000.0
    feedstock = dict(mars_caso4_sulfide_feedstock)
    feedstock["stage0_carbon_cleanup"] = dict(feedstock["stage0_carbon_cleanup"])
    caso4_feed_kg = feedstock["composition_wt_pct"]["CaSO4"] / 100.0 * mass_kg

    sim = _sim({"mars_caso4_sulfide": feedstock})
    registry = sim.species_formula_registry
    expected = _expected_caso4_sulfide_carbon_cleanup(caso4_feed_kg, registry)
    exact_c_kg = expected["C"]
    feedstock["stage0_carbon_cleanup"]["carbon_reductant_kg_per_tonne"] = (
        exact_c_kg * 1000.0 / mass_kg
    )

    sim = _sim({"mars_caso4_sulfide": feedstock})
    sim.load_batch(
        "mars_caso4_sulfide", mass_kg=mass_kg, additives_kg={"C": exact_c_kg})

    caso4_kg = sim.inventory.raw_components_kg["CaSO4"]
    expected = _expected_caso4_sulfide_carbon_cleanup(caso4_kg, registry)

    ledger_matte = sim.atom_ledger.kg_by_account("terminal.stage0_sulfide_matte")
    assert ledger_matte.get("CaS", 0.0) == pytest.approx(expected["CaS"], rel=0, abs=1e-9)

    caso4_formula = resolve_species_formula("CaSO4", registry)
    caso4_atoms = caso4_formula.atom_moles(expected["extent_mol"])
    cas_formula = resolve_species_formula("CaS", registry)
    cas_atoms = cas_formula.atom_moles(expected["extent_mol"])
    assert cas_atoms.get("S", 0.0) == pytest.approx(caso4_atoms.get("S", 0.0), rel=0, abs=1e-12)

    co_formula = resolve_species_formula("CO", registry)
    co_molar = co_formula.molar_mass_kg_per_mol()
    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("CO", 0.0) == pytest.approx(expected["CO"], rel=0, abs=1e-9)
    assert ledger_offgas.get("CO", 0.0) / co_molar == pytest.approx(
        4.0 * expected["extent_mol"], rel=0, abs=1e-9
    )

    assert "CaSO4" not in sim.inventory.salt_phase_kg
    salt_ledger = sim.atom_ledger.kg_by_account("terminal.stage0_salt_phase")
    assert "CaSO4" not in salt_ledger

    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_bare_so3_sulfate_path_unchanged(mars_caso4_feedstock):
    """Regression: pre-cracked SO3 surrogate path must still fire."""
    sim = _sim({"mars_caso4": mars_caso4_feedstock})
    mass_kg = 1000.0
    required_c = PyrolysisSimulator._carbon_reductant_required_kg(
        mars_caso4_feedstock, mass_kg)
    sim.load_batch("mars_caso4", mass_kg=mass_kg, additives_kg={"C": required_c})

    so3_kg = sim.inventory.raw_components_kg["SO3"]
    registry = sim.species_formula_registry
    extent_mol = so3_kg / resolve_species_formula(
        "SO3", registry).molar_mass_kg_per_mol()
    molar = {
        species: resolve_species_formula(species, registry).molar_mass_kg_per_mol()
        for species in ("SO2", "CO")
    }
    expected_so2 = extent_mol * molar["SO2"]
    expected_co = extent_mol * molar["CO"]

    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("SO2", 0.0) >= expected_so2 - 1e-9
    assert ledger_offgas.get("CO", 0.0) >= expected_co - 1e-9
    assert "SO3" not in sim.inventory.salt_phase_kg
