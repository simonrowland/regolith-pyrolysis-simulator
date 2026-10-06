# REVIEW (first review of record): tenkate-2010-vapor-instrument @ f3ece32ec6cc3f4f3aa4f95e6eac8cd6b7de3d26

Reviewer: regolith-empirical corpus review seat (batch13, 2026-10-05 ~23:00 ET). Read-only review: nothing tracked was written and nothing was pushed to the corpus mirror.
Source: ten Kate, I.L. et al. (2010). VAPoR – Volatile Analysis by Pyrolysis of Regolith – an instrument for in situ detection of water, noble gases, and organics on the Moon.
Planetary and Space Science 58, 1007–1017. doi:10.1016/j.pss.2010.03.006. 11 pp (printed pp. 1007–1017).

VERDICT ON COMMIT: FIX-FIRST

rows checked 36, mismatches 7, printed numbers not carried 6

## Setup and provenance
- Mirror is `mac-studio-256-1:Repos/regolith-corpus.git`, branch `hunt/tenkate-2010-vapor-instrument`. After `git fetch`, the tip is `f3ece32ec6cc3f4f3aa4f95e6eac8cd6b7de3d26`, which is the assigned sha.
  It has 2 commits over merge-base 3dab24d7: fd0a876c (claim) and f3ece32e (extract). Both are authored by Simon Rowland <simon@simonrowland.com>, and neither has an AI or co-author trailer.
  The changed files are `extracts/<sid>.yaml` (+942), `ledger/<sid>.yaml` (+14), `tables/<sid>/t1..t4.csv` plus provenance, and `raw/<sid>/sidecar.yaml`.
  The sidecar change is one line. It replaces an absolute `/Users/...` `provenance.original_path` with the corpus-relative path, which is correct.
- The review worktree is SPARSE: `~/Repos/regolith-corpus/worktrees/rev-tenkate-2010-vapor-instrument`. I created it with `git worktree add --no-checkout --detach` at f3ece32e and then ran
  `sparse-checkout set --no-cone '/*' '!/raw/*/*' '!/text/*/*' '/raw/*/sidecar.yaml' /raw/<sid>/* /text/<sid>/*`. It used 51 MB and was removed after delivery.
  Mac free disk at the start was 62 GB. I did not run build_index.py or migrate_pilot_extracts.py.
- Green is the read-only clone `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`. I ran `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` with `PYTHONPATH=<green-ro>`.
  `engines/engines.local.toml` EXISTS in green-ro. None of the checks below use it.
- Page images: I rendered all 11 pages with `pdftoppm -r 220 -png` (1819x2426 px) and READ every one. I also read full-resolution crops of these regions:
  Table 1 (p.1008), Table 2 (p.1010), Table 3 and the §3 right column (p.1011), the Fig. 4 strip (p.1012), the Table 4 block for 61221,7 (p.1015),
  the right column of p.1007, the right column of p.1010 and the left column of p.1016. pdftotext was used only as a lead.
- The PDF sha256 is `cad604c54f8982d5eb6f91f122ca1b257252d6994b220333226220b86154df5f`. It matches the sidecar `sha256` and `census_sha256` (size 865446).
  The citation and DOI match the print. The licence/access field reads `access: publisher`, which is unchanged from main.
- `rg '/Users/|/private/'` over the extract, ledger, sidecar and tables/<sid>/ finds no matches.

## Acceptance checks (all pass: FIX-FIRST is a fidelity verdict, not a tooling one)
- Migrator: I ran `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(Path('extracts/tenkate-2010-vapor-instrument.yaml'))` and then `finalize()`, with cwd = the sparse corpus worktree.
  Result: `result.validation.issues` = 0, so hard issues after finalize = 0. `registry_issues` 0, `evidence_fallthrough` {}.
  Output: 1 work, 1 bench, 4 experiments, 0 observations, 16 context rows. The queue has 1 entry, "extract yielded no observations", which is expected for this figure-only source.
