# Fidelity review (first review): corpus extract allen-1994-reduction-lunar-mare-soil @ 83f01066e60facb4b4e22d07db81075bb2f6d5f6

Reviewer: regolith-empirical corpus-review seat (read-only). Written 2026-10-05 ~22:10 ET.
Branch `hunt/allen-1994-reduction-lunar-mare-soil` on the corpus mirror (mac-studio-256-1:Repos/regolith-corpus.git).
`git fetch origin hunt/allen-1994-reduction-lunar-mare-soil` → FETCH_HEAD = 83f01066e60facb4b4e22d07db81075bb2f6d5f6 (matches the assignment).
Commits over mirror main: 7fc0040c (claim), 2340e3ba (extract), 83f01066 (typed after-run composition absence). Changed files:
`extracts/allen-1994-reduction-lunar-mare-soil.yaml` (+317) and `ledger/allen-1994-reduction-lunar-mare-soil.yaml` (+21). No tables/ CSV.

Setup: sparse corpus worktree on the Mac at `~/Repos/regolith-corpus/worktrees/rev-allen-1994-reduction-lunar-mare-soil`
(`git worktree add --no-checkout` detached at the tip; sparse-checkout `/*`, `!/raw/*/*`, `!/text/*/*`, `/raw/*/sidecar.yaml`, `/raw/<sid>/*`, `/text/<sid>/*`; 46 MB);
there is no `text/<sid>/` directory on the branch. Green readers: read-only detached clone `~/ci-scratch/regolith-green-ro` at
61ec839da3ba288c5df4a80f6d3ef142bd8ab461, Python `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH=~/ci-scratch/regolith-green-ro`.
`engines/engines.local.toml` EXISTS in the green clone (and in ~/Repos/regolith-pyrolysis-simulator). `df -g /Users` at start: 64 GB available.
tools/build_index.py and tools/migrate_pilot_extracts.py were not run.

Source: Allen C.C. (Lockheed ESC), Morris R.V. and McKay D.S. (NASA/JSC), "Reduction of Lunar Mare Soil and Pyroclastic Glass",
LPSC XXV, pp. 23–24 (1994). 2 PDF pages (scanned; NASA accession stamp N94-35398 and handwritten archive marks on p. 23).
Both pages rendered with `pdftoppm -r 250 -png` (2119×2750 px), and every page was read as a full page and as four overlapping
full-width strips; the reference line (p. 24) and Lunar Samples paragraph (p. 23) were also read as zoomed crops.

## 1. Inventory of the source (every table, equation and numeric statement)

| page | item | carried? |
|---|---|---|
| 23 | Abstract: "900-1100°C" | yes (abstract verbatim statement) |
| 23 | Lunar Samples: samples 75061 (high-Ti mare soil), 74220 ("the Apollo 17 'orange soil'"), mineralogy, citation (1) | yes, with findings M3 and O3 |
| 23 | Experimental: vertical tube furnace, weight change continually monitored, "as in previous tests with lunar simulants (2)" | furnace + monitoring yes; the "(2)" method lineage NOT carried (O2) |
| 23 | "Samples (200-300 mg)", "as received", hot furnace, flowing hydrogen "for three hours", fO2 "below the iron-wüstite buffer" | yes (0.0002–0.0003 kg; 10800 s; IW upper bound) |
| 23 | Analysis: SEM, EDS, XRD, FeMS, VSM | yes |
| 23 | Mare soil: "three experiments at 900-1050°C", "first 20 minutes", "after the first hour", "2.6-3.0 wt.%" | yes |
| 23 | "47-54% of the Fe2+", "1.9-2.2% of the starting sample weight as oxygen", "911°C" | yes (method_class findings M4) |
| 23 | Ilmenite/olivine/agglutinate/pyroxene statements, "900 to 1050°C", "1050°C experiment", agreement with (3) | yes |
| 23 | "They also provide an extreme "end member" for studies of reduction as a cause of lunar soil maturation." | NOT carried (O1) |
| 24 | Glass: "four experiments at 900-1100°C", "first 10-20 minutes", "2.2-5.4 wt.%" | yes |
| 24 | "32-68% of the Fe2+", "1.7-3.3% ... as oxygen", gamma iron | yes (method_class finding M5) |
| 24 | Devitrification: "less that 1 µm" at 900°C, "well over 10 µm at 1100°C", "In the 1100°C sample" | yes (verbatim, source typo "that" kept) |
| 24 | Closing: constraints on pyroclastic eruptions; ideal feedstock for oxygen production (4) | yes |
| 24 | References (1)–(4) | yes, with finding M1 (ref 1 venue) |
| 24 | Fig. 1 FeMS mare soil 75061 1050°C; Fig. 2 ilmenite 75061 1050°C; Fig. 3 FeMS glass 74220 1100°C; Fig. 4 glass 74220 1100°C | yes, figure_only, not digitised (correct). Velocity-axis ticks and micrograph scale bars are figure-only. |

