# Extraction audit — Richter et al. (2011)

## Table and row reconciliation

| Printed table | PDF page (published page) | Printed rows | Extracted records |
| --- | --- | ---: | --- |
| Table 1 | 39 (38) | 54 including header; 53 body rows | 49 temperature-bearing sample rows, 2 starting-composition rows, 1 zero-time 50Di50Le baseline without a printed temperature, and 1 gas-flow-rate separator row |
| Table 2 | 40 (39) | 18 including header; 17 body rows | All 17 body rows retained; 34 printed γK/γNa coefficient cells |

Table 1's 49 temperature-bearing samples contain 325 printed oxide residue cells and 21 numeric δ41K rows. The two additional printed `0.00` δ41K values are retained on the IIAB5-start and 50Di50Le-0 metadata rows, with their Table 1 row locators and source-material K2CO3 reference. Neither baseline has a printed isotope uncertainty. The `<0.01` Na2O value on IIAB1-3 remains verbatim with its ICP-MS method and is typed-refused because the residue-composition selector accepts numeric central amounts only. This limitation is recorded with `simulator/battery/migrate.py::Quantity.RESIDUE_COMPONENT_COMPOSITION`, `simulator/battery/migrate.py::select_declared_source`, and `simulator/battery/migrate.py::_classify_composition_cell`.

Residue compositions stay linked to their Table 1 sample/experiment IDs and are never used as starting bulk. Starting composition is separately stored on the experiment sample where the source identifies it. The paper describes zero-time runs as effective starting compositions. For later IIAB2 runs, Table 1 prints two different zero-time assays (IIAB2-13 and IIAB2-15), but the paper does not map either assay to individual runs. Per ruling, their starting-composition identity remains unknown; the reason names both Table 1 row locators (PDF page 39). Both measured rows and all seven oxide cells per row remain recorded as printed.

No sample mass or total mass-loss/fraction-evaporated value is printed in the tables or text inspected. No mass-loss fraction is calculated from oxide depletion. Figure-only values are not digitized: Figs. 8–10 (composition trajectories), Fig. 11 (evaporation rates), Figs. 12–13 (isotope series), and Fig. 15 (cooling-rate model curves).

The source assigns 2.5 mm iridium wire loops to the vacuum experiments. Section 4.3 does not identify a support or crucible for flowing-gas runs, so those records remain unknown. One-bar pressure and the printed gas mixtures/fO2 controls are retained only for the rows or experiment series where the paper states them. The Table 1 surface-area pairs and 24/70 cm/min flow rates are preserved in their printed columns; flow rate is not treated as pressure.

## Full-cell audit

The companion mapping enumerates 738 nonempty rendered table cells with printed row and column coordinates: 640 cells from Table 1 and 98 from Table 2. The table layouts and row/species assignments were checked against 600 dpi renders of PDF pages 39 and 40. Every numeric Table 1 cell used in the extract was confirmed from the page-39 render (method (b)): its MinerU draft has 563 numeric cells and only 0.002 numeric agreement with the pdftotext extraction, so the machine reads alone were not used to settle those values. Table 2's 81 numeric draft cells agree with pdftotext at 1.000; their digits also agree between MinerU and pdftotext (method (a)), and their row/column assignment was checked against the page-40 render (method (b)). The render-verified map records each checked cell as `row`, `column`, and printed text. Table 1 row 27 has one rendered label, `gas flow rate`, in column 5; the other Table 1 row 27 columns are empty.

`mineru_table_draft.py compare` was run on the assembled extract and table index. It reported `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 locator-error=0 unverifiable=0 total=0`; the committed CSV is header-only. The tool's `_numeric_points` reader recognizes selected scalar fields and `values.points`, but this extract stores residue and isotope cells under `values.series` and gamma values under `values.alpha`. Consequently the compare run discovered no candidate cells and is not evidence for transcription fidelity; the full-cell render map above is the cell audit.

The focused test independently pins rendered values and locators on both tables: SiO2 62.21 and Na2O `<0.01` on PDF page 39/Table 1, δ41K 10.6 with 2σ 0.53 on PDF page 39/Table 1, and γK 0.051 plus the average 0.061 ±0.014 on PDF page 40/Table 2. The migrated residue-composition value remains 62.21 on its printed wt% scale; the focused test pins the migrated value as well as the source string. Its mutation check changes 62.21 to 62.22 and confirms the render pin fails.

## Evidence, schema gaps, and migration

Table 1 residue chemistry and δ41K measurements are `measured_direct`. Table 2's present-work γ coefficients and the text-reported fitted isotope factors are `measured_reduced`; the Table 2 derivations point to stored residue rows, while the Figure 12–13 fit rows locate their unprinted plotted inputs. Fedkin et al. (2006) and Alexander (2002) values remain `quoted_attributed`. The three theoretical diffusion factors are `model_derived` with the printed relation and references.

Kinetic isotope-fractionation factors/exponents have no exact quantity token. The eight affected rows are explicitly typed-refused with descriptive quantity labels and remain the exact migrated quantity-unknown set. The check cites `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/enums.py::Quantity.ISOTOPE_DELTA`, `simulator/battery/enums.py::Quantity.EVAPORATION_COEFFICIENT_ALPHA`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, and `simulator/battery/migrate.py::PHASE_MAP`; δ composition and Hertz-Knudsen evaporation probability are not substitutes for kinetic isotope fractionation.

The isolated real-migrator run yields 387 observations, 0 hard issues, and 8 unavailable values (the eight typed-refused isotope-factor rows). The single typed-refused `<0.01` Na2O cell is preserved in the extract but cannot produce a migrated numeric observation under the current composition selector. There are 404 queue entries: 388 unknown method-token entries for the descriptive vacuum/flowing-gas experiment labels, 8 quantity entries for the kinetic-isotope schema gaps, and 8 corresponding unavailable-value entries. No phase-unknown observation is produced. Migrated identity binds printed temperature, pressure, and starting composition where a specific source value is available; unprinted aggregate-row conditions stay unknown.

## Ruling on IIAB2 starting-composition identity

The source does not identify which zero-time assay (Table 1, PDF page 39, row IIAB2-13 or row IIAB2-15) supplied the starting composition for each later IIAB2 run. Keep those identities unknown; selecting either assay, averaging them, or choosing the nearer row would invent a source mapping. The focused test pins the ambiguity reason on every IIAB2 sample row from IIAB2-11 onward and independently pins both assay rows' seven printed oxide values.
