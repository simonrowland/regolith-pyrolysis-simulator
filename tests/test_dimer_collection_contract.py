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
    DepositionInputRefusal,
    _CONFIG_BUNDLE,
    _try_antoine_psat_pa,
    deposition_input_refusal_aborts_train,
    dimer_dissociation_kp_pa,
)
from simulator.condensation_routing import (
    DESIGNATED_STAGE,
    DIMER_ALPHA_FROM_MONOMER_PROXY,
    DIMER_COLLISION_DIAMETER_FROM_MONOMER_PROXY,
    DIMER_COLLISION_DIAMETER_PROXY_REASON,
    DIMER_SATURATION_UPPER_BOUND_NO_KP,
    K_TRAP_STAGE_KEY,
    METAL_DIMER_SPECIES,
    MISSING_COLLECTION_PRODUCT_CONTRACT_SPECIES,
    NA_COLLECTOR_STAGE_KEY,
    dimer_collection_monomer,
    element_collection_pair,
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


@pytest.mark.parametrize("dimer", COLLECTED_DIMERS + REFUSED_DIMERS)
def test_missing_partial_on_every_dimer_refuses_that_species_only(dimer):
    """A missing partial is a typed refusal. It never aborts the train.

    Wall segments are reachable (lab geometry is set), so each dimer
    hits the wall handler and the stage handler. Al2 and Ca2 used to
    re-raise that missing P_local_pa and abort Na with them. Every
    dimer stays in the stream. Na, which has a partial, still captures.
    """

    species_kg = {dimer: 1.0, "Na": 1.0}
    model = _model(partials={"Na": 0.1})
    model.lab_geometry = object()
    route = model.route(
        EvaporationFlux(species_kg_hr=species_kg, total_kg_hr=2.0),
        MeltState(temperature_C=1500.0),
    )

    assert route.remaining_by_species[dimer] == pytest.approx(1.0)
    assert _deposited_kg(route, dimer) == 0.0
    assert _deposited_kg(route, "Na") > 0.0
    outcomes = route.condensation_refusals_by_species[dimer].get(
        "stage_outcomes", ()
    )
    parameters = {item.get("parameter") for item in outcomes}
    assert "flowing_pressure_pa" in parameters
    assert "P_local_pa" in parameters
    reason = route.condensation_refusals_by_species[dimer]["reason"]
    if dimer in REFUSED_DIMERS:
        assert reason == CONTRACT_REASON
    else:
        assert reason != CONTRACT_REASON
    assert _mass_closure_kg(route, species_kg) == pytest.approx(0.0, abs=1.0e-12)


# Invalid wall input aborts the train on every species and both capture
# channels. A missing partial, or an unconfigured collector area, refuses
# that species only. ``value is None`` is not what decides: a missing
# Antoine coefficient and a missing wall temperature are also None.
_ROUTE_SPECIES = (
    "Fe", "Cr", "CrO2", "Mn", "SiO", "Na", "K", "Mg",
    "Na2", "K2", "Mg2", "Al2", "Ca2",
)
_TERMINAL_WALL_INPUTS = (
    ("missing_coefficient", "pure_component_antoine.B", None, "must be numeric"),
    ("missing_wall_temperature", "T_wall_K", None, "holder_profile"),
    ("illegal_alpha", "alpha_s", 1.5, "sticking coefficient must be within [0, 1]"),
    ("nonfinite_partial", "P_local_pa", float("nan"), "must be finite"),
)
_SPECIES_LOCAL_WALL_INPUTS = (
    ("missing_partial", "P_local_pa", None, "wall species partial pressure is unconfigured"),
    ("missing_flowing_pressure", "flowing_pressure_pa", None, "must be numeric"),
    ("missing_stage_area", "stage_area_m2", None, "collector area is required"),
)


def _refusal(spec):
    _name, parameter, value, reason = spec
    return DepositionInputRefusal(parameter, value, reason)


@pytest.mark.parametrize("species", _ROUTE_SPECIES)
@pytest.mark.parametrize("channel", ("wall", "stage"))
@pytest.mark.parametrize("spec", _TERMINAL_WALL_INPUTS + _SPECIES_LOCAL_WALL_INPUTS)
def test_invalid_wall_input_aborts_on_every_species_and_channel(
    monkeypatch, species, channel, spec,
):
    """One chokepoint classifies every refusal the train can raise.

    The wall handler and the stage handler both call
    ``deposition_input_refusal_aborts_train``. This injects each input
    class into each handler for every routed species.
    """

    refusal = _refusal(spec)
    aborts = deposition_input_refusal_aborts_train(refusal)
    terminal_names = {item[0] for item in _TERMINAL_WALL_INPUTS}
    assert aborts is (spec[0] in terminal_names)
    witness = "Na" if species != "Na" else "Fe"
    partials = {species: 0.1, witness: 0.1}
    model = _model(partials=partials)
    model.lab_geometry = object()
    original_wall = CondensationModel._wall_deposit_candidates_by_segment_kg
    original_stage = CondensationModel._condensation_efficiency

    def wall(self, **kwargs):
        if channel == "wall" and kwargs.get("species") == species:
            raise _refusal(spec)
        return original_wall(self, **kwargs)

    def stage(self, **kwargs):
        if channel == "stage" and kwargs.get("species") == species:
            raise _refusal(spec)
        return original_stage(self, **kwargs)

    monkeypatch.setattr(CondensationModel, "_wall_deposit_candidates_by_segment_kg", wall)
    monkeypatch.setattr(CondensationModel, "_condensation_efficiency", stage)
    flux = EvaporationFlux(
        species_kg_hr={species: 1.0, witness: 1.0},
        total_kg_hr=2.0,
    )
    if aborts:
        with pytest.raises(DepositionInputRefusal) as caught:
            model.route(flux, MeltState(temperature_C=1500.0))
        assert caught.value.parameter == spec[1]
        return
    route = model.route(flux, MeltState(temperature_C=1500.0))
    assert _deposited_kg(route, witness) > 0.0
    assert route.remaining_by_species[species] >= 0.0


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


# Invariant. Every contracted metal-element capture, on the stage band
# and on the wall, draws the conserved element inventory down by each
# species' own Hertz-Knudsen drive and never below that surface's own
# p_elem,sat. No path snaps an oversized step to the coldest strip's
# floor, and no path caps the removal with min(rate, supply). A missing
# partial for any metal dimer, including Al2 and Ca2, is a typed refusal
# of that species and does not abort the train.
#
# p_elem = p_M + 2 p_M2 is conserved. The gas re-speciates at the wall,
# p_M is compared with P_sat,M, and the equilibrium capture fraction is
# (p_elem - p_elem,sat) / p_elem with p_elem,sat = P_sat + 2 P_sat^2 / K_p.
# The linear fraction (p_M - P_sat) / p_M is not the capture. The amount
# removed at finite area is the integral of J_M + 2 J_M2, not that
# equilibrium fraction by itself. Species outside a contracted pair are
# not on this criterion. A missing K_p stays the flagged
# p_elem-versus-P_sat upper bound.

WORKED_P_ELEM_PA = 6.0
WORKED_KP_PA = 1.0
WORKED_PSAT_PA = 1.0
CONTRACTED_PAIRS = (("K", "K2"), ("Mg", "Mg2"), ("Na", "Na2"))
CAPTURE_CHANNELS = ("stage", "wall")


def _worked_equilibrium():
    """Independent arithmetic for the controller's worked check.

    p_M = (K_p/4) (sqrt(1 + 8 p_elem/K_p) - 1) = 1.5 Pa.
    p_elem,sat = P_sat + 2 P_sat^2/K_p = 3 Pa.
    eta = 1/2. The linear monomer fraction (1.5-1)/1.5 is 1/3.
    """

    p_monomer = (WORKED_KP_PA / 4.0) * (
        math.sqrt(1.0 + 8.0 * WORKED_P_ELEM_PA / WORKED_KP_PA) - 1.0
    )
    p_elem_sat = (
        WORKED_PSAT_PA + 2.0 * WORKED_PSAT_PA * WORKED_PSAT_PA / WORKED_KP_PA
    )
    eta = (WORKED_P_ELEM_PA - p_elem_sat) / WORKED_P_ELEM_PA
    linear = (p_monomer - WORKED_PSAT_PA) / p_monomer
    assert p_monomer == pytest.approx(1.5)
    assert p_elem_sat == pytest.approx(3.0)
    assert eta == pytest.approx(0.5)
    assert linear == pytest.approx(1.0 / 3.0)
    return p_monomer, eta, linear


def _alkali_stage(model):
    for stage in model.train.stages:
        if int(stage.stage_number) == 4 and int(stage.sub_order) == 0:
            return stage
    raise AssertionError("Na collector stage is missing")


def _psat_flux(calls):
    def flux(_species, pressure, _temperature_K, _alpha, **kwargs):
        calls.append(float(pressure))
        diagnostic = kwargs.get("diagnostic_out")
        if isinstance(diagnostic, dict):
            diagnostic["wall_saturation_pressure_pa"] = WORKED_PSAT_PA
            diagnostic["wall_saturation_pressure_refused"] = False
        return 1.0e9 * max(0.0, float(pressure) - WORKED_PSAT_PA)

    return flux


def _arrival_partials(species, monomer, dimer):
    """Pure carrier of one element total. p_elem = 6 Pa either way."""

    if species == dimer:
        return 0.0, WORKED_P_ELEM_PA / 2.0
    return WORKED_P_ELEM_PA, 0.0


@pytest.mark.parametrize("monomer,dimer", CONTRACTED_PAIRS)
@pytest.mark.parametrize("channel", CAPTURE_CHANNELS)
@pytest.mark.parametrize("arrival", ("monomer", "dimer"))
def test_every_contracted_arrival_uses_the_element_capture_fraction(
    monkeypatch, monomer, dimer, channel, arrival,
):
    """Limiting capture is 1/2 of the element, not 1/3 of p_M.

    Grid: each contracted pair, each arrival (monomer and dimer), each
    channel (stage efficiency and wall candidate). Inlet p_elem = 6 Pa,
    K_p = 1 Pa, P_sat = 1 Pa.
    """

    _p_monomer, _eta_element, eta_linear = _worked_equilibrium()
    species = monomer if arrival == "monomer" else dimer
    monomer_partial, dimer_partial = _arrival_partials(species, monomer, dimer)
    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa",
        lambda *_args, **_kwargs: WORKED_KP_PA,
    )
    calls = []
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _psat_flux(calls),
    )
    from tests.test_condensation_input_refusals import _configured_model

    model = _configured_model()
    if channel == "stage":
        eta = model._condensation_efficiency(
            stage=_alkali_stage(model),
            species=species,
            T_cond_C=250.0,
            residence_s=5.0,
            available_kg=1.0,
            alpha_s_value=0.5,
            inlet_partial_pressure_pa=(
                dimer_partial if species == dimer else monomer_partial
            ),
            coexisting_monomer_partial_pa=(
                monomer_partial if species == dimer else None
            ),
            saturation_species=monomer if species == dimer else None,
        )
        assert calls, species
        assert calls[0] == pytest.approx(_p_monomer)
        k_monomer, k_dimer = _stage_series_coefficients(
            model, _alkali_stage(model), species, monomer, dimer,
            dimer_partial if species == dimer else monomer_partial,
            1.0, 5.0, 0.5,
        )
        final = _rk4_element_partial(
            WORKED_P_ELEM_PA, WORKED_KP_PA, WORKED_PSAT_PA,
            k_monomer, k_dimer, 5.0,
        )
        expected = (WORKED_P_ELEM_PA - final) / WORKED_P_ELEM_PA
        assert eta == pytest.approx(expected, rel=1.0e-6, abs=1.0e-9)
        assert eta != pytest.approx(eta_linear)
        assert model._dimer_hk_alpha_proxy[dimer] == monomer
        return

    from simulator.wall_deposition import wall_deposit_candidate_for_surface_kg

    partials = {}
    if monomer_partial > 0.0:
        partials[monomer] = monomer_partial
    if dimer_partial > 0.0:
        partials[dimer] = dimer_partial
    model.wall_species_partial_pressures_pa = partials
    deposited = wall_deposit_candidate_for_surface_kg(
        model,
        species=species,
        rate_kg_hr=1.0,
        T_cond_C=250.0,
        melt_temperature_C=1500.0,
        wall_temperature_C=250.0,
        surface_area_m2=1.0,
        collection_species=monomer if species == dimer else None,
    )
    assert calls, species
    assert calls[0] == pytest.approx(_p_monomer)
    assert deposited > 0.0
    assert model._dimer_hk_alpha_proxy[dimer] == monomer


