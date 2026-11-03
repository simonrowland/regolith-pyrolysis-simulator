# Wang et al. 2009 Cr-slag KEMS extraction audit

Source: `wang-2009-cr-slag-kems-original`, DOI 10.1002/rcm.4127. Machine inputs were the corpus MinerU content-list tables and Markdown plus `pdftotext-layout.txt`. The source is born-digital; the text layer supplied the transcription, while renders checked table layout and flagged details.

## Table and row reconciliation

| Printed table | PDF page (journal page) | Printed rows | Disposition |
| --- | ---: | ---: | --- |
| 1 | 2 (2234) | 20 | Initial charge comparison; sample 1 Cr2O3 fraction is 0.062. |
| 2 | 3 (2235) | 8 | Materials and purities; not vaporization results. |
| 3 | 4 (2236) | 20 | Five post-FeO-vaporization mole fractions per sample. |
| 4 | 5 (2237) | 20 | CrO and CrO2 pressure-fit coefficients per sample. |
| 5 | 5 (2237) | 20 | Activity-fit coefficients and activities at 1950 K and 2035 K. |

Table 4's MinerU draft marked the first sample row as a header and reported 21 body rows. The page render and `pdftotext-layout.txt` show sample numbers 1–20 only; the extract uses those 20 printed samples. Table 5 has 20 rows, each with two activity cells. Table 2 has eight materials rows; none is an observation in this extraction.

The Table 4 and Table 5 captions assign their results to Table 3 compositions after complete FeO vaporization. Those Table 3 compositions reach every result row's identity and `point_conditions.composition`, each with a Table 3 locator. The result tables also print their own Cr2O3 mole-fraction column; those values are retained verbatim in separate fields. Across all 20 samples, both result-table columns match Table 1's initial-charge Cr2O3 fractions. For sample 1, Table 1 and Tables 4–5 print 0.062 while Table 3 prints 0.066. The paper does not explain the discrepancy; neither value was averaged or recomputed.

## Transcription and cell audit

`tools/mineru_table_draft.py convert` produced the drafts under the page/table keys `p002-t01`, `p003-t02`, `p004-t03`, `p005-t04`, and `p005-t05`. `tools.mineru_table_qa.py` found numeric agreement of 1.000 for every table: Table 1 (140 numeric cells), Table 2 (0), Table 3 (120), Table 4 (122), and Table 5 (120). It reported no dropped ± marks. Table 4 had one LaTeX flag for the printed fit equation; the equation and all coefficient signs were checked on the page render.

The cell audit CSV has 461 source-cell-to-field records. It covers the Table 3 composition cells, Table 4 and Table 5 fit coefficients and printed widths, both activity columns, the two result-table composition columns, and the Table 1 sample 1 comparison cell. Table 3 source cells are reused for each matching sample's identity and composition condition. All independent machine reads agreed on numeric content. Render checks confirmed the row/column assignments and all Table 4 and Table 5 values, including minus signs and ± widths.

The distinct table locators were checked against PDF pages 2, 4, and 5: Table 1 sample 1 uses row 3, column 2; Table 3 sample 1 uses row 3; Table 4 sample 1 uses row 3, columns 2–6; Table 5 sample 1 uses row 2, columns 2–6. Pages 3–4 were rendered to verify the apparatus, temperature measurement, gold calibration, and equations 18–23 locators. PDF page numbers in the extract are absolute page numbers; journal pages are retained separately.

`mineru_table_draft.py compare` matched all 40 stored Table 5 activity points to their row and column cells, with no mismatches or locator errors. The coefficient records are not point pressures or point activities, so they are hand-transcribed from the drafts and independently checked on the render.

## Conditions, apparatus, and evidence classes

The paper reports measurements over 1950–2035 K. Table 5 activity points retain their printed temperatures individually; fit rows retain the printed interval without inventing a midpoint. Table 4 identifies CrO and CrO2 vapor species, stored in phase `g`; Table 5 Cr2O3 activities are stored in the high-temperature liquid phase `l`. No quenched-glass result is reported.

The experimental description identifies an MS-1301 Knudsen-effusion mass spectrometer, two tungsten cells (slag and pure Cr2O3 standard), electron-bombardment heating, an effusion-to-evaporation area ratio of 1:100, an AOP-66 disappearing-filament pyrometer, and a stated relative temperature error of 0.1–0.2%. It gives 25 eV ionization energy and calibrates the installation against gold vapor pressure, including the printed equation in Eqn. (23). No orifice diameter or channel length is printed. The paper does not state a molecular, transitional, or viscous flow class; the experiment's `regime_class` therefore remains unknown while the KEMS method is recorded.

