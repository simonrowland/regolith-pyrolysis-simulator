# E6 kems slag — printed starting compositions (t-954)

Branch: `empirical/e6-kems-slag-2026-09-22` (from `origin/work-v064-green` @ 2e9e17c3d)
Worktree: `/workspace/repos/wt/slot-02`
Tip: `ad781729ea4cd0ce1b162904f821b5955c6b1aec`

## Commits

| SHA | Source | Summary |
|-----|--------|---------|
| `9a993d5ba` | kems-057-kambayashi-1985 | Tables 3–4 post-run slag analyses on 3+8 per-melt experiments |
| `704cdcb41` | kems-088-ichise-1975 | Table 2 Coulomatic %S on 8 per-alloy experiments |
| `ad781729e` | kems-112-ichise-1989 | Tables 1 / 2 / 5 on 20+12+20 per-heat experiments |

## Per-source outcomes

### kems-057-kambayashi-1985 — LANDED

- **Locator:** Table 3, Tetsu-to-Hagané p. 1913 (PDF p. 3); Table 4, p. 1914 (PDF p. 4).
- **Nature:** Post-run chemical analysis (paper: composition after the KEMS run). Landed as printed sample composition.
- **Landing:** Replaced `pbo-p2o5-series` / `feto-p2o5-series` with:
  - `pbo-p2o5-melt-{1,2,3}` — Table 3 elemental `Pb` / `P` wt%; `X_P2O5` mole fraction in locator note only.
  - `feto-p2o5-sample-{1..8}` — Table 4 oxide wt% `P2O5` / `FeO` / `Fe2O3`; Total Fe, `t` in FetO, and `X_P2O5` in locator notes only.
- **Labels retained** in locator notes (reagent purities).
- **Observation FKs:** multi-melt bags that pointed at the old series had `experiment:` removed.
- **Validator:** OK.
- **Commit:** `9a993d5ba`

### kems-088-ichise-1975 — LANDED (Table 2 only; Table 1 trap respected)

- **Locator:** Table 2, Trans. ISIJ p. 117 (PDF p. 3), `%S` (Coulomatic).
- **TRAP confirmed:** Table 1 is the pure-iron impurity assay — not the alloy charge. Not landed.
- **Landing:** Split `fes-kems-series` into `fe-s-{0p31,0p42,0p65,1p01,1p59,2p19,3p37,4p32}-wtpct` with `printed_composition: {S: '<printed>'}`. Values: 0.31, 0.42, 0.65, 1.01, 1.59, 2.19, 3.37, 4.32.
- **Scout note:** scout said 9 rows; printed Table 2 / extract have **8**. Landed 8; did not invent a ninth.
- **Labels retained.** Multi-row observation FKs dropped.
- **Validator:** OK.
- **Commit:** `704cdcb41`

### kems-112-ichise-1989 — LANDED (Tables 1, 2, 5; Table 3 trap respected)

- **Locator:** Table 1 p. 846 (X_Ta, 20 heats); Table 2 p. 847 (metallography at% Ta, 12); Table 5 p. 848 (X_Nb, 20 heats).
- **TRAP confirmed:** Table 3 is EPMA phase-boundary at% Ta — not the bulk charge. Not landed; observation kept without experiment FK.
- **Landing:**
  - `fe-ta-heat-{N}` × 20 — Table 1 molar fraction `Ta`.
  - `fe-nb-heat-{N}` × 20 — Table 5 molar fraction `Nb`.
  - `fe-ta-metallography-{1..12}` × 12 — Table 2 at% `Ta`; method `metallography`.
- **Fe balance** not printed and not invented.
- **Scout note:** scout said 21 Nb heats; extract Table 5 has **20**. Landed 20; did not invent a 21st.
- **Validator:** OK.
- **Commit:** `ad781729e`

## Validation / hygiene

- `uv run python tools/validate_literature_extracts.py` on the three files → OK.
- Exact printed values; no normalisation; no invented provenance; one commit per source; pathspec-only; no PDF or scout files in the branch.

## Push

Pushed: yes — `origin/empirical/e6-kems-slag-2026-09-22` @ `ad781729ea4cd0ce1b162904f821b5955c6b1aec`.
