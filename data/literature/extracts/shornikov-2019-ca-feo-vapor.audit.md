# Shornikov (2019), CaO–FeO: extraction audit

## Table 1 (PDF p. 3; published p. 84)

The printed table has 11 body rows, 16 numeric cells, and 17 empty or dash
cells. The emitted rows retain the printed 1873 K heading, phase columns, and
values. MinerU and `pdftotext-layout.txt` agree on every stored digit and sign.
The 600 dpi render of PDF page 3 confirms the row and column assignments for
all 16 cells. No table cell was hand-corrected; no cell required an individual
render check for an OCR disagreement or flag.

| Emitted table cell | Species | Phase | Printed value (kJ/mol) | Audit |
| --- | --- | --- | ---: | --- |
| r03 c02 | CaO | solid | -435.296 | MinerU + pdftotext; render layout check |
| r03 c04 | CaO | liquid | -422.317 | MinerU + pdftotext; render layout check |
| r03 c06 | Ca | gas | -9.985 | MinerU + pdftotext; render layout check |
| r04 c02 | CaFeO2 | solid | -599.816 | MinerU + pdftotext; render layout check |
| r04 c06 | Ca2 | gas | 90.363 | MinerU + pdftotext; render layout check |
| r05 c02 | FeO | solid | -153.762 | MinerU + pdftotext; render layout check |
| r05 c04 | FeO | liquid | -157.951 | MinerU + pdftotext; render layout check |
| r05 c06 | CaO | gas | -68.953 | MinerU + pdftotext; render layout check |
| r06 c06 | Fe | gas | 145.501 | MinerU + pdftotext; render layout check |
| r07 c06 | Fe2 | gas | 399.724 | MinerU + pdftotext; render layout check |
| r08 c06 | FeO | gas | 76.891 | MinerU + pdftotext; render layout check |
| r09 c06 | FeO2 | gas | 34.624 | MinerU + pdftotext; render layout check |
| r10 c06 | O | gas | 130.138 | MinerU + pdftotext; render layout check |
| r11 c06 | O2 | gas | 0.000 | MinerU + pdftotext; render layout check |
| r12 c06 | O3 | gas | 266.369 | MinerU + pdftotext; render layout check |
| r13 c06 | O4 | gas | 181.230 | MinerU + pdftotext; render layout check |

The required `compare` invocation emitted no per-cell rows:

```text
compare: matched=0 no-match=0 mismatch=0 presence-only=0 total=0
species,observation_id,point_index,pdf_page,value,status,matched_cell,draft_candidate_count,path
```

The current comparison helper scans `values.points`; `mineru_table_draft
emit` emits these Gibbs-table cells as scalar `values.value` observations.
This comparison output therefore provides no cell comparison. The complete
manual cell audit above is the workaround; no tool code was changed.

## Figure 2 calibration and raw reads

Figure 2 is on the 600 dpi render of PDF page 3 (published page 84). Both axes
are linear. Pixel coordinates below are local to the cropped panel image;
the extract stores the same raw centers. Each marker was centered twice
independently. Read 2 uses a fresh panel crop offset by 25 px left and 20 px up,
then a new visual centre estimate; the coordinates below are translated back
to the first crop's origin. Stored axis values are the mean of the two converted reads,
rounded to two significant figures. The largest pairwise spread is 0.1603
FeO-mol%-points and 0.0078 activity. Maximum absolute log-ratio difference is
0.0059 dex, below the 0.02 dex re-read threshold; half-differences are stored
as read_uncertainty_dex. Both plotted axes are linear, so the plotted ordinate
remains the activity value.

| Panel | x tick labels and pixel x | Fitted x map | y tick labels and pixel y | Fitted y map |
| --- | --- | --- | --- | --- |
| a | 60: 756; 70: 1068; 80: 1380; 90: 1692 | FeO mol% = 60 + (px − 756) / 31.2 | 0.0: 1057; 0.2: 850.5; 0.4: 645.5; 0.6: 441; 0.8: 235; 1.0: 29 | aFeO = (1057 − py) / 1028 |
| b | 60: 331; 70: 643; 80: 954; 90: 1266; 100: 1578 | FeO mol% = 60 + (px − 331) / 31.2 | 0.0: 1052; 0.2: 845.5; 0.4: 640.5; 0.6: 435; 0.8: 230; 1.0: 26 | aFeO = (1052 − py) / 1026 |

