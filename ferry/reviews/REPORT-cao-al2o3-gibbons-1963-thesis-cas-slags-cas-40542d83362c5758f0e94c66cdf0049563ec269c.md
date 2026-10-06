# REPORT: CaO-Al2O3 hunt source gibbons-1963-thesis-cas-slags-cas (calibration data for b-721/t-1134)

**From:** regolith-empirical (simulator seat)   **To:** regolith-main   **At:** 2026-10-05 ~23:00 ET

## Tip
- Branch: `hunt/gibbons-1963-thesis-cas-slags-cas` on origin simonrowland/regolith-pyrolysis-simulator
- Assigned tip: `b543736bc47ef15cd0151e278a3c9c18f9f6b6d3`. The origin tip matched it, and so did the VPS local ref. There was no drift and no other worktree held the branch.
- **Rebased tip (pushed, force-with-lease): `40542d83362c5758f0e94c66cdf0049563ec269c`**
- Rebased on green: `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (green is the base of the rebased commit chain). The rebase was clean with no conflicts, and the diff against green is the single file `data/literature/extracts/gibbons-1963-thesis-cas-slags-cas.yaml`.
- Commits: 2 (`start ... (tables first)` and `gibbons-1963 Table I compositions + Table II 32 lime-alumina runs`), +426 lines. The extract blob `037b60b1a4d6` is unchanged.
- Extract shape: review_status draft. CaO has 2 observation blocks, `type: composition` (Table I, 5 rows) and `type: equilibration` (Table II, 32 runs), plus 2 context rows.

## Migrator delta (before 0 → after)
- observations **+37** (5 + 32), experiments +1, context +2, benches 0, works +1, registry_issues 0, queue 43
- validation issues 0 and **hard issues 0**
- Queue (43): value ×37 (every series point stored with unknown value); quantity ×2 (unsupported: `slag_composition_at_CaS_saturation_CaS_free_basis` and `gas_slag_equilibration_runs_lime_alumina`); phase ×2 (missing phase); method ×2 (method not stated).
- Net: all 37 rows land, but none is numeric under the current quantity map.

## Validator: **FAIL (5 errors)**
The same 5 errors appear with and without `--check-fidelity-match`. They predate the rebase.
1. `observations[0][gibbons_1963_table_i_cas_sat_compositions]`: type `'composition'` is not allowed. Did it mean `composition_series`?
2. Same observation: `T_range_K must be [T_min, T_max]`
3. `observations[1][gibbons_1963_table_ii_lime_alumina_runs]`: type `'equilibration'` is not allowed
4. Same observation: `T_range_K must be [T_min, T_max]`
5. `fidelity_samples[0]` pins a context path (`species.CaO.context[gibbons_1963_lime_alumina_method].values.temperature_C`), and metadata-only paths are refused.

These need a fix seat. I made no data edits here.

## How this was run (same for all six CaO-Al2O3 REPORTs)
- Seat: regolith-empirical simulator seat, VPS. Time 2026-10-05 22:50–23:00 ET. Before the rebase, the origin tip was fetched explicitly (`git fetch origin refs/heads/hunt/<sid>`) and compared with the local `hunt/<sid>` ref and the sha in the assignment.
- Worktree: my own detached worktree `/workspace/repos/wt/cao-al2o3-rpt`, reused for each source one after another and then removed. The slot-02..07 worktrees were not touched. They now hold other seats' review/* branches, not these hunt branches.
- Rebase: `git rebase 61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. Each branch adds one new file, `data/literature/extracts/<sid>.yaml`, which is not present on green, so there were no conflicts. The extract blob is byte-identical before and after the rebase. Authors and messages are unchanged, with no trailers added.
- Push: `git push --force-with-lease=refs/heads/hunt/<sid>:<old tip> origin HEAD:refs/heads/hunt/<sid>`. Only that branch was pushed. I checked the remote ref with ls-remote and moved the local `hunt/<sid>` ref to match. No `-r2` branch was needed.
- Validator: `tools/validate_literature_extracts.py <file>` and `--check-fidelity-match <file>` on the rebased tree, using the venv `/workspace/venvs/ror-b2`. The validator is unchanged between the hunt base 8089eadbf and green, so any FAIL below also applied before the rebase.
- Migrator delta: a targeted dry run with no writes, `Migrator(<rebased worktree>)` using the real INDEX.yaml, ALIASES.yaml and vocab, then `_migrate_extract(<this file>)` and `finalize()`. Because the file is new, the delta is "before 0 / after N". This is a per-source run: it does not check for cross-source work/alias collisions against the full store. **No regen was committed or written.**
- None of the six sources has an INDEX.yaml row, on green or on the branch. registry_issues=0 anyway.
- D-062 checklist: this is data only, so items 1–5 are not applicable (no code changed). **ASK:** please run a full `scripts/battery_migrate.py` dry run plus the battery gate on a Mac Studio before landing. Only targeted checks were run on the VPS.
