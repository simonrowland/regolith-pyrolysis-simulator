from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from tools import mineru_table_draft as draft


def test_leading_letter_decimal_is_flagged_for_render_check() -> None:
    assert draft._has_letter_for_digit("L.0")


def test_compare_uses_table_block_and_render_spelling_for_ion_currents(
    tmp_path: Path,
) -> None:
    source = (
        tmp_path
        / "text"
        / "example-source"
        / "mineru"
        / "p001-p003"
        / "chunk_content_list.json"
    )
    source.parent.mkdir(parents=True)
    source.write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "page_idx": 0,
                    "table_caption": "Table 2a",
                    "table_body": (
                        "<table><tr><th>T, K</th><th>I+O</th></tr>"
                        "<tr><td>1851</td><td>1.0(8.1)</td></tr>"
                        "<tr><td>1852</td><td>L.0</td></tr></table>"
                    ),
                },
                {
                    "type": "table",
                    "page_idx": 0,
                    "table_caption": "Table 2b",
                    "table_body": (
                        "<table><tr><th>T, K</th><th>I+O</th></tr>"
                        "<tr><td>1851</td><td>9.9</td></tr></table>"
                    ),
                },
            ]
        ),
        encoding="utf-8",
    )
    draft_dir = draft.convert_source(source.parent, tmp_path / "draft").parent
    observations = []
    for row in (2, 3):
        observations.append(
            {
                "observation_id": f"example_source_p001_t01_r{row:02d}_c02",
                "locator": {
                    "page": 1,
                    "pdf_page_index": 0,
                    "table": "2a",
                    "row": str(row),
                    "column": 2,
                },
                "phase": "gas",
                "values": {
                    "quantity": "ion_intensity_ratio",
                    "ion_current_ratio": "1.0",
                },
            }
        )
    extract = {"species": {"O": {"observations": observations}}}
    mapping = {
        "source_id": "example-source",
        "source": {"citation": "A. Author, Example measurements, 2020."},
        "extraction": {
            "method": "MinerU table-cell draft",
            "date": "2026-10-25",
            "worker": "test",
        },
        "review_status": "draft",
        "tables": [
            {
                "page": 1,
                "table_index": 1,
                "type": "rate_series",
                "quantity": "ion_current_ratio",
                "unit": "dimensionless",
                "evidence_class": "measured_direct",
                "phase": "gas",
                "rows": [2, 3],
                "temperature": {"column": 1, "unit": "K"},
                "values": [{"column": 2, "species": "O"}],
                "render_verified": [{"row": 3, "column": 2, "text": "1.0"}],
            }
        ],
    }

    compared = draft.compare_extract_to_draft(
        extract, draft_dir / "index.yaml", mapping
    )

    assert [row["status"] for row in compared] == ["matched", "matched"]
    assert [row["matched_cell"]["table_key"] for row in compared] == [
        "p001-t01",
        "p001-t01",
    ]
    assert [row["matched_cell"]["row"] for row in compared] == [2, 3]
    assert [Decimal(str(row["matched_cell"]["value"])) for row in compared] == [
        Decimal("1.0"),
        Decimal("1.0"),
    ]
