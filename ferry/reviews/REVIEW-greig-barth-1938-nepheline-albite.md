# Fidelity review (batch4 A, first review): corpus extract greig-barth-1938-nepheline-albite @ aafa4ace5521e682fca45f5ae985783f6a3b58bc

Reviewer: regolith-empirical corpus review seat, 2026-10-05 (~21:00 ET). This is a read-only review. Nothing tracked was changed.
- Corpus: a sparse worktree of `hunt/greig-barth-1938-nepheline-albite`, detached at `aafa4ace5521e682fca45f5ae985783f6a3b58bc`. It was created with the recipe in SEAT-COMMON (only this sid's raw/ and text/ plus every sidecar), and `git fetch origin hunt/<sid>` confirmed the tip is aafa4ace. No `text/<sid>/` exists (the PDF is an image-only scan).
- Green: `~/ci-scratch/regolith-green-ro` (detached at 61ec839da3ba288c5df4a80f6d3ef142bd8ab461, used read-only) with `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python`. **`engines/engines.local.toml` EXISTS** in that checkout.
- Diff under review (origin/main...tip): extracts/<sid>.yaml (1460 lines), ledger/<sid>.yaml, tables/<sid>/t1–t5.csv plus five provenance yamls. The author's report was treated as a claim to test.

## 0. Verdict summary

VERDICT ON COMMIT: FIX-FIRST

rows checked 104, mismatches 8, printed numbers not carried 9

(Rows checked = all 104 table rows, every cell, digit by digit, against images at 220 dpi plus 400/600 dpi crops. The 19 context records were also checked statement by statement. Of the 8 mismatches, 4 are table cells (1 composition binding, 1 phase cell, 2 reworded result texts) and 4 are context statements (1 reversed directional statement, 1 misstated preparation fact, 2 dropped "about" qualifiers). Figure-only annotations are listed separately in section 3 and not counted.)

The numbers and temperatures in Tables I, II, III and V match the print. Table IV has one run bound to the wrong composition, and Table V has one wrong original-condition cell. One context note reverses the paper's albite/carnegieite kinetics contrast. Several experiment facts and directional statements in the text are not carried. The machine gates pass (section 6).

## 1. Pages rendered and read; every table, equation and numeric statement

All 20 PDF pages (published pp. 93–112) were rendered with `pdftoppm -r 220` and read. The table pages (pdf 6, 7, 11, 12, 17) were also rendered at 400 dpi and read in crops; Table II was rotated to read upright. Table V's 33.5 wt% rows and the Fig. 1 labels were also read at 500/600 dpi. pdf_page_index in the extract is 0-based (p.93 = 0), and every locator was checked on that basis.

| page | printed item | carried? |
|---|---|---|
| 93 | Join at atmospheric pressure, range "about 500° C."; fn 1 Day–Sosman scale, ITS "probably always slightly higher" | yes (experiment_facts) |
| 94 | Fig. 1 equilibrium diagram; labels 1526, 1280, 1254, 1118, 1068 °C | figure_only (labels 1280/1254 not carried, see §3 advisory) |
| 94 | Quartz: residue after HF+H2SO4 evaporation < 0.04 %; converted to cristobalite | number yes; fact misstated (M6); cristobalite not carried |
| 95 | Alumina from distilled chloride by double precipitation with HCl gas and ignition of AlCl3·6H2O | **not carried** (report claims it is) |
| 95 | Soda as sodium carbonate monohydrate; analysis by E. G. Zies: SO3 <0.001, Cl <0.001, SiO2 <0.002, Al2O3+Fe2O3 0.003, CaO 0.008 %, MgO none | numbers yes; monohydrate and analyst not carried |
| 95 | Soda–silica preparation "about 35 per cent soda 65 per cent silica"; electric furnace max 1050 °C; several analyses agreed with synthesis, "no detectable loss of soda" | 35/65 carried without "about" (M7a); 1050 yes; analyses agreement **not carried** |
| 95 | Crushed glass heated in air at 900°, loss = water, later loss = soda; "With increase in silica the loss during heating decreases"; 10 g albite glass no loss 3 h at 1650–1675°; fn 3 Meker burner 900–1000 °C weight gain | yes |
| 96 | Composition projection onto the binary join (two sentences) | yes (verbatim) |
| 96–97 | Day & Allen traces of melting at 1100°, trials ≤ 1 day; Bowen glass after 1 h at 1105°, no change after 2 h at 1089°, solidus 1100° ± 10°; G&B 1118° higher, Bowen material suspected inhomogeneous | yes (one record located p.97 that spans pp.96–97) |
| 98 | Table I (15 rows) | yes, 2 text mismatches (M3, M4) |
| 98 | "A bracket 1113°-1122° is established by these runs." | **not carried** (N1) |
| 98 | "The melting temperature lies between 1115° and 1120°, probably at 1118°." | yes |
| 98 | fn 7 regulator (White & Adams type, adapted by Roberts for a.c.), Leeds & Northrup recording potentiometer | yes |
| 98→100 | Crystals grown in sealed silica-glass tubes under a small water-vapour pressure; Pt-foil crucible; evacuated tube; water source gibbsite or a rhyolitic glass "containing about 3 per cent of water"; silica volatilizes from tube walls, "no detectable contamination of the charge inside its platinum container" (fn 8) | only a generic phrase is carried; gibbsite/rhyolite, the **3 %** (N2) and the no-contamination statement are not carried |
| 99 | Table II (34 rows); "The melting point is closer to 1527° than to 1524°, therefore at 1526°" | yes, all 34 rows match |
| 100 | Materials dried in air close to the melting temperature for 24–48 h | yes, but located p.102 (L1) |
| 100 | Merwin: change in natural albite near 900° within a few hours; crystals grown at 800°, 930° and 1000° gave no differences | **not carried** (N3, N4) |
| 100 | Albite persists above melting; after four days at 1126° remnants persist; melting at surfaces; crystals in the 91.6 % liquid at 1115° behave like crystals in their own liquid; quartz analogy; fn 10 quartz heated to about 1800 °C, outer glass, inner crystalline | 1126/4 days yes; **91.6 %/1115° statement (N5) and fn 10 1800 °C (N6) not carried** |
| 101 | Carnegieite melting method; approach to equilibrium of carnegieite contrasted with albite; fn 11 typical charge 0.035 g, Pt foil ~0.06 g, 6 × 8 mm; fn 12 Bowen heating curves 1525°, 1527°, placed 1526° | numbers yes; **direction reversed** (M5) |
| 102 | Liquidus method; slow growth for 74.3, 84.2, 91.6 % albite | yes |
| 103 | Eutectic 76 % albite; Table III (4 rows); 1063° in 48 h not demonstrated below eutectic; 1068° ± 5° | yes, all 4 rows match |
| 104 | Table IV (17 rows); solidus curves "not located as well as the corresponding liquidi" | 1 binding mismatch (M1); qualifications yes |
| 105 | Residual liquid, spherulitic crystallization, voids (fn 13), refractive index | yes (qualitative) |
| 106 | 25.3/33.5/40.2 % for 3 days at 1080°; X-ray (Posnjak); solidus tentatively through 33 % at 1080°; 600° crystallization; glasses of 90, 95, 97, 98 **and 100** % albite | yes except the 100 % glass (N7) |
| 107 | Sealed-tube crystallization then two months in air at about 1035°; Mo Kα X-ray spectrograms, no differences; a few per cent nephelite undetectable; 1072°–1075° 3 d / +8 d; 1079° 15 d; limit 1075° between 95 and 97, i.e. 96 % | numbers yes; "about" dropped on 1035 (M7b); **Mo Kα and the detection-limit statement not carried** |
| 108 | All glasses except **the 33.5 per cent glass** crystallized completely to carnegieite and to nephelite; carnegieite forms first, nephelite only by inversion, slow; pure end member 1259°–1249°; Bowen 1245°–1252°; inversion rises with silica, occurs over a range | 1249/1259/1245/1252 and the directional statement yes; **33.5 % exception (N8)** and the carnegieite-first statement not carried |
| 108 fn 15 | Bowen: almost complete nephelite→carnegieite conversion in one hour at 1252°, partial carnegieite→nephelite in one hour at 1245° | **not carried** (N9) |
| 108–109 | Jadeite Na2O·Al2O3·4SiO2, USNM 94303 (Foshag); indices β 1.657 (variation < 0.002), γ 1.668, α 1.650; decomposition from 800 °C; 1015° → glass + large nephelite | yes (α stored as float 1.65; print 1.650) |
| 109 | Table V (34 rows) | 1 phase-cell mismatch (M2) |
| 110 | Fig. 2 ternary (hypothetical, straight-line interpolation); jadeite conversion → not stable | figure_only yes; conclusion yes |
| 111 | Jadeite unstable, high-pressure mineral; in both systems inversion temperature **rises with increased feldspar content**; **slight solubility of nephelite, carnegieite in the feldspar**; Bowen: nephelite birefringence decreased, became zero, then increased with anorthite (optically positive); here nephelites all optically negative | **directional statements not carried** (no "feldspar", "solubility" or "optically" anywhere in the extract; the report's p.111 bullet also words the solubility the wrong way round) |
| 112 | Ternary construction, tie lines, open/filled circles | figure context (adequate) |

No equations are printed in the paper.

## 2. Row-by-row check of every transcribed table row (104/104 rows, every cell)

The CSVs and the extract's `values.rows` are identical: 0 cell differences across all 104 rows, checked by script. The check below therefore covers both. Blank cells in the print are dittos of the cell above, and the CSV resolves every blank that way correctly except Table IV row 2.

#### Table I — p.98 (pdf 6) — 15 rows

| table | row | csv (wt% albite / °C / time / original → after) | print | match |
|---|---|---|---|---|
| 1 | 1 | — / 1113 / 3 days / Crystals in glass → Crystals have grown | same | yes |
| 1 | 2 | — / 1113 / 3 days / Crystals → No change | same | yes |
| 1 | 3 | — / 1116 / 2 days / Crystals in glass → Can see no change | same | yes |
| 1 | 4 | — / 1122 / 1 day / Crystals in glass from run at 1113 above → Can see no change | same | yes |
| 1 | 5 | — / 1122 / 2 days / Crystals in glass from run at 1113 above → Crystals are smaller | same | yes |
| 1 | 6 | — / 1122 / 3 days / Crystals in glass from run at 1113 above → Nearly all glass | same | yes |
| 1 | 7 | — / 1122 / 1 day / Crystals → A little interstitial glass | same | yes |
| 1 | 8 | — / 1122 / 2 days / Crystals → Glass increasing | same | yes |
| 1 | 9 | — / 1126 / 1 day / Crystals in glass → All glass | same | yes |
| 1 | 10 | — / 1126 / 1 day / Crystals → Films of glass between crystals throughout the charge | MISMATCH: print "Films of glass between the crystals throughout the charge" (word "the" dropped) | **NO** |
| 1 | 11 | — / 1126 / 2 days / Crystals → Charge now contains only a few per cent of crystals; smaller crystals have melted | MISMATCH: print "Charge now contains only a few per cent of crystals. The smaller crystals have melted." (reworded) | **NO** |
| 1 | 12 | — / 1126 / 3 days / Crystals → Crystals have decreased | same | yes |
| 1 | 13 | — / 1126 / 4 days / Crystals → Traces only of crystals | same | yes |
| 1 | 14 | — / 1115 / 7 days / Crystals in glass → Crystals have grown | same | yes |
| 1 | 15 | — / 1120 / 7 days / Crystals in glass → All glass | same | yes |

#### Table II — p.99 (pdf 7, rotated) — 34 rows

| table | row | csv (wt% albite / °C / time / original → after) | print | match |
|---|---|---|---|---|
| 2 | 1 | 0.0 / 1524 / 30 min / Glass → Glass + some carnegieite | same | yes |
| 2 | 2 | 0.0 / 1524 / 30 min / Carnegieite → Carnegieite | same | yes |
| 2 | 3 | 0.0 / 1527 / 30 min / Glass → Glass | same | yes |
| 2 | 4 | 0.0 / 1527 / 30 min / Carnegieite → Glass with trace of carnegieite | same | yes |
| 2 | 5 | 0.0 / 1527 / 60 min / Carnegieite → Glass | same | yes |
| 2 | 6 | 16.0 / 1476 / 60 min / Glass → Glass with carnegieite | same | yes |
| 2 | 7 | 16.0 / 1480 / 60 min / Glass → Glass | same | yes |
| 2 | 8 | 16.0 / 1480 / 60 min / Glass + carnegieite → Glass | same | yes |
| 2 | 9 | 33.5 / 1374 / 60 min / Glass → Glass + carnegieite | same | yes |
| 2 | 10 | 33.5 / 1374 / 60 min / Glass + carnegieite → Glass + carnegieite | same | yes |
| 2 | 11 | 33.5 / 1374 / 60 min / Glass + carnegieite → Glass | same | yes |
| 2 | 12 | 40.2 / 1307 / 120 min / Glass + carnegieite → Glass + carnegieite | same | yes |
| 2 | 13 | 40.2 / 1307 / 120 min / Glass → Glass + carnegieite | same | yes |
| 2 | 14 | 40.2 / 1316 / 75 min / Glass + carnegieite → Glass | same | yes |
| 2 | 15 | 40.2 / 1316 / 75 min / Glass → Glass | same | yes |
| 2 | 16 | 41.7 / 1289 / 120 min / Glass → Glass + carnegieite | same | yes |
| 2 | 17 | 41.7 / 1297 / 120 min / Glass → Glass + trace carnegieite | same | yes |
| 2 | 18 | 41.7 / 1297 / 120 min / Glass + nephelite → Glass + trace carnegieite | same | yes |
| 2 | 19 | 45.1 / 1256 / 150 min / Glass + nephelite → Nephelite crystals grown | same | yes |
| 2 | 20 | 45.1 / 1260 / 150 min / Glass + nephelite → No definite change | same | yes |
| 2 | 21 | 45.1 / 1264 / 150 min / Glass + nephelite → Glass | same | yes |
| 2 | 22 | 56.4 / 1190 / 17 hrs / Glass + nephelite → Nephelite crystals grown larger | same | yes |
| 2 | 23 | 56.4 / 1190 / 5 hrs / Glass + nephelite → Nephelite almost all gone | same | yes |
| 2 | 24 | 74.3 / 1072 / 48 hrs / Glass + nephelite → Nephelite has grown | same | yes |
| 2 | 25 | 74.3 / 1080 / 48 hrs / Glass + nephelite → Can see no change | same | yes |
| 2 | 26 | 74.3 / 1089 / 24 hrs / Glass + nephelite → Nephelite all gone | same | yes |
| 2 | 27 | 84.2 / 1088 / 72 hrs / Glass + albite → Albite grown | same | yes |
| 2 | 28 | 84.2 / 1097 / 48 hrs / Glass + albite → Albite slightly smaller | same | yes |
| 2 | 29 | 84.2 / 1101 / 48 hrs / Glass + albite → All glass | same | yes |
| 2 | 30 | 91.6 / 1102 / 24 hrs / Glass + albite → Albite grown | same; print "1102°" (degree mark only, csv temperature_as_printed "1102") | yes |
| 2 | 31 | 91.6 / 1110 / 96 hrs / Glass + albite → No change seen | same | yes |
| 2 | 32 | 91.6 / 1115 / 19 hrs / Glass + albite → Albite nearly all melted | same | yes |
| 2 | 33 | 100.0 / 1115 / 168 hrs / Glass + albite → Albite crystals grew | same | yes |
| 2 | 34 | 100.0 / 1120 / 168 hrs / Glass + albite → All glass | same | yes |

#### Table III — p.103 (pdf 11) — 4 rows

| table | row | csv (wt% albite / °C / time / original → after) | print | match |
|---|---|---|---|---|
| 3 | 1 | 74.3 / 1063 / 48 hrs / Crystalline → No sign of melting | same | yes |
| 3 | 2 | 74.3 / 1072 / 24 hrs / Crystalline → Mostly glass | same | yes |
| 3 | 3 | 84.2 / 1063 / 48 hrs / Crystalline → No sign of melting | same | yes |
| 3 | 4 | 84.2 / 1072 / 24 hrs / Crystalline → Good deal of glass | same | yes |

#### Table IV — p.104 (pdf 12) — 17 rows

| table | row | csv (wt% albite / °C / time / original → after) | print | match |
|---|---|---|---|---|
| 4 | 1 | 5.2 / 1459 / 13 hrs / Carnegieite → Carnegieite with a little interstitial glass | same | yes |
| 4 | 2 | 10.9 / 1447 / 21 hrs / Glass → Carnegieite; no glass found | MISMATCH: composition cell is blank (ditto) -> 5.2 wt%; 10.9 is printed on the 1439 row; csv binds 1447 °C run to 10.9 | **NO** |
| 4 | 3 | 10.9 / 1439 / 9 hrs / Carnegieite → Carnegieite with interstitial glass | same | yes |
| 4 | 4 | 15.0 / 1288 / 15 hrs / Glass → Carnegieite with traces of glass | same | yes |
| 4 | 5 | 15.8 / 1317 / 16 hrs / Carnegieite → Carnegieite with glass | same | yes |
| 4 | 6 | 17.0 / 1288 / 45 hrs / Glass → Carnegieite with glass | same | yes |
| 4 | 7 | 17.0 / 1268 / 21 hrs / Nephelite → Nephelite + glass | same | yes |
| 4 | 8 | 17.0 / 1259 / 23 hrs / Nephelite → Nephelite + trace of glass in rare grains | same | yes |
| 4 | 9 | 17.0 / 1250 / 45 hrs / Nephelite → Nephelite; no glass found | same; print has "." + capital ("X. No glass found"), csv uses ";" (punctuation only) | yes |
| 4 | 10 | 20.0 / 1268 / 21 hrs / Nephelite → Nephelite + glass | same | yes |
| 4 | 11 | 20.0 / 1259 / 23 hrs / Nephelite → Nephelite + glass | same | yes |
| 4 | 12 | 20.0 / 1250 / 45 hrs / Nephelite → Nephelite + glass | same | yes |
| 4 | 13 | 20.0 / 1226 / 42 hrs / Nephelite → Nephelite; no glass found | same; print has "." + capital ("X. No glass found"), csv uses ";" (punctuation only) | yes |
| 4 | 14 | 25.3 / 1200 / 24 hrs / Nephelite → Nephelite + glass | same | yes |
| 4 | 15 | 25.3 / 1200 / 24 hrs / Glass → Nephelite + glass | same | yes |
| 4 | 16 | 25.3 / 1160 / 48 hrs / Nephelite → Nephelite | same | yes |
| 4 | 17 | 25.3 / 1160 / 48 hrs / Glass → Nephelite | same | yes |

#### Table V — p.109 (pdf 17) — 34 rows

| table | row | csv (wt% albite / °C / time / original → after) | print | match |
|---|---|---|---|---|
| 5 | 1 | 0.3 / 1249 / 24 hrs / Nephelite → Nephelite | same | yes |
| 5 | 2 | 0.3 / 1249 / 24 hrs / Carnegieite → Carnegieite | same | yes |
| 5 | 3 | 0.3 / 1249 / 48 hrs / Nephelite → Nephelite | same | yes |
| 5 | 4 | 0.3 / 1249 / 48 hrs / Carnegieite → Carnegieite with a little nephelite | same | yes |
| 5 | 5 | 0.3 / 1268 / 23 hrs / Nephelite → Carnegieite | same | yes |
| 5 | 6 | 0.3 / 1259 / 24 hrs / Nephelite → Nephelite | same | yes |
| 5 | 7 | 0.3 / 1259 / 48 hrs / Nephelite → Nephelite + carnegieite | same | yes |
| 5 | 8 | 5.2 / 1250 / 28 hrs / Nephelite → Nephelite | same | yes |
| 5 | 9 | 5.2 / 1250 / 28 hrs / Carnegieite → Carnegieite + a little nephelite | same | yes |
| 5 | 10 | 5.2 / 1259 / 48 hrs / Nephelite → Nephelite | same | yes |
| 5 | 11 | 5.2 / 1259 / 48 hrs / Carnegieite → Carnegieite | same | yes |
| 5 | 12 | 5.2 / 1269 / 24 hrs / Nephelite → Nephelite | same | yes |
| 5 | 13 | 5.2 / 1269 / 24 hrs / Carnegieite → Carnegieite | same | yes |
| 5 | 14 | 5.2 / 1277 / 24 hrs / Nephelite → Nephelite | same | yes |
| 5 | 15 | 5.2 / 1277 / 24 hrs / Carnegieite → Carnegieite | same | yes |
| 5 | 16 | 5.2 / 1277 / 48 hrs / Nephelite → Nephelite + carnegieite | same | yes |
| 5 | 17 | 5.2 / 1285 / 4 hrs / Nephelite → Nephelite + carnegieite | same | yes |
| 5 | 18 | 10.9 / 1269 / 24 hrs / Nephelite → Nephelite | same | yes |
| 5 | 19 | 10.9 / 1269 / 24 hrs / Carnegieite → Carnegieite + rare nephelite | same | yes |
| 5 | 20 | 10.9 / 1269 / 48 hrs / Carnegieite → Carnegieite + increase in nephelite | same | yes |
| 5 | 21 | 10.9 / 1277 / 24 hrs / Nephelite → Nephelite | same | yes |
| 5 | 22 | 10.9 / 1277 / 48 hrs / Nephelite → Nephelite + carnegieite | same | yes |
| 5 | 23 | 15.8 / 1259 / 48 hrs / Nephelite → Nephelite + trace glass | same | yes |
| 5 | 24 | 15.8 / 1269 / 24 hrs / Carnegieite → Carnegieite + rare nephelite | same | yes |
| 5 | 25 | 15.8 / 1269 / 48 hrs / Carnegieite → Carnegieite + increase in nephelite | same | yes |
| 5 | 26 | 15.8 / 1273 / 22 hrs / Carnegieite → Carnegieite + rare nephelite + trace glass | same | yes |
| 5 | 27 | 15.8 / 1277 / 24 hrs / Nephelite → Nephelite + trace glass | same | yes |
| 5 | 28 | 15.8 / 1277 / 24 hrs / Carnegieite → Carnegieite + trace glass | same | yes |
| 5 | 29 | 15.8 / 1277 / 48 hrs / Nephelite → Nephelite + trace glass | same | yes |
| 5 | 30 | 15.8 / 1285 / 24 hrs / Nephelite → Nephelite + glass + a little carnegieite | same | yes |
| 5 | 31 | 33.5 / 1268 / 4 hrs / Carnegieite → Carnegieite + glass + nephelite | MISMATCH: original condition printed "Carnegieite + glass" (wrapped onto 2nd line); csv has "Carnegieite" | **NO** |
| 5 | 32 | 33.5 / 1273 / 22 hrs / Carnegieite + glass → Carnegieite + glass + nephelite | same | yes |
| 5 | 33 | 33.5 / 1285 / 4 hrs / Nephelite → Nephelite + glass | same | yes |
| 5 | 34 | 33.5 / 1285 / 24 hrs / Nephelite → Nephelite + carnegieite + glass | same | yes |

Why M1 is a binding error, not a reading choice: everywhere else in Table IV a blank composition cell means "same as above", e.g. the 17.0 rows at 1259/1250 and the 20.0 and 25.3 groups. 10.9 is printed on the 1439 °C row, not the 1447 row. The physics agrees. Bound to 5.2 wt%, the pair "1459 °C: a little interstitial glass / 1447 °C: no glass found" brackets the carnegieite solidus at 5.2 wt%. Bound to 10.9 wt% as in the csv, 10.9 wt% would have no glass at 1447 °C but interstitial glass at 1439 °C, which contradicts itself.

## 3. Experiment facts, typed absences, context statements

Checked against the page, quoting the sentence relied on:
- method `quench_equilibration`: acceptable. Runs at constant temperature, then "examining the product with the microscope" (p.96). On p.101: "is quenched shortly before the charge reaches this temperature".
- atmosphere "Atmospheric pressure" (p.93, "at atmospheric pressure"): correct. `background_pressure_Pa` typed `unknown/not_published`: correct, since no numeric pressure is printed. The sealed-tube preparations ran under "a small pressure of water vapor" (p.98), which is also unquantified and so correctly not given a number.
- cell_material and liner `not_applicable`, with the Pt-foil note: acceptable (p.98: "Powdered albite glass was placed in a crucible of platinum foil"). Orifice, pressure calibration and activities `not_applicable`: correct, as the paper has no effusion, mass-spectrometry or activity data.
- temperature_measurement / temperature_calibration are named located mappings (fn 7, p.98): correct. No sensor type (thermocouple or other) is printed, and the extract invents none. Temperature scale (fn 1, p.93): correct direction (ITS "probably always slightly higher").
- heating apparatus "Electric furnace" (pp.95, 98): correct.
- Purity numbers (p.95): all six match digit by digit. `quartz_purification_residue_pct_max <0.04` is correct and correctly located p.94.
- Eutectic (76 %, 1068 ± 5, p.103), albite bracket (1115/1120/1118, p.98), carnegieite 1526 and fn 11/12 numbers (p.101), inversion 1249/1259 and Bowen 1245/1252 (p.108), jadeite indices/800/1015/94303 (pp.108–109), solid-solution limit 96 %/1075/95–97/1072–1075/1079/3, 8, 15 d/2 months (p.107): all match.
- p.106 claims (1080 °C, 3 days, 25.3/33.5/40.2, 25 %, 33 %, 600 °C): match.

Context mismatches:
- **M5 (p.101, `greig_barth_1938_carnegieite_melting_context.values.note`), REVERSED DIRECTION.** Extract: "carnegieite equilibrium is approached slowly just above melting, in contrast to the rapid approach for albite". Print: "Although the rate of approach to equilibrium, when carnegieite is heated just above its melting temperature, is slow enough so that it can be held there for an appreciable time without melting completely, it is in marked contrast with the rate at which equilibrium is approached when albite is so treated. So rapid indeed is this approach that Bowen was able to determine the melting point by making heating curves". So carnegieite approaches equilibrium RAPIDLY, and albite is the sluggish phase: it persists four days at 1126 °C (p.100), and Day & Allen could not get albite melting from heating curves (p.96). The paragraph closes with "a difference very similar to that found by Day and Allen between the behaviors of anorthite and albite". The author's report repeats the reversal (p.101 bullet).
- **M6 (p.94, `material_purity_and_preparation.values.quartz_cleaning_as_printed`).** Extract: "Crushed quartz was cleaned with HF and H2SO4; residue less than 0.04%." Print: "Silica, a crushed quartz, which after evaporation with hydrofluoric and sulphuric acids leaves a residue of less than 0.04 per cent, was converted to cristobalite by firing it at a high temperature." The HF/H2SO4 step is a purity test (the non-volatile residue), not a cleaning step, and the field claims to be "as printed". The cristobalite conversion is dropped.
- **M7a (p.95, `batch_preparation_composition_as_printed`):** "35% soda and 65% silica" against the print's "about 35 per cent soda 65 per cent silica". **M7b (p.107, `solid_solution_limit_context.values.preparation_temperatures_C: [1035]`):** the print says "at about 1035°". Both drop the approximation qualifier.

Figure-only (advisory, not counted): Fig. 1 (p.94) prints axis annotations 1280 and 1254 (the top and bottom of the carnegieite + nephelite field on the left axis). They appear nowhere in the text or tables. Reading a printed label is not digitising, so carrying them as figure_only context labels (no curve values) is recommended.

Source-internal misreferences that should be recorded as notes (the extract records them nowhere):
- p.100 cites "the data of Table IV" for albite persisting four days at 1126°. The data are in Table I.
- p.100 cites "(see Table I)" for the 91.6 % liquid at 1115°. That is Table II row 32.
- p.101 says Bowen's carnegieite heating-curve temperature agrees with "the runs listed in Table I". Carnegieite is in Table II rows 1–5; Table I is albite.

## 4. Activities, standard states, reduced rows

The paper has no activities, activity coefficients, vapour pressures or ion currents. There are no reduced or model rows, and none are claimed. method_class values present: `measured_tabulated` (5 table records) and `figure_only`, both appropriate.

## 5. Citation, licence, sha, paths

- Sidecar sha256 `6d1128fcea91dd56e98c9562b58c61255b772903cd5fe7c2bb76d12b06609f88` equals `shasum -a 256` of the PDF in the worktree and of the box copy. 3,334,884 bytes, 20 pages. The extract's `source.sha256` is the same.
- The citation, DOI 10.2475/001c.120516 and Am. J. Sci. s5-35A, 93–112 were confirmed against the AJS landing page. Licence as recorded by intake ("DOWNLOADED OA").
- `rg "/Users/|/private/"` over the extract, ledger, tables/<sid>/ and the sidecar found no matches.
- Ledger `completeness.claims: true` overstates completeness given N1–N9 and the missing p.111 statements. Re-evaluate it after the fix.

## 6. Machine gates (green 61ec839da, ~/ci-scratch/regolith-green-ro, read-only)

- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/greig-barth-1938-nepheline-albite.yaml)` then `finalize()`: both complete. works 1, experiments 1, observations 0, registry_issues 0, **hard issues after finalize 0**. There are 19 context rows, and all **104** table rows survive in the migrated context payload.
- `evidence_for()`: `measured_tabulated` and `figure_only` both resolve to known EvidenceClass values, with no unknown.
- `PYTHONPATH=~/ci-scratch/regolith-green-ro .venv/bin/python tools/validate_literature_extracts.py --check-fidelity-match <worktree>/extracts/greig-barth-1938-nepheline-albite.yaml` (run from the green checkout, pointed at the corpus worktree by its absolute extract path) gives `OK: 1 extract file(s) valid`, exit 0.
- `python3 tools/test_ledgers_valid.py` in the corpus worktree: exit 0.
- `engines/engines.local.toml` exists in the green checkout.

## Required changes (verdict FIX-FIRST)

Required changes (each with page locator). Never guess. If any cell reads ambiguously at higher dpi, use a typed absence.
1. **M1, p.104 Table IV:** t4.csv row 2 and `table_4_run_records.values.rows[1]`: change `albite_wt_pct` 10.9 → **5.2** (the 1447 °C, 21 hrs, Glass → "Carnegieite. No glass found" run is in the 5.2 wt% group).
2. **M2, p.109 Table V:** t5.csv row 31 and `table_5_run_records.values.rows[30]`: change `original_condition` "Carnegieite" → **"Carnegieite + glass"** (33.5 wt%, 1268 °C, 4 hrs).
3. **M3, M4, p.98 Table I:** row 10 `result_as_printed` → "Films of glass between the crystals throughout the charge". Row 11 → "Charge now contains only a few per cent of crystals. The smaller crystals have melted." In the same pass, restore the printed punctuation in Table IV rows 2, 9 and 13 ("Carnegieite. No glass found", "Nephelite. No glass found") and the degree mark in Table II row 30's `temperature_as_printed` ("1102°"). These are as-printed fields.
4. **M5, p.101:** rewrite `carnegieite_melting_context.values.note` to the printed sense: carnegieite approaches equilibrium just above its melting point slowly enough to be held there for an appreciable time without melting completely, yet rapidly compared with albite ("in marked contrast"), so rapidly that Bowen could fix its melting point by heating curves (1525°/1527°, placed at 1526°, fn 12). Quote the sentence verbatim with the p.101 locator. Fix the report's p.101 bullet the same way.
5. **M6, p.94:** replace `quartz_cleaning_as_printed` with the printed sentence (residue < 0.04 % after evaporation with HF + H2SO4; converted to cristobalite by firing at a high temperature).
6. **M7, pp.95/107:** restore "about" for the 35/65 soda–silica preparation (p.95) and for the two-month crystallization at about 1035° (p.107). Use a quoted string or an `approximate: true` note; do not leave a bare exact value.
7. **Carry the printed numbers not carried (N1–N9)** as located context:
   - N1 p.98: "A bracket 1113°-1122° is established by these runs."
   - N2 pp.98–100: sealed-tube crystal growth, with gibbsite or a rhyolitic glass "containing about 3 per cent of water" as water source; silica volatilization from tube walls; "no detectable contamination of the charge inside its platinum container".
   - N3 p.100: Merwin, a change in natural albite near 900° within a few hours.
   - N4 p.100: crystals grown at 800°, 930°, 1000° showed no differences in behaviour.
   - N5 p.100: albite crystals in the 91.6 per cent liquid at 1115° behave like crystals in a liquid of their own composition.
   - N6 p.100 fn 10: quartz crystal heated to about 1800 °C, outer silica glass, inner still crystalline.
   - N7 p.106: the 100 % albite glass in the 90/95/97/98/100 % series.
   - N8 p.108: all glasses "with the exception of the 33.5 per cent glass" were completely crystallized to carnegieite and to nephelite.
   - N9 p.108 fn 15: Bowen, almost complete nephelite→carnegieite conversion in one hour at 1252° and partial carnegieite→nephelite in one hour at 1245°.
8. **Missing experiment facts (p.95, p.107, p.108):**
   - alumina from distilled chloride by double precipitation with HCl gas and ignition of the aluminium chloride six-hydrate;
   - soda as "a particularly good sodium carbonate monohydrate", analysis by E. G. Zies;
   - several analyses of the soda–silica preparation "agreed with the compositions as determined by synthesis. There was then no detectable loss of soda.";
   - X-ray powder spectrograms (Posnjak) with molybdenum Kα radiation; a few per cent of nephelite crystals mixed with albite undetectable by this means (p.107);
   - in the inversion temperature region "carnegieite formed first and … nephelite was obtained only by inverting the carnegieite", with charges crystallized to nephelite at low temperatures in sealed tubes (p.108).
9. **Missing directional/comparative statements, p.111, verbatim with locator:**
   - "In both systems the temperature of the inversion nephelite-carnegieite rises with increased feldspar content."
   - "At the other end of each diagram, as drawn, some slight solubility of nephelite, carnegieite in the feldspar is indicated." (This is nephelite/carnegieite dissolved IN feldspar. The report words it as "feldspar solubility"; correct that.)
   - Bowen's nephelite birefringence "decreased, became zero, then increased again" with increasing anorthite, the crystals becoming optically positive; the change "does not occur in this system. These nephelites are all optically negative".
10. **Locators:**
    - `liquidus_preparations_context`: the 24–48 h drying "in air, close to the melting temperature" is on **p.100** (pdf_page_index 7), not p.102. Give it its own locator.
    - Give per-claim locators where one record spans pages: `temperature_method_qualifications` (pp.96, 97, 100); `jadeite_xrd_qualifications` (pp.106–107; also rename it, since it is about albite/nephelite solid solution, not jadeite); `figures_only` (Fig. 2 is p.110, pdf_page_index 17).
    - Record the three source-internal misreferences listed in §3 (p.100 "Table IV", p.100 "Table I", p.101 "Table I") as `source_internally_inconsistent` notes.
11. After the fix: re-run migrator + finalize (hard issues 0), the fidelity validator and tools/test_ledgers_valid.py, and re-evaluate the ledger's `completeness.claims`. Do not run tools/build_index.py in the sparse tree.

rows checked 104, mismatches 8, printed numbers not carried 9

!COMPLETE: rev-greig-barth-1938-nepheline-albite — FIX-FIRST, pages read 20, rows checked 104, mismatches 8, printed numbers not carried 9
