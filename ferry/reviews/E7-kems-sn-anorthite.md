# E7 — kems-sn-anorthite (t-954) — 2026-09-22

Branch: `empirical/e7-kems-sn-anorthite-2026-09-22` (from `origin/work-v064-green` @ 2e9e17c3d)

Tip: `07e2e77e9` — three commits, one per source; no PDFs/scout committed.

## Commits

| SHA | Source | Summary |
|-----|--------|---------|
| `e8801b80b` | kems-118-yamamoto-1983 | Table 4 dilute N_Sn mole fractions on per-charge experiments |
| `509a0473c` | kems-137-bischof-2023 | Table 6 Start EPMA oxides on per-run An-Di experiments |
| `07e2e77e9` | kems-200-ueshima-1983 | Table 1 at% Mo as mole fractions on per-sample experiments |

## Per-source

### kems-118-yamamoto-1983

- **TRAP avoided:** Table 1 is the pure-Fe / pure-Sn impurity assay — not used as charge.
- **Locator:** Table 4, Trans. ISIJ p. 59 (`published_page: 59`, `pdf_page_index: 4`, `table: '4'`).
- **Action:** Added 10 dilute Fe–Sn experiments `fe-sn-nsn-0p010` … `fe-sn-nsn-0p100` with `initial_composition` mole fractions:
  - N_Sn as printed: 0.010, 0.020, 0.030, 0.040, 0.050, 0.060, 0.070, 0.080, 0.089, 0.100
  - N_Fe = 1 − N_Sn from binary Fe–Sn (not renormalised)
- Kept `fe-sn-series` as the multi-charge shell for Table 2/3 broader grid; note points dilute charges to `fe-sn-nsn-*`.
- Updated `fe-sn-cu-series` label to state N_Sn held at 0.03 with N_Cu stepped per Table 5 row — no single `initial_composition` map (N_Cu varies; mapping would invent a charge).
- **Label kept** in printed_composition notes.

### kems-137-bischof-2023

- **Locator:** Table 6 Start columns, PDF p. 15 (`page: 15`, `table: '6'`); nominal prose also on series (p. 4 §2.1).
- **Action:** Added three run experiments with Table 6 **Start** EPMA wt% (End not landed as start):
  - `an-di-1-low`: SiO2 50.47, Al2O3 15.38, MgO 10.42, CaO 23.63 (mass 79.1 mg)
  - `an-di-2-low`: same Start as 1_low (same starting glass); mass 72.92 mg
  - `an-di-3-high`: SiO2 49.72, Al2O3 15.15, MgO 10.27, CaO 23.28 (Total 98.42; Ga2O3/In2O3 explained in note, not in Table 6 columns)
- Kept `an-di-series` with **nominal** oxides SiO2 50.34, Al2O3 15.40, MgO 10.80, CaO 23.46 for multi-run aggregates.
- Re-pointed run-tagged observations (`run: 1_low` / `2_low` / `3_high`) to the matching experiment.
- **Label kept** (Anorthite-diopside eutectic / 42 wt% anorthite + 58 wt% diopside).

### kems-200-ueshima-1983

- **Locator:** Table 1, Tetsu-to-Hagané p. 558 (`published_page: 558`, `pdf_page_index: 3`, `table: '1'`), column “Composition of sample (at % Mo)”.
- **Action:** Added 42 per-sample experiments (`femo-sh-*` + `femo-as-melt`) with `initial_composition` mole fractions:
  - Mo = (at% Mo)/100 as printed (analysed or aimed-in-parentheses, flagged in note)
  - Fe = 1 − Mo from binary Fe–Mo (not renormalised)
- Kept `femo-annealing-series` as the multi-sample shell with the 30–88 at% Mo range label.
- Aimed compositions (parentheses in Table 1) retained and labelled aimed; not dropped.
- **Label kept** in printed_composition.

## Validation / hygiene

- `uv run python tools/validate_literature_extracts.py` on kems-118 and kems-200 → `OK`.
- kems-137: **26 pre-existing** `equipment.sample` / `equipment.starting_glass_wt_pct` errors (legacy equipment blob shape); error set identical before/after this landing (no new errors gained).
- Exact printed values; no normalisation; no invented provenance; one commit per source; pathspec-only adds; no PDF or scout files in the branch.

## Push

Pushed: `origin/empirical/e7-kems-sn-anorthite-2026-09-22` @ `07e2e77e9` (clean working tree; three extract YAML files only).
