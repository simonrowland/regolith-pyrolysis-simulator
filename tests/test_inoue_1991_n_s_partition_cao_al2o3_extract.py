"""Inoue et al. wide-grid binding and isolated migration regression.

RENDER_GRID contains the table cells transcribed independently from the 600-dpi
physical PDF renders (pages 2-3). The extract is compared cell-by-cell so a
wrong row label, column, sample block, or condition cannot pass as a valid row.
"""
from __future__ import annotations

import re
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from simulator.battery.enums import Phase, ValueKind
from simulator.battery.migrate import Migrator
from tests.literature_extract_test_helpers import corpus_root

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / "data/literature/extracts/inoue-1991-n-s-partition-cao-al2o3.yaml"
CORPUS_ROOT = corpus_root()
PDF = CORPUS_ROOT / "raw/inoue-1991-n-s-partition-cao-al2o3/inoue-1991-n-s-partition-cao-al2o3.pdf"

# (sample, equilibration time in min, printed n, {table column number: cell})
RENDER_GRID = {'1': (('A-001',
        '180',
        '6',
        {3: '4.74',
         4: '4.5',
         6: '15.0',
         7: '11.2',
         8: '13.1',
         9: '3.9',
         10: '3.3',
         11: '27.9',
         12: '31.9',
         13: '12.6'}),
       ('A-002',
        '215',
        '9',
        {3: '9.30',
         4: '4.0',
         6: '20.5',
         7: '12.0',
         8: '15.3',
         9: '9.4',
         10: '3.4',
         11: '27.9',
         12: '32.2',
         13: '10.3'}),
       ('A-003',
        '156',
        '6',
        {3: '14.6',
         4: '3.7',
         6: '25.7',
         7: '14.0',
         8: '20.7',
         9: '2.5',
         10: '5.7',
         11: '28.4',
         12: '31.9',
         13: '154'}),
       ('A-004',
        '208',
        '5',
        {3: '0.0040',
         4: '22.5',
         6: '136.5',
         7: '127.3',
         8: '130.0',
         9: '19.2',
         10: '0.21',
         11: '27.5',
         12: '32.7',
         13: '5.7'}),
       ('A-005',
        '261',
        '8',
        {3: '0.0141',
         4: '21.5',
         6: '114.6',
         7: '103.8',
         8: '108.2',
         9: '15.0',
         10: '0.50',
         11: '27.7',
         12: '32.7',
         13: '6.1'}),
       ('A-006',
        '184',
        '10',
        {3: '0.0794',
         4: '4.3',
         6: '13.4',
         7: '6.4',
         8: '10.2',
         9: '221',
         10: '0.93',
         11: '28.7',
         12: '31.5',
         13: '387'}),
       ('A-007',
        '241',
        '4',
        {3: '0.0663',
         4: '4.5',
         6: '11.1',
         7: '8.4',
         8: '10.0',
         9: '128',
         10: '0.72',
         11: '29.0',
         12: '31.4',
         13: '242'}),
       ('A-008',
        '168',
        '9',
        {3: '0.0200',
         4: '13.3',
         6: '74.3',
         7: '65.2',
         8: '70.9',
         9: '16.1',
         10: '0.17',
         11: '28.2',
         12: '31.9',
         13: '6.7'}),
       ('A-009',
        '241',
        '4',
        {3: '0.139',
         4: '5.1',
         6: '10.6',
         7: '8.5',
         8: '9.4',
         9: '21.0',
         10: '0.71',
         11: '29.1',
         12: '31.5',
         13: '60.0'}),
       ('A-010',
        '240',
        '9',
        {3: '2.28',
         4: '4.9',
         6: '11.2',
         7: '5.5',
         8: '7.8',
         9: '116',
         10: '2.9',
         11: '28.9',
         12: '31.7',
         13: '6220'}),
       ('A-011',
        '240',
        '5',
        {3: '9.59',
         4: '1.7',
         6: '20.3',
         7: '13.8',
         8: '17.3',
         9: '2.2',
         10: '6.4',
         11: '28.1',
         12: '32.5',
         13: '49.8'}),
       ('A-012',
        '240',
        '5',
        {3: '0.00036',
         4: '0.4',
         6: '140.7',
         7: '121.0',
         8: '128.0',
         9: '34.9',
         10: '3.3',
         11: '27.4',
         12: '31.9',
         13: '3.1'}),
       ('A-013',
        '240',
        '4',
        {3: '0.00052',
         4: '0.1',
         6: '114.6',
         7: '106.7',
         8: '108.8',
         9: '32.6',
         10: '1.5',
         11: '27.6',
         12: '32.0',
         13: '5.5'}),
       ('A-014',
        '239',
        '4',
        {3: '0.705',
         4: '2.2',
         6: '8.9',
         7: '5.3',
         8: '6.9',
         9: '32.4',
         10: '2.3',
         11: '28.3',
         12: '31.3',
         13: '424'}),
       ('C-001',
        '61',
        '9',
        {3: '4.97',
         4: '9.3',
         6: '19.8',
         7: '11.3',
         8: '15.1',
         9: '1.1',
         10: '8.0',
         11: '40.4',
         12: '23.2',
         13: '403'}),
       ('C-002',
        '60',
        '4',
        {3: '9.52',
         4: '4.9',
         6: '20.2',
         7: '16.3',
         8: '18.2',
         9: '4.0',
         10: '12.6',
         11: '40.5',
         12: '23.1',
         13: '955'}),
       ('C-003',
        '62',
        '4',
        {3: '14.8',
         4: '6.3',
         6: '16.5',
         7: '12.4',
         8: '14.6',
         9: '2.4',
         10: '17.4',
         11: '40.6',
         12: '22.4',
         13: '1900'}),
       ('C-004',
        '100',
        '6',
        {3: '0.677',
         4: '2.5',
         6: '13.1',
         7: '2.9',
         8: '6.9',
         9: '10.8',
         10: '2.4',
         11: '40.6',
         12: '22.7',
         13: '238'}),
       ('C-005',
        '99',
        '5',
        {3: '0.0710',
         4: '2.5',
         6: '5.0',
         7: '1.3',
         8: '2.5',
         9: '16.2',
         10: '1.2',
         11: '40.8',
         12: '22.5',
         13: '54.5'}),
       ('C-006',
        '98',
        '5',
        {3: '0.00013',
         4: '3.1',
         6: '88.3',
         7: '68.5',
         8: '75.7',
         9: '130',
         10: '0.91',
         11: '39.7',
         12: '23.1',
         13: '4.1'}),
       ('C-007',
        '100',
        '5',
        {3: '0.00011',
         4: '0.8',
         6: '62.4',
         7: '53.7',
         8: '57.1',
         9: '31.3',
         10: '1.4',
         11: '40.0',
         12: '22.4',
         13: '6.1'})),
 '2': (('A-201',
        '180',
        '8',
        {3: '4.83',
         4: '5.2',
         6: '24.3',
         7: '19.8',
         8: '22.0',
         9: '6.0',
         10: '3.3',
         11: '22.9',
         12: '36.1',
         13: '180'}),
       ('A-202',
        '180',
        '6',
        {3: '9.40',
         4: '5.8',
         6: '17.7',
         7: '14.1',
         8: '15.8',
         9: '4.2',
         10: '2.6',
         11: '22.7',
         12: '36.4',
         13: '327'}),
       ('A-203',
        '180',
        '4',
        {3: '14.8',
         4: '7.0',
         6: '24.9',
         7: '21.1',
         8: '22.6',
         9: '21.3',
         10: '8.0',
         11: '22.6',
         12: '35.5',
         13: '956'}),
       ('A-204',
        '178',
        '5',
        {3: '0.0449',
         4: '4.8',
         6: '19.5',
         7: '13.9',
         8: '16.9',
         9: '109',
         10: '0.59',
         11: '23.0',
         12: '35.8',
         13: '52.0'}),
       ('A-205',
        '176',
        '6',
        {3: '0.608',
         4: '7.6',
         6: '15.9',
         7: '10.9',
         8: '13.3',
         9: '49.4',
         10: '2.6',
         11: '22.9',
         12: '35.7',
         13: '393'}),
       ('A-206',
        '210',
        '6',
        {3: '9.35',
         4: '5.6',
         6: '20.0',
         7: '9.2',
         8: '12.1',
         9: '4.9',
         10: '8.0',
         11: '23.0',
         12: '35.8',
         13: '705'}),
       ('A-207',
        '210',
        '6',
        {3: '0.0273',
         4: '5.2',
         6: '17.3',
         7: '6.3',
         8: '10.2',
         9: '117',
         10: '0.88',
         11: '23.3',
         12: '35.9',
         13: '34.0'}),
       ('A-208',
        '212',
        '4',
        {3: '0.00137',
         4: '2.3',
         6: '155.2',
         7: '150.2',
         8: '152.8',
         9: '150',
         10: '4.8',
         11: '22.9',
         12: '35.8',
         13: '5.0'}),
       ('A-209',
        '210',
        '4',
        {3: '0.00107',
         4: '0.5',
         6: '111.8',
         7: '104.2',
         8: '107.1',
         9: '88.5',
         10: '<0.1',
         11: '23.0',
         12: '35.6',
         13: '3.6'}),
       ('C-201',
        '60',
        '6',
        {3: '4.79',
         4: '11.0',
         6: '24.9',
         7: '18.8',
         8: '21.6',
         9: '4.7',
         10: '12.0',
         11: '42.0',
         12: '22.1',
         13: '447'}),
       ('C-202',
        '61',
        '5',
        {3: '9.52',
         4: '8.8',
         6: '25.9',
         7: '21.2',
         8: '22.9',
         9: '5.6',
         10: '19.3',
         11: '42.7',
         12: '21.5',
         13: '1460'}),
       ('C-203',
        '50',
        '4',
        {3: '14.0',
         4: '13.8',
         6: '23.6',
         7: '19.5',
         8: '21.3',
         9: '4.0',
         10: '31.4',
         11: '42.0',
         12: '21.6',
         13: '2230'}),
       ('C-204',
        '60',
        '4',
        {3: '0.674',
         4: '2.4',
         6: '16.5',
         7: '8.1',
         8: '12.5',
         9: '16.9',
         10: '9.0',
         11: '42.5',
         12: '21.4',
         13: '288'}),
       ('C-205',
        '71',
        '3',
        {3: '0.0019',
         4: '0.7',
         6: '22.9',
         7: '19.3',
         8: '21.3',
         9: '46.0',
         10: '1.4',
         11: '42.6',
         12: '21.4',
         13: '7.0'}),
       ('C-206',
        '70',
        '5',
        {3: '0.00013',
         4: '1.8',
         6: '99.4',
         7: '86.8',
         8: '92.7',
         9: '142',
         10: '9.8',
         11: '42.2',
         12: '21.2',
         13: '3.2'}),
       ('C-207',
        '70',
        '5',
        {3: '0.00014',
         4: '1.9',
         6: '89.8',
         7: '71.9',
         8: '78.8',
         9: '26.5',
         10: '3.4',
         11: '42.0',
         12: '21.9',
         13: '3.7'})),
 '3': (('A-118S',
        '210',
        '3',
        {3: '0.00045',
         4: '0.7',
         6: '138.0',
         7: '129.5',
         8: '133.1',
         9: '358',
         10: '<0.1',
         11: '25.3',
         12: '33.6',
         13: '330'}),
       ('A-119S',
        '209',
        '3',
        {3: '0.00076',
         4: '0.4',
         6: '108.2',
         7: '96.3',
         8: '100.5',
         9: '306',
         10: '<0.1',
         11: '26.1',
         12: '34.0',
         13: '470'}),
       ('A-120S',
        '210',
        '7',
        {3: '0.921',
         4: '2.1',
         6: '14.2',
         7: '2.7',
         8: '8.9',
         9: '9.33',
         10: '0.38',
         11: '25.3',
         12: '33.8',
         13: '1520'}),
       ('C-113S',
        '99',
        '5',
        {3: '0.00002',
         4: '1.1',
         6: '103.8',
         7: '90.7',
         8: '97.3',
         9: '119',
         10: '<0.1',
         11: '40.5',
         12: '22.7',
         13: '1200'}),
       ('C-114S',
        '100',
        '4',
        {3: '0.0012',
         4: '1.2',
         6: '19.0',
         7: '14.6',
         8: '17.1',
         9: '12.4',
         10: '1.1',
         11: '40.6',
         12: '23.1',
         13: '1650'}),
       ('C-115S',
        '100',
        '5',
        {3: '0.210',
         4: '<0.1',
         6: '8.7',
         7: '2.4',
         8: '6.1',
         9: '0.30',
         10: '1.7',
         11: '40.8',
         12: '22.8',
         13: '1580'}),
       ('C-116S',
        '101',
        '5',
        {3: '0.769',
         4: '1.3',
         6: '16.4',
         7: '11.0',
         8: '13.6',
         9: '0.12',
         10: '7.1',
         11: '40.8',
         12: '22.1',
         13: '1600'}))}

