# REPORT: CaO-Al2O3 hunt source ohta-1996-cao-mgo-al2o3-activities (calibration data for b-721/t-1134)

**From:** regolith-empirical (simulator seat)   **To:** regolith-main   **At:** 2026-10-05 ~23:00 ET

## Tip
- Branch: `hunt/ohta-1996-cao-mgo-al2o3-activities` on origin simonrowland/regolith-pyrolysis-simulator
- Assigned tip: `5a4426f9aff2b3188abe1524b5c7e7929d43dce9`. The origin tip matched it, and so did the VPS local ref. There was no drift and no other worktree held the branch.
- **Rebased tip (pushed, force-with-lease): `e749717eca24714836ab1286c0202628466ca886`**
- Rebased on green: `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (green is the base of the rebased commit chain). The rebase was clean with no conflicts, and the diff against green is the single file `data/literature/extracts/ohta-1996-cao-mgo-al2o3-activities.yaml`.
- Commits: 2 (`start ... (tables first)` and `finish ohta-1996 Table 1 metal/slag rows (spinel-sat)`), +234 lines. The extract blob `3f9961e25961` is unchanged.
- Extract shape: review_status draft. Al2O3 has 1 observation block (`type: composition`, Table 1 spinel-saturated metal/slag compositions) with 10 rows. CaO has **context only (2 rows) and no `observations` list**.

## Migrator delta (before 0 → after)
- observations **+10**, experiments +1, context +2, benches 0, works +1, registry_issues 0, queue 14
- validation issues 0 and **hard issues 0**
- Queue (14): value ×10 (all 10 series points stored with unknown value); quantity ×1 (unsupported: `chemical_compositions_metal_and_slag`); phase ×1 (free-text phase string); temperature_K ×1 (T_range [1823, 1873], so no midpoint was invented); method ×1 (method not stated).
- Net: the 10 rows land as observations, but none carries a numeric value under the current quantity map.

## Validator: **FAIL (3 errors)**
The same 3 errors appear with and without `--check-fidelity-match`. They predate the rebase because the validator is unchanged since 8089eadbf.
1. `species.CaO: observations list is required` (CaO has context only)
2. `species.Al2O3.observations[0][ohta_1996_table1_spinel_sat_compositions]: type must be one of [...]`, got `'composition'`. The allowed list includes `composition_series`.
3. `fidelity_samples[0]`: path-based sample must pin observation evidence. It points at `species.CaO.context[ohta_1996_method_scope].values.temperatures_K[0]`, and metadata-only paths are refused.

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
