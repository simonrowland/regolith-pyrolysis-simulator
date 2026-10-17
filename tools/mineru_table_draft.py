"""Write loss-preserving CSV drafts from MinerU content-list tables."""

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
_NUMBER = re.compile(
    r"^[+\-−–—]?(?:\d+(?:\.\d+)?|\.\d+)(?:\(\d+\))?(?:[eE][+\-]?\d+)?$"
)
_COMMA_DECIMAL = re.compile(r"(?<![\d.])\d{2,3}\.\d{3}(?!\d)")
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
    return bool(_NUMBER.fullmatch(text.strip()))


def _number_decimal(text: str) -> Decimal | None:
    token = text.strip()
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
    if tagged_count:
        return tagged_count

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
            for point_index, point in enumerate(values.get("points") or []):
                value = point.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    continue
                locator = point.get("locator") or observation.get("locator") or {}
                pdf_page = locator.get("pdf_page")
                if pdf_page is None and locator.get("pdf_page_index") is not None:
                    pdf_page = int(locator["pdf_page_index"]) + 1
                points.append(
                    {
                        "species": species_name,
                        "observation_id": observation.get("observation_id", ""),
                        "point_index": point_index,
                        "path": (
                            f"species.{species_name}.observations"
                            f"[{observation.get('observation_id', '')}]"
                            f".values.points[{point_index}].value"
                        ),
                        "pdf_page": pdf_page,
                        "value": value,
                    }
                )
    return points


def compare_extract_to_draft(
    extract: Mapping[str, Any], draft_index_path: str | Path
) -> list[dict[str, Any]]:
    index_path = Path(draft_index_path)
    index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
    draft_by_page: dict[int, list[tuple[dict[str, Any], Decimal]]] = defaultdict(list)
    for table in index.get("tables") or []:
        page = int(table["pdf_page"])
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
                        "row": int(cell["row"]),
                        "column": int(cell["column"]),
                        "flags": json.loads(cell["flags"] or "[]"),
                    }
                    draft_by_page[page].append((candidate, number))

    consumed: dict[int, set[int]] = defaultdict(set)
    results: list[dict[str, Any]] = []
    for point in _numeric_points(extract):
        page = point["pdf_page"]
        indexed_candidates = draft_by_page.get(page, []) if page is not None else []
        point_value = Decimal(str(point["value"]))
        match_index = next(
            (
                index
                for index, (_, candidate_value) in enumerate(indexed_candidates)
                if index not in consumed[page]
                and point_value == candidate_value
            ),
            None,
        )
        if match_index is not None:
            consumed[page].add(match_index)
            status = "matched"
        elif indexed_candidates:
            status = "mismatch"
        else:
            status = "no-match"
        candidates = [candidate for candidate, _ in indexed_candidates]
        results.append(
            {
                **point,
                "status": status,
                "draft_cells": candidates,
                "matched_cell": (
                    candidates[match_index] if match_index is not None else None
                ),
            }
        )
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
        "draft_cells",
        "matched_cell",
        "path",
    )
    writer = csv.DictWriter(sys.stdout, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(
        {
            key: (
                json.dumps(row[key], ensure_ascii=False)
                if key in {"draft_cells", "matched_cell"}
                else row[key]
            )
            for key in fields
        }
        for row in results
    )
    counts = Counter(row["status"] for row in results)
    print(
        f"compare: matched={counts['matched']} no-match={counts['no-match']} "
        f"mismatch={counts['mismatch']} total={len(results)}",
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