COLUMNS = {
    3: ("Al", "Metal Al soluble (mass%)", "mass% Al soluble in metal"),
    4: ("Al", "Metal Al insoluble (mass ppm)", "mass ppm Al insoluble in metal"),
    6: ("O", "Metal total O maximum (mass ppm)", "mass ppm total O, maximum, in metal"),
    7: ("O", "Metal total O minimum (mass ppm)", "mass ppm total O, minimum, in metal"),
    8: ("O", "Metal total O average (mass ppm)", "mass ppm total O, average, in metal"),
    9: (None, "Metal N/S (mass ppm)", None),
    10: ("Ca", "Metal Ca (mass ppm)", "mass ppm Ca in metal"),
    11: ("Ca", "Slag Ca (mass%)", "mass% Ca in slag"),
    12: ("Al", "Slag Al (mass%)", "mass% Al in slag"),
    13: (None, "Slag N/S (mass ppm)", None),
}
TABLE_META = {
    "1": ("1823", 2, 1, 1390, "N"),
    "2": ("1923", 3, 2, 1391, "N"),
    "3": ("1873", 3, 2, 1391, "S"),
}
EXPECTED_CONTEXT = {
    "N": {"inoue_1991_n_capacity_and_partition_schema_gap",
          "inoue_1991_printed_cao_al2o3_molar_ratios",
          "inoue_1991_model_derived_aln_solubility"},
    "S": {"inoue_1991_s_capacity_and_partition_schema_gap"},
}


