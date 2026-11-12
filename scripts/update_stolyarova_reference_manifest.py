#!/usr/bin/env python3
"""Refresh the Stolyarova Table 3 migration rows from the real migrator."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.battery.migrate import to_plain
from tests.battery.test_migrate import _migrate_real_extract
from tests.helpers.named_manifests import write_named_manifest


SOURCE = "stolyarova-1996-cao-alumina-silica-kems.yaml"
MANIFEST = ROOT / "tests" / "manifests" / "stolyarova_table3_reference_states.jsonl"


def main() -> int:
    with tempfile.TemporaryDirectory() as scratch:
        result = _migrate_real_extract(Path(scratch), SOURCE)
    rows = {
        observation_id: to_plain(observation.identity.reference_state)
        for observation_id, observation in result.observations.items()
        if "stolyarova_1996_table3_" in observation_id
    }
    write_named_manifest(MANIFEST, rows)
    print(f"wrote {len(rows)} migrated observation rows to {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