The Figure 2 caption assigns marker symbol 1 to Ban-Ya et al. (1980) in panel
a and symbol 2 to Fujita et al. (1968) in panel b. The panel-b caption labels
the data 1873 K; the prose says Fujita et al. measured at 1833 K. The plotted
points stay located to the captioned panel; the conflict is retained here and
in the extract without reconciliation.

| Marker | Read 1 pixel (x,y) | Read 1 (FeO mol%, aFeO) | Read 2 pixel (x,y) | Read 2 (FeO mol%, aFeO) | Stored (FeO mol%, aFeO) | Read uncertainty (dex) |
| --- | --- | --- | --- | --- | --- | ---: |
| a1 | (733, 576) | (59.2628, 0.4679) | (734, 576) | (59.2949, 0.4679) | (59, 0.47) | 0 |
| a2 | (800, 528) | (61.4103, 0.5146) | (801, 529) | (61.4423, 0.5136) | (61, 0.51) | 0.0004 |
| a3 | (970, 478) | (66.859, 0.5632) | (972, 478) | (66.9231, 0.5632) | (67, 0.56) | 0 |
| a4 | (1022, 464) | (68.5256, 0.5768) | (1026, 472) | (68.6538, 0.5691) | (69, 0.57) | 0.0029 |
| a5 | (1065, 437) | (69.9038, 0.6031) | (1070, 439) | (70.0641, 0.6012) | (70, 0.6) | 0.0007 |
| a6 | (1112, 385) | (71.4103, 0.6537) | (1110, 386) | (71.3462, 0.6527) | (71, 0.65) | 0.0003 |
| a7 | (1152, 384) | (72.6923, 0.6547) | (1153, 389) | (72.7244, 0.6498) | (73, 0.65) | 0.0016 |
| a8 | (1269, 333) | (76.4423, 0.7043) | (1274, 334) | (76.6026, 0.7033) | (77, 0.7) | 0.0003 |
| a9 | (1294, 329) | (77.2436, 0.7082) | (1295, 330) | (77.2756, 0.7072) | (77, 0.71) | 0.0003 |
| a10 | (1354, 281) | (79.1667, 0.7549) | (1353, 283) | (79.1346, 0.7529) | (79, 0.75) | 0.0006 |
| a11 | (1412, 271) | (81.0256, 0.7646) | (1413, 270) | (81.0577, 0.7656) | (81, 0.77) | 0.0003 |
| a12 | (1480, 232) | (83.2051, 0.8025) | (1483, 232) | (83.3013, 0.8025) | (83, 0.8) | 0 |
| a13 | (1566, 166) | (85.9615, 0.8667) | (1568, 168) | (86.0256, 0.8648) | (86, 0.87) | 0.0005 |
| a14 | (1629, 175) | (87.9808, 0.858) | (1630, 176) | (88.0128, 0.857) | (88, 0.86) | 0.0002 |
| a15 | (1658, 135) | (88.9103, 0.8969) | (1658, 131) | (88.9103, 0.9008) | (89, 0.9) | 0.0009 |
| a16 | (1664, 107) | (89.1026, 0.9241) | (1662, 107) | (89.0385, 0.9241) | (89, 0.92) | 0 |
| a17 | (1731, 69) | (91.25, 0.9611) | (1732, 70) | (91.2821, 0.9601) | (91, 0.96) | 0.0002 |
| a18 | (1813, 45) | (93.8782, 0.9844) | (1813, 45) | (93.8782, 0.9844) | (94, 0.98) | 0 |
| b1 | (331, 581) | (60, 0.4591) | (331, 582) | (60, 0.4581) | (60, 0.46) | 0.0005 |
| b2 | (363, 521) | (61.0256, 0.5175) | (364, 522) | (61.0577, 0.5166) | (61, 0.52) | 0.0004 |
| b3 | (379, 592) | (61.5385, 0.4483) | (379, 592) | (61.5385, 0.4483) | (62, 0.45) | 0 |
| b4 | (431, 597) | (63.2051, 0.4435) | (432, 598) | (63.2372, 0.4425) | (63, 0.44) | 0.0005 |
| b5 | (453, 495) | (63.9103, 0.5429) | (458, 490) | (64.0705, 0.5478) | (64, 0.55) | 0.0019 |
| b6 | (517, 443) | (65.9615, 0.5936) | (518, 443) | (65.9936, 0.5936) | (66, 0.59) | 0 |
| b7 | (577, 442) | (67.8846, 0.5945) | (577, 442) | (67.8846, 0.5945) | (68, 0.59) | 0 |
| b8 | (659, 347) | (70.5128, 0.6871) | (660, 348) | (70.5449, 0.6862) | (71, 0.69) | 0.0003 |
| b9 | (923, 224) | (78.9744, 0.807) | (923, 224) | (78.9744, 0.807) | (79, 0.81) | 0 |
| b10 | (923, 265) | (78.9744, 0.7671) | (924, 265) | (79.0064, 0.7671) | (79, 0.77) | 0 |
| b11 | (985, 195) | (80.9615, 0.8353) | (986, 195) | (80.9936, 0.8353) | (81, 0.84) | 0 |
| b12 | (1043, 181) | (82.8205, 0.8489) | (1043, 181) | (82.8205, 0.8489) | (83, 0.85) | 0 |
| b13 | (1102, 141) | (84.7115, 0.8879) | (1103, 142) | (84.7436, 0.8869) | (85, 0.89) | 0.0002 |
| b14 | (1110, 189) | (84.9679, 0.8411) | (1110, 190) | (84.9679, 0.8402) | (85, 0.84) | 0.0003 |
| b15 | (1142, 165) | (85.9936, 0.8645) | (1142, 165) | (85.9936, 0.8645) | (86, 0.86) | 0 |
| b16 | (1292, 131) | (90.8013, 0.8977) | (1293, 131) | (90.8333, 0.8977) | (91, 0.9) | 0 |
| b17 | (1347, 82) | (92.5641, 0.9454) | (1348, 82) | (92.5962, 0.9454) | (93, 0.95) | 0 |
| b18 | (1381, 112) | (93.6538, 0.9162) | (1381, 113) | (93.6538, 0.9152) | (94, 0.92) | 0.0002 |

