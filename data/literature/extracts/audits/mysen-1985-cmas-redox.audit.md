# Mysen et al. 1985 redox extraction audit

Status: complete. The full 88-row Table 2 dataset is transcribed,
render-audited, and migrated. Numeric compositions remain unknown because the
paper supplies recipes only as a plot that is out of scope.

## Source coverage

- Duplicate check: no extract for this paper, DOI, or title appears at the seat
  base or in extract history. The Mysen hits found are citations to other works
  and a separate 1988 paper.
- MinerU contains three printed tables. Table 1 is a comparison of wet-chemical
  and Mössbauer results attributed to prior work; Table 2 is the experiment-run
  dataset; Table 3 reports standard free energies of reduction, not run-wise
  redox measurements.
- Table 2 is printed on PDF page 6 (journal page 322). It contains 88
  experiment rows; a printed MgO-system heading separates the 53 CAS rows from
  the 35 MAS rows and is not an experiment. The page render confirms the table's
  row and column assignments. Its columns are composition label, temperature
  (°C), −log fO2, hyperfine parameters, Fe3+/ΣFe, and Fe2+/Fe3+.
- The Mössbauer discussion (PDF page 3, journal page 319) states that relative
  Mössbauer doublet areas give Fe3+/ΣFe and defines
  Fe2+/Fe3+ = (1 − Fe3+/ΣFe)/(Fe3+/ΣFe). Fe3+/ΣFe is stored as
  `measured_direct`; the authors' Fe2+/Fe3+ column is stored as
  `measured_reduced`, with lineage to the paired Fe3+/ΣFe observation. The
  printed ratios are retained exactly. Seven pairs disagree with the printed
  relation beyond table rounding. Four more have different central values but
  overlapping table-rounding intervals. Each discrepancy is noted without
  replacing the printed value:

  | CSV row | Composition | Fe3+/ΣFe | Printed Fe2+/Fe3+ | Relation-implied Fe2+/Fe3+ | Comparison |
  |---:|---|---:|---:|---:|---|
  | 4 | CASIF5 | 0.556 | 0.678 | 0.799 | Beyond table rounding |
  | 31 | CASXIIF5 | 0.650 | 0.530 | 0.53846… | Beyond table rounding |
  | 37 | CASIXF5 | 0.155 | 5.472 | 5.452 | Central values differ; intervals overlap |
  | 46 | CASV_AF5 | 0.685 | 0.439 | 0.460 | Beyond table rounding |
  | 55 | CASIVF5 | 0.044 | 21.83 | 21.727 | Central values differ; intervals overlap |
  | 60 | MASIIIF5 | 0.350 | 1.850 | 1.85714… | Beyond table rounding |
  | 61 | MASIVF5 | 0.333 | 1.859 | 2.003 | Beyond table rounding |
  | 62 | MASIVF10 | 0.490 | 1.026 | 1.041 | Beyond table rounding |
  | 64 | MASVIIIF5 | 0.328 | 1.959 | 2.049 | Beyond table rounding |
  | 65 | MASIXF5 | 0.180 | 4.571 | 4.556 | Central values differ; intervals overlap |
  | 79 | MASIVF5 | 0.168 | 4.970 | 4.952 | Central values differ; intervals overlap |
- The experimental methods (PDF page 2, journal page 318) state that samples
  were quenched in water at a rate
  exceeding 500 °C/s. It does not state whether iron valence is preserved or
  altered by quenching; the measured phase is glass and quench_state is
  `not_stated`.
- The abstract states that these data were obtained at 1 atm pressure. The
  experimental methods (PDF page 2, journal page 318) state that oxygen
  fugacity was controlled with CO-CO2 gas mixtures, monitored by a Y2O3-doped
  ZrO2 sensor, and calibrated against Ni-NiO and iron-wüstite buffers. The
  reducing fO2 range is stated in atmospheres on PDF page 4 (journal page 320).
  Table 2 prints absolute −log fO2; each stored `fO2_log` carries the existing
  unprinted `reference_pressure` convention of 1 atm, located to that printed
  range, so the migrator converts identity values with 101325 Pa/atm.
- Table 2 identifies each run with a printed composition code (for example,
  CASIF5 or MASIXF5). The methods define F5/F10 as 5/10 wt.% Fe2O3 and Roman
  numerals as iron-free end-member composition labels. Full oxide proportions
  are shown only in Figure 2; Table 3 supplies NBO/T and Al/(Al + Si) for only
  a subset of the labeled compositions. Figure-derived values are out of scope.

## Cell verification

| Table | Printed rows | Cell assignment | Numeric agreement | Render check |
|---|---:|---|---|---|
| 2 | 88 experiment rows | MinerU Table 2 draft; pdftotext is an OCR layer and has poor cell agreement (QA 0.742 across 820 numeric cells) | All 339 stored numeric cells in columns 2, 3, 10, and 11 for CSV rows 4-56 and 58-92 were checked against the 300 dpi page 6 render; see `mysen-1985-cmas-redox.cells.csv`. | Render confirms each row/column assignment and all stored digits. Thirteen Fe2+/Fe3+ cells printed as `--` remain missing. |

