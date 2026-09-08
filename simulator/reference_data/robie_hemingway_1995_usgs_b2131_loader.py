"""Native USGS Bulletin 2131 records; exact printed temperatures only."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


COMPILATION_ROOT = Path(__file__).resolve().parents[2] / "data/literature/compilations/robie-hemingway-1995-usgs-b2131"


class PrintedTemperatureUnavailable(LookupError):
    """Requested temperature has no recoverable row in the printed grid."""


class UntranscribedTableError(LookupError):
    """The source table has no complete, recoverable transcription."""


class OcrSuspectTableValueError(LookupError):
    """The requested public result contains OCR-suspect numeric data."""


def load_manifest(root: Path = COMPILATION_ROOT) -> dict:
    return yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))


def _has_ocr_suspect_value(value) -> bool:
    if isinstance(value, dict):
        if value.get("ocr_suspect") is True:
            return True
        return any(_has_ocr_suspect_value(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_ocr_suspect_value(child) for child in value)
    return False


def load_records(root: Path = COMPILATION_ROOT, *, include_ocr_suspect: bool = False):
    """Yield native records, including explicitly identified transcription gaps."""
    for entry in load_manifest(root)["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record identity differs from manifest: {entry['path']}")
        if _has_ocr_suspect_value(record) and not include_ocr_suspect:
            raise OcrSuspectTableValueError(
                f"{record['record_id']} contains OCR-suspect values; "
                "pass include_ocr_suspect=True for explicit inspection"
            )
        yield record


def lookup_temperature(
    record_id: str,
    temperature: float,
    root: Path = COMPILATION_ROOT,
    *,
    include_ocr_suspect: bool = False,
) -> tuple[dict, ...]:
    """Return all rows at the exact temperature, retaining transition duplicates.

    Values and OCR flags are returned unchanged. A numeric value with an OCR
    suspicion is not promoted to verified data. No interpolation or correction.
    """
    entry = next((entry for entry in load_manifest(root)["entries"] if entry["record_id"] == record_id), None)
    if entry is None:
        raise KeyError(record_id)
    record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
    if record.get("transcription_status") == "untranscribed":
        raise UntranscribedTableError(record_id)
    if record.get("table_kind") == "reference_state_298K":
        if any(cell["value"] is not None and cell["value"] == temperature for cell in record["temperature_grid"]):
            matches = tuple(record["rows"])
            if _has_ocr_suspect_value(matches) and not include_ocr_suspect:
                raise OcrSuspectTableValueError(
                    f"{record_id} at {temperature!r} contains OCR-suspect values; "
                    "pass include_ocr_suspect=True for explicit inspection"
                )
            return matches
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not the printed reference temperature")
    if record.get("table_kind") != "high_temperature":
        raise PrintedTemperatureUnavailable(f"{record_id}: no printed temperature grid")
    matches = tuple(row for row in record["rows"] if row["cells"]["temperature"]["value"] is not None
                    and row["cells"]["temperature"]["value"] == temperature)
    if not matches:
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not in the recoverable printed grid")
    if _has_ocr_suspect_value(matches) and not include_ocr_suspect:
        raise OcrSuspectTableValueError(
            f"{record_id} at {temperature!r} contains OCR-suspect values; "
            "pass include_ocr_suspect=True for explicit inspection"
        )
    return matches
