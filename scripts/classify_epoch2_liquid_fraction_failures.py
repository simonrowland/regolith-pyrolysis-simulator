#!/usr/bin/env python3
"""Classify retained epoch-2 liquid-fraction failures for the grind controller.

The grind-campaign controller consumes this diagnostic-only report.  Replaying
these inputs through the public backend API requires a live alphaMELTS process,
so this tool classifies persisted payloads, epoch-1 phase references, and any
persisted post-epoch replay outcome.  It never executes, queues, retries, or
mutates grind work.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


BELOW_LIQUIDUS_HONEST_REFUSAL = "below-liquidus-honest-refusal"
SOLIDUS_MISSING = "solidus-missing"
REFERENCE_MISSING = "reference-missing"
NOW_RESOLVED = "now-resolved"
UNCLASSIFIED_PRESERVING_RAW = "unclassified-preserving-raw"
CLASSIFICATIONS = (
    BELOW_LIQUIDUS_HONEST_REFUSAL,
    SOLIDUS_MISSING,
    REFERENCE_MISSING,
    NOW_RESOLVED,
    UNCLASSIFIED_PRESERVING_RAW,
)

RETAINED_REASON = "LiquidFractionInvalidError"

# simulator/melt_backend/alphamelts.py:85-99 defines exact backend reason
# tokens; no_convergence is the typed out-of-domain outcome used here.
HONEST_REFUSAL_REASONS = frozenset({"no_convergence", "not_converged"})

# simulator/melt_backend/alphamelts.py:1017-1035 rejects missing, non-finite,
# or mismatched liquid fractions. simulator/melt_backend/base.py:512-533 then
# enforces the public successful-result liquid-fraction contract.
_CONTRACT_PROVENANCE = (
    "simulator/melt_backend/alphamelts.py:1017-1035",
    "simulator/melt_backend/base.py:512-533",
)


def _normalise(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _number(row: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = _finite_float(row.get(name))
        if value is not None:
            return value
    return None


def _reason(row: Mapping[str, Any], prefix: str = "") -> str:
    for name in (
        f"{prefix}reason",
        f"{prefix}refusal_reason",
        f"{prefix}backend_status_reason",
        f"{prefix}failure_reason_code",
    ):
        if row.get(name) not in (None, ""):
            return _normalise(row[name])
    return ""


def _is_retained(row: Mapping[str, Any]) -> bool:
    epoch = row.get("engine_epoch", 2)
    try:
        if int(epoch) != 2:
            return False
    except (TypeError, ValueError):
        return False
    original_reason = _reason(row)
    return original_reason == _normalise(RETAINED_REASON)


def classify_retained_row(row: Mapping[str, Any]) -> str:
    """Classify one retained row from explicit persisted evidence."""
    current_kind = _normalise(row.get("current_status_kind"))
    current_status = _normalise(row.get("current_status"))
    current_reason = _reason(row, "current_")
    temperature_C = _number(row, "temperature_C", "grid_temperature_C")
    liquidus_C = _number(
        row,
        "reference_liquidus_C",
        "liquidus_C",
        "curve_liquidus_T_C",
        "finder_liquidus_T_C",
        "alpha_liquidus_T_C",
        "generic_liquidus_T_C",
    )
    solidus_C = _number(
        row,
        "reference_solidus_C",
        "solidus_C",
        "curve_solidus_T_C",
        "finder_solidus_T_C",
        "alpha_solidus_T_C",
    )

    if current_kind == "success" or current_status == "ok":
        return NOW_RESOLVED
    if (
        current_kind == "refusal"
        and current_reason in HONEST_REFUSAL_REASONS
        and temperature_C is not None
        and liquidus_C is not None
        and temperature_C < liquidus_C
    ):
        return BELOW_LIQUIDUS_HONEST_REFUSAL
    if liquidus_C is None:
        return REFERENCE_MISSING
    if (
        temperature_C is not None
        and temperature_C < liquidus_C
        and solidus_C is None
    ):
        return SOLIDUS_MISSING
    return UNCLASSIFIED_PRESERVING_RAW


def _sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    try:
        grid_key_id = int(row.get("grid_key_id", -1))
    except (TypeError, ValueError):
        grid_key_id = -1
    return (
        grid_key_id,
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str),
    )


def build_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return deterministic counts and preserve every unclassified raw row."""
    counts = {classification: 0 for classification in CLASSIFICATIONS}
    classified_rows: list[dict[str, Any]] = []
    unclassified: list[dict[str, Any]] = []
    retained_rows = sorted((row for row in rows if _is_retained(row)), key=_sort_key)
    for row in retained_rows:
        classification = classify_retained_row(row)
        counts[classification] += 1
        classified_rows.append(
            {
                "classification": classification,
                "engine_epoch": int(row.get("engine_epoch", 2)),
                "expedited_key": row.get("expedited_key"),
                "grid_key_id": row.get("grid_key_id"),
            }
        )
        if classification == UNCLASSIFIED_PRESERVING_RAW:
            unclassified.append({"raw": dict(row)})
    return {
        "classification_mode": "recorded-payload-no-live-engine",
        "consumer": "grind-campaign-controller",
        "contract_provenance": list(_CONTRACT_PROVENANCE),
        "counts": counts,
        "rows": classified_rows,
        "total_retained": len(retained_rows),
        "unclassified": unclassified,
    }


