# Roeder (1974): extraction audit

## Table 1 transcription and cell review

Table 1 contains 45 runs: PDF pages 4–8 (printed pages 400–404), nine run
columns per page. The printed table count is 45. The table groups are SY-MN
1–9, CRB 4a 10–19, K14 G 20–22, 4135 23–25, GL-654 26–28, HAW 29–30,
GL-1050 31–33, 4136 34, GL-1868 35, K30J 36, CA 4 37, WRAB 38–39,
Z 519 40, Z 571 41, 653 42, P 340409 43, and GL-C71 44–45.

The complete cell-to-page/row/column audit is in
`roeder-1974-iron-activity-olivine-basaltic-liquids.table1-cell-audit.csv`.
Each required cell was read from its 600 dpi page render (audit method b),
with MinerU and Tesseract used only as cross-checks. MinerU has only 16.6%
numeric agreement on this table; the alternate MinerU pass failed and the
300 dpi Tesseract layer also drops digits and decimal marks. The render is
therefore the authority for all 675 audited cells. The table has 649 numeric
cells in the extracted columns and 26 blank MnO cells. Blank MnO cells are
typed absent; no zero was inferred. The decimal places follow the printed
column precision: two for oxide composition and total, integer °C, three for
CO2/H2 and Act. FeO, and two for Log fO2 and γ FeO. The audit preserves the
machine-text spelling beside each render-confirmed value.

The table's sample label is printed `4135` with no suffix in its header. The
separate quotation-mark footnote `*` on PDF p. 2 (printed p. 398) is retained
in the extract. No superscript or other marker was visible on the Table 1
activity or activity-coefficient cells.

Table 1 prints composition as liquid oxide wt%, `Temp.` in °C, CO2/H2, `Log
fO2`, `Act. FeO`, and `γ FeO`. The source has no EMF column or EMF result. It
also prints duration, phase assemblage, silica activity, and olivine mol%;
these are not copied as FeO scoring quantities. Table 2 contains correlation
coefficients for olivine/liquid oxide ratios, not FeO activities. Table 1
values are retained verbatim, including trailing zeros. Every activity row
contains all oxide cells and the printed composition total; blank MnO cells
remain explicitly absent.

## Conditions and interpretation

- Table 1 reports 45 runs. The prose says 47 runs; two additional Fe–Si–O
  checks are described only by 1260°C, CO2/H2 ratios 0.400 and 0.393, and
  SiO2 contents 31.8 and 31.2 wt%. No individual activity, activity
  coefficient, or log fO2 is printed for those checks, so they are not
  assigned invented run numbers or observations.
- The abstract on PDF p. 1 (printed p. 397) states the study-wide range as
  `log fO2 = -12.5 to -14.5 atm`. The Table 1 `Log fO2` column is recorded
  relative to atm using that abstract locator; the bar default is not used.
- Experimental details on PDF p. 3 (printed p. 399) state that experiments
  used iron crucibles, a metallic iron mixing rod at 60 rpm for at least 90 h,
  a CO2/H2 gas atmosphere, and water quenching to below red heat in less than
  ten seconds. The activities are of the high-temperature liquid and are
  phase `l`; the quenched glass is not the measured activity phase.
- The paper does not print the experimental total pressure. Every observation
  carries a typed unknown for total pressure; no 1 atm or 1 bar condition is
  supplied.
- The exact standard-state sentence on PDF p. 9 (printed p. 405) is `The
  standard state for “FeO” is the liquid oxide in equilibrium with metallic
  iron.` Each activity row now carries the existing Raoultian liquid
  `Fe(1-x)O` reference state, with component basis `Fe(1-x)O`, and the printed
  iron-equilibrium condition. The named Doyle extract in this checkout stores
  its Fe0.947O standard as prose; this extract uses the repository's existing
  structured `StandardState` payload. The source's footnote on PDF p. 2
  (printed p. 398) says the iron/oxygen ratio is close to one but deviates
  from one. It prints no numeric O/Fe or x value at saturation, so no
  saturation composition is recorded or computed.
- Equation (1) on PDF p. 1 is `Fe + 1/2 O2 ⇌ FeO (dissolved in liquid)`.
  Equation (2) defines the activity and activity coefficient of a
  hypothetical ferrous species in liquid and the mole fraction of iron
  calculated as ferrous oxide. The authors say the experiments were in
  equilibrium with metallic iron and assume pure iron activity is one.