- `evidence_for('figure_only')` returns EvidenceClass.FIGURE_ONLY with no fallthrough. It is the only method_class in the extract, and there are no reduced rows.
- Fidelity: `cd <green-ro>; PYTHONPATH=<green-ro> python tools/validate_literature_extracts.py --check-fidelity-match <sparse worktree>/extracts/tenkate-2010-vapor-instrument.yaml`
  printed `OK: 1 extract file(s) valid` and exited 0. The script is green's; the extract path points at the corpus worktree.
- Corpus ledger: `python -m pytest -q -p no:cacheprovider tools/test_ledgers_valid.py` in the worktree gave **708 passed**.
- Payload survival, read from the migrated `result`:
  - Bench `cell_materials` holds C_graphite and SiO2, both located. `cell_material_and_liner` is a typed unknown, not_published (see M5).
  - `geometry` orifice diameter and channel length are typed unknowns. `temperature_measurement` keeps sensor, controller, relay, power_source and uncertainty as named located members.
    `temperature_calibration.calibration` is a typed unknown.
  - `other_facts` has the intensity_to_pressure_calibration and ionization unknowns, plus the chamber pressures `1e-8` and `1e-7` mbar as exact points (see M7).
  - The 4 experiments keep sample `mass_kg` 0.00006, 0.000008, 0.00006 and the blank. Every one migrates `approximate: false` (see M7).
    `pressure_environment.total_pressure_Pa` is a typed unknown on all 4 experiments.
  - Context rows: the Table 1 VAPoR/SAM rows survive numerically (100/500 mm³ approximate, 6/2 ovens, 74 cups). The Table 3 VAPoR/RGA rows survive (1–1000 Da, 1e-4, ~1 W, <0.4 kg, <1000 cm³; 1–300 Da, ~4e-2, 60 W, 2.7 kg, 855 cm³).
- Chamber pressure home: I confirm the extractor's choice. Green's reader treats an approximate chamber value under a Knudsen-cell bench as a chamber condition, not an in-cell total (`_pressure_hit_is_chamber_condition`).
  Keeping the two "~" chamber pressures as located bench facts is therefore acceptable. I raise no finding on that.

## 1. Inventory of tables, equations and numeric statements (page: carried?)
- p.1007 (abstract, §1): "up to 1400 °C in vacuo" is carried. Viking "up to 500 °C" duplicates Table 1, so it is carried.
  **TEGA "up to 1000 °C" is not carried (N3)**, and it contradicts Table 1 (TEGA ambient–950 °C).
- p.1008: **Table 1** is carried, with 2 blank cells where values are printed (M1, M2) and one attribution that is not printed (M6). The text numbers 30 ppm, ~100 km, 4.1–3.8 Ga and 4 billion years are carried, but the 4-Ga value is mis-bound (M4).
  The MSL launch year 2011 and the "six gas chromatographic (GC) columns" are not carried. These are background with no bearing on the data, so they are optional and not counted.
- p.1009: The Fig. 1 axes (δD, δ13C) are figure-only and correctly not digitised. The 50 K sample return temperature is carried. "6 individual pyrolysis oven crucibles" agrees with Table 1 (6), so it is carried.
- p.1010: **Table 2** is carried in full and correct (16 rows). The ~10–100 mg, 1400 °C, ~40 kg, ~200 W, ~20 dm³, ~10–15 kg, 40–60 W, 2–535 amu, m/Δm ~500 and ~10⁻⁴ values are all carried.
  **"some noble gases that are only released at temperatures in excess of 1200 °C under vacuum" is not carried (N4).**
- p.1011: **Table 3** is carried in full and correct (8 rows × 6 instruments). The 1200 °C prototype maximum and 1400 °C crucible-under-test values are carried.
  The Pfeiffer, SRS RGA 300, Watlow, Omega, Variac and Pt-10%Rh hardware is carried.
  **The Faraday cup detector sensitivity "2 × 10⁻⁴ A/torr" is not carried (N1).** The alumina heater with tungsten heater wire, and the stainless-steel tube in a water-cooled stainless-steel jacket, are not carried (M5).
  The cleaning steps of 10 min, 10 min, 99.5% ethanol, 2 h and 500 °C are carried, but **"95% HPLC grade n-hexane" was carried as "grade n-hexane" (N2)**.
