# Ban-ya, Hino & Yuge (1985) extraction audit

## Table 1

Table 1 (printed page 854; PDF page 2) has **30 runs**: 101–104, 301–304, 306, 501, 502, 504–506, 508–510, 575, 500, 701–706, and 901–905. The extract preserves every run in that printed order as 30 activity observations and 30 Fe3+/Fe2+ observations. For each run it stores the analysed FeO, Fe2O3, SiO2 and MnO wt% values, the printed H2O/H2 ratio, T = 1450 C from the caption, the printed FetO activity and the printed Fe3+/Fe2+ ratio. CaO and MgO are typed unknown because they do not appear in Table 1. Fe2O3 is analysed and numeric in every printed row.

The MinerU table draft has 30 body rows. MinerU table QA reports 0.000 numeric agreement for Table 1 against pdftotext and flags numeric mismatches, so the numeric row cells were checked against the 300 dpi PDF page render. The rendered column alignment was checked once for the full table; each stored numerical cell (seven numeric table columns per run plus the caption temperature) is listed with its render confirmation in the accompanying `.cells.csv`. The render agrees with the MinerU draft values for the transcribed cells. No table cell needed OCR redo.

The compare tool output is included as `.mineru-compare.csv`. It reports all 30 activities as mismatches because its phase matcher accepts `gas`, `liquid`, and `solid`, but not the required migrator phase token `l`; it consequently filters out every candidate cell. The values were resolved against the page render as recorded above. No simulator or tooling code was changed.

Run 101's four rendered oxide values sum to 98.70 wt%, while the paper's experimental text says the four-component totals were within 100 ± 1 wt%. The printed cells are retained unchanged; the discrepancy is noted on that observation rather than silently corrected.

The table caption states equilibrium with solid iron, so all 30 slag runs are iron-saturated. The experimental text on printed page 853 says the slag was equilibrated in a pure iron crucible with H2O/H2 gas and rapidly quenched and solidified in the lower cooling zone. It reports about 6 h as sufficient for equilibrium. The text does not say that quenching preserved or altered oxidation state; the observation records `quench_direction: not_stated`. The activity is reported for the 1450 C equilibrium melt (`phase: l`); the source's standard state is FetO(l), as named in Table 2 reaction (1), not stoichiometric FeO(l). The authors define NFetO = NFeO + NFe2O3 (printed page 853). No conversion to a Masson N_Fe2+ basis is applied. The authors' Table 1 values for both a_FetO and Fe3+/Fe2+ are their own printed experimental measurements, so both are stored as `measured_tabulated`. The activities remain exactly as printed; they are not recomputed from the gas-equilibrium equations. The printed H2O/H2 ratio and temperature are stored as row conditions, and neither measurement has an invented derivation parent.

The total pressure is not printed. No total pressure or absolute fO2 is assigned. The per-run H2O/H2 ratio is retained as the gas-mix condition without converting it to an oxygen pressure.

## Figures

Figures 1–13 are recorded as individual context records with their captions summarized and locators. No plotted values are digitised. Figure locators follow the pdftotext page breaks: Figures 1–2 on PDF page 2, Figure 3 on page 3, Figure 4 on page 4, 5–8 on page 5, 9–11 on page 6, and 12–13 on page 7.

## Row and cell audit

The row count is reconciled to the printed Table 1: 30 printed rows, represented by 30 activity observations and 30 Fe3+/Fe2+ observations. The full cell audit records source column, exact digits, run, PDF/published page, table locator, and render verification for each of the 240 stored numeric cells, including the printed Fe3+/Fe2+ column (Table 1 column 7).
