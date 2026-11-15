#!/usr/bin/env python3
"""Refresh the named extract/source_id census from seated extract YAML files."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_corpus_extract_source_on_file import EXTRACT_DIR, _extract_source_ids
from tests.helpers.named_manifests import write_named_manifest


MANIFEST = ROOT / "tests" / "manifests" / "literature_extract_source_ids.jsonl"


def main() -> int:
    rows = dict(_extract_source_ids(EXTRACT_DIR))
    write_named_manifest(MANIFEST, rows)
    print(f"wrote {len(rows)} extract/source_id rows to {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
