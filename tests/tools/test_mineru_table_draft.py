from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

import pytest
import yaml

from tools import mineru_table_draft as draft


FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "berry-2018-morb-iron-redox"
    / "ocr-mineru"
    / "p001-p010"
    / "content_list.json"
)


def test_berry_table_rowspan_and_colspan_expansion() -> None:
    item = json.loads(FIXTURE.read_text(encoding="utf-8"))[0]
    grid = draft.parse_table_html(item["table_body"])
    header_count = draft._header_row_count(grid)
    numeric_columns = draft._numeric_columns(grid, header_count)
    draft._add_cell_flags(grid, header_count, numeric_columns)

    assert grid[2][0].text == "4.8"
    assert (grid[2][0].origin_row, grid[2][0].origin_column) == (1, 0)
    assert grid[2][0].merged
    assert "merged_cell" in grid[2][0].flags
    assert "merged_cell" not in grid[1][3].flags

    colspan = draft.parse_table_html(
        "<table><tr><th>A</th><th colspan='2'>B</th></tr>"
        "<tr><td rowspan='2'>1</td><td colspan='2'>2.3</td></tr>"
        "<tr><td>4.5</td></tr></table>"
    )
    assert [[cell.text for cell in row] for row in colspan] == [
        ["A", "B", "B"],
        ["1", "2.3", "2.3"],
        ["1", "4.5", ""],
    ]
    assert (colspan[1][2].origin_row, colspan[1][2].origin_column) == (1, 1)
    assert colspan[1][2].merged


def test_absolute_page_mapping_across_chunks(tmp_path: Path) -> None:
    sid = "berry-2018-morb-iron-redox"
    source_root = tmp_path / "raw" / sid / "ocr-mineru"
    for chunk in ("p001-p010", "p011-p020"):
        target = source_root / chunk / "content_list.json"
        target.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURE, target)

    index_path = draft.convert_source(
        sid,
        tmp_path / "draft",
        corpus_root=tmp_path / "raw",
    )
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    assert [table["pdf_page"] for table in index["tables"]] == [4, 14]
    with (index_path.parent / index["tables"][0]["csv"]).open(
        encoding="utf-8", newline=""
    ) as stream:
        cells = list(csv.DictReader(stream))
    expanded = next(cell for cell in cells if cell["row"] == "3" and cell["column"] == "1")
    assert expanded["text"] == "4.8"
    assert (expanded["origin_row"], expanded["origin_column"]) == ("2", "1")


def test_each_mechanical_flag_fires_only_on_the_suspect_cell() -> None:
    grid = draft.parse_table_html(
        "<table><tr><th>Sample</th><th>negative values</th><th>Result</th></tr>"
        "<tr><td>A</td><td>−1.2</td><td>25,540</td></tr>"
        "<tr><td>B</td><td>1.2</td><td>1.23*</td></tr>"
        "<tr><td>C</td><td>-2.3</td><td>0.540</td></tr>"
        "<tr><td>D</td><td>-3.4</td><td>invalid</td></tr>"
        "<tr><td>E</td><td></td><td></td></tr>"
        "<tr><td>F</td><td>-5.0</td><td>2.34<sup>†</sup></td></tr>"
        "<tr><td>G</td><td>–6.7</td><td>1.0</td></tr>"
        "<tr><td>H</td><td>—8.9</td><td>1.0</td></tr></table>"
    )
    header_count = draft._header_row_count(grid)
    numeric_columns = draft._numeric_columns(grid, header_count)
    draft._add_cell_flags(grid, header_count, numeric_columns)

    assert "unicode_minus" in grid[1][1].flags
    assert "ambiguous_comma_separator" in grid[1][2].flags
    assert "comma_decimal_suspect" not in grid[1][2].flags
    assert "missing_minus_suspect" in grid[2][1].flags
    assert grid[2][2].value_text == "1.23"
    assert grid[2][2].mark == "*"
    assert "superscript_or_footnote_mark" in grid[2][2].flags
    assert "non_numeric_in_numeric_column" in grid[4][2].flags
    assert "empty_cell" in grid[5][1].flags
    assert "empty_cell" in grid[5][2].flags
    assert grid[6][2].text == "2.34†"
    assert grid[6][2].value_text == "2.34"
    assert grid[6][2].mark == "†"
    assert "superscript_or_footnote_mark" in grid[6][2].flags
    assert grid[7][1].numeric_value == -6.7
    assert "en_dash_minus" in grid[7][1].flags
    assert grid[8][1].numeric_value == -8.9
    assert "em_dash_minus" in grid[8][1].flags

    assert "unicode_minus" not in grid[3][2].flags
    assert "missing_minus_suspect" not in grid[3][1].flags
    assert "comma_decimal_suspect" not in grid[3][2].flags
    assert "superscript_or_footnote_mark" not in grid[3][2].flags
    assert not grid[3][2].flags


