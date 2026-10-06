# REPORT: CaO-Al2O3 hunt source seki-2011-ca-o-solubility-fe-cao-al2o3 (calibration data for b-721/t-1134)

**From:** regolith-empirical (simulator seat)   **To:** regolith-main   **At:** 2026-10-05 ~23:00 ET

## Tip
- Branch: `hunt/seki-2011-ca-o-solubility-fe-cao-al2o3` on origin simonrowland/regolith-pyrolysis-simulator
- Assigned tip: `80065abbb355c3cc449ae27c451454558d561099`. The origin tip matched it, and so did the VPS local ref. There was no drift and no other worktree held the branch.
- **Rebased tip (pushed, force-with-lease): `8affdbc16c9ee8e9e6911a806a8597f3f662558f`**
- Rebased on green: `61ec839da3ba288c5df4a80f6d3ef142bd8ab461` (green is the base of the rebased commit chain). The rebase was clean with no conflicts, and the diff against green is the single file `data/literature/extracts/seki-2011-ca-o-solubility-fe-cao-al2o3.yaml`.
- Commits: 1 (`extracts: start seki-2011-ca-o-solubility-fe-cao-al2o3 CaO-Al2O3 backlog (tables first)`), +156 lines. The extract blob `63f7d77d6daa` is unchanged.
- Extract shape: review_status draft. CaO has 3 observation blocks and 1 context row:
  - `seki_2011_abstract_slag_compositions` (`composition`)
  - `seki_2011_table3_calculated_a_CaO` (`activity_coefficient`, 10 rows: runs 1–7 plus 3 per-crucible averages `Av_Al2O3`, `Av_CSZ` and `Av_CaO`)
  - `seki_2011_fig5_figure_only` (`method`)

## Migrator delta (before 0 → after)
- observations **+12** (10 Table 3 rows + 2), experiments +1, context +1, benches 0, works +1, registry_issues 0, queue 41
- validation issues 40: identity_incomplete 20 (soft) and conditional_field 20 (**hard**)
- **Hard issues 20:** `derived_from` + `derivation` on each of the 10 `seki_2011_table3_calculated_a_CaO` rows. These a_CaO values are calculated, but the extract has no ancestry for them.
- Queue (41): derived_from ×10; derivation ×10; value ×12 (series points 0–9 stored with unknown value, plus 2 unsupported-quantity refusals); method ×3; phase ×3 (2 free-text, 1 missing); quantity ×2 (unsupported: `slag_composition_at_saturation` and `ca_o_solubility_plot`); evidence.class ×1 (`method_only`).

## Validator: **FAIL (2 errors; 3 with --check-fidelity-match)**
These errors predate the rebase.
1. `observations[0][seki_2011_abstract_slag_compositions]`: type `'composition'` is not allowed
2. `observations[2][seki_2011_fig5_figure_only]`: type `'method'` is not allowed
3. With `--check-fidelity-match`: `fidelity_samples[0]` value mismatch, sample=0.81 vs extract=0.02. The sample path is `...values.rows[5].a_CaO` with the note "Table 3 CaO-crucible run 6". In the extract, rows[5] is run 5 (CSZ crucible, 0.02) and run 6 (CaO crucible, 0.81) is rows[7], because the `Av_Al2O3` and `Av_CSZ` average rows are interleaved. The data looks consistent and the pin index looks stale. A fix seat should confirm this against the page (p.1373, Table 3) before re-pointing the pin to rows[7].

## How this was run (same for all six CaO-Al2O3 REPORTs)
- Seat: regolith-empirical simulator seat, VPS. Time 2026-10-05 22:50–23:00 ET. Before the rebase, the origin tip was fetched explicitly (`git fetch origin refs/heads/hunt/<sid>`) and compared with the local `hunt/<sid>` ref and the sha in the assignment.
- Worktree: my own detached worktree `/workspace/repos/wt/cao-al2o3-rpt`, reused for each source one after another and then removed. The slot-02..07 worktrees were not touched. They now hold other seats' review/* branches, not these hunt branches.
- Rebase: `git rebase 61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. Each branch adds one new file, `data/literature/extracts/<sid>.yaml`, which is not present on green, so there were no conflicts. The extract blob is byte-identical before and after the rebase. Authors and messages are unchanged, with no trailers added.
- Push: `git push --force-with-lease=refs/heads/hunt/<sid>:<old tip> origin HEAD:refs/heads/hunt/<sid>`. Only that branch was pushed. I checked the remote ref with ls-remote and moved the local `hunt/<sid>` ref to match. No `-r2` branch was needed.
- Validator: `tools/validate_literature_extracts.py <file>` and `--check-fidelity-match <file>` on the rebased tree, using the venv `/workspace/venvs/ror-b2`. The validator is unchanged between the hunt base 8089eadbf and green, so any FAIL below also applied before the rebase.
- Migrator delta: a targeted dry run with no writes, `Migrator(<rebased worktree>)` using the real INDEX.yaml, ALIASES.yaml and vocab, then `_migrate_extract(<this file>)` and `finalize()`. Because the file is new, the delta is "before 0 / after N". This is a per-source run: it does not check for cross-source work/alias collisions against the full store. **No regen was committed or written.**
- None of the six sources has an INDEX.yaml row, on green or on the branch. registry_issues=0 anyway.
- D-062 checklist: this is data only, so items 1–5 are not applicable (no code changed). **ASK:** please run a full `scripts/battery_migrate.py` dry run plus the battery gate on a Mac Studio before landing. Only targeted checks were run on the VPS.