def _load_extract():
    return yaml.safe_load(EXTRACT.read_text(encoding="utf-8"))


def _assert_render_grid(doc):
    raw_rows = {
        row["observation_id"]: (formula, row)
        for formula, block in doc["species"].items()
        for row in block.get("observations", [])
    }
    assert len(raw_rows) == 446
    expected_cell_count = 0
    for table_no, expected_rows in RENDER_GRID.items():
        temperature, page, page_index, folio, component = TABLE_META[table_no]
        actual_rows = [
            row for _formula, row in raw_rows.values()
            if row["locator"].get("table") == table_no
            and row.get("type") == "equilibrium_phase_composition"
        ]
        assert len(actual_rows) == len(expected_rows) * 10
        by_cell = {}
        for row in actual_rows:
            sample = row["sample_no"]
            col_no = int(row["observation_id"].rsplit("_c", 1)[1])
            assert col_no in COLUMNS
            formula, column_label, basis = COLUMNS[col_no]
            if formula is None:
                formula = component
                basis = f"mass ppm {component} in {'metal' if col_no == 9 else 'slag'}"
            assert row["locator"]["page"] == page
            assert row["locator"]["pdf_page_index"] == page_index
            assert row["locator"]["published_page"] == folio
            assert row["locator"]["note"] == f"Table {table_no}, printed sample {sample}, {column_label}"
            assert row["values"]["basis_as_published"] == basis
            assert row["values"]["T_K"] == temperature
            assert row["sample_block_as_printed"] == ("Al2O3 crucible" if sample.startswith("A-") else "CaO crucible")
            assert row["assemblage_id"] == f"inoue_1991_table_{table_no}_{sample}"
            assert row["assemblage_as_printed"] == (
                f"CaO-Al2O3 slag and liquid iron; Table {table_no}, "
                f"{row['sample_block_as_printed']}; {temperature} K"
            )
            assert row["phase_as_printed"] == ("metal" if col_no in {3, 4, 6, 7, 8, 9, 10} else "slag")
            cell_key = (sample, col_no)
            assert cell_key not in by_cell
            by_cell[cell_key] = row
        assert len(by_cell) == len(expected_rows) * 10

        for sample, time_min, replicate_count, expected_values in expected_rows:
            sample_cells = [
                row for (row_sample, _column), row in by_cell.items()
                if row_sample == sample
            ]
            assert len(sample_cells) == 10
            block = "Al2O3 crucible" if sample.startswith("A-") else "CaO crucible"
            for col_no, expected in expected_values.items():
                row = by_cell[(sample, col_no)]
                values = row["values"]
                if expected.startswith("<"):
                    assert "value" not in values
                    assert values["value_as_printed"] == expected
                    assert values["admission_status"] == "typed_unavailable"
                    assert "strict upper limit" in values["reason"]
                else:
                    assert values["value"] == expected
                expected_cell_count += 1
                conditions = row["point_conditions"]
                assert conditions["temperature_K"]["state"]["value"]["point"] == temperature
                assert conditions["temperature_K"]["locator"]["page"] == page
                assert conditions["fO2_control"]["state"]["value"] == "Al/(Al2O3) equilibrium"
                assert conditions["total_pressure_Pa"]["state"]["tag"] == "unknown"
                assert conditions["composition"]["state"]["tag"] == "unknown"
                assert conditions["quench_state"]["state"]["value"] == "not_stated"
                assert conditions["equilibration_time_min"]["state"]["value"] == time_min
                assert conditions["crucible"]["state"]["value"] == block
        context = doc["species"][component]["context"]
        count_row = next(
            row for row in context
            if row["observation_id"] == f"inoue_1991_table_{table_no}_replicate_counts"
        )
        expected_counts = [
            {"sample_no": sample, "time_min_as_printed": time_min, "n_as_printed": n}
            for sample, time_min, n, _values in expected_rows
        ]
        assert count_row["values"]["rows_as_printed"] == expected_counts
    assert expected_cell_count == 440


