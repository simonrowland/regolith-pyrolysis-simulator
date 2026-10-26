"""Write loss-preserving CSV drafts from MinerU content-list tables.

This parser retains spans, header tags, and cell origins for draft auditing;
the Pankratz ingest parser intentionally returns plain cell text. They have
different outputs and neither can replace the other without changing ingest
or draft semantics.
"""

from __future__ import annotations

import argparse
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
_TABLE_LABEL_PREFIX = r"(?:tabelle|tableau|table|tab\.?)\s*(?:no\.?\s*)?"
_TABLE_LABEL_PREFIX_RE = re.compile(r"^" + _TABLE_LABEL_PREFIX, re.IGNORECASE)
_TABLE_CAPTION_LABEL = re.compile(
    r"^\s*(?:[#>*_`|~]+\s*)?"
    + _TABLE_LABEL_PREFIX
    + r"([IVXLCDM]+(?:\.\d+)*|\d+(?:\.\d+)*[A-Z]?|[A-Z](?:-\d+)?)\b",
    re.IGNORECASE,
)
_APPENDIX_HEADER_LABEL = re.compile(
    r"^\s*(?:[#>*_`|~]+\s*)?annexe\s+([IVXLCDM]+)\b", re.IGNORECASE
)
_DRAFT_TABLE_ID = re.compile(r"_p(?P<page>\d+)_t(?P<table>\d+)_r\d+_c\d+$", re.I)
_NUMBER = re.compile(
    r"^[+\-−–—]?(?:\d+(?:\.\d+)?|\.\d+)(?:\(\d+\))?(?:[eE][+\-]?\d+)?$"
)
_LATEX_SCIENTIFIC = re.compile(
    r"^([+\-−–—]?(?:\d+(?:\.\d+)?|\.\d+))\s*\\times\s*"
    r"10\^\{?([+\-−–—]?\d+)\}?$"
)
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


