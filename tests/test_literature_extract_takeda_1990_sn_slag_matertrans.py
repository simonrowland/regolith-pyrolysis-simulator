"""Source pins and isolated migration for Takeda et al. (1990)."""

import csv
import re
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from tests.battery.factories import migrate_one_extract
from tests.helpers.corpus_paths import regolith_corpus_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/takeda-1990-sn-slag-matertrans.yaml"
CELL_AUDIT = ROOT / "data/literature/extracts/audits/takeda-1990-sn-slag-matertrans.cells.csv"

TABLE1 = (
    ("1.0 to 1.3", "1.0", "1.3", "1423", ("0 to 60", "—", "—", "—", "100 to 40")),
    ("1.0 to 1.4", "1.0", "1.4", "1373", ("0 to 60", "—", "—", "—", "100 to 40")),
    ("1.0 to 1.6", "1.0", "1.6", "1433", ("0 to 60", "—", "—", "—", "100 to 40")),
    ("1.0 to 1.6", "1.0", "1.6", "1373", ("0 to 60", "—", "—", "—", "100 to 40")),
    ("1.4 to 0.9", "0.9", "1.4", "1423", ("0 to 60", "—", "—", "—", "100 to 40")),
    ("1.6 to 1.7", "1.6", "1.7", "1373", ("37 to 46", "12 to 16", "—", "—", "52 to 39")),
    ("1.1 to 1.8", "1.1", "1.8", "1423", ("42 to 58", "0 to 13", "—", "—", "58 to 32")),
    ("1.2 to 1.4", "1.2", "1.4", "1373", ("32 to 47", "—", "10 to 12", "—", "58 to 43")),
    ("0.9 to 1.6", "0.9", "1.6", "1423", ("43 to 57", "—", "0 to 20", "—", "48 to 34")),
    ("1.8", "1.8", "1.8", "1433", ("69", "16", "25", "3", "14")),
    ("1.0 to 7.0", "1.0", "7.0", "1573", ("0 to 53", "0 to 39", "0 to 86", "—", "100 to 0")),
    ("0.8 to 2.4", "0.8", "2.4", "1473, 1573", ("30 to 35", "—", "55 to 63", "5 to 11", "—")),
    ("1.7", "1.7", "1.7", "1523", ("42 to 45", "—", "58 to 55", "—", "—")),
    ("0.9", "0.9", "0.9", "1523", ("—", "32", "68", "—", "—")),
)
FIGURE4 = (
    ("A-S", "open square on solid line", "0 - 0.21", "0.40 - 0.56"),
    ("B-S", "open diamond on solid line", "0.34 - 0.45", "0.12 - 0.19"),
    ("E-S", "open inverted triangle on solid line", "0.44 - 0.54", "0.27 - 0.35"),
    ("C-S", "open upright triangle on solid line", "0.49 - 0.57", "0.18 - 0.22"),
    ("D-S", "divided open circle followed by open circle on solid line", "1.0", "0.67 - 0.72"),
    ("a-S", "filled square on dashed line", "0 - 0.33", "0.77 - 0.85"),
    ("b-S", "filled diamond on dashed line", "0.33 - 0.48", "0.58 - 0.71"),
    ("c-S", "filled upright triangle on dashed line", "0.49 - 0.62", "0.56 - 0.65"),
    ("d-S", "filled circle on dashed line", "0", "0.82 - 0.86"),
)
TABLE5_ROWS = (
    ("D-S45", "-12.03**", "-12.03", "7.9", "23.2", "0.6", "39.0", "11.4", None),
    ("46", "-11.75*", "-11.75", "12.3", "23.1", "0.7", "30.6", "16.7", "4.8"),
    ("47", "-11.69*", "-11.69", "13.1", "23.0", "0.7", "30.6", "15.7", "3.1"),
    ("48", "-11.75*", "-11.75", "14.3", "23.5", "0.9", "29.1", "15.6", "2.5"),
    ("49", "-11.31*", "-11.31", "16.3", "21.3", "0.7", "27.1", "16.4", "1.7"),
    ("50", "-11.12*", "-11.12", "18.8", "20.7", "0.8", "25.2", "17.5", "1.5"),
)


