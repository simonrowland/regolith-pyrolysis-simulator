#!/usr/bin/env python3
"""Validate patch 0005's data-only Na2O(l) JANAF condensate rows."""

from __future__ import annotations

import argparse
import csv
import io
import subprocess
from decimal import Decimal
from pathlib import Path

SPECIES = "Na2O(l)"
TARGET = "data/condensate-thermo-data.csv"
FIELDS = (
    "species_name", "state", "cation", "cat_num", "oxy_num", "T_min",
    "T_max", "dH298_R", "dG_A", "dG_B", "dG_C", "dG_D", "dG_E", "Ref",
    "", "", "",
)
SOURCE_FIELDS = FIELDS[:14]
COPIED_FIELDS = (
    "T_min", "T_max", "dH298_R", "dG_A", "dG_B", "dG_C", "dG_D",
    "dG_E", "Ref",
)
R_J_MOL_K = Decimal("8.31446261815324")
LOW_MAX_RESIDUAL_J_MOL = Decimal("0.484144162619486")
HIGH_MAX_RESIDUAL_J_MOL = Decimal("5.21823957213201")


def parse(path: Path) -> tuple[list[str], list[list[str]], list[bytes]]:
    payload = path.read_bytes()
    text = payload.decode("utf-8-sig")
    records = list(csv.reader(io.StringIO(text, newline="")))
    physical = payload.splitlines(keepends=True)
    if not records or len(records) != len(physical):
        raise ValueError(f"{path}: empty or multiline CSV")
    return records[0], records[1:], physical


