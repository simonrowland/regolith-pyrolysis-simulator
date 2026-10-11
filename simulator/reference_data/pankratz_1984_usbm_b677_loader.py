"""Native USBM Bulletin 677 records; exact printed temperatures only."""

from __future__ import annotations

import json
from functools import lru_cache
from decimal import Decimal
from pathlib import Path

from simulator.yaml_cache import load_cached_safe_yaml
from simulator.reference_data.published_curve_gates import three_cell_gate_passes


COMPILATION_ROOT = Path(__file__).resolve().parents[2] / "data/literature/compilations/pankratz-1984-usbm-b677"


class PrintedTemperatureUnavailable(LookupError):
    """Requested temperature is absent from the printed table grid."""


class UntranscribedTableError(LookupError):
    """The source table has no complete recoverable transcription."""


@lru_cache(maxsize=None)
def load_manifest(root: Path = COMPILATION_ROOT) -> dict:
    return load_cached_safe_yaml((root / "manifest.yaml").read_text(encoding="utf-8"))


def load_records(root: Path = COMPILATION_ROOT):
    for entry in load_manifest(root)["entries"]:
        record = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if record["record_id"] != entry["record_id"]:
            raise ValueError(f"record identity differs from manifest: {entry['path']}")
        yield record


def load_published_curves(root: Path = COMPILATION_ROOT) -> tuple[dict, ...]:
    """Load curated equation records and gate them against their printed table."""
    records = {record["record_id"]: record for record in load_records(root)}
    curves = json.loads((root / "source/published-curves.json").read_text(encoding="utf-8"))
    results = []
    for curve in curves:
        record = records[curve["source_record_id"]]
        residuals = []
        for row_index, temperature in zip(curve["source_cell_rows"], curve["gate_temperatures_K"]):
            bound_row = record["rows"][row_index]
            cells = bound_row["cells"]
            if cells[0]["value"] != temperature:
                results.append({**curve, "record_kind": "published_curve_fit", "gate": {
                    "status": "transcription_gate_failed", "reason": "gate_row_temperature_mismatch", "residuals": []}})
                break
            duplicate_rows = [row for row in record["rows"] if row["cells"][0]["value"] == temperature]
            if len(duplicate_rows) > 1:
                phase_by_position = ("c", "l")
                phase = phase_by_position[duplicate_rows.index(bound_row)] if duplicate_rows.index(bound_row) < 2 else None
                if phase != curve["phase"]:
                    results.append({**curve, "record_kind": "published_curve_fit", "gate": {
                        "status": "transcription_gate_failed", "reason": "gate_row_phase_mismatch", "residuals": []}})
                    break
            predicted = _evaluate(curve, temperature)
            observed = Decimal(str(cells[curve["source_cell_column"]]["value"]))
            if curve["quantity"] == "heat_content_increment":
                # Bulletin table enthalpies are kcal/mol; the printed equation is cal/mol.
                observed *= 1000
            tolerance = max(abs(observed) * Decimal("0.005"), Decimal("2") * _last_digit(
                cells[curve["source_cell_column"]]["raw"]
            ) * (1000 if curve["quantity"] == "heat_content_increment" else 1))
            residuals.append({
                "temperature_K": temperature,
                "predicted": float(predicted),
                "observed": float(observed),
                "residual": float(predicted - observed),
                "tolerance": float(tolerance),
            })
        passed = three_cell_gate_passes(residuals)
        if len(residuals) != len(curve["source_cell_rows"]):
            continue
        full_range = (_full_range_heat_check(curve, record)
                      if curve["quantity"] == "heat_content_increment" else None)
        results.append({
            **curve,
            "record_kind": "published_curve_fit",
            "gate": {
                "status": "passed" if passed else "transcription_gate_failed",
                "kind": "printed_heat_content_cells" if curve["quantity"] == "heat_content_increment" else "printed_heat_capacity_cells",
                "residuals": residuals,
            },
            **({"full_range_check": full_range} if full_range is not None else {}),
        })
    return tuple(results)


def _evaluate(curve: dict, temperature: int) -> Decimal:
    coefficients = [Decimal(token) for token in curve["coefficients_as_printed"]]
    if curve["quantity"] == "heat_content_increment":
        constant, linear, quadratic = coefficients
        return constant + linear * temperature + quadratic * temperature**2
    value = coefficients[0]
    if len(coefficients) > 1:
        value += coefficients[1] * temperature
    return value


def _last_digit(token: str) -> Decimal:
    return Decimal(1).scaleb(-len(token.partition(".")[2]))


def _full_range_heat_check(curve: dict, record: dict) -> dict:
    low, high = curve["temperature_range_K"]
    column = curve["source_cell_column"]
    accuracy_percent = Decimal(str(float(curve["accuracy_as_printed"].split()[0]) / 100)) if curve["accuracy_as_printed"] else Decimal("0.005")
    rows = []
    for row in record["rows"]:
        cells = row["cells"]
        temperature = int(cells[0]["value"])
        if temperature is None or not low <= temperature <= high:
            continue
        duplicates = [item for item in record["rows"] if item["cells"][0]["value"] == temperature]
        if len(duplicates) > 1:
            phase = ("c", "l")[duplicates.index(row)] if duplicates.index(row) < 2 else None
            if phase != curve["phase"]:
                continue
        observed = Decimal(str(cells[column]["value"]))
        if curve["quantity"] == "heat_content_increment":
            observed *= 1000
        predicted = _evaluate(curve, temperature)
        tolerance = max(abs(observed) * accuracy_percent,
                        Decimal(2) * _last_digit(cells[column]["raw"]) *
                        (1000 if curve["quantity"] == "heat_content_increment" else 1))
        rows.append((temperature, predicted - observed, tolerance))
    worst = max(rows, key=lambda item: abs(item[1]), default=None)
    return {"n_cells": len(rows),
            "n_outside_tolerance": sum(abs(residual) > tolerance for _, residual, tolerance in rows),
            "worst_residual": None if worst is None else float(worst[1]),
            "worst_T": None if worst is None else worst[0]}


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