def _parallel_element_rate(pressure, kp, psat, k_monomer, k_dimer):
    """Independent J_M + 2 J_M2. Not the production integrator."""

    monomer = (2.0 * pressure) / (math.sqrt(1.0 + 8.0 * pressure / kp) + 1.0)
    drive = monomer - psat
    if drive <= 0.0:
        return 0.0
    dimer_drive = drive * (monomer + psat) / kp
    return -(k_monomer * drive + 2.0 * k_dimer * dimer_drive)


def _rk4_element_partial(p_elem, kp, psat, k_monomer, k_dimer, duration, steps=20000):
    """RK4 of J_M + 2 J_M2. Not the production integrator.

    A step that would pass p_elem,sat is halved until it stays above
    that floor. The floor is not substituted for the step.
    """

    pressure = float(p_elem)
    floor = psat + 2.0 * psat * psat / kp
    t = 0.0
    base = float(duration) / steps
    while t < duration - 1.0e-15 * max(1.0, duration):
        step = min(base, duration - t)
        updated = pressure
        accepted = False
        for _halve in range(40):
            k1 = _parallel_element_rate(
                pressure, kp, psat, k_monomer, k_dimer,
            )
            if k1 == 0.0 or not math.isfinite(k1):
                return pressure
            k2 = _parallel_element_rate(
                pressure + 0.5 * step * k1, kp, psat, k_monomer, k_dimer,
            )
            k3 = _parallel_element_rate(
                pressure + 0.5 * step * k2, kp, psat, k_monomer, k_dimer,
            )
            k4 = _parallel_element_rate(
                pressure + step * k3, kp, psat, k_monomer, k_dimer,
            )
            updated = pressure + step * (
                k1 + 2.0 * k2 + 2.0 * k3 + k4
            ) / 6.0
            if math.isfinite(updated) and updated > floor:
                accepted = True
                break
            step *= 0.5
        if not accepted or updated >= pressure:
            return pressure
        pressure = updated
        t += step
    return pressure


