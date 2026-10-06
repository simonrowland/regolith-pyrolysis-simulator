# REPORT: batch6 B1, extract asai-yokokawa-1982-nasoborosilicate-activity

from: regolith-empirical   to: regolith-main   at: 2026-10-05 ~23:30 ET
REQ: REQ-corpus-batch6-fix-and-extraction-from-regolith-main-2026-10-05.md, item B1
source: Asai, K. & Yokokawa, T. (1982), "Thermodynamic activity of Na2O in Na2O–B2O3–SiO2 melt", Trans. JIM 23(9) 571–577, doi 10.2320/matertrans1960.23.571
corpus branch: hunt/asai-yokokawa-1982-nasoborosilicate-activity (origin = mac-studio-256-1:Repos/regolith-corpus.git)
start: b663bc1b83b487ab87892b95e5d3d460575f0d44 (origin tip at start; verified)
commits pushed (fast-forward, no force), author Simon Rowland, no trailers:
- 4520c9527481a0485d46b6a9a036dae3f9af8758  claim (took over from codex-resume-97001-1791247179 per REQ B1)
- 15837404b398b95a7feb9528ec6fca85b70cc7cf  extract + tables + provenance + decode note + ledger
- 4b5e07b0a26aeccfaca726120d8d70b685f6030c  release claim  <- TIP (ls-remote verified)
files: extracts/<sid>.yaml; tables/<sid>/t1.csv, t2.csv, t1.provenance.yaml, t2.provenance.yaml; text/<sid>/decode-note.md; ledger/<sid>.yaml (stages decoded/transcribed/extracted; completeness tables 1–2 full; figures 1–6 figure-only; unseen []). No PDFs or PNGs committed. INDEX was not regenerated (sparse tree; build_index.py was not run).
green for reader checks: ~/ci-scratch/regolith-green-ro at 61ec839da3ba288c5df4a80f6d3ef142bd8ab461. The brief names c52b9265, and 61ec839da descends from it (merge-base --is-ancestor checked). The tree is read-only; I wrote nothing in it.
engines/engines.local.toml: EXISTS in ~/ci-scratch/regolith-green-ro (1331 B, 2026-10-04).

## P0: the b663bc1b draft CSVs were wrong (now corrected from the images)
The draft t1/t2 at b663bc1b were labelled "all numeric cells checked against rendered page images", but they had these errors:
- t1 had 363 rows, and 11 printed points were missing: (1.5-m) m=1.0 1237 K −209.1; (2-m) m=2/3 1141 K −37.2; (3-m) m=0 1203 K "128,5"; six (3-m) m=2.0 points at 1191, 1166, 1140, 1160, 1184 and 1176 K; 0.5B2O3+3.0SiO2 at 1202 K −17.4 and 1185 K −15.1.
- t1 had about 80 sign errors: the minus was dropped wherever the print sets it apart ("− x"). Affected blocks: (2-m) m=2/3, 4/3, 5/3 (1134 K), 2.0; (3-m) m=2.0, 2.5, 3.0; 0.5B2O3+2.0SiO2, 0.3B2O3+3.0SiO2, 0.5B2O3+3.0SiO2.
- t2 had 3 errors: (1.5-m) m=1.5 ΔH is −5 (draft 5); (2-m) m=1/3 ΔH is −18 (draft 18); the m=4.0 row (−25.8±1.0, −51±14, −21±12) is the first row of the right half of Table 2 and continues the (6-m) block. The draft put it under (1.5-m).
I transcribed both tables independently from 300 dpi renders, in two passes each (row strips, then columns). I then diffed against the draft and resolved every difference on the image.
Cross-check: ΔG = −2FE reproduces Table 2 from Table 1 (m=3/4: about −172 mV at 1200 K gives +33.2 kJ/mol; printed 33.6).

