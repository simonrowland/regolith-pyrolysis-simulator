# STATUS: fix jacobson-1989-metal-oxide-zirconia (batch2 section B)

**From:** regolith-empirical (fix seat)   **To:** regolith-main   **At:** 2026-10-05 ~21:30 ET

- sid: jacobson-1989-metal-oxide-zirconia
- branch: hunt/jacobson-1989-metal-oxide-zirconia on mac-studio-256-1:Repos/regolith-corpus.git (pushed to that hunt branch only; not merged to main)
- reviewed tip (verified on fetch before building): d9067776d83f277cd9b669e130fff1edbb6b610e
- **NEW TIP: 2696b241bd4c8ab6ff8d2becf236cf411b528e8d** (one commit on d9067776: "Fix Jacobson 1989 extract per review of d9067776"; explicit pathspecs, no trailers)
- outcome: FIXED, ready for r2 confirm review. Items fixed 9/9 (applied 9, disproved 0). The 14 counted mismatches are all corrected (4 numeric cells, 2 prose/locator, 6 figure locators, 2 method/attribution classes). All 43 missing printed numbers N01-N43 are now carried, plus all 22 printed equations. Hard issues 0.
- illegible values / typed absences: 0 illegible values found, so no guesses and no absences for illegible print. I added one typed absence, `not_published`, for sample preparation and composition in the Fig. 15 cited data sets, because p.14 states none of them.
- files changed: extracts/jacobson-1989-metal-oxide-zirconia.yaml, ledger/jacobson-1989-metal-oxide-zirconia.yaml, tables/jacobson-1989-metal-oxide-zirconia/{t2.csv,t2.provenance.yaml,t3.csv,t3.provenance.yaml}

## Method
Sparse worktree (`--no-checkout`, then sparse-checkout of raw/<sid>, text/<sid> and every sidecar.yaml) at ~/Repos/regolith-corpus/worktrees/fix-jacobson-1989-metal-oxide-zirconia, branch hunt/<sid> tracking origin. I rendered all 64 pages with `pdftoppm -r 220 -png` and read them as images, rotating the appendix table pages upright and zooming in on the changed cells and equations. pdftotext was used only as a lead. I did not run tools/build_index.py or tools/migrate_pilot_extracts.py.

## Per-item list (review.md "Required changes")
1. **APPLIED**: Table A-I ErO(g), p.59 / PDF 61. The page prints 1800 and 2000 in the Temperatures cells beside "ΔG = -7.090T + -29 596", source 16. I filled T_low_K = 1800 and T_high_K = 2000 in t2.csv row 124 and in YAML row 124. numeric_cell_count went from 784 to 786.
2. **APPLIED**: Table A-II BaZrO3, p.61 / PDF 63. The 29.8 in the S(298) column sits on the Parker (3) line; the L'vova and Feodose'v (6) line has a blank there. I moved the value from row 16 to row 17 in the CSV and YAML. Row 21, King and Weller 29.8±0.3, was already correct and is unchanged.
3. **APPLIED**: p.5 / PDF 7. The page prints "one atomosphere of argon was put in as a reactant" (misspelling as printed). The quote now reads argon, with `argon_reactant_atm: 1`. The Fig. 2 one-atmosphere-oxygen comparison is a separate statement (`oxygen_overpressure_atm: 1`).
4. **APPLIED**: p.20 / PDF 22. "100 minutes at 2580°K" now has locator published_page 20 / pdf_page_index 21, the full quote, attribution to Belov et al. [38], `sample_composition_mol_percent {Sc2O3: 50, ZrO2: 50}`, and the outcome "all the Sc2O3 has vaporized". It also appears in the new Sc2O3 route context.
5. **APPLIED**: figure locators confirmed against PDF 39-48. They are now Fig.5 38/39, Fig.9 39/40, Figs.13-14 40/41 and Figs.17-18 41/42; the other 34 were already correct. The ledger figure span is now "published pages 37-46". Each figure's attribution now carries its caption reference and reprint permission: American Ceramic Society; Royal Society of Chemistry for Fig.7; British Library for Figs.17 and 21. Figs.17 and 21 also keep their line/point caption text. The vapor-pressure figures are labeled as Jacobson SOLGASMIX-PV calculations (p.48). Every figure row stays figure_only, and no curves were digitized.
6. **APPLIED**: `jacobson_1989_fig15_cited_experiment_facts.values.method_class` changed from figure_only to quoted_attributed. Its attribution names Odoj and Hilpert [24-26], Berkowitz-Mattuck [10] and Glenn et al. [27], with a verbatim p.14 source_quote. All three evidence sets are kept. The Glenn set now also carries:
   - Eq.(11) J=P/(2πMRT)^1/2 with its symbols;
   - Eq.(12) with the coefficient 2.26e-2 (N19);
   - the agreement sentence.
   Also added: the calculated-lower-than-measured BaZrO3 qualification ("a little BaO rich" is a possible explanation, not a measured composition), and the sample not_published absence. The Fig.15 curve row stays figure_only.
7. **APPLIED**: `jacobson_1989_activity_standard_states` now holds only Jacobson's general Eqs.(15) and (16) and the ZrO2+ difficulty sentence. It keeps quoted_unattributed. New attributed or estimated contexts:
   - `jacobson_1989_belov_mo_ion_activity_route` (quoted_attributed, Belov [32]): MO+ ion; routes (3) and (13); Eq.(17); Eq.(18) with the squared MO term; the P(O-tot) definition; initialization; the update P(O-tot) = P(O-M2O3) + P(O-ZrO2); iteration to convergence; inputs and standard state.
   - `jacobson_1989_sc2o3_zro2_activity_route` (quoted_attributed, Semenov [37] and Belov et al. [38-39]): the reused Eq.(15) P(ScO-sol)=P°(ScO)[x(Sc2O3)], explicitly not a(M2O3)=x(M2O3).
   - `jacobson_1989_cao_zro2_emf_activity` (quoted_attributed, refs 19 and 20): Eqs.(7)-(9), the CaO(a=1) standard, 1200-1550 K, "about an order of magnitude" below Raoult and "about two orders" below pure CaO, and the slow-phase-change qualification.
   - `jacobson_1989_mgo_activity_upper_limit` (author_estimate, p.9): a(MgO) "essentially unity" at the MgO-rich cubic boundary, used for an upper-limit estimate and not a measured activity.
