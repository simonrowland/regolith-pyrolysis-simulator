# STATUS: fix renggli-2022-te-evaporation (batch3 B)

**From:** regolith-empirical corpus fix seat   **At:** 2026-10-05 ~21:50 ET
**Review applied:** review.md (FIX-FIRST on 276c83cb; 44 rows, 7 mismatches, 166 printed numbers not carried)

- **Start tip (verified):** `276c83cbcc30721a1477e9d383e498279e646121`. `git fetch origin hunt/renggli-2022-te-evaporation` on mirror `mac-studio-256-1:Repos/regolith-corpus.git` resolved to exactly this sha.
- **New tip on mirror:** `9b607b727e24de66eed1d520e9f280eaff88e199` on `hunt/renggli-2022-te-evaporation`. Pushed to origin only; `git ls-remote` confirms it. Not merged to mirror main.
- **Items fixed: 8/8** (required changes 1–8). Item 1 is 6/7 cells changed: the 7th cell, DB23, is a review error. The print shows `3.6 ± 0.2`, the same as the CSV, so it was left alone (see below).
- **Corrections: 23 numeric cell edits.**
  - 15 CSV cells: 8 corrected values and 7 restored weights.
  - 8 mirrored YAML point fields: DB08 log fO2 and the 7 weights.
  - Additions: 11 equations/relations and 55 located facts (N01–N55, 159 numeric occurrences). Together with the 7 weights, that covers all 166 printed numbers the review listed as not carried.
  - Also: 5 locator/meaning fixes, the evidence-class split, the rights statement, and the DB02 reference inconsistency.
- **Hard issues: 0** (migrator after finalize). Fidelity validator OK. `tools/test_ledgers_valid.py` 687 passed.

## Table 1 (p. 40, PDF index 5): each cell re-checked on a 400 dpi render and against the born-digital text layer

| Row | Column | Was | Now (print) | Source |
|---|---|---|---|---|
| DB08 | log fO2 | −6.91 | −8.86 | review item 1. Changed in CSV and in the YAML point. |
| DB20 | δ128/126Te ±2s.d. | 1.56 ± 0.01 | 1.56 ± 0.03 | review item 1 |
| CR09 | Te ±2σ | 13.3 ± 0.9 | 13.3 ± 3.9 | review item 1 |
| DB24 | Te ±2σ | 3.9 ± 0.4 | 3.9 ± 0.2 | review item 1 |
| DB24 | +2s.d. / −2s.d. | 0.06 / 0.07 | 0.07 / 0.08 | review item 1 |
| DB13 | δ128/126Te ±2s.d. | 0.33 ± 0.03 | 0.33 ± 0.01 | **found in this pass**; the review had marked it "=" |
| CO10 | δ128/126Te ±2s.d. | 0.22 ± 0.01 | 0.22 ± 0.02 | **found in this pass**; the review had marked it "=" |
| DB01, DC01, DB02, CO01, DB03, CR01, DB04 | Weight (mg) | blank | 6.3, 8.7, 20.6, 11.9, 24.0, 9.3, 13.6 | review item 2. Restored in CSV and YAML points. |
| DB23 | Te ±2σ | 3.6 ± 0.2 | **unchanged**: the print is 3.6 ± 0.2 | The review asked for ±0.4. The 400 dpi crop and the PDF text layer both read `3.6 ± 0.2`, so the review's DB23 item does not hold. DB23's log errors are 0.07/0.08, which matches the CSV. |

After the fix, a script compared all 43 CSV sample rows with the PDF text layer and found 0 differences. It compared every numeric value; signs were checked separately on the image. The 43 YAML points match the CSV central values (0 mismatches). The provenance now carries the caption and footnotes a–d verbatim. Its note describes this fix pass and no longer makes the unsupported "footnotes fully transcribed" claim.

## Other required changes

- **Item 3, equations:** each equation is a located `equation` context with its printed variable definitions:
  - Eqs. 1–2 (p. 38).
  - Eq. 3 and the unnumbered fTe definition (p. 39).
  - Eqs. 4–6, the K* = K αev γTe definition, n = 0 and the isotope-fit relation with slope 1000(1−αK) (p. 41).
  - (fTe2/fO2)^1/2 and Eq. 7 (p. 42).

  The phase labels (l)/(g) are kept. No activity standard state was invented: γTe and αev are printed as unknown.