def _coalesce(row: sqlite3.Row | None, *names: str) -> Any:
    if row is None:
        return None
    keys = set(row.keys())
    for name in names:
        if name in keys and row[name] is not None:
            return row[name]
    return None


def load_retained_rows(
    db_path: Path,
    *,
    engine_epoch: int = 2,
    reference_epoch: int = 1,
) -> list[dict[str, Any]]:
    """Load retained rows and references through a read-only SQLite handle."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        retained = connection.execute(
            "SELECT o.id, o.grid_key_id, o.expedited_key, o.engine_epoch, "
            "o.status, o.status_kind, o.refusal_reason, o.raw_payload, "
            "o.alpha_backend_status_reason, o.alpha_backend_diagnostics_json, "
            "g.temperature_C FROM alphamelts_outputs o "
            "JOIN grid_keys g ON g.id = o.grid_key_id "
            "WHERE o.engine_epoch = ? AND o.status = 'error' "
            "AND COALESCE(o.alpha_backend_status_reason, o.refusal_reason) = ? "
            "ORDER BY o.grid_key_id, o.id",
            (engine_epoch, RETAINED_REASON),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for source in retained:
            reference = connection.execute(
                "SELECT curve_liquidus_T_C, finder_liquidus_T_C, "
                "alpha_liquidus_T_C, generic_liquidus_T_C, "
                "curve_solidus_T_C, finder_solidus_T_C, alpha_solidus_T_C "
                "FROM alphamelts_outputs WHERE grid_key_id = ? "
                "AND engine_epoch = ? ORDER BY id DESC LIMIT 1",
                (source["grid_key_id"], reference_epoch),
            ).fetchone()
            current = connection.execute(
                "SELECT engine_epoch, status, status_kind, refusal_reason, "
                "alpha_backend_status_reason FROM alphamelts_outputs "
                "WHERE grid_key_id = ? AND engine_epoch > ? "
                "ORDER BY engine_epoch DESC, id DESC LIMIT 1",
                (source["grid_key_id"], engine_epoch),
            ).fetchone()
            row = dict(source)
            row["reference_liquidus_C"] = _coalesce(
                reference,
                "curve_liquidus_T_C",
                "finder_liquidus_T_C",
                "alpha_liquidus_T_C",
                "generic_liquidus_T_C",
            )
            row["reference_solidus_C"] = _coalesce(
                reference,
                "curve_solidus_T_C",
                "finder_solidus_T_C",
                "alpha_solidus_T_C",
            )
            row["current_engine_epoch"] = _coalesce(current, "engine_epoch")
            row["current_status"] = _coalesce(current, "status")
            row["current_status_kind"] = _coalesce(current, "status_kind")
            row["current_refusal_reason"] = _coalesce(current, "refusal_reason")
            row["current_backend_status_reason"] = _coalesce(
                current, "alpha_backend_status_reason"
            )
            result.append(row)
        return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, required=True)
    result.add_argument("--engine-epoch", type=int, default=2)
    result.add_argument("--reference-epoch", type=int, default=1)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.engine_epoch != 2:
        raise SystemExit("--engine-epoch must be 2 for the retained taxonomy")
    report = build_report(
        load_retained_rows(
            args.db,
            engine_epoch=args.engine_epoch,
            reference_epoch=args.reference_epoch,
        )
    )
    print(json.dumps(report, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
