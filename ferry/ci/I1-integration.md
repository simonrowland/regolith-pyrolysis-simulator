# I1 integration — regolith-empirical

**Copy-ready target:** `ferry/ci/I1-integration.md`

**Raw pytest log:** `/workspace/ferry-inbox/ci/I1-pytest.log` (full suite attempts)  
**Landing smoke log:** `/workspace/ferry-inbox/ci/I1-landing-smoke.log`

**Branch:** `empirical/integration-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d`  
**Tip SHA:** `9de4ecd38303437711218c3525c7ca5c396ccf8b`  
**Worktree (final):** `/workspace/repos/wt/slot-06` (slot-02 was reassigned mid-run to n15)  
**Conflict resolutions:** 4 cherry-pick + 2 post-stack integration fixes  
**G1–G5:** not folded (optional; step 17 required F1–F6 only)

## Status for parent (quick)

- **Cherry-pick steps 1–17:** COMPLETE (F1–F6 included; F4 @ `f5e4a6aca`)
- **Tip:** `9de4ecd38303` (push pending this report commit)
- **Blockers:** full W3-style suite did not finish on the shared box (load spikes 60–170 from concurrent W3b `-n auto`; S5 PHYSICS made condensation fixtures hang 40+ min). Landing smoke on tip: **64 passed**.

## Stack order (cherry-pick)

1. t961 `d4f91337f` — clean  
2. r34 `22cf80906`, `07ad01dae` — clean  
3. p4a-chain (4 ..`bba465959`) — clean  
4. s7 `472febdd3` — clean  
5. r2 `b50438e0f` — clean  
6. b559 `ef1c2a97b` — clean  
7. t952 `7861f7188` — **conflict** resolved  
8. n1 `3a7824764` — clean  
9. r6-r8 `0c2e59997`, `1f8df6cbe` — clean  
10. s9 `b5b9dacc8` — clean  
11. w1 `620fc776a` — clean  
12. s1-s4 `88288f738` `f6a0e1712` `d004cd510` — clean  
13. s2 `e875a0d32` — clean  
14. w2 `f7a79f2a3` — clean  
15. t967 `ded1d4c86` — clean  
16. s5-s8 `756b72fb7`, `5660dbce2` — clean (**PHYSICS**)  
17. F1 `5094d315c` then F2 `3b7aa9641` then F3 `1bbe8e857` then F4 `f5e4a6aca` then F5 `b616a5739` then F6 `2ed3e4093`  
    - F3 empty re-picks of n1/s7 skipped  
    - F5/F6 derived-queue conflicts resolved

## Conflict resolutions (keep BOTH behaviours)

1. **t952** `tests/battery/test_migrate.py` — kept both `DuplicateContextIdError` and `QUEUE_SCHEMA_VERSION`.  
2. **F5** derived `migration-queue.yaml` — kept HEAD queue; landed F5 extracts + r16 pins; superseded by post-stack regen.  
3. **F6** derived queue+report — kept HEAD; landed glass obs + r17 pins; superseded by post-stack regen.  
4. **F4 same-T / USGS collisions** — `series_row_extra` + USGS `v=`/`poly=` content suffixes; no ordinals.  
5. **F4 times F6 R17 pins** — retarget r17 store pins to F4 phase-window ids + GLASS reason markers.

## Post-stack migrate

    python scripts/battery_migrate.py
    rows_in=46370 observations=91469 works=238 experiments=2916 queue=131229 hard_issues=1468
    exit 1 (expected)
    python data/literature/build_index.py --write-store-summary

Store regen commit: `c2ba15805`.  
`data/battery/migration-queue.yaml`: **64 MB** (< 100 MB).

## Suite

### Landing smoke (completed on tip)

`pytest -o addopts= --timeout=90` covering F2/F4/F5/F6/S7/N1 notice + related pins.  
**Result: 64 passed** (see `I1-landing-smoke.log`).

### Full W3-style suite (incomplete)

Attempted `pytest tests/ -q -n auto` / `-n 3 --timeout=90`.  
**Status: INCOMPLETE** — shared box contended (concurrent W3b-green `-n auto`); after S5 PHYSICS, `full_electrolysis_run` spent 40+ minutes in condensation/Antoine. Partial logs under `/workspace/ferry-inbox/ci/I1-pytest*.log`.

**W3 baseline** (`W3-suite.md`, tip `07ad01dae`): 16616 passed / 234 failed / 17 errors; ENV-CORPUS 36, ENV-ENGINE 54, ENV-OPTIONAL 52, ENV-FIXTURE 1, INFRA 5, REAL 103. Expect the same ENV floor once a full run finishes; new risk: S5 condensation wall-time (INFRA timeouts or REAL slowdown).

## READY

- Branch: `origin/empirical/integration-2026-09-22`  
- Tip: `9de4ecd38303437711218c3525c7ca5c396ccf8b`  
- Reports: `/workspace/ferry-inbox/ci/I1-integration.md` and `ferry/ci/I1-integration.md`  
- Re-run full suite when quiet: `.venv/bin/python -m pytest tests/ -q -n 4 -p no:cacheprovider --timeout=120`