- p.1012: Sample facts are carried: 64801,53, station 4, <1 mm, 310 Myr from ²¹Ne, ~60 mg, the 8 and 60 mg aliquots, Murchison 28 Sept 1969, CM2, USNM 6650,2, 6 g, <150 µm and 68 mg.
  Also carried: the XRD-PSD phases 58.5 / 22.8 / 7.4 / 2.2 / 2 %; ~1×10⁻⁸ and ~1×10⁻⁷ mbar; 1 h; 25 °C; 5 °C/min; 1200 °C; and 1000 °C.
  Fig. 4 has scale bars (5 cm, 3.5 cm, 0.6 cm, 3 cm). These are figure annotations and are not counted.
- p.1013: Figs. 5–7 are figure-only and correctly not digitised. The m/z 29, 39, 23, 57, 78, 91, 39/43 at ~1100 °C, 250–500 °C, 1000–1200 °C and 37 years are carried.
  **The Fig. 6 caption m/z list "(12, 14, 15, 16, and 17)" is not carried (N6).** **The CO⁺/N₂⁺ apportionment fragments "12 and 16 for CO⁺, and 14 for N₂⁺" are not carried (N5).**
- p.1014: Figs. 8–9 are figure-only. The 61221,7 and 68401,53 comparison, 14 / 6 / 5 / 10 °C/min and H₂⁺ background are carried. The 68401,53 sample-number misprint is correctly flagged.
- p.1015: **Table 4** is carried as a qualitative chart (6 rows). The chart has no printed axis values, and none were digitised, which is correct. One gas label is missing (M3). O₂ >1270 is carried.
- p.1016: Discussion and conclusion; the 1200 °C is carried. The grant numbers are not data. pp.1016–1017 are references only.

