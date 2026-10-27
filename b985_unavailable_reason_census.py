#!/usr/bin/env python3
"""Count unavailable observations covered by the derivation-lineage exemption."""

from collections import Counter
from pathlib import Path
import sys

from simulator.battery.migrate import load_migrated_store
from simulator.battery.validate import _unavailable_missing_input_work_items


REPO_ROOT = Path(__file__).resolve().parent


def main() -> None:
    if len(sys.argv) > 2:
        raise SystemExit("usage: b985_unavailable_reason_census.py [data-directory]")
    data_directory = Path(sys.argv[1]) if len(sys.argv) == 2 else REPO_ROOT / "data"
    _works, _experiments, observations = load_migrated_store(
        data_directory.resolve().parent
    )
    candidates = _unavailable_missing_input_work_items(observations)
    reasons = Counter()
    for observation in candidates:
        reason = observation.value.unavailable_reason
        if isinstance(reason, str) and reason.strip():
            reasons[reason.strip()] += 1
    without_reason = [
        observation
        for observation in candidates
        if not isinstance(observation.value.unavailable_reason, str)
        or not observation.value.unavailable_reason.strip()
    ]

    print(f"unavailable model-derived/reduced without derivation: {len(candidates)}")
    print(f"with typed unavailable_reason: {sum(reasons.values())}")
    print(f"without typed unavailable_reason: {len(without_reason)}")
    print("top unavailable_reason values:")
    for reason, count in reasons.most_common(20):
        print(f"  {reason}: {count}")
    print("examples without a reason (up to 20):")
    for observation in without_reason[:20]:
        print(f"  {observation.observation_id}")


if __name__ == "__main__":
    main()
