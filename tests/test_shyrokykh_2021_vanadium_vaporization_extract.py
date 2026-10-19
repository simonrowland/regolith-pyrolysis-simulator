from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity, ValueKind
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import iter_locators


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "shyrokykh-2021-vanadium-vaporization"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"

# Independently transcribed from the 600 dpi render of Table I, PDF page 3.
TABLE_I_RENDER = [
    ("CSV1", "44.61", "47.29", "8.10"),
    ("CSV2", "44.37", "49.53", "6.09"),
    ("CSV3", "44.35", "49.52", "6.12"),
]

# Independently transcribed from the 600 dpi render of Table II, PDF page 5.
TABLE_II_RENDER = [
    ("1", "CSV1", "85 kPa", "argon", "250"),
    ("2", "CSV2", "85 kPa", "argon", "not marked"),
    ("3", "CSV2", "85 kPa", "argon", "500"),
    ("4", "CSV2", "100 kPa", "argon", "500"),
    ("5", "CSV3", "50 kPa", "argon", "250"),
    ("6", "CSV3", "85 kPa", "argon", "500"),
    ("7", "CSV3", "85 kPa", "argon", "not marked"),
]

# Independently transcribed from the 600 dpi render of Table III, PDF page 7.
TABLE_III_RENDER = [
    ("O K", "51.86", "35.33", "45.30", "43.60", "45.04"),
    ("Al K", "2.81", "1.16", "0.31", "0.51", "0.76"),
    ("Si K", "21.54", "15.07", "25.87", "23.13", "26.52"),
    ("Ca K", "8.80", "17.16", "21.92", "21.35", "20.17"),
    ("V K", "5.16", "13.19", "0.36", "2.14", "1.72"),
]


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text())


def _table_context(document: dict, number: str) -> dict:
    return next(
        item
        for item in document["species"]["V2O5"]["context"]
        if item["locator"].get("table") == number
    )


def _table_i_grid(document: dict) -> list[tuple[str, ...]]:
    rows = []
    for observation in document["species"]["V2O5"]["observations"]:
        values = observation["values"]
        if values.get("quantity") != "mass_loss_fraction":
            continue
        composition = values["initial_composition_as_printed"]
        rows.append(
            (
                values["sample_id"],
                composition["CaO_wt_pct"],
                composition["SiO2_wt_pct"],
                composition["V2O5_wt_pct"],
            )
        )
    csv3 = _table_context(document, "I")["values"]["rows"][0]
    rows.append(
        (
            csv3["sample_id"],
            csv3["CaO_wt_pct"],
            csv3["SiO2_wt_pct"],
            csv3["V2O5_wt_pct"],
        )
    )
    return sorted(rows)


def _table_ii_grid(document: dict) -> list[tuple[str, ...]]:
    rows = _table_context(document, "II")["values"]["rows"]
    return [
        (
            row["table_row"],
            row["sample_id"],
            row["total_pressure_as_printed"],
            row["heating_atmosphere"],
            row["oxygen_top_blowing_cm3_min"],
        )
        for row in rows
    ]


def _table_iii_grid(document: dict) -> list[tuple[str, ...]]:
    rows = _table_context(document, "III")["values"]["rows"]
    return [
        (
            row["element"],
            row["spot_1_atomic_pct"],
            row["spot_2_atomic_pct"],
            row["spot_3_atomic_pct"],
            row["area_1_atomic_pct"],
            row["area_2_atomic_pct"],
        )
        for row in rows
    ]


def _migrate() -> Migrator:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator


def test_render_transcribed_tables_bind_cells_to_samples_and_headers() -> None:
    document = _document()
    assert _table_i_grid(document) == TABLE_I_RENDER
    assert _table_ii_grid(document) == TABLE_II_RENDER
    assert _table_iii_grid(document) == TABLE_III_RENDER

    for locator in iter_locators(document):
        assert locator["page"] == locator["pdf_page_index"] + 1
        assert "published_page" in locator


def test_render_and_extract_cell_mutations_fail_table_binding() -> None:
    document = _document()

    changed_render = deepcopy(TABLE_II_RENDER)
    changed_render[0] = (*changed_render[0][:-1], "251")
    with pytest.raises(AssertionError):
        assert _table_ii_grid(document) == changed_render

    changed_extract = deepcopy(document)
    changed_table = _table_context(changed_extract, "II")
    changed_table["values"]["rows"][0][
        "oxygen_top_blowing_cm3_min"
    ] = "251"
    with pytest.raises(AssertionError):
        assert _table_ii_grid(changed_extract) == TABLE_II_RENDER


def test_text_bounds_keep_sample_and_conditions_but_expose_migration_gap() -> None:
    document = _document()
    observations = document["species"]["V2O5"]["observations"]
    expected_bounds = {"CSV1": "77", "CSV2": "45"}
    for observation in observations:
        values = observation["values"]
        sample = values["sample_id"]
        assert values["mass_loss_pct"] == {
            "kind": "bound",
            "bound_operator": ">",
            "bound_value": expected_bounds[sample],
        }
        composition = values["initial_composition_as_printed"]
        assert (
            sample,
            composition["CaO_wt_pct"],
            composition["SiO2_wt_pct"],
            composition["V2O5_wt_pct"],
        ) == next(row for row in TABLE_I_RENDER if row[0] == sample)
        assert observation["quench_statement"]["preservation"] == "not_stated"
        assert observation["point_conditions"]["oxygen_partial_pressure"]["state"]["tag"] == "unknown"

    migrator = _migrate()
    rows = list(migrator.result.observations.values())
    assert len(rows) == 2
    assert not migrator.result.validation.hard_issues
    for row in rows:
        assert row.identity.quantity.value is Quantity.MASS_LOSS_FRACTION
        assert row.identity.species.formula == "V2O5"
        assert row.identity.species.phase.value is Phase.L
        assert row.identity.temperature_K.value == 1873
        assert row.point_conditions is not None
        assert len(row.point_conditions) == 8
        assert all(item.locator is not None for item in row.point_conditions.values())
        assert row.value.kind is ValueKind.UNAVAILABLE

    figures = [
        item
        for item in document["species"]["V2O5"]["context"]
        if item["type"] == "figure_record"
    ]
    assert len(figures) == 16
    assert all(item["values"]["points"] == [] for item in figures)
    assert all(
        item["values"]["digitisation"] == "deferred to a figure pass"
        for item in figures
    )
