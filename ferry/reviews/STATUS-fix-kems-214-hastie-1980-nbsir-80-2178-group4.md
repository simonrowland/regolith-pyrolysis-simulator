# STATUS (group 4 of 5): fix kems-214-hastie-1980-nbsir-80-2178

**From:** regolith-empirical (fix seat, batch4 B)   **At:** 2026-10-05 ET
**Pushed tip after this group:** 9ae01ea6fcc5eb3c938d6e340a4cbf1a32731266 on hunt/kems-214-hastie-1980-nbsir-80-2178 (mac-studio-256-1:Repos/regolith-corpus.git). Parent 8bcaae19 (group 3). Not merged.
**Groups covered:** 1-4 (main report PDF 11-31; Appendix A apparatus PDF 33-56 with t3; NaCl PDF 57-70 with t4-t7; Na2SO4 PDF 71-85 with t8). Remaining: 5 (Appendix B, PDF 87-112, t9-t11).
**Items done:** 78 / 113. Group 4 contributed 19:
- 15 page-inventory rows (PDF 71-85).
- 4 cross-cutting items: Table 5 (t8) corrections, the a_Na2SO4 / a_Na2O reservoir distinction, Na2SO4 species assignment, and the Na2SO4 all-Pt Knudsen cell assignment.

## Group 4 (pages read at 220 dpi: PDF 71-85; Table 5 rotated and zoomed for every cell and underline; PDF 47 re-checked for the footnote-19 discrepancy)

**t8 (Table 5, 11 rows).** The draft (104fa4cb) t8 numerics were verified cell by cell and **kept**. They already contain the review's Table 5 corrections:
- Cubicciotti N2 deltaH +/- 1.7.
- Kohl 1196-1400 / 1290 K as Na: 73.5 +/- 2.5, LOG P not given.
- Kohl 1175-1375 / 1267 K Na2SO4: 4.87 - 14440/T, 65.9 +/- 3, 22.3 +/- 2.8.
- This-work molecular Na2SO4 row: 65.1 +/- 3.5.
- Uy restored; no alpha_Na.

What was **redone** in t8:
- Method and material strings now as printed: "transpiration (wt loss)", "Knudsen ms w/shutter", "platinum, mullite", JANAF materials "--".
- New column `directly_observed_species_underlined`. The draft provenance's underlining claim was wrong for Ficalora: Na, SO2 and O2 are underlined there. In the this-work TMS row, Na, SO2 and O2 are underlined.
- New column `row_method_class`: literature rows quoted_attributed, this-work rows measured_reduced.
- New column `row_note`: the Cubicciotti N2 inherited range, Ficalora's printed "uy" and the reanalysis-vs-78.8 note, and the JANAF print layout.
- Provenance rewritten to match.

**Table 5 context (table8_rows).** Redone:
- The whole-table quoted class now applies to rows 1-8 only.
- Per-row classes and underlining are carried in `rows`.
- The locator now notes the continuation on PDF 81.

**Page contexts PDF 71-85.** Numbers verified. Wording and classes corrected:
- PDF 71: class model_derived → measured_reduced. Added the NaCl Knudsen/TMS consistency and the Section 5 rationale; Kohl SO3 <10 % marked as quoted.
- PDF 72: added "R9 usually predominant", the constant-O2 / vary-SO2 protocol, muffle furnace, bake-out removal and the higher low-T Na pressure. The Fryxell reactions are marked as quoted.
- PDF 73: added in-situ drying monitoring, the aluminium-wall H2O source, Matheson spec and P_NaOH negligible.
- PDF 74: the draft's "neutral even after 2-3 h" is **corrected**. The print says "even for experiments as short as 2-3 hr, did not exhibit any basic pH character"; the neutral-pH observation is Fryxell's, now quoted.
- PDF 73-74 `activity_statements` (review: distinct reservoirs):
  - a_Na2SO4 ~1 is an assumption for the condensed, nearly pure salt.
  - a_Na2O < 4x10^-5 is an author-reduced bound for Na2O in Na2SO4 solution, with its relation.
- PDF 75: symbol definitions and Figure 13 caption. Added the typical point's JANAF agreement with Sbar = 0.6 +/- 0.06.
- PDF 76: universal-correction statement. The Figure 14 fit is now a structured `fits` entry.
- PDF 77: added the Eq 5.2 inputs, the saturated-sampling argument, the ±7 K causes and the slow SO2 response. The two P0-range fits are now structured.
- PDF 78: Figure 15 caption details. The KMS results are structured: R9 as Na 72.3 +/- 4 at 1165 K, and R10 second law 65.1 +/- 3.5.
- PDF 79: Figure 16 factor-2 caption and the congruent conversion.
- PDF 81: footnotes and per-row underlining.
- PDF 82: added the Ficalora, Kohl and Uhlig details and the conclusion.
- PDF 83: footnote 19 wording. Re-checked on the PDF 47 image: it prints "3.7 x 10^8 cm", and the discrepancy is preserved.
- PDF 84: added k varying slowly with T and P, and the expansion dependence.
- PDF 85: the 120-nozzle-diameter statement is corrected. It is the reported maximum-expansion-ratio position, not an "optimum spacing". Added platinum cones and Clausius-Clapeyron line.

**Species.** PDF 71-82 contexts and table8_rows were filed under K. They are moved to a new `Na2SO4` species section; migrated contexts now read Na2SO4 12, NaCl 19, K 80. PDF 83-85 (gas-dynamics footnote and discussion) stay with the generic TMS apparatus contexts.

**Pt Knudsen bench.** Added configuration (3): the Na2SO4 all-Pt cell (PDF 77 / 401).

**Not done (proposal):** no Na2SO4 TMS or KMS experiment record exists. Results are carried as located contexts. Adding experiment records would need full typed-absence sets, and main should decide on that.

## Draft disposition (cumulative)
- extract yaml PDF 11-85: kept or corrected as listed in groups 1-4.
- t3: kept (group 2).
- t8 csv: numerics kept, descriptive strings redone.
- t8 provenance: redone (underlining claim was wrong).
- t9: group 5.

## Checks at 9ae01ea6 (green 61ec839da, ~/ci-scratch/regolith-green-ro; engines/engines.local.toml present)
- Migrator + finalize: 1 work, 3 benches, 3 experiments, 61 observations, 121 contexts.
- Hard issues: 0 (183 advisory identity_incomplete).
- Fidelity validator --check-fidelity-match: OK (exit 0).
- Corpus tools/test_ledgers_valid.py: exit 0.
