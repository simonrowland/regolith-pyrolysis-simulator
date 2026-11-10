"""Metal-dimer collection under physics ruling d-378.

Na2, K2, and Mg2 have no distinct condensate. M2(g) deposits as 2 M(l/s)
on the monomer's stage, against the monomer's saturation pressure, using
the monomer sticking coefficient as a flagged proxy. Al and Ca have no
condenser collection contract, so Al2 and Ca2 stay a typed refusal and
their mass stays in the flowing stream.
"""

from __future__ import annotations

import pytest

from simulator.accounting.formulas import resolve_species_formula
from simulator.condensation import CondensationModel, _CONFIG_BUNDLE
from simulator.condensation_routing import (
    DESIGNATED_STAGE,
    DIMER_ALPHA_FROM_MONOMER_PROXY,
    K_TRAP_STAGE_KEY,
    METAL_DIMER_SPECIES,
    MISSING_COLLECTION_PRODUCT_CONTRACT_SPECIES,
    NA_COLLECTOR_STAGE_KEY,
    dimer_collection_monomer,
)
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


COLLECTED_DIMERS = ("K2", "Mg2", "Na2")
REFUSED_DIMERS = ("Al2", "Ca2")
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


def _route(species_kg: dict[str, float], *, partials: dict[str, float] | None):
    return _model(partials=partials).route(
        EvaporationFlux(
            species_kg_hr=species_kg,
            total_kg_hr=float(sum(species_kg.values())),
        ),
        MeltState(temperature_C=1500.0),
    )


def _deposited_kg(route, species: str) -> float:
    staged = sum(
        by_species.get(species, 0.0)
        for by_species in route.condensed_by_stage_species.values()
    )
    return staged + route.wall_deposit_by_species.get(species, 0.0)


def _moles(species: str, kg: float) -> float:
    molar_mass_kg = resolve_species_formula(species).molar_mass_g_per_mol() / 1000.0
    return kg / molar_mass_kg


def _mass_closure_kg(route, inlet: dict[str, float]) -> float:
    condensed = sum(
        sum(by_species.values())
        for by_species in route.condensed_by_stage_species.values()
    )
    wall = sum(route.wall_deposit_by_species.values())
    remaining = sum(route.remaining_by_species.values())
    retained = sum(route.retained_in_source_by_species.values())
    return condensed + wall + remaining + retained - sum(inlet.values())


def test_only_monomers_without_a_contract_stay_refused():
    assert set(METAL_DIMER_SPECIES) == set(COLLECTED_DIMERS + REFUSED_DIMERS)
    assert set(MISSING_COLLECTION_PRODUCT_CONTRACT_SPECIES) == set(REFUSED_DIMERS)
    assert dimer_collection_monomer("Na2") == "Na"
    assert dimer_collection_monomer("K2") == "K"
    assert dimer_collection_monomer("Mg2") == "Mg"
    assert dimer_collection_monomer("Al2") is None
    assert dimer_collection_monomer("Ca2") is None
    assert DESIGNATED_STAGE["Na"] == NA_COLLECTOR_STAGE_KEY
    assert DESIGNATED_STAGE["Mg"] == NA_COLLECTOR_STAGE_KEY
    assert DESIGNATED_STAGE["K"] == K_TRAP_STAGE_KEY
    for species in REFUSED_DIMERS:
        assert species not in DESIGNATED_STAGE


@pytest.mark.parametrize("dimer", COLLECTED_DIMERS)
def test_dimer_credits_two_monomer_moles_and_closes_mass(dimer):
    """1 kg of M2 becomes monomer condensate. Atoms: 2 mol M per mol M2."""

    monomer = dimer[:-1]
    inlet = {dimer: 1.0}
    route = _route(inlet, partials={dimer: 0.1})

    deposited_monomer = _deposited_kg(route, monomer)
    deposited_dimer = _deposited_kg(route, dimer)
    remaining_dimer = route.remaining_by_species[dimer]
    captured_dimer = 1.0 - remaining_dimer

    assert deposited_dimer == 0.0
    assert deposited_monomer > 0.0
    assert captured_dimer == pytest.approx(deposited_monomer)
    assert _moles(monomer, deposited_monomer) == pytest.approx(
        2.0 * _moles(dimer, captured_dimer)
    )
    assert _mass_closure_kg(route, inlet) == pytest.approx(0.0, abs=0.0)
    assert route.retained_in_source_by_species.get(dimer, 0.0) == 0.0
    assert route.condensation_refusals_by_species.get(dimer, {}).get("reason") != (
        CONTRACT_REASON
    )
    assert dimer not in {
        landed
        for by_species in route.condensed_by_stage_species.values()
        for landed in by_species
    }
    assert dimer not in route.wall_deposit_by_species

    authority = route.condensation_authority_by_species[dimer]
    assert authority["mass_closure_error_kg_hr"] == pytest.approx(0.0, abs=0.0)

    notice = route.sticking_alpha_provenance_notice
    assert notice[DIMER_ALPHA_FROM_MONOMER_PROXY][dimer] == monomer
    assert notice["authoritative_for_deposit_mass"] is False


