# Bischof 2023 spike-link extraction audit

Status: complete. The controller authorized regenerated child IDs while preserving both parent IDs. Full old-to-new mapping is in `kems-bischof-2023-spike-link-id-map.md`.

## Target observable and engine readiness

The observable is vapour partial pressure, stored as migrator quantity `p_partial` and consumed on `Rail.VAPOUR` by `simulator/battery/score.py::rail_for_quantity`. The extract has 22 kems-137 rows and 10 kems-138 rows. The real migrator emits all 32 as gas-phase points with known temperatures and no quantity-unknown or phase-unknown rows. The scorer's `simulator/battery/score.py::vapour_crystalline_sample_liquid_engine_refusal` returns no phase refusal for the An-Di liquid sample and refuses all 10 kems-138 crystalline-sample pressure observations for single-liquid engines. No target row is engine-ready: kems-137 lacks a schema-representable full charge composition (mixed oxide wt% and elemental ppm), while kems-138 lacks an exact composition and is outside a single-liquid engine's sample domain.

| Observable | Rows | Engine-ready | Missing input paths |
|---|---:|---:|---|
| `p_partial` / `Rail.VAPOUR`, kems-137 | 22 | 0 | `identity.composition` (full charge is mixed-basis; per-run measurement-state composition is not published); `identity.total_pressure_Pa` (chamber pressure is approximate and in-cell total pressure is not derivable). Absolute fO2 is not printed: only buffer-relative ΔIW is available and cannot populate `identity.fO2_Pa`. |
| `p_partial` / `Rail.VAPOUR`, kems-138 | 10 | 0 | `identity.composition` (exact assay not published; sample purity is only >99.9%); `identity.total_pressure_Pa` (only approximate chamber pressure is printed); `identity.fO2_Pa` (not published). Six paragraph values also lack an experiment/run link (`values.points[].run_link`); the four Table 12 points link to Oct. 2019. All 10 use crystalline oxide samples and are refused by the single-liquid scorer applicability check. |

The approximate chamber pressure is retained as a located `point_conditions.total_pressure_Pa` value, not promoted to the experiment's in-cell total pressure. kems-137 S1 ΔIW is retained as a located `point_conditions.fO2_control` statement, not converted to absolute fO2. Figure-only kems-138 series remain `figure_only` and are not counted among the 10 rows.

## Run and charge links

| Extract rows | Printed link | Charge composition evidence | Link result |
|---|---|---|---|
| kems-137 `bischof_2023_ga_psat_s1_1low_polytherm` and `bischof_2023_in_psat_s1_1low_polytherm` (11 rows each) | Table 2, physical PDF p. 6: `1_low, initially 1000 ppm`; Supplement Table S1, physical p. 2: `1_low (KN_07-11-19_Ga-In-Tl_1000)`; Table 2 polytherm ramp is up | Methods, physical p. 4, nominal base: SiO2 50.34, Al2O3 15.40, MgO 10.80, CaO 23.46 wt%; Ga, In, and Tl each added at 1000 ppm as sesquioxides. Table 6 Start (physical p. 15) reports measured bulk oxides; Table 7 Start (physical p. 16) reports Ga 720 ± 12 ppm and In 47 ± 9 ppm | Supplement Table S1 labels both Ga and In pressure columns as 1_low. Main §4.4.1 discusses In in run 1_low and says run 2_low In is omitted above 1500 K as unreliable. Both target species therefore link to run 1_low and nominal 1000 ppm additions. The KEMS start composition is mixed basis and is not exactly representable by one current `AmountBasis`. Table 6 oxide totals are 99.90 wt%; Table 7 is separate, measured concentration evidence. Do not normalize, convert, or replace one with the other. |
| kems-138 Ga, GaO, Ga2O at 1752 K; In, InO, In2O at 1611 K | Results paragraphs, published p. 8 / physical PDF p. 9; the six pressure statements do not identify a specific 2019 or 2021 run | Methods, published p. 2 / physical PDF p. 3: approximately 40 mg pure (>99.9%) alpha-Ga2O3 or alpha-In2O3 powder; Table 3 gives different 2019 and 2021 charges | Specific charge linkage is not published for these six summary values. Keep the broad series FK and record missing run/charge; do not borrow a Table 3 charge. |
| kems-138 In and In2O at 1425 and 1550 K | Table 12, published p. 16 / physical PDF p. 17: “This work, Oct. 2019” column | Table 3 identifies the Oct. 2019 In2O3 charge and its run schedule; sample purity remains printed as >99.9%, not an exact composition map | Table 12 links all four points to Oct. 2019. The current extract groups them under `in2o3-series`; a run-specific experiment FK is required for full consumer-aware linkage. |

