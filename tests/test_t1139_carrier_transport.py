"""Live vapour carriers keep a molar mass through production transport.

The species-catalog registry is the formula owner. Compiled vapour
declarations that the catalog does not already name must join that same
registry, or overhead transport drops their flux before routing and the
ledger ever see it.
"""

from __future__ import annotations

import pytest

from simulator.accounting.formulas import parse_formula
from simulator.overhead import OverheadGasModel, _mean_molar_mass_kg_mol
from simulator.state import EvaporationFlux
from tests.chemistry.conftest import _build_sim, _load_yaml


def _live_generated_carriers(catalog_payload: dict) -> list[str]:
    carriers: list[str] = []
    for family in catalog_payload["families"].values():
        species_rows = family["physical_properties"]["species"]
        for species_id, row in species_rows.items():
            if (
                row.get("chemical_family") == "t1139_generated_carrier"
                and not row.get("flux_dormant")
            ):
                carriers.append(str(species_id))
    return carriers


def test_every_live_generated_carrier_joins_the_formula_registry():
    sim = _sim()
    live = _live_generated_carriers(sim.vapor_pressure_catalog_data)
    assert len(live) == 25
    missing = [species for species in live if species not in sim.species_formula_registry]
    assert missing == []
    geo = sim.species_formula_registry["GeO"]
    assert geo.molar_mass_g_mol == pytest.approx(
        parse_formula("GeO").molar_mass_g_per_mol()
    )
    # Cs2 (dicesium) and CS2 (carbon disulfide) are different species.
    assert dict(sim.species_formula_registry["Cs2"].elements) == {"Cs": 2.0}
    assert dict(sim.species_formula_registry["CS2"].elements) == {"C": 1.0, "S": 2.0}
    # VR-3 collision ids stay out of the live registry.
    assert "P2O5_gas" not in sim.species_formula_registry


def test_mixed_geo_flow_is_transported_routed_and_closed():
    sim = _sim()
    sim.atom_ledger.load_external_mol(
        "process.cleaned_melt",
        {"SiO2": 1.0, "GeO2": 1.0, "Cs2O": 1.0},
        source="t1139 mixed carrier seed",
        material_origin="feedstock",
    )
    species_kg_hr = {"SiO": 0.004, "GeO": 0.002, "Cs": 0.001}
    flux = EvaporationFlux(species_kg_hr=dict(species_kg_hr))
    flux.update_totals()

    vapor_pressure_mbar = 1.0
    partials = OverheadGasModel.species_partial_pressures(
        flux,
        vapor_pressure_mbar,
        sim.species_formula_registry,
    )
    assert set(partials) == set(species_kg_hr)
    assert sum(partials.values()) == pytest.approx(vapor_pressure_mbar)
    total_mol = sum(
        kg_hr / parse_formula(species).molar_mass_kg_per_mol()
        for species, kg_hr in species_kg_hr.items()
    )
    assert partials["GeO"] == pytest.approx(
        (species_kg_hr["GeO"] / parse_formula("GeO").molar_mass_kg_per_mol())
        / total_mol
        * vapor_pressure_mbar
    )
    mean_kg_mol = _mean_molar_mass_kg_mol(
        species_kg_hr,
        species_formula_registry=sim.species_formula_registry,
    )
    assert mean_kg_mol == pytest.approx(sum(species_kg_hr.values()) / total_mol)

    sim._route_to_condensation(flux)
    routed_partials = sim.condensation_model.species_partial_pressures_mbar
    assert routed_partials["GeO"] > 0.0
    assert "SiO" in routed_partials
    assert "Cs" in routed_partials
    committed = sim._ledger_committed_evap_flux_this_tick
    assert committed.species_kg_hr["GeO"] == pytest.approx(species_kg_hr["GeO"])
    assert committed.species_kg_hr["SiO"] == pytest.approx(species_kg_hr["SiO"])
    assert committed.species_kg_hr["Cs"] == pytest.approx(species_kg_hr["Cs"])

    accounts = sim.atom_ledger.mol_by_account()
    geo_mol = (
        accounts.get("process.overhead_gas", {}).get("GeO", 0.0)
        + accounts.get("process.condensation_train", {}).get("GeO", 0.0)
    )
    assert geo_mol == pytest.approx(
        species_kg_hr["GeO"] / parse_formula("GeO").molar_mass_kg_per_mol()
    )
    report = sim.atom_ledger.element_atom_drift_report()
    for surface in (
        "accepted_transition_residual_mol_atoms",
        "whole_run_boundary_residual_mol_atoms",
    ):
        for element, residual in report[surface].items():
            assert residual == pytest.approx(0.0, abs=1e-9), (surface, element, residual)


def _sim():
    return _build_sim(
        "lunar_mare_low_ti",
        _load_yaml("vapor_pressures.yaml"),
        _load_yaml("feedstocks.yaml"),
        _load_yaml("setpoints.yaml"),
    )
