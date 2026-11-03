import math
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
from simulator.accounting.queries import CONDENSATION_TRAIN_ACCOUNT
from simulator.condensation_routing import (
    DESIGNATED_STAGE,
    K_TRAP_STAGE_KEY,
    NA_COLLECTOR_STAGE_KEY,
    PRODUCT_DESTINATIONS,
    STAGE_KEY_BY_NUMBER,
    accepted_species_for_stage_number,
    product_stage_number,
    stage_index_for_visit_identity,
    target_species_for_stage_number,
)
from simulator.evaporation import EvaporationMixin
from simulator.extraction import ExtractionMixin
from simulator.three_product_report import (
    _metal_product_accounting,
    _product_stage_accepts_species,
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
    """Pin the production band-integrated route, not a calibration target.

    K at 1 mbar is supersaturated on the 250 C Na collector. That surface
    uses the same capture law as the 40 C trap, so most of the kilogram
    condenses there and is impurity of the Na/Mg stage. The trap keeps
    the remainder. Stage number 4 is the sum of those two identities.
    The previous pin (stage 4 = 0.9999661577804508, all of it the trap)
    was the designated-species skip: the collector refused K, so the
    trap was the first surface that could capture it and more of the
    tail reached stages 5 and 6.
    """
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
        4: {'K': 0.9999999705550642},
        5: {'K': 2.9101495551411142e-08},
        6: {'K': 3.434399382784235e-10},
    }
    assert route.capture_kg_by_stage_identity[(4, 0)] == {
        'K': 0.9169775631320057,
    }
    assert route.capture_kg_by_stage_identity[(4, 1)] == {
        'K': 0.0830224074230584,
    }
    assert route.remaining_by_species == {'K': 3.7485254416350975e-16}
    assert route.wall_deposit_by_species == {}
    assert route.impurity_by_stage_species == {
        4: {'K': 0.9169775631320057},
        5: {'K': 2.9101495551411142e-08},
        6: {'K': 3.434399382784235e-10},
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
    so the ``#wall:`` key is not the record this route writes. Band
    sampling adds unique extrapolation records before these wall records;
    their serial key depends on which stages are active.
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
    # Stage number 4 is two surfaces. Impurity is the off-design surface
    # only: Na on the K trap, K on the Na collector when that wall captures.
    na_trap = route.capture_kg_by_stage_identity[(4, 1)]['Na']
    assert na_trap > 0.0
    assert route.impurity_by_stage_species[4]['Na'] == pytest.approx(na_trap)
    k_collector = route.capture_kg_by_stage_identity.get((4, 0), {}).get('K', 0.0)
    assert route.impurity_by_stage_species.get(4, {}).get('K', 0.0) == pytest.approx(
        k_collector
    )
    _, na_record = _extrapolated_record_at(route, 'Na', 523.15)
    _, k_record = _extrapolated_record_at(route, 'K', 313.15)
    assert 'Na#wall:523.15' not in route.antoine_extrapolations
    assert 'K#wall:313.15' not in route.antoine_extrapolations
    assert tuple(na_record['valid_range_K']) == (864.0, 2499.0)
    assert tuple(k_record['valid_range_K']) == (679.4, 1033.0)
    for record in (na_record, k_record):
        assert record['authority_level'] == 'extrapolated'
        assert record['reason'] == 'wall_saturation_pressure_out_of_validated_range'
        assert record['status'] == 'extrapolated'
    warnings = ' '.join(route.antoine_extrapolation_warnings)
    assert 'valid_range_K [864, 2499] at 523.15 K' in warnings
    assert '24%' in warnings
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


def test_na_at_the_k_trap_is_a_contaminant_and_mass_closes():
    """Na that reaches the 40 C trap condenses there.

    Designation still names the Na collector as Na's product stage and
    the trap as K's. The trap's Na is that stage's impurity, and the
    product owner does not count it as Na product or as K product.
    """

    train = CondensationTrain.create_default()
    model = CondensationModel(train)
    model.configure_operating_conditions(
        overhead_pressure_mbar=2.0,
        species_partial_pressures_mbar={'Na': 1.0, 'K': 1.0},
        stage_area_m2_by_stage={
            str(stage.stage_number): 1.0 for stage in model.train.stages
        },
    )
    inlet = {'Na': 1.0, 'K': 1.0}
    route = model.route(
        EvaporationFlux(species_kg_hr=inlet, total_kg_hr=2.0),
        MeltState(temperature_C=1650.0),
    )

    na_trap = route.capture_kg_by_stage_identity[(4, 1)]['Na']
    na_collector = route.capture_kg_by_stage_identity.get((4, 0), {}).get('Na', 0.0)
    assert na_trap > 0.0
    assert route.condensed_by_stage_species[4]['Na'] == pytest.approx(
        na_collector + na_trap
    )
    assert route.impurity_by_stage_species[4]['Na'] == pytest.approx(na_trap)
    k_trap = route.capture_kg_by_stage_identity.get((4, 1), {}).get('K', 0.0)
    k_collector = route.capture_kg_by_stage_identity.get((4, 0), {}).get('K', 0.0)
    assert route.impurity_by_stage_species.get(4, {}).get('K', 0.0) == pytest.approx(
        k_collector
    )

    for species, mass in inlet.items():
        closed = (
            route.condensed_for_species(species)
            + route.wall_deposit_by_species.get(species, 0.0)
            + route.remaining_by_species[species]
        )
        assert closed == pytest.approx(mass)
        identity_sum = sum(
            bucket.get(species, 0.0)
            for bucket in route.capture_kg_by_stage_identity.values()
        )
        assert identity_sum == pytest.approx(route.condensed_for_species(species))

    assert DESIGNATED_STAGE['Na'] == NA_COLLECTOR_STAGE_KEY
    assert DESIGNATED_STAGE['K'] == K_TRAP_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['Na'] == NA_COLLECTOR_STAGE_KEY
    assert PRODUCT_DESTINATIONS['MRE']['K'] == K_TRAP_STAGE_KEY
    assert not _product_stage_accepts_species('Na', K_TRAP_STAGE_KEY)
    assert _product_stage_accepts_species('Na', NA_COLLECTOR_STAGE_KEY)
    assert _product_stage_accepts_species('K', K_TRAP_STAGE_KEY)
    assert not _product_stage_accepts_species('K', NA_COLLECTOR_STAGE_KEY)

    host = SimpleNamespace(
        train=train,
        _last_condensed_by_stage_species_delta={},
        _last_impurity_delta={},
        _stage_collection_kg_by_source={},
    )
    host._record_stage_collection_source = (
        ExtractionMixin._record_stage_collection_source.__get__(host)
    )
    for species in inlet:
        EvaporationMixin._project_condensed_stage_collection(
            host, route, species, route.condensed_for_species(species),
        )

    collector_index = stage_index_for_visit_identity(train, 4, 0)
    trap_index = stage_index_for_visit_identity(train, 4, 1)
    source = host._stage_collection_kg_by_source
    assert source[(CONDENSATION_TRAIN_ACCOUNT, trap_index, 'Na')] == pytest.approx(
        na_trap
    )
    assert source[(CONDENSATION_TRAIN_ACCOUNT, collector_index, 'Na')] == pytest.approx(
        na_collector
    )
    assert host._last_impurity_delta[(4, 'Na')] == pytest.approx(na_trap)
    assert host._last_condensed_by_stage_species_delta[(4, 'Na')] == pytest.approx(
        na_collector + na_trap
    )

    purity = stage_purity_report(train)
    trap_purity = purity[K_TRAP_STAGE_KEY]
    collector_purity = purity[NA_COLLECTOR_STAGE_KEY]
    assert trap_purity['impurity_species_kg']['Na'] == pytest.approx(na_trap)
    assert 'Na' not in trap_purity['designated_species_kg']
    assert collector_purity['designated_species_kg'].get('Na', 0.0) == pytest.approx(
        na_collector
    )
    assert 'Na' not in collector_purity['impurity_species_kg']
    assert collector_purity['impurity_species_kg'].get('K', 0.0) == pytest.approx(
        k_collector
    )
    assert trap_purity['designated_species_kg'].get('K', 0.0) == pytest.approx(k_trap)
    assert 'K' not in trap_purity['impurity_species_kg']

    def account_kg(account):
        if account != CONDENSATION_TRAIN_ACCOUNT:
            return {}
        totals = {}
        for stage in train.stages:
            for name, kg in stage.collected_kg.items():
                if kg > 0.0:
                    totals[name] = totals.get(name, 0.0) + kg
        return totals

    sim = SimpleNamespace(
        train=train,
        _stage_collection_kg_by_source=source,
        atom_ledger=SimpleNamespace(project_account_kg=account_kg),
        record=SimpleNamespace(
            initial_inventory=SimpleNamespace(metal_alloy_kg={}),
        ),
    )
    accounting = _metal_product_accounting(sim, products={})
    assert accounting['species_kg'].get('Na', 0.0) == pytest.approx(na_collector)
    assert accounting['species_kg'].get('K', 0.0) == pytest.approx(k_trap)