## Rows per table
| table | pages | rows | csv | in extract |
|---|---|---|---|---|
| 1: emf (mV) vs T (K) | 572–574 (pdf 1–3) | 374 points in 44 composition blocks (7 constant-X(Na2O) series + 6 complementary compositions) | tables/<sid>/t1.csv (374 + header; T_as_printed, emf_as_printed, note) | 44 context rows, type emf_series_table, values.rows [{T_K, emf_mV, emf_as_printed, note?}] |
| 2: relative partial molar ΔG, ΔH, ΔS of Na2O at 1200 K | 574 (pdf 3) | 44 | tables/<sid>/t2.csv (central value, ± and as-printed strings) | 44 context rows, type relative_partial_molar_table, method_class measured_reduced, derived_from + derivation pointing at the matching Table 1 row |
Other context rows: 1 method/cell; 1 sample preparation; 1 composition design (experiment_series); 1 unprinted quantities (typed nulls); 1 print anomalies; 5 directional/author-statement rows (verbatim); 8 quoted_attributed literature rows; 6 figure_only rows (Figs 1–6, captions and printed contour labels; not digitised). Total: 0 observations and 112 context rows. Benches 1, experiments 1.

## Encoding decision (standing ruling applied)
I first encoded both tables as gibbs_table observations with quantities cell_emf_vs_reference_melt and relative_partial_molar_quantities_Na2O. Migration then gave 0 hard issues, but every value was ValueKind.UNAVAILABLE ("declared quantity is unknown; refusing to pick a number", simulator/battery/migrate.py:4809–4812 and :7960). So 0 of 1056 numbers survived, which fails PAYLOAD SURVIVAL. The closed Quantity enum (simulator/battery/enums.py:53) has no cell-emf quantity and no relative-partial-molar ΔG/ΔS quantity. partial_molar_enthalpy exists, but no landed extract uses it with a resolvable identity (kems-133-costa-2017 also falls to "unsupported quantity"), and it needs a reference_state that cannot express "Na2O in Na2O·2B2O3 melt".
Following the STANDING RULING (values with no supported numeric home become located context, as in shornikov-2007-cao-aluminosilicate-melts), both tables are carried as context rows with full numeric payloads. In the migrated work (context_by_work), all 1056 numeric cells come out as numbers. The validator refuses scored types in context, so the context types are free labels (emf_series_table, relative_partial_molar_table). Each table row carries a context_reason that points to the AMENDMENTS below. The migrator queue holds one item: "['document'] extract yielded no observations".

## Acceptance (green 61ec839da, PYTHONPATH=~/ci-scratch/regolith-green-ro, simulator venv python, cwd = green)
```
_migrate_extract: completed
finalize: completed
observations 0 experiments 1 benches 1 works 1 queue 1 hard_issues 0
QUEUE ['document'] extract yielded no observations
method_classes {'measured_direct': 45, 'measured_reduced': 44, None: 9, 'quoted_attributed': 8, 'figure_only': 6}
evidence_for measured_direct -> value measured_direct queue: None
evidence_for measured_reduced -> value measured_reduced queue: None
evidence_for quoted_attributed -> value quoted_attributed queue: None
evidence_for figure_only -> value figure_only queue: None
reduced rows 44 with lineage parents and derivation non-None 44
context rows in migrated work 112
numeric cells checked 1056 non-numeric or missing 0
```
(None = 9 descriptive context rows: method, sample prep, unprinted quantities, print anomalies, 5 author-statement rows. They carry no method_class, matching the takeda-1990 precedent. evidence_for(None) gives "absent method_class", and no method_class string gives unknown.)
- Fidelity validator: `cd ~/ci-scratch/regolith-green-ro && PYTHONPATH=$PWD /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py --check-fidelity-match /Users/simonrowland/Repos/regolith-corpus/worktrees/b6-asai-yokokawa/extracts/asai-yokokawa-1982-nasoborosilicate-activity.yaml` → `OK: 1 extract file(s) valid` (rc 0). That is the absolute path into the sparse corpus worktree. There are 5 structured samples, all on Table 2 (ΔG 33.6; ΔH −194; ΔS 23; ΔG −25.8 for (6-m) m=4.0; ΔH −18 for (2-m) m=1/3).
- Corpus tools/test_ledgers_valid.py (pytest, in the worktree): 687 passed.