def _stage_series_coefficients(
    model, stage, species, monomer, dimer, flowing, available_kg, residence_s, alpha,
):
    """q * (A/n) * G/A for the monomer and the dimer.

    G is the series conductance. Alpha sits inside G. The stand-in
    flux monkeypatch supplies P_sat and does not set this rate.
    """

    from simulator.condensation import (
        AVOGADRO_MOL,
        CELSIUS_TO_KELVIN_OFFSET,
        HKL_BAND_SAMPLES,
        _element_series_coefficients_mol_m2_s_pa,
        _molecular_mass_kg_per_molecule,
    )

    molar_mass = _molecular_mass_kg_per_molecule(
        species, vapor_pressure_data=model.vapor_pressure_data,
    ) * AVOGADRO_MOL
    inventory_mol = available_kg / molar_mass / 3600.0 * residence_s
    width = stage.temp_range_C[1] - stage.temp_range_C[0]
    sample_count = 1 if width == 0.0 else HKL_BAND_SAMPLES
    area = model._stage_area_m2_for_stage(stage)
    scale = (flowing / inventory_mol) * area / sample_count
    temperature_K = model.gas_temperature_C + CELSIUS_TO_KELVIN_OFFSET
    k_monomer, k_dimer = _element_series_coefficients_mol_m2_s_pa(
        monomer=monomer,
        dimer=dimer,
        alpha_s=alpha,
        pipe_diameter_m=model._hydraulic_diameter_m_for_stage_number(
            stage.stage_number,
        ),
        stir_factor=model.stir_factor,
        radial_stir_factor=model.radial_stir_factor,
        regime_factor=model.regime_factor,
        T_gas_K=temperature_K,
        overhead_pressure_pa=float(model.overhead_pressure_mbar) * 100.0,
        carrier_gas=str(model.carrier_gas),
        vapor_pressure_data=model.vapor_pressure_data,
    )
    return scale * k_monomer, scale * k_dimer


@pytest.mark.parametrize("monomer,dimer", CONTRACTED_PAIRS)
@pytest.mark.parametrize("arrival", ("monomer", "dimer"))
def test_stage_drawdown_follows_the_element_inventory_ode(
    monkeypatch, monomer, dimer, arrival,
):
    """Finite residence follows J_M + 2 J_M2, not a jump to p_elem,sat.

    The series-resistance monkeypatch supplies P_sat. The rate is each
    form's series conductance, not that stand-in flux. A monomer-only
    drive fails this integral.
    """

    from tests.test_condensation_input_refusals import _configured_model

    species = monomer if arrival == "monomer" else dimer
    monomer_partial, dimer_partial = _arrival_partials(species, monomer, dimer)
    flowing = dimer_partial if species == dimer else monomer_partial
    residence_s = 2.0
    available_kg = 1.0

    def flux(_species, pressure, _temperature_K, _alpha, **kwargs):
        diagnostic = kwargs.get("diagnostic_out")
        if isinstance(diagnostic, dict):
            diagnostic["wall_saturation_pressure_pa"] = WORKED_PSAT_PA
            diagnostic["wall_saturation_pressure_refused"] = False
        return max(0.0, float(pressure) - WORKED_PSAT_PA)

    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa",
        lambda *_args, **_kwargs: WORKED_KP_PA,
    )
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        flux,
    )
    model = _configured_model()
    stage = _alkali_stage(model)
    assert stage.temp_range_C[0] == stage.temp_range_C[1]
    eta = model._condensation_efficiency(
        stage=stage,
        species=species,
        T_cond_C=250.0,
        residence_s=residence_s,
        available_kg=available_kg,
        alpha_s_value=0.5,
        inlet_partial_pressure_pa=flowing,
        coexisting_monomer_partial_pa=(
            monomer_partial if species == dimer else None
        ),
        saturation_species=monomer if species == dimer else None,
    )
    k_monomer, k_dimer = _stage_series_coefficients(
        model, stage, species, monomer, dimer, flowing,
        available_kg, residence_s, 0.5,
    )
    p_final = _rk4_element_partial(
        WORKED_P_ELEM_PA, WORKED_KP_PA, WORKED_PSAT_PA,
        k_monomer, k_dimer, residence_s,
    )
    expected = (WORKED_P_ELEM_PA - p_final) / WORKED_P_ELEM_PA
    assert eta == pytest.approx(expected, rel=1.0e-6, abs=1.0e-9)
    assert eta != pytest.approx((1.5 - 1.0) / 1.5)


@pytest.mark.parametrize("monomer,dimer", CONTRACTED_PAIRS)
@pytest.mark.parametrize("channel", CAPTURE_CHANNELS)
@pytest.mark.parametrize("arrival", ("monomer", "dimer"))
def test_strong_association_blocks_both_arrivals_of_one_element(
    monkeypatch, monomer, dimer, channel, arrival,
):
    """Inlet p_M above P_sat does not capture when equilibrium p_M is below it.

    p_M = 2 Pa, p_M2 = 10 Pa, p_elem = 22 Pa, K_p = 1e-8 Pa, P_sat = 1 Pa.
    The monomer's own partial is supersaturated. The shared element
    equilibrium is not, so the monomer and the dimer both pass through.
    """

    kp_pa = 1.0e-8
    psat_pa = 1.0
    monomer_partial = 2.0
    dimer_partial = 10.0
    p_elem = monomer_partial + 2.0 * dimer_partial
    # Conjugate root, same closed form as the chokepoint. The K_p -> 0
    # square-root asymptotic is a few ulp away at 1e-8 Pa.
    p_monomer = (2.0 * p_elem) / (
        math.sqrt(1.0 + 8.0 * p_elem / kp_pa) + 1.0
    )
    assert monomer_partial > psat_pa > p_monomer
    species = monomer if arrival == "monomer" else dimer
    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa",
        lambda *_args, **_kwargs: kp_pa,
    )
    calls = []

    def flux(_species, pressure, _temperature_K, _alpha, **kwargs):
        calls.append(float(pressure))
        diagnostic = kwargs.get("diagnostic_out")
        if isinstance(diagnostic, dict):
            diagnostic["wall_saturation_pressure_pa"] = psat_pa
            diagnostic["wall_saturation_pressure_refused"] = False
        return 1.0e9 * max(0.0, float(pressure) - psat_pa)

    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        flux,
    )
    from tests.test_condensation_input_refusals import _configured_model

    model = _configured_model()
    if channel == "stage":
        eta = model._condensation_efficiency(
            stage=_alkali_stage(model),
            species=species,
            T_cond_C=250.0,
            residence_s=5.0,
            available_kg=1.0,
            alpha_s_value=0.5,
            inlet_partial_pressure_pa=(
                dimer_partial if species == dimer else monomer_partial
            ),
            coexisting_monomer_partial_pa=(
                monomer_partial if species == dimer else None
            ),
            coexisting_dimer_partial_pa=(
                dimer_partial if species == monomer else None
            ),
            saturation_species=monomer if species == dimer else None,
        )
        assert calls and calls[0] == pytest.approx(p_monomer, rel=1.0e-6)
        assert eta == 0.0
        return

    from simulator.wall_deposition import wall_deposit_candidate_for_surface_kg

    model.wall_species_partial_pressures_pa = {
        monomer: monomer_partial,
        dimer: dimer_partial,
    }
    deposited = wall_deposit_candidate_for_surface_kg(
        model,
        species=species,
        rate_kg_hr=1.0,
        T_cond_C=250.0,
        melt_temperature_C=1500.0,
        wall_temperature_C=250.0,
        surface_area_m2=1.0,
        collection_species=monomer if species == dimer else None,
    )
    assert calls and calls[0] == pytest.approx(p_monomer, rel=1.0e-6)
    assert deposited == 0.0


