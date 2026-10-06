# REPORT: b-693/b-691 round-2 FIX-FIRST resolution, `review/b693-b691-scorer-gates`

- from: regolith-empirical · to: regolith-main · at: 2026-10-05 ~21:25 ET
- answers: REQ-fix-b693-b691-r2 (10-04 19:04 ET) and REVIEW-b693-b691-r2-043759ba3 (FIX-FIRST, 6 findings); REQ-backlog-reseat §3
- reviewed HEAD: `043759ba35b1765d61e2da71921f16a56f50f92d` · base G: `8089eadbfc856179668287e9c4e503e0e92c2682`
- **branch tip now: `38e6ed5e25f7fd2f99a03913263497f90293e8d5`** (`git ls-remote origin refs/heads/review/b693-b691-scorer-gates` = 38e6ed5e2…, verified 2026-10-05 ~21:20 ET)

## The tip moved past cc10e8386, and why
Your REQ named `cc10e8386355241003f6d4aae8f61f3ad797a70f` (10-04 19:32 ET). That tip was pushed with no REPORT. The seat
then found a B regression in its own F4 fix and left the repair **uncommitted** in slot-03 (19:49–19:50 ET) when
the outage hit. Today I re-checked it and confirmed it. I committed it the same way, pin first, and pushed two commits on top:

| commit | what |
|---|---|
| `c0a90cad247b6a9a8d07e940b443d2e8e262ebdc` | test only: `test_r2_f4_guard_borrowed_envelope_never_attaches_to_residue_dex_rows`. **Red at cc10e8386** (1 failed / 10 passed in the r2 pin file), green at 8089eadbf |
| `38e6ed5e25f7fd2f99a03913263497f90293e8d5` | fix: `populate_numeric(band_operation=…)`. The identity-aware metric checks only a figure row's OWN printed reading band. Every borrowed or derived band keeps the quantity-default dimension check, as at green |

The defect at cc10e8386: 7730c2f31 passed the row's identity metric (DEX for residue element ppm) into `decision_band_for`
and `band_dimension_matches` for *every* band. The KEMS p_partial envelope that `score_store` hands every row as
`derived_band` (0.146, dimensionless) therefore attached to measured residue element-ppm rows, such as the Sossi 2019 rows,
which green leaves NO_BAND. That breaks "keep B unchanged". Direct probe of `populate_numeric(RESIDUE_COMPONENT_COMPOSITION,
10 vs 5, derived_band=KEMS envelope, operation_override=DEX)`: 8089eadbf → band None; 043759ba3 → band None;
cc10e8386 → **band 0.146**; 38e6ed5e2 → band None (own-band case → 0.146 attaches). I did not run a whole-store
B comparison for this (see ASK).

## Finding-by-finding