def _document() -> dict:
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _table1_rows(document: dict) -> list[dict]:
    return sorted(
        document["species"]["SnO"]["observations"],
        key=lambda row: row["table_row_number"],
    )


def _assert_table1_render_cells(document: dict) -> None:
    rows = _table1_rows(document)
    assert len(rows) == 14
    assert [row["table_row_number"] for row in rows] == list(range(3, 17))
    formulas = ("SiO2", "CaO", "FeOx", "Al2O3", "SnO")
    for row, (printed, low, high, temperature, composition) in zip(rows, TABLE1, strict=True):
        values = row["values"]
        assert values["gamma_as_published"] == printed
        if "range" in values:
            assert tuple(str(value) for value in values["range"]) == (low, high)
        else:
            assert str(values["gamma"]) == printed
        assert str(values["temperature_K_as_published"]) == temperature
        assert tuple(values["composition_as_published"][name] for name in formulas) == composition
        assert row["type"] == "activity_coefficient"
        assert row["phase"] == "l"
        assert row["locator"]["page"] == 2 and row["locator"]["table"] == "1"
        standard = row["standard_state"]
        assert standard["state"]["value"]["endmember"]["formula"] == "SnO"
        assert standard["state"]["value"]["endmember"]["phase"]["value"] == "l"
        assert standard["locator"]["page"] == 3 and standard["locator"]["equation"] == "5"
        assert "table" not in standard["locator"]
        assert row["point_conditions"]["temperature_K"]["state"]["tag"] in {"value", "unknown"}
        assert row["point_conditions"]["composition"]["state"]["tag"] == "unknown"
        assert row["point_conditions"]["fO2_log"]["state"]["tag"] == "unknown"
        pressure = row["point_conditions"]["total_pressure_Pa"]
        assert pressure["state"] == {"tag": "unknown", "reason": "not_published"}
        assert pressure["locator"]["page"] == 2 and pressure["locator"]["table"] == "1"
        assert row["values"]["calibration_window"]["state"] == {
            "tag": "unknown",
            "reason": "not_published",
        }
        assert row["uncertainty"]["confidence_level"] == "not_stated"
        assert "uncertainty" not in row["values"]
    assert "T_range_K" not in rows[11]
    assert rows[11]["point_conditions"]["temperature_K"]["state"]["tag"] == "unknown"
    assert "composition_sum_as_printed" not in rows[9]["values"]


def _assert_figure4_marker_legend(document: dict) -> None:
    records = {
        row["values"]["slag_set"]: row
        for row in document["species"]["SnO"]["context"]
        if row["observation_id"].startswith("takeda_1990_fig4_legend_")
    }
    assert len(records) == 9
    for slag_set, marker, ca_mgo, fe_on in FIGURE4:
        values = records[slag_set]["values"]
        assert values["legend_marker_as_rendered_at_600_dpi"] == marker
        assert values["n_CaO_plus_MgO_over_total_as_printed"] == ca_mgo
        assert values["n_FeOn_over_total_as_printed"] == fe_on
        assert records[slag_set]["locator"]["page"] == 6
        assert records[slag_set]["locator"]["figure"] == "4"


