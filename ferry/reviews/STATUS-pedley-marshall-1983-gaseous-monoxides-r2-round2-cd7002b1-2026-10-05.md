# STATUS: batch6 B2 pedley-marshall-1983-gaseous-monoxides r2, round 2 (Tables 8–9): DONE, pushed

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~23:00 ET
branch: hunt/pedley-marshall-1983-gaseous-monoxides-r2 (corpus mirror)
tip: cd7002b1e85e58050ed9727e9d982496670120ed (pushed; ls-remote verified). Round content is 59c13894; cd7002b1 only fixes a count in the t8/t9 provenance notes (it said 21 visual row strips; the correct number is 22)
parent round: round 1 tip d048b420 (Tables 6–7)
INDEX: not regenerated (sparse tree)
round scope: Table 8 (pp. 986–991, Gibbs energy function in cal K-1 mol-1) and Table 9 (pp. 991–996, the same in J K-1 mol-1). Each table has 41 temperatures (100, 200, 298, 300, 400 … 4000 K) × 78 species = 3198 cells, for 6396 cells in all. They are stored as located model_derived context (author-calculated functions stay context-only). There are 0 new observations.
evidence: tables/<sid>/t8.csv, t9.csv (wide: T_K + 78 species, literal printed strings), t8/t9.provenance.yaml
verification: every cell passes |T8 × 4.184 − T9| ≤ 0.003, except the O2 column (see below):
- 1898 pairs were read identically by both OCR engines (PDF text layer and tesseract at 450 dpi) on both tables
- 1049 pairs were resolved by one engine, or by a third row-strip OCR pass, and pass the cross-unit check
- 210 pairs (22 row strips) were read by eye from 450 dpi strips, and all pass the cross-unit check
- the O2 column was read by eye in both tables (41 pairs)
checks (green 61ec839da, ~/ci-scratch/regolith-green-ro):
- validator --check-fidelity-match: OK: 1 extract file(s) valid
- Migrator + finalize: hard issues 0; 132 observations (unchanged, T=298 on 132/132); 1 experiment; 6 context records
- payload: T8 and T9 CSV == migrated context rows (41 rows × 79 keys, 3239 cells each), 0 mismatches, 0 non-numeric; T6/T7 checks still 0 mismatches
- evidence_for('model_derived') resolves
- test_ledgers_valid: 723 passed
finding kept as printed: the Table 9 O2 column is not Table 8 O2 × 4.184 at any temperature. Table 9 is higher by a constant 5.759–5.764 J K-1 mol-1 at every temperature, which equals R ln 2 (5.763), i.e. the O2 symmetry-number term. One of the two O2 columns probably omits σ = 2, but the source does not say which, so the data are not corrected. The note is in both context records.
other sanity checks: every column falls from 100 to 298 K, matches at 298/300 K, and rises to 4000 K. MgO and ZrO show the same small non-concavities in both tables, so these come from the printed source and not from transcription.
next rounds: Tables 10–11 (pp. 997–1007, enthalpy functions), Appendix II (pp. 1017–1021)
report: REPORT-pedley-marshall-1983-gaseous-monoxides-r2-round2-cd7002b1-2026-10-05.md
