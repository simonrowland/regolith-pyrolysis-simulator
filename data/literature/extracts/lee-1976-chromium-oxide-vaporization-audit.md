# Lee et al. (1976), Table 3 audit

Table 3 is printed on PDF p. 3 (journal p. 580). It contains four apparent-porosity rows and four temperature columns: 16 numeric cells total. The caption assigns the whole table to Cr2O3. The Table 3 HTML in the MinerU content-list block and its Markdown contain the same digits. `pdftotext-layout.txt` does not preserve the table grid, so the page render was used to confirm all cell row/column assignments. The rendered header prints `μg·cm^-2·sec^-1`.

Every cell below was checked against both the MinerU table block and the rendered PDF page (confirmation method b for cell placement; the exact numeric token came from MinerU). No OCR redo was needed.

| PDF page | Porosity row | Temperature | Value | Cell confirmation |
|---:|---:|---:|---:|---|
| 3 | 0.018 | 1745°C | 39.9 | MinerU + render, row/column b |
| 3 | 0.018 | 1635°C | 5.62 | MinerU + render, row/column b |
| 3 | 0.018 | 1575°C | 1.62 | MinerU + render, row/column b |
| 3 | 0.018 | 1457°C | 0.114 | MinerU + render, row/column b |
| 3 | 0.250 | 1745°C | 42.3 | MinerU + render, row/column b |
| 3 | 0.250 | 1635°C | 6.66 | MinerU + render, row/column b |
| 3 | 0.250 | 1575°C | 1.84 | MinerU + render, row/column b |
| 3 | 0.250 | 1457°C | 0.141 | MinerU + render, row/column b |
| 3 | 0.300 | 1745°C | 45.1 | MinerU + render, row/column b |
| 3 | 0.300 | 1635°C | 6.89 | MinerU + render, row/column b |
| 3 | 0.300 | 1575°C | 2.19 | MinerU + render, row/column b |
| 3 | 0.300 | 1457°C | 0.186 | MinerU + render, row/column b |
| 3 | 0.380 | 1745°C | 47.6 | MinerU + render, row/column b |
| 3 | 0.380 | 1635°C | 7.27 | MinerU + render, row/column b |
| 3 | 0.380 | 1575°C | 2.47 | MinerU + render, row/column b |
| 3 | 0.380 | 1457°C | 0.322 | MinerU + render, row/column b |

The 16 stored points retain the table's printed material as `material_as_printed: Cr2O3` and the outer species assignment `Cr2O3`. The source does not identify a vapor species for this table; `gas_species` is absent. No total pressure, carrier atmosphere, or flow/geometry is inferred. In §3.2 (PDF p. 4, journal p. 581), the authors say the Langmuir vaporization was carried out at about pO2 ≈ 10^-9 atm. It is stored as a nominal approximate point condition, not recast as total pressure or fO2.

The migrator stores the printed rate with canonical `kg m^-2 s^-1` magnitude via the exact factor `1 μg cm^-2 s^-1 = 1e-5 kg m^-2 s^-1`; the original printed quantity and unit remain in the extract and provenance. The source reports sintered specimens, so the observed condensed phase is solid. Method is Langmuir free evaporation.

Figure 1 contains plotted values and is a figure-read candidate only; none were extracted. An isolated run of the real migrator produced 16 observations, 0 hard issues, 0 unavailable values, and 0 queue entries. Three representative identities were inspected: composition is unknown because no melt composition is printed; total pressure is unknown because no numeric total pressure is stated; fO2 is not applicable to this quantity profile; and temperature_K is the printed T_C converted by the migrator. Each observation also retains its row temperature, porosity, and stated pO2 point conditions with locators. Expected battery store delta: +16 observations, all measured_direct evaporation_flux points. Missing flow/geometry yields the expected missing_input scoring refusal.

## MinerU compare

The converter produced five table drafts, including Table 3 at PDF page 3. `compare` reported `matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 total=0`: its numeric-field allowlist does not include `values.evaporation_flux`, so it supplied no cell-comparison rows for this extract. This limitation is recorded rather than disguising it as comparator coverage; every one of the 16 cells is independently confirmed against the rendered page above.
