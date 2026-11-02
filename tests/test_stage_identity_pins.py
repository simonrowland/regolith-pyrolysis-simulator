"""Golden pins for the three stage-identity consumers."""

from types import SimpleNamespace

import pytest

from simulator.accounting.queries import CONDENSATION_TRAIN_ACCOUNT
from simulator.condensation_routing import (
    K_TRAP_STAGE_KEY,
    NA_COLLECTOR_STAGE_KEY,
    stage_key_for_stage,
)
from simulator.runner import _stage_silica_fume_kg
from simulator.three_product_report import (
    _metal_product_accounting,
    product_story_extracted_projection,
)


def _stage_roster(*, empty_stage_four_keys=False):
    rows = (
        (0, 0, "stage_0_hot_duct"),
        (1, 0, ""),
        (2, 0, "stage_2_cr_oxide_harvest"),
        (3, 0, ""),
        (4, 0, NA_COLLECTOR_STAGE_KEY),
        (5, 0, "stage_5_vortex_dust_filter"),
        (6, 0, "stage_6_turbine_compressor"),
        (7, 0, "stage_7_accumulator"),
        (4, 1, K_TRAP_STAGE_KEY),
        (8, 0, K_TRAP_STAGE_KEY),
        (9, 0, ""),
    )
    return [
        SimpleNamespace(
            stage_number=number,
            sub_order=sub_order,
            stage_key=(
                ""
                if empty_stage_four_keys
                and (number, sub_order) in ((4, 0), (4, 1))
                else stage_key
            ),
            collected_kg={"SiO2": float(index + 1)},
        )
        for index, (number, sub_order, stage_key) in enumerate(rows)
    ]


def _stage_capture_simulator(*, empty_stage_four_keys=False):
    stages = _stage_roster(empty_stage_four_keys=empty_stage_four_keys)
    collections = {
        (CONDENSATION_TRAIN_ACCOUNT, 0, "Fe"): 1.0,
        (CONDENSATION_TRAIN_ACCOUNT, 1, "Fe"): 2.0,
        (CONDENSATION_TRAIN_ACCOUNT, 2, "Cr"): 3.0,
        (CONDENSATION_TRAIN_ACCOUNT, 3, "SiO"): 4.0,
        (CONDENSATION_TRAIN_ACCOUNT, 3, "SiO2"): 0.4,
        (CONDENSATION_TRAIN_ACCOUNT, 4, "Na"): 5.0,
        (CONDENSATION_TRAIN_ACCOUNT, 4, "Mg"): 5.5,
        (CONDENSATION_TRAIN_ACCOUNT, 5, "Fe"): 6.0,
        (CONDENSATION_TRAIN_ACCOUNT, 6, "Cr"): 7.0,
        (CONDENSATION_TRAIN_ACCOUNT, 7, "Mg"): 8.0,
        (CONDENSATION_TRAIN_ACCOUNT, 8, "K"): 9.0,
        (CONDENSATION_TRAIN_ACCOUNT, 9, "K"): 10.0,
        (CONDENSATION_TRAIN_ACCOUNT, 10, "Na"): 11.0,
    }
    account_kg = {
        CONDENSATION_TRAIN_ACCOUNT: {
            "Fe": 9.0,
            "Cr": 10.0,
            "SiO": 4.0,
            "SiO2": 0.4,
            "Na": 16.0,
            "Mg": 13.5,
            "K": 19.0,
        }
    }

    class Ledger:
        def project_account_kg(self, account):
            return dict(account_kg.get(account, {}))

    return SimpleNamespace(
        train=SimpleNamespace(stages=stages),
        _stage_collection_kg_by_source=collections,
        atom_ledger=Ledger(),
    ), account_kg


@pytest.mark.parametrize(
    ("stage_number", "sub_order", "expected_key"),
    [
        pytest.param(
            4,
            0,
            "stage_4_alkali_mg_cyclone",
            id="na-mg-collector",
        ),
        pytest.param(4, 1, "stage_4b_k_trap", id="k-trap"),
    ],
)
def test_stage_identity_empty_key_uses_independent_identity_key(
    stage_number, sub_order, expected_key
):
    stage = SimpleNamespace(
        stage_number=stage_number,
        sub_order=sub_order,
        stage_key="",
    )

    assert stage_key_for_stage(stage) == expected_key


def test_runner_stage_identity_output_pin_for_shipped_roster():
    sim, _account_kg = _stage_capture_simulator()

    assert _stage_silica_fume_kg(sim) == {
        "stage_1_fe_condenser_impurity": 2.0,
        "stage_3_sio_zone_product": 4.0,
        "stage_4_alkali_mg_carryover": 5.0,
        "stage_4b_k_trap_carryover": 19.0,
        "stage_5_dust_filter_carryover": 6.0,
        "unreported_stage_silica_capture": 30.0,
    }


def test_metal_product_stage_identity_output_pin_for_shipped_roster():
    sim, _account_kg = _stage_capture_simulator()

    result = _metal_product_accounting(sim, products={})

    assert result["species_kg"] == {
        "Cr": 3.0,
        "Fe": 2.0,
        "K": 19.0,
        "Mg": 5.5,
        "Na": 5.0,
    }
    assert result["source_accounts_by_species"] == {
        species: [{"account": CONDENSATION_TRAIN_ACCOUNT, "kg": mass}]
        for species, mass in (
            ("Cr", 3.0),
            ("Fe", 2.0),
            ("K", 19.0),
            ("Mg", 5.5),
            ("Na", 5.0),
        )
    }


def test_product_story_stage_identity_output_pin_for_shipped_roster():
    sim, account_kg = _stage_capture_simulator()

    result = product_story_extracted_projection(
        sim,
        account_kg=account_kg,
        classification=None,
    )

    assert result == {
        "metal_ingots": {"Cr": 3.0, "Fe": 2.0, "K": 10.0},
        "glass": {"SiO": 4.0, "SiO2": 0.4},
        "captured_volatiles": {"K": 9.0, "Mg": 5.5, "Na": 5.0},
        "oxygen": {},
        "oxygen_partition": {},
        "off_spec_condensate": {
            "Cr": 7.0,
            "Fe": 7.0,
            "Mg": 8.0,
            "Na": 11.0,
        },
    }


def test_stage_identity_consumers_fallback_for_empty_stage_four_keys():
    sim, account_kg = _stage_capture_simulator(empty_stage_four_keys=True)

    silica = _stage_silica_fume_kg(sim)
    assert silica["stage_4_alkali_mg_carryover"] == 5.0
    assert silica["stage_4b_k_trap_carryover"] == 19.0

    metals = _metal_product_accounting(sim, products={})["species_kg"]
    assert metals == {
        "Cr": 3.0,
        "Fe": 2.0,
        "K": 19.0,
        "Mg": 5.5,
        "Na": 5.0,
    }

    story = product_story_extracted_projection(
        sim,
        account_kg=account_kg,
        classification=None,
    )
    assert story["metal_ingots"] == {"Cr": 3.0, "Fe": 2.0, "K": 10.0}
    assert story["captured_volatiles"] == {"K": 9.0, "Mg": 5.5, "Na": 5.0}
