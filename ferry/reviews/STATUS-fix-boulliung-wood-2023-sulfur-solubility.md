# STATUS-fix-boulliung-wood-2023-sulfur-solubility

**From:** regolith-empirical seat (batch 5, fix lane B)   **To:** regolith-main   **At:** 2026-10-05 ~22:15 ET
Source: Boulliung & Wood (2023), Contrib. Mineral. Petrol. 178:56, DOI 10.1007/s00410-023-02033-9
Review applied: package `review.md` (grok review, FIX-FIRST: 20 rows, 1 mismatch, 151 printed numbers not carried)

- Start tip (REQ, matched origin before work): 2e848764276917631286403a911450f7bd5bf055
- **New tip on mirror `hunt/boulliung-wood-2023-sulfur-solubility`: 9f3bf1fe22b1ed6d14a1b48130c9a86162d6dcfa**
- Commits: group 1 26eb664a440c3d16d9766897fcb4e68a534faa20, group 2 1fe05387656f597421629523d27d7e12c92250b8, group 3 9f3bf1fe22b1ed6d14a1b48130c9a86162d6dcfa (each verified, then pushed; per-group STATUS-…-group1/2/3.md alongside)
- Only `extracts/boulliung-wood-2023-sulfur-solubility.yaml` changed; t1.csv, t2.csv, ledger, sidecar, raw and text are untouched. Explicit pathspecs, no AI trailers, not merged to mirror main.

**items fixed 14/14** (items 1–13 applied; item 14 = keep the typed absences, re-checked and unchanged)
**corrections 2** (item 1 printed subscript log C_S6+ restored with a conflict note; collateral: ~0.1 log unit at 1 GPa is relative to Ni-NiO, not FMQ). Two printed internal inconsistencies are recorded, not corrected: p. 6 "ours from 1273 to 1773 K" and the Fig. 6 caption "between 1423 and 1673 K".
**printed numbers not carried: 151 → 0.** All 151 counted by the review are now in located context records, each with a verbatim quote and structured values (283 structured numeric fields in 12 new context records; this includes the Eq. 6/7/9 coefficients, which were already carried as strings and are now also structured). No number was put context-only for a reason other than the review's own exclusions: figure curves and points and in-panel annotations (Fig. 1B fit line and R² 0.90, Fig. 3 R² 0.87, Fig. 4 ΔFMQ ~0.6 arrow) stay figure-only, and bibliographic furniture (dates, OX1 3AN, grant ids, reference-list numbers) is not stored. No observation rows were made: the measured per-sample S and capacities are in the Supplementary Table, which was not supplied, so nothing was reconstructed.
**hard issues 0.**

