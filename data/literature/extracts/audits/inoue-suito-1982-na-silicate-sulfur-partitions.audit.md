# Inoue and Suito (1982): cell and scope audit

## Printed row counts and reads

- Table 1: 20 rows, PDF p. 5 (initial assays, metal assays, total sulfur, FeS) and p. 6 (remaining final slag assays and sodium-loss rate). Page 5 was machine-read from the studio MinerU OCR redo, checked cell by cell against the high-resolution render, and reconciled with pdftotext. Page 6 was checked against MinerU, pdftotext, and the render.
- Table 2: 7 rows, p. 6. Table 3: 6 rows, p. 7. Table 4: 4 rows, p. 8. Each table's row/column assignment was checked against its render. The CSV lists the transcribed numeric cells and the confirmation source.
- The initial Table 1 and Table 3 charge assays are retained row-by-row as table context, distinct from final equilibrium assays; every numeric cell in those initial columns is also listed in the CSV audit. Table 1 samples 151 and 152 have final metal S .0194 and .0113 wt%; their initial metal S remains .003 wt%. All 75 incorrect Table 1 composition locators and 19 Table 2 linked composition locators now point to the p. 6 final-assay row. Printed dash components remain absent.
- Trace marks in Table 2 (samples 100 and 101) remain `tr.`; they are not zero. Dash cells remain absent.
- Figures 1–11 are caption context only; the located caption inventory is in the mapping YAML. No plotted values were digitized.

## Additional printed context from tables and prose

Table 1 initial-charge columns (metal S, metal C, initial slag Na2O, SiO2, Na2S; wt%):

| Sample | S | C | Na2O | SiO2 | Na2S |
|---|---:|---:|---:|---:|---:|
| 15 | .290 | 4.45 | 60.0 | 40.02 | — |
| 16 | .003 | 4.47 | 54.6 | 43.18 | 2.18 |
| 20 | .294 | 4.45 | 50.4 | 49.60 | — |
| 19 | .003 | 4.47 | 48.9 | 49.20 | 1.86 |
| 24 | .574 | 4.43 | 40.7 | 59.27 | — |
| 22 | .010 | 4.47 | 39.2 | 59.03 | 1.76 |
| 29 | .994 | 4.31 | 26.1 | 73.89 | — |
| 28 | .003 | 4.47 | 27.3 | 70.69 | 2.04 |
| 98 | .994 | — | 26.1 | 73.89 | — |
| 99 | .574 | — | 41.2 | 58.79 | — |
| 100 | .574 | — | 50.8 | 49.23 | — |
| 101 | .994 | — | 60.6 | 39.36 | — |
| 151 | .003 | — | 27.3 | 70.69 | 2.04 |
| 152 | .003 | — | 39.2 | 59.03 | 1.76 |
| 105 | .574 | — | 26.1 | 73.89 | — |
| 106 | .574 | — | 41.2 | 58.79 | — |
| 107 | .574 | — | 50.8 | 49.23 | — |
| 108 | .994 | — | 60.6 | 39.36 | — |
| 153 | .003 | — | 27.3 | 70.69 | 2.04 |
| 154 | .003 | — | 39.2 | 59.03 | 1.76 |

Table 3 initial slag (metal S, Na2O, SiO2, CaO; wt%): samples 102 (.574, 42.2, 49.51, 8.33), 103 (.574, 34.7, 51.25, 14.1), 104 (.574, 28.4, 56.33, 15.3), 155 (.994, 53.0, 40.38, 6.58), 156 (.994, 47.5, 40.51, 12.0), 157 (.994, 41.9, 40.26, 17.9).

