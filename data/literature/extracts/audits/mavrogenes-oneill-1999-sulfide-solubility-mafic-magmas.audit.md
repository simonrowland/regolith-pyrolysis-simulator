# Mavrogenes and O'Neill (1999) extraction audit

## Scope and transcription

The extraction contains every printed row from Tables 1–3. Table 1 has 30 rows: two starting-glass analyses and 28 run-product analyses. Table 2 has 28 runs (23 basalt and 5 picrite). Table 3 has eight regression-fit rows. Starting compositions are retained separately from each quenched run-product composition; Table 1 prints `MACV26` while Table 2 prints `MAV26`, and both labels remain unchanged.

The input order was MinerU table blocks and machine text, followed by the pdftotext layout text. MinerU table QA reports numeric digit agreement of 1.000 for all three tables. The attached cell audit has 559 populated cells in numeric columns: 270 from Table 1, 252 from Table 2 (including the printed `ND` entries in `X_FeS`), and 37 from Table 3. It binds each cell to its absolute PDF page, printed table, row label/group, column and printed text. The table-page renders (PDF pages 3, 4 and 6) were used to check row and column assignment and the printed labels. The prose locator and regression method on PDF page 5 were also checked against its render. No table-cell numeric disagreement required a render override.

The Table 2 footnote says the Fe20 capsule is Fe20Ir20, while its run rows and methods say Fe20Ir80. The note and row entries are both preserved as printed. Table 1 and Table 2 row correspondence uses table sequence and material group; the `MACV26` / `MAV26` discrepancy is explicitly retained.

The comparison artifact checks all 28 migrated sulfur values against the MinerU Table 2 block with row and column locators: 28 matched, 0 unmatched, 0 mismatched, 0 presence-only, 0 mapping-needed, 0 locator errors, and 0 unverifiable.

## What is stored

Table 1 retains all starting and run-product oxide values and totals with the printed headers. The analysis method is Cameca Microbeam electron-probe analysis; Tables 1–2 report means and standard deviations of twelve analyses, while Table 1 has no per-cell standard-deviation column. Table 2 retains, per run, its id, duration, capsule, printed `log fO2* (ΔIW)`, pressure, temperature, quenched-glass S mean and observed standard deviation, calculated `S_calc`, `X_FeS`, and `a_FeS`. Numeric conditions and their locators reach each observation through `point_conditions`; only the printed buffer-relative ΔIW is stored. The absolute `fO2_Pa` identity remains unknown because no absolute fO2 is printed.

The 28 direct S measurements are WDS analyses of quenched silicate glass (ppm, means of twelve spot analyses). The method uses a PET crystal, 15 kV, 40 nA, two-minute counting, troilite standard, and reports a 60–70 ppm detection limit. The Table 2 observed standard deviations are retained as printed; confidence level is not stated. The paper says the runs quenched to homogeneous silicate glass containing immiscible sulfide, but does not explicitly state that sulfur concentration is preserved by quenching. Each glass observation therefore records quench preservation as `not_stated`, with the page 4 locator. No sulfur speciation ratio or anhydrite composition is reported.

Coexisting sulfide is described as stoichiometric FeS in Fe-Ir runs, quenching to nearly pure troilite (Ir ≤ 500 ppm); the authors could not distinguish any oxide associated with it. Fe-capsule sulfide quenches to troilite plus Fe metal, with image analysis estimating the original composition in two runs. The printed `X_FeS` values (including `ND`) remain linked to their Table 2 cells in each run's phase context. This value is typed-refused as a separate phase-composition observation: `equilibrium_phase_composition` and `mole_fraction` exist, and `park-2021-vanadium-thermodynamics-thesis` demonstrates that path for a metal role, but `PhaseRole` has no exact `sulfide` token and `_phase_role_token` rejects an unsupported role. The candidates checked were `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, `simulator/battery/migrate.py::_phase_role_token`, `simulator/battery/enums.py::PHASE_BASIS_TOKENS`, and `simulator/battery/enums.py::PhaseRole`. The printed values are not relabeled as matte or metal. Anhydrite is not reported.

Table 2's `a_FeS` values are model-derived activity values relative to pure liquid FeS, not experimental pressure. The authors use the 1-bar Fe-S liquid model of Kongoli et al. (1998), report the same 0.50 result using Chuang et al. (1985), then apply 0.50 at all pressures because a high-pressure correction was not available. The ΔIW calculation cites Woodland and O'Neill (1977) and the `a_FeO = 1.7 X_FeO` relation of Holzheid et al. (1997).

All eight Table 3 fit rows are retained as printed model-derived results. The authors fit their own basalt and picrite measurements by multiple nonlinear least squares to Eqn. 10, with the printed weighting method on PDF page 5. Those two fits name exactly their 23 and five stored Table 2 S observations as inputs. Wendlandt (1982) fit rows and ranges are retained as printed; the source experiment rows are not printed in this paper. The Discussion's Li and Agee (1996) regression is retained as model-derived with the six raw inputs recorded as `not_printed` at PDF page 6. Parenthesized parameter widths are preserved as printed; the paper does not state their confidence level.

Figures 1–5 remain figure-read candidates with no digitized points. Their graphical values are not mixed with tabulated values.

## Isolated migration

The isolated migration of this extract produced 28 observations, zero hard issues, zero unavailable values, an empty queue, and zero registry issues. All migrated observation identities have known quantity `equilibrium_phase_composition` and phase role `glass`. Composition, total pressure, and temperature map to the identity for each run. `fO2_Pa` is unknown for each run because the source gives buffer-relative ΔIW rather than absolute fO2.

Three migrated identity-axis checks (oxide composition is the migrator's mole-fraction composition from that run's printed Table 1 run-product analysis):

| Run | temperature_K | total_pressure_Pa | fO2_Pa | identity composition (oxide mole fractions) |
|---|---:|---:|---|---|
| MAV1 | 1673.15 | 500000000 | unknown | SiO2 0.5087490433925147236746357574; TiO2 0.01275250223197329032312532242; Al2O3 0.1081826890309747710835057464; FeO 0.1169763620507421428410404483; MgO 0.1114101214606005878891398823; CaO 0.1038326388140947916548173110; Na2O 0.02986835408531233763088498617; K2O 0.008228288933787354902850545686 |
| MAV34 | 1773.15 | 500000000 | unknown | SiO2 0.4684387855320105976291390448; TiO2 0.01308781622005441808553199349; Al2O3 0.06220489254470288670172221845; FeO 0.1002812512284044999640806371; MgO 0.2471822892280683712508814868; CaO 0.08961799141342717445205128791; Na2O 0.01705540815953758176971358175; K2O 0.002131565673794470146879749804 |
| MAV69 | 2073.15 | 5500000000 | unknown | SiO2 0.5319381817345556470561144871; TiO2 0.01275476740432041098919068427; Al2O3 0.1074632008505367867111727339; FeO 0.09056688865097143771331389078; MgO 0.1168941093117427428887747431; CaO 0.1147824367869465286220212190; Na2O 0.01951737138154510641008771873; K2O 0.006083043879381339609324523912 |

The focused test pins rendered values from Tables 1, 2 and 3, the 30/28/8 printed row counts, the two table-label discrepancies, all 28 row-linked sulfide refusals, and the post-migration identity axes. It also checks that no migrated observation has an unknown quantity or phase role.