def _normalize_table_label(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    token = _TABLE_LABEL_PREFIX_RE.sub("", token, count=1)
    token = token.strip().rstrip(".,:;")
    return token.upper() if token else None


def _printed_table_label(caption: Any, text: Any = None) -> str | None:
    for value in (*_caption_lines(caption), *_caption_lines(text)):
        for line in str(value).splitlines():
            match = _TABLE_CAPTION_LABEL.match(line)
            if match:
                return _normalize_table_label(match.group(1))
    return None


def _appendix_header_label(text: Any) -> str | None:
    for value in _caption_lines(text):
        for line in str(value).splitlines():
            match = _APPENDIX_HEADER_LABEL.match(line)
            if match:
                return f"ANNEXE {match.group(1).upper()}"
    return None


def _caption_evidence(
    content: list[Any], chunk_start_page: int
) -> dict[int, set[str]]:
    """Return printed captions by PDF page, including next-page appendix tables."""
    captions_by_page: dict[int, set[str]] = defaultdict(set)
    for item in content:
        if not isinstance(item, Mapping) or item.get("page_idx") is None:
            continue
        page = absolute_pdf_page(chunk_start_page, int(item["page_idx"]))
        if item.get("type") == "table" and any(
            line.strip() for line in _caption_lines(item.get("table_caption"))
        ):
            captions_by_page.setdefault(page, set())
        label = _printed_table_label(item.get("table_caption"), item.get("text"))
        if label:
            captions_by_page[page].add(label)
        if item.get("type") == "header":
            appendix_label = _appendix_header_label(item.get("text"))
            if appendix_label:
                captions_by_page[page].add(appendix_label)
                captions_by_page[page + 1].add(appendix_label)
    return captions_by_page


def _published_page(item: Mapping[str, Any]) -> int | str | None:
    value = item.get("published_page")
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            page = int(value)
        except ValueError:
            return value if value else None
        return page if page > 0 else None
    return None


def _locator_page(locator: Mapping[str, Any]) -> int | None:
    for key in ("page", "pdf_page"):
        value = locator.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    value = locator.get("pdf_page_index")
    if value is not None:
        try:
            return int(value) + 1
        except (TypeError, ValueError):
            return None
    return None


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


def _render_table(
    item: Mapping[str, Any],
    *,
    block_ordinal: int,
    source_file: Path,
    chunk_start_page: int,
    table_dir: Path,
    caption_context: Any = None,
) -> dict[str, Any]:
    grid = parse_table_html(str(item.get("table_body") or ""))
    header_count = _header_row_count(grid)
    numeric_columns = _numeric_columns(grid, header_count)
    flag_counts = _add_cell_flags(grid, header_count, numeric_columns)
    page_idx = int(item["page_idx"])
    pdf_page = absolute_pdf_page(chunk_start_page, page_idx)
    table_key = f"p{pdf_page:03d}-t{block_ordinal:02d}"
    csv_path = table_dir / f"{table_key}.csv"
    footnotes = _caption_lines(
        item.get("table_footnote", item.get("footnotes", item.get("footnote")))
    )
    caption_lines = _caption_lines(item.get("table_caption"))
    text_lines = _caption_lines(item.get("text"))
    context_lines = _caption_lines(caption_context)
    if _printed_table_label(None, context_lines) is None:
        context_lines = []
    printed_label = _printed_table_label(
        caption_lines, [*text_lines, *context_lines]
    )
    locator = {"page": pdf_page, "table": printed_label or "unknown"}
    published_page = _published_page(item)
    if published_page is not None:
        locator["published_page"] = published_page
    header_rows = [
        [cell.text for cell in row] for row in grid[:header_count]
    ]

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
        "page_idx": page_idx,
        "pdf_page": pdf_page,
        "locator": locator,
        **(
            {
                "locator_todo": (
                    "TODO: extractor must fill the printed table label before extraction"
                )
            }
            if printed_label is None
            else {}
        ),
        "caption": "\n".join((*caption_lines, *text_lines, *context_lines)),
        "footnotes": footnotes,
        "header_rows": header_rows,
        "body_rows": max(0, len(grid) - header_count),
        "columns": [
            {
                "column": column + 1,
                "header": " ".join(row[column] for row in header_rows).strip(),
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
    caption_evidence: dict[int, set[str]] = defaultdict(set)
    for content_file in content_files:
        chunk_start_page = _chunk_start_page(content_file)
        content = json.loads(content_file.read_text(encoding="utf-8"))
        if not isinstance(content, list):
            raise ValueError(f"expected a list of content blocks in {content_file}")
        for page, labels in _caption_evidence(content, chunk_start_page).items():
            caption_evidence[page].update(labels)
        for item_index, item in enumerate(content):
            if not isinstance(item, Mapping) or item.get("type") != "table":
                continue
            caption_context = None
            for neighbor_index in (item_index - 1, item_index + 1):
                if neighbor_index < 0 or neighbor_index >= len(content):
                    continue
                neighbor = content[neighbor_index]
                if (
                    isinstance(neighbor, Mapping)
                    and neighbor.get("type") == "text"
                    and neighbor.get("page_idx") == item.get("page_idx")
                    and _printed_table_label(None, neighbor.get("text")) is not None
                ):
                    caption_context = neighbor.get("text")
                    break
            block_ordinal = len(tables) + 1
            tables.append(
                _render_table(
                    item,
                    block_ordinal=block_ordinal,
                    source_file=content_file,
                    chunk_start_page=chunk_start_page,
                    table_dir=table_dir,
                    caption_context=caption_context,
                )
            )

    index_path = table_dir / "index.yaml"
    index_path.write_text(
        yaml.safe_dump(
            {
                "source_id": sid,
                "source_files": [str(path) for path in content_files],
                "caption_evidence": [
                    {"page": page, "labels": sorted(labels)}
                    for page, labels in sorted(caption_evidence.items())
                ],
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
            for point_index, point in enumerate(values.get("points") or []):
                value_field = "value"
                value = point.get(value_field)
                if value is None and "pressure_atm" in point:
                    value_field = "pressure_atm"
                    value = point.get(value_field)
                if isinstance(value, bool) or value is None:
                    continue
                number = _number_decimal(str(value))
                if number is None:
                    continue
                locator = point.get("locator") or observation.get("locator") or {}
                pdf_page = _locator_page(locator)
                table_label = _normalize_table_label(locator.get("table"))
                if table_label == "UNKNOWN":
                    table_label = None
                observation_id = str(observation.get("observation_id", ""))
                draft_table = _DRAFT_TABLE_ID.search(observation_id)
                points.append(
                    {
                        "species": species_name,
                        "observation_id": observation_id,
                        "point_index": point_index,
                        "path": (
                            f"species.{species_name}.observations"
                            f"[{observation.get('observation_id', '')}]"
                            f".values.points[{point_index}].{value_field}"
                        ),
                        "pdf_page": pdf_page,
                        "table_number": table_label,
                        "draft_table_key": (
                            f"p{int(draft_table.group('page')):03d}-"
                            f"t{int(draft_table.group('table')):02d}"
                            if draft_table
                            and pdf_page == int(draft_table.group("page"))
                            else None
                        ),
                        "equation_locator": locator.get("equation") is not None,
                        "phase": point.get(
                            "phase", locator.get("phase", observation.get("phase"))
                        ),
                        "table_row_number": point.get(
                            "table_row_number",
                            locator.get(
                                "table_row_number", observation.get("table_row_number")
                            ),
                        ),
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
    if token not in {"gas", "liquid", "solid"}:
        return False
    return (
        re.search(rf"(?<!\w){re.escape(token)}(?!\w)", column_header, re.I)
        is not None
    )


def _walk_mappings(
    value: Any, path: str = "$", inherited_page: int | None = None
) -> list[tuple[str, Mapping[str, Any], int | None]]:
    """Yield mapping nodes with their nearest enclosing PDF page."""
    rows: list[tuple[str, Mapping[str, Any], int | None]] = []
    if isinstance(value, Mapping):
        page = _locator_page(value) or inherited_page
        rows.append((path, value, page))
        for key, child in value.items():
            rows.extend(_walk_mappings(child, f"{path}.{key}", page))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_walk_mappings(child, f"{path}[{index}]", inherited_page))
    return rows


def _locator_issues(
    extract: Mapping[str, Any], index: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Find printed-caption mismatches and inconsistent published-page offsets."""
    issues: list[dict[str, Any]] = []
    captions_by_page: dict[int, set[str]] = defaultdict(set)
    pages_with_caption_evidence: set[int] = set()
    for evidence in index.get("caption_evidence") or []:
        page = _locator_page(evidence)
        if page is not None:
            pages_with_caption_evidence.add(page)
            captions_by_page[page].update(
                label
                for label in (
                    _normalize_table_label(raw_label)
                    for raw_label in evidence.get("labels") or []
                )
                if label
            )
    if "caption_evidence" not in index:
        for table in index.get("tables") or []:
            page = _locator_page(table.get("locator") or table)
            if page is None:
                continue
            if any(line.strip() for line in _caption_lines(table.get("caption"))):
                pages_with_caption_evidence.add(page)
            caption_label = _printed_table_label(table.get("caption"))
            if caption_label:
                captions_by_page[page].add(caption_label)

    mappings = _walk_mappings(extract)
    for path, row, inherited_page in mappings:
        locator = row.get("locator")
        if not isinstance(locator, Mapping):
            continue
        if locator.get("equation") is not None:
            continue
        label = _normalize_table_label(locator.get("table"))
        if label is None or label == "UNKNOWN":
            continue
        page = _locator_page(locator) or inherited_page
        if page is None:
            continue
        page_captions = captions_by_page.get(page, set())
        if page not in pages_with_caption_evidence:
            issues.append(
                {
                    "path": f"{path}.locator.table",
                    "page": page,
                    "status": "unverifiable",
                    "message": (
                        f"cannot verify table label {label!r} on PDF page {page}: "
                        "no printed-caption evidence is available"
                    ),
                }
            )
        elif label not in page_captions:
            found = ", ".join(sorted(page_captions)) or "no printed labels"
            issues.append(
                {
                    "path": f"{path}.locator.table",
                    "page": page,
                    "status": "locator-error",
                    "message": (
                        f"table label {label!r} is not among printed captions on "
                        f"PDF page {page} (found: {found})"
                    ),
                }
            )

    page_pairs: list[tuple[str, int, int]] = []
    unique_pairs: set[tuple[int, int]] = set()
    for path, row, _ in mappings:
        if row.get("page") is None or row.get("published_page") is None:
            continue
        if isinstance(row["page"], bool) or isinstance(row["published_page"], bool):
            continue
        if not isinstance(row["published_page"], (int, str)):
            continue
        try:
            page = int(row["page"])
            published_page = int(row["published_page"])
        except (TypeError, ValueError):
            continue
        if page <= 0 or published_page <= 0:
            continue
        page_pairs.append((path, page, published_page))
        unique_pairs.add((page, published_page))

    explicit_offset = extract.get("page_offset")
    try:
        expected_offset = int(explicit_offset) if explicit_offset is not None else None
    except (TypeError, ValueError):
        expected_offset = None
    # Offset direction is printed folio minus 1-based PDF page.
    if expected_offset is None and len({page for page, _ in unique_pairs}) > 1:
        offsets = Counter(
            published_page - page for page, published_page in unique_pairs
        )
        common = offsets.most_common()
        if len(common) == 1 or common[0][1] > common[1][1]:
            expected_offset = common[0][0]
    if expected_offset is not None:
        for path, page, published_page in page_pairs:
            actual_offset = published_page - page
            if actual_offset != expected_offset:
                issues.append(
                    {
                        "path": path,
                        "page": page,
                        "status": "locator-error",
                        "message": (
                            f"page/published_page offset {actual_offset:+d} "
                            f"contradicts mapped offset {expected_offset:+d}"
                        ),
                    }
                )
    return issues


def compare_extract_to_draft(
    extract: Mapping[str, Any], draft_index_path: str | Path
) -> list[dict[str, Any]]:
    index_path = Path(draft_index_path)
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    draft_by_page: dict[int, list[tuple[dict[str, Any], Decimal]]] = defaultdict(list)
    draft_by_table: dict[tuple[int, str], list[tuple[dict[str, Any], Decimal]]] = defaultdict(list)
    draft_by_key: dict[str, list[tuple[dict[str, Any], Decimal]]] = defaultdict(list)
    draft_table_keys: set[str] = set()
    for table in index.get("tables") or []:
        page = int(table["pdf_page"])
        table_key = str(table["table_key"])
        draft_table_keys.add(table_key)
        locator = table.get("locator") or {}
        table_label = _normalize_table_label(locator.get("table"))
        column_headers = {
            int(column["column"]): str(column.get("header") or "")
            for column in table.get("columns") or []
        }
        csv_path = index_path.parent / table["csv"]
        with csv_path.open(encoding="utf-8", newline="") as stream:
            for cell in csv.DictReader(stream):
                if cell["row_type"] != "body":
                    continue
                if (
                    not cell["origin_row"]
                    or not cell["origin_column"]
                    or int(cell["row"]) != int(cell["origin_row"])
                    or int(cell["column"]) != int(cell["origin_column"])
                ):
                    continue
                number = _number_decimal(cell["value_text"])
                if number is not None:
                    candidate = {
                        "value": float(number),
                        "text": cell["text"],
                        "table_key": table["table_key"],
                        "printed_table_label": table_label,
                        "row": int(cell["row"]),
                        "column": int(cell["column"]),
                        "column_header": column_headers.get(int(cell["column"]), ""),
                        "flags": json.loads(cell["flags"] or "[]"),
                    }
                    draft_by_page[page].append((candidate, number))
                    draft_by_key[table_key].append((candidate, number))
                    if table_label and table_label != "UNKNOWN":
                        draft_by_table[(page, table_label)].append((candidate, number))

    results: list[dict[str, Any]] = []
    consumed: dict[tuple[int | None, str | None], set[int]] = defaultdict(set)
    for point in _numeric_points(extract):
        page = point["pdf_page"]
        table_number = point["table_number"]
        draft_table_key = point.pop("draft_table_key")
        equation_locator = point.pop("equation_locator")
        keyed_candidates = draft_by_key.get(draft_table_key, [])
        use_draft_table_key = draft_table_key in draft_table_keys
        location = (page, draft_table_key if use_draft_table_key else table_number)
        if equation_locator or page is None:
            indexed_candidates = []
        elif use_draft_table_key:
            indexed_candidates = keyed_candidates
        elif table_number is None:
            indexed_candidates = draft_by_page.get(page, [])
        else:
            indexed_candidates = draft_by_table.get((page, table_number), [])
        point_value = Decimal(str(point["value"]))
        has_location = any(
            point.get(key) is not None
            for key in ("table_number", "table_row_number", "phase", "table_phase_column")
        )
        matching_candidates = [
            (index, candidate)
            for index, (candidate, value) in enumerate(indexed_candidates)
            if index not in consumed[location]
            and point_value == value
            and (
                point.get("table_row_number") is None
                or candidate["row"] == int(point["table_row_number"])
            )
            and (
                point.get("table_phase_column") is None
                or candidate["column"] == int(point["table_phase_column"])
            )
            and _phase_matches_column(point.get("phase"), candidate["column_header"])
        ]
        matched_index, matched = matching_candidates[0] if matching_candidates else (None, None)
        if matched is not None:
            consumed[location].add(matched_index)
            status = "matched" if has_location else "presence-only"
        elif equation_locator:
            status = "unverifiable"
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
    for issue in _locator_issues(extract, index):
        results.append(
            {
                "species": "",
                "observation_id": "",
                "point_index": "",
                "pdf_page": issue["page"],
                "value": "",
                "matched_cell": None,
                "draft_candidate_count": 0,
                "path": issue["path"],
                "locator_issue": issue["message"],
                "status": issue.get("status", "locator-error"),
            }
        )
    for result in results:
        result.setdefault("locator_issue", "")
    return results


def _convert_command(args: argparse.Namespace) -> int:
    index = convert_source(args.source, args.out, corpus_root=args.corpus_root)
    document = yaml.safe_load(index.read_text(encoding="utf-8"))
    print(f"{index}: {len(document['tables'])} tables")
    return 0


def _compare_command(args: argparse.Namespace) -> int:
    extract = yaml.safe_load(Path(args.extract).read_text(encoding="utf-8"))
    results = compare_extract_to_draft(extract, args.draft_index)
    fields = (
        "species",
        "observation_id",
        "point_index",
        "pdf_page",
        "value",
        "status",
        "matched_cell",
        "draft_candidate_count",
        "locator_issue",
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
        f"locator-error={counts['locator-error']} "
        f"unverifiable={counts['unverifiable']} "
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
    compare.set_defaults(run=_compare_command)
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
