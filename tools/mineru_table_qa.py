"""Rank MinerU tables by deterministic OCR-mangling signals.

Cell-level damage rates are hit cells divided by numeric cells; raggedness is
ragged body rows divided by body rows. Dropped plus-minus requires a matching
pdftotext value. Every signal contributes only with at least two hits. The
combined score weights dropped plus-minus, numeric mismatch, comma/dot
decimals, ragged rows, fused-reference decimals, run-together columns, and
letter-for-digit at 100; multiple numbers at 40; lost-exponent-minus at 60;
and LaTeX damage at 30. Numeric mismatch contributes only when its page-number
overlap rate is at least 0.5, and scan pages skip numeric-mismatch scoring.
The ``flags`` column retains hit counts and ``rates`` shows normalized values.

Markdown ``<details>`` blocks are counted per source and explicitly marked as
invented material; they are never treated as data or included in table scores.
The CLI writes every dropped-plus-minus hit, the top 40 ranked tables, and the
per-source summary, in that order.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tools import mineru_table_draft as draft


_DEFAULT_CORPUS_ROOT = Path.home() / "Repos" / "regolith-corpus"
_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:[+\-−–—])?(?:\d+(?:[.,]\d+)?|\.\d+)"
    r"(?:\(\d+\))?(?:[eE][+\-−–—]?\d+)?"
)
_CHUNK_PAGE_START = re.compile(r"(?:^|[-_])p(\d+)(?:[-_]|$)")
_NUMBER_ATOM = (
    r"[+\-−–—]?(?:\d+(?:[.,]\d+)?|\.\d+)"
    r"(?:\(\d+\))?(?:[eE][+\-−–—]?\d+)?"
)
_NUMERIC_EXPRESSION = re.compile(
    rf"{_NUMBER_ATOM}(?:\s*(?:±|\+\s*/\s*[-−])\s*{_NUMBER_ATOM})?\s*%?"
)
_LATEX = re.compile(r"\\[A-Za-z]+|[$]")
_LATEX_EXPONENT = re.compile(r"\^\s*(?:\{\s*[+\-−]?\s*\d+\s*\}|[+\-−]?\s*\d+)")
_LATEX_MULTIPLY = re.compile(r"\\(?:times|cdot)\b")
_LATEX_MINUS_ENCODING = re.compile(
    r"\\(?:minus|textminus|mathminus)\b"
    r"|\\(?:text|mbox)\s*\{\s*[-−]\s*\}"
    r"|\\unicode\s*\{\s*x?2212\s*\}",
    re.IGNORECASE,
)
_LATEX_MATHSPACE = re.compile(r"\\[,;!]")
_LATEX_MATHRM = re.compile(r"\\mathrm\s*\{([^{}]*)\}")
_UNCERTAINTY_SEPARATOR = re.compile(r"(?:±|\+\s*/\s*[-−])")
_RANGE_SEPARATOR = re.compile(r"\s*[–—]\s*")
_RANGE_HEADER = re.compile(r"\brange\b", re.IGNORECASE)
_MULTI_NUMBER_GAP = re.compile(r"\s+(?:[A-Za-zµ°%]+\s+)*")
_MINIMUM_SIGNAL_HITS = 2
_SCORE_WEIGHTS = {
    "dropped_plusminus": 100,
    "numeric_mismatch": 100,
    "comma_dot_decimal": 100,
    "ragged_rows": 100,
    "multiple_numbers": 40,
    "fused_reference_decimal": 100,
    "run_together_columns": 100,
    "letter_for_digit": 100,
    "lost_exponent_minus": 60,
    "latex": 30,
}
_DETAILS = re.compile(r"<details\b", re.IGNORECASE)
_EXPONENT = re.compile(
    r"(?P<mantissa>(?:\d+(?:[.,]\d+)?|\.\d+))[eE](?P<exponent>\d+)"
)

TABLE_FIELDS = (
    "sid",
    "chunk",
    "absolute_page",
    "table_index",
    "n_rows",
    "n_cols",
    "numeric_cells",
    "ragged_rate",
    "numeric_agreement",
    "flags",
    "rates",
    "combined_score",
)
DROPPED_PM_FIELDS = ("sid", "page", "table", "cell_text", "pdftotext_text")
SUMMARY_FIELDS = (
    "sid",
    "content_lists",
    "tables",
    "flagged_tables",
    "scan_tables",
    "max_score",
    "mean_score",
    "details_invented_never_data",
)


def _source_roots(corpus_root: Path) -> dict[str, list[Path]]:
    roots: dict[str, list[Path]] = defaultdict(list)
    text_root = corpus_root / "text"
    raw_root = corpus_root / "raw"
    if text_root.is_dir():
        for sid_dir in sorted(text_root.iterdir()):
            if sid_dir.is_dir():
                mineru_roots = [
                    path
                    for path in sorted(sid_dir.iterdir())
                    if path.is_dir() and path.name.startswith("mineru")
                ]
                if mineru_roots:
                    roots[sid_dir.name].extend(mineru_roots)
    if raw_root.is_dir():
        for sid_dir in sorted(raw_root.iterdir()):
            mineru_root = sid_dir / "ocr-mineru"
            if mineru_root.is_dir():
                roots[sid_dir.name].append(mineru_root)
    return roots


def _content_list_files(source_roots: list[Path]) -> list[Path]:
    return sorted(
        {
            path
            for source_root in source_roots
            for path in source_root.rglob("*content_list*.json")
            if path.is_file()
        }
    )


def _details_count(source_roots: list[Path]) -> int:
    count = 0
    for source_root in source_roots:
        for path in source_root.rglob("*.md"):
            if path.is_file():
                count += len(_DETAILS.findall(path.read_text(encoding="utf-8")))
    return count


def _chunk_name(path: Path) -> str:
    for part in reversed(path.parts):
        match = draft._CHUNK_START.search(part)
        if match:
            return match.group()
    return "full-document"


def _chunk_start_page(path: Path) -> int:
    """Use the draft tool's range parser, then accept single-page chunk names."""
    try:
        return draft._chunk_start_page(path)
    except ValueError:
        pass
    for part in reversed(path.parts):
        match = _CHUNK_PAGE_START.search(part)
        if match:
            return int(match.group(1))
    return 1


