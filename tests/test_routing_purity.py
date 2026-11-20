import math
from dataclasses import replace
from types import SimpleNamespace

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
    stage_list_index_for_destination,
    target_species_for_stage_number,
)
from simulator.account_ids import MANGANESE_STAGE3_CARTRIDGE_ACCOUNT
from simulator.evaporation import EvaporationMixin
from simulator.state import CondensationTrain, EvaporationFlux, MeltState
from simulator.accounting import AccountingError


@pytest.mark.parametrize("stage_number", [1, 2, 3, 4])
def test_default_train_targets_use_canonical_registry(stage_number):
    train = CondensationTrain.create_default()

    assert train.stages[stage_number].target_species == (
        target_species_for_stage_number(stage_number)
    )


def test_stage_list_index_resolves_stage_keys_for_all_collector_bands():
    train = CondensationTrain.create_default()

    assert stage_list_index_for_destination(train, "stage_0_hot_duct") == 0
    assert stage_list_index_for_destination(
        train, "stage_5_vortex_dust_filter") == 5
    assert stage_list_index_for_destination(
        train, K_TRAP_STAGE_KEY) == 8


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


def _populated_stage_four_purity_train(*, empty_keys=False):
    na_key = "" if empty_keys else NA_COLLECTOR_STAGE_KEY
    k_key = "" if empty_keys else K_TRAP_STAGE_KEY
    return SimpleNamespace(
        stages=[
            SimpleNamespace(
                stage_number=4,
                sub_order=0,
                stage_key=na_key,
                label="Na/Mg collector",
                collected_kg={"Na": 5.0, "Mg": 5.5},
            ),
            SimpleNamespace(
                stage_number=4,
                sub_order=1,
                stage_key=k_key,
                label="K trap",
                collected_kg={"K": 9.0},
            ),
        ]
    )


def test_stage_purity_report_pins_explicit_stage_four_collectors():
    report = stage_purity_report(_populated_stage_four_purity_train())

    assert report == {
        "stage_4_alkali_mg_cyclone": {
            "stage_number": 4,
            "label": "Na/Mg collector",
            "accepted_species": ["Mg", "Na"],
            "designated_species_kg": {"Mg": 5.5, "Na": 5.0},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 10.5,
            "impurity_kg": 0.0,
            "total_kg": 10.5,
            "purity_fraction": 1.0,
            "verdict": "PURE",
            "warning": "",
            "activity": {"Mg": True, "Na": True},
        },
        "stage_4b_k_trap": {
            "stage_number": 4,
            "label": "K trap",
            "accepted_species": ["K"],
            "designated_species_kg": {"K": 9.0},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 9.0,
            "impurity_kg": 0.0,
            "total_kg": 9.0,
            "purity_fraction": 1.0,
            "verdict": "PURE",
            "warning": "",
            "activity": {"K": True},
        },
    }


def test_stage_purity_report_pins_empty_stage_four_collapse():
    report = stage_purity_report(
        _populated_stage_four_purity_train(empty_keys=True)
    )

    # The K trap contents previously overwrote the Na/Mg row for empty keys.
    assert report == {
        "stage_4_alkali_mg_cyclone": {
            "stage_number": 4,
            "label": "Na/Mg collector",
            "accepted_species": ["Mg", "Na"],
            "designated_species_kg": {"Mg": 5.5, "Na": 5.0},
            "coproduct_species_kg": {},
            "impurity_species_kg": {},
            "designated_kg": 10.5,
            "impurity_kg": 0.0,
            "total_kg": 10.5,
            "purity_fraction": 1.0,
            "verdict": "PURE",
            "warning": "",
            "activity": {"Mg": True, "Na": True},
        },
        "stage_4b_k_trap": {
            "stage_number": 4,
            "label": "K trap",
            "accepted_species": ["Mg", "Na"],
            "designated_species_kg": {},
            "coproduct_species_kg": {},
            "impurity_species_kg": {"K": 9.0},
            "designated_kg": 0.0,
            "impurity_kg": 9.0,
            "total_kg": 9.0,
            "purity_fraction": 0.0,
            "verdict": "CONTAMINATED",
            "warning": "non-designated condensate present",
        },
    }