## Per-item table
| review item | what changed (record) | page evidence |
|---|---|---|
| 1 | `bw2023_results_text_statements` / `log_capacity_range_1473K`: printed `log C S6+` quoted; −5.90 / −4.94; `printed_subscript_conflict: source_internally_inconsistent` (sulfide wording, Fig. 1 Log C S2−, sulfate C_S6+ positive) | p. 4 R col; p. 5 Figs. 1, 2B |
| 2 | `bw2023_epma_analysis_calibration`: 45 nA, 15 kV, 10 μm, 4 standards, L17 1320 ppm, 30/15, 20/10, 80/40 s, ~60 ppm, 10–30 points | p. 4 Analyses |
| 3 | `bw2023_equilibration_time_checks`: ≥10 min; ~50 mg, ~8 h and 4 h at 1673 K (O'Neill & Mavrogenes 2002); 1/5; CMAS1/basanite 1–8 h at 1673 K, ~2 h | p. 4 L col |
| 4 | `bw2023_defining_equations`: abstract 0.5 form; Eqs. (1)–(5) | pp. 1, 2, 5 |
| 5 | `bw2023_capacity_regressions`: Eqs. (6), (7), (9) structured; Kress & Carmichael X_FeO; ≥ 8 h, 19 + 150 compositions, 1 atm, 1673 K; 1273 quoted next to 1473–1773 | pp. 6–7 |
| 6 | `bw2023_comparison_with_previous_studies`: < 0.03, FMQ − 0.78/−1.02/−1.76, 6 compositions, 1573 K, 1 atm, 2 dacite points, 32S/18O, JFR, L17, < 50% and 64 wt.% SiO2 | pp. 6–7, Fig. 3 caption |
| 7 | `bw2023_pressure_hypotheses_matjuschkin_and_eq8`: Eq. (8), pure solids; 0.5–1.5 GPa, 1173–1223 K, NNO at 0.2 GPa, ~2 log units at 1.5 GPa | p. 7 |
| 8 | `bw2023_temperature_and_pressure_effects_on_transition`: +1.15, 8 equations, +0.11; 29.2, 6.2, 23, 11.5, 10, 2.303, 8.314; Eq. (10) 0.06(P − 1)/T, P in bars; 0.4 log units; ~0.1 vs NNO; negative vs FMQ | p. 8 |
| 9 | `bw2023_eq11_and_hydrous_anchors`: Eq. (11) all coefficients and SEs, 0.135, 0.99; 1/T² curvature; Jugo and Botcharnikov conditions; 4–7 wt% H2O; 0.25; within 0.1; caution attached | pp. 8–9, 13 |
| 10 | `bw2023_martian_and_terrestrial_glass_applications`: 17.4 wt% FeO, 1473/1673 K, 1423 (caption) vs 1473; oxybarometry −1, −3.72, −0.21, +2.2, +1.6, ~2.5 wt%, +2 to +3; 1480–350 ppm, ~1473 K, 1266 K ± 30°, 1479 K ± 46°, ~200°; 1.5–2 log units | pp. 9–11, 13 |
| 11 | `bw2023_degassing_model`: Eq. (12), ΔV 29.2/6.2; fluid standard state; Eq. (13); 0.95/0.05; JANAF Ka/Kb/Kc; 250 → 1.7 MPa in 200 steps; +0.4 (+2 → +2.4); FMQ path +0.4; +2.1; 250 MPa, > 80%, 100 MPa; FR06MI07 4.8%, 0.123%, 400 → 0.1 MPa, 1303 K; 50, 150, < 2 MPa; 5–7 km, 125–175 MPa | pp. 1, 10–13 |
| 12 | `bw2023_design_criteria_and_introduction_bounds`: < 0.1 (1 atm, 1573 K, < FMQ); > 1 log unit below FMQ; FeO 0–16.9 wt% (Table 1 span); 0.4–1.6 GPa; ~FMQ + 1; 1–2 log units; 1323–1773 K; 11th | pp. 1–3 |
| 13 | Equations on `model_derived` records; `bw2023_model_and_comparative_claims` is now figure_only for Figs. 1–9 only (caption conditions; nothing digitised) | pp. 5–12 |
| 14 | Typed absences unchanged (not_applicable cell, orifice, ionisation, multiplier; not_published T sensor, calibration, uncertainty; supplement absent; aggregate T, duration and flow unknown with per-run values in t2.csv) | pp. 3–4, 14 |

All page images were rendered by me (pdftoppm -r 220, 15 pages) and every carried value was read on the image. I used the PDF text layer only to locate passages and to copy spellings as printed (e.g. "Souffrière", "measureable", "46o", "cm−3/mol"). Tables 1 and 2 were not edited. The reviewer had already checked them cell by cell, and I re-read p. 3 against t1.csv and t2.csv with no change needed.

## Checks (at 9f3bf1fe)
- Green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461, read-only full clone ~/ci-scratch/regolith-green-ro (cwd and PYTHONPATH). Interpreter /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python (Python 3.12.13).
- `engines/engines.local.toml` **exists** in that green checkout (and in ~/Repos/regolith-pyrolysis-simulator).
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree extract>)` then `finalize()`: completed; **hard issues 0**; 1 work, 1 experiment, 1 bench, 17 context rows, 0 observations.
- `evidence_for()` on all 8 method_class strings used (author_estimate, figure_only, measured_direct, measured_reduced, measured_tabulated, model_derived, quoted_attributed, quoted_unattributed): known class, queue reason None.
- `tools/validate_literature_extracts.py --check-fidelity-match <absolute path of the extract in the sparse worktree>`, run from the green clone: `OK: 1 extract file(s) valid` (no fidelity samples because there are no observations).
- Corpus worktree `python -m pytest tools/test_ledgers_valid.py -q`: 687 passed.
- rg `/Users/` and `/private/` in the extract, ledger and tables: none.
- Worktree: sparse, ~/Repos/regolith-corpus/worktrees/fix-b57-boulliung-wood-2023-sulfur-solubility (~59 MB), removed after delivery. Never ran build_index.py or migrate_pilot_extracts.py.

## Notes for main
- Method facts of this study's own procedure (EPMA, equilibration, design) use `quoted_unattributed`, following the existing landed `bw2023_method_and_unreported_measurement_details` record. Statements by other workers are `quoted_attributed` with an `attribution` field on each item. Re-tag them if you prefer another convention.
- AMENDMENTS PROPOSED (unchanged from the author): a typed home for non-scored tabulated composition and run-condition series, and EPMA calibration slots on the bench.
- No confirm review was asked for in this REQ line, but I re-checked every item against the page images myself.