@pytest.mark.parametrize("channel", CAPTURE_CHANNELS)
@pytest.mark.parametrize("monomer,dimer", CONTRACTED_PAIRS)
@pytest.mark.parametrize("arrival", ("monomer", "dimer"))
def test_both_channels_take_the_comparison_from_one_chokepoint(
    monkeypatch, channel, monomer, dimer, arrival,
):
    """A second re-speciation formula cannot satisfy this sentinel pressure."""

    from simulator.condensation import element_arrival_comparison_pa

    sentinel = 4.25

    def marked(*args, **kwargs):
        p_elem, _compare, flag, kp = element_arrival_comparison_pa(*args, **kwargs)
        return p_elem, sentinel, flag, kp

    monkeypatch.setattr(
        "simulator.condensation.element_arrival_comparison_pa",
        marked,
    )
    species = monomer if arrival == "monomer" else dimer
    calls = []

    def flux(_species, pressure, _temperature_K, _alpha, **kwargs):
        calls.append(float(pressure))
        diagnostic = kwargs.get("diagnostic_out")
        if isinstance(diagnostic, dict):
            diagnostic["wall_saturation_pressure_pa"] = 1.0
            diagnostic["wall_saturation_pressure_refused"] = False
        return 0.0

    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        flux,
    )
    from tests.test_condensation_input_refusals import _configured_model

    model = _configured_model()
    if channel == "stage":
        model._condensation_efficiency(
            stage=_alkali_stage(model),
            species=species,
            T_cond_C=250.0,
            residence_s=5.0,
            available_kg=1.0,
            alpha_s_value=0.5,
            inlet_partial_pressure_pa=3.0,
            coexisting_monomer_partial_pa=0.0 if species == dimer else None,
            coexisting_dimer_partial_pa=0.0 if species == monomer else None,
            saturation_species=monomer if species == dimer else None,
        )
    else:
        from simulator.wall_deposition import wall_deposit_candidate_for_surface_kg

        model.wall_species_partial_pressures_pa = {species: 3.0}
        wall_deposit_candidate_for_surface_kg(
            model,
            species=species,
            rate_kg_hr=1.0,
            T_cond_C=250.0,
            melt_temperature_C=1500.0,
            wall_temperature_C=250.0,
            surface_area_m2=1.0,
            collection_species=monomer if species == dimer else None,
        )
    assert calls == [sentinel]


def test_element_capture_fraction_matches_the_worked_check_and_both_limits():
    """p_elem,sat and the capture fraction, independent of the stage solver."""

    from simulator.condensation_routing import (
        element_equilibrium_comparison_pa,
        element_limiting_capture_fraction,
        saturated_element_partial_pa,
    )

    p_elem, p_monomer, flag = element_equilibrium_comparison_pa(
        monomer_partial_pa=0.0,
        dimer_partial_pa=WORKED_P_ELEM_PA / 2.0,
        kp_pa=WORKED_KP_PA,
    )
    assert flag is None
    assert p_elem == pytest.approx(WORKED_P_ELEM_PA)
    assert p_monomer == pytest.approx(1.5)
    assert saturated_element_partial_pa(WORKED_PSAT_PA, WORKED_KP_PA) == pytest.approx(3.0)
    assert element_limiting_capture_fraction(
        WORKED_P_ELEM_PA, WORKED_PSAT_PA, WORKED_KP_PA,
    ) == pytest.approx(0.5)
    assert element_limiting_capture_fraction(
        WORKED_P_ELEM_PA, WORKED_PSAT_PA, WORKED_KP_PA,
    ) != pytest.approx(1.0 / 3.0)

    assert saturated_element_partial_pa(WORKED_PSAT_PA, math.inf) == pytest.approx(
        WORKED_PSAT_PA
    )
    assert element_limiting_capture_fraction(
        WORKED_P_ELEM_PA, WORKED_PSAT_PA, math.inf,
    ) == pytest.approx((WORKED_P_ELEM_PA - WORKED_PSAT_PA) / WORKED_P_ELEM_PA)
    assert math.isinf(saturated_element_partial_pa(WORKED_PSAT_PA, 0.0))
    assert element_limiting_capture_fraction(
        WORKED_P_ELEM_PA, WORKED_PSAT_PA, 0.0,
    ) == 0.0

    _p_elem, compared, upper_flag = element_equilibrium_comparison_pa(
        monomer_partial_pa=1.0,
        dimer_partial_pa=2.0,
        kp_pa=None,
    )
    assert compared == pytest.approx(5.0)
    assert upper_flag == DIMER_SATURATION_UPPER_BOUND_NO_KP
    assert saturated_element_partial_pa(WORKED_PSAT_PA, None) == pytest.approx(
        WORKED_PSAT_PA
    )


def test_stiff_parallel_flux_lands_on_half_not_the_linear_third():
    """Huge coefficients reach p_elem,sat. The capture is 1/2, not 1/3."""

    from simulator.condensation_routing import final_element_partial_pa

    huge = 1.0e12
    final = final_element_partial_pa(
        element_partial_initial_pa=WORKED_P_ELEM_PA,
        residence_s=5.0,
        strips=((huge, huge, WORKED_PSAT_PA, WORKED_KP_PA),),
    )
    eta = (WORKED_P_ELEM_PA - final) / WORKED_P_ELEM_PA
    assert final == pytest.approx(3.0)
    assert eta == pytest.approx(0.5)
    assert eta != pytest.approx(1.0 / 3.0)

    # Moderate coefficients stay on the trajectory, short of the floor.
    k_monomer = 0.05
    k_dimer = 0.05
    duration = 2.0
    partial = final_element_partial_pa(
        element_partial_initial_pa=WORKED_P_ELEM_PA,
        residence_s=duration,
        strips=((k_monomer, k_dimer, WORKED_PSAT_PA, WORKED_KP_PA),),
    )
    expected = _rk4_element_partial(
        WORKED_P_ELEM_PA, WORKED_KP_PA, WORKED_PSAT_PA,
        k_monomer, k_dimer, duration,
    )
    eta_partial = (WORKED_P_ELEM_PA - partial) / WORKED_P_ELEM_PA
    assert 0.0 < eta_partial < 0.5
    assert partial == pytest.approx(expected, rel=1.0e-6)

    # K_p -> inf drops the dimer channel even when its coefficient is huge.
    dissociated = final_element_partial_pa(
        element_partial_initial_pa=WORKED_P_ELEM_PA,
        residence_s=3.0,
        strips=((0.2, huge, WORKED_PSAT_PA, math.inf),),
    )
    monomer_only = WORKED_PSAT_PA + (
        WORKED_P_ELEM_PA - WORKED_PSAT_PA
    ) * math.exp(-0.2 * 3.0)
    assert dissociated == pytest.approx(monomer_only, rel=1.0e-8)


