# Full-cell audit — Ban-ya, Hino and Takezoe (1985)

## Printed row counts and cell checks

| Printed table | PDF page (published page) | Printed data rows | Audit |
| --- | ---: | ---: | --- |
| Table 1 | 2 (1766) | 7 | Every body cell, including all 35 numeric cells, checked against the 300 dpi page render. |
| Table 2 | 2 (1766) | 19 | Every body cell, including all 152 numeric cells, checked against the 300 dpi page render. The render resolves the last header as a_FetO; MinerU read a_Fe2O. |
| Table 3 | 3 (1767) | 11 | All 11 reaction, free-energy and reference rows checked against the 300 dpi page render; retained as context only. |

The cell-level ledger is `banya-hino-takezoe-1985-na-fe-silicate-redox.cells.csv`. Each body cell is keyed by its printed table, CSV table row and column. It also lists each retained equation parameter and the prose activity bound at its page/equation locator. `confirmation=b` means the cell or equation value was checked against the page render. No `<details>` figure-derived tables were included.

MinerU table QA reported numeric agreement 0.086 for Table 1, 0.039 for Table 2 and 0.529 for Table 3. The pdftotext layer does not reliably place the Table 1/2 body digits into their columns; Tables 1 and 2 were therefore checked in full against the render. Table 3 layout and all equation signs/numbers were checked against its render. No individual trapped cells were left unresolved and no OCR redo was needed because the MinerU body tables were readable and the renders settled their contents.

## Scientific and schema audit

- Table 1 contributes 7 `valence_ratio` observations for quenched glass, with FeO, Fe2O3 and Na2O wt% retained as each row's measured composition. The table reports Fe3+/Fe2+ as ratios; no conversion to ferric fraction is made.
- Table 2 contributes 19 quenched-glass `valence_ratio` observations and 19 equilibrium-liquid Fe_tO activity observations. The authors calculate the activities from the printed run-specific P_H2O/P_H2 ratio using Eq. (2) and K1. The ratio is stored as a row condition and the standard free-energy relation is retained as Table 3 context citing Ref. 10, but those inputs cannot be referenced as stored observation parents; the activity values therefore use `quoted_attributed` under the missing-input rule. The printed reference state is Fe_tO(l), not FeO(l).
- Methods on PDF page 1 state that both sample series were quenched under Ar. Neither the methods nor the result tables state whether Fe valence was preserved or altered; every glass ratio row records `quench_state=not_stated` with the page and section. The activity rows describe the 1400°C liquid equilibrium.
- Table 1 conditions are 1610°C and pure CO2 with P_CO2=1 atm. The printed gas condition is retained in `gas_mix`; it is not converted to a total-pressure or fO2 identity. Table 2 records 1400°C and each run's P_H2O/P_H2; no total pressure or absolute fO2 is printed. Those conditions remain absent.
- The paper defines N_FetO=N_FeO+N_Fe2O3. FeO and Fe2O3 remain separate measured composition components; no total iron value is relabeled as ferrous FeO.
- Table 3's 11 standard free-energy expressions and the regular-solution interaction parameters are context, not observation rows. Explicit printed parameters are α12(Fe2+−Fe3+)=−4460 cal; α14−α24=22500 cal; α14(Fe2+−Na+)=4600 cal; α24(Fe3+−Na+)=−17900 cal; α34(Si4+−Na+)=−26600 cal. The fitted coefficient expressions in Eqs. (15)–(18) are likewise context only.
- **Na2O activity upper bound:** On PDF page 7 (published page 1771), §4.4.4 states `a_Na2O(l) ≤ 10^-5.5` across the displayed composition range in Fig. 11. The figure caption identifies FetO–SiO2–Na2O slags in equilibrium with liquid iron at 1400°C; the dotted line marks where the regular-solution model applies. Section 4.4 states that component activities use the stable pure substance at the considered temperature as standard state, and the bound is specifically printed as `a_Na2O(l)`.
- The bound is **model-derived**, not measured or quoted from a reference: the paper says the 1400°C activities were calculated from regular-solution relations, Eqs. (24), (26) and (33), and plotted as iso-activity contours in Fig. 11. Section 4.4.3 explicitly attributes the selected Na2O·2SiO2(l) formation free-energy change used for the activity conversion to reference [36], D. N. Rego, G. K. Sigworth and W. O. Philbrook, *Proc. of Japan-United States Seminer*, Kyoto (1983), p. 87 [ISIJ]. This is a thermochemical input to the conversion, not a citation from which the activity bound is quoted.
- No discrete composition coordinates or per-composition activity values for this bound are printed; the prose gives it for the displayed Fig. 11 composition range. The extract preserves the exact printed exponent and inequality in `activity_upper_bound_as_printed` and `inequality_as_printed`; the typed numeric bound is an approximate decimal representation for migration, not a point value. No contour points are digitised.
- The paper's other activity and redox contours in Figs. 1–11 remain figure-read candidates and are not digitised.

## Tool compare

The committed `banya-hino-takezoe-1985-na-fe-silicate-redox-mineru-compare.csv` records the row/column-aware extract-to-draft compare. It reports 45 mismatches because `tools/mineru_table_draft.py::_phase_matches_column` only accepts `gas`, `liquid` and `solid`, while these observations correctly carry `glass` and `l`; each value and its exact row/column was instead confirmed against the page 2 render, as recorded in the cell ledger. The focused test independently pins every Table 1 and Table 2 row and the printed row counts.
