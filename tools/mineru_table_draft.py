"""Write loss-preserving CSV drafts from MinerU content-list tables, and emit
extract rows from a declarative mapping of those drafts.

This parser retains spans, header tags, and cell origins for draft auditing;
the Pankratz ingest parser intentionally returns plain cell text. They have
different outputs and neither can replace the other without changing ingest
or draft semantics.

``emit`` reads a draft directory (``index.yaml`` plus the cell CSVs) and a
mapping file. It does not parse HTML again and it does not convert units.
One observation is written per measured value cell.

Mapping file (YAML). No code, no repeated keys. Quote any flow scalar that
contains a comma; block style is safer. Document keys:

- ``source_id`` — must match the draft index when both are set
- ``source.citation`` — required; other ``source`` keys are copied
- ``extraction.method``, ``extraction.date``, ``extraction.worker`` — required
- ``review_status`` — optional, default ``draft``
- ``fidelity_samples`` — optional, copied as the worker wrote them
- ``tables`` — one entry per draft table

Table keys:

- ``page`` and ``table_index`` select ``pPPP-tNN`` (1-based PDF page and the
  draft table index)
- ``type``, ``quantity``, ``unit`` — observation type, quantity token, printed unit.
  ``activity`` is stored as ``values.activity``. ``activity_coefficient`` is
  stored as ``values.gamma``. ``partial_pressure`` accepts ``atm``, ``bar``,
  or ``Pa`` only; any other unit raises ``ValueError`` naming that unit.
  ``delta_fG`` stays on ``values.value``
- ``evidence_class`` — stored as ``values.method_class``; a value column may override it
- ``phase``, ``per``, ``experiment``, ``regime``, ``condensed_form`` — copied when set
- ``rows`` — ``all`` (default) or a list of 1-based CSV row numbers (header rows included
  in the numbering; numeric mapped cells in header rows are also selected and require
  ``render_verified``)
- ``row_predicate`` — ``{column, equals}`` or ``{column, matches}`` on that row's cell.
  Do not combine it with a ``rows`` list
- ``temperature`` — ``{column, unit}`` where ``unit`` is ``K`` or ``C`` / ``°C``,
  or ``{as_printed, unit}`` when the temperature is not a body column. Optional
  ``locator`` / ``column_header`` for the printed-constant case. ``K`` is stored
  as ``values.T_K`` plus ``point_conditions.temperature_K``. ``C`` is stored as
  ``values.T_C`` only; the migrator derives kelvin
- the locator row label is the temperature cell, or the species cell of a value
  pair, otherwise the CSV row number
- ``values`` — ``{column, species}`` plus optional ``phase``, ``evidence_class``,
  ``unit``, ``quantity``, ``uncertainty``, ``attribution``, and ``scale_factor``.
  ``scale_factor`` is ``{header, exponent, direction}`` for a printed power-of-ten
  column factor. ``header`` is the exact MinerU header cell and ``exponent`` is the
  signed integer in its single ``10^n`` factor. ``direction`` is either
  ``quantity_times_factor_equals_cell`` or ``cell_times_factor_equals_quantity``.
  This scales the one stored quantity with exact decimal arithmetic; it does not
  convert units or emit a converted twin
- ``value_pairs`` — ``{species_column, value_column, phase}`` for a species cell
  beside each value. ``species_form: printed_formula`` strips ``$``, ``\\mathrm``
  / ``\\text``, and ``{ } _ ^ \\`` spaces. Do not set both ``values`` and ``value_pairs``
- ``conditions`` — ``{key, state, locator}`` copied onto every row. Do not repeat
  ``temperature_K``; the temperature axis writes that key
- ``uncertainty`` — copied onto every value, unless ``uncertainty.columns.<n>``
  replaces it for that column
- ``attribution`` — copied to ``values.attribution`` (required by the migrator
  for ``quoted_attributed``)
- ``published_page`` — copied onto locators when the worker knows it
- ``render_verified`` — ``{row, column, text}`` for a trapped cell. ``row`` and
  ``column`` are the 1-based CSV coordinates. ``text`` is the spelling the
  worker read on the page render; that spelling is stored. Numeric cells in
  MinerU header rows also require render verification before emission

Cell text is stored verbatim after stripping surrounding whitespace. Units are
not converted. Empty cells and cells whose text is only
``-``, ``–``, ``—``, or ``−`` are skipped and counted under
``skipped_empty_or_dash``. They are never written as zero. A cell is
``needs_render_check`` if its draft flags include ``comma_decimal_suspect`` or
``ambiguous_comma_separator``, if this command marks it as
``fused_superscript``, ``run_together``, ``letter_for_digit``,
``stacked_values``, or ``dropped_plusminus``, or if it is numeric in a header
row. ``emit`` raises ``ValueError`` naming those traps unless
``render_verified`` supplies a spelling for that cell. The marker is not
written onto the row. ``dropped_plusminus`` is marked only when a pdftotext
page confirms the ``±`` (``--pdftotext``, ``<draft>/pdftotext-layout.txt``, or
index ``pdftotext_layout``). Traps are read on the measured value cell, not on
species labels.

``emit`` refuses a mapped table whose only source is pdftotext-layout, or whose
CSV has no MinerU ``flags`` column. Transcribe that table, or request MinerU.

Worked example (a two-column pressure table; the dash is skipped, not stored
as zero)::

    source_id: example-source
    source:
      citation: A. Author, Example measurements, 2020.
    extraction:
      method: mineru_table_draft emit
      date: '2026-10-17'
      worker: example
    review_status: draft
    tables:
      - page: 2
        table_index: 1
        type: partial_pressure
        quantity: partial_pressure
        evidence_class: measured_direct
        unit: atm
        phase: gas
        rows: all
        temperature:
          column: 1
          unit: K
        values:
          - column: 2
            species: Mg
        conditions:
          - key: total_pressure_Pa
            state:
              tag: unknown
              reason: not_published
            locator:
              page: 2
              table: '1'
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

import yaml


_CHUNK_START = re.compile(r"p(\d+)-p\d+")
_NUMBER = re.compile(
    r"^[+\-−–—]?(?:\d+(?:\.\d+)?|\.\d+)(?:\(\d+\))?(?:[eE][+\-]?\d+)?$"
)
_LATEX_SCIENTIFIC = re.compile(
    r"^([+\-−–—]?(?:\d+(?:\.\d+)?|\.\d+))\s*\\times\s*"
    r"10\^\{?([+\-−–—]?\d+)\}?$"
)
_HEADER_POWER_OF_TEN = re.compile(r"10\s*\^\s*\{?\s*([+\-−]?\d+)\s*\}?")
_COMMA_DECIMAL = re.compile(
    r"(?<![\d.,])(?:\d{1,3}\.\d{3},\d+|\d+,\d{1,2})(?!\d)"
)
_AMBIGUOUS_COMMA = re.compile(r"(?<![\d.,])\d{2,},\d{3}(?!\d)")
_FOOTNOTE_MARK = re.compile(r"([*†‡§¶|]+)$")
_NUMERIC_SUPERSCRIPT = re.compile(
    r"[+\-−–—]?\d+|[⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+"
)
_UNICODE_SUPERSCRIPT_SUFFIX = re.compile(r"[⁺⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+$")
_NEGATIVE_HEADER = re.compile(
    r"\b(?:negative|minus)\b|below\s+zero|less\s+than\s+zero|[<≤]\s*0",
    re.IGNORECASE,
)
_CSV_FIELDS = (
    "row",
    "column",
    "row_type",
    "text",
    "value_text",
    "mark",
    "numeric_column",
    "origin_row",
    "origin_column",
    "rowspan",
    "colspan",
    "flags",
)
_DEFAULT_CORPUS_ROOT = Path.home() / "Repos" / "regolith-corpus" / "raw"


@dataclass
class _RawCell:
    text_parts: list[str] = field(default_factory=list)
    superscript_parts: list[str] = field(default_factory=list)
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    superscript_depth: int = 0

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def superscript_text(self) -> str:
        return "".join(self.superscript_parts)


@dataclass
class Cell:
    text: str
    origin_row: int | None
    origin_column: int | None
    rowspan: int = 1
    colspan: int = 1
    merged: bool = False
    is_header: bool = False
    superscript_text: str = ""
    value_text: str = ""
    mark: str = ""
    numeric_value: float | None = None
    flags: list[str] = field(default_factory=list)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[_RawCell]] = []
        self.row: list[_RawCell] | None = None
        self.cell: _RawCell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            if self.row is None:
                self.row = []
            attributes = dict(attrs)
            self.cell = _RawCell(
                rowspan=_span(attributes.get("rowspan")),
                colspan=_span(attributes.get("colspan")),
                is_header=tag == "th",
            )
        elif tag == "sup" and self.cell is not None:
            self.cell.superscript_depth += 1
        elif tag == "br" and self.cell is not None:
            self.cell.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "sup" and self.cell is not None:
            self.cell.superscript_depth = max(0, self.cell.superscript_depth - 1)
        elif tag in {"td", "th"} and self.row is not None and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.text_parts.append(data)
            if self.cell.superscript_depth:
                self.cell.superscript_parts.append(data)


def _span(value: str | None) -> int:
    try:
        return max(1, int(value or "1"))
    except ValueError:
        return 1


def _is_number(text: str) -> bool:
    token = text.strip().strip("$").strip()
    return bool(_NUMBER.fullmatch(token) or _LATEX_SCIENTIFIC.fullmatch(token))


def _number_decimal(text: str) -> Decimal | None:
    token = text.strip().strip("$").strip()
    token = re.sub(r"\\mathrm\s*\{([^{}]*)\}", r"\1", token)
    token = token.replace(r"\,", "").replace("−", "-").replace("–", "-").replace("—", "-")
    scientific = re.fullmatch(
        r"([+\-]?\d+(?:\.\d+)?|[+\-]?\.\d+)\s*\\times\s*10\s*\^\s*\{?([+\-]?\d+)\}?",
        token,
    )
    if scientific:
        try:
            return Decimal(scientific.group(1)) * (Decimal(10) ** int(scientific.group(2)))
        except InvalidOperation:
            return None
    if not _is_number(token):
        return None
    token = re.sub(r"\(\d+\)", "", token)
    token = token.translate(str.maketrans({"−": "-", "–": "-", "—": "-"}))
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _number_value(text: str) -> float | None:
    value = _number_decimal(text)
    return float(value) if value is not None else None


def _split_mark(text: str, superscript_text: str = "") -> tuple[str, str]:
    token = text.strip()
    superscript = superscript_text.strip()
    mark = ""
    mark_match = _FOOTNOTE_MARK.search(token)
    if mark_match:
        candidate = token[: mark_match.start()].rstrip()
        suffix = mark_match.group(1)
        numeric_superscript = bool(
            superscript
            and _NUMERIC_SUPERSCRIPT.fullmatch(superscript)
            and candidate.endswith(superscript)
            and _is_number(candidate[: -len(superscript)].rstrip())
        )
        unicode_superscript = _UNICODE_SUPERSCRIPT_SUFFIX.search(candidate)
        raw_numeric_superscript = bool(
            unicode_superscript
            and _is_number(candidate[: unicode_superscript.start()].rstrip())
        )
        if _is_number(candidate) or numeric_superscript or raw_numeric_superscript:
            token = candidate
            mark = suffix
    if superscript and token.endswith(superscript):
        candidate = token[: -len(superscript)].rstrip()
        if _is_number(candidate) and _NUMERIC_SUPERSCRIPT.fullmatch(superscript):
            numeric_superscript = re.fullmatch(r"[+\-−–—]?\d+", superscript)
            return f"{candidate}^{superscript}", mark or (
                "" if numeric_superscript else superscript
            )
        if _is_number(candidate):
            return candidate, mark or superscript
    unicode_superscript = _UNICODE_SUPERSCRIPT_SUFFIX.search(token)
    if unicode_superscript:
        candidate = token[: unicode_superscript.start()].rstrip()
        if _is_number(candidate):
            return f"{candidate}^{unicode_superscript.group()}", mark or (
                unicode_superscript.group()
            )
    if mark:
        return token, mark
    return token, ""


def parse_table_html(table_body: str) -> list[list[Cell]]:
    parser = _TableParser()
    parser.feed(table_body)
    parser.close()

    placed: dict[tuple[int, int], Cell] = {}
    row_count = len(parser.rows)
    for row_index, source_row in enumerate(parser.rows):
        column_index = 0
        for raw_cell in source_row:
            while any(
                (row_index, column_index + offset) in placed
                for offset in range(raw_cell.colspan)
            ):
                column_index += 1
            for row_offset in range(raw_cell.rowspan):
                for column_offset in range(raw_cell.colspan):
                    placed[(row_index + row_offset, column_index + column_offset)] = Cell(
                        text=raw_cell.text,
                        origin_row=row_index,
                        origin_column=column_index,
                        rowspan=raw_cell.rowspan,
                        colspan=raw_cell.colspan,
                        merged=raw_cell.rowspan > 1 or raw_cell.colspan > 1,
                        is_header=raw_cell.is_header,
                        superscript_text=raw_cell.superscript_text,
                    )
            row_count = max(row_count, row_index + raw_cell.rowspan)
            column_index += raw_cell.colspan

    width = max((column + 1 for _, column in placed), default=0)
    grid: list[list[Cell]] = []
    for row_index in range(row_count):
        row = []
        for column_index in range(width):
            row.append(
                placed.get(
                    (row_index, column_index),
                    Cell(text="", origin_row=None, origin_column=None),
                )
            )
        grid.append(row)
    return grid


def _header_row_count(grid: list[list[Cell]]) -> int:
    tagged_count = 0
    for row in grid:
        if row and any(cell.is_header for cell in row):
            tagged_count += 1
        else:
            break
    axis_with_unit = re.compile(
        r"\b(?:temperature|pressure|time)\b"
        r"[^\n]{0,24}(?:\b(?:K|bar|Pa|atm|mmHg|s|sec|min|h|hr)\b|"
        r"(?<![A-Za-z])°C(?![A-Za-z])|\bdeg(?:ree)?\s+C\b)",
        re.I,
    )
    count = tagged_count
    if not count and grid:
        first_row = grid[0]
        first_row_text = " ".join(cell.text for cell in first_row)
        if axis_with_unit.search(first_row_text) and not any(
            _number_value(_split_mark(cell.text, cell.superscript_text)[0]) is not None
            for cell in first_row
        ):
            count = 1
    if count:
        while count < len(grid):
            previous = " ".join(cell.text for cell in grid[count - 1])
            # A rowspan cell is expanded into later grid rows but belongs to its
            # origin row; it must not make an otherwise numeric axis row look mixed.
            row = [cell for cell in grid[count] if cell.origin_row == count]
            populated = [cell for cell in row if cell.text.strip()]
            numeric_count = sum(
                _number_value(_split_mark(cell.text, cell.superscript_text)[0]) is not None
                for cell in populated
            )
            trailing_unit = bool(
                populated
                and populated[-1].text.strip() in {"K", "°C", "C"}
            )
            if (
                axis_with_unit.search(previous)
                and numeric_count >= 2
                and all(
                    _number_value(_split_mark(cell.text, cell.superscript_text)[0])
                    is not None
                    or (trailing_unit and index == len(populated) - 1)
                    for index, cell in enumerate(populated)
                )
            ):
                count += 1
            else:
                break
        return count

    numeric_rows = [
        [
            _number_value(_split_mark(cell.text, cell.superscript_text)[0])
            for cell in row
        ]
        for row in grid
    ]
    for row_index, row_values in enumerate(numeric_rows):
        if sum(value is not None for value in row_values) >= 2:
            return row_index
    for row_index, row_values in enumerate(numeric_rows):
        if any(value is not None for value in row_values):
            return row_index
    return len(grid)


def _numeric_columns(grid: list[list[Cell]], header_count: int) -> set[int]:
    if not grid:
        return set()
    numeric: Counter[int] = Counter()
    populated: Counter[int] = Counter()
    for row in grid[header_count:]:
        for column_index, cell in enumerate(row):
            if cell.text.strip():
                populated[column_index] += 1
            value_text, _ = _split_mark(cell.text, cell.superscript_text)
            if _number_value(value_text) is not None:
                numeric[column_index] += 1
    return {
        column
        for column, count in numeric.items()
        if count and count / max(1, populated[column]) >= 0.5
    }


def _add_cell_flags(
    grid: list[list[Cell]], header_count: int, numeric_columns: set[int]
) -> Counter[str]:
    width = max((len(row) for row in grid), default=0)
    header_text = {
        column: " ".join(
            row[column].text for row in grid[:header_count] if column < len(row)
        )
        for column in range(width)
    }
    counts: Counter[str] = Counter()
    for row_index, row in enumerate(grid):
        for column_index, cell in enumerate(row):
            cell.value_text, cell.mark = _split_mark(
                cell.text, cell.superscript_text
            )
            cell.numeric_value = _number_value(cell.value_text)
            flags: list[str] = []
            if not cell.text.strip():
                flags.append("empty_cell")
            if cell.merged:
                flags.append("merged_cell")
            for sign, flag in (
                ("−", "unicode_minus"),
                ("–", "en_dash_minus"),
                ("—", "em_dash_minus"),
            ):
                if sign in cell.text:
                    flags.append(flag)
            if cell.mark:
                flags.append("superscript_or_footnote_mark")
            if (
                row_index >= header_count
                and column_index in numeric_columns
                and cell.text.strip()
                and cell.numeric_value is None
            ):
                flags.append("non_numeric_in_numeric_column")
            if (
                row_index >= header_count
                and column_index in numeric_columns
                and _NEGATIVE_HEADER.search(header_text[column_index])
                and cell.numeric_value is not None
                and not re.match(r"^\s*[+\-−–—]", cell.value_text)
            ):
                flags.append("missing_minus_suspect")
            if _COMMA_DECIMAL.search(cell.text):
                flags.append("comma_decimal_suspect")
            if _AMBIGUOUS_COMMA.search(cell.text):
                flags.append("ambiguous_comma_separator")
            cell.flags = flags
            counts.update(flags)
    return counts


def _caption_lines(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _sid_from_path(path: Path) -> str:
    parts = path.parts
    if "ocr-mineru" in parts:
        return parts[parts.index("ocr-mineru") - 1]
    if "mineru" in parts:
        mineru_index = parts.index("mineru")
        if mineru_index >= 2 and parts[mineru_index - 2] == "text":
            return parts[mineru_index - 1]
    if "raw" in parts and parts.index("raw") + 1 < len(parts):
        return parts[parts.index("raw") + 1]
    return path.stem.removesuffix("_content_list")


def _chunk_start_page(path: Path) -> int:
    for part in reversed(path.parts):
        match = _CHUNK_START.search(part)
        if match:
            return int(match.group(1))
    raise ValueError(f"cannot determine chunk start page from {path}")


def absolute_pdf_page(chunk_start_page: int, page_idx: int) -> int:
    """MinerU page_idx is zero-based within a chunk; the result is 1-based."""
    return chunk_start_page + page_idx


def _source_files(source: str | Path, corpus_root: Path) -> tuple[str, list[Path]]:
    supplied = Path(source).expanduser()
    if supplied.exists():
        sid = _sid_from_path(supplied)
        if supplied.is_file():
            files = [supplied]
        else:
            files = sorted(supplied.rglob("*content_list.json"))
    else:
        sid = str(source)
        root = corpus_root / sid / "ocr-mineru"
        files = sorted(root.rglob("*content_list.json")) if root.is_dir() else []
    if not files:
        raise FileNotFoundError(f"no MinerU content-list JSON found for {source}")
    return sid, files


def _column_headers(
    header_rows: list[list[str]], numeric_columns: set[int], width: int
) -> list[str]:
    leaf_headers = header_rows[-1] if header_rows else []
    repeated_numeric_headers = Counter(
        leaf_headers[column].strip()
        for column in numeric_columns
        if column < len(leaf_headers) and leaf_headers[column].strip()
    )
    headers = []
    for column in range(width):
        parts = [row[column] for row in header_rows if column < len(row)]
        leaf = leaf_headers[column].strip() if column < len(leaf_headers) else ""
        group_header = " ".join(
            row[column].strip()
            for row in header_rows[:-1]
            if column < len(row) and row[column].strip()
        ).casefold()
        previous_leaf = (
            leaf_headers[column - 1].strip()
            if column > 0 and column - 1 < len(leaf_headers)
            else ""
        )
        if (
            column in numeric_columns
            and repeated_numeric_headers[leaf] > 1
            and column > 0
            and column - 1 < len(leaf_headers)
            and column - 1 not in numeric_columns
            and group_header in {"gas phase", "condensed phases"}
            and re.search(
                r"\b(?:solid|liquid|gas|vapor|vapour)\b",
                previous_leaf.casefold(),
            )
        ):
            # Paired species/value tables put the phase heading over the species
            # cell and repeat one generic value heading over each numeric cell.
            parts = [previous_leaf, leaf]
        headers.append(" ".join(parts).strip())
    return headers


def _render_table(
    item: Mapping[str, Any],
    *,
    table_number: int,
    source_file: Path,
    chunk_start_page: int,
    table_dir: Path,
) -> dict[str, Any]:
    grid = parse_table_html(str(item.get("table_body") or ""))
    header_count = _header_row_count(grid)
    numeric_columns = _numeric_columns(grid, header_count)
    flag_counts = _add_cell_flags(grid, header_count, numeric_columns)
    page_idx = int(item["page_idx"])
    pdf_page = absolute_pdf_page(chunk_start_page, page_idx)
    table_key = f"p{pdf_page:03d}-t{table_number:02d}"
    csv_path = table_dir / f"{table_key}.csv"
    footnotes = _caption_lines(
        item.get("table_footnote", item.get("footnotes", item.get("footnote")))
    )
    caption_lines = _caption_lines(item.get("table_caption"))
    header_rows = [
        [cell.text for cell in row] for row in grid[:header_count]
    ]
    column_headers = _column_headers(
        header_rows,
        numeric_columns,
        max((len(row) for row in grid), default=0),
    )

    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for row_index, row in enumerate(grid):
            row_type = "header" if row_index < header_count else "body"
            for column_index, cell in enumerate(row):
                writer.writerow(
                    {
                        "row": row_index + 1,
                        "column": column_index + 1,
                        "row_type": row_type,
                        "text": cell.text,
                        "value_text": cell.value_text,
                        "mark": cell.mark,
                        "numeric_column": int(column_index in numeric_columns),
                        "origin_row": (
                            "" if cell.origin_row is None else cell.origin_row + 1
                        ),
                        "origin_column": (
                            ""
                            if cell.origin_column is None
                            else cell.origin_column + 1
                        ),
                        "rowspan": cell.rowspan,
                        "colspan": cell.colspan,
                        "flags": json.dumps(cell.flags, ensure_ascii=False),
                    }
                )

    return {
        "table_key": table_key,
        "source_file": str(source_file),
        "source_table_number": table_number,
        "page_idx": page_idx,
        "pdf_page": pdf_page,
        "caption": "\n".join(caption_lines),
        "footnotes": footnotes,
        "header_rows": header_rows,
        "body_rows": max(0, len(grid) - header_count),
        "columns": [
            {
                "column": column + 1,
                "header": column_headers[column],
                "numeric": column in numeric_columns,
            }
            for column in range(max((len(row) for row in grid), default=0))
        ],
        "csv": csv_path.name,
        "flag_counts": dict(sorted(flag_counts.items())),
    }


def convert_source(
    source: str | Path,
    out_dir: str | Path,
    *,
    corpus_root: str | Path = _DEFAULT_CORPUS_ROOT,
) -> Path:
    sid, content_files = _source_files(source, Path(corpus_root).expanduser())
    table_dir = Path(out_dir).expanduser() / "tables" / sid
    table_dir.mkdir(parents=True, exist_ok=True)
    tables: list[dict[str, Any]] = []
    for content_file in content_files:
        chunk_start_page = _chunk_start_page(content_file)
        content = json.loads(content_file.read_text(encoding="utf-8"))
        if not isinstance(content, list):
            raise ValueError(f"expected a list of content blocks in {content_file}")
        for item in content:
            if not isinstance(item, Mapping) or item.get("type") != "table":
                continue
            table_number = len(tables) + 1
            tables.append(
                _render_table(
                    item,
                    table_number=table_number,
                    source_file=content_file,
                    chunk_start_page=chunk_start_page,
                    table_dir=table_dir,
                )
            )

    index_path = table_dir / "index.yaml"
    index_path.write_text(
        yaml.safe_dump(
            {
                "source_id": sid,
                "source_files": [str(path) for path in content_files],
                "tables": tables,
            },
            sort_keys=False,
            allow_unicode=True,
            width=120,
        ),
        encoding="utf-8",
    )
    return index_path


def _numeric_points(extract: Mapping[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    species = extract.get("species") or {}
    for species_name, species_data in species.items():
        for observation in species_data.get("observations") or []:
            values = observation.get("values") or {}
            value_fields = (
                "value",
                "pressure_atm",
                "pressure_bar",
                "pressure_Pa",
                "activity",
                "gamma",
                "T_m_K",
            )
            stored_points = values.get("points") or []
            if stored_points:
                entries = [
                    (point_index, point, True)
                    for point_index, point in enumerate(stored_points)
                ]
            else:
                scalar_field = next(
                    (
                        field
                        for field in value_fields
                        if values.get(field) is not None
                    ),
                    None,
                )
                entries = (
                    [(None, {scalar_field: values[scalar_field]}, False)]
                    if scalar_field is not None
                    else []
                )
            for point_index, point, is_point in entries:
                value_field = next(
                    (
                        field
                        for field in value_fields
                        if point.get(field) is not None
                    ),
                    None,
                )
                if value_field is None:
                    continue
                value = point[value_field]
                if isinstance(value, bool) or value is None:
                    continue
                number = _number_decimal(str(value))
                if number is None:
                    continue
                locator = point.get("locator") or observation.get("locator") or {}
                observation_id = observation.get("observation_id", "")
                cell_coordinates = re.search(
                    r"_p\d+_t\d+_r(?P<row>\d+)_c(?P<column>\d+)$",
                    str(observation_id),
                )
                pdf_page = locator.get("pdf_page")
                if pdf_page is None and locator.get("pdf_page_index") is not None:
                    pdf_page = int(locator["pdf_page_index"]) + 1
                table_row_number = point.get(
                    "table_row_number",
                    locator.get("table_row_number", observation.get("table_row_number")),
                )
                if table_row_number is None and cell_coordinates:
                    table_row_number = int(cell_coordinates.group("row"))
                table_column_number = point.get(
                    "table_column_number",
                    locator.get(
                        "table_column_number", observation.get("table_column_number")
                    ),
                )
                if table_column_number is None and cell_coordinates:
                    table_column_number = int(cell_coordinates.group("column"))
                points.append(
                    {
                        "species": species_name,
                        "observation_id": observation_id,
                        "point_index": point_index,
                        "path": f"species.{species_name}.observations[{observation_id}]"
                        + (
                            f".values.points[{point_index}].{value_field}"
                            if is_point
                            else f".values.{value_field}"
                        ),
                        "pdf_page": pdf_page,
                        "table_number": (
                            str(int(locator["table"]))
                            if locator.get("table") is not None
                            and str(locator["table"]).isdecimal()
                            else None
                        ),
                        "phase": point.get(
                            "phase", locator.get("phase", observation.get("phase"))
                        ),
                        "table_row_number": table_row_number,
                        "table_column_number": table_column_number,
                        "table_phase_column": point.get(
                            "table_phase_column",
                            locator.get(
                                "table_phase_column",
                                observation.get("table_phase_column"),
                            ),
                        ),
                        "value": number,
                    }
                )
    return points


def _phase_matches_column(phase: Any, column_header: str) -> bool:
    if phase is None:
        return True
    token = str(phase).strip().casefold()
    if token == "cr":
        token = "solid"
    if token not in {"gas", "liquid", "solid"}:
        return False
    # A column with no phase qualifier cannot contradict the row phase.
    header_phases = {
        candidate
        for candidate in ("gas", "liquid", "solid")
        if re.search(rf"(?<!\w){candidate}(?!\w)", column_header, re.I)
    }
    return not header_phases or token in header_phases


def compare_extract_to_draft(
    extract: Mapping[str, Any],
    draft_index_path: str | Path,
    mapping: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Compare stored numeric points to draft cells, applying mapped scale factors."""
    index_path = Path(draft_index_path)
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    tables = index.get("tables") or []
    indexed_tables = {
        (
            int(table["pdf_page"]),
            str(int(table["table_key"].rsplit("-t", 1)[1])),
        ): table
        for table in tables
    }
    scale_factors: dict[tuple[int, str, int], int] = {}
    if mapping is not None:
        mapping = _require_mapping(mapping, "mapping")
        _unknown_keys(mapping, _DOCUMENT_KEYS, "mapping")
        for mapped_table in mapping.get("tables") or []:
            page = int(mapped_table["page"])
            table_number = str(int(mapped_table["table_index"]))
            draft_table = indexed_tables.get((page, table_number))
            if draft_table is None:
                continue
            for measurement in _measurement_columns(mapped_table):
                raw_scale = measurement.get("scale_factor")
                if raw_scale is None:
                    continue
                column = int(measurement["column"])
                header_cells = [
                    str(row[column - 1])
                    for row in draft_table.get("header_rows") or []
                    if column <= len(row)
                ]
                _, exponent = _scale_factor_spec(raw_scale, column, header_cells)
                scale_factors[(page, table_number, column)] = exponent

    draft_by_page: dict[
        int, list[tuple[dict[str, Any], Decimal, int]]
    ] = defaultdict(list)
    draft_by_table: dict[
        tuple[int, str], list[tuple[dict[str, Any], Decimal, int]]
    ] = defaultdict(list)
    for table in tables:
        page = int(table["pdf_page"])
        table_number = str(int(table["table_key"].rsplit("-t", 1)[1]))
        column_headers = {
            int(column["column"]): str(column.get("header") or "")
            for column in table.get("columns") or []
        }
        csv_path = index_path.parent / table["csv"]
        with csv_path.open(encoding="utf-8", newline="") as stream:
            for cell in csv.DictReader(stream):
                if (
                    not cell["origin_row"]
                    or not cell["origin_column"]
                    or int(cell["row"]) != int(cell["origin_row"])
                    or int(cell["column"]) != int(cell["origin_column"])
                ):
                    continue
                number = _number_decimal(cell["value_text"])
                if number is not None:
                    column = int(cell["column"])
                    candidate = {
                        "value": float(number),
                        "text": cell["text"],
                        "table_key": table["table_key"],
                        "row": int(cell["row"]),
                        "column": column,
                        "column_header": column_headers.get(column, ""),
                        "flags": json.loads(cell["flags"] or "[]"),
                    }
                    scale_exponent = scale_factors.get(
                        (page, table_number, column), 0
                    )
                    draft_by_page[page].append((candidate, number, scale_exponent))
                    draft_by_table[(page, table_number)].append(
                        (candidate, number, scale_exponent)
                    )

    results: list[dict[str, Any]] = []
    consumed: dict[tuple[int | None, str | None], set[int]] = defaultdict(set)
    for point in _numeric_points(extract):
        page = point["pdf_page"]
        table_number = point["table_number"]
        location = (page, table_number)
        if page is None:
            indexed_candidates = []
        elif table_number is None:
            indexed_candidates = draft_by_page.get(page, [])
        else:
            indexed_candidates = draft_by_table.get((page, table_number), [])
        point_value = Decimal(str(point["value"]))
        has_location = any(
            point.get(key) is not None
            for key in (
                "table_number",
                "table_row_number",
                "table_column_number",
                "phase",
                "table_phase_column",
            )
        )
        located_candidates = [
            (index, candidate, value, exponent)
            for index, (candidate, value, exponent) in enumerate(indexed_candidates)
            if index not in consumed[location]
            and (
                point.get("table_row_number") is None
                or candidate["row"] == int(point["table_row_number"])
            )
            and (
                point.get("table_phase_column") is None
                or candidate["column"] == int(point["table_phase_column"])
            )
            and (
                point.get("table_column_number") is None
                or candidate["column"] == int(point["table_column_number"])
            )
            and _phase_matches_column(point.get("phase"), candidate["column_header"])
        ]
        mapped_column = point.get("table_column_number")
        if mapped_column is None:
            mapped_column = point.get("table_phase_column")
        needs_mapping = mapping is None and mapped_column is not None and any(
            candidate["column"] == int(mapped_column)
            and _HEADER_POWER_OF_TEN.search(candidate["column_header"])
            for _, candidate, _, _ in located_candidates
        )
        matching_candidates = [
            (index, candidate)
            for index, candidate, value, exponent in located_candidates
            if point_value == value.scaleb(exponent)
        ]
        matched_index, matched = (
            matching_candidates[0] if matching_candidates else (None, None)
        )
        if needs_mapping:
            status = "needs_mapping"
            matched = None
        elif matched is not None:
            consumed[location].add(matched_index)
            status = "matched" if has_location else "presence-only"
        elif indexed_candidates:
            status = "mismatch"
        else:
            status = "no-match"
        results.append(
            {
                **point,
                "status": status,
                "matched_cell": matched,
                "draft_candidate_count": len(indexed_candidates),
            }
        )
    return results


