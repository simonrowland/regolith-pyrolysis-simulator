# Ban-ya, Chiba & Hikosaka (1980) extraction audit

Scored experimental rows are from Tables 1–4, each captioned experimental results at 1400 °C. The printed counts are 19, 12, 27, and 11 runs (69 total). Table 5 is retained as one located author-summary context record with 10 composition rows and 46 nonblank activity/composition cells; it has no run IDs, gas ratios, or stated interpolation method, so it has no scored rows, evidence class, or parent lineage. Table 6 is an interaction-energy comparison, outside the requested FetO liquid measurements. Figures 1–5 are contextual; no figure-read values were transcribed.

## Cell and layout audit

Input order: MinerU content-list tables and markdown, then `pdftotext-layout.txt`. The table QA pre-screen reported zero numeric agreement between the MinerU tables and the text layer for Tables 1–5, so every numeric cell in Tables 1–4 was checked against a 300 dpi page render. The born-digital text layer did not expose the tabular cell content in a comparable form.

| Printed table | PDF page | Rendered body rows | Numeric cells checked | Render/layout evidence |
|---|---:|---:|---:|---|
| 1, FetO–CaO | 2 | 19 | 114 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. |
| 2, FetO–SiO2 | 3 | 12 | 72 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. |
| 3, FetO–TiO2 | 4 | 27 | 162 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. Zero-form cells printed as `0.` were checked as zero, not missing. |
| 4, FetO–Al2O3 | 5 | 11 | 66 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. |
| 5, four-system activity summary | 6 | 10 | 46 | Page render confirms the four headers, composition axis, all populated cells, and the blank SiO2/Al2O3 entries at the last two compositions. Retained as located author-summary context only. |

One visibly unusual Table 1 cell is retained as printed: heat C B-6 has `P_H2O/P_H2 = 9.761` in both the table render and MinerU. This does not agree with its printed `a_FetO = 0.869` under Eq. (4); the value was not silently corrected to `0.761`.

The cell-level audit is in `banya-chiba-hikosaka-1980-feto-binary-solid-iron.cells.csv` (460 numeric cells, each marked render confirmation `(b)`). The table tool compare output is in `banya-chiba-hikosaka-1980-feto-binary-solid-iron-mineru-compare.csv`: all 69 activity cells match by page, row, and column. Its 69 valence-ratio cells are reported as mismatches because the compare helper's `tools/mineru_table_draft.py::_phase_matches_column` does not accept the required `glass` phase token; each ratio cell was independently checked against the render and the printed row and column. No locator errors remain.

## Evidence, phase, and conditions

- Tables 1–4 report analyses in wt% FeO, Fe2O3, and the listed additive oxide, alongside P_H2O/P_H2, a_FetO, and Fe3+/Fe2+. Composition is retained as printed. The source says Fe2+, total Fe, and additive oxide were chemically analysed, then composition was converted to FeO/Fe2O3/additive-oxide wt% (PDF page 2, section 2.2).
- The printed a_FetO and Fe3+/Fe2+ table values are stored as `measured_tabulated`, per the controller ruling: printed results need no derivation parents. Each activity row retains the printed pure-liquid FetO standard saturated with solid iron (Eq. (4), PDF page 2); each activity and valence row carries the experiment's printed gas ratio and full oxide analysis as located conditions. The same analysis is marked `datum_state` on both scored rows so the existing migrator binds the printed composition to their identities; the printed wt% values are retained without a Masson conversion.
- The printed Fe3+/Fe2+ value is retained as a general valence ratio (Fe3+ over Fe2+, ratio form). No valence conversion or activity recalculation is performed.
- The analyses are made on quenched material, so composition and valence rows use phase `glass`. The paper states that samples were rapidly quenched in an argon atmosphere in a water-cooled copper tube; it does not state whether Fe redox was preserved or altered (PDF page 2, section 2.2). Activity rows represent the 1400 °C liquid and use phase `l`.
- Each run has its own 1400 °C caption condition and printed gas pressure ratio. No total pressure is printed, so no total-pressure condition is stored. Gas flow rates in section 2.2 are apparatus procedure, not pressure.
- Table 5's 10-row activity grid is stored as one located context record on PDF page 6, printed Table 5. The four systems' values, printed composition axis, and blank cells are retained as printed. Following the controller ruling, it is an author summary without scored observations, measured evidence class, run IDs, gas ratios, or invented parent lineage.

## Fidelity and migration

The focused test pins Table 1 heat C B-1 activity 0.985 and Fe3+/Fe2+ 0.107 on PDF page 2, Table 2 heat C A-1 gas ratio 0.876 on PDF page 3, Table 3 heat C C-1 Fe2O3 8.55 wt% on PDF page 4, and the Table 5 grid's four printed activities at N_MxOy = 0.05 on PDF page 6. It also checks the full 69-run count, converted migrated composition and gas ratio, absence of unprinted total pressure, and mutation sensitivity of the fidelity samples.

## Isolated migration result

The real `migrate_one_extract` run produced 138 observations from the printed activity and valence values, plus 69 experiments. It reported zero hard issues and zero unavailable values. The 69 full oxide analyses remain attached as located point-condition compositions; printed gas ratios are retained in the linked experiments and on each observation's located gas-mixture condition. No simulator or tools code was changed.

The focused test pins rendered cells and Table 5's printed grid, checks all 69 runs and 138 migrated values, verifies composition conversion through the production resolver, confirms condition and identity axes and table locators after migration, confirms no derivation lineage is attached to printed values, and tests that mutated fidelity values fail.
