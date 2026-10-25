# Tsuchiyama & Fujimoto (1995) full-cell audit

Source: *Evaporation experiments of metallic iron in vacuum*, Proc. NIPR Symp. Antarct. Meteorites 8, 205–213, DOI 10.15094/00004677. Physical PDF page numbers below correspond to published pages 205–213 with the cover/title page offset shown by each locator.

## Duplicate and source checks

The seat extract and `git log --all` searches by DOI, Tsuchiyama/Fujimoto plus 1995, and title words found no existing extract of this paper. The corpus also has a MinerU output nested under a `src--condensation-coefficients` directory. Its content is this same 1995 paper and its two tables, not a second paper or an existing extract. The two MinerU content-list reads were retained as duplicate reads for the audit.

The born-digital `pdftotext-layout.txt` is the exact text layer. The two MinerU reads and pdftotext were used before rendering. The Table 1 column assignment was checked once against the physical PDF page 2 render. Table 2 was checked cell by cell against the physical PDF page 6 render because the table QA flagged its LaTeX numeric cells and first-row header assignment. No OCR redo was needed: the tables were legible and all flagged cells could be resolved from the render.

## Table 1: all printed runs

The printed count is **20 runs**. The first body row (run 1101) was incorrectly classified as a header by the table draft; runs 1101–1352 below include it as data. Column assignments were checked on page 2, published page 206. For each row, all eight numeric cells (columns 1–8) have the shown printed digits in both MinerU reads and pdftotext **(a)**; the render confirms that the digits occupy these columns and rows. Row coordinates are PDF p. 2, table 1, data rows 2–21.

| Run (c1) | T °C (c2) | Duration h (c3) | Initial mg (c4) | Final mg (c5) | Weight loss mg (c6) | Surface area mm² (c7) | ΔM/S mg mm⁻² (c8) | Cell audit |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1101 | 1075 | 72 | 150.45 | 149.51 | 0.94 | 94.260 | 0.010 | p. 2, r2, c1–8 (a); render confirms layout |
| 1102 | 1075 | 96 | 159.72 | 154.87 | 4.85 | 98.895 | 0.0490 | p. 2, r3, c1–8 (a); render confirms layout |
| 1103 | 1075 | 96 | 127.76 | 122.28 | 5.48 | 92.240 | 0.0594 | p. 2, r4, c1–8 (a); render confirms layout |
| 1151 | 1123 | 72 | 103.60 | 93.50 | 10.10 | 85.255 | 0.1185 | p. 2, r5, c1–8 (a); render confirms layout |
| 1153 | 1123 | 84 | 147.52 | 123.10 | 24.42 | 92.320 | 0.2645 | p. 2, r6, c1–8 (a); render confirms layout |
| 1154 | 1123 | 84 | 161.18 | 138.72 | 22.46 | 99.410 | 0.2259 | p. 2, r7, c1–8 (a); render confirms layout |
| 1152 | 1123 | 96 | 151.96 | 134.73 | 17.23 | 97.020 | 0.1776 | p. 2, r8, c1–8 (a); render confirms layout |
| 1201 | 1170 | 24 | 138.82 | 128.20 | 10.62 | 91.020 | 0.1167 | p. 2, r9, c1–8 (a); render confirms layout |
| 1202 | 1170 | 24 | 147.67 | 136.60 | 11.07 | 88.556 | 0.1250 | p. 2, r10, c1–8 (a); render confirms layout |
| 1203 | 1170 | 24 | 140.52 | 128.49 | 12.03 | 95.050 | 0.1266 | p. 2, r11, c1–8 (a); render confirms layout |
| 1204 | 1170 | 36 | 152.17 | 123.48 | 28.69 | 98.300 | 0.2919 | p. 2, r12, c1–8 (a); render confirms layout |
| 1253 | 1220 | 4 | 154.02 | 137.94 | 16.08 | 91.540 | 0.1757 | p. 2, r13, c1–8 (a); render confirms layout |
| 1251 | 1220 | 6 | 154.59 | 141.95 | 12.64 | 100.200 | 0.1262 | p. 2, r14, c1–8 (a); render confirms layout |
| 1252 | 1220 | 8 | 137.59 | 117.30 | 20.29 | 93.525 | 0.2170 | p. 2, r15, c1–8 (a); render confirms layout |
| 1303 | 1265 | 2 | 147.52 | 137.41 | 10.11 | 96.700 | 0.1046 | p. 2, r16, c1–8 (a); render confirms layout |
| 1302 | 1265 | 4 | 142.50 | 124.64 | 17.86 | 92.830 | 0.1924 | p. 2, r17, c1–8 (a); render confirms layout |
| 1301 | 1265 | 24 | 161.80 | 54.47 | 107.33 | 96.625 | 1.1108 | p. 2, r18, c1–8 (a); render confirms layout |
| 1351 | 1312 | 0.5 | 132.11 | 125.29 | 6.82 | 90.280 | 0.0755 | p. 2, r19, c1–8 (a); render confirms layout |
| 1353 | 1312 | 0.75 | 110.51 | 80.36 | 30.15 | 91.800 | 0.3284 | p. 2, r20, c1–8 (a); render confirms layout |
| 1352 | 1312 | 1 | 133.68 | 110.47 | 23.21 | 89.485 | 0.2594 | p. 2, r21, c1–8 (a); render confirms layout |

