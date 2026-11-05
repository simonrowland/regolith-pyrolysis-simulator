# Mysen and Virgo (1989) Fe redox extraction audit

## Scope and row reconciliation

Table 2 (printed page 60, PDF page 3) contains 53 experimental rows; the extract has one observation per printed row. All rows report Fe3+/ΣFe. The seven double-starred rows remain separate observations because the footnote identifies spectra obtained at 77 K. Their `T_C` values remain the melt experiment temperatures from Table 2; no 298 K spectrum temperature is inferred for unmarked rows.

Table 1 (printed page 59, PDF page 2) supplies the 13 nominal starting compositions. Each Table 2 sample label was joined to its Table 1 heading, with the printed Table 1 hyphens retained in `composition_sample_as_published` for NAS-VII, NAS-VIII, NAS-XII, and NAS-XIII. The composition values are stored as printed weight percentages. The methods say 5 wt% Fe2O3 was added to every starting composition, and Figure 2 labels its composition scale in wt%. The authors also state that reliable microprobe analyses of the quenched glasses could not be obtained because of Na volatilization, so these are nominal starting compositions rather than analyzed final-glass compositions.

## Cell reads and locators

Inputs were the MinerU Table 1 and Table 2 table blocks and `pdftotext-layout.txt`. The QA pre-screen reported:

- Table 1, PDF page 2: 56 numeric candidates, agreement 0.833, 10 numeric mismatches, one comma/dot flag, and four LaTeX flags.
- Table 2, PDF page 3: 419 numeric candidates, agreement 0.969, and 13 numeric mismatches.
- Table 3, PDF page 15: 31 numeric candidates, agreement 0.947, and two numeric mismatches.
- Table 4, PDF page 16: 14 numeric candidates, agreement 0.810, two numeric mismatches, and one run-together flag.

The full page renders at 400 dpi settled the QA flags and confirmed each table's row and column layout. Pages 15 and 16 were also rendered because the pre-screen flagged Tables 3 and 4. `<details>` chart blocks were ignored. The committed cell audit CSV lists each of the 52 Table 1 composition cells and each of the 159 Table 2 temperature, log fO2, and Fe3+/ΣFe cells stored in this extract. Each is marked `(b)` because it was checked against a page render. The Table 2 comparison output has 53 matches, zero mismatches, and no locator errors.

The observation IDs encode the PDF page, printed table, MinerU draft row, and value column so the comparator can check the last-column fraction cell individually. The observation locator still uses printed Table 2, PDF page 3, the sample label, and `Fe3+/ΣFe`. `locator.phase: null` records that the printed numeric cell has no phase-qualified column; the observation itself remains `phase: glass`. This keeps the comparator from misreading the physical phase through `tools/mineru_table_draft.py::_phase_matches_column`, while the isolated migration test confirms the observation phase is glass.

## Quantity and conditions

The stored value is the printed fraction Fe3+/ΣFe, represented with `Quantity.VALENCE_RATIO`, numerator state 3, denominator total Fe, and `value_form: fraction`. No Fe3+/Fe2+ conversion or percentage conversion is stored. The authors fitted their own 57Fe Mössbauer spectra with Lorentzian lines and report the ratio as the Fe3+ doublet area divided by the total absorption envelope. The integrated areas are not tabulated, so the value is `measured_reduced`, with `derivation.raw_inputs` marked `not_printed` and located to the representative spectra and methods on PDF page 4. It is not attributed to another author.

Every observation carries its printed `T_C`, absolute log10(fO2/bar) from Table 2 column 3, sample ID, and the matching Table 1 nominal composition. The methods describe CO-CO2 gas mixing to control fO2 but do not print a gas ratio. No experimental total pressure is printed. The extract records total pressure as unknown; the migrator leaves `total_pressure_Pa` absent and the focused test pins that absence.

The measured material is quenched glass, so all rows use phase `glass`. On PDF page 4 (printed page 61), the authors state that Fe3+/ΣFe in melts is the same as in quenched melts (glasses); each row records this as `asserted_preserved` with that locator. The methods describe quenching on liquid nitrogen or in water, at about 500 °C/s to below 1000 °C, producing glass free of quench crystals.

## Exclusions

Table 3 reports average Fe3+/ΣFe and distribution percentages for natural rock groups compiled from Chayes data. It is not a set of sample-specific NAS melt experiments and has no matching experimental temperature or fO2, so it is not mixed into the Table 2 point set. Table 4 contains regression coefficients for Equation 11, not sample points. Their flagged numeric cells were render-checked, but neither table contributes observations. Figure-only redox relationships in Figures 9 and 15 are candidates for a separate figure pass; no plotted coordinates were digitized here. Figures 3–5 are representative spectra and are used only to locate the unprinted fit inputs.

## Isolated migration

The real migrator produced 53 observations, 0 hard issues, 0 unavailable values, 0 quantity-unknown observations, and 0 phase-unknown observations. Identity axes after migration:

| Table 2 row | composition | total_pressure_Pa | fO2_Pa | temperature_K |
| --- | --- | --- | --- | --- |
| NASI, 1550 °C, log fO2 −0.68 | mapped from Table 1 NASI | unknown | 20892.96130854039483122233736 | 1823.15 |
| NASIV, 1550 °C, log fO2 −2.00 | mapped from Table 1 NASIV | unknown | 1000.00 | 1823.15 |
| NASXIII, 1250 °C, log fO2 −9.00 | mapped from Table 1 NAS-XIII | unknown | 0.000100000 | 1523.15 |

The focused test checks all 53 migrated rows for quantity, valence basis, phase, composition, temperature, log fO2, and absence of total pressure. It also pins three Table 2 fractions and Table 1 composition cells to the renders, and verifies that an interior value mutation and an invented 101325 Pa pressure both fail.

## Verification status

The focused extract test passed (2 tests), including isolated migration and both mutation checks. `tests/test_yaml_flow_scalar_commas.py` passed (17 tests), and the single-extract validator with fidelity matching passed. The required repo-wide `tests/test_literature_extracts.py` run is `UNVERIFIED-local`: the suite's collection and global validation did not finish within the 300-second per-call limit. An earlier attempt caught and led to removal of a metadata-only fidelity sample; the suite was not rerun to completion after that correction.

## Expected store effect

Regeneration is not committed. The expected observation-store delta is +53 Fe valence-ratio observations; no battery-store files were regenerated or added to this change.
