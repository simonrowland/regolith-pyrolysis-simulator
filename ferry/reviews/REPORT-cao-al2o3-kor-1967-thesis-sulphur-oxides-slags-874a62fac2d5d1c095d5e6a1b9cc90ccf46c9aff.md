# REPORT: CaO-Al2O3 hunt source kor-1967-thesis-sulphur-oxides-slags (calibration data for b-721/t-1134)

**From:** regolith-empirical (simulator seat)   **To:** regolith-main   **At:** 2026-10-05 ~23:00 ET

## Tip
- Branch: `hunt/kor-1967-thesis-sulphur-oxides-slags` on origin simonrowland/regolith-pyrolysis-simulator
- Assigned tip: `ddf70e61f9dc4729caeb3e1216af33c0404b2142`. The origin tip matched it, and so did the VPS local ref. There was no drift and no other worktree held the branch.
- **Rebased tip (pushed, force-with-lease): `874a62fac2d5d1c095d5e6a1b9cc90ccf46c9aff`**
- Rebased on green: `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (green is the base of the rebased commit chain). The rebase was clean with no conflicts, and the diff against green is the single file `data/literature/extracts/kor-1967-thesis-sulphur-oxides-slags.yaml`.
- Commits: 1 (`extracts: start kor-1967-thesis-sulphur-oxides-slags CaO-Al2O3 backlog (tables first)`), +219 lines. The extract blob `4b16b615e5d7` is unchanged.
- Extract shape: review_status draft. CaO has 1 observation block (`activity_coefficient`, Table XXIV CaS-saturated lime activities) with 4 rows, plus 2 context rows.

## Migrator delta (before 0 → after: **CRASH**)
- **Hard blocker: the migrator raises and aborts.** The error is `decimal.InvalidOperation` in `_composition_from_plain` (migrate.py:1161, reached from `_emit_exploded_point` at migrate.py:13282 via the row `point_conditions`).
- Cause: rows 0 and 1 of the CaO observation carry `point_conditions.composition.state.value.components: [[CaO, '0.57'], [Al2O3, missing]]` (and `0.62` / `missing`). The string `missing` is not a decimal.
- `Migrator.run()` has no per-extract guard. If this branch landed as-is, the full `battery_migrate.py` run, and so any regen, would abort, not just this source.
- **Validator gap:** the validator accepts this file (see below) but the migrator crashes on it. A validator check for non-numeric composition components would catch it.
- **Diagnostic only, not committed and not pushed:** a scratch copy with just those two `[Al2O3, missing]` components dropped migrates as:
  - observations +4, experiments +1, context +2, benches 0, works +1, queue 6
  - validation issues 12: identity_incomplete 8 (soft) and conditional_field 4 (**hard**)
  - **hard issues 4:** `derived observation requires derived_from` on all 4 Table XXIV rows (T=1773 ×2, T=1823 ×2)
  - queue: derived_from ×4; phase ×1 (free-text phase string not in the closed map); method ×1 (regime `gas_slag_cas_saturation` is not a closed method token)
- Suggested fix, for main or a fix seat to rule on: drop the `Al2O3` component, or mark it typed-unknown, where the table does not print it. Do **not** derive 1 − x_CaO unless the source prints it. Add derived_from for the 4 activity rows.

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
