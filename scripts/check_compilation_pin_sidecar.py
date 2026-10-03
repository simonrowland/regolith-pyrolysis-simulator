#!/usr/bin/env python3
"""Check compilation comparison pins against a scorer sidecar."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.compilation_tier import iter_compilation_comparisons_jsonl
from simulator.battery.pins import load_pins, pin_failures


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
    comparisons: list[dict[str, str]] = []
    failures = pin_failures(
        [],
        loaded["pin_band_records"],
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
    outside_failures.sort(
        key=lambda row: Decimal(str(row.get("delta") or "0")),
        reverse=True,
    )

    print(f"residuals={args.residuals}")
    print(f"sidecar={sidecar}")
    print(
        f"compared={len(comparisons)} in_band={len(in_band)} "
        f"outside_band={len(out_of_band)} unmapped={len(unmapped)} absent={len(absent)}"
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
