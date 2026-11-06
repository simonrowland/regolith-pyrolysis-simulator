# Yamaguchi, Kaneko and Iguchi (1987) extraction audit

Source: *Transactions of the Japan Institute of Metals* 28(12), 986–993,
doi: 10.2320/matertrans1960.28.986. Machine-text order was MinerU markdown and
table blocks, then `pdftotext-layout.txt`. The MinerU draft is retained only in
worker scratch; the committed mapping and cell audit use printed table numbers
and absolute PDF page numbers.

## Scope and row reconciliation

| Source location | Printed rows | Extract treatment |
|---|---:|---|
| Table 1, p. 988 / PDF p. 3 | 5 cell identities: 0, 6, 9, 10, 11 | All five bulk formulas and phase assemblages retained in context. Cells 6/9 are repeat cells; 10/11 are thermal-EMF controls, not independent activity rows. |
| Table 2, p. 989 / PDF p. 4 | 8 fit rows: 3 for cell 0 and 5 for cells 6/9 | All 8 piecewise EMF fits, including A, dA, B and dB, retained as measured-reduced context. The raw points are plotted and not tabulated. |
| Table 3, p. 991 / PDF p. 6 | 4 log-activity fit rows | All four coefficient fits are stored as measured-reduced `activity` observations in printed log10 form, typed-refused because no numeric points are printed. |
| Table 4, p. 991 / PDF p. 6 | 4 Gibbs fit rows: one Na3Fe5O9 and three Na2Fe2O4 | Three fits are measured-reduced `delta_fG` observations; the Na3Fe5O9 row is a reaction-(B) energy for two formula units and remains quantity-unknown under a descriptive label. All are typed-refused as coefficient functions. |
| Abstract, p. 986 / PDF p. 1 | 4 coefficient fits | Retained separately from Table 4 so the printed disagreements remain visible. |

The extraction therefore contains 12 migrated fit observations (4 activity,
7 `delta_fG`, and one quantity-unknown reaction-(B) Gibbs fit), plus 8 located
Table 2 EMF fit rows in source context. It does
not create points by evaluating an equation or reading values from a figure.
The cell audit CSV records all 100 numeric cells across the printed fit tables
and abstract (80 coefficients and uncertainty cells plus 20 temperature-range
cells), together with each Table 1 cell identifier and formula cell.

## Cell audit and render checks

The committed `yamaguchi-kaneko-iguchi-1987-na2o-fe2o3-activity.cells.csv`
maps each numeric fit cell to its printed page, table, row and column. The
Table 1 layout and five-row count were checked against the 200 dpi render of
PDF page 3. Table 2's row and column assignments and every coefficient were
checked against the 400 dpi render of PDF page 4. Table 3 and Table 4 row and
column assignments and every coefficient were checked against the 400 dpi
render of PDF page 6. Abstract formula and coefficient assignments were
checked against the 200 dpi render of PDF page 1. The standard-state sentence
and cell equations were checked against page 2; phase transitions against
page 5; attributed comparisons against page 7.

MinerU QA flagged Table 2 (0.259 numeric agreement; merged temperature cells
and numeric mismatches), Table 3 (0.276; LaTeX/numeric mismatches), and Table 4
(0.000; LaTeX/numeric mismatches). The renders resolve those flagged values and
row assignments. Table 2 contains 8 body rows despite a caption naming cell 0;
the last five rows in the printed table are explicitly labelled cells 6 and 9.
The Table 3/4 QA tool compares numeric point cells, so the committed
`yamaguchi-kaneko-iguchi-1987-na2o-fe2o3-activity.compare.csv` has only its
header: 12 observations carry coefficient functions rather than point values.
It reports 0 matched, 0 mismatched and 0 total candidates.

## Printed conflicts retained

- The phase boundary is 1033 K in the body/Figure 3 discussion, 1053 K in
  Tables 2/3, and 1047 K in the abstract/Table 4. These locators are kept
  independently; no boundary is selected as canonical.
- The Na2Fe2O4 midrange intercept is −152.7 ± 2.4 in the abstract and
  −162.7 ± 2.4 in Table 4. The high-range intercept is −147.3 ± 2.9 in the
  abstract and −147.2 ± 2.9 in Table 4.
- The abstract gives Na3Fe5O9 as −(248.6 ± 1.1) − (2.447 ± 1.188)×10⁻³T.
  Table 4 gives the reaction-basis intercept −497.2 ± 2.2 but repeats the
  same slope rather than an obvious factor of two. Both printed bases are
  preserved. The abstract intercept is one formula unit (half the reaction
  intercept), while its slope is unhalved; neither coefficient is adjusted.
- Table 4 labels its first row “Eq. (A)” even though the caption and body
  identify the formation reaction as (B); Eq. (A) is the carbonate reaction
  on p. 987. The row label is retained verbatim.
- Equation (6) prints both `ΔG°(Na2Fe2O4) = 1/5[ΔG°(B) + ΔG°(C)]` and its
  expanded activity expression. The expanded form uses NF/N3F5, while equation
  (5) and the preceding activity definition use N3F5/NF; the printed ratio is
  retained without inversion.
- The Table 4 Eq. (D) phase heading `−NF <1405 K` is stored literally. Its
  `<1405 K` window overlaps the `<1047 K` and `1047<T<1278 K` rows and is not
  the abstract's `1278<T<1405 K` interval.