def test_comma_formats_distinguish_decimal_from_ambiguous_grouping() -> None:
    grid = draft.parse_table_html(
        "<table><tr><th>Value</th></tr><tr><td>123.456</td></tr>"
        "<tr><td>1.234,56</td></tr><tr><td>12,5</td></tr>"
        "<tr><td>3,14</td></tr><tr><td>25,540</td></tr>"
        "<tr><td>123,456</td></tr><tr><td>1,234</td></tr></table>"
    )
    draft._add_cell_flags(grid, draft._header_row_count(grid), {0})
    assert "comma_decimal_suspect" not in grid[1][0].flags
    assert "comma_decimal_suspect" in grid[2][0].flags
    assert "comma_decimal_suspect" in grid[3][0].flags
    assert "comma_decimal_suspect" in grid[4][0].flags
    for cell in grid[5:7]:
        assert "ambiguous_comma_separator" in cell[0].flags
        assert "comma_decimal_suspect" not in cell[0].flags
    assert "comma_decimal_suspect" not in grid[7][0].flags
    assert "ambiguous_comma_separator" not in grid[7][0].flags
    assert all(cell[0].numeric_value is None for cell in grid[2:8])


@pytest.mark.parametrize(
    ("header", "header_tag", "axis_row", "following_row", "expected"),
    [
        ("Composition, wt%", "th", "50.1 12.3 8.4", "SiO2 Al2O3 FeO", 1),
        ("Composition", "th", "25.4 31.2", "", 1),
        ("composition of sample C", "th", "50.1 12.3", "SiO2 Al2O3", 1),
        ("Temperature, K", "th", "1833 1883 1933", "Cr 1 2", 2),
        ("Pressure, bar", "th", "1 2 5", "FeO 10 11", 2),
        ("Temperature (°C)", "th", "1400 1500 1600", "Cr 1 2", 2),
        ("Temperature (°C)", "td", "1400 1500 1600", "Cr 1 2", 2),
        ("Temperature, °C", "th", "1400 1500", "SiO2 50.1", 2),
        ("Temperature °C", "th", "1400 1500", "SiO2 50.1", 2),
        ("Temperature (deg C)", "th", "1400 1500", "SiO2 1", 2),
        ("T°C", "th", "1400 1500", "SiO2 1", 1),
    ],
)
def test_axis_header_probe_table(
    header: str,
    header_tag: str,
    axis_row: str,
    following_row: str,
    expected: int,
) -> None:
    header_cell = f"<{header_tag}>{header}</{header_tag}>"
    rows = ["<tr>" + header_cell + "</tr>"]
    rows.append(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in axis_row.split()) + "</tr>"
    )
    if following_row:
        rows.append(
            "<tr>"
            + "".join(f"<td>{cell}</td>" for cell in following_row.split())
            + "</tr>"
        )
    grid = draft.parse_table_html("<table>" + "".join(rows) + "</table>")

    header_count = draft._header_row_count(grid)
    assert header_count == expected
    if expected == 1:
        draft._add_cell_flags(
            grid, header_count, draft._numeric_columns(grid, header_count)
        )
        assert [cell.numeric_value for cell in grid[1]] == [
            float(value) for value in axis_row.split()
        ]


