"""Render-pinned regression coverage for Hasapis et al. (1960), Table 4."""

from __future__ import annotations

import copy
from collections import Counter
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator, to_plain
from tools import validate_literature_extracts as vle


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTRACT = (
    REPO_ROOT
    / "data/literature/extracts/hasapis-panish-rosen-1960-refractory-vaporization.yaml"
)


def _observations(document: dict) -> dict[str, dict[str, dict]]:
    return {
        species: {
            row["observation_id"]: row
            for row in block.get("observations", [])
        }
        for species, block in document["species"].items()
    }


def test_table4_values_and_eight_printed_temperature_rows_match_render() -> None:
    """Pin three Table 4 cells to PDF p. 40 and retain all eight rows."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    rows = _observations(document)
    pins = (
        ("Al", "r02_c03", "≈ 0.4", 2, 3),
        ("Al", "r04_c04", "3.52", 4, 4),
        ("WO2", "r09_c09", "1.3", 9, 9),
    )
    for species, suffix, expected, table_row, table_column in pins:
        observation_id, observation = next(
            (key, row)
            for key, row in rows[species].items()
            if key.endswith(suffix)
        )
        assert observation["values"]["value"] == expected
        assert observation["locator"]["page"] == 40
        assert observation["locator"]["published_page"] == 34
        assert observation["locator"]["table_caption"] == "4"
        assert observation["locator"]["table_row_number"] == table_row
        assert observation["locator"]["table_column_number"] == table_column
        if species == "WO2":
            assert observation["values"]["ion_as_printed"] == (
                "$\\Sigma I_{WO_2}^{+}$"
            )

    wo2_log_rows = [
        row
        for row in rows["WO2"].values()
        if row["locator"]["table_column_number"] == 10
    ]
    assert len(wo2_log_rows) == 7
    for row in wo2_log_rows:
        assert row["locator"]["column_header"] == r"$\log \Sigma I_{WO_2}^{+}$"
        assert row["units"] == "log ΣI+WO2, as printed"
        assert row["values"]["quantity"] == "log_ion_current_sum"
        assert (
            row["values"]["log_ion_current_sum_as_printed"]
            == row["values"]["value"]
        )
        assert "no temperature multiplier is inferred" in row["values"]["reason"]

    aluminum_temperatures = {
        row["values"]["T_K"] for row in rows["Al"].values()
    }
    assert aluminum_temperatures == {
        "2126", "2260", "2273", "2345", "2360", "2389", "2441", "2495"
    }
    assert document["skipped_empty_or_dash"]["total"] == 10


def test_table4_fidelity_samples_turn_red_after_value_mutation() -> None:
    """The extract's independent render pins detect a changed stored cell."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert vle.check_all_fidelity_samples_match(document) == []

    mutated = copy.deepcopy(document)
    sample = mutated["fidelity_samples"][0]
    row = next(
        row
        for row in mutated["species"][sample["species"]]["observations"]
        if row["observation_id"] == sample["observation_id"]
    )
    row["values"][sample["field"]] = "≈ 0.5"
    assert vle.check_all_fidelity_samples_match(mutated)


def test_table4_isolated_migration_maps_temperature_and_keeps_pressure_absent() -> None:
    """All printed temperatures migrate; unpublished pressure stays absent."""
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    source_rows = {
        f"{document['source_id']}::{row['observation_id']}": row
        for block in document["species"].values()
        for row in block["observations"]
    }
    assert len(source_rows) == 54
    for row in source_rows.values():
        pressure = row["point_conditions"]["total_pressure_Pa"]
        assert pressure["state"] == {"tag": "unknown", "reason": "not_published"}
        assert "value" not in pressure["state"]
        oxygen = row["point_conditions"]["fO2_Pa"]
        assert oxygen["state"] == {"tag": "unknown", "reason": "not_published"}
        assert "value" not in oxygen["state"]

    migrator = Migrator(root=REPO_ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == 54
    assert len(result.works) == 1
    assert len(result.experiments) == 1
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    assert Counter(row.value.kind.value for row in result.observations.values()) == {
        "point": 27,
        "unavailable": 27,
    }

    migrated = {
        row.observation_id: to_plain(row)
        for row in result.observations.values()
    }
    assert len(migrated) == len(source_rows)
    for observation_id, source_row in source_rows.items():
        record = migrated[observation_id]
        temperature = record["point_conditions"]["temperature_K"]["state"]
        assert temperature["tag"] == "value"
        assert str(temperature["value"]) == source_row["values"]["T_K"]
        assert record["identity"]["temperature_K"]["tag"] == "value"
        assert (
            str(record["identity"]["temperature_K"]["value"])
            == source_row["values"]["T_K"]
        )
        assert record["identity"].get("total_pressure_Pa", {}).get("tag") != "value"
        assert record["identity"].get("fO2_Pa", {}).get("tag") != "value"
        for condition_name in ("composition", "fO2_Pa"):
            source_condition = source_row["point_conditions"][condition_name]
            migrated_condition = record["point_conditions"][condition_name]
            assert migrated_condition["state"] == source_condition["state"]
            assert migrated_condition["locator"] == source_condition["locator"]

    numeric = [
        record
        for record in migrated.values()
        if record["value"]["kind"] == "point"
    ]
    assert len(numeric) == 27
    assert sum(record["value"].get("approximate", False) for record in numeric) == 9
    assert sum(
        record["value"]["kind"] == "unavailable" for record in migrated.values()
    ) == 27
    for source_row in source_rows.values():
        values = source_row["values"]
        if values.get("approximate") is True:
            assert values["value"].startswith("≈ ")
            assert values["ion_intensity_as_printed"] == values["value"]
            assert values["admission_status"] == "admitted"

    for suffix in ("r06_c03", "r09_c05", "r09_c09"):
        observation_id = next(
            key for key in migrated if key.endswith(suffix)
        )
        identity = migrated[observation_id]["identity"]
        assert identity["composition"] == {"tag": "unknown", "reason": "not_published"}
        assert identity["temperature_K"]["tag"] == "value"
