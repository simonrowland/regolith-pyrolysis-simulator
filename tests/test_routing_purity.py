import math

import pytest

from simulator.condensation import (
    CondensationModel,
    STAGE_PURITY_NO_CAPTURED_MASS,
    STAGE_PURITY_VERDICT_INDETERMINATE,
    STAGE_PURITY_VERDICT_PURE,
    stage_purity_report,
    stage_visit_key,
)
from simulator.condensation_routing import (
    DESIGNATED_STAGE,
    K_TRAP_STAGE_KEY,
    NA_COLLECTOR_STAGE_KEY,
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


def test_default_k_route_allocation_numeric_pin():
    """Deterministic cb13f9f92 output pin, not a physics calibration target."""
    model = CondensationModel(CondensationTrain.create_default())
    assert tuple(stage_purity_report(model.train)) == (
        'stage_0', 'stage_1_fe_condenser', 'stage_2_cr_oxide_harvest',
        'stage_3_sio_zone', 'stage_4_alkali_mg_cyclone',
        'stage_5', 'stage_6', 'stage_7', 'stage_4b_k_trap',
    )
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={'K': 1.0},
        stage_area_m2_by_stage={str(s.stage_number): 1.0 for s in model.train.stages},
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={'K': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1650.0),
    )
    assert route.condensed_by_stage_species == {
        4: {'K': 0.999966157780451},
        5: {'K': 2.7683081079644686e-05},
        6: {'K': 6.1577634990554845e-06},
    }
    assert route.remaining_by_species == {'K': 1.374970351584895e-09}
    assert route.wall_deposit_by_species == {}
    assert route.impurity_by_stage_species == {
        5: {'K': 2.7683081079644686e-05},
        6: {'K': 6.1577634990554845e-06},
    }


def _visit_labels(train: CondensationTrain) -> list[int | str]:
    labels: list[int | str] = []
    for stage in sorted(train.stages, key=stage_visit_key):
        if stage.stage_number == 0 and stage.sub_order == 0:
            continue
        if stage.stage_key == K_TRAP_STAGE_KEY:
            labels.append('4b')
        else:
            labels.append(stage.stage_number)
    return labels


def test_route_visits_k_trap_between_stage_4_and_stage_5():
    """Visit order is route()'s (stage_number, sub_order) sort.

    Stage 0 is walked first and is not part of the pinned product order.
    Setting the trap's stage_number above 7 is the mutation that moves 4b
    after stage 7; sub_order alone cannot, because (4, n) sorts before 5.
    """

    train = CondensationTrain.create_default()
    assert [stage.stage_number for stage in train.stages[:8]] == list(range(8))
    assert train.stages[8].stage_key == K_TRAP_STAGE_KEY
    assert train.stages[8].sub_order == 1
    assert _visit_labels(train) == [1, 2, 3, 4, '4b', 5, 6, 7]
    assert sorted(train.stages, key=stage_visit_key)[0].stage_number == 0

    trap = train.stages[8]
    trap.stage_number = 8
    swapped = _visit_labels(train)
    assert swapped != [1, 2, 3, 4, '4b', 5, 6, 7]
    assert swapped.index('4b') > swapped.index(7)


def _extrapolated_record_at(route, species: str, temperature_K: float):
    matches = [
        (str(key), record)
        for key, record in route.antoine_extrapolations.items()
        if str(key).split('#', 1)[0] == species
        and record.get('temperature_K') == temperature_K
        and record.get('status') == 'extrapolated'
    ]
    assert len(matches) == 1, (species, temperature_K, list(route.antoine_extrapolations))
    return matches[0]


def test_cold_alkali_walls_predict_and_flag_antoine_extrapolation():
    """250 C and 40 C are below the Na and K Antoine bands.

    The route still captures. The notice is the existing
    ``_record_antoine_extrapolation`` record (reason
    ``wall_saturation_pressure_out_of_validated_range``). At these two
    walls ``require_antoine_source_certified_temperature`` does not raise,
    so the ``#wall:`` key is not the record this route writes. This
    combined route recorded the collector walls as ``Na#4`` and ``K#4``.
    """

    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=2.0,
        species_partial_pressures_mbar={'Na': 1.0, 'K': 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    route = model.route(
        EvaporationFlux(species_kg_hr={'Na': 1.0, 'K': 1.0}, total_kg_hr=2.0),
        MeltState(temperature_C=1650.0),
    )

    assert route.condensed_by_stage_species[4]['Na'] > 0.0
    assert route.condensed_by_stage_species[4]['K'] > 0.0
    assert 'Na' not in route.impurity_by_stage_species.get(4, {})
    assert 'K' not in route.impurity_by_stage_species.get(4, {})
    na_key, na_record = _extrapolated_record_at(route, 'Na', 523.15)
    k_key, k_record = _extrapolated_record_at(route, 'K', 313.15)
    assert na_key == 'Na#4'
    assert k_key == 'K#4'
    assert 'Na#wall:523.15' not in route.antoine_extrapolations
    assert 'K#wall:313.15' not in route.antoine_extrapolations
    assert tuple(na_record['valid_range_K']) == (924.0, 1118.0)
    assert tuple(k_record['valid_range_K']) == (679.4, 1033.0)
    for record in (na_record, k_record):
        assert record['authority_level'] == 'extrapolated'
        assert record['reason'] == 'wall_saturation_pressure_out_of_validated_range'
        assert record['status'] == 'extrapolated'
    warnings = ' '.join(route.antoine_extrapolation_warnings)
    assert 'valid_range_K [924, 1118] at 523.15 K' in warnings
    assert 'valid_range_K [679.4, 1033] at 313.15 K' in warnings


def test_k_moves_to_its_trap_and_other_stage_4_destinations_stay():
    assert DESIGNATED_STAGE['K'] == K_TRAP_STAGE_KEY
    assert DESIGNATED_STAGE['Na'] == NA_COLLECTOR_STAGE_KEY
    assert DESIGNATED_STAGE['Mg'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['C7']['Ca'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['Mg'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['Na'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['K'] == K_TRAP_STAGE_KEY
    # Public stage number stays 4. The trap is not a renumber of stages 5-7.
    assert product_stage_number('MRE', 'K') == 4
    assert product_stage_number('MRE', 'Na') == 4
    assert target_species_for_stage_number(4) == ['Na', 'Mg']
    train = CondensationTrain.create_default()
    na = train.stages[4]
    trap = train.stages[8]
    assert na.temp_range_C == (250.0, 250.0)
    assert trap.temp_range_C == (40.0, 40.0)
    assert na.target_species == ['Na', 'Mg']
    assert trap.target_species == ['K']
