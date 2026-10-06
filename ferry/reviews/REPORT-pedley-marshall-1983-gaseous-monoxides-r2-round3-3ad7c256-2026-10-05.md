# REPORT: pedley-marshall-1983-gaseous-monoxides r2, round 3 (Tables 10–11, enthalpy functions)

from: regolith-empirical   to: regolith-main   date: 2026-10-05 (ET)
item: batch6 B2 (to-empirical-2026-10-05-batch5-7), extraction continuation, round 3 of 4
branch: hunt/pedley-marshall-1983-gaseous-monoxides-r2
commit: 3ad7c256044d676698779b501257207e916adf5f (tip). It covers the extract, the ledger, and the t10/t11 CSVs and provenance. Author Simon Rowland <simon@simonrowland.com>, no trailers, fast-forward from cd7002b1, no PDFs, no index changes.

## What was extracted
- Table 10, "Enthalpy functions, (H°T − H°298), for gaseous monoxides in kcal mol-1", published pp. 997–1002 (PDF pp. 32–37)
- Table 11, the same in kJ mol-1, published pp. 1002–1007 (PDF pp. 37–42)
- Layout: the same 8 page-groups and 78 species as Tables 8–9. There are 42 temperature rows (0, 100, 200, 298, 300, 400 … 4000 K). The 298 K row is 0.000 throughout, and values are negative below 298 K. There are no footnotes.
- Storage: two context records, pedley_marshall_1983_table_10_enthalpy_functions and pedley_marshall_1983_table_11_enthalpy_functions. Each has type compilation_table, method_class model_derived, a locator ({published_page, published_pages, table}), a title quote, notes, and rows (42 dicts: T_K + 78 species as floats). The CSV evidence keeps the printed 3-decimal strings, apart from the 4 signs below.
- Ledger: added t10/t11 locator entries (42 rows, full), set completeness table_10/table_11 to true, and added round r2-3. The extract's method, completed_scope and remaining_scope were updated, so remaining_scope is now Appendix II plus the located-context items.

## How the values were verified
In round 2, a cell pair was accepted when the cal and J values agreed within rounding. That check pins the cal value to its last digit. It does not pin the last digit of the larger-unit value, because the tolerance window is about ±0.0026, which spans up to 5 last-digit values. So in round 3 I added a third independent read (tesseract on each row strip) and required agreement between reads, not just the cross-unit check:
1. Each cell was read three ways: the PDF text layer, tesseract psm 6 on the 450 dpi page, and tesseract psm 7 (digits only) on each 450 dpi row strip. Signs come from the temperature: negative below 298 K, zero at 298 K, positive above. This was then checked against the print (see the sign finding).
2. A pair is accepted if |T10 × 4.184 − T11| ≤ 0.0026 (the largest possible difference between two values each rounded to 3 decimals) and one of these holds:
   - both cells were read identically by at least 2 of the 3 passes (1762 unanimous, 1136 two-pass)
   - the kJ cell has ≥2 votes and only one 3-decimal kcal value fits it, which is the one read (77)
3. The remaining 301 pairs (56 group × temperature row strips) were read by eye from 450 dpi strips (both tables), and all 301 pass the cross-unit check. Those strips also hold 498 cells that were already resolved, and my visual reads of them agree with the resolved values exactly. One visual 3/5 ambiguity (Table 10 CaO 2900 K, 27.565) was settled by the cross-check and the two-pass reads.
4. Sanity checks: every column is negative and increasing at 0–200 K, exactly 0.000 at 298 K, and strictly increasing from 300 to 4000 K.

## Round-2 re-audit (Tables 8–9, already pushed at cd7002b1)
I applied the stricter rule above to Tables 8–9, adding a row-strip tesseract pass over all 656 rows, with O2 excluded because it is a known source inconsistency.
- Result: 0 committed cells differ from the strictly resolved values.
- 95 pairs had a J cell supported by only one pass. I checked all of them by eye on 30 row strips (both tables, against page images), and every committed value matches the print.
- Round 2 therefore needs no correction. Its STATUS counts (1898/1049/210) describe the original method; this audit adds the stronger check.

## Findings (kept as printed unless stated)
- Missing minus signs: Table 11 at 100 K prints BaO 6.101, BeO 5.781, BiO 6.082 and BrO 6.140 with no minus. A zoomed crop of the 450 dpi render confirms the minus is missing. The matching Table 10 cells print −1.458, −1.382, −1.454 and −1.468, and H°T − H°298 must be negative below 298 K. They are stored as −6.101, −5.781, −6.082 and −6.140, with a context note naming the four cells. This is the only place where a stored string differs from the printed text. If main prefers literal strings, change those 4 CSV cells and the 4 context values back.
- Steps in the 100 K increments (the implied Cp jumps) appear identically in Tables 10 and 11, so they are in the source:
  - GaO: Table 10, 26.795 at 3300 K → 27.902 at 3400 K, an increment of 1.107 against about 0.93 on either side, with a higher level afterwards
  - LiO: 2000 → 2100 K
  - SrO: 3800–4000 K
  - smaller steps in BeO, BrO, AsO, MgO and ZrO
  These probably come from the author's partition-function treatment (piecewise electronic levels). They are not corrected.
- O2: in Tables 10/11 the O2 column agrees ×4.184 at all 42 temperatures. The symmetry number cancels in H°T − H°298, which fits the round-2 finding that the Table 8/9 O2 Gibbs-function columns differ by a constant R ln 2 (an omitted or extra σ = 2 term in one table).
- The cross-check caught two text-layer errors: Table 10 YbO 3000 K = 23.585 (text layer 22.585), and Table 11 ZnO 2300 K = 73.268 (text layer 72.268). Both are confirmed by the other passes and by the other unit table.

## Checks run (green 61ec839da at ~/ci-scratch/regolith-green-ro)
- tools/validate_literature_extracts.py --check-fidelity-match <extract>: OK: 1 extract file(s) valid
- Migrator + finalize (read-only harness): hard issues 0; observations 132 (unchanged; T = 298 K on 132/132); experiments 1; context records 8 (T4–T11)
- Payload: for T10 and T11, CSV == migrated context rows (42 rows × 79 keys, 3318 cells per table), with 0 mismatches and 0 non-numeric values. The T6–T9 checks re-ran with 0 mismatches.
- evidence_for('model_derived') resolves; tools/test_ledgers_valid.py: 723 passed; no absolute paths
- Not run: build_index.py, migrate_pilot_extracts.py

## Remaining for B2
- Round 4: Appendix II, pp. 1017–1021
- Tables 1–3 and Appendices I and III are located context only, as in the REQ.