The test pins run 1101 (PDF p. 2, r2 c8 = 0.010; c2 = 1075°C; c3 = 72 h; c6 = 0.94 mg), run 1301 (p. 2, r18 c7 = 96.625 mm²; c6 = 107.33 mg), and the table’s full 20-run count.

## Table 2: all measured rates and standard deviations

The printed count is **6 rate rows**. The machine draft had a header-row assignment issue and QA flagged its LaTeX numeric cells; all 18 numeric cells (temperature, rate, and standard deviation) below were read from the page 6 render **(b)**. The render confirms the row and column assignments. The exact rendered spellings of the rate and SD are kept in the extract; the numeric `evaporation_rate` field is in the same printed mol m⁻² s⁻¹ unit and is admission-rejected as described below.

| T °C (c1) | Rate, mol m⁻² s⁻¹ (c2) | SD, mol m⁻² s⁻¹ (c3) | Cell audit |
|---:|---:|---:|---|
| 1075 | 2.344 × 10⁻⁶ | 6.43 × 10⁻⁷ | p. 6, r2, c1–3 (b), page render |
| 1123 | 1.165 × 10⁻⁵ | 1.72 × 10⁻⁶ | p. 6, r3, c1–3 (b), page render |
| 1170 | 3.182 × 10⁻⁵ | 4.27 × 10⁻⁶ | p. 6, r4, c1–3 (b), page render |
| 1220 | 1.370 × 10⁻⁴ | 2.49 × 10⁻⁵ | p. 6, r5, c1–3 (b), page render |
| 1265 | 2.307 × 10⁻⁴ | 1.99 × 10⁻⁶ | p. 6, r6, c1–3 (b), page render |
| 1312 | 1.491 × 10⁻³ | 3.49 × 10⁻⁴ | p. 6, r7, c1–3 (b), page render |

The standard deviations are stored as printed, with confidence level not stated. The focused test pins the page 6, row 6 rate and SD and row 7 SD. The committed `*.compare.csv` records 26/26 matches for the Table 1 ΔM/S values and Table 2 rates; the SD columns are separately hand-transcribed from the render because the table emitter does not split rate/SD pairs.

## Other stored numeric text and conditions

