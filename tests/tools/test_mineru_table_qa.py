from __future__ import annotations

import json
import os
import subprocess
import sys
from io import StringIO
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


def test_single_page_chunk_uses_its_absolute_start_page(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    chunk = corpus / "raw" / "robiew" / "ocr-mineru" / "chunk-p125-rot180"
    chunk.mkdir(parents=True)
    (chunk / "content_list.json").write_text(
        json.dumps([{"type": "table", "page_idx": 0, "table_body":
                     "<table><tr><th>Value</th></tr><tr><td>1.2</td></tr></table>"}]),
        encoding="utf-8",
    )
    text_dir = corpus / "text" / "robiew"
    text_dir.mkdir(parents=True)
    (text_dir / "pdftotext-layout.txt").write_text("\f" * 124 + "values " + "1.2 " * 30)

    rows, _ = qa.rank_corpus(corpus)

    assert _by_sid(rows, "robiew")["absolute_page"] == 125


def test_digit_matching_accepts_pdftotext_grouping_spaces(tmp_path: Path) -> None:
    body = "<table><tr><th>Value</th></tr><tr><td>1234.5</td></tr></table>"
    corpus = _write_corpus(tmp_path, "grouped", body, "Value 1 234.5. " * 10)

    rows, _ = qa.rank_corpus(corpus)

    assert _by_sid(rows, "grouped")["numeric_agreement"] == "1.000"
    assert "numeric_mismatch" not in str(_by_sid(rows, "grouped")["flags"])


def test_numeric_mismatch_is_not_scored_without_page_number_overlap(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Value</th></tr>"
        "<tr><td>12.3</td></tr><tr><td>45.6</td></tr>"
        "<tr><td>78.9</td></tr><tr><td>10.1</td></tr></table>"
    )
    corpus = _write_corpus(tmp_path, "unreliable-page-text", body, "Lots of prose, no table values. " * 10)

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "unreliable-page-text")

    assert "numeric_mismatch=" in str(row["flags"])
    assert "numeric_mismatch=0.000" in str(row["rates"])
    assert row["combined_score"] == 0


def test_fused_reference_digit_and_run_together_columns_are_flagged(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Value</th><th>Reference</th></tr>"
        "<tr><td>11.0</td><td>5</td></tr>"
        "<tr><td>11.06</td><td>6</td></tr>"
        "<tr><td>11.07</td><td>7</td></tr>"
        "<tr><td>11.1</td><td>8</td></tr></table>"
    )
    fused = _write_corpus(tmp_path, "fused", body, "Values 11.0 11.06 11.1 references. " * 4)
    rows, _ = qa.rank_corpus(fused)
    fused_row = _by_sid(rows, "fused")

    assert "fused_reference_decimal=2" in str(fused_row["flags"])
    assert "fused_reference_decimal=0.250" in str(fused_row["rates"])
    assert fused_row["combined_score"] == 25.0

    merged_body = (
        "<table><tr><th>Composition</th></tr>"
        "<tr><td>47.147.549.2</td></tr><tr><td>40.1</td></tr></table>"
    )
    merged = _write_corpus(tmp_path, "run-together", merged_body, "Composition values. " * 10)
    rows, _ = qa.rank_corpus(merged)

    assert "run_together_columns=1" in str(_by_sid(rows, "run-together")["flags"])


def test_run_number_column_is_not_treated_as_reference_column(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Run No.</th><th>Slag composition</th></tr>"
        "<tr><td>1.8</td><td>85.27</td></tr>"
        "<tr><td>1.10</td><td>85.88</td></tr>"
        "<tr><td>3.10</td><td>81.67</td></tr></table>"
    )
    corpus = _write_corpus(tmp_path, "run-numbers", body, "Run data. " * 20)

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "run-numbers")

    assert "fused_reference_decimal" not in str(row["flags"])


def test_reference_column_does_not_treat_run_no_as_citations() -> None:
    assert qa._reference_column(["Run No."]) is False
    assert qa._reference_column(["No."]) is True