def _row_cell_counts(grid: list[list[draft.Cell]]) -> list[int]:
    counts: list[int] = []
    for row in grid:
        origins: dict[tuple[int, int], draft.Cell] = {}
        for cell in row:
            if cell.origin_row is not None and cell.origin_column is not None:
                origins[(cell.origin_row, cell.origin_column)] = cell
        counts.append(sum(cell.colspan for cell in origins.values()))
    return counts


def _number_tokens(text: str) -> list[str]:
    return [match.group() for match in _NUMBER_TOKEN.finditer(text)]


def _digit_string(token: str) -> str:
    return "".join(character for character in token if character.isdigit())


def _page_digit_strings(page_text: str) -> set[str]:
    values = {_digit_string(token) for token in _number_tokens(page_text)}
    # pdftotext can split thousands groups at spaces (e.g. "1 234.5"). Keep
    # this narrow so whitespace between separate table values is not collapsed.
    grouped = re.finditer(r"(?<!\d)\d{1,3}(?:\s+\d{3})+(?:[.,]\d+)?(?!\d)", page_text)
    values.update(_digit_string(match.group()) for match in grouped)
    return values


def _decimal_places(token: str) -> int | None:
    match = _NUMBER_TOKEN.fullmatch(token)
    if not match or not any(mark in token for mark in ".,"):
        return None
    mantissa = re.split(r"[eE]", token, maxsplit=1)[0]
    return len(re.split(r"[.,]", mantissa)[-1])


def _reference_column(headers: list[str]) -> bool:
    return any(
        re.search(r"\bref(?:erence)?s?\.?", header, re.I)
        or re.fullmatch(r"\s*no\.\s*", header, re.I)
        for header in headers
    )


def _extra_decimal_digit_cells(
    grid: list[list[draft.Cell]], header_count: int, header_texts: list[str]
) -> int:
    has_reference_marks = any(
        re.search(r"\d+\.\d+[)\]]", cell.text) for row in grid for cell in row
    )
    if not (_reference_column(header_texts) or has_reference_marks):
        return 0
    hits = 0
    width = max((len(row) for row in grid), default=0)
    for column in range(width):
        places = [
            _decimal_places(_number_tokens(row[column].text.strip())[0])
            for row in grid[header_count:]
            if column < len(row) and _number_tokens(row[column].text.strip())
        ]
        ordinary = [value for value in places if value is not None]
        if len(ordinary) < 2:
            continue
        baseline = statistics.mode(ordinary)
        hits += sum(value == baseline + 1 for value in ordinary)
    return hits


