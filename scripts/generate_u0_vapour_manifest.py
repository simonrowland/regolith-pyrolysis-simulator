#!/usr/bin/env python3
"""Generate the U0 vapour-rail canonical species-manifest fixture.

Reads the three U0 inputs (species inventory, gas-closure binding delta,
refractory vapor registry), applies collision-only ``_gas`` canonicalization,
de-duplicates, and writes the checked-in fixture.

Golden- and cache-neutral: does not edit runtime vapor-pressure / catalog data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.vapour_rail.u0_manifest import (  # noqa: E402
    DEFAULT_FIXTURE_PATH,
    DEFAULT_GAS_CLOSURE_PATH,
    DEFAULT_INVENTORY_PATH,
    DEFAULT_REFRACTORY_PATH,
    build_u0_manifest,
    validate_manifest_document,
    write_u0_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_FIXTURE_PATH,
        help=f"output fixture path (default: {DEFAULT_FIXTURE_PATH})",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        default=DEFAULT_INVENTORY_PATH,
        help="path to species-inventory.md",
    )
    parser.add_argument(
        "--gas-closure",
        type=Path,
        default=DEFAULT_GAS_CLOSURE_PATH,
        help="path to species-inventory-gas-closure.md (provenance; binding delta is coded)",
    )
    parser.add_argument(
        "--refractory",
        type=Path,
        default=DEFAULT_REFRACTORY_PATH,
        help="path to refractory_vapor_species.yaml",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="build and validate without writing",
    )
    args = parser.parse_args(argv)

    document = build_u0_manifest(
        inventory_path=args.inventory,
        gas_closure_path=args.gas_closure,
        refractory_path=args.refractory,
    )
    errors = validate_manifest_document(document)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    row_count = document["row_count"]
    if args.check:
        print(f"U0 manifest OK: row_count={row_count} (check only, not written)")
        return 0

    write_u0_manifest(args.output, document)
    print(f"Wrote {args.output} with row_count={row_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
