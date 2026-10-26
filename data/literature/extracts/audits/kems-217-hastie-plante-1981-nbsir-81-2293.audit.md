# Hastie and Plante (1981), NBSIR 81-2293: extraction audit

## Duplicate check and extraction decision

No extract with this source ID, DOI, title, or first-author/year was found at the seat base or in the refs checked. The historical path check for this source ID and DOI/title searches returned no prior extraction. `kems-020-hastie-1981-nbsir` and `kems-027-plante-hastie-1983` were checked separately.

Table 1 contains 17 printed fit rows. All 17 match rows 1–17 of Table 2 in `kems-020-hastie-1981-nbsir` by A and B, temperature bounds, any additional C/D/E coefficients, and printed composition values or ranges. The row crosswalk checked all 17 rows against the corpus Table 1 transcription and prior extract. `kems-027-plante-hastie-1983` has no row matching a Table 1 A/B/temperature-range fingerprint. The duplicate fit rows were not emitted again. The existing `kems-020` transcription retains C/D/E in `additional_coefficients_as_printed`; composition details appear in its system, phase, comment, or composition fields. Text formatting and footnote marks differ in a few cells; the numeric values and row assignments agree. For example, Table 1 prints row 9 A as `6.350`, while the existing numeric field is `6.35`.

## MinerU and source table inventory

The seat table tool converted the MinerU content lists and the table QA tool reported numeric agreement 1.000 with no scientific numeric cells to compare. The only table block is the NBS bibliographic datasheet on absolute PDF page 35; it has no scientific body rows. Seven `<details>` chart blocks contain MinerU-invented figure tables and were ignored. No scientific table was available to emit, so the mapping lists no emitted tables; the emitter rejects an empty table list. The compare output is:

```text
compare: matched=0 no-match=0 mismatch=0 presence-only=0 needs_mapping=0 total=0
species,observation_id,point_index,pdf_page,value,status,matched_cell,draft_candidate_count,path
```

The printed Table 1 is present in the corpus manual transcription at `tables/kems-217-hastie-plante-1981-nbsir-81-2293/t1.csv`, but not as a MinerU table block. The transcription was audited against all three PDF page renders. The table row/column layout and every printed row cell were checked visually. Each numeric table cell therefore uses audit method (b), a page render; MinerU provided no second read of this table. The PDF pages below are absolute, one-based PDF pages; the printed folios are 15–17.

For every row, the render check also covered the system and phase cells and the full comments/source cells, including embedded numeric text such as the standard-deviation and composition notes in row 12, sulfur content and the fitted K2O composition in row 13, composition changes in rows 14–15, and the pressure/K2O notes in row 17. The table below enumerates the numeric fit/composition fields; each row's remaining printed cells were checked in the same render pass.

| Printed row | PDF page | Numeric and composition cells checked against render | Result |
|---:|---:|---|---|
| 1 | 19 | T bounds, A/B/C, K2O range | (b); matches prior row 1 |
| 2 | 19 | T bounds, A/B | (b); matches prior row 2 |
| 3 | 19 | T bounds, A/B | (b); matches prior row 3 |
| 4 | 19 | T bounds, A/B | (b); matches prior row 4 |
| 5 | 19 | T bounds, A/B | (b); matches prior row 5 |
| 6 | 19 | T bounds, A/B | (b); matches prior row 6 |
| 7 | 19 | T bounds, A/B | (b); matches prior row 7 |
| 8 | 19 | T bounds, A/B, K2O composition | (b); matches prior row 8 |
| 9 | 19 | T bounds, A/B, K2O composition | (b); matches prior row 9 |
| 10 | 19 | T bounds, A/B, K2O composition | (b); matches prior row 10 |
| 11 | 19 | T bounds, A/B, K2O composition | (b); matches prior row 11 |
| 12 | 20 | T bounds, A/B/C/D/E, K2O/CaO/Al2O3 ranges | (b); matches prior row 12 |
| 13 | 20 | T bounds, A/B, K2O/Al2O3/Fe2O3/CaO/MgO/SiO2/Na2O composition | (b); matches prior row 13 |
| 14 | 20 | T bounds, A/B, K2O/Al2O3/Fe2O3/CaO/MgO/SiO2 composition | (b); matches prior row 14 |
| 15 | 20 | T bounds, A/B, K2O/Al2O3/Fe2O3/CaO/MgO/SiO2 composition | (b); matches prior row 15 |
| 16 | 20 | T bounds, A/B, K2O/Al2O3/Fe2O3/MgO/SiO2 composition | (b); matches prior row 16 |
| 17 | 21 | T bounds, A/B, K2O/Al2O3/Fe2O3/CaO/MgO/SiO2 composition, post-vaporization K2O | (b); matches prior row 17 |

