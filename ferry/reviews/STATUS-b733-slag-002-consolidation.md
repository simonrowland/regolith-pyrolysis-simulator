# STATUS: b-733 consolidation of slag-002-banya-hino-nagasaka-1993 and banya-hino-nagasaka-1993-hydroxyl

**From:** regolith-empirical (b-733 seat)   **To:** regolith-main   **At:** 2026-10-05 ~23:40 ET
**Ask:** REQ-corpus-batch10-from-regolith-main-2026-10-06.md, lines 18-21, plus SEAT-COMMON.md (batch10 and batch8).
**Branch:** `hunt/b-733-slag-002-consolidation` on `mac-studio-256-1:Repos/regolith-corpus.git` (new branch, plain push, no force).
**Started from:** mirror main `491c74a3a49106bb454ff4613b18dc1d398eb276`, which already contains the hydroxyl fix 196f8df2 (I checked it with `merge-base --is-ancestor`).
**New tip:** `820632e99d295bdf6f5a6b3bb1f86f4b7db0fe87` (one commit, parent 491c74a3, author Simon Rowland, explicit pathspecs, no trailers, no PDFs).
**Files changed:** `extracts/slag-002-banya-hino-nagasaka-1993.yaml`, `extracts/banya-hino-nagasaka-1993-hydroxyl.yaml`, `ledger/slag-002-banya-hino-nagasaka-1993.yaml`, `ledger/banya-hino-nagasaka-1993-hydroxyl.yaml` (+416 / −297). I did not touch the sidecar, raw/, text/ or INDEX.

hard issues before 54, after 0 (slag-002) | context records folded 24 (+ legend temperatures of Figs. 2, 3, 8) | observation+context totals 27+25 -> 27+25 | values changed 0

## Identity of the two records
- Both extracts carry DOI 10.2355/isijinternational.33.12 and use the same PDF, `raw/banya-hino-nagasaka-1993-hydroxyl/banya-hino-nagasaka-1993-hydroxyl.pdf` (sha256 b70ddca6…20c0f, 8 pages, printed pp. 12–19). slag-002 has no raw/ or text/ folder of its own.
  Its ledger already records `canonical_corpus_id: banya-hino-nagasaka-1993-hydroxyl` ("alias of corpus id …; PDF lives under that folder").
- The duplicate is the *extract record* `extracts/banya-hino-nagasaka-1993-hydroxyl.yaml`. Its own notice row already said `duplicate_of: slag-002-banya-hino-nagasaka-1993`.
  The raw/sidecar/text under the hydroxyl corpus id serve both ids and are unchanged.

## What changed in slag-002 (54 -> 0)
All 54 hard issues had one cause: every one of the 27 observations was `method_class: model_derived` with no `derived_from` and no `derivation`, giving 27 × 2 conditional-field issues.
I fixed each row on its own merits and did not move any observation to context.

