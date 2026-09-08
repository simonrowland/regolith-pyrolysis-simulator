"""Native USBM Bulletin 677 records; exact printed temperatures only."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml


COMPILATION_ROOT = Path(__file__).resolve().parents[2] / "data/literature/compilations/pankratz-1984-usbm-b677"


class PrintedTemperatureUnavailable(LookupError):
    """Requested temperature is absent from the printed table grid."""


class UntranscribedTableError(LookupError):
    """The source table has no complete recoverable transcription."""


@lru_cache(maxsize=None)
def load_manifest(root: Path = COMPILATION_ROOT) -> dict:
    return yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))


def load_records(root: Path = COMPILATION_ROOT):
    for entry in load_manifest(root)["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record identity differs from manifest: {entry['path']}")
        yield record


def lookup_temperature(record_id: str, temperature: float, root: Path = COMPILATION_ROOT) -> tuple[dict, ...]:
    entry = next((item for item in load_manifest(root)["entries"] if item["record_id"] == record_id), None)
    if entry is None:
        raise KeyError(record_id)
    record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
    if record["transcription_status"] == "untranscribed":
        raise UntranscribedTableError(record_id)
    matches = tuple(
        row for row in record["rows"]
        if row["cells"] and row["cells"][0]["value"] is not None and row["cells"][0]["value"] == temperature
    )
    if not matches:
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not in the printed grid")
    return matches
