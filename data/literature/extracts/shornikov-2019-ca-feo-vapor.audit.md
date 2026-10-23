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

Figure 2 is on the 600 dpi render of PDF page 3 (published page 84).
Both axes are linear. Read 1 coordinates are local to the original panel
crops, whose page origins are panel a (300, 2350) and panel b (2199, 2349).
Each marker has an independent second visual centre estimate from its own
fresh crop from the page render. The `read_2.crop_id`, full-page crop origin,
and within-crop centre are stored with that read. Read 2 is a second measurement
of the half-disk centre, not a translation of the first coordinate. Where the
diameter is clear, the centre is the white/black equator: panel a is dark below
that boundary and panel b is dark above it. Where a curve breaks the diameter,
the centre is the peak of a half-disk template of radius 22 px. The axis maps
below apply to panel-local coordinates. Stored values are the mean of both
converted reads, rounded to two significant figures. Read uncertainty is half
the absolute difference of the two log10 activities when the ordinates differ.
When the two centres share an ordinate, the uncertainty is the dex width of
half a pixel on the activity axis. The key is present and positive on every
marker. Panel-b marker 2 is the half-disk under the printed curve label 5.
Both reads are the white/black equator of that disk, page (2581, 2966), below the
curve that crosses the fill. Marker 3's click sits in the same fill. The next
half-disks are markers 1 and 4, so marker 3 is not a separate symbol and its
read is unchanged.

| Panel | x tick labels and pixel x | Fitted x map | y tick labels and pixel y | Fitted y map |
| --- | --- | --- | --- | --- |
| a | 60: 756; 70: 1068; 80: 1380; 90: 1692 | FeO mol% = 60 + (px − 756) / 31.2 | 0.0: 1057; 0.2: 850.5; 0.4: 645.5; 0.6: 441; 0.8: 235; 1.0: 29 | aFeO = (1057 − py) / 1028 |
| b | 60: 331; 70: 643; 80: 954; 90: 1266; 100: 1578 | FeO mol% = 60 + (px − 331) / 31.2 | 0.0: 1052; 0.2: 845.5; 0.4: 640.5; 0.6: 435; 0.8: 230; 1.0: 26 | aFeO = (1052 − py) / 1026 |

The Figure 2 caption assigns marker symbol 1 to Ban-Ya et al. (1980) in panel a and symbol 2 to Fujita et al. (1968) in panel b. The caption labels panel b 1873 K; the prose reports the Fujita measurement at 1833 K. Each panel-b point stores the reported 1833 K identity temperature. The captioned plot condition remains separately stored as `point_conditions.plotted_panel_temperature_K = 1873` K.

