"""Mechanical pre-review checks for one literature extract."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import yaml
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Mapping

from simulator.battery import migrate
from simulator.battery.enums import Quantity
from simulator.battery.identity import quantity_token
from simulator.battery.score import parse_species_formula


_NUMBER = re.compile(
    r"^[~≈<>≤≥]?\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"
)
_TABLE_NUMBER = re.compile(
    r"\btable\s*([0-9]+[A-Za-z]?|[IVXLCDM]+[A-Za-z]?)\b", re.I
)
_CHEMICAL_FORMULA = re.compile(
    r"\b(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?){1,8}\b"
)
_PRINTED_COMPOSITION_COLUMN = re.compile(
    r"x_(.+)_(?:mol|wt|mass)_percent_as_printed"
)
_TABLE_CELL_NUMBER = re.compile(
    r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)
_SCORED_SPECIES_QUANTITIES = frozenset(
    {
        Quantity.ACTIVITY,
        Quantity.ACTIVITY_COEFFICIENT,
        Quantity.P_SAT,
        Quantity.P_PARTIAL,
        Quantity.P_REFERENCE,
        Quantity.RESIDUE_COMPONENT_COMPOSITION,
    }
)
_COMPOSITION_META_KEYS = migrate._COMPOSITION_MOL_META_KEYS
_LEGACY_LOCATOR_INDEX_CUTOFF = date(2026, 10, 18)

@dataclass(frozen=True)
class Finding:
    check: str
    severity: str
    detail: str


@dataclass(frozen=True)
class TableCoverage:
    table: str
    numeric_cells: int
    extract_values: int

    @property
    def percent(self) -> float:
        if not self.numeric_cells:
            return 100.0 if not self.extract_values else 0.0
        return min(100.0, 100.0 * self.extract_values / self.numeric_cells)


@dataclass
class GateReport:
    findings: list[Finding] = field(default_factory=list)
    value_kinds: dict[str, int] = field(default_factory=dict)
    refusal_reasons: dict[str, int] = field(default_factory=dict)
    migration_hard_issues: int = 0
    migration_error: str | None = None
    composition_drops: list[str] = field(default_factory=list)
    tables: list[TableCoverage] = field(default_factory=list)

    @property
    def hard_failed(self) -> bool:
        return any(item.severity == "HARD" for item in self.findings)


def _add(
    report: GateReport,
    check: str,
    severity: str,
    issues: list[str],
) -> None:
    if issues:
        shown = "; ".join(issues[:3])
        if len(issues) > 3:
            shown += f"; and {len(issues) - 3} more"
        report.findings.append(
            Finding(check, severity, f"{len(issues)} issue(s): {shown}")
        )


def _mappings(node: object):
    seen: set[int] = set()

    def visit(value: object, path: str):
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            yield value, path
            for key, child in value.items():
                yield from visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            for index, child in enumerate(value):
                yield from visit(child, f"{path}[{index}]")

    yield from visit(node, "")


def _as_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if not isinstance(value, (str, int, float, Decimal)):
        return None
    token = str(value).strip()
    if not _NUMBER.fullmatch(token):
        return None
    token = re.sub(r"^[~≈<>≤≥]\s*", "", token)
    try:
        result = Decimal(token)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _pdf_page_count(
    corpus: Path, source_ids: Iterable[str]
) -> tuple[int | None, str | None]:
    pdf_paths = [
        corpus / "raw" / source_id / f"{source_id}.pdf"
        for source_id in dict.fromkeys(source_ids)
        if source_id
    ]
    pdf_path = next((path for path in pdf_paths if path.is_file()), None)
    if pdf_path is None:
        return None, None
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return None, f"cannot run pdfinfo for {pdf_path}: {exc}"
    if result.returncode:
        return None, f"pdfinfo failed for {pdf_path}: {result.stderr.strip()}"
    match = re.search(r"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    if match is None:
        return None, f"pdfinfo returned no page count for {pdf_path}"
    return int(match.group(1)), None


def _legacy_missing_locator_index(doc: object) -> bool:
    if not isinstance(doc, Mapping):
        return False
    extraction = doc.get("extraction")
    if not isinstance(extraction, Mapping):
        return False
    extracted_on = extraction.get("date")
    if isinstance(extracted_on, datetime):
        return extracted_on.date() < _LEGACY_LOCATOR_INDEX_CUTOFF
    if isinstance(extracted_on, date):
        return extracted_on < _LEGACY_LOCATOR_INDEX_CUTOFF
    if isinstance(extracted_on, str):
        try:
            return date.fromisoformat(extracted_on) < _LEGACY_LOCATOR_INDEX_CUTOFF
        except ValueError:
            return False
    return False


def _locator_issues(
    doc: object,
    *,
    pdf_page_count: int | None,
    pdf_error: str | None,
    legacy_missing_index: bool,
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    hard_issues: list[str] = []
    page_offset_issues: list[str] = []
    soft_pdf_issues: list[str] = []
    soft_legacy_issues: list[str] = []
    verification_issues: list[str] = []
    saw_locator = False
    for mapping, path in _mappings(doc):
        for key, locator in mapping.items():
            if key != "locator" and not (
                isinstance(key, str) and key.endswith("_locator")
            ):
                continue
            location = f"{path}.{key}" if path else str(key)
            saw_locator = True
            if not isinstance(locator, Mapping):
                hard_issues.append(f"{location} must be a mapping")
                continue
            verification = locator.get("page_verification")
            if verification is not None:
                if verification != "unverified_pdf_absent":
                    hard_issues.append(
                        f"{location}.page_verification has unsupported value {verification!r}"
                    )
                elif pdf_page_count is None and pdf_error is None:
                    verification_issues.append(
                        f"{location}: physical page could not be verified because the PDF is absent"
                    )
                else:
                    page_offset_issues.append(
                        f"{location}.page_verification is invalid because a corpus PDF is present"
                    )
            index = locator.get("pdf_page_index")
            if index is None:
                message = f"{location}.pdf_page_index is missing"
                if legacy_missing_index:
                    soft_legacy_issues.append(
                        f"{message} on a pre-{_LEGACY_LOCATOR_INDEX_CUTOFF.isoformat()} extract"
                    )
                else:
                    hard_issues.append(message)
                continue
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                hard_issues.append(
                    f"{location}.pdf_page_index must be a nonnegative "
                    "zero-based integer"
                )
                continue
            if pdf_page_count is not None and index >= pdf_page_count:
                hard_issues.append(
                    f"{location}.pdf_page_index {index} is outside the PDF's "
                    f"0..{pdf_page_count - 1} page range"
                )
            page = _as_decimal(locator.get("page"))
            if page is None and verification != "unverified_pdf_absent":
                verification_issues.append(
                    f"{location}.page is missing or invalid; physical page is unverified"
                )
            elif (
                page is not None
                and page != Decimal(index + 1)
                and verification != "unverified_pdf_absent"
            ):
                page_offset_issues.append(
                    f"{location}: page {page} differs from pdf_page_index + 1; "
                    "page must be the one-based physical PDF page"
                )
    if saw_locator:
        if pdf_error:
            hard_issues.append(pdf_error)
        elif pdf_page_count is None:
            soft_pdf_issues.append(
                "corpus PDF is absent; pdf_page_index upper bounds could not be checked"
            )
    return (
        hard_issues,
        page_offset_issues,
        soft_pdf_issues,
        soft_legacy_issues,
        verification_issues,
    )


def _component_pairs(value: object) -> list[tuple[str, object]]:
    if isinstance(value, Mapping):
        components = value.get("components")
        if isinstance(components, Mapping):
            return [(str(key), amount) for key, amount in components.items()]
        if isinstance(components, (list, tuple)):
            return [
                (str(item[0]), item[1])
                for item in components
                if isinstance(item, (list, tuple)) and len(item) == 2
            ]
        return [
            (str(key), amount)
            for key, amount in value.items()
            if key not in _COMPOSITION_META_KEYS
        ]
    if isinstance(value, (list, tuple)):
        return [
            (str(item[0]), item[1])
            for item in value
            if isinstance(item, (list, tuple)) and len(item) == 2
        ]
    return []


def _composition_issues(
    doc: object, migration_drops: list[str] | None = None
) -> list[str]:
    issues = list(migration_drops or [])
    seen_percent: set[tuple[str, str]] = set()

    def check_amounts(amounts: list[tuple[str, object]], path: str) -> None:
        numeric = [_as_decimal(value) for _, value in amounts]
        numeric_values = [value for value in numeric if value is not None]
        if not numeric_values:
            return
        total = sum(numeric_values, Decimal(0))
        if (
            abs(total - Decimal(100)) <= Decimal(2)
            and abs(total - Decimal(1)) > Decimal("0.01")
        ):
            key = (path, str(total))
            if key not in seen_percent:
                seen_percent.add(key)
                issues.append(
                    f"{path} mole-fraction components sum to {total}, likely "
                    "percent-as-fraction"
                )

    for mapping, path in _mappings(doc):
        raw = mapping.get("composition_mol")
        if isinstance(raw, Mapping):
            check_amounts(_component_pairs(raw), f"{path}.composition_mol")
        elif isinstance(mapping.get("composition_mole_fraction"), Mapping):
            values = mapping["composition_mole_fraction"]
            check_amounts(
                _component_pairs(values),
                f"{path}.composition_mole_fraction",
            )

        if "components" in mapping and "amount_basis" in mapping:
            amount_basis = str(mapping.get("amount_basis") or "")
            if amount_basis in {"", "mole_fraction"}:
                check_amounts(
                    _component_pairs(mapping),
                    path or "composition",
                )
    return issues


def _uncertainty_zero_paths(node: object, path: str) -> list[str]:
    issues: list[str] = []

    def amount(value: object) -> Decimal | None:
        token = str(value).strip() if isinstance(value, str) else ""
        if token.startswith("±"):
            return _as_decimal(token[1:].strip())
        return _as_decimal(value)

    def visit(value: object, current: str, band_context: bool = False) -> None:
        if isinstance(value, Mapping):
            sigma_arms: dict[tuple[str, str], dict[str, tuple[str, Decimal | None]]] = defaultdict(dict)
            for key, child in value.items():
                match = re.fullmatch(r"(.*)sigma_(plus|minus)(.*)", str(key).lower())
                if match:
                    group = (match.group(1), match.group(3))
                    sigma_arms[group][match.group(2)] = (str(key), _as_decimal(child))
            paired_sigma_keys: set[str] = set()
            for arms in sigma_arms.values():
                if "plus" not in arms or "minus" not in arms:
                    continue
                paired_sigma_keys.update((arms["plus"][0], arms["minus"][0]))
                plus, minus = arms["plus"][1], arms["minus"][1]
                if plus is not None and minus is not None and plus == minus == 0:
                    issues.append(current)
            endpoints: dict[str, Decimal | None] = {}
            for key in ("low", "lower", "min", "high", "upper", "max"):
                if key in value:
                    endpoints[key] = _as_decimal(value[key])
            low = next(
                (endpoints[k] for k in ("low", "lower", "min") if k in endpoints),
                None,
            )
            high = next(
                (endpoints[k] for k in ("high", "upper", "max") if k in endpoints),
                None,
            )
            if low is not None and high is not None and low == high:
                issues.append(current)
            for key, child in value.items():
                key_text = str(key).lower()
                child_path = f"{current}.{key}"
                if str(key) in paired_sigma_keys:
                    continue
                if key_text in {"low", "lower", "min", "high", "upper", "max"}:
                    continue
                if "band_width" in key_text or (
                    key_text == "width" and band_context
                ):
                    number = amount(child)
                    if number == 0:
                        issues.append(child_path)
                    continue
                visit(child, child_path, band_context or "band" in key_text)
        elif isinstance(value, (list, tuple)):
            numbers = [_as_decimal(item) for item in value]
            if len(value) == 2 and all(item is not None for item in numbers):
                if numbers[0] == numbers[1]:
                    issues.append(current)
                return
            for index, child in enumerate(value):
                visit(child, f"{current}[{index}]", band_context)
        elif amount(value) == 0:
            issues.append(current)

    visit(node, path)
    return issues


def _uncertainty_issues(doc: object) -> list[str]:
    issues: list[str] = []
    for mapping, path in _mappings(doc):
        for key, value in mapping.items():
            key_text = str(key).lower()
            item_path = f"{path}.{key}" if path else str(key)
            if key_text == "uncertainty":
                issues.extend(_uncertainty_zero_paths(value, item_path))
            elif key_text.startswith("read_uncertainty_"):
                issues.extend(_uncertainty_zero_paths(value, item_path))
            elif "band_width" in key_text and _as_decimal(value) == 0:
                issues.append(item_path)
    return sorted(set(issues))


class _TableCellParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[str] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"td", "th"}:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"td", "th"} and self._cell is not None:
            self.cells.append(" ".join(self._cell))
            self._cell = None


def _numeric_table_cells(table_body: object) -> int:
    if not isinstance(table_body, str):
        return 0
    parser = _TableCellParser()
    try:
        parser.feed(table_body)
    except Exception:
        return 0
    count = 0
    for cell in parser.cells:
        text = re.sub(r"\$[^$]*\$", " ", cell)
        text = _CHEMICAL_FORMULA.sub(" ", text)
        if _TABLE_CELL_NUMBER.search(text):
            count += 1
    return count


def _table_key(value: object) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    match = _TABLE_NUMBER.search(token)
    if match:
        token = match.group(1)
    token = token.strip(" .:#()[]")
    roman = re.fullmatch(r"([IVXLCDM]+)([A-Za-z]?)", token, re.I)
    if roman:
        values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
        number = 0
        previous = 0
        for numeral in reversed(roman.group(1).lower()):
            current = values[numeral]
            number += -current if current < previous else current
            previous = current
        token = f"{number}{roman.group(2)}"
    return f"table {token.casefold()}" if token else None


def _locator_table_keys(value: object) -> list[str]:
    if not isinstance(value, str):
        key = _table_key(value)
        return [key] if key else []
    keys = []
    for part in re.split(r"[,;/]|\band\b", value, flags=re.I):
        key = _table_key(part)
        if key and key not in keys:
            keys.append(key)
    return keys


def _caption_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(part) for part in value)
    return ""


def _mineru_roots(corpus: Path, source_id: str) -> list[Path]:
    if not source_id or Path(source_id).name != source_id:
        return []
    return [
        corpus / "raw" / source_id / "ocr-mineru",
        corpus / "raw" / source_id / "mineru",
        corpus / "text" / source_id / "mineru",
        corpus / "text" / source_id / "ocr-mineru",
    ]


def _mineru_table_cells(corpus: Path, source_id: str) -> dict[str, int]:
    cells: dict[str, int] = defaultdict(int)
    for root in _mineru_roots(corpus, source_id):
        if not root.is_dir():
            continue
        content_paths = sorted(root.glob("**/*content_list.json"))
        if not content_paths:
            continue
        for content_path in content_paths:
            try:
                content = json.loads(content_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, Mapping) or item.get("type") != "table":
                    continue
                caption = _caption_text(item.get("table_caption"))
                key = _table_key(caption)
                if key is None:
                    continue
                cells[key] += _numeric_table_cells(item.get("table_body"))
        break
    return dict(cells)


def _numeric_point_value_count(node: object) -> int:
    if isinstance(node, Mapping):
        return sum(
            _numeric_point_value_count(value)
            for key, value in node.items()
            if key != "locator"
        )
    if isinstance(node, (list, tuple)):
        return sum(_numeric_point_value_count(value) for value in node)
    return int(_as_decimal(node) is not None)


def _count_located_numeric_values(
    node: object,
    counts: dict[str, int],
    tables: list[str],
) -> None:
    if isinstance(node, Mapping):
        locator = node.get("locator")
        located_tables = (
            _locator_table_keys(locator.get("table"))
            if isinstance(locator, Mapping)
            else []
        )
        current_tables = located_tables or tables
        for key, value in node.items():
            if key != "locator":
                _count_located_numeric_values(value, counts, current_tables)
    elif isinstance(node, (list, tuple)):
        for value in node:
            _count_located_numeric_values(value, counts, tables)
    elif _as_decimal(node) is not None:
        for table in tables:
            counts[table] += 1


def _extract_table_values(doc: object) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    if not isinstance(doc, Mapping):
        return counts
    species_blocks = doc.get("species")
    if not isinstance(species_blocks, Mapping):
        return counts
    for block in species_blocks.values():
        if not isinstance(block, Mapping):
            continue
        for container in ("observations", "context"):
            rows = block.get(container)
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                locator = row.get("locator")
                tables = (
                    _locator_table_keys(locator.get("table"))
                    if isinstance(locator, Mapping)
                    else []
                )
                _count_located_numeric_values(row.get("values"), counts, tables)
    return counts


def _capture_report(doc: object, corpus: Path) -> list[TableCoverage]:
    source_id = str(doc.get("source_id") or "") if isinstance(doc, Mapping) else ""
    if not source_id:
        return []
    cells = _mineru_table_cells(corpus, source_id)
    values = _extract_table_values(doc)
    return [
        TableCoverage(table, count, values.get(table, 0))
        for table, count in sorted(cells.items())
    ]


def _has_numeric_points(node: object) -> bool:
    for mapping, _path in _mappings(node):
        for key, value in mapping.items():
            if (
                ("point" in str(key).lower() or "series" in str(key).lower())
                and isinstance(value, (list, tuple))
                and _numeric_point_value_count(value) > 0
            ):
                return True
    return False


def _figure_policy_defers(text: str) -> bool:
    lowered = text.casefold()
    return bool(
        re.search(
            r"\b(?:not|no)\b.{0,80}\b(?:digitis\w*|digitiz\w*|numeric\w*\s+"
            r"(?:curve|figure|data|point|value)\w*|plotted\s+(?:curve|value)\w*)\b",
            lowered,
        )
        or re.search(r"\bdefer(?:red|s|ring)?\b.{0,40}\bfigures?\b", lowered)
        or re.search(r"\bfigures?\b.{0,40}\bdefer(?:red|s|ring)?\b", lowered)
    )


def _deferred_figure_issues(doc: object) -> list[str]:
    if not isinstance(doc, Mapping):
        return []
    policy = doc.get("figure_policy")
    if not isinstance(policy, str) or not _figure_policy_defers(policy):
        return []
    issues: list[str] = []
    species_blocks = doc.get("species")
    if isinstance(species_blocks, Mapping):
        for species, block in species_blocks.items():
            if not isinstance(block, Mapping):
                continue
            observations = block.get("observations")
            if not isinstance(observations, list):
                continue
            for row in observations:
                if not isinstance(row, Mapping):
                    continue
                locator = row.get("locator")
                values = row.get("values")
                if (
                    isinstance(locator, Mapping)
                    and locator.get("figure")
                    and _has_numeric_points(values)
                ):
                    issues.append(
                        f"species.{species}.{row.get('observation_id', '?')} "
                        "contains numeric points under a deferred figure policy"
                    )
    figures = doc.get("figures")
    if isinstance(figures, list):
        for index, row in enumerate(figures):
            if isinstance(row, Mapping) and _has_numeric_points(row):
                issues.append(
                    f"figures[{index}] contains numeric points under deferred policy"
                )
    return issues


def _normalize_identity(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(char for char in normalized if char.isalnum())


def _citation_title(citation: object) -> str | None:
    if not isinstance(citation, str):
        return None
    quoted = re.search(r"[\"“”']([^\"“”']{8,})[\"“”']", citation)
    if quoted:
        return quoted.group(1)
    year = re.search(r"\b(?:1[6-9]\d{2}|20\d{2})\b", citation)
    remainder = citation[year.end() :] if year else citation
    remainder = re.sub(r"^[\s,.:;()\-]+", "", remainder)
    title = re.split(r"\.\s+(?=[A-Z])", remainder, maxsplit=1)[0].strip()
    return title or None


def _identity(doc: object) -> dict[str, str]:
    source = doc.get("source") if isinstance(doc, Mapping) else None
    if not isinstance(source, Mapping):
        return {}
    keys: dict[str, str] = {}
    raw_doi = source.get("doi")
    doi = (
        migrate.canonicalize_doi(raw_doi)
        if isinstance(raw_doi, str) and raw_doi.strip()
        else None
    )
    if doi:
        keys["doi"] = doi.casefold()
    citation = source.get("citation")
    title = source.get("title") or _citation_title(citation)
    normalized_title = _normalize_identity(title)
    if normalized_title:
        keys["title"] = normalized_title
    authors = source.get("authors")
    year = source.get("year")
    if isinstance(citation, str):
        year_match = re.search(r"\b(?:1[6-9]\d{2}|20\d{2})\b", citation)
        if year is None and year_match:
            year = year_match.group(0)
        if not authors and year_match:
            authors = citation[: year_match.start()]
    author_key = _normalize_identity(
        authors if isinstance(authors, str) else " ".join(map(str, authors or []))
    )
    if author_key and year is not None:
        keys["author_year"] = f"{author_key}:{year}"
    return keys


def _source_header(path: Path) -> Mapping[str, Any] | None:
    schema_line = ""
    lines: list[str] = []
    found = False
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if not found:
                if line.startswith("schema_version:"):
                    schema_line = line
                if not line.startswith("source:"):
                    continue
                found = True
                lines.append(line)
                if line.partition(":")[2].strip():
                    break
                continue
            if line.strip() and not line[0].isspace():
                break
            lines.append(line)
    if not lines or not schema_line:
        return None
    try:
        parsed = yaml.safe_load(schema_line + "".join(lines))
    except yaml.YAMLError:
        return None
    if (
        not isinstance(parsed, Mapping)
        or parsed.get("schema_version") != "literature_extract.v1"
    ):
        return None
    source = parsed.get("source") if isinstance(parsed, Mapping) else None
    return source if isinstance(source, Mapping) else None


def _duplicate_issues(
    path: Path,
    doc: object,
    extracts_dir: Path,
) -> list[str]:
    target = _identity(doc)
    if not target or not extracts_dir.is_dir():
        return []
    issues: list[str] = []
    for other in extracts_dir.iterdir():
        if (
            not other.is_file()
            or other.suffix.lower() not in {".yaml", ".yml"}
            or other.name.startswith("_")
            or other.resolve() == path.resolve()
        ):
            continue
        source = _source_header(other)
        other_doc = {"source": source} if source is not None else {}
        other_keys = _identity(other_doc)
        for kind in ("doi", "title", "author_year"):
            if kind in target and target.get(kind) == other_keys.get(kind):
                issues.append(f"{kind} duplicates {other.name}")
                break
    return issues


def _source_prints_mixture(values: object, parent_formula: str) -> bool:
    if not isinstance(values, Mapping):
        return False
    try:
        composition, _omitted = migrate._mole_fraction_composition_from_values(values)
    except ValueError:
        composition = None
    if composition is not None and sum(
        amount != 0 for _formula, amount in composition.components
    ) >= 2:
        return True

    for printed in (
        values.get("composition_as_printed"),
        values.get("sample_as_printed"),
        values.get("composition_mol"),
        values.get("composition_mole_fraction"),
        values.get("composition_wt_pct"),
        values.get("composition"),
    ):
        if isinstance(printed, str):
            if len(set(_CHEMICAL_FORMULA.findall(printed))) >= 2:
                return True
        elif isinstance(printed, (Mapping, list, tuple)):
            present = sum(
                _CHEMICAL_FORMULA.fullmatch(formula) is not None
                and not _is_explicit_zero(raw_amount)
                for formula, raw_amount in _component_pairs(printed)
            )
            if present >= 2:
                return True

    printed_oxides: set[str] = set()
    for key, raw_amount in values.items():
        match = _PRINTED_COMPOSITION_COLUMN.fullmatch(str(key))
        if match and not _is_explicit_zero(raw_amount):
            oxide = match.group(1)
            if _CHEMICAL_FORMULA.fullmatch(oxide) is None:
                continue
            printed_oxides.add(oxide)
            if oxide != parent_formula:
                return True
    if len(printed_oxides) >= 2:
        return True
    return False


def _is_explicit_zero(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return False
    token = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", token):
        return False
    try:
        return Decimal(token) == 0
    except InvalidOperation:
        return False


def _source_has_printed_composition(values: object) -> bool:
    if not isinstance(values, Mapping):
        return False
    composition_fields = {
        "composition_as_printed",
        "composition_mol",
        "composition_mole_fraction",
        "composition_wt_pct",
        "composition",
        "sample_as_printed",
    }
    return any(
        key in composition_fields
        or _PRINTED_COMPOSITION_COLUMN.fullmatch(str(key))
        for key in values
    )


def _transition_mixture_identity_issues(
    doc: Mapping[str, Any],
    source_id: str,
    observations: Mapping[str, Any],
) -> list[str]:
    issues: list[str] = []
    for parent_formula, raw in migrate.iter_extract_observations(doc):
        raw_id = str(raw.get("observation_id") or "")
        values = raw.get("values")
        if not raw_id or not isinstance(values, Mapping):
            continue
        prefix = f"{source_id}::{raw_id}"
        point_containers = [
            (name, values[name])
            for name in ("rows", "series", "points", "tests")
            if isinstance(values.get(name), list) and values[name]
        ]
        inherited_mixture = _source_prints_mixture(
            migrate._values_without_row_lists(values), parent_formula
        )
        point_mixtures: dict[str, bool] = {}
        for container, rows in point_containers:
            for index, point in enumerate(rows):
                if not isinstance(point, Mapping):
                    continue
                point_mixture = (
                    _source_prints_mixture(point, parent_formula)
                    if _source_has_printed_composition(point)
                    else inherited_mixture
                )
                point_temperature = migrate.select_declared_source(
                    migrate.AXIS_TEMPERATURE_K, None, point
                ).amount
                point_extra = migrate.series_row_extra(
                    point, include_locator=container != "series"
                )
                point_item = {
                    "index": index,
                    "item": point,
                    "units": values.get("units"),
                }
                if container != "series":
                    point_item["container"] = container
                point_id = migrate._exploded_point_id(
                    prefix,
                    point_item,
                    point_temperature,
                    point,
                    point_extra,
                )
                point_mixtures[point_id] = point_mixture

        if point_containers:
            migrated = (
                (observation_id, observation)
                for observation_id, observation in observations.items()
                if observation_id in point_mixtures
            )
        else:
            parent_mixture = _source_prints_mixture(values, parent_formula)
            if not parent_mixture:
                continue
            migrated = (
                (observation_id, observation)
                for observation_id, observation in observations.items()
                if observation_id == prefix
                or observation_id.startswith(prefix + "::")
            )
        for observation_id, observation in migrated:
            identity = observation.identity
            quantity_state = identity.quantity
            quantity = (
                quantity_state.value if quantity_state.is_value else None
            )
            if quantity not in {
                migrate.Quantity.TRANSITION_TEMPERATURE,
                migrate.Quantity.ENTHALPY_OF_FUSION,
            }:
                continue
            if point_containers and not point_mixtures[observation_id]:
                continue
            profile = migrate.profile_for(identity)
            composition = identity.composition
            if (
                "composition" not in profile.required
                or composition is None
                or composition.is_not_applicable
            ):
                issues.append(
                    f"{raw_id}: printed mixture has no composition in migrated "
                    f"identity {observation.observation_id}"
                )
    return issues


def _migrate_one(path: Path, doc: object, report: GateReport) -> list[str]:
    if not isinstance(doc, Mapping):
        report.migration_error = "extract root is not a mapping"
        return [report.migration_error]
    source = doc.get("source")
    source = source if isinstance(source, Mapping) else {}
    source_id = str(doc.get("source_id") or path.stem)
    index_row = {
        "source_id": source_id,
        "citation": str(source.get("citation") or source_id),
        "doi": source.get("doi"),
    }
    try:
        migrator = migrate.Migrator(
            root=migrate.REPO_ROOT,
            index={source_id: index_row},
            aliases={},
        )
        migrator._migrate_extract(path)
        migrator.finalize()
    except Exception as exc:  # noqa: BLE001 — a gate failure must remain visible
        report.migration_error = f"{type(exc).__name__}: {exc}"
        return [report.migration_error]

    result = migrator.result
    species_issues = _species_container_issues(doc, result.observations)
    kinds = Counter(
        observation.value.kind.value
        for observation in result.observations.values()
    )
    report.value_kinds = dict(sorted(kinds.items()))
    refusals: Counter[str] = Counter()
    for observation in result.observations.values():
        reason = observation.value.unavailable_reason
        if reason:
            refusals[str(reason)] += 1
    for entry in result.queue:
        refusals[str(entry.why)] += 1
        if entry.why.startswith("unusable composition component(s):"):
            location = entry.observation_id or entry.source or entry.work_id
            report.composition_drops.append(f"{location}: {entry.why}")
    if result.validation is not None:
        for issue in result.validation.hard_issues:
            refusals[str(issue.reason.value)] += 1
    report.refusal_reasons = dict(sorted(refusals.items()))
    hard_issues = (
        () if result.validation is None else result.validation.hard_issues
    )
    transition_issues = _transition_mixture_identity_issues(
        doc, source_id, result.observations
    )
    report.migration_hard_issues = len(hard_issues) + len(transition_issues)
    issues = []
    if transition_issues:
        issues.append(
            f"{len(transition_issues)} printed mixture identity issue(s): "
            f"{transition_issues[0]}"
        )
    if hard_issues:
        issues.append(
            f"{len(hard_issues)} hard migration issue(s): "
            f"{hard_issues[0].path} {hard_issues[0].reason.value}"
        )
    issues.extend(species_issues)
    return issues


def _species_container_issues(doc: object, observations: Mapping[str, Any]) -> list[str]:
    """Reject scored rows whose printed component differs from migrated identity."""
    if not isinstance(doc, Mapping):
        return []
    species_blocks = doc.get("species")
    if not isinstance(species_blocks, Mapping):
        return []

    container_formulas = {
        str(formula)
        for formula in species_blocks
        if parse_species_formula(str(formula)) is not None
    }
    issues: list[str] = []
    for container, observation in migrate.iter_extract_observations(doc):
        raw_id = str(observation.get("observation_id") or "")
        migrated = observations.get(f"{doc.get('source_id', '')}::{raw_id}")
        if migrated is None:
            migrated = next(
                (item for key, item in observations.items() if key.endswith(f"::{raw_id}")),
                None,
            )
        if migrated is None:
            continue
        identity_formula = migrated.identity.species.formula
        values = observation.get("values")
        if not isinstance(values, Mapping):
            continue
        quantity = quantity_token(migrated.identity)
        if quantity not in _SCORED_SPECIES_QUANTITIES:
            continue
        row_species = _scored_row_species(
            raw_id,
            values,
            quantity,
            container_formulas,
        )
        if row_species is not None and identity_formula != row_species:
            issues.append(
                f"species.{container}.observations[{raw_id}] migrated as "
                f"{identity_formula}, while row names {row_species}"
            )
    return issues


def _scored_row_species(
    observation_id: str,
    values: Mapping[str, Any],
    quantity: Quantity,
    known_formulas: set[str],
) -> str | None:
    """Read one component only from fields that name a scored quantity's species."""
    explicit = values.get("formula")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    for key in ("oxide_formula_as_published", "component_as_published"):
        explicit = values.get(key)
        if isinstance(explicit, str) and parse_species_formula(explicit) is not None:
            return explicit

    candidates = set(known_formulas)
    for key in values:
        match = re.fullmatch(
            r"(?:a|gamma|activity|p_partial|p_sat|p_reference)_"
            r"([A-Za-z][A-Za-z0-9.]*)",
            str(key),
        )
        if match and parse_species_formula(match.group(1)) is not None:
            candidates.add(match.group(1))

    if quantity is Quantity.RESIDUE_COMPONENT_COMPOSITION:
        component = values.get("component_as_published")
        if isinstance(component, str) and parse_species_formula(component) is not None:
            return component
        primary_oxide = values.get("primary_oxide_wt_pct")
        if isinstance(primary_oxide, Mapping) and len(primary_oxide) == 1:
            formula = str(next(iter(primary_oxide)))
            if parse_species_formula(formula) is not None:
                return formula
        return None

    # `values.quantity` and component-bearing value keys describe the measured
    # activity/gas species. A caption or bulk composition is deliberately ignored.
    quantity_text = values.get("quantity")
    text_matches: set[str] = set()
    if isinstance(quantity_text, str):
        match = re.fullmatch(
            r"(?:a|gamma|activity(?: coefficient)?(?: of)?|"
            r"partial pressure(?: of)?|p_(?:partial|sat|reference))"
            r"\s*[_(:]?\s*([A-Z][A-Za-z0-9.]*)\)?",
            quantity_text.strip(),
            re.I,
        )
        if match and parse_species_formula(match.group(1)) is not None:
            text_matches.add(match.group(1))
    component_keys = {
        match.group(1)
        for key in values
        if (match := re.fullmatch(
            r"(?:a|gamma|activity|p_partial|p_sat|p_reference)_([A-Za-z][A-Za-z0-9.]*)",
            str(key),
        ))
        and parse_species_formula(match.group(1)) is not None
    }
    text_matches.update(component_keys)

    marker = re.compile(
        r"(?:^|_)(?:a|gamma|activity|p_partial|p_sat|p_reference)_", re.I
    )
    if marker.search(observation_id):
        text_matches.update(
            formula
            for formula in candidates
            if re.search(
                rf"(?:^|_)(?:a|gamma|activity|p_partial|p_sat|p_reference)_"
                rf"{re.escape(formula)}(?:_|$)",
                observation_id,
                re.I,
            )
        )
    else:
        text_matches.update(
            formula
            for formula in candidates
            if re.search(
                rf"(?:^|_){re.escape(formula)}_"
                r"(?:activity|gamma|p_partial|p_sat|p_reference)(?:_|$)",
                observation_id,
                re.I,
            )
        )
    return next(iter(text_matches)) if len(text_matches) == 1 else None


