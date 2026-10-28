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
| 4 / 2 | S50-15, 1200 °C | 72.9; 26.5; 0.6; 51.01/11.53; 40.37/48.50; 8.62/39.97 |
| 4 / 2 | S55-15, 1200 °C | 65.1; 33.5; 1.3; 62.01/2.33; 33.86/47.84; 4.12/49.84 |
| 4 / 2 | S62-15, 1200 °C | 62.7; 37.3; 0.0; 71.12/1.43; 26.53/48.96; 2.35/49.61 |
| 4 / 2 | S70-15, 1200 °C | 54.6; 38.8; 6.6; 0.0; 78.49/3.76; 19.45/46.95; 2.06/49.29 |
| 4 / 2 | S50-15, 1000 °C | 61.7; 36.3; 2.0; 58.61/3.01; 37.99/49.46; 3.40/47.53 |
| 4 / 2 | S55-15, 1000 °C | 59.6; 38.3; 2.1; 66.18/0.62; 32.09/48.67; 1.74/50.71 |
| 4 / 2 | S62-15, 1000 °C | 59.2; 40.8; 0.0; 73.64/2.28; 25.21/49.69; 1.15/48.03 |
| 4 / 2 | S70-15, 1000 °C | 38.4; 41.9; 19.7; 0.0; 76.66/1.39; 22.23/50.67; 1.11/47.94 |
| 6 / 4 | Mo2O6-Na2MoO4 | k=0 −161,000 + 72 T; k=1 10,000; k=2 0 |
| 6 / 4 | Si2O4-Mo2O6 | 70,000; 0; 0 |
| 6 / 4 | Si2O4-Na2MoO4 | 55,000; 0; 0 |
| 6 / 4 | Na2Si2O5-Na2MoO4 | 44,000; −5000; 0 |
| 6 / 4 | Na4Si2O6-Na2MoO4 | 12,000; 0; 10,000 |
| 6 / 4 | Na8Si2O8-Na2MoO4 | 0; 0; 0 |

For Table 2, the slash separates the source's paired SL and ML values. The
first four numeric cells in each row after temperature are SL, ML, Qtz, and
Na2O loss mass%; remaining values are the three oxide composition pairs. The
S50-15, 1000 °C Qtz cell is blank in print; the extract preserves that blank
as an empty cell. Numeric zeroes are kept as printed.

## Classification and limitations

- Table 2 values are the authors' direct EPMA/XRF phase analyses. The source
  says water quenching caused Mo-rich particles in whitish glass to precipitate
  during cooling (PDF p. 4), so quench state is recorded as
  `asserted_altered`. SL glass analyses and ML precipitate analyses remain as
  the paper reports them; no in-situ liquid state is inferred.
- Table 2 composition values use mol%. The closed phase-composition profile
  currently supports wt%, mole fraction, or ppm-mass, so these values remain
  context rather than being emitted as scoreable observations. In addition,
  the quenched ML precipitate has no closed `solid` phase role.
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
- Isolated migration: 0 scored observations, 0 hard issues, 1 unavailable
  document record (`extract yielded no observations`), and 4 context rows. The
  unavailable record reflects the explicit schema gaps above; the measured
  cells remain present in context.
- MinerU row-aware compare output: 0 matched, 0 no-match, 0 mismatch, 0
  presence-only, 0 locator-error, 0 unverifiable, 0 total observation cells.
  The comparator only sees observations; all source table rows in this extract
  are deliberately context, so this is an empty comparison rather than a
  fidelity claim.

## Verification

- Focused extraction test and YAML flow-scalar comma guard: 18 passed.
- Mutation check: changing the first rendered Table 1 SiO2 value from `43.50`
  to `43.51` made the focused render assertion fail; the committed extract was
  then restored and the focused test and comma guard passed again.
- The combined literature, comma-guard, and focused run completed 655 passed
  and 3 failed. The failures were the repo-wide validator, coverage-pending,
  and merge-smoke tests; each exceeded the existing 300-second per-test
  timeout while loading the full corpus through PyYAML. The focused
  `test_repo_extracts_validate_green` check passed separately with the focused
  extraction test in 157 seconds before this resource-contended run.
- No battery-store regeneration was performed. Expected store delta: zero
  observations; the migration carries four context rows and queues the source
  document as unavailable because the current schema has no matching types.
