# STATUS (group 3 of 5): fix kems-214-hastie-1980-nbsir-80-2178

**From:** regolith-empirical (fix seat, batch4 B)   **At:** 2026-10-05 ET
**Pushed tip after this group:** 8bcaae190613b1815b7ff9394e4b7bb4086cf0d2 on hunt/kems-214-hastie-1980-nbsir-80-2178 (mac-studio-256-1:Repos/regolith-corpus.git). Parent 7b6b928f (group 2). Not merged.
**Groups covered:** 1 (main report, PDF 11-31), 2 (Appendix A apparatus/gas dynamics, PDF 33-56, t3), 3 (Appendix A NaCl, PDF 57-70, t4-t7, Pt Knudsen cell). Remaining: 4 (Na2SO4, PDF 71-85, t8), 5 (Appendix B, PDF 87-112, t9-t11).
**Items done:** 59 / 113 (group 1: 11; group 2: 28; group 3: 20 = 14 page-inventory rows PDF 57-70 + 6 cross-cutting: t4, t5, t6 and t7 method-class/lineage corrections, Pt-cell configurations with the benches[2]/experiments[2] absence audit, NaCl species assignment).

## Group 3 (pages read at 220 dpi: PDF 57-70, plus PDF 91 top half for the slag Pt cell)
- **Draft (104fa4cb) page contexts PDF 57-70:** every number re-checked on the images. The numbers were kept; the wording was corrected or extended on every page:
  - PDF 57: footnote c-g values now tied to their rows (c = Pt-Knudsen Na+ 920 microV at 10^7 ohm; d/e = 1312 K row: 1.8x10^4 microV, N2 0.54 atm, I29 = 1.00, k_N2 1.66x10^-10; f/g = 1360 K row: weight-loss run, "S factors differ to e", k_N2 3.7x10^-11 (0.65 atm), 1.9x10^4 microV). Added the missing "no measurable NaCl+ over solid NaCl" and (NaCl)2 -> Na2Cl+ from footnote 7. The draft's "allowing R about 2" now reads as printed: R about 2 "cannot be ruled out". Feather/Searcy and Grimley are split out as quoted_components.
  - PDF 58: added the furnace-off/sampler test. Berkowitz LiF is now marked as quoted.
  - PDF 59: added both-ion liquid data, melting-point agreement, and "dimer correction may be too high but within quoted uncertainty".
  - PDF 60: Figure 8 symbol/method assignments and the method (b) variable definitions.
  - PDF 61: Table 3 footnotes a/b, plus the k', S and sigma meaning of k_NaCl.
  - PDF 62: Table 4 footnotes a-d (incl. typical +/-0.2 and Sbar = 0.6) and the N2/O2 literature agreement.
  - PDF 63: Sbar invariance and applied-to-all-condensibles. Both TMS fits are also carried as structured `fits`.
  - PDF 64: the key-uncertainty statement and the condition for k_Na2Cl2 = 4.0 k_N2.
  - PDF 65: the Figure 9 symbol assignments, plus a structured R7 fit.
  - PDF 66: saturated-flow and isothermal/isentropic statements, plus the Section 4.4 motivation.
  - PDF 67: Figure 10 ion set, unsaturation order and footnote 10.
  - PDF 68: k constants carried as structured `calibration_constants` with units as printed.
  - PDF 69: the KMS R1/R3/R5 fits are structured; literature values in parentheses are quoted_components.
  - PDF 70: Table 4 header semantics and the Figure 12 caption.
- **Species correction:** all NaCl contexts (PDF 56-70, table4-7 rows; 19 entries) were filed under species K, so the Migrator tagged them species K. They are moved to a new `NaCl` species section, and the migrated contexts now read NaCl 19 / K 92.
- **t4 (Table 2, 5 rows):** all 40 body cells match the print, so they were kept. The cell column is now carried as printed ("Pt-transp."). New columns: row_method_class (rows 1-2 quoted_attributed [31]/[29], rows 3-5 measured_tabulated), Na+ and cell footnote letters, and each footnote's printed numbers (920, 1.8e4, 1.9e4 microV; N2 0.54/0.65 atm; k_N2 1.66e-10/3.7e-11). The provenance records the blank row-5 source cell. The context gives per-row classes and the R relation. Its wrong experiment link (hastie_1980_nacl_kems, a KMS run) is removed, because rows 4-5 are TMS.
- **t5 (Table 3, 9 rows):** values match. New value_class column: measured / auxiliary_input_JANAF_derived / author_reduced. The printed relation and its inputs are now carried. A reviewer recomputation (not printed; STP 22414 cm^3/mol) gives 2.97e-2 vs 2.96e-2 atm printed, and 0.77 x 2.96e-2 = 2.28e-2. The wrong KMS experiment link is removed, because this is TMS method (a).
- **t6 (Table 4 cross sections, 7 rows):** NaCl "1.0" is **redone** as printed "1.00". New per-row author_value_class (measured_reduced; (NaCl)2 = author_estimate), literature_value_class (quoted_attributed Kieffer/Dunn) and footnote columns. The provenance carries footnotes a-d.
- **t7 (Table 4 dimerization, 4 rows):** the Knudsen intercept uncertainty "0.20" is **redone** as printed "0.2". New method_class column: JANAF quoted_attributed, the other three measured_reduced. The context carries the log Kd = A/T + B definition. No curves digitized.
- **Pt Knudsen bench (benches[2]):**
  - The two configurations are now distinct: NaCl lightweight welded vertical cell (PDF 66 / 390) and slag weld-sealed cells (PDF 91 / B5).
  - The generic PDF 91 locator for NaCl facts is corrected to index 65 / printed 390.
  - The single bench orifice diameter is a typed absence `would_invent`. Configuration-specific other_facts carry NaCl 0.051 cm, slag about 0.025 cm (approximate: true, survives migration), NaCl lid 0.01 cm and pyrometer reading uncertainty 5 K.
  - Thermocouple is marked as the primary temperature source.
- **NaCl KMS experiment (experiments[2]):**
  - Calibration **redone**. The draft said "ion-intensity comparison" (a TMS method) at the Table 2 locator. It is now the integrated signal-weight-loss Eq 4.7 with the 20/80 partition, trimer <1 % and the k constants, at PDF 67 / 391.
  - Reference substance **redone**: the NaCl charge itself, not "NaCl vapor" at the TMS Table 3 locator.
  - 30 eV kept, with a note.
  - Channel-length note quoted correctly, with the lid thickness.
  - total_pressure: unknown kept, narrowed so no TMS vacuum values are substituted.
  - sweep_gas: not_applicable kept, with evidence.
  - regime: unknown kept.

## Draft disposition (cumulative)
- extract yaml: PDF 11/12/13/15/18 and 33-56 contexts were kept or corrected as listed in groups 1-2. The PDF 57-70 contexts keep their numbers, but the wording and classes were corrected (above). The table4-7 row contexts were redone.
- t3: verified, kept in group 2. t8/t9 csv and t8 provenance: still to verify (groups 4-5).

## Checks at 8bcaae19 (green 61ec839da, ~/ci-scratch/regolith-green-ro; engines/engines.local.toml present)
Migrator + finalize: 1 work, 3 benches, 3 experiments, 61 observations, 121 contexts; hard issues 0 (183 advisory identity_incomplete). Fidelity validator --check-fidelity-match: OK (exit 0). Corpus tools/test_ledgers_valid.py: exit 0.
