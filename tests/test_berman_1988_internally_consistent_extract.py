from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from tests.battery.factories import migrate_one_extract
from tools.validate_literature_extracts import (
    check_all_fidelity_samples_match,
    validate_extract_document,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / (
    "data/literature/extracts/"
    "berman-1988-internally-consistent-mineral-thermodynamic-data.yaml"
)
REFERENCE_TEMPERATURE_K = Decimal("298.15")
STANDARD_PRESSURE_PA = Decimal("100000")


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observation(document: dict, species_key: str, quantity: str) -> dict:
    return next(
        row
        for row in document["species"][species_key]["observations"]
        if row["values"]["quantity"] == quantity
    )


TABLE2_PRINTED_PINS = {
    ("akermanite", "delta_fG"): -3663.786,
    ("akermanite", "delta_fH"): -3860.441,
    ("akermanite", "S"): 212.000,
    ("gehlenite", "delta_fG"): -3785.954,
    ("gehlenite", "delta_fH"): -3988.158,
    ("gehlenite", "S"): 198.600,
    ("merwinite", "delta_fG"): -4309.707,
    ("merwinite", "delta_fH"): -4537.497,
    ("merwinite", "S"): 251.777,
    ("monticellite", "delta_fG"): -2132.222,
    ("monticellite", "delta_fH"): -2250.027,
    ("monticellite", "S"): 108.300,
    ("quartz_beta", "delta_fG"): -855.033,
    ("quartz_beta", "delta_fH"): -908.627,
    ("quartz_beta", "S"): 44.207,
    ("spinel", "delta_fG"): -2176.537,
    ("spinel", "delta_fH"): -2300.313,
    ("spinel", "S"): 84.535,
    ("tridymite_low", "delta_fG"): -854.026,
    ("tridymite_low", "delta_fH"): -907.750,
    ("tridymite_low", "S"): 43.770,
    ("tridymite_high", "delta_fG"): -853.844,
    ("tridymite_high", "delta_fH"): -907.045,
    ("tridymite_high", "S"): 45.524,
}

TRANSITION_PRINTED_PINS = {
    "akermanite": 358,
    "cristobalite_alpha": 535,
    "hematite": 955,
    "magnetite": 848,
    "quartz_alpha": 848,
    "tridymite_low": 383,
}


def _assert_printed_table_cells(document: dict) -> None:
    for (species_key, quantity), expected in TABLE2_PRINTED_PINS.items():
        values = _observation(document, species_key, quantity)["values"]
        assert values["series"][0]["value"] == expected
    for species_key, expected in TRANSITION_PRINTED_PINS.items():
        row = _observation(document, species_key, "transition_temperature")
        assert row["values"]["value_K"] == expected


def test_extract_validates_and_matches_fidelity_samples() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    assert check_all_fidelity_samples_match(document, label=str(EXTRACT)) == []


def test_printed_table_pins_and_fidelity_guard_detect_mutation() -> None:
    document = _document()
    _assert_printed_table_cells(document)

    mutated = deepcopy(document)
    row = _observation(mutated, "gehlenite", "delta_fH")
    row["values"]["series"][0]["value"] += 0.001
    assert check_all_fidelity_samples_match(mutated, label="mutated Berman Table 2")
    with pytest.raises(AssertionError):
        _assert_printed_table_cells(mutated)


def _assert_water_gas_cp_extension(document: dict) -> None:
    observation = next(
        row
        for row in document["species"]["water_gas"]["context"]
        if row["observation_id"] == "berman1988_water_gas_table3a_cp"
    )
    extension = observation["values"]["equation_extension"]
    assert extension["footnote_as_printed"].endswith(
        "-0.002062 T + 51055.16 T^-1."
    )
    assert extension["additional_terms"][0]["coefficient"] == -0.002062
    assert extension["additional_terms"][1]["coefficient"] == 51055.16
    assert extension["equation_form_as_printed"] == (
        "Eq. 4 extended by -0.002062 T + 51055.16 T^-1"
    )


def test_water_gas_cp_extension_printed_pin_detects_mutation() -> None:
    document = _document()
    _assert_water_gas_cp_extension(document)

    mutated = deepcopy(document)
    observation = next(
        row
        for row in mutated["species"]["water_gas"]["context"]
        if row["observation_id"] == "berman1988_water_gas_table3a_cp"
    )
    observation["values"]["equation_extension"]["additional_terms"][0][
        "coefficient"
    ] = -0.0002062
    with pytest.raises(AssertionError):
        _assert_water_gas_cp_extension(mutated)


def test_isolated_migration_preserves_every_scored_point_and_table2_conditions() -> None:
    document = _document()
    migrator = migrate_one_extract(root=ROOT, path=EXTRACT, index={}, aliases={})
    migrator.finalize()

    assert migrator.result.queue == []
    observations = list(migrator.result.observations.values())
    assert len(observations) == 219
    assert all(row.value.kind is ValueKind.POINT for row in observations)

    source_rows = {
        row["observation_id"]: row
        for species in document["species"].values()
        for row in species.get("observations", [])
    }
    quantities = Counter()
    table2_count = 0
    for observation in observations:
        source_id = next(
            (
                candidate
                for candidate in source_rows
                if observation.observation_id
                in {
                    f"{EXTRACT.stem}::{candidate}",
                    f"{EXTRACT.stem}::{candidate}::",
                }
                or observation.observation_id.startswith(
                    f"{EXTRACT.stem}::{candidate}::"
                )
            ),
            None,
        )
        assert source_id is not None, observation.observation_id
        source = source_rows[source_id]
        quantity = source["values"]["quantity"]
        quantities[quantity] += 1
        assert observation.identity.quantity.is_value
        assert observation.identity.quantity.value is Quantity(quantity)
        assert observation.value.kind is ValueKind.POINT

        if quantity == "transition_temperature":
            assert observation.value.point == Decimal(
                str(source["values"]["value_K"])
            )
            assert observation.locator.table == "3b"
            continue

        table2_count += 1
        printed = source["values"]["series"][0]
        assert observation.value.point == Decimal(str(printed["value"]))
        assert observation.identity.temperature_K.is_value
        assert observation.identity.temperature_K.value == REFERENCE_TEMPERATURE_K
        assert observation.locator.table == "2"

        conditions = observation.point_conditions
        assert conditions is not None
        temperature = conditions["temperature_K"]
        assert temperature.state.is_value
        assert temperature.state.value == REFERENCE_TEMPERATURE_K
        assert temperature.locator is not None
        assert temperature.locator.table == "2"

        pressure = conditions["standard_pressure_Pa"]
        assert pressure.state.is_value
        assert pressure.state.value == STANDARD_PRESSURE_PA
        assert pressure.locator is not None
        assert pressure.locator.table == "2"

    assert table2_count == 213
    assert quantities == {
        "delta_fG": 71,
        "delta_fH": 71,
        "S": 71,
        "transition_temperature": 6,
    }
