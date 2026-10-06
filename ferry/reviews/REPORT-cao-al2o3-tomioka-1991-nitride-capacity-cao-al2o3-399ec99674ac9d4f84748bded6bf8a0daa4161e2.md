# REPORT: CaO-Al2O3 hunt source tomioka-1991-nitride-capacity-cao-al2o3 (calibration data for b-721/t-1134)

**From:** regolith-empirical (simulator seat)   **To:** regolith-main   **At:** 2026-10-05 ~23:00 ET

## Tip
- Branch: `hunt/tomioka-1991-nitride-capacity-cao-al2o3` on origin simonrowland/regolith-pyrolysis-simulator
- Assigned tip: `23ad938bc37279978cc0d462f7207e49ab63eba4`. The origin tip matched it, and so did the VPS local ref. There was no drift and no other worktree held the branch.
- **Rebased tip (pushed, force-with-lease): `399ec99674ac9d4f84748bded6bf8a0daa4161e2`**
- Rebased on green: `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (green is the base of the rebased commit chain). The rebase was clean with no conflicts, and the diff against green is the single file `data/literature/extracts/tomioka-1991-nitride-capacity-cao-al2o3.yaml`.
- Commits: 2 (`start ... (tables first)` and `finish tomioka-1991 Table 1 gas-slag rows (all crucibles)`), +303 lines. The extract blob `5c784c376c07` is unchanged.
- Extract shape: review_status draft. CaO has 3 observation blocks and 1 context row:
  - `tomioka_1991_liquidus_molar_ratio_ranges` (`composition`, printed ranges stored as strings)
  - `tomioka_1991_table1_gas_slag_equilibrium` (`composition`, holding the **30 Table 1 rows** under the non-standard keys `rows_Al2O3_crucible` (19), `rows_CaO_crucible` (7) and `rows_Mo_crucible` (4))
  - `tomioka_1991_fig7_liquidus_figure_only` (`method`, figure-only)

## Migrator delta (before 0 → after)
- observations **+3**, experiments +1, context +1, benches 0, works +1, registry_issues 0, queue 16
- validation issues 0 and **hard issues 0**
- **The 30 Table 1 rows are not exploded.** The migrator reads only `values.rows`, so the per-crucible lists become one observation with an unknown value. Each of the 3 observations queues `value: declared quantity is unknown ... refusing to pick a number`.
- Queue (16): method ×3; quantity ×3 (unsupported: `liquidus_composition_molar_ratio_ranges`, `equilibrium_data_gas_slag` and `cao_al2o3_phase_diagram_with_present_liquidus_points`); value ×3; phase ×3 (2 free-text, 1 missing); temperature_K ×2 (T_range [1723, 1923], so no midpoint was invented); reference_state ×1; evidence.class ×1 (`method_only`).
- To land the 30 rows, a fix seat would have to merge the three lists into `values.rows` with a per-row crucible field and map the quantity. That should be done without changing any printed value.

## Validator: **FAIL (3 errors)**
The same 3 errors appear with and without `--check-fidelity-match`. They predate the rebase. All three are disallowed `type` values:
- `observations[0]` is `'composition'`
- `observations[1]` is `'composition'`
- `observations[2]` is `'method'`

The fidelity samples themselves match.

## How this was run (same for all six CaO-Al2O3 REPORTs)
- Seat: regolith-empirical simulator seat, VPS. Time 2026-10-05 22:50–23:00 ET. Before the rebase, the origin tip was fetched explicitly (`git fetch origin refs/heads/hunt/<sid>`) and compared with the local `hunt/<sid>` ref and the sha in the assignment.
- Worktree: my own detached worktree `/workspace/repos/wt/cao-al2o3-rpt`, reused for each source one after another and then removed. The slot-02..07 worktrees were not touched. They now hold other seats' review/* branches, not these hunt branches.
- Rebase: `git rebase 61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. Each branch adds one new file, `data/literature/extracts/<sid>.yaml`, which is not present on green, so there were no conflicts. The extract blob is byte-identical before and after the rebase. Authors and messages are unchanged, with no trailers added.
- Push: `git push --force-with-lease=refs/heads/hunt/<sid>:<old tip> origin HEAD:refs/heads/hunt/<sid>`. Only that branch was pushed. I checked the remote ref with ls-remote and moved the local `hunt/<sid>` ref to match. No `-r2` branch was needed.
- Validator: `tools/validate_literature_extracts.py <file>` and `--check-fidelity-match <file>` on the rebased tree, using the venv `/workspace/venvs/ror-b2`. The validator is unchanged between the hunt base 8089eadbf and green, so any FAIL below also applied before the rebase.
- Migrator delta: a targeted dry run with no writes, `Migrator(<rebased worktree>)` using the real INDEX.yaml, ALIASES.yaml and vocab, then `_migrate_extract(<this file>)` and `finalize()`. Because the file is new, the delta is "before 0 / after N". This is a per-source run: it does not check for cross-source work/alias collisions against the full store. **No regen was committed or written.**
- None of the six sources has an INDEX.yaml row, on green or on the branch. registry_issues=0 anyway.
- D-062 checklist: this is data only, so items 1–5 are not applicable (no code changed). **ASK:** please run a full `scripts/battery_migrate.py` dry run plus the battery gate on a Mac Studio before landing. Only targeted checks were run on the VPS.