def _assert_table5_render_cells(document: dict) -> None:
    context = document["species"]["SnO"]["context"]
    rows = {
        row["values"]["test_no"]: row
        for row in context
        if row["observation_id"].startswith("takeda_1990_t5_row_")
    }
    assert set(rows) == {row[0] for row in TABLE5_ROWS}
    for (test_no, _oxygen_cell, oxygen, sn, cao, mgo, fe2, fe3, metal_fe) in TABLE5_ROWS:
        row = rows[test_no]
        values = row["values"]
        assert values["temperature_K"] == 1473
        assert values["log_pO2"] == float(oxygen)
        for field, expected in (
            ("slag_Sn_mass_percent", sn),
            ("slag_CaO_mass_percent", cao),
            ("slag_MgO_mass_percent", mgo),
            ("slag_Fe2plus_mass_percent", fe2),
            ("slag_Fe3plus_mass_percent", fe3),
        ):
            assert values[field] == float(expected)
        assert values["metal_Sn_mass_percent"] is None
        assert values["metal_Fe_mass_percent"] == (None if metal_fe is None else float(metal_fe))
        assert row["locator"]["page"] == 5
        assert row["locator"]["pdf_page_index"] == 4
        assert row["locator"]["published_page"] == 797
        assert row["locator"]["table"] == "5"


def _assert_table5_cell_audit(cells: list[dict[str, str]]) -> None:
    rows = [cell for cell in cells if cell["table"] == "5"]
    expected = []
    columns = (
        (2, "log_pO2", "values.log_pO2"),
        (3, "slag_Sn_mass_percent", "values.slag_Sn_mass_percent"),
        (4, "slag_CaO_mass_percent", "values.slag_CaO_mass_percent"),
        (5, "slag_MgO_mass_percent", "values.slag_MgO_mass_percent"),
        (6, "slag_Fe2plus_mass_percent", "values.slag_Fe2plus_mass_percent"),
        (7, "slag_Fe3plus_mass_percent", "values.slag_Fe3plus_mass_percent"),
        (9, "metal_Fe_mass_percent", "values.metal_Fe_mass_percent"),
    )
    for test_no, oxygen_cell, oxygen, sn, cao, mgo, fe2, fe3, metal_fe in TABLE5_ROWS:
        values = (
            (oxygen_cell, oxygen),
            (sn, sn),
            (cao, cao),
            (mgo, mgo),
            (fe2, fe2),
            (fe3, fe3),
            (metal_fe, metal_fe),
        )
        for (column, header, field), (cell_text, stored_value) in zip(columns, values, strict=True):
            if stored_value is not None:
                expected.append((test_no, str(column), header, cell_text, field, stored_value))
    actual = [
        (
            cell["printed_data_row"],
            cell["table_column"],
            cell["column_header"],
            cell["mineru_cell_text"],
            cell["extract_field"],
            cell["stored_value"],
        )
        for cell in rows
    ]
    assert actual == expected
    assert len(rows) == 41

    caption = [cell for cell in cells if cell["table"] == "Table 5 caption"]
    assert len(caption) == 1
    assert caption[0]["physical_pdf_page"] == "5"
    assert caption[0]["pdf_page_index"] == "4"
    assert caption[0]["published_page"] == "797"
    assert caption[0]["mineru_cell_text"] == "1473 K"
    assert caption[0]["extract_field"] == "takeda_1990_magnesia_1473_runs.conditions.temperature_K"
    assert caption[0]["stored_value"] == '"1473"'


def _source_pages() -> dict[int, str]:
    text_path = (
        regolith_corpus_root()
        / "text/takeda-1990-sn-slag-matertrans/tesseract-layout.txt"
    )
    text = text_path.read_text(encoding="utf-8")
    return {
        int(page): body
        for page, body in re.findall(
            r"=== PDF page (\d+) ===(.*?)(?=\f?=== PDF page |\Z)", text, re.S
        )
    }


