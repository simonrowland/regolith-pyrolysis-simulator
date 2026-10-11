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
            cells = record["rows"][row_index]["cells"]
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
        results.append({
            **curve,
            "record_kind": "published_curve_fit",
            "gate": {
                "status": "passed" if passed else "transcription_gate_failed",
                "kind": "printed_heat_content_cells" if curve["quantity"] == "heat_content_increment" else "printed_heat_capacity_cells",
                "residuals": residuals,
            },
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
