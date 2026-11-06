# Shaiu (1976) Fe-bearing oxide extraction audit

## Scope and source method

The extract covers the thesis's printed Fe-bearing thermodynamic results for
Na2FeO2, NaFeO2, and Na4FeO3 (also printed as (Na2O)2·FeO). The thesis does not
report Na3FeO3 or Na8Fe2O7 in its Fe thermodynamic tables. No figure-only emf
points were digitized; Figures 7 and 9 are figure-read candidates deferred by
the task rule. The printed Table 7 Cell VII result is explicitly described as
erratic and unreliable and is retained as an exclusion note rather than a
numeric measurement.

Machine text was the transcription basis: MinerU content-list tables and
markdown, checked with the born-digital pdftotext layout. MinerU table QA
identified low numeric agreement for Tables 7–11. Accordingly, every stored
numeric cell below was confirmed against a page render (audit method b), and
each table's row/column assignment was checked against the render once. No
local filesystem paths are embedded in the extract.

## Numeric cell audit

| Printed table / PDF page | Fe-bearing cells stored | Printed count | Audit |
|---|---|---:|---|
| Table 7 / 46 | Cell V fit: A=1.5993, s(A)=0.0736, B×10^5=−20.42, s(B)×10^5=8.38, C×10^3=4.0, range 843–911 K; Cell VI fit: A=1.3559, s(A)=0.0184, B×10^5=−7.55, s(B)×10^5=1.95, C×10^3=6.4, range 795–1048 K | 2 Fe cells among 7 table rows | Page 46 render confirms the values, signs, exponents, and row/column assignments. The source defines C as RMS deviation; the other ± values are standard deviations of intercept and slope. Cell VII is not numeric and is labelled erratic/unreliable. |
| Table 8 / 47 | Na2FeO2: −A=183060, B=51.34, C=3000; NaFeO2: −A=173410, B=43.66, C=1200; Na4FeO3: −A=289180, B=82.69, no C | 3 Fe rows among 8 body rows | Page 47 render confirms all values and formula assignments. Printed equation is ΔG°=(A+BT)±C in cal/mol; the first column is −A. Formula basis is retained as printed. Table 8 footnote c marks the Na4FeO3 value estimated. |
| Table 9 / 50 | (Na2O)2·FeO critical oxygen concentration: 1.81×10^3, 3.53×10^3, 5.71×10^3, 8.56×10^3 ppm at 773, 873, 973, 1073 K | 4 target cells among 6 temperatures | Page 50 render confirms row/column assignment and all eight stored numeric cells. Table 9 footnote states these values are calculated from Table 8 assuming liquid sodium in equilibrium with pure metal and metal activity unity. |
| Table 10 / 55 | Na2FeO2 at 973 K: ΔH column 183.1, ΔS 51.34, ΔG column 133.1 (this work); 182.2, 48.2, 135.3 (ref. 6). (Na2O)2·FeO at 973 K: 289.2, 82.69, 208.7 (this work); 276.4, 80.6, 198.0 (ref. 6). | 4 Fe comparison rows, 12 numeric cells | Page 55 render confirms the 3 numeric thermodynamic columns in all 4 rows and their column alignment. ΔH and ΔG headings are printed with a leading minus sign in the unit; values are retained as printed in context without applying a sign conversion. This-work rows are `measured_reduced`; ref. [6] rows are `quoted_attributed`. |
| Table 11 / 57 | Na2FeO2 and Na4FeO3 formation reactions and sodium reduction reactions, at 298 and 873 K | 13 Fe-related values | Page 57 render confirms all 13 row assignments, signs, temperatures, reaction labels, methods, and reference labels. This-work reductions are `measured_reduced`; rows attributed to [10], [18], [35], and [65] are `quoted_attributed`. The 298 K calorimetric reaction value −25.07 is separately expanded at Equations 61–63. |
| Table A4 / 76 | Cell V: 843/1.4248, 858/1.4220, 870/1.4246, 889/1.4249, 902/1.4149, 911/1.4080; Cell VI: 20 temperature/emf pairs in the extract | 26 measured pairs | Page 76 render confirms all 26 pairs and each cell heading. In particular Cell VI rows 948 K = 1.2743 V and 970 K = 1.2754 V are taken from the render where OCR/pdftotext differed. |

## Formula basis and evidence class

Table 8 is a standard formation relation for the compound formulas from the
general reaction printed in its caption. The extracted Na2FeO2 and NaFeO2
relations come from the authors' own emf measurements and are
`measured_reduced`; `derived_from` connects Na2FeO2 to Cell V and NaFeO2 to
Cell VI. The NaFeO2
polymorph is not stated. Na4FeO3 is marked estimated by the authors and its
text traces the estimate to Gross and Wilson solution calorimetry for
2Na2O + FeO → Na4FeO3. Equations 61–63 preserve that reaction basis and the
printed uncertainty. Table 9 values are the authors' calculations from Table
8, not measurements; their calculation assumptions are retained. Table 10
and Table 11 comparison values retain each printed method and attribution.

Table 7 fit coefficients and Table 8 coefficient-only functions cannot be
represented as migrated numeric points. The Table 7 emf fit rows therefore
retain descriptive quantity labels and a typed schema refusal. Table 9's
critical dissolved-oxygen concentration likewise has no exact quantity token;
it is not mapped to generic concentration. Equation 62 is a reaction
enthalpy, not a standard enthalpy of formation: `Quantity.DELTA_FH` and the
`delta_fH` alias in `simulator/battery/enums.py::Quantity.DELTA_FH` and
`simulator/battery/migrate.py::QUANTITY_ALIASES` are a near-miss, so the
printed reaction enthalpy is retained as a typed refusal. The six source rows
in these schema-gap groups are the complete expected quantity-unknown set
after migration. This follows the closed
`tools/validate_literature_extracts.py::OBSERVATION_TYPES`,
`simulator/battery/migrate.py::QUANTITY_ALIASES` and
`simulator/battery/migrate.py::TYPE_QUANTITY`, and
`simulator/battery/enums.py::Quantity` vocabularies. The `concentration_series`
observation type exists, but no quantity in `Quantity` means critical oxygen
concentration in liquid sodium, and no alias supplies that exact meaning.
Phase `cr` is the existing condensed-solid phase token. No additional
pressure, oxygen fugacity, or composition condition is printed for these
measurements and none is assigned.

## Migration and expected store effect

The focused isolated-migration test expects nine source observations, which
expand to 33 migrated observations because the 26 Table A4 emf pairs migrate
point by point. There are zero hard issues, zero registry issues, and 33
unavailable numeric values. The exact quantity-unknown set is the two Table 7
emf fits, two Table A4 measured emf series, the Table 9 critical oxygen
concentration, and the Equation 62 reaction enthalpy. Temperature intervals
are not turned into midpoint values.
The Table 10 comparisons and Table 11 reaction comparison cells remain
explicitly located context. The MinerU compare reports 26 `no-match` cells
because the Appendix A4 page has no extracted numeric table block; the page
render was used for each pair. It reports 3 `unverifiable` entries because
the Table A4 caption is on an earlier page and is not in MinerU's caption
index; the render confirms these locators and values. There are no digit
mismatches or locator errors. No generated
extract store was committed; expected store delta is 0 observations and 0
numeric values.
