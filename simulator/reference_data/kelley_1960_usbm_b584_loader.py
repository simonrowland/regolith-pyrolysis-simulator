"""Native Kelley (1960) USBM Bulletin 584 exact-grid records."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


COMPILATION_ROOT = Path(__file__).resolve().parents[2] / "data/literature/compilations/kelley-1960-usbm-b584"


class PrintedTemperatureUnavailable(LookupError):
    """Requested temperature is absent from the printed table grid."""


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
    matches = tuple(
        row
        for row in record["rows"]
        if row["cells"]["temperature"]["value"] is not None
        and row["cells"]["temperature"]["value"] == temperature
    )
    if not matches:
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not in the printed grid")
    return matches
