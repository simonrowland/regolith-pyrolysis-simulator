# Wood & Wade (2013) trace activity extraction audit

## Scope and source checks

The extract covers the three prose activity-coefficient calculations, Table 1, the model-derived Table 1 FeO activity coefficient, and digitized points from every labelled contour in Figures 1–4. The source paper is `wood-wade-2013-trace-activities.pdf`; PDF text was decoded with `pdftotext -layout`, and the figure panels were checked against rendered pages. The private OpenIMCC transcription was used as a page-cited starting aid, with every retained value checked against the source PDF. No MinerU run was used.

The Table 1 audit has 16 numeric cells: 14 measured phase-composition assay cells, the reported silicate total of 99.0 wt%, and calculated gamma FeO = 0.92. The 14 assay cells are separate measured observations. The total and calculated gamma remain distinct from measured assay values. The full printed cell audit remains in `wood-wade-2013-trace-activities.table1-cell-audit.csv`; the table has 21 printed row slots including headings.

## Observation inventory

| Observable | Rows | Evidence and source |
| --- | ---: | --- |
| Melt activity coefficient, PbO / CuO0.5 / InO1.5 | 3 | Author-derived values printed in prose, BW1102 partitioning data, pages 918–919 |
| Melt activity coefficient, FeO | 1 | Calculated from Eq. (4), Table 1 row 9, page 918; model-derived |
| Equilibrium phase composition | 14 | Measured silicate and metal assay cells, Table 1 rows 3–7, 11–14, 16, 18–21, page 918 |
| Digitized activity-coefficient contour points | 307 | Model-derived, 19 labelled branches: NiO 68; FeO 66; MoO2 77; WO3 96, Figures 1–4, pages 913, 915–917 |
| **Total observations** | **325** | |

The 19 contour branches contain at least six points each. Polylines were resampled at no more than 0.05 normalized ternary-composition arc length per interval, including endpoints and visible axis intersections. Each point records Diopside, Anorthite, and Forsterite mole fractions, the labelled gamma value, figure and branch locator, and its digitization method. Negative raster-rounding slivers at simplex edges were projected to the edge and the ternary fractions renormalized; the largest correction was below 0.005 mole fraction and within the stated ±0.02 per-component read uncertainty.

The affine axis calibration maps each printed endmember vertex to its corresponding unit ternary coordinate. Raster anchors (Diopside, Anorthite, Forsterite) are (60,500), (330,30), (600,500) px for Figures 1, 3, and 4, and (60,508), (330,30), (600,508) px for Figure 2. Composition read uncertainty is ±0.02 per endmember fraction. Gamma read uncertainty is half the last displayed contour-label digit; it is recorded on each point. Figure 1 contours were fitted to 172 Ni-saturated literature data; Figure 2 to 83 Fe-saturated literature data; Figure 3 uses Mo-saturated literature data and measured Mo metal–silicate partition coefficients; Figure 4 contours are calculated from fitted metal–silicate partitioning and saturation data. These points are therefore model-derived, not direct measurements. Per-figure fit context and calibration are retained with the extract.

## Conditions, derivations, and absences

All Table 1 observations carry 1,650 °C and 1.5 GPa. The three prose coefficients also retain the 1,923 K reporting temperature and those run conditions. Their point-condition composition uses the analyzed silicate oxide values as mass percent (SiO2 59.4, Al2O3 10.7, FeO 11.3, MgO 10.4, CaO 7.2); the printed total is 99.0. This basis is recorded for the derived rows. No amount is supplied for any omitted component. The figure points carry 1,650 °C and their digitized ternary compositions; the paper does not state a pressure for those model contours, so none is assigned. Table 1 metal assay rows retain the run conditions but have no separate bulk-composition condition.

The PbO, CuO0.5, and InO1.5 activity rows point to the matching silicate and metal assay observations, the calculated FeO activity observation, and the method/constants context. Their model assumptions and pure-liquid standard state are documented in the extract. The FeO gamma row points to the method/constants context. Figure-point lineage points to its own figure-fit context. No table cell is represented as zero when absent.

Table 1 includes no numeric Ag activity coefficient. The printed total and gamma FeO are not additional phase assay cells. The paper cites approximately 4.2 at 1,573 K and 3.2 at 1,923 K from Holzheid and Lodders (2001); these comparator values come from another paper's graph and are not observations of this source. Figure 5 shows volatility curves but tabulates no numeric values, so it remains context only. BW1102 oxygen fugacity is not reported. The starting recipe Diopside28–Forsterite22–Anorthite50 has no stated basis or sum; it is retained as text, not converted into a numeric composition. Table 1 has no metal Ag/In/Pb/Cu oxide activities beyond the assay cells listed above.

## Locator page checks

Physical PDF pages are one-based, PDF indices zero-based, and published page numbers follow the journal folio.

| Source item | Physical page | PDF index | Published page |
| --- | ---: | ---: | ---: |
| Figure 1 NiO contours | 3 | 2 | 913 |
| Figure 2 FeO contours | 5 | 4 | 915 |
| Figure 3 MoO2 contours | 6 | 5 | 916 |
| Figure 4 WO3 contours | 7 | 6 | 917 |
| Eq. (4) model fit provenance | 4 | 3 | 914 |
| Run pressure, temperature, Table 1, and three prose coefficients | 8 | 7 | 918 |
| InO1.5 activity coefficient | 9 | 8 | 919 |

## Isolated migration and validation

The duplicate-key-rejecting YAML load and `tools/validate_literature_extracts.py` both pass. Isolated migration of this extract alone yields 325 numeric observations, zero hard issues, and zero unavailable values. All 325 rows receive 1,650 °C; all 18 Table 1/prose-derived observations receive 1.5 GPa. Composition reaches all 311 model activity-coefficient rows and all nine measured silicate assay rows; five metal assays have no bulk composition condition. The four figure contexts are not attached to the BW1102 experiment because they summarize fitted surfaces from other datasets.

The focused source tests pin the three prose coefficient values, Table 1 assay cells, all 307 digitized rows, measured/model evidence classes, and the isolated migration. The source-value assertion rejects a mutation of PbO gamma from 0.22 to 0.23. The generated store was not regenerated or committed. Expected store delta is one work record, one BW1102 experiment, seven context records, 325 observations, and a source-level generated experiment record for the figure-model observations.
