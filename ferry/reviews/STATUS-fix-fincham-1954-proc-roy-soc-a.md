# STATUS: fix fincham-1954-proc-roy-soc-a (batch 7)

**From:** regolith-empirical seat (fix-fincham, resumed)   **To:** regolith-main   **At:** 2026-10-05 ~23:00 ET
**Source:** C. J. B. Fincham and F. D. Richardson (1954), "The behaviour of sulphur in silicate and aluminate melts", Proc. R. Soc. Lond. A 223 (1152), 40–62, DOI 10.1098/rspa.1954.0099
**Branch:** `hunt/fincham-1954-proc-roy-soc-a` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Review applied:** batch7 `review.md` (main's grok review: FIX-FIRST at d6b4b9d6; rows checked 86, mismatches 13, printed numbers not carried 96, plus 10 missing equations)

- Start tip (checked with `git fetch`; it matches the assignment): `d6b4b9d6844ff6e69ea7f715bfade60093408ad5`
- **New tip, pushed to origin `hunt/fincham-1954-proc-roy-soc-a`: `3d82f84a9da44db211e4d2d70c8729dae1ad2319`**
  (one commit, parent d6b4b9d6. Author Simon Rowland. Explicit pathspecs, `git add --sparse`, no trailers. Fast-forward push, no force.)

items fixed 13/13 (+ change 4: 10/10 equations, + change 5: 96/96 printed tokens carried), corrections 13, hard issues 0

The previous seat stopped at about 21:50 ET on a held approval and had applied 0/13. This seat started again from d6b4b9d6.
I checked every item against the 220 dpi page renders (`pdftoppm -r 220`, 1386 × 2166 px). I used crops and 2–3× zooms for the fractions,
exponents and equation layouts. The text layer was used only to find lines. Main runs a grok confirm review after this fix.

## Per-item table (the 13 mismatches = change 1 ×1, change 2 ×6, change 3 ×6)

| # | Review item | What changed in the extract | Page evidence (220 dpi render) |
|---|---|---|---|
| 1 | Ch.1, silica-cup diameter | `fincham-feo-sio2-silica-cups` `sample.container`: "1/4 in." changed to "Silica cups sealed from 3/8 in. silica tubing (p. 48)". Locator moved from the shared p. 42 anchor to page 9 / published 48, section "Melts containing iron oxide and silica", with the printed sentence quoted. The Pt-cup "radius about 1/4 in." on both benches is unchanged. | p. 48: "…small silica cups made by sealing off the ends of lengths of ⅜ in. diameter silica tubing" (⅜ confirmed in zoom). p. 42: "a radius of about ¼ in." refers to the platinum cups. |
| 2 | Ch.2, `fincham1954_t4_cao_silicate_056_1600c` | method/evidence class changed from `quoted_attributed` to `author_derived`. `attribution` renamed to `a_MO_attribution` (Richardson 1953, Fig. 6), with the sentence "applies to the a(MO) column only". Added `class_note` with the p. 55 basis. | p. 55: 1600 °C CaO–SiO2 Cs "derived from the Cs values at 1500 and 1650° C by the use of log Cs, 1/T plots"; "The γ(MS) used here is equal to a(MS)/N(MS)", from K9–K11, Cs and the a(MO) column. |
| 3 | Ch.2, `fincham1954_t4_cao_silicate_050_1600c` | Same change as #2. | Same, p. 55 / Table 4 p. 57. |
| 4 | Ch.2, `fincham1954_t4_cao_silicate_039_1600c` | Same change as #2. | Same. |
| 5 | Ch.2, `fincham1954_t4_mgo_silicate_050_1650c` | Same change as #2. The a(MgO) "assigned to 1650° C without correction" attribution is kept on a(MO). | Same. |
| 6 | Ch.2, `fincham1954_t4_feo_silicate_054_1500c` | `author_derived`. a(FeO) attribution to Michal & Schuhmann (1952) kept as `a_MO_attribution` (a(MO) only). Added `class_note` (Cs from the authors' Table 3 runs; γ from K11). | p. 55: "Values for a(FeO) in the 'FeO'-SiO2 mixtures have been measured by Michal & Schuhmann (1952)". |
| 7 | Ch.2, `fincham1954_t4_feo_silicate_057_1350c` | Same change as #6. | Same. |
| — | Ch.2, other rows | `..._056_1600c_gamma5` and the four aluminate rows were already `author_derived`. Left unchanged, as the review says. | — |
| 8–10 | Ch.3, `pressure_environment.sweep_gas` on all 3 experiments | `unknown/not_published` changed to `not_applicable/not_applicable`. The note quotes the printed gas ("Mixtures of hydrogen, carbon dioxide, sulphur dioxide and nitrogen…", p. 42) and the flow (1.5–17.3 ml/s s.t.p., p. 44). It says the ingoing ratio changes per run and points to the Appendix context row (p. 62). not_applicable means "no one composition", not an absent value. | p. 42 "Gas mixtures" paragraph. Appendix p. 62 has 36 rows with differing % SO2, p_N2 and p_H2/p_CO2. |
| 11–12 | Ch.3, `sample.printed_composition` on the 1500 °C and 1650 °C experiments | `unknown/not_published` changed to `not_applicable/not_applicable`, locator Table 1 (p. 44). The note says Table 1 prints S1–S17 (context rows `fincham1954_t1_*`) and that several mixtures were used per experiment. | p. 44 Table 1. Table 2 (p. 47), Fig. 2a (p. 45), Fig. 4 mixtures a–i (p. 47). |
| 13 | Ch.3, `sample.printed_composition` on `fincham-feo-sio2-silica-cups` | `unknown/not_published` changed to `not_applicable/not_applicable`. Locator moved from Table 1 p. 44 to **Table 3 p. 48**. The note carries the printed compositions (1350 °C: FeO 56.8, Fe2O3 4.5, SiO2 38.7; 1500 °C: 54, 4, 42 wt%), the Michal & Schuhmann source sentence ("those at 1500° C were obtained by extrapolation and are thus only approximate") and the "slightly less silica" starting-charge sentence. | p. 48 Table 3 and the paragraph under it. |

Genuine absences left as they were, per the review: sample mass `not_published`, total pressure `not_published` (the p. 54 "1 atm" is
the Hatch & Chipman comparison, now carried with a scope note), and regime `not_published`.

## Change 4: 10 numbered equations carried (type `equation`, quantity `printed_equation`, `quoted_unattributed`, as in shornikov-2008 and guo-2021; context only, not scored)

| Row | Page (pdf/printed) | Relation as printed (K as printed) |
|---|---|---|
| `fincham1954_equation_1` | 10/49 | ½S2 + (O)melt = ½O2 + (S)melt; K_1 = (S)(p_O2)^(1/2)/((O)(p_S2)^(1/2)) |
| `fincham1954_equation_2` | 10/49 | ½S2 + 3/2O2 + (O)melt = (SO4)melt; K_2 = (SO4)/((p_S2)^(1/2)(p_O2)^(3/2)(O)). The p_O2 exponent is small in the scan and reads 3/2 in a 3× zoom, consistent with the relation; noted in the row. |
| `fincham1954_equation_5`, `_6`, `_7` | 13/52 | (CaO)melt / (MgO)melt / ('FeO')melt + ½S2 = (CaS)/(MgS)/('FeS')melt + ½O2 |
| `fincham1954_equation_8` | 14/53 | 'FeS'(l) + ½O2 = 'FeO'(l) + ½S2 (no K printed; literature sources listed) |
| `fincham1954_equation_12` | 17/56 | CaO(s) + ½S2 + 3/2O2 = CaSO4(s); K_12 = 1/((p_S2)^(1/2)(p_O2)^(3/2)) |
| `fincham1954_equation_cs_temperature_coefficient` | 19/58 | R ∂ln C_S/∂(1/T) = −ΔH° − ΔH̄_CaS + ΔH̄_CaO, with the printed definitions |
| `fincham1954_equation_13` | 21/60 | O2− + :Si—O—Si: = 2(:Si—O−); K = (:Si—O−)^2/(O2−)(:Si—O—Si:) |
| `fincham1954_equation_14` | 21/60 | O2− + Ca2+ + :Si—O—Si: = (:Si—O−Ca2+O−—Si:); K' = (…)/(O2−)(Ca2+)(:Si—O—Si:) |

## Change 5: 96 printed tokens carried as 22 located context rows (quoted verbatim; class per page)

| Row | pdf/printed | Class | Tokens carried |
|---|---|---|---|
| `fincham1954_p40_abstract_thresholds` | 1/40 | author_derived | p_O2 < ~10^-5 atm sulphide; > ~10^-3 atm sulphate (2) |
| `fincham1954_p41_abstract_and_introduction` | 2/41 | quoted_unattributed | above 1300 °C; 2:1 and 3:1 (5) |
| `fincham1954_p42_alumina_blocks` | 3/42 | method_only | blanks fired to 1000 °C (1) |
| `fincham1954_p43_preparation` | 4/43 | method_only | calcination ~1000 °C for 2 h; iron crucibles; high-S charges ~1500 °C; Fig. 1 "six" cups (4) |
| `fincham1954_p45_p46_figure_series` | 6/45 (+46) | figure_only | Fig. 2a S1/S5 at 1 and 2 % SO2 (4); two-sample rule and digit 2; Fig. 3 1425/1500/1550/1650 approx., S1, 1 % SO2 (5) |
| `fincham1954_p47_fig4_point_spread` | 8/47 | measured_direct | ±0.002 % S (1) |
| `fincham1954_p47_figure_4_caption` | 8/47 | figure_only | full caption incl. 1 % SO2 and the 2.00 / 1.00 / 1.00 forms that differ from Table 1 (4) |
| `fincham1954_p48_feo_sio2_run_notes` | 9/48 | measured_direct | 62 %, 38 %, 1350 °C iron cups; 1.1×10^-11 vs 9.73×10^-12 atm, 0.3 % SO2, 9 h; 1.60×10^-10 atm, ×10^3 (8). The 1300/1350 discrepancy is still unresolved. |
| `fincham1954_p49_error_estimates` | 10/49 | author_estimate | flowmeters ±1 %; >0.10 % ±2 %; <0.05 % ±50 %; probable ±4 % above 0.1 % (7) |
| `fincham1954_p50_equilibrium_ranges` | 11/50 | author_derived | 10^-6, 10^-4, ~10^-8, 10^-8.5, 10^-7.5 atm (5) |
| `fincham1954_p51_pyrosulphate_literature` | 12/51 | quoted_attributed | Darken & Shields 8 % SO2, ~1300–1500 °C, negligible at 1600 °C (4) |
| `fincham1954_p52_sulphate_and_fe_fes_notes` | 13/52 | author_derived | 0.13 %; ×10^3 (2) |
| `fincham1954_p53_capacity_constancy_range` | 14/53 | author_derived | up to 1 % S and 0.01 % S (2) |
| `fincham1954_p53_literature_capacities` | 14/53 | quoted_attributed | pure 'FeO' at 1500 and 1600 °C; ~1600 °C; <5 % P2O5; <10 % MnO (5) |
| `fincham1954_p54_hatch_chipman_comparison` | 15/54 | quoted_attributed | 1500 °C, 10^-11 to 10^-6 atm, 1 atm, 10^-15.72 atm (4+) |
| `fincham1954_p55_literature_gammas` | 16/55 | quoted_attributed | γ(FeS) ~1.5 (Chipman 1948); γ(CaS) ~10 (Glaser 1926) (2) |
| `fincham1954_p57_formation_energies_quoted` | 18/57 | quoted_attributed | ca. −5 and ca. −22 kcal at 298 K (4) |
| `fincham1954_p57_aluminate_activity_notes` | 18/57 | author_estimate | molar fraction < 0.60; 1600 °C, 3 % on ΔG, ±2 kcal (4) |
| `fincham1954_p58_heat_inputs` | 19/58 | author_estimate | CaS fusion ~18 kcal (Kelley 1936 for CaO); ≤2 kcal over 150 °C (3) |
| `fincham1954_p59_feo_heat_and_contours` | 20/59 | author_derived | 0.52 to 0.56; ~10 %; γ_FeS = 6; 1500 °C guide for 1650 °C contours (5) |
| `fincham1954_p60_ratio_patterns_quoted` | 21/60 | quoted_attributed | 2:1 (Taylor & Chipman 1942), 3:1 (Oelsen & Maetz 1941) (4) |
| `fincham1954_p60_p61_structural_argument` | 21/60 (+61) | author_derived | factor ~630, 0.90 to 0.30 at 1650 °C; 0.3–0.6, factor 2, "= 1", twofold (7); elevenfold at 1500 °C and eightfold at 1650 °C (4) |

All 96 inventory tokens in the review (2+5+1+4+4+5+5+8+7+5+4+2+7+4+2+8+3+5+11+4) are covered. The print gives the "mole of melt" n subscripts as
"CaSiO" (with a mark over the a) and "O2". I transcribed them as they appear and did not complete them. Figure axes, Fig. 5/7 readings and curve coordinates stay figure-only.
Nothing was digitised and no numbers were invented.

Ledger: changed `stages.extracted.note` from "51 context rows" to "83 context rows (… 10 printed equations, 22 located printed-number context
rows added by the batch7 fix)". Descriptive only.
Diff from d6b4b9d6: 2 files (`extracts/fincham-1954-proc-roy-soc-a.yaml` +638/−51, `ledger/fincham-1954-proc-roy-soc-a.yaml` +2/−1), 640 insertions, 52 deletions.
`git diff --check` is clean. `rg` for `/Users/` and `/private/` in the changed files finds nothing. No tables/CSV, sidecar or PDF changes. No PDF committed.

## Acceptance (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)

- Green checkout: read-only `~/ci-scratch/regolith-green-ro`, HEAD `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`, clean (status empty after the runs).
  Python `~/Repos/regolith-pyrolysis-simulator/.venv/bin/python`, `PYTHONPATH=<green-ro>`, `PYTHONDONTWRITEBYTECODE=1`.
  **`engines/engines.local.toml` EXISTS in green-ro.**
- Migrator (`Migrator(root=<corpus worktree>, index={}, aliases={})._migrate_extract(<extract>)` then `finalize()`): registry issues 0 before
  and after finalize; validation issues 0; **hard issues 0**; observations 0; context **83** under DOI 10.1098/rspa.1954.0099 (was 51); benches 2;
  experiments 3; queue 1 ("extract yielded no observations", as before); evidence fallthrough {}. The migrated experiments show sweep_gas and
  printed_composition as NOT_APPLICABLE with the new locators, and the silica-cup container reads "3/8 in." with the p. 48 locator.
- `tools/validate_literature_extracts.py --check-fidelity-match <extract>`: `OK: 1 extract file(s) valid`.
- Corpus `tools/test_ledgers_valid.py` (plugin autoload off, `-c /dev/null`, `--rootdir=.`, `--confcutdir=.`): **716 passed**.
- Never run: `tools/build_index.py`, `tools/migrate_pilot_extracts.py`.

## Housekeeping

- Sparse worktree `~/Repos/regolith-corpus/worktrees/fix-fincham-1954-proc-roy-soc-a` **removed** after the push (`git worktree remove`).
  Mac free space afterwards about 57 GB (floor 30).
- Page renders are left at `~/Repos/regolith-corpus/.ferry-tmp/fincham-fix-pages/` (untracked scratch, not in git).
- No Auto-review holds in this resumed seat.
