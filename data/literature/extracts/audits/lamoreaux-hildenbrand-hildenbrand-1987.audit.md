# Lamoreaux, Hildenbrand, and Brewer (1987), Part II audit

This addendum extends the existing extract, preserving its six prior rows. It covers the shared source tables for BeO, MgO, CaO, B2O3, Al2O3, and SiO2. Numeric table values are evaluated compilation data (`quoted_attributed`) with the printed Table 4 reference(s) on each observation. The source's standard-state pressure is 1 atm; it is not stored as experimental total pressure.

## Table coverage and cell audit

| Printed table/page | Printed data-row count | Selected records | Cell verification |
|---|---:|---:|---|
| Table 1, p. 421 (PDF p. 4) | 19 | 7 transition records for BeO, MgO, CaO, B2O3, Al2O3, SiO2(qu), and SiO2(cr) | Render check: rows 2, 3, 4, 7, 8, 13, and 14, columns 2–5. All stored temperatures, uncertainties, fusion enthalpies, and uncertainties match the p. 421 render and pdftotext; the CaO asymmetric interval is `(-50,+300)`. The tool draft classified the table body correctly; selected row and column assignments were checked against the render. |
| Table 2, pp. 422–423 (PDF pp. 5–6) | 176 printed rows in three MinerU blocks; 40 distinct coefficient row labels selected across the six target systems | Six system-level typed refusals; no coefficients stored | The redo OCR variants were compared against renders of PDF pp. 5–6. The table prints Gibbs energy-function polynomials, not log-pressure equations. There is no schema quantity token for the printed coefficient form. System-level refusals retain the species labels and locators. The render settles a p. 422 MgO(s) coefficient disagreement (hybrid-high reads 37.705 for 10^6C; render and pipeline-ocr read 31.705); the coefficient is not stored. Page/column layout was checked against each render. |
| Table 3, pp. 424–425 (PDF pp. 7–8) | 123 printed rows across two MinerU blocks; 30 target rows stored | 30 typed refusals; all retain Table 4 attribution | Every stored numeric cell is verified from the p. 424 render (method b). Table 3 combines enthalpy of formation, entropy, and enthalpy increment, for which the schema has no single quantity token; the printed values remain attached to typed-refused rows. A flagged disagreement: Be5O5(g), S°298/R is `(38.67)` in the render and hybrid-high, while pdftotext reads `(38.87)`; `(38.67)` is stored. The Table 3 numeric row labels and cell groups are listed in the cell audit below. |
| Table 4, pp. 425–426 (PDF pp. 8–9) | 122 source-reference rows across two blocks | Used for row attribution; no measurements extracted | The printed reference key and the Table 2/3 property columns are used to attach source citations per selected thermodynamic row. |

Table 1 source assignments are: BeO Tfus ref. 1 (Glushko et al., 1981); MgO refs. 1 and 19 (Glushko et al., 1981; Hlavac, 1982); CaO ref. 1; B2O3 ref. 1; Al2O3 refs. 1 and 19; SiO2(quartz) refs. 19 and 2 (Hlavac, 1982; Stull et al., 1971); SiO2(cristobalite) refs. 16 and 2 (Glushko et al., 1979a; Stull et al., 1971). The Tfus source column applies to the fusion data in Table 1.

The committed compare output contains seven unmatched Table 1 cells for `values.T_m_K`. The compare tool does not bind this field name to a table measurement column, so it reports no matched cell (`matched_cell=null`); these are not numeric disagreements. Each temperature was checked against its printed row and the p. 421 render, and the emitter mapping identifies its MinerU row/column.

The Table 1 CaO interval is asymmetric, and the schema has no token for that uncertainty form. Its full printed form and central value are retained, with `status` and `admission_status` set to `typed_refusal`; no symmetric uncertainty was substituted. This is a schema gap, not a source ambiguity.

Table 3 selected-cell audit (PDF p. 7 / published p. 424; all entries are in the left or right printed table column, and the listed numeric fields plus their adjacent uncertainty cells were checked against the render):

