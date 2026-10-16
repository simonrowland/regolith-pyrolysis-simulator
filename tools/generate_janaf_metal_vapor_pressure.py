#!/usr/bin/env python3
"""Generate pure-metal saturation pressures from the JANAF compilation."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.reference_data.janaf_metal_vapor_pressure import (  # noqa: E402
    build_janaf_metal_vapor_pressure_data,
)

OUTPUT = ROOT / "data" / "pure_metal_vapor_pressure_janaf.yaml"


def main() -> int:
    payload = build_janaf_metal_vapor_pressure_data()
    OUTPUT.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )
    print(f"metals={len(payload['metals'])} output={OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