def test_heterogeneous_linear_strips_stop_on_their_own_floor():
    """Closed form for a monomer-only strip. No shared step and no snap.

    p(t) = P_sat + (p0 - P_sat) exp(-k t). Chain two strips over one
    second each, then the same second with both strips together.
    K_p is infinite, so the drive is linear in the element partial.

    Inlet 200 Pa. Fast strip k = 1e12 /s, P_sat = 100 Pa. Slow strip
    k = 1e-6 /s, P_sat = 1 Pa.

        fast: exp(-1e12) underflows, p = 100 Pa
        slow: p = 1 + (100 - 1) exp(-1e-6)
              = 1 + 99 (1 - 1e-6 + 5e-13 - ...)
              = 99.999901 Pa

    The fast strip turns off at 100 Pa. The slow strip then barely
    moves. The cold floor, 1 Pa, is not the result.
    """

    from simulator.condensation_routing import final_element_partial_pa

    fast = final_element_partial_pa(
        element_partial_initial_pa=200.0,
        residence_s=1.0,
        strips=((1.0e12, 0.0, 100.0, math.inf),),
    )
    assert fast == pytest.approx(100.0)
    slow_closed_form = 1.0 + 99.0 * math.exp(-1.0e-6)
    assert slow_closed_form == pytest.approx(99.999901, abs=1.0e-9)
    slow = final_element_partial_pa(
        element_partial_initial_pa=fast,
        residence_s=1.0,
        strips=((1.0e-6, 0.0, 1.0, math.inf),),
    )
    assert slow == pytest.approx(slow_closed_form, abs=1.0e-9)
    both = final_element_partial_pa(
        element_partial_initial_pa=200.0,
        residence_s=1.0,
        strips=(
            (1.0e12, 0.0, 100.0, math.inf),
            (1.0e-6, 0.0, 1.0, math.inf),
        ),
    )
    assert both == pytest.approx(slow_closed_form, abs=1.0e-6)
    assert both > 99.0


def _closed_form_linear_partial(case: str) -> tuple[float, float, tuple, float]:
    """Independent arithmetic for one linear-strip inlet. Not the solver.

    While the strips with P_sat < p stay fixed,

        C = sum k_i
        p_eq = sum (k_i P_i) / C
        p(t) = p_eq + (p0 - p_eq) exp(-C t).

    A strip with P_sat >= p is absent from that sum. Reaching its floor
    removes it and the colder strips continue for the time that remains.
    """

    if case == "inactive_hot":
        # p0 = 75 Pa, residence 10 s, k = 1 at 100, 50 and 1 Pa.
        # 75 < 100, so the hot strip is inactive.
        # dp/dt = -(p - 50) - (p - 1) = -2p + 51 until p = 50.
        # 25.5 + 49.5 exp(-2 t) = 50
        # exp(-2 t) = 24.5 / 49.5 = 49/99
        # t = ln(99/49) / 2
        # Then only the 1 Pa strip acts:
        # p(10) = 1 + 49 exp(-(10 - t))
        p0, duration = 75.0, 10.0
        rows = ((1.0, 100.0), (1.0, 50.0), (1.0, 1.0))
        t_hit = math.log(99.0 / 49.0) / 2.0
        expected = 1.0 + 49.0 * math.exp(-(duration - t_hit))
    elif case == "below_every_floor":
        p0, duration = 10.0, 10.0
        rows = ((1.0, 100.0), (1.0, 50.0), (1.0, 40.0))
        expected = p0
    elif case == "only_cold_strip_active":
        # 100 Pa and 50 Pa are inactive. p = 1 + 39 exp(-10).
        p0, duration = 40.0, 10.0
        rows = ((1.0, 100.0), (1.0, 50.0), (1.0, 1.0))
        expected = 1.0 + (p0 - 1.0) * math.exp(-duration)
    elif case == "single_strip":
        p0, duration = 80.0, 2.0
        rows = ((0.5, 20.0),)
        expected = 20.0 + (p0 - 20.0) * math.exp(-0.5 * duration)
    elif case == "active_set_holds":
        # C = 0.2, p_eq = 25. p(0.5) = 25 + 75 exp(-0.1) stays above 40,
        # so the hotter strip does not turn off.
        p0, duration = 100.0, 0.5
        rows = ((0.1, 40.0), (0.1, 10.0))
        expected = 25.0 + 75.0 * math.exp(-0.2 * duration)
        assert expected > 40.0
    elif case == "cross_then_cold_only":
        # C = 2, p_eq = 35. Hit 60 Pa at ln((100-35)/(60-35))/2
        # = ln(13/5)/2. Then k = 1 at 10 Pa for the time left.
        p0, duration = 100.0, 5.0
        rows = ((1.0, 60.0), (1.0, 10.0))
        t_hit = math.log(65.0 / 25.0) / 2.0
        expected = 10.0 + 50.0 * math.exp(-(duration - t_hit))
    elif case == "zero_residence":
        p0, duration = 75.0, 0.0
        rows = ((1.0, 100.0), (1.0, 50.0), (1.0, 1.0))
        expected = p0
    else:
        raise AssertionError(case)
    return p0, duration, rows, expected


@pytest.mark.parametrize("order", ("hot_first", "cold_first"))
@pytest.mark.parametrize(
    "case",
    (
        "inactive_hot",
        "below_every_floor",
        "only_cold_strip_active",
        "single_strip",
        "active_set_holds",
        "cross_then_cold_only",
        "zero_residence",
    ),
)
def test_every_linear_inlet_skips_inactive_strips(order, case):
    """Enumerate inlet position and strip order against the closed form.

    The oracle is the arithmetic in ``_closed_form_linear_partial``.
    It does not call the production march.
    """

    from simulator.condensation_routing import final_element_partial_pa

    p0, duration, rows, expected = _closed_form_linear_partial(case)
    if order == "cold_first":
        rows = tuple(reversed(rows))
    strips = tuple((coefficient, 0.0, saturation, math.inf) for coefficient, saturation in rows)
    got = final_element_partial_pa(
        element_partial_initial_pa=p0,
        residence_s=duration,
        strips=strips,
    )
    assert got == pytest.approx(expected, rel=1.0e-12, abs=1.0e-12)


