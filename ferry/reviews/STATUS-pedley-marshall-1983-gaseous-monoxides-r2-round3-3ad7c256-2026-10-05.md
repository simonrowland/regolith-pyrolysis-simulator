# STATUS: batch6 B2 pedley-marshall-1983-gaseous-monoxides r2, round 3 (Tables 10–11): DONE, pushed

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~23:45 ET
branch: hunt/pedley-marshall-1983-gaseous-monoxides-r2 (corpus mirror)
tip: 3ad7c256044d676698779b501257207e916adf5f (pushed; ls-remote verified; parent cd7002b1 = round-2 tip)
INDEX: not regenerated (sparse tree)
round scope: Table 10 (pp. 997–1002, H°T − H°298 in kcal mol-1) and Table 11 (pp. 1002–1007, the same in kJ mol-1). Each table has 42 temperatures (0, 100, 200, 298, 300, 400 … 4000 K) × 78 species = 3276 cells, for 6552 cells in all. They are stored as located model_derived context (author-calculated functions stay context-only). There are 0 new observations.
evidence: tables/<sid>/t10.csv, t11.csv (wide: T_K + 78 species), t10/t11.provenance.yaml
verification (stricter than round 2): every pair passes |T10 × 4.184 − T11| ≤ 0.0026, and each cell is either read identically by at least 2 of 3 passes (text layer, page tesseract, row-strip tesseract) or read by eye:
- 1762 pairs read identically by all three passes
- 1136 pairs read identically by two passes
- 77 pairs where the kJ cell has ≥2 votes and uniquely implies the kcal cell
- 301 pairs (56 row strips) read by eye, all passing the cross-unit check; 498 visual reads of already-resolved cells in those strips agree exactly
round-2 re-audit: under the same strict rule, Tables 8–9 change 0 committed cells. The 95 pairs that relied on a single engine read of the J value (30 row strips) were all checked by eye against the page images and match. Round 2 needs no correction.
checks (green 61ec839da, ~/ci-scratch/regolith-green-ro):
- validator --check-fidelity-match: OK: 1 extract file(s) valid
- Migrator + finalize: hard issues 0; 132 observations (unchanged, T=298 on 132/132); 1 experiment; 8 context records
- payload: T10 and T11 CSV == migrated context (42 rows × 79 keys, 3318 cells each), 0 mismatches, 0 non-numeric; the T6–T9 payload checks still show 0 mismatches
- test_ledgers_valid: 723 passed; no absolute paths
findings (noted in context):
- 4 Table 11 cells at 100 K (BaO, BeO, BiO, BrO) are printed with no minus sign (confirmed on the 450 dpi crop). They are stored signed (−6.101, −5.781, −6.082, −6.140), because Table 10 prints them negative and H°T − H°298 < 0 below 298 K. The magnitudes are as printed. If literal strings are preferred, this is the only deviation from the printed text.
- Steps in the 100 K increments are printed identically in both tables, so they are in the source: GaO 3300→3400 K, LiO 2000→2100 K, SrO 3900 K, and smaller ones in BeO, BrO, AsO, MgO and ZrO
- Unlike Tables 8–9, the O2 column of Tables 10–11 is consistent ×4.184 at every temperature, which supports the R ln 2 (symmetry-number) reading of the Table 8/9 O2 offset
- Text-layer errors fixed by the cross-check: Table 10 YbO 3000 K is 23.585 (the text layer reads 22.585), and Table 11 ZnO 2300 K is 73.268 (72.268)
next round: Appendix II (pp. 1017–1021)
report: REPORT-pedley-marshall-1983-gaseous-monoxides-r2-round3-3ad7c256-2026-10-05.md
