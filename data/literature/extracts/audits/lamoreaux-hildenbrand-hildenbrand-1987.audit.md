# Lamoreaux, Hildenbrand, and Brewer (1987), Part II audit

This addendum extends the existing extract, preserving its six prior rows. It covers the shared source tables for BeO, MgO, CaO, B2O3, Al2O3, and SiO2. Numeric table values are evaluated compilation data (`quoted_attributed`) with the printed Table 4 reference(s) on each observation. The source's standard-state pressure is 1 atm; it is not stored as experimental total pressure.

## Table coverage and cell audit

| Printed table/page | Printed data-row count | Selected records | Cell verification |
|---|---:|---:|---|
| Table 1, p. 421 (PDF p. 4) | 19 | 7 transition records for BeO, MgO, CaO, B2O3, Al2O3, SiO2(qu), and SiO2(cr) | Render check: rows 2, 3, 4, 7, 8, 13, and 14, columns 2–5. All stored temperatures, uncertainties, fusion enthalpies, and uncertainties match the p. 421 render and pdftotext; the CaO asymmetric interval is `(-50,+300)`. The tool draft classified the table body correctly; selected row and column assignments were checked against the render. |
| Table 2, pp. 422–423 (PDF pp. 5–6) | 176 provisional rows across the three MinerU table blocks | Pending OCR redo | The QA screen reports 0.396 digit agreement on p. 422 (146 run-together flags among 446 numeric cells) and 0.771 on p. 423. Multiple selected coefficient rows would need render transcription. OCR variants are required before storing or auditing coefficients. |
| Table 3, pp. 424–425 (PDF pp. 7–8) | 123 provisional rows across two MinerU table blocks | Pending OCR redo | The QA screen reports 0.965 and 0.860 digit agreement respectively, with fused-reference/decimal flags. OCR variants are required before storing or auditing selected values. |
| Table 4, pp. 425–426 (PDF pp. 8–9) | 122 source-reference rows across two blocks | Used for row attribution; no measurements extracted | The printed reference key and the Table 2/3 property columns are used to attach source citations per selected thermodynamic row. |

Table 1 source assignments are: BeO Tfus ref. 1 (Glushko et al., 1981); MgO refs. 1 and 19 (Glushko et al., 1981; Hlavac, 1982); CaO ref. 1; B2O3 ref. 1; Al2O3 refs. 1 and 19; SiO2(quartz) refs. 19 and 2 (Hlavac, 1982; Stull et al., 1971); SiO2(cristobalite) refs. 16 and 2 (Glushko et al., 1979a; Stull et al., 1971). The Tfus source column applies to the fusion data in Table 1.

The committed compare output contains seven unmatched Table 1 cells for `values.T_m_K`. The compare tool does not bind this field name to a table measurement column, so it reports no matched cell (`matched_cell=null`); these are not numeric disagreements. Each temperature was checked against its printed row and the p. 421 render, and the emitter mapping identifies its MinerU row/column.

The Table 1 CaO interval is asymmetric, and the schema has no token for that uncertainty form. Its full printed form and central value are retained, with `status` and `admission_status` set to `typed_refusal`; no symmetric uncertainty was substituted. This is a schema gap, not a source ambiguity.

## Qualitative vapor species and conditions

The six system summaries (Be-O p. 422; Mg-O p. 422; Ca-O p. 428; B-O p. 431; Al-O p. 432; Si-O p. 435) are recorded as `gas_speciation` typed refusals. They preserve the printed species, qualitative condition labels, reaction wording where given, and locators. The schema has no admitted representation for qualitative cross-regime vapor composition or unbalanced reaction prose.

No total pressure, single temperature, or numeric fO2 is assigned to these prose summaries. The numeric oxygen conditions are attached to calculated figures: 0.2 bar O2, congruent vaporization, and 10^-15 bar O2 or the metal-metal-oxide equilibrium pressure when higher. For B2O3, the reducing figure uses 10^-15 bar O2 up to 1923 K and the B-B2O3 equilibrium above that; for Al2O3 it uses 10^-15 bar O2 below 2230 K and the Al-Al2O3 equilibrium above. These are figure conditions only and are not promoted to thermodynamic table rows. Figure curves and their partial-pressure values are not digitized; they remain figure-read candidates.

## Schema gaps and exclusions

- Table 2 gives Gibbs energy-function polynomial coefficients with segment temperature ranges. The existing schema has no quantity token for these printed coefficients; selected rows will be preserved as typed refusals after OCR verification. No coefficients have been added while the OCR redo is pending.
- The source provides no printed log-p equation table for the selected systems. Its partial-pressure curves are figures and were not transcribed.
- Table 1 has no experimental total-pressure condition. The printed 1 atm statement is the standard-state pressure, not an experiment pressure.
- Out of scope in Part II: SrO, BaO, Ga2O, GeO2, SnO/SnO2, PbO, ZnO, CdO, HgO, In2O3, and thallium oxides. Existing extract observations for SrO, BaO, and Ga2O remain unchanged.
