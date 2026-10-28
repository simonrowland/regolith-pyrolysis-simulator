# Kurka, Kupčák & Leško (1989) extraction audit

## Scope and transcription

Table 1 is the measured dataset: **16 rows** (3 at 1400 °C, 4 at 1500 °C, 9 at 1600 °C). All rows report equilibrium compositions in the CaO–FeO–Fe₂O₃–SiO₂ system. Table 2 is excluded because its six interaction energies are attributed to reference [14]. Figures 2 and 3 are figure-read candidates; their plotted points were not digitized. Equation (2) is a regression against the same 16 rows, not independent measurements. Equation (3)'s rough oxidation-enthalpy slope values are outside the measured redox table.

MinerU's Table 1 block and `pdftotext-layout.txt` were the transcription sources. The table QA screen reported numeric agreement 0.988 for 83 numeric tokens and flagged one mismatch. I checked the complete table layout and every row/column assignment against the PDF page 4 render (printed page 636); the render confirmed the transcription below, including all 16 row labels and the temperature groupings. The discrepancy did not leave any numeric cell unresolved. No OCR redo was needed. Table 2's OCR minus flags do not affect the extracted dataset because that table is excluded.

### Table 1 full-cell audit

Every source cell below is confirmed by method **(b), rendered page 4**. This settles the QA mismatch and checks each row/column assignment. The rendered column order is Point, t/°C, x(CaO), x(SiO₂), x(FeO), x(Fe₂O₃). `T_C` repeats the printed merged temperature for each following row in that group; the paper leaves the repeated temperature cells blank.

| Table 1 data row | Point | T_C | x(CaO) | x(SiO₂) | x(FeO) | x(Fe₂O₃) | Audit |
|---:|---:|---:|---:|---:|---:|---:|:---|
| 1 | 1 | 1400 | 0.523 | 0.328 | 0.019 | 0.130 | (b), p. 4 render |
| 2 | 2 | 1400 | 0.388 | 0.489 | 0.036 | 0.086 | (b), p. 4 render |
| 3 | 5 | 1400 | 0.451 | 0.412 | 0.031 | 0.105 | (b), p. 4 render |
| 4 | 1 | 1500 | 0.521 | 0.326 | 0.027 | 0.125 | (b), p. 4 render |
| 5 | 2 | 1500 | 0.386 | 0.486 | 0.050 | 0.078 | (b), p. 4 render |
| 6 | 3 | 1500 | 0.327 | 0.560 | 0.047 | 0.065 | (b), p. 4 render |
| 7 | 5 | 1500 | 0.435 | 0.420 | 0.044 | 0.101 | (b), p. 4 render |
| 8 | 1 | 1600 | 0.518 | 0.325 | 0.036 | 0.120 | (b), p. 4 render |
| 9 | 2 | 1600 | 0.383 | 0.482 | 0.063 | 0.071 | (b), p. 4 render |
| 10 | 3 | 1600 | 0.325 | 0.555 | 0.062 | 0.057 | (b), p. 4 render |
| 11 | 4 | 1600 | 0.451 | 0.470 | 0.031 | 0.049 | (b), p. 4 render |
| 12 | 5 | 1600 | 0.431 | 0.417 | 0.060 | 0.092 | (b), p. 4 render |
| 13 | 6 | 1600 | 0.400 | 0.380 | 0.085 | 0.135 | (b), p. 4 render |
| 14 | 7 | 1600 | 0.403 | 0.583 | 0.006 | 0.008 | (b), p. 4 render |
| 15 | 8 | 1600 | 0.483 | 0.503 | 0.005 | 0.009 | (b), p. 4 render |
| 16 | 9 | 1600 | 0.538 | 0.452 | 0.003 | 0.008 | (b), p. 4 render |

The rendered Table 1 has 16 body rows. The last three 1600 °C row labels are 7, 8 and 9; no values were inferred from Fig. 1's point positions.

## Redox reduction and evidence

The table reports FeO and Fe₂O₃ oxide formula mole fractions. Per controller ruling d-119, the stored redox value is ferric iron as a fraction of total elemental Fe:

```text
n(Fe3+) = 2 x(Fe2O3)
n(Fe2+) = x(FeO)
f3 = 2 x(Fe2O3) / (2 x(Fe2O3) + x(FeO))
f2 = 1 - f3
```

The result is `measured_reduced`: the oxide cells are the measured inputs from Table 1, and every redox row's `derived_from` and derivation inputs identify its corresponding FeO and Fe₂O₃ observation rows. The reduced result carries `identity.valence_basis` as Fe³⁺ over total Fe, fraction form. For row 1, `2(0.130)/(2(0.130)+0.019) = 0.93189964…`, stored as 0.931900; Fe²⁺ is 0.068100. This is a stoichiometric reduction of the measured cells, not a regression output.

