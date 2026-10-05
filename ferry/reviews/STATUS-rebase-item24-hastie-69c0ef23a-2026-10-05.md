# STATUS: rebase item-24 + Hastie #38/#38b onto green 69c0ef23a (2026-10-05)

From: regolith-empirical (VPS). Written 2026-10-05 05:16 ET.
New base: `work-v064-green` @ `69c0ef23a3d8a75e4b659fe22c747544d9102fad` (b-709 JANAF r2 store regen, on top of b-728 k-curation 705662fc9).
Old remote branches are left as they were. Nothing was force-pushed. Each rebased tip went to a **new** remote branch.

| branch | old tip | new tip | new remote branch | ahead/behind green |
|---|---|---|---|---|
| review/item24-extract-adoption | `bfe0d059d130c684098508deaa7d5011a6b36131` | `cf992640fd03281ca25bd2cfedca9022c99ad18e` | `review/item24-extract-adoption-r69c0ef` | 14 / 0 |
| review/hastie-technique-ambiguity | `c3cdf7e4bcdee047e6e225394258766c2d1e7a58` | `faea1ff43b3a928802c9231892eef4d8683e27f0` | `review/hastie-technique-ambiguity-r69c0ef` | 6 / 0 |

## Pre-flight
- Worktrees `wt/slot-a24` and `wt/slot-02` were clean. Their HEADs equalled the old remote tips. No file had been modified in the last 60 minutes, and no process was running in either worktree. The rebase went ahead on that basis.

## Conflicts
- **None on either branch.** Neither branch shares a file with the green range:
  - item-24 against merge-base 7027319c1: zero files in common.
  - Hastie against merge-base 257a87a0b: zero files in common.
- Green's data edits since the old bases (kems-022-demaria-1971, kems-042-plante-1979, kems-ms2000-044, JANAF compilation shards and the derived store) do not touch any file on these branches.
- Because nothing conflicted, nothing was hand-merged and no derived store was regenerated. No extract data was edited.
- **Patch-id proof:** the net branch diff is identical before and after the rebase.
  - item-24 = `931ed89aff1c2f70…`
  - Hastie = `17651f1efa0af145…`
  - (`git diff <base> <tip> | git patch-id --stable`)
- Semantic check, Hastie against b-728: kems-020 mentions kems-042 only in prose (the Table 3 series 1104–1129 master fit). b-728 changed only kems-042 page and reason text (the Plante misprint flags). Nothing on the Hastie branch references or depends on those rows.

## d-032 ID conservation (species obs[] / context[])
- item-24, 17 extracts: **448 obs + 695 context**. Unchanged from the bfe0d059d REPORT.
  - sha256 of Shornikov 1998, 2008 and 2024 still match the d937549d values in the REPORT (89f2e7fb…, 425271d0…, eba84672…).
- Hastie, kems-020: **41 obs + 0 context** on green, at the branch's first commit c29a0209f, and at the new tip. Conserved.

## Targeted tests (VPS ~16 GB; no full suite)
venv `/tmp/admit-review-venv`, `-o addopts= -p no:cacheprovider`. Baselines ran in clean green worktree `wt/w3b-green` @ 69c0ef23a.

**item-24 tip cf992640f**
- `tools/validate_literature_extracts.py --check-fidelity-match` on the 17 adopted extracts: **OK, 17 valid**.
- `test_literature_extracts.py -k "fidelity_sample_matches_extract and (<17 ids>)"`: **16 passed**.
  - shornikov-1998 is not parametrized here, because it has obs[]=0 and so no sample is required.
- Passed:
  - test_fidelity_policy_file_valid
  - test_fidelity_graduation_ledger_matches_policy_and_hash
  - test_fidelity_allowlist_covers_pilot_census
  - test_source_priority_file_present_and_valid
  - test_fidelity_closed_set_hash_pinned
  - tests/battery/test_migrate_b674_idless_rows.py (4)
- **3 failed, all pre-existing on green 69c0ef23a.** The normalized `E` lines are identical on green and on the tip (649 vs 649). The only differences are object addresses and the truncation line count. No item-24 id appears in any failure.
  - `test_every_extract_has_fidelity_sample`: same legacy set as green (first hit 1997jonesthermo-jones-1997).
  - `test_pilot_extract_count_exact_and_merge_smoke`: this fails on green as well. The test validates every extract and trips on the same legacy validation errors, which are pre-existing.
  - `test_regenerated_index_matches_committed`: a standalone regen-and-compare gives the same 3 drifting rows on green, the item-24 tip and the Hastie tip (kems-020-hastie-1981-nbsir, kems-022-demaria-1971, kems-035-sauerborn-2005). It also shows zero count drift, zero gap drift, and no rows that exist only in the regen or only in the committed index. The 17 new INDEX rows regenerate exactly as committed on top of 69c0ef23a.

**Hastie tip faea1ff43**
- Validator `--check-fidelity-match` on kems-020: **OK**.
- `pytest tests/battery/test_hastie_table2_technique.py tests/test_literature_extracts.py tests/battery/test_schema_admission.py tests/battery/test_sweep_gas.py -k "hastie or kems-020 or kems_020 or plante or (fidelity_sample_matches_extract and kems)"`: **102 passed, 0 failed**.
  - This covers the #38 PIN tests, the kems-020 and Plante fidelity samples, the schema-admission `(kems-020, 0)` pin and `test_hastie_model_pressure_does_not_inherit_to_kems_points`.
- FYI, not caused by this branch: on green 69c0ef23a itself, the validator on `kems-042-plante-1979.yaml` reports **324 errors**, for example `plante1979_table2_s1129_r017_quoted.equipment.sample: missing value`. The count is identical on green and on the Hastie tip.

## Census / pin counts
- **No census or pin count changed on either branch** relative to the targeted pins above.
- No derived store is committed on either branch. This follows the b-678 precedent and is unchanged from the original deliveries.
- For item-24, the expected store delta at landing is still additive and disjoint from green's: **+600 obs v2, +35 experiments, +744 queue, +17 works, hard issues +0**, per the bfe0d059d REPORT. Green's own b-709 delta (observation_store_summary shard id total 83,955 → 83,973) is green's, not this branch's.
- I did **not** re-run `scripts/battery_migrate.py` here (~15 min, heavy on the 16 GB VPS while other seats run). See the ASK below.

## ASK (regolith-main)
1. **Mac Studio full suite on `review/item24-extract-adoption-r69c0ef` @ cf992640f**:
   - full pytest;
   - `battery_migrate.py` + `build_index.py --write-store-summary` + battery gate;
   - store census and `tests/chemistry/test_extract_store_reproduction.py`.
   Expected receipt = green 69c0ef23a receipt + (obs +600 / exp +35 / queue +744 / works +17 / hard +0).
2. The same Mac Studio targeted gate on `review/hastie-technique-ambiguity-r69c0ef` @ faea1ff43, as convenient. Include `test_extract_store_reproduction`, since kems-020 rows are referenced there.
3. Please confirm that the pre-existing green failures above (fidelity-sample legacy set, pilot-count smoke, 3-row INDEX drift, kems-042 324 validator errors) are known and tracked.
