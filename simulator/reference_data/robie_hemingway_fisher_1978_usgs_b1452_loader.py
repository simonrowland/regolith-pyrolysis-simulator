"""Lossless loader for Robie, Hemingway and Fisher, USGS Bulletin 1452."""

from __future__ import annotations

import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml
from simulator.yaml_cache import load_cached_safe_yaml


ROOT = Path(__file__).resolve().parents[2]
SOURCE_ID = "robie-hemingway-fisher-1978-usgs-b1452"
COMPILATION_ROOT = ROOT / "data" / "literature" / "compilations" / SOURCE_ID
SOURCE_SHA256 = "3e394cccef03515310ead8e3b363f631be3af4d60ceeec8f329ff86c74040622"
ROLE = {
    "kind": "assessed_thermodynamic_functions",
    "engine_reference_input": True,
    "validation_measurement": False,
    "scoring_eligible": False,
    "battery_refusal": "gibbs_table_not_runtime_observable",
    "circularity_warning": "Do not validate an engine against a compilation it consumes.",
}
_NUMBER_RE = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?")


def token_has_printed_shape(raw: str, column: str | None = None) -> bool:
    """Return whether ``raw`` is a numeric token for ``column`` with no glyph stripping.

    Bullets, stray dots, spaces, and letters stay in the token. They mark it
    OCR-suspect; they are never stripped to admit a different number.
    """

    numeric_token = raw.strip()
    if _NUMBER_RE.fullmatch(numeric_token) is None:
        return False
    if column == "temperature":
        try:
            parsed_temperature = Decimal(numeric_token)
        except InvalidOperation:
            return False
        return Decimal("250") <= parsed_temperature <= Decimal("2500")
    return True


class Bulletin1452LookupError(LookupError):
    """Base class for typed Bulletin 1452 lookup refusals."""


class UnknownRecordError(Bulletin1452LookupError):
    """The requested census record does not exist."""


class UntranscribedTableError(Bulletin1452LookupError):
    """The OCR text layer did not support a lossless table transcription."""


class TemperatureNotInPrintedGridError(Bulletin1452LookupError):
    """The requested temperature is not an exact printed grid value."""


class OcrSuspectGridTokenError(Bulletin1452LookupError):
    """A numeric-looking temperature token failed the printed-form checks."""


class OcrSuspectTableValueError(Bulletin1452LookupError):
    """The requested printed row contains at least one OCR-suspect value."""


@dataclass(frozen=True)
class PublishedNumber:
    """A printed OCR token paired with a parsed float when unambiguous."""

    as_published: str
    value: float | None
    ocr_suspect: bool
    footnote_markers: tuple[str, ...]

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any], column: str | None = None) -> "PublishedNumber":
        number = cls(
            as_published=str(item["as_published"]),
            value=None if item["value"] is None else float(item["value"]),
            ocr_suspect=bool(item["ocr_suspect"]),
            footnote_markers=tuple(str(marker) for marker in item["footnote_markers"]),
        )
        number.validate_round_trip(column)
        return number

    def validate_round_trip(self, column: str | None = None) -> None:
        numeric_token = self.as_published.strip()
        token_is_numeric = token_has_printed_shape(self.as_published, column)
        if self.value is None:
            if not self.ocr_suspect:
                raise ValueError(f"withheld token lacks the suspect flag: {self.as_published!r}")
            return
        if not token_is_numeric:
            raise ValueError(f"parsed value repairs an OCR token: {self.as_published!r}")
        try:
            parsed = float(Decimal(numeric_token))
        except InvalidOperation as exc:
            raise ValueError(f"invalid numeric token: {self.as_published!r}") from exc
        if parsed != self.value:
            raise ValueError(
                f"parsed value differs from published token: {self.as_published!r} -> {self.value!r}"
            )


def load_manifest(root: Path = COMPILATION_ROOT) -> dict[str, Any]:
    manifest = load_cached_safe_yaml((root / "manifest.yaml").read_text(encoding="utf-8"))
    if manifest["source_id"] != SOURCE_ID or manifest["compilation_role"] != ROLE:
        raise ValueError("Bulletin 1452 manifest identity or role differs from the loader contract")
    return manifest


def _record_has_ocr_suspect_values(record: Mapping[str, Any]) -> bool:
    numbers = [record["formula_weight"], *record["uncertainty_values"]]
    numbers.extend(
        row["cells"][column]
        for row in record["rows"]
        for column in record["column_ids"]
    )
    return any(number["ocr_suspect"] for number in numbers)


def load_records(
    root: Path = COMPILATION_ROOT,
    *,
    include_ocr_suspect: bool = False,
) -> Iterator[dict[str, Any]]:
    manifest = load_manifest(root)
    for entry in manifest["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record id differs from manifest: {entry['path']}")
        if record["source_sha256"] != SOURCE_SHA256 or record["compilation_role"] != ROLE:
            raise ValueError(f"source identity or role differs from manifest: {entry['path']}")
        validate_record_round_trip(record)
        if _record_has_ocr_suspect_values(record) and not include_ocr_suspect:
            raise OcrSuspectTableValueError(
                f"{record['record_id']} contains OCR-suspect values; "
                "pass include_ocr_suspect=True for explicit inspection"
            )
        yield record


def load_record(
    record_id: str,
    root: Path = COMPILATION_ROOT,
    *,
    include_ocr_suspect: bool = False,
) -> dict[str, Any]:
    for record in load_records(root, include_ocr_suspect=True):
        if record["record_id"] == record_id:
            if _record_has_ocr_suspect_values(record) and not include_ocr_suspect:
                raise OcrSuspectTableValueError(
                    f"{record_id} contains OCR-suspect values; "
                    "pass include_ocr_suspect=True for explicit inspection"
                )
            return record
    raise UnknownRecordError(f"unknown Bulletin 1452 record: {record_id}")


