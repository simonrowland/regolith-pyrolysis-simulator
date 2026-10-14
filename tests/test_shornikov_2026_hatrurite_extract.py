"""Source pins for Shornikov (2026) Table 2 partial pressures."""

from __future__ import annotations

import copy
import sys
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator, REPO_ROOT, convert_pressure_to_pa
from simulator.battery.enums import EvidenceClass, IdentityEqualKind, Quantity
from simulator.battery.identity import identity_equal

TOOLS = REPO_ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import validate_literature_extracts as validator  # noqa: E402


EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/shornikov-2026-hatrurite-evaporation.yaml"
)


def test_table_2_all_points_migrate_with_temperature_pressure_and_conditions():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    migrator = Migrator(root=REPO_ROOT, index=None, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    records = list(result.observations.values())

    assert len(records) == 134
    assert len(result.experiments) == 1
    assert len(result.queue) == 2
    assert sorted((tuple(item.axes), item.why) for item in result.queue) == [
        (("phase",), "missing phase"),
        (("phase",), "missing phase"),
    ]
    assert not result.registry_issues

    pressure_records = [
        record
        for record in records
        if record.identity.quantity.value is Quantity.P_PARTIAL
    ]
    activity_records = [
        record for record in records if record.identity.quantity.value is Quantity.ACTIVITY
    ]
    assert len(pressure_records) == 104
    assert len(activity_records) == 30

    for formula, block in document["species"].items():
        source_observation = next(
            observation
            for observation in block["observations"]
            if observation["observation_id"].startswith("shornikov_2026_table2_")
        )
        source_points = source_observation["values"]["points"]
        assert "molybdenum effusion cell (according to Knudsen)" in source_observation[
            "values"
        ]["note"]
        migrated = sorted(
            (
                record
                for record in pressure_records
                if record.identity.species.formula == formula
            ),
            key=lambda record: record.identity.temperature_K.value,
        )
        assert len(migrated) == len(source_points)
        for point, record in zip(source_points, migrated, strict=True):
            expected_pressure, relation = convert_pressure_to_pa(
                point["pressure_atm"], "atm"
            )
            assert relation == "atm_to_Pa"
            assert record.value.point == expected_pressure
            assert record.identity.temperature_K.value == Decimal(point["T_K"])
            condition = record.point_conditions["temperature_K"]
            assert condition.state.value == Decimal(point["T_K"])
            assert condition.locator.table == "2"
            assert condition.locator.pdf_page_index == 10
            assert record.locator.published_page == 306

    # The table states no composition map, oxygen fugacity, or in-cell total
    # pressure. Keep those axes unknown while confirming printed T is bound.
    outcomes = [
        identity_equal(record.identity, record.identity) for record in pressure_records
    ]
    assert all("temperature_K" not in outcome.fields for outcome in outcomes)
    assert all(outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN for outcome in outcomes)

    for formula in ("CaO", "SiO2"):
        observation = next(
            row
            for row in document["species"][formula]["observations"]
            if row["observation_id"].startswith("shornikov_2026_figure4_")
        )
        source_points = observation["values"]["points"]
        migrated = sorted(
            (
                record
                for record in activity_records
                if record.identity.species.formula == formula
            ),
            key=lambda record: record.identity.temperature_K.value,
        )
        assert len(migrated) == len(source_points) == 15
        reference = migrated[0].identity.reference_state.value
        assert reference.convention.value == "raoultian_pure_endmember"
        assert reference.endmember.formula == formula
        assert reference.endmember.phase.tag.value == "unknown"
        assert reference.endmember.polymorph.tag.value == "unknown"
        outcomes = [identity_equal(record.identity, record.identity) for record in migrated]
        assert all(outcome.kind is IdentityEqualKind.IDENTITY_UNKNOWN for outcome in outcomes)
        for point, record in zip(source_points, migrated, strict=True):
            assert record.value.point == Decimal(point["activity"])
            assert record.identity.temperature_K.value == Decimal(point["T_K"])
            assert record.evidence.class_.value is EvidenceClass.FIGURE_ONLY
            condition = record.point_conditions["temperature_K"]
            assert condition.state.value == Decimal(point["T_K"])
            assert condition.locator.table == "2"
            assert record.locator.figure == "4"


def test_table_2_fidelity_series_pins_fail_after_value_mutation():
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert not validator.check_all_fidelity_samples_match(document, label=str(EXTRACT))

    for sample in document["fidelity_samples"]:
        changed = copy.deepcopy(document)
        block = changed["species"][sample["species"]]
        row = next(
            item
            for item in block["observations"]
            if item["observation_id"] == sample["observation_id"]
        )
        if sample["field"] == "points":
            point = row["values"]["points"][0]
            value_key = "pressure_atm" if "pressure_atm" in point else "activity"
            point[value_key] = "9.99e-99"
        else:
            row["values"][sample["field"]] += " mutation"
        assert validator.check_fidelity_sample_matches(changed, sample)
