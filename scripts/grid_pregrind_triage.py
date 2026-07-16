#!/usr/bin/env python3
"""Classify persisted grid-pregrind non-evals for the grind controller.

SC-50: this diagnostic-only report is consumed by the grind campaign controller.
It does not execute, queue, retry, or mutate grind work.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from scripts.grid_pregrind import DEFAULT_FEEDSTOCK_ANCHORS


FAITHFUL_RUMP = "faithful-rump"
CALCULATION_BUG = "calculation-bug"
MELTS_VS_FREEZE = "melts-vs-freeze"
UNCLASSIFIED = "unclassified"
TRIAGE_CLASSES = (
    FAITHFUL_RUMP,
    CALCULATION_BUG,
    MELTS_VS_FREEZE,
    UNCLASSIFIED,
)

_FAITHFUL_RUMP_REASONS = frozenset(
    {
        "below_liquidus",
        "faithful_rump",
        "fully_solid",
        "no_liquid_phase",
        "physical_refusal",
        "rump_only",
        "silicate_window",
        "zero_component_boundary",
    }
)
_CALCULATION_BUG_REASONS = frozenset(
    {
        "executed_temperature_mismatch",
        "executed_temperature_missing",
        "missing_binary",
        "parse_empty_output",
        "subprocess_died",
        "thermoengine_equilibrium_status",
        "timeout",
    }
)
_MELTS_VS_FREEZE_REASONS = frozenset(
    {
        "alphamelts_freeze_disagreement",
        "freeze_gate_disagreement",
        "kernel_liquidus_disagree",
        "melts_vs_freeze",
        "no_convergence",
    }
)
_REASON_KEYS = frozenset(
    {
        "backend_failure_reason_code",
        "backend_status_reason",
        "classification",
        "failure_reason_code",
        "reason",
        "refusal_reason",
        "triage_class",
    }
)


def _normalise_token(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _decoded_payload(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _reason_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in _REASON_KEYS and nested is not None:
                tokens.add(_normalise_token(nested))
            if isinstance(nested, (Mapping, list, tuple)):
                tokens.update(_reason_tokens(nested))
    elif isinstance(value, (list, tuple)):
        for nested in value:
            tokens.update(_reason_tokens(nested))
    return tokens


def _finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _recorded_number(row: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _finite_float(row.get(key))
        if value is not None:
            return value
    return None


def _reported_liquidus(row: Mapping[str, Any]) -> float | None:
    value = _recorded_number(row, "generic_liquidus_T_C", "finder_liquidus_T_C")
    if value is not None:
        return value
    raw_payload = row.get("raw_payload")
    if not isinstance(raw_payload, str):
        raw_payload = json.dumps(raw_payload, sort_keys=True, default=str)
    match = re.search(
        r"found\s+the\s+liquidus\s+at\s+t\s*=\s*([-+0-9.eE]+)",
        raw_payload,
        flags=re.IGNORECASE,
    )
    return _finite_float(match.group(1)) if match else None


def classify_non_eval(row: Mapping[str, Any]) -> str:
    """Classify one persisted non-eval row using explicit recorded signals."""
    payload = _decoded_payload(row.get("raw_payload"))
    tokens = _reason_tokens(row) | _reason_tokens(payload)
    if tokens & _MELTS_VS_FREEZE_REASONS:
        return MELTS_VS_FREEZE
    melts_fraction = _recorded_number(row, "generic_liquid_fraction")
    freeze_fraction = _recorded_number(row, "finder_liquid_fraction")
    if melts_fraction is not None and freeze_fraction is not None and (
        (melts_fraction > 0.0) != (freeze_fraction > 0.0)
    ):
        return MELTS_VS_FREEZE
    if tokens & _FAITHFUL_RUMP_REASONS:
        return FAITHFUL_RUMP
    requested_temperature = _recorded_number(
        row, "generic_requested_temperature_C", "grid_temperature_C"
    )
    liquidus_temperature = _reported_liquidus(row)
    if (
        _normalise_token(row.get("status_kind", "")) == "refusal"
        and (
            melts_fraction == 0.0
            or (
                requested_temperature is not None
                and liquidus_temperature is not None
                and requested_temperature < liquidus_temperature
            )
        )
    ):
        return FAITHFUL_RUMP
    status_kind = _normalise_token(row.get("status_kind", ""))
    failure_reason = _normalise_token(row.get("failure_reason_code", ""))
    if (
        status_kind == "failure"
        or failure_reason.startswith("exception_")
        or tokens & _CALCULATION_BUG_REASONS
    ):
        return CALCULATION_BUG
    return UNCLASSIFIED


def _feedstock(row: Mapping[str, Any]) -> str:
    for key in ("feedstock_id", "feedstock", "feedstock_name"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    payload = _decoded_payload(row.get("raw_payload"))
    if isinstance(payload, Mapping):
        for key in ("feedstock_id", "feedstock", "feedstock_name"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
    return "<unassigned>"


def _is_non_eval(row: Mapping[str, Any]) -> bool:
    return not (
        _normalise_token(row.get("status_kind", "")) == "success"
        or _normalise_token(row.get("status", "")) == "ok"
    )


def _sort_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (
        _feedstock(row),
        json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str),
    )


def build_triage_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return deterministic counts, feedstock breakdown, and raw unknowns."""
    counts = {name: 0 for name in TRIAGE_CLASSES}
    by_feedstock: dict[str, dict[str, int]] = {}
    unclassified: list[dict[str, Any]] = []
    non_eval_rows = sorted((row for row in rows if _is_non_eval(row)), key=_sort_key)
    for row in non_eval_rows:
        triage_class = classify_non_eval(row)
        feedstock = _feedstock(row)
        counts[triage_class] += 1
        feedstock_counts = by_feedstock.setdefault(
            feedstock, {name: 0 for name in TRIAGE_CLASSES}
        )
        feedstock_counts[triage_class] += 1
        if triage_class == UNCLASSIFIED:
            unclassified.append({"feedstock": feedstock, "raw": dict(row)})
    return {
        "counts": counts,
        "per_feedstock": {
            feedstock: by_feedstock[feedstock] for feedstock in sorted(by_feedstock)
        },
        "total_non_eval": len(non_eval_rows),
        "unclassified": unclassified,
    }


