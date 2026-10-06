# REVIEW (first, full fidelity): christopoulou-2019-jace102-508-aam @ 78be96e667f6e3ab2b11f1b05b32c329d8f7b771

Reviewer: regolith-empirical seat (Grok Bot), 2026-10-05 ~22:45 ET. Corpus batch 9, sent by regolith-main.
This is a rebuild. An earlier seat finished this review but its text was lost before it was written to disk. Every finding below
comes from my own reading of the page images. I used no earlier notes as evidence, and none survived anyway: the scratch held only page renders.
Source: Christopoulou G., Modarresifar F., Allsopp B.L., Jones A.H. [cover: "JONES, Hywel"] & Bingham P.A., "Non-isothermal crystallization kinetics and
stability of leucite and kalsilite from K2O-Al2O3-SiO2 glasses", J. Am. Ceram. Soc. 102(1) 508-523 (2019). This is the accepted manuscript (SHURA 22027):
26 PDF pages, LibreOffice export of a Word file, with no tables and no figure panels. PDF p1 is the repository cover. Printed manuscript page n is PDF page n+1.

VERDICT ON COMMIT: FIX-FIRST

rows checked 202, mismatches 22, printed numbers not carried 27

Also: printed directional/qualifying statements not carried **26** (list D below). The brief (item 6) makes these FIX-FIRST.
Severity: **P0 0**, P1 4 findings (7 rows), P2 3 findings + 27 numbers not carried + 26 statements, P3 12 locator/wording rows. No transcribed number differs from the print.
The extract has 0 observations, so no wrong number reaches a score or ledger today.

## Setup (what I ran, and where)
- Mirror `mac-studio-256-1:Repos/regolith-corpus.git`, branch `hunt/christopoulou-2019-jace102-508-aam`. Tip `78be96e667f6e3ab2b11f1b05b32c329d8f7b771`, the assigned sha.
  Commits over mirror main e1c71da5: add48504 (claim), 04b9db84 (extract), 91cf0e82 (abstract Ea claim), 78be96e6 (remaining crystallization qualification).
  Author on all four: Simon Rowland <simon@simonrowland.com>. No AI or co-author trailers.
  Changed files: `extracts/<sid>.yaml` (+448), `ledger/<sid>.yaml` (+20), `tables/<sid>/t1,t3,t4,t5.csv` + provenance, and `text/<sid>/` (26 page PNGs, pdfinfo, pdfimages list, pdftotext layout).
- The sparse review worktree on the Mac was the earlier seat's: `~/Repos/regolith-corpus/worktrees/rev-christopoulou-2019-jace102-508-aam`, detached at 78be96e6. It holds
  only `/raw/<sid>/*`, `/text/<sid>/*` and the `raw/*/sidecar.yaml` files. I did not run build_index.py or migrate_pilot_extracts.py. I wrote nothing tracked.
  The test run created an untracked `tools/__pycache__/`. I deleted it, then removed the worktree after delivery.