- Equation (3) on PDF p. 9 prints `γFeO = (0.048)(wt.% Al2O3) +
  (0.084)(wt.% Na2O) + (0.12)(wt.% K2O) + 0.29; PMCC = 0.94`. The activity
  equation is printed as `log a_FeO = 12045/T + 0.5 log fO2 - 2.336`; no
  temperature unit is printed with `T`. No derived duplicate values are
  added.
- Figure 2's text and caption give constant activity 0.113 ± 0.005; the
  confidence level is not stated. It is retained as context; plotted points
  are undigitised. Figures 1–5 contain other figure-read candidates and are
  not digitized.

## Cell and locator reconciliation

The MinerU table block flattens all five page sections into one CSV on PDF
page 4. Its later section headings occur at CSV rows 26, 51, 76, and 101;
the physical page sections are p. 5, 6, 7, and 8 respectively. This mismatch
was resolved against each page render. Extract locators use the actual PDF
pages 4–8 and printed table number `1`, never the draft table ordinal. The
compare output is committed alongside this audit. Its counts are 0 matched,
72 no-match, 18 mismatch, 0 presence-only, 0 needs-mapping, 0 locator-error,
and 596 unverifiable (686 rows total). The 72 no-matches are the 18 numeric
activity and gamma values on each continued page, p. 5–8, for which the draft
has no table block. The 18 p. 4 mismatches are the nine activity and nine
gamma values in the transposed table: the draft has candidates but does not
bind them to the printed run columns and row headings, and several recognized
decimal marks are spaces. All 90 values were checked against their exact
rendered cells in the cell audit. The 596 unverifiable rows are locator
checks on p. 5–8: MinerU did not capture repeated caption evidence for the
continued table pages. The renders confirm those cells continue printed
Table 1, and their actual page/table locators are retained in the extract.

## Migration and verification

The controller ruling selects `measured_reduced` for both quantities. Activity
rows derive from typed context records for the printed equilibrium relation,
its constants, and that run's printed conditions; each derivation locates the
printed equation. Gamma rows derive from that run's activity observation via
the printed `gamma = a/X` relation. No code or migrator changes were needed.

The isolated real migration has 90 observations, 0 hard issues, 90 numeric
points, and 0 unavailable values. No migrated quantity or phase is unknown.
All 90 temperatures, fO2 values, typed reference states, and iron-saturation
conditions reach each observation. Total pressure is unknown for all
observations because the experiment pressure is not printed. Composition is
unknown for the 26 Table 1 rows with a blank MnO cell; Table 2 contains
correlation coefficients, not alternate run compositions to join. The same
26 run compositions remain unknown on both their activity and gamma rows.

Identity axes after isolated migration:

| Migrated observation | Composition (mole fraction) | Total pressure (Pa) | fO2 (Pa) | Temperature (K) | Reference state |
| --- | --- | --- | --- | --- | --- |
| Run 1 activity | unknown (MnO cell blank) | unknown | 3.355186088422354263011974178E-8 | 1579.15 | Fe(1-x)O(l), iron-saturated |
| Run 23 activity | SiO2 0.4409393808421535756384873969; Al2O3 0.07658540930972451407797632525; FeO 0.2148773963931374284090054678; MgO 0.1187616688312790971630567709; CaO 0.1131610931577071212528078463; Na2O 0.009006655167539827897226302601; K2O 0.004291365910699174949932913055; TiO2 0.01912080072684749533319786024; MnO 0.003256229660911765278309117548 | unknown | 3.513311137210276836756623921E-8 | 1530.15 | Fe(1-x)O(l), iron-saturated |
| Run 45 activity coefficient | SiO2 0.4260776928251815069283717692; Al2O3 0.07163868688048699330316767969; FeO 0.2426043852729424545605017238; MgO 0.08848185734986622236447848445; CaO 0.1293460902884312134414549193; Na2O 0.01469295199343954464321964951; K2O 0.003853528652495700983836950295; TiO2 0.02097073277180754048091865827; MnO 0.002334073965348823294050165649 | unknown | 2.487233808817857008300817804E-8 | 1486.15 | Fe(1-x)O(l), iron-saturated |

The focused Roeder test pins every numeric value and condition to the render
cell audit, checks the typed state and saturation condition on every
observation, and verifies that mutations to the run 23 gamma or reference
state fail.
It passed (1 test). `tests/test_yaml_flow_scalar_commas.py` passed (17 tests).
The full `tests/test_literature_extracts.py` module and its targeted
`test_repo_extracts_validate_green` selection each exceeded the 300-second
local cap; the focused Roeder test did validate and migrate this extract.
The required j01 census selection also exceeded the 300-second local cap, so
both broad checks remain UNVERIFIED-local.

Expected generated store delta, without regeneration: 90 numeric observation
rows; the generated store was not modified.