def test_takeda_1990_render_cells_and_page_proofs() -> None:
    document = _document()
    _assert_table1_render_cells(document)
    _assert_figure4_marker_legend(document)
    _assert_table5_render_cells(document)
    context = document["species"]["SnO"]["context"]
    assert {
        table: sum(row["observation_id"].startswith(f"takeda_1990_t{table}_row_") for row in context)
        for table in (2, 3, 4, 5, 6)
    } == {2: 5, 3: 11, 4: 29, 5: 6, 6: 81}
    context_by_id = {row["observation_id"]: row for row in context}
    t2 = context_by_id["takeda_1990_t2_row_01"]["values"]
    assert t2["temperature_K"] == 1366
    assert t2["EMF_mV"] == -147.7 and t2["EMF_uncertainty_mV"] == 1.5
    t3 = context_by_id["takeda_1990_t3_row_a_5"]["values"]
    assert t3["slag_Sn_mass_percent"] == 2.0 and t3["log_pO2"] == -11.4
    t6 = context_by_id["takeda_1990_t6_row_a_s51"]["values"]
    assert t6["slag_Sn_mass_percent"] == 2.4 and t6["log_pO2"] == -11.3
    cited_result = context_by_id["takeda_1990_table3_wustite_comparison"]["values"]
    assert cited_result["coexisting_metal_Sn_activity"] == 0.8
    assert cited_result["coexisting_metal_phases_at_1573K"] == [
        {"Sn_mass_percent": 80, "Fe_mass_percent": 20},
        {"Sn_mass_percent": 53, "Fe_mass_percent": 47},
    ]
    pages = _source_pages()
    assert "Table 1 Reported values of the activity coefficient" in pages[2]
    assert "1.4 to 0.9" in pages[2]
    assert "activities of unity for SnO() and Sn(1)" in pages[3]
    assert "Table 2 E.m.f. of the oxygen concentration cell" in pages[3]
    assert "A5 1573, —11.40 2.0" in pages[4]
    assert re.search(r"tivity of Sn is 0\.8 at this miscibility gap", pages[4])
    assert "47 mass% Fe metal phases coexist at 1573 K" in pages[4]
    assert "in magnesia crucibles at 1473 K" in pages[5]
    assert "A-S51 —11.30** 2.4" in pages[5]
    assert "Fig. 4 Effect of oxygen partial pressure on tin content" in pages[6]
    assert "Fig. 6 Activity coefficient of SnO" in pages[7]
    assert "(12) S. Nunoue and E. Kato: Tetu to Hagané, 73 (1987), 868." in pages[8]
    sn_activity = document["species"]["Sn"]["observations"][0]
    assert sn_activity["locator"]["page"] == 4
    assert sn_activity["locator"]["section"] == "Results and Discussions"
    assert "table" not in sn_activity["locator"]
    assert sn_activity["experiment"] == "takeda_1990_quoted_nunoue_kato_miscibility_gap"
    assert sn_activity["type"] == "activity_coefficient"
    assert sn_activity["values"]["quantity"] == "activity"
    assert sn_activity["values"]["activity"] == "0.8"
    assert sn_activity["values"]["coexisting_metal_phases_as_printed"] == [
        "80 mass% Sn-20 mass% Fe",
        "53 mass% Sn-47 mass% Fe",
    ]


def test_takeda_1990_render_pin_rejects_interior_mutation() -> None:
    mutated = deepcopy(_document())
    _table1_rows(mutated)[6]["values"]["gamma_as_published"] = "1.1 to 8.8"
    with pytest.raises(AssertionError):
        _assert_table1_render_cells(mutated)


def test_takeda_1990_table5_audit_pin_rejects_interior_mutation() -> None:
    with CELL_AUDIT.open(newline="", encoding="utf-8") as source:
        cells = list(csv.DictReader(source))
    _assert_table5_cell_audit(cells)
    mutated = deepcopy(cells)
    mg_cell = next(
        cell
        for cell in mutated
        if cell["table"] == "5"
        and cell["printed_data_row"] == "D-S45"
        and cell["table_column"] == "5"
    )
    mg_cell["stored_value"] = "0.8"
    with pytest.raises(AssertionError):
        _assert_table5_cell_audit(mutated)


