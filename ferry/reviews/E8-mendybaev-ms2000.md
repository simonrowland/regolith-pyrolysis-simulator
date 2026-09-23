# E8 — kems-ms2000-044 / mendybaev-2017 / mendybaev-2021 (t-954)

Branch: `empirical/e8-mendybaev-ms2000-2026-09-22` (from `origin/work-v064-green` @ 2e9e17c3d)
Worktree: `/workspace/repos/wt/slot-01`
Tip: `a856a8c271247123aee4ecbda763dae89d27b6dd`

## Commits

| SHA | Source | Summary |
|-----|--------|---------|
| `fcb778a80` | kems-ms2000-044 | Table 1 x(SiO2) on per-melt experiments |
| `c7f05c672` | mendybaev-2017-fun-cai-lab-evaporation | Structure FUNC starting oxides on sample |
| `a856a8c27` | mendybaev-2021-cai-low-pressure-h2-evap | Quote CAI4B2 starting prose composition |

## Per-source

### kems-ms2000-044 — LANDED

- **Locator:** PDF p. 22, Table 1 (activities of Na2O/K2O/SiO2 in melts chosen at random).
- **Action:** Replaced lumped `alkali-silicate-kems-series` with **22** per-melt experiments (12 Na2O-SiO2 + 10 K2O-SiO2 unique x(SiO2); Table 1 has 24 rows — x=0.722 and x=0.630 each appear at two T and share one experiment).
- **Landing:** `printed_composition` keeps the binary+x label; `initial_composition` is mole_fraction with printed x(SiO2) and binary complement x(Me2O)=1−x(SiO2) (note records the identity; not renormalised).
- **Observation FKs:** 96 melt-activity rows retargeted; Table 2/3 solid-silicate gibbs rows left without an experiment FK (scout: stoichiometric solids, not a wt% melt assay).
- **Not used:** Table 2 solid formulas as charge assays.
- **Validator:** OK.

### mendybaev-2017-fun-cai-lab-evaporation — LANDED

- **Locator:** §3.1 prose (PDF p. 8) and Table 1 FUNC starting row (PDF p. 39).
- **Action:** Structured the four numbers already in the label into `sample.printed_composition` oxide map: MgO 37.9, Al2O3 11.6, SiO2 42.9, CaO 7.6 (quoted spelling). Label retained in locator note.
- **Not used:** residue rows (B133R-10 + FUNC-*) as start.
- **Validator:** OK.

### mendybaev-2021-cai-low-pressure-h2-evap — LANDED

- **Locator:** PDF p. 5, §3.1 prose (not a numbered table).
- **Action:** Quoted the printed prose so YAML keeps the full string: `16 wt% MgO, 36% SiO2, 27% Al2O3, and 21% CaO` (was unquoted flow value; parser kept only `16 MgO`). Applied on all three CAI4B2 experiments (H2 2e-4, H2 2e-5, vacuum). Locator note records prose source; Table 1 has no oxide columns (scout-confirmed).
- **Not structured** into an oxide map (lane instruction: land as printed prose).
- **Validator:** still FAIL with 1 pre-existing `fidelity_samples` required error (unchanged count; not touched).

## Validation / hygiene

- `validate_literature_extracts.py`: ms2000 OK; 2017 OK; 2021 pre-existing fidelity_samples only (no new errors).
- Exact printed values; no normalisation; one commit per source; pathspec-only adds; no PDF or scout files in the branch (3 extract YAML files only).

## Push

Pushed: yes (`origin/empirical/e8-mendybaev-ms2000-2026-09-22` @ `a856a8c27`).