The table tool conversion and QA include all three printed tables. The extract
targets Table 2 only; Table 1 is prior-work comparison data and Table 3 is a
standard free-energy table, not run-wise redox data. The draft identifiers
p004-t01, p006-t02, and p009-t03 are not printed locators. The printed count is
88 experiments; the MgO heading is structural context and is excluded from the
count. Table 2 prints 88 temperatures, 88 −log fO2 values, and 88 Fe3+/ΣFe
fractions. It prints 75 numeric Fe2+/Fe3+ ratios and 13 `--` entries, which
remain absent rather than being encoded as zero.

The primary-source comparison used MinerU plus pdftotext, then the page 6
render for the stored cells because the PDF text layer is OCR and digit
agreement was only 0.742. The render also checked the layout and row/column
assignment once for the table. Renders of PDF pages 1, 2, 3, 4, and 6 confirm
the abstract's 1 atm statement, the quench and CO-CO2 control/sensor/buffer
statements, the Mössbauer equation, the reducing fO2 range in atm, and the
Table 2 cells. Those statements are located respectively on pages 1, 2, 3,
and 4; the table cells are on page 6.
The CSV coordinates are MinerU draft rows; the extract locators use printed
Table 2 and absolute PDF page 6. These 13 composition labels were corrected
against the page 6 render and are reflected in the cell audit:

| CSV row | OCR label | Printed label |
|---:|---|---|
| 20 | CASVIF5 | CASIVF5 |
| 21 | CASVIF5 | CASVF5 |
| 25 | CASVIF5 | CASVIIF5 |
| 31 | CASXIF5 | CASXIIF5 |
| 43 | CASIIF5 | CASIIIF5 |
| 48 | CASVIF10 | CASVF10 |
| 60 | MASIIF5 | MASIIIF5 |
| 63 | MASVIF5 | MASVIIF5 |
| 68 | MASXIIIF5 | MASXIIF5 |
| 70 | MASIIF5 | MASIIIF5 |
| 72 | MASVIF5 | MASVIIF5 |
| 77 | MASXIF5 | MASXIIF5 |
| 81 | MASIXIF5 | MASIXF5 |

The Table 2 footnote marks underlined entries as 77 K Mössbauer spectra.
Every observation for underlined CSV rows 26, 40, 51, 81, 84, and 88 carries
the separate 77 K measurement annotation; the melt temperatures remain those
printed in the temperature column.

The table tool's row/column/phase-aware comparison matched all 163 stored
values against the MinerU draft (0 mismatches); see
`mysen-1985-cmas-redox.compare.csv`. This exposed a compare helper gap: it
rejected `glass` rows before checking unqualified value columns. The helper now
accepts glass alongside the existing phase tokens, with a regression test.

## Composition binding ruling

The controller ruled to retain all rows with their printed composition codes,
bind only numeric compositions explicitly defined by the article, and leave
other identity compositions typed unknown with reason `composition given only
as a code; numeric recipe only in Figure 2 (not digitized)`. Figure 2 will not
be digitized and the schema will not change. Table 3 provides NBO/T and
Al/(Al + Si) descriptors for a subset, but no numeric oxide recipe; these
descriptors will not be converted into estimated component compositions.
Accordingly, 0 of 88 rows bind numeric identity composition and 88 remain
unknown. The 36 distinct printed labels (counting the daggered form
separately) are listed below in first-appearance order: CASIF5, CASIIF5,
CASIIIF5, CASIVF5, CASVF5, CASV_AF5, CASV_B, CASV_CF5, CASVIF5, CASIF10,
CASIIF10, CASIIIF10, CASVF10, CASV_BF5, CASVIIF5, CASVIIIF5†, CASVIIIF5,
CASIXF5, CASXF5, CASXIF5, CASXIIF5, CASXIIIF5, CASIVF10, CASVIF10, MASIF5,
MASIIF5, MASIIIF5, MASIVF5, MASIVF10, MASVIIF5, MASVIIIF5, MASIXF5,
MASXF5, MASXIF5, MASXIIF5, and MASXIIIF5.

## Migrator and store impact

The isolated real migrator admitted 163 observations: 88 measured-direct
Fe3+/ΣFe fractions and 75 measured-reduced Fe2+/Fe3+ ratios. It reported 0
hard issues and 0 unavailable values. The 163 non-hard validation issues are
the expected identity-incomplete notices for numeric composition: the article
prints only the composition codes, with numeric recipes confined to Figure 2.
For each observation, the migrator receives the printed T_C, absolute log fO2,
printed composition code, total pressure, and quench state. Identity
temperature_K, fO2_Pa, and total_pressure_Pa are populated; identity
composition is the ruled unknown. The expected battery-store delta is +163
observations. No store regeneration was committed.

| Observation | Identity composition | Identity total_pressure_Pa | Identity fO2_Pa | Identity temperature_K |
|---|---|---:|---:|---:|
| CASIF5, CSV row 4 | unknown (composition code only) | 101325 | 21169.79304587855506273603333 | 1898.15 |
| CASIXF5, CSV row 37 | unknown (composition code only) | 101325 | 0.101325 | 1823.15 |
| MASIVF5, CSV row 61 | unknown (composition code only) | 101325 | 21169.79304587855506273603333 | 1923.15 |

Table 2 is the figure-independent dataset in scope. Figure 2 recipe points are
not digitized and remain a figure-read candidate only.
