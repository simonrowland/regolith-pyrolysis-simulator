"""Lossless loader for Robie, Hemingway and Fisher, USGS Bulletin 1452."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml


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
_FOOTNOTE_SUFFIX_RE = re.compile(
    r"^(?P<number>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
    r"\s+(?P<markers>[*•†‡]+)$"
)


class Bulletin1452LookupError(LookupError):
    """Base class for typed Bulletin 1452 lookup refusals."""


class UnknownRecordError(Bulletin1452LookupError):
    """The requested census record does not exist."""


class UntranscribedTableError(Bulletin1452LookupError):
    """The OCR text layer did not support a lossless table transcription."""


class TemperatureNotInPrintedGridError(Bulletin1452LookupError):
    """The requested temperature is not an exact printed grid value."""


@dataclass(frozen=True)
class PublishedNumber:
    """A printed OCR token paired with a parsed float when unambiguous."""

    as_published: str
    value: float | None
    ocr_suspect: bool
    footnote_markers: tuple[str, ...]

    @classmethod
    def from_mapping(cls, item: Mapping[str, Any]) -> "PublishedNumber":
        number = cls(
            as_published=str(item["as_published"]),
            value=None if item["value"] is None else float(item["value"]),
            ocr_suspect=bool(item["ocr_suspect"]),
            footnote_markers=tuple(str(marker) for marker in item["footnote_markers"]),
        )
        number.validate_round_trip()
        return number

    def validate_round_trip(self) -> None:
        numeric_token = self.as_published
        suffix = _FOOTNOTE_SUFFIX_RE.fullmatch(numeric_token)
        if suffix:
            numeric_token = suffix.group("number")
        token_is_numeric = _NUMBER_RE.fullmatch(numeric_token) is not None
        if self.value is None:
            if token_is_numeric:
                raise ValueError(f"numeric token lost its parsed value: {self.as_published!r}")
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
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    if manifest["source_id"] != SOURCE_ID or manifest["compilation_role"] != ROLE:
        raise ValueError("Bulletin 1452 manifest identity or role differs from the loader contract")
    return manifest


def load_records(root: Path = COMPILATION_ROOT) -> Iterator[dict[str, Any]]:
    manifest = load_manifest(root)
    for entry in manifest["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record id differs from manifest: {entry['path']}")
        if record["source_sha256"] != SOURCE_SHA256 or record["compilation_role"] != ROLE:
            raise ValueError(f"source identity or role differs from manifest: {entry['path']}")
        validate_record_round_trip(record)
        yield record


def load_record(record_id: str, root: Path = COMPILATION_ROOT) -> dict[str, Any]:
    for record in load_records(root):
        if record["record_id"] == record_id:
            return record
    raise UnknownRecordError(f"unknown Bulletin 1452 record: {record_id}")


def iter_published_numbers(record: Mapping[str, Any]) -> Iterator[PublishedNumber]:
    yield PublishedNumber.from_mapping(record["formula_weight"])
    for item in record["uncertainty_values"]:
        yield PublishedNumber.from_mapping(item)
    for row in record["rows"]:
        for column in record["column_ids"]:
            yield PublishedNumber.from_mapping(row["cells"][column])


def validate_record_round_trip(record: Mapping[str, Any]) -> None:
    list(iter_published_numbers(record))
    rows = record["rows"]
    if record["row_count"] != len(rows):
        raise ValueError(f"row count differs in {record['record_id']}")
    grid = [row["cells"]["temperature"] for row in rows]
    if record["temperature_grid"] != grid:
        raise ValueError(f"temperature grid differs from printed rows in {record['record_id']}")
    if record["transcription_status"] == "transcribed":
        if not rows:
            raise ValueError(f"transcribed record has no rows: {record['record_id']}")
        expected = set(record["column_ids"])
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

    record = load_record(record_id, root)
    if record["transcription_status"] != "transcribed":
        reasons = "; ".join(record["untranscribed_reasons"])
        raise UntranscribedTableError(f"{record_id} is untranscribed: {reasons}")
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
        return matches
    available = tuple(
        row["cells"]["temperature"]["as_published"]
        for row in record["rows"]
        if row["cells"]["temperature"]["value"] is not None
    )
    raise TemperatureNotInPrintedGridError(
        f"{temperature!r} is not an exact printed temperature for {record_id}; "
        f"available parsed grid tokens: {available}"
    )
