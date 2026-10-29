# Ban-ya, Chiba & Hikosaka (1980) extraction audit

Source rows are from Tables 1–4, each captioned experimental results at 1400 °C. The printed counts are 19, 12, 27, and 11 runs (69 total). Table 6 is an interaction-energy comparison, outside the requested FetO liquid measurements. Figures 1–5 are contextual; no figure-read values were transcribed.

## Cell and layout audit

Input order: MinerU content-list tables and markdown, then `pdftotext-layout.txt`. The table QA pre-screen reported zero numeric agreement between the MinerU tables and the text layer for Tables 1–5, so every numeric cell in Tables 1–4 was checked against a 300 dpi page render. The born-digital text layer did not expose the tabular cell content in a comparable form.

| Printed table | PDF page | Rendered body rows | Numeric cells checked | Render/layout evidence |
|---|---:|---:|---:|---|
| 1, FetO–CaO | 2 | 19 | 114 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. |
| 2, FetO–SiO2 | 3 | 12 | 72 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. |
| 3, FetO–TiO2 | 4 | 27 | 162 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. Zero-form cells printed as `0.` were checked as zero, not missing. |
| 4, FetO–Al2O3 | 5 | 11 | 66 | Page render confirms all seven column positions, heat labels, and row order; every numeric cell compared with the MinerU draft. |

One visibly unusual Table 1 cell is retained as printed: heat C B-6 has `P_H2O/P_H2 = 9.761` in both the table render and MinerU. This does not agree with its printed `a_FetO = 0.869` under Eq. (4); the value was not silently corrected to `0.761`.

The cell-level audit is in `banya-chiba-hikosaka-1980-feto-binary-solid-iron.cells.csv` (414 cells, each marked render confirmation `(b)`). The table tool compare output is in `banya-chiba-hikosaka-1980-feto-binary-solid-iron-mineru-compare.csv`: all 69 activity cells match by page, row, and column. Its 69 valence-ratio cells are reported as mismatches because the compare helper's `tools/mineru_table_draft.py::_phase_matches_column` does not accept the required `glass` phase token; each ratio cell was independently checked against the render and the printed row and column. No locator errors remain.

## Evidence, phase, and conditions

- Tables 1–4 report analyses in wt% FeO, Fe2O3, and the listed additive oxide, alongside P_H2O/P_H2, a_FetO, and Fe3+/Fe2+. Composition is retained as printed. The source says Fe2+, total Fe, and additive oxide were chemically analysed, then composition was converted to FeO/Fe2O3/additive-oxide wt% (PDF page 2, section 2.2).
- a_FetO is the authors' measured-reduced calculation using Eq. (4), with the printed gas ratio as input. The standard state is pure liquid FetO saturated with solid iron (Eqs. (1)–(4), PDF page 2).
- The printed Fe3+/Fe2+ ratio is retained as a general valence ratio (Fe3+ over Fe2+, ratio form) and as measured-reduced from the chemical analyses.
- The analyses are made on quenched material, so composition and valence rows use phase `glass`. The paper states that samples were rapidly quenched in an argon atmosphere in a water-cooled copper tube; it does not state whether Fe redox was preserved or altered (PDF page 2, section 2.2). Activity rows represent the 1400 °C liquid and use phase `l`.
- Each run has its own 1400 °C caption condition and printed gas pressure ratio. No total pressure is printed, so no total-pressure condition is stored. Gas flow rates in section 2.2 are apparatus procedure, not pressure.
- Table 5 is a regular-composition summary of FetO activity values and not a list of the gas-equilibrated analyses in Tables 1–4. Its row-to-run mapping and any interpolation method are not stated. Its treatment is pending controller clarification rather than assigning unsupported parent rows or evidence class.

## Fidelity and migration

The focused test pins Table 1 heat C B-1 activity 0.985 and Fe3+/Fe2+ 0.107 on PDF page 2, Table 2 heat C A-1 gas ratio 0.876 on PDF page 3, and Table 3 heat C C-1 Fe2O3 8.55 wt% on PDF page 4. It also checks the full 69-run count and mutation sensitivity of the fidelity samples.

The isolated migration result and issue counts are added after the focused test succeeds.