def test_takeda_1990_isolated_migration_preserves_quantities_conditions_and_uncertainty() -> None:
    document = _document()
    _assert_table1_render_cells(document)
    _assert_figure4_marker_legend(document)
    _assert_table5_render_cells(document)
    migrator = migrate_one_extract(ROOT, EXTRACT, index={}, aliases={})
    migrator.finalize()
    result = migrator.result
    assert result.validation.hard_issues == ()
    assert len(result.observations) == 15
    assert sum(row.value.kind.value == "unavailable" for row in result.observations.values()) == 0

    migrated = {
        raw["observation_id"]: result.observations[f"{EXTRACT.stem}::{raw['observation_id']}"]
        for species in document["species"].values()
        for raw in species["observations"]
    }
    assert len(migrated) == 15
    assert Counter(row.value.kind.value for row in migrated.values()) == {
        "interval": 11,
        "point": 4,
    }
    assert Counter(row.uncertainty.kind.value for row in migrated.values()) == {"printed": 15}
    assert Counter(row.uncertainty.verbatim["confidence_level"] for row in migrated.values()) == {
        "not_stated": 15
    }

    gamma_rows = [migrated[row["observation_id"]] for row in _table1_rows(document)]
    assert [row.identity.quantity.value.value for row in gamma_rows] == [
        "activity_coefficient"
    ] * 14
    assert all(row.identity.species.phase.is_value for row in gamma_rows)
    assert all(row.identity.species.phase.value.value == "l" for row in gamma_rows)
    assert [row.value.interval_low for row in gamma_rows][4] == Decimal("0.9")
    assert [row.value.interval_high for row in gamma_rows][4] == Decimal("1.4")
    for raw, row in zip(_table1_rows(document), gamma_rows, strict=True):
        assert row.locator.page == 2 and row.locator.table == "1"
        assert row.identity.reference_state.is_value
        assert row.identity.reference_state.value.endmember.formula == "SnO"
        assert row.identity.temperature_K.is_value == (raw["table_row_number"] != 14)
        assert row.identity.composition.is_unknown
        assert row.identity.fO2_Pa.is_unknown
        assert row.identity.total_pressure_Pa.is_unknown
        assert row.point_conditions["temperature_K"].locator.page == 2
        assert row.point_conditions["composition"].state.is_unknown
        assert row.point_conditions["fO2_log"].state.is_unknown

    sn_raw = document["species"]["Sn"]["observations"][0]
    sn = migrated[sn_raw["observation_id"]]
    assert sn.value.kind.value == "point" and sn.value.point == Decimal("0.8")
    assert sn.identity.quantity.value.value == "activity"
    assert sn.identity.species.formula == "Sn"
    assert sn.identity.species.phase.value.value == "l"
    assert sn.identity.reference_state.is_value
    assert sn.identity.reference_state.value.endmember.formula == "Sn"
    assert sn.identity.temperature_K.value == Decimal("1573")
    assert sn.identity.composition.is_unknown and sn.identity.fO2_Pa.is_unknown
    assert sn.identity.total_pressure_Pa.is_unknown
    assert sn.locator.page == 4 and sn.locator.table is None

    axes = (
        "composition",
        "total_pressure_Pa",
        "fO2_Pa",
        "temperature_K",
    )
    for observation_id in (
        "takeda_1990_table1_gamma_01",
        "takeda_1990_table1_gamma_12",
        "takeda_1990_sn_activity_miscibility_gap",
    ):
        identity = migrated[observation_id].identity
        print("Takeda identity axes", observation_id, {name: getattr(identity, name).tag.value for name in axes})


def test_takeda_1990_full_cell_audit_rows_are_pinned() -> None:
    with CELL_AUDIT.open(newline="", encoding="utf-8") as source:
        cells = list(csv.DictReader(source))
    _assert_table5_cell_audit(cells)
    assert len(cells) == 1126
    assert Counter(cell["table"] for cell in cells) == {
        "1": 79,
        "2": 14,
        "3": 99,
        "4": 203,
        "5": 41,
        "6": 653,
        "Fig. 4 legend": 18,
        "Table 4 caption": 4,
        "Table 5 caption": 1,
        "Table 3 discussion": 10,
        "Table 5 footnote": 2,
        "Table 4 discussion": 1,
        "Prose: Nunoue and Kato": 1,
    }
    assert {cell["verification"] for cell in cells} == {"page render at 600 dpi"}