The text describes prevailing high vacuum and quite low oxygen partial pressure qualitatively. It prints no numeric total pressure or fO2 for these runs. Those identity axes remain unknown; no ambient, 1 atm, or other assumed pressure was added. Conditions and temperature locators migrate for every observation.

Table 4 coefficients are author-reported pressure-fit results based on ion-current comparison (Eqn. 22). Table 5 activities are author-reported values calculated from sample/reference ion-current ratios (Eqn. 21); its fit coefficients are also author-reported. The underlying point pressures and numeric ion-current intensities are not tabulated, so there are no stored inputs for those reductions. They are classified `quoted_attributed` under the extraction rule for author-reported fits without stored inputs; no parent rows or synthetic ion intensities were invented. Table 5's activity reference is pure solid Cr2O3, the printed standard.

The source gives no numeric ion-current intensities. It describes detection onsets for Fe+, Cr+, CrO+, and CrO2+, but those are not intensity measurements. No numeric atomic Cr or O2 pressure is tabulated. Figures 1 and 2 contain pressure traces only; figure-read values remain deferred. Candidate figure records: Figure 1, PDF page 6, sample 17 (Fe, Cr, CrO×10, CrO2×10, SiO×10, Ca, Al×10); Figure 2, PDF page 7, sample 18 (Fe, Cr, CrO×10, SiO×10, Ca, Mg×10, Al×10). No points were digitized.

## Typed schema gaps and sign record

Table 4 prints the coefficients A and B of a temperature-dependent log partial-pressure relation, not point pressures. There is no exact fit-coefficient quantity in `simulator/battery/enums.py::Quantity`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, or `simulator/battery/migrate.py::TYPE_QUANTITY`. Using the `partial_pressure` value token would misstate coefficients as pressures. The rows retain descriptive quantity `log_partial_pressure_temperature_fit_coefficients` and migrate as typed refusals.

Table 5 prints coefficients A and B for log activity, not activity coefficients. `simulator/battery/enums.py::Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT` is a near-miss quantity for a temperature-dependent activity coefficient (log gamma); it does not describe the printed log activity (log a). No exact token exists in `simulator/battery/migrate.py::QUANTITY_ALIASES` or `simulator/battery/migrate.py::TYPE_QUANTITY`; the rows retain descriptive quantity `log_activity_temperature_fit_coefficients` and migrate as typed refusals. Their observation types are valid members of `tools/validate_literature_extracts.py::OBSERVATION_TYPES`; the unsupported quantity is the reason for refusal.

The rendered Table 4 caption prints `log(p_i, Pa) = −A/T + B`, and its coefficient cells print negative A values (sample 1: CrO A = −36066.0 ± 2827.4; CrO2 A = −41668.5 ± 1781.7). The page render and machine text agree. Both the equation and A cells are preserved exactly; the source's printed equation/value combination gives a sign-convention ambiguity. The paper's surrounding text, Eqn. (22), and Figures 1–2 do not identify which printed sign is erroneous. This transcription follows the primary page rather than changing the sign or equation.

## Isolated migration

The real migrator produced 100 observations, 0 hard issues, and 60 unavailable values. Those 60 values are the 40 Table 4 and 20 Table 5 coefficient records whose exact quantities have no schema token. The migrated quantity-unknown set is exactly those 60 row IDs; none is inferred as `p_partial` or `activity_coefficient`. All 100 observations have a known phase; the 40 Table 5 activity points migrate as `activity` in phase `l`.

Three migrated activity identity checks:

| Sample and temperature | Identity composition (mole fraction) | Total pressure | fO2 |
| --- | --- | --- | --- |
| 1, 1950 K | Cr2O3 0.066; CaO 0.712; MgO 0.099; Al2O3 0.013; SiO2 0.111 | unknown; not printed | unknown; not printed |
| 1, 2035 K | Cr2O3 0.066; CaO 0.712; MgO 0.099; Al2O3 0.013; SiO2 0.111 | unknown; not printed | unknown; not printed |
| 2, 1950 K | Cr2O3 0.138; CaO 0.543; MgO 0.169; Al2O3 0.010; SiO2 0.140 | unknown; not printed | unknown; not printed |

The focused test checks all temperatures and composition locators, exact typed-refusal IDs, phase coverage, and the absence of numeric total-pressure and fO2 values. It also pins printed Table 4, Table 5, and composition cells to their PDF pages and checks both printed result-table row counts. Its sign-flip mutation fails the same render pin.

Expected store delta without regeneration: 100 source observations (40 activity points and 60 typed-refused fit-coefficient records). No generated store files were committed.