def evaluate_extract(
    path: Path,
    doc: object,
    *,
    corpus: Path,
    validator_errors: list[str] | None = None,
    yaml_issues: list[tuple[str, int, str]] | None = None,
    extracts_dir: Path | None = None,
) -> GateReport:
    """Run hard and soft mechanical checks for one already-parsed extract."""
    report = GateReport()
    _add(report, "schema/fidelity", "HARD", list(validator_errors or []))
    _add(
        report,
        "yaml-source",
        "HARD",
        [
            f"line {line}: {kind}: {detail}"
            for kind, line, detail in (yaml_issues or [])
        ],
    )

    migration_issues = _migrate_one(path, doc, report)
    _add(report, "isolated-migration", "HARD", migration_issues)
    source_id = path.stem
    source = doc.get("source") if isinstance(doc, Mapping) else None
    corpus_source_id = (
        source.get("corpus_source_id") if isinstance(source, Mapping) else None
    )
    source_ids = [source_id]
    if isinstance(corpus_source_id, str):
        source_ids.append(corpus_source_id)
    page_count, pdf_error = _pdf_page_count(corpus, source_ids)
    (
        locator_hard,
        locator_page_issues,
        locator_pdf_soft,
        locator_legacy_soft,
        locator_verification,
    ) = _locator_issues(
        doc,
        pdf_page_count=page_count,
        pdf_error=pdf_error,
        legacy_missing_index=_legacy_missing_locator_index(doc),
    )
    _add(report, "locator", "HARD", locator_hard)
    _add(report, "locator-page-offset", "HARD", locator_page_issues)
    _add(report, "locator-pdf", "SOFT", locator_pdf_soft)
    _add(report, "locator-legacy", "SOFT", locator_legacy_soft)
    _add(report, "locator-verification", "SOFT", locator_verification)
    _add(
        report,
        "composition",
        "HARD",
        _composition_issues(doc, report.composition_drops),
    )
    _add(report, "zero-uncertainty", "HARD", _uncertainty_issues(doc))

    report.tables = _capture_report(doc, corpus)
    low_coverage = [
        f"{table.table} {table.percent:.1f}% "
        f"({table.extract_values}/{table.numeric_cells} numeric cells)"
        for table in report.tables
        if table.percent < 80
    ]
    _add(report, "capture", "SOFT", low_coverage)

    duplicates = _duplicate_issues(
        path,
        doc,
        extracts_dir or path.parent,
    )
    _add(report, "duplicate", "SOFT", duplicates)
    _add(report, "deferred-figure", "SOFT", _deferred_figure_issues(doc))
    return report
