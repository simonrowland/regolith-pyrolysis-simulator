# Cho and Suito (1994), Table 5 audit

## Source and alignment

Table 5 is printed on PDF page 4 (journal page 180) and contains eight slag labels and 33 numeric activity cells. MinerU's table HTML retains all 12 table rows and six columns, but its draft labels all body rows as headers. The pdftotext page text contains the digits but interleaves narrow columns. Therefore every numeric cell below was checked against the page render. The render confirms the physical row and column assignment.

Column alignment was checked as follows:

| Printed column | Header | Row assignment confirmed in render |
| --- | --- | --- |
| 1 | Slag | ACS2, ACS4, CAS5, CAS8, CAS9, CS, AC, CA |
| 2 | log aSiO2 from C(N)/LN (CS2-/LS also included for CS) | ACS2, ACS4, CAS5, CAS8, CAS9, CS |
| 3 | log aAl2O3 from C(N)/LN (CS2-/LS parentheticals shown separately) | ACS2, ACS4, CAS5, CAS8, CAS9, AC, AC alternative, CA, CA alternative |
| 4 | log aCaO from C(N)/LN | ACS2, ACS4, CAS5, CAS8, CAS9, CS, AC, CA |
| 5 | log aAl2O3 from intercepts in Figs. 5 and 6 | ACS2, ACS4, CAS5, CAS8, CAS9 |
| 6 | log aCaO from intercepts in Figs. 5 and 6 | ACS2, ACS4, CAS5, CAS8, CAS9 |

Every numeric value and uncertainty was checked in its printed row and column in the render (audit method b). The two parenthesized alumina estimates are separate values in column 3, not misplaced entries in column 4. Table 5's footnote marks the CS silica value as including results from CS2- and LS.

## Cell audit and reconciliation

`log a ± SD` below preserves the Table 5 spelling. All entries are on page 4, journal page 180. The five activity columns are ordered SiO2 capacity/distribution, Al2O3 capacity/distribution, CaO capacity/distribution, Al2O3 intercept, CaO intercept.

| Table 5 row | Column 2 | Column 3 | Column 4 | Column 5 | Column 6 | Render check / reconciliation |
| --- | --- | --- | --- | --- | --- | --- |
| ACS2 | −0.97 ± 0.10 | −0.63 ± 0.16 | −1.68 ± 0.13 | −0.63 ± 0.11 | −1.69 ± 0.14 | All five existing cells unchanged; render confirms assignment. |
| ACS4 | −1.99 ± 0.23 | −0.95 ± 0.40 | −1.43 ± 0.13 | −0.99 ± 0.11 | −1.44 ± 0.14 | All five existing cells unchanged; render confirms assignment. |
| CAS5 | −3.39 ± 0.15 | −1.87 ± 0.41 | −0.11 ± 0.33 | −1.92 ± 0.12 | −0.067 ± 0.35 | All five existing cells unchanged; render confirms assignment. |
| CAS8 | −4.51 ± 0.22 | −3.35 ± 0.66 | −0.93 ± 0.83 | −3.18 ± 0.17 | −0.82 ± 0.76 | All five existing cells unchanged; render confirms assignment. |
| CAS9 | −2.34 ± 0.07 | −0.62 ± 0.13 | −1.00 ± 0.49 | −0.76 ± 0.30 | −1.37 ± 0.02 | Five numeric cells added; render confirms row assignment. |
| CS | −1.20 ± 0.17* | — | −1.60 ± 0.40 | — | — | Two numeric cells added; render confirms row assignment and footnote marker. |
| AC | — | −0.57 ± 0.23; (−0.90 ± 0.23) | −1.08 ± 0.13 | — | — | Three numeric cells added; render confirms both Al2O3 values occupy column 3. |
| CA | — | −2.82 ± 0.41; (−2.62 ± 0.40) | −0.15 ± 0.44 | — | — | Three numeric cells added; render confirms both Al2O3 values occupy column 3. |

No existing activity, printed-log-activity, or standard-deviation cell changed. The 13 additions are the cells shown for CAS9, CS, AC, and CA. No previous CAS9 composition or CAS9 Table 2 input IDs existed in the extract, so there is no CAS9 composition correction to remove.

The draft compare output is saved as `cho-suito-1994-cas-slag-activities-table5-compare.csv`. It reports no match for the 33 stored `values.activity` points because Table 5 prints log10 activities while the extract stores the converted activity for the existing consumer. The MinerU draft also classifies every Table 5 body row as a header, so compare finds no body candidates; its result is not treated as cell verification. The per-cell render audit and the focused test's direct printed-log pins resolve the cells independently.

## Composition and standard state

The Table 2 analyses support the existing visibly derived mean compositions for ACS2, ACS4, CAS5, and CAS8. Their run IDs, averaging method, and maximum half-range notes were preserved. Table 2 has no CAS9 run, and no full composition for CAS9, CS, AC, or CA is printed in the tables, text, or captions. Figs. 7–9 show labelled positions but no numeric compositions; those locations were not digitized. Those activity rows retain their values and carry the ruled typed unknown composition with reason `Table 5 label has no printed or analysed composition`, located to Table 5.

The caption says the estimates are relative to the solid standard state. The species-specific records retain the printed oxide formula in the standard-state description (`pure solid SiO2(s)`, `pure solid Al2O3(s)`, or `pure solid CaO(s)`). The paper does not identify the SiO2 or Al2O3 polymorph.

## Migration

The isolated migration produced 48 observations total, including all 33 Table 5 activities, with zero hard issues. All 33 Table 5 rows migrated as point values at 1873 K, with phase `l`. The 13 explicitly unknown compositions reached identity composition with the exact ruled reason and retained a located composition point condition. The remaining 20 rows retained their derived composition identities. Table 5 contributes 13 additional observations; no store regeneration was committed.