| Printed row label | Extract observation | Columns checked | Verification |
|---|---|---|---|
| BeO(s) | `LH87_BeO_s_table3` | ΔfH°298/R, S°298/R, (H°298−H°0)/R | (b) render; pdftotext and both OCR variants agree |
| BeO(g) | `LH87_BeO_g_table3` | same | (b) render-transcribed |
| Be2O(g) | `LH87_Be2O_g_table3` | same | (b) render-transcribed |
| Be2O2(g) | `LH87_Be2O2_g_table3` | same | (b) render-transcribed |
| Be3O3(g) | `LH87_Be3O3_g_table3` | same | (b) render-transcribed |
| Be4O4(g) | `LH87_Be4O4_g_table3` | same | (b) render-transcribed |
| Be5O5(g) | `LH87_Be5O5_g_table3` | same | (b) render; hybrid-high agrees for S°298/R; pdftotext differs |
| Be6O6(g) | `LH87_Be6O6_g_table3` | same | (b) render-transcribed |
| MgO(s) | `LH87_MgO_s_table3` | same | (b) render-transcribed |
| MgO(g) | `LH87_MgO_g_table3` | same | (b) render-transcribed |
| CaO(s) | `LH87_CaO_s_table3` | same | (b) render-transcribed |
| CaO(g) | `LH87_CaO_g_table3` | same | (b) render-transcribed |
| B2O3(s) | `LH87_B2O3_s_table3` | same | (b) render-transcribed |
| B2O3(g) | `LH87_B2O3_g_table3` | same | (b) render-transcribed |
| BO(g) | `LH87_BO_g_table3` | same | (b) render-transcribed |
| BO2(g) | `LH87_BO2_g_table3` | same | (b) render-transcribed |
| B2O(g) | `LH87_B2O_g_table3` | same | (b) render-transcribed |
| B2O2(g) | `LH87_B2O2_g_table3` | same | (b) render-transcribed |
| Al2O3(s) | `LH87_Al2O3_s_table3` | same | (b) render-transcribed |
| AlO(g) | `LH87_AlO_g_table3` | same | (b) render-transcribed |
| AlO2(g) | `LH87_AlO2_g_table3` | same | (b) render-transcribed |
| Al2(g) | `LH87_Al2_g_table3` | same | (b) render-transcribed |
| Al2O(g) | `LH87_Al2O_g_table3` | same | (b) render-transcribed |
| Al2O2(g) | `LH87_Al2O2_g_table3` | same | (b) render-transcribed |
| SiO(g) | `LH87_SiO_g_table3` | same | (b) render-transcribed |
| SiO2(qu) | `LH87_SiO2_quartz_table3` | same | (b) render-transcribed |
| SiO2(g) | `LH87_SiO2_g_table3` | same | (b) render-transcribed |
| Si2(g) | `LH87_Si2_g_table3` | same | (b) render-transcribed |
| Si2O2(g) | `LH87_Si2O2_g_table3` | same | (b) render confirms the minus sign missing from one OCR variant; Table 4 footnote b points to the p. 426 text, where the p. 435 Si-O discussion cites Khanna et al. (1981), Anderson and Ogden (1969), Porter et al. (1955), and Zmbov et al. (1973); retained as quoted_attributed, typed refusal only because no schema quantity token represents the combined Table 3 values |
| Si3(g) | `LH87_Si3_g_table3` | same | (b) render-transcribed |

The Table 2 system-level refusals cover printed species labels BeO(s/l/g), Be2O(g), Be2O2(g), Be3O3(g), Be4O4(g), Be5O5(g), Be6O6(g); MgO(s/l/g), Mg2(g); CaO(s/l/g), Ca2(g); B2O3(s/l/g), BO(g), BO2(g), B2(g), B2O(g), B2O2(g); AlO(g), AlO2(g), Al2(g), Al2O(g), Al2O2(g), Al2O3(s/l); and SiO(g), SiO2(qu./cr./l./g), Si2(g), Si2O2(g), Si3(g). Coefficient numbers remain unrecorded because of the schema gap; the rendered table confirms the row/column assignments and relevant coefficient spellings.

## Qualitative vapor species and conditions

The six system summaries (Be-O p. 426; Mg-O p. 426; Ca-O p. 428; B-O p. 431; Al-O pp. 431–432; Si-O p. 435) are recorded as `gas_speciation` typed refusals. They preserve the printed species, qualitative condition labels, reaction wording where given, and locators. The schema has no admitted representation for qualitative cross-regime vapor composition or unbalanced reaction prose.

No total pressure, single temperature, or numeric fO2 is assigned to these prose summaries. The numeric oxygen conditions are attached to calculated figures: 0.2 bar O2, congruent vaporization, and 10^-15 bar O2 or the metal-metal-oxide equilibrium pressure when higher. For B2O3, the reducing figure uses 10^-15 bar O2 up to 1923 K and the B-B2O3 equilibrium above that; for Al2O3 it uses 10^-15 bar O2 below 2230 K and the Al-Al2O3 equilibrium above. These are figure conditions only and are not promoted to thermodynamic table rows. Figure curves and their partial-pressure values are not digitized; they remain figure-read candidates.

## Schema gaps and exclusions

- Table 2 gives Gibbs energy-function polynomial coefficients with segment temperature ranges. The existing schema has no quantity token for these printed coefficients; six target-system aggregates are typed refusals and retain the selected species labels. Coefficients are not log-pressure equations and no coefficient values are stored.
- The source provides no printed log-p equation table for the selected systems. Its partial-pressure curves are figures and were not transcribed.
- Table 1 has no experimental total-pressure condition. The printed 1 atm statement is the standard-state pressure, not an experiment pressure.
- Out of scope in Part II: SrO, BaO, Ga2O, GeO2, SnO/SnO2, PbO, ZnO, CdO, HgO, In2O3, and thallium oxides. Existing extract observations for SrO, BaO, and Ga2O remain unchanged.
