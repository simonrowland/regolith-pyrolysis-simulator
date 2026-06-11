#!/usr/bin/env python3
"""Summarize dose sweep study cache behavior from optimizer artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


DOSE_PATH = ("campaigns", "C3", "alkali_dosing")


def load_provenance(study_dir: Path) -> list[dict[str, Any]]:
    path = study_dir / "provenance.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"missing provenance artifact: {path}")
    records: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON") from exc
        if isinstance(payload, Mapping):
            records.append(dict(payload))
    return records


def dose_from_patch(patch: Mapping[str, Any]) -> dict[str, float | None]:
    node: Any = patch
    for key in DOSE_PATH:
        if not isinstance(node, Mapping):
            return {"Na_kg": None, "K_kg": None}
        node = node.get(key)
    if not isinstance(node, Mapping):
        return {"Na_kg": None, "K_kg": None}
    return {
        "Na_kg": _optional_float(node.get("Na_kg")),
        "K_kg": _optional_float(node.get("K_kg")),
    }


def summarize(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    candidate_hits = sum(1 for row in rows if row.get("cache_hit") is True)
    candidate_misses = sum(1 for row in rows if row.get("cache_hit") is False)
    dose_rows: list[dict[str, Any]] = []
    cache_key_to_doses: dict[str, set[tuple[float | None, float | None]]] = defaultdict(set)
    hour_counts: dict[int, Counter[str]] = defaultdict(Counter)
    aggregate_counts: Counter[str] = Counter()
    aggregate_misses = 0
    aggregate_available = False
    limitations: list[str] = []

    for row in rows:
        patch = row.get("patch") if isinstance(row.get("patch"), Mapping) else {}
        dose = dose_from_patch(patch)
        if dose["Na_kg"] is not None or dose["K_kg"] is not None:
            dose_rows.append(
                {
                    "candidate_id": row.get("candidate_id"),
                    "cache_key": row.get("cache_key"),
                    **dose,
                }
            )
            cache_key = row.get("cache_key")
            if isinstance(cache_key, str) and cache_key:
                cache_key_to_doses[cache_key].add((dose["Na_kg"], dose["K_kg"]))

        trace = row.get("trace_summary")
        if isinstance(trace, Mapping):
            for hour, state in iter_hour_cache_states(trace):
                hour_counts[int(hour)][state] += 1
            reduced_real = reduced_real_summary(trace)
            if reduced_real:
                aggregate_available = True
                counts = reduced_real.get("cache_state_counts") or reduced_real.get(
                    "replay_cache_state_counts"
                )
                if isinstance(counts, Mapping):
                    for state, count in counts.items():
                        aggregate_counts[str(state)] += int(count)
                aggregate_misses += int(
                    reduced_real.get("misses")
                    or reduced_real.get("replay_misses")
                    or 0
                )

    if not hour_counts:
        limitations.append(
            "provenance.jsonl lacks per-hour reduced-real cache state; current study.py strips full run_execution.per_hour"
        )
    if not aggregate_available:
        limitations.append(
            "provenance.jsonl lacks reduced_real_cache summary; use runner/populate artifacts or add study trace preservation for PT-1 hit/miss counts"
        )

    shared_cache_keys = [
        {
            "cache_key": cache_key,
            "dose_pairs": [
                {"Na_kg": pair[0], "K_kg": pair[1]}
                for pair in sorted(doses, key=lambda item: (item[0] is None, item[0] or 0.0, item[1] or 0.0))
            ],
        }
        for cache_key, doses in sorted(cache_key_to_doses.items())
        if len(doses) > 1
    ]

    return {
        "records": len(rows),
        "dose_records": len(dose_rows),
        "dose_ranges": dose_ranges(dose_rows),
        "candidate_result_cache": {
            "hits": candidate_hits,
            "misses": candidate_misses,
            "note": "optimizer ResultStore cache, not PT-1 reduced-real cache",
        },
        "dose_divergent_shared_evalspec_cache_keys": shared_cache_keys,
        "per_hour_profile_available": bool(hour_counts),
        "per_hour_cache_state_counts": {
            str(hour): dict(counts) for hour, counts in sorted(hour_counts.items())
        },
        "reduced_real_aggregate_available": aggregate_available,
        "reduced_real_cache_state_counts": dict(sorted(aggregate_counts.items())),
        "reduced_real_misses": aggregate_misses,
        "limitations": limitations,
    }


def iter_hour_cache_states(trace: Mapping[str, Any]) -> Iterable[tuple[int, str]]:
    for key in ("per_hour", "hours"):
        entries = trace.get(key)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, Mapping):
                continue
            state = cache_state_from_mapping(entry)
            if state is None:
                continue
            hour = entry.get("hour", index)
            try:
                hour_int = int(hour)
            except (TypeError, ValueError):
                hour_int = index
            yield hour_int, state


def cache_state_from_mapping(entry: Mapping[str, Any]) -> str | None:
    for key in ("reduced_real_cache_state", "cache_state", "last_cache_state"):
        value = entry.get(key)
        if value:
            return str(value)
    for key in ("reduced_real_cache", "backend_diagnostics"):
        nested = entry.get(key)
        if isinstance(nested, Mapping):
            value = cache_state_from_mapping(nested)
            if value:
                return value
            rr = nested.get("reduced_real_cache")
            if isinstance(rr, Mapping):
                value = cache_state_from_mapping(rr)
                if value:
                    return value
    return None


def reduced_real_summary(trace: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("reduced_real_cache", "backend_diagnostics"):
        value = trace.get(key)
        if isinstance(value, Mapping):
            if key == "reduced_real_cache":
                return value
            nested = value.get("reduced_real_cache")
            if isinstance(nested, Mapping):
                return nested
    return {}


def dose_ranges(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    rows = list(rows)
    for key in ("Na_kg", "K_kg"):
        values = sorted(
            {
                float(row[key])
                for row in rows
                if row.get(key) is not None
            }
        )
        result[key] = {
            "min": values[0] if values else None,
            "max": values[-1] if values else None,
            "unique_count": len(values),
        }
    return result


def write_hour_csv(summary: Mapping[str, Any], path: Path) -> None:
    rows = summary.get("per_hour_cache_state_counts")
    if not isinstance(rows, Mapping):
        rows = {}
    states = sorted({state for counts in rows.values() for state in dict(counts)})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["hour", *states])
        writer.writeheader()
        for hour, counts in sorted(rows.items(), key=lambda item: int(item[0])):
            writer.writerow({"hour": hour, **dict(counts)})


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("study_dir", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--csv-out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = summarize(load_provenance(args.study_dir))
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    if args.csv_out is not None:
        args.csv_out.parent.mkdir(parents=True, exist_ok=True)
        write_hour_csv(summary, args.csv_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
