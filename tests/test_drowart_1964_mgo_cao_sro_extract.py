"""Render pins and isolated migration for the Drowart 1964 KEMS tables."""

from __future__ import annotations

import copy
from decimal import Decimal

import pytest
import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document


SID = "drowart-1964-mgo-cao-sro-wadd-tr60-782-pt22"
EXTRACT = REPO_ROOT / "data/literature/extracts" / f"{SID}.yaml"

RENDER_PINS = (
    (
        "O2",
        "drowart_1964_mgo_cao_sro_wadd_tr60_782_pt22_p015_t04_r02_c03",
        "5.8e-5",
        15,
        "2a",
    ),
    (
        "MgO",
        "drowart_1964_mgo_cao_sro_wadd_tr60_782_pt22_p015_t04_r02_c05",
        "1.3e-3",
        15,
        "2a",
    ),
    (
        "O",
        "drowart_1964_mgo_cao_sro_wadd_tr60_782_pt22_p016_t06_r02_c04",
        "3.5e-3",
        16,
        "3a",
    ),
    (
        "Sr2O",
        "drowart_1964_mgo_cao_sro_wadd_tr60_782_pt22_p018_t14_r02_c05",
        "3.7e-4",
        18,
        "5c",
    ),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_pins(document: dict) -> None:
    for formula, observation_id, value, page, printed_table in RENDER_PINS:
        observation = next(
            item
            for item in document["species"][formula]["observations"]
            if item["observation_id"] == observation_id
        )
        assert observation["values"]["ion_current_ratio"] == value
        assert observation["locator"]["page"] == page
        assert observation["locator"]["table"] == printed_table


def _migrate() -> Migrator:
    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_pins_and_mutation_red_check() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=SID,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []
    _assert_render_pins(document)

    mutated = copy.deepcopy(document)
    row = next(
        item
        for item in mutated["species"]["O2"]["observations"]
        if item["observation_id"] == RENDER_PINS[0][1]
    )
    row["values"]["ion_current_ratio"] = "5.9e-5"
    with pytest.raises(AssertionError):
        _assert_render_pins(mutated)


def test_isolated_migration_preserves_table_rows_and_unprinted_conditions() -> None:
    document = _document()
    ratio_observations = [
        observation
        for body in document["species"].values()
        for observation in body["observations"]
        if observation["values"].get("quantity") == "ion_intensity_ratio"
    ]
    assert len(ratio_observations) == 169
    printed_rows = {
        (observation["locator"]["table"], observation["T_range_K"][0])
        for observation in ratio_observations
        if observation["values"].get("ion_current_ratio") is not None
    }
    assert len(printed_rows) == 43
    assert all("fO2_Pa" not in obs["point_conditions"] for obs in ratio_observations)
    assert all("composition" not in obs["point_conditions"] for obs in ratio_observations)
    assert all(
        obs["point_conditions"]["total_pressure_Pa"]["state"]["tag"] == "unknown"
        and "value" not in obs["point_conditions"]["total_pressure_Pa"]["state"]
        for obs in ratio_observations
    )
    assert all("temperature_K" in obs["point_conditions"] for obs in ratio_observations)

    migrated = _migrate()
    rows = [row for row in migrated.result.observations.values() if row.source_id == SID]
    assert len(rows) == 236
    assert not migrated.result.validation.hard_issues
    assert not migrated.result.registry_issues
    unavailable = [row for row in rows if row.value.kind is ValueKind.UNAVAILABLE]
    points = [row for row in rows if row.value.kind is ValueKind.POINT]
    assert len(unavailable) == 67
    assert len(points) == 169
    assert all(row.identity.quantity.value is Quantity.ION_INTENSITY_RATIO for row in points)

    for row in points:
        assert row.identity.temperature_K.is_value
        assert row.point_conditions is not None
        assert row.point_conditions["temperature_K"].state.is_value
        assert row.point_conditions["temperature_K"].locator is not None
        assert "total_pressure_Pa" not in row.point_conditions
        assert row.identity.composition.tag.value == "not_applicable"
        assert row.identity.total_pressure_Pa.tag.value == "not_applicable"
        assert row.identity.fO2_Pa.tag.value == "not_applicable"

    migrated_pins = {
        observation_id: Decimal(value)
        for _, observation_id, value, _, _ in RENDER_PINS
    }
    for row in points:
        for observation_id, value in migrated_pins.items():
            if row.observation_id.endswith(observation_id):
                assert row.value.point == value
