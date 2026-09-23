# E3 KEMS comps — printed starting compositions (t-954)

Branch: `empirical/e3-kems-comps-2026-09-22` (from `origin/work-v064-green`)
Worktree: `/workspace/repos/wt/slot-05`
Tip: `f0a41b196`

## Per-source outcomes

### kems-012-sossi-2019 — LANDED

- Source table: Table 1, corpus PDF p. 53 (author manuscript of GCA 260).
- Landing: `open-furnace-ferrobasalt-series.sample.printed_composition` is the Measured major-element wt% map (SiO2 40.60, Al2O3 10.67, MgO 15.40, FeO 16.26, CaO 16.82). Label kept in locator note.
- Not folded in: Calculated column; trace-element ppm rows; no average of Calculated and Measured.
- Validator: pre-existing 144 unknown-equipment-field errors unchanged by this edit (same FAIL count before/after). No new errors from the composition landing.
- Commit: `d0ff7c2b6` — extracts: land Sossi 2019 Table 1 measured majors on ferrobasalt series

### kems-015-hashimoto-1983 — LANDED

- Source table: Table 1, Geochemical Journal 17 p. 113 (PDF p. 3).
- Landing: `fcmas-evaporation-series.sample.printed_composition` is row (1) Ave. wt% 100% normalized (SiO2 35.43, Al2O3 3.16, FeO 35.04, MgO 23.84, CaO 2.53). Paper note (1) designates this Ave. as the starting composition. Label kept in locator note.
- Not used: replicate rows 1–7, sigma, P.C., or S.C. rows as the sample map.
- Validator: OK.
- Commit: `0601e1a3b` — extracts: land Hashimoto 1983 Table 1 Ave. oxides on FCMAS series

### kems-021-plante-1992-feo — LANDED

- Source table: Table 1, ISIJ International 32 p. 1278 (PDF p. 3). Mole fractions on join x(MgO)/x(SiO2)=1.
- Landing: seven per-charge experiments `feo-mgo-sio2-xfeo-{0p15,0p30,0p40,0p54,0p70,0p80,0p90}`; each `initial_composition` is mole_fraction Composition with printed x(FeO), x(MgO), and x(SiO2)=x(MgO) from the caption join (SiO2 is not a separate Table 1 column). Labels retained on `printed_composition`.
- Table 1 ion-intensity observation split into seven rows, each FK'd to its charge experiment. Fidelity pin retargeted to `plante_1992_table1_xfeo_0p15` / `I_Fe_over_I_Mg` = 7.04.
- Kept: `feo-mgo-sio2-series` for the join-wide Table 2 activity grid (includes x values not in Table 1); label only, no single-charge map.
- Validator: OK.
- Commit: `f0a41b196` — extracts: land Plante 1992 Table 1 mole fractions on per-charge experiments

## Push

Pushed: yes (`origin/empirical/e3-kems-comps-2026-09-22`).
