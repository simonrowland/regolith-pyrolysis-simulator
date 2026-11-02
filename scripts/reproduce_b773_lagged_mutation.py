#!/usr/bin/env python3
"""Show that restoring the lagged hourly reader fails the b-773 A gate."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping
from pathlib import Path

from reproduce_b773_evap_oscillation import (
    _NONZERO_KG_HR,
    _cases,
    _run_case,
)


def _acceptance_failures(result: dict) -> list[str]:
    failures: list[str] = []
    hours = result["hours"]
    started = False
    for row in hours:
        if row["flux_kg_hr"]:
            started = True
        elif started:
            failures.append(f"empty_flux_hour_after_start_{row['hour']}")
    species = set().union(*(set(row["flux_kg_hr"]) for row in hours))
    for name in sorted(species):
        for left, middle, right in zip(hours, hours[1:], hours[2:]):
            present = tuple(
                float(row["flux_kg_hr"].get(name, 0.0)) > _NONZERO_KG_HR
                for row in (left, middle, right)
            )
            same_campaign = len(
                {row["campaign"] for row in (left, middle, right)}
            ) == 1
            if present == (True, False, True) and same_campaign:
                failures.append(
                    f"{name}_period_two_hours_"
                    f"{left['hour']}_{middle['hour']}_{right['hour']}"
                )
                break
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        choices=("lunar", "yields"),
        default="yields",
        help="campaign set to run with the lagged reader restored",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")

    from simulator.evaporation import EvaporationMixin

    original_feedback = (
        EvaporationMixin._calculate_evaporation_with_headspace_feedback
    )

    def lagged_feedback(self, equilibrium, *args, **kwargs):
        previous_partials_Pa = {
            str(species): max(0.0, float(partial_mbar)) * 100.0
            for species, partial_mbar in (
                getattr(self, "_melt_headspace_composition_mbar", {}) or {}
            ).items()
        }
        instance_calculation = self.__dict__.get(
            "_calculate_evaporation",
            _NO_INSTANCE_CALCULATION,
        )
        original_calculation = self._calculate_evaporation

        def lagged_calculation(current_equilibrium, *call_args, **call_kwargs):
            supplied = call_kwargs.get("overhead_partials_override_Pa")
            if isinstance(supplied, Mapping):
                # Keep the current pressure override intact; only hold each
                # participant's partial at its persisted previous-hour value.
                call_kwargs["overhead_partials_override_Pa"] = {
                    species: previous_partials_Pa.get(species, 0.0)
                    for species in supplied
                }
            return original_calculation(
                current_equilibrium,
                *call_args,
                **call_kwargs,
            )

        self._calculate_evaporation = lagged_calculation
        try:
            return original_feedback(self, equilibrium, *args, **kwargs)
        finally:
            if instance_calculation is _NO_INSTANCE_CALCULATION:
                del self._calculate_evaporation
            else:
                self._calculate_evaporation = instance_calculation

    _NO_INSTANCE_CALCULATION = object()
    EvaporationMixin._calculate_evaporation_with_headspace_feedback = (
        lagged_feedback
    )
    payload = {"reader": "lagged", "cases": []}
    try:
        for repeat in range(1, args.repeat + 1):
            for case in _cases(args.cases):
                label = f"{case['feedstock_id']} {case['campaign']}"
                print(f"start {label} repeat={repeat}/{args.repeat}", flush=True)
                wall_started = time.perf_counter()
                cpu_started = time.process_time()
                result = _run_case(case)
                result["wall_seconds"] = time.perf_counter() - wall_started
                result["process_cpu_seconds"] = time.process_time() - cpu_started
                result["repeat"] = repeat
                result["acceptance_failures"] = _acceptance_failures(result)
                payload["cases"].append(result)
                print(
                    f"done {label}: empty={sum(not row['flux_kg_hr'] for row in result['hours'])} "
                    f"gate_failures={result['acceptance_failures']}",
                    flush=True,
                )
    finally:
        EvaporationMixin._calculate_evaporation_with_headspace_feedback = (
            original_feedback
        )
    failures_by_case = {
        f"{case['feedstock_id']} {case['campaign']} repeat {case['repeat']}": case[
            "acceptance_failures"
        ]
        for case in payload["cases"]
    }
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.json}", flush=True)
    print(json.dumps(failures_by_case, sort_keys=True), flush=True)
    if failures_by_case and all(failures_by_case.values()):
        print("lagged temporal partial failed acceptance A in every case", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