def iter_published_numbers(record: Mapping[str, Any]) -> Iterator[PublishedNumber]:
    yield PublishedNumber.from_mapping(record["formula_weight"])
    for item in record["uncertainty_values"]:
        yield PublishedNumber.from_mapping(item)
    for row in record["rows"]:
        for column in record["column_ids"]:
            yield PublishedNumber.from_mapping(row["cells"][column], column)


class _MinerUTableParser(HTMLParser):
    """Collect cell text from a MinerU ``table_body`` HTML fragment."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            text = html.unescape("".join(self._cell)).replace("\n", "")
            text = re.sub(r"[ \t]+", " ", text).strip()
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(cell.strip() for cell in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_mineru_table_html(table_body: str) -> list[list[str]]:
    """Return row-major cell strings from a MinerU HTML table, newlines removed."""

    parser = _MinerUTableParser()
    parser.feed(table_body)
    parser.close()
    return parser.rows


def validate_record_round_trip(record: Mapping[str, Any]) -> None:
    list(iter_published_numbers(record))
    rows = record["rows"]
    if record["row_count"] != len(rows):
        raise ValueError(f"row count differs in {record['record_id']}")
    column_ids = list(record["column_ids"])
    if "temperature" in column_ids:
        grid = [row["cells"]["temperature"] for row in rows]
        if record["temperature_grid"] != grid:
            raise ValueError(f"temperature grid differs from printed rows in {record['record_id']}")
    elif record["temperature_grid"]:
        raise ValueError(f"non-temperature table has a temperature grid in {record['record_id']}")
    if record["transcription_status"] == "transcribed":
        if not rows:
            raise ValueError(f"transcribed record has no rows: {record['record_id']}")
        expected = set(column_ids)
        if any(set(row["cells"]) != expected for row in rows):
            raise ValueError(f"row columns differ in {record['record_id']}")
    elif record["transcription_status"] == "untranscribed":
        if rows or not record["untranscribed_reasons"]:
            raise ValueError(f"untranscribed record contains rows or lacks a reason: {record['record_id']}")
    else:
        raise ValueError(f"unknown transcription status in {record['record_id']}")


def lookup_temperature(
    record_id: str,
    temperature: float | int | str | Decimal,
    root: Path = COMPILATION_ROOT,
) -> tuple[dict[str, Any], ...]:
    """Return every row printed at exactly ``temperature``; never interpolate."""

    record = load_record(record_id, root, include_ocr_suspect=True)
    if record["transcription_status"] != "transcribed":
        reasons = "; ".join(record["untranscribed_reasons"])
        raise UntranscribedTableError(f"{record_id} is untranscribed: {reasons}")
    if "temperature" not in record["column_ids"]:
        raise TemperatureNotInPrintedGridError(
            f"{record_id} has no printed temperature grid; available columns: {tuple(record['column_ids'])}"
        )
    try:
        requested = Decimal(str(temperature))
    except InvalidOperation as exc:
        raise TemperatureNotInPrintedGridError(
            f"{temperature!r} is not a numeric printed-grid request for {record_id}"
        ) from exc
    matches = tuple(
        row
        for row in record["rows"]
        if row["cells"]["temperature"]["value"] is not None
        and Decimal(str(row["cells"]["temperature"]["value"])) == requested
    )
    if matches:
        suspect = tuple(
            column
            for row in matches
            for column, cell in row["cells"].items()
            if cell["ocr_suspect"]
        )
        if suspect:
            raise OcrSuspectTableValueError(
                f"{record_id} at {temperature!r} contains OCR-suspect columns: {suspect}"
            )
        return matches
    for row in record["rows"]:
        cell = row["cells"]["temperature"]
        if not cell["ocr_suspect"]:
            continue
        raw = cell["as_published"].strip()
        if _NUMBER_RE.fullmatch(raw) and Decimal(raw) == requested:
            raise OcrSuspectGridTokenError(
                f"{temperature!r} matches OCR-suspect temperature token {raw!r} in {record_id}"
            )
    available = tuple(
        row["cells"]["temperature"]["as_published"]
        for row in record["rows"]
        if row["cells"]["temperature"]["value"] is not None
    )
    raise TemperatureNotInPrintedGridError(
        f"{temperature!r} is not an exact printed temperature for {record_id}; "
        f"available parsed grid tokens: {available}"
    )


def feedstock_coverage(records: list[dict[str, Any]]) -> dict[str, int]:
    """Count compilation records containing each element declared by feedstocks."""

    from simulator.accounting.formulas import load_species_formulas, resolve_species_formula

    registry = load_species_formulas(ROOT / "data" / "species_catalog.yaml")
    feedstocks = load_cached_safe_yaml(
        (ROOT / "data" / "feedstocks.yaml").read_text(encoding="utf-8")
    )
    elements = set()
    for feedstock in feedstocks.values():
        for species in feedstock.get("composition_wt_pct", {}):
            local = feedstock.get("stage0_formula_inventory", {}).get(species, {})
            formula = local.get("template_formula", species)
            elements.update(resolve_species_formula(formula, registry).elements)
    folded_symbols = {element.casefold(): element for element in elements}

    def formula_elements(formula: str) -> set[str]:
        """Read element symbols without silently repairing the published formula."""

        found = set(re.findall(r"[A-Z][a-z]?", formula)) & elements
        for token in re.findall(r"[A-Za-z]+", formula):
            if token.islower() and token.casefold() in folded_symbols:
                found.add(folded_symbols[token.casefold()])
        return found

    counts: Counter[str] = Counter()
    for record in records:
        formula = record.get("formula_as_published") or ""
        counts.update(formula_elements(formula))
    return {element: counts[element] for element in sorted(elements)}
