# REPORT: pedley-marshall-1983-gaseous-monoxides r2, round 2 (Tables 8–9, Gibbs energy functions)

from: regolith-empirical   to: regolith-main   date: 2026-10-05 (ET)
item: batch6 B2 (to-empirical-2026-10-05-batch5-7), extraction continuation, round 2 of 4
branch: hunt/pedley-marshall-1983-gaseous-monoxides-r2
commits:
- 59c13894e3d720a142011fea04633b7392ee844e: Tables 8–9 as located context (extract, ledger, t8/t9 CSV + provenance)
- cd7002b1e85e58050ed9727e9d982496670120ed (tip): corrects the visual-read row-strip count in t8/t9 provenance from 21 to 22
Both commits are authored by Simon Rowland <simon@simonrowland.com>, have no trailers, were pushed fast-forward from d048b420, and have no PDFs or index changes.

## What was extracted
- Table 8, "Gibbs energy functions −(G°T − H°298)/T", cal K-1 mol-1, published pp. 986–991 (PDF pp. 21–26)
- Table 9, the same function in J K-1 mol-1, published pp. 991–996 (PDF pp. 26–31)
- Layout: 8 page-groups of about 10 species × 41 temperature rows each, with 78 species in total. The species are AgO … ZrO plus O2 (the O2 reference column is printed in group 5). Temperatures are 100, 200, 298, 300, 400, 500 … 4000 K. There are no footnotes and no dash cells.
- Storage: two context records, pedley_marshall_1983_table_8_gibbs_energy_functions and pedley_marshall_1983_table_9_gibbs_energy_functions. Each has type compilation_table, method_class model_derived, a locator ({published_page, published_pages, table}), a title quote, notes, and rows (41 dicts: T_K + 78 species as floats). The CSV evidence keeps the literal printed 3-decimal strings. These are author-calculated functions, so they stay context-only (per REQ) and are not promoted to observations.
- Ledger: added t8/t9 locator entries (41 rows, full), set completeness table_8/table_9 to true, and added round r2-2 under stages.extracted.rounds. The extract's extraction.method, completed_scope and remaining_scope were updated.

## How the values were verified
The scan is 1-bit at about 323 ppi, which is too noisy for single-pass OCR on 6396 small numerals. Table 8 and Table 9 are the same quantity in two units, so they cross-check each other cell by cell: a value pair is accepted only if |T8 × 4.184 − T9| ≤ 0.003, which allows for rounding of both printed values to 3 decimals.
1. Engine A was the PDF text layer (pdftotext -layout). Engine B was tesseract (psm 6) on 450 dpi renders of each page-group.
2. Of 3198 species-temperature pairs:
   - 1898 were read identically by both engines on both tables and pass the check
   - 1049 were resolved by taking the engine value (or a third row-strip OCR pass) that passes the check
   - the remaining 210 pairs, in 22 group × temperature row strips, were read by eye from 450 dpi strips (both tables), and all 210 pass the check
3. O2 (41 pairs) never passes, so both O2 columns were read by eye from dedicated 450 dpi crops.
4. Monotonicity checks: every species column decreases from 100 to 298 K, gives 298 K ≈ 300 K (within 0.002), and increases from 300 to 4000 K. MgO and ZrO have small second-difference sign changes. These appear identically in both tables, so they are in the printed source.

## Finding: O2 Table 8 vs Table 9 (kept as printed)
At every temperature, Table 9 O2 minus Table 8 O2 × 4.184 is 5.759 to 5.764 J K-1 mol-1. Examples:
- 298 K: 48.999 cal → 205.012 J, but 210.775 is printed
- 100 K: 55.197 → 230.944, but 236.707 is printed

The offset is constant and equals R ln 2 = 5.763 J K-1 mol-1, the contribution of the O2 rotational symmetry number (σ = 2). One of the two columns appears to omit (or double-apply) the symmetry correction. The paper does not say which, so neither column is corrected. The context notes record the inconsistency with the two examples. The R ln 2 interpretation is my arithmetic observation and is not in the context records. If O2 is ever used from this source, check it against JANAF first.

## Checks run (green 61ec839da at ~/ci-scratch/regolith-green-ro, engines.local.toml present)
- tools/validate_literature_extracts.py --check-fidelity-match <extract>: OK: 1 extract file(s) valid
- Migrator + finalize (read-only harness): hard issues 0; observations 132 (unchanged; child temperature 298 K on 132/132); experiments 1; context records 6 (T4/T5 from LAND, T6/T7 from round 1, T8/T9 from this round)
- Payload: for T8 and T9 the migrated context rows equal the CSV (41 rows × 79 keys, 3239 cells per table): 0 mismatches, 0 non-numeric values. The T6/T7 payload checks re-ran with 0 mismatches.
- evidence_for('model_derived'): resolves to class model_derived
- tools/test_ledgers_valid.py: 723 passed
- rg for absolute paths in the extract, ledger and tables: none
- Not run: build_index.py, migrate_pilot_extracts.py (as instructed)

## Remaining for B2
- Round 3: Tables 10–11, enthalpy functions (H°T − H°298) in kcal/kJ, pp. 997–1007. Same cross-unit method.
- Round 4: Appendix II, pp. 1017–1021.
- Tables 1–3 and Appendices I and III are located context only, as in the REQ.
