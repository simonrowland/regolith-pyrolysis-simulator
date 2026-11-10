"""Metal-dimer collection under physics ruling d-378.

Na2, K2, and Mg2 have no distinct condensate. M2(g) deposits as 2 M(l/s)
on the monomer's stage, against the monomer's saturation pressure, using
the monomer sticking coefficient as a flagged proxy. Al and Ca have no
condenser collection contract, so Al2 and Ca2 stay a typed refusal and
their mass stays in the flowing stream.
"""

from __future__ import annotations

import math

import pytest

from simulator.accounting.formulas import resolve_species_formula
from simulator.condensation import (
    CondensationModel,
    _CONFIG_BUNDLE,
    _try_antoine_psat_pa,
    dimer_dissociation_kp_pa,
)
from simulator.condensation_routing import (
    DESIGNATED_STAGE,
    DIMER_ALPHA_FROM_MONOMER_PROXY,
    DIMER_SATURATION_UPPER_BOUND_NO_KP,
    K_TRAP_STAGE_KEY,
    METAL_DIMER_SPECIES,
    MISSING_COLLECTION_PRODUCT_CONTRACT_SPECIES,
    NA_COLLECTOR_STAGE_KEY,
    dimer_collection_monomer,
    element_partial_pa,
    respeciated_monomer_partial_pa,
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
    # The shared stage bucket sums the monomer deposit and the dimer credit
    # to the inlet within one ulp.
    assert _mass_closure_kg(route, inlet) == pytest.approx(0.0, abs=1.0e-15)
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


def test_respeciated_monomer_partial_obeys_the_quadratic_and_both_limits():
    """d-378 amendment 1 closed form. K_p -> inf is p_elem; K_p -> 0 is 0."""

    p_elem = 20.0
    kp = 11268.6
    partial = respeciated_monomer_partial_pa(p_elem, kp)
    assert partial + 2.0 * partial * partial / kp == pytest.approx(p_elem)
    direct = (kp / 4.0) * (math.sqrt(1.0 + 8.0 * p_elem / kp) - 1.0)
    assert partial == pytest.approx(direct)

    assert respeciated_monomer_partial_pa(p_elem, math.inf) == pytest.approx(p_elem)
    assert respeciated_monomer_partial_pa(p_elem, 1.0e20) == pytest.approx(
        p_elem, rel=1.0e-9
    )
    assert respeciated_monomer_partial_pa(p_elem, 0.0) == 0.0
    tiny = 1.0e-20
    assert respeciated_monomer_partial_pa(p_elem, tiny) == pytest.approx(
        math.sqrt(tiny * p_elem / 2.0), rel=1.0e-6
    )
    assert element_partial_pa(3.0, 4.0) == pytest.approx(11.0)


def test_stable_dimer_below_monomer_saturation_passes_through(monkeypatch):
    """p_M2 > P_sat,M but the re-speciated p_M is below it, so nothing captures.

    Downstream cold bands are moved to 1500 C so the only cold surface is
    the isothermal Na collector. The monkeypatched K_p is the stable-dimer
    side of amendment 1: real Na2 thermo is dissociated and cannot stage
    p_M < P_sat < p_M2.
    """

    psat_pa, refused = _try_antoine_psat_pa(
        "Na",
        250.0 + 273.15,
        vapor_pressure_data=_CONFIG_BUNDLE.vapor_pressures,
    )
    assert refused is False
    assert psat_pa is not None and psat_pa > 0.0
    dimer_partial_pa = 10.0
    assert dimer_partial_pa > psat_pa
    kp_pa = 1.0e-25
    p_monomer = respeciated_monomer_partial_pa(
        element_partial_pa(0.0, dimer_partial_pa), kp_pa,
    )
    assert 0.0 < p_monomer < psat_pa

    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa",
        lambda *_args, **_kwargs: kp_pa,
    )
    model = _model(partials={"Na2": dimer_partial_pa / 100.0})
    for stage in model.train.stages:
        if stage.stage_number >= 5:
            stage.temp_range_C = (1500.0, 1500.0)
    route = model.route(
        EvaporationFlux(species_kg_hr={"Na2": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1500.0),
    )
    assert route.remaining_by_species["Na2"] == pytest.approx(1.0, abs=1.0e-12)
    assert _deposited_kg(route, "Na") == pytest.approx(0.0, abs=0.0)
    assert _mass_closure_kg(route, {"Na2": 1.0}) == pytest.approx(0.0, abs=0.0)
    assert DIMER_SATURATION_UPPER_BOUND_NO_KP not in (
        route.sticking_alpha_provenance_notice
    )


def test_missing_kp_flags_the_element_partial_upper_bound(monkeypatch):
    """No K_p: compare p_elem with P_sat,M and flag it. Not a silent zero.

    At 480 C, p_M2 is below P_sat,Na and p_elem is above it, so the old
    p_M2 comparison would not foul this wall. The flagged bound does.
    """

    wall_T_K = 480.0 + 273.15
    psat_pa, refused = _try_antoine_psat_pa(
        "Na",
        wall_T_K,
        vapor_pressure_data=_CONFIG_BUNDLE.vapor_pressures,
    )
    assert refused is False and psat_pa is not None
    dimer_partial_pa = 50.0
    p_elem = element_partial_pa(0.0, dimer_partial_pa)
    assert dimer_partial_pa < psat_pa < p_elem
    # The landed exchange is available. The patch below is the missing-K_p seam.
    assert dimer_dissociation_kp_pa(
        "Na2", wall_T_K, _CONFIG_BUNDLE.vapor_pressures,
    ) is not None

    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa",
        lambda *_args, **_kwargs: None,
    )
    model = _model(partials={"Na2": dimer_partial_pa / 100.0})
    temperatures = {segment.name: 1500.0 for segment in model.pipe_segments}
    temperatures["stage_3_to_stage_4"] = 480.0
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        species_partial_pressures_mbar={"Na2": dimer_partial_pa / 100.0},
        pipe_segment_temperatures_C=temperatures,
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={"Na2": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1500.0),
    )
    assert route.wall_deposit_by_species.get("Na", 0.0) > 0.0
    assert route.wall_deposit_fraction_by_species["Na2"] > 0.0
    notice = route.sticking_alpha_provenance_notice
    assert notice[DIMER_SATURATION_UPPER_BOUND_NO_KP]["Na2"] == (
        DIMER_SATURATION_UPPER_BOUND_NO_KP
    )
    assert _mass_closure_kg(route, {"Na2": 1.0}) == pytest.approx(0.0, abs=0.0)


def test_landed_kp_does_not_raise_the_no_kp_bound():
    """A real Na2 K_p at 480 C supersaturates without the upper-bound flag."""

    model = _model(partials={"Na2": 0.5})
    temperatures = {segment.name: 1500.0 for segment in model.pipe_segments}
    temperatures["stage_3_to_stage_4"] = 480.0
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        species_partial_pressures_mbar={"Na2": 0.5},
        pipe_segment_temperatures_C=temperatures,
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={"Na2": 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1500.0),
    )
    assert route.wall_deposit_by_species.get("Na", 0.0) > 0.0
    assert DIMER_SATURATION_UPPER_BOUND_NO_KP not in (
        route.sticking_alpha_provenance_notice
    )
    assert _moles("Na", _deposited_kg(route, "Na")) == pytest.approx(
        2.0 * _moles("Na2", 1.0 - route.remaining_by_species["Na2"])
    )
    assert _mass_closure_kg(route, {"Na2": 1.0}) == pytest.approx(0.0, abs=1.0e-15)