def test_stage4_net_unique_purity_uses_disjoint_current_ledger_balances():
    train = _populated_stage_four_purity_train()
    collector = train.stages[0]
    collector.collected_kg.update({
        "Na": 0.4,
        "Mg": 2.0,
        "SiO2": 0.25,
        "Si": 0.25,
        "Ca": 1.5,
    })

    class LedgerView:
        def kg_by_account(self):
            return {
                "process.reagent_inventory": {"Na": 0.6, "Mg": 3.0},
            }

        def origin_atom_moles_by_account(self):
            return {
                "process.reagent_inventory": {
                    "Na": {"feedstock": 0.6},
                    "Mg": {"feedstock": 3.0},
                },
            }

        def unresolved_origin_atom_moles_by_account(self):
            return {}

    stage = stage_purity_report(train, LedgerView())[
        NA_COLLECTOR_STAGE_KEY
    ]

    # Na is 0.4 kg in the collector now and 0.6 kg recovered in inventory;
    # earlier recovery/recollection events do not add another copy.
    assert stage["net_unique_designated_species_kg"] == {
        "Na": pytest.approx(1.0),
        "Mg": pytest.approx(5.0),
    }
    assert stage["net_unique_designated_kg"] == pytest.approx(6.0)
    assert stage["sio_impurity_kg"] == pytest.approx(0.5)
    assert stage["sio_impurity_to_designated_ratio"] == pytest.approx(1 / 12)
    assert stage["net_unique_impurity_kg"] == pytest.approx(2.0)
    assert stage["net_unique_purity_fraction"] == pytest.approx(0.75)


def test_stage3_report_splits_mn_cartridge_from_silica_window():
    train = CondensationTrain.create_default()
    train.stages[3].collected_kg.update({"SiO2": 3.0, "Si": 1.0, "Mn": 1.0})

    class LedgerView:
        def kg_by_account(self):
            return {
                MANGANESE_STAGE3_CARTRIDGE_ACCOUNT: {
                    "Mn": 0.5,
                    "SiO2": 0.25,
                },
            }

        def origin_atom_moles_by_account(self):
            return {}

        def unresolved_origin_atom_moles_by_account(self):
            return {}

    stage = stage_purity_report(train, LedgerView())[STAGE_KEY_BY_NUMBER[3]]

    assert stage["mn_cartridge_mass_kg"] == pytest.approx(0.75)
    assert stage["mn_cartridge_mn_mass_kg"] == pytest.approx(0.5)
    assert stage["mn_cartridge_purity_fraction"] == pytest.approx(2 / 3)
    assert stage["mn_cartridge_capture_status"] == "FLAGGED_UNCERTIFIED"
    assert stage["silica_without_mn_purity_fraction"] == pytest.approx(3.75 / 4.25)


@pytest.mark.parametrize(
    ("elapsed_hour", "expected_fraction"),
    [(10, 0.0), (11, 0.6), (29, 0.6), (30, 0.0)],
)
def test_stage3_mn_cartridge_schedule_uses_inclusive_run_hours(
    elapsed_hour, expected_fraction
):
    host = SimpleNamespace(
        setpoints={
            "condensation_train": {
                "metals_train": {
                    STAGE_KEY_BY_NUMBER[3]: {
                        "stage3_cartridge_schedule": {
                            "mn_cartridge_run_hour_window": [12, 30],
                        },
                    },
                },
            },
        },
        melt=SimpleNamespace(hour=elapsed_hour),
    )
    route = SimpleNamespace(
        condensed_by_stage_species={STAGE_KEY_BY_NUMBER[3]: {"Mn": 6.0}},
        condensed_for_species=lambda _species: 10.0,
    )

    assert EvaporationMixin._stage3_mn_cartridge_fraction(
        host, route, "Mn"
    ) == pytest.approx(expected_fraction)


def test_stage3_mn_cartridge_schedule_rejects_bool_hour_bounds():
    host = SimpleNamespace(
        setpoints={
            "condensation_train": {
                "metals_train": {
                    STAGE_KEY_BY_NUMBER[3]: {
                        "stage3_cartridge_schedule": {
                            "mn_cartridge_run_hour_window": [True, 30],
                        },
                    },
                },
            },
        },
        melt=SimpleNamespace(hour=11),
    )
    route = SimpleNamespace(
        condensed_by_stage_species={STAGE_KEY_BY_NUMBER[3]: {"Mn": 6.0}},
        condensed_for_species=lambda _species: 10.0,
    )

    with pytest.raises(AccountingError, match="hour bounds must be real numbers"):
        EvaporationMixin._stage3_mn_cartridge_fraction(host, route, "Mn")


def test_stage3_mn_cartridge_schedule_ignores_wall_only_routes():
    host = SimpleNamespace(
        setpoints={
            "condensation_train": {
                "metals_train": {
                    STAGE_KEY_BY_NUMBER[3]: {
                        "stage3_cartridge_schedule": {
                            "mn_cartridge_run_hour_window": [12, 30],
                        },
                    },
                },
            },
        },
        melt=SimpleNamespace(hour=11),
    )
    route = SimpleNamespace(
        wall_deposit_fraction_by_species={"organic_tar": 1.0},
    )

    assert EvaporationMixin._stage3_mn_cartridge_fraction(
        host, route, "organic_tar"
    ) == 0.0


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
        for species_kg in route.impurity_by_stage_species.values()
    )

    assert condensed > 0.0
    assert impurity >= 0.0
    total_deposited = condensed + route.wall_deposit_by_species.get("K", 0.0)
    assert route.remaining_by_species["K"] == pytest.approx(1.0 - total_deposited)


