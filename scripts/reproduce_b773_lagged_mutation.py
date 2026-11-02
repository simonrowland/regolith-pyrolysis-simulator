#!/usr/bin/env python3
"""Show that restoring the lagged hourly reader fails the b-773 A gate."""

from __future__ import annotations

import json
import sys

from reproduce_b773_evap_oscillation import _cases, _run_case


def _acceptance_failures(result: dict) -> list[str]:
    failures: list[str] = []
    hours = result["hours"]
    if any(not row["flux_kg_hr"] for row in hours):
        failures.append("empty_flux_hour")
    species = set().union(*(set(row["flux_kg_hr"]) for row in hours))
    for name in sorted(species):
        started = False
        was_off = False
        flips_after_start = 0
        previous = False
        for row in hours:
            present = name in row["flux_kg_hr"]
            if present:
                started = True
            if started and previous != present:
                flips_after_start += 1
            if started and was_off and present:
                failures.append(f"{name}_restarted_after_off")
                break
            if started and previous and not present:
                was_off = True
            previous = present
        if flips_after_start > 1 and not any(
            failure.startswith(f"{name}_") for failure in failures
        ):
            failures.append(f"{name}_alternated")
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