The paper defines `j = x(Fe2O3)/(x(Fe2O3) + x(FeO))` on printed PDF page 637 (PDF page 5, equation 2 definition). Its oxide-formula fraction is not the elemental ferric fraction. The computed `j` is retained beside each redox value with that exact definition, the oxide-mole-fraction basis and a note that the paper defines the form but does not separately tabulate row-specific j values. For row 1, the printed-cell calculation is `0.130/(0.130+0.019) = 0.872483…`. No uncertainty is propagated; the source gives no per-row iron uncertainty.

## Conditions, method and phase

- PDF page 2, printed page 634, defines `p° = 101325 Pa`. PDF page 4, printed page 636, states `p(O2)/p° = 0.21`. Their product gives the absolute oxygen partial pressure, 21278.25 Pa, carried into identity through the migrator's `pO2_Pa` path. The ratio is prose on page 636, not Table 1.
- PDF page 3, printed page 635, states the experiments were performed in air, with 20 g of mixture in a platinum crucible in an SF 2 furnace; after approximately 60–90 min to reach equilibrium, each melt was held at its working temperature for 2 h. The stated temperature accuracy is ±2 °C.
- The total furnace pressure is not reported. `total_pressure_Pa` is unknown on the experiment and every observation. The printed standard reference pressure p° is not treated as the experiment's total pressure.
- The samples were quenched in water, dried, ground and homogenized before chemical analysis (PDF page 3). The measured state is stored as the reported quenched melt (phase token l), not glass. The source does not state that quenching preserves or alters the ferric/ferrous ratio, so `quench_statement.preservation` is `not_stated` at page 3; no quench direction is inferred.
- The paper states that iron redox components were determined by the bichromate method with N-phenylanthranilic acid indicator. Other components were measured by atomic absorption spectroscopy. The redox ratio is reduced from those measurements; no row-level uncertainty is printed.

## Closed-token and schema check

No schema gap was found for the measured oxide mole fractions or elemental Fe fraction. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` (lines 79–99) admits `concentration_series` and `equilibrium_phase_composition`. The closed `simulator/battery/enums.py::Quantity` enum includes `FE3_FE2_RATIO` (line 81), `VALENCE_RATIO` (line 82), and `EQUILIBRIUM_PHASE_COMPOSITION` (line 108), but no basicity-index member. `simulator/battery/migrate.py::QUANTITY_ALIASES` includes the Fe ratio alias and average-oxidation-state valence token. `PHASE_BASIS_TOKENS` in `simulator/battery/enums.py` line 663 includes `mole_fraction`. Existing `park-2021-vanadium-thermodynamics-thesis.yaml` rows (lines 253–260) use the same `equilibrium_phase_composition` quantity, `mole_fraction` basis, and `measured_direct` evidence. `PHASE_MAP` in `simulator/battery/migrate.py` maps `l` to `Phase.L`; `PhaseRole.MELT` in `simulator/battery/enums.py` identifies the reported melt role.

The basicity index `k = x(CaO)/x(SiO2)` was checked against the closed quantity candidates: `TYPE_QUANTITY` (`migrate.py:435–444`), `QUANTITY_ALIASES` (`migrate.py:453 onward`), and the `Quantity` enum members above contain no exact basicity-index token. The source's measured component mole fractions do have the existing phase-composition type, but k is a derived index rather than a separate phase composition; its row values remain unscored context with a `schema_gap` note and no invented quantity token. No schema or migrator change was made.

## Isolated migration

The real `Migrator` ran on a temporary directory containing only this extract. It emitted 48 observations: 16 Fe valence-fraction rows and 32 measured FeO / Fe₂O₃ component rows. All 48 have mapped quantity and phase; all 16 redox rows have measured-reduced evidence, both parent rows, T, row composition and fO₂_Pa. The exact migrated quantity set is 16 `valence_ratio` and 32 `equilibrium_phase_composition`; there are no quantity-unknown or phase-unknown observations. The result has zero queue entries and zero registry issues. There are 48 `identity_incomplete` validation entries, one per observation, all exclusively for `total_pressure_Pa`; that quantity is not printed by the paper and is correctly unknown. There are zero unavailable values. For the reviewed composition issue, oxide rows use the existing `composition_mol` path, which preserves the source map; the focused migration test pins row 2 (sum 0.999) and row 16 (sum 1.001) on both the oxide and valence identities. The test also pins the absence of any reported total pressure and checks identity axes for three temperatures.

The table compare CSV is committed beside this report. It reports 16 mismatches because its numeric-point matcher compares each derived Fe-total fraction to Table 1 cells, where that output is not printed. These are expected non-source values, not unresolved cell disagreements: their two input cells per row are table-audited above and are individually retained as measured observations with lineage. The compare output reports no locator errors or unverified points.