## Cell audit: kems-137 Supplement Table S1

Source table layout and all target numbers were checked against the rendered Supplement Table S1 (physical p. 2). The supplementary machine text has one usable read (pdftotext); MinerU content is not supplied for this supplement, so every target cell below is verification method (b), render. Temperature is the row coordinate, Ga and In are the printed partial-pressure columns, and ΔIW is the printed buffer-offset column. The 11 body rows are reconciled to the printed 1_low polytherm series.

| Table S1 row | T (K) | Ga (Pa) | In (Pa) | ΔIW | Verification |
|---:|---:|---:|---:|---:|---|
| 1 | 1586.4 | 4.04E-05 | 6.69E-05 | 1.66 | (b), physical p. 2 render |
| 2 | 1598.0 | 4.37E-05 | 7.25E-05 | 1.62 | (b), physical p. 2 render |
| 3 | 1608.6 | 4.49E-05 | 9.76E-05 | 1.61 | (b), physical p. 2 render |
| 4 | 1619.2 | 5.16E-05 | 8.90E-05 | 1.57 | (b), physical p. 2 render |
| 5 | 1630.8 | 6.45E-05 | 1.17E-04 | 1.57 | (b), physical p. 2 render |
| 6 | 1642.5 | 1.10E-04 | 1.23E-04 | 1.57 | (b), physical p. 2 render |
| 7 | 1653.0 | 9.98E-05 | 1.46E-04 | 1.54 | (b), physical p. 2 render |
| 8 | 1664.7 | 1.45E-04 | 1.60E-04 | 1.54 | (b), physical p. 2 render |
| 9 | 1674.2 | 1.66E-04 | 1.97E-04 | 1.54 | (b), physical p. 2 render |
| 10 | 1684.8 | 2.07E-04 | 2.07E-04 | 1.53 | (b), physical p. 2 render |
| 11 | 1695.4 | 2.73E-04 | 2.34E-04 | 1.53 | (b), physical p. 2 render |

Table S1 row order plus Table 2's ramp-up column supplies run-order indices 1–11 for each species. The source reports ΔIW relative to IW, not an absolute fO2 identity. The source also prints calculated p(O2) output in Table S1; it is not the experiment's total pressure. The KEMS chamber pressure is printed on physical PDF p. 5 (approximately 10^-6 mbar, or 10^-4 Pa after unit conversion); all 22 target point-condition locators now cite p. 5. Table 7 and the accompanying paragraph report run-level start/end concentrations and about 40% Ga loss for 1_low after its polytherm followed by a 15 h isotherm; no per-point depletion series is printed. No source statement of congruent or incongruent vaporisation for the An-Di run was located.

## Cell audit: kems-138 pressure rows

The three Ga-bearing paragraph values and their species/temperature assignments were checked against rendered physical p. 8; the three In-bearing values were checked against physical p. 9. Table 12's four Oct. 2019 target values were checked against the rendered table on physical p. 17. All eight printed uncertainty widths are faithfully retained in the source wording; confidence is not stated. Table 12 prints no uncertainty for its four target pressure cells.

| Source location / row | Species | T (K) | p (Pa) | Printed width | Verification |
|---|---|---:|---:|---:|---|
| Results, physical p. 8 | Ga2O | 1752 | 0.79 | 0.3 | (b), physical p. 8 render |
| Results, physical p. 8 | Ga | 1752 | 0.11 | 0.016 | (b), physical p. 8 render |
| Results, physical p. 8 | GaO | 1752 | 0.007 | 0.002 | (b), physical p. 8 render |
| Results, physical p. 9 | In2O | 1611 | 1.41 | 0.52 | (b), physical p. 9 render |
| Results, physical p. 9 | In | 1611 | 0.30 | 0.05 | (b), physical p. 9 render |
| Results, physical p. 9 | InO | 1611 | 0.0036 | 0.001 | (b), physical p. 9 render |
| Table 12, physical p. 17, this work / 1425 K | In | 1425 | 0.003 | none printed | (b), physical p. 17 render |
| Table 12, physical p. 17, this work / 1550 K | In | 1550 | 0.072 | none printed | (b), physical p. 17 render |
| Table 12, physical p. 17, this work / 1425 K | In2O | 1425 | 0.014 | none printed | (b), physical p. 17 render |
| Table 12, physical p. 17, this work / 1550 K | In2O | 1550 | 0.327 | none printed | (b), physical p. 17 render |

## Composition schema gap