@pytest.mark.parametrize("monomer,dimer", CONTRACTED_PAIRS)
@pytest.mark.parametrize("channel", CAPTURE_CHANNELS)
@pytest.mark.parametrize("arrival", ("monomer", "dimer"))
def test_large_area_stops_at_half_the_element_inventory(
    monkeypatch, monomer, dimer, channel, arrival,
):
    """Large area, p_elem = 6 Pa, K_p = 1 Pa, P_sat = 1 Pa.

    p_M = (K_p/4) (sqrt(1 + 8*6) - 1) = 1.5 Pa and p_M2 = 2.25 Pa
    (1.5 + 2*2.25 = 6). At saturation p_M = p_M2 = 1, so p_elem,eq = 3
    and the stage integral removes 1/2. The linear fraction
    (1.5 - 1) / 1.5 = 1/3 is not that capture. The wall evaluates
    J_M + 2 J_M2 at the inlet and caps at the supply, so this area
    removes the whole kilogram. Stopping the wall on p_elem,sat is
    the exponential drawdown, which is a separate correction.
    """

    _p_monomer, eta_element, eta_linear = _worked_equilibrium()
    assert eta_element == pytest.approx(0.5)
    species = monomer if arrival == "monomer" else dimer
    monomer_partial, dimer_partial = _arrival_partials(species, monomer, dimer)
    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa",
        lambda *_args, **_kwargs: WORKED_KP_PA,
    )
    monkeypatch.setattr(
        "simulator.condensation._series_resistance_deposition_flux_mol_m2_s",
        _psat_flux([]),
    )
    from tests.test_condensation_input_refusals import _configured_model

    model = _configured_model()
    if channel == "stage":
        for key in model.stage_area_m2_by_stage:
            model.stage_area_m2_by_stage[key] = 1.0e12
        eta = model._condensation_efficiency(
            stage=_alkali_stage(model),
            species=species,
            T_cond_C=250.0,
            residence_s=5.0,
            available_kg=1.0,
            alpha_s_value=0.5,
            inlet_partial_pressure_pa=(
                dimer_partial if species == dimer else monomer_partial
            ),
            coexisting_monomer_partial_pa=(
                monomer_partial if species == dimer else None
            ),
            saturation_species=monomer if species == dimer else None,
        )
        assert eta == pytest.approx(eta_element, abs=1.0e-9)
        assert eta != pytest.approx(eta_linear)
        assert eta != pytest.approx(1.0)
        return

    from simulator.wall_deposition import wall_deposit_candidate_for_surface_kg

    partials = {}
    if monomer_partial > 0.0:
        partials[monomer] = monomer_partial
    if dimer_partial > 0.0:
        partials[dimer] = dimer_partial
    model.wall_species_partial_pressures_pa = partials
    deposited = wall_deposit_candidate_for_surface_kg(
        model,
        species=species,
        rate_kg_hr=1.0,
        T_cond_C=250.0,
        melt_temperature_C=1500.0,
        wall_temperature_C=250.0,
        surface_area_m2=1.0e12,
        collection_species=monomer if species == dimer else None,
    )
    assert deposited == pytest.approx(1.0, abs=1.0e-12)
    assert deposited != pytest.approx(eta_element)
    assert deposited != pytest.approx(eta_linear)


def test_janaf_kp_at_313_K_is_in_band_and_replaces_the_cea_polynomial():
    """Primary table. 313.15 K is inside the printed JANAF grid.

    K2(g), table K-011, has no 350 K row. The printed formation Gibbs
    energies are 87.579 kJ/mol at 300 K and 76.804 kJ/mol at 400 K:

        weight = (313.15 - 300) / (400 - 300) = 0.1315
        G_K2 = 87.579 + (76.804 - 87.579) * 0.1315 = 86.1620875 kJ/mol

    K(g), table K-005, does have a 350 K row, so its neighbours stay
    300 K and 350 K. The printed values are 60.299 and 55.652 kJ/mol:

        weight = (313.15 - 300) / (350 - 300) = 0.263
        G_K = 60.299 + (55.652 - 60.299) * 0.263 = 59.076839 kJ/mol
        ΔG_assoc = 86.1620875 - 2 * 59.076839 = -31.9915905 kJ/mol
        K_p = 1e5 Pa * exp(1000 * ΔG_assoc / (R * 313.15 K))

    Fail-before on the CEA path: dimer_dissociation_kp_pa('K2', 313.15)
    returned 1.325277167193154 Pa. That figure is the in-band 300-1000 K
    NASA-9 polynomial, not a high-T extrapolation, and it is the wrong
    source.
    """

    import yaml

    from simulator.condensation import DATA_DIR

    document = yaml.safe_load(
        (DATA_DIR / "literature" / "dimer_dissociation_kp.yaml").read_text()
    )
    assert document["extrapolation"] == "none"
    assert document["standard_pressure_Pa"] == 100000.0
    assert document["band"]["potassium_trap_K"] == 313.15
    assert document["band"]["in_printed_table"] is True
    assert document["source"]["valid_from_K"] == 298.15

    def formation_gibbs(table_id):
        table = yaml.safe_load(
            (
                DATA_DIR / "literature" / "compilations" / "janaf" / "tables"
                / f"{table_id}.yaml"
            ).read_text()
        )
        rows = []
        for row in table["table"]["values"]:
            gibbs = row["formation_gibbs_energy"]["value"]
            rows.append((
                float(row["temperature"]["value"]),
                None if gibbs is None else float(gibbs),
            ))
        return sorted(rows)

    def gibbs_at(rows, temperature):
        for index, (row_temperature, gibbs) in enumerate(rows):
            if row_temperature == temperature:
                return gibbs
            if row_temperature > temperature:
                left_temperature, left_gibbs = rows[index - 1]
                weight = (temperature - left_temperature) / (
                    row_temperature - left_temperature
                )
                return left_gibbs + (gibbs - left_gibbs) * weight
        raise AssertionError(temperature)

    for table_id in ("K-005", "K-011", "Na-005", "Na-011", "Mg-005", "Mg-027"):
        rows = formation_gibbs(table_id)
        temperatures = [temperature for temperature, _gibbs in rows]
        assert min(temperatures) <= 298.15 <= 313.15 <= max(temperatures)
        below = max(temperature for temperature in temperatures if temperature <= 313.15)
        above = min(temperature for temperature in temperatures if temperature >= 313.15)
        assert below < 313.15 < above
        assert gibbs_at(rows, below) is not None
        assert gibbs_at(rows, above) is not None

    from simulator.physical_constants import GAS_CONSTANT

    def independent_kp(monomer_table, dimer_table, temperature):
        delta_g = gibbs_at(formation_gibbs(dimer_table), temperature) - 2.0 * gibbs_at(
            formation_gibbs(monomer_table), temperature,
        )
        return 1.0e5 * math.exp(delta_g * 1000.0 / (GAS_CONSTANT * temperature))

    k2_rows = formation_gibbs("K-011")
    k_rows = formation_gibbs("K-005")
    assert 350.0 not in {temperature for temperature, _gibbs in k2_rows}
    k2_below = max(temperature for temperature, _gibbs in k2_rows if temperature <= 313.15)
    k2_above = min(temperature for temperature, _gibbs in k2_rows if temperature >= 313.15)
    k_below = max(temperature for temperature, _gibbs in k_rows if temperature <= 313.15)
    k_above = min(temperature for temperature, _gibbs in k_rows if temperature >= 313.15)
    assert (k2_below, k2_above) == (300.0, 400.0)
    assert (k_below, k_above) == (300.0, 350.0)
    g_k2_300 = gibbs_at(k2_rows, 300.0)
    g_k2_400 = gibbs_at(k2_rows, 400.0)
    g_k_300 = gibbs_at(k_rows, 300.0)
    g_k_350 = gibbs_at(k_rows, 350.0)
    assert (g_k2_300, g_k2_400) == (87.579, 76.804)
    assert (g_k_300, g_k_350) == (60.299, 55.652)
    trap_k = 313.15
    g_k2 = g_k2_300 + (g_k2_400 - g_k2_300) * (trap_k - 300.0) / (400.0 - 300.0)
    g_k = g_k_300 + (g_k_350 - g_k_300) * (trap_k - 300.0) / (350.0 - 300.0)
    assert g_k2 == 86.1620875
    assert g_k == 59.076839
    delta_g_assoc = g_k2 - 2.0 * g_k
    assert delta_g_assoc == -31.9915905
    kp_from_printed_rows = 1.0e5 * math.exp(
        delta_g_assoc * 1000.0 / (GAS_CONSTANT * trap_k)
    )
    assert dimer_dissociation_kp_pa("K2", trap_k) == pytest.approx(
        kp_from_printed_rows, rel=1.0e-12,
    )
    assert dimer_dissociation_kp_pa("K2", trap_k) == pytest.approx(
        independent_kp("K-005", "K-011", trap_k), rel=1.0e-12,
    )
    assert kp_from_printed_rows != pytest.approx(1.325277167193154, rel=1.0e-4)
    assert dimer_dissociation_kp_pa("K2", 298.15) == pytest.approx(
        independent_kp("K-005", "K-011", 298.15), rel=1.0e-12,
    )
    assert dimer_dissociation_kp_pa("Na2", 313.15) == pytest.approx(
        independent_kp("Na-005", "Na-011", 313.15), rel=1.0e-12,
    )
    assert dimer_dissociation_kp_pa("Mg2", 313.15) == pytest.approx(
        independent_kp("Mg-005", "Mg-027", 313.15), rel=1.0e-12,
    )
    assert dimer_dissociation_kp_pa("K2", 7000.0) is None
    assert dimer_dissociation_kp_pa("Al2", 313.15) is None