def test_monomer_present_with_its_dimer_keeps_both_masses():
    """The monomer's own deposit is booked before the dimer credit is added."""

    inlet = {"Na": 1.0, "Na2": 1.0}
    route = _route(inlet, partials={"Na": 0.1, "Na2": 0.1})
    captured_dimer = 1.0 - route.remaining_by_species["Na2"]
    deposited_na = _deposited_kg(route, "Na")

    assert captured_dimer > 0.0
    assert _deposited_kg(route, "Na2") == 0.0
    # Overwriting the monomer account with the credit would leave only the
    # dimer mass. The monomer's own capture at this partial is larger.
    assert deposited_na > captured_dimer
    assert _moles("Na", deposited_na) > 2.0 * _moles("Na2", captured_dimer)
    assert _mass_closure_kg(route, inlet) == pytest.approx(0.0, abs=0.0)
    for species in inlet:
        assert route.condensation_authority_by_species[species][
            "mass_closure_error_kg_hr"
        ] == pytest.approx(0.0, abs=0.0)
    assert route.sticking_alpha_provenance_notice[
        DIMER_ALPHA_FROM_MONOMER_PROXY
    ]["Na2"] == "Na"


def test_potassium_dimer_does_not_land_on_the_sodium_collector():
    route = _route({"K2": 1.0}, partials={"K2": 0.1})
    sodium_stage = route.condensed_by_stage_species.get(NA_COLLECTOR_STAGE_KEY, {})
    assert sodium_stage.get("K", 0.0) == 0.0
    assert _deposited_kg(route, "K") > 0.0


def test_refused_dimers_keep_missing_collection_product_and_their_mass():
    species_kg = {species: 1.0 for species in REFUSED_DIMERS}
    species_kg["Na"] = 1.0
    route = _route(
        species_kg,
        partials={species: 0.1 for species in species_kg},
    )

    assert _deposited_kg(route, "Na") > 0.0
    assert route.condensation_refusals_by_species.get("Na", {}).get("reason") != (
        CONTRACT_REASON
    )
    for species in REFUSED_DIMERS:
        assert _deposited_kg(route, species) == 0.0
        assert route.remaining_by_species[species] == pytest.approx(1.0)
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
    assert _mass_closure_kg(route, species_kg) == pytest.approx(0.0, abs=0.0)


def test_missing_dimer_partial_pressure_refuses_that_quantity_only():
    """A positive dimer with no supplied partial pressure must not abort the train.

    Collected dimers (Na2, K2, Mg2) refuse the missing pressure and keep
    their mass in the stream. Al2 and Ca2 still refuse the missing
    collection product. Na, which has a partial and a stage, still routes.
    """

    dimers = COLLECTED_DIMERS + REFUSED_DIMERS
    species_kg = {species: 1.0 for species in dimers}
    species_kg["Na"] = 1.0
    route = _route(species_kg, partials={"Na": 0.1})
    na_only = _route({"Na": 1.0}, partials={"Na": 0.1})

    assert _deposited_kg(route, "Na") == pytest.approx(_deposited_kg(na_only, "Na"))
    assert _deposited_kg(route, "Na") > 0.0

    for species in COLLECTED_DIMERS:
        assert _deposited_kg(route, species) == 0.0
        assert _deposited_kg(route, species[:-1]) == (
            pytest.approx(_deposited_kg(na_only, "Na")) if species == "Na2" else 0.0
        )
        assert route.remaining_by_species[species] == pytest.approx(1.0)
        refusal = route.condensation_refusals_by_species[species]
        assert refusal["reason"] != CONTRACT_REASON
        assert any(
            item.get("parameter") == "flowing_pressure_pa"
            and item.get("status") == "refused"
            for item in refusal.get("stage_outcomes", ())
        )
        assert route.retained_in_source_by_species.get(species, 0.0) == 0.0

    for species in REFUSED_DIMERS:
        assert route.remaining_by_species[species] == pytest.approx(1.0)
        refusal = route.condensation_refusals_by_species[species]
        assert refusal["reason"] == CONTRACT_REASON
        assert refusal["collection_product_kg_hr"] is None

    assert _mass_closure_kg(route, species_kg) == pytest.approx(0.0, abs=0.0)