## Payload survival (as it sits in the migrated work)
| item | printed | migrated |
|---|---|---|
| Table 1 row ((1.5-m) m=3/4, first point, p.572) | 1201 K, −171.6 mV | context rows[0] {'T_K': 1201, 'emf_mV': -171.6, 'emf_as_printed': '−171.6'} |
| Table 1 outlier (2.0B2O3+3.0SiO2, p.574) | 1216 K, 182.5 | {'T_K': 1216, 'emf_mV': 182.5, note 'carried as printed … probable misprint, not corrected'} |
| Table 2 row ((1.5-m) m=3/4, p.574) | 33.6±0.6, 6±6, −23±5 | {'T_K': 1200, 'delta_G_kJ_mol': 33.6, 'delta_G_uncertainty_kJ_mol': 0.6, 'delta_H_kJ_mol': 6, 'delta_H_uncertainty_kJ_mol': 6, 'delta_S_J_mol_K': -23, 'delta_S_uncertainty_J_mol_K': 5}; derived_from ['asai_1982_t1_emf_k1p5_m3_4'] |
| Table 2 (6-m) m=4.0 | −25.8±1.0, −51±14, −21±12 | as-printed strings and numeric fields present |
| all numeric cells | — | 1056 checked, 0 non-numeric or missing |

## Experiment facts
| fact | value (as printed) | locator | migrated value |
|---|---|---|---|
| method | emf concentration cell O2(Pt)\|Na2O·2B2O3¦Na2O–B2O3–SiO2\|O2(Pt) | p.571 II | Experiment.method = MethodToken.EMF_CELL |
| apparatus family | oxide-melt concentration cell with alumina-arch liquid junction | p.571 II | Bench.apparatus_family VALUE (string) |
| cell material and liner | Pt crucibles (one per half-cell), subsidiary compartments joined through a 1 mm hole; alumina arch wetted by the melts as liquid junction; Pt wire electrodes 1 mm | p.571 II (+p.574, p.575) | Bench.cell_material_and_liner VALUE; Bench.cell_materials = (CellMaterial.PT, CellMaterial.AL2O3) |
| junction hole diameter | 1 mm | p.571 II | BenchFact liquid_junction_hole_diameter POINT 1, unit mm |
| electrode wire diameter | 1 mm | p.575 II | BenchFact electrode_wire_diameter POINT 1, unit mm |
| detector | Yokogawa p-1 type potentiometer | p.575 II | Bench.detector VALUE |
| temperature measurement | CA thermocouple | p.575 II | Bench.temperature_measurement {'sensor': VALUE 'CA thermocouple'} |
| temperature calibration | melting points of Cu and Sb (printed "caribrated") | p.575 II | Bench.temperature_calibration {'method': VALUE, 'uncertainty': UNKNOWN not_published} |
| heating method / furnace | not described (construction in ref. 2) | p.571 II | Bench.heating_method UNKNOWN not_published |
| orifice / ionisation / calibration substance / cross-sections | not applicable (no KEMS) | p.571–575 | absent by design; stated in context row asai_1982_method_and_cell |
| background / total pressure, gas flow | not printed; O2 gas electrodes both sides; melts prepared in air | p.571, p.574 | PressureEnvironment.total_pressure_Pa and sweep_gas UNKNOWN not_published; regime UNKNOWN |
| sample preparation | Na2CO3, H3BO3, SiO2 (guaranteed reagent, Wako), Pt crucible, air, around 1300 K, more than 20 h | p.574–575 II | Sample.pretreatment VALUE; context row |
| composition check | did not change beyond ±1 wt% (SiO2 chemical analysis; Na2O ion-selective electrode) | p.575 II | Sample.characterization VALUE |
| printed composition | constant-X(Na2O) lines 2/5, 1/3, 1/4, 1/5, 1/7, 1/10, 1/16 plus complementary points | p.575 III | Sample.printed_composition VALUE; per-row moles_per_mol_Na2O in context |
| emf stabilisation / schedule | constant within 16 h (overnight); about ten temperatures per charge | p.575 II | ThermalSchedule.method VALUE; total_duration_s UNKNOWN not_published |
| T range | 1090–1264 K (Table 1); Table 2 at 1200 K | Tables 1–2 | conditions.temperature_K UNKNOWN not_published (no single setpoint); per-row T_K in context |
| activity-defining ion | Na+ assumed sole charge carrier | p.571 eq. (2) | context row asai_1982_method_and_cell |