No printed tables and no equations. No KEMS/vapour-pressure/activity content: the brief's KEMS facts are correctly typed not_applicable.
Archival marks (N94-35398, handwritten "S3-91 ABS ONLY 2943", "p. 2") are not source data; carrying N94-35398 in the sidecar is optional.

## 2. Row-by-row check (table | row | extract | print | match)

### 2a. Numeric scalars in the four aggregate context records (20)
| record | field | extract | print (locator) | match |
|---|---|---|---|---|
| allen_1994_mare_soil_weight_loss_range | experiment_count | 3 | "three experiments" (p23 Results-Mare Soil) | yes |
| same | temperature_C_range[0] | 900 | "900-" | yes |
| same | temperature_C_range[1] | 1050 | "1050°C" | yes |
| same | bulk_mass_loss_wt_pct_range[0] | 2.6 | "2.6-" | yes |
| same | bulk_mass_loss_wt_pct_range[1] | 3.0 | "3.0 wt.%" | yes |
| allen_1994_mare_soil_alpha_iron_reduction_range | experiment_count | 3 | "three experiments" | yes |
| same | Fe2plus_reduced_to_alpha_iron_pct_range[0] | 47 | "47-" | yes |
| same | [1] | 54 | "54%" | yes |
| same | equivalent_starting_sample_oxygen_loss_wt_pct_range[0] | 1.9 | "1.9-" | yes |
| same | [1] | 2.2 | "2.2%" | yes |
| allen_1994_pyroclastic_glass_bulk_mass_loss_range | experiment_count | 4 | "four experiments" (p24 Results-Pyroclastic Glass) | yes |
| same | temperature_C_range[0] | 900 | "900-" | yes |
| same | [1] | 1100 | "1100°C" | yes |
| same | bulk_mass_loss_wt_pct_range[0] | 2.2 | "2.2-" | yes |
| same | [1] | 5.4 | "5.4 wt.%" | yes |
| allen_1994_pyroclastic_glass_alpha_iron_reduction_range | experiment_count | 4 | "four experiments" | yes |
| same | Fe2plus_reduced_to_alpha_iron_pct_range[0] | 32 | "32-" | yes |
| same | [1] | 68 | "68%" | yes |
| same | equivalent_..._oxygen_loss_wt_pct_range[0] | 1.7 | "1.7-" | yes |
| same | [1] | 3.3 | "3.3%" | yes |

