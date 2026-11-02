# Eliezer and Howald (1978) extraction audit

## Scope and migration

The source was transcribed first from the MinerU content list/markdown and the
born-digital `pdftotext-layout.txt`. The PDF was used only for the flagged
Table II row label and to check row/column placement on printed Tables I–IV.
No plotted values were digitized. The extract contains 17 observations: 6
numeric and 11 unavailable; the isolated migrator reports 0 hard issues.

The six numeric migrated observations are one tridymite KO0.5 activity, two
quoted SiO2 activities, two quoted log10 SiO2 activity coefficients, and one
author-derived log10 SiO2 activity coefficient. The printed log values remain
as printed in the extract; the focused test pins the migrator outputs.

The isolated migration prints the required identity axes for three scored
rows: at 1743 K the tridymite activity has KO0.5=0.0234 and SiO2=0.9766; at
1784 K both the phase-diagram log10 coefficient and SiO2 activity have
KO0.5=0.102 and SiO2=0.898. Their identity temperatures and compositions are
values. Total pressure and absolute fO2 remain unknown because the source
prints neither. All six numeric observations are tested for these same
temperature/composition bindings and unknown pressure/fO2 states.

Five partial-molar-enthalpy values (four Table I cells and 860 ± 700 cal mol-1)
remain unavailable because `simulator/battery/migrate.py::QUANTITY_SOURCE_FIELDS`
has no value-field selector for
`simulator/battery/enums.py::Quantity.PARTIAL_MOLAR_ENTHALPY`. Four Table I
values are printed in kcal mol-1; 860 ± 700 is printed in cal mol-1. The
separate -15.0 value is excess enthalpy of mixing, not partial molar enthalpy;
it remains unavailable under its descriptive non-token quantity. Its exact
schema gap is recorded in the extract. No kJ/mol twins were added. The
quantity-unknown set is exactly the five printed linear equilibrium-constant
rows plus this excess-enthalpy row; no phase is unknown.

No total pressure or absolute oxygen fugacity is printed for these
observations. They remain unknown. The argon atmosphere is recorded as
printed; it is not converted into a pressure condition. The paper reports no
numeric pressure, partial pressure, ion intensity, flow rate, or vaporization
rate. Figure 1 gives potassium vapor pressure only as an undigitized plot, with
no pressure unit on its axis. The atomic-absorption calibration relation and
the three printed gas-phase reactions are retained in method context; KOH(g)
is identified as the authors' interpretation of Preston and Turner's data,
not a measured species from this work.

## Full-cell audit

On each table page, MinerU and pdftotext agree on numeric digits, signs, and
exponents. The row and column assignments below were checked against the
rendered page. Coordinates use the printed table label and the CSV row/column
coordinates in the committed mapping.

| Printed table | Printed rows and numeric cells | Independent-read / render result |
|---|---|---|
| I, PDF p. 3 / journal p. 1023 | 2 data rows, 8 cells. Row 2: c1 `0.21537`, c2 `-82.7`, c3 `0.33497`, c4 `-59.4`. Row 3: c1 `0.28548`, c2 `-63.2`, c3 `0.34025`, c4 `-63.0`. | MinerU and pdftotext agree on every cell. Render confirms the two repeated X / partial-molal-enthalpy column pairs and all row assignments. The four enthalpy cells are stored as observations; the X values and units are preserved with them. |
| II, PDF p. 3 / journal p. 1023 | 7 data rows, 21 coefficient cells (columns A(H), B(H), A + 0.5519B): this work `-132, -9, -137`; Plante `-116, -103, -173`; rate-of-vaporization coefficients `-97, -24, -110`; joint `-118, -5, -121`; glasses `-75, -61, -109`; corrected `-71, -43, -95`; selected `-86, -53, -115`. | Numeric coefficient cells agree. QA's single digit mismatch is the Table II row-label text `Corrected to 1500 K^{6,11}`: the superscript references `6,11` are tokenized as `611`, not a coefficient. The page render confirms the comma and both footnote references. All source labels, including `^{12}`, `^{13}`, `^{6-8}`, and `^{6,11}`, are retained in `row_labels_as_printed`. This table reports enthalpy coefficients; the `Rate of vaporization` label is not a numeric rate. |
| III, PDF p. 3 / journal p. 1023 | 8 material rows, 47 numeric cells across H°298.15, H°1000, Φ°1000 and the four printed Cp coefficient columns. All 8 rows and blank cells are transcribed in `table3_standard_state_context`; blank cells remain null, never zero. | MinerU/pdftotext numeric agreement is 1.000. The table layout and material/column assignments were checked against the page render. QA's two LaTeX flags concern header markup only. |
| IV, PDF p. 5 / journal p. 1025 | 5 coefficient rows, 16 numeric cells: liquid log γ `-10.9923, -6.7801, -1.0101, -11.3290, -6.7801, -0.6734`; liquid H `-86000, -53000, -86000, -53000`; liquid Cp `8, 8`; tridymite log γ `-4.8045, -4.8045`; tridymite H `-35842, -35842`. | MinerU and pdftotext agree on all 16 cells. Render confirms the Liquid and Tridymite section labels, the Redlich–Kister / Bale–Pelton columns, and row placement. |