def test_monomer_only_potassium_flags_the_dimer_alpha_proxy():
    """A pure K feed still uses alpha_K for the re-speciated K2 channel."""

    route = _route({"K": 1.0}, partials={"K": 1.0})
    notice = route.sticking_alpha_provenance_notice
    assert notice[DIMER_ALPHA_FROM_MONOMER_PROXY]["K2"] == "K"
    assert notice["authoritative_for_deposit_mass"] is False


# Invariant. The monomer sticking coefficient used as alpha_M2 is
# published as dimer_alpha_from_monomer_proxy. The stage integral is
# draw_element_partial_pa: it records the proxy, then integrates. The
# wall uses the inlet cap and records the same proxy when K_p is
# finite. The grid is every contracted pair, every arrival (monomer
# only, dimer only, both), and every surface (wall only, stage only,
# both). Wall-only keeps the duct area and then sets every collector
# area to 0, so the stage integral never runs. 250 C is cold enough
# that 10 Pa of K, Na, and Mg are all supersaturated on that duct.
# 40 C, the potassium trap, refuses Na's wall curve, so the shared
# grid does not use it; the potassium counterexample below does. A
# 1500 C duct and a species with no contracted dimer do not record
# the proxy.


PROXY_ARRIVALS = ("monomer", "dimer", "both")
PROXY_SURFACES = ("wall_only", "stage_only", "both")
# Cold enough for 10 Pa of K, Na, and Mg. Hotter than this, K's
# extrapolated P_sat rises through the partial and the wall channel
# stays off (measured: 300 C deposits nothing).
PROXY_COLD_WALL_C = 250.0


def _proxy_route(monomer, dimer, arrival, surface, *, wall_c=None):
    species_kg = {}
    partials = {}
    if arrival in ("monomer", "both"):
        species_kg[monomer] = 1.0
        partials[monomer] = 0.1
    if arrival in ("dimer", "both"):
        species_kg[dimer] = 1.0
        partials[dimer] = 0.1
    model = _model(partials=partials)
    # Duct area is copied from the collector area at configure time.
    # Pass 1 m2 so the duct stays a real surface, then zero the
    # collectors when this case is wall-only.
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar=partials,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
        pipe_segment_temperatures_C={
            segment.name: (
                PROXY_COLD_WALL_C if wall_c is None else wall_c
            )
            if surface != "stage_only"
            else 1500.0
            for segment in model.pipe_segments
        },
    )
    if surface == "wall_only":
        model.stage_area_m2_by_stage = {
            key: 0.0 for key in model.stage_area_m2_by_stage
        }
    if surface == "stage_only":
        model.pipe_segments = []
    return model.route(
        EvaporationFlux(
            species_kg_hr=species_kg,
            total_kg_hr=float(sum(species_kg.values())),
        ),
        MeltState(temperature_C=1500.0),
    )


@pytest.mark.parametrize("monomer,dimer", CONTRACTED_PAIRS)
@pytest.mark.parametrize("arrival", PROXY_ARRIVALS)
@pytest.mark.parametrize("surface", PROXY_SURFACES)
def test_dimer_alpha_proxy_is_published_whenever_the_channel_runs(
    monomer, dimer, arrival, surface,
):
    """Every path that deposits with alpha_M as alpha_M2 names the proxy."""

    route = _proxy_route(monomer, dimer, arrival, surface)
    notice = route.sticking_alpha_provenance_notice
    assert notice[DIMER_ALPHA_FROM_MONOMER_PROXY][dimer] == monomer
    assert notice[DIMER_COLLISION_DIAMETER_FROM_MONOMER_PROXY][dimer] == (
        DIMER_COLLISION_DIAMETER_PROXY_REASON
    )
    assert notice["authoritative_for_deposit_mass"] is False
    deposited = _deposited_kg(route, monomer)
    assert deposited > 0.0
    if surface == "wall_only":
        staged = sum(
            by_species.get(monomer, 0.0)
            for by_species in route.condensed_by_stage_species.values()
        )
        assert staged == pytest.approx(0.0, abs=0.0)
        assert route.wall_deposit_by_species.get(monomer, 0.0) > 0.0


def test_monomer_only_potassium_on_a_40c_wall_flags_the_proxy():
    """Reviewer's route: pure K, 40 C duct, every collector area zero.

    The stage integral returns before the dimer channel. The wall
    still re-speciates K2 and deposits it with alpha_K.
    """

    route = _proxy_route("K", "K2", "monomer", "wall_only", wall_c=40.0)
    notice = route.sticking_alpha_provenance_notice
    assert notice[DIMER_ALPHA_FROM_MONOMER_PROXY]["K2"] == "K"
    assert notice[DIMER_COLLISION_DIAMETER_FROM_MONOMER_PROXY]["K2"] == (
        DIMER_COLLISION_DIAMETER_PROXY_REASON
    )
    assert notice["authoritative_for_deposit_mass"] is False
    assert route.wall_deposit_by_species.get("K", 0.0) > 0.0
    staged = sum(
        by_species.get("K", 0.0)
        for by_species in route.condensed_by_stage_species.values()
    )
    assert staged == pytest.approx(0.0, abs=0.0)