## Standard states
- Table 1 emf: E of the sample (right) electrode against the reference (left) electrode in Na2O·2B2O3. By eq. (3), E = −(RT/2F) ln[a(Na2O)/a°(Na2O)] = −ΔG(Na2O)/2F, where a° is the Na2O activity in the reference melt Na2O·2B2O3.
- Table 2 ΔG, ΔH, ΔS of Na2O: relative partial molar quantities referred to Na2O in Na2O·2B2O3 melt at 1200 K. The paper gives no pure-Na2O (solid or liquid) standard state and no absolute activity.
- Figs 1–3, 6: same reference (captions "referred to that in Na2O·2B2O3").

## Statements that qualify the data
- No per-value emf uncertainty is printed. Composition did not change beyond ±1 wt% (p.575).
- The Table 2 ± is printed, but the statistic is not defined.
- No temperature uncertainty is given beyond the Cu/Sb thermocouple calibration (p.575).
- "The present data on the terminal binary (Na2O–SiO2) melts did not reproduce quite well the previous value … this only qualitative reproducibility seems tolerable." (p.575)
- "The present data from repeated measurements with many runs of different charges are more plausible compared with the previous ones." (p.575)
- "The use of a platinum crucible as well as the installation of separate room for the liquid junction made the cell life long enough to determine the reliable temperature dependence of emf values." (p.574)
- Na+ is assumed to be the sole charge carrier in the liquid-junction term (p.571).
- Print anomalies, carried as printed and flagged in the CSV note column and in the context row asai_1982_print_anomalies:
  - "128,5" (comma decimal; read as 128.5), (3-m) m=0, 1203 K;
  - −51.1 at 1166 K for 0.5B2O3+3.0SiO2 (the other nine are −14.4 to −18.7);
  - 182.5 at 1216 K for 2.0B2O3+3.0SiO2 (the other nine are 117.5–145.0);
  - broken glyph "1ĭ35", read as 1135;
  - blemished "1208";
  - p.576 "Figs. 3 and 4 with Fig. 5" (Fig. 4 is the phase diagram; probably a slip).

## Directional and comparative statements (verbatim, each re-checked against the 300 dpi page image)
- p.571 Abstract: "B2O3 functions as an acid more strongly than SiO2 in the composition studied." / "Large entropy change of Na2O implies the nature characteristic of network formation."
- p.575 Results: "Most of lines go down on the right side. In other words, the sodium oxide activity decreases more extensively with B2O3 than SiO2."
- p.575 Discussion: "The smooth parallel lines in Fig. 1 suggest that SiO2 functions as a weaker acid than B2O3 or even simply as a diluent in this ternary system."
- p.576: "The minimum goes even deeper at first and then slowly increases toward the SiO2 apex."
- p.576: "This implies stronger acidity of B2O3 over that of SiO2, and Na2O to B2O3 ratio is more important rather than Na2O to B2O3+SiO2 to determine the overall basicity of the melt." / "In other words, Na2O neutralizes B2O3 first and SiO2."
- p.576 (quoted, Milberg; Brungs & McCartney): "N4=R within R<0.5 in the binary and N4 stays high beyond R≥0.5 in the ternary … N4=0.8 at R=1".
- p.577: "Al2O3 is almost as acidic as B2O3 except for a high dilute alkali range, where Al2O3 is even more acidic than B2O3. Therefore the acidity order is Al2O3≧B2O3>SiO2."
- p.577: "The low entropy valley in the Na2O–B2O3 binary goes down to Na2O poor direction on one hand and climbs gradually toward 1 to 1 of Na2O to Al2O3 mole ratio (along the dashed line) on the other hand."
- p.577 Conclusion: "B2O3 was found to be more acidic than SiO2. The borate anomaly continues to be pronounced and then gradually decays on addition of SiO2."
- p.571 Intro (quoted, ref. 6): "Al2O3 was found to reduce the activity of Na2O as strongly as B2O3 … minima … shift to the low alkali side with increasing Al2O3 content."
All are carried verbatim with locators in the five directional_statement rows and the literature_context rows. None is reversed.

