"""Stage-0 cation routing — stoichiometry-grounded regression tests.

Guards audit rows #2b/#4/#8: carbonate MCO3 -> MO(melt)+CO2(offgas) and
cation-bearing sulfate S->SO2 with cation credited to melt (not deleted to
salt bucket).  Assertions use published stoichiometry, not simulator parity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engines.builtin.foulant_disposition import (
    FoulantCarbonRankingRefusal,
    chi_decomp,
    foulant_dg_kJ_per_mol_carbon,
    load_foulant_registry,
)
from simulator.accounting import AccountingError, resolve_species_formula
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


def _limiting_carbon_probe(carbon_mol=1.0, *, include_boudouard=False):
    from tests.test_subsolidus_bakeout import _molar

    host_kg = {"CaSO4": _molar("CaSO4"), "FeSO4": 2 * _molar("FeSO4")}
    reactions = ["sulfate_so3_to_so2_co"]
    if include_boudouard:
        reactions.append("co2_boudouard_to_co")
    feed = {
        "stage0_profile": "mars_carbon_cleanup",
        "stage0_carbon_cleanup": {
            "carbon_reductant_kg_per_tonne": carbon_mol * _molar("C"),
            "cation_sulfate_product": "oxide",
            "reactions": reactions,
        },
        "composition_wt_pct": {
            "SiO2": 100 - sum(host_kg.values()) / 10,
            **{species: kg / 10 for species, kg in host_kg.items()},
        },
    }
    if include_boudouard:
        feed["environment"] = {"atmosphere": "96% CO2"}
    sim = _sim({"feed": feed})
    sim.load_batch(
        "feed", mass_kg=1000, additives_kg={"C": carbon_mol * _molar("C")}
    )
    sim.atom_ledger.assert_balanced()
    consumed = sum(
        amounts.get("C", 0.0) / _molar("C")
        for spec in sim._stage0_carbon_cleanup_specs
        for account, amounts, _origin in spec["debits"]
        if account == "process.reagent_inventory"
    )
    snapshot = sim._make_snapshot()
    return {
        "ledger": sim.atom_ledger.mol_by_account(),
        "reserved": sim._stage0_bakeout_reserved_carbon_kg / _molar("C"),
        "consumed": consumed,
        "mass_in_kg": snapshot.mass_in_kg,
        "mass_out_kg": snapshot.mass_out_kg,
    }


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


def test_unreacted_feso4_remainder_is_ledgered_and_mass_closes():
    result = _limiting_carbon_probe(0.25)

    assert result["mass_in_kg"] == pytest.approx(result["mass_out_kg"], abs=1e-9)
    assert result["ledger"]["process.stage0_salt_feed"]["FeSO4"] == pytest.approx(
        1.5, abs=1e-12
    )


def test_full_carbon_sulfate_routing_is_unchanged():
    result = _limiting_carbon_probe(1.0)
    ledger = result["ledger"]

    assert ledger["process.stage0_salt_feed"]["CaSO4"] == pytest.approx(1.0)
    assert ledger["process.stage0_salt_feed"].get("FeSO4", 0.0) == pytest.approx(0.0)
    assert ledger["process.cleaned_melt"]["Fe2O3"] == pytest.approx(1.0)
    assert ledger["terminal.offgas"]["SO2"] == pytest.approx(2.0)
    assert result["reserved"] == pytest.approx(0.0)


@pytest.mark.parametrize("carbon_mol", [0.25, 1.0, 1.5, 2.0])
def test_sulfate_carbon_claims_share_one_budget(carbon_mol):
    result = _limiting_carbon_probe(carbon_mol, include_boudouard=True)

    assert result["reserved"] + result["consumed"] <= carbon_mol + 1e-12
    assert result["reserved"] + result["consumed"] == pytest.approx(carbon_mol)


def test_bakeout_reservation_cannot_overclaim_remaining_carbon():
    from tests.test_subsolidus_bakeout import _molar

    sim = _sim({})
    reserved, remaining = sim._reserve_stage0_bakeout_sulfate_carbon(
        "CaSO4", 2.0, 1.0, _molar("C"), 1.0,
    )
    assert reserved
    assert remaining == 0.0
    assert sim._stage0_bakeout_reserved_carbon_kg == _molar("C")


def test_carbon_admission_guard_rejects_summed_overclaims(monkeypatch):
    original = PyrolysisSimulator._apply_stage0_cation_sulfate_carbon_reactions

    def overclaim(sim, feedstock, carbon_mol, specs):
        remaining = original(sim, feedstock, carbon_mol, specs)
        specs.append({"debits": (("process.reagent_inventory", {"C": 1.0}, "reagent"),)})
        return remaining

    monkeypatch.setattr(
        PyrolysisSimulator, "_apply_stage0_cation_sulfate_carbon_reactions", overclaim,
    )
    with pytest.raises(AccountingError, match="claims exceed the admitted C budget"):
        _limiting_carbon_probe()


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


def test_caso4_bakeout_host_is_retained_and_carbon_cleanup_does_not_credit_it(
    mars_caso4_feedstock,
):
    """CaSO4 stays a mutable host. SO3 carbon cleanup still runs.

    Carbon that the old CaSO4 + C cleanup would have taken is reserved, so
    the SO3 reaction and the following Boudouard step see the same remaining
    carbon. No CaO, SO2, or CO is credited from CaSO4.
    """
    sim = _sim({"mars_caso4": mars_caso4_feedstock})
    mass_kg = 1000.0
    required_c = PyrolysisSimulator._carbon_reductant_required_kg(
        mars_caso4_feedstock, mass_kg)
    sim.load_batch("mars_caso4", mass_kg=mass_kg, additives_kg={"C": required_c})

    registry = sim.species_formula_registry
    caso4_kg = sim.inventory.raw_components_kg["CaSO4"]
    so3_kg = sim.inventory.raw_components_kg["SO3"]
    caso4_mol = caso4_kg / resolve_species_formula(
        "CaSO4", registry).molar_mass_kg_per_mol()
    so3_mol = so3_kg / resolve_species_formula(
        "SO3", registry).molar_mass_kg_per_mol()
    c_molar = resolve_species_formula("C", registry).molar_mass_kg_per_mol()
    co_molar = resolve_species_formula("CO", registry).molar_mass_kg_per_mol()
    so2_molar = resolve_species_formula("SO2", registry).molar_mass_kg_per_mol()
    carbon_mol = required_c / c_molar
    so3_extent = min(carbon_mol, so3_mol)
    carbon_after_so3 = carbon_mol - so3_extent
    caso4_reserved_mol = min(carbon_after_so3, caso4_mol)
    boudouard_mol = carbon_after_so3 - caso4_reserved_mol

    salt_feed = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    assert salt_feed.get("CaSO4", 0.0) == pytest.approx(caso4_kg, rel=0, abs=1e-9)
    assert sim.inventory.cation_sulfate_feed_kg.get("CaSO4", 0.0) == pytest.approx(
        caso4_kg, rel=0, abs=1e-9
    )
    assert sim.record.initial_inventory.cation_sulfate_feed_kg.get("CaSO4", 0.0) == (
        pytest.approx(caso4_kg, rel=0, abs=1e-9)
    )

    ledger_melt = sim.atom_ledger.kg_by_account("process.cleaned_melt")
    # Declared weights sum to 75. Load scales every positive component so
    # the masses sum to the batch: CaO is 5/75 of 1000 kg, not 5% of it.
    declared = mars_caso4_feedstock["composition_wt_pct"]
    declared_total = sum(float(value) for value in declared.values())
    baseline_cao = mass_kg * float(declared["CaO"]) / declared_total
    assert ledger_melt.get("CaO", 0.0) == pytest.approx(baseline_cao, rel=0, abs=1e-6)

    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("SO2", 0.0) == pytest.approx(
        so3_extent * so2_molar, rel=0, abs=1e-6
    )
    assert ledger_offgas.get("CO", 0.0) == pytest.approx(
        so3_extent * co_molar + 2.0 * boudouard_mol * co_molar,
        rel=0,
        abs=1e-6,
    )
    assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(
        caso4_reserved_mol * c_molar, rel=0, abs=1e-9
    )

    assert "CaSO4" not in sim.inventory.salt_phase_kg
    salt_ledger = sim.atom_ledger.kg_by_account("terminal.stage0_salt_phase")
    assert "CaSO4" not in salt_ledger
    assert ledger_offgas.get("CaSO4", 0.0) == pytest.approx(0.0, abs=1e-12)

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


def test_caso4_sulfide_cleanup_is_replaced_by_a_retained_host(
    mars_caso4_sulfide_feedstock,
):
    """The CaSO4 + 4C sulfide credit does not fire. The host stays mutable.

    The carbon that reaction would have consumed is reserved and remains in
    the reagent reservoir. FeSO4's sulfide branch is covered separately.
    """
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
    salt_feed = sim.atom_ledger.kg_by_account("process.stage0_salt_feed")
    assert salt_feed.get("CaSO4", 0.0) == pytest.approx(caso4_kg, rel=0, abs=1e-9)
    assert sim.inventory.cation_sulfate_feed_kg.get("CaSO4", 0.0) == pytest.approx(
        caso4_kg, rel=0, abs=1e-9
    )

    ledger_matte = sim.atom_ledger.kg_by_account("terminal.stage0_sulfide_matte")
    assert ledger_matte.get("CaS", 0.0) == pytest.approx(0.0, abs=1e-12)
    assert ledger_matte.get("CaSO4", 0.0) == pytest.approx(0.0, abs=1e-12)

    ledger_offgas = sim.atom_ledger.kg_by_account("terminal.offgas")
    assert ledger_offgas.get("CO", 0.0) == pytest.approx(0.0, abs=1e-12)
    reagent_c = sim.atom_ledger.kg_by_account("reservoir.reagent.C")
    assert reagent_c.get("C", 0.0) == pytest.approx(exact_c_kg, rel=0, abs=1e-9)
    assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(
        exact_c_kg, rel=0, abs=1e-9
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


def _scarce_sulfate_feed(registry, order: tuple[str, ...]):
    """1 mol CaSO4, 2 mol FeSO4, and 1 mol C. ``order`` is dict insertion."""
    molar = {
        species: resolve_species_formula(species, registry).molar_mass_kg_per_mol()
        for species in ("CaSO4", "FeSO4", "C", "Fe2O3", "SO2", "CO")
    }
    feeds = {"CaSO4": molar["CaSO4"], "FeSO4": 2.0 * molar["FeSO4"]}
    return molar, {species: feeds[species] for species in order}


def test_scarce_carbon_allocates_to_lower_dg_per_mol_c_not_dict_order():
    """1 mol C cannot reduce both 1 CaSO4 and 2 FeSO4.

    At 1050 C the foulant tables give FeSO4 -35 kJ/mol C and CaSO4
    +85.75 kJ/mol C, both extrapolated. FeSO4 takes the mole of C:
    2 FeSO4 + C -> Fe2O3 + 2 SO2 + CO. CaSO4 stays a host and reserves
    nothing. Insertion order must not change that.
    """
    sim = _sim({"feed": {"label": "b-1056 rank"}})
    registry = sim.species_formula_registry
    for order in (("CaSO4", "FeSO4"), ("FeSO4", "CaSO4")):
        molar, cation_feed = _scarce_sulfate_feed(registry, order)
        sim.inventory.cation_sulfate_feed_kg = dict(cation_feed)
        sim.inventory.gas_volatiles_kg = {}
        sim.inventory.stage0_products_kg = {}
        sim.inventory.melt_oxide_kg = {}
        sim.inventory.sulfide_matte_kg = {}
        sim.inventory.stage0_temp_range_C = (20.0, 1050.0)
        sim._stage0_bakeout_reserved_carbon_kg = 0.0
        specs: list[dict] = []
        remaining = sim._apply_stage0_cation_sulfate_carbon_reactions(
            {"stage0_carbon_cleanup": {"cation_sulfate_product": "oxide"}},
            1.0,
            specs,
        )
        assert remaining == pytest.approx(0.0, abs=1e-12)
        assert sim.inventory.melt_oxide_kg.get("Fe2O3", 0.0) == pytest.approx(
            molar["Fe2O3"], rel=0, abs=1e-12
        )
        assert sim.inventory.gas_volatiles_kg.get("SO2", 0.0) == pytest.approx(
            2.0 * molar["SO2"], rel=0, abs=1e-12
        )
        assert sim.inventory.gas_volatiles_kg.get("CO", 0.0) == pytest.approx(
            molar["CO"], rel=0, abs=1e-12
        )
        assert sim.inventory.cation_sulfate_feed_kg.get("CaSO4", 0.0) == (
            pytest.approx(molar["CaSO4"], rel=0, abs=1e-12)
        )
        assert "FeSO4" not in sim.inventory.cation_sulfate_feed_kg
        assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(0.0, abs=1e-12)
        notice = sim._stage0_carbon_allocation_notices[0]
        assert notice["order"] == ["FeSO4", "CaSO4"]
        assert notice["rationed"] is True
        by_species = {claim["species"]: claim for claim in notice["claims"]}
        assert by_species["FeSO4"]["dG_kJ_per_mol_C"] == pytest.approx(-35.0)
        assert by_species["CaSO4"]["dG_kJ_per_mol_C"] == pytest.approx(85.75)
        assert by_species["FeSO4"]["confidence"] == "out_of_domain"
        assert by_species["CaSO4"]["confidence"] == "out_of_domain"
        assert by_species["FeSO4"]["oxygen_couple"] is True
        assert by_species["CaSO4"]["oxygen_couple"] is True
        assert notice["out_of_domain"] is True
        reported = sim.stage0_carbon_allocation_run_notice()
        assert reported["out_of_domain_species"] == ["FeSO4", "CaSO4"]
        assert reported["claims"][0]["basis"] == "FeSO4_thermal_decomp"
        assert reported["claims"][1]["basis"] == "CaSO4_thermal_decomp"


def test_load_batch_scarce_sulfate_carbon_follows_dg_not_set_order():
    """The set-built cation feed used by load_batch follows the same rank."""
    backend = InternalAnalyticalBackend()
    backend.initialize({})
    probe = PyrolysisSimulator(
        backend, {"campaigns": {}}, {"feed": {}}, {"metals": {}, "oxide_vapors": {}}
    )
    registry = probe.species_formula_registry
    molar = {
        species: resolve_species_formula(species, registry).molar_mass_kg_per_mol()
        for species in ("CaSO4", "FeSO4", "C", "Fe2O3", "SO2", "SiO2")
    }
    caso4_kg = molar["CaSO4"]
    feso4_kg = 2.0 * molar["FeSO4"]
    sio2_kg = 10.0
    mass_kg = caso4_kg + feso4_kg + sio2_kg
    carbon_kg = molar["C"]
    feedstock = {
        "label": "b-1056 scarce carbon",
        "stage0_profile": "mars_carbon_cleanup",
        "stage0_temp_range_C": [20.0, 1050.0],
        "stage0_carbon_cleanup": {
            "carbon_reductant_kg_per_tonne": carbon_kg * 1000.0 / mass_kg,
            "reactions": ["sulfate_so3_to_so2_co"],
        },
        "composition_wt_pct": {
            "SiO2": 100.0 * sio2_kg / mass_kg,
            "CaSO4": 100.0 * caso4_kg / mass_kg,
            "FeSO4": 100.0 * feso4_kg / mass_kg,
        },
    }
    sim = PyrolysisSimulator(
        backend,
        {"campaigns": {}},
        {"feed": feedstock},
        {"metals": {}, "oxide_vapors": {}},
    )
    sim.load_batch("feed", mass_kg=mass_kg, additives_kg={"C": carbon_kg})
    assert sim.inventory.melt_oxide_kg.get("Fe2O3", 0.0) == pytest.approx(
        molar["Fe2O3"], rel=0, abs=1e-9
    )
    assert sim.inventory.gas_volatiles_kg.get("SO2", 0.0) == pytest.approx(
        2.0 * molar["SO2"], rel=0, abs=1e-9
    )
    assert sim.inventory.cation_sulfate_feed_kg.get("CaSO4", 0.0) == pytest.approx(
        caso4_kg, rel=0, abs=1e-9
    )
    assert "FeSO4" not in sim.inventory.cation_sulfate_feed_kg
    assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(0.0, abs=1e-9)
    assert sim._stage0_carbon_allocation_notices[0]["order"] == ["FeSO4", "CaSO4"]
    reported = sim.stage0_carbon_allocation_run_notice()
    assert reported["out_of_domain"] is True
    assert reported["out_of_domain_species"] == ["FeSO4", "CaSO4"]
    assert [claim["basis"] for claim in reported["claims"]] == [
        "FeSO4_thermal_decomp",
        "CaSO4_thermal_decomp",
    ]
    from simulator.runner import (
        _attach_stage0_carbon_allocation_notice,
        _product_classification_report,
    )

    metadata: dict = {}
    _attach_stage0_carbon_allocation_notice(metadata, sim)
    assert metadata["stage0_carbon_allocation_notice"]["out_of_domain"] is True
    classification = _product_classification_report(
        sim, feedstock_id="feed", campaign="C0"
    )
    assert classification["stage0_carbon_allocation_notice"]["out_of_domain_species"] == [
        "FeSO4",
        "CaSO4",
    ]
    assert "flagged best estimate" in classification["markdown"]
    assert "FeSO4_thermal_decomp" in classification["markdown"] or (
        "FeSO4" in classification["markdown"]
    )
    snapshot = sim._make_snapshot()
    assert snapshot.mass_balance_error_pct == pytest.approx(0.0, abs=5e-12)


def test_mgso4_thermal_table_is_not_a_carbon_ranking_input():
    registry = load_foulant_registry(FOULANT_THERMO)
    with pytest.raises(FoulantCarbonRankingRefusal, match="MgSO4"):
        foulant_dg_kJ_per_mol_carbon(
            registry.foulant_dG["MgSO4_thermal_decomp"],
            1323.15,
            species="MgSO4",
            carbon_per_mol_feed=1.0,
        )
    with pytest.raises(FoulantCarbonRankingRefusal, match="CaSO4"):
        foulant_dg_kJ_per_mol_carbon(
            registry.foulant_dG["CaSO4_thermal_decomp"],
            1323.15,
            species="CaSO4",
            carbon_per_mol_feed=4.0,
        )


def _cation_masses(registry, species: str, moles: float) -> float:
    return moles * resolve_species_formula(
        species, registry
    ).molar_mass_kg_per_mol()


def test_scarce_mgsO4_refuses_thermal_dg_ranking_and_does_not_allocate():
    """MgSO4 -> MgO + SO3 is not MgSO4 + C -> MgO + SO2 + CO.

    1 mol C, 1 mol MgSO4, 2 mol FeSO4 used to give the carbon to FeSO4
    because the SO3 table's thermal dG ranked below FeSO4. That quotient
    is not the cleanup reaction. The scarce budget is refused.
    """
    sim = _sim({"feed": {"label": "b-1056 mgsO4"}})
    registry = sim.species_formula_registry
    feeds = {
        "MgSO4": _cation_masses(registry, "MgSO4", 1.0),
        "FeSO4": _cation_masses(registry, "FeSO4", 2.0),
    }
    sim.inventory.cation_sulfate_feed_kg = dict(feeds)
    sim.inventory.stage0_temp_range_C = (20.0, 1050.0)
    sim.inventory.melt_oxide_kg = {}
    sim.inventory.gas_volatiles_kg = {}
    with pytest.raises(AccountingError, match="MgSO4"):
        sim._apply_stage0_cation_sulfate_carbon_reactions(
            {"stage0_carbon_cleanup": {"cation_sulfate_product": "oxide"}},
            1.0,
            [],
        )
    assert sim.inventory.cation_sulfate_feed_kg == feeds
    assert sim.inventory.melt_oxide_kg == {}
    assert sim.inventory.gas_volatiles_kg == {}
    notice = sim.stage0_carbon_allocation_run_notice()
    assert notice["reason"] == "stage0_cation_sulfate_carbon_ranking_refused"
    assert notice["refused"][0]["species"] == "MgSO4"
    assert notice["refused"][0]["basis"] == "MgSO4_thermal_decomp"


def test_abundant_mgsO4_still_serves_every_stoichiometric_claim():
    """A covered carbon budget does not need the missing MgSO4 rank."""
    sim = _sim({"feed": {"label": "b-1056 mgsO4 covered"}})
    registry = sim.species_formula_registry
    sim.inventory.cation_sulfate_feed_kg = {
        "MgSO4": _cation_masses(registry, "MgSO4", 1.0),
        "FeSO4": _cation_masses(registry, "FeSO4", 1.0),
    }
    sim.inventory.stage0_temp_range_C = (20.0, 1050.0)
    sim.inventory.melt_oxide_kg = {}
    sim.inventory.gas_volatiles_kg = {}
    sim._stage0_bakeout_reserved_carbon_kg = 0.0
    remaining = sim._apply_stage0_cation_sulfate_carbon_reactions(
        {"stage0_carbon_cleanup": {"cation_sulfate_product": "oxide"}},
        2.0,
        [],
    )
    assert remaining == pytest.approx(0.5, abs=1e-12)
    assert sim.inventory.melt_oxide_kg.get("Fe2O3", 0.0) == pytest.approx(
        _cation_masses(registry, "Fe2O3", 0.5), abs=1e-12
    )
    assert sim.inventory.cation_sulfate_feed_kg.get("MgSO4", 0.0) == pytest.approx(
        _cation_masses(registry, "MgSO4", 1.0), abs=1e-12
    )
    assert "FeSO4" not in sim.inventory.cation_sulfate_feed_kg
    assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(
        _cation_masses(registry, "C", 1.0), abs=1e-12
    )
    assert sim.stage0_carbon_allocation_run_notice() is None


def test_scarce_sulfide_refuses_oxide_table_ranking():
    """Sulfide carbon demand is not the oxide decomposition dG."""
    sim = _sim({"feed": {"label": "b-1056 sulfide"}})
    registry = sim.species_formula_registry
    feeds = {
        "CaSO4": _cation_masses(registry, "CaSO4", 1.0),
        "FeSO4": _cation_masses(registry, "FeSO4", 1.0),
    }
    sim.inventory.cation_sulfate_feed_kg = dict(feeds)
    sim.inventory.stage0_temp_range_C = (20.0, 1050.0)
    sim.inventory.sulfide_matte_kg = {}
    sim.inventory.gas_volatiles_kg = {}
    with pytest.raises(AccountingError, match="sulfide cleanup"):
        sim._apply_stage0_cation_sulfate_carbon_reactions(
            {"stage0_carbon_cleanup": {"cation_sulfate_product": "sulfide"}},
            4.0,
            [],
        )
    assert sim.inventory.cation_sulfate_feed_kg == feeds
    assert sim.inventory.sulfide_matte_kg == {}
    assert sim.inventory.gas_volatiles_kg == {}
    notice = sim.stage0_carbon_allocation_run_notice()
    assert notice["reason"] == "stage0_cation_sulfate_carbon_ranking_refused"
    assert {item["species"] for item in notice["refused"]} == {"CaSO4", "FeSO4"}


def test_abundant_sulfide_still_converts_each_host():
    sim = _sim({"feed": {"label": "b-1056 sulfide covered"}})
    registry = sim.species_formula_registry
    sim.inventory.cation_sulfate_feed_kg = {
        "CaSO4": _cation_masses(registry, "CaSO4", 1.0),
        "FeSO4": _cation_masses(registry, "FeSO4", 1.0),
    }
    sim.inventory.stage0_temp_range_C = (20.0, 1050.0)
    sim.inventory.sulfide_matte_kg = {}
    sim.inventory.gas_volatiles_kg = {}
    sim._stage0_bakeout_reserved_carbon_kg = 0.0
    remaining = sim._apply_stage0_cation_sulfate_carbon_reactions(
        {"stage0_carbon_cleanup": {"cation_sulfate_product": "sulfide"}},
        8.0,
        [],
    )
    assert remaining == pytest.approx(0.0, abs=1e-12)
    assert sim.inventory.sulfide_matte_kg.get("FeS", 0.0) == pytest.approx(
        _cation_masses(registry, "FeS", 1.0), abs=1e-12
    )
    assert "CaS" not in sim.inventory.sulfide_matte_kg
    assert sim.inventory.cation_sulfate_feed_kg.get("CaSO4", 0.0) == pytest.approx(
        _cation_masses(registry, "CaSO4", 1.0), abs=1e-12
    )
    assert sim._stage0_bakeout_reserved_carbon_kg == pytest.approx(
        _cation_masses(registry, "C", 4.0), abs=1e-12
    )
    assert sim.stage0_carbon_allocation_run_notice() is None
