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
        "<tr><td>B</td><td>1.2</td><td>1.23a</td></tr>"
        "<tr><td>C</td><td>-2.3</td><td>0.540</td></tr>"
        "<tr><td>D</td><td>-3.4</td><td>invalid</td></tr>"
        "<tr><td>E</td><td></td><td></td></tr>"
        "<tr><td>F</td><td>-5.0</td><td>2.34<sup>†</sup></td></tr></table>"
    )
    header_count = draft._header_row_count(grid)
    numeric_columns = draft._numeric_columns(grid, header_count)
    draft._add_cell_flags(grid, header_count, numeric_columns)

    assert "unicode_minus" in grid[1][1].flags
    assert "comma_decimal_suspect" in grid[1][2].flags
    assert "missing_minus_suspect" in grid[2][1].flags
    assert grid[2][2].value_text == "1.23"
    assert grid[2][2].mark == "a"
    assert "superscript_or_footnote_mark" in grid[2][2].flags
    assert "non_numeric_in_numeric_column" in grid[4][2].flags
    assert "empty_cell" in grid[5][1].flags
    assert "empty_cell" in grid[5][2].flags
    assert grid[6][2].text == "2.34†"
    assert grid[6][2].value_text == "2.34"
    assert grid[6][2].mark == "†"
    assert "superscript_or_footnote_mark" in grid[6][2].flags

    assert "unicode_minus" not in grid[3][2].flags
    assert "missing_minus_suspect" not in grid[3][1].flags
    assert "comma_decimal_suspect" not in grid[3][2].flags
    assert "superscript_or_footnote_mark" not in grid[3][2].flags
    assert not grid[3][2].flags


def test_compare_reports_match_and_planted_mismatch(tmp_path: Path) -> None:
    csv_path = tmp_path / "p002-t01.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=draft._CSV_FIELDS)
        writer.writeheader()
        for row, text, origin_row in (
            (1, "1.5", 1),
            (2, "1.5", 1),
            (3, "3.0", 3),
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
                        "observation_id": "match",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 1.5}]},
                    },
                    {
                        "observation_id": "planted-mismatch",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 2.0}]},
                    },
                    {
                        "observation_id": "expanded-copy-is-not-a-second-cell",
                        "locator": {"pdf_page_index": 1},
                        "values": {"points": [{"value": 1.5}]},
                    },
                ]
            }
        }
    }

    result = draft.compare_extract_to_draft(extract, index_path)

    assert [row["status"] for row in result] == [
        "matched",
        "mismatch",
        "mismatch",
    ]
    assert result[1]["draft_cells"]