| rows | change | page evidence (250 dpi renders) |
|---|---|---|
| 10 figure rows: fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8, fig9, fig10 `_figure_only` | `method_class` model_derived -> `figure_only` (they were already `admission_status: figure_only`, typed refusal, no value). Follows the costa-2016 / shornikov-2002 precedent on mirror main. | captions PDF 3–7 / pp. 14–18 |
| `table2_alpha_ij` | -> `quoted_attributed`, plus an attribution naming the per-pair Reference column (7, 8, 9, 11, 12, 13, 35). K–Si "Present work" and Li–Si "35), present work" point to their own rows (Eq. 20, Eq. 21). | PDF 2/13 Table 2. The text there says "these interaction energies have been determined by the authors" in earlier work. |
| `eq14_dG11` | -> `quoted_attributed`, Ban-ya and Hino refs 10, 12 | PDF 3/14 "ΔG°11 has been determined by the authors10,12) as Eq. (14)" |
| `eq16_dG15` | -> `quoted_attributed`, Turkdogan ref 32 | PDF 3/14 Eq. (16) superscript 32) |
| `eq21_alpha_Li_Si` | -> `quoted_attributed`, Pein et al. ref 35 | PDF 4/15 "αLi−Si = −142 130 J determined by Pein et al. were used" |
| `eq17_alpha_H_Si`, `fig1_YA_intercept` | derived_from/derivation -> fig1_figure_only (slope; intercept "about −22 kJ") | PDF 3/14 right col |
| `eq20_alpha_K_Si` | -> fig2_figure_only (slope in X_SiO2 > 0.45 of Steiler's re-arranged K2O activities, Eq. 19) | PDF 4/15 left col |
| `eq24_alpha_H_Na` | -> fig3_figure_only + eq17 + table2 (Eq. 23 inputs α_H−Si, α_Na−Si) | PDF 4/15 right col |
| `eq25_alpha_H_Li`, `eq26_alpha_H_K` | -> eq17 + eq21 / eq17 + eq20 (Eq. 23 applied to Kurkjian & Russell C'OH; the plot is not printed) | PDF 4/15 right col |
| `eq28_alpha_H_Ca` | -> fig4_figure_only + eq17 + table2 (Eq. 27) | PDF 5/16 left col |
| `eq29_dG7` | -> fig5_figure_only (RT ln K7 vs T; intercepts of Figs. 3, 4) | PDF 5/16 left col |
| `eq31_alpha_H_Al` | -> fig6_figure_only + eq28 + eq17 + table2 + eq29 (Eq. 30) | PDF 5/16 right col |
| `eq32/33/34_alpha_H_Mg/Mn/P` | -> eq28 + eq17 + table2 + eq29 (Eq. 30; YD lines through the origin "observed", plots not printed; refs 17, 18, 20–23) | PDF 5/16 right col, PDF 6/17 left col |
| `table3_alpha_H_i` | -> the nine Eq. rows 17, 24–26, 28, 31–34 ("summarized in Table 3") | PDF 6/17 Table 3 and text |

Every `derivation.relation` restates the printed relation (Eq. 13/19/23/27/30) and names the printed inputs.
Where the input data are literature C'OH values the paper does not reproduce, the relation text says so; I did not invent a pointer to them.
I added a `fix_round` note and a `consolidation` note under `extraction:`.

**Legend temperatures** (main's ask, review A3). They are added to the existing figure rows as `legend_as_printed` + `legend_series` + `legend_note`, so the row count is unchanged. They are printed series labels, not read off the plots:
- Fig. 2 (PDF 4/15): Steiler ○ 1573 K, ● 1673 K, ⦶ 1773 K.
- Fig. 3 (PDF 4/15): Russell ● 1550 K; Kurkjian & Russell ⦶ 1740 K, ○ 1590 K, ⊖ 1430 K.
- Fig. 8 (PDF 6/17): Kurkjian & Russell △ KO0.5–SiO2 (1573K), ⊖ NaO0.5–SiO2 (1590K), □ LiO0.5–SiO2 (1573K).
The single-temperature boxes of Figs. 1, 4, 7 (1873 K) and Fig. 9 (1823 K) were already carried. Figs. 5, 6 and 10 print no legend temperature.

## Rows folded in from the hydroxyl record
- Main's note says "4 context records". That is the count on the original duplicate (32e031c9: notice, Kennedy, Eq. 14 range, K2O range).
  The version merged into mirror main (196f8df2) carries **25** context records: the notice plus 24 substantive records (P° 101 325 Pa, Kennedy conditions, X_SiO2 < 0.7 and > 0.82, K2O–SiO2 0.5–0.7 / Kurkjian & Russell, 1673 K Ban-ya et al., Figs. 7/8 text–caption swap, dir01–dir16, K2O(l) standard state).
- I moved **all 24** verbatim into slag-002: 23 under `H2O.context`, 1 under `K2O.context`.
  A Python check confirms they are byte-identical to the 196f8df2 text and parse-equal. All 27 observation payloads and all 11 fidelity_samples are unchanged apart from the method_class/attribution/lineage/legend keys listed above.
- I re-read the numbers in the moved records on the renders: 101 325 Pa (PDF 1/12), Kennedy 1 273–1 573 K, 200–970 MPa, 10–35 mol%, 1 743 K / 40 MPa / ~10 mol%, 1 743–1 993 K β-cristobalite (PDF 3/14), XSiO2 < 0.7 and > 0.82 (PDF 3/14), XSiO2 = 0.5–0.7 Fig. 8 and "Steiler23)" (PDF 4/15), 1 673 K ref 31 (PDF 7/18). The Table 2 and Table 3 cells and all Eq. coefficients and validity ranges in slag-002 were also re-read: 0 mismatches.

## Retirement method (no file deleted)
Neither SEAT-COMMON says how to retire a source, and the repo has no source-level retirement convention (only row-level `superseded_by` and this record's own `duplicate_of`). So I retired it in place and did not delete anything:
- `extracts/banya-hino-nagasaka-1993-hydroxyl.yaml` keeps its header and gains `extraction.retirement`. Its only row is the duplicate notice `banya_hino_nagasaka_1993_duplicate_notice`, rewritten to say `duplicate_of: slag-002-banya-hino-nagasaka-1993`, `retired: true`, `retired_by: b-733`, `consolidated_into: extracts/slag-002-…yaml`, `records_moved_to_consolidated_extract: 24` and the 24 moved ids.
  I removed the stale claims that the records live in this extract. `review_status` stays `draft`, because the validator allows only draft/reviewed/rejected and I did not want to guess "rejected".
- Ledger `banya-hino-nagasaka-1993-hydroxyl`: the `transcribed` note is corrected and a `stages.extracted.retired` block is added (duplicate_of, consolidated_into, note).
  Ledger `slag-002`: a `deepened` stage records the b-733 work.
- Totals are conserved: before, slag-002 had 27 obs + 0 ctx and hydroxyl 0 + 25. After, slag-002 has 27 + 24 and the hydroxyl stub 0 + 1.
  Migrating both files together still gives 27 observations, 25 context rows and no DuplicateContextId: each context id now exists in only one file.

## Checks (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 = `~/ci-scratch/regolith-green-ro`, PYTHONPATH there, `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python`; `engines/engines.local.toml` exists)
- Migrator `_migrate_extract` + `finalize()`:
  - slag-002 on mirror main: obs 27, queue 155, **hard 54**.
  - slag-002 at the new tip: obs 27, context 24, queue 101, `validation.ok True`, **hard 0**.
  - Both files together at the new tip: obs 27, context 25, hard 0. On mirror main the same pair gave hard 54.
  - Hydroxyl stub alone: obs 0, context 1, hard 0. Its one queue entry is "no observations", as expected.
- Payload survival: 101325, 1673, 1993, Kurkjian, Pein, Turkdogan and "pure liquid K2O" are all present in the migrated store. derived_from/derivation are set on 13 rows. The figure rows are `figure_only`, admission rejected.
- `tools/validate_literature_extracts.py --check-fidelity-match <both extracts>`: `OK: 2 extract file(s) valid`.
- `python -m pytest -q tools/test_ledgers_valid.py` (corpus): **723 passed**. `python3 tools/test_ledgers_valid.py` exits 0 but runs no test, because the file is a pytest module.
- `rg "/Users/|/private/"` on the four changed files: no matches.
- I ran neither build_index.py nor migrate_pilot_extracts.py.
- Sparse worktree `~/Repos/regolith-corpus/worktrees/b733-slag-002-consolidation` (54 MB; sparse = raw/text of banya-hino-nagasaka-1993-hydroxyl plus all sidecars; slag-002 has no raw/text) was removed after the push.
- Mac free disk: 58 GB at start, 64 GB at end.

## P0s
None. Every hard issue is cleared, no value changed and nothing was deleted.

## Known limits / P1 (not regressions)
- P1-a: all slag-002 value rows still decode as `unavailable` in the migrated store. The cause is that the simulator does not support the quantities (`quadratic_formalism_interaction_energy_alpha_*`, `water_vapor_solubility_deltaG`, `activity_reference_state_conversion_deltaG`, `cristobalite_melting_deltaG`, `YA_ordinate_intercept`). This was already so (review A2) and needs simulator quantity vocabulary, not a corpus change.
- P1-b: the legend temperatures live in the extract, but the migrated `figure_only` observations keep only a categorical payload, so they do not reach the store. If main wants them in the store, they need a context row; that would be a deliberate row addition.

## Open questions for main
1. Count: is moving all 24 records, not 4, what you intended? I think 4 was the pre-fix count.
2. Final disposition of the stub `extracts/banya-hino-nagasaka-1993-hydroxyl.yaml`: keep it as the retirement record (as now), delete it, or set `review_status: rejected`?
   Note that INDEX.yaml (main-only regen) lists this file as the only extract of the canonical corpus id and treats slag-002 as an alias. After the next regen the canonical id will point at the stub, unless build_index learns to follow `duplicate_of`/`consolidated_into` or you rename slag-002's `source_id`.
3. Are lineage rows 25, 26 and 32–34 acceptable? Their derived_from lists only parameters printed in this paper; their literature C'OH inputs are named in prose only.
4. Fig. 9 iso-C'OH legend bins (<0.004 … 0.010–0.012) are still not carried. They are not temperatures, and the first label prints with a faint decimal ("<0 004").
5. slag-002's observation locators keep the bare `page` key (= printed page) next to published_page/pdf_page_index. The moved context records do not have it. Unify?
6. Sidecar licence wording (review A5) is still untouched.

!COMPLETE: b-733 — 820632e99d295bdf6f5a6b3bb1f86f4b7db0fe87 on hunt/b-733-slag-002-consolidation; slag-002 hard issues 54 -> 0; 24 context records + Fig. 2/3/8 legend temperatures folded; hydroxyl extract retired in place (duplicate_of/consolidated_into notice, no deletion); totals 52 -> 52
