"""Native Kelley (1960) USBM Bulletin 584 exact-grid records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from simulator.yaml_cache import load_cached_safe_yaml


COMPILATION_ROOT = Path(__file__).resolve().parents[2] / "data/literature/compilations/kelley-1960-usbm-b584"


class PrintedTemperatureUnavailable(LookupError):
    """Requested temperature is absent from the printed table grid."""


@dataclass(frozen=True)
class OCRSuspectCell:
    source_row_index: int
    panel_index: int
    column: str
    raw_ocr_token: str
    corrected_value: float | None
    reason: str


class OCRSuspectRow(LookupError):
    """Requested record or row contains one or more OCR-suspect cells."""

    def __init__(
        self,
        *,
        record_id: str,
        printed_page: int,
        suspect_cells: tuple[OCRSuspectCell, ...],
    ) -> None:
        first = suspect_cells[0]
        self.record_id = record_id
        self.printed_page = printed_page
        self.source_row_index = first.source_row_index
        self.panel_index = first.panel_index
        self.column = first.column
        self.raw_ocr_token = first.raw_ocr_token
        self.corrected_value = first.corrected_value
        self.reason = first.reason
        self.suspect_cells = suspect_cells
        columns = ", ".join(sorted({cell.column for cell in suspect_cells}))
        super().__init__(
            f"{record_id} (printed page {printed_page}) has OCR-suspect cells: {columns}"
        )


def load_manifest(root: Path = COMPILATION_ROOT) -> dict:
    return load_cached_safe_yaml((root / "manifest.yaml").read_text(encoding="utf-8"))


def _suspect_cells(record: dict[str, Any], rows: Iterable[dict[str, Any]]) -> tuple[OCRSuspectCell, ...]:
    corrections = {
        (item["source_row_index"], item["panel_index"], item["column"]): item
        for item in record["corrections"]
    }
    suspect_cells = []
    for row in rows:
        for column, cell in row["cells"].items():
            if not cell["ocr_suspect"]:
                continue
            correction = corrections.get((row["source_row_index"], row["panel_index"], column))
            suspect_cells.append(
                OCRSuspectCell(
                    source_row_index=row["source_row_index"],
                    panel_index=row["panel_index"],
                    column=column,
                    raw_ocr_token=cell["raw"],
                    corrected_value=cell["value"] if correction is not None else None,
                    reason=(
                        f"{correction['basis']}; image-verified correction remains OCR-suspect"
                        if correction is not None
                        else cell["ocr_check"]
                    ),
                )
            )
    return tuple(
        sorted(
            suspect_cells,
            key=lambda item: (
                item.corrected_value is None,
                item.source_row_index,
                item.panel_index,
                item.column,
            ),
        )
    )


def _raise_for_ocr_suspect(record: dict[str, Any], rows: Iterable[dict[str, Any]]) -> None:
    suspect_cells = _suspect_cells(record, rows)
    if suspect_cells:
        raise OCRSuspectRow(
            record_id=record["record_id"],
            printed_page=record["page"],
            suspect_cells=suspect_cells,
        )


def load_records(root: Path = COMPILATION_ROOT, *, include_ocr_suspect: bool = False):
    for entry in load_manifest(root)["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record identity differs from manifest: {entry['path']}")
        suspect_cells = _suspect_cells(record, record["rows"])
        record["contains_ocr_suspect_cells"] = bool(suspect_cells)
        if suspect_cells and not include_ocr_suspect:
            raise OCRSuspectRow(
                record_id=record["record_id"],
                printed_page=record["page"],
                suspect_cells=suspect_cells,
            )
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
    _raise_for_ocr_suspect(record, matches)
    return matches
