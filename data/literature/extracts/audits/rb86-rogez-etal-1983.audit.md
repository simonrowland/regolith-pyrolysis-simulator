# Rogez et al. (1983) extraction audit

The duplicate search by DOI, author/year, and title found no existing extract at the seat base or in reachable refs. The target extract had no prior history.

## Table and row reconciliation

The seat table tool converted the MinerU content list into 11 blocks. Its QA pre-screen reported low agreement for Table II (0.733), Table III (0.590), Table VI (0.500), and appendix blocks on PDF pp. 9–10 (0.372, 0.251, 0.006). The two empty appendix blocks on pp. 10–11 were retained in the inventory and reconciled against the renders.

| PDF page | Printed material | Printed rows | Reconciliation |
|---|---|---:|---|
| 3 | Table I, glass analyses | 18 | Render confirms six theoretical composition rows, each followed by two measured analyses; five columns including the row label. |
| 5 | Table II, alumina-alpha and quartz dissolution enthalpies | 7 | Render confirms four alumina-alpha and three quartz rows; material, attribution, enthalpy, deviation, n, and T columns were assigned separately. |
| 5 | Table III, feldspar-glass dissolution enthalpies | 6 | Render confirms six xOr rows, with replicate and mean ± standard deviation cells kept separate. |
| 5 | Table IV, feldspar-glass mixing enthalpies | 6 | Render confirms six xOr rows and both zero endpoints. MinerU's final `∅` was corrected to the printed `0`; the uncertainty width has no stated confidence level. |
| 8 | Table V, normalized ion-current ratio | 10 | Numeric rows were marked as header by MinerU; all ten rows were transcribed from the render. Figures 2–7 remain figure-read candidates. |
| 9 | Table VI, activities and excess Gibbs energies | 11 | Render confirms six numeric columns, the 11 xOr rows, and their row/column assignment. The addendum's doubled spectrometry formula-unit basis is recorded separately from the monomer calorimetry basis. |
| 9–11 | Appendix, primary KEMS series | 318 | Hybrid-high and pipeline-ocr variants were compared with the page renders. The xAb=0.3 second assay (19 rows) was absent from both variants and transcribed from PDF p. 10. The printed xAb=0.875 at assay 2, run 18 is retained literally and assigned to the xAb=0.975 series, with the apparent source typo qualified. Nineteen OCR numeric cells were corrected from the renders, including the xAb=0.1 assay transition and the xAb=0.997 assay 2 run 9. The xAb=0.997 series on p. 11 has 7 + 19 rows. |

Appendix rows by printed composition and assay: xAb=0.003, 36 (16+20); 0.025, 41 (20+21); 0.1, 33 (9+24); 0.3, 29 (10+19); 0.5, 20; 0.6, 40 (20+20); 0.7, 36 (13+23); 0.9, 25 (13+12); 0.975, 32 (14+18); 0.997, 26 (7+19). Total: 318 rows.

The cell audit at `rb86-rogez-etal-1983.cells.csv` records 1,488 numeric cells with PDF page, printed table/row/column, printed value, selected read source, and cell-level render confirmation: Table I 72, II 28, III 18, IV 12, V 20, VI 66, appendix 1,272. The appendix records the explicit render overrides, OCR agreement checked against the render, direct render transcriptions for the missing assay rows, and the literal 0.875 cell with its assigned series. The initial audit had 20 incorrect appendix cells marked render-confirmed; the corrected values and bindings are reflected in this regenerated audit.

The render re-audit corrected 20 appendix cells: 19 numeric OCR values and the xAb cell at series 0.975, assay 2, run 18, where the render prints `0,875`. It also corrected the Table VI xOr=0.9 partial excess-Gibbs cell from `-24,53` to `-24,63`. The audit now binds each appendix cell to `series`, `assay`, `run`, and the literal printed xAb cell rather than an OCR source-row number.

## Extract decisions

Table VI's 22 component activities are the scored observations. The paper says they were obtained by graphical integration of its Table V ratio relation (section 4.3, PDF p. 8). Table V's ratio has no matching quantity token and is retained as context, so d-110's rule for reported fit outputs with unstored inputs makes the activities `quoted_attributed`, attributed to Rogez et al. (1983), Table VI. The addendum (PDF p. 11 / published p. 128) distinguishes monomer feldspar units for calorimetry from the spectrometry components `(Na2O, Al2O3, 6SiO2)` and `(K2O, Al2O3, 6SiO2)`. The activity species and pure liquid standard states use the resulting doubled formulas `Na2Al2Si6O16` and `K2Al2Si6O16`; the printed values were not converted. Each observation carries its printed 1300 K temperature and binary row composition.

No absolute alkali partial pressures, total pressure, or oxygen fugacity are tabulated. The Appendix's `10^4/T` and log ion-current ratio remain in their printed form; no absolute T or pressure was inferred. The migrated records leave total pressure out of point conditions and carry unknown fO2. No ambient, vacuum, or standard pressure was added.

Other tables are retained as typed-refused context because the schema has no matching quantity token for glass oxide composition (Table I), dissolution enthalpies (Tables II–III), mixing enthalpy (Table IV), normalized ion-current ratio (Table V), excess Gibbs energies (Table VI), or the Appendix's reciprocal-temperature and log-ratio columns. The reference paper is not used as a derivation parent. Figures 2–7 are listed as figure-read candidates and were not digitized.

PDF p. 2 states that the samples were held at 1873 K for 5–8 hours and quenched. It does not state whether quenching preserved or altered composition; glass context rows use `quench_status: not_stated` with that page-grounded statement. The 1320 K, 1.5 hour solvent preparation is kept separate from sample preparation. KEMS apparatus context records the initial molybdenum cell and MoOx/NaMoO3 signals, the switch to a platinum container with welded lid, 14 eV ionization, pyrometer ±5 K, temperature calibration by ionic-intensity plateaus at fusion, and the Belton–Fruehan (1967) ratio calibration method.

## Tool comparison and migration

`mineru_table_draft.py compare` reports that the 22 Table VI activity observations do not match its OCR draft cells. The comparison report was regenerated with the doubled formula identities and is preserved in `rb86-rogez-etal-1983.audit.compare.txt`. The activity literals were independently checked against the rendered Table VI and are listed in the cell audit.

The isolated real migrator produced 22 observations, 10 context rows, 0 unavailable values, 0 queue entries, and 0 hard issues. All 22 identities received T=1300 K, the printed binary composition, and the doubled spectrometry basis. The identity total-pressure and fO2 axes remain unknown. After migration, no total-pressure point condition is present; point-condition fO2 is unknown. No store regeneration was committed; the expected rebuilt-store delta is one work, one experiment, 22 activity observations, and 10 context rows.

The focused regression test pins both rendered tables, printed row counts, all 22 migrated activity values, exact row compositions, dimer standard states, observation locators, and located conditions. Isolated mutations changing the migrated xOr=0.5 activity to 0.999 and its composition to 0.9/0.1 each fail the migration test while the other focused test passes. The focused test file passed both tests; `tests/test_literature_extracts.py -k rogez` passed 1 test with 800 deselected; `tests/test_yaml_flow_scalar_commas.py` passed all 17 checks; and the direct validator returned `OK: 1 extract file(s) valid`.