# Per-cell render traps. mineru_table_qa imports _has_letter_for_digit,
# _DROPPED_PM_VALUE, _RUN_TOGETHER, and confirm_dropped_plusminus. QA still
# scores a table only after two hits (_MINIMUM_SIGNAL_HITS); that gate stays
# in QA, and this module does not import QA. Stacked-values stays here: QA's
# multiple-quantity predicate also exempts range headers and requires a
# numeric column, so it is not this check. Comma traps reuse this module's
# cell flags rather than a second detector.
_LETTER_FOR_DIGIT = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<mantissa>[+\-−]?[0-9ILlOoSs]+(?:\.[0-9ILlOoSs]+)?)"
    r"\s*(?:×|\\times|x)\s*"
    r"(?P<scale>[0-9ILlOoSs]+(?:\s*\^\s*\{?[+\-−]?[0-9ILlOoSs]+\}?)?)?",
    re.ASCII,
)
_RUN_TOGETHER = re.compile(r"\d+\.\d+\.\d")
_FUSED_SUPERSCRIPT_EXPONENT = re.compile(r"(?<![A-Za-z0-9])10[-−]\d+(?!\w)")
_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])[+\-−–—]?(?:\d+(?:[.,]\d+)?|\.\d+)"
)
_LATEX_OR_CARET = re.compile(r"\\times|\\cdot|\^")
_UNCERTAINTY_MARK = re.compile(r"±|\+\s*/\s*[-−]")
_DROPPED_PM_VALUE = re.compile(r"(?<!\d)(\d{4})\.(\d{2})(?!\d)")
_DASH_TEXT = frozenset({"-", "–", "—", "−"})
_TEMPERATURE_FIELDS = {"K": "T_K", "C": "T_C", "°C": "T_C"}
_TRAP_ORDER = (
    "fused_superscript",
    "run_together",
    "comma_decimal_ambiguous",
    "letter_for_digit",
    "stacked_values",
    "dropped_plusminus",
    "numeric_header_row",
)
_DOCUMENT_KEYS = frozenset(
    {
        "source_id",
        "source",
        "extraction",
        "review_status",
        "fidelity_samples",
        "tables",
    }
)
_TABLE_KEYS = frozenset(
    {
        "page",
        "table_index",
        "type",
        "quantity",
        "evidence_class",
        "unit",
        "phase",
        "per",
        "rows",
        "row_predicate",
        "temperature",
        "values",
        "value_pairs",
        "species_form",
        "conditions",
        "uncertainty",
        "experiment",
        "attribution",
        "published_page",
        "regime",
        "condensed_form",
        "render_verified",
    }
)
_VALUE_KEYS = frozenset(
    {
        "column",
        "species",
        "phase",
        "evidence_class",
        "unit",
        "quantity",
        "uncertainty",
        "attribution",
        "scale_factor",
    }
)
_PAIR_KEYS = frozenset(
    {
        "species_column",
        "value_column",
        "phase",
        "evidence_class",
        "unit",
        "quantity",
        "uncertainty",
        "attribution",
    }
)
_PRESSURE_FIELDS = {
    ("partial_pressure", "atm"): "pressure_atm",
    ("partial_pressure", "bar"): "pressure_bar",
    ("partial_pressure", "Pa"): "pressure_Pa",
}