def test_latex_numeric_cells_convert_and_compare(tmp_path: Path) -> None:
    html = ("<table><tr><th>Value</th></tr>"
            "<tr><td>$1.2 \\times 10^{-5}$</td></tr>"
            "<tr><td>$−3.4$</td></tr><tr><td>$\\mathrm{2.5}$</td></tr>"
            "<tr><td>$\\cdot$</td></tr></table>")
    grid = draft.parse_table_html(html)
    draft._add_cell_flags(grid, draft._header_row_count(grid), {0})
    assert [row[0].numeric_value for row in grid[1:]] == [1.2e-5, -3.4, 2.5, None]
    assert draft._number_value("$1.2 \\times 10^{-5}$") == 1.2e-5
    csv_path = tmp_path / "p001-t01.csv"
    draft._add_cell_flags(grid, draft._header_row_count(grid), {0})
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=draft._CSV_FIELDS)
        writer.writeheader()
        for row_number, cell in enumerate(grid, start=1):
            writer.writerow({
                "row": row_number, "column": 1,
                "row_type": "header" if row_number == 1 else "body",
                "text": cell[0].text, "value_text": cell[0].value_text,
                "mark": cell[0].mark, "numeric_column": 1,
                "origin_row": row_number, "origin_column": 1,
                "rowspan": 1, "colspan": 1, "flags": json.dumps(cell[0].flags),
            })
    index_path = tmp_path / "index.yaml"
    index_path.write_text(yaml.safe_dump({"tables": [{"table_key": "p001-t01", "pdf_page": 1, "csv": csv_path.name}]}))
    extract = {"species": {"Cr": {"observations": [{
        "observation_id": "latex", "locator": {"pdf_page": 1},
        "values": {"points": [{"value": 1.2e-5}, {"value": -3.4}, {"value": 2.5}]},
    }]}}}
    compared = draft.compare_extract_to_draft(extract, index_path)
    assert [row["status"] for row in compared] == ["presence-only"] * 3


def test_nazarov_table_2_rowspan_temperature_header_is_not_a_body_row(
    tmp_path: Path,
) -> None:
    html = (Path(__file__).parent / "fixtures" / "nazarov-2015-table-2.html").read_text(
        encoding="utf-8"
    )
    grid = draft.parse_table_html(html)
    header_count = draft._header_row_count(grid)
    table = draft._render_table(
        {"page_idx": 4, "table_body": html},
        table_number=2,
        source_file=Path("nazarov-2015-p001-p008_content_list.json"),
        chunk_start_page=1,
        table_dir=tmp_path,
    )

    assert header_count == 2
    assert table["body_rows"] == 7


def test_source_id_from_text_and_raw_mineru_layouts() -> None:
    assert draft._sid_from_path(Path("/corpus/text/source-a/mineru/chunk-1/content_list.json")) == "source-a"
    assert draft._sid_from_path(Path("/corpus/raw/source-b/ocr-mineru/chunk-1/content_list.json")) == "source-b"