### 2b. Method class and prose fields of the aggregate records (15)
| record | field | extract | print | match |
|---|---|---|---|---|
| mare weight loss | method_class | measured_direct | weight "continually monitored" (p23) | yes |
| mare weight loss | method_as_printed | continuous weight monitoring | p23 Experimental | yes |
| mare weight loss | mass_loss_direction_as_printed | increased slightly with increasing temperature | "increasing slightly with increasing temperature" | yes (direction correct) |
| mare weight loss | time_course_as_printed | "...first 20 minutes, then fell more slowly, remaining essentially constant after the first hour." | "...first 20 minutes and then fell more slowly, ..." | yes (nit N1: not verbatim, "and" dropped) |
| mare alpha-Fe | method_class | measured_direct | VSM-derived fraction of starting Fe2+; oxygen loss is "equivalent to" | **NO (M4)** |
| mare alpha-Fe | basis | VSM; oxygen equivalent | "The VSM data indicated ... This was equivalent to" | yes |
| mare alpha-Fe | derivation_status | authors' equivalence; no per-run values | correct | yes |
| glass weight loss | method_class | measured_direct | same apparatus | yes |
| glass weight loss | method_as_printed | continuous weight monitoring | p23 | yes |
| glass weight loss | mass_loss_direction_as_printed | increased steadily with temperature | "increasing steadily with temperature" | yes |
| glass weight loss | time_course_as_printed | rapid first 10–20 minutes, then more gradual | "rapid for the first 10-20 minutes, and then became more gradual" | yes |
| glass alpha-Fe | method_class | measured_direct | as M4 | **NO (M5)** |
| glass alpha-Fe | reduction_direction_as_printed | percentage increased with temperature | "with the percentage increasing with temperature" | yes |
| glass alpha-Fe | basis | VSM; oxygen equivalent | p24 | yes |
| glass alpha-Fe | derivation_status | authors' equivalence | p24 | yes |

### 2c. Experiment facts (12 per experiment × 2 = 24)
| experiment | fact | extract | print | match |
|---|---|---|---|---|
| mare-soil-75061 | method | tga | "vertical tube furnace, with weight change continually monitored" | yes (nearest token; amendment proposed) |
| mare-soil-75061 | sample.form | "Apollo 17 lunar mare soil sample 75061; used as received" | "Sample 75061 is a high-Ti mare soil ..." — "Apollo 17" is printed only for 74220 | **NO (M3)** |
| mare-soil-75061 | mass_kg | interval 0.0002–0.0003 | "Samples (200-300 mg)" | yes |
| mare-soil-75061 | printed_composition | high-Ti mare soil; pyroxene, plagioclase, ilmenite, minor olivine, agglutinates, trace glass | "...agglutinates, and a trace of glass (1)" | yes |
| mare-soil-75061 | temperature_K | 1173.15–1323.15 | 900–1050 °C | yes |
| mare-soil-75061 | total_pressure_Pa | unknown/not_published | none printed | yes |
| mare-soil-75061 | sweep_gas | H2; flow, pH2 unknown/not_published | "flowing hydrogen"; no flow printed | yes |
| mare-soil-75061 | regime | unknown/not_published | none printed | yes |
| mare-soil-75061 | total_duration_s | 10800 | "for three hours" | yes |
| mare-soil-75061 | thermal_schedule.method | placed in hot furnace, flowing H2, 3 h | "placed into the hot furnace and reduced in flowing hydrogen for three hours" | yes |
| mare-soil-75061 | fO2_control.channel | gas_mix | H2 atmosphere | yes (acceptable token) |
| mare-soil-75061 | fO2_control.buffer | below IW, not at buffer | "The fO2 was kept below the iron-wüstite buffer." | yes |
| glass-74220 | method | tga | same | yes |
| glass-74220 | sample.form | "Apollo 17 orange soil sample 74220; pyroclastic glass; used as received" | "the Apollo 17 "orange soil," an essentially pure pyroclastic glass deposit (1)" | yes |
| glass-74220 | mass_kg | 0.0002–0.0003 | 200-300 mg | yes |
| glass-74220 | printed_composition | essentially pure pyroclastic glass; orange spheres wholly glassy; black spheres partly recrystallized to olivine, ilmenite, spinel | "Orange spheres, which comprise the bulk of this sample, are completely glassy. Black spheres are the same glass partly recrystallized to olivine, ilmenite, and spinel." | yes for what is carried; omission O3 |
| glass-74220 | temperature_K | 1173.15–1373.15 | 900–1100 °C (p24) | yes |
| glass-74220 | total_pressure_Pa | unknown/not_published | none | yes |
| glass-74220 | sweep_gas | H2; unknowns | as above | yes |
| glass-74220 | regime | unknown | none | yes |
| glass-74220 | total_duration_s | 10800 | three hours | yes |
| glass-74220 | thermal_schedule.method | as above | as above | yes |
| glass-74220 | fO2 channel | gas_mix | H2 | yes |
| glass-74220 | fO2 buffer | below IW | as above | yes |