def test_supersaturated_k_route_records_na_tap_co_deposition():
    """Supersaturated K can co-deposit on the Na tap and is counted as impurity."""
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
    na_tap_k = route.condensed_by_stage_species[NA_COLLECTOR_STAGE_KEY]['K']
    assert na_tap_k > 0.0
    assert route.impurity_by_stage_species[NA_COLLECTOR_STAGE_KEY]['K'] == (
        pytest.approx(na_tap_k)
    )
    assert route.condensed_by_stage_species[K_TRAP_STAGE_KEY]['K'] > 0.0
    assert sum(
        values.get('K', 0.0)
        for values in route.condensed_by_stage_species.values()
    ) + route.wall_deposit_by_species.get('K', 0.0) + route.remaining_by_species['K'] == (
        pytest.approx(1.0)
    )


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
    so the ``#wall:`` key is not the record this route writes. Band
    sampling adds unique extrapolation records before the same 523.15 K
    and 313.15 K wall records, whose serial keys are now ``Na#67`` and
    ``K#67``.
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

    assert route.condensed_by_stage_species[NA_COLLECTOR_STAGE_KEY]['Na'] > 0.0
    assert route.condensed_by_stage_species[K_TRAP_STAGE_KEY]['K'] > 0.0
    na_tap_k = route.condensed_by_stage_species[NA_COLLECTOR_STAGE_KEY].get('K', 0.0)
    assert route.impurity_by_stage_species[NA_COLLECTOR_STAGE_KEY]['K'] == (
        pytest.approx(na_tap_k)
    )
    k_trap_na = route.condensed_by_stage_species[K_TRAP_STAGE_KEY].get('Na', 0.0)
    if k_trap_na > 0.0:
        assert route.impurity_by_stage_species[K_TRAP_STAGE_KEY]['Na'] == (
            pytest.approx(k_trap_na)
        )
    na_key, na_record = _extrapolated_record_at(route, 'Na', 523.15)
    k_key, k_record = _extrapolated_record_at(route, 'K', 313.15)
    assert na_key == 'Na#67'
    assert k_key == 'K#67'
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


@pytest.mark.parametrize('species', ['Cr', 'Mn'])
def test_designated_stage_two_species_can_reach_downstream_duct_walls(species):
    model = CondensationModel(CondensationTrain.create_default())

    candidates = model._mixed_temperature_wall_candidate_segments(species)

    assert candidates
    assert any(
        int(segment.downstream_stage.removeprefix('stage_')) > 2
        for segment in candidates
    )


def test_stage_two_manganese_freezes_out_at_k_trap_as_tap_impurity():
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={'Mn': 0.04254},
        stage_area_m2_by_stage={
            'fe_stage1': 0.0,
            'cr_stage2': 0.0,
            'sio_stage3': 0.0,
            'alkali_stage4': 0.0,
            'k_stage4b': 10.0,
            'terminal': 0.0,
        },
    )
    # Isolate cold-stage capture from the default interstage duct surfaces.
    model.pipe_segments = []

    route = model.route(
        EvaporationFlux(species_kg_hr={'Mn': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1650.0),
    )

    trap_capture = route.capture_kg_by_stage_identity[(4, 1)]['Mn']
    assert trap_capture > 0.0
    assert route.impurity_by_stage_species[K_TRAP_STAGE_KEY]['Mn'] == (
        pytest.approx(trap_capture)
    )
    assert route.remaining_by_species['Mn'] < 1.0


def test_stage_two_manganese_can_deposit_on_downstream_duct_wall():
    model = CondensationModel(CondensationTrain.create_default())
    model.configure_operating_conditions(
        overhead_pressure_mbar=1.0,
        species_partial_pressures_mbar={'Mn': 0.04254},
        stage_area_m2_by_stage={
            'fe_stage1': 0.0,
            'cr_stage2': 0.0,
            'sio_stage3': 0.0,
            'alkali_stage4': 0.0,
            'k_stage4b': 0.0,
            'terminal': 0.0,
        },
    )
    model.pipe_segments = [
        replace(
            segment,
            wall_temperature_C=(
                250.0 if segment.downstream_stage == 'stage_4' else 1500.0
            ),
        )
        for segment in model.pipe_segments
    ]
    cold_duct = next(
        segment
        for segment in model.pipe_segments
        if segment.downstream_stage == 'stage_4'
    )

    route = model.route(
        EvaporationFlux(species_kg_hr={'Mn': 1.0}, total_kg_hr=1.0),
        MeltState(temperature_C=1650.0),
    )

    assert route.wall_deposit_by_segment_species[cold_duct.name]['Mn'] > 0.0


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
