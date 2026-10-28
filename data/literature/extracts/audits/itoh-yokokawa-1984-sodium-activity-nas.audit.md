# Itoh and Yokokawa (1984) activity extraction audit

## Reconciliation

Green retained Eq. (5)'s printed `log10(alpha*_Na2O) = -9.0` as context and did
not compute a linear activity. Batch4's reviewed extraction adds the derived
linear activity `1e-9` and a typed-refusal support row for the exact printed
log token. The Green context row is replaced by that linked pair, so the
printed token appears once and the derivation input remains addressable.

| Green row | Batch4 rows | Render evidence | Decision |
|---|---|---|---|
| `itoh_yokokawa_1984_equation_5_log_activity_sodium_oxide` | `itoh_yokokawa_1984_eq5_printed_log_input` and `itoh_yokokawa_1984_eq5_absolute_activity` | PDF p. 4 / printed p. 882, Eq. (5) prints `-9.0` and identifies pure solid Na2O | Keep the exact printed log as a typed-refusal support row; retain the linked `1e-9` reduction and propagated rounding half-width |

The render gives `T = 1300 K` and `X_Na2O = 0.33`; Eq. (3) gives
`X_SiO2 = 0.67`. The linear activity is the direct base-10 reduction of the
printed log value. Its half-width from one-decimal rounding is
`1.1512925465e-10`; the confidence level is not stated.

## Remaining source content

Table 1 contains cell-EMF readings for 27 listed compositions against the
40Na2O·60SiO2 melt. The schema has no measured cell-EMF quantity, so both raw
tables remain context and are not converted to activity. Table 2 retains all
28 rows of relative partial-molar quantities at 1400 K, including its two
blank composition cells. Its printed ΔS unit is retained as kJ/mol; row closure
is consistent only when interpreted as J/(mol·K). The Table 2 reduction from
Table 1 remains pending a corpus table asset (controller, t-1382). Figures 1–5
remain metadata-only; no plotted values were added.

## Migration and conditions

The isolated migration emits two source rows, one admitted activity point and
one rejected typed-refusal support row, with 0 hard issues. Both rows carry
temperature and composition through located `point_conditions`; the admitted
activity row also retains unknown oxygen fugacity and pressure with locators.
The tests pin the exact `-9.0`, `1e-9`, uncertainty, standard state, method,
composition, and page locations. Mutation checks prove that changing the
printed log value is detected.

Expected store delta on regeneration: +1 admitted Na2O activity observation
at 1300 K and XNa2O = 0.33; the support row is not admitted. No battery-store
regeneration is committed.