### 2d. Bench facts (11)
apparatus_family (vertical tube furnace + continuous weight monitoring: yes); heating_method (hot furnace: yes); cell_material_and_liner
not_applicable (no cell is described: yes); orifice_diameter_m and orifice_channel_length_m not_applicable (yes, yes);
intensity_to_pressure_calibration, ionization_cross_sections_and_source, ionization_energy_eV, multiplier_and_isotope_corrections
not_applicable (no mass spectrometry: yes ×4); temperature_measurement.furnace_temperature unknown/not_published (no sensor named
anywhere on pp. 23–24: yes); temperature_calibration.calibration unknown/not_published (no calibration or uncertainty printed: yes).

### 2e. Method/characterisation context (5)
characterization (SEM+EDS on bulk samples and thin sections; XRD; FeMS for oxidation states and relative Fe abundances; VSM for
alpha-Fe: yes, p23 "Bulk samples and thin sections were examined by SEM and EDS. Minerals were identified by XRD. Iron Mössbauer
spectroscopy (FeMS) was used to indicate the oxidation states and relative abundances of iron in the glass and mineral phases. The
abundances of alpha iron metal in the reduced samples were also determined by vibrating sample magnetometry (VSM)."); initial_materials
(no chemical analysis/purity; "as received": yes); sample_composition_after_run unknown/not_published (no post-run bulk chemistry
printed — only phase observations, which are carried as statements: yes); activity_ions_and_reference_states not_applicable (yes);
uncertainty unknown/not_published (no uncertainty printed: yes). Nit N2: this record's method_class is measured_direct although it is a
methods description.

### 2f. Figure context (4)
Fig. 1 "FeMS spectrum of mare soil 75061, reduced at 1050°C" (yes); Fig. 2 "Ilmenite from mare soil 75061, reduced at 1050°C" (yes);
Fig. 3 "FeMS spectrum of pyroclastic glass 74220, reduced at 1100°C" (yes); Fig. 4 "Pyroclastic glass from 74220, reduced at 1100°C"
(yes). All figure_only, digitized false.

### 2g. Verbatim statements (31), each re-read against the page image, directions included
Abstract (6/6 match, p23). Mare-soil qualifying statements (13/13 match, p23; "911°C", "900 to 1050°C", "1050°C" correct; "much less
evidence", "became smaller with increasing temperature", "increasing concentrations ... as reduction temperature increased" all in the
printed direction). Pyroclastic-glass statements (12/12 match, p24; "less that 1 µm", "900°C", "well over 10 µm at 1100°C",
"In the 1100°C sample", "ever greater amounts" correct). The extract writes Fe2+ for the printed superscript Fe²⁺ — acceptable.

### 2h. Cited comparison and references (6)
| field | extract | print | match |
|---|---|---|---|
| comparison | close agreement with previous reduction experiments utilizing crushed lunar basalt (3) | p23 last paragraph | yes |
| quoted_from (attribution read by the migrator) | "Allen et al. (1994), reference (3)" | reference (3) is "Gibson et al., 1994, submitted JGR" (p24) | **NO (M2)** |
| ref (1) | "(1) Heiken and McKay, 1974, PLPSC 5, 843." | "(1) Heiken and McKay, 1974, PLSC5, 843" | **NO (M1)** |
| ref (2) | "(2) Allen et al., 1993, Icarus 104, 291." | "(2) Allen et al., 1993, Icarus, 104, 291" | yes |
| ref (3) | "(3) Gibson et al., 1994, submitted JGR." | same | yes |
| ref (4) | "(4) Hawke et al., 1990, PLPSC 20, 249." | "PLPSC20, 249" | yes |

### 2i. Citation and asset (3)
Citation (Allen C.C., Morris R.V., McKay D.S., LPSC XXV, pp. 23–24, 1994): yes. year 1994: yes. corpus_sha256
2d49c7a0dd8a24b8f8823e32883a6988242b3bb4f86e135e37769dcc3e5b7bdb = `shasum -a 256` of the PDF and = sidecar sha256; bytes 176214 = sidecar: yes.
Licence: the sidecar says "not recorded — verify before any redistribution"; the extract makes no licence claim (consistent).
`rg "/Users/|/private/"` over the extract, ledger and sidecar: no hits.

## 3. Experiment facts and typed absences (quoted sentences relied on)
- "The samples were reduced in a vertical tube furnace, with weight change continually monitored, as in previous tests with lunar
  simulants (2). All samples were run "as received." Samples (200-300 mg) were placed into the hot furnace and reduced in flowing hydrogen
  for three hours. The fO2 was kept below the iron-wüstite buffer." (p23 Experimental/Analytical). Every typed absence (sensor,
  calibration, background pressure, H2 flow/pH2, regime, uncertainty, after-run composition) checked: none of these is printed. No
  typed absence hides a printed value.