def test_superscript_exponent_is_not_split_or_compared_as_its_mantissa(
    tmp_path: Path,
) -> None:
    grid = draft.parse_table_html(
        "<table><tr><th>Value</th></tr><tr><td>10<sup>-3</sup></td></tr>"
        "<tr><td>10<sup>3</sup></td></tr>"
        "<tr><td>10<sup>3</sup>*</td></tr><tr><td>10³</td></tr>"
        "<tr><td>10³*</td></tr></table>"
    )
    header_count = draft._header_row_count(grid)
    numeric_columns = draft._numeric_columns(grid, header_count)
    draft._add_cell_flags(grid, header_count, numeric_columns)

    cells = [row[0] for row in grid[1:]]
    assert [cell.value_text for cell in cells] == [
        "10^-3",
        "10^3",
        "10^3",
        "10^³",
        "10^³",
    ]
    assert [cell.mark for cell in cells] == ["", "", "*", "³", "*"]
    assert [cell.numeric_value for cell in cells] == [None] * 5
    assert all(
        "superscript_or_footnote_mark" in cell.flags for cell in cells[2:]
    )

    csv_path = tmp_path / "p001-t01.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=draft._CSV_FIELDS)
        writer.writeheader()
        for row_number, cell in enumerate(cells, start=2):
            writer.writerow(
                {
                    "row": row_number,
                    "column": 1,
                    "row_type": "body",
                    "text": cell.text,
                    "value_text": cell.value_text,
                    "mark": cell.mark,
                    "numeric_column": 0,
                    "origin_row": row_number,
                    "origin_column": 1,
                    "rowspan": 1,
                    "colspan": 1,
                    "flags": json.dumps(cell.flags),
                }
            )
    index_path = tmp_path / "index.yaml"
    index_path.write_text(
        yaml.safe_dump(
            {"tables": [{"table_key": "p001-t01", "pdf_page": 1, "csv": csv_path.name}]}
        ),
        encoding="utf-8",
    )
    extract = {
        "species": {
            "Cr": {
                "observations": [
                    {
                        "observation_id": "negative-exponent-is-not-mantissa",
                        "locator": {"pdf_page_index": 0},
                        "values": {"points": [{"value": 10.0}]},
                    },
                    {
                        "observation_id": "positive-exponent-is-not-mantissa",
                        "locator": {"pdf_page_index": 0},
                        "values": {"points": [{"value": 10.0}]},
                    },
                    {
                        "observation_id": (
                            "marked-exponent-is-not-a-concatenated-number"
                        ),
                        "locator": {"pdf_page_index": 0},
                        "values": {"points": [{"value": 103.0}]},
                    },
                    {
                        "observation_id": "plain-unicode-exponent-is-not-the-mantissa",
                        "locator": {"pdf_page_index": 0},
                        "values": {"points": [{"value": 10.0}]},
                    },
                    {
                        "observation_id": "marked-unicode-exponent-is-not-concatenated",
                        "locator": {"pdf_page_index": 0},
                        "values": {"points": [{"value": 103.0}]},
                    },
                ]
            }
        }
    }

    result = draft.compare_extract_to_draft(extract, index_path)

    assert [row["status"] for row in result] == ["no-match"] * 5


def test_barin_table_12_and_label_value_rows_are_comparable(tmp_path: Path) -> None:
    chunk_dir = tmp_path / "raw" / "barin-1995" / "ocr-mineru" / "p001-p040"
    chunk_dir.mkdir(parents=True)
    items = [
        {"type": "table", "page_idx": 0, "table_body": "<table></table>"}
        for _ in range(12)
    ]
    items.extend(
        [
            {
                "type": "table",
                "page_idx": 21,
                "table_body": (
                    "<table><tr><td>La</td><td>LANTHANUM</td><td>925</td></tr>"
                    "<tr><td>La2O3</td><td>LANTHANUM OXIDE</td>"
                    "<td>936</td></tr></table>"
                ),
            },
            {
                "type": "table",
                "page_idx": 22,
                "table_body": (
                    "<table><tr><td>Oxide</td><td>wt%</td></tr>"
                    "<tr><td>SiO2</td><td>49.2</td></tr>"
                    "<tr><td>Al2O3</td><td>15.1</td></tr></table>"
                ),
            },
        ]
    )
    (chunk_dir / "content_list.json").write_text(
        json.dumps(items), encoding="utf-8"
    )
    index_path = draft.convert_source(
        "barin-1995", tmp_path / "draft", corpus_root=tmp_path / "raw"
    )
    index = yaml.safe_load(index_path.read_text(encoding="utf-8"))

    assert index["tables"][12]["table_key"] == "p022-t13"
    assert index["tables"][12]["header_rows"] == []
    assert index["tables"][12]["body_rows"] == 2
    assert index["tables"][13]["header_rows"] == [["Oxide", "wt%"]]
    assert index["tables"][13]["body_rows"] == 2

    extract = {
        "species": {
            "La": {
                "observations": [
                    {
                        "observation_id": "barin-table-12",
                        "locator": {"pdf_page_index": 21},
                        "values": {"points": [{"value": 925}, {"value": 936}]},
                    }
                ]
            },
            "oxides": {
                "observations": [
                    {
                        "observation_id": "label-value-grid",
                        "locator": {"pdf_page_index": 22},
                        "values": {"points": [{"value": 49.2}, {"value": 15.1}]},
                    }
                ]
            },
        }
    }

    result = draft.compare_extract_to_draft(extract, index_path)

    assert [row["status"] for row in result] == ["presence-only"] * 4
    assert [row["matched_cell"]["value"] for row in result] == [
        925.0,
        936.0,
        49.2,
        15.1,
    ]


