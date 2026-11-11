"""d-378 amendment 1: dimer wall capture is a wall deposit, not collector product.

A cold upstream wall removes Na2. The projection already credits that mass
as Na on the wall segment. The ledger handoff is wall_deposit_fraction_by_species
on the gas species. At 0db2363df that fraction stays 0, so
_dispatch_condensation_route commits the whole removal to
process.condensation_train.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from simulator.accounting.formulas import resolve_species_formula
from simulator.state import EvaporationFlux, MeltState

_CONFTEST = Path(__file__).resolve().parent / "conftest.py"
_spec = importlib.util.spec_from_file_location(
    "dimer_wall_ledger_conftest", _CONFTEST
)
_conftest = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_conftest)
_build_sim = _conftest._build_sim

_COLD_SEGMENT = "stage_3_to_stage_4"


@pytest.fixture(scope="module")
def loaded_inputs(vapor_pressure_data, feedstocks_data, setpoints_data):
    return vapor_pressure_data, feedstocks_data, setpoints_data


def test_cold_upstream_na2_wall_books_monomer_not_collector(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Na2 captured on a 400 C upstream wall lands as Na on that wall account."""

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    model = sim.condensation_model
    segment_temperatures = {
        segment.name: 1500.0 for segment in model.pipe_segments
    }
    segment_temperatures[_COLD_SEGMENT] = 400.0
    model.configure_operating_conditions(
        wall_temperature_C=1500.0,
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"Na2": 0.5},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        pipe_segment_temperatures_C=segment_temperatures,
    )
    inlet_kg = 1.0
    route = model.route(
        EvaporationFlux(species_kg_hr={"Na2": inlet_kg}, total_kg_hr=inlet_kg),
        MeltState(temperature_C=1500.0),
    )
    cold = next(
        segment for segment in model.pipe_segments if segment.name == _COLD_SEGMENT
    )
    wall_account = cold.wall_deposit_account
    projected_wall_na = route.wall_deposit_by_segment_species[_COLD_SEGMENT]["Na"]
    assert projected_wall_na > 0.0

    removed_kg = inlet_kg - route.remaining_by_species["Na2"]
    # Inlet-form wall: captured = min(T, supply). At 400 C this 1 m2
    # duct's transport exceeds 1 kg/h, so the whole parcel is a wall
    # deposit and the collector stage receives none. The strict
    # remainder was the previous under-capture. The destination is
    # still the wall account, not process.condensation_train.
    assert removed_kg == pytest.approx(projected_wall_na, rel=0.0, abs=0.0)
    assert route.condensed_by_stage_species == {}
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {"Na2": removed_kg},
        source="d-378 dimer wall deposit pin",
        material_origin="feedstock",
    )
    credited_baffle_kg = sim._dispatch_condensation_route(
        "Na2",
        removed_kg,
        {"condensation_products_mol_per_mol_vapor": {"Na": 2.0}},
        route,
    )

    wall_kg = sim.atom_ledger.kg_by_account(wall_account)
    train_kg = sim.atom_ledger.kg_by_account("process.condensation_train")
    overhead = sim.atom_ledger.kg_by_account("process.overhead_gas")

    assert wall_kg.get("Na2", 0.0) == 0.0
    assert train_kg.get("Na2", 0.0) == 0.0
    assert overhead.get("Na2", 0.0) == pytest.approx(0.0, abs=0.0)
    wall_na = wall_kg["Na"]
    train_na = train_kg["Na"]
    assert wall_na == pytest.approx(projected_wall_na, rel=0.0, abs=1e-12)
    assert train_na == pytest.approx(credited_baffle_kg, rel=0.0, abs=1e-12)
    assert train_na == pytest.approx(removed_kg - wall_na, rel=0.0, abs=1e-12)
    assert credited_baffle_kg < removed_kg

    na2_mol = removed_kg / (
        resolve_species_formula("Na2", sim.species_formula_registry)
        .molar_mass_kg_per_mol()
    )
    na_mol = (wall_na + train_na) / (
        resolve_species_formula("Na", sim.species_formula_registry)
        .molar_mass_kg_per_mol()
    )
    assert na_mol == pytest.approx(2.0 * na2_mol, rel=0.0, abs=1e-12)