- Activities: none in the source; no standard states to check. No measured_reduced/model_derived rows, so there is no derivation to check;
  but see M4/M5: the oxygen-loss values are an authors' equivalence (a reduction of the VSM data), not a direct measurement.

## 4. Acceptance checks (all against green 61ec839da)
- Migrator: `cd ~/ci-scratch/regolith-green-ro; PYTHONPATH=$PWD .venv-python -c "Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/allen-1994-reduction-lunar-mare-soil.yaml); finalize()"`
  → 0 observations, 2 experiments, 1 bench, 13 context rows; **hard issues after finalize: 0** (total issues 0). One queue entry
  "extract yielded no observations" (expected for a context-only extract; its source_path is the runtime path of my worktree, not stored in the extract).
- Payload survival (serialised migrated work): all 20 numeric context scalars are numbers; temperature intervals 1173.15–1323.15 / 1173.15–1373.15 K,
  mass 0.0002–0.0003 kg, duration 10800 s, H2 sweep gas with typed unknowns, IW buffer text, gas_mix channel, total_pressure unknown/not_published,
  cell/orifice not_applicable, temperature_measurement / temperature_calibration named located unknowns — all present and non-empty.
- `evidence_for()`: measured_direct, figure_only, quoted_attributed → none unknown.
- Fidelity validator: `PYTHONPATH=~/ci-scratch/regolith-green-ro python tools/validate_literature_extracts.py --check-fidelity-match --show-warnings <worktree>/extracts/allen-1994-reduction-lunar-mare-soil.yaml`
  (run from the green clone, extract path passed explicitly) → `OK: 1 extract file(s) valid`, exit 0.
- Corpus worktree `python -m pytest -c /dev/null -q -p no:cacheprovider tools/test_ledgers_valid.py` → 687 passed.
- Duplicate check: no sibling extract carries this source's numbers (`rg 75061|74220` hits only lpi-lunar-soils.yaml, the 74220 data
  sheet, and nasa-cea-thermo numeric coincidences). Not a duplicate. Optional: cross-reference extracts/lpi-lunar-soils.yaml for 74220.

## 5. Findings

Mismatches (5):
- **M1** p24 References: ref (1) is printed "Heiken and McKay, 1974, PLSC5, 843"; the extract (line 268) writes "PLPSC 5, 843".
- **M2** p23 last paragraph + p24 References: the quoted_attributed record's attribution field `quoted_from` (line 265) reads
  "Allen et al. (1994), reference (3)"; the attributed work is reference (3) = "Gibson et al., 1994, submitted JGR".
