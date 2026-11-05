# Mysen et al. (1985) extraction audit

## Printed rows and numeric-cell audit

Table 1 contains six comparison samples. Table 2 contains 88 experimental samples: 53 CAS rows and 35 MAS rows. The MinerU
draft reports 89 body rows because draft row 57 is the MgO system heading, not a sample. The printed row and column layout
for each table was checked against the complete PDF page render: page 4 for Table 1 and page 6 for Table 2.

The cell audit has 303 entries: 264 Table 2 temperature, `-log fO2`, and Fe3+/ΣFe cells; six 77 K underline annotations;
24 Table 1 value and uncertainty cells; eight copies of the four MV78 composition components as they are stored on the two
method rows; and the abstract's 1 atm pressure statement mapped to all 88 Table 2 samples. Table 2 numeric cells were checked
against the 600 dpi render. The underlined 77 K sample labels on data rows 23, 37, 48, 77, 80, and 84 were checked at 1200 dpi;
the Table 2 footnote locates all six spectra at 77 K. Table 1 numeric cells and its row layout were checked against the 600 dpi
render.

The Table 1 footnote's composition text is an OCR disagreement: pdftotext reads Fo15.2, while the page render reads Fo16.2.
The extract and cell audit use the rendered value. The footnote states that the bulk composition is by weight.

Two Table 2 labels were corrected from the machine draft using the page 6 render: data row 23 reads `CASVIIF5†` (MinerU:
`CASVIIIF5+`), and data row 77 reads `MASIXF5` (MinerU: `MASIXIF5`). The repeated sample-code fields in the cell audit use the
rendered labels. The focused test pins both corrections.

`mysen-1985-cmas-redox-mineru-compare.csv` reports 100 mismatches. The values are confirmed against the page renders and
the complete row and column layout. The mismatch is caused by
`tools/mineru_table_draft.py::_phase_matches_column`, which rejects the extraction's correct `glass` phase before it checks
table coordinates. The Table 1 compound value±width cells also require render review because the draft reads each compound
cell as one string. The render decisions and machine-read disagreements are recorded per stored cell in
`mysen-1985-cmas-redox.cells.csv`.

## Conditions, phases, and methods

All 88 Table 2 sample codes, temperatures, printed positive `-log fO2` values, and Fe3+/ΣFe fractions are retained per row.
The source's printed positive `-log fO2` number remains in the row text; `point_conditions.fO2_log` carries the corresponding
signed absolute-log coordinate used by the existing identity path. The abstract states that the experiments were at 1 atm,
so this pressure is mapped to those Table 2 rows. Table 1 prints no temperature, oxygen fugacity, or total pressure; the
focused test verifies that no such values reach its migrated identities. Both tables measure quenched glass. Table 2 says
that samples were quenched in water above 500 °C/s, but does not state whether Fe redox is preserved; Table 1 states no
quench-redox information. Both are recorded as `not_stated` with their page locators.

The Table 2 Mössbauer fractions and Table 1 Mössbauer fractions are `measured_reduced`: the text says Fe3+/ΣFe is obtained
from relative areas of fitted ferric and ferrous doublets. The raw spectra and fitted areas are not tabulated; each reduction
has a located `derivation.raw_inputs` declaration. Table 1 wet-chemistry values are `quoted_attributed`, following its
footnote. Figure 2 nominal composition coordinates and Figures 7–8 redox values remain figure-read candidates and are not
digitized.

## Composition schema gap

Table 1's MV78 footnote prints `An46Fo16.2(SiO2)29.7(Fe2O3)8.1` as a bulk composition by weight. The printed shorthand is
preserved on both MV78 method rows and its four numeric components are render-audited. The closed oxide lookup
`simulator/battery/migrate.py::_COMPOSITION_COMPONENTS` contains no `An` or `Fo`; the mass-percent path
`simulator/battery/migrate.py::_mass_percent_components` rejects unsupported component names, and
`simulator/battery/migrate.py::_composition_located_from_plain` emits a typed unknown for that input. The code's exact
amount-basis token is `simulator/battery/enums.py::AmountBasis.MASS_PERCENT`. Existing extracts do not provide a located
identity representation for this normative-mineral weight-basis form. The point condition therefore remains typed unknown
with the Table 1 locator, rather than converting An or Fo into oxide percentages. The printed text and refusal are preserved
on both methods; no migrator or schema code was changed.
