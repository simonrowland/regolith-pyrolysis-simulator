from __future__ import annotations

import json
from pathlib import Path

from tools import mineru_table_qa as qa


def _write_corpus(
    tmp_path: Path,
    sid: str,
    table_body: str,
    page_text: str,
    *,
    page_idx: int = 0,
    details: str = "",
) -> Path:
    corpus = tmp_path / "corpus"
    content_dir = corpus / "raw" / sid / "ocr-mineru" / "p001-p010"
    content_dir.mkdir(parents=True, exist_ok=True)
    content_path = content_dir / "content_list.json"
    content_path.write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "page_idx": page_idx,
                    "table_body": table_body,
                }
            ]
        ),
        encoding="utf-8",
    )
    text_dir = corpus / "text" / sid
    text_dir.mkdir(parents=True, exist_ok=True)
    (text_dir / "pdftotext-layout.txt").write_text(page_text, encoding="utf-8")
    if details:
        markdown_dir = text_dir / "mineru" / "p001-p010"
        markdown_dir.mkdir(parents=True, exist_ok=True)
        (markdown_dir / "output.md").write_text(details, encoding="utf-8")
    return corpus


def _by_sid(rows: list[dict[str, object]], sid: str) -> dict[str, object]:
    return next(row for row in rows if row["sid"] == sid)


def test_clean_table_scores_zero_and_uses_absolute_pdf_page(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Oxide</th><th>wt%</th></tr>"
        "<tr><td>SiO2</td><td>49.2</td></tr>"
        "<tr><td>Al2O3</td><td>15.1</td></tr></table>"
    )
    page_one = "An unrelated first page with enough extracted words. " * 3
    page_two = "Oxide values 49.2 and 15.1 are printed in this table. " * 3
    corpus = _write_corpus(
        tmp_path,
        "clean",
        body,
        f"{page_one}\f{page_two}",
        page_idx=1,
        details="<details><summary>chart</summary>generated text</details>",
    )

    rows, summaries = qa.rank_corpus(corpus)
    row = _by_sid(rows, "clean")

    assert row["absolute_page"] == 2
    assert row["chunk"] == "p001-p010"
    assert row["numeric_agreement"] == "1.000"
    assert row["combined_score"] == 0
    assert _by_sid(summaries, "clean")["details_invented_never_data"] == 1


def test_full_document_output_without_chunk_uses_page_one_origin(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    mineru_dir = corpus / "text" / "unchunked" / "mineru"
    mineru_dir.mkdir(parents=True)
    (mineru_dir / "content_list.json").write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "page_idx": 1,
                    "table_body": (
                        "<table><tr><th>Oxide</th><th>wt%</th></tr>"
                        "<tr><td>SiO2</td><td>49.2</td></tr></table>"
                    ),
                }
            ]
        ),
        encoding="utf-8",
    )
    text_dir = corpus / "text" / "unchunked"
    (text_dir / "pdftotext-layout.txt").write_text(
        "first page text\fsecond page includes 49.2 and enough text. " * 3,
        encoding="utf-8",
    )

    rows, _ = qa.rank_corpus(corpus)

    assert _by_sid(rows, "unchunked")["absolute_page"] == 2
    assert _by_sid(rows, "unchunked")["chunk"] == "full-document"


def test_multiple_numbers_in_one_cell_scores_high(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Oxide</th><th>Values</th></tr>"
        "<tr><td>SiO2</td><td>49.2 wt% 15.1</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "merged", body, "Values 49.2 and 15.1 are printed. " * 4
    )

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "merged")

    assert "multiple_numbers=1" in str(row["flags"])
    assert row["combined_score"] >= 8


def test_numeric_cells_with_unit_suffix_are_included_in_agreement(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Reading</th></tr>"
        "<tr><td>1000 °C</td></tr><tr><td>1200 °C</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "unit-values", body, "Readings 1000 °C and 1200 °C. " * 4
    )

    rows, _ = qa.rank_corpus(corpus)

    assert _by_sid(rows, "unit-values")["numeric_agreement"] == "1.000"


def test_dropped_plusminus_cell_scores_high(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Temperature °C</th></tr>"
        "<tr><td>1643.15</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "dropped-pm", body, "Temperature 1643±15 degrees C. " * 4
    )

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "dropped-pm")

    assert "dropped_plusminus=1" in str(row["flags"])
    assert row["numeric_agreement"] == "0.000"
    assert row["combined_score"] >= 15


def test_ragged_body_row_scores_high(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Oxide</th><th>wt%</th><th>Notes</th></tr>"
        "<tr><td>SiO2</td><td>49.2</td><td>ok</td></tr>"
        "<tr><td>Al2O3</td><td>15.1</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "ragged", body, "SiO2 49.2 Al2O3 15.1. " * 5
    )

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "ragged")

    assert row["ragged_score"] != "0.000"
    assert "ragged_rows=1" in str(row["flags"])
    assert row["combined_score"] >= 5


def test_short_pdftotext_page_is_scan_and_does_not_fail(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Oxide</th><th>wt%</th></tr>"
        "<tr><td>SiO2</td><td>49.2</td></tr></table>"
    )
    corpus = _write_corpus(tmp_path, "scan", body, "scan")

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "scan")

    assert row["numeric_agreement"] == "scan"
    assert row["combined_score"] == 0
    assert "numeric_check_skipped_scan=1" in str(row["flags"])


def test_latex_lost_exponent_minus_and_comma_dot_are_flagged(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Reading</th><th>Other</th></tr>"
        "<tr><td>1.2E3</td><td>12,5</td></tr>"
        "<tr><td>$x^2$</td><td>13.0</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path,
        "patterns",
        body,
        "Values 1.2E-3 and 12.5 are shown; $x^2$ is a label. " * 3,
    )

    rows, _ = qa.rank_corpus(corpus)
    flags = str(_by_sid(rows, "patterns")["flags"])

    assert "latex=1" in flags
    assert "lost_exponent_minus=1" in flags
    assert "comma_dot_decimal=1" in flags


def test_sources_without_mineru_output_are_omitted(tmp_path: Path) -> None:
    (tmp_path / "text" / "no-mineru").mkdir(parents=True)

    rows, summaries = qa.rank_corpus(tmp_path)

    assert rows == []
    assert summaries == []
