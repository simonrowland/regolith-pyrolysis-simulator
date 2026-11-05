"""Render pins and isolated migration for Barin's NaFeO2 thermochemistry."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.records import Value
from tests.battery.factories import migrate_one_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/barin-1995-nafeo2-thermochemistry.yaml"
SOURCE_ID = "barin-1995"
PDF_PAGE = 823
PRINTED_PAGE = 708
PDF_PAGE_INDEX = 822

RENDER_PINS = {
    "barin95_nafeo2_solb_cp": (0, "84.517", "Cp"),
    "barin95_nafeo2_solb_entropy": (0, "88.299", "S"),
    "barin95_nafeo2_solb_delta_fH": (10, "-808.006", "delta Hf"),
    "barin95_nafeo2_solb_heat_content": (15, "123.343", "H-H298"),
    "barin95_nafeo2_liq_heat_content": (0, "172.714", "H-H298"),
}


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _observations(document: dict) -> dict[str, dict]:
    return {
        row["observation_id"]: row
        for row in document["species"]["NaFeO2"]["observations"]
    }


def _assert_render_pins(document: dict) -> None:
    rows = _observations(document)
    for observation_id, (point_index, expected, printed_column) in RENDER_PINS.items():
        observation = rows[observation_id]
        point = observation["values"]["points"][point_index]
        assert point["value"] == expected
        assert point["locator"]["page"] == PDF_PAGE
        assert point["locator"]["published_page"] == PRINTED_PAGE
        assert point["locator"]["pdf_page_index"] == PDF_PAGE_INDEX
        assert point["locator"]["column"] == printed_column

    fusion = rows["barin95_nafeo2_heat_of_fusion_1620"]
    assert fusion["values"]["value"] == "49.371"
    assert fusion["locator"]["page"] == PDF_PAGE
    assert fusion["locator"]["row"] == "1620.00 SOL-B to LIQ transition"
    assert fusion["locator"]["column"] == "H"
    entropy_fusion = rows["barin95_nafeo2_entropy_of_fusion_1620"]
    assert entropy_fusion["values"]["value"] == "30.476"
    assert entropy_fusion["locator"]["page"] == PDF_PAGE
    assert entropy_fusion["locator"]["row"] == "1620.00 SOL-B to LIQ transition"
    assert entropy_fusion["locator"]["column"] == "S"
    for observation_id, expected_temperature in (
        ("barin95_nafeo2_solb_transition_870", "870."),
        ("barin95_nafeo2_solb_transition_1270", "1270."),
    ):
        transition = rows[observation_id]
        assert transition["values"]["T_m_K"] == expected_temperature
        assert transition["locator"]["page"] == PDF_PAGE
        assert transition["locator"]["section"] == "References"
        assert transition["locator"]["row"] == "SOL-B"
        assert transition["locator"]["column"] == "Remarks"
        assert transition["point_conditions"]["temperature_K"]["locator"][
            "section"
        ] == "References"


def _assert_printed_row_count(document: dict) -> None:
    rows = _observations(document)
    solid_points = rows["barin95_nafeo2_solb_cp"]["values"]["points"]
    liquid_points = rows["barin95_nafeo2_liq_cp"]["values"]["points"]
    assert len(solid_points) == 16
    assert len(liquid_points) == 5
    assert len(solid_points) + 1 + len(liquid_points) == 22
    assert len({point["T_K"] for point in solid_points + liquid_points}) == 20


def test_render_cells_and_printed_row_count_are_pinned() -> None:
    document = _document()
    _assert_render_pins(document)
    _assert_printed_row_count(document)
    rows = _observations(document)
    for observation_id in (
        "barin95_nafeo2_solb_cp",
        "barin95_nafeo2_solb_entropy",
        "barin95_nafeo2_liq_cp",
        "barin95_nafeo2_liq_entropy",
        "barin95_nafeo2_entropy_of_fusion_1620",
    ):
        assert rows[observation_id]["units"] == "J/mol/K"
        assert rows[observation_id]["units_note"] == "Printed as J/(K mol)."


def test_render_value_and_row_count_mutations_fail() -> None:
    document = _document()
    mutated_value = deepcopy(document)
    rows = _observations(mutated_value)
    rows["barin95_nafeo2_solb_cp"]["values"]["points"][0]["value"] = "84.516"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated_value)

    mutated_count = deepcopy(document)
    rows = _observations(mutated_count)
    rows["barin95_nafeo2_solb_cp"]["values"]["points"].pop()
    with pytest.raises(AssertionError):
        _assert_printed_row_count(mutated_count)


def _point_value(observation: object) -> Decimal:
    value = observation.value
    assert value.kind is ValueKind.POINT
    assert value.point is not None
    return value.point


def _temperature_condition(observation: object) -> Decimal:
    condition = observation.point_conditions["temperature_K"]
    state_value = condition.state.value
    if isinstance(state_value, Value):
        assert state_value.kind is ValueKind.POINT
        assert state_value.point is not None
        return state_value.point
    return Decimal(str(state_value))


def test_isolated_migration_maps_all_rows_phases_and_temperatures() -> None:
    document = _document()
    _assert_render_pins(document)
    _assert_printed_row_count(document)

    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    migrated = migrator.result.observations
    assert len(migrated) == 89
    assert not migrator.result.validation.hard_issues

    unknown_quantities = {
        observation_id
        for observation_id, observation in migrated.items()
        if observation.identity.quantity is None
        or not observation.identity.quantity.is_value
    }
    unknown_phases = {
        observation_id
        for observation_id, observation in migrated.items()
        if observation.identity.species.phase is None
        or not observation.identity.species.phase.is_value
    }
    assert unknown_quantities == set()
    assert unknown_phases == set()
    unavailable_values = {
        observation_id
        for observation_id, observation in migrated.items()
        if observation.value.kind is ValueKind.UNAVAILABLE
    }
    assert unavailable_values == set()
    assert all(
        observation.point_conditions
        and observation.point_conditions["temperature_K"].state.is_value
        and observation.point_conditions["temperature_K"].locator.page == PDF_PAGE
        for observation in migrated.values()
    )
    transition_observations = [
        observation
        for observation in migrated.values()
        if observation.identity.quantity.value is Quantity.TRANSITION_TEMPERATURE
    ]
    assert len(transition_observations) == 3
    assert all(
        observation.identity.temperature_K.is_not_applicable
        and _point_value(observation) == _temperature_condition(observation)
        for observation in transition_observations
    )
    tpt_870 = migrated[f"{SOURCE_ID}::barin95_nafeo2_solb_transition_870"]
    assert tpt_870.locator.page == PDF_PAGE
    assert tpt_870.locator.section == "References"
    fusion_transition = migrated[
        f"{SOURCE_ID}::barin95_nafeo2_fusion_transition_1620"
    ]
    assert fusion_transition.locator.page == PDF_PAGE
    assert fusion_transition.locator.section is None
    assert all(
        observation.identity.temperature_K.is_value
        and Decimal(str(observation.identity.temperature_K.value))
        == _temperature_condition(observation)
        for observation in migrated.values()
        if observation not in transition_observations
    )
    assert all(
        observation.identity.total_pressure_Pa is not None
        and not observation.identity.total_pressure_Pa.is_value
        and observation.identity.fO2_Pa is not None
        and not observation.identity.fO2_Pa.is_value
        for observation in migrated.values()
    )

    expected_quantities = {
        Quantity.CP: 21,
        Quantity.S: 21,
        Quantity.DELTA_FH: 21,
        Quantity.H_MINUS_H298: 21,
        Quantity.TRANSITION_TEMPERATURE: 3,
        Quantity.ENTHALPY_OF_FUSION: 1,
        Quantity.ENTROPY_OF_FUSION: 1,
    }
    for quantity, count in expected_quantities.items():
        assert sum(
            observation.identity.quantity.value is quantity
            for observation in migrated.values()
        ) == count

    solid_cp = next(
        observation
        for observation in migrated.values()
        if observation.identity.quantity.value is Quantity.CP
        and observation.identity.species.phase.value is Phase.CR
        and observation.identity.temperature_K.value == Decimal("298.15")
    )
    assert _point_value(solid_cp) == Decimal("84.517")
    assert _temperature_condition(solid_cp) == Decimal("298.15")
    assert solid_cp.locator.page == PDF_PAGE
    assert solid_cp.locator.published_page == PRINTED_PAGE
    assert solid_cp.locator.section is None
    solid_entropy = next(
        observation
        for observation in migrated.values()
        if observation.identity.quantity.value is Quantity.S
        and observation.identity.temperature_K.value == Decimal("298.15")
    )
    assert _point_value(solid_entropy) == Decimal("88.299")
    formation_1200 = next(
        observation
        for observation in migrated.values()
        if observation.identity.quantity.value is Quantity.DELTA_FH
        and observation.identity.temperature_K.value == Decimal("1200.00")
    )
    assert _point_value(formation_1200) == Decimal("-808.006")
    assert formation_1200.locator.page == PDF_PAGE
    assert formation_1200.locator.section is None
    solid_heat_content_1620 = next(
        observation
        for observation in migrated.values()
        if observation.identity.quantity.value is Quantity.H_MINUS_H298
        and observation.identity.species.phase.value is Phase.CR
        and observation.identity.temperature_K.value == Decimal("1620.00")
    )
    assert _point_value(solid_heat_content_1620) == Decimal("123.343")
