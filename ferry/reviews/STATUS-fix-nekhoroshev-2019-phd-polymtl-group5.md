# STATUS: fix nekhoroshev-2019-phd-polymtl — group 5 (activity reference states + reduction lineage, item 9)

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~22:05 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git`
**Pushed full tip after this group:** `9fb4916aae12a9af39a770417e8e081571537c39`
(chain: cfaa7465 → b02bcc7b g1 → 2a9892ad g2 → a99679a4 g3 → 6c47c26b g4 → 9fb4916a g5)

## Groups covered
- g1 6/6 mismatches; g2 Tables 14.1/21.1 continuations; g3 Table A.42 p.529 + Table A.6 fractions; g4 Tables 1.1/1.2.
- g5 (this push), item 9 — new YAML context rows (extract only; no table files):
  - `nekhoroshev_2019_activity_reference_states`: **40 figure captions** (Figs 6.10–30.6, pp.90–425 / PDF147–482), each with page, verbatim
    caption, `reference_state_as_printed` (β-Na2O (Na2O(s2)) for Figs 6.12, 7.2; pure liquid Na2O for 6.13–6.17, 8.10–8.15, 14.x, 21.x, 22.x,
    24.9, 25.29, 28.6, 29.x; pure liquid K2O for 9.6, 9.7, 26.7, 26.8, 30.6; pure solid K2O for 10.2; pure liquid Na2O and K2O for 13.14;
    pure liquid SiO2 for 6.10), typed `unknown` where the caption prints none (12.5, 12.6, 13.10–13.12, 25.21) with the named end-members /
    reference pressures as `activity_basis`, and `data_basis` tags (experimental as compiled / interpolated / extrapolated / recalculated from
    published or raw data / original values without recalculation / calculated model lines).
  - `nekhoroshev_2019_activity_reduction_lineage`: 12 study groups with printed relations Eqs (102)–(105) pp.109–110, (116)–(121) p.188,
    (123)–(125) p.207, (126)–(128) p.210, (130)–(131) pp.224–225, (133)–(134) pp.302–303, (135) p.312, (138) p.411; measured inputs,
    FactSage/FTsalt auxiliary inputs, reference compositions, treatment per study (recalculated in this work; taken as recalculated by
    the authors at pp.224, 225, 411; used directly / original activities at pp.110, 224, 412, 424) and `derived_from` links to figures/tables.
    Raw EMFs for [222] are a typed absence ("not published", p.412), not reconstructed. Table 6.2 lineage locator is p.92 / PDF149
    (Adams & Cohen [208] liquidus curvature; [24], [209] drop calorimetry; [157] DSC). The review's p.91 locator for that sentence is wrong: the sentence is printed on p.92.
  - Scope context `activity_ions_and_standard_states` reason now points to these two rows.
  All captions, equations and quoted statements were re-checked on 220 dpi page-image crops; no figure curve digitized.

## Checks (Mac; green 61ec839da3ba288c5df4a80f6d3ef142bd8ab461 at /Users/simonrowland/ci-scratch/regolith-green-ro; engines/engines.local.toml exists there)
- Migrator `_migrate_extract` + `finalize()`: hard issues **0**. Fidelity validator: OK: 1 extract file(s) valid. `tools/test_ledgers_valid.py`: 674 passed.

## Items done so far: 8/10 (review.md items 1, 2, 3, 4, 5, 6, 9, 10)
Remaining: 8 per-study method facts (p.5 done in g1; pp.144, 159, 216, 256, 302, 385, 400 ...); 7 numbered equations (1)–(138) + narrative quantities.