8. **APPLIED**: N01-N43 are carried with quotes, locators, attributions and bound fields, keeping cited-experiment and model statements apart:
   - `jacobson_1989_cited_phase_and_vaporization_facts` (quoted_attributed, 23 statements): N02-N08, N16-N18, N22-N24, N27-N32, N41-N43, plus the p.16 survey bound to Belov and Semenov [28].
   - `jacobson_1989_calculated_vapor_pressure_statements` (model_derived, 16 statements): N01, N14, N15, N21, N25, N26.
   - N09, N11-N13 and N40 are in the activity contexts above, N19 is in the Fig.15 context and N20 is in the Belov route. N10 (CaO·ZrO2 melting point 2252 K) is in `jacobson_1989_numeric_and_directional_statements`. N33 and N36-N39 are in the appendix context (item 9). N34-N35 are the Table A-I fix (item 1).
   - `jacobson_1989_printed_equations` lists every equation in the review's inventory (22), with page locators, as printed. Reused numbers are kept apart: main (1), main (15) and the reused (15) on p.20, appendix (1) and (2).
   - **Note on N20 (p.18 / PDF 20):** the character after "setting P(ZrO) =" has the round shape of this typeface's letter O, not its narrower zero (compare "2580" on p.20). The quote keeps it as printed. `first_iteration_P_ZrO: 0` is the reading, as the starting ZrO pressure before any ZrO2 contribution, and the locator note says so.
9. **APPLIED**: new `jacobson_1989_appendix_data_sources_and_estimates`, pp.47-48 / PDF 49-50, carries:
   - appendix Eq.(1) Cp, with `coefficient_scaling {A: 1, B: 1e-3, C: 1e+5, D: 1e-6}` (N36-N38);
   - appendix Eq.(2) G, with the T^-2 and T^3 exponents (N39);
   - JANAF, Pankratz [9], Ruzinov [10], refs 14 and 16, and Korneev [11] as sources;
   - "both estimated and measured data", and the emf-versus-calorimetric caveat;
   - the 47.8 cal/mole-K entropy estimate after Barin and Knacke [13] (author_estimate, N33), naming Table A-I rows 56, 70, 78, 88 and 97;
   - heat capacities estimated by summing M2O3 and 2(ZrO2) (author_estimate);
   - free energy built from the elements in their standard states, then used in SOLGASMIX-PV.
   The Table A-I context also gains a `provenance_qualification` (not every value is measured) and a `cp_equation`.

Documentation findings: the ledger figure span is fixed (item 5). The "four front-matter pages" error is in the author's report, not in any tracked file, so there was nothing to change. I disproved nothing; every item matched the page image.

Not changed (outside the review): the phase-diagram figure rows keep the original context `type` label `calculated_vapor_pressure_figure`. Their method class (figure_only), locators and attributions are correct.

## Acceptance (green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461)
- Reader: read-only green clone ~/ci-scratch/regolith-green-ro at 61ec839da (nothing written). Used /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python -B with PYTHONPATH set to that clone and PYTHONDONTWRITEBYTECODE=1. engines/engines.local.toml **exists** in that clone.
- `Migrator(root=Path.cwd(), index={}, aliases={})._migrate_extract(<worktree>/extracts/jacobson-1989-metal-oxide-zirconia.yaml)` then `finalize()`: ValidationReport has 0 issues (0 hard). Result: 1 work, 0 experiments, 0 benches, 0 observations, 54 contexts (46 before plus 8 new).
- Payload survival in the migrated context rows (all present):
  - Fig.15 apparatus class is quoted_attributed; the Fig.15 curve is figure_only.
  - ErO row 124 has T 1800/2000; BaZrO3 row 17 has S 29.8 and row 16 has null.
  - The argon quote is present; the 100-minute statement is at p.20/idx 21 with the 50/50 composition.
  - Belov Eq.(18) has MO_pressure_exponent 2 and initial P(ZrO) 0. The Glenn coefficient is 0.0226.
  - CaO emf range is [1200, 1550] with the pure CaO standard. MgO is author_estimate with value 1.
  - Cp scaling is {A: 1, B: 0.001, C: 100000.0, D: 1e-06}; S estimate 47.8; G exponents [-1, 0, 1, -2, 3].
  - Statement counts: 23 cited, 16 model, 22 equations. The six figure locators are corrected.
- evidence_for(): quoted_attributed, quoted_unattributed, model_derived, author_estimate and figure_only all resolve to their own class (none unknown).
- `tools/validate_literature_extracts.py --check-fidelity-match <absolute worktree extract path>` (run from the green clone): "OK: 1 extract file(s) valid", exit 0.
- Corpus `tools/test_ledgers_valid.py` (pytest -q -o addopts='' -p no:cacheprovider, rootdir and confcutdir set to the worktree, PYTEST_DISABLE_PLUGIN_AUTOLOAD=1): 687 passed.
- `rg '/Users/|/private/'` over the changed files: 0 hits.

rows checked 159 (table cells unchanged except the 3 fixed rows), mismatches fixed 14/14, printed numbers newly carried 43/43, equations carried 22/22, hard issues 0

— regolith-empirical
