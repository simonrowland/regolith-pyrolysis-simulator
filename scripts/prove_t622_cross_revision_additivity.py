#!/usr/bin/env python3
"""Prove t-622 catalog additivity.

The compiler baseline is d9f4f5313. That commit replaced
``poly.evaluate`` with ``evaluate_gibbs_state``, so the evaluator
digest of every species moved. The catalog under test stays
97969c43 against the t-622 blob 3a36e9bb: d9f4f5313's own yaml
already contains MnO and CoO, and archiving that tree as both
compiler and catalog would change the species delta.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import prove_t609_cross_revision_additivity as shared  # noqa: E402


# d9f4f5313 carries evaluate_gibbs_state. The catalog blob compared
# with it is still the pre-addition revision below.
BASE_REVISION = "d9f4f53137341637b9eab46c3a8096c86e1074d3"
BASELINE_CATALOG_REVISION = "97969c434cb679d149756cbfd119e40220763d7a"
CANDIDATE_REVISION = "3a36e9bb6ff79a6a3f51ca969d3a2d41c4e800a9"
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
    evidence = shared._generate_evidence(
        args.candidate_root,
        args.baseline_root,
        candidate_revision=CANDIDATE_REVISION,
        candidate_materialization="catalog_blob",
        baseline_catalog_revision=BASELINE_CATALOG_REVISION,
    )
    evidence["proof_id"] = "t622_cross_revision_additivity_2026-08-12"
    evidence["generated_by"] = "scripts/prove_t622_cross_revision_additivity.py"
    evidence["method"]["candidate_revision"] = CANDIDATE_REVISION
    evidence["method"]["candidate_catalog_materialization"] = "immutable_git_blob"
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
