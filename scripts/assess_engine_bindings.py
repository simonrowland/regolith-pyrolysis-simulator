#!/usr/bin/env python3
"""Assess this host's reduced-real producer bindings against reviewed pins."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.engine_binding_admission import (  # noqa: E402
    REAL_BINDING_PIN_DIRECTORY,
    assess_bindings,
    assessment_candidates,
    binding_receipt_path,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pins",
        type=Path,
        default=REPO_ROOT / "tests" / "fixtures" / REAL_BINDING_PIN_DIRECTORY,
        help="tracked reviewed binding pins (defaults to the repository pin directory)",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=binding_receipt_path(),
        help="host-local receipt path (defaults beside engines.local.toml)",
    )
    args = parser.parse_args(argv)

    candidates = assessment_candidates(args.pins)
    results = assess_bindings(
        candidates,
        pin_directory=args.pins,
        receipt_path=args.receipt,
    )
    for result, candidate in zip(results, candidates, strict=True):
        status = "ADMITTED" if result.status == "admitted" else "REFUSED"
        detail = f" ({result.reason})" if result.reason else ""
        print(
            f"{candidate.identity.producer_transport} "
            f"{candidate.artifact}: {status}{detail}"
        )
    print(f"receipt: {args.receipt}")
    return 0 if all(result.status == "admitted" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