- **Item 4, N01–N55:** all are carried as verbatim quotes in contexts located by page and section:
  - p. 35 intro and abstract; p. 36 §1 and §2.1; p. 36/37/38 §2.2; p. 38 §2.3 digestion, chemistry and MC-ICP-MS sequence.
  - p. 39 §2.4 and §3.1; p. 39 §4.1 model; p. 39/40 melt-speciation literature; p. 40 Te inference.
  - p. 41 model and αK; p. 42 §4.1, §4.2 αlowT (with the 8-sample subset) and §5; p. 43 Fig. 4 caption; p. 44 §5.

  The directional and qualifying statements the review listed are included.
- **Item 5:** the Table 1 context carries the printed caption (DB02), the §3.1 "bulk starting glass" wording, and the inconsistency itself. DB02 is printed with fTe 1.11 and DB05 with 0.86; both ratios match the 6839 µg/g initial mean. The ratios are a seat arithmetic check, labelled as such. Nothing is recomputed.
- **Item 6:**
  - The bench `gas_volume` point is now `approximate: true`; the migrated value shows `"approximate": true`.
  - The blank now reads "≪1%" (the method context quotes the print).
  - Locators: MORB <10 ppb is on p. 36; the speciation calculation and the −4/−3.1 (ΔFMQ+5.3) thresholds are on p. 39 §4.1; the 1.47 maximum is in p. 39 prose and on p. 40 (CR06); lunar FMQ−3.3 to −6 and ~580 °C are on p. 42; 0.28 ppm is on p. 44, as the **66095 anorthosite leachate (66095, 425B L)**.
  - The aggregate pp. 37–43 numeric row is gone. The figure-only row now holds only figure content (pp. 37–44).
- **Item 7:**
  - Table 1 has a `column_evidence` field: conditions vs measured (Te, N, δ) vs author-reduced (fTe, log fTe and its errors, Δ).
  - Literature values are `quoted_attributed`, with an attribution field for each context.
  - The HSC model and the equations are `model_derived`.
  - Author inferences are `author_estimate`.
  - αK and αlowT are `model_derived`, with a note that each is an author-derived fit. `author_derived` would queue for missing measured lineage, which these context rows do not carry.
  - All context method classes resolve through `evidence_for` without a queue reason.
- **Item 8:** the context `renggli_2022_rights_statement` carries the printed "© 2022 Elsevier Ltd. All rights reserved." (p. 35) and, separately, where the file was acquired. In the sidecar, `licence` is now the printed rights statement, and the author-page note moved to a new `acquisition_note` key.

Values printed as shown but not reconciled are flagged in context notes:
- Fig. 1's "1301 °C in air" panel has no matching Table 1 row.
- The duplicate labels 1400b/1450c/1500c and "3.3–3.9 ng/g" are printed as shown.
- The FMQ minimum is quoted "from 1350 to 1500 °C".

Nothing illegible was found, so no new typed absences were needed.

## Validation (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)
- Reader: `~/ci-scratch/regolith-green-ro`, detached at 61ec839da and used read-only; `git status --short` was empty afterwards. Interpreter: `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, with `PYTHONDONTWRITEBYTECODE=1`. `engines/engines.local.toml` EXISTS in that checkout.
- Migrator: `Migrator(root=green, index={}, aliases={})`, then `_migrate_extract(<worktree extract>)`, then `finalize()`. Result: **hard issues 0**, 0 issues in total. Counts: works 1, benches 1, experiments 1, observations 0, contexts 35 (was 5). Evidence fallthrough `{}`. The one queue entry is "extract yielded no observations". It is identical on 276c83cb and is expected, because the extract has no scored observations.
- Fidelity validator: `tools/validate_literature_extracts.py --check-fidelity-match <absolute worktree extract path>` (run from green cwd) gave `OK: 1 extract file(s) valid`, exit 0.
- Ledger tests: `tools/test_ledgers_valid.py` gave 687 passed (pytest); the direct run exits 0.
- `rg '/Users/|/private/'` over the extract, `tables/<sid>/` and the sidecar exits 1 (no matches).
- Pages: all 11 rendered with `pdftoppm -r 230` and read. Table 1 was also rendered at 400 dpi (and 800 dpi), and pp. 38, 39, 40, 41 and 42 were read at 400 dpi crops. The PDF sha256 matches the sidecar: b15a8cf5…ef52e95.
- Corpus worktree: sparse `~/Repos/regolith-corpus/worktrees/fix-renggli-2022-te-evaporation` (48 MB). It was removed after the push. No build_index or migrate_pilot script was run. INDEX regeneration is needed at merge.
- Commit `9b607b72`: explicit pathspecs (4 files: extract, t1.csv, t1.provenance.yaml, raw sidecar), author Simon Rowland, no AI/co-author trailers.

!COMPLETE: fix-renggli-2022-te-evaporation — 9b607b727e24de66eed1d520e9f280eaff88e199, items fixed 8/8, corrections 23, hard issues 0
