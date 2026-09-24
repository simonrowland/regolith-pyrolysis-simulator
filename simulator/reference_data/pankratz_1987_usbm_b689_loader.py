"""Native B689 sulfide reference tables; exact printed grids, no interpolation."""

from __future__ import annotations

import json
from pathlib import Path

from simulator.yaml_cache import load_cached_safe_yaml as _load_cached_safe_yaml


COMPILATION_ROOT = Path(__file__).resolve().parents[2] / "data/literature/compilations/pankratz-1987-usbm-b689"


class OCRSuspectRow(LookupError):
    """Source material requires explicit OCR-suspect opt-in."""


class PrintedTemperatureUnavailable(LookupError):
    """Temperature is absent from the printed grid."""


def _suspect(value):
    if isinstance(value, dict):
        return bool(value.get("ocr_suspect") or value.get("metadata_ocr_suspect")
                    or value.get("ocr_suspect_count", 0)) or any(_suspect(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_suspect(v) for v in value)
    return False


def _guard(value, record_id, include_ocr_suspect):
    if not include_ocr_suspect and _suspect(value):
        raise OCRSuspectRow(f"{record_id}: OCR-suspect source material; use include_ocr_suspect=True to inspect")


def _manifest(root):
    return _load_cached_safe_yaml((root / "manifest.yaml").read_text(encoding="utf-8"))


def _record(root, entry):
    record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
    if record["record_id"] != entry["record_id"]:
        raise ValueError(f"record identity differs from manifest: {entry['path']}")
    return record


def load_manifest(root: Path = COMPILATION_ROOT, *, include_ocr_suspect: bool = False) -> dict:
    manifest = _manifest(root)
    _guard(manifest, "manifest", include_ocr_suspect)
    return manifest


def load_records(root: Path = COMPILATION_ROOT, *, include_ocr_suspect: bool = False):
    """Yield substances only; structural and unresolved identities stay in source artifacts."""
    for entry in _manifest(root)["entries"]:
        record = _record(root, entry)
        if any(item.get("record_kind", "substance") != "substance" for item in (entry, record)):
            continue
        _guard(entry, entry["record_id"], include_ocr_suspect)
        _guard(record, record["record_id"], include_ocr_suspect)
        yield record


def lookup_temperature(record_id: str, temperature: float, root: Path = COMPILATION_ROOT,
                       *, include_ocr_suspect: bool = False) -> tuple[dict, ...]:
    entry = next((item for item in _manifest(root)["entries"] if item["record_id"] == record_id), None)
    if entry is None:
        raise KeyError(record_id)
    record = _record(root, entry)
    if any(item.get("record_kind", "substance") != "substance" for item in (entry, record)):
        raise KeyError(f"{record_id}: not a resolved substance")
    matches = tuple(row for row in record["rows"] if row["cells"]["temperature"]["value"] == temperature)
    if not matches:
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not in the printed grid")
    _guard({"metadata_ocr_suspect": entry.get("metadata_ocr_suspect") or record.get("metadata_ocr_suspect"),
            "ocr_suspect": entry.get("ocr_suspect") or record.get("ocr_suspect"),
            "rows": matches}, record_id, include_ocr_suspect)
    return matches
