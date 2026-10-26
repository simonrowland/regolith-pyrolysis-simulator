# Floss et al. 1998 extraction audit

All 10 MinerU table blocks were converted to cell drafts and checked against
their page renders. The pdftotext layout file contains no usable article text,
so every numeric cell is recorded as `page_render` in the companion cell audit.
Table row and column placement was checked against each rendered layout. The
first MinerU block for Table 2 and the entire Table 3 and Table 6 blocks marked
body rows as headers; these were corrected in the draft before emission and
comparison. Table 2 has 12 body rows and three composition columns; Table 3
has 13 rows including `Total`.

| Printed table | PDF page | Printed rows / coverage | Numeric cells checked |
| --- | ---: | --- | ---: |
| 1 | 2 | 18 runs: 13 MH, 3 CA6, 2 RTE | 98 |
| 2 | 2 | 12 oxide rows including `Total`; EPMA, EDS, synthetic CA6 | 31 |
| 3 | 5 | 13 rows including `Total`; 12 oxide rows across six phase/sample columns | 46 |
| 4 | 5 | 14 rows including `Total` and `No. Anal.`; 16 MH/CA6 residue columns | 128 |
| 5 | 6 | 13 rows including `Total`; 16 MH/CA6 residue columns | 98 |
| 6 | 9–10 | 24 trace elements plus `# An.` in four panels; page 10 is the printed continuation | 336 |
| 7 | 11 | 23 labeled rows per panel, including the blank Gd row; 10 columns across two panels | 220 |
| 8 | 13 | 22 trace-element rows; starting, eprobe, and ionprobe columns for RTE-12/13 | 97 |
| 9 | 14 | 16 run rows | 32 |
| **Total** |  |  | **1086** |

The audit uses printed table numbers and absolute PDF pages. Tables 1–9 were
rendered on PDF pages 2, 2, 5, 5, 6, 9–10, 11, 13, and 14 respectively. The
higher-resolution page 10 and 11 renders were used for the dense Table 6
continuation and Table 7 censored/italic cells. The apparent Table 3
`fused_superscript` warning at row 11, column 7 is the printed range
`0.13(0.10–0.15)`; the render confirms it is a range, not a fused exponent or
footnote.

Lower-panel locator labels follow the panel headers: Table 4 draft row 16,
Table 5 draft row 14, and Table 7 draft row 25. This corrects 41, 31, and 69
human-readable column labels respectively; row and column coordinates and
numeric cells are unchanged. The same 141 sample labels are corrected in the
copied `total_pressure_Pa` point-condition locators. Nine Table 7 censored or
not-reported MH-8 context locators now preserve the printed `MH-8*` header.

The table-tool comparison covers 641 stored numeric values. It matched 346
simple numeric cells, including all 18 Table 1 mass-loss values. Table 3's 28 central-value/range cells are `no-match`
because the comparator cannot split parenthesized ranges. Table 6's 267
value-plus-uncertainty cells are `mismatch` because the comparator treats the
whole printed cell as nonnumeric. Each was checked against its render; the
extract stores Table 3 central values with the printed ranges and Table 6
central values with the printed 1σ widths. The comparison view adapts the
extract's `values.series` and printed table locators to the comparator's
`values.points` and MinerU block indexes; it does not alter the extract. Table
1 percent values are separately pinned through the isolated migration test.

## Data and schema decisions

- Table 1's 18 printed mass-loss values are retained in `%`; the migrator turns
  MH-1's printed 37.5% into a mass-loss fraction of 0.375. Each run has its
  printed temperature and a located mass-loss endpoint. The paper gives a
  1–120 min range across runs without assigning individual durations; every
  run duration stays unknown, with the range kept as run-level context. The
  ruling for d-289 allows its area-free mode to use the same-run mass-loss
  endpoint without a duration.
- The apparatus is called a high-vacuum furnace on page 1. Numeric pressure,
  absolute fO2, and gas mix are not printed. No numeric values are assigned;
  the printed atmosphere phrase is retained. The measured temperature is
  carried on every observation through `T_C` and located `point_conditions`.
- Natural starting material has distinct EDS and electron-probe analyses in
  Table 2. The authors do not designate which analysis is used in the
  remaining-fraction calculation, which defers to Floss et al. (1996). Both
  analyses remain as located context; natural-run identity composition is
  unknown with the controller's specified reason.
- Synthetic CA6 has a unique Table 2 composition. The unsupported printed
  components `REE2O3` 0.28 wt%, `Y2O3` 0.02 wt%, and `HfO2` 0.02 wt% total
  0.32 wt%, below the controller's 5 wt% threshold. The identity binds the
  supported oxides at their printed values (`Al2O3` 89.26, `CaO` 8.35,
  `TiO2` 1.59, `MgO` 0.80, `ZrO2` 0.02 wt%) without renormalizing their
  100.02 wt% subtotal. The composition locator names the omitted components
  and values; all Table 2 values remain in located context.
