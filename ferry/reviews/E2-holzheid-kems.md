# E2 — holzheid-kems (t-954) — 2026-09-22

Branch: `empirical/e2-holzheid-kems-2026-09-22` (from `origin/work-v064-green` @ 2e9e17c3d)

Tip: `1a57a0fd4` — three commits, one per source; validate_literature_extracts OK on all three; no PDFs/scout committed.

## Commits

| SHA | Source | Summary |
|-----|--------|---------|
| `3acaa8374` | holzheid-1997-feo-nio-coo-activity-metal-saturated | Split AD/BK experiments; Table 1 starting oxides |
| `7ae1654ae` | kems-006-zhang-2021 | Table 1 Initial glass EPMA oxides |
| `1a57a0fd4` | kems-010-richter-2007 | Table 1 Starting material oxides |

## Per-source

### holzheid-1997-feo-nio-coo-activity-metal-saturated

- **Locator:** Table 1, Chemical Geology 139 p. 22 (`page: 22`, `table: '1'`).
- **Action:** Replaced categorical `printed_composition` on the single lumped `variable-mgo-equilibration-series` with two experiments:
  - `ad-variable-mgo-equilibration-series` — AD (anorth.-diopside): SiO2 50.9, CaO 24.9, MgO 10.4, Al2O3 13.8, FeO 0.0
  - `bk-variable-mgo-equilibration-series` — BK (komatiitic basalt): SiO2 49.1, CaO 19.2, MgO 10.6, Al2O3 14.1, FeO 7.0
- **Mapping:** Table 3 run blocks are labelled AD vs BK; observation `experiment:` FKs re-pointed (table3a/3b AD → ad-…; table3c/3d BK → bk-…). Table 1 observation (starting compositions) left without an experiment FK (characterization, both columns).
- **Not used:** Table 3 variable-MgO / dopant run rows (not a fresh five-oxide starting vector). FeO 0.0 retained as printed (not dropped as absence).
- **Label kept** in locator notes.

### kems-006-zhang-2021

- **Locator:** Table 1, corpus PDF p. 71 (`page: 71`, `table: '1'`), **Initial glass** row only.
- **Landed on** `basalt-vacuum-evaporation-series.sample.printed_composition`: Na2O 2.27, MgO 7.09, CaO 11.21, TiO2 1.79, SiO2 45.94, Al2O3 16.00, FeO 10.67, K2O 2.49, Rb2O 1.86.
- **Not used:** Mixed powder row (Na/Al, Rb/Al only); evaporation residue rows.
- **Label kept** in locator note (`synthetic basaltic oxide and carbonate mixture` / Initial glass). Rb2O retained as printed (outside the migrate oxide key set; same pattern as E1 FeOT/SO3).

### kems-010-richter-2007

- **Locator:** Table 1, GCA 71 p. 5549 (`published_page: 5549`, `pdf_page_index: 6`, `table: '1'`), **Starting material** row.
- **Landed on** `cai-vacuum-evaporation-series.sample.printed_composition`: MgO 11.48, SiO2 46.00, Al2O3 19.39, CaO 23.12.
- **Not used:** residue composition rows.
- **Label kept** in locator note (Type B CAI-like starting glass).

## Validation / hygiene

- `uv run python tools/validate_literature_extracts.py` on the three files → `OK: 3 extract file(s) valid` (before and after commits).
- Exact printed values; no normalisation; no invented provenance; one commit per source; pathspec-only adds; no PDF or scout files in the branch.

## Push

Pushed: `origin/empirical/e2-holzheid-kems-2026-09-22` @ `1a57a0fd4` (clean working tree; three extract YAML files only).