- **M3** p23 Lunar Samples: "Apollo 17" is printed only for 74220 ("Sample 74220 is the Apollo 17 'orange soil'"); for 75061 the page
  prints only "Sample 75061 is a high-Ti mare soil". The extract adds "Apollo 17" to 75061 in sample.form (line 68) and phase labels
  (lines 149, 164, 221, 227, 278). True in fact, but not printed in this source.
- **M4 / M5** p23 Results-Mare Soil and p24 Results-Pyroclastic Glass: `allen_1994_mare_soil_alpha_iron_reduction_range` (line 169) and
  `allen_1994_pyroclastic_glass_alpha_iron_reduction_range` (line 198) are measured_direct, but both quantities are reductions of the VSM data:
  the Fe2+ fraction is the VSM alpha-Fe abundance normalised to the (unprinted) starting Fe2+, and the oxygen loss is printed as
  "This was equivalent to the loss of 1.9-2.2% [1.7-3.3%] of the starting sample weight as oxygen". The record's own derivation_status says so.

Printed statements not carried (non-numeric; required):
- **O1** p23 last paragraph: "They also provide an extreme "end member" for studies of reduction as a cause of lunar soil maturation." Absent from the extract.
- **O2** p23 Experimental/Analytical: "as in previous tests with lunar simulants (2)" (method lineage to Allen et al., 1993, Icarus 104, 291). Absent.
- **O3** p23 Lunar Samples: "Orange spheres, which comprise the bulk of this sample, are completely glassy" — the "comprise the bulk"
  clause is dropped from 74220's printed_composition (line 114); and both sample descriptions carry the printed citation "(1)" (Heiken and McKay 1974), which is not recorded.

Nits (no count): N1 mare-soil `time_course_as_printed` (line 160) is not verbatim ("minutes and then fell more slowly"); N2 method/characterisation
context method_class measured_direct for a methods description (line 212); optional sidecar note of NASA accession N94-35398.

## 6. Required changes (FIX-FIRST)
1. M1: line 268 → `'(1) Heiken and McKay, 1974, PLSC5, 843.'` (p24, References).
2. M2: line 265 → attribute to `Gibson et al. (1994), submitted JGR — reference (3)` (p23 final paragraph; p24 References).
3. M3: drop "Apollo 17" from 75061's sample.form (line 68) and phase labels (149, 164, 221, 227, 278), or keep it only in a located note
   flagged as not printed in this source (p23 Lunar Samples).
4. M4/M5: re-class the two alpha-iron/oxygen-equivalent records (lines 169, 198) as measured_reduced (or split the oxygen-equivalent range into
   its own record as measured_reduced), with the printed relation "equivalent to the loss of ... of the starting sample weight as oxygen"
   and input "VSM alpha-iron abundance relative to starting Fe2+" (p23 Results-Mare Soil; p24 Results-Pyroclastic Glass). If the context
   validator refuses measured_reduced without parents, keep the class the validator accepts and record the reduced status in a located note,
   and list it under AMENDMENTS PROPOSED.
5. O1: carry the "end member ... lunar soil maturation" sentence verbatim (p23 final paragraph).
6. O2: carry "as in previous tests with lunar simulants (2)" with attribution to Allen et al., 1993, Icarus, 104, 291 (p23 Experimental/Analytical).
7. O3: restore "which comprise the bulk of this sample" in 74220's printed_composition and record that both sample descriptions cite (1) Heiken and McKay 1974 (p23 Lunar Samples).
8. Nits N1/N2 at the fixer's discretion (make N1 verbatim).
After the fix: re-run migrator+finalize (hard issues 0), the fidelity validator and tools/test_ledgers_valid.py.

VERDICT ON COMMIT: FIX-FIRST

rows checked 119, mismatches 5, printed numbers not carried 0

!COMPLETE: rev-allen-1994-reduction-lunar-mare-soil — FIX-FIRST, pages read 2, rows checked 119, mismatches 5, printed numbers not carried 0