def load_feedstock_anchors(path: Path) -> dict[str, dict[str, float]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        str(feedstock_id): {
            str(species): float(value)
            for species, value in dict(record["composition_wt_pct"]).items()
        }
        for feedstock_id, record in payload.items()
        if feedstock_id in DEFAULT_FEEDSTOCK_ANCHORS
        and isinstance(record, Mapping)
        and record.get("composition_wt_pct")
    }


def _nearest_feedstock(
    composition_kg_json: Any,
    anchors: Mapping[str, Mapping[str, float]],
) -> str:
    composition = _decoded_payload(composition_kg_json)
    if not isinstance(composition, Mapping) or not anchors:
        return "<unassigned>"
    values = {
        str(species): float(value)
        for species, value in composition.items()
        if _finite_float(value) is not None and float(value) >= 0.0
    }
    total = sum(values.values())
    if total <= 0.0:
        return "<unassigned>"
    wt_pct = {species: value * 100.0 / total for species, value in values.items()}

    def distance(anchor: Mapping[str, float]) -> float:
        species = set(wt_pct) | set(anchor)
        return sum(
            (wt_pct.get(name, 0.0) - float(anchor.get(name, 0.0))) ** 2
            for name in species
        )

    return min(sorted(anchors), key=lambda name: distance(anchors[name]))


def load_non_eval_rows(
    db_path: Path,
    *,
    engine_epoch: int,
    feedstock_anchors: Mapping[str, Mapping[str, float]],
) -> list[dict[str, Any]]:
    """Read persisted rows without opening a write-capable SQLite connection."""
    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT o.id, o.grid_key_id, o.expedited_key, o.status, o.status_kind, "
            "refusal_reason, failure_reason_code, failure_message, raw_payload, "
            "raw_payload_format, engine_mode, engine_model, "
            "o.generic_requested_temperature_C, o.generic_liquidus_T_C, "
            "o.generic_liquid_fraction, o.finder_liquidus_T_C, "
            "o.finder_liquid_fraction, h.temperature_C AS grid_temperature_C, "
            "h.composition_kg_json "
            "FROM alphamelts_outputs o JOIN grid_keys h ON h.id = o.grid_key_id "
            "WHERE o.engine_epoch = ? AND o.status_kind <> 'success' "
            "ORDER BY o.grid_key_id, o.id",
            (engine_epoch,),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["feedstock_id"] = _nearest_feedstock(
                item.pop("composition_kg_json"), feedstock_anchors
            )
            item["feedstock_assignment"] = "nearest_anchor"
            result.append(item)
        return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", type=Path, required=True)
    result.add_argument("--engine-epoch", type=int, default=2)
    result.add_argument(
        "--feedstocks",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "feedstocks.yaml",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.engine_epoch < 1:
        raise SystemExit("--engine-epoch must be >= 1")
    report = build_triage_report(
        load_non_eval_rows(
            args.db,
            engine_epoch=args.engine_epoch,
            feedstock_anchors=load_feedstock_anchors(args.feedstocks),
        )
    )
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
