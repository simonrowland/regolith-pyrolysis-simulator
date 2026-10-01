#!/usr/bin/env python3
"""Build the data-only 0005 Na2O(l) condensate patch from openimcc's rows."""

from __future__ import annotations

import argparse
import csv
import difflib
import io
from pathlib import Path

SPECIES = "Na2O(l)"
FIELDS = (
    "species_name", "state", "cation", "cat_num", "oxy_num", "T_min",
    "T_max", "dH298_R", "dG_A", "dG_B", "dG_C", "dG_D", "dG_E", "Ref",
)
PATCH_HEADER = """0005-na2o-liquid-janaf.patch
================================

Replace the Na2O(l) row introduced by patch 0003 with the two JANAF Na-013
rows packaged by openimcc e724b6b (NIST-JANAF 4th ed.). The quartics fit the
apparent Gibbs-energy function divided by R: the supercooled 1000–1500 K fit
uses six nodes, including recovered 1500 K thermal cells, and has a maximum
residual of 0.484144162619486 J/mol; the 1500–3000 K fit uses 16 nodes and has
a maximum residual of 5.21823957213201 J/mol. The high interval is listed
second to follow VapoRock's ascending-temperature multi-row convention; the
openimcc reader selects the row with greatest T_min, including the high row
at the shared 1500 K boundary. Its numeric fields and Ref are copied exactly
from openimcc's packaged rows. The three unnamed trailing columns are empty:
no VapoRock code reads this condensate CSV, and no meaning is established for
the former stray value. This supersedes only the Na2O(l) part of patch 0003;
the Al2O3(l) row remains unchanged. The choice is for source fidelity.

"""


def rows(path: Path) -> tuple[bytes, list[str], list[list[str]]]:
    payload = path.read_bytes()
    text = payload.decode("utf-8-sig")
    parsed = list(csv.reader(io.StringIO(text, newline="")))
    if not parsed:
        raise ValueError(f"{path}: empty CSV")
    return payload, parsed[0], parsed[1:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post-0004-csv", required=True, type=Path)
    parser.add_argument("--openimcc-csv", required=True, type=Path)
    parser.add_argument("--candidate-csv", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    args = parser.parse_args()

    base_bytes, header, base_rows = rows(args.post_0004_csv)
    _, source_header, source_rows = rows(args.openimcc_csv)
    if header != [*FIELDS, "", "", ""]:
        raise ValueError(f"unexpected VapoRock condensate header: {header}")
    if source_header != list(FIELDS):
        raise ValueError(f"unexpected openimcc condensate header: {source_header}")

    source_na = [row for row in source_rows if row[0] == SPECIES]
    if len(source_na) != 2:
        raise ValueError(f"expected two packaged {SPECIES} rows, got {len(source_na)}")
    source_na.sort(key=lambda row: int(row[5]))
    if [(row[5], row[6]) for row in source_na] != [("1200", "1500"), ("1500", "3000")]:
        raise ValueError(f"unexpected packaged {SPECIES} intervals")

    ending = b"\r\n" if b"\r\n" in base_bytes else b"\n"
    physical = base_bytes.splitlines(keepends=True)
    if len(physical) != len(base_rows) + 1:
        raise ValueError("multiline or inconsistent base CSV records are unsupported")
    na_indexes = [i for i, row in enumerate(base_rows) if row[0] == SPECIES]
    if len(na_indexes) != 1:
        raise ValueError(f"expected one post-0004 {SPECIES} row, got {len(na_indexes)}")

    replacements: list[bytes] = []
    for source in source_na:
        fields = [source[FIELDS.index(name)] for name in FIELDS] + ["", "", ""]
        if len(fields) != len(header):
            raise ValueError("generated row does not match the VapoRock CSV width")
        line = ",".join(fields).encode("utf-8") + ending
        replacements.append(line)

    output = physical[: na_indexes[0] + 1] + replacements + physical[na_indexes[0] + 2 :]
    candidate_bytes = b"".join(output)
    args.candidate_csv.parent.mkdir(parents=True, exist_ok=True)
    args.candidate_csv.write_bytes(candidate_bytes)

    before_text = base_bytes.decode("utf-8-sig").splitlines(keepends=True)
    after_text = candidate_bytes.decode("utf-8-sig").splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            before_text,
            after_text,
            fromfile="a/data/condensate-thermo-data.csv",
            tofile="b/data/condensate-thermo-data.csv",
            n=3,
        )
    )
    if diff and not diff.endswith("\n"):
        diff += "\n\\ No newline at end of file\n"
    diff = "diff --git a/data/condensate-thermo-data.csv b/data/condensate-thermo-data.csv\n" + diff
    args.patch.parent.mkdir(parents=True, exist_ok=True)
    args.patch.write_text(PATCH_HEADER + diff, encoding="utf-8", newline="")
    print(f"wrote candidate: {args.candidate_csv}")
    print(f"wrote patch: {args.patch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
