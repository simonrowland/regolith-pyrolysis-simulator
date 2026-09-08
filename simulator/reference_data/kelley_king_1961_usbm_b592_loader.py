"""Native Kelley & King (1961) USBM Bulletin 592 exact-grid records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml


COMPILATION_ROOT = Path(__file__).resolve().parents[2] / "data/literature/compilations/kelley-king-1961-usbm-b592"


class PrintedTemperatureUnavailable(LookupError):
    """Requested temperature is absent from the printed table grid."""


@dataclass(frozen=True)
class OCRSuspectCell:
    source_row_index: int
    column: str
    raw_ocr_token: str
    reason: str


class OCRSuspectRow(LookupError):
    """Requested record or result contains OCR-suspect source material."""

    def __init__(self, *, record_id: str, printed_page: int, suspect_cells: tuple[OCRSuspectCell, ...]) -> None:
        self.record_id = record_id
        self.printed_page = printed_page
        self.suspect_cells = suspect_cells
        first = suspect_cells[0]
        self.source_row_index = first.source_row_index
        self.column = first.column
        self.raw_ocr_token = first.raw_ocr_token
        self.reason = first.reason
        super().__init__(f"{record_id} (printed page {printed_page}) has OCR-suspect source material")


def _metadata_digit_ocr_candidate(token: str | None) -> bool:
    if not token:
        return False
    plain = re.sub(r"[^A-Za-z0-9.,/()]+", "", token)
    return bool(re.search(r"(?<![\d./])1(?=[A-Za-z,(]|$)", plain))


def _manifest_suspect_cells(entry: dict[str, Any]) -> tuple[OCRSuspectCell, ...]:
    suspects = []
    for field in ("formula", "formula_as_published", "name_as_published"):
        token = entry.get(field)
        if _metadata_digit_ocr_candidate(token):
            suspects.append(OCRSuspectCell(-1, field, token, "unresolved metadata digit OCR candidate"))
    if entry.get("metadata_ocr_suspect") and not suspects:
        suspects.append(
            OCRSuspectCell(
                -1,
                "substance_metadata",
                entry.get("metadata_ocr_token") or entry.get("formula_as_published") or entry.get("name_as_published") or "",
                entry.get("metadata_ocr_check") or "metadata raster disagreement",
            )
        )
    return tuple(suspects)


def load_manifest(root: Path = COMPILATION_ROOT, *, include_ocr_suspect: bool = False) -> dict:
    manifest = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    if not include_ocr_suspect:
        for entry in manifest["entries"]:
            suspects = _manifest_suspect_cells(entry)
            if suspects:
                raise OCRSuspectRow(
                    record_id=entry["record_id"],
                    printed_page=entry["source_locator"]["printed_page"],
                    suspect_cells=suspects,
                )
    return manifest


def _numeric_cells(value: Any, *, source_row_index: int = -1):
    if isinstance(value, dict):
        row_index = int(value.get("source_row_index", source_row_index))
        if "raw" in value and "value" in value:
            yield row_index, value
        for child in value.values():
            yield from _numeric_cells(child, source_row_index=row_index)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _numeric_cells(child, source_row_index=source_row_index)


def _suspect_cells(record: dict[str, Any], value: Any) -> tuple[OCRSuspectCell, ...]:
    suspects = []
    if record.get("metadata_ocr_suspect"):
        suspects.append(
            OCRSuspectCell(
                source_row_index=-1,
                column="substance_metadata",
                raw_ocr_token=record.get("substance_as_published") or "",
                reason=record.get("metadata_ocr_check") or "metadata raster disagreement",
            )
        )
    for row_index, cell in _numeric_cells(value):
        if cell.get("ocr_suspect"):
            suspects.append(
                OCRSuspectCell(
                    source_row_index=row_index,
                    column=str(cell.get("column") or "numeric_cell"),
                    raw_ocr_token=cell.get("raw") or "",
                    reason=cell.get("ocr_check") or "numeric raster disagreement",
                )
            )
    return tuple(suspects)


def _raise_for_ocr_suspect(record: dict[str, Any], value: Any) -> None:
    suspects = _suspect_cells(record, value)
    if suspects:
        raise OCRSuspectRow(record_id=record["record_id"], printed_page=record["page"], suspect_cells=suspects)


def load_records(root: Path = COMPILATION_ROOT, *, include_ocr_suspect: bool = False):
    """Yield records; suspect material requires an explicit flag and retains flags."""
    for entry in load_manifest(root, include_ocr_suspect=include_ocr_suspect)["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record identity differs from manifest: {entry['path']}")
        record["contains_ocr_suspect_cells"] = bool(_suspect_cells(record, record))
        if record["contains_ocr_suspect_cells"] and not include_ocr_suspect:
            _raise_for_ocr_suspect(record, record)
        yield record


TABLE6_CP_COLUMNS = {
    10.0: "cp_10_k",
    25.0: "cp_25_k",
    50.0: "cp_50_k",
    100.0: "cp_100_k",
    150.0: "cp_150_k",
    200.0: "cp_200_k",
    298.15: "cp_298_15_k",
}


def lookup_temperature(
    record_id: str,
    temperature: float,
    root: Path = COMPILATION_ROOT,
    *,
    include_ocr_suspect: bool = False,
) -> tuple[dict, ...]:
    """Return only exact printed-grid data; never expose suspect cells bare."""
    entry = next(
        (
            item
            for item in load_manifest(root, include_ocr_suspect=True)["entries"]
            if item["record_id"] == record_id
        ),
        None,
    )
    if entry is None:
        raise KeyError(record_id)
    record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
    grid_cell = next(
        (cell for cell in record["temperature_grid"] if cell.get("value") == temperature),
        None,
    )
    if grid_cell is None:
        raise PrintedTemperatureUnavailable(f"{record_id}: {temperature!r} is not in the printed grid")
    if record["table_number"] == 6:
        column = TABLE6_CP_COLUMNS[temperature]
        cells = record["rows"][0]["cells"]
        result = {
            "record_id": record_id,
            "temperature": grid_cell,
            "heat_capacity": cells[column],
        }
        if temperature == 298.15:
            result["entropy_at_298_15"] = {
                key: cells[key]
                for key in (
                    "entropy_third_law",
                    "entropy_spectrographic_or_molecular_constants",
                    "entropy_other_sources",
                    "entropy_recommended",
                )
            }
        if not include_ocr_suspect:
            _raise_for_ocr_suspect(record, result)
        return (result,)
    if record["table_number"] == 7:
        matches = tuple(
            row for row in record["rows"] if row["cells"]["temperature"]["value"] == temperature
        )
    else:
        matches = tuple(record["rows"])
    if not include_ocr_suspect:
        _raise_for_ocr_suspect(record, matches)
    return matches
