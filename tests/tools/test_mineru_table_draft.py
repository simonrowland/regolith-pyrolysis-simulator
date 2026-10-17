from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

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
        "<tr><td>A</td><td>−1.2</td><td>25.540</td></tr>"
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
    assert "comma_decimal_suspect" in grid[1][2].flags
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

    assert [row["status"] for row in result] == ["matched"] * 4
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

    assert [row["status"] for row in result] == ["matched"] * 6 + ["mismatch"] * 3
    assert result[6]["matched_cell"] is None
    assert result[7]["matched_cell"] is None
    assert result[8]["matched_cell"] is None
    assert [row["matched_cell"]["flags"] for row in result[:6]] == [
        ["comma_decimal_suspect"],
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
            {"tables": [{"table_key": "p002-t01", "pdf_page": 2, "csv": csv_path.name}]}
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
                        "values": {"points": [{"value": 0.871}]},
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
        "matched",
    ]
    assert result[0]["matched_cell"] is None
    assert result[3]["matched_cell"]["value"] == 0.871
    assert result[3]["matched_cell"]["flags"] == []
    assert result[5]["matched_cell"] is None
    assert result[6]["matched_cell"]["text"] == "9007199254740993"
