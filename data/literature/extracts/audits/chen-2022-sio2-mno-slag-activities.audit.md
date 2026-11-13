# Chen et al. (2022): activity extraction audit

## Source and transcription

Source: Chen J.-B., Luan H.-Z., Huang H.-H., Zhao M.-H., Pan W.-B., Chen Z.-Y. (2022), “Thermodynamic activity of SiO2 in CaO-SiO2-Al2O3-MnO-MgO system molten slags,” *ISIJ International* 62(7), DOI `10.2355/isijinternational.ISIJINT-2022-018`.

Transcription began with the MinerU table blocks and `pdftotext-layout.txt`. `tools/mineru_table_draft.py convert` produced drafts for Tables 1–3; `tools.mineru_table_qa` reported numeric agreement 1.000 for all three tables and no ragged rows. Table 2 has merged header cells and a second header band that the converter counted as a body row. The PDF page 3 render confirms that the table has 37 numbered data rows, not the converter's 38 body rows. The PDF page 2 render confirms Table 1 also has 37 rows. The full Table 2 row and column assignment was checked on the page 3 render; page 2 was rendered for the method and Table 1; page 4 was rendered for Eqs. (4)–(7), the reference-state prose, and the figure captions.

Table 2 has 12 numeric data columns for each of its 37 rows: temperature, metal Si and Mn, five slag oxides, two activity coefficients, and two activities. All 444 body cells are listed in `chen-2022-sio2-mno-slag-activities.cells.csv`; the two printed ×10^-3 headers and three method-condition values are also listed there. Each cell is confirmed against the page render (verification `(b)`); the machine reads agree on the digits. Table 2's `No.` labels are locators, not measured values. The cell audit identifies every printed table row and column and the corresponding extract fields.

`chen-2022-sio2-mno-slag-activities.mapping.yaml` maps the activity columns and their ×10^-3 header for the table-tool comparison. Its grid row numbers account for the two header bands; source locators in the extract retain the printed Table 2 row numbers 1–37 and PDF page 3. The comparison report is `chen-2022-sio2-mno-slag-activities.compare.csv`. The command reported 74 mismatches because the comparator's `tools/mineru_table_draft.py::_phase_matches_column` accepts `gas`, `liquid`, or `solid`, while the required migrator token on each observation is `l`. An in-memory diagnostic that removed only the phase field from the 74 activity rows matched all 74 values. No extract or tool code was changed for that diagnostic. The raw comparison output is retained; all reported values and coordinates were checked against the render in the cell audit.

## Scope and row mapping

Table 1 has 37 initial charge recipes; they are not equilibrium melt compositions or activity outputs, so the activity rows use the corresponding measured molten-slag compositions from Table 2. Table 2's 37 equilibrium rows are fully transcribed. The extract has 222 observations:

- 37 measured-direct ICP-AES Si assays in the liquid metal and 37 measured-direct Mn assays, retained as parents for the printed reduction equations;
- 37 SiO2 and 37 MnO activity-coefficient outputs from the authors' printed equilibrium reductions;
- 37 SiO2 and 37 MnO activities reported for the same slag composition and temperature.

Every row carries the printed `T_C`, the row-local Table 2 five-oxide composition in `point_conditions.composition`, the same printed Ar/CO condition and pCO, and located unknown states for total pressure and absolute fO2. The gas condition is Ar 500 mL/min plus CO 200 mL/min, with pCO = 0.286 atm. The flow ratio and pCO are retained as printed; they are not used to infer total pressure or absolute fO2. The paper does not state total pressure or absolute fO2. The migrator binds slag composition and temperature on all six records for each table row and leaves pressure and fO2 unknown.

Metal assay parents use phase `l` with `phase_label: metal`; their identity composition is the coexisting equilibrium slag composition printed on the same Table 2 row. Activity and activity-coefficient rows use phase `l`, the liquid slag state at the experimental temperature. The source cools samples in Ar before analysis, but it does not report glass activities; these thermodynamic outputs are for the equilibrium molten slag, not a quenched-glass measurement.

The γSiO2 coefficient and both activity columns carry the printed ×10^-3 scale; γMnO is unscaled. The extract keeps each printed mantissa and its printed scale, and stores the dimensionless migrated quantity using that scale. No row-specific uncertainty is printed. The coefficients are measured-reduced outputs of Eqs. (5) and (7), with their corresponding ICP-AES metal-assay observations as lineage parents. Activities are measured-reduced outputs from the reported coefficient and the oxide mole fraction of that same printed slag composition.

The source states that SiO2 is Raoultian relative to pure SiO2(s), identified as quartz in Eq. (3). MnO is Raoultian relative to pure solid manganese oxide; the paper does not identify its polymorph. Those reference states are represented structurally, with MnO polymorph left unknown. The closed extract vocabulary supports `activity`, `activity_coefficient`, `equilibrium_phase_composition`, and phase `l`; there is no schema gap. The relevant existing code owners are `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, and `simulator/battery/enums.py::PHASE_BASIS_TOKENS`.

## Source consistency review

All SiO2 activity and coefficient rows are retained as `measured_reduced` with `admission_status: typed_refusal`; none is reclassified. At 1400 °C, Eqs. (4)–(5) and the method's single printed pCO = 0.286 atm imply a common proportionality between SiO2 activity and the equilibrium metal-Si activity at fixed temperature and gas condition. For rows 1–8, printed activity mantissa divided by printed metal Si mass% ranges 22.77–23.20. For rows 9–18 it ranges 1.176–1.208, a roughly 19-fold split. No second gas condition is printed for either group. The paper's Table 2 outputs therefore cannot be admitted as one consistent measured series from the stated conditions. The extract preserves the paper's values for review while refusing their use.

The MnO data were checked separately against Eq. (7). Within each printed temperature group, activity mantissa divided by metal Mn mass% is stable to table rounding: 2.149–2.190 at 1400 °C, 1.176–1.219 at 1450 °C, and 0.684–0.708 at 1500 °C. This supports keeping the MnO coefficient and activity rows admitted. It does not supply the unprinted pressure or fO2.

Table 3 (24 rows) is excluded because it reports phase-structure and oxide-consumption interpretation, not independent activities. Figures 1–2 contain plotted activity-coefficient comparisons; figure-read values were not digitized. No binary supplement was present in the source support location.

## Migration and tests

The real migrator was run on this extract in isolation. It produced 222 observations, 0 hard issues, 0 registry issues, and 0 unavailable values. No observation has an unknown quantity or phase. The focused test asserts all 37 rows per quantity, lineage, migrated temperature and composition for all six records per row, unknown total pressure and fO2, separate pCO, reference states, and six scaled activity values from the page render. Its mutation check changes a pinned Table 2 activity mantissa and confirms the fidelity matcher fails.

Expected generated store delta: 222 observations (148 admitted and 74 typed-refused), with 0 unavailable values. The battery store was not regenerated.

## Verification

- The focused extract tests pass: 2 passed. The mutation check changes a Table 2 activity mantissa and makes the fidelity matcher fail.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- `tests/test_literature_extracts.py`: 891 of 893 collected cases passed across bounded partitions, including the isolated full-corpus validator and all 637 fidelity-sample cases. The `test_coverage_pending_stubs_not_found` and `test_pilot_extract_count_exact_and_merge_smoke` cases are UNVERIFIED-local: neither produced a result within the 300-second per-call limit; the interrupted invocations reported 313.02 s and 317.68 s respectively. No assertion failure was reported in either invocation.
- No source supplement was found at the corpus support location.

No changes were made under `simulator/` or `tools/`. No generated battery store or `INDEX.*` file is included.
