"""Native Kelley (1960) USBM Bulletin 584 exact-grid records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
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


def load_published_curves(root: Path = COMPILATION_ROOT) -> tuple[dict[str, Any], ...]:
    """Load curated published equations, gated against the source's printed cells."""
    curves = json.loads((root / "source/published-curves.json").read_text(encoding="utf-8"))
    by_id = {record["record_id"]: record for record in load_records(root, include_ocr_suspect=True)}
    results = []
    for curve in curves:
        result = dict(curve)
        result["record_kind"] = "published_curve_fit"
        if curve["quantity"] == "heat_content_increment":
            results.append(_gate_heat_content(result, by_id[curve["source_record_id"]]))
        elif curve["quantity"] == "heat_capacity":
            results.append(_gate_derivative_cp(result, curves, by_id))
        else:
            results.append({**result, "gate": {"status": "transcription_gate_failed", "reason": "no_independent_check"}})
    return tuple(results)


def _number(token: str) -> float:
    return float(_decimal(token))


def _decimal(token: str) -> Decimal:
    normalized = token.replace(",", "").replace(" ", "")
    mantissa, marker, exponent = normalized.partition("×10^")
    if marker:
        return Decimal(mantissa) * Decimal(10) ** int(exponent)
    return Decimal(normalized)


def _evaluate_heat_content(curve: dict[str, Any], temperature: float) -> float:
    powers = {"a*T": 1, "b*T^2": 2, "c*T^-1": -1, "d": 0}
    return sum(
        _number(coefficient) * temperature**power
        for coefficient, term in zip(curve["coefficients_as_printed"], curve["terms"])
        for power in (powers[term],)
    )


def _gate_heat_content(curve: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    residuals = []
    for cell in curve["gate_cells"]:
        predicted = _evaluate_heat_content(curve, cell["temperature_K"])
        if "source_row_index" in cell:
            printed = record["rows"][cell["source_row_index"]]["cells"]
            if printed["temperature"]["value"] != cell["temperature_K"]:
                raise ValueError("published-curve gate row does not match its temperature")
        else:
            printed = next(
                row["cells"]
                for row in record["rows"]
                if row["cells"]["temperature"]["value"] == cell["temperature_K"]
            )
        observed = printed["heat_content"]["value"]
        accuracy_percent = (
            float(curve["accuracy_as_printed"].split()[0]) / 100
            if curve["accuracy_as_printed"]
            else 0.005
        )
        accuracy = accuracy_percent * abs(observed)
        tolerance = max(accuracy, 2 * _last_digit_unit(printed["heat_content"]["raw"]))
        residuals.append({"temperature_K": cell["temperature_K"], "predicted": predicted,
                          "observed": observed, "residual": predicted - observed,
                          "tolerance": tolerance})
    passed = len(residuals) >= 3 and all(abs(row["residual"]) <= row["tolerance"] for row in residuals)
    return {**curve, "gate": {"status": "passed" if passed else "transcription_gate_failed",
                              "kind": "printed_heat_content_cells",
                              "reason": None if passed else "transcription_gate_failed" if len(residuals) >= 3 else "no_independent_check",
                              "residuals": residuals}}


def _printed_decimals(token: str) -> int:
    return len(token.replace(",", "").partition(".")[2])


def _last_digit_unit(token: str) -> float:
    normalized = token.replace(",", "").replace(" ", "")
    mantissa, marker, exponent = normalized.partition("×10^")
    exponent_value = int(exponent) if marker else 0
    decimal_places = len(mantissa.partition(".")[2])
    return float(Decimal(10) ** (exponent_value - decimal_places))


def _gate_derivative_cp(curve: dict[str, Any], curves: list[dict[str, Any]],
                        records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    heat = curves[curve["derivative_of_equation_index"]]
    gated_heat = _gate_heat_content(heat, records[heat["source_record_id"]])
    if gated_heat["gate"]["status"] != "passed":
        return {**curve, "gate": {"status": "transcription_gate_failed", "reason": "no_independent_check",
                                  "heat_gate": gated_heat["gate"]}}
    derivative_terms = {"a*T": "a", "b*T^2": "b*T", "c*T^-1": "c*T^-2"}
    expected_by_term = {}
    for term, coefficient in zip(heat["terms"], heat["coefficients_as_printed"]):
        derivative_term = derivative_terms.get(term)
        if derivative_term:
            multiplier = 2 if term == "b*T^2" else -1 if term == "c*T^-1" else 1
            expected_by_term[derivative_term] = multiplier * _decimal(coefficient)
    actual = tuple(map(_decimal, curve["coefficients_as_printed"]))
    expected = tuple(expected_by_term[term] for term in curve["terms"])
    residuals = tuple(got - want for got, want in zip(actual, expected))
    matches = len(actual) == len(expected) and all(
        abs(delta) <= Decimal(str(_last_digit_unit(token)))
        for delta, token in zip(residuals, curve["coefficients_as_printed"])
    )
    return {**curve, "gate": {"status": "passed" if matches else "transcription_gate_failed",
                              "kind": "analytic_derivative_of_gated_H",
                              "reason": None if matches else "derivative_mismatch",
                              "coefficient_residuals": tuple(float(value) for value in residuals)}}


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