def test_compare_reads_partial_pressure_atm_strings(tmp_path: Path) -> None:
    chunk_dir = tmp_path / "raw" / "pressure-string" / "ocr-mineru" / "p001-p007"
    chunk_dir.mkdir(parents=True)
    items = [
        {
            "type": "table",
            "page_idx": 3,
            "table_body": (
                "<table><tr><th>T, K</th><th>pMg, atm</th></tr>"
                "<tr><td>1851</td><td>$4.68 \\times 10^{-7}$</td></tr></table>"
            ),
        },
        {
            "type": "table",
            "page_idx": 3,
            "table_body": (
                "<table><tr><th>T, K</th><th>pMg, atm</th></tr>"
                "<tr><td>1914</td><td>$1.25 \\times 10^{-6}$</td></tr></table>"
            ),
        },
    ]
    (chunk_dir / "content_list.json").write_text(
        json.dumps(items), encoding="utf-8"
    )
    index_path = draft.convert_source(
        "pressure-string", tmp_path / "draft", corpus_root=tmp_path / "raw"
    )
    extract = {
        "species": {
            "Mg": {
                "observations": [
                    {
                        "observation_id": "pressure-series",
                        "locator": {"pdf_page_index": 3, "table": "2"},
                        "values": {
                            "quantity": "partial_pressure",
                            "points": [{"pressure_atm": "1.25e-6"}],
                        },
                    }
                ]
            }
        }
    }

    result = draft.compare_extract_to_draft(extract, index_path)

    assert len(result) == 1
    assert result[0]["status"] == "matched"
    assert result[0]["path"].endswith(".values.points[0].pressure_atm")
    assert result[0]["matched_cell"]["table_key"] == "p004-t02"
    assert result[0]["matched_cell"]["text"] == "$1.25 \\times 10^{-6}$"


def test_compare_carries_flags_for_matched_cells(tmp_path: Path) -> None:
    chunk_dir = tmp_path / "raw" / "flagged-values" / "ocr-mineru" / "p001-p010"
    chunk_dir.mkdir(parents=True)
    item = {
        "type": "table",
        "page_idx": 1,
        "table_body": (
            "<table><tr><th>Value</th></tr>"
            "<tr><td>25.540</td></tr><tr><td>2.34*</td></tr>"
            "<tr><td>837.6**</td></tr><tr><td>0.029||</td></tr>"
            "<tr><td>–1.2</td></tr><tr><td>—2.4</td></tr>"
            "<tr><td>1ppm</td></tr><tr><td>1 m</td></tr>"
            "<tr><td>1m</td></tr></table>"
        ),
    }
    (chunk_dir / "content_list.json").write_text(
        json.dumps([item]), encoding="utf-8"
    )
    index_path = draft.convert_source(
        "flagged-values", tmp_path / "draft", corpus_root=tmp_path / "raw"
    )
    observations = [
        {"observation_id": f"point-{index}", "values": {"points": [{"value": value}]}}
        for index, value in enumerate(
            (25.54, 2.34, 837.6, 0.029, -1.2, -2.4, 1.0, 1.0, 1.0)
        )
    ]
    for observation in observations:
        observation["locator"] = {"pdf_page_index": 1}
    extract = {"species": {"Cr": {"observations": observations}}}

    result = draft.compare_extract_to_draft(extract, index_path)

    assert [row["status"] for row in result] == ["presence-only"] * 6 + ["mismatch"] * 3
    assert result[6]["matched_cell"] is None
    assert result[7]["matched_cell"] is None
    assert result[8]["matched_cell"] is None
    assert [row["matched_cell"]["flags"] for row in result[:6]] == [
            [],
        ["superscript_or_footnote_mark"],
        ["superscript_or_footnote_mark"],
        ["superscript_or_footnote_mark"],
        ["en_dash_minus"],
        ["em_dash_minus"],
    ]
    with (index_path.parent / "p002-t01.csv").open(
        encoding="utf-8", newline=""
    ) as stream:
        cells = list(csv.DictReader(stream))
    assert [(cell["value_text"], cell["mark"]) for cell in cells[3:5]] == [
        ("837.6", "**"),
        ("0.029", "||"),
    ]
    assert (cells[7]["value_text"], cells[7]["mark"]) == ("1ppm", "")
    assert (cells[8]["value_text"], cells[8]["mark"]) == ("1 m", "")
    assert (cells[9]["value_text"], cells[9]["mark"]) == ("1m", "")


