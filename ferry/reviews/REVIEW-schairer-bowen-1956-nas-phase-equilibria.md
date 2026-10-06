# REVIEW (first, full fidelity): schairer-bowen-1956-nas-phase-equilibria @ 08b6852661a9637b71361d60e5cc151cf54245d6

Reviewer: regolith-empirical seat (Grok Bot), 2026-10-05 23:10 ET. Corpus batch 9, sent by regolith-main.
Source: J.F. Schairer & N.L. Bowen, "The system Na2O–Al2O3–SiO2", Am. J. Sci. 254 (1956) 129–195, doi:10.2475/ajs.254.3.129.
67 pp. The PDF is image-only (scanned print). Published page = pdf_page_index + 129.

VERDICT ON COMMIT: FIX-FIRST

rows checked 763, mismatches 44, printed numbers not carried 34

Also: Table 2 omits **11 printed runs** (M2). P0 = one structural finding (S1) plus 16 rows with a wrong number or wrong composition binding (B1 5, B2 5, B3 6).
In S1, all 749 observations migrate as composition-less `transition_temperature` points of "Na2O-Al2O3-SiO2", and each is admitted.

## Setup (what I ran, and where)
- Mirror `mac-studio-256-1:Repos/regolith-corpus.git`: `git ls-remote origin refs/heads/hunt/schairer-bowen-1956-nas-phase-equilibria` gave tip `08b6852661a9637b71361d60e5cc151cf54245d6`. That is the sha I was assigned.
  Its parent is fe659471 ("Correct Schairer Bowen Table 2 duration"), after f6364066 (claim release) and 2cbf0490 (extract). Every commit is authored by Simon Rowland. No AI or co-author trailers.
- Sparse review worktree on the Mac: `~/Repos/regolith-corpus/worktrees/rev-schairer-bowen-1956-nas-phase-equilibria`. I made it with `git worktree add --no-checkout --detach` and then
  sparse-checkout of `/*`, `!/raw/*/*`, `!/text/*/*`, `/raw/*/sidecar.yaml`, `/raw/<sid>/*` and `/text/<sid>/*`. df stayed at 64–65 GB free, above the 30 GB floor.
  I did not run build_index.py or migrate_pilot_extracts.py. I wrote nothing tracked. I removed the worktree after delivery.