def test_calibration_tsv_matches_live_scores_and_ranks_mangled_tables(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    calibration = repo_root / "tests/tools/mineru_table_qa_calibration.tsv"
    output = tmp_path / "calibration.tsv"
    env = os.environ.copy()
    env.setdefault("MPLCONFIGDIR", "/private/tmp/claude-501/main-review/mplcache")
    repo_path = str(repo_root)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (repo_path, env.get("PYTHONPATH", "")) if part
    )
    corpus_root = Path(os.environ.get(
        "REGOLITH_CORPUS_ROOT", "/Users/simonrowland/Repos/regolith-corpus"
    )).expanduser()

    subprocess.run(
        [
            sys.executable,
            str(repo_root / "tests/tools/calibrate_mineru_table_qa.py"),
            str(calibration),
            str(corpus_root),
            str(output),
        ],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert output.read_bytes() == calibration.read_bytes()
    rows = [line.split("\t") for line in output.read_text(encoding="utf-8").splitlines()[1:]]
    mangled = [row for row in rows if row[1] == "MANGLED"]
    assert len(mangled) == 4
    assert all(int(row[4]) <= 8 for row in mangled)


def test_score_formula_docstring_lists_all_weights_and_overlap_gate() -> None:
    doc = qa.__doc__ or ""

    for signal in (
        "fused-reference decimals", "run-together columns", "letter-for-digit",
        "overlap rate is at least 0.5", "Every signal contributes only with at least two hits",
    ):
        assert signal in doc


def test_letter_for_digit_is_flagged_in_numeric_scientific_column(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Sample</th><th>Reading</th></tr>"
        "<tr><td>A</td><td>I.4×10^{-6}</td></tr>"
        "<tr><td>B</td><td>I.6×10^{-6}</td></tr>"
        "<tr><td>C</td><td>2.0×10^{-5}</td></tr></table>"
    )
    corpus = _write_corpus(tmp_path, "letter-for-digit", body, "Sample readings. " * 10)

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "letter-for-digit")

    assert "letter_for_digit=2" in str(row["flags"])
    assert row["combined_score"] > 0


def test_latex_superscript_citations_do_not_count_as_fused_reference_digits(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Values</th></tr>"
        "<tr><td>$32.4^4$</td></tr><tr><td>$34.1^1$</td></tr>"
        "<tr><td>$18.5^2$</td></tr></table>"
    )
    corpus = _write_corpus(tmp_path, "latex-citations", body, "Values 32.4 34.1 18.5. " * 8)

    rows, _ = qa.rank_corpus(corpus)

    assert "fused_reference_decimal" not in str(_by_sid(rows, "latex-citations")["flags"])


def test_uncertainty_and_parenthetical_uncertainty_are_not_multiple_numbers(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Values</th></tr>"
        "<tr><td>1.23 ± 0.04</td></tr>"
        "<tr><td>1.23 +/- 0.04</td></tr>"
        "<tr><td>0.11(2)</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path,
        "uncertainties",
        body,
        "Values 1.23 ± 0.04, 1.23 +/- 0.04, and 0.11(2) are printed. " * 3,
    )

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "uncertainties")

    assert "multiple_numbers" not in str(row["flags"])
    assert row["combined_score"] == 0


def test_range_column_does_not_count_range_endpoints_as_merged_values(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Range</th></tr>"
        "<tr><td>0.1–0.2</td></tr><tr><td>1.1–1.4</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "ranges", body, "Ranges 0.1–0.2 and 1.1–1.4 are shown. " * 3
    )

    rows, _ = qa.rank_corpus(corpus)

    assert "multiple_numbers" not in str(_by_sid(rows, "ranges")["flags"])


def test_plain_numeric_latex_wrappers_are_formatting(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Reading</th></tr>"
        "<tr><td>$1.23$</td></tr>"
        "<tr><td>\\mathrm{1.24}</td></tr>"
        "<tr><td>$1.25\\%$</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "plain-latex", body, "Readings 1.23 and 1.24 are printed. " * 4
    )

    rows, _ = qa.rank_corpus(corpus)

    assert "latex" not in str(_by_sid(rows, "plain-latex")["flags"])


def test_latex_exponents_and_multiplication_are_number_damage(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Reading</th></tr>"
        "<tr><td>$10^{-5}$</td></tr>"
        "<tr><td>$1.2 \\times 10^{-3}$</td></tr>"
        "<tr><td>$\\text{-}1.23$</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "latex-math", body, "Readings 10 to minus 5 and 1.2 times 10 to minus 3. " * 3
    )

    rows, _ = qa.rank_corpus(corpus)
    flags = str(_by_sid(rows, "latex-math")["flags"])

    assert "latex=3" in flags
    assert "multiple_numbers" not in flags


