"""Rb and Cs stay flagged condensables. Their mass is not vented or zeroed."""

from __future__ import annotations

import pytest

from simulator.condensation import CondensationModel
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


def test_route_flags_rb_and_cs_without_zeroing_or_venting() -> None:
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Rb": 1.0, "Cs": 0.01, "Ga": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    flux = EvaporationFlux(species_kg_hr={"Rb": 0.4, "Cs": 0.2, "Ga": 0.1})
    flux.update_totals()

    result = model.route(flux, MeltState())

    for species, rate_kg_hr in flux.species_kg_hr.items():
        assert result.remaining_by_species[species] == rate_kg_hr
        assert result.retained_in_source_by_species.get(species, 0.0) == 0.0
        assert species not in result.wall_deposit_by_species
        authority = result.condensation_authority_by_species[species]
        assert authority["authoritative_for_terminal_offgas"] is False
        assert authority["authoritative_for_condensation"] is False
        assert authority["input_mass_kg_hr"] == rate_kg_hr
        assert authority["remaining_mass_kg_hr"] == rate_kg_hr
        assert authority["condensed_mass_kg_hr"] == 0.0
    for species in ("Rb", "Cs"):
        refusal = result.condensation_refusals_by_species[species]
        assert refusal["reason"] == "flagged_uncaptured_condensable"
        assert refusal["mass_disposition"] == "flagged_uncaptured_condensable"
        assert refusal["authoritative_for_terminal_offgas"] is False
        assert refusal["remaining_mass_kg_hr"] == flux.species_kg_hr[species]
        assert "lower bound" in refusal["activity_premise"]
        assert "co-condensation is not modelled" in refusal["activity_premise"]
        assert result.condensation_authority_by_species[species][
            "hot_train_applicability"
        ] == "uncaptured_condensable"
    assert (
        result.condensation_authority_by_species["Ga"]["mass_disposition"]
        == "impurity_capture"
    )
    assert (
        result.condensation_authority_by_species["Ga"]["hot_train_applicability"]
        == "applicable"
    )
    assert "flagged_uncaptured_condensable" not in str(
        result.condensation_refusals_by_species.get("Ga", {})
    )


def test_missing_trace_partial_pressure_is_retained_not_a_keyerror() -> None:
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Cs": 0.01},
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    flux = EvaporationFlux(species_kg_hr={"Rb": 0.4})
    flux.update_totals()

    result = model.route(flux, MeltState())

    assert result.remaining_by_species["Rb"] == 0.0
    assert result.retained_in_source_by_species["Rb"] == 0.4
    refusal = result.condensation_refusals_by_species["Rb"]
    assert refusal["reason"] == "wall_species_partial_pressure_missing"
    assert refusal["mass_disposition"] == "retained_in_source_pending_authority"
    assert refusal["retained_in_source_mass_kg_hr"] == 0.4


def test_designated_species_still_require_a_partial_pressure() -> None:
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Rb": 1.0},
        pipe_diameter_m=0.12,
        gas_temperature_C=1700.0,
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    flux = EvaporationFlux(species_kg_hr={"Fe": 0.1})
    flux.update_totals()

    with pytest.raises(ValueError, match="partial pressures"):
        model.route(flux, MeltState())