class _Printed(str):
    """Printed text that must reload as a string, not as a number."""


class _EmitDumper(yaml.SafeDumper):
    pass


def _represent_printed(dumper: yaml.SafeDumper, data: _Printed) -> yaml.Node:
    text = str(data)
    style = '"' if ("\n" in text or "'" in text) else "'"
    return dumper.represent_scalar("tag:yaml.org,2002:str", text, style=style)


_EmitDumper.add_representer(_Printed, _represent_printed)


def _printed(text: str) -> _Printed:
    return _Printed(text)


def _dump_fragment(document: Mapping[str, Any]) -> str:
    return yaml.dump(
        document,
        Dumper=_EmitDumper,
        sort_keys=False,
        allow_unicode=True,
        width=10000,
        default_flow_style=False,
    )


def _unknown_keys(payload: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"{label} has unknown keys {unknown}")


def _require_mapping(payload: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return payload


def _scale_factor_spec(
    raw_scale: Any, column: int, header_cells: list[str]
) -> tuple[str, int]:
    scale = _require_mapping(raw_scale, f"scale_factor for column {column}")
    _unknown_keys(
        scale,
        frozenset({"header", "exponent", "direction"}),
        f"scale_factor for column {column}",
    )
    if not {"header", "exponent", "direction"}.issubset(scale):
        raise ValueError(
            f"scale_factor for column {column} needs header, exponent, and direction"
        )
    header = scale["header"]
    exponent = scale["exponent"]
    direction = str(scale["direction"])
    if not isinstance(header, str) or not header:
        raise ValueError(f"scale_factor.header for column {column} must be a string")
    if isinstance(exponent, bool) or not isinstance(exponent, int):
        raise ValueError(f"scale_factor.exponent for column {column} must be an integer")
    if header not in header_cells:
        raise ValueError(
            f"scale_factor.header for column {column} is not an exact MinerU header cell"
        )
    printed_powers = [
        int(match.group(1).replace("−", "-"))
        for match in _HEADER_POWER_OF_TEN.finditer(header)
    ]
    if len(printed_powers) != 1:
        raise ValueError(
            f"scale_factor.header for column {column} must contain exactly one "
            "parseable power of ten"
        )
    if printed_powers[0] != exponent:
        raise ValueError(
            f"scale_factor.exponent {exponent} does not match printed header "
            f"power {printed_powers[0]} in column {column}"
        )
    if direction == "quantity_times_factor_equals_cell":
        stored_exponent = -exponent
    elif direction == "cell_times_factor_equals_quantity":
        stored_exponent = exponent
    else:
        raise ValueError(
            f"scale_factor.direction for column {column} must be "
            "quantity_times_factor_equals_cell or cell_times_factor_equals_quantity"
        )
    return header, stored_exponent


def resolve_draft_index(draft_dir: str | Path, source_id: str | None) -> Path:
    """Return ``tables/<source>/index.yaml`` for a draft directory or that file."""

    supplied = Path(draft_dir).expanduser()
    if supplied.is_file() and supplied.name == "index.yaml":
        return supplied
    direct = supplied / "index.yaml"
    if direct.is_file():
        return direct
    tables = supplied / "tables"
    candidates = sorted(tables.glob("*/index.yaml")) if tables.is_dir() else []
    if source_id:
        matched = [path for path in candidates if path.parent.name == source_id]
        if len(matched) == 1:
            return matched[0]
        if matched:
            raise ValueError(f"multiple draft indexes for {source_id} under {supplied}")
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"no draft index under {supplied}")
    raise ValueError(
        f"multiple draft indexes under {supplied}; pass tables/<source_id> or set source_id"
    )


