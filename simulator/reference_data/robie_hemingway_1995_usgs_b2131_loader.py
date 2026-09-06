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


def load_manifest(root: Path = COMPILATION_ROOT) -> dict:
    return yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))


def load_records(root: Path = COMPILATION_ROOT):
    """Yield native records, including explicitly identified transcription gaps."""
    for entry in load_manifest(root)["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record identity differs from manifest: {entry['path']}")
        yield record


def lookup_temperature(record_id: str, temperature: float, root: Path = COMPILATION_ROOT) -> tuple[dict, ...]:
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
            return tuple(record["rows"])
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not the printed reference temperature")
    if record.get("table_kind") != "high_temperature":
        raise PrintedTemperatureUnavailable(f"{record_id}: no printed temperature grid")
    matches = tuple(row for row in record["rows"] if row["cells"]["temperature"]["value"] is not None
                    and row["cells"]["temperature"]["value"] == temperature)
    if not matches:
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not in the recoverable printed grid")
    return matches
