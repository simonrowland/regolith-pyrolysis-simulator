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
    assert removed_kg > projected_wall_na
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