def patch_targets(path: Path) -> list[str]:
    numstat = subprocess.run(
        ["git", "apply", "--numstat", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    applied = sorted(
        {line.split("\t", 2)[2] for line in numstat.splitlines() if line.count("\t") >= 2}
    )
    headers = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("+++ "):
            target = line[4:].split("\t", 1)[0].strip()
            if target != "/dev/null":
                headers.add(target[2:] if target.startswith("b/") else target)
    if sorted(headers) != applied:
        raise ValueError(f"'+++' headers {sorted(headers)} disagree with git apply --numstat {applied}")
    return applied


def get_na(rows: list[list[str]], label: str, width: int) -> list[list[str]]:
    bad_width = [index for index, row in enumerate(rows, 2) if len(row) != width]
    if bad_width:
        raise ValueError(f"{label}: malformed CSV row widths at lines {bad_width}")
    return [row for row in rows if row[0] == SPECIES]


def gibbs(row: list[str], T: Decimal) -> Decimal:
    tau = T / Decimal(1000)
    polynomial = sum(
        Decimal(row[8 + index]) * tau**index for index in range(5)
    )
    return R_J_MOL_K * Decimal(1000) * Decimal(row[7]) - R_J_MOL_K * T * polynomial


def run(args: argparse.Namespace) -> list[str]:
    results: list[str] = []
    header, base_rows, base_raw = parse(args.base_csv)
    candidate_header, candidate_rows, candidate_raw = parse(args.candidate_csv)
    source_header, source_rows, _ = parse(args.openimcc_csv)
    if header != list(FIELDS) or candidate_header != list(FIELDS):
        raise ValueError("unexpected condensate CSV header")
    if source_header != list(SOURCE_FIELDS):
        raise ValueError("unexpected openimcc condensate CSV header")

    targets = patch_targets(args.patch)
    ok = targets == [TARGET]
    results.append(f"I1 {'PASS' if ok else 'FAIL'}: git apply --numstat/+++ targets={targets}; expected={[TARGET]}")

    base_non_na = [raw for row, raw in zip(base_rows, base_raw[1:]) if row[0] != SPECIES]
    candidate_non_na = [raw for row, raw in zip(candidate_rows, candidate_raw[1:]) if row[0] != SPECIES]
    header_identical = base_raw[0] == candidate_raw[0]
    base_na = get_na(base_rows, "post-0004", len(FIELDS))
    candidate_na = get_na(candidate_rows, "candidate", len(FIELDS))
    ok_i2 = (
        len(base_na) == 1
        and len(candidate_na) == 2
        and header_identical
        and base_non_na == candidate_non_na
    )
    results.append(
        f"I2 {'PASS' if ok_i2 else 'FAIL'}: Na2O(l) rows {len(base_na)}->{len(candidate_na)}; "
        f"header bytes identical={header_identical}; "
        f"all other row bytes identical={base_non_na == candidate_non_na}"
    )

    source_na = get_na(source_rows, "openimcc", len(SOURCE_FIELDS))
    source_na.sort(key=lambda row: Decimal(row[5]))
    candidate_na_sorted = sorted(candidate_na, key=lambda row: Decimal(row[5]))
    field_indexes = [FIELDS.index(field) for field in COPIED_FIELDS]
    copied_equal = (
        len(source_na) == 2
        and len(candidate_na_sorted) == 2
        and [tuple(row[i] for i in field_indexes) for row in candidate_na_sorted]
        == [tuple(row[i] for i in field_indexes) for row in source_na]
    )
    results.append(f"I3 {'PASS' if copied_equal else 'FAIL'}: both numeric-field/Ref tuples exactly equal packaged strings")

    try:
        bounds = [(Decimal(row[5]), Decimal(row[6])) for row in candidate_na]
        ordered = sorted(bounds)
        intervals_ok = (
            len(bounds) == 2
            and bounds == ordered
            and ordered == [(Decimal(1200), Decimal(1500)), (Decimal(1500), Decimal(3000))]
            and ordered[0][1] == ordered[1][0]
        )
    except Exception:
        intervals_ok = False
        ordered = []
    results.append(f"I4 {'PASS' if intervals_ok else 'FAIL'}: intervals={ordered}; expected contiguous 1200–1500–3000 K")

    continuity_delta = Decimal("Infinity")
    tolerance = LOW_MAX_RESIDUAL_J_MOL + HIGH_MAX_RESIDUAL_J_MOL
    continuity_ok = False
    if len(candidate_na_sorted) == 2:
        try:
            continuity_delta = abs(gibbs(candidate_na_sorted[0], Decimal(1500)) - gibbs(candidate_na_sorted[1], Decimal(1500)))
            continuity_ok = continuity_delta <= tolerance
        except Exception:
            pass
    # Each fitted G differs from the JANAF target by at most its provenance
    # residual ε. At a shared T, |G_low-G_high| ≤ |G_low-G_JANAF|+|G_high-G_JANAF|
    # ≤ ε_low+ε_high = 0.484144162619486+5.21823957213201 = 5.702383734751496 J/mol.
    # This is a sum of absolute Gibbs-energy residuals (J/mol), not an arbitrary
    # seam threshold; the packaged fits' seam is 2.779451693408191 J/mol.
    results.append(
        f"I5 {'PASS' if continuity_ok else 'FAIL'}: |ΔG(1500 K)|={continuity_delta} J/mol; "
        f"derived tolerance={tolerance} J/mol"
    )
    # I6 is a byte check on the physical Na2O(l) lines. The parsed-field checks
    # above cannot see a line ending or a value left in the unnamed trailing
    # columns: csv.reader drops the terminator, and I3 compares only the copied
    # fields. Each new line must be openimcc's 14 fields verbatim, then the
    # three unnamed columns empty, then the file's own line terminator (taken
    # from the unchanged header line), in ascending T_min order.
    terminator = base_raw[0][len(base_raw[0].rstrip(b"\r\n")):]
    expected_lines = [
        (",".join(row) + "," * (len(FIELDS) - len(SOURCE_FIELDS))).encode("utf-8") + terminator
        for row in source_na
    ]
    candidate_na_lines = [
        raw for row, raw in zip(candidate_rows, candidate_raw[1:]) if row[0] == SPECIES
    ]
    bytes_ok = bool(terminator) and candidate_na_lines == expected_lines
    results.append(
        f"I6 {'PASS' if bytes_ok else 'FAIL'}: Na2O(l) physical lines byte-equal to openimcc fields + "
        f"empty trailing columns + file terminator {terminator!r}"
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-csv", required=True, type=Path, help="post-0004 condensate CSV")
    parser.add_argument("--candidate-csv", required=True, type=Path)
    parser.add_argument("--openimcc-csv", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    args = parser.parse_args()
    try:
        results = run(args)
    except (OSError, UnicodeError, ValueError, csv.Error, subprocess.CalledProcessError) as exc:
        print(f"FAIL INPUT: {exc}")
        return 2
    print("\n".join(results))
    failed = sum(" FAIL:" in line for line in results)
    if failed:
        print(f"FAIL: {failed} validation condition(s) failed")
        return 1
    print("PASS: I1–I6 satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