## 2. Row-by-row check (every row and every cell of all 4 tables was checked; columns as in the CSVs; Table 4 print column lists the distinct gas labels printed in each row)
### Table 1 (p.1008), 6 rows: Viking, TEGA, COSAC, Ptolemy, SAM, VAPoR
| row | csv | print | match |
|---|---|---|---|
| Viking | 50; 200; 350; 500 / Ceramic / 2 (inner) / 19 / ~60 mm3 / 3 / Biemann 1977 | 50, 200, 350, and 500 °C / Ceramic / 2 mm: inner / 19 mm / ~60 mm³ / 3 / a | yes |
| TEGA | ambient–950 / Nickel…platinum…ceramic coating / 7.2 / 21.6 / ~38 µl / 8 / Boynton 2001 | ambient–950 °C / "Nickel wrapped with platimun [sic] resistance wire and ceramic coating" / 7.2 / 21.6 / ~38 µl / 8 / b | yes (the print's "platimun" typo was silently normalised; cosmetic) |
| COSAC | ambient–600 / Pt…glass / 3 / 6 / (blank) / **(blank)** / Goesmann 2007 | ambient–600 °C / Pt…glass / 3 mm / 6 mm / – / **2** / c | **NO: M1** |
| Ptolemy | ambient–800 / Pt…glass / 3 / 6 / (blank) / **(blank)** / Wright 2007 | ambient–800 °C / Pt…glass / 3 mm / 6 mm / – / **3** / d | **NO: M2** |
| SAM | ambient–1000 / quartz cup in alumina… / – / – / ~500 mm3 / 2 / Mahaffy 2008 / 74 cups | ambient–1000 °C / … / "~500 mm³" (printed on the diameter line) / "2 ovens, 74 sample cups" / e | yes (the volume placement is explained in the provenance) |
| VAPoR | ambient–1200 / Pt wire in zirconia / – / – / ~100 mm3 / 6 / **ten Kate et al. (2010)** | ambient–1200 °C / "Platinum resistance wire encased in Zirconia." / ~100 mm³ / 6 / **no footnote** | values yes; **attribution not printed: M6** |

### Table 2 (p.1010), 16 rows: all match
| row | csv | print | match |
|---|---|---|---|
| Atmospheric volatiles | not applicable | Not applicable | yes |
| H2O; H2; CO2; CO; N2; SO2 | 0–1400 (a,b,c) | 0–1400 a,b,c | yes |
| 13C/12C of CO2 | 100–1400 (b) | 100–1400 b | yes |
| 15N/14N in N2 | 600–1400 (b) | 600–1400 b | yes |
| HDO/H2O | 0–1400 (none) | 0–1400 | yes |
| He; Ne; Ar | 300–1400 (b,e) | 300–1400 b,e | yes |
| isotope ratios He | 200–500 (d) | He: 200–500 d | yes |
| isotope ratios Ar | 300–1400 (b,e) | Ar: 300–1400 b,e | yes |
| 13C/12C CO2 organics combustion | 400–500 (f) | 400–500 f | yes (printed inside the Noble Gases block; CSV category follows the print) |
| volatile hydrocarbons | 300–1000 (b) | 300–1000 b | yes |
| water–ice in regolith | 0–100 | 0–100 | yes |
| O2 | 1100–1400 (d) | 1100–1400 d | yes |
| HCN/NH3 | 100–900 (b) | HCN/NH3: 100–900 b | yes |
| H2S | 700–1300 (c) | H2S: 700–1300 c | yes |
| 3He rel. abundance | 200–500 (d) | He: 200–500 d | yes |
| 3He rel. abundance (dup.) | 200–500 (d) | He: 200–500 d (printed twice) | yes |

### Table 3 (p.1011), 8 property rows × 6 instruments: all 48 cells match
| row | csv (Strofio / SAM / COSAC / Ptolemy / VAPoR / RGA) | print | match |
|---|---|---|---|
| Type | TOF / Scanning Quadrupole / TOF / Ion trap MS / Reflectron TOF / Scanning Quadrupole | same | yes |
| Mass range | 1–60 / 2–535 / 1–1500 / 12–100; 40–150 / 1–1000 / 1–300 Da | same | yes |
| Electron emitter | Thermionic / Thermionic / Thermionic / Nanotips / CNT Field / Thermionic | same | yes |
| Arrayed emitters | No / No / No / Yes / Yes / No | same | yes |
| Sensitivity/N2 | 1.4E-1 / 5E-3 / – / – / 1E-4 / ~4E-2 | 1.4×10⁻¹ / 5×10⁻³ / – / – / 1×10⁻⁴ / ~4×10⁻² | yes (the dashes are blanks, not 0) |
| Power avg W | ~1 / 14.5 / 8 / 10 / ~1 / 60 | same | yes |
| Mass kg | 1.7 / 1.3 / 4.9 / 4.5 / <0.4 / 2.7 | same | yes |
| Volume cm3 | 1000 / 3000 / 4700 / 198 / <1000 / 855 | same | yes |

The column header "COSAG Rosetta" is a print typo, and it was correctly normalised to COSAC. The footnote "Modified and updated from King et al. (2008)" is carried.

### Table 4 (p.1015), 6 sample rows (gas labels; there is no numeric axis)
| row | csv labels | print labels | match |
|---|---|---|---|
| Apollo 16 64801,53 (this work) | H2O; CO2; CO/N2; He; SO2; H2S; S/O2; COS; CS2; CH4 | H2O, CO/N2, CO2, CH4, SO2, H2S, S/O2, COS, CS2, He | yes |
| Apollo 14 14163,178 | H2O; CO2; CO/N2; H2; He; N2 | H2O, CO/N2, N2, CO2, H2, He | yes |
| Apollo 15 15601,31 | H2O; CO2; CO/N2; H2; He; O2 >1270 | H2O, CO2, CO/N2, O2 > 1270, H2, He | yes |
| Apollo 16 61221,7 | H2O; CO2; CO/N2; CH4; HCN; NO; SO2; organics | H2O, CO/N2, CO2, CH4, HCN, NO, SO2, **H2S**, Organics | **NO: M3 (H2S omitted)** |
| Murchison (this work) | H2O; CO2; CO/N2; CH4; SO2; H2S; S/O2; CS2; organics | same set | yes |
| Murchison (Simoneit 1973) | H2O; CO2; CO; CH4; SO2; H2S; COS | same set | yes |

## 3. Experiment and bench facts (checked against the page)
| fact | extract | print (quoted) | verdict |
|---|---|---|---|
| method | vacuum_chamber_pyrolysis; context knudsen_effusion + evolved_gas_ms | "Samples were pyrolyzed using a modified Knudsen cell … direct line of sight from the heated sample to the ionization region of the RGA" (p.1011 §3) | ok |
| cell | cell_materials C_graphite, SiO2; **cell_material_and_liner unknown/not_published** | "a quartz sample holder (Fig. 4B) placed in a graphite crucible that is mounted inside an alumina heater threaded with tungsten heater wire. The heater is held in place by a stainless steel tube in a stainless steel water-cooled jacket." (p.1011 §3) | **M5**: a typed absence where the page prints the build. Al2O3 and W (both closed CellMaterial tokens) are missing. |
| orifice diameter / channel length | unknown/not_published | not printed | ok |
| intensity→pressure calibration | unknown; the context quotes the no-calibration statement | "The purpose of this work was not to calibrate the instrument; therefore, no attempt has been made to quantify the amount of evolved gases" (**p.1016**) | ok (bench locator p.1016 is right; the context quote has the wrong page, L1) |
| ionisation cross-section / energy / multiplier | unknown | not printed | ok |
| detector sensitivity | **absent** | "sensitivity of the faraday cup detector: 2 × 10⁻⁴ A/torr" (p.1011 §3) | **N1** |
| temperature measurement | Pt-10%Rh thermocouple on heater; Watlow SD 6C-HF-AA-AARG; Omega SSR240D25; Variac 033-2558 | same, verbatim (p.1011 §3) | ok |
| temperature calibration / uncertainty | unknown | not printed | ok |
| chamber background | 1e-8 and 1e-7 mbar (bench other_facts, exact points); context marks them approximate | "pumped down to a pressure of ~1 × 10⁻⁸ mbar prior to heating … the pressure inside the chamber increased to ~ 1 × 10⁻⁷ mbar" (p.1012 §3) | values ok; **"~" dropped in bench: M7** |
| thermal schedule | 298.15 K start, 0.08333 K/s, 1473.15 K end | "The start temperature was 25 °C and the samples were heated at a rate of 5 °C per min up to 1200 °C." (p.1012) | ok |
| Apollo 16 sample | 0.000060 kg; <1 mm; 64801,53 | "Approximately 60 mg of Apollo regolith" (p.1012 §3) | value ok; **migrates `approximate: false`: M7** |
| Murchison aliquots | 8 mg, 60 mg; 68 mg of 6 g; <150 µm | "two separate Murchison meteorite powdered aliquots (8 and 60 mg)"; "The 6 g interior fragment was crushed and sieved to <150 µm, from this 68 mg was allocated" (p.1012) | ok |
| Murchison mineralogy (cited XRD-PSD) | 58.5 / 22.8 / 7.4 / 2.2 / 2 | "tochilinite/cronstedtite (58.5%), serpentine (22.8%), olivine (Fo100 (7.4%), Fo80 (2.2%), Fo50 (2%))" (p.1012 §4.2) | ok |
| holder cleaning | 10 min DI water, 10 min "grade n-hexane", 10 min 99.5% ethanol, 2 h at 500 °C | "10 min in deionized water (Cole–Palmer IonXchanger), followed by 10 min in 95% HPLC grade n-hexane (Fisher Scientific), and 10 min in 99.5% absolute 200 proof ethanol" (pp.1011–1012) | **N2** (95% HPLC dropped) |
| blank | experiment vapor-empty-holder-blank (Fig. 9) | "Fig. 9 shows a procedural blank of the system taken before the first sample was analyzed." (p.1012) | ok |
| activities / standard states | not applicable | there are no activities in this paper | ok |

## 4. Standard states and reduced rows
There are no activities, no reduced rows and no model rows; every experimental result is a curve. The extract correctly has 0 observations, and each figure is carried as figure_only context without digitisation.
The swapped captions of Figs. 1 and 2 are correctly identified. I checked both: the p.1009 plot is δD vs δ13C under the gas-flow caption, and the p.1010 diagram is the gas-flow diagram under the organic-sources caption.

## FINDINGS: required changes (FIX-FIRST)
**Mismatches (7)**
- **M1** `tables/<sid>/t1.csv` COSAC `number_of_ovens` is blank, but p.1008 Table 1 prints **2**. Set it to 2. Also update the extract's Table 1 context if it lists rows.
- **M2** `t1.csv` Ptolemy `number_of_ovens` is blank, but p.1008 Table 1 prints **3**. Set it to 3.
- **M3** `t4.csv` row "Apollo 16, 61221,7" (Simoneit 1973) omits **H2S**. p.1015 Table 4 prints an H2S band below the high-temperature SO2 band in that row. Add H2S.
- **M4** In the extract context `tenkate_numeric_background_context`, `lunar_hydrogen_reservoir_age_span_Ga: 4` is mis-bound. p.1008 says "the ¹⁵N/¹⁴N ratios on the lunar surface, which may have varied significantly over the last 4 billion years".
  Rename it (e.g. `lunar_surface_15N_14N_variation_timescale_Ga: 4`) and keep its locator p.1008.
- **M5** Bench `cell_material_and_liner` is typed unknown/not_published, but p.1011 §3 prints the cell build: the quartz sample holder sits in a graphite crucible, mounted inside an alumina heater threaded with tungsten heater wire, held by a stainless-steel tube in a water-cooled stainless-steel jacket.
  Make it a located value quoting that sentence. Add `Al2O3` (alumina heater) and `W` (tungsten heater wire) to `cell_materials`, each with a locator note saying they are heater parts, not sample-contacting. Carry the stainless-steel tube/jacket as located context.
  The orifice remains a typed unknown.
- **M6** `t1.csv` VAPoR `attribution` = "ten Kate et al. (2010)" is not printed (the VAPoR column has no footnote). Use "this work (no footnote printed)" or leave it blank.
  Optionally, transcribe TEGA "platimun" as printed with [sic] in notes.
- **M7** The printed "~" was dropped on 3 values. The bench `other_facts` chamber pressures 1e-8 and 1e-7 mbar are printed "~1 × 10⁻⁸" and "~ 1 × 10⁻⁷" (p.1012). The Apollo 16 `sample.mass_kg` 0.000060 is printed "Approximately 60 mg" (p.1012) and migrates as `approximate: false`.
  Mark all three as approximate in their value payloads (the `kind`/`approximate` form the reader accepts), not only in notes and context.

**Printed numbers not carried (6)**
- **N1** p.1011 §3: "sensitivity of the faraday cup detector: 2 × 10⁻⁴ A/torr". Carry it as a located bench/detector fact or other_fact (unit A/torr).
- **N2** p.1011 §3: "10 min in 95% HPLC grade n-hexane (Fisher Scientific)". The extract reads "grade n-hexane". Restore "95% HPLC grade" and the vendors (Cole–Parmer IonXchanger DI water, Fisher Scientific), and mention the ultrasonic bath.
- **N3** p.1007 §1: "The TEGA ovens heated polar regolith samples up to 1000 °C". Carry it as located context and flag it as source-internal inconsistency with Table 1 (TEGA ambient–950 °C, p.1008). Do not change either value.
- **N4** p.1010: "releasing oxygen and some noble gases that are only released at temperatures in excess of 1200 °C under vacuum". Carry it verbatim as a directional numeric statement.
- **N5** p.1013: "an estimate about the majority of CO⁺ or N₂⁺ in the CO⁺/N₂⁺ ratio could be made based on analysis of the fragments of the CO⁺ and N₂⁺ mass spectra (12 and 16 for CO⁺, and 14 for N₂⁺). Similar analysis of the Apollo 16 sample did not provide conclusive informative about the CO⁺/N₂⁺ ratio."
  Carry it verbatim. It is data-qualifying, and it explains the "CO<N2" and "CO>N2" labels in Fig. 5 (Murchison).
- **N6** p.1013 Fig. 6 caption: "methane contribution in its other fragments (12, 14, 15, 16, and 17) is too small to be distinguishable from other fragments at these m/zs". Carry it in the figure_only context for Figs. 5–8. It is the reason methane is shown at m/z 13.

**Qualifying or directional statements not carried (required by brief items 4 and 6)**
- **S1** p.1016: "…should make it possible to identify some of the species (e.g. Ne) that we are currently unable to detect in the Apollo regolith sample using the VAPoR breadboard." This is a non-detection that qualifies the noble-gas data.
- **S2** pp.1012–1013: "Previous evolved gas analyses of lunar samples showed similar results, which were attributed to bursting of gas-containing vesicles (Gibson and Johnson, 1971) or other traps, or high temperature nitrides (Flory et al., 1972)." This is the authors' interpretation of the >1000 °C m/z 12/16/28/29 rise.
- **S3** p.1014: "…could explain the broader evaporation rates of some of the fragments in the earlier Apollo 16 and Murchison data" (heating rate 14 or 6 °C/min vs 5 °C/min).
- **S4** p.1014: the antecedent of the carried "This most likely explains the low temperature evolved SO2…" sentence. It reads: 61221,7 is "rich in complex volatiles" and "unique"; 64801 is "dark soil"; 61221 comes "from Station 1 on the rim of Plum Crater and 64801 from Station 4 in a shielded position from South Ray Crater".
- **S5** p.1015: "even without the getter as demonstrated in these experiments, we can still measure solar wind implanted He."

**Locator errors (L1, 9 quotes; fix the page, keep the text)**
- p.1012, not 1013. All are in the §5 first paragraph, which starts on p.1012:
  - "The mass spectrometer used in this study does not have sufficient resolution…"
  - "Only the evolved gas patterns of the 8 mg Murchison sample are shown…"
  - "The 60 mg Murchison sample caused saturation of the RGA…"
  - "The partial pressures of the evolved gases varied considerably…"
  - "Both samples have a similar increase in m/z 12 … above 1000 °C …" (both copies)
  - also experiment `vapor-murchison-60mg-saturated` locator (p.1013 §5 → p.1012 §3 / §5)
- p.1016, not 1013: "The purpose of this work was not to calibrate the instrument…"
- p.1014, not 1013: "a faster heating rate causes considerable widening…"; "neither CS2+ nor COS+ have been reported for Apollo samples…"

**Clean-up (not counted)**
- 4 quotes are duplicated in `tenkate_data_qualifications_and_comparisons`: the 6 g/68 mg fragment, "Both samples have a similar increase…", the minor 39/43 peak, and organics at 250–500 °C. Keep one copy of each.
- The `earlier_comparison_heating_rates_C_per_min: [14, 6]` list loses its binding. Bind each rate to its samples: 14 °C/min for the other Apollo 16 and the Murchison sample, 6 °C/min for Apollo 14 and 15 (p.1014).

## What is right (no change)
- Tables 2 and 3 are transcribed exactly: 16 + 8 rows, every cell correct, including the 1.4×10⁻¹ / 5×10⁻³ / 1×10⁻⁴ / ~4×10⁻² exponents and the dashes as blanks.
- The figure curves (Figs. 5–9, in torr) are not digitised, and the authors' no-calibration statement is carried. The sample masses, mineralogy, thermal program and chamber pressures are correct.
- The 68401,53 misprint and the Fig. 1/2 caption swap are both correctly flagged. There are no absolute paths, the sha256 matches, and the migrator/finalize, fidelity validator and ledger tests all pass.

!COMPLETE: rev-tenkate-2010-vapor-instrument — FIX-FIRST, pages read 11, rows checked 36, mismatches 7, printed numbers not carried 6