- Green: the read-only detached clone `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. Interpreter:
  `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH=<green-ro>`.
  `engines/engines.local.toml` **exists** in green-ro (untracked; 1331 B, Oct 4 04:26). None of the checks below use it. The extractor said it is absent in its own worktree.
- Page images: `pdftoppm -r 220 -png` of all 26 pages on the VPS (1819x2573 px). 25 of the 26 renders are byte-identical to the committed `text/<sid>/pages/`.
  The exception is p-01, the cover, whose JPEG logo decodes differently. I read every page image: pp2-20 as two overlapping full-width halves, and the reference pages
  pp21-26 as page images (they hold refs 8-68 only). The PDF text layer (`pdftotext -layout`) was a second, programmatic check. Where the two disagree, the image decides.
- Inventory result: the AAM contains **no table bodies and no figure panels**. The text cites Tables 1, 3, 4 and 5 (Table 2 is never cited) and Figures 1-13.
  pdfimages lists only two images: the cover logo (p1) and a thin 618x29 px rule (p2). The extractor's central claim is correct, and t1/t3/t5 are correctly typed as absent bodies.

## Acceptance checks (green 61ec839)
| check | result |
|---|---|
| Migrator(root=green-ro, index={}, aliases={})._migrate_extract(<worktree>/extracts/<sid>.yaml), then finalize() | completes. 0 observations, 1 experiment, 3 benches, 11 context rows. **hard issues after finalize: 0** (total 0). Queue 1: `extract yielded no observations` (document axis; its source_path is my runtime worktree path, not stored in the extract). |
| evidence_for() on measured_direct / model_derived / figure_only / quoted_attributed | all known (none unknown) |
| `tools/validate_literature_extracts.py --check-fidelity-match --show-warnings <abs worktree path>/extracts/<sid>.yaml`, run from green-ro (a full clone) with PYTHONPATH=green-ro | `OK: 1 extract file(s) valid`, exit 0 |
| corpus worktree `tools/test_ledgers_valid.py` | script exit 0; pytest 687 passed |
| payload survival | K-loss: 1250, KAS-1/2 `not_detected`, 1.5, [2.7, 3.6] numeric. Ea: 579/8, 473/1, 364/1, 360/1, 548/1 numeric. Schedule lists, XRF masses (1.0, 0.002, 10.0, 0.002), XRD settings, DSC 1400/[5,40]/50.0 all survive. 59 statements survive. Bench DSC sensor/calibration survive as typed unknown/not_published. Migrated experiment: conditions.temperature_K typed unknown, sample.mass_kg null (see F7). |
| corpus sha256 vs file | a7568abaa6086d4fe4efa17e2993d9019b9c772ae8a6674a12d1bc639e368a6b = file = sidecar = extract; 225950 B; 26 pp. |
| citation | Volume, issue and pages (102(1), 508-523) match the cover (PDF p1). The DOI 10.1111/jace.15944 is not printed anywhere in the PDF; it comes from the sidecar/landing page (acceptable). Advisory: the manuscript title page (PDF p2) prints "Benjamin L. Allsopp" and "Alan H. Jones", while the cover prints "JONES, Hywel". The citation's "Jones, H." follows the cover; note the discrepancy. |
| licence | sidecar: SHURA "License All rights reserved", openly downloadable, recorded from the landing page. The PDF prints only "Copyright and re-use policy See http://shura.shu.ac.uk/information.html" (p1). The extract makes no licence claim (consistent). |
| rg "/Users/" "/private/" in extract, ledger, tables, sidecar | no hits |

## A. Inventory: every table, equation and numeric statement (PDF page)
| PDF p | item | carried? |
|---|---|---|
| 1 | Cover: SHURA 22027, citation 102 (1) 508-523 | yes (citation) |
| 2 | Abstract: 850/950/1250 °C; "5 minutes to 1000 hours"; 1000 h at 1250 °C; 579 and 548 kJ/mol | temps, 1000 h, Ea yes; **"5 minutes" no (N26)**. It also contradicts "1 minute" on p7 (E3) |
| 3 | >1600 °C (three K aluminosilicates); 1686 °C; 635 °C; <665 °C | yes |
| 4 | 655 and 675 °C transitions; "six different crystal structures" | 655/675 yes; "six" advisory |
| 5 | UUDUDD : UUUDDD rings "in a 2:1 ratio"; KAlSiO4 >1700 °C | 1700 yes (extract note says p6; it is printed p5, advisory); **2:1 no (N27)** |
| 6 | 1 h at 2000 °C; 550 °C anneal; 1-1000 h; 1250 °C; 5 °C/min; removal accuracies ±2 s / ±5 s / ±5 min / ±1 h | yes, but **the ±5 s group drops the 1 h sample (F4)** |
| 7 | 1-30 min short runs at 1250 °C; "1 minute to 1000 hours"; **Eq. (1) Kissinger**; **Eq. (2) n = (2.5/ΔT)·T0²/(Ea/R)** | durations yes; Eq. 1 named only (N25); **Eq. 2 and 2.5 no (N1)** |
| 8 | Avrami terms: integers 1, 2 or 3; nucleation 0 or 1; n > 2.5 diffusion-controlled; n > 4 polymorphic | **no (N2-N5)** |
| 8 | XRF 1.000 ± 0.002 g + 10.000 ± 0.002 g Li2B4O7; XRD 40 kV/40 mA, 5-100°, 0.013°, ~70 s, 0.25 Hz | yes |
| 9 | DSC: RT-1400 °C, 5-40 °C/min, 50.0 mg, 99.9% α-Al2O3, 1450 °C 15 h | yes (locator wrong, L8) |
| 9 | Raman: Thermo DXR2, 532 nm, 300 mW, 1200 lines/mm, 50× objective | **no: no bench, no context (N6-N9)** |
| 9 | "first crystallization event occurred for all the samples at approximately 1250°C"; 1250 °C below liquidus; up to 24 h; KAS-1/KAS-5 up to 1000 h | 24 h quote yes; **~1250 °C DSC event no (N10)** |
| 10 | ICDD 01-076-8737 (tetragonal leucite), 00-033-0988 (orthorhombic kalsilite); <5 / <15 / <30 min; Fig. 7 "for 1 hour"; 850/950/1250 °C | minutes and temps yes; **ICDD no (N11, N12); 1 h no (N15)** |
| 11 | ICDD 00-012-0134 (hexagonal kalsilite), 04-010-8954 (KAl11O17); ~24 h (KAS-4) / 500 h (KAS-5); 1200 °C 48 h Pt; 1-24 h; KAS-4 "after 24 hours ... only tetragonal leucite and orthorhombic kalsilite"; <15 min; <24 h; 800 °C | **ICDD no (N13, N14); KAS-4 24 h statement no (N16)**; rest yes |
| 12 | 900 °C; K-β-alumina peak ca. 7° 2θ; earlier scans from 10° 2θ; Raman 300-650, 700-1200, 525-625, 400-525, 900-1200; 1020 cm-1 | yes (Raman method_class wrong, F2) |
| 13 | Fig. 11b samples "fired at 1250°C for 24 hours"; hexagonal kalsilite [28] 300-400 and 900-1050 cm-1; modes 240-260 … 900-950; 1000-1200; K loss 1.5 and 2.7-3.6 wt% | modes and K loss yes; **24 h firing no (N17); [28] bands no (N18)** |
| 14 | heating rate Φ = dT/dt; KAS-1 at 5 °C/min excluded | 5 °C/min yes; Φ symbol advisory |
| 15 | KAS-1: kalsilite first "at around 850°C … for 1 hour"; Ea 579±8, 473±1, 364±1, 360±1, 548±1 kJ/mol | Ea yes (all digits match); **850 °C/1 h no (N19)** |
| 16 | **Eq. (3) KAlSiO4 → ¼K2O + ¼Al2O3 + ½KAlSi2O6** | **no (N20)** |
| 17 | **Eq. (4) 3/2 K2O + 11/2 Al2O3 → KAl11O17 + K2O↑; Eq. (5) KAlSiO4 + SiO2 → KAlSi2O6**; mullite 1000 °C; KAS-5 "1250°C for 1000 hours"; Li2SiO3 650 °C, 830 °C | 1000/650/830 yes (650/830 locator wrong, L10); **Eqs 4-5 no (N21, N22); KAS-5 1000 h XRD no (N23)** |
| 18 | KAS-3..5 at 1250 °C and KAS-1 at 850 °C: "between 24 and 500 hours … evaporation of potassium" | **no (N24)** |
| 19 | "by 100 kJ/mol"; 1250 °C; 1000 hours | yes (100 is consistent with 579-473 = 106 as printed rounding) |
| 20-26 | Acknowledgements; references 1-68 | bibliographic, not data |
Table 1, Table 3, Table 4 and Table 5 bodies: absent from this copy (typed absent, correct). Figures 1-13: absent panels. The extract records Figs 4-13 only; Figs 1-3 (structures, cited pp3-5) are advisory.

## B. Row-by-row check (record | field | extract | print | match)
### B1. Tables (tables/<sid>/), 7 CSV rows + 4 provenance records
| table | row | extract | print (PDF p) | match |
|---|---|---|---|---|
| t1 | all rows | body absent; "weighed stoichiometrically" | "All inorganic raw materials (Table 1) were weighed according to stoichiometry." (p6) | yes |
| t3 | all rows | body absent | "schedules summarised in Table 3" (p6), "(Table 3)" (p7) | yes |
| t4 | KAS-1 | no potassium loss, PDF p13 | "no potassium loss was observed after heat treatment at 1250°C" (p13) | yes |
| t4 | KAS-2 | same | same | yes |
| t4 | KAS-3 | 1.5 wt%, **locator empty** | "(1.5 wt%)" (p13) | value yes; **locator NO (L11)** |
| t4 | KAS-4 and KAS-5 | 2.7-3.6 wt% | "(2.7-3.6 wt%)" (p13) | yes |
| t5 | shape factor | "similar for all sample data" (p16) | p16 | yes |
| provenance t1/t3/t4/t5 | page 6/6/13/16 | as cited | yes (t3 is also cited on p7) |

### B2. `christopoulou_2019_k_loss_prose_values` (9)
treatment_temperature_C 1250 (yes); KAS-1_loss not_detected (yes); KAS-2_loss not_detected (yes); KAS-3 1.5 (yes); KAS-4/5 [2.7, 3.6] (yes); measurement_method XRF (yes);
composition_context (yes, p13); uncertainty not_stated (yes, none printed); **exposure_duration "not_stated_for_each_sample_in_prose": NO (F1)**.

### B3. `..._sample_preparation_and_compositions` (7): all yes against p6
compositions (five glasses, increasing K2O, tie-line; "their chemical compositions are presented in Figure 4", so figure-only), starting_materials (Table 1 absent), batching, melting
("submerged arc furnace for 1 hour at 2000°C"), casting ("poured onto a large steel plate"), annealing ("annealed at 550°C to relieve internal stresses"), atmosphere (air).

### B4. `..._missing_apparatus_and_unreported_run_facts` (8): all yes
No cell, orifice, background pressure, mass spectrometry, ionisation, activity or furnace calibration is printed anywhere on pp2-20. No typed absence here hides a printed value.

### B5. `..._heat_treatment_schedule` (16)
furnace electric (yes); atmosphere air (yes); temperatures [850, 950, 1250] (yes, p2/p10); long_term 1250 (yes); ramp 5 °C/min (yes); 1-1000 h (yes); 1-30 min (yes, pp6-7);
insertion at RT (yes); short-term insertion at 1250 °C (yes, p7); removal at temperature (yes); removal_accuracy_s [2, 5] (yes); removal_accuracy_min 5 / 24 h (yes); removal_accuracy_h 1 / [500, 1000] h (yes);
**removal_accuracy_s_applies_to_hold_minutes [1, 5, 15, 30]: NO (F4)**. The print reads "± 2s for 1min and 5min samples; ± 5s for 15min, 30min and 1h samples" (p6).
Locator page 6 is fine; the short-term sentence runs onto p7 (advisory: '6-7').

### B6. `..._xrf_and_fused_bead_method` (8)
instrument (yes); fused bead (yes); 1.0 / 0.002 / 10.0 / 0.002 (numerically yes, but the printed precision is "1.000 ± 0.002g" and "10.000 ± 0.002g"; advisory E5); bead making (yes);
calibration "identical across samples" (yes; printed p14: "the analysis settings (XRF calibration and fused bead sample making) were identical"; add the p14 locator).

### B7. `..._xrd_conditions` (12): all yes against p8
Empyrean/PANalytical; room temperature; Cu; 40 kV; 40 mA; [5, 100]° (advisory: the print says "normally in the 2θ range", qualifier dropped); 0.013°; ~70 s with the approximate flag (yes); reflection spinner; 0.25 Hz; mortar and pestle.

### B8. `..._dsc_conditions_and_reference` (13)
instrument NETZSCH STA 449 F5 Jupiter (yes, pp8-9); RT-1400 °C (yes); [5, 40] (yes); 1400 (yes); 50.0 mg (yes); 99.9% α-Al2O3 (yes); same mass (yes); 1450 °C for 15 h (yes);
cell, liner, atmosphere, T calibration, T uncertainty "not reported" (yes ×5: none printed). **Record locator page 8: NO (L8)**. Every number here is printed on PDF p9.

### B9. `..._activation_energy_results` (13)
579/8, 473/1, 364/1, 360/1, 548/1 kJ/mol: all ten match p15 digit by digit ("579±8 … 473±1 … 364±1 … 360±1 … 548±1"). The KAS-1 and KAS-5 phase labels match p15. The relation (Kissinger, Origin
"Fit Multi-Peaks") matches p7, but Eq. (1) itself is not carried (N25). **method_class model_derived: see F5.**

### B10. `..._numeric_background_context` (21)
1600 (p3), 1686 (p3), 635 (p3), 665 (p3), [655, 675] (p4), 1700 (p5), 800 (p11), 900 (p12), 1200 °C / 48 h (p11), 7° (p12), 10° (p12), 1000 °C (p17), 650/830 °C (p17):
all values yes. 525-625 / 400-525 / 900-1200 band assignments (p12, refs 53-55): yes as attributed.
**as_annealed_raman_band_ranges [[300,650],[700,1200]], raman_modes_reported_for_KAS3_to_KAS5, raman_high_frequency_modes [1000,1200]: values yes, class NO (F2).**
**locator_notes "PDF page 18: 650 C and 830 C": NO (L10)**; they are printed on p17. The notes also give no page for 800 (p11), 900, 7° or 10° (p12), and put >1700 on "page 6" when it is printed on p5 (advisory).
Record method_class/attribution: yes for the literature values, but see F2.

### B11. `..._reported_qualifications_and_directions` (59 quotes)
I re-read each quote against the page image for wording, direction, sign and number. **Every direction is printed as carried; there are no reversed comparatives.**
Every number inside the quotes matches (1000 h/1250 °C, 579/548, <5/<15/<30 min, 850/950/1250 °C, ~24 h/500 h, 1-24 h, 1020 cm-1, the Raman ranges, 1.5 and 2.7-3.6 wt%, 5 °C/min, 364/360, 100 kJ/mol).
Programmatic: 51 quotes are verbatim substrings of the text layer. The other 8 differ only by a page break, superscript reference numbers or º/°, except these:
- **#41 (p15)**: the extract reads "At a higher heating rate, the reaction …"; the print reads "At a higher Φ, the reaction in the sample requires less time." The sentence also starts on p14. **Wording NO (V1).**
- #11 (p10) silently drops "(ICDD 01-076-8737)" from inside the quote, and #23 (p11) drops "(Figure 10)". Mark the elisions with an ellipsis (advisory).
- #47 (p16) ends at "as a function of time." The print continues ", as also reported in the literature.27,43" (advisory; restore the attribution).
- #51 (p17) is a fragment with no subject (see D20).
Locators wrong (7, **L1-L7**):
| # | quote (start) | extract | printed on |
|---|---|---|---|
| 7 | "the formation of kalsilite is a transitional stage …" | PDF p5 | **p6** |
| 8 | "the prolonged heat treatment of natural or synthesized kalsilite …" | p5 | **p6** |
| 12 | "Sample KAS-2 formed only leucite so long-term …" | p9, sec. 3.1 | **p10** (top) |
| 28 | "Oxide glasses with high K2O contents …" | p12 | **p13** |
| 35 | "in the case of potassium loss it is unclear …" | p13 | **p14** |
| 49 | "Again, it is not confirmed in which chemical form …" | p16 | **p17** |
| 52 | "XRD results do not indicate the presence of residual silica …" | p17 | **p18** |
**Record method_class figure_only: NO (F6).**

### B12. `..._figures_without_numeric_values` (6)
figure_4 … figure_13 entries are correct: the panels are absent and no curve was digitised. Locator page 9: Fig. 4 is first cited on p6 and Figs 5-6 on p10 (advisory). Figs 1-3 are missing from the record (advisory).

### B13. Experiment and benches (10)
experiment method dta_dsc (yes); thermal_schedule.method "RT to 1400 C at 5 to 40 C/min" (value yes; **locator p8 NO, printed p9 (L9)**; experiment.locator and total_duration locator likewise);
total_duration_s unknown/not_published (yes: variable rates, no duration printed); bench DSC apparatus (yes), heating_method (yes), temperature_measurement.sensor and temperature_calibration.calibration
unknown/not_published (yes: "between room temperature and 1400ºC" is the only thermal statement and no sensor is named); XRF bench (yes); XRD bench (yes; the id spells `empirean`, advisory).
**No Raman bench (N6-N9).** **Migrated experiment conditions.temperature_K is typed unknown and sample.mass_kg is null although RT-1400 °C and 50.0 mg are printed (F7).**

### B14. Citation, asset, acceptance (9)
citation, year, DOI, sha256, licence statement (5) and migrator, evidence_for, validator, ledger test (4): yes. Advisories as in the acceptance table.

## C. Findings (22 mismatches), each a required change
**P0 (0).** No wrong number reaches a result, score or ledger. Every transcribed value matches the print, and the extract has no observations.

**P1: binding/provenance errors that would mislead if promoted (4 findings, 7 rows)**
- **F1** (1) `k_loss_prose_values.exposure_duration: not_stated_for_each_sample_in_prose` puts a typed absence where the page prints a value. The abstract (p2) prints "lose potassium upon prolonged heat
  treatment (1000 hours at 1250°C)"; the Conclusions (p19) print "lose potassium after prolonged heat treatment (1000 hours)"; p14 prints "temperature and heat treatment duration … were identical for all the samples".
  But p10 prints "Sample KAS-2 formed only leucite so long-term (after 24 hours) experiments were not performed", so "identical for all" cannot hold for KAS-2.
  Fix: bind the KAS-3/4/5 losses to 1250 °C / 1000 h with the p2/p19 locators, and add `source_internally_inconsistent:` for KAS-1/KAS-2 exposure (p14 vs p10).
- **F2** (3) Raman rows in `numeric_background_context` carry this study's own measurements under `quoted_attributed`, with the note "Other-study values are attributed background, not measurements in this source".
  The print shows them as the authors' own: "Raman spectra (Figure 11a) of as-annealed glass samples show the presence of Raman bands which occur in the ranges of ca. 300-650 cm-1, and 700-1200 cm-1" (p12);
  "The observed modes in the ranges of ca. 240-260 … 900-950 cm-1 for samples KAS-3, KAS-4 and KAS-5" (p13); "The presence of high frequency modes at 1000-1200 cm-1" (p13).
  Fix: move these three to an authors'-measurement record (measured_direct, figure-only spectra; samples as printed, with the Fig 11b samples "fired at 1250°C for 24 hours"). Keep 525-625/400-525/900-1200 as attributed [53-55].
- **F3** (2) Attribution stripped from the §1.3 quotes. #7 is Zhang et al.'s finding: "Zhang et al.36 reported that the formation of kalsilite is a transitional stage that leads to crystallization of leucite" (p6).
  #8 is "in some studies it is indicated that …27,43" (p6). Both are carried as unattributed authors' statements. Fix: quote from "Zhang et al." with [36], and add [27,43] to #8. Move both to a quoted_attributed home.
- **F4** (1) Removal-accuracy mapping. `removal_accuracy_s [2, 5]` applies to `[1, 5, 15, 30]` min, which drops the 1 h sample from the ±5 s group and leaves the pairing ambiguous.
  Print (p6): "± 2s for 1min and 5min samples; ± 5s for 15min, 30min and 1h samples; ± 5min for the 24h sample; and ± 1h for the 500h and 1000h samples." Fix: give one explicit pair per group.

**P2: class and encoding (3 findings), printed numbers not carried (27), statements not carried (26)**
- **F5** (1) `activation_energy_results.method_class: model_derived`. These are Kissinger reductions of the authors' measured DSC peak temperatures (p7 Eq. 1, p15), not an author model.
  Use `measured_reduced` with derivation.relation = Eq. (1) and inputs = exothermic peak temperatures T0 at each heating rate, typed unpublished/figure-only (Fig 12/13). If main's precedent keeps Kissinger outputs as model_derived, say so in the extract.
- **F6** (1) `reported_qualifications_and_directions.method_class: figure_only`. These are verbatim prose claims, not figure readings. Split them into the authors' own (quoted_unattributed) and the attributed ones (quoted_attributed: #7, #8 per F3, #47).
- **F7** (1) Experiment fields empty although printed. The migrated `christopoulou_2019_nonisothermal_dsc_series` has conditions.temperature_K typed unknown and sample.mass_kg null; the page prints "between room temperature
  and 1400ºC" and "50.0 mg samples" (p9). Fix: set the experiment's temperature interval (RT lower bound as printed/typed) and sample mass 5.00e-5 kg, located at p9.
- **N1-N27**: printed numbers not carried (list below).
- **D1-D26**: directional/qualifying statements not carried (list D).

**P3: locators and wording (12 rows)**
- **L1-L7**: the seven quote pages in B11.
- **L8**: `dsc_conditions_and_reference` page 8 → 9.
- **L9**: experiment locator / thermal_schedule.method / total_duration_s page 8 → 9 (the DSC paragraph is pp8-9; every program number is on p9).
- **L10**: background locator_notes "PDF page 18: 650 C and 830 C" → p17.
- **L11**: t4.csv KAS-3 row has an empty locator → "PDF page 13".
- **V1**: quote #41: restore "At a higher Φ" and locate it as pp14-15.

## N. Printed numbers not carried (27). Carry each with its locator (figure-only excepted; none of these is figure-only)
1. p7 Eq. (2) n = (2.5/ΔT) · T0²/(Ea/R) (the constant 2.5; ΔT = FWHM of the exotherm), the defining relation for the cited Table 5 Avrami n.
2. p8 "having integer values of 1, 2 or 3 corresponding to one-, two- or three-dimensional entities".
3. p8 "nucleation with values of either 0 or 1, where 0 corresponds to instantaneous nucleation and 1 to sporadic nucleation".
4. p8 "larger values of n are expected when increased nucleation rates occur, such as in the diffusion-controlled reaction (>2.5)" (the basis of the p16 Table 5 interpretation).
5. p8 "… or the case of polymorphic transformation (>4)".
6. p9 Raman laser "emitting at 532 nm".
7. p9 "at 300 mW output power".
8. p9 "a 1200 lines/mm grating monochromator".
9. p9 "the collection optic was set at 50× objective" (instrument "Thermo Scientific DXR2 microscope spectrometer"; add a Raman bench).
10. p9 "the first crystallization event occurred for all the samples at approximately 1250°C" (DSC; the stated basis for choosing 1250 °C).
11. p10 tetragonal leucite "ICDD 01-076-8737".
12. p10 orthorhombic kalsilite "ICDD 00-033-0988".
13. p11 hexagonal kalsilite "ICDD 00-012-0134".
14. p11 hexagonal potassium aluminium oxide "ICDD 04-010-8954" (KAl11O17, K-β-alumina).
15. p10 "Figure 7 shows the crystallization behaviour of sample KAS-1 as a function of temperature for 1 hour of heat treatment". The 850/950/1250 °C quotes (#13-#15) lack this 1 h binding.
16. p11 "After 24 hours of heat treatment at 1250°C only tetragonal leucite and orthorhombic kalsilite are present" (KAS-4).
17. p13 "All samples were fired at 1250°C for 24 hours before being analysed" (Raman Fig 11b).
18. p13 "Hexagonal kalsilite28 is expected to show a sharp band at around 300-400 cm-1 and a group of low intensity bands in the range 900-1050 cm-1" (attributed [28]).
19. p15 "in the case of leucite (KAS-1) XRD shows that kalsilite will develop first, at around 850°C … the sample must be heat treated at this temperature for 1 hour, which explains the differences between the two measurements."
20. p16 Eq. (3) KAlSiO4 → ¼ K2O + ¼ Al2O3 + ½ (KAlSi2O6) (balanced as printed).
21. p17 Eq. (4) 3/2 K2O + 11/2 Al2O3 → KAl11O17 + K2O↑ (balanced as printed).
22. p17 Eq. (5) KAlSiO4 + SiO2 → KAlSi2O6.
23. p17 "consistent with the XRD results presented in this study for KAS-5 samples heat treated at 1250°C for 1000 hours".
24. p18 "XRF and XRD analyses for samples KAS-3 to KAS-5 at 1250°C and sample KAS-1 at 850°C demonstrated that kalsilite will nucleate first; and then between 24 and 500 hours of heat treatment the development of leucite, and in some cases potassium aluminium oxide (K-β-alumina) is accompanied by the evaporation of potassium."
25. p7 Eq. (1) ln(a/T0²) = −Ea/(R·T0) + C (a = heating rate, T0 = exothermic peak temperature in K); the slope of ln(a/T0²) vs 1/T0 gives Ea. Only the name is carried.
26. p2 abstract "heat treated at 850ºC, 950ºC and 1250ºC for times ranging from 5 minutes to 1000 hours". The 5-minute lower bound is not carried and conflicts with "1 minute" (p7) and "1min" samples (p6); see E3.
27. p5 "two types of six-membered rings, UUDUDD and UUUDDD, in a 2:1 ratio (Figure 3)" (background, Gregorkiewitz et al. [27]).

## D. Printed directional/qualifying statements not carried (26). Carry each verbatim with its locator
1. p9: "the selected heat treatment temperature of 1250°C is below the liquidus temperature for all compositions studied, hence all compositions were expected to crystallise."
2. p9: "Prolonged heat treatment altered the phases present in some of the samples as discussed below."
3. p10: "This suggests that leucite is a stable crystalline phase in terms of time at 1250°C."
4. p10: "orthorhombic potassium aluminium silicate, kalsilite51, (ICDD 00-033-0988) and tetragonal leucite peaks form simultaneously, as suggested by the phase diagram wherein the composition of KAS-3 lies on a phase field boundary."
5. p11: "Capobianco et al.43 obtained orthorhombic kalsilite by annealing natural hexagonal kalsilite at 1200°C in open Pt crucibles for 48 hours, which is not inconsistent with our results." (the Pt crucible and the comparison are missing)
6. p11: "For sample KAS-4, Figure 9 illustrates that at the beginning of crystallization most of the diffraction peaks are matched by hexagonal kalsilite."
7. p11: "The unstable nature of kalsilite is also described in other studies29,30,36 which show that it probably acts as a precursor of leucite."
8. p12: "It is important to stress that our study confirms that during the transformation of kalsilite to leucite, K-β-alumina is also formed. To our knowledge this has not been previously reported, and its formation may modify the properties of the products produced via the kalsite-to-leucite conversion." ("kalsite" as printed)
9. p12: "… reported XRD data started from 10o 2θ, which may explain why K-β-alumina has not previously been associated with the kalsilite-leucite phase transition." (the number is carried; the qualifying clause is not)
10. p13: "Figure 11b shows Raman spectra demonstrating the effects of heat treatment on glass samples which exhibit stronger, sharper Raman bands consistent with the existence of crystalline phases."
11. p13: "Samples KAS-1 and KAS-2 produce Raman spectra corresponding to tetragonal leucite57. As confirmed by XRD, samples KAS-3 and KAS-4 form orthorhombic kalsilite whilst sample KAS-5 forms hexagonal kalsilite."
12. p14: "These results suggest that chemical stability is strongly connected with the starting composition and hence the amount of kalsilite formed at 1250°C, since apart from the starting chemical composition all of the remaining experimental conditions (electric furnace, temperature and heat treatment duration) and the analysis settings (XRF calibration and fused bead sample making) were identical for all the samples under investigation."
13. p14: "even though the XRF results are presented as oxides" (the basis of the Table 4 K-loss values; quote #35 starts after it).
14. p14: "As depicted in Figure 12, DSC curves show one strong exothermic peak related to the main crystallization event."
15. p15: "… then increasing again to 548±1 kJ/mol for sample KAS-5 (stoichiometric kalsilite), indicating that the formation of kalsilite is kinetically favoured."
16. p15: "It is believed59,60 that if the melt and the crystal are identical in composition, the crystal growth rate is controlled by interface reactions, but if the composition of the melt differs to that of the crystal, both interdiffusion and the interface reactions can control crystal growth which may explain the lower activation energies for the KAS-2 sample and particularly for the KAS-3 and KAS-4 samples."
17. p16: "The three-dimensional nucleation and crystal growth mechanism for kalsilite and leucite samples has also been reported in other studies.36,37,61"
18. pp16-17: "This mechanism shows that after prolonged heat treatment, kalsilite will completely transform to leucite, particularly in the case of stoichiometric kalsilite. In this case some of the ejected potassium will react with alumina to form K-β-alumina, and the remainder will evaporate, according to Eq. 4."
19. p17: "The formation of potassium aluminium oxide, or K-β-alumina, has been confirmed by XRD for KAS-5 samples when heat treated at 1250°C and seems to be more applicable only for the samples that are stoichiometrically close to kalsilite."
20. p17: "The formation of K-β-alumina, as described in Eq. 4, requires significantly more alumina than potassium oxide; thus if this excess of alumina is not present, the ejected potassium may evaporate without K-β-alumina forming, as occurs in the case of sample KAS-4 sample, where even though the potassium loss is higher than for sample KAS-5, K-β-alumina is not formed."
    Carried quote #51 keeps only the last clause and so loses its subject. The full sentence is the only printed ordering inside the 2.7-3.6 wt% range: **K loss KAS-4 > KAS-5**. Carry it in the K-loss record too.
21. p18: "Kalsilite will transform to leucite by reacting with SiO2 from the amorphous phase. This reaction will create in the system an excess amount of potassium and aluminium. In addition, some of the potassium will react with the remaining aluminium forming potassium aluminium oxide (K-β-alumina) and the rest will evaporate as described in Eq.4."
22. p18: "XRF (Table 4) reveals that sample KAS-1 will not have any elemental alteration as a function of heat treatment time."
23. p18: "Indeed, Zhang et al.36 determined by the Kissinger method44 that the activation energy for crystallization of kalsilite is lower than that of leucite, which is consistent with the results of this work."
24. pp18-19: "According to Abbot67 orthorhombic kalsilite is more likely to be a metastable phase."
25. p19: "Nucleation and growth kinetics can be altered by the addition of nucleating agents29,31,68 such as leucite nanocrystals, which lead to the elimination of kalsilite as an intermediate crystallization product, and the formation of leucite at lower temperatures."
26. p19: "Heat treatment of glasses in the ternary K2O-Al2O3-SiO2 system has revealed that kalsilite is an unstable phase at 1250°C and that it behaves as an intermediate precursor of leucite." and "For both mechanisms, in the case of the stoichiometric kalsilite sample (KAS-5) the excess potassium evaporates, and some reacts with the additional aluminium oxide existing in the system (KAS-5) to form KAl11O17 (K-β-alumina)."

## E. Advisory (no count)
- E1. `page` in every locator is the PDF page index (cover = 1). The printed manuscript page is one less. The report says this, but the extract says it only inside the quote strings.
  Put `pdf_page_index` beside `page`, or state the convention in `extraction.method`, so that `page: 8` cannot be read as printed page 8.
- E2. The 1686 °C leucite melting point (p3) has no citation on the page. It sits under `quoted_attributed`; `quoted_unattributed` fits better.
- E3. Source-internal inconsistencies to mark (`source_internally_inconsistent:`): the abstract's "5 minutes to 1000 hours" (p2) vs "1 minute to 1000 hours" (p7); "identical … heat treatment duration" (p14) vs KAS-2 not run beyond 24 h (p10);
  author "Alan H. Jones" (p2) vs "JONES, Hywel" (p1).
- E4. XRD "normally in the 2θ range between 5 and 100°" (p8): keep "normally".
- E5. XRF masses: keep the printed precision "1.000 ± 0.002 g" and "10.000 ± 0.002 g".
- E6. Mark the elisions in #11 and #23, and restore "as also reported in the literature.27,43" in #47.
- E7. Figures record: add Figs 1-3 (leucite/kalsilite structures, pp3-5). Locate Fig. 4 at p6 and Figs 5-13 at pp10-15.
- E8. Bench id `christopoulou_2019_xrd_empirean`: the print spells "Empyrean". Cosmetic.
- E9. The heat-treatment schedule locator should be '6-7'. The XRF calibration statement is printed on p14.

## Count basis
Basis of the 202 rows: tables 7 CSV rows + 4 provenance; K-loss 9; preparation 7; missing apparatus 8; schedule 16; XRF 8; XRD 12; DSC 13; Ea 13; background 21; statements 59; figures 6;
experiment and benches 10; citation/asset 5; acceptance 4.
Basis of the 22 mismatches: F1 1 + F2 3 + F3 2 + F4 1 (P1, 7) + F5 1 + F6 1 + F7 1 (P2, 3) + L1-L11 11 + V1 1 (P3, 12). No transcribed value differs from print.
The 27 printed numbers not carried are N1-N27. The 26 statements not carried (D1-D26) are reported separately, as in the sibling reviews.

## Required changes (FIX-FIRST), in order
1. F1: bind the K-loss rows to 1250 °C / 1000 h (p2, p19) and mark the p14 vs p10 inconsistency. Add D20 (KAS-4 loss > KAS-5) to the K-loss record.
2. F2: move the authors' own Raman bands/modes to an own-measurement record, and add a Raman bench (N6-N9).
3. F3: restore the Zhang et al. [36] and [27,43] attributions for quotes #7 and #8.
4. F4: one explicit removal-accuracy pair per group, including 1 h at ±5 s (p6).
5. F5/F6: method_class for the Kissinger Ea (measured_reduced + Eq. 1 derivation, or main's precedent) and for the statements record (quoted_unattributed / quoted_attributed, not figure_only).
6. F7: experiment temperature interval (RT-1400 °C) and sample mass 50.0 mg at p9.
7. N1-N27: carry Eqs (1)-(5), the Avrami criteria, the Raman instrument settings, the four ICDD cards, and the duration/temperature statements, each with its locator.
8. D1-D26: carry verbatim with locators.
9. L1-L11, V1: fix the locators and the #41 wording.
10. Advisory E1-E9.
Then re-run on green 61ec839: migrator + finalize (hard 0), `validate_literature_extracts.py --check-fidelity-match`, and `tools/test_ledgers_valid.py`.

!COMPLETE: rev-christopoulou-2019-jace102-508-aam — FIX-FIRST, pages read 26, rows checked 202, mismatches 22, printed numbers not carried 27
