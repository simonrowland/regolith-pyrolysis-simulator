# I1 integration — regolith-empirical

**Copy-ready target:** `ferry/ci/I1-integration.md`

**Raw pytest log:** `/workspace/ferry-inbox/ci/I1-pytest.log`

**Branch:** `empirical/integration-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d`  
**Tip SHA:** `c2ba158056c628a25366f63bee23cfce41acc1f7`  
**Worktree:** `/workspace/repos/wt/slot-02`  
**Commits on tip since base:** 51  
**Conflict resolutions:** 4 (see below)  
**G1–G5:** not folded (optional; night-2 step 17 required F1–F6 only)

## Stack order (cherry-pick)

1. `review/t961-printed-fo2` @ `d4f91337f` — clean  
2. `review/r34-hardening` @ `22cf80906`, `07ad01dae` — clean  
3. `review/p4a-chain` (4 commits ..`bba465959`) — clean  
4. `review/s7-battery-flags` @ `472febdd3` — clean  
5. `review/r2-gap-fix` @ `b50438e0f` — clean  
6. `review/b559-vanish` @ `ef1c2a97b` — clean  
7. `review/t952-queue-group` @ `7861f7188` — **conflict** (resolved)  
8. `review/n1-run-commissioning` @ `3a7824764` — clean  
9. `review/r6-r8-fix` @ `0c2e59997`, `1f8df6cbe` — clean  
10. `review/s9-glass` @ `b5b9dacc8` — clean  
11. `review/w1-rebase` @ `620fc776a` — clean  
12. `review/s1-s4-fix` @ `88288f738`, `f6a0e1712`, `d004cd510` — clean  
13. `review/s2-provenance` @ `e875a0d32` — clean  
14. `review/w2-port` @ `f7a79f2a3` — clean  
15. `review/t967-helper` @ `ded1d4c86` — clean  
16. `review/s5-s8-fix` @ `756b72fb7`, `5660dbce2` — clean (**PHYSICS**)  
17. F1 @ `5094d315c` … F2 @ `3b7aa9641` … F3 @ `1bbe8e857` … F4 @ `f5e4a6aca` … F5 @ `b616a5739` … F6 @ `2ed3e4093`  
    - F3 cherry-picks of n1/s7 were empty (already on stack) — skipped  
    - F5/F6 derived-queue conflicts — resolved (see below)

## Conflict resolutions (keep BOTH behaviours)

1. **t952** `tests/battery/test_migrate.py` — import list: kept both `DuplicateContextIdError` (r34) and `QUEUE_SCHEMA_VERSION` (t952).  
2. **F5** `data/battery/migration-queue.yaml` — both sides derived migrate output; kept HEAD queue; landed F5 extracts-v2 printed-fO2 deltas + `tests/battery/test_r16_store_regen.py`. Superseded by post-stack regen.  
3. **F6** `migration-queue.yaml` + `migration-report.md` — same derived-artefact pattern; kept HEAD; landed observations-v2 JANAF glass-region relabel + `tests/battery/test_r17_glass_store_regen.py`. Superseded by post-stack regen.  
4. **F4 same-T / USGS cell collisions** (integration fix commit `950c88655`) — F4 content-stable ids aborted `battery_migrate.py` with `DuplicateObservationIdError` on distinct printed rows/cells that shared T (or T+col+name). Resolution: `series_row_extra` (row label + sha1 of non-T payload) and USGS `tabulated_cell_suffix` extras `v=` / `poly=` — no encounter ordinals reintroduced. F4 no-ordinal behaviour and one-obs-per-printed-row behaviour both kept.

## Post-stack migrate

```
python scripts/battery_migrate.py
→ rows_in=46370 observations=91469 works=238 experiments=2916 queue=131229 hard_issues=1468
  exit 1 (expected whenever hard_issues nonzero)
python data/literature/build_index.py --write-store-summary
```

Store regen commit: `c2ba15805` (own commit; explicit pathspecs).  
`data/battery/migration-queue.yaml` size: **64 MB** (< 100 MB).

## Suite (W3-style)

**Command:** `.venv/bin/python -m pytest tests/ -q -n auto -p no:cacheprovider`  
**Status:** RUNNING (results pending)  
**W3 baseline tip:** `07ad01dae` — see `/workspace/ferry-inbox/ci/W3-suite.md`  
**Classification legend:** ENV-CORPUS / ENV-ENGINE / ENV-OPTIONAL / ENV-FIXTURE / INFRA / REAL

_Counts and failure classification will be filled when the suite finishes._

## READY

- Branch: `origin/empirical/integration-2026-09-22` (push pending suite + this report)  
- Local tip: `c2ba158056c6`  
- Report paths: `/workspace/ferry-inbox/ci/I1-integration.md` and `ferry/ci/I1-integration.md` on the branch