@pytest.mark.parametrize(
    "monomer,dimer",
    (("Na", "Na2"), ("K", "K2"), ("Mg", "Mg2")),
)
def test_mixed_feed_wall_ledger_books_one_element_budget(
    monkeypatch,
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
    monomer,
    dimer,
):
    """A transport-limited wall debits the element once, then the ledger keeps it.

    Both supplies are 4 T. The old per-arrival cap booked min(m, T) + min(d, T)
    = 2 T onto the wall account. The handoff is the same
    _dispatch_condensation_route path as a single dimer feed. The expected
    kilograms are (J_M + 2 J_M2) * A * M from the contract-test arithmetic,
    not the route's own output.
    """

    from tests.test_dimer_collection_contract import (
        _BUDGET_KP_PA,
        _budget_transport_kg_hr,
        _install_budget_physics,
    )

    _install_budget_physics(monkeypatch, _BUDGET_KP_PA)
    transport = _budget_transport_kg_hr(monomer, _BUDGET_KP_PA)
    monomer_rate = transport * 4.0
    dimer_rate = transport * 4.0
    partials_mbar = {monomer: 1.5 / 100.0, dimer: 2.25 / 100.0}
    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    model = sim.condensation_model
    temperatures = {segment.name: 1500.0 for segment in model.pipe_segments}
    temperatures[_COLD_SEGMENT] = 250.0
    model.configure_operating_conditions(
        wall_temperature_C=1500.0,
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar=partials_mbar,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        pipe_segment_temperatures_C=temperatures,
    )
    model.stage_area_m2_by_stage = {
        key: 0.0 for key in model.stage_area_m2_by_stage
    }
    inlet = {monomer: monomer_rate, dimer: dimer_rate}
    route = model.route(
        EvaporationFlux(
            species_kg_hr=inlet,
            total_kg_hr=monomer_rate + dimer_rate,
        ),
        MeltState(temperature_C=1500.0),
    )
    removed_monomer = monomer_rate - route.remaining_by_species[monomer]
    removed_dimer = dimer_rate - route.remaining_by_species[dimer]
    assert removed_monomer + removed_dimer == pytest.approx(
        transport, rel=1e-12, abs=1e-12,
    )
    assert removed_monomer + removed_dimer != pytest.approx(2.0 * transport)
    for species in inlet:
        assert route.wall_deposit_fraction_by_species[species] == pytest.approx(1.0)

    cold = next(
        segment for segment in model.pipe_segments if segment.name == _COLD_SEGMENT
    )
    wall_account = cold.wall_deposit_account
    sim.atom_ledger.load_external(
        "process.overhead_gas",
        {monomer: removed_monomer, dimer: removed_dimer},
        source="b-325 mixed element wall budget",
        material_origin="feedstock",
    )
    sim._dispatch_condensation_route(monomer, removed_monomer, {}, route)
    sim._dispatch_condensation_route(
        dimer,
        removed_dimer,
        {"condensation_products_mol_per_mol_vapor": {monomer: 2.0}},
        route,
    )

    wall_kg = sim.atom_ledger.kg_by_account(wall_account)
    train_kg = sim.atom_ledger.kg_by_account("process.condensation_train")
    overhead = sim.atom_ledger.kg_by_account("process.overhead_gas")
    assert wall_kg.get(dimer, 0.0) == 0.0
    assert train_kg.get(dimer, 0.0) == 0.0
    assert train_kg.get(monomer, 0.0) == pytest.approx(0.0, abs=1e-12)
    assert overhead.get(monomer, 0.0) == pytest.approx(0.0, abs=0.0)
    assert overhead.get(dimer, 0.0) == pytest.approx(0.0, abs=0.0)
    assert wall_kg[monomer] == pytest.approx(transport, rel=1e-12, abs=1e-12)

    monomer_mol = wall_kg[monomer] / (
        resolve_species_formula(monomer, sim.species_formula_registry)
        .molar_mass_kg_per_mol()
    )
    vapor_mol = removed_monomer / (
        resolve_species_formula(monomer, sim.species_formula_registry)
        .molar_mass_kg_per_mol()
    ) + 2.0 * removed_dimer / (
        resolve_species_formula(dimer, sim.species_formula_registry)
        .molar_mass_kg_per_mol()
    )
    assert monomer_mol == pytest.approx(vapor_mol, rel=0.0, abs=1e-12)