def _letter_for_digit_cells(
    grid: list[list[draft.Cell]], header_count: int
) -> int:
    hits = 0
    width = max((len(row) for row in grid), default=0)
    for column in range(width):
        cells = [
            row[column].text.strip()
            for row in grid[header_count:]
            if column < len(row) and row[column].text.strip()
        ]
        numeric_like = [
            text for text in cells
            if _number_tokens(text) or draft._LETTER_FOR_DIGIT.search(text)
        ]
        if len(numeric_like) >= 2 and len(numeric_like) / len(cells) >= 0.5:
            hits += sum(bool(draft._LETTER_FOR_DIGIT.search(text)) for text in numeric_like)
    return hits


def _cell_is_numeric(
    text: str, column: int, numeric_columns: set[int]
) -> bool:
    tokens = _number_tokens(text)
    if not tokens:
        return False
    if column in numeric_columns:
        return True
    numeric_text = _latex_numeric_text(text)
    return len(tokens) >= 2 or _NUMBER_TOKEN.match(numeric_text) is not None


def _latex_numeric_text(text: str) -> str:
    value = text.strip()
    if value.startswith("$") and value.endswith("$"):
        value = value[1:-1].strip()
    value = _LATEX_MATHRM.sub(r"\1", value)
    value = _LATEX_MINUS_ENCODING.sub("-", value)
    value = re.sub(r"\\pm\b", "±", value)
    value = re.sub(r"\\%", "%", value)
    return _LATEX_MATHSPACE.sub(" ", value).strip()


def _latex_changes_number(text: str, is_numeric: bool) -> bool:
    if not is_numeric or not _LATEX.search(text):
        return False
    if (
        _LATEX_EXPONENT.search(text)
        or _LATEX_MULTIPLY.search(text)
        or _LATEX_MINUS_ENCODING.search(text)
    ):
        return True
    return _NUMERIC_EXPRESSION.fullmatch(_latex_numeric_text(text)) is None


def _has_multiple_independent_quantities(text: str, header: str) -> bool:
    matches = list(_NUMBER_TOKEN.finditer(text))
    if len(matches) < 2 or _UNCERTAINTY_SEPARATOR.search(text):
        return False
    if _LATEX_EXPONENT.search(text) or _LATEX_MULTIPLY.search(text):
        return False
    if _RANGE_HEADER.search(header) and _RANGE_SEPARATOR.search(text):
        return False
    return any(
        _MULTI_NUMBER_GAP.fullmatch(text[left.end() : right.start()])
        for left, right in zip(matches, matches[1:])
    )


def _evidence_rate(hits: int, population: int) -> float:
    if hits < _MINIMUM_SIGNAL_HITS or population <= 0:
        return 0.0
    return hits / population


def _page_texts(corpus_root: Path, sid: str) -> list[str] | None:
    path = corpus_root / "text" / sid / "pdftotext-layout.txt"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace").split("\f")


def _header_texts(grid: list[list[draft.Cell]], header_count: int) -> list[str]:
    width = max((len(row) for row in grid), default=0)
    rows = grid[:header_count]
    return [
        " ".join(row[column].text for row in rows if column < len(row))
        for column in range(width)
    ]


def _exponent_minus_missing(token: str, page_text: str) -> bool:
    match = _EXPONENT.fullmatch(token)
    if not match:
        return False
    mantissa = re.escape(match.group("mantissa"))
    exponent = re.escape(match.group("exponent"))
    return bool(
        re.search(
            rf"(?<![\w.]){mantissa}\s*[eE]\s*[\-−–—]\s*{exponent}(?!\d)",
            page_text,
        )
    )


def _decimal_separator_mismatch(tokens: list[str], page_text: str) -> bool:
    for token in tokens:
        if "," not in token and "." not in token:
            continue
        alternate = token.replace(",", "\0").replace(".", ",").replace("\0", ".")
        if alternate == token:
            alternate = token.replace(",", ".")
        if re.search(
            rf"(?<![\d.,]){re.escape(alternate)}(?![\d.,])", page_text
        ):
            return True
    return False