def test_two_merged_quantity_cells_score_above_a_large_clean_table(
    tmp_path: Path,
) -> None:
    clean_values = [str(1000 + index) for index in range(100)]
    clean_body = (
        "<table><tr><th>Reading</th></tr>"
        + "".join(f"<tr><td>{value}</td></tr>" for value in clean_values)
        + "</table>"
    )
    merged_body = (
        "<table><tr><th>Values</th></tr>"
        "<tr><td>49.2 wt% 15.1</td></tr>"
        "<tr><td>43.7 wt% 12.8</td></tr></table>"
    )
    clean_corpus = _write_corpus(
        tmp_path,
        "large-clean",
        clean_body,
        "Readings " + " ".join(clean_values) + ". " + "Measured values. " * 6,
    )
    merged_corpus = _write_corpus(
        tmp_path,
        "small-merged",
        merged_body,
        "Values 49.2 wt% 15.1 and 43.7 wt% 12.8 are shown. " * 3,
    )

    clean_rows, _ = qa.rank_corpus(clean_corpus)
    merged_rows, _ = qa.rank_corpus(merged_corpus)
    clean = _by_sid(clean_rows, "large-clean")
    merged = _by_sid(merged_rows, "small-merged")

    assert clean["numeric_cells"] == 100
    assert clean["combined_score"] == 0
    assert "multiple_numbers=2" in str(merged["flags"])
    assert "multiple_numbers=1.000" in str(merged["rates"])
    assert merged["combined_score"] > clean["combined_score"]


def test_one_odd_numeric_cell_does_not_clear_the_two_hit_score_floor(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Values</th></tr>"
        "<tr><td>49.2 wt% 15.1</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "one-merged", body, "Values 49.2 wt% 15.1 are printed. " * 3
    )

    rows, _ = qa.rank_corpus(corpus)

    assert _by_sid(rows, "one-merged")["combined_score"] == 0


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
        "<tr><td>1643.15</td></tr><tr><td>1700.25</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path,
        "dropped-pm",
        body,
        "Temperature 1643±15 and 1700±25 degrees C are printed. " * 4,
    )

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "dropped-pm")

    assert "dropped_plusminus=2" in str(row["flags"])
    assert row["numeric_agreement"] == "0.000"
    assert row["combined_score"] >= 15
    assert row["_dropped_plusminus_hits"][0]["pdftotext_text"] == "1643±15"


def test_plain_temperature_decimals_are_not_dropped_plusminus_hits(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Temperature °C</th></tr>"
        "<tr><td>1000.00</td></tr><tr><td>1100.00</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path,
        "plain-temperatures",
        body,
        "Temperatures 1000.00 and 1100.00 are printed. " * 4,
    )

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "plain-temperatures")

    assert "dropped_plusminus" not in str(row["flags"])
    assert row["_dropped_plusminus_hits"] == []


def test_scan_temperature_without_pdftotext_evidence_is_not_a_hit(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Temperature K</th></tr>"
        "<tr><td>1643.15</td></tr><tr><td>1700.25</td></tr></table>"
    )
    corpus = _write_corpus(tmp_path, "scan-temperatures", body, "scan")

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "scan-temperatures")

    assert "dropped_plusminus" not in str(row["flags"])
    assert row["_dropped_plusminus_hits"] == []


def test_output_places_all_dropped_hits_before_top_40_and_summary(
    tmp_path: Path,
) -> None:
    body = (
        "<table><tr><th>Temperature °C</th></tr>"
        "<tr><td>1643.15</td></tr><tr><td>1700.25</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path,
        "output-order",
        body,
        "Temperature 1643±15 and 1700±25 degrees C are printed. " * 4,
    )
    rows, summaries = qa.rank_corpus(corpus)
    output = StringIO()

    qa.write_tsv(rows, summaries, output)

    rendered = output.getvalue()
    assert rendered.index("# (a) every dropped_plusminus hit") < rendered.index(
        "# (b) top 40"
    ) < rendered.index("# (c) per-sid summary")
    assert "1643.15\t1643±15" in rendered


def test_ragged_body_row_scores_high(tmp_path: Path) -> None:
    body = (
        "<table><tr><th>Oxide</th><th>wt%</th><th>Notes</th></tr>"
        "<tr><td>SiO2</td><td>49.2</td><td>ok</td></tr>"
        "<tr><td>Al2O3</td><td>15.1</td></tr>"
        "<tr><td>FeO</td><td>8.4</td></tr></table>"
    )
    corpus = _write_corpus(
        tmp_path, "ragged", body, "SiO2 49.2 Al2O3 15.1. " * 5
    )

    rows, _ = qa.rank_corpus(corpus)
    row = _by_sid(rows, "ragged")

    assert row["ragged_rate"] != "0.000"
    assert "ragged_rows=2" in str(row["flags"])
    assert row["combined_score"] >= 50


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