def test_every_rendered_cell_is_bound_to_its_sample_and_conditions():
    _assert_render_grid(_load_extract())


def test_table3_oxygen_control_locators_point_to_section_34():
    doc = _load_extract()
    controls = [
        row["point_conditions"]["fO2_control"]["locator"]
        for block in doc["species"].values()
        for row in block.get("observations", [])
        if row.get("locator", {}).get("table") == "3"
    ]
    assert len(controls) == 70
    assert all(
        (locator["page"], locator["pdf_page_index"], locator["published_page"], locator["section"])
        == (6, 5, 1394, "3.4")
        for locator in controls
    )


def test_quoted_alumina_activity_values_are_independently_pinned():
    expected = {
        "inoue_1991_quoted_al2o3_activity_al2o3_1823": ("0.25", "1823", "Al2O3 crucible"),
        "inoue_1991_quoted_al2o3_activity_al2o3_1873": ("0.33", "1873", "Al2O3 crucible"),
        "inoue_1991_quoted_al2o3_activity_al2o3_1923": ("0.42", "1923", "Al2O3 crucible"),
        "inoue_1991_quoted_al2o3_activity_cao_1823": ("0.0063", "1823", "CaO crucible"),
        "inoue_1991_quoted_al2o3_activity_cao_1873": ("0.0048", "1873", "CaO crucible"),
        "inoue_1991_quoted_al2o3_activity_cao_1923": ("0.0042", "1923", "CaO crucible"),
    }
    rows = {
        row["observation_id"]: row
        for row in _load_extract()["species"]["Al2O3"]["observations"]
    }
    assert set(rows) == set(expected)
    for observation_id, (activity, temperature, crucible) in expected.items():
        row = rows[observation_id]
        assert row["values"]["activity"] == activity
        assert row["values"]["formula"] == "Al2O3"
        assert row["locator"]["section"] == "3.2"
        assert row["T_range_K"] == [int(temperature), int(temperature)]
        assert row["sample_block_as_printed"] == crucible


