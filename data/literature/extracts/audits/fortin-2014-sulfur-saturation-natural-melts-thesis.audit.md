# Fortin 2014 extraction audit

Source: Marc-Antoine Fortin (2014), McGill M.Sc. thesis. All locators use absolute PDF pages.

## Printed row counts

- Table 3: 27 author experiment rows (15 on PDF page 38 and 12 on page 39); 18 printed `<d.l.` cells remain unavailable.
- Appendix I: 238 rows (18 Fortin runs and 220 attributed literature runs).
- Appendix II: 14 attributed test rows.
- Appendix III: 108 attributed indirect-water rows: source 1 = 4, source 2 = 53, source 3 = 15, source 4 = 10, source 5 = 21, source 6 = 5.

Appendix values are stored as individual observations so each row's own printed temperature, pressure, fO2, and melt composition bind to its identity through the migrator's observation-level paths. Appendix I's 18 Fortin rows also occur in Table 3. The Appendix I records remain as the values listed in the published fit input, and each corresponding Table 3 measurement supersedes its duplicate; both Table 4 model fits link to the 238 exact Appendix I row observations used by the thesis.

## Cell audit and verification

`fortin-2014-sulfur-saturation-natural-melts-thesis.cells.csv` records the printed cells from Tables 1–4 and Appendices I–III. The appendices have no MinerU blocks, so their cells were transcribed from the exact born-digital pdftotext layer and checked against page renders. Pages 93–96 of Appendix III were rendered to verify row and column assignment, source number, run ID, and water method. This caught the fused source/method parsing of the `albite-H2O Burnham` rows: the 21 Luhr rows are source 5 and run IDs 198, 161, 223, 185, 163, 260, 200, 286, 275, 148, 144, 218, 187, 230, 156, 146, 220, 225, 153, 228, and 155; Zajacz run 196 is source 6. The corrected cell CSV retains `albite-H2O Burnham` verbatim.

Table 1's two LaTeX cells and two numeric mismatches were settled against its page render. Table 2 numeric cells agree across MinerU and pdftotext. Table 3 page 38 cells agree across both machine reads and row assignments were checked against the render; its page 39 continuation was checked against pdftotext and render. Page 38's merged `MORB + 5000 ppm S` group row is accounted for in cell coordinates. Table 4 coefficient cells agree across machine reads; equation and footer layout were checked against its render. The mapping and full compare output are committed alongside this audit.

The regenerated compare output reports zero matches, 177 mismatches, 487 no-matches, and 3,297 unverifiable entries (3,961 total). The p38 Table 3 mismatches are resolved by the cell audit and render: the tool's phase matcher rejects the source's `glass` token and its numeric parser rejects whitespace in parenthetical values such as `47.7 (3)`. The no-match and unverifiable entries are context-only tables, the page 39 continuation, and appendix values for which no MinerU draft exists. The compare output was regenerated after row-level migration changes.

## Attribution and green inventory

`fortin-2014-sulfur-saturation-natural-melts-thesis.sources.csv` maps each appendix source number to its author-year citation and DOI check. The added Appendix III source 5 is Luhr (1990), DOI 10.1093/petrology/31.5.1071; source 6 is Zajacz et al. (2013), DOI 10.1016/j.gca.2013.02.026. The thesis's printed source list and page renders agree with both attributions. The DOI metadata was checked against publisher/source records and author/year; no same-source/run row or matching DOI record was found in the green extract inventory.

## Conditions, evidence, and uncertainty

The 27 Table 3 runs are represented as row observations so each carries its run ID, starting material, temperature, pressure, log fO2, complete melt composition, H2O method, quench statement, and experiment record. The 18 below-detection cells keep the literal `<d.l.` and now use the same observation-level condition path as detected rows, including 1 GPa = 1,000,000,000 Pa, temperature, and composition. The isolated migration binds pressure, temperature, and composition for sampled rows from each appendix and for all 18 below-detection Table 3 rows.

Table 3 values are measured_direct except Raman water, which is measured_reduced because the printed calibration equation `H2O = m*x + b` and Table 2 coefficients reduce the spectra. Raw per-run Raman spectra/intensities are not tabulated (Figure 2, PDF page 30); the stored derivation records those inputs as not printed. Table 3 parenthetical widths remain 1σ standard deviations. The apparatus temperature control tolerance (1250±2 °C, page 26) and VG-2 standard result (1402±28 ppm, page 28) retain their parent uncertainty envelopes in located context records. The authors' reported average SCSS/water slope (93±4 ppm/wt% H2O, page 40) is measured_reduced with figure-located `raw_inputs.state: not_printed`; its confidence level is not stated.

The isolated migration produces 688 observations (665 point, 23 unavailable) and 28 experiments, with zero hard issues. It reports 670 printed uncertainty objects and 18 without an uncertainty object. Of the printed uncertainties, confidence counts are 307 `one_sigma`, 362 `confidence_level_not_stated`, and one explicit `not_stated` for the reported slope. Eighteen Appendix I duplicate measurements are superseded by Table 3, leaving one admitted row per author measurement. No phase is unknown. Exactly five SCSS equation/coefficient/slope observations have quantity-unknown identities because no exact quantity token exists; the focused test pins those row IDs.

## Schema gap and exclusions

The thesis's SCSS equations, coefficients, and reported slope have no exact quantity token in `simulator/battery/enums.py::Quantity` or `simulator/battery/migrate.py::QUANTITY_ALIASES`; the sulfur observation types in `tools/validate_literature_extracts.py::OBSERVATION_TYPES` do not define those quantities. The located values are retained as typed refusals with descriptive non-token labels. Model A/B fit rows link to their 238 stored Appendix I fit inputs. Comparison equations from Liu et al. (2007), Eq. 7, and Li & Ripley (2009), Eq. 9, are typed refusals with located method references and `raw_inputs.state: not_printed`; those source studies' original fit rows are not part of this extract. Figure-only outputs are not digitized; candidate plots are Figures 1, 4, 5, 7, 8, and 9 (PDF pages 2, 42, 57, 61, 64, and 66).

The source reports that sulfur was over 97% sulphide even in the most hydrous experiments (PDF page 24), but prints no per-run sulfide composition. That statement remains unscored context; no per-run sulfide composition is inferred.
