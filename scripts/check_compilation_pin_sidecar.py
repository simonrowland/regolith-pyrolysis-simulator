#!/usr/bin/env python3
"""Check compilation comparison pins against a scorer sidecar."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.compilation_tier import iter_compilation_comparisons_jsonl
from simulator.battery.pins import (
    PIN_COMPILATION_CHANNEL_ENGINES,
    load_pins,
    pin_failures,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--residuals",
        type=Path,
        default=Path.home()
        / "repos"
        / "ci-jobs"
        / "score-run"
        / "data"
        / "battery"
        / "residuals.jsonl",
        help="score output whose sibling compilation-comparisons.jsonl is checked",
    )
    parser.add_argument("--sidecar", type=Path, default=None)
    parser.add_argument(
        "--pins",
        type=Path,
        default=ROOT / "data" / "battery" / "pins.yaml",
    )
    args = parser.parse_args(argv)

    if not args.residuals.is_file():
        parser.error(f"residuals file does not exist: {args.residuals}")
    sidecar = args.sidecar or args.residuals.with_name(
        "compilation-comparisons.jsonl"
    )
    if not sidecar.is_file():
        parser.error(f"compilation comparison sidecar does not exist: {sidecar}")

    loaded = load_pins(args.pins)
    compilation_channels = {
        channel.value for channel in PIN_COMPILATION_CHANNEL_ENGINES
    }
    pins = tuple(
        record
        for record in loaded["pin_band_records"]
        if not record.tombstone
        and record.centre is not None
        and record.pin_band_value is not None
        and record.key.rsplit("::", 1)[-1] in compilation_channels
    )
    comparisons: list[dict[str, str]] = []
    failures = pin_failures(
        [],
        pins,
        compilation_comparisons=iter_compilation_comparisons_jsonl(sidecar),
        comparisons=comparisons,
    )
    in_band = [row for row in comparisons if row["within_pin_band"] == "true"]
    out_of_band = [row for row in comparisons if row["within_pin_band"] == "false"]
    unmapped = [row for row in failures if row["reason"] == "unmapped_pin_channel"]
    absent = [
        row
        for row in failures
        if row["reason"] == "no_compilation_comparison_for_reference"
    ]
    outside_failures = [
        row for row in failures if row["reason"] == "outside_pin_band"
    ]
    unmatched = [
        row for row in failures if row["reason"] != "outside_pin_band"
    ]
    unmatched_by_reason = Counter(str(row["reason"]) for row in unmatched)
    outside_failures.sort(
        key=lambda row: Decimal(str(row.get("delta") or "0")),
        reverse=True,
    )

    print(f"residuals={args.residuals}")
    print(f"sidecar={sidecar}")
    print(
        f"pins_total={len(pins)} matched={len(comparisons)} "
        f"in_band={len(in_band)} outside_band={len(out_of_band)} "
        f"unmatched={len(unmatched)} unmapped={len(unmapped)} absent={len(absent)}"
    )
    print(
        "unmatched_by_reason: "
        + json.dumps(dict(sorted(unmatched_by_reason.items())), sort_keys=True)
    )
    print("outside_band_worst_20:")
    for row in outside_failures[:20]:
        print(json.dumps(row, sort_keys=True, ensure_ascii=False))
    print("unmapped:")
    for row in unmapped:
        print(json.dumps(row, sort_keys=True, ensure_ascii=False))
    print("absent:")
    for row in absent:
        print(json.dumps(row, sort_keys=True, ensure_ascii=False))
    return int(bool(unmatched or outside_failures))


if __name__ == "__main__":
    raise SystemExit(main())