def test_isolated_migration_preserves_species_temperature_conditions_and_bounds():
    doc = _load_extract()
    migrator = Migrator(root=ROOT, index={}, aliases={})
    migrator._migrate_extract(EXTRACT)
    migrator.finalize()
    result = migrator.result
    assert result.validation is not None
    assert result.validation.hard_issues == ()
    migrated = {
        row.observation_id.rsplit("::", 1)[-1]: row
        for row in result.observations.values()
    }
    assert len(migrated) == 446
    for formula, block in doc["species"].items():
        for raw in block.get("observations", []):
            row = migrated[raw["observation_id"]]
            assert row.identity.quantity.is_value
            assert row.identity.species.formula == formula
            if raw["type"] == "equilibrium_phase_composition":
                temperature = Decimal(raw["values"]["T_K"])
                assert row.identity.temperature_K.is_value
                assert row.identity.temperature_K.value == temperature
                assert row.identity.species.phase.is_value
                assert row.identity.species.phase.value is Phase.L
                conditions = row.point_conditions
                assert conditions is not None
                assert conditions["temperature_K"].state.value.point == temperature
                assert conditions["fO2_control"].state.value == "Al/(Al2O3) equilibrium"
                assert conditions["equilibration_time_min"].state.value == Decimal(
                    raw["point_conditions"]["equilibration_time_min"]["state"]["value"]
                )
                assert conditions["crucible"].state.value == raw["sample_block_as_printed"]
                assert conditions["composition"].state.is_unknown
                assert conditions["quench_state"].state.is_value
                assert str(conditions["quench_state"].state.value) == "not_stated"
                if raw["values"].get("admission_status") == "typed_unavailable":
                    assert row.value.kind is ValueKind.UNAVAILABLE
                else:
                    assert row.value.kind is ValueKind.POINT
    assert sum(row.value.kind is ValueKind.UNAVAILABLE for row in migrated.values()) == 5
    assert sum(row.value.kind is ValueKind.POINT for row in migrated.values()) == 441
    for formula, context_ids in EXPECTED_CONTEXT.items():
        found = {row["observation_id"] for row in doc["species"][formula]["context"]}
        assert context_ids <= found


