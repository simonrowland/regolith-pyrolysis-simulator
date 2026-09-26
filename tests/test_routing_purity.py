import math
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.campaigns import CampaignManager
from simulator.condensation import (
    CondensationModel,
    STAGE3_BYPASS_SEGMENT_NAME,
    STAGE_PURITY_NO_CAPTURED_MASS,
    STAGE_PURITY_VERDICT_INDETERMINATE,
    STAGE_PURITY_VERDICT_PURE,
    stage_purity_report,
)
from simulator.condensation_routing import (
    PRODUCT_DESTINATIONS,
    STAGE_KEY_BY_NUMBER,
    accepted_species_for_stage_number,
    product_stage_number,
    target_species_for_stage_number,
)
from simulator.state import CampaignPhase, CondensationTrain, EvaporationFlux, MeltState


STAGE3_BYPASS_CONFIG = {
    "length_m": 0.75,
    "inner_diameter_m": 0.12,
    "declared_area_m2": 0.28,
    "liner_material": "hot_duct_refractory_liner",
}
SETPOINTS_PATH = Path(__file__).resolve().parents[1] / "data" / "setpoints.yaml"


def _alkali_route_model(route: str):
    train = CondensationTrain.create_default()
    model = CondensationModel(
        train,
        bypass_segment_config=STAGE3_BYPASS_CONFIG,
    )
    melt = MeltState(temperature_C=1650.0)
    melt.oxygen_reservoir.headspace_transport_pO2_bar = 1.0e-6
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"Na": 1.0, "K": 0.5},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in train.stages
        },
        campaign_name="C2A",
        stage3_route=route,
    )
    flux = EvaporationFlux({"Na": 1.0, "K": 0.5})
    flux.update_totals()
    return train, model, melt, flux


@pytest.mark.parametrize("stage_number", [1, 2, 3, 4])
def test_default_train_targets_use_canonical_registry(stage_number):
    train = CondensationTrain.create_default()

    assert train.stages[stage_number].target_species == (
        target_species_for_stage_number(stage_number)
    )


@pytest.mark.parametrize(
    ("recipe", "species", "stage_number"),
    [
        ("MRE", "Fe", 1),
        ("MRE", "Cr", 2),
        ("MRE", "Mn", 2),
        ("MRE", "Mg", 4),
        ("C3", "Cr", 2),
        ("C3", "Ti", None),
        ("C6", "Al", None),
        ("C6", "Si", None),
    ],
)
def test_recipe_product_destinations_are_canonical(recipe, species, stage_number):
    assert recipe in PRODUCT_DESTINATIONS
    assert product_stage_number(recipe, species) == stage_number


def test_stage_purity_report_empty_is_indeterminate_not_pure():
    train = CondensationTrain.create_default()
    empty = stage_purity_report(train)[STAGE_KEY_BY_NUMBER[1]]

    assert empty["total_kg"] == 0
    assert empty["purity_fraction"] is None
    assert empty["verdict"] == STAGE_PURITY_VERDICT_INDETERMINATE
    assert empty["reason"] == STAGE_PURITY_NO_CAPTURED_MASS

    train.stages[1].collected_kg["Fe"] = 1e-9
    tiny = stage_purity_report(train)[STAGE_KEY_BY_NUMBER[1]]

    assert tiny["total_kg"] == pytest.approx(1e-9)
    assert tiny["purity_fraction"] == pytest.approx(1.0)
    assert tiny["verdict"] == STAGE_PURITY_VERDICT_PURE
    assert "reason" not in tiny

    train.stages[1].collected_kg["Fe"] = math.nan
    with pytest.raises(
        ValueError,
        match="stage 1 inventory for Fe must be finite and non-negative",
    ):
        stage_purity_report(train)


def test_stage_purity_report_flags_non_designated_stage_landings():
    train = CondensationTrain.create_default()
    train.stages[1].collected_kg["Fe"] = 9.0
    train.stages[1].collected_kg["SiO2"] = 1.0

    report = stage_purity_report(train)
    stage = report[STAGE_KEY_BY_NUMBER[1]]

    assert stage["accepted_species"] == sorted(
        accepted_species_for_stage_number(1))
    assert stage["designated_species_kg"] == {"Fe": 9.0}
    assert stage["impurity_species_kg"] == {"SiO2": 1.0}
    assert stage["purity_fraction"] == pytest.approx(0.9)
    assert stage["verdict"] == "MIXED"


def test_stage_purity_activity_uses_explicit_accepted_species_capture_state():
    train = CondensationTrain.create_default()
    train.stages[2].collected_kg.update({"Cr": 1.0, "Mn": 0.0})

    stage = stage_purity_report(train)[STAGE_KEY_BY_NUMBER[2]]

    assert stage["activity"] == {"Cr": True, "Mn": False}
    assert "Cr2O3" not in stage["activity"]
    assert "activity" not in stage_purity_report(train)[STAGE_KEY_BY_NUMBER[3]]


