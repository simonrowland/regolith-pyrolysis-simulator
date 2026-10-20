from __future__ import annotations

from copy import deepcopy
import csv
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, Quantity
from simulator.battery.identity import profile_for
from simulator.battery.migrate import Migrator


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "borisov-2018-compilation"
EXTRACT = ROOT / "data/literature/extracts" / f"{SOURCE_ID}.yaml"
GRID = ROOT / "tests/fixtures/borisov_2018_table1_render_grid.psv"
BAR_REFERENCE_PA = Decimal("100000")


def _grid() -> list[dict[str, str]]:
    with GRID.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source, delimiter="|"))


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table1_rows(document: dict) -> dict[str, dict]:
    return {
        str(item["values"]["row_as_printed"]): item
        for item in document["species"]["Fe"]["observations"]
        if item["observation_id"].startswith("borisov_2018_table1_row_")
    }


def _assert_table1_grid(document: dict, expected: list[dict[str, str]]) -> None:
    source_rows = _table1_rows(document)
    assert len(expected) == 31
    assert len(source_rows) == 31
    for row in expected:
        item = source_rows[row["row"]]
        values = item["values"]
        assert values["sample_as_published"] == row["sample"]
        assert values["T_C"] == row["T_C"]
        assert values["T_cell_as_published"] == row["T_cell"]
        assert values["duration_h_as_published"] == row["duration_cell"]
        assert values["fe3_fe2_ratio"] == row["Fe3+/Fe2+"]
        assert values["composition_wt_pct_as_published"] == {
            "SiO2": row["SiO2"],
            "Al2O3": row["Al2O3"],
            "Fe2O3": row["Fe2O3_total"],
            "MgO": row["MgO"],
            "CaO": row["CaO"],
            "Na2O": row["Na2O"],
            "K2O": row["K2O"],
        }
        assert values["Total_wt_pct_as_published"] == row["total"]
        assert values["second_locator"] == {
            "table": "Supplementary Table 2",
            "record": f"spreadsheet row {int(row['row']) + 154}",
        }
        oxygen = item["point_conditions"]["fO2_log"]
        assert oxygen["reference_pressure"] == {
            "value": 1,
            "units": "bar",
            "basis": "convention",
            "printed": False,
        }
        assert item["locator"]["page"] == 3
        assert item["locator"]["pdf_page_index"] == 2
        assert item["locator"]["published_page"] == 98
        assert item["locator"]["table"] == "1"


def _migrate():
    migrator = Migrator(root=ROOT)
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    return migrator.result


def test_article_table1_is_render_pinned_and_not_duplicated() -> None:
    document = _document()
    expected = _grid()
    _assert_table1_grid(document, expected)

    mutated_grid = deepcopy(expected)
    mutated_grid[0]["Fe3+/Fe2+"] = "9.99"
    with pytest.raises(AssertionError):
        _assert_table1_grid(document, mutated_grid)

    mutated_extract = deepcopy(document)
    first = _table1_rows(mutated_extract)["1"]
    first["values"]["fe3_fe2_ratio"] = "9.99"
    with pytest.raises(AssertionError):
        _assert_table1_grid(mutated_extract, expected)

    observations = document["species"]["Fe"]["observations"]
    assert len(observations) == 435
    assert not any(
        item.get("values", {}).get("source_block_label_as_printed") == "Present Ms. / air"
        for item in observations
    )
    quoted = [
        item
        for item in observations
        if item.get("values", {}).get("evidence_class") == "quoted_attributed"
    ]
    assert len(quoted) == 404
    assert all(item["values"].get("attribution") for item in quoted)

    figures = [
        item
        for item in document["species"]["Fe"]["context"]
        if item["type"] == "figure_record"
    ]
    assert len(figures) == 10
    assert [item["locator"]["figure"] for item in figures] == [
        str(number) for number in range(1, 11)
    ]
    for item in figures:
        assert item["locator"]["page"] == item["locator"]["pdf_page_index"] + 1
        assert item["values"]["identity"]["species"] == "Fe"
        assert item["values"]["identity"]["phase"] == "l"
        assert item["values"]["identity"].get("temperature_as_printed")
        assert item["values"]["axes_as_printed"]
        assert item["values"]["quantity"] == "valence_ratio"
        assert item["values"]["digitisation"] == "deferred to a figure pass"
        assert item["values"].get("points") is None
        assert all(type(series["marker_count"]) is int for series in item["values"]["series"])


def test_article_table1_migration_binds_conditions_and_ferric_ratio() -> None:
    expected = _grid()
    result = _migrate()
    records = {
        record.observation_id.rsplit("_row_", 1)[-1]: record
        for record in result.observations.values()
        if "::borisov_2018_table1_row_" in record.observation_id
    }
    assert len(records) == 31

    for row in expected:
        record = records[row["row"]]
        assert record.identity.quantity.value is Quantity.FE3_FE2_RATIO
        assert record.identity.species.formula == "Fe"
        assert record.identity.species.phase.value is Phase.L
        assert record.identity.temperature_K.value == Decimal(row["T_C"]) + Decimal("273.15")
        assert record.value.point is not None
        assert record.value.point == Decimal(row["Fe3+/Fe2+"])

        profile = profile_for(record.identity)
        for axis in profile.required:
            state = getattr(record.identity, axis)
            assert state is not None and not state.is_unknown, axis
        assert record.identity.total_pressure_Pa.value == Decimal("101325")

        conditions = record.point_conditions
        assert conditions is not None
        for key in (
            "T_C",
            "duration_h",
            "fO2_log",
            "atmosphere",
            "total_pressure_Pa",
            "composition",
            "quench_state",
        ):
            assert key in conditions
            assert conditions[key].locator is not None
        assert conditions["T_C"].state.value == Decimal(row["T_C"])
        if row["duration_h"] == "unknown":
            assert conditions["duration_h"].state.is_unknown
        else:
            assert conditions["duration_h"].state.value == Decimal(row["duration_h"])
        oxygen = conditions["fO2_log"]
        assert oxygen.state.value == Decimal("-0.68")
        assert oxygen.locator.table == "Supplementary Table 2"
        assert record.identity.fO2_Pa.value == BAR_REFERENCE_PA * Decimal("10") ** Decimal("-0.68")
        assert conditions["total_pressure_Pa"].state.value == Decimal("101325")
        assert conditions["atmosphere"].state.value == "air"
        assert conditions["quench_state"].state.value.value == "not_stated"

    assert all(
        record.identity.species.formula == "Fe"
        for record in result.observations.values()
    )

    context = _document()["species"]["Fe"]["context"]
    model_context = [item for item in context if item["type"] == "model_fit_context"]
    assert {item["locator"]["table"] for item in model_context} >= {"2", "3"}
    assert all(item["values"]["method_class"] == "model_derived" for item in model_context)

