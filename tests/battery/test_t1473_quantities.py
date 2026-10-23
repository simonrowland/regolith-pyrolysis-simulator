"""Migrator and scorer for the three t-1473 quantity tokens."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from simulator.battery.enums import (
    AmountBasis,
    Engine,
    ExecutionState,
    IdentityEqualKind,
    Phase,
    Quantity,
    RefusalReason,
    ValueKind,
)
from simulator.battery.identity import (
    Exposure,
    SweepIdentity,
    identity_equal,
    quantity_token,
)
from simulator.battery.migrate import (
    fill_identity,
    make_species,
    map_quantity,
)
from simulator.battery.records import Composition, State
from simulator.battery.score import (
    EVAPORATION_FLUX_SCORER_PENDING,
    alpha_evidence_catalog,
    element_fraction_remaining_ratio,
    evaporation_flux_rail,
    predict_with_engine,
    rail_for_quantity,
)
from tests.battery.test_migrate import (
    _migrate_real_extract,
    _scalar_extract,
    _write_min_tree,
    migrate,
)


def _one(result, suffix: str):
    matches = [
        obs
        for obs in result.observations.values()
        if obs.observation_id == suffix or obs.observation_id.endswith(f"::{suffix}")
    ]
    assert len(matches) == 1, [obs.observation_id for obs in result.observations.values()]
    return matches[0]


def _migrate_one(tmp_path: Path, **kwargs):
    extract = _scalar_extract(**kwargs)
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    return _one(result, "na_psat"), result


def test_bare_alpha_stays_evaporation_coefficient() -> None:
    state, reason = map_quantity("alpha", {"alpha": "0.95"}, units="dimensionless")
    assert reason is None
    assert state.value is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    state, reason = map_quantity(
        None, {"quantity": "condensation_coefficient", "alpha": "0.2", "activity": "0.2"}
    )
    assert reason is None
    assert state.value is Quantity.CONDENSATION_COEFFICIENT


def test_condensation_point_binds_alpha_not_the_range(tmp_path: Path) -> None:
    obs, _result = _migrate_one(
        tmp_path,
        quantity="condensation_coefficient",
        units="dimensionless",
        values={
            "quantity": "condensation_coefficient",
            "alpha": "0.998",
            "alpha_range": ["0.9975", "0.9985"],
            "gas_species": "W(g)",
            "substrate": "polycrystalline_W",
            "technique": "Mass spectrometry",
            "vacuum": "1.0e-6 torr",
            "method_class": "review_compilation",
        },
        obs_type="alpha",
    )
    assert quantity_token(obs.identity) is Quantity.CONDENSATION_COEFFICIENT
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == Decimal("0.998")
    assert obs.identity.species.formula == "W"
    assert obs.identity.species.phase.value is Phase.G
    assert obs.identity.wall.value.material.value == "polycrystalline_W"
    # 1e-6 torr × 101325 Pa/atm / 760 torr/atm.
    assert obs.identity.total_pressure_Pa.value == (
        Decimal("1e-6") * Decimal("101325") / Decimal("760")
    )
    assert obs.provenance["technique"] == "Mass spectrometry"
    assert obs.provenance["substrate_phase"] == "gas"


def test_condensation_bound_is_a_bound_not_a_categorical(tmp_path: Path) -> None:
    obs, _result = _migrate_one(
        tmp_path,
        quantity="condensation_coefficient",
        units="dimensionless",
        values={
            "quantity": "condensation_coefficient",
            "alpha_lower_bound": "0.998",
            "printed": ">0.998",
            "semantics": "bound_not_point_ordering",
            "gas_species": "Pt(g)",
            "substrate": "polycrystalline_Pt",
            "vacuum": "1.0e-6 torr",
            "method_class": "review_compilation",
        },
        obs_type="alpha",
    )
    assert obs.value.kind is ValueKind.BOUND
    assert obs.value.bound_operator == ">"
    assert obs.value.bound_value == Decimal("0.998")
    assert obs.identity.species.formula == "Pt"


def test_evaporation_flux_keeps_printed_number_and_unit(tmp_path: Path) -> None:
    obs, _result = _migrate_one(
        tmp_path,
        quantity="evaporation_flux",
        units="mol m^-2 s^-1",
        values={
            "quantity": "evaporation_flux",
            "evaporation_flux": "1.2e-5",
            "gas_species": "FeO",
            "composition_as_printed": "printed melt analysis",
            "P_total": "1.0",
            "P_total_unit": "bar",
            "atmosphere": "air",
            "flow": {
                "flow_rate": {"value": "10", "unit": "sccm"},
                "velocity": {"state": "not_printed"},
                "geometry": {"value": "open crucible"},
                "crucible_or_sample_area": {"value": "1.5", "unit": "cm^2"},
            },
            "method_class": "measured_direct",
        },
        obs_type="rate_series",
    )
    assert quantity_token(obs.identity) is Quantity.EVAPORATION_FLUX
    assert obs.identity.species.formula == "FeO"
    assert obs.value.kind is ValueKind.POINT
    assert obs.value.point == Decimal("1.2e-5")
    assert obs.identity.subtype.value == "mol_m-2_s-1"
    assert obs.provenance["printed_unit"] == "mol m^-2 s^-1"
    assert obs.provenance["flow"]["velocity"] == {"state": "not_printed"}
    assert "value" not in obs.provenance["flow"]["velocity"]
    assert obs.identity.total_pressure_Pa.value == Decimal("100000")
    assert obs.identity.exposure.value.area_m2.value == Decimal("1.5") / Decimal("10000")
    assert obs.identity.sweep_gas.value.flow_sccm.value == Decimal("10")
    assert obs.identity.composition.is_unknown


def test_mass_loss_rate_does_not_become_evaporation_flux() -> None:
    state, reason = map_quantity(
        "rate_series",
        {"mass_loss_rate": "1.0", "method_class": "measured_direct"},
        units="kg s^-1",
    )
    assert state.value is Quantity.MASS_LOSS_RATE
    assert reason is None


def test_element_fraction_binds_basis_normaliser_and_note(tmp_path: Path) -> None:
    obs, _result = _migrate_one(
        tmp_path,
        quantity="element_fraction_remaining",
        units="dimensionless",
        values={
            "quantity": "element_fraction_remaining",
            "element": "K",
            "fraction_remaining": "0.5",
            "basis": "mass",
            "basis_as_printed": "K/K initial by mass",
            "normalising_element": "Ca",
            "retention_assumption": "Ca is fully retained",
            "starting_composition": {"K": "2", "Ca": "4"},
            "duration": {"value": "60", "unit": "min"},
            "atmosphere": "H2/CO2",
            "sample_mass": {"value": "20", "unit": "mg"},
            "sample_geometry": {"state": "not_printed"},
            "method_class": "author_derived",
            "provenance": {"predicted_residue_amount": {"K": "1", "Ca": "4"}},
        },
        obs_type="concentration_series",
    )
    assert quantity_token(obs.identity) is Quantity.ELEMENT_FRACTION_REMAINING
    assert obs.identity.species.formula == "K"
    assert obs.value.point == Decimal("0.5")
    assert obs.identity.subtype.value == "mass"
    assert obs.identity.normalising_element.value == "Ca"
    assert obs.provenance["basis_as_printed"] == "K/K initial by mass"
    assert obs.provenance["retention_assumption"] == "Ca is fully retained"
    assert "predicted_residue_amount" not in obs.provenance
    assert obs.identity.sample_mass_kg.value == Decimal("20") / Decimal("1000000")
    assert obs.identity.exposure.value.duration_s.value == Decimal("3600")
    assert obs.identity.composition.value.as_map()["K"] == Decimal("2")


def test_omitted_basis_is_not_applicable_and_absolute_has_no_normaliser(
    tmp_path: Path,
) -> None:
    obs, _result = _migrate_one(
        tmp_path,
        quantity="element_fraction_remaining",
        units="dimensionless",
        values={
            "quantity": "element_fraction_remaining",
            "element": "Mg",
            "fraction_remaining": "0.8",
            "basis_as_printed": "not stated",
            "starting_composition": {"Mg": "1"},
            "method_class": "measured_direct",
        },
        obs_type="concentration_series",
    )
    assert obs.identity.subtype.is_not_applicable
    assert obs.identity.normalising_element.is_not_applicable
    assert obs.provenance["basis_as_printed"] == "not stated"


def test_temperature_and_point_conditions_reach_the_observation(tmp_path: Path) -> None:
    extract = _scalar_extract(
        quantity="element_fraction_remaining",
        units="dimensionless",
        values={
            "quantity": "element_fraction_remaining",
            "element": "Mg",
            "fraction_remaining": "1",
            "basis": "atom",
            "basis_as_printed": "atom",
            "method_class": "measured_direct",
        },
        obs_type="concentration_series",
    )
    row = extract["species"]["Na"]["observations"][0]
    row["T_C"] = 1400
    row["point_conditions"] = {
        "atmosphere": {
            "state": {"tag": "value", "value": "H2/CO2"},
            "locator": {"table": "1", "page": 4},
        }
    }
    result = migrate(_write_min_tree(tmp_path, extract), write=False)
    observations = list(result.observations.values())
    assert observations
    for obs in observations:
        assert obs.identity.temperature_K.value == Decimal("1400") + Decimal("273.15")
        located = obs.point_conditions["atmosphere"]
        assert located.state.value == "H2/CO2"
        assert located.locator.table == "1"
        assert located.locator.page == 4


def _fraction_identity(*, normaliser: State[str]) -> object:
    species = make_species("K", Phase.L)
    composition = Composition(
        basis="printed_element_amount",
        components=(("K", Decimal("2")), ("Ca", Decimal("4"))),
        amount_basis=AmountBasis.MOL_INVENTORY,
    )
    return fill_identity(
        Quantity.ELEMENT_FRACTION_REMAINING,
        species,
        subtype=State.not_applicable("basis omitted"),
        normalising_element=normaliser,
        temperature_K=State.of(Decimal("1673.15")),
        composition=State.of(composition),
        total_pressure_Pa=State.of(Decimal("100000")),
        sample_mass_kg=State.of(Decimal("0.00002")),
        sweep_gas=State.of(
            SweepIdentity(
                species="H2/CO2",
                flow_sccm=State.of(Decimal("10")),
                partial_pressure_Pa=State.of(Decimal("1000")),
            )
        ),
        exposure=State.of(
            Exposure(
                area_m2=State.of(Decimal("0.00015")),
                duration_s=State.of(Decimal("3600")),
            )
        ),
    )


def test_normalised_and_absolute_fraction_identities_are_not_equal() -> None:
    absolute = _fraction_identity(
        normaliser=State.not_applicable("source does not print a normalised ratio")
    )
    normalised = _fraction_identity(normaliser=State.of("Ca"))
    outcome = identity_equal(absolute, normalised)
    assert outcome.kind is IdentityEqualKind.IDENTITY_MISMATCH
    assert "normalising_element" in outcome.fields
    same = identity_equal(absolute, _fraction_identity(
        normaliser=State.not_applicable("source does not print a normalised ratio")
    ))
    assert same.kind is IdentityEqualKind.EQUAL


def test_normaliser_mutation_makes_the_identities_equal() -> None:
    absolute = _fraction_identity(
        normaliser=State.not_applicable("source does not print a normalised ratio")
    )
    normalised = _fraction_identity(normaliser=State.of("Ca"))
    assert identity_equal(absolute, normalised).kind is not IdentityEqualKind.EQUAL
    mutated = replace(absolute, normalising_element=State.of("Ca"))
    assert identity_equal(mutated, normalised).kind is IdentityEqualKind.EQUAL


def test_fraction_ratio_matches_the_printed_form() -> None:
    # Premise: the source printed a remaining fraction. Absolute rows are
    # n_X(t)/n_X(0). Normalised rows are (n_X(t)/n_N(t)) / (n_X(0)/n_N(0)).
    # Algebra, start K=2, Ca=4, residue K=1, Ca=4:
    #   absolute f_K = 1/2 = 0.5
    #   normalised (1/4) / (2/4) = 0.5
    # They match here only because Ca is unchanged.
    # Mutation, residue Ca=2:
    #   absolute stays 1/2 = 0.5
    #   normalised (1/2) / (2/4) = 1
    # A scorer that assumed Ca retained would still report 0.5.
    # Units: amount/amount, dimensionless.
    # Sanity: no evaporation, n(t)=n(0), f = 1 on both forms.
    start = {"K": Decimal("2"), "Ca": Decimal("4")}
    residue = {"K": Decimal("1"), "Ca": Decimal("4")}
    absolute, _detail = element_fraction_remaining_ratio(
        element="K",
        normalising_element=None,
        start_amounts=start,
        residue_amounts=residue,
    )
    normalised, _detail = element_fraction_remaining_ratio(
        element="K",
        normalising_element="Ca",
        start_amounts=start,
        residue_amounts=residue,
    )
    assert absolute == Decimal("0.5")
    assert normalised == Decimal("0.5")
    unchanged, _detail = element_fraction_remaining_ratio(
        element="K",
        normalising_element="Ca",
        start_amounts=start,
        residue_amounts={"K": Decimal("2"), "Ca": Decimal("4")},
    )
    assert unchanged == Decimal("1")


def test_normaliser_amount_mutation_moves_only_the_normalised_ratio() -> None:
    start = {"K": Decimal("2"), "Ca": Decimal("4")}
    mutated_residue = {"K": Decimal("1"), "Ca": Decimal("2")}
    absolute, _detail = element_fraction_remaining_ratio(
        element="K",
        normalising_element=None,
        start_amounts=start,
        residue_amounts=mutated_residue,
    )
    normalised, _detail = element_fraction_remaining_ratio(
        element="K",
        normalising_element="Ca",
        start_amounts=start,
        residue_amounts=mutated_residue,
    )
    assert absolute == Decimal("0.5")
    assert normalised == Decimal("1")


def test_fraction_scorer_uses_the_predicted_residue_not_a_retention_assumption(
    tmp_path: Path,
) -> None:
    normalised, _result = _migrate_one(
        tmp_path / "norm",
        quantity="element_fraction_remaining",
        units="dimensionless",
        values={
            "quantity": "element_fraction_remaining",
            "element": "K",
            "fraction_remaining": "0.5",
            "basis_as_printed": "(K/Ca)/(K/Ca)_0",
            "normalising_element": "Ca",
            "retention_assumption": "Ca is fully retained",
            "starting_composition": {"K": "2", "Ca": "4"},
            "method_class": "author_derived",
        },
        obs_type="concentration_series",
    )
    absolute, _result = _migrate_one(
        tmp_path / "abs",
        quantity="element_fraction_remaining",
        units="dimensionless",
        values={
            "quantity": "element_fraction_remaining",
            "element": "K",
            "fraction_remaining": "0.5",
            "basis_as_printed": "K remaining",
            "starting_composition": {"K": "2", "Ca": "4"},
            "method_class": "measured_direct",
        },
        obs_type="concentration_series",
    )
    residue = {"K": "1", "Ca": "4"}
    normalised = replace(
        normalised,
        provenance={**(normalised.provenance or {}), "predicted_residue_amount": residue},
    )
    absolute = replace(
        absolute,
        provenance={**(absolute.provenance or {}), "predicted_residue_amount": residue},
    )
    produced = predict_with_engine(Engine.INTERNAL_ANALYTICAL, normalised)
    assert produced.execution.state is ExecutionState.PRODUCED
    assert produced.value == Decimal("0.5")
    mutated = replace(
        normalised,
        provenance={
            **normalised.provenance,
            "predicted_residue_amount": {"K": "1", "Ca": "2"},
        },
    )
    moved = predict_with_engine(Engine.INTERNAL_ANALYTICAL, mutated)
    assert moved.value == Decimal("1")
    absolute_mutated = replace(
        absolute,
        provenance={
            **(absolute.provenance or {}),
            "predicted_residue_amount": {"K": "1", "Ca": "2"},
        },
    )
    stayed = predict_with_engine(Engine.INTERNAL_ANALYTICAL, absolute_mutated)
    assert stayed.value == Decimal("0.5")


def test_fraction_refusals(tmp_path: Path) -> None:
    obs, _result = _migrate_one(
        tmp_path,
        quantity="element_fraction_remaining",
        units="dimensionless",
        values={
            "quantity": "element_fraction_remaining",
            "element": "K",
            "fraction_remaining": "0.5",
            "basis_as_printed": "K remaining",
            "method_class": "measured_direct",
        },
        obs_type="concentration_series",
    )
    missing_start = predict_with_engine(Engine.INTERNAL_ANALYTICAL, obs)
    assert missing_start.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert missing_start.refusal_detail["reason"] == "starting_composition_missing"
    assert missing_start.value is None
    supplied = replace(
        obs,
        provenance={
            **(obs.provenance or {}),
            "predicted_residue_amount": {"Ca": "1"},
        },
    )
    # Starting composition is still missing, so the element check is not reached.
    assert predict_with_engine(Engine.INTERNAL_ANALYTICAL, supplied).refusal_detail[
        "reason"
    ] == "starting_composition_missing"
    value, detail = element_fraction_remaining_ratio(
        element="K",
        normalising_element=None,
        start_amounts={"K": Decimal("0")},
        residue_amounts={"K": Decimal("1")},
    )
    assert value is None
    assert detail["reason"] == "element_amount_not_positive"
    value, detail = element_fraction_remaining_ratio(
        element="K",
        normalising_element="Ca",
        start_amounts={"K": Decimal("2"), "Ca": Decimal("4")},
        residue_amounts={"K": Decimal("1")},
    )
    assert value is None
    assert detail == {"reason": "element_not_in_residue", "element": "Ca"}


def test_condensation_is_evidence_only_and_catalogued_apart_from_evaporation_alpha(
    tmp_path: Path,
) -> None:
    condensation, _result = _migrate_one(
        tmp_path / "cond",
        quantity="condensation_coefficient",
        units="dimensionless",
        values={
            "quantity": "condensation_coefficient",
            "alpha": "0.998",
            "gas_species": "W(g)",
            "substrate": "polycrystalline_W",
            "vacuum": "1.0e-6 torr",
            "method_class": "review_compilation",
        },
        obs_type="alpha",
    )
    evaporation, _result = _migrate_one(
        tmp_path / "evap",
        quantity="evaporation_coefficient_alpha",
        units="dimensionless",
        values={
            "quantity": "evaporation_coefficient_alpha",
            "alpha": "0.95",
            "gas_species": "K(g)",
            "method_class": "review_compilation",
        },
        obs_type="alpha",
    )
    prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, condensation)
    assert prediction.value is None
    assert prediction.refusal_reason is RefusalReason.UNSUPPORTED
    assert prediction.refusal_detail["reason"] == "not_scored"
    assert prediction.refusal_detail["outcome"] == "evidence_only"
    assert prediction.refusal_detail["catalog"] == "alpha_evidence"
    catalog = alpha_evidence_catalog((condensation, evaporation))
    assert [row["quantity"] for row in catalog] == [
        "condensation_coefficient",
        "evaporation_coefficient_alpha",
    ]
    assert catalog[0]["identity"] != catalog[1]["identity"]
    alpha_prediction = predict_with_engine(Engine.INTERNAL_ANALYTICAL, evaporation)
    assert alpha_prediction.refusal_detail["reason"] == "quantity_not_predicted"


def test_evaporation_flux_refuses_missing_flow_then_species_then_pending_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("legacy pipe-length flux was called")

    monkeypatch.setattr(
        "engines.builtin.evaporation_flux._series_resistance_evaporation_flux_kg_m2_s",
        boom,
    )
    missing, _result = _migrate_one(
        tmp_path / "missing",
        quantity="evaporation_flux",
        units="kg m-2 s-1",
        values={
            "quantity": "evaporation_flux",
            "evaporation_flux": "3",
            "gas_species": "Na",
            "atmosphere": "air",
            "method_class": "measured_direct",
        },
        obs_type="rate_series",
    )
    refused = predict_with_engine(Engine.INTERNAL_ANALYTICAL, missing)
    assert refused.value is None
    assert refused.refusal_reason is RefusalReason.IDENTITY_INCOMPLETE
    assert refused.refusal_detail["reason"] == "missing_input"
    assert "velocity" in refused.refusal_detail["missing"]

    def complete(species: str):
        return {
            "quantity": "evaporation_flux",
            "evaporation_flux": "3",
            "gas_species": species,
            "composition_as_printed": {"Na": "1"},
            "P_total": "1",
            "P_total_unit": "bar",
            "atmosphere": "air",
            "flow": {
                "flow_rate": {"value": "10", "unit": "sccm"},
                "velocity": {"value": "0.2", "unit": "m/s"},
                "geometry": {"value": "crucible"},
                "crucible_or_sample_area": {"value": "1", "unit": "cm^2"},
            },
            "method_class": "measured_direct",
        }

    sodium, _result = _migrate_one(
        tmp_path / "na",
        quantity="evaporation_flux",
        units="kg m-2 s-1",
        values=complete("Na"),
        obs_type="rate_series",
    )
    pending = predict_with_engine(Engine.INTERNAL_ANALYTICAL, sodium)
    assert pending.value is None
    assert pending.refusal_detail["reason"] == EVAPORATION_FLUX_SCORER_PENDING
    assert pending.refusal_detail["rail"] == "pyrolysis_yield"
    assert rail_for_quantity(Quantity.EVAPORATION_FLUX, species_formula="Na") is not None
    assert evaporation_flux_rail("SiO").value == "SiO_evolution"
    oxide, _result = _migrate_one(
        tmp_path / "feo",
        quantity="evaporation_flux",
        units="mol m^-2 s^-1",
        values=complete("FeO"),
        obs_type="rate_series",
    )
    outside = predict_with_engine(Engine.INTERNAL_ANALYTICAL, oxide)
    assert outside.refusal_reason is RefusalReason.OUTSIDE_SUPPORTED_SPECIES
    assert oxide.identity.species.formula == "FeO"
    sio, _result = _migrate_one(
        tmp_path / "sio",
        quantity="evaporation_flux",
        units="mol m^-2 s^-1",
        values=complete("SiO"),
        obs_type="rate_series",
    )
    sio_pending = predict_with_engine(Engine.INTERNAL_ANALYTICAL, sio)
    assert sio_pending.refusal_detail["reason"] == EVAPORATION_FLUX_SCORER_PENDING
    assert sio_pending.refusal_detail["rail"] == "SiO_evolution"


def test_pound_1972_condensation_rows_and_potassium_alpha(tmp_path: Path) -> None:
    result = _migrate_real_extract(tmp_path, "kems-003-pound-1972.yaml")
    platinum = _one(result, "pound_1972_pt_condensation_alpha_chupka_1500k")
    rhodium = _one(result, "pound_1972_rh_condensation_alpha_chupka_1500k")
    tungsten = _one(result, "pound_1972_w_condensation_alpha_chupka_2200k")
    potassium = _one(result, "pound_1972_k_liquid_alpha_neumann")
    assert platinum.value.kind is ValueKind.BOUND
    assert platinum.value.bound_value == Decimal("0.998")
    assert rhodium.value.kind is ValueKind.BOUND
    assert rhodium.value.bound_value == Decimal("0.99")
    assert tungsten.value.kind is ValueKind.POINT
    assert tungsten.value.point == Decimal("0.998")
    assert tungsten.identity.temperature_K.value == Decimal("2200")
    assert platinum.identity.temperature_K.value == Decimal("1500")
    for obs in (platinum, rhodium, tungsten):
        assert quantity_token(obs.identity) is Quantity.CONDENSATION_COEFFICIENT
    assert quantity_token(potassium.identity) is Quantity.EVAPORATION_COEFFICIENT_ALPHA
    assert potassium.value.point == Decimal("0.95")
