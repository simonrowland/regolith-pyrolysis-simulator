"""Table 3 pins and isolated migration for Stolyarova et al. (1996)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.enums import Quantity
from tests.battery.test_migrate import _migrate_real_extract


ROOT = Path(__file__).resolve().parents[1]
EXTRACT_NAME = "stolyarova-1996-cao-alumina-silica-kems.yaml"
EXTRACT = ROOT / "data/literature/extracts" / EXTRACT_NAME


def test_table3_cells_and_migration_match_page9_render(tmp_path):
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    observations = [
        observation
        for body in document["species"].values()
        for observation in body.get("observations", [])
        if observation.get("locator", {}).get("table") == "3"
    ]
    assert len(observations) == 5
    assert {
        (point["x_A_over_x_C_as_printed"], point["x_S_as_printed"])
        for observation in observations
        for point in observation["values"]["points"]
    } == {
        (x_a_over_x_c, x_s)
        for x_a_over_x_c, x_s in (
            ("0.11", "0.44"),
            ("0.15", "0.25"),
            ("0.15", "0.40"),
            ("0.16", "0.33"),
            ("0.16", "0.42"),
            ("0.16", "0.50"),
            ("0.17", "0.10"),
            ("0.29", "0.44"),
            ("0.30", "0.50"),
            ("0.33", "0.10"),
            ("0.33", "0.40"),
            ("0.34", "0.25"),
            ("0.34", "0.33"),
            ("0.35", "0.50"),
            ("0.39", "0.35"),
            ("0.50", "0.25"),
            ("0.58", "0.32"),
            ("0.60", "0.33"),
            ("0.61", "0.10"),
            ("0.61", "0.50"),
            ("0.62", "0.40"),
            ("0.63", "0.25"),
            ("0.64", "0.48"),
            ("0.67", "0.38"),
            ("0.78", "0.43"),
            ("1.00", "0.10"),
            ("1.00", "0.25"),
            ("1.00", "0.33"),
            ("1.00", "0.40"),
            ("1.00", "0.50"),
        )
    }
    assert sum(len(observation["values"]["points"]) for observation in observations) == 137

    cells_by_column = {
        observation["values"]["activity_column_as_printed"]: {
            (point["x_A_over_x_C_as_printed"], point["x_S_as_printed"]): point[
                "activity_as_published"
            ]
            for point in observation["values"]["points"]
        }
        for observation in observations
    }
    # Page 9 render: preserve both printed CaO 0.853 cells, including the flagged outlier.
    assert cells_by_column["1"][("0.15", "0.25")] == "0.853"
    assert cells_by_column["1"][("0.33", "0.10")] == "0.853"
    assert cells_by_column["2"][("0.61", "0.50")] == "0.251"
    assert cells_by_column["5"][("1.00", "0.50")] == "0.519"

    migrated = _migrate_real_extract(tmp_path, EXTRACT_NAME)
    assert migrated.validation is not None
    assert migrated.validation.hard_issues == ()
    table3_rows = [
        row
        for row in migrated.observations.values()
        if "::stolyarova_1996_table3_" in row.observation_id
    ]
    assert len(table3_rows) == 137
    assert Counter(row.identity.quantity.value for row in table3_rows) == {
        Quantity.ACTIVITY: 137
    }
    assert all(row.identity.phase.is_value for row in table3_rows)
    assert all(row.identity.temperature_K.is_value for row in table3_rows)
    assert all(row.identity.temperature_K.value == 1933 for row in table3_rows)
    assert all(row.identity.composition.is_value for row in table3_rows)
    assert all(row.point_conditions["temperature_K"].locator is not None for row in table3_rows)
    assert all(row.point_conditions["composition"].locator is not None for row in table3_rows)
    assert all("total_pressure_Pa" not in row.point_conditions for row in table3_rows)

    migrated_cao_outliers = {
        row.value.point
        for row in table3_rows
        if row.identity.species.formula == "CaO"
        and row.value.kind.value == "point"
        and row.value.point == Decimal("0.853")
    }
    assert migrated_cao_outliers == {Decimal("0.853")}