- Table 1 modal volume percentages have no schema quantity and remain typed
  refusals. Table 3 group-average corundum values do not bind to individual
  runs and remain typed refusals; numeric phase values for MH-7/MH-14
  thorianite and REE-phase rows are retained. Aggregate `REE2O3` has no
  canonical species formula and remains typed-refused where printed.
- Table 4 WDS values describe quenched glass. The paper says samples were
  cooled radiatively by turning off power but makes no preservation or
  alteration claim; glass rows carry `quench_state: not_stated` and the page 1
  statement. Table 5 broadbeam EDS values describe bulk crystalline residue.
- Table 6 SIMS trace concentrations are retained in ppm with printed 1σ
  widths. The 13 natural MH Gd values are interpolated per page 6 and marked
  `Gd*` in Table 6. The asterisk stays in the source row locator; the canonical
  species is `Gd`. Since the neighboring measured values used for interpolation
  are not stored as explicit parents, these values are `quoted_attributed` with
  the interpolation note. The three unstarred synthetic CA6 Gd values have
  printed uncertainties and remain `measured_direct`.
- Table 7 INAA values retain their printed ppm values, censored limits, and
  italic high-error notation. Figure 7's caption states that RTE residue Yb
  values are upper limits. The Table 8 RTE-13 value 1598 ppm is retained as a
  typed-refusal context row with `upper_limit` status and `<=` operator; the
  residue-component path cannot emit a bound-valued `residue_ppm`, so no point
  observation is created. Specifically, `select_declared_source` sends
  `residue_ppm` through `_as_dec_or_none` at
  `simulator/battery/migrate.py:10627`; the series emitter skips unavailable
  cells at `simulator/battery/migrate.py:17731`. Adding bound support would
  require a migrator change outside this extraction's schema scope. Charge
  weights are kept as context, not sample
  charge mass. Elemental Fe wt% has no matching residue field and is
  typed-refused. Table 8 ionprobe ppm values are retained;
  eprobe elemental wt% and incomplete O-free starting RTE values are preserved
  as located context/refusals. Table 9's printed `F_Ca` values and uncertainties
  are typed-refused because no matching isotope-fractionation quantity token
  exists; the table's repeated mass-loss values are already stored from Table
  1.
- No figure-only values are extracted. Figures 2–8 are listed as figure-read
  candidates; MinerU's `<details>` figure-derived tables were ignored.

## Isolated migration

The real migrator produced 18 experiments and 641 observations with zero hard
issues and zero registry issues. It retained 677 context records, including
the censored RTE-13 Yb value. The migrator reported 1300 unavailable-queue
entries: 641 for an unstated experiment method token, 641 because the source
PDF is not yet listed in the repository INDEX asset registry, and 18 mass-loss
rows with no printed phase. These do not create hard issues. Across all observations,
temperature and condition locators reached migration; no numeric total
pressure was created. MH-1, CA6-1, and RTE-12 all migrate their printed
temperatures (2373.15 K, 2373.15 K, and 2673.15 K); total pressure and absolute
fO2 remain unknown. Natural MH and RTE identities remain composition-unknown;
CA6 identity receives its supported Table 2 composition, with the 0.32 wt%
omission note on both experiment and residue-row locators.
The 13 interpolated natural Gd values migrate as species `Gd` with
`quoted_attributed` evidence; the three synthetic CA6 Gd values migrate as
species `Gd` with `measured_direct` evidence and no evidence attribution. The
extract retains their top-level `present work` source attribution, but removes
the value-level interpolation flag and attribution from those synthetic rows.
The RTE-13 Yb upper limit
(1598 ppm, `<=`) is preserved in located typed-refusal context; it does not
migrate as a point observation because the residue-composition path has no
bound representation.

The focused test validates the extract and its three fidelity samples, then
pins the rendered values from Table 1 p. 2, Table 4 p. 5, and Table 6 p. 10,
including the printed row/column locators, the migrated page/table locators,
and the 18-run count. It checks all 141 copied lower-panel condition locators
and all nine `MH-8*` context locators. All 677 context records carry an
explicit unit token. A deliberate
change to the Table 1 MH-1 mass loss from 37.5 to 37.6 makes the focused test
fail; restoring the printed value makes it pass again.

`tests/test_yaml_flow_scalar_commas.py` passed (17 tests), and the focused
Floss test passed. The repo-wide `tests/test_literature_extracts.py` run
completed with 797 passed and four failures. The existing nested
`docs-private` provenance-path expectation failed. The corpus validator,
pending-stub scan, and pilot count/merge checks failed because existing Kems
extracts contain nonportable `docs-private` `source_path` values
(`kems-012-sossi-2019.yaml` and `kems-020-hastie-1981-nbsir.yaml`). The Floss
extract did not appear in those failures and passes the document validator in
the focused test. Mutation checks make the focused test fail for an altered
natural Gd species, changed Yb upper-limit status, incorrect lower-panel sample
label or copied condition locator, a missing Table 7 header asterisk, and
interpolation metadata added to a synthetic CA6 Gd value. No battery store was
regenerated. Expected store delta, if regenerated by the controller: one work,
18 experiments, 641 observations, and 677 context records.
