#!/usr/bin/env python3
"""Show that restoring the lagged hourly reader fails the b-773 A gate."""

from __future__ import annotations

import json
import sys

from reproduce_b773_evap_oscillation import _cases, _run_case


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
    from simulator.evaporation import EvaporationMixin

    original = EvaporationMixin._calculate_evaporation

    def lagged_reader(self, equilibrium, *args, **kwargs):
        # The mutation drops only the hourly solver opt-in, restoring the
        # original previous-hour partial reader while retaining the writer.
        kwargs.pop("solve_headspace_feedback", None)
        return original(self, equilibrium, *args, **kwargs)

    EvaporationMixin._calculate_evaporation = lagged_reader
    failures_by_case = {}
    try:
        for case in _cases("yields"):
            label = f"{case['feedstock_id']} {case['campaign']}"
            print(f"start {label}", flush=True)
            result = _run_case(case)
            failures = _acceptance_failures(result)
            failures_by_case[label] = failures
            print(
                f"done {label}: empty={sum(not row['flux_kg_hr'] for row in result['hours'])} "
                f"gate_failures={failures}",
                flush=True,
            )
    finally:
        EvaporationMixin._calculate_evaporation = original
    print(json.dumps(failures_by_case, sort_keys=True), flush=True)
    if not any(failures_by_case.values()):
        print("restored lagged reader unexpectedly passed acceptance A", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