def test_mg2_dust_sio2_subfloor_rollback_books_monomer(
    vapor_pressure_data,
    feedstocks_data,
    setpoints_data,
):
    """Above-floor Mg2 on dust SiO2 rolls back to Mg, never the gas.

    The silica split's Si credit sits in (0, 1e-12] kg while the Mg2
    parcel is above the floor. The rollback used to replace the monomer
    plan with the arriving gas, so the wall account booked Mg2. It now
    keeps the monomer element. The dust SiO2 stays, and no account holds
    a dimer product.
    """

    from simulator.accounting.lots import EMPTY_KG_TOLERANCE
    from simulator.chemistry.kernel import ChemistryIntent

    sim = _build_sim(
        "lunar_mare_low_ti",
        vapor_pressure_data,
        feedstocks_data,
        setpoints_data,
    )
    registry = sim.species_formula_registry
    wall = "process.wall_deposit"

    def molar_mass(name: str) -> float:
        return resolve_species_formula(name, registry).molar_mass_kg_per_mol()

    m_si = molar_mass("Si")
    m_sio2 = molar_mass("SiO2")
    m_mg2 = molar_mass("Mg2")
    floor = EMPTY_KG_TOLERANCE
    # Si credit kg = dust_kg * M_Si / M_SiO2 when the dust limits the
    # reaction. The dust inventory itself stays above the floor.
    max_dust_kg = floor * m_sio2 / m_si
    dust_kg = 0.5 * (floor + max_dust_kg)
    sio2_mol = dust_kg / m_sio2
    si_credit_kg = sio2_mol * m_si
    assert floor < dust_kg < max_dust_kg
    assert 0.0 < si_credit_kg <= floor

    arrival_kg = 1.0e-6
    arrival_mol = arrival_kg / m_mg2
    sim.atom_ledger.load_external_mol(
        wall,
        {"SiO2": sio2_mol},
        source="b-325 dust SiO2",
        material_origin="feedstock",
    )
    sim.atom_ledger.load_external_mol(
        "process.overhead_gas",
        {"Mg2": arrival_mol},
        source="b-325 Mg2 wall arrival",
        material_origin="feedstock",
    )
    watched = (
        "process.overhead_gas",
        wall,
        "process.condensation_train",
        "process.condensation_retained_holdup",
    )

    def element_mol(element: str) -> float:
        return sum(
            sim.atom_ledger.atom_moles_by_account(account).get(element, 0.0)
            for account in watched
        )

    mg_before = element_mol("Mg")
    si_before = element_mol("Si")
    o_before = element_mol("O")

    result = sim._chem_kernel.dispatch(
        ChemistryIntent.CONDENSATION_ROUTE,
        temperature_C=1100.0,
        pressure_bar=1e-6,
        control_inputs={
            "species": "Mg2",
            "condensed_kg": arrival_kg,
            "sp_data": {
                "condensation_products_mol_per_mol_vapor": {"Mg": 2.0},
            },
            "wall_deposit_fraction": 1.0,
            "wall_deposit_account_fractions": {wall: 1.0},
            "dt_hr": 1.0,
        },
    )
    assert result.status == "ok"
    assert result.transition is not None
    diagnostic = result.diagnostic["wall_reaction_diagnostics_by_account"][wall]
    assert diagnostic["materialization_adjustment"] == (
        "rollback_coupled_reaction_to_unchanged_arrival"
    )
    pre_si_mol = float(diagnostic["pre_adjustment_products_mol"]["Si"])
    assert 0.0 < pre_si_mol * m_si <= floor
    assert set(result.transition.credits[wall]) == {"Mg"}
    assert result.transition.credits[wall]["Mg"] == pytest.approx(
        2.0 * arrival_mol, rel=0.0, abs=0.0
    )
    for species_mol in result.transition.credits.values():
        assert "Mg2" not in species_mol
        assert "Na2" not in species_mol
        assert "K2" not in species_mol
    assert result.transition.debits["process.overhead_gas"]["Mg2"] == pytest.approx(
        arrival_mol, rel=0.0, abs=0.0
    )
    assert wall not in result.transition.debits

    committed = sim._chem_kernel.commit_batch(
        ChemistryIntent.CONDENSATION_ROUTE, result.transition
    )
    assert committed.debit_mass_kg(registry) == pytest.approx(
        arrival_kg, rel=0.0, abs=1e-18
    )
    assert committed.credit_mass_kg(registry) == pytest.approx(
        arrival_kg, rel=0.0, abs=1e-18
    )

    by_account = sim.atom_ledger.kg_by_account()
    for species_kg in by_account.values():
        assert species_kg.get("Mg2", 0.0) == 0.0
        assert species_kg.get("Na2", 0.0) == 0.0
        assert species_kg.get("K2", 0.0) == 0.0
    wall_kg = by_account[wall]
    assert wall_kg["Mg"] == pytest.approx(arrival_kg, rel=0.0, abs=0.0)
    assert wall_kg["SiO2"] == pytest.approx(dust_kg, rel=0.0, abs=0.0)
    assert wall_kg.get("MgO", 0.0) == 0.0
    assert wall_kg.get("Si", 0.0) == 0.0
    assert element_mol("Mg") == pytest.approx(mg_before, rel=0.0, abs=1e-12)
    assert element_mol("Si") == pytest.approx(si_before, rel=0.0, abs=1e-18)
    assert element_mol("O") == pytest.approx(o_before, rel=0.0, abs=1e-18)
    assert mg_before == pytest.approx(2.0 * arrival_mol, rel=0.0, abs=1e-12)