def _species_capture(route, species):
    return {
        "stages": {
            stage_key: by_species.get(species, 0.0)
            for stage_key, by_species in route.condensed_by_stage_species.items()
        },
        "wall": route.wall_deposit_by_species.get(species, 0.0),
        "remaining": route.remaining_by_species[species],
    }


def _potassium_wall_kg():
    """Pure K on a 40 C wall. Area is small, so the inlet flux is the cap."""

    from simulator.wall_deposition import wall_deposit_candidate_for_surface_kg

    model = _model(partials=None)
    model.wall_species_partial_pressures_pa = {"K": 100.0}
    deposited = wall_deposit_candidate_for_surface_kg(
        model,
        species="K",
        rate_kg_hr=1.0,
        T_cond_C=250.0,
        melt_temperature_C=1500.0,
        wall_temperature_C=40.0,
        surface_area_m2=1.0e-4,
    )
    return model, deposited


def test_wall_inlet_form_is_continuous_in_kp(monkeypatch):
    """One wall formula. K_p = 1e30 matches K_p = inf within 1e-12.

    K_p -> inf is not a branch. The dimer drive is zero, so the result
    is the monomer series flux capped by the supply, which is the wall
    with the element pair removed.
    """

    holder = {"kp": math.inf}

    def kp_of(_name, _temperature_K, vapor_pressure_data=None):
        return holder["kp"]

    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa", kp_of,
    )
    inf_model, inf_kg = _potassium_wall_kg()
    holder["kp"] = 1.0e30
    finite_model, finite_kg = _potassium_wall_kg()
    assert inf_kg > 0.0
    assert finite_kg == pytest.approx(inf_kg, rel=1.0e-12, abs=1.0e-18)
    assert inf_model._dimer_hk_alpha_proxy.get("K2") is None
    assert inf_model._dimer_collision_diameter_proxy.get("K2") is None
    assert finite_model._dimer_hk_alpha_proxy["K2"] == "K"
    assert finite_model._dimer_collision_diameter_proxy["K2"] == (
        DIMER_COLLISION_DIAMETER_PROXY_REASON
    )

    def no_pair(_name):
        return None

    monkeypatch.setattr(
        "simulator.condensation_routing.element_collection_pair", no_pair,
    )
    _green_model, green_kg = _potassium_wall_kg()
    assert inf_kg == pytest.approx(green_kg, rel=1.0e-12, abs=1.0e-18)


@pytest.mark.parametrize("species", ("K", "Na", "Mg"))
def test_infinite_kp_matches_the_shared_series_solver(monkeypatch, species):
    """K_p -> +inf is a test probe, not a production knob.

    The dimer channel is off, so capture is the series-resistance
    linear solver. A species with no element pair uses that same
    function, which is the green path. The two routes share the code,
    so the masses match bit for bit. The 1e-12 relative allowance
    would apply only if the arithmetic order had to differ. It does
    not, on these default routes.
    """

    dimer = f"{species}2"

    def kp_inf(name, temperature_K, vapor_pressure_data=None):
        if name == dimer:
            return math.inf
        return dimer_dissociation_kp_pa(
            name, temperature_K, vapor_pressure_data,
        )

    monkeypatch.setattr(
        "simulator.condensation.dimer_dissociation_kp_pa", kp_inf,
    )
    inf_route = _route({species: 1.0}, partials={species: 1.0})
    notice = inf_route.sticking_alpha_provenance_notice
    assert DIMER_ALPHA_FROM_MONOMER_PROXY not in notice
    assert DIMER_COLLISION_DIAMETER_FROM_MONOMER_PROXY not in notice

    def no_pair(name):
        if name in (species, dimer):
            return None
        return element_collection_pair(name)

    monkeypatch.setattr(
        "simulator.condensation.element_collection_pair", no_pair,
    )
    monkeypatch.setattr(
        "simulator.condensation_routing.element_collection_pair", no_pair,
    )
    plain_route = _route({species: 1.0}, partials={species: 1.0})
    assert _species_capture(inf_route, species) == _species_capture(
        plain_route, species,
    )


def test_hot_wall_with_zero_collector_does_not_invent_the_proxy():
    """1500 C is undersaturated for 10 Pa of K, so the dimer channel stays off."""

    route = _proxy_route("K", "K2", "monomer", "wall_only", wall_c=1500.0)
    assert route.wall_deposit_by_species.get("K", 0.0) == pytest.approx(0.0, abs=0.0)
    assert DIMER_ALPHA_FROM_MONOMER_PROXY not in route.sticking_alpha_provenance_notice
    assert (
        DIMER_COLLISION_DIAMETER_FROM_MONOMER_PROXY
        not in route.sticking_alpha_provenance_notice
    )


@pytest.mark.parametrize(
    "species",
    ("Al", "Al2", "Ca", "Ca2", "Fe", "SiO", "Cr"),
)
def test_uncontracted_species_do_not_publish_a_dimer_alpha_proxy(species):
    route = _route({species: 1.0}, partials={species: 0.1})
    notice = route.sticking_alpha_provenance_notice
    proxy = notice.get(DIMER_ALPHA_FROM_MONOMER_PROXY, {})
    assert species not in proxy
    assert f"{species}2" not in proxy
    diameters = notice.get(DIMER_COLLISION_DIAMETER_FROM_MONOMER_PROXY, {})
    assert species not in diameters
    assert f"{species}2" not in diameters


@pytest.mark.parametrize(
    "species",
    ("Al", "Al2", "Ca", "Ca2", "Fe", "SiO", "Cr", "CrO2", "Mn"),
)
def test_species_outside_a_contracted_pair_are_not_element_arrivals(species):
    from simulator.condensation_routing import element_collection_pair

    assert element_collection_pair(species) is None
    assert species not in {
        name for pair in CONTRACTED_PAIRS for name in pair
    }


def test_route_hands_both_partials_to_monomer_and_dimer_arrivals(monkeypatch):
    """The stage call, not only a direct efficiency call, carries the partner."""

    seen = {}

    def capture(self, **kwargs):
        stage = kwargs["stage"]
        if int(stage.stage_number) == 4 and int(stage.sub_order) == 0:
            seen[kwargs["species"]] = kwargs
        return 0.0

    monkeypatch.setattr(
        "simulator.condensation.CondensationModel._condensation_efficiency",
        capture,
    )
    model = _model(partials={"Na": 0.02, "Na2": 0.03})
    model.pipe_segments = []
    model.route(
        EvaporationFlux(species_kg_hr={"Na": 1.0, "Na2": 1.0}, total_kg_hr=2.0),
        MeltState(temperature_C=1500.0),
    )
    assert seen["Na"]["coexisting_dimer_partial_pa"] == pytest.approx(
        seen["Na2"]["inlet_partial_pressure_pa"]
    )
    assert seen["Na2"]["coexisting_monomer_partial_pa"] == pytest.approx(
        seen["Na"]["inlet_partial_pressure_pa"]
    )
    assert seen["Na"]["coexisting_dimer_partial_pa"] > 0.0
    assert seen["Na2"]["coexisting_monomer_partial_pa"] > 0.0
