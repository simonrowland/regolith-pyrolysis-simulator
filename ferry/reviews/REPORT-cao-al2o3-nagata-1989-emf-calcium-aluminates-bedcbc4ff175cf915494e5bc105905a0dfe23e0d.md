# REPORT: CaO-Al2O3 hunt source nagata-1989-emf-calcium-aluminates (calibration data for b-721/t-1134)

**From:** regolith-empirical (simulator seat)   **To:** regolith-main   **At:** 2026-10-05 ~23:00 ET

## Tip
- Branch: `hunt/nagata-1989-emf-calcium-aluminates` on origin simonrowland/regolith-pyrolysis-simulator
- Assigned tip: `669ad7d363f85848e568104dd06ebc48f0db55d7`. The origin tip matched it, and so did the VPS local ref. There was no drift and no other worktree held the branch.
- **Rebased tip (pushed, force-with-lease): `bedcbc4ff175cf915494e5bc105905a0dfe23e0d`**
- Rebased on green: `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (green is the base of the rebased commit chain). The rebase was clean with no conflicts, and the diff against green is the single file `data/literature/extracts/nagata-1989-emf-calcium-aluminates.yaml`.
- Commits: 1 (`extracts: start nagata-1989-emf-calcium-aluminates CaO-Al2O3 backlog (tables first)`), +238 lines. The extract blob `ed3ea6a9ac4e` is unchanged.
- Extract shape: review_status draft. CaO has 3 observation blocks: 2 `gibbs_table` (6 + 4 rows; Table 2 ΔG°f and EMF cells) and 1 `activity_coefficient` (liquidus a_CaO points, not in `rows`). There are 2 context rows.

## Migrator delta (before 0 → after)
- observations **+13**, experiments +1, context +2, benches 0, works +1, registry_issues 0, queue 32
- validation issues 17: identity_incomplete 6 (soft) and conditional_field 11 (**hard**)
- **Hard issues 11:**
  - 4 × (`derived_from` + `derivation`) on the 4 `nagata_1989_table2_delta_Gf` rows, which is 8
  - 3 × `derived_from` on the `nagata_1989_liquidus_a_CaO` points (T=1773 ×2, T=1823)
- Queue (32): derived_from ×7; derivation ×4; value ×10 (series points 0–5 of the Gibbs tables stored with unknown value); temperature_K ×3 (T_range only, so no midpoint was invented); method ×3 (one is `emf_liquidus`, not a closed token); phase ×3 (free-text phase strings); quantity ×2 (unsupported: `emf_of_galvanic_cells` and `standard_free_energy_of_formation`).
- Net: none of the 10 Table-2 / EMF rows yields a numeric value under the current quantity map, because both quantities are unsupported.

## Validator
- `validate_literature_extracts.py`: **OK: 1 extract file(s) valid**
- `--check-fidelity-match`: **OK**

## How this was run (same for all six CaO-Al2O3 REPORTs)
- Seat: regolith-empirical simulator seat, VPS. Time 2026-10-05 22:50–23:00 ET. Before the rebase, the origin tip was fetched explicitly (`git fetch origin refs/heads/hunt/<sid>`) and compared with the local `hunt/<sid>` ref and the sha in the assignment.
- Worktree: my own detached worktree `/workspace/repos/wt/cao-al2o3-rpt`, reused for each source one after another and then removed. The slot-02..07 worktrees were not touched. They now hold other seats' review/* branches, not these hunt branches.
- Rebase: `git rebase 61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. Each branch adds one new file, `data/literature/extracts/<sid>.yaml`, which is not present on green, so there were no conflicts. The extract blob is byte-identical before and after the rebase. Authors and messages are unchanged, with no trailers added.
- Push: `git push --force-with-lease=refs/heads/hunt/<sid>:<old tip> origin HEAD:refs/heads/hunt/<sid>`. Only that branch was pushed. I checked the remote ref with ls-remote and moved the local `hunt/<sid>` ref to match. No `-r2` branch was needed.
- Validator: `tools/validate_literature_extracts.py <file>` and `--check-fidelity-match <file>` on the rebased tree, using the venv `/workspace/venvs/ror-b2`. The validator is unchanged between the hunt base 8089eadbf and green, so any FAIL below also applied before the rebase.
- Migrator delta: a targeted dry run with no writes, `Migrator(<rebased worktree>)` using the real INDEX.yaml, ALIASES.yaml and vocab, then `_migrate_extract(<this file>)` and `finalize()`. Because the file is new, the delta is "before 0 / after N". This is a per-source run: it does not check for cross-source work/alias collisions against the full store. **No regen was committed or written.**
- None of the six sources has an INDEX.yaml row, on green or on the branch. registry_issues=0 anyway.
- D-062 checklist: this is data only, so items 1–5 are not applicable (no code changed). **ASK:** please run a full `scripts/battery_migrate.py` dry run plus the battery gate on a Mac Studio before landing. Only targeted checks were run on the VPS.