Table 1 normalized sodium-loss column, ΔNa/(Na_init·t), row order 15, 16, 20, 19, 24, 22, 29, 28, 98, 99, 100, 101, 151, 152, 105, 106, 107, 108, 153, 154: `2.73×10^-3, 1.18×10^-3, 2.34×10^-3, 1.98×10^-3, .56×10^-3, .54×10^-3, .26×10^-3, .17×10^-3, —, 1.23×10^-3, 1.59×10^-3, 6.01×10^-3, .55×10^-3, .18×10^-3, 1.63×10^-3, 2.73×10^-3, 5.11×10^-3, 11.7×10^-3, .99×10^-3, .58×10^-3`. The first numeric cell prints ×10^-3 as the column multiplier; every row inherits it. `value` stores the scaled rate while `value_as_published` and the CSV retain each printed cell. Table 3 rates are `1.52×10^-3, —, —, 4.28×10^-3, 2.46×10^-3, 2.38×10^-3` for rows 102, 103, 104, 155, 156, 157. These normalized sodium rates have no exact quantity token: checked `simulator/battery/enums.py::Quantity`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, and `tools/validate_literature_extracts.py::OBSERVATION_TYPES`. The descriptive quantity labels keep these rows typed-refused instead of allowing `TYPE_QUANTITY` to infer an unrelated rate token.

Table 1 final slag total sulfur is now recorded for the twelve graphite rows; the seven Table 2 Na2O/SiO2 ratios are stored on linked rows with page-6/table-2 cell locators. The p. 3 SiO2(l) activities quoted from Callow are .13/.6 at xSiO2=.5/.67, and those quoted from Charles are .009/.38. Other printed prose values: log fS at 1250 and 1350°C is .74 and .75 (author calculation; PDF p. 5 render checked; typed refusal because the printed logarithmic sulfur activity coefficient has no exact token; checked `simulator/battery/enums.py::Quantity.ACTIVITY_COEFFICIENT` and `simulator/battery/migrate.py::QUANTITY_ALIASES`). These rows have no printed slag composition. The abstract reports this work's sulfide-capacity range as 10^-2 to 10^-4 for Na2O/SiO2 molar ratios 3/2, 1/1, 2/3, and 1/3 at 1250 and 1350°C; these are range context, not per-row values. Pure-liquid log C_s is 3.44 for Na2O(l) at 1250°C and .23 for CaO(l) at 1500°C. These are Inoue and Suito's calculations from the printed thermodynamic reactions. Abstract prior-work capacities at 1250°C are log C_s=-4 at Na2O/SiO2=.5 for Nagashima and Katsura and Denier, and -3.0 and -2.0 at ratio 1.0 for those respective sources. Each of the six capacity observations carries the capacity-specific `author_derived` class and its printed equation; cited prior-work attributions are retained in the attribution field.

Sodium vapor experiment: graphite bell/crucible, 10 g (2NaCl+CaC2) immersed in 1 kg C-saturated iron (0.1% P, 0.05% S) at 1300°C. Eq. (20) on PDF p. 8 gives calculated pNa=1.32 atm at 1300°C and 2.65 atm at 1400°C; these outputs are not total-pressure conditions. On PDF p. 3, the paper also gives Δ log P_Na=1.44 and 1.57 at 1250 and 1350°C for the C/CO versus Fe/FeO equilibrium comparison. Those differences have no exact quantity token, so they remain typed refusals with quantity-unknown IDs `inoue_suito_1982_delta_log_pNa_1250C` and `inoue_suito_1982_delta_log_pNa_1350C`. The code candidates checked were `simulator/battery/enums.py::Quantity`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `simulator/battery/migrate.py::TYPE_QUANTITY`; `p_partial` is not the printed log-pressure difference. PDF p. 5 states sodium evaporation loss was about 5% at maximum during Table 3 experiments; the row and denominator are not stated, so the value is retained as located context, not assigned to a row or converted to a fraction.

