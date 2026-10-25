from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from tools import mineru_table_draft as draft


def test_compare_matches_the_migrator_evaporation_rate_field(tmp_path: Path) -> None:
    draft_dir = tmp_path / "tables"
    draft_dir.mkdir()
    (draft_dir / "p001-t01.csv").write_text(
        "row,column,text,value_text,mark,numeric_column,origin_row,origin_column,flags\n"
        "1,1,Temperature,Temperature,,0,1,1,[]\n"
        "1,2,j_meas,j_meas,,0,1,2,[]\n"
        "2,1,1075,1075,,1,2,1,[]\n"
        "2,2,2.344e-6,2.344e-6,,1,2,2,[]\n",
        encoding="utf-8",
    )
    index_path = draft_dir / "index.yaml"
    index_path.write_text(
        yaml.safe_dump(
            {
                "tables": [
                    {
                        "table_key": "p001-t01",
                        "pdf_page": 1,
                        "csv": "p001-t01.csv",
                        "columns": [{"column": 2, "header": "j_meas"}],
                    }
                ]
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    extract = {
        "species": {
            "Fe": {
                "observations": [
                    {
                        "observation_id": "example-p001-t01-r2-c2",
                        "phase": "cr",
                        "locator": {
                            "page": 1,
                            "pdf_page_index": 0,
                            "table": "1",
                            "table_row_number": 2,
                            "table_column_number": 2,
                        },
                        "values": {"evaporation_rate": "2.344e-6"},
                    }
                ]
            }
        }
    }

    compared = draft.compare_extract_to_draft(extract, index_path)

    assert len(compared) == 1
    assert compared[0]["status"] == "matched"
    assert compared[0]["value"] == Decimal("2.344e-6")
    assert compared[0]["path"].endswith(".values.evaporation_rate")
    assert compared[0]["matched_cell"]["row"] == 2
    assert compared[0]["matched_cell"]["column"] == 2
