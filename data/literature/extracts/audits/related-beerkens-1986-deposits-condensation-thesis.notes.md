# Beerkens (1986) extraction audit

## Stop condition

**BLOCKED: migrator-change-needed.** The thesis prints gas compositions for three extracted sodium-sulphate dewpoint series, but the real migration leaves `Identity.composition` unknown for all three. `simulator/battery/identity.py::profile_for` requires `composition` for a transition-temperature dewpoint when composition is unknown. The printed mixtures are retained at their source locators in `point_conditions.atmosphere`; this does not bind the required identity axis.

The test migration reports 66 migrated observations and zero hard issues. For gas-I Table 3.8 / section 3.3.2, measured Table 4.3, and the Kohl dewpoint, the required identity axes are `composition` and `total_pressure_Pa`; observed axes are composition unknown, total pressure unknown, fO2 not-applicable, and temperature not-applicable. The total pressure omission is correct because none is printed. The composition omission is a contract defect because a mixture is printed.

The existing composition path has no exact representation for these mixtures. `simulator/battery/enums.py::AmountBasis` has no volume-percent or mixed volume-percent/volume-ppm basis. `simulator/battery/records.py::Composition` stores one amount basis for all components. The mole-percent parser `simulator/battery/migrate.py::_mole_percent_components` accepts only components in `simulator/battery/migrate.py::_COMPOSITION_COMPONENTS`, which does not include the printed gas species, and `simulator/battery/migrate.py::_mole_percent_closure_error` requires the values to close to 100 percent. The source mixes vol-% major gases with volume-ppm dopants, so hand-normalizing or assigning a custom identity basis would invent a schema/science choice. No migrator change was made, per the extraction contract.

## Extracted scope

- Table 1.5: 8 furnace rows, 19 non-dash concentration cells (6 S, 7 Na, 6 K), in volume ppm. The source attributes these to industrial reports and literature; dashes are omitted. The source says these are assumed gaseous elements. No total pressure is printed, so they are not partial pressures.
- Table 2.4: 12 first/end-port and 7 along-furnace carryover values, 19 total, in g/24 h, attributed to Busby and Sengelow [34]. The accompanying deposit chemistry rows are not activities and are listed under remaining material.
- Table 3.8 plus section 3.3.2: gas-I composition is retained as a condition; the explicit measured Na2SO4 dewpoint is 1240 K. Cases II–VI have figure-based outcomes only.
- Table 4.2: four measured Na2SO4 maximum deposition rates (0.38, 1.04, 1.17, 1.60 mg/cm²·h) and four separate CFBL outputs (0.28, 0.70, 1.04, 1.40). The CFBL values are quoted-attributed because its full model inputs are not stored.
- Table 4.3: four measured dewpoints (1120, 1190, 1237, 1263 K) and separate CFBL series at supersaturation 1 and 10. The calculated series are quoted-attributed, not merged with the measurements.
- Section 4.5.5: measured K2SO4 maximum 2.9 mg/m²·s at 1100 K. The thesis states: "The maximum deposition rate at approximately 1280 K for the theoretical case is 30% higher than the measured maximum rate at 1100 K." The Na2SO4 comparison is 2.3 mg/m²·s; it is classified author_estimate because PDF p160 identifies it as the author's theoretically calculated result, but the calculation inputs are not stored and no derivation lineage can be claimed. The printed sentence is: "The theoretically calculated maximum deposition rate at 1200 K is 2.3 mg/m²·s, for the experimental case this value is approximately 20% lower." The experimental value is not printed or inferred. The Na2SO4 comparator's separate gas mixture and maximum-rate temperature are not printed.
- Section 4.5.6: measured PbO maximum 3.0 mg/m²·s at approximately 1000 K and measured onset at 1090 K (PDF p168, printed p159).
- Section 2.4 literature summaries: Jackson and Duffin [26] NaCl dewpoints of 910 K at 34 mg/m³ and 1038 K at 1700 mg/m³; Kohl et al. [33] Na2SO4 dewpoint 1260 K with the printed sea-salt, oxygen, and water-vapour conditions. These remain quoted-attributed.

Gas and apparatus conditions are attached only to the rows they support. No total pressure, absolute fO2, numeric paddle temperature, individual exposure time, or probe area was inferred. Table 4.4's standard channel duration is retained as a reported range, not substituted for an individual run time. Table 4.7 and Table 4.8 gas compositions and Table 4.1 base gas are retained as located atmosphere conditions.

Table 1.6 links furnace types and glass compositions to the Table 1.5 furnace labels. The source calls these glass compositions and does not state they are sampling-time melt compositions. They are not copied as flue-gas composition or bound as melt composition; if the owner treats the reported glass composition as the melt condition, that source-specific interpretation needs a ruling.

## Schema review