The quoted activity provenance is pinned row by row in the mapping YAML and CSV. On PDF p. 2, Sanders and Haller report Na2O activities 6.56×10^-8 at 1 250 °C and 1.98×10^-7 at 1 350 °C relative to the liquid standard state for Na2O·2SiO2 glass melt. Page 3 identifies these absolute activities at the Na2O·2SiO2 composition. Their full reference-list entry is reference 22. On PDF p. 3, the four SiO2(l) activities are .13/.6 from Callow and .009/.38 from Charles at the printed xSiO2 values 0.5/0.67 and 1 250 °C; the source does not state their standard state. The corresponding Na2O estimates (1.1×10^-7/6.6×10^-9 from Callow and 1.6×10^-6/1.7×10^-8 from Charles) are Inoue and Suito calculations from those cited silica activities and thermodynamic data, not direct Na2O measurements by the cited authors; they use the printed xSiO2 values and 1 250 °C, and their standard state is not printed. The extract stores printed compositions verbatim, without complementary mole fractions, and records the complete reference-list entries 22, 29, and 30.

Both pages were checked against their renders because the MinerU machine text did not cover PDF pp. 1–5. Table 4's printed Na2O/SiO2 molar ratios (.3, .6, .9, 1.3) are preserved as printed in every gamma_FeO/N_FeO row; the migrator's existing `simulator/battery/migrate.py::_mole_fraction_composition_from_values` path normalizes those binary ratios for identity composition. The paper prints aFeO=1.30×10^-3 at 1250°C for PCO=1 atm, aC=1, and aFe(l)=.82; this is a calculation input, not total experimental pressure. Yamamoto's prior-work pO2 comparison is retained here as context: 2.8×10^-13–2.4×10^-14 atm at 1350°C and Na2O/SiO2=1.5, versus 4.5×10^-17 atm at PCO=1 atm.

Table 2's 14 Charles/Callow calculated silicon cells use the printed C(aC=1)-CO (pCO=1 atm) condition in Eq. (14), PDF p. 5. The four Table 4 N_FeO cells use pCO=1 atm, aC=1, and aFe(l)=0.82 in Eq. (17), PDF p. 7. Each calculation condition is attached to its rows and audited cell-by-cell in the CSV. These printed calculation inputs do not state the total experimental pressure.

## Evidence and limits

Measured final Table 1/3 compositions and sulfur assays are `measured_direct`; Table 2 observed Si is `measured_direct`; the Charles and Callow comparisons are `quoted_attributed`; Table 4 gamma is attributed to reference 45 and N_FeO is kept as the authors’ quoted calculation because the activity inputs are not all stored. FeS assays are reported with the authors’ assumption that total iron is FeS. The page-2 methods paragraph specifies Ar at 200 ml/min for the stated Na2O/SiO2 ratios 1 and 1.5, and CO at 100 ml/min otherwise; observed-row condition locators point there. Table 2 measured-Si rows use the linked Table 1 sample's temperature and the same gas condition. These flow rates are not pressures. The calculated Table 2 and Table 4 comparison rows instead carry their printed C/CO calculation atmosphere from Eq. (14) and Eq. (17). No absolute experimental fO2, total experimental pressure, sodium-in-metal result, or quench procedure is stated.

## Table-tool compare reconciliation

The compare output has 1,178 statuses: 172 matched, 62 no-match, 1 mismatch, 335 locator-error, and 608 unverifiable. The no-match cells on PDF p. 5 use the studio OCR redo and are absent from the original draft index; page-6 Table 1 is a continuation whose caption is on p. 5; the prose activity and calculation-condition cells on PDF pp. 2–5 are outside the table blocks. The single mismatch is Table 3 sample 102's normalized sodium-loss cell: the source prints 1.52×10^-3, while the compare parser does not retain the exponent; the cell was checked against the p. 7 render. Every p. 5 OCR-redo table cell and each remaining compare mismatch or unmatched printed value was checked against its source paragraph or page render. `locator-error` entries are table-caption evidence limitations for the continuation and nested condition locators; they do not represent unresolved numeric disagreements.

The Table 1/3 FeS and Na2S values are `quoted_attributed`: the authors assume total iron is FeS, and the paper does not separately state a reduction method for Na2S. The total sulfur, metal sulfur/carbon, and oxide assays remain `measured_direct`. Table 2's observed silicon is measured directly; Charles/Callow comparison values and the prose model outputs are attributed because their inputs are not all stored.
