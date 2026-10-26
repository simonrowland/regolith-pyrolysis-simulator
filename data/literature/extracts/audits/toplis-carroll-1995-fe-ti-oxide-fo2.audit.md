# Toplis–Carroll (1995) extraction audit

Source id: `toplis-carroll-1995-fe-ti-oxide-fo2`.

## Duplicate check

No duplicate source id, DOI, first-author/year, or title match exists in the extract tree at the seat base or across refs. The source-id path has no history in `git log --all`; searches for the title words and Toplis/Carroll 1995 found citations to other works but no duplicate extraction.

## Source reads and table-tool audit

The PDF has no text layer. MinerU and the supplied Tesseract `eng`, 300 dpi, `psm 6` page-layout OCR were the two machine reads. The Tesseract page layout did not preserve usable row and column assignments for the rotated Table 2 pages, so the complete numeric-cell audit uses rendered pages as method (b). The source was read from machine text first; renders settled cell assignment and values.

`tools/mineru_table_draft.py convert` drafted the MinerU tables. The `mineru_table_qa` scan pre-screen flagged PDF pages 8, 13, and 30; scan mode does not provide a numeric agreement score. Page 8 and page 13 were re-read with controller-provided OCR variants. Page 8 uses hybrid-high and pipeline-ocr; page 13 uses the orientation-corrected Tesseract `psm 6` read and rotated-page MinerU hybrid-high output. `*.cell-audit.csv` records the page, row, column, stored cell, verification method, and OCR source where a redo was used. Every stored numeric cell is render-verified.

The emitted Table 2 draft provided 288 glass component cells. The Fe-42 Liq row on page 9 and Fe-23 Liq row on page 11 were absent from MinerU and were transcribed from their renders (8 components each), giving 304 glass components. The row/column assignment on every Table 2 page was checked against the render. Table 2 spans PDF pages 7–15.

The emitter compare command cannot compare this extract's `values.component_amount` field directly: its generic projection reads `values.value` and returned no points. The committed `*.compare.csv` is the row/column/phase-aware comparison using a temporary, non-mutating projection from `component_amount` to the tool's `value` field. It reports 296 matched cells, 0 mismatches, and 0 missing cells among the comparable draft cells. All eight manually restored Fe-42 Liq cells are included in that comparison; Fe-23's eight manually restored Liq components on page 11 are the only glass cells outside it. The projection and generated migration/store data were not committed.

The audit CSV contains 3,854 cell records: Table 1, 252; Table 2, 3,379; Table 3, 156; Table 4, 60; prose conditions and estimates, 7. Table 1 pages 5–6, Table 2 pages 7–15, Table 3 pages 16–17, Table 4 page 30, and the cited prose pages were checked against renders.

## Row inventory and extract decisions

| Printed table | Printed data rows | Stored evidence |
|---|---:|---|
| Table 1, PDF pages 5–6 | 42 run rows, plus 4 FMQ group labels | 42 experiments with temperature, absolute log fO2, delta-FMQ, D1/ramp/D2 schedule, and run products |
| Table 2, PDF pages 7–15 | 177 run-product analyses (38 Liq + 139 crystals), plus 3 starting-glass analyses and section labels | 304 glass oxide observations; 139 crystal analyses retained as typed refusals with all eight oxide values; 3 starting-glass context records |
| Table 3, PDF pages 16–17 | 38 calculated run rows, plus 4 FMQ group labels | 38 quoted-attributed typed-refusal context records retain printed phase proportions, Na loss, and residual statistic; 4 group labels are retained without an evidence class |
| Table 4, PDF page 30 | 12 fitted regions, plus 2 section labels | 12 quoted-attributed typed-refusal context records retain the printed phase proportions |

The Table 2 page-8 render has 19 analyses: Fe-45 (3), Fe-36 (3), Fe-37 (4), Fe-35 (4), and Fe-39 (5). The rotated page-13 render has 21 analyses: Fe-70 (6), Fe-16 (3), Fe-15 (3), Fe-56 (4), and Fe-62 (5). Table 2's full run-product count is 177: 38 glass plus 139 crystal rows. Three non-run starting compositions are separately retained.

Table 1 has 42 experimental rows, including Fe-20, Fe-18, Fe-48*, and Fe-50†, which have no Table 2 analyses. Their printed conditions remain experiment records; no glass observations were fabricated. The delta values retain their printed FMQ buffer basis, and the absolute `log fO2` values remain in log form.

Every glass observation keeps the printed Table 1 temperature in `point_conditions.T_C`, the Table 1 absolute `fO2_log` and FMQ offset, and a source locator on each located condition. The per-run starting composition and Table 1 assemblage are also attached. The paper states atmospheric pressure and describes a 1-atm gas-mixing furnace in the Experimental Approach and Methods section on PDF page 3, so total pressure is 101325 Pa with that locator. It does not publish a run-specific CO/CO2 ratio; the sweep-gas ratio is unknown and no gas ratio or ambient/vacuum condition was inferred.

