# Hashimoto (1983) condition recovery evidence

## Isolated migration, before and after

| Metric | Before | After |
|---|---:|---:|
| Observations | 666 | 666 |
| Hard issues | 0 | 0 |
| Unavailable values | 16 | 16 |
| Queue entries | 110 | 110 |
| `residue_component_composition` rows | 648 | 648 |
| Complete identities for that quantity | 0 | 0 |
| Missing required `fO2_Pa` axis | 648 | 648 |

The paper says the experiments were unbuffered with respect to P_O2 and gives vacuum only as an example; it prints no numeric run fO2 or pO2 (PDF p17, published p127). The identity therefore remains unknown on that axis. Table 3's 216 migrated run-linked observations have zero quantity-unknown and zero phase-unknown identities. The pre-existing extract has 18 quantity-unknown and 3 phase-unknown observations outside this Table 3 target on both sides of the migration.

Three migrated Table 3 rows from sample C3 (2), run 17C3 (PDF p6, published p116), pin the required identity axes as follows:

| Component row | Composition | Total pressure | fO2 | Temperature |
|---|---|---|---|---|
| SiO2, 39.09 wt% | value | approx. 10^-4 Torr = 0.01333223684210526315789473684 Pa | unknown: no numeric run fO2/pO2 | 1973.15 K |
| Al2O3, 3.57 wt% | value | approx. 10^-4 Torr = 0.01333223684210526315789473684 Pa | unknown: no numeric run fO2/pO2 | 1973.15 K |
| FeO, 27.92 wt% | value | approx. 10^-4 Torr = 0.01333223684210526315789473684 Pa | unknown: no numeric run fO2/pO2 | 1973.15 K |

## Score sample

Scored ten matched Table 3 rows through `simulator/battery/score.py::score_store` against full Hashimoto source context. OpenIMCC's declared 66ec4a3 tree hash was `308672269d6e6090023f4a86c4fc0d83c4c24688` (prefix `308672269d6e`). Both sides returned 120 scored references and 120 engine candidates; all ten sampled engine executions were produced. Before recovery, all ten residuals were `no_band`, refusal reason `decision_rule_missing`, with detail `unparseable_printed_uncertainty` from the printed balance accuracy notice. After recovery, all ten residuals were refused as `identity_unknown`, with the missing axis `fO2_Pa`. The exact ten matched rows and per-row residuals are in `kems-015-hashimoto-1983.score-sample.csv`. No scoring thresholds or values were tuned.

## Expected store delta

No battery-store regeneration is committed. The isolated migration retains 666 observations and adds no hard issues; regeneration should update the existing Table 3 run-linked observation conditions and identity bindings without changing the observation count. Numeric fO2 remains unknown as printed.