def _table_row(
    *,
    sid: str,
    path: Path,
    item: dict[str, Any],
    table_index: int,
    page_texts: list[str] | None,
) -> dict[str, Any]:
    grid = draft.parse_table_html(str(item.get("table_body") or ""))
    header_count = draft._header_row_count(grid)
    numeric_columns = draft._numeric_columns(grid, header_count)
    row_counts = _row_cell_counts(grid)
    body_counts = row_counts[header_count:]
    if body_counts:
        if header_count:
            reference_width = max(row_counts[:header_count], default=0)
        else:
            reference_width = statistics.mode(body_counts)
        mismatch_count = sum(count != reference_width for count in body_counts)
    else:
        mismatch_count = 0
    ragged_rate = mismatch_count / len(body_counts) if body_counts else 0.0

    page_idx = int(item["page_idx"])
    chunk_start_page = _chunk_start_page(path)
    page = draft.absolute_pdf_page(chunk_start_page, page_idx)
    page_text: str | None = None
    scan = False
    missing_page_text = page_texts is None or page > len(page_texts)
    if not missing_page_text:
        page_text = page_texts[page - 1]
        scan = len(page_text.strip()) < 80
    else:
        scan = True

    flags: Counter[str] = Counter()
    tokens: list[str] = []
    numeric_cells: list[list[str]] = []
    dropped_plusminus_hits: list[dict[str, Any]] = []
    header_texts = _header_texts(grid, header_count)
    decimal_outlier_count = _extra_decimal_digit_cells(grid, header_count, header_texts)
    if decimal_outlier_count:
        flags["fused_reference_decimal"] = decimal_outlier_count
    run_together_count = 0
    letter_for_digit_count = _letter_for_digit_cells(grid, header_count)
    if letter_for_digit_count:
        flags["letter_for_digit"] = letter_for_digit_count
    for row_index, row in enumerate(grid):
        if row_index < header_count:
            continue
        for column, cell in enumerate(row):
            text = cell.text.strip()
            cell_tokens = _number_tokens(text)
            is_numeric = _cell_is_numeric(text, column, numeric_columns)
            if is_numeric:
                tokens.extend(cell_tokens)
                numeric_cells.append(cell_tokens)
            if not text:
                continue
            if _latex_changes_number(text, is_numeric):
                flags["latex"] += 1
            header_text = header_texts[column] if column < len(header_texts) else ""
            if is_numeric and _has_multiple_independent_quantities(text, header_text):
                flags["multiple_numbers"] += 1
            if is_numeric and draft._RUN_TOGETHER.search(text):
                run_together_count += 1

            if is_numeric and page_text is not None:
                if any(_exponent_minus_missing(token, page_text) for token in cell_tokens):
                    flags["lost_exponent_minus"] += 1
                if _decimal_separator_mismatch(cell_tokens, page_text):
                    flags["comma_dot_decimal"] += 1

            if is_numeric:
                pdf_text = draft.confirm_dropped_plusminus(text, page_text)
                if pdf_text:
                    flags["dropped_plusminus"] += 1
                    dropped_plusminus_hits.append(
                        {
                            "sid": sid,
                            "page": page,
                            "table": table_index,
                            "cell_text": text,
                            "pdftotext_text": pdf_text,
                        }
                    )

    agreement: float | str
    numeric_mismatch_score_hits = 0
    if scan:
        agreement = "scan"
        flags["numeric_check_skipped_scan"] += 1
        if missing_page_text:
            flags["pdftotext_missing"] += 1
    else:
        page_digit_strings = _page_digit_strings(page_text or "")
        matching_tokens = [
            _digit_string(token) in page_digit_strings for token in tokens
        ]
        matching_cells = sum(
            bool(cell_tokens)
            and all(_digit_string(token) in page_digit_strings for token in cell_tokens)
            for cell_tokens in numeric_cells
        )
        agreement = (
            sum(matching_tokens) / len(tokens)
            if tokens
            else 1.0
        )
        numeric_mismatch_count = sum(
            bool(cell_tokens)
            and any(
                _digit_string(token) not in page_digit_strings
                for token in cell_tokens
            )
            for cell_tokens in numeric_cells
        )
        if numeric_mismatch_count:
            flags["numeric_mismatch"] = numeric_mismatch_count
        # A page with too little numeric overlap is not a trustworthy
        # pdftotext comparison surface (common for scanned and layout-heavy
        # pages). Keep its diagnostic count, but do not score it as OCR damage.
        if matching_cells >= 2 and matching_cells / max(len(numeric_cells), 1) >= 0.5:
            numeric_mismatch_score_hits = numeric_mismatch_count

    if run_together_count:
        flags["run_together_columns"] = run_together_count

    if mismatch_count:
        flags["ragged_rows"] = mismatch_count
    numeric_cell_count = len(numeric_cells)
    rates = {
        name: _evidence_rate(flags[name], numeric_cell_count)
        for name in _SCORE_WEIGHTS
        if name != "ragged_rows"
    }
    rates["numeric_mismatch"] = _evidence_rate(numeric_mismatch_score_hits, numeric_cell_count)
    rates["ragged_rows"] = _evidence_rate(mismatch_count, len(body_counts))
    score = sum(_SCORE_WEIGHTS[name] * rates[name] for name in _SCORE_WEIGHTS)
    return {
        "sid": sid,
        "chunk": _chunk_name(path),
        "absolute_page": page,
        "table_index": table_index,
        "n_rows": len(grid),
        "n_cols": max((len(row) for row in grid), default=0),
        "numeric_cells": numeric_cell_count,
        "ragged_rate": f"{ragged_rate:.3f}",
        "numeric_agreement": (
            agreement if agreement == "scan" else f"{agreement:.3f}"
        ),
        "flags": ";".join(
            f"{name}={count}" for name, count in sorted(flags.items())
        ),
        "rates": ";".join(f"{name}={rates[name]:.3f}" for name in sorted(rates)),
        "combined_score": round(score, 3),
        "_score": score,
        "_scan": scan,
        "_dropped_plusminus_hits": dropped_plusminus_hits,
    }