| # | finding (review of 043759ba3) | resolved in | how | pin (red at 043759ba3 → green at tip) |
|---|---|---|---|---|
| 1 | [P1] payload JSON + Markdown omit all-numeric SOURCE accounting | `7730c2f31a76f962b39a977a3089b5d1fb77b497` | A has one owner, `_AllNumericHeadline` / `_all_numeric_fact` (score.py:6219, :6279). Typed (`headline_rows/records`), streamed (`_ScorePayloadAccumulator`) and report-only payload (`all_numeric_payload_records`, :6444) writers all build A from the same facts. Every writer carries the same source rows, and zero-certified sources keep their flagged n | `test_r2_f1_every_writer_carries_the_same_all_numeric_source_rows` |
| 2 | [P1] A gated by B's measured-only engine set; A n=0 rows hidden | 7730c2f31 | A gets the full rail × engine grid, and zero rows are printed in both Markdown writers. B's engine and grid membership are unchanged (B code is back to green 8089eadbf apart from the `_headline_payload_admits` extraction, :7298, and the accumulator's single `in_measured_headline` decision). The payload Markdown "Engines: …" line that 043759ba3 dropped is restored | `test_r2_f2_all_numeric_has_full_engine_rail_grid_and_prints_zero_rows` |
| 3 | [P1] A statistics pool unlike units under *_dex labels | 7730c2f31 | median/IQR/RMS `_dex` use DEX residuals only. Other metrics are reported per (quantity, operation, unit) in `metric_strata`. The review's 1 kJ/mol + 100 J/mol/K probe now gives median_dex None, and each stratum is labelled in its own unit. `test_pin_all_numeric_non_dex_median_iqr_in_own_metric` (gates file) was updated to the F3 contract (median_dex None; relative stratum median 0.2): a deliberate contract change named in the commit | `test_r2_f3_all_numeric_statistics_are_unit_stratified_never_dex_labelled` |
| 4 | [P2] figure reading band lost on residue element-ppm DEX | 7730c2f31, then narrowed by `38e6ed5e2` | The band and its dimension check use the residual's identity-aware metric (`_residual_metric_operation`, score.py:669), but **only for the row's own printed band** (38e6ed5e2). The 0.3 dex band now attaches to the residue element-ppm figure row, and borrowed envelopes stay off measured DEX rows | `test_r2_f4_residue_element_ppm_figure_band_follows_identity_metric`; guard `test_r2_f4_guard_borrowed_envelope_never_attaches_to_residue_dex_rows` (red at cc10e8386) |
| 5a | [P2] oxygen-input decision exists twice | `cc10e8386355241003f6d4aae8f61f3ad797a70f` | Both cell branches call `_oxygen_input_request` (score.py:3036). The Knudsen-vapour test and the cell-oxygen class are single helpers (`_knudsen_vapour_equilibrium` :3009, `_cell_oxygen_class` :3022). Notice order is unchanged. Behaviour-preserving move | `test_r2_f5_oxygen_input_decision_is_one_policy_on_both_cell_branches[3 cases]`, committed green **before** the move in 60d7ee5af |
| 5b | [P2] source/certification aggregation repeated in typed/payload/stream owners | 7730c2f31 | One A owner (row 1). "Certified" consumes B's own membership decision (`_headline_payload_admits`) and is not re-derived in presentation | covered by f1/f2/f3 pins |
| 6 | [P1] explicit `predict=predict_with_engine` drops the reactive label; can admit an Re-cell row to B | 7730c2f31 | The recorded bench goes to the built-in predictor whether it is chosen implicitly or explicitly. Custom predictors keep the handles/experiment keyword contract | `test_r2_f6_reactive_label_follows_the_bench_not_the_call_style[explicit]`; `test_r2_f6_custom_predictor_keyword_contract_is_unchanged`; **original pin `test_pin_reactive_re_cell_predicts_with_notice_not_refusal` restored byte-for-byte from `75e94a16ccde8bbb86ed9dedbeaddd9db870c227`** (AST body compare 75e94a16c vs 38e6ed5e2: identical, 34 lines) and green. The implicit-call rewrite is kept separately as `test_pin_reactive_re_cell_implicit_predictor_keeps_notice` |

Pins commit: `60d7ee5af6998fc6a08f8fd3f4e663aa06464f55` (tests only, before any fix).

**Re-run today (VPS, tip code vs 043759ba3 code, same tip test files):** at 043759ba3 there are **7 failed / 19 passed**. That is f1, f2, f3, f4,
f6[explicit], the original reactive pin, and `test_pin_all_numeric_non_dex_median_iqr_in_own_metric` (its F3 contract
update). At 60d7ee5af's own message the count was 6 failed / 20 passed, because the gates-file assertion change came with the fix. At tip: **27 passed**.

### Review notes (not findings)
- **De Maria 1971 figure rows refusing `missing_fO2`:** this is unchanged by design. The unmodelled-Re branch requires external oxygen and refuses its absence.
  No oxygen scalar was invented, so Na/K numeric n = 0 in this scorer stays as you measured.
- **Na reading widths:** this is not an extract data loss. It is a migrator vocabulary gap. `extracts/kems-022-demaria-1971.yaml` stores every Na
  point's own `reading_uncertainty: {x: {…, total}, y: {…, total}}`, with the method in `uncertainty.figure_reading_method`
  (for example, point C1 y total 0.061774 in log10 p). K rows use a flat `log10_p_atm_absolute_uncertainty_dex: '0.1'`, which the reader
  maps. The migrator does not read the per-point nested `reading_uncertainty.y.total` shape, so the migrated Na rows
  (`extracts-v2/kems-022-demaria-1971.yaml`) carry only the method text. The fix belongs in a reader item (map the per-point y total,
  in dex for a log10 axis, to the row's PRINTED reading uncertainty), not in this scorer branch. I propose it as a new ticket and have not touched it here.

## Tests run (VPS, no engines: `engines/engines.local.toml` absent in slot-03; ≤300 s per file)
- `tests/battery/test_b693_b691_r2_pins.py` + `tests/battery/test_b693_b691_scorer_gates.py` @ tip: **27 passed** (2.7 s)
- `tests/battery/test_score.py tests/battery/test_compilation_tier.py tests/test_openimcc_battery_engine.py` @ tip: **265 passed, 3 skipped** (161.6 s, -n 3; the skips are engine-gated)
- `tests/test_import_boundary.py tests/test_engines_import_no_cycle.py tests/test_ellingham_import_no_cycle.py` @ tip: **19 passed**
- Pin chronology for the new guard: `c0a90cad2` red at cc10e8386 (1 failed / 10 passed) before `38e6ed5e2`.

## NOT run here: ASK for a Mac Studio pregate
1. Certified line (B) identity: re-run your review's 32-source / 3,311-observation `score_run.py` set, six engines, at
   8089eadbf vs **38e6ed5e2**. The VPS has no engines and 16 GB. Expect B identical, in particular that Sossi 2019 residue rows stay NO_BAND, which is
   what 38e6ed5e2 restores. cc10e8386 would not have passed this.
2. The changed-symbol paired file set from the review's §6 (16 files at tip / 15 at G). Expect the same 15 matched baseline failures.
3. Green moved to `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, and the branch is 175 commits behind it. `git merge-tree --write-tree 61ec839da 38e6ed5e2`
   is **clean (no conflicts)**. I did not merge or rebase, so the reviewed history stays intact. Tell me if you want a green merge before the confirm review.

## D-062 canonical answers (tip 38e6ed5e2 vs reviewed 043759ba3)
1. **Second copy of added/edited logic? NO.** `rg` finds single definitions: `_oxygen_input_request` score.py:3036 (3 call sites, no inline copy),
   `_knudsen_vapour_equilibrium` :3009, `_cell_oxygen_class` :3022, `_all_numeric_fact` :6219, `_AllNumericHeadline` :6279,
   `all_numeric_payload_records` :6444, `_headline_payload_admits` :7298, `_residual_metric_operation` :669. `band_operation` is one
   parameter threaded through `populate_numeric` (:2124), with no second band policy.
2. **Rule/threshold/physics in presentation/wiring? NO.** The Markdown/JSON writers consume `_all_numeric_fact` rows and B's
   `_headline_payload_admits`, with no admission or certification recomputation. Oxygen routing stays in prediction code.
3. **Forbidden-layer import or cycle? NO.** The only imports added are function-local imports of modules that score.py already imports
   (`simulator.diagnostic_helpers.binary_pot_battery` PO2 tokens, `simulator.battery.compilation_tier`). There is no new edge and no
   baseline entry. The import-boundary and no-cycle tests pass (19).
4. **Behaviour-preserving moves pinned first? YES.** The F5a one-owner move (cc10e8386) is pinned by
   `test_r2_f5_oxygen_input_decision_is_one_policy_on_both_cell_branches`, committed green in 60d7ee5af before the move. The
   `_headline_payload_admits` extraction is covered by the B-unchanged pins (f1/f2). The F4 narrowing (38e6ed5e2) is a behaviour
   change back to green, pinned red-first in c0a90cad2.
5. **Relaxed guard / baseline entry / move into relaxed module? NO new ones.** The relaxations named in r1 (figure candidacy,
   unmodelled reactive-reservoir guard, FIGURE_ONLY notice with empty affected quantities) are unchanged and authorised. 38e6ed5e2
   *tightens* the band guard back to green for borrowed bands. No baseline entry was added.

— regolith-empirical
