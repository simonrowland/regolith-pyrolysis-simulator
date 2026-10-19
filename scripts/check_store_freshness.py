#!/usr/bin/env python3
"""Tripwire: is the committed derived store stale w.r.t. migrate inputs?

The derived store (works/, extracts-v2/, observations-v2/, migration-report.md)
is what consumers read, but nothing asserts it was regenerated after the
extracts, compilations, or migration code last changed. This script is the
cheap half of that assertion: it finds the newest commit that touched store
outputs and fails if any later commit touched migrate inputs.

It cannot prove byte equality — that needs a full regen + diff (minutes,
nightly-tier). It proves two cheaper facts: nobody changed an input and walked
away without touching the store, and no extract silently lacks a store trace.

Exit 0: store is at least as new as every migrate input, and every extract
has an extracts-v2 sibling or a migration-queue entry.
Exit 1: input commits landed after the last store touch, or an extract has
no store trace at all (store suspect).

Usage: scripts/check_store_freshness.py [--head REF]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STORE_OUTPUTS = (
    "data/literature/works",
    "data/literature/extracts-v2",
    "data/literature/observations-v2",
    "data/battery/migration-report.md",
)

# Everything battery_migrate.py reads that is not itself a store output:
# extract sources (extracts/*.yaml minus _*/SCHEMA*), compilation sources
# (compilations/**/*.{yaml,yml,json}), the index, named-source ledgers that
# live as top-level data/literature/*.yaml, the lab-parameter vocabulary,
# and the migration code.
INPUT_EXACT = {
    "data/literature/INDEX.yaml",
    "data/literature/lab_parameter_vocabulary.yaml",
}

# Migration-code closure outside data/literature and simulator/battery: the
# molar-mass table (atomic weights), the shared physical constants
# (STANDARD_ATMOSPHERE_PA), the JANAF formula parser validate.py uses, and the
# State/StateTag owner that battery records and enums re-export.
CODE_INPUTS = (
    "simulator/accounting/exceptions.py",
    "simulator/accounting/formulas.py",
    "simulator/physical_constants.py",
    "simulator/reference_data/janaf.py",
    "simulator/state_types.py",
)


# Migrate-input commits that are PROVEN store-neutral: a full regen on a tree
# containing them leaves every STORE_OUTPUT byte-identical, so no regen commit
# can exist to follow them, and they would read as STALE forever. Each entry
# needs that receipt (regen run, no store diff). Short shas, 9 characters,
# matching the keys stale_input_commits() emits.
#   80fad2bfa: the OpenIMCC af0f2a2 pin bump. The Sossi residue prediction path
#     (simulator/battery/residue.py and score.py _sossi_residue_prediction) sends melt evaluation
#     only the melt pack's oxides and passes the existing UNCERTIFIED MnO activity extension to the
#     new Mn gas channel, with an OUT_OF_CERTIFIED_BAND notice. Migration does not execute the residue
#     prediction path (see the 23df83343 entry), and its only score import is the unchanged
#     parse_species_formula. Store receipt to be confirmed by the landing gate's full regen on the
#     merge with green (expected: 0 files changed under data/). This acknowledges STORE outputs only;
#     the residue scoring change is covered by the commit's tests.
#   09c498b65: b-851 renames the private scorer helper _fusion_comparison_reference
#     in simulator/battery/score.py to the public fusion_comparison_reference so the
#     melt-activity benchmark shares the one owner. Name and docstring only: no
#     expression, branch or literal changes. Migration imports only the unchanged
#     parse_species_formula from score.py (simulator/battery/migrate.py), and the
#     series changes nothing under scripts/battery_migrate.py, migrate.py,
#     validate.py or data/. Store receipt to be confirmed by the landing gate's full
#     regen on the merge with green (expected: 0 files changed under data/). This acknowledges
#     STORE outputs only; the benchmark shift is covered by the series' tests and
#     owner decision d-135.
#   1b78b5697, 574800443, 149df2858: bench payload and ledger/sticking changes
#     not consumed by battery_migrate or build_index (R17).
#   9bb0a22ce, 1d254d51e, b8be6d576: kems-041 misprint notes and compilation
#     status wording (t-1145, t-1146); regen at their merge produced no store
#     diff on the pregate host and on the landing gate.
#   d47e84bec: openimcc provenance moves from table paths and file hashes to the
#     engine binding identity (b-690); emitted provenance only, and a regen on
#     top of it produced no store diff on the landing gate.
#   23df83343, 2bd2918e6, 34132a932: rename OpenIMCC pack aliases and carry the
#     LOADED pack's identity through refusals, residue results, and coefficient
#     lineage. scripts/battery_migrate.py runs migrate.main; the migrator's only
#     score import is unchanged parse_species_formula. residue.py is imported
#     only by the two scorer residue wrappers and tests; migration validates
#     with residuals=None, and validate uses the unchanged
#     coefficient_lineage_sources filter. Thus migration does not execute the
#     changed prediction, refusal, or residual-lineage paths.
#     A full regen on their tree produced no store diff on the landing gate
#     (rows_in=47960 observations=122164 works=276 experiments=3576 queue=77761
#     hard_issues=3631). This acknowledges STORE outputs only; runtime effects on
#     scores, refusals, and lineage labels are covered by the commits' tests,
#     not by this regeneration.
#   6c3b40660: State/StateTag move from simulator/battery to
#     simulator/state_types.py with re-exports (b-672); class bodies unchanged,
#     and a full regen on the rebased tip produced no store diff (0 store paths
#     changed; observations 122164, works 276, experiments 3576, queue 77761,
#     hard issues 3631).
#   8d90cc936: the residue binding-provenance sites in simulator/battery/
#     residue.py load the pack named by the bridge's one production-default
#     constant instead of the literal "default". The constant holds that same
#     alias, and migration does not execute the residue prediction path (see
#     the 23df83343 entry). A full regen on its tree produced no store diff
#     (0 of 19921 files under data/ changed, sha256 manifest before and after;
#     rows_in=47960 observations=122164 works=276 experiments=3576 queue=77761
#     hard_issues=3631). This acknowledges STORE outputs only; the runtime tie
#     to the constant is covered by the commit's tests.
#   f522e152c, da3640753, e84b44277: b-837 unstated solid polymorph
#     predict-and-flag (score.py inference of the stable solid reference and
#     its notice; the JANAF fusion-pair owner in generators/janaf.py; the
#     above-crossing conversion). The series changes no file under
#     scripts/battery_migrate.py, simulator/battery/migrate.py, validate.py or
#     data/ (git diff 2c49cd0b8 e84b44277 on those paths is empty), and the
#     review of record's fresh migrations at base and tip gave identical
#     observation hashes. The landing train's full regen at the merge onto
#     green ba62a746d (3dd98193c, studio-3) changed 0 files under data/.
#     This acknowledges STORE outputs only; the scoring change (237 residual
#     records, 94 new numeric candidates) is covered by the commits' tests.
#   554fd0e07, b746bcf30, 3a27c5abd: t-1275 cell oxygen balance for every
#     engine (d-142). These pass the requested oxygen to the alphaMELTS
#     activity call in generators/bench.py, solve the vapour oxygen balance
#     from engine pressures in score.py and oxygen_balance.py, and close the
#     alphaMELTS oxygen-balance projection. The landing train's full regen of
#     798c1b672 (gate b133, studio-3) produced no store change (rows_in=50497
#     observations=126296 works=349 experiments=3823 queue=80926
#     hard_issues=3334; j01 7/7). This acknowledges STORE outputs only; the
#     scoring change (cell-balance residuals per engine) is covered by the
#     commits' tests.
#   dbf438ba8: b-899 one melt redox state from the iron ledger. Its only
#     migrate-input path is simulator/battery/residue.py, where the Hashimoto
#     residue projection collapses Fe2O3 onto the printed FeOt basis
#     (collapse_feot_composition) at prediction time; migration does not run
#     that path. The landing train's full regen of b140 (b-899 + b-926 +
#     openimcc 66ec4a3, studio-3) produced no store change (observations=126296
#     queue=80926 hard_issues=3334; j01 7/7). This acknowledges STORE outputs
#     only; the residue scoring change is covered by the commit's tests.
STORE_NEUTRAL_INPUT_COMMITS = frozenset({
    "80fad2bfa",
    "09c498b65",
    "1b78b5697", "574800443", "149df2858",
    "9bb0a22ce", "1d254d51e", "b8be6d576",
    "d47e84bec", "6c3b40660",
    "23df83343", "2bd2918e6", "34132a932",
    "8d90cc936",
    "f522e152c", "da3640753", "e84b44277",
    "554fd0e07", "b746bcf30", "3a27c5abd",
    "dbf438ba8",
})


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def _is_input(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if path in INPUT_EXACT or path in CODE_INPUTS or path.startswith("simulator/battery/"):
        return True
    if path.startswith("data/literature/extracts/"):
        return (
            path.endswith(".yaml")
            and path.count("/") == 3
            and not name.startswith("_")
            and not name.upper().startswith("SCHEMA")
        )
    if path.startswith("data/literature/compilations/"):
        return (
            path.endswith((".yaml", ".yml", ".json"))
            and name != "README.md"
            and "__pycache__" not in path
        )
    # Named-source ledgers: top-level data/literature/*.yaml only.
    return (
        path.startswith("data/literature/")
        and path.endswith(".yaml")
        and path.count("/") == 2
    )


def _tree_names(head: str, directory: str) -> set[str]:
    listing = _git("ls-tree", "--name-only", f"{head}:{directory}")
    return {line.strip() for line in listing.splitlines() if line.strip()}


def _untraced_extracts(head: str) -> list[str]:
    """Extracts at <head> with no extracts-v2 sibling and no queue entry.

    A regen that consumed an extract always leaves one of the two traces;
    neither means the extract was never consumed (or yields rows nobody
    grounded), which a commit-order check cannot see.
    """
    extracts = {
        name
        for name in _tree_names(head, "data/literature/extracts")
        if name.endswith(".yaml")
        and not name.startswith("_")
        and not name.upper().startswith("SCHEMA")
    }
    siblings = _tree_names(head, "data/literature/extracts-v2")
    try:
        queue = _git("show", f"{head}:data/battery/migration-queue.yaml")
    except subprocess.CalledProcessError:
        queue = ""
    return sorted(
        name
        for name in extracts
        if name not in siblings and f"extracts/{name}" not in queue
    )


def stale_input_commits(head: str, last_store: str) -> dict[str, list[str]]:
    """Migrate-input commits after the last store-output commit, keyed
    "<sha9> <subject>", minus the acknowledged STORE_NEUTRAL_INPUT_COMMITS."""
    stale: dict[str, list[str]] = {}
    log = _git(
        "log", "--format=%H%x00%s", "--name-only", f"{last_store}..{head}", "--",
        "data/literature", "simulator/battery", *CODE_INPUTS,
    )
    commit: str | None = None
    subject = ""
    for line in log.splitlines():
        if "\x00" in line:
            commit, subject = line.split("\x00", 1)
        elif (
            line.strip()
            and commit is not None
            and commit[:9] not in STORE_NEUTRAL_INPUT_COMMITS
            and _is_input(line.strip())
        ):
            stale.setdefault(f"{commit[:9]} {subject}", []).append(line.strip())
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--head", default="HEAD", help="ref to inspect (default: HEAD)")
    args = parser.parse_args(argv)
    head = _git("rev-parse", args.head).strip()

    last_store = _git("log", "-1", "--format=%H", head, "--", *STORE_OUTPUTS).strip()
    if not last_store:
        print("no commit touches the derived store; nothing to check against")
        return 1

    stale = stale_input_commits(head, last_store)

    untraced = _untraced_extracts(head)
    short = _git("log", "-1", "--format=%h %ad %s", "--date=short", last_store).strip()
    if not stale and not untraced:
        print(f"OK: store last touched at {short}; no later migrate-input commits, "
              "every extract has a store trace")
        return 0
    if stale:
        print(f"STALE: store last touched at {short}")
        print(f"{len(stale)} later commit(s) touched migrate inputs without a store regen:")
        for title, paths in stale.items():
            sample = ", ".join(paths[:3]) + (" ..." if len(paths) > 3 else "")
            print(f"  {title}\n    {sample}")
        print("regenerate: .venv/bin/python scripts/battery_migrate.py && "
              "data/literature/build_index.py --write-store-summary")
    if untraced:
        print(f"UNTRACED: {len(untraced)} extract(s) have no extracts-v2 sibling and "
              "no migration-queue entry:")
        for name in untraced[:10]:
            print(f"  data/literature/extracts/{name}")
        if len(untraced) > 10:
            print(f"  ... and {len(untraced) - 10} more")
        print("migrate them, or record the typed absence in "
              "data/battery/migration-queue.yaml")
    return 1


if __name__ == "__main__":
    sys.exit(main())
