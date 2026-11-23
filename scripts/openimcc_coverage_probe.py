#!/usr/bin/env python3
"""OpenIMCC produced / typed-refused / absent, per rail and quantity.

Streams residual JSONL. Does not score. Exit 1 when the candidate has a
listed quantity with a non-zero absent count: OpenIMCC was not asked.

Usage:
    python3 scripts/openimcc_coverage_probe.py CAR.jsonl [BASE.jsonl]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from simulator.battery.engine_capability import openimcc_coverage_tally


def _rows(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: {exc}") from exc
            yield row


def _print_tally(label: str, tally: dict[str, object]) -> None:
    print(f"# {label} absent_listed={tally['absent_listed']}")
    print("rail\tquantity\tlisted\tproduced\ttyped_refused\tabsent\tother")
    for row in tally["rows"]:
        if (
            not row["listed"]
            and row["produced"] == 0
            and row["typed_refused"] == 0
            and row["absent"] == 0
            and row["other"] == 0
        ):
            continue
        print(
            f"{row['rail']}\t{row['quantity']}\t{int(bool(row['listed']))}\t"
            f"{row['produced']}\t{row['typed_refused']}\t{row['absent']}\t{row['other']}"
        )


def main(argv: list[str]) -> int:
    if len(argv) not in {2, 3}:
        raise SystemExit(
            "usage: python3 scripts/openimcc_coverage_probe.py CAR.jsonl [BASE.jsonl]"
        )
    candidate = openimcc_coverage_tally(_rows(Path(argv[1])))
    _print_tally("candidate", candidate)
    if len(argv) == 3:
        base = openimcc_coverage_tally(_rows(Path(argv[2])))
        _print_tally("base", base)
    if candidate["absent_listed"]:
        print(
            f"absent_listed={candidate['absent_listed']}: "
            "OpenIMCC is listed and a cell has no OpenIMCC residual"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
