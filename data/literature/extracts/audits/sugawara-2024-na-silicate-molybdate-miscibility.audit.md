# Sugawara et al. phase-equilibrium extraction audit

## Scope and row reconciliation

Table 1 has 11 printed experimental-composition rows. Table 2 has 8 printed
sample-temperature rows, each with phase proportions and paired SL/ML mol%
composition values for SiO2, Na2O, and MoO3. Table 4 has 6 printed
CALPHAD interaction-parameter rows. All three tables are retained in the
extract context. Table 3 is the cited thermodynamic database input, not a
measured phase-equilibrium result or fitted interaction parameter, so it is
outside the requested extraction. Figure 4 is a figure-read candidate and was
not digitized.

## Full-cell audit

Machine source order was MinerU table blocks/markdown, then pdftotext. The
MinerU table QA found Table 1 with 33 numeric cells and 100% digit agreement;
Table 2 with the 8-row layout and composition pairs in shared cells; and
Table 4 flagged comma-grouped coefficients plus one numeric mismatch. The
rendered page was used to verify each table's row and column layout and settle
all flagged Table 4 coefficient cells. All numeric cells stored in context
are transcribed below. Table 2's composition pairs are ordered SL then ML in
each oxide column as confirmed by the rendered table headings and cell
alignment.

| PDF page / table | Printed row | Numeric cells confirmed from machine text; row/column assignment checked against render |
| --- | --- | --- |
| 2 / 1 | S50-13 | SiO2 43.50; Na2O 43.50; MoO3 13.00 |
| 2 / 1 | S35-15 | 29.75; 55.25; 15.00 |
| 2 / 1 | S40-15 | 34.00; 51.00; 15.00 |
| 2 / 1 | S45-15 | 38.25; 46.75; 15.00 |
| 2 / 1 | S50-15 | 42.50; 42.50; 15.00 |
| 2 / 1 | S55-15 | 46.75; 38.25; 15.00 |
| 2 / 1 | S62-15 | 52.70; 32.30; 15.00 |
| 2 / 1 | S70-15 | 59.50; 25.50; 15.00 |
| 2 / 1 | S30-25 | 22.50; 52.50; 25.00 |
| 2 / 1 | S40-25 | 30.00; 45.00; 25.00 |
| 2 / 1 | S50-25 | 37.50; 37.50; 25.00 |
| 4 / 2 | S50-15, 1200 °C | SL 72.9; ML 26.5; Qtz blank; Na2O loss 0.6; SiO2 51.01/11.53; Na2O 40.37/48.50; MoO3 8.62/39.97 |
| 4 / 2 | S55-15, 1200 °C | SL 65.1; ML 33.5; Qtz blank; Na2O loss 1.3; SiO2 62.01/2.33; Na2O 33.86/47.84; MoO3 4.12/49.84 |
| 4 / 2 | S62-15, 1200 °C | SL 62.7; ML 37.3; Qtz blank; Na2O loss 0.0; SiO2 71.12/1.43; Na2O 26.53/48.96; MoO3 2.35/49.61 |
| 4 / 2 | S70-15, 1200 °C | SL 54.6; ML 38.8; Qtz 6.6; Na2O loss 0.0; SiO2 78.49/3.76; Na2O 19.45/46.95; MoO3 2.06/49.29 |
| 4 / 2 | S50-15, 1000 °C | SL 61.7; ML 36.3; Qtz blank; Na2O loss 2.0; SiO2 58.61/3.01; Na2O 37.99/49.46; MoO3 3.40/47.53 |
| 4 / 2 | S55-15, 1000 °C | SL 59.6; ML 38.3; Qtz blank; Na2O loss 2.1; SiO2 66.18/0.62; Na2O 32.09/48.67; MoO3 1.74/50.71 |
| 4 / 2 | S62-15, 1000 °C | SL 59.2; ML 40.8; Qtz blank; Na2O loss 0.0; SiO2 73.64/2.28; Na2O 25.21/49.69; MoO3 1.15/48.03 |
| 4 / 2 | S70-15, 1000 °C | SL 38.4; ML 41.9; Qtz 19.7; Na2O loss 0.0; SiO2 76.66/1.39; Na2O 22.23/50.67; MoO3 1.11/47.94 |
| 6 / 4 | Mo2O6-Na2MoO4 | k=0 −161,000 + 72 T; k=1 10,000; k=2 0 |
| 6 / 4 | Si2O4-Mo2O6 | 70,000; 0; 0 |
| 6 / 4 | Si2O4-Na2MoO4 | 55,000; 0; 0 |
| 6 / 4 | Na2Si2O5-Na2MoO4 | 44,000; −5000; 0 |
| 6 / 4 | Na4Si2O6-Na2MoO4 | 12,000; 0; 10,000 |
| 6 / 4 | Na8Si2O8-Na2MoO4 | 0; 0; 0 |

