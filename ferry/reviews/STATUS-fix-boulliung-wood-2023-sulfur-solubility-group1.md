# STATUS fix group 1: boulliung-wood-2023-sulfur-solubility

**From:** regolith-empirical seat (batch 5, fix lane)   **At:** 2026-10-05 ~21:45 ET
Branch `hunt/boulliung-wood-2023-sulfur-solubility` on mac-studio-256-1:Repos/regolith-corpus.git
Start tip (REQ): 2e848764276917631286403a911450f7bd5bf055 (origin matched before work)
Group 1 tip pushed: **26eb664a440c3d16d9766897fcb4e68a534faa20**
File changed: `extracts/boulliung-wood-2023-sulfur-solubility.yaml` only (explicit pathspec; no AI trailers).

Review items in this group: 1, 2, 3, 12, 14 (14 = keep the typed absences; unchanged, re-checked).

| review item | what changed | page evidence (220 dpi image read) |
|---|---|---|
| 1 log C_S6+ subscript | New record `bw2023_results_text_statements` item `log_capacity_range_1473K`: quote with the printed `log CS6+`, `printed_symbol: log C_S6+`, values −5.90 (phonolite) / −4.94 (nephelinite), and `printed_subscript_conflict: source_internally_inconsistent: ...` (sentence says sulfide capacity; Fig. 1 axis Log C S2−; sulfate log C_S6+ positive in Eq. 9 / Fig. 2B). Old `log C_S2−` qualification removed. | p. 4 right column "the logarithm of the sulfide capacity (log C S6+) ranges from – 5.90 (phonolite) to – 4.94 (nephelinite)"; p. 5 Fig. 1A y-axis "Log C S2-"; Fig. 2B axes Log C S6+ 4–10 |
| 2 EPMA calibration | New record `bw2023_epma_analysis_calibration`: Cameca SX-Five-FE, 45 nA, 15 kV, 10 μm; albite (Si, Al, Na), andradite (Ca, Fe), sanidine (K), pyrite (reduced S); L17 basaltic glass 1320 ppm S re-analysed each session; counting 30/15 s, Na and K 20/10 s, S 80/40 s; detection limit ~60 ppm; 10–30 points per sample; verbatim quote | p. 4 Analyses, left column bottom + right column top |
| 3 equilibration checks | New record `bw2023_equilibration_time_checks`: ≥10 min pre-flush; O'Neill & Mavrogenes (2002) ~50 mg, ~8 h (Fe-free) and 4 h (Fe-bearing) at 1673 K (quoted_attributed); about 1/5 the mass; CMAS1 and basanite 1–8 h at 1673 K, constant S after ~2 h; 8/6/3 h durations pointed to t2.csv (not duplicated); quench in reduced S atmosphere | p. 4 left column, Experimental conditions |
| 12 design criteria | New record `bw2023_design_criteria_and_introduction_bounds`: Nash S6+/ΣS < 0.1 at 1 atm, 1573 K, fO2 < FMQ (p. 3); fO2 > 1 log unit below FMQ imposed (p. 3); FeO 0 to 16.9 wt% sentence tied to the Table 1 span (Lunar Basalt 16.9) (p. 3); 14 melts / 13 used / rhyolite excluded after 8 h (p. 3); 1323–1773 K scope (p. 2); 2 oxidation states; S0/S4+ at 0.4–1.6 GPa "may probably be neglected" (p. 2); sulfate at > ~FMQ + 1 (p. 2); at least 1–2 log units above FMQ (p. 2); Buchanan & Nolan 1473 K, Nash 1573 K, O'Neill & Mavrogenes 1673 K (p. 2); 13 melts, 1473–1773 K in 50 K increments (p. 2); 11th most abundant (p. 1) | pp. 1–3 as listed |
| 14 typed absences stay | No change: cell, orifice, ionisation, multiplier `not_applicable`; temperature sensor/calibration/uncertainty `not_published`; composition after runs `referenced_in_unavailable_supplement`; aggregate T/duration/flow unknown with per-run values in t2.csv. Re-checked: pp. 3–4 print no sensor, calibration or T uncertainty | pp. 3–4 |

Moved, not lost: rhyolite, alkali gain/loss, crystallised-product exclusion, 0.1 log unit fO2/fS2 uncertainty, NIB 760 ± 25 → 2433 ± 39 ppm, the −5.55 → −4.33 range, capacity-increase directions and the S0/S4+ neglect left the figure_only record's `qualifications` for the records above (each now with its verbatim quote and structured numbers). The results record is `measured_reduced` with `derivation.relation` Eq. (2) and `derived_from: {kind: absent, reason: referenced_in_unavailable_supplement}`; per-item classes are measured_direct (S ppm), measured_reduced (log C), author_estimate (0.1 log unit).

Checks at this tip (green 61ec839da, read-only ~/ci-scratch/regolith-green-ro, interpreter /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python 3.12.13):
Migrator(root=green, index={}, aliases={})._migrate_extract + finalize(): completed, **hard issues 0** (1 work, 1 experiment, 1 bench, 9 context rows, 0 observations); evidence_for() known with no queue reason for every method_class; validate_literature_extracts.py --check-fidelity-match <absolute worktree extract path>: `OK: 1 extract file(s) valid`; corpus `pytest tools/test_ledgers_valid.py`: 687 passed; no `/Users/` or `/private/` in extract/ledger/tables.