| Marker | Read 1 pixel (x,y) | Read 1 (FeO mol%, aFeO) | Read 2 crop; page origin; pixel | Read 2 (FeO mol%, aFeO) | Stored (FeO mol%, aFeO) | Read uncertainty (dex) |
| --- | --- | --- | --- | --- | --- | ---: |
| a1 | (733, 576) | (59.2628, 0.4679) | lmax-r2-a01; (1011, 2896); (21, 30) | (59.2308, 0.4679) | (59, 0.47) | 0.000451 |
| a2 | (800, 528) | (61.4103, 0.5146) | lmax-r2-a02; (1052, 2860); (48, 18) | (61.4103, 0.5146) | (61, 0.51) | 0.00041 |
| a3 | (970, 478) | (66.859, 0.5632) | lmax-r2-a03; (1240, 2773); (34, 55) | (66.9872, 0.5632) | (67, 0.56) | 0.000375 |
| a4 | (1022, 464) | (68.5256, 0.5768) | lmax-r2-a04; (1262, 2774); (57, 37) | (68.4295, 0.5798) | (68, 0.58) | 0.001126 |
| a5 | (1065, 437) | (69.9038, 0.6031) | review-p1-a-5; (1291, 2711); (74, 72) | (69.9038, 0.607) | (70, 0.61) | 0.0014 |
| a6 | (1112, 385) | (71.4103, 0.6537) | review-p1-a-6; (1345, 2670); (69, 68) | (71.4744, 0.6508) | (71, 0.65) | 0.000965 |
| a7 | (1152, 384) | (72.6923, 0.6547) | review-p1-a-7; (1392, 2655); (57, 78) | (72.5962, 0.6556) | (73, 0.66) | 0.000298 |
| a8 | (1269, 333) | (76.4423, 0.7043) | review-p1-a-8; (1491, 2615); (80, 69) | (76.5064, 0.7033) | (76, 0.7) | 0.000309 |
| a9 | (1294, 329) | (77.2436, 0.7082) | lmax-r2-a09; (1576, 2617); (19, 60) | (77.2756, 0.7101) | (77, 0.71) | 0.000582 |
| a10 | (1354, 281) | (79.1667, 0.7549) | review-p1-a-10; (1590, 2560); (62, 70) | (79.1026, 0.7558) | (79, 0.76) | 0.000259 |
| a11 | (1412, 271) | (81.0256, 0.7646) | lmax-r2-a11; (1672, 2599); (41, 20) | (81.0577, 0.7665) | (81, 0.77) | 0.000539 |
| a12 | (1480, 232) | (83.2051, 0.8025) | review-p1-a-12; (1705, 2508); (78, 73) | (83.3013, 0.8035) | (83, 0.8) | 0.00027 |
| a13 | (1566, 166) | (85.9615, 0.8667) | review-p1-a-13; (1798, 2453); (68, 61) | (85.9615, 0.8687) | (86, 0.87) | 0.000501 |
| a14 | (1629, 175) | (87.9808, 0.858) | lmax-r2-a14; (1874, 2467); (55, 56) | (87.9808, 0.8599) | (88, 0.86) | 0.00048 |
| a15 | (1656, 126) | (88.8462, 0.9056) | lmax-r2-a15; (1932, 2441); (24, 35) | (88.8462, 0.9056) | (89, 0.91) | 0.000233 |
| a16 | (1675, 107) | (89.4551, 0.9241) | lmax-r2-a16; (1932, 2414); (43, 43) | (89.4551, 0.9241) | (89, 0.92) | 0.000229 |
| a17 | (1731, 69) | (91.25, 0.9611) | lmax-r2-a17; (1997, 2403); (35, 16) | (91.2821, 0.9611) | (91, 0.96) | 0.00022 |
| a18 | (1813, 45) | (93.8782, 0.9844) | lmax-r2-a18; (2067, 2345); (46, 49) | (93.8782, 0.9854) | (94, 0.98) | 0.00022 |
| b1 | (331, 581) | (60, 0.4591) | review-p1-b-1; (2464, 2849); (68, 84) | (60.0641, 0.4561) | (60, 0.46) | 0.001424 |
| b2 | (382, 617) | (61.6346, 0.4240) | lmax-r2-b02; (2535, 2902); (46, 64) | (61.6346, 0.4240) | (62, 0.42) | 0.000499 |
| b3 | (379, 592) | (61.5385, 0.4483) | review-p1-b-3; (2501, 2857); (77, 87) | (61.5385, 0.4454) | (62, 0.45) | 0.001409 |
| b4 | (431, 597) | (63.2051, 0.4435) | lmax-r2-b04; (2572, 2882); (57, 64) | (63.1731, 0.4435) | (63, 0.44) | 0.000477 |
| b5 | (453, 495) | (63.9103, 0.5429) | review-p1-b-5; (2589, 2782); (66, 63) | (64.0064, 0.5419) | (64, 0.54) | 0.0004 |
| b6 | (517, 443) | (65.9615, 0.5936) | review-p1-b-6; (2635, 2716); (85, 78) | (66.0897, 0.5916) | (66, 0.59) | 0.000733 |
| b7 | (577, 442) | (67.8846, 0.5945) | lmax-r2-b07; (2738, 2763); (38, 29) | (67.8846, 0.5936) | (68, 0.59) | 0.000329 |
| b8 | (659, 347) | (70.5128, 0.6871) | review-p1-b-8; (2791, 2617); (68, 81) | (70.5449, 0.6852) | (71, 0.69) | 0.000601 |
| b9 | (923, 224) | (78.9744, 0.807) | review-p1-b-9; (3062, 2505); (60, 70) | (78.9744, 0.8051) | (79, 0.81) | 0.000512 |
| b10 | (923, 265) | (78.9744, 0.7671) | lmax-r2-b10; (3070, 2568); (52, 52) | (78.9744, 0.7612) | (79, 0.76) | 0.001677 |
| b11 | (985, 195) | (80.9615, 0.8353) | review-p1-b-11; (3113, 2473); (73, 72) | (81.0256, 0.8343) | (81, 0.83) | 0.00026 |
| b12 | (1043, 181) | (82.8205, 0.8489) | lmax-r2-b12; (3218, 2472); (27, 60) | (82.9167, 0.847) | (83, 0.85) | 0.000487 |
| b13 | (1102, 141) | (84.7115, 0.8879) | review-p1-b-13; (3219, 2416); (83, 77) | (84.7436, 0.885) | (85, 0.89) | 0.00071 |
| b14 | (1120, 187) | (85.2885, 0.8431) | lmax-r2-b14; (3274, 2491); (45, 45) | (85.2885, 0.8431) | (85, 0.84) | 0.000251 |
| b15 | (1142, 165) | (85.9936, 0.8645) | review-p1-b-15; (3273, 2437); (66, 76) | (85.9295, 0.8655) | (86, 0.86) | 0.000251 |
| b16 | (1292, 131) | (90.8013, 0.8977) | lmax-r2-b16; (3447, 2460); (44, 22) | (90.8013, 0.8957) | (91, 0.9) | 0.000484 |
| b17 | (1347, 82) | (92.5641, 0.9454) | review-p1-b-17; (3467, 2351); (81, 83) | (92.6282, 0.9425) | (93, 0.94) | 0.000667 |
| b18 | (1381, 112) | (93.6538, 0.9162) | lmax-r2-b18; (3518, 2425); (62, 38) | (93.6538, 0.9142) | (94, 0.92) | 0.000475 |

All 36 activity values and printed FeO mol% coordinates are retained in the
extract with their independent read centres and the new crop provenance. The migrator identity marks
composition unknown and typed-refuses these rows because no mole-percent
composition basis exists. The printed percentage is not converted to a mole
fraction. All Figure 2 model curves 3–8 have their panel identity temperatures
bound in `values.T_K`; no numeric points are sampled.

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
- The text reports CaFeO2 formed in the 1173–1523 K sintering region, citing
  Srikanth et al. (2005) and Bazhin et al. (2017). This is retained as an
  unscored thermodynamic-example context record with its source sentence,
  locator, attribution, and reason no numeric observation is scored.
- Figure 1 is a compiled phase diagram; no discrete marker values are printed
  or digitized. It is retained as a figure-read schema-gap candidate in this
  audit; its phase-boundary curves were not sampled.
- Figure 3 shows mixing-energy curves at 1673 and 1873 K. The prose minimum
  range (−6.5 to −9.0 kJ/mol) and all six attributed model curves are retained
  as unscored species context records with source sentence or caption,
  locator, attribution, and the reason no numeric observation is scored. No
  delta_fG surrogate or curve samples were created.