def _pdf_page_text(page: int) -> str:
    if not PDF.is_file():
        pytest.skip(f"source PDF not available: {PDF}")
    return subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(PDF), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_physical_pdf_pages_support_table_and_figure_locators():
    table_labels = {1: (2, "Table 1"), 2: (3, "Table 2"), 3: (3, "Table 3")}
    for _table, (page, label) in table_labels.items():
        text = _pdf_page_text(page)
        if _table == 1:
            assert "Table" in text and ("Table 1" in text or "Table l" in text)
        else:
            assert label in text
    for figure, page in ((1, 4), (2, 4), (3, 4), (4, 5), (5, 5), (6, 6), (7, 6), (8, 7)):
        text = _pdf_page_text(page)
        assert "Fig." in text
        label = r"Fig\.\s*[l1I]\b" if figure == 1 else rf"Fig\.\s*{figure}\b"
        assert re.search(label, text)
    p4 = _pdf_page_text(4)
    assert all(value in p4 for value in ("0.25", "0.33", "0.42", "0.0063", "0.0048", "0.0042"))
    p6 = _pdf_page_text(6)
    assert re.search(r"3\.4\.\s+Relationship between Sulfur Distribution", p6)
    assert "controlled by the Al/" in p6 and "equilibrium" in p6
