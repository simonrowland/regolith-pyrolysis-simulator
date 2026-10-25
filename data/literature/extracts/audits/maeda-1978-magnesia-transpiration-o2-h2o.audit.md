# Maeda et al. (1978) extraction audit

## Scope and row reconciliation

- Table 2 (PDF page 6; published page 496) has 24 run rows. All 24 Mg(OH)2 partial pressures are extracted, with temperature, run time, flow, water-vapor pressure, and collected Mg mass. The printed `kp` column is excluded because the task asks for vapor pressures and run conditions.
- Table 4 (PDF page 8; published page 498) has 10 run rows. All 10 MgO partial pressures are extracted with temperature, time, flow, and collected Mg mass. Its third-law columns are excluded. The three `*` rows are runs 3, 4, and 6; the footnote identifies them as O2+CO2 carrier gas.
- The audit CSV lists every numeric cell stored from these two tables: 168 cells from Table 2 and 60 from Table 4. The first column was checked as the run identifier; every other listed cell was assigned to its printed column and run against the page render.
- Table 1 is sample-purity context (99.9% MgO plus an elemental impurity assay), not a run table. It does not establish a closed run composition, so composition remains unknown. Table 3 is a thermochemical treatment of Table 2, not a second set of runs. Figures 3–8 are not digitized; Figures 5 and 8 are figure-read candidates.

## Cell verification

`mineru_table_qa` reported low numeric agreement for the rendered tables: Table 2 = 0.568 with 10 fused-reference flags and 166 numeric mismatches; Table 4 = 0.426 with 1 fused-reference flag and 68 numeric mismatches. Because these tables contain the printed power-of-ten cells and the QA did not establish cell-level agreement, all stored numeric cells in both tables were checked against the rendered pages. The table row and column assignments were checked against the same renders.

The table-tool compare output reports 34 pressure-value mismatches and no matched cells. These are the 24 Table 2 and 10 Table 4 pressures written with `×10` superscript exponents; the draft candidate parser did not produce matching numeric candidates for those cells. Each of the 34 was read from the page render and is listed in the mapping's `render_verified` cells. There are no unresolved numeric mismatches. `*.compare.txt` preserves the full compare output; `*.audit.csv` records the cell-by-cell render checks.

The focused rendered values are Table 2 run 1: T=1948 K, p(H2O)=8.26×10^-2 atm, collected Mg=1.79×10^-5 g, p=2.58×10^-6 atm; Table 2 run 20: T=1725 K, p(H2O)=1.85×10^-1 atm, collected Mg=2.29×10^-5 g, p=4.81×10^-6 atm; Table 4 run 1: p=3.33×10^-7 atm; and Table 4 run 3*: p=4.62×10^-8 atm.

## Conditions and lineage

- The paper defines `P` as total reaction-system pressure and states that it is 1 atm in this experiment (PDF page 4; published page 494, equation 1). Table 4's caption also states oxygen at 1.0 atm (PDF page 8; published page 498). The extract stores those printed source forms and lets the migrator convert them for identity.
- Table 2 prints O2-H2O carrier gas and per-run p(H2O), but no per-run O2:H2O ratio or pO2. No O2 balance is calculated; fO2 stays unknown.
- Table 4's unstarred runs use O2 carrier gas at the printed 1.0 atm; runs 3, 4, and 6 are O2+CO2. The starred mixture ratio and its oxygen partial pressure are not printed, so those rows leave fO2 unknown.
- Each derived pressure row links to its same-run typed-refusal condensate-input record. The collected absolute Mg mass has no quantity token and is not a mass-loss fraction; the refused record retains the printed mass, flow, and duration. The pressure relation uses these stored inputs. No unprinted temperature, pressure, gas ratio, or composition was supplied.

## Migration result

The isolated real migrator produced 68 observations: 34 numeric partial-pressure observations and 34 unavailable typed-refusal mass-input observations; 0 hard validation issues. The 34 typed refusals each produce three queue entries for their unsupported quantity, unmapped condensate phase label, and unavailable typed value. The pressure observations carry temperature, total pressure, and composition on identity. Table 2 fO2 is unknown; Table 4 fO2 is 1 atm on pure-O2 runs and unknown on the three O2+CO2 runs.