- One body occurrence reads Na3Fe5O5; it is recorded as a literal typo, not
  treated as another phase.

Table 4's Na2Fe2O4 is represented under canonical species NaFeO2 because its
empirical formula is NaFeO2. All printed equations and coefficient bases keep
Na2Fe2O4 exactly; no reaction or Gibbs value is scaled. The Na3Fe5O9 data stay
under their own species.

## Method, conditions and schema

The printed cell is sample | Na+ beta-alumina | reference. Equation (1) is
`2FE = RT ln(aNa2O/a*Na2O) − 1/2 RT ln(pO2/p*O2)`; equation (2) is the equal
oxygen-pressure form. The calibration cell uses the reference
5Na2O + 21Fe2O3 and pure Na2CO3, with equation (A) printed as
Na2O(sol. or liq.) + CO2(gas) = Na2CO3(sol. or liq.). The paper's standard
state sentence is retained verbatim in the extract: “The standard state of
Na2O activity was taken as pure solid Na2O below the melting point of Na2O
(1405 K) and pure liquid Na2O above the melting point.”

For cell 0, the paper prints pCO2=80 kPa and pO2=20 kPa (0.8 and 0.2 atm) over
the carbonate reference and reports the oxygen-sensor EMF as 5 ± 3 mV at
1150 K. It describes purified air over cells 6, 9, 10 and 11. It does not
print total experimental pressure or an absolute pO2 for those experimental
cells. Neither is inferred. Table 2's 1 mV / 1 h stability criterion is not
stored as a per-run uncertainty. The cell temperature control is reported
within 3 K; it is not a coefficient uncertainty.

The free-energy reductions use solid hematite as the Fe2O3 standard,
aNa3Fe5O9=1, and assume stoichiometric Na3Fe5O9. The authors restrict those
calculations to below 1405 K because liquid-phase Na2O activities were not
known. The paper states that error estimates are statistical only; confidence
level is not stated and reference/systematic errors are excluded.

`simulator/battery/enums.py::Quantity.ACTIVITY`,
`simulator/battery/enums.py::Quantity.DELTA_FG`,
`simulator/battery/migrate.py::QUANTITY_ALIASES`,
`simulator/battery/migrate.py::TYPE_QUANTITY` and
`simulator/battery/migrate.py::PHASE_MAP` support the stored quantities and
`cr` phase. The unresolved
fit form is the coefficient-only function, which
`simulator/battery/migrate.py::select_declared_source` does not convert to
point observations. Comparable coefficient-only fits in
`chatain-2021-nacro2-thermodynamics.yaml` are typed-refused. The exact EMF fit
quantity has no token in `simulator/battery/enums.py::Quantity` or
`simulator/battery/migrate.py::QUANTITY_ALIASES`; the eight EMF fits therefore
remain located context and are not mislabeled as activity. These code checks
are also recorded in the extract's schema-audit context.

The Table 4 Na3Fe5O9 row is the standard Gibbs energy of reaction (B), whose
extent is two Na3Fe5O9 formula units. `simulator/battery/enums.py::Quantity`
and `simulator/battery/migrate.py::QUANTITY_ALIASES` / `::TYPE_QUANTITY` have
no exact reaction-extent Gibbs-energy token; `Quantity.DELTA_FG` would assign
the full reaction energy to one species formula unit. The descriptive
`reaction_standard_gibbs_energy_for_reaction_B` label therefore migrates with
quantity unknown, pinned to exactly this row in the focused test.

The authors' fit outputs are `measured_reduced`, with Table 3 activity fits
carrying `raw_inputs.state=not_printed` at Figure 2 (cell 0) or Figure 3 (cells
6/9). Table 4 and abstract Gibbs fits link to the stored Table 3 fits named by
their printed reduction equations. The cited auxiliary inputs include the
1971 JANAF formation data used in calibration and Thery [4]'s 1033 K phase
interpretation. Numerical prior-work fits are not invented; the discussion
of Dai et al., Lin and Elliott, and Neudorf and Elliott is retained only as
printed qualitative attribution. Figure-only numeric values from Figures 2–5
are deferred.

## Isolated migration

The focused test runs the real migrator on this extract. Result: 12
observations, 12 unavailable coefficient-only expressions, 0 hard issues,
one quantity-unknown reaction-basis row (`yki1987_gibbs_table4_na3fe5o9`),
and 0 unknown phases. All 12 carry a page-located
temperature bound or interval. No total pressure or fO2 condition is added.

| Observation | composition | total pressure | fO2 | identity temperature | located temperature condition |
|---|---|---|---|---|---|
| `yki1987_activity_f_n3f5` | unknown; no numeric composition printed | unknown | unknown | unknown; fit has no single T | `<1405 K`, Table 3, p. 991 |
| `yki1987_gibbs_table4_na3fe5o9` | unknown; reaction quantity has no profile | unknown | unknown | unknown; fit has no single T | `<1405 K`, Table 4, p. 991 |
| `yki1987_gibbs_table4_na2fe2o4_mid` | not applicable to delta_fG | not applicable | not applicable | unknown; fit has no single T | `1047<T<1278 K`, Table 4, p. 991 |

The isolated migration preserves every printed range as a bound or interval
condition and leaves the identity temperature unknown because the paper gives
coefficient functions across the range, not a single measurement temperature.
The current generated-store delta is 12 observations plus the source metadata;
no store regeneration is committed.