| Stored item | Source and cell/text locator | Confirmation |
|---|---|---|
| 99.99% starting-plate purity | p. 2, published p. 206, Experiments prose | MinerU content-list text and pdftotext agree; stored as starting-material text, not a full composition |
| Total-pressure range 10⁻⁵ to 10⁻⁶ Torr | p. 3, published p. 207, Experiments prose | MinerU text and pdftotext agree; stored verbatim on each row and as a located Pa interval in `point_conditions.total_pressure_Pa`, using 1 Torr = 101325/760 Pa |
| Estimated pO₂ about 10⁻⁹ atm, range 3 × 10⁻⁹ to 3 × 10⁻¹⁰ atm | p. 3, published p. 207, Results prose | MinerU text and pdftotext agree; the paper conditions this estimate on residual gas being air and notes diffusion-pump oil can lower it. It is retained as text and not assigned to fO₂ |
| γ-iron stable over 1075–1312°C | p. 7, published p. 211, Discussion | MinerU markdown and pdftotext agree; rows are `phase: cr` with `condensed_form.polymorph: gamma` |
| αᵥ about 0.5; ln(αᵥ) = −0.890 + 299/T | p. 8, published p. 212, Discussion | MinerU markdown and pdftotext agree; retained as quoted model context because JANAF equilibrium Fe(g) inputs are not stored |

The caption and row headings assign all Table 1 and Table 2 rows to metallic Fe. The starting plates are stated as 99.99% pure polycrystalline metallic iron; the source reaction is Fe(s) = Fe(g). The experiment pressure is a series range, not a per-run point. The source gives only a residual-air estimate of oxygen partial pressure, not a stated oxygen fugacity. There is no full sample composition. No ambient pressure, one atmosphere, or unprinted room temperature was added.

## Lineage, admission, and migration

Table 2 rates were obtained by least-squares fitting of Table 1 ΔM/S versus time using ΔM/S = MFe jmeas t. Each rate’s `derived_from` points to exactly the Table 1 runs at that temperature (3, 4, 4, 3, 3, and 3 runs respectively); the paper’s fit is marked `measured_reduced`, not `model_derived`. The reported αᵥ comparison is not model-derived because the required JANAF inputs are absent. Fig. 4’s plotted rate curve is a figure-read candidate and was not digitized.

Isolated `Migrator` result: **26 observations; 0 hard validation issues; 0 registry issues; 20 unavailable values; 6 Table 2 values retained numerically but Admission REJECTED; queue size 60**. For the 20 Table 1 rows, the queue has three notices per row: the existing `mass_loss_areal_density` converter does not accept printed mg mm⁻² (20 unit refusals), no closed method token represents these gravimetric rows (20 method notices), and the quantity remains unavailable after those refusals (20 dependent notices). Table 2’s six rates are rejected because `evaporation_rate` has no form for mol Fe m⁻² s⁻¹; their original values and units remain in the extract. No conversion to kg s⁻¹, no inferred area, and no admitted store rows result. All 26 migrated observations have a located temperature and located total-pressure, fO₂, and composition conditions.

For the `evaporation_rate` identity profile, the isolated migration printed these axes for three rows:

| Table 2 row | Identity T (K) | Identity composition | Identity total pressure | Identity fO₂ | Point-condition pressure |
|---:|---:|---|---|---|---|
| 1075°C | 1348.15 | unknown: full composition not stated | unknown: scalar identity cannot represent the printed interval | unknown: no fO₂ stated | located interval 1.3332236842105263 × 10⁻⁴ to 1.3332236842105263 × 10⁻³ Pa |
| 1265°C | 1538.15 | unknown: full composition not stated | unknown: scalar identity cannot represent the printed interval | unknown: no fO₂ stated | located interval 1.3332236842105263 × 10⁻⁴ to 1.3332236842105263 × 10⁻³ Pa |
| 1312°C | 1585.15 | unknown: full composition not stated | unknown: scalar identity cannot represent the printed interval | unknown: no fO₂ stated | located interval 1.3332236842105263 × 10⁻⁴ to 1.3332236842105263 × 10⁻³ Pa |

Identity pressure is the remaining schema limitation: the paper prints a pressure range, while the existing identity axis is a scalar Decimal. The full interval is preserved in each located point condition and the observations are typed-refused; no representative pressure is invented. The printed pure-Fe(s) reservoir is retained verbatim in each row. The current extract migrator does not project that text onto the `EVAPORATION_RATE` identity reservoir axis, so it remains unknown on rejected rows.
