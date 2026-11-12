#!/usr/bin/env python3
"""Refresh the named Fegley 2023 Table 2 rows from the seated extract."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers.named_manifests import write_named_manifest


SOURCE = ROOT / "data" / "literature" / "extracts" / "fegley-2023-chemical-equilibrium-calculations-bu.yaml"
MANIFEST = ROOT / "tests" / "manifests" / "fegley_2023_table_2_printed_rows.jsonl"


def main() -> int:
    document = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    rows = document["species"]["BSE"]["observations"][0]["values"]["rows_as_printed"]
    named_rows = {
        f"{index:04d}:{row['oxide']}": row
        for index, row in enumerate(rows, start=1)
    }
    write_named_manifest(MANIFEST, named_rows)
    print(f"wrote {len(named_rows)} printed rows to {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