The printed prose cells are read from both machine-text sources and checked
against their source paragraph locators: page 3 gives (1784 K, X=0.102,
activity 0.94293, log10 γ=0.0212), (1150 K, X=0.297, activity 0.78139,
log10 γ=0.0459), the Gibbs–Duhem result log10 γ=-0.012 at 1784 K and X=0.293,
and 860 ± 700 cal mol-1 at X=0.297 with no single temperature printed. Page 5
gives the cristobalite–tridymite constants 1.00455 at 1175 K, 0.99658 at 1175
K, `<0.99526` at 1623 K, `0.993` at 1623 K, and a possible value as small as
`0.98` with no specific temperature. The 0.7157 × 10^-6 activity at X=0.0234
and 1743 K is also prose on page 5. The Holmquist sodium comparison is retained
as quoted context: 0.00484 mole fraction NaO0.5 with quartz at 1623 K for
weeks; no Na/K species identity is inferred.

The `± 700 cal mol-1` uncertainty on the Gibbs–Duhem Hbar2 result was checked
against the page 3 render. Its width is stored as printed; the source states no
confidence level.

## Table-tool reconciliation

The committed compare output has 12 `mismatch` and 5 `unverifiable` rows. The
five equation (10) constants are correctly `unverifiable`: they are prose
values, not cells in a MinerU table. Eight other mismatch rows are prose
values (the tridymite activity, five page-3 activity/log-activity values, and
the -15.0 excess-enthalpy and 860 ± 700 partial-molar-enthalpy values); the
page-only table fallback compares them with unrelated tabular cells. The
remaining four mismatches are the Table I
enthalpy values: their page/table/row/column coordinates select the printed
cells, but `tools/mineru_table_draft.py::_phase_matches_column` rejects the
required migrator phase token `l` because that helper accepts only `gas`,
`liquid`, or `solid`. Independent machine reads and the page render confirm all
four digits and assignments; the phase remains the valid migrator token `l`.
No numeric disagreement remains unresolved.

The figure-only list includes the pressure curve in Figure 1, log γ1 points in
Figure 3, and phase-diagram points in Figure 4. Figure 3's printed marker
legend is retained as context and as a fidelity sample; no point is assigned
by color or plotting order.

## Validation

- Isolated migration: 17 observations, 6 numeric, 11 unavailable, 0 hard
  issues; no phase-unknown observations.
- Quantity-unknown observations: exactly the five linear-K rows and the one
  excess-enthalpy row, each retained as printed and refused rather than
  inferred as log K, Gibbs energy, or partial molar enthalpy.
- The focused test binds all six migrated numeric values and their printed
  temperature/composition conditions, pins the unavailable conditions,
  checks the 17-row census and source-cell render samples, and proves an
  interior activity mutation fails.
- `tests/test_yaml_flow_scalar_commas.py`, the j01 census test, and
  `tests/test_literature_extracts.py` are required final checks.
- Expected observations-v2 delta after controller regeneration: 17 rows for
  this source, of which 6 carry numeric values and 11 are unavailable; no
  committed store regeneration was made.
