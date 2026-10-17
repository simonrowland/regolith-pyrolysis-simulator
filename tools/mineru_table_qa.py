"""Rank MinerU tables by deterministic OCR-mangling signals.

The combined score is ``10 * ragged_score + 10 * (1 - numeric_agreement)``
plus 10 per dropped-plus-minus cell, 8 per multi-number cell, 5 per lost
exponent-minus cell, 3 per LaTeX cell, and 2 per comma/dot decimal mismatch.
``ragged_score`` is the population variance of body-row cell counts plus the
fraction of body rows differing from the header width (or modal row width if
the table has no detected header). Scan pages skip the agreement penalty.
Markdown ``<details>`` blocks are counted per source and explicitly marked as
invented material; they are never treated as data or included in table scores.

The CLI writes ranked table TSV rows followed by a per-source summary section.
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
_DROPPED_PM_VALUE = re.compile(r"(?<!\d)(\d{4})\.(\d{2})(?!\d)")
_TEMPERATURE_UNIT = re.compile(
    r"(?:°\s*C|º\s*C|deg\.?\s*C|degrees?\s*C|\bCelsius\b|\bKelvin\b|\bK\b)",
    re.IGNORECASE,
)
_LATEX = re.compile(r"\\[A-Za-z]+|[$]")
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
    "ragged_score",
    "numeric_agreement",
    "flags",
    "combined_score",
)
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


def _cell_is_numeric(
    text: str, column: int, numeric_columns: set[int]
) -> bool:
    tokens = _number_tokens(text)
    if not tokens:
        return False
    if column in numeric_columns:
        return True
    return len(tokens) >= 2 or _NUMBER_TOKEN.match(text.lstrip()) is not None


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
        variance = statistics.pvariance(body_counts)
        if header_count:
            reference_width = max(row_counts[:header_count], default=0)
        else:
            reference_width = statistics.mode(body_counts)
        mismatch_count = sum(count != reference_width for count in body_counts)
        mismatch_fraction = mismatch_count / len(body_counts)
    else:
        variance = 0.0
        mismatch_count = 0
        mismatch_fraction = 0.0
    ragged_score = variance + mismatch_fraction

    page_idx = int(item["page_idx"])
    try:
        chunk_start_page = draft._chunk_start_page(path)
    except ValueError:
        chunk_start_page = 1
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
    header_texts = _header_texts(grid, header_count)
    for row_index, row in enumerate(grid):
        if row_index < header_count:
            continue
        for column, cell in enumerate(row):
            text = cell.text.strip()
            cell_tokens = _number_tokens(text)
            is_numeric = _cell_is_numeric(text, column, numeric_columns)
            if is_numeric:
                tokens.extend(cell_tokens)
            if not text:
                continue
            if _LATEX.search(text):
                flags["latex"] += 1
            if len(cell_tokens) >= 2:
                flags["multiple_numbers"] += 1

            if page_text is not None:
                if any(_exponent_minus_missing(token, page_text) for token in cell_tokens):
                    flags["lost_exponent_minus"] += 1
                if _decimal_separator_mismatch(cell_tokens, page_text):
                    flags["comma_dot_decimal"] += 1

            temperature_context = " ".join(
                [
                    text,
                    header_texts[column] if column < len(header_texts) else "",
                    row[column - 1].text if column else "",
                    row[column + 1].text if column + 1 < len(row) else "",
                ]
            )
            dropped_pm = _DROPPED_PM_VALUE.search(text)
            pdf_has_pm_at_value = bool(
                dropped_pm
                and page_text is not None
                and re.search(
                    rf"(?<!\d){re.escape(dropped_pm.group(1))}\s*"
                    rf"(?:±|\uf0b1)\s*{re.escape(dropped_pm.group(2))}(?!\d)",
                    page_text,
                )
            )
            if dropped_pm and (
                _TEMPERATURE_UNIT.search(temperature_context) or pdf_has_pm_at_value
            ):
                flags["dropped_plusminus"] += 1

    if mismatch_count:
        flags["ragged_rows"] = mismatch_count
    agreement: float | str
    if scan:
        agreement = "scan"
        flags["numeric_check_skipped_scan"] += 1
        if missing_page_text:
            flags["pdftotext_missing"] += 1
    else:
        page_digit_strings = {
            _digit_string(token) for token in _number_tokens(page_text or "")
        }
        agreement = (
            sum(_digit_string(token) in page_digit_strings for token in tokens)
            / len(tokens)
            if tokens
            else 1.0
        )
        if agreement < 1:
            flags["numeric_mismatch"] += 1

    score = (
        10 * ragged_score
        + (0 if agreement == "scan" else 10 * (1 - float(agreement)))
        + 10 * flags["dropped_plusminus"]
        + 8 * flags["multiple_numbers"]
        + 5 * flags["lost_exponent_minus"]
        + 3 * flags["latex"]
        + 2 * flags["comma_dot_decimal"]
    )
    return {
        "sid": sid,
        "chunk": _chunk_name(path),
        "absolute_page": page,
        "table_index": table_index,
        "n_rows": len(grid),
        "n_cols": max((len(row) for row in grid), default=0),
        "ragged_score": f"{ragged_score:.3f}",
        "numeric_agreement": (
            agreement if agreement == "scan" else f"{agreement:.3f}"
        ),
        "flags": ";".join(
            f"{name}={count}" for name, count in sorted(flags.items())
        ),
        "combined_score": round(score, 3),
        "_score": score,
        "_scan": scan,
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
    writer = csv.DictWriter(stream, fieldnames=TABLE_FIELDS, delimiter="\t", lineterminator="\n")
    writer.writeheader()
    writer.writerows({key: row[key] for key in TABLE_FIELDS} for row in table_rows)
    stream.write("# per-sid summary; details blocks are invented, never data\n")
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
