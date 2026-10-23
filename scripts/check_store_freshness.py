#!/usr/bin/env python3
"""Tripwire: is the committed derived store stale w.r.t. migrate inputs?

The derived store (works/, extracts-v2/, observations-v2/, migration-report.md)
is what consumers read, but nothing asserts it was regenerated after the
extracts, compilations, or migration code last changed. This script is the
cheap half of that assertion: it finds the newest commit that touched store
outputs and fails if any later commit touched migrate inputs.

It cannot prove byte equality — that needs a full regen + diff (minutes,
nightly-tier). At the train gate, run it after the batch regen: it proves two
cheaper facts, that no unacknowledged input changed after the store and no
extract silently lacks a store trace. Under d-255, offers carry extracts and
code without regenerated store outputs, so an offer with migrate-input changes
is expected to report STALE before the train gate. Offer authors can use
--pre-gate to label that result. This checker currently recognizes historical
store-neutral commits through STORE_NEUTRAL_INPUT_COMMITS; the separately
accepted ackdir change adds validated per-file train acknowledgements under
scripts/store_neutral_acks/ after the gate regen.

Exit 0: store is at least as new as every migrate input, and every extract
has an extracts-v2 sibling or a migration-queue entry.
Exit 1: input commits landed after the last store touch, or an extract has
no store trace at all (store suspect).

Usage: scripts/check_store_freshness.py [--head REF] [--pre-gate]
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


# Historical migrate-input commits that are PROVEN store-neutral: a full regen
# on a tree containing them leaves every STORE_OUTPUT byte-identical, so no
# regen commit can exist to follow them, and they would read as STALE forever.
# Each entry has its evidence described in the notes below. New train
# acknowledgements use validated per-file records in the separately accepted
# ackdir change. Short shas, 9 characters, match the keys
# stale_input_commits() emits.
#   037f97ff6, 5d46c88e1: d-216, source-oxygen scoring. They score Knudsen
#     vapour rows at typed source oxygen conditions and keep typed reasons for
#     closure fallbacks, in simulator/battery/source_oxygen.py and score.py.
#   6f308879c, 82655ba66, 9c40ad728, 6444f432c: the d-216 co-measured
#     follow-up. A co-measured O/O2 pair is one printed pO2 condition; the
#     JANAF atomic-O conversion moves into its owner; identity-based oxygen
#     exclusions. 6444f432c also reads the optional extract field
#     values.oxygen_partial_pressure.source_observation_id in
#     simulator/battery/migrate.py (documented in the extract SCHEMA).
#     The landing train's full regen of b149 (tip 988454487 on green
#     2c7bcb1cc, studio-3) was a fixed point, so these change no STORE output.
#     This acknowledges STORE outputs only; the scoring moves are covered by
#     d-216 and by the commits' tests.
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
#   23bee5e7b, 8c154cdb0, ed246fcb7, 62224868f: b-932, the alphaMELTS redox
#     handoff. 23bee5e7b moves the multivalent-melt rule into
#     simulator/melt_backend/base.py (score.py and generators/bench.py import
#     it). 8c154cdb0 made score.py require oxygen for every modeled quantity
#     over a multivalent melt; 62224868f removes that branch again, restoring
#     the identity-profile rule. ed246fcb7 keeps alphaMELTS's not-an-input
#     oxygen mode in the melt backends and
#     diagnostic_helpers/binary_pot_battery.py; its score.py change is a
#     comment.
#   2e69a5faa, 4f9402dea, 31bb956c5, cdd49ec85: t-1347, the hero deck. These
#     add scorer-owned deck axes in score.py (solved oxygen conditions, typed
#     deck records, the NBO/T domain bound) that the hero plotter reads.
#   9364d3828: b-782. diagnostic_references() also routes observations that
#     is_compilation_evidence() flags to the diagnostic path.
#   The landing train's full regen of b143 (tip 800c01e37 on green 95a870f36)
#     produced no store change (observations=126296 queue=80926
#     hard_issues=3334; j01 7/7). This acknowledges STORE outputs only; the
#     scoring and diagnostic changes are covered by the commits' tests.
#   c21acfc5d, 6ba1b9771: t-1424 public battery tools. c21acfc5d names the
#     residual-key spellings in simulator/battery/score.py, shares its key
#     splitter with pins.py, and gives compilation_tier.py the shared
#     no-headline rail token (same strings throughout). 6ba1b9771
#     streams residual ledgers through iter_residual_payloads in score.py for
#     scripts/battery_residuals_report.py. Migration's only import from these
#     modules is the unchanged parse_species_formula, and neither commit
#     touches scripts/battery_migrate.py, migrate.py, validate.py or data/.
#     The landing train's full regen of b148 (on green 850a09e93) was a fixed
#     point. This acknowledges STORE outputs only; the key and report changes
#     are covered by the commits' tests.
#   f74ef7a6f: scan-only parser; corpus census 24 source_form strings, 0 verdict changes.
#     The census compared 29 occurrences across repo data and battery store
#     inputs with the green implementation. This is the store-neutrality
#     receipt for this entry; no store regeneration was run.
#   d6ccae4b4: SC-333 S333-04 (d-195), one formula owner for oxide molar
#     masses. It routes oxide projection through simulator/accounting/formulas.py
#     and removes the literal mass tables in core.py and the builtin
#     electrolysis/overhead-gas engines. The landing train's full regen of b145
#     (tip 3ae48641c on green a4608882d, studio-3) was a fixed point: the regen
#     commit equals the tip, so no store change. This acknowledges STORE
#     outputs only; the projection value moves (d-195) are covered by the
#     commit's tests.
#   7e0508bb5, b37e28bac, 297df02b9: t-1413 (d-223), printed phase
#     compositions, distribution ratios and S/Cl capacities. They add the
#     enums, identity axes, migrate readers and score.py paths for those rows.
#     The landing train's full regen of b148 (tip 20a005ad5 on green 850a09e93,
#     studio-3) was a fixed point: no store change. This acknowledges STORE
#     outputs only; the new rows' scoring is covered by the commits' tests.
STORE_NEUTRAL_INPUT_COMMITS = frozenset({
    "037f97ff6", "5d46c88e1",
    "6f308879c", "82655ba66", "9c40ad728", "6444f432c",
    "80fad2bfa",
    "09c498b65",
    "1b78b5697", "574800443", "149df2858",
    "9bb0a22ce", "1d254d51e", "b8be6d576",
    "d47e84bec", "6c3b40660",
    "23df83343", "2bd2918e6", "34132a932",
    "8d90cc936",
    "f522e152c", "da3640753", "e84b44277",
    "554fd0e07", "b746bcf30", "3a27c5abd",
    "23bee5e7b", "8c154cdb0", "ed246fcb7", "62224868f",
    "2e69a5faa", "4f9402dea", "31bb956c5", "cdd49ec85",
    "9364d3828",
    "c21acfc5d", "6ba1b9771",
    "f74ef7a6f",
    "d6ccae4b4",
    "7e0508bb5", "b37e28bac", "297df02b9",
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
    parser.add_argument(
        "--pre-gate",
        action="store_true",
        help="label stale migrate inputs as awaiting the train gate regen under d-255",
    )
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
        if args.pre_gate:
            print("d-255: listed migrate-input commits await the train's gate regen; "
                  "this STALE result is expected before the gate and a defect after it.")
        else:
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
