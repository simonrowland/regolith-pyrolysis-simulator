# Kim et al. (2004) extraction audit

Input order: MinerU tables and markdown, then `pdftotext-layout.txt`. All three pages are born digital. Tables 1, 2 and 3 were checked against the page render at 200 dpi for row/column assignment and full numeric cell fidelity. Rendered pages are PDF pages 2–4 (printed pages 192–194).

| Printed table | Printed page | Printed rows | Audit |
|---|---:|---:|---|
| 1, molten float-glass composition | 192 (PDF page 2) | 9 components | MinerU table has 9 body rows. QA reported two numeric mismatches and 0.846 agreement; each row and digit was checked against `pdftotext-layout.txt` and the render. All nine values match. |
| 2, sodium-silicate check melt | 193 (PDF page 3) | 9 temperature rows | MinerU and pdftotext agree on all numeric cells (1.000 QA agreement). The render confirms the two measurement groups and blank cells; 9 rows have populated EMF/activity pairs. |
| 3, float glass | 194 (PDF page 4) | 8 temperature rows | MinerU and pdftotext agree on all numeric cells (1.000 QA agreement). The render confirms columns for measurement 1 elevating, measurement 1 lowering, and measurement 2; total 12 populated EMF/activity pairs. |

Table 1's printed values are SiO2 70.175, Na2O 13.190, CaO 10.046, MgO 5.852, Al2O3 0.319, SO2 0.242, Fe2O3 0.035, Cl 0.106, TiO2 0.035 m/o. The text and render agree; no decimal-comma, sign, exponent, superscript, or uncertainty flags were present.

The committed cell audit contains 68 numeric cells: 9 composition cells, 27 populated Table 2 cells, and 32 populated Table 3 cells. Every cell is recorded by absolute PDF page, printed table, data row and column, and was confirmed against the 200 dpi render. There were no unverified cells.

Table 2's activity/EMF rows in printed order: 1268 K, -344.5 mV, 6.745×10^-10; 1272, -356.1, 8.762×10^-10; 1300, -346.4, 1.028×10^-9; 1307, -339.5, 9.898×10^-10; 1325, -342.7, 1.286×10^-9; 1341, -336.5, 1.385×10^-9; 1348, -338.5, 1.548×10^-9; 1373, -335.5, 1.931×10^-9; 1375, -331.2, 1.834×10^-9.

Table 3's 8 printed temperatures yield 12 pairs: 1296 K (-121.9, 1.76×10^-11; -123.4, 1.81×10^-11); 1302 (-118.5, 1.81×10^-11); 1325 (-110.8, 2.21×10^-11; -112.3, 2.27×10^-11); 1333 (-108.4, 2.38×10^-11); 1348 (-103.2, 2.69×10^-11; -106.7, 2.86×10^-11); 1354 (-100.6, 2.80×10^-11); 1365 (-97.4, 3.10×10^-11; -98.4, 3.14×10^-11); 1370 (-95.2, 3.19×10^-11).

The nine Table 1 float-glass components are bound to every Table 3 activity point through `composition_mol`, with each printed m/o value divided by 100 and no renormalization. The printed vector sums to 1.00000. All nine original m/o values remain in Table 1 context and on the activity rows. The measured liquid activities remain stored. Useful figures (not digitized): Fig. 1 cell schematic, Fig. 2 activity versus temperature comparison, Fig. 3 activity versus Na2O content.

Activity is printed as a positive dimensionless activity, not log activity. The authors calculate it from measured EMF using the printed Nernst relation; the Na2O–WO3 reference melt activity relation is attributed to Lin and Elliott [15]. The working/reference electrode gases are pure O2 at 1 atm, explicitly stated for both electrodes. The reported table measurements are in-situ liquid melt values. No quench condition is stated.

The extract stores 21 activities as `measured_reduced`, each linked to its printed EMF input. The EMF quantity has no quantity token and remains typed-refused and unavailable in migration. The isolated migration produced 42 observations, 0 hard issues, and 21 unavailable EMF inputs. All nine printed glass components reach the migrated activity identity. The focused test pins all 21 activity values and their page/table locators, checks migrated temperature, pressure, fO2 and composition, calls the single-file validator, and includes a deliberate mutation that turns the render pins red. The validator currently reports 21 errors because `electromotive_force` is not an allowed observation type. The extract schema has no honest existing observation type for those measured EMF input rows; the validator's closed type set and SCHEMA.md's instruction not to invent a type leave this pending an owner ruling. No schema or migrator change has been made.

The repo-wide `tests/test_literature_extracts.py` run completed with 638 passed and 3 failures: `test_repo_extracts_validate_green`, `test_coverage_pending_stubs_not_found`, and `test_pilot_extract_count_exact_and_merge_smoke` each hit the configured 300-second timeout while parsing/validating the full corpus. The failures occur in the corpus-wide YAML parse/coverage work; no extract-specific failure was reported. The YAML flow-scalar guard passed after the final data changes.

These measurements contribute held-out Na2O activity data for the broader CMAS question (t-098), particularly the multicomponent float-glass series. They do not directly resolve a CMAS model question because this paper reports one float-glass composition.