def _table_index_number(table: Mapping[str, Any]) -> int:
    return int(str(table["table_key"]).rsplit("-t", 1)[1])


def _is_pdftotext_only(
    table: Mapping[str, Any], index: Mapping[str, Any], csv_path: Path
) -> str | None:
    kind = str(
        table.get("source_kind")
        or table.get("origin")
        or index.get("source_kind")
        or index.get("origin")
        or ""
    )
    normalized = kind.strip().lower().replace("_", "-")
    if normalized in {"pdftotext-layout", "pdftotext"}:
        return "pdftotext-layout is the only source"
    if Path(str(table.get("source_file") or "")).name == "pdftotext-layout.txt":
        return "pdftotext-layout is the only source"
    if not csv_path.is_file():
        return "draft cells have no MinerU CSV"
    with csv_path.open(encoding="utf-8") as stream:
        header = stream.readline()
    fields = [part.strip() for part in header.split(",")]
    if "flags" not in fields:
        return "draft cells have no MinerU flags"
    return None


def _load_cells(csv_path: Path) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    with csv_path.open(encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            flags = raw.get("flags") or "[]"
            raw["flags"] = json.loads(flags) if isinstance(flags, str) else list(flags)
            cells.append(raw)
    return cells


def _is_origin(cell: Mapping[str, Any]) -> bool:
    return bool(
        cell.get("origin_row")
        and cell.get("origin_column")
        and str(cell["row"]) == str(cell["origin_row"])
        and str(cell["column"]) == str(cell["origin_column"])
    )


def _blank_or_dash(text: str) -> bool:
    token = text.strip()
    if len(token) >= 2 and token[0] == "$" and token[-1] == "$":
        token = token[1:-1].strip()
    return token == "" or token in _DASH_TEXT


def printed_formula(text: str) -> str:
    """Unwrap a printed species cell to its formula letters and digits.

    ``${\\mathrm{{Al}}}_{2}{\\mathrm{O}}_{3}$`` becomes ``Al2O3``. This strips
    math wrappers only; it does not interpret chemistry or drop ``circ``.
    """

    token = text.strip()
    if len(token) >= 2 and token[0] == "$" and token[-1] == "$":
        token = token[1:-1].strip()
    token = re.sub(r"\\(?:mathrm|text|rm)\b", "", token)
    return re.sub(r"[{}_^\\\s]", "", token)


def _stacked_values(text: str) -> bool:
    if _LATEX_OR_CARET.search(text) or _UNCERTAINTY_MARK.search(text):
        return False
    # Keep shorter bare integers unflagged: they are common measurements, while
    # seven or more digits are a deliberate conservative cutoff for concatenation.
    if re.fullmatch(r"\d{7,}", text.strip()):
        return True
    if "\n" in text:
        numbered = [part for part in text.split("\n") if _NUMBER_TOKEN.search(part)]
        if len(numbered) >= 2:
            return True
    matches = list(_NUMBER_TOKEN.finditer(text))
    if len(matches) < 2:
        return False
    gap = text[matches[0].end() : matches[1].start()]
    return gap.strip() == "" and bool(re.search(r"\s", gap))


def _has_letter_for_digit(text: str) -> bool:
    return any(
        any(
            char in "ILlOoSs"
            for group in ("mantissa", "scale")
            for char in (match.group(group) or "")
        )
        for match in _LETTER_FOR_DIGIT.finditer(text)
    )


def confirm_dropped_plusminus(text: str, page_text: str | None) -> str | None:
    """Return the pdftotext span that shows a plus-minus the cell dropped.

    ``None`` when the cell does not match ``_DROPPED_PM_VALUE`` or the page
    text does not confirm ``±`` between those parts. QA records the span;
    emit treats it as a yes or no.
    """

    if not page_text:
        return None
    match = _DROPPED_PM_VALUE.search(text)
    if match is None:
        return None
    pattern = re.compile(
        rf"(?<!\d){re.escape(match.group(1))}\s*(?:±|\uf0b1|\+\s*/\s*[-−])\s*"
        rf"{re.escape(match.group(2))}(?!\d)"
    )
    found = pattern.search(page_text)
    return found.group() if found else None


def _dropped_plusminus(text: str, page_text: str | None) -> bool:
    return confirm_dropped_plusminus(text, page_text) is not None


def _render_traps(cell: Mapping[str, Any], page_text: str | None) -> list[str]:
    text = str(cell.get("text") or "")
    value_text = str(cell.get("value_text") or "")
    flags = set(cell.get("flags") or [])
    found: list[str] = []
    if (
        ("^" in value_text and "^" not in text)
        or _FUSED_SUPERSCRIPT_EXPONENT.search(text)
    ):
        found.append("fused_superscript")
    if _RUN_TOGETHER.search(text):
        found.append("run_together")
    if flags.intersection({"comma_decimal_suspect", "ambiguous_comma_separator"}):
        found.append("comma_decimal_ambiguous")
    if _has_letter_for_digit(text):
        found.append("letter_for_digit")
    if _stacked_values(text):
        found.append("stacked_values")
    if _dropped_plusminus(text, page_text):
        found.append("dropped_plusminus")
    if (
        cell.get("row_type") == "header"
        and _number_decimal(value_text or text) is not None
    ):
        found.append("numeric_header_row")
    return [name for name in _TRAP_ORDER if name in found]


def _page_text_pages(
    explicit: str | Path | None,
    draft_dir: Path,
    index: Mapping[str, Any],
    index_path: Path,
) -> list[str] | None:
    path: Path | None = None
    if explicit:
        path = Path(explicit).expanduser()
    elif index.get("pdftotext_layout"):
        raw = Path(str(index["pdftotext_layout"])).expanduser()
        path = raw if raw.is_absolute() else index_path.parent / raw
    else:
        for candidate in (
            draft_dir / "pdftotext-layout.txt",
            index_path.parent / "pdftotext-layout.txt",
        ):
            if candidate.is_file():
                path = candidate
                break
    if path is None or not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace").split("\f")


def _page_text_for(pages: list[str] | None, pdf_page: int) -> str | None:
    if pages is None or pdf_page < 1 or pdf_page > len(pages):
        return None
    return pages[pdf_page - 1]


def _value_field(quantity: str, unit: str) -> str:
    if quantity == "partial_pressure":
        field = _PRESSURE_FIELDS.get((quantity, unit))
        if field is None:
            raise ValueError(
                f"partial_pressure unit {unit!r} is not atm, bar, or Pa"
            )
        return field
    if quantity == "activity":
        return "activity"
    if quantity == "activity_coefficient":
        return "gamma"
    return "value"


def _uncertainty_for(table: Mapping[str, Any], column: int) -> dict[str, Any] | None:
    raw = table.get("uncertainty")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("uncertainty must be a mapping")
    columns = raw.get("columns") or {}
    if not isinstance(columns, Mapping):
        raise ValueError("uncertainty.columns must be a mapping")
    override = columns.get(column, columns.get(str(column)))
    if override is not None:
        if not isinstance(override, Mapping):
            raise ValueError(f"uncertainty.columns.{column} must be a mapping")
        return copy.deepcopy(dict(override))
    base = {key: value for key, value in raw.items() if key != "columns"}
    return copy.deepcopy(base) if base else None


def _selected_body_rows(
    table: Mapping[str, Any], cells: list[dict[str, Any]]
) -> list[int]:
    if "rows" in table and "row_predicate" in table:
        raise ValueError("rows and row_predicate are alternatives; set only one")
    body_rows = sorted(
        {int(cell["row"]) for cell in cells if cell["row_type"] == "body" and _is_origin(cell)}
    )
    spec = table.get("rows", "all")
    if spec != "all":
        if not isinstance(spec, list) or not spec:
            raise ValueError("rows must be 'all' or a non-empty list of CSV row numbers")
        wanted = {int(row) for row in spec}
        known = {int(cell["row"]) for cell in cells}
        missing = sorted(wanted - known)
        if missing:
            raise ValueError(f"rows not in the draft: {missing}")
        headers = sorted(
            row
            for row in wanted
            if any(int(cell["row"]) == row and cell["row_type"] != "body" for cell in cells)
        )
        if headers:
            raise ValueError(f"rows are not body rows: {headers}")
        body_rows = [row for row in body_rows if row in wanted]
    predicate = table.get("row_predicate")
    if predicate is not None:
        predicate = _require_mapping(predicate, "row_predicate")
        _unknown_keys(predicate, frozenset({"column", "equals", "matches"}), "row_predicate")
        if "equals" in predicate and "matches" in predicate:
            raise ValueError("row_predicate equals and matches are alternatives")
        if "equals" not in predicate and "matches" not in predicate:
            raise ValueError("row_predicate needs equals or matches")
        column = int(predicate["column"])
        if "equals" in predicate:
            expected = str(predicate["equals"])
            body_rows = [
                row
                for row in body_rows
                if _origin_text(cells, row, column).strip() == expected
            ]
        else:
            pattern = re.compile(str(predicate["matches"]))
            body_rows = [
                row
                for row in body_rows
                if pattern.search(_origin_text(cells, row, column).strip())
            ]
    return body_rows


def _origin_text(cells: list[dict[str, Any]], row: int, column: int) -> str:
    for cell in cells:
        if int(cell["row"]) == row and int(cell["column"]) == column and _is_origin(cell):
            return str(cell["text"])
    return ""


def _locator(
    *,
    page: int,
    table_index: int,
    row_label: str,
    column_header: str,
    published_page: Any,
) -> dict[str, Any]:
    locator: dict[str, Any] = {
        "page": page,
        "pdf_page_index": page - 1,
        "table": str(table_index),
        "row_label": _printed(row_label),
        "column_header": _printed(column_header),
    }
    if published_page is not None:
        locator["published_page"] = published_page
    return locator


def _temperature_spec(table: Mapping[str, Any]) -> Mapping[str, Any] | None:
    raw = table.get("temperature")
    if raw is None:
        return None
    spec = _require_mapping(raw, "temperature")
    _unknown_keys(
        spec,
        frozenset({"column", "unit", "as_printed", "column_header", "locator"}),
        "temperature",
    )
    if "column" in spec and "as_printed" in spec:
        raise ValueError("temperature column and as_printed are alternatives")
    if "column" not in spec and "as_printed" not in spec:
        raise ValueError("temperature needs column or as_printed")
    unit = str(spec.get("unit") or "")
    if unit not in _TEMPERATURE_FIELDS:
        raise ValueError("temperature.unit must be K or C")
    return spec


def _observation_id(source_id: str, page: int, table_index: int, row: int, column: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", source_id).strip("_")
    return f"{slug}_p{page:03d}_t{table_index:02d}_r{row:02d}_c{column:02d}"


def _copy_conditions(table: Mapping[str, Any]) -> dict[str, Any]:
    conditions = table.get("conditions") or []
    if not isinstance(conditions, list):
        raise ValueError("conditions must be a list")
    copied: dict[str, Any] = {}
    for index, raw in enumerate(conditions):
        condition = _require_mapping(raw, f"conditions[{index}]")
        _unknown_keys(condition, frozenset({"key", "state", "locator"}), f"conditions[{index}]")
        key = str(condition["key"])
        if key == "temperature_K":
            raise ValueError(
                "temperature_K is written from the temperature axis; do not repeat it in conditions"
            )
        if key in copied:
            raise ValueError(f"repeated condition key {key}")
        copied[key] = {
            "state": copy.deepcopy(condition["state"]),
            "locator": copy.deepcopy(condition["locator"]),
        }
    return copied


def _render_verified_spellings(table: Mapping[str, Any]) -> dict[tuple[int, int], str]:
    raw = table.get("render_verified") or []
    if not isinstance(raw, list):
        raise ValueError("render_verified must be a list")
    verified: dict[tuple[int, int], str] = {}
    for index, item in enumerate(raw):
        entry = _require_mapping(item, f"render_verified[{index}]")
        _unknown_keys(
            entry,
            frozenset({"row", "column", "text"}),
            f"render_verified[{index}]",
        )
        if "row" not in entry or "column" not in entry or "text" not in entry:
            raise ValueError(f"render_verified[{index}] needs row, column, and text")
        key = (int(entry["row"]), int(entry["column"]))
        if key in verified:
            raise ValueError(f"repeated render_verified cell {key}")
        verified[key] = str(entry["text"])
    return verified


def _measurement_columns(table: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = table.get("values")
    pairs = table.get("value_pairs")
    if values is not None and pairs is not None:
        raise ValueError("values and value_pairs are alternatives; set only one")
    if values is None and pairs is None:
        raise ValueError("table needs values or value_pairs")
    if table.get("species_form") not in (None, "printed_formula"):
        raise ValueError("species_form must be printed_formula")
    columns: list[dict[str, Any]] = []
    if values is not None:
        if not isinstance(values, list) or not values:
            raise ValueError("values must be a non-empty list")
        for index, raw in enumerate(values):
            entry = _require_mapping(raw, f"values[{index}]")
            _unknown_keys(entry, _VALUE_KEYS, f"values[{index}]")
            if "column" not in entry or "species" not in entry:
                raise ValueError(f"values[{index}] needs column and species")
            columns.append({"kind": "fixed", **dict(entry)})
        return columns
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("value_pairs must be a non-empty list")
    for index, raw in enumerate(pairs):
        entry = _require_mapping(raw, f"value_pairs[{index}]")
        _unknown_keys(entry, _PAIR_KEYS, f"value_pairs[{index}]")
        if "species_column" not in entry or "value_column" not in entry:
            raise ValueError(f"value_pairs[{index}] needs species_column and value_column")
        columns.append({"kind": "pair", **dict(entry)})
    return columns


def _emit_table(
    *,
    spec: Mapping[str, Any],
    draft_table: Mapping[str, Any],
    cells: list[dict[str, Any]],
    source_id: str,
    page_text: str | None,
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
    _unknown_keys(spec, _TABLE_KEYS, f"table p{spec.get('page')}-t{spec.get('table_index')}")
    for key in ("page", "table_index", "type", "quantity", "unit"):
        if spec.get(key) in (None, ""):
            raise ValueError(f"table mapping needs {key}")
    page = int(spec["page"])
    table_index = int(spec["table_index"])
    if int(draft_table["pdf_page"]) != page or _table_index_number(draft_table) != table_index:
        raise ValueError(
            f"draft table {draft_table['table_key']} does not match page {page} index {table_index}"
        )
    headers = {
        int(column["column"]): str(column.get("header") or "").strip()
        for column in draft_table.get("columns") or []
    }
    origin = {
        (int(cell["row"]), int(cell["column"])): cell
        for cell in cells
        if _is_origin(cell)
    }
    measurements = _measurement_columns(spec)
    scale_factors: dict[int, tuple[str, int]] = {}
    for measurement in measurements:
        raw_scale = measurement.get("scale_factor")
        if raw_scale is None:
            continue
        column = int(
            measurement["column"]
            if measurement["kind"] == "fixed"
            else measurement["value_column"]
        )
        header_cells = [
            str(row[column - 1])
            for row in draft_table.get("header_rows") or []
            if column <= len(row)
        ]
        scale_factors[column] = _scale_factor_spec(
            raw_scale, column, header_cells
        )
    temperature = _temperature_spec(spec)
    rows = _selected_body_rows(spec, cells)
    measurement_columns = {
        int(measurement["column"])
        if measurement["kind"] == "fixed"
        else int(measurement["value_column"])
        for measurement in measurements
    }
    numeric_header_rows = sorted(
        {
            int(cell["row"])
            for cell in cells
            if cell["row_type"] == "header"
            and int(cell["column"]) in measurement_columns
            and _is_origin(cell)
            and _number_decimal(str(cell.get("value_text") or cell["text"]))
            is not None
        }
    )
    if numeric_header_rows:
        if spec.get("rows", "all") != "all":
            raise ValueError(
                f"numeric header rows contain mapped values and cannot be skipped: "
                f"{numeric_header_rows}"
            )
        rows = sorted(set(rows) | set(numeric_header_rows))
    point_conditions = _copy_conditions(spec)
    published_page = spec.get("published_page")
    verified_spellings = _render_verified_spellings(spec)
    used_verified: set[tuple[int, int]] = set()
    emitted: list[tuple[str, dict[str, Any]]] = []
    skipped = 0
    for row in rows:
        temperature_text: str | None = None
        temperature_locator: dict[str, Any] | None = None
        if temperature is not None and "column" in temperature:
            temperature_cell = origin.get((row, int(temperature["column"])))
            if temperature_cell is None:
                raise ValueError(
                    f"temperature column {temperature['column']} is missing on row {row}"
                )
            temperature_text = temperature_cell["text"].strip()
            temperature_locator = _locator(
                page=page,
                table_index=table_index,
                row_label=temperature_text,
                column_header=headers.get(int(temperature["column"]), ""),
                published_page=published_page,
            )
        elif temperature is not None:
            temperature_text = str(temperature["as_printed"]).strip()
            supplied = temperature.get("locator")
            if supplied is not None:
                temperature_locator = copy.deepcopy(dict(supplied))
            else:
                temperature_locator = _locator(
                    page=page,
                    table_index=table_index,
                    row_label=temperature_text,
                    column_header=str(temperature.get("column_header") or ""),
                    published_page=published_page,
                )
        for measurement in measurements:
            if measurement["kind"] == "fixed":
                column = int(measurement["column"])
                species = str(measurement["species"])
                default_label_column = (
                    int(temperature["column"])
                    if temperature is not None and "column" in temperature
                    else None
                )
            else:
                column = int(measurement["value_column"])
                species_cell = origin.get((row, int(measurement["species_column"])))
                species_text = "" if species_cell is None else species_cell["text"]
                species = printed_formula(species_text)
                default_label_column = int(measurement["species_column"])
            if column not in headers and not any(key[1] == column for key in origin):
                raise ValueError(f"column {column} is not in draft table p{page:03d}-t{table_index:02d}")
            cell = origin.get((row, column))
            if cell is None or _blank_or_dash(str(cell["text"])):
                skipped += 1
                continue
            if (
                row in numeric_header_rows
                and cell["row_type"] == "header"
                and _number_decimal(str(cell.get("value_text") or cell["text"]))
                is None
            ):
                raise ValueError(
                    f"numeric header row {row} has nonnumeric mapped value "
                    f"in column {column}"
                )
            if measurement["kind"] == "pair" and not species:
                raise ValueError(f"row {row} column {column} has an empty species cell")
            quantity = str(measurement.get("quantity") or spec["quantity"])
            unit = str(measurement.get("unit") or spec["unit"])
            evidence = measurement.get("evidence_class", spec.get("evidence_class"))
            if not evidence:
                raise ValueError(
                    f"column {column} needs evidence_class on the column or the table"
                )
            phase = measurement.get("phase", spec.get("phase"))
            verbatim = _printed(str(cell["text"]).strip())
            value_field = _value_field(quantity, unit)
            values: dict[str, Any] = {
                "quantity": quantity,
                "method_class": evidence,
            }
            if temperature is not None and temperature_text is not None:
                values[_TEMPERATURE_FIELDS[str(temperature["unit"])]] = _printed(temperature_text)
            if value_field in values:
                raise ValueError(
                    f"quantity {quantity} {unit} would repeat {value_field}; refuse the unit twin"
                )
            values[value_field] = verbatim
            attribution = measurement.get("attribution", spec.get("attribution"))
            if attribution:
                values["attribution"] = attribution
            if default_label_column is None:
                row_label = str(row)
            else:
                row_label = _origin_text(cells, row, default_label_column).strip()
            locator = _locator(
                page=page,
                table_index=table_index,
                row_label=row_label,
                column_header=headers.get(column, ""),
                published_page=published_page,
            )
            observation: dict[str, Any] = {
                "observation_id": _observation_id(source_id, page, table_index, row, column),
                "type": spec["type"],
                "locator": locator,
                "units": unit,
                "mineru_flags": list(cell["flags"]),
                "values": values,
            }
            if phase not in (None, ""):
                observation["phase"] = phase
            for field_name in ("per", "experiment", "regime", "condensed_form"):
                if spec.get(field_name) not in (None, ""):
                    observation[field_name] = spec[field_name]
            row_conditions = copy.deepcopy(point_conditions)
            if (
                temperature is not None
                and temperature_text is not None
                and _TEMPERATURE_FIELDS[str(temperature["unit"])] == "T_K"
                and temperature_locator is not None
            ):
                row_conditions["temperature_K"] = {
                    "state": {
                        "tag": "value",
                        "value": {"kind": "point", "point": _printed(temperature_text)},
                    },
                    "locator": temperature_locator,
                }
            if row_conditions:
                observation["point_conditions"] = row_conditions
            uncertainty = measurement.get("uncertainty")
            if uncertainty is None:
                uncertainty = _uncertainty_for(spec, column)
            elif not isinstance(uncertainty, Mapping):
                raise ValueError(f"column {column} uncertainty must be a mapping")
            else:
                uncertainty = copy.deepcopy(dict(uncertainty))
            if uncertainty:
                observation["uncertainty"] = uncertainty
            traps = _render_traps(cell, page_text)
            if traps:
                spelling = verified_spellings.get((row, column))
                if spelling is None:
                    raise ValueError(
                        f"emit refuses p{page:03d}-t{table_index:02d} "
                        f"row {row} column {column}: needs_render_check {traps}; "
                        "supply render_verified text for that cell"
                    )
                observation["values"][value_field] = _printed(spelling)
                used_verified.add((row, column))
            scale = scale_factors.get(column)
            if scale is not None:
                # Printed header form: ` $p_{Ca}, \cdot 10^{7}$ ` means p_Ca × 10^7 = cell.
                # Algebra: p_Ca = cell × 10^-7.
                # Stolyarova 1996 cell `0.75` under that header stores `0.000000075 atm`.
                # Unit check: 10^-7 is dimensionless, so the table's atm unit remains atm.
                printed_cell = str(observation["values"][value_field]).strip()
                number = _number_decimal(printed_cell)
                if number is None:
                    raise ValueError(
                        f"scale_factor for row {row} column {column} needs a numeric cell"
                    )
                observation["values"][value_field] = _printed(
                    format(number.scaleb(scale[1]), "f")
                )
                locator["printed_cell"] = _printed(printed_cell)
                locator["scale_factor_header"] = _printed(scale[0])
            emitted.append((species, observation))
    unused = sorted(set(verified_spellings) - used_verified)
    if unused:
        raise ValueError(f"render_verified cells are not trapped: {unused}")
    return emitted, skipped


def emit_extract(
    draft_dir: str | Path,
    mapping: str | Path,
    out: str | Path,
    *,
    pdftotext: str | Path | None = None,
) -> dict[str, Any]:
    """Write a literature_extract.v1 fragment for ``mapping`` and return it."""

    mapping_path = Path(mapping).expanduser()
    spec = yaml.safe_load(mapping_path.read_text(encoding="utf-8"))
    spec = _require_mapping(spec, "mapping")
    _unknown_keys(spec, _DOCUMENT_KEYS, "mapping")
    source_id = spec.get("source_id")
    supplied = Path(draft_dir).expanduser()
    index_path = resolve_draft_index(supplied, str(source_id) if source_id else None)
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    if not isinstance(index, Mapping):
        raise ValueError(f"{index_path} is not a mapping")
    draft_source = str(index.get("source_id") or "")
    if source_id and draft_source and str(source_id) != draft_source:
        raise ValueError(
            f"mapping source_id {source_id} does not match draft source_id {draft_source}"
        )
    source_id = str(source_id or draft_source)
    if not source_id:
        raise ValueError("source_id is required on the mapping or the draft index")
    source = _require_mapping(spec.get("source"), "source")
    if not source.get("citation"):
        raise ValueError("source.citation is required")
    extraction = _require_mapping(spec.get("extraction"), "extraction")
    for key in ("method", "date", "worker"):
        if not extraction.get(key):
            raise ValueError(f"extraction.{key} is required")
    review_status = spec.get("review_status") or "draft"
    tables = spec.get("tables")
    if not isinstance(tables, list) or not tables:
        raise ValueError("tables must be a non-empty list")
    fidelity = spec.get("fidelity_samples")
    if fidelity is not None and not isinstance(fidelity, list):
        raise ValueError("fidelity_samples must be a list")
    draft_tables = {(_table_index_number(table), int(table["pdf_page"])): table for table in index.get("tables") or []}
    pages = _page_text_pages(pdftotext, supplied if supplied.is_dir() else index_path.parent, index, index_path)
    species: dict[str, dict[str, list[dict[str, Any]]]] = {}
    skip_rows: list[dict[str, Any]] = []
    for raw_table in tables:
        table_spec = _require_mapping(raw_table, "table")
        key = (int(table_spec["table_index"]), int(table_spec["page"]))
        draft_table = draft_tables.get(key)
        if draft_table is None:
            raise ValueError(f"no draft table page {key[1]} index {key[0]}")
        csv_path = index_path.parent / str(draft_table["csv"])
        reason = _is_pdftotext_only(draft_table, index, csv_path)
        if reason is not None:
            raise ValueError(
                f"emit refuses {draft_table['table_key']}: {reason}; "
                "transcribe the table or request MinerU"
            )
        cells = _load_cells(csv_path)
        emitted, skipped = _emit_table(
            spec=table_spec,
            draft_table=draft_table,
            cells=cells,
            source_id=source_id,
            page_text=_page_text_for(pages, int(draft_table["pdf_page"])),
        )
        for formula, observation in emitted:
            block = species.setdefault(formula, {"observations": []})
            block["observations"].append(observation)
        skip_rows.append(
            {
                "page": int(table_spec["page"]),
                "table_index": int(table_spec["table_index"]),
                "count": skipped,
            }
        )
    document: dict[str, Any] = {
        "schema_version": "literature_extract.v1",
        "source_id": source_id,
        "source": copy.deepcopy(dict(source)),
        "extraction": copy.deepcopy(dict(extraction)),
        "review_status": review_status,
        "skipped_empty_or_dash": {
            "total": sum(row["count"] for row in skip_rows),
            "tables": skip_rows,
        },
        "species": species,
    }
    if fidelity is not None:
        document["fidelity_samples"] = copy.deepcopy(fidelity)
    out_path = Path(out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_dump_fragment(document), encoding="utf-8")
    return document


def _emit_command(args: argparse.Namespace) -> int:
    document = emit_extract(
        args.draft_dir,
        args.mapping,
        args.out,
        pdftotext=args.pdftotext,
    )
    count = sum(
        len(block.get("observations") or []) for block in document["species"].values()
    )
    skipped = document["skipped_empty_or_dash"]["total"]
    print(f"{args.out}: {count} observations, skipped_empty_or_dash={skipped}")
    return 0


def _convert_command(args: argparse.Namespace) -> int:
    index = convert_source(args.source, args.out, corpus_root=args.corpus_root)
    document = yaml.safe_load(index.read_text(encoding="utf-8"))
    print(f"{index}: {len(document['tables'])} tables")
    return 0


def _compare_command(args: argparse.Namespace) -> int:
    extract = yaml.safe_load(Path(args.extract).read_text(encoding="utf-8"))
    mapping_path = getattr(args, "mapping", None)
    mapping = (
        yaml.safe_load(Path(mapping_path).read_text(encoding="utf-8"))
        if mapping_path
        else None
    )
    results = compare_extract_to_draft(extract, args.draft_index, mapping)
    fields = (
        "species",
        "observation_id",
        "point_index",
        "pdf_page",
        "value",
        "status",
        "matched_cell",
        "draft_candidate_count",
        "path",
    )
    writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(
        {
            key: (
                json.dumps(row[key], ensure_ascii=False)
                if key == "matched_cell"
                else row[key]
            )
            for key in fields
        }
        for row in results
    )
    counts = Counter(row["status"] for row in results)
    print(
        f"compare: matched={counts['matched']} no-match={counts['no-match']} "
        f"mismatch={counts['mismatch']} presence-only={counts['presence-only']} "
        f"needs_mapping={counts['needs_mapping']} "
        f"total={len(results)}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    convert = commands.add_parser("convert", help="convert MinerU tables to draft CSVs")
    convert.add_argument("source", help="a source id, content-list path, or directory")
    convert.add_argument("--out", required=True, help="draft output root")
    convert.add_argument(
        "--corpus-root",
        default=str(_DEFAULT_CORPUS_ROOT),
        help="raw corpus root used when source is a source id",
    )
    convert.set_defaults(run=_convert_command)
    compare = commands.add_parser("compare", help="compare stored extract points to draft")
    compare.add_argument("extract", help="extract YAML path")
    compare.add_argument("draft_index", help="tables/<source-id>/index.yaml")
    compare.add_argument(
        "--mapping",
        help=(
            "mapping YAML for scale_factor columns; without it, cells under "
            "power-of-ten headers report needs_mapping"
        ),
    )
    compare.set_defaults(run=_compare_command)
    emit = commands.add_parser(
        "emit",
        help="emit extract rows from a draft mapping",
        epilog=(
            "A value column may declare scale_factor: {header, exponent, direction}. "
            "header must be the exact MinerU header cell with one matching 10^n; "
            "exponent is that signed integer. direction is "
            "quantity_times_factor_equals_cell or cell_times_factor_equals_quantity. "
            "Scaling uses exact decimal arithmetic and does not convert units."
        ),
    )
    emit.add_argument("draft_dir", help="draft directory containing index.yaml, or its parent")
    emit.add_argument("mapping", help="per-source table mapping YAML")
    emit.add_argument("--out", required=True, help="extract fragment path")
    emit.add_argument(
        "--pdftotext",
        default=None,
        help="pdftotext -layout text; required to mark dropped plus-minus",
    )
    emit.set_defaults(run=_emit_command)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
