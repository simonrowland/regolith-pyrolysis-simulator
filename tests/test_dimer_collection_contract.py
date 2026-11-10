"""Dimer carriers have formation rows and no collection-product contract.

Na2, K2, Mg2, Al2, and Ca2 evaporate. Their catalog rows do not supply a
wall saturation curve, a stable collected phase, an accommodation
coefficient, or kinetics. Crediting them on the monomer stage would invent
a two-atom product. The collection quantity is refused and the vapor mass
stays in the uncollected stream.
"""

from __future__ import annotations

import pytest

from simulator.condensation import CondensationModel, _CONFIG_BUNDLE
from simulator.condensation_routing import (
    DESIGNATED_STAGE,
    MISSING_COLLECTION_PRODUCT_CONTRACT_SPECIES,
    NA_COLLECTOR_STAGE_KEY,
)
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


DIMER_CARRIERS = ("Al2", "Ca2", "K2", "Mg2", "Na2")
CONTRACT_REASON = "missing_collection_product_contract"


def _model(*, partials: dict[str, float] | None) -> CondensationModel:
    # Production route() sees the schema-v2 catalogue. The legacy view
    # alone reports these dimers as antoine_data_unavailable and withholds
    # the evaporation debit; that is not the live collection path.
    model = CondensationModel(
        CondensationTrain.create_default(),
        vapor_pressure_data=_CONFIG_BUNDLE.vapor_pressures,
    )
    kwargs = {
        "overhead_pressure_mbar": 1.0,
        "stage_area_m2_by_stage": {
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    }
    if partials is not None:
        kwargs["species_partial_pressures_mbar"] = partials
    model.configure_operating_conditions(**kwargs)
    return model


def _deposited_kg(route, species: str) -> float:
    staged = sum(
        by_species.get(species, 0.0)
        for by_species in route.condensed_by_stage_species.values()
    )
    return staged + route.wall_deposit_by_species.get(species, 0.0)


def test_dimer_carriers_are_not_designated_monomer_products():
    assert set(MISSING_COLLECTION_PRODUCT_CONTRACT_SPECIES) == set(DIMER_CARRIERS)
    for species in DIMER_CARRIERS:
        assert species not in DESIGNATED_STAGE
    assert DESIGNATED_STAGE["Na"] == NA_COLLECTOR_STAGE_KEY
    assert DESIGNATED_STAGE["K"] != DESIGNATED_STAGE["Na"]
    assert DESIGNATED_STAGE["Mg"] == NA_COLLECTOR_STAGE_KEY


def test_positive_dimer_mass_refuses_collection_and_keeps_vapor():
    species_kg = {species: 1.0 for species in DIMER_CARRIERS}
    species_kg["Na"] = 1.0
    route = _model(partials={species: 0.1 for species in species_kg}).route(
        EvaporationFlux(species_kg_hr=species_kg, total_kg_hr=float(len(species_kg))),
        MeltState(temperature_C=1500.0),
    )

    na_deposited = _deposited_kg(route, "Na")
    assert na_deposited > 0.0
    assert route.condensation_refusals_by_species.get("Na", {}).get("reason") != (
        CONTRACT_REASON
    )

    for species in DIMER_CARRIERS:
        assert _deposited_kg(route, species) == 0.0
        assert route.remaining_by_species[species] == pytest.approx(1.0)
        assert species not in {
            landed
            for by_species in route.impurity_by_stage_species.values()
            for landed in by_species
        }
        refusal = route.condensation_refusals_by_species[species]
        assert refusal["reason"] == CONTRACT_REASON
        assert refusal["status"] == "refused"
        assert refusal["severity"] == "warning"
        assert refusal["collection_product_kg_hr"] is None
        assert refusal["remaining_mass_kg_hr"] == pytest.approx(1.0)
        assert refusal["mass_disposition"] == (
            "uncollected_vapor_missing_collection_product_contract"
        )
        assert route.retained_in_source_by_species.get(species, 0.0) == 0.0