- Green: read-only detached checkout `/Users/simonrowland/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. Interpreter:
  `/Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH=<green-ro>`.
- `engines/engines.local.toml` **exists** in the green-ro checkout (untracked). None of the checks below use it.
- Page images: `pdftoppm -r 250 -png` of all 67 pages. I re-rendered the 21 Table 2 pages (pp137–157) and the Table 3/4 pages at 400 dpi.
  - I read every table page and pp129–136 and pp157–177 as images.
  - I read pp178–195 (discussion, comparisons, references) from 250 dpi OCR, with image spot checks (pp184–186).
  - Digit checks: Table 2 was checked column by column. I cropped the RI, Na2O, Al2O3, SiO2 and Temp columns of each 400 dpi page and OCR'd each strip with a digit whitelist.
    Each column sequence was aligned against the CSV page by page. Every disagreement, and every row the OCR missed, was then decided on a zoomed crop of the page image.
    Most disagreements were OCR noise. The ones listed below are confirmed on the image.
  - Tables 1, 3 and 4 were read cell by cell from zoomed crops.

## Acceptance checks (green 61ec839)
| check | result |
|---|---|
| Migrator(root=green, index={}, aliases={})._migrate_extract(<worktree>/extracts/schairer-bowen-1956-nas-phase-equilibria.yaml), then finalize() | Completes: 1 work, 2 experiments, **749 observations**, 8 context rows, **hard issues after finalize 0**. The queue has 749 entries: 727 × `phase string 'Na2O-Al2O3-SiO2 assemblage' is not in the closed automatic map` and 22 × `... 'Na2O-Al2O3-SiO2 invariant assemblage' ...`. See S1. |
| what survives in a migrated observation | `quantity/subtype transition_temperature`, species formula `Na2O-Al2O3-SiO2`, phase **unknown**, **composition `not_applicable` ("profile transition_temperature does not use composition")**, value = run T in K, evidence measured_tabulated, admission **admitted** (defaulted). The RI, the composition, the field/section, the phases observed and the duration are all absent from the observation. Checked for T2 rows 1 and 110 and for T3 I and T4 G. |
| evidence_for() classes | measured_tabulated / quoted_attributed only; evidence_fallthrough {} |
| `tools/validate_literature_extracts.py --check-fidelity-match <abs worktree path>/extracts/schairer-bowen-1956-nas-phase-equilibria.yaml` (run from green-ro) | `OK: 1 extract file(s) valid` (exit 0) |
| corpus worktree `python3 tools/test_ledgers_valid.py` | exit 0 |
| sidecar sha256 vs file | 527df39afed789241db422e14865bfd1b74033036eaea03aca298e32a624af47 matches the PDF and `source.sha256`. Citation, DOI, volume 254 and pp129–195 match. Licence: `"DOWNLOADED OA" (AJS; as recorded by fetcher from landing page)`. |
| rg "/Users/" "/private/" in extract, ledger, tables/<sid>, text/<sid>, sidecar | no hits |
| YAML vs CSV | Programmatic. All 727 T2 observations have the same T, composition, RI string and page as t2.csv, and the T3/T4 observations equal t3/t4.csv. So every CSV error below is also in the YAML. |

## A. Tables
| table | rows | method | value mismatches | notes |
|---|---:|---|---:|---|
| 1 (p130) t1.csv | 14 | every cell, image | 0 | 59.12 … 0.50, total 100.00. Attributed Clark and Washington in context. |
| 2 (pp137–157) t2.csv | 727 (CSV); **738 printed** | every row: T, RI, Na2O, Al2O3, SiO2 (strip-OCR alignment + image for every flag); composition sum check | 41 (B1–B5) | Plus 11 printed runs missing (M2). 340 printed composition groups; the CSV has 338 (Al731 and Z37-1/2 are lost, B2). The abstract (p129, carried in context) itself says 340 compositions. |
| 3 (p160) t3.csv | 11 | every cell, image | 0 | All T, ±, liquid compositions, alternate joins and attributions match. Precision loss: advisory P3-1. |
| 4 (p169) t4.csv | 11 | every cell, image | 1 (B6) | Every number matches. The G′ attribution is wrong. |

## B. Mismatches (44). Each is a required change. [P0] marks a wrong number that reaches the migrated, admitted observations.
**B1 [P0]. Table 2 temperatures (5 rows).**
| row | page / group (prep no.) | CSV T | printed T |
|---|---|---|---|
| 49 | p139, 1.483 13.5 8.0 78.5 (Al708) | 1080 | **1050** (4 days, All glass) |
| 93 | p140, 1.500 25.8 6.0 68.2 (Al106) | 7800 | **780** (2 hr, All glass) |
| 103 | p140, 1.502 27.4 5.8 66.8 (Al3) | 1765 | **765** (14 days, all crystalline: Na disilicate + albite) |
| 111 | p140, 1.503 27.9 7.0 65.1 (Al1107) | 8000 | **800** (2 hr, All glass) |
| 504 | p151, 1.517 35.4 20.8 43.8 (MA18) | 9809 | **980** (2 hr, rare nepheline) |

**B2 [P0]. Rows bound to the wrong composition (5 rows; 2 printed composition groups lost).**
- p140: row 110 (795, 4 hr, rare Na disilicate) is printed under **1.503 27.9 7.0 65.1 (Al1107)**. The CSV binds it to 1.506 32.2 2.5 65.3 (Dij1).
  Row 112 (830, 1 hr, rare Na disilicate) is printed under **1.505 30.3 5.0 64.7 (Dij2)**. The CSV binds it to the 27.9 group.
  So printed Al1107 = {795, 800} and Dij2 = {830, 835}.
- p147: row 367 (1611, 4 hr, very rare corundum) belongs to **Al731: 1.504 10.1 31.0 58.9**. The CSV binds it to Al827-1/2 (1.499 12.7 27.5 59.8), and the Al731 composition is absent.
- p154: rows 646–647 (1295 rare carnegieite / 1300 all glass) are **Z37-1/2: — 44.0 20.2 35.8**. The CSV repeats X26's 50.6 13.0 36.4.

**B3 [P0]. SiO2 typo (6 rows).** Rows 535–538 (p152, MA62): printed **43.5**, CSV 43.55. Rows 687–688 (p156, MN40): printed **29.5**, CSV 29.55. In both groups the CSV composition sums to 100.05.

**B4. Typed absence where the RI is printed (8 rows).** RI `—` in the CSV/YAML (`refractive_index_state` not reported) where the page prints a value:
118–119 p141 Dij4 **1.503**; 180–181 p143 LM **1.480**; 205–206 p143 Al715 **1.487**; 333–334 p146 J25 **1.495**.

**B5. Section label (19 rows).** Rows 709–727 (pp156–157) are labelled "Points in Sodium Orthosilicate (?) Field". p156 prints a new heading, **"Points on or very near Boundary Curves"**, above row 709 (1.491 19.5 5.0 75.5, Al605).
These runs sit on boundary curves, so they do not belong to the Na4SiO4 field.

**B6. Table 4 G′ attribution (1).** p169 prints G′ "(Tilley, 1933)" only. t4.csv/YAML have `Schairer and Bowen; Tilley (1933)` with `measured_tabulated`.
Fix: `Tilley (1933)`, `quoted_attributed`, matching the Table 3 Tilley rows.

**M2. Printed runs not carried (11).** Each is a full printed run line (T, time, phases) absent from t2.csv and the YAML. Most are the "All glass" half of a liquidus bracket:
| page | group (prep) | missing run | insert |
|---|---|---|---|
| p142 | 1.518 43.2 15.0 41.8 (M15) | 975, 1 hr, very rare Na metasilicate in glass | before row 165 |
| p146 | 1.489 10.6 20.0 69.4 (J20) | 1170, 10 days, All glass | after row 320 |
| p147 | 1.500 8.6 28.0 63.4 (Al928) | 1607, 4 hr, All glass | after row 335 |
| p150 | 1.500 17.2 25.0 57.8 (Al425) | 1180, 5 hr, All glass | after row 470 |
| p151 | 1.509 23.7 26.0 50.3 (Al1226) | 1270, 2 hr, All glass | after row 523 |
| p152 | 1.516 31.0 26.1 42.9 (MA21) | 1215, 1 hr, All glass | after row 547 |
| p153 | 1.519 20.4 40.0 39.6 (Dial 40) | 1480, 2 hr, moderate amount carnegieite in glass | between rows 589 and 590 |
| p153 | 1.520 39.7 21.8 38.5 (3Na2O·Al2O3·3SiO2 comp.) | 1175, 1 hr, All glass | after row 604 |
| p154 | 1.520 39.6 22.0 38.4 (M22) | 1175, 2 hr, All glass | after row 609 |
| p154 | 1.521 38.1 25.0 36.9 (M25) | 1367, 1 hr, All glass | after row 641 |
| p155 | — 46.8 18.7 34.5 (MN30) | 1000, 16 hr, carnegieite and sodium orthosilicate (?) in glass | between rows 662 and 663 |

Checked correct, even though OCR disagreed: rows 3, 16, 34, 64, 203, 239, 296, 460, 473, 589 and 657.
Rows 127 (700) and 129 (759) on p141 are printed as such (see P3-3).

## S1 [P0]. Structural: every run is scored as a composition-less "transition temperature"
The extract encodes all 727 Table 2 runs and the 22 Table 3/4 invariant points as `type: transition_point`, `quantity: transition_temperature`, species `Na2O-Al2O3-SiO2`.
The composition, RI, section and raw phase text sit only in `values`. The migrator's transition_temperature profile does not use composition, so after migration each observation is just
"Na2O-Al2O3-SiO2 transitions at T", **admitted**, measured_tabulated. Phase is unknown, and that queues all 749. This puts wrong numbers into the scoring path:
- Most Table 2 runs are not transitions. They are single quench observations on either side of a liquidus or solidus ("All glass" above the liquidus, crystals + glass, all crystalline).
  Row 2 (1588 °C, All glass) becomes a 1861.15 K "transition temperature" of the system.
- 727 different compositions collapse onto one species with no composition axis. Even Table 3/4 invariant temperatures lose their liquid composition and phase assemblage on migration.

Required change (main to confirm the encoding against its phase-equilibrium precedent):
- Do not emit Table 2 runs as transition_temperature observations. Keep them as run records, in context (the greig-barth-1938 precedent: table rows in migrated context, 0 observations)
  or as a phase-equilibrium run type if green has one. Each record keeps composition, RI, T, duration, phases and prep no.
- If liquidus/solidus points are to be scored, derive them per composition from the bracketing runs. Label them derived (e.g. `measured_reduced`, with derivation from the two row ids).
- For Tables 3/4, use an observation shape that carries the printed liquid composition and phase assemblage (and ±), or keep them in context.
  Map the phase so the queue does not hold 749 "phase not in closed map" entries.
- Re-run the migrator: hard 0, and confirm composition survives.

## C. Experiment / bench facts and typed absences (22 checked)
Correct as printed: quench equilibration; the same SiO2/Al2O3 sources, quenching techniques and thermocouple fixed-point calibration as Schairer & Bowen (1955) (p131);
very pure NaHCO3 (Merck lot 7398) heated to 200 °C and weighed as Na2CO3 (p131); thermocouple type, T uncertainty and numeric purity typed not_stated (none printed);
no activities, no mass spectrometry (correct n/a); atmosphere/fO2 not reported (correct: the paper names none); the RI ±0.003 (Table 2 heading); the hygroscopic-glass and soda-loss
qualifications (pp132–133, p159). Table 1 attributed to Clark and Washington.
Required:
- **C1. cell_material typed `unknown / not_applicable_to_quench_equilibration`.** It applies, and it is printed. p159: "of difficulties with attack of the **platinum containers** by these melts and loss of soda
  as described later in this paper." Set the container to platinum, citing p159. The jadeite runs on p185 also use "platinum envelopes".
- **C2.** `liner`/`orifice` `not_applicable_to_quench_equilibration` is fine. `pressure: not stated` should be typed (atmospheric runs; not printed → not_published).
  Advisory only.

## D. Printed numbers and qualifying statements not carried (34). Carry each verbatim with its locator (context; quoted_attributed where another author's).
Data qualifications on Table 2/4 rows (carry these first):
1. p159: in compositions with less than about 40 % silica, slight Na2O volatility from preparations with liquidus above about **1525°**, and at much lower T in area B′J′K′ (figs 3–4).
2. p162: tridymite could not be crystallized in any albite–silica preparation. Cristobalite persisted below **1470° ± 10°**, in the tridymite stability range.
3. p164: Al731 metastable β-Al2O3 liquidus **1607 °C** before corundum was present. Stable corundum liquidus **1611 °C** after recrystallization. β-Al2O3 and mullite needles at **75°–100°** below liquidus later disappeared. (This explains the Al731 row in B2.)
4. p167: "no values for the indices of these glasses are given in table 2" (inhomogeneous glasses). This is the printed reason for the `—` RIs on pp153–157. Crystallization at about **1450 °C** gave twinned carnegieite s.s. + β-Al2O3/corundum. Pt 90 Rh 10 furnace limit. The furnace burned out after a short run at **1725 °C** (rows 689–690; "+" in fig. 12).
5. p171: M at **1050° ± 10°**: "tridymite (from the viscous dry liquids actually cristobalite metastably, which introduces only a small temperature error)".
6. p175: "the precision of the data in the quadrilateral B′WK′J′ is much less than for the rest of the system".
7. p175: B′WK′ compositions begin to melt between **900° ± 2°** (H′) and **995° ± 5°** (E′). MN23 is completely crystalline at **955°**, the temperature of E′. (See P2-2.)
8. p162: **42 days** at **7°** below the eutectic leave only a trace of glass. I is bracketed by M (sharp albite at **1060°** after 42 days; only cristobalite at **1065°**) and LM (albite + rare cristobalite at 1060°; corroded albite only at 1065°).
9. p163: glasses with ≥ **90 %** albite were viscous. Al9-1/2 (albite **95**, Na disilicate **5**) grew albite in a few weeks at **1025 °C**. Four preparations crystallized completely at **740 °C**.
10. p164: many months at **1050 °C** for preps 721 and 721-1/2. Runs of a month located the albite–corundum eutectic (1108 ± 3).
11. p177: albite crystallized in days/weeks at **50°–75°** below liquidus where liquidus ≤ about **1040°**. Mullite: difficulty below ~**1400°**. Liquidus ~**1200°** or below needed many months at ~**75°** below liquidus.
12. p173: T falls along SR from about **1280°** (S) to about **1270°** (R). Nepheline and carnegieite are limited NaAlSiO4–NaAlSi3O8 solid solutions.
13. p174: no quenching data for joins Na2O·SiO2–(Na2O **70** Al2O3 **30**) and Na2SiO3–(Na2O **90** Al2O3 **10**). I′F′ falls from **1163° ± 5°** to **915° ± 5°**.
Phase data printed in the text:
14. p176: Na2Si2O5 congruent mp **874° ± 3°**. Transitions Aα⇌Aβ **707°** and Aβ⇌B **678°**. 2V about **55°**.
15. p176: Na2SiO3 (B′) congruent mp **1089° ± 1°**. 2V about **80°**.
16. p177: sodium metasilicate α = **1.513**, β = **1.520**, γ = **1.528**. Mullite incongruent mp **1810° ± 10°**.
17. p167: carnegieite maximum γ = **1.514**, rising to ≈ **1.537** at NaAlSiO4 **63.4** / Na2O·Al2O3 **36.6**. Brownmiller & Bogue γ = **1.580** (Na2O·Al2O3).
18. p168: carnegieite inverts on cooling at **687°** (Bowen & Greig 1925). Na2O·Al2O3 mp **1650°**, not confirmed by Brownmiller & Bogue (no melting at 1650°).
19. p168: Tilley eutectic Na2SiO3–NaAlSiO4 at **46.75 %** NaAlSiO4 / **53.25 %** Na2SiO3 at **906°**. Spivak **907° ± 2°** and coexistence to **900° ± 2°**. Eutectic "about **47.0** … **53.0**". Melting during cooling below **24 %** silica.
20. p184: albite crystals growing at **1115°** and dissolving at **1120°**, giving 1118 ± 3. Amelia albite converted at **1050°–1100°**. Low/high albite inversion ≤ **700 °C** (Tuttle & Bowen).
21. p161: preliminary albite–silica eutectic **1115°** (1935), revised to **1062° ± 3°** (Schairer 1950).
Background and literature values (quoted_attributed):
22. p134: Bowen & Greig cristobalite–mullite eutectic **1545° ± 5°** at Al2O3 **5.5** SiO2 **94.5**, changed to **1585° ± 10°** (fig. 2).
23. p134/135: cristobalite mp **1713° ± 5°** (Greig 1927). Corundum mp **2050° ± 20°** (Kanolt 1914).
24. p135: Na2O·Al2O3 analyses **61.76, 61.97, 61.59 %** Al2O3 (calc. **62.19**). Mp **1650 °C**, with dissociation below 1650° for Na2O-excess compositions.
25. p135: Brownmiller & Bogue homogeneous solid at Na2O **37.8** Al2O3 **62.2**.
26. p136: Na2O **3.5** Al2O3 **96.5** heated at **1100°** gives corundum + β-Al2O3. β-Al2O3 found up to **96.5 %** Al2O3.
27. p136: Na2O·Al2O3 by fusion complete only at **1000°–1100 °C**, dissociating at **1200°–1300°**.
28. p136: Kato & Yamauchi (1943): Na2O **2, 4, 6, 8, 10, 15, 25 %**; **1550°–1600 °C** for one hour; Na2O·**12.34**Al2O3; < **10 %** Na2O gives corundum, ≥ **15 %** gives β-like.
29. p131: Burma jadeite decomposed as low as **800 °C** and converted to nepheline + liquid at **1015°** (Greig & Barth).
30. p185: jadeites USNM **94303/94829**. 3 weeks at **1000°** gives nepheline + albite. 1 hr at 1000° gives glass. 1 hr at **950°** leaves ~**20 %** residual jadeite. 4 hr / 4 weeks at **900°**. Burma jadeite liquidus **1128°**. Synthetic jadeite glass nepheline liquidus **1138°**.
31. p185: Yoder: two months at **100°** intervals, **500°–1000°**. ≤ **800°** no crystallization. **900°/1000°** nepheline + albite.
32. p186: analcite = nepheline + albite + vapor to **585°** and **38,000 psi**.
33. pp186–187: "anhydrous paragonite" at albite **72.0** corundum **28.0**. Corundum liquidus about **1740°**. Melting begins **1108°**.
34. p191: leucite–KAlSiO4–Al2O3 begins to melt at **1553° ± 5°**.
Carried and correct: pp129, 131–133 and 178–183 context. That includes the p132 Na2O–SiO2 preparation key, the 340 compositions, the β-Al2O3 statement, 1118 ± 3, 1526 ± 2, 1248/1254,
the 687.0/692.1/226.5 inversions, the solid-solution limits, Kracek 1120/1022, γ 1.537 / α 1.524, MN32 980/2V 50, and the acclimating schedule. Wording and direction checked.

## P2 / P3 advisories
- **P2-1. Raw duration/phase/prep text is unusable as data.** t2.csv has no duration, phases or preparation-number columns. These are the observable that defines every bracket.
  `duration_phase_preparation_raw` is OCR text, garbled in at least **171/727** rows (e.g. "Tdays", "Sdays", "Lhr", "Very carnegieite" with "rare" dropped, "Smali"). **211/727** rows have no parseable duration.
  Add clean `duration`, `phases_as_printed` and `preparation_no` columns transcribed from the image. Fixing this is the precondition for S1.
- **P2-2. Source-internal inconsistency not marked.** p175 prints E′ at "995° ± 5°", while Table 4 (p169) and the next sentence on p175 give **955°**. Add a `source_internally_inconsistent` note. Do not change the printed value.
- **P2-3. Ledger overstates completeness.** `completeness.table_2: 'true'`, and `relevance` says "727 runs over 338 tabulated composition groups". The print has 738 runs over 340 groups (M2, B2). Re-evaluate after the fix.
- **P3-1. Precision lost.** T3 H′ 47.0/53.0 and I′ 69.0/31.0, and T4 N 20.0, R 17.0, T 26.0, G′ 32.0, E′ 44.0, are stored as 47, 53, 69, 31, 20, 17, 26, 32, 44.
  That makes them indistinguishable from the integer-precision H (15 9 76) and L (2 9 89). Keep as-printed strings.
- **P3-2. Context duplicated.** About 21 statements (pp129–133, 160, 178–183) appear twice, in `sb1956_data_qualifications` and again in `sb1956_numeric_context`. Keep one copy.
- **P3-3. Apparent source misprints carried silently.** p141 Al1210 prints 765 (very rare Na disilicate) then **700** All glass. Al1211 prints 755 then **759** All glass.
  The CSV is faithful. Flag both rows as printed-suspect rather than changing them.

## Count basis
Rows checked 763: 14 (T1) + 727 (T2 CSV rows; the T2 YAML is programmatically identical) + 11 (T3) + 11 (T4).
Mismatches 44: B1 5 + B2 5 + B3 6 + B4 8 + B5 19 + B6 1. M2 (11 printed runs absent) and S1 are counted separately.
Printed numbers not carried 34: list D (statements, each with ≥1 printed number).

## Required changes (FIX-FIRST), in order
1. S1: stop emitting Table 2 runs and Tables 3/4 points as composition-less `transition_temperature` observations. Use run records and composition-bearing invariant-point records (main's precedent). Re-run the migrator and check that composition survives.
2. B1–B3 (P0): rows 49→1050, 93→780, 103→765, 111→800, 504→980. Rebind rows 110 (Al1107), 112 (Dij2), 367 (Al731 1.504 10.1 31.0 58.9) and 646–647 (Z37-1/2 — 44.0 20.2 35.8). SiO2 43.5 for rows 535–538 and 29.5 for rows 687–688.
3. M2: add the 11 missing runs (table above).
4. B4: RIs 1.503/1.480/1.487/1.495 for rows 118–119, 180–181, 205–206, 333–334.
5. B5: rows 709–727 section "Points on or very near Boundary Curves".
6. B6: G′ → Tilley (1933), quoted_attributed.
7. C1: cell material platinum (p159).
8. D1–D34: carry with locators. D1–D13 first, because they qualify Table 2/4 rows.
9. P2-1: clean duration/phases/prep columns. P2-2: E′ 995/955 note. P2-3: ledger. P3-1–P3-3.
Then re-run on green 61ec839: migrator + finalize (hard 0), `validate_literature_extracts.py --check-fidelity-match`, and `tools/test_ledgers_valid.py`. Do not run build_index.py in a sparse tree.

!COMPLETE: rev-schairer-bowen-1956-nas-phase-equilibria — FIX-FIRST, pages read 67, rows checked 763, mismatches 44, printed numbers not carried 34