def test_compare_reports_match_and_planted_mismatch(tmp_path: Path) -> None:
    csv_path = tmp_path / "p002-t01.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=draft._CSV_FIELDS)
        writer.writeheader()
        for row, text, origin_row in (
            (1, "0.871", 1),
            (2, "0.871", 1),
            (3, "3.0", 3),
            (4, "9007199254740993", 4),
        ):
            writer.writerow(
                {
                    "row": row,
                    "column": 1,
                    "row_type": "body",
                    "text": text,
                    "value_text": text,
                    "mark": "",
                    "numeric_column": 1,
                    "origin_row": origin_row,
                    "origin_column": 1,
                    "rowspan": 1,
                    "colspan": 1,
                    "flags": "[]",
                }
            )
    index_path = tmp_path / "index.yaml"
    index_path.write_text(
        yaml.safe_dump(
            {"tables": [{"table_key": "p002-t01", "pdf_page": 2, "csv": csv_path.name,
                         "columns": [{"column": 1, "header": "Components of gas phase"}]}]}
        ),
        encoding="utf-8",
    )
    extract = {
        "species": {
            "Cr": {
                "observations": [
                    {
                        "observation_id": "0.871-vs-0.817",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 0.817}]},
                    },
                    {
                        "observation_id": "sign-flip",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": -0.871}]},
                    },
                    {
                        "observation_id": "one-place-decimal-shift",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 8.71}]},
                    },
                    {
                        "observation_id": "exact-match",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 0.871, "table_row_number": 1,
                                                   "phase": "gas", "table_phase_column": 1}]},
                    },
                    {
                        "observation_id": "expanded-copy-is-not-a-second-cell",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 0.871}]},
                    },
                    {
                        "observation_id": "large-integer-near-miss",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 9007199254740992}]},
                    },
                    {
                        "observation_id": "large-integer-exact-match",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 9007199254740993}]},
                    },
                ]
            }
        }
    }

    result = draft.compare_extract_to_draft(extract, index_path)

    assert [row["status"] for row in result] == [
        "mismatch",
        "mismatch",
        "mismatch",
        "matched",
        "mismatch",
        "mismatch",
        "presence-only",
    ]
    assert result[0]["matched_cell"] is None
    assert result[3]["matched_cell"]["value"] == 0.871
    assert result[3]["matched_cell"]["flags"] == []
    assert result[3]["draft_candidate_count"] == 3
    assert result[5]["matched_cell"] is None
    assert result[6]["matched_cell"]["text"] == "9007199254740993"

    wrong_row = {
        "species": {
            "Cr": {
                "observations": [{
                    "observation_id": "wrong-row",
                    "locator": {"pdf_page": 2},
                    "values": {"points": [{"value": 0.871, "table_row_number": 99}]},
                }]
            }
        }
    }
    mutated = draft.compare_extract_to_draft(wrong_row, index_path)
    assert mutated[0]["status"] == "mismatch"
    assert mutated[0]["matched_cell"] is None