For Table 2, the slash separates the source's paired SL and ML mol%
compositions. The printed Qtz mass% cell is blank on six rows and is shown as
`blank` above; only S70-15 has printed Qtz values (6.6 at 1200 °C and 19.7 at
1000 °C). Na2O loss is a separate mass% column on every row; its values must
not be read as Qtz. The context transcription preserves all six blank Qtz
cells as empty strings.

## Classification and limitations

- Table 2's 48 direct EPMA/XRF mol% cells are emitted as
  `equilibrium_phase_composition`, with each printed value divided by 100 into
  `component_amount` and retained as printed in
  `component_amount_as_published`. The basis is `mole_fraction`; SL is typed
  as `glass` and ML as `melt`, following the table title and Fig. 4 tie-line
  labels. Each cell has a page 4 / Table 2 locator with sample, temperature,
  oxide, and phase. The separate statement about Mo-rich microspheres in
  whitish glass concerns cooling precipitates and is not assigned as
  `asserted_altered` to the macroscopic tie-line observations.
- Table 2 SL mass%, ML mass%, Qtz mass%, and Na2O-loss mass% remain in context.
  The phase proportions and mass-balance loss have no matching observation
  quantity in the current schema.
- Table 2 phase proportions and Na2O-loss values are calculated by mass
  balance from starting composition and analytical results (PDF p. 3), but
  there is no fitting observation type for phase proportion or Na2O loss.
- Table 4 gives CALPHAD Redlich-Kister liquid interaction coefficients. The
  current `interaction_parameter` profile represents Wagner parameters. Table
  4 therefore remains context with `method_class: model_derived`; its fit used
  the undigitized Fig. 4 phase relationship, so no model-derived observation
  with incomplete lineage is emitted.
- The paper reports experiments at 1200 °C and 1000 °C, 24 h holds, and water
  quenching (PDF p. 2). It does not state total pressure or fO2. No pressure,
  fO2, ambient condition, or other unstated condition was added.
- Isolated migration emits 48 phase-composition observations, zero hard
  issues, and zero unavailable values. Table 1, the Table 2 mass columns, Table
  4, and experiment conditions remain as context; no unavailable-document
  record is expected.
- The MinerU row-aware comparator reports no cells for this extract because it
  compares numeric `values.points`; these phase-composition observations store
  one component per observation in `values.component_amount`. The result is not
  a fidelity claim; all 48 printed cells are pinned in the focused test.

## Verification

- The review-directed isolated migration emits 48 observations with 0 hard
  issues. The focused test pins every cell and verifies each migrated quantity,
  phase role, and mole-fraction value; its S50-15 / 1200 °C / SL / SiO2 point is
  0.5101 with the printed `51.01 mol%` retained, while fO2 and total pressure
  are unknown. No `asserted_altered` state is attached to the tie lines.
- Focused extraction test and YAML flow-scalar comma guard: 18 passed locally
  in the foreground after the remote request remained silent beyond the
  10-minute queue window.
- Mutation check: changing the first Table 2 printed value from `51.01 mol%`
  to `51.02 mol%` made the all-cell assertion fail on that cell. The extract
  was restored from its pre-mutation copy.
- Repo-wide literature suite: remote gate requests for the focused tests and
  repo-wide suite both ended with `queue-deadline-exceeded` before execution.
  A prior local run passed 636 tests and failed four: a fidelity-sample failure
  corrected by the six Table 2 fidelity samples above, plus configured load
  timeouts in the validator, coverage, and pilot-merge checks. The suite was
  not rerun locally because the dispatch directs local fallback to the focused
  file only.

No battery-store regeneration was performed. Expected store delta: 48
phase-composition observations with fO2 and total pressure unknown. The extract
commit does not modify the generated store.
