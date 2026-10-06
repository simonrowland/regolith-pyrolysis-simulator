# Fidelity review (first review): corpus extract richter-2008-cai-like-liquids-lpsc-abstract @ 570192a9312a3b1a0f4a398da178b9e6c0c5bc8d

**Reviewer:** regolith-empirical corpus review seat (REQ-corpus-batch11, item A3). **Written:** 2026-10-05 ~22:40 ET.
**Branch:** `hunt/richter-2008-cai-like-liquids-lpsc-abstract` on `mac-studio-256-1:Repos/regolith-corpus.git`. I fetched it and `origin/hunt/<sid>` = `570192a9312a3b1a0f4a398da178b9e6c0c5bc8d`, which matches the REQ.
**Commits over mirror main:** `3cc8373b` (claim), `570192a9` (extract). Files changed: `extracts/<sid>.yaml` (+265), `ledger/<sid>.yaml` (+36), `raw/<sid>/sidecar.yaml` (citation + original_path).
**Corpus worktree:** a sparse, detached worktree on Simon's Mac at `~/Repos/regolith-corpus/worktrees/rev-richter-2008-cai-like-liquids-lpsc-abstract`. It used `--no-checkout` and a sparse set containing only raw/<sid>, text/<sid> and all sidecar.yaml. I did not run build_index.py or migrate_pilot_extracts.py. I removed the worktree after delivery.
**Green / readers:** `~/ci-scratch/regolith-green-ro` at `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, used read-only and clean. I ran it with `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python` and `PYTHONPATH=~/ci-scratch/regolith-green-ro`. **`engines/engines.local.toml` exists** in that clone.
**Source:** PDF `raw/<sid>/<sid>.pdf`, 2 pages, 464521 bytes. Its sha256 is `a6f675576c888f2d0d20f2a3092c053a5d8ec378c4fa164984a77aca93d1364d`, which matches the sidecar `sha256` and `census_sha256`. I rendered both pages with `pdftoppm -r 250` and read every quadrant of both pages, plus zoomed crops of Figs 1–3 and the header.

VERDICT ON COMMIT: FIX-FIRST

rows checked 62, mismatches 3, printed numbers not carried 0

There are three mismatches and one required carry of an uncarried qualifying statement (R1–R4 below). All four are small, context-only edits. There are no numeric observation rows. Every printed number in the source sits somewhere in the extract, but one axis set is bound to the wrong figure and includes unprinted tick labels (M1).

## 1. Inventory of the source (every table, equation and numeric statement)

| Page / place | Item | Carried? |
|---|---|---|
| p1 header | Title "ELEMENTAL AND ISOTOPE FRACTIONATION OF CAI-LIKE LIQUIDS BY EVAPORATION IN LOW PRESSURE H2"; authors F. M. Richter, F.-Z. Teng, R. A. Mendybaev, A. M. Davis, R. B. Georg; "1385.pdf"; LPS XXXIX (2008) | yes (extract source.citation + sidecar citation, corrected from the census metadata; correction is right) |
| p1 Elemental Fractionations | "about 10^-7 bars H2" threshold (< vacuum-like; > rate ∝ sqrt(pH2)) | yes (`pressure_threshold_bar: 1.0e-7` + both quotes) |
| p1 Elemental Fractionations | "Figure 1 illustrates ... in vacuum to that in 2×10^-4 bars of hydrogen" | number yes (via Fig 1 caption); sentence not quoted (see R4, minor) |
| p1 equation | Hertz–Knudsen J_i = γ_i P_i^sat / sqrt(2π m_i R T) and its symbol definitions | yes |
| p1 Fig 1 | caption 2×10^-4 bar, T=1500°C, "about two orders of magnitude larger that ..."; legend J_Mg (circles), J_Si (squares), PH2 = 1.87x10^-4 bar, vacuum; axes x 4.6–5.6, top T 1900–1500 °C, y −6.0…−10.0 log rate (moles cm^-2 s^-1) | numbers yes. Legend species J_Mg/J_Si not named (recommended). |
| p1 Isotopic Fractionations | fO2 ≈ solar-gas statement [4]; "wealth of high-precision data ... vacuum evaporation residues [3]"; "we remeasured ... residues that were evaporated in 1.87×10^-4 bars of hydrogen. The vacuum experiments are described in [2] ... Isoprobe multicollector ICPMS ... Al/Mg ... dissolved chips" | 1.87e-4 yes; fO2 quote yes; ICPMS methods yes. **The "remeasured" sentence, "vacuum experiments are described in [2]" and the "wealth"/"key question" sentences are not carried (R4).** |
| p2 text | Rayleigh slope 1−α definition; H2 vs vacuum "approximately the same ... slightly above the trend"; "much less fractionating (i.e., closer to one)"; Summary (P>10^-7 bars; Mg and SiO; effect on Mg α "quite small"); Si isotope work in progress [5] | yes, all quoted. One quote is not verbatim (M2). |
| p2 Fig 2 | α = 0.98797±0.00022 (red fit); α = 0.97980 (blue, ideal); legend "This work" (solid) / "Richter et al. [2]" (open); axes x −ln(f^24Mg) ticks 0,1,2,3; y 1000 ln(R/R0) ticks 0,10,20,30,40; caption 1.87×10^-4 bars H2 | α, ±, 0.97980, caption and axis labels: yes. **Tick labels are carried in the Fig 3 row, not the Fig 2 row, with extra unprinted ticks (M1).** |
| p2 Fig 3 | caption 1500 °C, 1.87×10^-4 bars; "Ideal" value α = (23.985/24.986)^0.5; legend vacuum [3], fit to vacuum [3], CAI crystallization range (grey bands), PH2=1.87x10^-4 bar; axes x 4.5–6.5 (10000/T(K)), top T 2100…1300 and a clipped "120"; y 1000(1−α) 6–20 | yes (formula, caption, axes in `richter_2008_numeric_comparative_claims.figure_3_axes_as_printed`). "CAI crystallization range" not carried (recommended). |
| p2 References | [1]–[5] (journal, volume, page) | yes (`additional_numeric_context`) |
| Tables | none in the source | correct: 0 tables, 0 CSVs |

I found no figure-only curve that was digitised.

## 2. Row-by-row check (extract value | print | match)

| # | Extract row / field | Extract | Print (page) | Match |
|---|---|---|---|---|
| 1 | α row `alpha` | 0.98797 | "α = 0.98797±0.00022" (p2 Fig 2 box) | ✓ |
| 2 | α row `uncertainty` + `uncertainty.alpha` | 0.00022 | ±0.00022 (p2 Fig 2) | ✓ |
| 3 | α row `temperature_C` | 1500 | "evaporated at 1500˚C" (p2 Fig 3 caption) | ✓ |
| 4 | α row `hydrogen_pressure_bar` | 0.000187 | 1.87×10^-4 (p2 Fig 2/3 captions) | ✓ |
| 5 | α row `method_class` | measured_reduced | the authors' fitted slope of their measured series | ✓ (correct class) |
| 6 | α row derivation relation | slope = 1 − α (Rayleigh) | p2 "line with slope equal to 1–α" | ✓ |
| 7 | α row derived_from / inputs | fig2 figure_only id | an id that exists in this extract | ✓ |
| 8 | α row `composition` | "exact composition not stated" | no composition printed | ✓ |
| 9 | Fig2 row `ideal_alpha_as_printed` | '0.97980' | "α = 0.97980" (p2 Fig 2 blue box) | ✓ |
| 10 | Fig2 row axes labels | -ln(f24Mg); 1000 ln(R/R0) | as printed (p2 Fig 2) | ✓ |
| 11 | Fig2 row caption | quoted | p2 Fig 2 caption, word for word | ✓ |
| 12 | Fig2 row attribution / symbols | open = [2]; small solid = new | caption + legend "This work"/"Richter et al. [2]" | ✓ |
| 13 | Exp row `hydrogen_pressure_bar.value` | 0.000187 | "evaporated in 1.87×10^-4 bars of hydrogen" (p1 Isotopic Fract.) | ✓ |
| 14 | Exp row `temperature_C.value` | 1500, locator Fig 3 | p2 Fig 3 caption | ✓ |
| 15 | Exp row gas_species | H2 | p1 | ✓ |
| 16 | Exp row Mg isotope method | GV Instruments Isoprobe MC-ICPMS, U Chicago, as in [3] | p1 last paragraph | ✓ |
| 17 | Exp row Al/Mg method | MC-ICPMS on dissolved chips, as in [3] | p1→p2 | ✓ |
| 18 | Exp row `process_as_printed` | "Evaporation of CAI-like liquid residues in low-pressure hydrogen." | p1: "we **remeasured** the isotopic composition of a series of residues that were evaporated in 1.87×10^-4 bars of hydrogen" | ✓ as wording, but incomplete: see R4 |
| 19–30 | Exp row typed absences (12): cell_material, cell_liner, orifice_diameter, orifice_channel_length, pressure_calibration, ionization cross-sections, ionization_energy_eV, multiplier/isotope corrections, temperature measurement/calibration, chamber background, sample prep/purity (all `unknown`), activity ions/standard states (`not_applicable`) | none of these is printed on either page; no oxide activity is reported | ✓ (12/12; no absence hides a printed value) |
| 31 | Rate row `pressure_threshold_bar` | 1.0e-7 | "about 10^-7 bars H2" (p1) | ✓ |
| 32 | Rate row below/above statements | paraphrases | p1 Elemental Fract. | ✓ (direction correct: < vacuum-like; > rate ∝ sqrt pH2) |
| 33 | Rate row Hertz–Knudsen relation | J_i = γ_i P_i^sat / sqrt(2π m_i R T) | p1 equation | ✓ |
| 34 | Rate row symbol definitions | γ_i, P_i^sat, m_i, R, T | p1 | ✓ |
| 35 | Rate row mechanism + dominant species | Mg, SiO | p1/p2 | ✓ |
| 36 | Fig1 row `hydrogen_pressure_bar` | 0.0002 | "2×10^-4 bars hydrogen" (p1 Fig 1 caption) | ✓ |
| 37 | Fig1 row `plotted_legend_hydrogen_pressure_bar` | 0.000187 | legend "PH2 = 1.87x10^-4 bar" | ✓ |
| 38 | Fig1 row `temperature_C` | 1500 | caption "(T=1500°C)" | ✓ |
| 39 | Fig1 row `rate_ratio_order_of_magnitude` | 2 | "about two orders of magnitude larger" | ✓ |
| 40 | Fig1 x ticks | 4.6, 4.8, 5.0, 5.2, 5.4, 5.6 | as printed | ✓ |
| 41 | Fig1 top T ticks | 1900, 1800, 1700, 1600, 1500 | as printed | ✓ |
| 42 | Fig1 y ticks | −10 … −6 | −6.0, −7.0, −8.0, −9.0, −10.0 | ✓ |
| 43 | Fig3 row caption | quoted | p2 Fig 3 caption, word for word | ✓ |
| 44 | Fig3 row pressure / T | 0.000187 / 1500 | caption | ✓ |
| 45 | Fig3 row ideal formula | (23.985/24.986)^0.5 | legend box "“Ideal” value, for α = (23.985/24.986)^0.5" | ✓ |
| 46 | Fig3 row vacuum attribution | Richter et al. [3] | legend "vacuum [3]", "fit to vacuum [3]" | ✓ |
| 47 | **Fig3 row `plotted_axes_as_printed`** | x_negative_ln_fraction_24Mg [0, 0.5, 1, 1.5, 2, 2.5, 3]; y_1000_ln_R_over_R0 [0,10,20,30,40] | Fig 3 axes are 10000/T(K) and 1000(1−α). These are **Figure 2's** axes, and Fig 2 labels only 0, 1, 2, 3 on x (0.5/1.5/2.5 are unlabelled minor ticks) | **✗ M1** |
| 48 | Comparative row `figure_3_axes_as_printed` x | 4.5, 5, 5.5, 6, 6.5 | 4.5, 5.0, 5.5, 6.0, 6.5 | ✓ |
| 49 | Comparative row Fig 3 top T | 2100 … 1300, 1200 | 2100…1300 printed; the last label is clipped at the frame edge and prints as "120" | ✓ (1200 is plainly the clipped label; recommended note) |
| 50 | Comparative row Fig 3 y | 6, 8, …, 20 | as printed | ✓ |
| 51 | Quote: Type B CAIs (p1 Intro) | verbatim | p1 | ✓ |
| 52 | Quote: 10^-7 threshold (p1) | verbatim | p1 | ✓ |
| 53 | Quote: increase in rate ... saturation vapor pressure (p1) | verbatim | p1 | ✓ |
| 54 | Quote: "That the main effect ... (see Figure 3 in [3])." (p1) | verbatim, incl. "coefficient ... are" | p1 | ✓ |
| 55 | Quote: Fig 1 caption incl. "larger that" | verbatim | p1 | ✓ |
| 56 | Quote: Rayleigh sentence incl. "than" (p2) | verbatim | p2 | ✓ |
| 57 | Quote: fO2 / solar composition gas [4] (p1) | verbatim | p1 | ✓ |
| 58 | Quote: "approximately the same, but ... slightly above the trend" (p2) | verbatim | p2 | ✓ (direction correct: H2 point above the vacuum trend, as plotted: blue 12.0 above the red fit at 5.64) |
| 59 | **Quote: "much less fractionating ..." (p2)** | "...the often used value corresponding **to the** inverse square root ... in Figure 3**)**." | "...the often used value corresponding **the to** inverse square root of the mass of the isotope (shown as the “ideal” value in Figure 3." (no closing parenthesis) | **✗ M2** |
| 60 | Quote: Summary (p2) | verbatim | p2 | ✓ |
| 61 | Quote: Si isotopes in progress [5] (p2) | verbatim incl. "new measurements residues" | p2 | ✓ |
| 62 | **Prior-vacuum row `comparison_scope`** | "Figures 2 and 3 compare earlier vacuum isotope data attributed to [2] and [3]." | Fig 2 shows only residues evaporated in 1.87×10^-4 bars H2: open symbols are earlier measurements **of those H2 residues** by [2], solid symbols are this work. Fig 2 has no vacuum data. Only Fig 3 compares with vacuum [3]. | **✗ M3** |

I also checked rows not counted in the 62 above:
- The references [1]–[5] (authors, year, journal, volume, page) all match.
- The vacuum attributions [2] and [3] match: "vacuum experiments are described in [2]", "vacuum evaporation residues [3]", Fig 1 "Data from [2] and [3]", and Fig 3 "vacuum [3]".
- The extraction.method text matches.
- In the ledger, completeness numbered_figures 1–3 are figure-only, and its true_absences are correct.

### Consistency checks (independent, from the print)
- Fig 2 slope: 1000(1−0.98797) = 12.03. This agrees with the blue Fig 3 point plotted at 1000(1−α) ≈ 12.0 at 10000/T = 5.64 (1500 °C = 1773 K). ✓
- The ideal α printed as 0.97980 is not exactly (23.985/24.986)^0.5 = 0.979764. The print rounds or derives it differently, by 4×10^-5. The extract carries both as printed, which is right. A located note is recommended (O3); this is not a mismatch.

## 3. Experiment facts and typed absences (sentences relied on)
- p1: "To address this with high-precision isotopic data, we remeasured the isotopic composition of a series of residues that were evaporated in 1.87×10^-4 bars of hydrogen. The vacuum experiments are described in [2] and the magnesium isotopic measurements were done as described in [3] using the GV Instruments Isoprobe multicollector ICPMS at the University of Chicago. Al/Mg ratios were measured on the dissolved chips by multicollector ICPMS as described in [3]."
- Neither page prints any cell or crucible, orifice, pressure calibration, ionisation data, multiplier correction, temperature sensor or uncertainty, background pressure, starting composition, purity or oxide activity. All 12 typed absences are correct, and `method_token: unknown` is correct: the source names no apparatus or method token, and this is not KEMS.
- The H2 pressure is correctly kept as the atmosphere (`pressure_role`), not as background.
- Standard states: none. No activities are reported, and α is an isotope-ratio factor. `not_applicable` is correct.

## 4. Citation, licence, sha, paths
- **Citation:** the PDF header prints Richter, Teng, Mendybaev, Davis and Georg, with the title ending "... IN LOW PRESSURE H2", and "1385.pdf". The corrected extract and sidecar citations match the print; the census citation (Janney/Wadhwa, "in hydrogen gas and in vacuum") was wrong. Note that the Janney/Mendybaev/Davis/Wadhwa author list is the 2007 GCA reference [3]. ✓
- **Access:** `access: open`, LPI URL `lpsc2008/pdf/1385.pdf`; no licence statement is printed. ✓
- **sha256 and size:** match the file. ✓
- **Absolute paths:** `rg "/Users/|/private/"` over the extract, ledger and sidecar finds no hits. ✓ See O1 on the sidecar original_path.

## 5. Acceptance checks (green 61ec839da, run on the Mac)
- **Migrator:** `cd ~/ci-scratch/regolith-green-ro; PYTHONPATH=$PWD .venv/python script: Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/<sid>.yaml); finalize()`. Result: 1 work, 0 experiments, 0 benches, 0 observations, 8 context rows. **Hard issues after finalize: 0** (0 issues in total). There is one queue entry, `extract yielded no observations` (axes ['document']), which is expected for a context-only extract.
- **evidence_for():** `figure_only` → FIGURE_ONLY and `measured_reduced` → MEASURED_REDUCED, with no queue reason for either. None is unknown.
- **Payload survival** (serialised migrated context rows):
  - Present: α 0.98797, ±0.00022, '0.97980', pH2 0.000187, 0.0002, threshold 1e-07, T 1500 and the ideal formula string.
  - Present and non-empty: every apparatus typed absence (cell_material, orifice_diameter, ionization_energy_eV, chamber_background_pressure, temperature_measurement_and_calibration, pressure_calibration_substance_and_method).
  - All 8 context rows survive.
  - The report's claim that α and ± serialise as quoted strings is inaccurate. They are YAML floats in the extract and migrate as floats (0.98797, 0.00022). Only 0.97980 is a quoted string. No digits are lost, so this is not a fidelity defect.
  - There are no numeric observation rows (0 non-numeric of 0).
- **Fidelity validator:** `PYTHONPATH=~/ci-scratch/regolith-green-ro .venv/bin/python tools/validate_literature_extracts.py --check-fidelity-match --show-warnings <abs path to worktree>/extracts/<sid>.yaml`, run from green-ro (a full clone) with the corpus-worktree extract path passed explicitly. Output: `OK: 1 extract file(s) valid`, exit 0.
- **Corpus worktree tools/test_ledgers_valid.py:** `python -m pytest -c /dev/null -q -p no:cacheprovider tools/test_ledgers_valid.py` gave 707 passed, and the script run directly exited 0.
- **Duplicate check:** `rg` for 0.98797, 1.87×10, "lpsc2008/pdf/1385" and "low pressure H2" over corpus extracts/ and green extracts. The only other hit is `kems-ms2000-044.yaml`, a coincidental "1.87×10-16"/"1.87×10-2" in an unrelated table. This source is not a duplicate.
- **AMENDMENT PROPOSED by the author:** a supported quantity for a kinetic Mg isotope fractionation factor, so that α can become a scored observation. I endorse forwarding it to main. Context-only is the correct home today.

## 6. Findings and required changes (FIX-FIRST)

**R1 (M1): Figure 2 axes are bound to the Figure 3 row, with unprinted tick labels** (p2, Fig 2 and Fig 3).
- In `richter_2008_figure3_and_isotope_comparisons.values`, delete `plotted_axes_as_printed` (`x_negative_ln_fraction_24Mg`, `y_1000_ln_R_over_R0`). Those are Figure 2's axes, not Figure 3's.
- Carry Figure 2's printed tick labels on `richter_2008_fig2_mg_isotope_series_figure_only` instead, as x [0, 1, 2, 3] and y [0, 10, 20, 30, 40]. Drop 0.5/1.5/2.5: they are unlabelled minor ticks, not printed numbers.
- Recommended: move `figure_3_axes_as_printed` from `richter_2008_numeric_comparative_claims` into the Fig 3 row as its `plotted_axes_as_printed`. Its values are already correct.

**R2 (M2): non-verbatim quote** (p2, Isotopic Fractionations, last sentence before Summary).
- In `richter_2008_numeric_comparative_claims.values.statements_as_printed`, transcribe the quote exactly as printed: "The experimentally determined values for α both in vacuum and in low-pressure hydrogen are much less fractionating (i.e., closer to one) than the often used value corresponding the to inverse square root of the mass of the isotope (shown as the “ideal” value in Figure 3."
- Add a `printed_wording_note` that the print reads "corresponding the to" and lacks the closing parenthesis, as was already done for "larger that".

**R3 (M3): wrong figure scope** (p2, Fig 2 caption and legend; Fig 3 legend).
- Change `richter_2008_prior_vacuum_comparator_scope.values.comparison_scope` to say:
  - Figure 1 cites [2] and [3] (vacuum and H2 rates).
  - Figure 2 shows only residues evaporated in 1.87×10^-4 bars H2: open symbols are earlier measurements by [2], small solid symbols are new high-precision data ("This work").
  - Figure 3 compares the H2 value with vacuum data and the vacuum fit from [3].
- No vacuum isotope data appear in Figure 2.

**R4: uncarried qualifying statement on what is new in this work** (p1, Isotopic Fractionations).
- In `richter_2008_experiment_and_method_context`, quote verbatim (located): "To address this with high-precision isotopic data, we remeasured the isotopic composition of a series of residues that were evaporated in 1.87×10^-4 bars of hydrogen. The vacuum experiments are described in [2] ..."
- Reword `process_as_printed` so it says the new data are isotopic re-measurements of existing H2-evaporated residues, which [2] measured earlier (Fig 2 caption). As written it reads as new evaporation runs.
- Also carry, as located quotes:
  - "There already exist a wealth of high-precision data on the isotopic fractionation of CAI-like vacuum evaporation residues [3]."
  - "A key question is whether the kinetic fractionation factors determined in the vacuum experiments are relevant to evaporations in environments with finite hydrogen pressure."
  - p1 "Figure 1 illustrates the effect of hydrogen pressure by comparing the evaporation rate in vacuum to that in 2×10^-4 bars of hydrogen."

After the fix, re-run migrator + finalize (hard issues 0), `validate_literature_extracts.py --check-fidelity-match` and `tools/test_ledgers_valid.py` on green 61ec839da. Commit with explicit pathspecs.

### Recommended (non-blocking)
- **O1 (sidecar provenance):** the extract commit rewrote `provenance.original_path` from the census Dropbox path to the corpus asset path, which loses where the file came from. On this tree 84 sidecars keep an absolute `original_path` and only this one is relative. The brief's no-absolute-paths rule covers the sidecar, so this is not a defect of the extract, but main should rule on the convention, for example a Dropbox-root-relative path such as `regolith-pyrolysis-simulator/docs-private/deep-research/literature/<sid>/source.pdf`.
- **O2 (legends):**
  - Name the Fig 1 legend species: J_Mg (circles) and J_Si (squares), filled for PH2 = 1.87x10^-4 bar and open for vacuum.
  - Carry the Fig 3 legend item "CAI crystallization range" (grey bands, no printed numbers).
  - Carry the Fig 2 legend text "This work" / "Richter et al. [2]".
- **O3 (ideal α):** printed 0.97980 against (23.985/24.986)^0.5 = 0.979764. Add a located note; this is not a source_internally_inconsistent tag, since the difference is only in the 5th decimal.
- **O4 (Fig 3 top axis):** the last label is clipped at the frame and prints as "120". Note that 1200 is read from tick spacing.

!COMPLETE: rev-richter-2008-cai-like-liquids-lpsc-abstract — FIX-FIRST, pages read 2, rows checked 62, mismatches 3, printed numbers not carried 0
