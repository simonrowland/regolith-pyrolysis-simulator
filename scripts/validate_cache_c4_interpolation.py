#!/usr/bin/env python3
"""Read-only held-out validation for cache-C4 interpolation."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import statistics
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote


_repo_root_env = os.environ.get("REGOLITH_REPO_ROOT")
REPO_ROOT = (
    Path(_repo_root_env)
    if _repo_root_env
    else Path(__file__).resolve().parent.parent
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import simulator.reduced_real_cache_interpolation as rci  # noqa: E402
import simulator.reduced_real_determinism as rrd  # noqa: E402


TABLE = rrd.PT1_EQUILIBRIUM_TABLE


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    summary = validate(args.db, sample_limit=args.sample)
    print(json.dumps(summary, sort_keys=True))
    return 0


def validate(db_path: Path, *, sample_limit: int) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    uri = f"file:{quote(str(Path(db_path).resolve()))}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            f"""
            SELECT key_hash, key_bytes, payload_bytes, replay_scope_sha256
            FROM {TABLE}
            WHERE artifact = 'equilibrium_post_record'
            ORDER BY key_hash
            """
        ):
            rows.append(
                {
                    "key_hash": str(row["key_hash"]),
                    "key": json.loads(_blob_bytes(row["key_bytes"]).decode("utf-8")),
                    "payload": json.loads(_blob_bytes(row["payload_bytes"]).decode("utf-8")),
                    "replay_scope_sha256": str(row["replay_scope_sha256"] or ""),
                }
            )

    by_scope: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        scope = row["replay_scope_sha256"] or rci.replay_scope_for_interpolation(row["key"])
        by_scope.setdefault(scope, []).append(row)

    gated_errors: list[float] = []
    operator_errors: list[float] = []
    reachable = 0
    refused = 0
    refusal_reasons: dict[str, int] = {}
    near_knee_refused = 0
    near_knee_total = 0
    evaluated = 0
    operator_evaluated = 0

    for scope_rows in by_scope.values():
        if len(scope_rows) < 3:
            continue
        for target in scope_rows[:sample_limit]:
            evaluated += 1
            candidates = [
                {
                    "key": candidate["key"],
                    "key_hash": candidate["key_hash"],
                    "payload": candidate["payload"],
                }
                for candidate in scope_rows
                if candidate["key_hash"] != target["key_hash"]
            ]
            pO2 = target["key"].get("controls", {}).get("pO2_bar")
            if pO2 is not None and abs(float(pO2) - 1.0e-9) <= 1.0e-12:
                near_knee_total += 1
            operator_error = _held_out_operator_error(target, candidates)
            if operator_error is not None:
                operator_evaluated += 1
                operator_errors.extend(operator_error)
            attempt = rci.attempt_cached_interpolation(target["key"], candidates)
            if attempt is None:
                refused += 1
                neighbors = rci.greedy_nearest_neighbors(target["key"], candidates)
                gate = rci.interpolation_validity_gate(target["key"], neighbors)
                reason = str(gate.get("refusal_reason") or "interpolation_refused")
                refusal_reasons[reason] = refusal_reasons.get(reason, 0) + 1
                if pO2 is not None and abs(float(pO2) - 1.0e-9) <= 1.0e-12:
                    near_knee_refused += 1
                continue
            reachable += 1
            gated_errors.extend(
                _payload_relative_errors(
                    target["payload"],
                    attempt["payload"],
                )
            )

    gated_errors.sort()
    operator_errors.sort()
    return {
        "db_path": str(db_path),
        "rows": len(rows),
        "scopes": len(by_scope),
        "evaluated": evaluated,
        "reachable": reachable,
        "refused": refused,
        "reachable_fraction": (reachable / evaluated) if evaluated else 0.0,
        "held_out_error_gated": _distribution(gated_errors),
        "held_out_error_operator": _distribution(operator_errors),
        "operator_evaluated": operator_evaluated,
        "refusal_reasons": refusal_reasons,
        "near_knee": {
            "total": near_knee_total,
            "refused": near_knee_refused,
            "refused_fraction": (
                near_knee_refused / near_knee_total if near_knee_total else 0.0
            ),
        },
    }


def _held_out_operator_error(
    target: Mapping[str, Any],
    candidates: list[dict[str, Any]],
) -> list[float] | None:
    neighbors = rci.greedy_nearest_neighbors(
        target["key"],
        candidates,
        max_distance=1.0,
    )
    if len(neighbors) < 2:
        return None
    weight_info = rci.barycentric_interpolation_weights(target["key"], neighbors)
    if weight_info is None:
        return None
    payload = rci.interpolate_equilibrium_payload(
        target["key"],
        neighbors,
        weights=weight_info["weights"],
    )
    return _payload_relative_errors(target["payload"], payload)


def _payload_relative_errors(
    exact_payload: Mapping[str, Any],
    interpolated_payload: Mapping[str, Any],
) -> list[float]:
    exact = exact_payload.get("equilibrium_result", {})
    interpolated = interpolated_payload.get("equilibrium_result", {})
    if not isinstance(exact, Mapping) or not isinstance(interpolated, Mapping):
        return []
    errors: list[float] = []
    for species in sorted(
        set(exact.get("vapor_pressures_Pa", {})) |
        set(interpolated.get("vapor_pressures_Pa", {}))
    ):
        exact_value = float(exact.get("vapor_pressures_Pa", {}).get(species, 0.0) or 0.0)
        interp_value = float(
            interpolated.get("vapor_pressures_Pa", {}).get(species, 0.0) or 0.0
        )
        scale = max(abs(exact_value), abs(interp_value), 1.0e-30)
        errors.append(abs(exact_value - interp_value) / scale)
    return errors


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p90": None, "max": None, "count": 0}
    return {
        "p50": statistics.median(values),
        "p90": _percentile(values, 0.90),
        "max": max(values),
        "count": len(values),
    }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return math.nan
    index = max(0, min(len(values) - 1, math.ceil(fraction * len(values)) - 1))
    return values[index]


def _blob_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, memoryview):
        return value.tobytes()
    return bytes(value)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=REPO_ROOT / "docs-private/recipe-db/reduced-real.db",
    )
    parser.add_argument("--sample", type=int, default=500)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())