## AMENDMENTS PROPOSED
1. A closed quantity for a cell emf against a stated reference electrode or melt (e.g. `cell_emf`, unit mV, with a reference_state that can name a reference melt composition). Table 1 (374 points) would then be scoreable rather than context.
2. Closed quantities for relative partial molar ΔG and ΔS (with partial_molar_enthalpy) whose reference_state can be "component X in reference melt Y" rather than a pure-substance state. Table 2 (44 × 3 values) would then be scoreable. Until then both tables live in context (standing ruling).
3. Fidelity resolver: structured samples can address context rows, but cannot index into a nested values.rows list. Path samples that could do so are refused unless they point under observations[] ("metadata-only paths refused"). So Table 1 numbers cannot be fidelity-pinned while they are context. Proposal: allow `index` (or `T_K`) to select from `values.rows` on context rows.
4. A `T_as_printed` / `value_as_printed` convention for printed misprints (comma decimals, broken glyphs). I used per-row note fields here.

## FACTS THE READER DROPS
- With the tables encoded as observations of an unsupported quantity, the numeric values are dropped: Value.kind becomes UNAVAILABLE (simulator/battery/migrate.py:4809–4812 resolves quantity to UNKNOWN, and the value is refused at :7960). The shipped encoding avoids this by carrying the tables as context, so nothing numeric is dropped now.
- Observation-level composition (moles per mol Na2O) has no typed home for context rows. It is kept as strings in values.moles_per_mol_Na2O and survives verbatim.

## Open questions for main
1. The outliers −51.1 (0.5B2O3+3.0SiO2, 1166 K) and 182.5 (2.0B2O3+3.0SiO2, 1216 K) are carried as printed and not corrected. Should a source_internally_inconsistent reason be added (kems-053 pattern)? I did not, because Table 2 gives no separate check on single points.
2. Is context-only encoding acceptable for this source (0 scored observations), or does main want amendments 1–2 first?
3. The Table 2 ± statistic is undefined in the paper and is stored as printed.
4. Claim takeover: main's claim (codex-resume-97001-1791247179) was about 2 h old, and claim.py --force needs 12 h. Per REQ B1 I released it with tools/release.py and re-claimed in commit 4520c952. Release done at tip 4b5e07b0.
5. Green: the brief names c52b9265 (and ebf8541d3 in the boilerplate). The checks ran on 61ec839da, which descends from c52b9265.

## Disk / cleanup
Mac free space was 57-63 GB throughout (above 30 GB). The sparse worktree ~/Repos/regolith-corpus/worktrees/b6-asai-yokokawa was removed after the push (git worktree remove).

!COMPLETE: extract-asai-yokokawa-1982-nasoborosilicate-activity — 4b5e07b0a26aeccfaca726120d8d70b685f6030c, tables 2, rows 418 (374 Table 1 points + 44 Table 2 rows), cell Pt (+Al2O3 junction), calibration CA thermocouple at Cu/Sb melting points (emf_cell, no pressure calibration applicable); migrator completes, hard issues 0
