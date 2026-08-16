#!/usr/bin/env python3
"""Prove t-622 catalog additivity against clean revision 97969c43."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prove_t609_cross_revision_additivity as shared  # noqa: E402


BASE_REVISION = "97969c434cb679d149756cbfd119e40220763d7a"
EXPECTED_ADDITIONS = ("CoO_gas", "MnO_gas")
DEFAULT_EVIDENCE = (
    ROOT / "validation-data" / "pin-evidence" / "t622_additivity_2026-08-12.yaml"
)


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, default=ROOT)
    parser.add_argument(
        "--baseline-root",
        type=Path,
        help="optional clean detached checkout of the pinned baseline revision",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    shared.BASE_REVISION = BASE_REVISION
    shared.EXPECTED_ADDITIONS = EXPECTED_ADDITIONS
    evidence = shared._generate_evidence(args.candidate_root, args.baseline_root)
    evidence["proof_id"] = "t622_cross_revision_additivity_2026-08-12"
    evidence["generated_by"] = "scripts/prove_t622_cross_revision_additivity.py"
    rendered = shared._render_evidence(evidence)

    if args.check:
        if (
            not args.output.is_file()
            or args.output.read_text(encoding="utf-8") != rendered
        ):
            raise shared.ProofFailure(f"evidence is stale: regenerate {args.output}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")

    equivalence = evidence["preexisting_equivalence"]
    print(
        "PASS: "
        f"{equivalence['compiled_species_compared']} pre-existing species, "
        f"{equivalence['evaluation_cases_compared']} exact grid cases, "
        f"{evidence['t583_coverage']['total_t583_compositions_covered']} "
        "t-583 compositions covered"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except (shared.ProofFailure, subprocess.CalledProcessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
