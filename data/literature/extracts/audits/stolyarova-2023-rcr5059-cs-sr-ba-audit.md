# Stolyarova et al. (2023), RCR5059 — extraction audit

DOI: [10.57634/RCR5059](https://doi.org/10.57634/RCR5059). The existing
Stolyarova et al. (2023) extract is a different paper (DOI 10.1002/rcm.9459).
No extract with this DOI, title, or source ID was found at the seat base or in
the available refs. The direct cited-source searches found no existing extract
for refs. 55, 70, 75, or 78.

## Scope and row counts

| Printed table | PDF page | Printed rows | Scope decision |
|---|---:|---:|---|
| 1 | 7 | 6 | Exclude: chemical potentials and Gibbs energies, not the requested activity or vapour quantities. |
| 2 | 8 | 11 | Include: quoted CsBO2 melt activities, printed compositions, and T=1020 K. |
| 3 | 10 | 6 | Include as typed-refusal candidates: qualitative partial-pressure trends (`>`, `≈`, `<`), with no numerical pressure points. |
| 4 | 11 | 36 | Include as typed-refusal candidates: fitted log-pressure equations, T intervals, and cited sources; no pressure point is printed. |
| 5–12 | 11–17 | as printed | Exclude: formation/atomization/reaction thermochemistry, not the requested rails. Table 11 is barium phosphate data. |
| 13 | 18 | 6 | Context only: oxide-glass compositions and species identified at 1270 K; no species quantities or partial pressures. |
| 14 | 20 | 13 | Exclude duplicate: the system, T, and species rows match Table 1 in the existing `kems-017-stolyarova-2013` extract. |

The narrative also discusses Cs2SeO4 (a selenate, §3.8) and barium
phosphates (Table 11 / §5.4); neither is an oxide-silicate melt datum. Table 13
is a multicomponent borosilicate glass, so it is not excluded as nuclear-only;
its qualitative species list is retained as located context, not as a measured
species-presence observation.

Table 2 is the only table here with numeric activity points in the requested
rail. The cited 2006 source and the cited Asano–Kou papers were not present as
primary-source assets in the corpus search. Table 2's text says the activities
were obtained by the monomer/dimer ratio method and refers to the approach in
ref. 50; the review does not state an apparatus pressure-calibration method or
reference substance for those ref. 55 measurements. The review says most
results in §4.1 followed ref. 69's procedure and describes gold as that
procedure's partial-vapour-pressure standard. This scoped calibration context
is attached to the ref. 70 SrO–B2O3 rows (Table 3 and Table 4 rows 1–18) on
their shared experiment apparatus. It is not transferred to the ref. 75 or 78
BaO–B2O3 rows; their calibration method and reference substance remain
unknown.

## Machine-text and page-render audit

The seat table reader converted all 14 MinerU tables from the supplied content
list. QA found 14 tables, four flagged tables, no dropped-plus/minus hits, and
five invented `<details>` chart blocks (ignored). Numeric agreement with
pdftotext was 100% for every table except MinerU block p011-t07, where two
numeric cells differed. The page 11 render resolves those cells as the printed
SrSiO2 and SrSiO3 values `-474`, `1583`, `-841`, and `2199`; this block is
outside the requested rails.

Rendered pages 7, 8, 10, 11, 12, 13, 16, 17, 18, and 20 to check table
row/column assignment. Page 8 confirms the 11 Table 2 rows, seven estimated
phase-composition dash cells, and the printed activity `8 × 10^-3` in row 1.
The printed row 10 composition is Cs2O 50,
B2O3 10, SiO2 10 mol.% (sum 70 mol.%); the high-resolution page render
confirms the SiO2 cell is `10`, so no remainder is inferred. Page 11 confirms
Table 4's 36 rows and the coefficient/range columns. Page 18 confirms the 6
Table 13 rows. Page 20
confirms the 13 Table 14 rows and their overlap with the already-held 2013
primary extract. No table values were read from figures.

| Page | Printed table / row | Cell set | Check |
|---:|---|---|---|
| 8 | Table 2, rows 1–11 | input/estimated composition, activity, fixed T | Numeric cells agree (a), render confirms row/column placement; seven estimated-composition dashes checked in the render (b). |
| 8 | Table 2, row 10 | SiO2 composition cell | (b) composition totals 70 mol.%; high-resolution render confirms the printed cell is `10`. |
| 10 | Table 3, rows 1–6 | gas species and trend signs | (b) page render confirms all six rows and three condensed-phase columns. |
| 11 | Table 4, rows 1–36 | T intervals, A and B values/uncertainties, ref numbers | (a) numeric reads agree; page render confirms group and column placement. |
| 11 | Table 5, rows 1–5 | enthalpy cells | (a) numeric reads agree; page render confirms layout; excluded from requested rails. |
| 11 | Table 6, rows 1–3 | enthalpy cells | (a) numeric reads agree; page render confirms layout; excluded from requested rails. |
| 11 | MinerU block 7, rows 1–2 | SrSiO2 / SrSiO3 values | (b) QA mismatch resolved against page render; excluded from requested rails. |
| 12 | Tables 7–8, rows 1–4 each | thermochemistry cells | (a) numeric reads agree; page render confirms layout; excluded from requested rails. |
| 13 | Tables 9–10, rows 1–6 and 1–2 | thermochemistry cells | (a) numeric reads agree; page render confirms layout; excluded from requested rails. |
| 16 | Table 11, rows 1–2 | barium phosphate thermochemistry | (a) numeric reads agree; page render confirms layout; excluded. |
| 17 | Table 12, rows 1–2 | thermochemistry cells | (a) numeric reads agree; page render confirms layout; excluded from requested rails. |
| 18 | Table 13, rows 1–6 | oxide wt.% cells and fixed T | (a) numeric reads agree; page render confirms layout. |
| 20 | Table 14, rows 1–13 | fixed/interval T values | (b) page render confirms row and column placement; excluded as duplicate. |

## Table 2 row/cell audit

All numeric cells below agree between MinerU and pdftotext (a); the page 8
render confirms row and column assignment. Seven estimated-composition cells
are printed dashes, not numeric sevens: Cs2SiO3 in rows 1–4, B2O3 in rows 5–6,
and SiO2 in row 6; these are render-checked (b). The row 10 SiO2 cell was also
checked at high resolution (b). Printed row count: 11.

| Row | Input Cs2O/B2O3/SiO2 mol.% | Estimated CsBO2/B2O3/SiO2/Cs2SiO3 mol.% | Activity as printed | Check |
|---:|---|---|---:|---|
| 1 | 6/56/38 | 12/50/38/— | 8 × 10^-3 | b on dash; a otherwise |
| 2 | 10/54/36 | 20/44/36/— | 0.01 | b on dash; a otherwise |
| 3 | 20/48/32 | 40/28/32/— | 0.06 | b on dash; a otherwise |
| 4 | 33.3/40/26.7 | 66.7/6.6/26.7/— | 0.29 | b on dash; a otherwise |
| 5 | 40/36/24 | 75/—/20.8/4.2 | 0.78 | b on dash; a otherwise |
| 6 | 50/30/20 | 75/—/—/25 | 0.84 | b on dashes; a otherwise |
| 7 | 50/50/− | 100/−/−/− | 1.0 | a |
| 8 | 50/40/10 | 88.9/−/−/11.1 | 0.88 | a |
| 9 | 50/20/30 | 57.1/−/−/42.9 | 0.81 | a |
| 10 | 50/10/10 | 33.3/−/−/66.7 | 0.67 | b on SiO2; a otherwise |
| 11 | 50/−/50 | −/−/−/100 | 0 | a |

The controller ruled that the existing reader has no mol.% basis. Therefore
each row retains both printed mol.% maps without division or normalization;
identity composition is typed unknown with reason “printed mol.% composition;
reader lacks a mole-percent basis (t-1524)”. Row 10 records the additional
reason “printed 50/10/10 sums to 70%, remainder not stated”. All 11 activity
rows remain admitted. T=1020 K reaches each identity and point condition. The
review does not state total experiment pressure, fO2/pO2, or the activity
reference state. Pressure and fO2 remain unknown; the migrator reports the
reference state unavailable rather than assuming one.

## Table 3 and Table 4 audit

Table 3 has six printed qualitative rows (Sr, SrBO2, BO, BO2, B2O2, B2O3),
each with three trend-symbol cells. Page 10 render confirms all row and column
assignments (b); the symbols mean increase, approximate independence, and
decrease as defined in the table footnotes. The extract preserves all six as
typed-refused gas-phase partial-pressure rows because no numeric pressure
points are printed.

Table 4 has 36 printed rows. Each row preserves the printed species, condensed
phase, temperature range, A×10^-3 ± width, B ± width, and reference. MinerU and
pdftotext agree on every numeric cell (a); page 11 render confirms row and
column assignment once for the table. Printed widths have no stated confidence
level. The equations are retained as typed refusals in the gas phase because
the schema has no temperature-dependent log-pressure equation token.
References 70, 75, and 78 are carried per row. Ref. 70 rows 1–18 share the
§4.1 ref. 69 procedure context, including the gold standard, on their migrated
experiment apparatus. Refs. 75 and 78 have unknown calibration method and
reference substance; the ref. 69 statement is not generalized to them. No
total pressure or fO2 is inferred.

Table 13 has six glass rows attributed to V.L. Stolyarova (2013), ECS
Transactions 46, 55 (ref. 111). Page 18 render confirms its row and column layout;
numeric oxide-composition cells agree between MinerU and pdftotext (a). The six
rows are stored in the extract's located context container with their printed
wt.% compositions, 1270 K condition, identified vapour species, primary-source
attribution, and note that no quantities are printed. No scored observation is
created because the table reports no species quantities or partial pressures.
The review does not state apparatus calibration for ref. 111.

## Migration and store delta

The committed table-draft compare report matched all 11 Table 2 activity
values to their page 8 cells, with no mismatch or unmatched stored value.

The isolated real migrator emitted 53 observations: 11 admitted Table 2
activities and 42 rejected typed-refusal rows from Tables 3–4. It reported zero
hard registry issues. The 53 unavailable entries are expected: the 11
activities lack a printed reference state, and the 42 refused rows have no
numeric pressure point. Four experiment records lift calibration onto the
apparatus: Table 2/ref. 55, and Table 4 refs. 75 and 78 remain unknown; the
shared SrO–B2O3 ref. 70 experiment has the qualified ref. 69 method and gold
standard. The six Table 13 rows lift as one context record and do not create
observations. Identity axes were inspected for Table 2 rows 1, 4, and 10:
T=1020 K; composition is unknown for the ruled mol.% basis; pressure and fO2
are not values because the review does not print them. Expected battery store
delta is zero; no store regeneration is committed.


## Table 3 row-level trend cells

| Row | Species | Three printed trend cells | Page/row check |
|---:|---|---|---|
| 3 | Sr | >^{\mathrm{a}} / \approx^{\mathrm{b}} / \approx | p. 10, (b) render |
| 4 | SrBO2 | <^{\mathrm{c}} / < / < | p. 10, (b) render |
| 5 | BO | < / < / < | p. 10, (b) render |
| 6 | BO2 | < / < / < | p. 10, (b) render |
| 7 | B2O2 | < / < / < | p. 10, (b) render |
| 8 | B2O3 | < / < / < | p. 10, (b) render |

## Table 4 row-level numeric cells

Cited sources named by the review: ref. 70, T. Kou and M. Asano, High Temperature Science 24, 1 (1987); ref. 75, M. Asano and T. Kou, High Temperature Science 29, 171 (1990); ref. 78, M. Asano and T. Kou, Journal of Chemical Thermodynamics 21, 837 (1989). The qualified ref. 69/gold calibration context is recorded on the ref. 70 experiment only. Refs. 75 and 78 have unknown calibration method and reference substance.

| Row | Condensed phase | Species | T range K | A×10^-3 ± width | B ± width | Ref. | Check |
|---:|---|---|---|---|---|---:|---|
| 1 | 2 SrO · B2O3 | Sr | 1452-1572 | 23.93 ± 1.00 | 10.00 ± 0.66 | 70 | p. 11, (a) |
| 2 | 2 SrO · B2O3 | SrBO2 | 1452-1572 | 28.18 ± 1.45 | 13.74 ± 0.96 | 70 | p. 11, (a) |
| 3 | 2 SrO · B2O3 | BO | 1473-1572 | 22.34 ± 0.68 | 9.79 ± 0.45 | 70 | p. 11, (a) |
| 4 | 2 SrO · B2O3 | BO2 | 1473-1572 | 29.95 ± 0.86 | 11.33 ± 0.57 | 70 | p. 11, (a) |
| 5 | 2 SrO · B2O3 | B2O2 | 1473-1572 | 23.03 ± 1.03 | 11.05 ± 0.68 | 70 | p. 11, (a) |
| 6 | 2 SrO · B2O3 | B2O3 | 1453-1572 | 23.85 ± 0.73 | 10.88 ± 0.48 | 70 | p. 11, (a) |
| 7 | SrO · B2O3 | Sr | 1373-1442 | 32.30 ± 1.48 | 15.32 ± 1.05 | 70 | p. 11, (a) |
| 8 | SrO · B2O3 | SrBO2 | 1310-1442 | 30.80 ± 0.53 | 16.06 ± 0.38 | 70 | p. 11, (a) |
| 9 | SrO · B2O3 | BO | 1310-1442 | 21.21 ± 0.35 | 10.10 ± 0.26 | 70 | p. 11, (a) |
| 10 | SrO · B2O3 | BO2 | 1310-1442 | 32.78 ± 0.38 | 14.36 ± 0.27 | 70 | p. 11, (a) |
| 11 | SrO · B2O3 | B2O2 | 1310-1442 | 19.34 ± 0.49 | 9.85 ± 0.35 | 70 | p. 11, (a) |
| 12 | SrO · B2O3 | B2O3 | 1310-1442 | 25.31 ± 0.28 | 13.91 ± 0.20 | 70 | p. 11, (a) |
| 13 | SrO · 2 B2O3 | Sr | 1304-1440 | 25.60 ± 0.58 | 10.59 ± 0.42 | 70 | p. 11, (a) |
| 14 | SrO · 2 B2O3 | SrBO2 | 1304-1440 | 24.92 ± 0.28 | 12.46 ± 0.20 | 70 | p. 11, (a) |
| 15 | SrO · 2 B2O3 | BO | 1281-1440 | 20.75 ± 0.27 | 9.79 ± 0.20 | 70 | p. 11, (a) |
| 16 | SrO · 2 B2O3 | BO2 | 1281-1440 | 28.97 ± 0.16 | 12.24 ± 0.12 | 70 | p. 11, (a) |
| 17 | SrO · 2 B2O3 | B2O2 | 1281-1440 | 19.83 ± 0.19 | 11.02 ± 0.14 | 70 | p. 11, (a) |
| 18 | SrO · 2 B2O3 | B2O3 | 1281-1440 | 22.00 ± 0.42 | 12.82 ± 0.31 | 70 | p. 11, (a) |
| 19 | 3 BaO · B2O3 | Ba | 1434-1630 | 21.41 ± 0.86 | 7.40 ± 0.56 | 75 | p. 11, (a) |
| 20 | 3 BaO · B2O3 | BaBO2 | 1434-1630 | 23.04 ± 1.46 | 9.10 ± 0.30 | 75 | p. 11, (a) |
| 21 | 3 BaO · B2O3 | BO | 1434-1630 | 11.41 ± 0.33 | 2.88 ± 0.22 | 75 | p. 11, (a) |
| 22 | 3 BaO · B2O3 | BO2 | 1434-1630 | 27.12 ± 0.33 | 8.79 ± 0.22 | 75 | p. 11, (a) |
| 23 | 3 BaO · B2O3 | B2O2 | 1434-1630 | 11.57 ± 0.31 | 3.95 ± 0.20 | 75 | p. 11, (a) |
| 24 | 3 BaO · B2O3 | B2O3 | 1434-1630 | 10.95 ± 0.27 | 1.92 ± 0.18 | 75 | p. 11, (a) |
| 25 | BaO · B2O3 | Ba | 1333-1535 | 22.48 ± 0.34 | 8.40 ± 0.24 | 78 | p. 11, (a) |
| 26 | BaO · B2O3 | BaBO2 | 1333-1535 | 23.26 ± 0.87 | 11.73 ± 0.60 | 78 | p. 11, (a) |
| 27 | BaO · B2O3 | BO | 1389-1535 | 17.83 ± 1.34 | 7.38 ± 0.91 | 78 | p. 11, (a) |
| 28 | BaO · B2O3 | BO2 | 1389-1535 | 29.69 ± 1.07 | 12.43 ± 0.73 | 78 | p. 11, (a) |
| 29 | BaO · B2O3 | B2O2 | 1389-1535 | 16.95 ± 1.17 | 7.75 ± 0.80 | 78 | p. 11, (a) |
| 30 | BaO · B2O3 | B2O3 | 1389-1535 | 21.64 ± 1.04 | 11.21 ± 0.72 | 78 | p. 11, (a) |
| 31 | BaO · 2 B2O3 | Ba | 1277-1431 | 25.29 ± 0.49 | 10.14 ± 0.37 | 78 | p. 11, (a) |
| 32 | BaO · 2 B2O3 | BaBO2 | 1277-1431 | 25.65 ± 0.22 | 13.24 ± 0.17 | 78 | p. 11, (a) |
| 33 | BaO · 2 B2O3 | BO | 1277-1431 | 17.84 ± 0.71 | 8.08 ± 0.53 | 78 | p. 11, (a) |
| 34 | BaO · 2 B2O3 | BO2 | 1277-1431 | 32.20 ± 0.78 | 14.67 ± 0.58 | 78 | p. 11, (a) |
| 35 | BaO · 2 B2O3 | B2O2 | 1277-1431 | 15.42 ± 0.39 | 9.39 ± 0.29 | 78 | p. 11, (a) |
| 36 | BaO · 2 B2O3 | B2O3 | 1277-1431 | 22.07 ± 0.68 | 12.66 ± 0.51 | 78 | p. 11, (a) |

## Table 13 row-level audit data

Page 18 layout is render-checked (b); numeric composition cells agree between MinerU and pdftotext (a). These six glass rows are located context only: their wt.% compositions and identified-vapour-species lists have no quantitative species values or partial pressures. The table cites V.L. Stolyarova (2013), ECS Transactions 46, 55 (ref. 111); this review does not state apparatus calibration for that source.

| Glass | SiO2 | Al2O3 | B2O3 | Na2O | CaO | Li2O | Cs2O | Fe2O3 | Others | Vapour species at 1270 K | Check |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| 1 | 45.5 | 4.9 | 14.0 | 9.9 | 4.0 | 2.0 | 1.4 | 2.9 | 15.4 | $CsBO_2$ ,  $NaBO_2$ ,  $LiBO_2$ | (a) |
| 2 | 45.3 | 4.9 | 14.0 | 9.8 | 4.0 | 2.0 | 1.4 | 2.9 | 15.7 | $CsBO_2$ ,  $NaBO_2$ ,  $LiBO_2$ | (a) |
| 3 | 46.6 | 5.0 | 14.2 | 10.0 | 3.0 | 3.0 | 0.8 | 2.0 | 15.4 | $CsBO_2$ ,  $NaBO_2$ ,  $LiBO_2$ | (a) |
| 4 | 46.2 | 4.2 | 13.4 | 9.1 | 2.5 | 3.4 | - | 5.2 | 16.0 | $NaBO_2$ ,  $LiBO_2$ ,  $KBO_2$ | (a) |
| 5 | 40.7 | 1.7 | 15.1 | 12.5 | 15.6 | - | 2.1 | 1.7 | 10.6 | $CsBO_2$ ,  $NaBO_2$ ,  $KBO_2$ | (a) |
| 6 | 43.0 | 2.8 | 20.5 | 16.4 | 11.6 | - | 1.8 | 3.9 | - | $CsBO_2$ ,  $NaBO_2$ , Na | (a) |