def test_route_result_records_scaled_stage_impurity_without_changing_capture():
    train = CondensationTrain.create_default()
    model = CondensationModel(train)
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={"K": 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in train.stages
        },
    )
    flux = EvaporationFlux({"K": 1.0})
    flux.update_totals()

    route = model.route(flux, MeltState(temperature_C=1650.0))
    condensed = route.condensed_for_species("K")
    impurity = sum(
        species_kg.get("K", 0.0)
        for stage_number, species_kg in route.impurity_by_stage_species.items()
        if stage_number != 4
    )

    assert condensed > 0.0
    assert impurity >= 0.0
    total_deposited = condensed + route.wall_deposit_by_species.get("K", 0.0)
    assert route.remaining_by_species["K"] == pytest.approx(1.0 - total_deposited)


def test_stage3_divert_skips_silica_and_conserves_alkali_to_stage4():
    train, model, melt, flux = _alkali_route_model("divert")

    route = model.route(flux, melt)

    assert route.stage3_route == "divert"
    assert route.condensed_by_stage_species.get(3, {}) == {}
    assert route.condensed_by_stage_species[4]["Na"] > 0.0
    assert route.condensed_by_stage_species[4]["K"] > 0.0
    assert route.diverted_flow_by_species_kg_hr["Na"] > 0.0
    assert route.diverted_flow_by_species_kg_hr["K"] > 0.0
    assert any(
        segment.name == STAGE3_BYPASS_SEGMENT_NAME
        for segment in model._route_pipe_segments()
    )
    for species, input_kg in flux.species_kg_hr.items():
        deposited_kg = sum(
            stage_species.get(species, 0.0)
            for stage_species in route.condensed_by_stage_species.values()
        )
        deposited_kg += route.wall_deposit_by_species.get(species, 0.0)
        deposited_kg += route.remaining_by_species.get(species, 0.0)
        deposited_kg += route.retained_in_source_by_species.get(species, 0.0)
        assert deposited_kg == pytest.approx(input_kg, abs=1.0e-12)


def test_stage3_through_exposes_alkali_and_surfaces_d025_finding():
    train, model, melt, flux = _alkali_route_model("through")

    route = model.route(flux, melt)

    assert route.condensed_by_stage_species[3]["Na"] > 0.0
    assert route.condensed_by_stage_species[3]["K"] > 0.0
    assert "silica_exposed_to_alkali" in route.stage3_route_diagnostic[
        "finding_keys"
    ]
    report = stage_purity_report(train, route.stage3_route_diagnostic)
    assert report[STAGE_KEY_BY_NUMBER[3]]["finding_keys"] == [
        "silica_exposed_to_alkali"
    ]


def test_stage3_open_knob_uses_existing_alkali_hard_finding():
    setpoints = deepcopy(yaml.safe_load(SETPOINTS_PATH.read_text()))
    setpoints["campaigns"]["C2A_continuous"]["stage3_open_T_C"] = 1300
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=1400.0)
    resolution = manager.stage3_route_resolution_for(melt)
    assert resolution["stage3_route"] == "through"

    train, model, routed_melt, flux = _alkali_route_model("through")
    routed_melt.campaign = CampaignPhase.C2A
    routed_melt.temperature_C = 1400.0
    model.configure_operating_conditions(stage3_route_basis=resolution)
    route = model.route(flux, routed_melt)

    assert route.stage3_route_diagnostic["route_basis"] == "temperature_window"
    assert route.stage3_route_diagnostic["stage3_open_T_C"] == pytest.approx(1300.0)
    assert "silica_exposed_to_alkali" in route.stage3_route_diagnostic[
        "finding_keys"
    ]


def test_stage3_open_knob_with_no_alkali_is_through_without_hard_finding():
    setpoints = deepcopy(yaml.safe_load(SETPOINTS_PATH.read_text()))
    setpoints["campaigns"]["C2A_continuous"].update({
        "stage3_open_T_C": 1500,
        "stage3_close_T_C": 1600,
    })
    manager = CampaignManager(setpoints)
    melt = MeltState(campaign=CampaignPhase.C2A, temperature_C=1550.0)
    resolution = manager.stage3_route_resolution_for(melt)
    assert resolution["stage3_route"] == "through"

    train = CondensationTrain.create_default()
    model = CondensationModel(train, bypass_segment_config=STAGE3_BYPASS_CONFIG)
    model.configure_operating_conditions(
        overhead_pressure_mbar=10.0,
        species_partial_pressures_mbar={"SiO": 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in train.stages
        },
        stage3_route=resolution["stage3_route"],
        stage3_route_basis=resolution,
    )
    flux = EvaporationFlux({"SiO": 1.0})
    flux.update_totals()
    route = model.route(flux, melt)

    assert route.stage3_route_diagnostic["route_basis"] == "temperature_window"
    assert route.stage3_route_diagnostic["stage3_open_T_C"] == pytest.approx(1500.0)
    assert "silica_exposed_to_alkali" not in route.stage3_route_diagnostic[
        "finding_keys"
    ]