The printed row count is 17. The focused test pins row 1, 12, and 17 values against their renders and verifies the duplicate row count. After isolated migration it pins the migrated KMS/TMS method facts on PDF page 7, the Figure 7 experiment on PDF page 15 / printed page 11, the calibration check on PDF page 16 / printed page 12, and unknown temperature and total pressure.

## Experimental method and calibration

PDF page 7, section 1, describes the KMS and TMS methods by reference to earlier works. Its render confirms the printed upper pressure limits (`10^-4` atm for KMS and one atm for TMS) and upper gas residence times (about 0.04 s and 20 s, respectively). These are method limits, not run pressures or residence times, and were stored only as bench facts; the approximate qualifier is retained for both residence times. PDF page 15 / printed page 11, section 5.1, identifies the TMS salt-exchange sample as NaCl + K1 slag with 19.4 wt.% K2O. The partial analysis is stored as printed and is not normalized or supplemented with other components; Table 1's distinct 19.5 wt.% K2O statement is not substituted. The report does not specify an actual total pressure or temperature for the Figure 7 salt-exchange experiment; both migrate as unknown. No calibration procedure is specified in the method description here.

On PDF page 16 / printed page 12, section 5.1, the authors state that near-unit NaCl activity in the initial phase of the TMS experiment confirms the calibration factors used to convert mass-spectral ion intensities to partial pressures and reflects thermodynamic equilibrium. This printed apparatus check is recorded as experiment context. The calibration procedure itself remains unknown; no procedure is inferred from the check.

Figures 1–8 contain useful figure-read candidates: non-equilibrium K2O-Al2O3-SiO2 vaporization; synthetic Western/Eastern slag K pressure; K and O2 pressures; K2O dissociation-pressure comparisons; K2O activity coefficients versus temperature and composition; plasma-slag pressure comparisons; TMS NaCl/KCl activities; and KCl activity in the NaCl(s)-K2 system. None were digitized in this extraction.

## Migration and verification

The isolated migration of this extract produced 0 observations, 0 hard issues, 0 unavailable values, 2 benches, 1 experiment, and 1 queue entry. The queue entry is the expected typed absence (`extract yielded no observations`): Table 1 is duplicated in `kems-020`, and figure-only measurements are out of scope. No observations means no temperature, fO2, pressure, or melt-composition conditions were invented.

- `tests/test_kems_217_hastie_plante_1981_extract.py`: 1 passed. It pins the row count, fit values from rows on PDF pages 19–21, migrated method limits on page 7, migration locators, unknown total pressure/temperature, and the typed-absence queue.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- Direct extract validation: 0 errors.
- Mutation check: changing the pinned prior row 1 A value from 4.721 to 4.720 in a temporary copy made the focused test fail at the pinned-value assertion; the temporary file was removed.
- `tests/test_literature_extracts.py`: interrupted after 158 passed and 3 failures. The failures reported a pre-existing docs-private provenance-path expectation mismatch, existing docs-private source paths in `kems-012` and `kems-020`, and this extract's missing empty `species` map. The `species: {}` omission was fixed; direct validation and the focused test pass afterward. The repo-wide suite was not rerun to completion because the run exceeded the task's 300-second command limit.

Expected store delta, without regeneration: +1 work, +2 benches, +1 experiment, +0 observations.
