# E9 nakano / rusiecka / yam — printed starting compositions (t-954)

Branch: `empirical/e9-nakano-rusiecka-yam-2026-09-22` (from `origin/work-v064-green` @ 2e9e17c3d)
Worktree: `/workspace/repos/wt/slot-07`
Tip: `00c7ea66d1999b8607918aba3bd4774e027669b8`

## Per-source outcomes

### nakano-hashimoto-2020-bubbles-to-chondrites-i — LANDED

- Source table: Table 1, PEPS 7:47 p. 2 (PDF p. 2), bulk composition mol% of MOx.
- Landing: split into `allende-laser-boiling` and `lsil-laser-boiling`; each `sample.initial_composition` is mole_fraction Composition from the printed mol% (/100 unit conversion only; not renormalised). Labels retained on `printed_composition`. Total row omitted. Later evaporation-product tables not used as the charge.
- Kept: `laser-boiling-series` (label only) for mixed Allende+Lsil observation bags.
- Validator: OK.
- Commit: `07d596495` — extracts: land Nakano 2020 Table 1 Allende and Lsil mol% on per-material experiments

### rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts — LANDED

- Source table: Table 1, GCA 393 p. 210 (PDF p. 3), starting materials on anhydrous basis; three powders ICB-2 / ICB-4 / ICB-8.
- Landing: `icb-2-start`, `icb-4-start`, `icb-8-start` each get the printed anhydrous oxide wt% map (SiO2, TiO2, Al2O3, FeO, MgO, CaO, Na2O, K2O). H2O column retained only in locator notes (anhydrous-basis oxide map). Analytical +/- uncertainties not folded in. Labels retained in locator notes.
- Not used as sample composition: Table 2 product-glass rows; Table 3 basalt/andesite/rhyolite calculation mixes. Table 2 run→powder mapping is not explicit in the paper, so product-glass observation FKs stay on umbrella `icb-series` (label only).
- Validator: still FAIL with the same 4 pre-existing errors (provenance_path absolute; model_comparison type; fidelity_samples shape); unchanged by this edit.
- Commit: `c2f3ed8e1` — extracts: land Rusiecka 2025 Table 1 starting powders on per-powder experiments

### yam1983 — LANDED

- Source table: Table 1, J. Japan Inst. Metals 47 p. 738, seven binary X_Na2O batch values.
- Landing: seven experiments `na2o-sio2-xna2o-{0p205,0p298,0p356,0p400,0p429,0p500,0p601}`; each `initial_composition` is mole_fraction Composition with printed X_Na2O and SiO2=1-X_Na2O complement. Labels retained on `printed_composition`. Printed spelling kept (0.400, 0.500).
- Not used as these charges: Table 1 Ref. row; prose reference melt 0.395Na2O–0.526SiO2–0.079Fe2O3 (separate electrode melt).
- Kept: `na2o-sio2-emf-series` (label only) for mixed observation bags.
- Validator: OK.
- Commit: `00c7ea66d` — extracts: land Yamaguchi 1983 Table 1 X_Na2O mole fractions on per-charge experiments

## Push

Pushed: yes (`origin/empirical/e9-nakano-rusiecka-yam-2026-09-22`).
