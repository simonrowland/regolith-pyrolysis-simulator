"""Source pins and isolated migration for Kambayashi & Kato (1984)."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import yaml

from simulator.battery.enums import Quantity, ValueKind
from simulator.battery.migrate import Migrator, REPO_ROOT
from tools.validate_literature_extracts import validate_extract_document


EXTRACT = REPO_ROOT / "data/literature/extracts/kambayashi-kato-1984-mgo-sio2-1973k.yaml"

TABLE1_RATIOS = (
    ("0.795", "27.33"), ("0.795", "26.90"), ("0.631", "27.17"),
    ("0.581", "24.11"), ("0.547", "20.27"), ("0.547", "20.42"),
    ("0.516", "14.08"), ("0.476", "8.49"), ("0.442", "3.85"),
    ("0.438", "3.80"), ("0.415", "2.79"), ("0.348", "2.79"),
    ("0.348", "2.77"), ("0.220", "0.0949"), ("0.220", "0.1000"),
)

TABLE2_ACTIVITIES = (
    ("0.795", "1", "0.107"), ("0.795", "1", "0.107"),
    ("0.631", "1", "0.107"), ("0.581", "0.975", "0.114"),
    ("0.547", "0.870", "0.128"), ("0.547", "0.870", "0.128"),
    ("0.516", "0.760", "0.152"), ("0.500", "0.684", "0.168"),
    ("0.476", "0.567", "0.203"), ("0.442", "0.394", "0.279"),
    ("0.438", "0.363", "0.292"), ("0.415", "0.313", "0.327"),
    ("0.348", "0.313", "0.327"), ("0.348", "0.313", "0.327"),
    ("0.220", "0.0335", "1"), ("0.220", "0.0335", "1"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def test_tables_match_rendered_rows_and_migrate_all_typed_values_as_points() -> None:
    document = _document()
    assert validate_extract_document(
        document,
        expected_source_id=EXTRACT.stem,
        pre_policy_ids=set(),
        check_fidelity_match=True,
    ) == []

    migrator = Migrator(root=REPO_ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    assert not migrator.result.validation.hard_issues
    assert not migrator.result.registry_issues
    assert len(migrator.result.observations) == 77

    by_quantity = Counter()
    for formula, body in document["species"].items():
        for row in body["observations"]:
            q = Quantity(row["values"]["quantity"])
            by_quantity[q] += 1
            migrated = migrator.result.observations[f"{EXTRACT.stem}::{row['observation_id']}"]
            source_value = next(
                row["values"][field]
                for field in ("activity", "ion_intensity", "ion_intensity_ratio")
                if field in row["values"]
            )
            assert migrated.value.kind is ValueKind.POINT, row["observation_id"]
            assert migrated.value.point == Decimal(str(source_value))
            assert migrated.identity.species.formula == formula
    assert by_quantity == {
        Quantity.ION_INTENSITY: 30,
        Quantity.ION_INTENSITY_RATIO: 15,
        Quantity.ACTIVITY: 32,
    }

    ratios = [
        (row["values"]["x_SiO2_as_printed"], row["values"]["ion_intensity_ratio"])
        for row in document["species"]["SiO"]["observations"]
        if row["values"]["quantity"] == "ion_intensity_ratio"
    ]
    assert sorted(ratios) == sorted(TABLE1_RATIOS)

    activities = []
    for formula in ("SiO2", "MgO"):
        for row in document["species"][formula]["observations"]:
            composition = row["values"]["composition_mole_fraction"]
            activities.append(
                (
                    composition["SiO2"],
                    row["values"]["activity"] if formula == "SiO2" else None,
                    row["values"]["activity"] if formula == "MgO" else None,
                    row["observation_id"],
                )
            )
    grouped = {}
    for x, si, mg, obs_id in activities:
        row_number = obs_id.rsplit("_r", 1)[1][:2]
        grouped.setdefault((x, row_number), {})
        grouped[(x, row_number)]["SiO2" if si is not None else "MgO"] = si if si is not None else mg
    assert sorted((x, pair.get("SiO2"), pair.get("MgO")) for (x, _), pair in grouped.items()) == sorted(TABLE2_ACTIVITIES)

    assert all(
        row["point_conditions"]["temperature_K"]["state"]["value"] == "1973"
        for formula in ("SiO", "Mg", "SiO2", "MgO")
        for row in document["species"][formula]["observations"]
    )


def test_figure_values_are_deferred_and_table2_blank_cell_stays_blank() -> None:
    document = _document()
    figures = [
        row for row in document["species"]["SiO2"]["context"]
        if row.get("type") == "figure_record"
    ]
    assert {row["locator"]["figure"] for row in figures} == {"1", "2", "3", "4", "5", "6"}
    assert all(
        row["values"]["digitisation"] == "deferred to a figure pass"
        and row["values"]["marker_count_per_series"]
        for row in figures
    )
    ratios = next(
        row for row in document["species"]["SiO2"]["context"]
        if row["observation_id"] == "kambayashi_1984_table2_activity_current_ratio_schema_gap"
    )
    assert len(ratios["values"]["rows"]) == 16
    assert ratios["values"]["rows"][7]["activity_current_ratio_as_printed"] is None
