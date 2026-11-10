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


# Invariant. Every contracted metal element (Na, K, Mg and their dimers)
# is admitted and drawn down by one element-equilibrium criterion, on
# every capture channel. p_elem = p_M + 2 p_M2 is conserved. The gas
# re-speciates at the wall, p_M is compared with P_sat,M, and the captured
# fraction of that element inventory is (p_elem - p_elem,sat) / p_elem with
# p_elem,sat = P_sat + 2 P_sat^2 / K_p. The linear fraction
# (p_M - P_sat) / p_M is not the capture. Species outside a contracted
# pair are not on this criterion. A missing K_p stays the flagged
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
        k_monomer, k_dimer = _stage_hk_coefficients(
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


def _parallel_element_rate(pressure, kp, psat, k_monomer, k_dimer):
    """Independent J_M + 2 J_M2. Not the production integrator."""

    monomer = (2.0 * pressure) / (math.sqrt(1.0 + 8.0 * pressure / kp) + 1.0)
    drive = monomer - psat
    if drive <= 0.0:
        return 0.0
    dimer_drive = drive * (monomer + psat) / kp
    return -(k_monomer * drive + 2.0 * k_dimer * dimer_drive)


def _rk4_element_partial(p_elem, kp, psat, k_monomer, k_dimer, duration, steps=20000):
    """Independent integral of the parallel Hertz-Knudsen element rate."""

    dt = duration / steps
    pressure = p_elem
    floor = psat + 2.0 * psat * psat / kp
    for _ in range(steps):
        k1 = _parallel_element_rate(pressure, kp, psat, k_monomer, k_dimer)
        if k1 == 0.0 or pressure + dt * k1 <= floor:
            return floor
        k2 = _parallel_element_rate(
            pressure + dt * k1 / 2.0, kp, psat, k_monomer, k_dimer,
        )
        k3 = _parallel_element_rate(
            pressure + dt * k2 / 2.0, kp, psat, k_monomer, k_dimer,
        )
        k4 = _parallel_element_rate(
            pressure + dt * k3, kp, psat, k_monomer, k_dimer,
        )
        updated = pressure + dt * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        if updated <= floor:
            return floor
        pressure = updated
    return pressure


def _stage_hk_coefficients(
    model, stage, species, monomer, dimer, flowing, available_kg, residence_s, alpha,
):
    """q * (A/n) * alpha * J(1 Pa) for the monomer and the dimer."""

    from simulator.condensation import (
        AVOGADRO_MOL,
        CELSIUS_TO_KELVIN_OFFSET,
        HKL_BAND_SAMPLES,
        _hkl_impingement_flux_mol_m2_s,
        _molecular_mass_kg_per_molecule,
    )

    molar_mass = _molecular_mass_kg_per_molecule(
        species, vapor_pressure_data=model.vapor_pressure_data,
    ) * AVOGADRO_MOL
    inventory_mol = available_kg / molar_mass / 3600.0 * residence_s
    width = stage.temp_range_C[1] - stage.temp_range_C[0]
    sample_count = 1 if width == 0.0 else HKL_BAND_SAMPLES
    area = model._stage_area_m2_for_stage(stage)
    scale = (flowing / inventory_mol) * area / sample_count * alpha
    temperature_K = model.gas_temperature_C + CELSIUS_TO_KELVIN_OFFSET
    return (
        scale * _hkl_impingement_flux_mol_m2_s(
            monomer, 1.0, temperature_K,
            vapor_pressure_data=model.vapor_pressure_data,
        ),
        scale * _hkl_impingement_flux_mol_m2_s(
            dimer, 1.0, temperature_K,
            vapor_pressure_data=model.vapor_pressure_data,
        ),
    )


@pytest.mark.parametrize("monomer,dimer", CONTRACTED_PAIRS)
@pytest.mark.parametrize("arrival", ("monomer", "dimer"))
def test_stage_drawdown_follows_the_element_inventory_ode(
    monkeypatch, monomer, dimer, arrival,
):
    """Finite residence follows J_M + 2 J_M2, not a jump to p_elem,sat.

    The series-resistance monkeypatch still supplies P_sat. It does not
    set the rate: a monomer-only drive fails this integral.
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
    k_monomer, k_dimer = _stage_hk_coefficients(
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


def test_janaf_kp_at_313_K_is_in_band_and_replaces_the_cea_polynomial():
    """Primary table. 313.15 K is inside the printed JANAF grid.

    Adjacent rows are 300 K and 350 K, except K2(g), which has no 350 K
    row and is interpolated between 300 K and 400 K. Fail-before on the
    CEA path: dimer_dissociation_kp_pa('K2', 313.15) returned
    1.325277167193154 Pa. That figure is the in-band 300-1000 K NASA-9
    polynomial, not a high-T extrapolation, and it is the wrong source.
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

    # Independent evaluation of the printed rows. K2 at 313.15 K is 0.461 Pa,
    # not the CEA 1.325277 Pa.
    assert dimer_dissociation_kp_pa("K2", 313.15) == pytest.approx(
        independent_kp("K-005", "K-011", 313.15), rel=1.0e-12,
    )
    assert dimer_dissociation_kp_pa("K2", 313.15) == pytest.approx(
        0.4610863442751231, rel=1.0e-12,
    )
    assert dimer_dissociation_kp_pa("K2", 313.15) != pytest.approx(
        1.325277167193154, rel=1.0e-4,
    )
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