def test_compare_phase_is_bound_to_the_candidate_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "p001-t01.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=draft._CSV_FIELDS)
        writer.writeheader()
        for column_number, text in ((1, "1.50"), (2, "2.50")):
            writer.writerow({
                "row": 2, "column": column_number, "row_type": "body",
                "text": text, "value_text": text, "mark": "",
                "numeric_column": column_number, "origin_row": 2,
                "origin_column": column_number, "rowspan": 1, "colspan": 1,
                "flags": "[]",
            })
    index_path = tmp_path / "index.yaml"
    index_path.write_text(yaml.safe_dump({"tables": [{
        "table_key": "p001-t01", "pdf_page": 1, "csv": csv_path.name,
        "columns": [
            {"column": 1, "header": "liquid wt%"},
            {"column": 2, "header": "gas wt%"},
        ],
    }]}), encoding="utf-8")
    extract = {"species": {"Fe": {"observations": [{
        "observation_id": "wrong-phase-column", "locator": {"pdf_page": 1},
        "values": {"points": [{
            "value": 1.50, "table_row_number": 2, "phase": "gas",
        }]},
    }]}}}

    result = draft.compare_extract_to_draft(extract, index_path)[0]
    assert result["status"] == "mismatch"
    assert result["matched_cell"] is None


def test_phase_tokens_do_not_match_words_in_other_column_headers(tmp_path: Path) -> None:
    csv_path = tmp_path / "p005-t01.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=draft._CSV_FIELDS)
        writer.writeheader()
        writer.writerow({
            "row": 3, "column": 1, "row_type": "body", "text": "1.93e-9",
            "value_text": "1.93e-9", "mark": "", "numeric_column": 1,
            "origin_row": 3, "origin_column": 1, "rowspan": 1, "colspan": 1,
            "flags": "[]",
        })
    index_path = tmp_path / "index.yaml"
    index_path.write_text(yaml.safe_dump({"tables": [{
        "table_key": "p005-t01", "pdf_page": 5, "csv": csv_path.name,
        "columns": [
            {"column": 1, "header": "liquid (K)"},
            {"column": 2, "header": "Components of gas phase"},
        ],
    }]}), encoding="utf-8")

    for phase in ("", "K", "of", "phase", "gas"):
        extract = {"species": {"Ca": {"observations": [{
            "observation_id": f"wrong-{phase!r}", "locator": {"pdf_page": 5},
            "values": {"points": [{
                "value": 1.93e-9, "table_row_number": 3,
                "table_phase_column": 1, "phase": phase,
            }]},
        }]}}}
        result = draft.compare_extract_to_draft(extract, index_path)[0]
        assert result["status"] == "mismatch", phase


def test_compare_summary_prints_presence_only_count(tmp_path: Path, capsys) -> None:
    csv_path = tmp_path / "p001-t01.csv"
    csv_path.write_text(
        "row,column,row_type,text,value_text,mark,numeric_column,origin_row,"
        "origin_column,rowspan,colspan,flags\n"
        "2,1,body,1.5,1.5,,1,2,1,1,1,[]\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "index.yaml"
    index_path.write_text(yaml.safe_dump({"tables": [{
        "table_key": "p001-t01", "pdf_page": 1, "csv": csv_path.name,
    }]}), encoding="utf-8")
    extract_path = tmp_path / "extract.yaml"
    extract_path.write_text(yaml.safe_dump({"species": {"Fe": {"observations": [{
        "observation_id": "presence", "locator": {"pdf_page": 1},
        "values": {"points": [{"value": 1.5}]},
    }]}}}), encoding="utf-8")

    draft._compare_command(
        argparse.Namespace(extract=str(extract_path), draft_index=str(index_path))
    )

    assert "presence-only=1" in capsys.readouterr().err