Table 2 labels are retained verbatim (`Liq`, including the source's `Lig` spelling where printed); those analyses are quenched glass and use phase role `glass`, with phase label `melt`. The electron-microprobe analyses use ZAF correction. The prose on PDF page 4 says samples were drop-quenched into water but does not state that glass composition was preserved; every glass row records quench state `not_stated` at page 4. Crystal analyses remain typed refusals because no crystal PhaseRole exists. Parenthetical Table 2 uncertainties are one standard deviation in the least units cited; the source does not state a confidence level. Mean and uncertainty are stored separately while the printed cell spelling is retained.

Three starting-glass analyses (SC1, SC4, SC4a) are stored as measured-direct context rows and linked to each run's initial composition. Table 3 reports weighted least-squares phase proportions and sodium-loss estimates based on the run's Table 2 phase analyses, errors, and starting-glass composition, using the method of Albarède & Provost (1977). The extract retains the input IDs in each derivation note, but crystal analyses are typed refusals and starting-glass analyses are context records; neither migrates as an observation. The 38 outputs therefore use `quoted_attributed`, with no `derived_from` lineage. Table 3 shortens the Table 1/Table 2 run label Fe-126*R to Fe-126*; it maps to that single matching run. The four FMQ section labels are not calculations and carry no evidence class.

On PDF page 22, the paper prints ferric-oxide estimates for Fe-79, Fe-41, and Fe-95 clinopyroxene calculated assuming ideal pyroxene stoichiometry. Their inputs are `toplis95_fe_79_crystal_cpx_p7_r13`, `toplis95_fe_41_crystal_cpx_p7_r17`, and `toplis95_fe_95_crystal_cpx_p7_r21`; each crystal analysis is a typed refusal and does not migrate as an observation. The three estimates are therefore `quoted_attributed` to Toplis & Carroll (1995), with no `derived_from` lineage. They are not printed Fe3+/Fe2+ or FeO/Fe2O3 ratios and remain typed refusals because no matching schema quantity exists.

Table 4's printed fitted proportions are retained as typed refusals because the phase-mode quantity has no schema token. The paper states that the regional gradients are regressions of modal-abundance data shown in Figs. 11–14 for paths parallel to FMQ, within regions defined in Fig. 20. The text does not identify per-region run IDs, and these plotted modal-abundance values are not migrated observations; the regional fits therefore use `quoted_attributed`, with those figure data named as unresolved inputs. Region IV is extrapolated from adjoining regions (Table 4 footnote e), with no regional regression inputs; its method record cites that footnote. The crystallizing-proportion rows are calculated from the corresponding Table 4 gradients, which are typed-refusal context records rather than migrated observations; those rows also use `quoted_attributed`. Figure-only values were not extracted; figures are listed as figure-read candidates for a later task.

The page-30 render labels the two fitted regions **IIIa** and **IIIb** in both sections, **IV** with region footnotes **d,e**, and **V** with region footnote **d**. The extract keeps the `c` marker on the `res` olivine cells for region IIIb. Footnote d qualifies the assumed linear magnetite trend; footnote e marks the extrapolation from adjoining regions and is cited by the Region IV method record. The IV/V markers remain attached to their region records.

## Render discrepancies and cell notes

- Table 2, PDF page 8, Fe-45 Liq TiO2: the rendered cell is `3·25(1.7)`. Hybrid-high reads `3·25(1,7)` while pipeline-ocr reads the decimal point; the render resolves it as mean 3.25 wt% with standard deviation 0.017 wt%. This is marked `render_verified` in the mapping.
- Table 2, PDF page 13 is printed upside down. The orientation-corrected Tesseract and rotated MinerU hybrid align the rows. For Fe-56 Cpx TiO2, Tesseract reads `4·39(18)`; hybrid-high and the render read `1·39(18)`. The stored mean is 1.39 wt%.
- Table 2, PDF pages 9 and 11: MinerU omitted the Fe-42 Liq and Fe-23 Liq rows, respectively. Their eight oxide cells and associated row statistics were transcribed from the page renders; the cell audit identifies these manual rows.
- Table 4, PDF page 30: the rendered row labels are IIIa, IIIb, IV, and V. The IV region carries footnotes d,e; the V region carries d. The rendered footer keeps footnote c on the `res` olivine cells in region IIIb.
- Table 1, Table 3, Table 4, and prose locators in the extract are printed table/page locators, not MinerU block indices. The focused test pins rendered cells on Table 2 pages 8, 11, and 13, and pins Table 3 rows on pages 16 and 17.

No schema or migrator changes were made. No figure-derived numbers were added. The expected store delta is 42 experiments and 304 admitted glass observations; the 139 crystal analyses and the 60 context rows remain evidence/refusal records. No battery-store regeneration is committed.
