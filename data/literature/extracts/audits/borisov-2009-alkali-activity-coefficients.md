# Borisov (2009) alkali activity audit

## Reconciliation

Green's extract is the base. Batch4's Table 4 and Table 5 Na2O readings agree
with Green's page-render reads at the printed precision, so no duplicate
observations were added. Green keeps the observation IDs and stores the three
Table 4 standard melts and the Table 5 CSA6 reference melt in reference-state
context rather than as independent coefficient rows.

| Batch4 rows | Green representation | Page-render check | Decision |
|---|---|---|---|
| Table 4: 12 Na2O coefficients; 3 series standards | 12 Na2O observations and 3 `reference_rows` under `borisov_2009_table4_reference_melts` | PDF p. 6 / printed p. 584; gamma and four printed mole fractions match exactly | Keep Green IDs and values; keep the standards in context |
| Table 5: 3 Na2O coefficients; CSA6 reference | 3 Na2O observations and `borisov_2009_table5_csa6_reference_melt` | PDF p. 8 / printed p. 586; gamma and composition match exactly | Keep Green IDs and values; keep CSA6 in context |

Batch4's exact regression pins remain in the focused test: DARNK-36 has
`gamma = 0.868` and `X_SiO2 = 0.477`; DAS200RNK-39 has `gamma = 0.580` and
`X_SiO2 = 0.747`; CSA25 has `gamma = 0.881` and `X_Al2O3 = 0.164`. The Green
test independently pins all Table 2–5 cell values and the Table 4 and 5 row
counts. Across Na2O, the 15 coefficient observations plus four reference rows
preserve all 19 printed entries without duplicate facts.

## Evidence and migration

Tables 4 and 5 report relative activity coefficients referenced to multicomponent
melts. Green retains those coefficients as `activity_coefficient` values and
keeps the reference phase unknown; the extract does not turn them into absolute
Na2O activities. The abstract prints total pressure as 1 atm. Table 2 supplies
the temperature and positive `−log fO2` for each series. These conditions,
their locators, and the Table 3 sample compositions reach the migrated rows.

The isolated migration emits 15 Na2O coefficient observations with 0 hard
issues. The full source test pins 169 observations, including 45 activity
coefficients across Na2O, K2O, and Rb2O. Mutation checks reject changed gamma
and cell values.

Expected store delta relative to green: 0 observations. The Green extraction
already contains these rows. No battery-store regeneration is committed.
