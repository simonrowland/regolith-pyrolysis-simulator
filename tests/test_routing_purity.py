import pytest

from simulator.condensation import CondensationModel, stage_purity_report
from simulator.condensation_routing import (
    PRODUCT_DESTINATIONS,
    STAGE_KEY_BY_NUMBER,
    accepted_species_for_stage_number,
    product_stage_number,
    target_species_for_stage_number,
)
from simulator.state import CondensationTrain, EvaporationFlux, MeltState


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


def test_route_result_records_scaled_stage_impurity_without_changing_capture():
    train = CondensationTrain.create_default()
    model = CondensationModel(train)
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