The kems-137 source-native An-Di plus dopant description requires oxide wt% and elemental ppm simultaneously. `simulator/battery/enums.py::AmountBasis` provides one amount-basis token for a whole `Composition`, and `simulator/battery/migrate.py::_percent_pairs` reads a single declared basis for every component. `simulator/battery/enums.py::PHASE_BASIS_TOKENS` has `ppm_mass` for the typed phase-composition quantity, not a mixed-basis bulk melt composition. Existing KEMS extracts represent a charge as a single wt% or mole-fraction map; no existing extract provides this mixed wt%+ppm bulk composition. The supported observation/quantity candidates are present (`tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`), and the pressure phase is supported. The unsupported form is specifically the mixed-basis initial bulk composition. Under the extraction rule, retain the printed oxide wt% and spike ppm separately with locators, leave the full composition typed-refused, and do not change the schema or migrator.

## Validation and migration

The isolated real-migrator results for the full extracts are:

| Source | Migrated observations | Hard issues | Unavailable values | Target rows | Target quantity/phase unknown |
|---|---:|---:|---:|---:|---:|
| kems-137 | 164 | 15 | 12 | 22 | 0 |
| kems-138 | 36 | 4 | 16 | 10 | 0 |

The hard-issue counts are unchanged from the seat base: kems-137 has 12 existing conditional-field issues on derived gamma/Factsage rows (missing `derived_from` or `derivation`) and 3 existing `sample.initial_composition` conflicts where Table 6 measured start inventories differ from the nominal recipe. kems-138 has 4 existing conditional-field issues on its two derived thermodynamic rows (missing `derived_from` and `derivation`). The old-to-current isolated migration comparison found 0 new or resolved hard issues in either extract. None of those pre-existing issues is on the 32 pressure target rows.

All 32 targets migrate as `p_partial`/gas with mapped temperature and no quantity-unknown or phase-unknown rows. Identity pressure remains `in_cell_total_pressure_not_derivable`; unknown composition and fO2 are documented above. Migrated uncertainty counts are kems-137: 22 `none` / `not_stated`; kems-138: 10 `printed` / `not_stated`. The latter preserves six printed widths at the parent observation and four explicit no-width Table 12 records. These are source confidence statements, not inferred confidence levels.

The focused test pins four numeric values independently to rendered source cells (kems-137 Supplement Table S1 physical p. 2; kems-138 Results physical pp. 8, 9, and Table 12 physical p. 17), asserts both target row counts and migrated scorer applicability, pins the kems-137 silicate-liquid wording to p. 1 / Abstract and all 22 chamber-pressure locators to physical p. 5, and includes mutation evidence: changing a pinned Table 12 pressure causes the independent fidelity check to fail. It passes locally after the locator corrections. `tests/test_yaml_flow_scalar_commas.py` passes locally (17 passed, 120.93 s). The full `tests/test_literature_extracts.py` run hit its 300 s local cap; its isolated `test_repo_extracts_validate_green` also hit 300 s. The remote profile refused the explicit `--timeout` flag. After the lane came live, a no-timeout full-file remote run returned no output or receipt in five minutes and was interrupted. Mark the suite `UNVERIFIED-local` and remote-unverified. The local j01 test hit its 300 s cap; the remote profile refused both `-k` and a `::test` node ID. A whole-file remote run of `tests/battery/test_migrate.py` also returned no output or receipt in five minutes and was interrupted; mark j01 `UNVERIFIED-local` and remote-unverified.

The full scratch `battery_migrate.py --root` run timed out at 1800 s before writing the store. To validate the changed source outputs, both extracts were then re-migrated with the real migrator into the scratch full-store copy, replacing their generated extract records and work records. `battery_validate_store.py --root` completed on that updated scratch store: `forbidden_shape_count=0`; allowed hard-issue shapes were unchanged from green (249 conditional `derivation`, 1,191 conditional `derived_from`); store content hash changed from `64b1334af71411ff2eb33743800a5f13d616ea9cacdfb10745820aa8b86d6748` to `51e854361b03273e3b14426f8aa4b345f6bffbb61236225997b026d115a7d57e`. The full-repository scratch regeneration itself remains incomplete. No generated store files are committed.

### Run order, depletion and phase statements

The kems-137 S1 rows are ascending-temperature points on Table 2's ramp-up; sequence is recorded here and is not migrated per the controller ruling. Table 7 gives run-level start/end concentrations and about 40% Ga loss after the 1_low polytherm followed by a 15 h isotherm; per-point depletion is unreported. No statement of congruent or incongruent vaporisation was located for the An-Di run. kems-138's six summary values have no specific run link; Table 12 points are linked to Oct. 2019.
