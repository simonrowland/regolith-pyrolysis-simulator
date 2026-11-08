# Namur et al. (2016) table audit notes

## Table coverage and cell verification

Table 1 is on PDF page 3 / printed page 104. It has 12 labeled rows across three sections: three MESSENGER composition rows, three synthetic silicate composition rows, and six silicate–metal–sulfide mixture-ratio rows. The cell audit retains 64 numeric cells (78 numeric tokens), including 14 value/uncertainty pairs, and 11 printed dashes. Table 2 is on PDF page 7 / printed page 108. It has seven coefficient rows and five fit-quality statistics: 26 numeric body cells, plus the 10% and 5% significance thresholds in the table footnotes. `namur-charlier-holtz-cartier-mccammon-2016-reduced-mafic-sulfur-solubility.cells.csv` records the section, row, column, printed spelling, and render verification for these 103 cells.

MinerU and pdftotext numeric agreement was 1.000 for both tables. The Table 1 and Table 2 row/column layout, formula/letter flags, negative coefficient signs, powers of ten, 1σ values, fit statistics, and footnote thresholds were checked against rendered PDF pages 3 and 7. The focused test compares each table data cell in the stored context with the audit CSV by PDF page, printed table, section, row, and column. The mapping contains only fields accepted by the table tool; its empty `tables` list reflects that these two tables contribute context, not scored observations. The table-tool comparison is observation-only: its numeric-point walker traverses scored observations, and this extract contains none, so the tool reports total 0. The focused cell audit covers the context rows independently.

Table 1 footnotes read: (a) calculated using elemental ratios from MESSENGER (Weider et al., 2012); (b) K2O and Na2O calculated from Peplowski et al. (2012, 2014), TiO2 and MnO contents based on Weider et al. (2014), and EH composition from Berthet et al. (2009); (c) experiment-specific silicate–metal mixtures and added Si metal are referred to Supplementary Dataset 1. Table 2 footnotes state that 1σ is the standard deviation of each coefficient; p-values test parameter significance and only parameters significant at 10% were retained; r² is the coefficient of determination, F and p-value test regression significance at 5%, and n is the number of observations.

## Missing supplementary run data

The held postprint refers to Supplementary Dataset 1 for run-specific starting mixtures, added Si metal, experimental conditions, and method details. It refers to Supplementary Dataset 2 for run-level phase analyses, including glass sulfur. Neither dataset is present in the held corpus or the read-only supporting-files directory. Therefore the main-text tables do not supply run IDs, run-specific T, P, fO2, duration, capsules, final glass S, or coexisting metal/sulfide compositions. No experiment rows or conditions are inferred from prose ranges.

The paper describes quenched silicate melt but does not state whether sulfur is preserved or altered on quenching. No glass-result row is present in the held main-text tables, so there is no per-run quench status to attach.

## Located fO2 method

Section 2.2, “Experiments, analytical methods and oxygen fugacity,” on PDF page 3 / printed page 104 gives Equation (2): `SiMet + O2 = SiO2Sil`. The authors calculate fO2 using Si in metal and SiO2 in silicate and refer to the Appendix for calculation details. Run-specific Si analyses and fO2 results are in missing Supplementary Dataset 1.

## Figure-only candidates

- Figure 3a, PDF page 5 / printed page 106: measured silicate-glass sulfur at sulfide saturation versus delta-IW, with error bars. Not extracted because values are figure-only.
- Figure 3b, PDF page 5 / printed page 106: metal and sulfide melt composition points. Not extracted because values are figure-only.
- Figure 5, PDF page 7 / printed page 108: model comparison and diagnostics. Not digitized.

## Typed-unavailable regression context

Table 2 regression coefficients are retained as `model_derived` context with Equation (10), printed 1σ widths, and fit statistics. They are marked `typed_unavailable` because no exact regression-coefficient quantity token exists. `OBSERVATION_TYPES` names the sulfur measured series but not model-fit coefficients; `TYPE_QUANTITY` does not infer a coefficient quantity from a sulfur series; `QUANTITY_ALIASES` and `Quantity` contain no exact SCSS regression-coefficient token. `interaction_parameter` would misidentify this fitted SCSS model. Code symbols: `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `simulator/battery/enums.py::Quantity`.

## Expected store change

Expected delta is +1 work, +3 context records, and +0 scored observations. No battery-store regeneration is committed.