All 36 activity values and printed FeO mol% coordinates are retained in the
extract with their two reads and pixel centers. The migrator identity marks
composition unknown and typed-refuses these rows because no mole-percent
composition basis exists. The printed percentage is not converted to a mole
fraction. The Figure 2 model curves 3–8 in both panels have no numeric points;
their real quantity, species, phase, temperature, curve label, attribution,
and figure locator are recorded as unavailable figure-only rows.

## Text values and additional figure schema gaps

- Table 1: 11 printed body rows; 16 numeric cells emitted and audited above;
  17 empty or dash cells retained in the empty-cell count.
- The paper describes present-study thermodynamic calculations over
  1300–2200 K. The text says FeO activities were measured in cited studies over
  1473–1873 K. Figure 2 panel a/b labels are 1673/1873 K.
- Oelsen and Maetz (1941) report Ca2Fe2O5 decomposition at 1423 K.
  Allen and Snow (1955) report Ca2Fe2O5 decomposition at 1381±3 K and CaFe2O3
  dissociation at 1406±3 K. Confidence level is not stated. These are separate
  quoted-attributed transition observations in the extract.
- Shugurov et al. (2005) report calculated CaFeO2 ΔfH298 = −213.7 kJ/mol;
  retained as a quoted-attributed observation at 298 K.
- The text reports CaFeO2 formed in cited sintering work over 1173–1523 K.
  This is a process range, not a transition temperature; it is retained here
  as a typed-refused schema gap rather than mis-typed as a transition.
- Figure 1 is a compiled phase diagram; no discrete marker values are printed
  or digitized. It is retained as a figure-read schema-gap candidate in this
  audit; its phase-boundary curves were not sampled.
- Figure 3 shows mixing-energy curves for 1673 and 1873 K. The text reports
  the minimum as −6.5 to −9.0 kJ/mol over the considered range and says the
  present study and Bjorkvall et al. (2001) are close. The schema has no
  Gibbs-energy-of-mixing quantity token, so the range and six model curves are
  typed-refused schema gaps; no delta_fG surrogate was created.