- `tools/validate_literature_extracts.py::OBSERVATION_TYPES` includes the used concentration, rate, and transition observation types, so rows remain typed.
- `simulator/battery/enums.py::Quantity`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `simulator/battery/migrate.py::TYPE_QUANTITY` provide no exact quantity for elemental flue-gas volume-ppm concentration, paddle carryover in g/24 h, or wall deposition mass per area per time. `p_partial` would assert an absolute partial pressure despite no printed total pressure; `equilibrium_phase_composition` describes a phase composition rather than the printed elemental concentration. `wall_deposit_mass` has no time denominator, `mass_loss_areal_density` has no time denominator, and `mass_loss_rate` is sample mass loss rather than deposition. These rows use descriptive non-token quantity labels with explicit schema-gap notes to prevent type inference.
- `simulator/battery/enums.py::Quantity.ACTIVITY` is available, but the thesis reports no measured Na2O or K2O activities. Table 2.2 is a calculated saturation-pressure relation and is excluded as model output, not relabelled as measurement.
- The mapped phase token for collected solids is `cr`, which is accepted by `simulator/battery/migrate.py::PHASE_MAP` and by the MinerU comparator's solid-phase check. The migration has no phase-unknown observations.
- Existing `equilibrium_phase_composition` examples, including `data/literature/extracts/park-2021-vanadium-thermodynamics-thesis.yaml`, do not represent the exact measured elemental volume-ppm or areal deposition-rate quantities here.

## Cell audit and locator checks

The sibling `.cells.csv` records 134 audited source cells, including the numeric condition cells needed to interpret the measured rows. Tables 1.5, 2.4, 4.2, and 4.3 have printed row counts of 8, 12 plus 7, 4, and 4, respectively. Every target cell and each table's row/column assignment was checked against its rendered page. Table 2.4's OCR uses a middle dot in its decimals; all 19 carryover values were confirmed from the render and normalized only in the scratch draft for comparison. The page-70 prose render confirms both Jackson and Duffin values and the printed page 61 locator; page 71 confirms Kohl's values and printed page 62. Page 112 confirms the 1240 K passage and printed page 103. Page 168 confirms the PbO onset and printed page 159.

`tools/mineru_table_draft.py compare` reports 46 matches, 3 expected prose-only no-matches, zero mismatches, and zero locator errors. The three no-matches are the explicit prose deposition maxima on pages 167 and 169; there is no corresponding MinerU table cell. Table 4.3 dewpoint values are stored as `T_K`, not numeric `value` cells handled by the comparator, and were checked cell-by-cell against the page-139 render and pinned in the focused test. The focused test also pins the row counts, independent source fidelity samples, and a mutation of the 1.60 mg/cm²·h value to 1.61 as a failing fidelity check.

## Remaining material

- Table 1.3 typical exhaust compositions, Table 1.4 fuel impurities, Table 1.6 furnace-glass compositions, and Table 1.7 particulate dust emissions are not measured Na/K/S vapour concentrations or wall-deposition rates. Table 1.7 reports dust, not vapour.
- Table 2.1 lists gas species; Table 2.2 gives model-derived vapour-pressure relations. Neither is a measured vapour-pressure table. Table 2.4's deposit chemistry rows are not activities and remain unextracted.
- Tables 3.1–3.7 are typical or calculated exhaust-gas compositions. Table 3.8 cases II–VI are input mixtures for figure-only experimental outcomes; their plotted results are not digitized.
- Tables 4.5 and 4.6 give gas mixtures for deposition curves without tabulated measured rates. The figure-only experimental curves in Figures 4.14–4.19 remain candidates. Figures 4.20 and 4.21 also contain measured K2SO4 and PbO curves; only the explicit prose maxima/onset were transcribed.
- Tables 5.2–5.9 describe modelled furnace/regenerator cases and remain outside this measured-data pass; their model curves and values are not extracted.

## Migration and store expectation

The isolated real migrator produced 66 observations, zero hard issues, 49 unavailable quantities, and 17 point-valued quantities. All phases are known. The three dewpoint series above still violate required identity-composition binding despite zero hard validation issues; this is why the task is stopped for a migrator change. No generated store was committed. If the extract is later admitted and regenerated, the expected delta is 66 observations (49 unavailable quantity rows and 17 point-valued rows).

Verification: `tests/test_beerkens_1986_deposits.py` passed (1 test), including the render-value mutation check. `tests/test_yaml_flow_scalar_commas.py` passed (17 tests). `tests/test_literature_extracts.py` passed 887 cases in bounded selections; `test_repo_extracts_validate_green`, `test_coverage_pending_stubs_not_found`, and `test_pilot_extract_count_exact_and_merge_smoke` are UNVERIFIED-local because each capped run ended without a pytest summary. The remaining selections passed; the full file was not reported as a single green run.
