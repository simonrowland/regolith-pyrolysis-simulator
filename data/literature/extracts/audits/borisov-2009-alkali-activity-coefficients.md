# Borisov (2009) alkali activity audit

## Extraction decision

Tables 4 and 5 report relative activity coefficients, normalized to a virtual melt with
`XSiO2 = 0.45` in Table 4 or the CSA6 melt in Table 5. They do not print absolute
`a(Na2O)` or `a(NaO0.5)`. Following the Asai 1982 precedent and controller ruling, all
19 printed rows are preserved as `activity_coefficient` observations with the printed
relative gamma and composition. The absolute activity is typed-refused on every row;
no reference-melt activity or conversion is invented. The schema gap is the lack of a
relative-to-reference activity form (t-1563).

The authors calculated gamma from their experimental data using Eq. (1), with the
crucible-supported loop technique for alkali-vapor saturation and electron-microprobe
composition measurements. Table 4 has 15 printed rows and Table 5 has 4; all 19 rows,
including the four normalization references (Standard 36, 39, 37, and CSA6), are retained
as printed.

The abstract prints total pressure as 1 atm (render-checked on PDF page 1). Table 2 supplies
temperatures and positive `−log fO2` values for each experimental series; these are retained
as printed without sign or pressure conversion. The abstract and methods identify the CO/CO2
gas mix at the iron–wüstite buffer. No Table 4–5 gamma uncertainty is printed. The experiment
rows preserve the exact table compositions, series conditions, reference convention, and
method locators.

## Cell audit

| Printed table | Rows | Stored numeric cells per row | Cell evidence |
|---|---:|---|---|
| 4, PDF p. 6 / printed p. 584 | 15 | XSiO2, XNa2O, XK2O, XRb2O, gamma_Na2O | MinerU and pdftotext agree on all retained digits; row and column assignment checked against render. |
| 5, PDF p. 8 / printed p. 586 | 4 | XAl2O3, XNa2O, XK2O, XRb2O, gamma_Na2O | MinerU and pdftotext agree on all retained digits; row and column assignment checked against render. |

The QA pre-screen reported numeric agreement 1.000 for both target tables. No flagged cell
or render disagreement occurred. The visual page checks confirmed table numbers, row
labels, column assignments, and the reference rows. Figures 4–7 are figure-read candidates
and were not digitized. Table 6 contains fit coefficients and derivatives, not measured
absolute activities, so it was excluded. The draft compare reports zero comparable points
because the numeric gamma is retained in a typed-refusal field rather than as a migrated
activity value; its locator audit reports no errors. Numeric agreement was checked directly
between the two machine reads and the page layouts visually.

## Isolated migration

The real migrator emits 19 unavailable activity values, 0 hard issues, and 19 rejected
admissions. Temperature, composition, and total pressure reach every identity. The printed
positive `−log fO2` remains in each row's located `point_conditions`; identity `fO2_Pa`
remains unknown on typed-refused activity rows, per the controller ruling. The log-form
condition has no identity mapping for typed-refused activity rows (schema gap t-1563); no
conversion to Pa is made.

Expected store delta without regeneration: +19 activity observations, 0 admitted absolute
activity values. No battery store was regenerated.
