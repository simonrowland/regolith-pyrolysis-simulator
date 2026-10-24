from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Iterable


def decimal_text(value: object) -> str:
    if value in (None, ""):
        return ""
    return format(Decimal(str(value)).normalize(), "f")


def read_observation_grid_fixture(
    path: Path, numeric_fields: Iterable[str]
) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    for row in rows:
        for field in numeric_fields:
            row[field] = decimal_text(row[field])
    return rows


def assert_observation_grid_matches(
    actual: list[dict[str, str]],
    expected: list[dict[str, str]],
    fields: Iterable[str],
) -> None:
    ordered_fields = tuple(fields)
    actual_rows = sorted(actual, key=lambda row: row["observation_id"])
    expected_rows = sorted(expected, key=lambda row: row["observation_id"])
    assert [tuple(row[field] for field in ordered_fields) for row in actual_rows] == [
        tuple(row[field] for field in ordered_fields) for row in expected_rows
    ]