def rank_corpus(
    corpus_root: str | Path = _DEFAULT_CORPUS_ROOT,
    sids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ranked table records and per-source counts without writing files."""
    root = Path(corpus_root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"corpus root does not exist: {root}")
    roots_by_sid = _source_roots(root)
    if sids is not None:
        roots_by_sid = {
            sid: roots_by_sid[sid] for sid in dict.fromkeys(sids) if sid in roots_by_sid
        }

    table_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for sid, source_roots in sorted(roots_by_sid.items()):
        content_files = _content_list_files(source_roots)
        pages = _page_texts(root, sid)
        source_rows: list[dict[str, Any]] = []
        table_index = 0
        for path in content_files:
            content = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(content, list):
                raise ValueError(f"expected a content-list array in {path}")
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "table":
                    continue
                table_index += 1
                source_rows.append(
                    _table_row(
                        sid=sid,
                        path=path,
                        item=item,
                        table_index=table_index,
                        page_texts=pages,
                    )
                )
        table_rows.extend(source_rows)
        scores = [float(row["_score"]) for row in source_rows]
        summaries.append(
            {
                "sid": sid,
                "content_lists": len(content_files),
                "tables": len(source_rows),
                "flagged_tables": sum(float(row["_score"]) > 0 for row in source_rows),
                "scan_tables": sum(bool(row["_scan"]) for row in source_rows),
                "max_score": f"{max(scores, default=0.0):.3f}",
                "mean_score": f"{statistics.mean(scores) if scores else 0.0:.3f}",
                "details_invented_never_data": _details_count(source_roots),
            }
        )

    table_rows.sort(
        key=lambda row: (
            -float(row["_score"]),
            row["sid"],
            int(row["absolute_page"]),
            int(row["table_index"]),
            row["chunk"],
        )
    )
    summaries.sort(key=lambda row: (-float(row["max_score"]), row["sid"]))
    return table_rows, summaries


def write_tsv(
    table_rows: list[dict[str, Any]], summaries: list[dict[str, Any]], stream: Any
) -> None:
    stream.write("# (a) every dropped_plusminus hit\n")
    dropped_writer = csv.DictWriter(
        stream,
        fieldnames=DROPPED_PM_FIELDS,
        delimiter="\t",
        lineterminator="\n",
    )
    dropped_writer.writeheader()
    dropped_hits = [
        hit
        for row in table_rows
        for hit in row["_dropped_plusminus_hits"]
    ]
    dropped_hits.sort(key=lambda hit: (hit["sid"], hit["page"], hit["table"]))
    dropped_writer.writerows(dropped_hits)
    stream.write("# (b) top 40 by normalized score\n")
    writer = csv.DictWriter(
        stream, fieldnames=TABLE_FIELDS, delimiter="\t", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(
        {key: row[key] for key in TABLE_FIELDS} for row in table_rows[:40]
    )
    stream.write("# (c) per-sid summary; details blocks are invented, never data\n")
    summary_writer = csv.DictWriter(
        stream, fieldnames=SUMMARY_FIELDS, delimiter="\t", lineterminator="\n"
    )
    summary_writer.writeheader()
    summary_writer.writerows(
        {key: row[key] for key in SUMMARY_FIELDS} for row in summaries
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_root", nargs="?", default=str(_DEFAULT_CORPUS_ROOT))
    parser.add_argument("--sids", nargs="*", help="limit the scan to these source IDs")
    args = parser.parse_args(argv)
    try:
        rows, summaries = rank_corpus(args.corpus_root, args.sids)
        write_tsv(rows, summaries, sys.stdout)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
