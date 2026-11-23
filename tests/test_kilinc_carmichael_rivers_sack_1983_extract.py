from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import yaml

from simulator.battery.migrate import Migrator
from simulator.yaml_cache import yaml_mapping_source_issues
from tools.validate_literature_extracts import validate_extract_document, validate_extract_file

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air.yaml"
SOURCE_ID = "kilinc-carmichael-rivers-sack-1983-ferric-ferrous-air"
EXPECTED_ROWS = (36, 10, 5)
PRINTED_OXIDES = {"SiO2", "TiO2", "Al2O3", "Fe2O3", "Cr2O3", "FeO", "MnO", "MgO", "CaO", "Na2O", "K2O", "P2O5"}


def test_kilinc_extract_fidelity_and_source_mapping() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    assert validate_extract_file(EXTRACT, check_fidelity_match=True) == []
    assert yaml_mapping_source_issues(EXTRACT.read_text(encoding="utf-8")) == []
    observations = document["species"]["Fe"]["observations"]
    assert tuple(len(item["values"]["rows"]) for item in observations) == EXPECTED_ROWS
    for item in observations[:2]:
        for row in item["values"]["rows"]:
            assert set(row["oxide_wt_percent"]) == PRINTED_OXIDES
            assert isinstance(row["T_C"], str)
            assert isinstance(row["T_C_as_printed"], str)
            assert isinstance(row["run_id_as_printed"], str)
            assert all(isinstance(value, str) for value in row["oxide_wt_percent"].values())
            assert isinstance(row["Fe2O3_wt_pct"], str)
            assert isinstance(row["FeO_wt_pct"], str)
            assert row["Fe2O3_wt_pct"] == row["oxide_wt_percent"]["Fe2O3"]
            assert row["FeO_wt_pct"] == row["oxide_wt_percent"]["FeO"]
            assert row["point_conditions"]["T_C"]["state"]["value"] == row["T_C"]
            assert row["point_conditions"]["composition"]["state"]["value"]["components"] == row["oxide_wt_percent"]
            assert row["point_conditions"]["fO2_log"]["state"]["tag"] == "unknown"
            assert "locator" not in row["point_conditions"]["fO2_log"]
    table1_rows = observations[0]["values"]["rows"]
    table1_by_sample = {row["run_id_as_printed"]: row for row in table1_rows}
    assert table1_by_sample["9"]["locator"]["published_page"] == 138
    assert table1_by_sample["9"]["locator"]["pdf_page_index"] == 2
    for sample in ("22", "27"):
        assert table1_by_sample[sample]["locator"]["published_page"] == 137
        assert table1_by_sample[sample]["locator"]["pdf_page_index"] == 1
    timed_rows = observations[2]["values"]["rows"]
    assert all("Cr2O3" not in row["oxide_wt_percent"] for row in timed_rows)
    assert all(isinstance(row["T_C"], str) for row in timed_rows)
    assert all(isinstance(row["T_C_as_printed"], str) for row in timed_rows)
    assert all(isinstance(value, str) for row in timed_rows for value in row["oxide_wt_percent"].values())
    for row in timed_rows:
        assert row["Fe2O3_wt_pct"] == row["oxide_wt_percent"]["Fe2O3"]
        assert row["FeO_wt_pct"] == row["oxide_wt_percent"]["FeO"]
        assert row["FeO_method_as_printed"] == "volumetric and colorimetric procedure of Wilson (1960)"
        assert row["FeO_method_locator"]["published_page"] == 137
        assert row["FeO_method_locator"]["pdf_page_index"] == 1
        assert row["FeO_method_locator"]["section"] == "Experimental Procedures"
        assert row["FeO_method_locator"]["column"] == "left"
        assert row["point_conditions"]["atmosphere"]["locator"] == {
            "published_page": 137,
            "pdf_page_index": 1,
            "section": "Results and Discussion",
            "column": "right",
        }


def test_kilinc_cell_locators_follow_printed_table_orientation() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    table1, table2, table3 = (
        item["values"]["rows"] for item in document["species"]["Fe"]["observations"]
    )
    for rows, columns in (
        (table1, [str(n) for n in (*range(1, 9), *range(21, 29), *range(9, 21), *range(29, 37))]),
        (table2, "37 38 49 40 41 42 43 44 45 46".split()),
        (table3, "5 12 25 48 70".split()),
    ):
        assert [row["run_id_as_printed"] for row in rows] == columns
        for row, column in zip(rows, columns, strict=True):
            assert row["locator"]["column"] == column
            printed_rows = {
                "run_id_as_printed": "Time (h)" if rows is table3 else "Sample #",
                "T_C": "T° C",
                "duration_h_as_printed": "Time (h)",
                "FeO_wt_pct": "FeO",
                "Fe2O3_wt_pct": "Fe2O3",
            }
            if rows is table3:
                printed_rows["printed_total_wt_percent"] = "Total"
            else:
                printed_rows["sample_identifier_as_printed"] = "Identifier"
            assert row["cell_locators"] == {
                field: {"row": label, "column": column}
                for field, label in printed_rows.items()
            }
            assert row["point_conditions"]["T_C"]["locator"] == row["locator"] | {"row": "T° C"}

    assert [
        (sample["locator"]["row"], sample["locator"]["column"])
        for sample in document["fidelity_samples"]
    ] == [("Fe2O3", "1"), ("FeO", "1"), ("Fe2O3", "37"), ("FeO", "5"), ("Fe2O3", "1"), ("FeO", "1")]


def test_kilinc_row_coordinates_reach_every_migrated_observation() -> None:
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert len(result.observations) == sum(EXPECTED_ROWS)
    assert len(result.queue) == 0
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    for observation in result.observations.values():
        conditions = observation.point_conditions
        assert conditions["T_C"].state.is_value
        assert conditions["composition"].state.is_value
        assert conditions["atmosphere"].state.value == "air"
        assert conditions["fO2_log"].state.is_unknown
        assert observation.identity.total_pressure_Pa.is_unknown
        assert observation.identity.quantity.value.value == "fe3_fe2_ratio"
    first = next(iter(result.observations.values()))
    assert first.value.point == Decimal("1.588727479381540137894756618")


def test_kilinc_fidelity_pin_detects_changed_primary_cell() -> None:
    document = yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))
    mutated = deepcopy(document)
    mutated["species"]["Fe"]["observations"][0]["values"]["rows"][0]["FeO_wt_pct"] = "0.65"
    assert validate_extract_document(mutated, expected_source_id=SOURCE_ID, check_fidelity_match=True)
