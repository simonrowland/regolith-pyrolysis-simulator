# Melt-activity benchmark report

## Evidence boundary

Literal SF04 basalt empirical points: **0**. The scored experimental population is six Hastie-1981 KEMS gas-pressure points, six Richter-2007 Type-B CAI-like CMAS gamma targets, 12 Tsaplin-2000 Na2O-SiO2 a(SiO2) targets, and 28 Yamaguchi-1983 Na2O-SiO2 liquid-reference a(SiO2) targets. SF04 workbook pressures are scored only as an explicitly non-empirical regression anchor.

Residual convention: `log10(predicted/measured)`; positive means overprediction. No coefficient tuning was performed.

## Per-species comparison

| Species | Observable | Engine | n | RMSE (dex) | Median residual | ok | OOD | crash | refused | not converged | not attempted | observable unavailable | unavailable |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Al | activity | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 62 | 0 |
| Al | activity | imcc-ext | 62 | 1.211 | -0.324 | 62 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Al | activity | imcc-published | 62 | 1.211 | -0.324 | 62 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Al | activity | internal_analytic | 62 | 1.121 | -0.8134 | 62 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Al | activity | thermoengine | 34 | 1.398 | -0.1375 | 34 | 10 | 0 | 18 | 0 | 0 | 0 | 0 |
| Ca | activity | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 99 | 0 |
| Ca | activity | imcc-ext | 99 | 0.8344 | -0.6161 | 99 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Ca | activity | imcc-published | 99 | 0.8344 | -0.6151 | 99 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Ca | activity | internal_analytic | 99 | 0.4597 | 0.1005 | 99 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Ca | activity | thermoengine | 0 | — | — | 0 | 10 | 0 | 31 | 0 | 0 | 58 | 0 |
| K | partial_pressure | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| K | partial_pressure | imcc-ext | 3 | 0.933 | -0.9496 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| K | partial_pressure | imcc-published | 3 | 0.9339 | -0.9505 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| K | partial_pressure | internal_analytic | 3 | 0.373 | 0.3825 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| K | partial_pressure | thermoengine | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| K | partial_pressure | vaporock | 2 | 0.07991 | -0.07488 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 0 |
| Mg | activity | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 45 | 0 |
| Mg | activity | imcc-ext | 45 | 1.09 | -1.009 | 45 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Mg | activity | imcc-published | 45 | 1.099 | -1.021 | 45 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Mg | activity | internal_analytic | 45 | 0.3062 | 0.01139 | 45 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Mg | activity | thermoengine | 0 | — | — | 0 | 1 | 0 | 16 | 0 | 0 | 28 | 0 |
| Mg | activity_coefficient | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| Mg | activity_coefficient | imcc-ext | 3 | 0.1182 | 0.05689 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Mg | activity_coefficient | imcc-published | 3 | 0.1103 | 0.05021 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Mg | activity_coefficient | internal_analytic | 3 | 0.9525 | 0.9217 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Mg | activity_coefficient | thermoengine | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| Mg | evaporation_flux | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 |
| Mg | evaporation_flux | imcc-ext | 0 | — | — | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| Mg | evaporation_flux | imcc-published | 0 | — | — | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| Mg | evaporation_flux | internal_analytic | 0 | — | — | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| Mg | evaporation_flux | thermoengine | 0 | — | — | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| Mg | evaporation_flux | vaporock | 0 | — | — | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 |
| Na | activity | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 54 | 0 |
| Na | activity | imcc-ext | 0 | — | — | 0 | 54 | 0 | 0 | 0 | 0 | 0 | 0 |
| Na | activity | imcc-published | 0 | — | — | 0 | 54 | 0 | 0 | 0 | 0 | 0 | 0 |
| Na | activity | internal_analytic | 0 | — | — | 54 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Na | activity | thermoengine | 0 | — | — | 0 | 1 | 0 | 17 | 0 | 0 | 36 | 0 |
| SiO | activity | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 126 | 0 |
| SiO | activity | imcc-ext | 86 | 0.4467 | -0.3158 | 86 | 40 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | activity | imcc-published | 86 | 0.4466 | -0.3158 | 86 | 40 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | activity | internal_analytic | 126 | 1.1 | 0.239 | 126 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | activity | thermoengine | 74 | 0.5834 | -0.2833 | 74 | 11 | 0 | 41 | 0 | 0 | 0 | 0 |
| SiO | activity_coefficient | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| SiO | activity_coefficient | imcc-ext | 3 | 0.5754 | 0.5414 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | activity_coefficient | imcc-published | 3 | 0.5769 | 0.5429 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | activity_coefficient | internal_analytic | 3 | 0.9451 | 0.9251 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | activity_coefficient | thermoengine | 3 | 0.7269 | 0.7028 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | partial_pressure | alphamelts | 0 | — | — | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 |
| SiO | partial_pressure | imcc-ext | 3 | 0.2309 | -0.2496 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | partial_pressure | imcc-published | 3 | 0.2307 | -0.2495 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | partial_pressure | internal_analytic | 3 | 0.2563 | -0.2757 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | partial_pressure | thermoengine | 3 | 0.2508 | -0.2692 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| SiO | partial_pressure | vaporock | 3 | 0.2498 | -0.268 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

## Paired engine decisions

Every pair of engines with both-ok residuals on the same scored point enters this comparison. Low-n verdicts are annotated in the verdict sentence, not suppressed.

| Species | Observable | Engine A | Engine B | Paired n | A RMSE (dex) | B RMSE (dex) | A closer | B closer | ties | Decision |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| Al | activity | imcc-ext | imcc-published | 62 | 1.211 | 1.211 | 2 | 13 | 47 | imcc-published |
| Al | activity | imcc-ext | internal_analytic | 62 | 1.211 | 1.121 | 41 | 21 | 0 | internal_analytic |
| Al | activity | imcc-ext | thermoengine | 34 | 1.442 | 1.398 | 14 | 20 | 0 | thermoengine |
| Al | activity | imcc-published | internal_analytic | 62 | 1.211 | 1.121 | 41 | 21 | 0 | internal_analytic |
| Al | activity | imcc-published | thermoengine | 34 | 1.441 | 1.398 | 15 | 19 | 0 | thermoengine |
| Al | activity | internal_analytic | thermoengine | 34 | 1.241 | 1.398 | 10 | 24 | 0 | internal_analytic |
| Ca | activity | imcc-ext | imcc-published | 99 | 0.8344 | 0.8344 | 7 | 33 | 59 | imcc-ext |
| Ca | activity | imcc-ext | internal_analytic | 99 | 0.8344 | 0.4597 | 26 | 73 | 0 | internal_analytic |
| Ca | activity | imcc-published | internal_analytic | 99 | 0.8344 | 0.4597 | 26 | 73 | 0 | internal_analytic |
| K | partial_pressure | imcc-ext | imcc-published | 3 | 0.933 | 0.9339 | 3 | 0 | 0 | imcc-ext |
| K | partial_pressure | imcc-ext | internal_analytic | 3 | 0.933 | 0.373 | 0 | 3 | 0 | internal_analytic |
| K | partial_pressure | imcc-ext | vaporock | 2 | 0.9221 | 0.07991 | 0 | 2 | 0 | vaporock |
| K | partial_pressure | imcc-published | internal_analytic | 3 | 0.9339 | 0.373 | 0 | 3 | 0 | internal_analytic |
| K | partial_pressure | imcc-published | vaporock | 2 | 0.923 | 0.07991 | 0 | 2 | 0 | vaporock |
| K | partial_pressure | internal_analytic | vaporock | 2 | 0.4116 | 0.07991 | 0 | 2 | 0 | vaporock |
| Mg | activity | imcc-ext | imcc-published | 45 | 1.09 | 1.099 | 45 | 0 | 0 | imcc-ext |
| Mg | activity | imcc-ext | internal_analytic | 45 | 1.09 | 0.3062 | 1 | 44 | 0 | internal_analytic |
| Mg | activity | imcc-published | internal_analytic | 45 | 1.099 | 0.3062 | 1 | 44 | 0 | internal_analytic |
| Mg | activity_coefficient | imcc-ext | imcc-published | 3 | 0.1182 | 0.1103 | 0 | 3 | 0 | imcc-published |
| Mg | activity_coefficient | imcc-ext | internal_analytic | 3 | 0.1182 | 0.9525 | 3 | 0 | 0 | imcc-ext |
| Mg | activity_coefficient | imcc-published | internal_analytic | 3 | 0.1103 | 0.9525 | 3 | 0 | 0 | imcc-published |
| SiO | activity | imcc-ext | imcc-published | 86 | 0.4467 | 0.4466 | 11 | 16 | 59 | imcc-published |
| SiO | activity | imcc-ext | internal_analytic | 86 | 0.4467 | 0.5329 | 31 | 55 | 0 | imcc-ext |
| SiO | activity | imcc-ext | thermoengine | 48 | 0.3571 | 0.3748 | 27 | 21 | 0 | imcc-ext |
| SiO | activity | imcc-published | internal_analytic | 86 | 0.4466 | 0.5329 | 31 | 55 | 0 | imcc-published |
| SiO | activity | imcc-published | thermoengine | 48 | 0.3564 | 0.3748 | 27 | 21 | 0 | imcc-published |
| SiO | activity | internal_analytic | thermoengine | 74 | 0.3844 | 0.5834 | 57 | 17 | 0 | internal_analytic |
| SiO | activity_coefficient | imcc-ext | imcc-published | 3 | 0.5754 | 0.5769 | 2 | 1 | 0 | imcc-ext |
| SiO | activity_coefficient | imcc-ext | internal_analytic | 3 | 0.5754 | 0.9451 | 3 | 0 | 0 | imcc-ext |
| SiO | activity_coefficient | imcc-ext | thermoengine | 3 | 0.5754 | 0.7269 | 3 | 0 | 0 | imcc-ext |
| SiO | activity_coefficient | imcc-published | internal_analytic | 3 | 0.5769 | 0.9451 | 3 | 0 | 0 | imcc-published |
| SiO | activity_coefficient | imcc-published | thermoengine | 3 | 0.5769 | 0.7269 | 3 | 0 | 0 | imcc-published |
| SiO | activity_coefficient | internal_analytic | thermoengine | 3 | 0.9451 | 0.7269 | 0 | 3 | 0 | thermoengine |
| SiO | partial_pressure | imcc-ext | imcc-published | 3 | 0.2309 | 0.2307 | 0 | 3 | 0 | imcc-published |
| SiO | partial_pressure | imcc-ext | internal_analytic | 3 | 0.2309 | 0.2563 | 3 | 0 | 0 | imcc-ext |
| SiO | partial_pressure | imcc-ext | thermoengine | 3 | 0.2309 | 0.2508 | 3 | 0 | 0 | imcc-ext |
| SiO | partial_pressure | imcc-ext | vaporock | 3 | 0.2309 | 0.2498 | 3 | 0 | 0 | imcc-ext |
| SiO | partial_pressure | imcc-published | internal_analytic | 3 | 0.2307 | 0.2563 | 3 | 0 | 0 | imcc-published |
| SiO | partial_pressure | imcc-published | thermoengine | 3 | 0.2307 | 0.2508 | 3 | 0 | 0 | imcc-published |
| SiO | partial_pressure | imcc-published | vaporock | 3 | 0.2307 | 0.2498 | 3 | 0 | 0 | imcc-published |
| SiO | partial_pressure | internal_analytic | thermoengine | 3 | 0.2563 | 0.2508 | 0 | 3 | 0 | thermoengine |
| SiO | partial_pressure | internal_analytic | vaporock | 3 | 0.2563 | 0.2498 | 0 | 3 | 0 | vaporock |
| SiO | partial_pressure | thermoengine | vaporock | 3 | 0.2508 | 0.2498 | 0 | 3 | 0 | vaporock |

Decision verdict: `imcc-ext` vs `imcc-published`: mixed by species/observable (4 imcc-ext, 4 imcc-published, 0 tied; n=62,99,3,45,3,86,3,3); `imcc-ext` vs `internal_analytic`: mixed by species/observable (4 imcc-ext, 4 internal_analytic, 0 tied; n=62,99,3,45,3,86,3,3); `imcc-ext` vs `thermoengine`: mixed by species/observable (3 imcc-ext, 1 thermoengine, 0 tied; n=34,48,3,3); `imcc-ext` vs `vaporock`: mixed by species/observable (1 imcc-ext, 1 vaporock, 0 tied; n=2,3); `imcc-published` vs `internal_analytic`: mixed by species/observable (4 imcc-published, 4 internal_analytic, 0 tied; n=62,99,3,45,3,86,3,3); `imcc-published` vs `thermoengine`: mixed by species/observable (3 imcc-published, 1 thermoengine, 0 tied; n=34,48,3,3); `imcc-published` vs `vaporock`: mixed by species/observable (1 imcc-published, 1 vaporock, 0 tied; n=2,3); `internal_analytic` vs `thermoengine`: mixed by species/observable (2 internal_analytic, 2 thermoengine, 0 tied; n=34,74,3,3); `internal_analytic` vs `vaporock`: vaporock better on every comparable group (0 internal_analytic, 2 vaporock, 0 tied; n=2,3); `thermoengine` vs `vaporock`: vaporock better on every comparable group (0 thermoengine, 1 vaporock, 0 tied; n=3).

## IMCC extrapolated tier (computed-and-marked, not validated)

These rows are a second IMCC pass with `allow_extrapolation` and `allow_out_of_envelope` enabled. They are category-2 out-of-domain physics: compute and mark. They are **not** a validated domain widening and do **not** certify. Residuals live only in `informational_residual_dex` and do not enter the scored RMSE table or the decision column.

Marks are orthogonal and both appear on every row: `extrapolated` is the temperature-domain mark (`ImccResult.extrapolated`); `envelope_status` is the X_Me2O ≤ 0.5 composition test.

### Informational RMSE by composition envelope

| Engine | Envelope | n (tier) | n (scored informational) | Informational RMSE (dex) |
|---|---|---:|---:|---:|
| imcc-ext | inside | 64 | 27 | 0.1103 |
| imcc-ext | outside_validated | 30 | 13 | 3.481 |
| imcc-published | inside | 64 | 27 | 0.1028 |
| imcc-published | outside_validated | 30 | 13 | 3.502 |

### Per-row computed-and-marked results

| point_id | engine | T_K | species | observable | extrapolated | envelope_status | informational residual (dex) | prediction | measured |
|---|---|---:|---|---|---|---|---:|---:|---:|
| tsaplin2000_a_sio2_x0805_1473 | imcc-published | 1473 | SiO | activity | True | inside | -0.09309 | 0.6941 | 0.86 |
| tsaplin2000_a_sio2_x0805_1473 | imcc-ext | 1473 | SiO | activity | True | inside | -0.09221 | 0.6955 | 0.86 |
| tsaplin2000_a_na2o_x0805_1473 | imcc-published | 1473 | Na | activity | True | inside | -0.1506 | 5.083e-10 | 7.19e-10 |
| tsaplin2000_a_na2o_x0805_1473 | imcc-ext | 1473 | Na | activity | True | inside | -0.09571 | 5.768e-10 | 7.19e-10 |
| tsaplin2000_a_sio2_x0753_1273 | imcc-published | 1273 | SiO | activity | True | inside | -0.1335 | 0.5404 | 0.735 |
| tsaplin2000_a_sio2_x0753_1273 | imcc-ext | 1273 | SiO | activity | True | inside | -0.1251 | 0.5511 | 0.735 |
| tsaplin2000_a_na2o_x0753_1273 | imcc-published | 1273 | Na | activity | True | inside | -0.254 | 2.948e-11 | 5.29e-11 |
| tsaplin2000_a_na2o_x0753_1273 | imcc-ext | 1273 | Na | activity | True | inside | -0.04427 | 4.777e-11 | 5.29e-11 |
| tsaplin2000_a_sio2_x0709_1673 | imcc-published | 1673 | SiO | activity | True | inside | -0.1136 | 0.4127 | 0.536 |
| tsaplin2000_a_sio2_x0709_1673 | imcc-ext | 1673 | SiO | activity | True | inside | -0.1198 | 0.4067 | 0.536 |
| tsaplin2000_a_na2o_x0709_1673 | imcc-published | 1673 | Na | activity | True | inside | 0.1075 | 4.15e-08 | 3.24e-08 |
| tsaplin2000_a_na2o_x0709_1673 | imcc-ext | 1673 | Na | activity | True | inside | 0.05119 | 3.645e-08 | 3.24e-08 |
| tsaplin2000_a_sio2_x0671_1173 | imcc-published | 1173 | SiO | activity | True | inside | -0.2084 | 0.2116 | 0.342 |
| tsaplin2000_a_sio2_x0671_1173 | imcc-ext | 1173 | SiO | activity | True | inside | -0.1219 | 0.2583 | 0.342 |
| tsaplin2000_a_na2o_x0671_1173 | imcc-published | 1173 | Na | activity | True | inside | -0.2328 | 2.732e-11 | 4.67e-11 |
| tsaplin2000_a_na2o_x0671_1173 | imcc-ext | 1173 | Na | activity | True | inside | -0.09282 | 3.771e-11 | 4.67e-11 |
| tsaplin2000_a_sio2_x0625_1573 | imcc-published | 1573 | SiO | activity | True | inside | -0.1644 | 0.1363 | 0.199 |
| tsaplin2000_a_sio2_x0625_1573 | imcc-ext | 1573 | SiO | activity | True | inside | -0.1638 | 0.1365 | 0.199 |
| tsaplin2000_a_na2o_x0625_1573 | imcc-published | 1573 | Na | activity | True | inside | 0.123 | 1.013e-07 | 7.63e-08 |
| tsaplin2000_a_na2o_x0625_1573 | imcc-ext | 1573 | Na | activity | True | inside | 0.1083 | 9.792e-08 | 7.63e-08 |
| tsaplin2000_a_sio2_x0573_1473 | imcc-published | 1473 | SiO | activity | True | inside | -0.1751 | 0.04698 | 0.0703 |
| tsaplin2000_a_sio2_x0573_1473 | imcc-ext | 1473 | SiO | activity | True | inside | -0.1329 | 0.05177 | 0.0703 |
| tsaplin2000_a_na2o_x0573_1473 | imcc-published | 1473 | Na | activity | True | inside | 0.06213 | 1.177e-07 | 1.02e-07 |
| tsaplin2000_a_na2o_x0573_1473 | imcc-ext | 1473 | Na | activity | True | inside | 0.03414 | 1.103e-07 | 1.02e-07 |
| tsaplin2000_a_sio2_x0524_1573 | imcc-published | 1573 | SiO | activity | True | inside | -0.2062 | 0.01269 | 0.0204 |
| tsaplin2000_a_sio2_x0524_1573 | imcc-ext | 1573 | SiO | activity | True | inside | -0.2052 | 0.01272 | 0.0204 |
| tsaplin2000_a_na2o_x0524_1573 | imcc-published | 1573 | Na | activity | True | inside | 0.1788 | 2.309e-06 | 1.53e-06 |
| tsaplin2000_a_na2o_x0524_1573 | imcc-ext | 1573 | Na | activity | True | inside | 0.1636 | 2.23e-06 | 1.53e-06 |
| tsaplin2000_a_sio2_x0477_1373 | imcc-published | 1373 | SiO | activity | True | outside_validated | -4.999 | 1.753e-08 | 0.00175 |
| tsaplin2000_a_sio2_x0477_1373 | imcc-ext | 1373 | SiO | activity | True | outside_validated | -4.955 | 1.942e-08 | 0.00175 |
| tsaplin2000_a_na2o_x0477_1373 | imcc-published | 1373 | Na | activity | True | outside_validated | 4.824 | 0.08797 | 1.32e-06 |
| tsaplin2000_a_na2o_x0477_1373 | imcc-ext | 1373 | Na | activity | True | outside_validated | 4.824 | 0.08797 | 1.32e-06 |
| tsaplin2000_a_sio2_x0430_1473 | imcc-published | 1473 | SiO | activity | True | outside_validated | -4.179 | 2.519e-08 | 0.00038 |
| tsaplin2000_a_sio2_x0430_1473 | imcc-ext | 1473 | SiO | activity | True | outside_validated | -4.165 | 2.596e-08 | 0.00038 |
| tsaplin2000_a_na2o_x0430_1473 | imcc-published | 1473 | Na | activity | True | outside_validated | 4.128 | 0.2456 | 1.83e-05 |
| tsaplin2000_a_na2o_x0430_1473 | imcc-ext | 1473 | Na | activity | True | outside_validated | 4.128 | 0.2456 | 1.83e-05 |
| tsaplin2000_a_sio2_x0405_1423 | imcc-published | 1423 | SiO | activity | True | outside_validated | -4.228 | 8.16e-09 | 0.000138 |
| tsaplin2000_a_sio2_x0405_1423 | imcc-ext | 1423 | SiO | activity | True | outside_validated | -4.2 | 8.708e-09 | 0.000138 |
| tsaplin2000_a_na2o_x0405_1423 | imcc-published | 1423 | Na | activity | True | outside_validated | 4.201 | 0.3193 | 2.01e-05 |
| tsaplin2000_a_na2o_x0405_1423 | imcc-ext | 1423 | Na | activity | True | outside_validated | 4.201 | 0.3193 | 2.01e-05 |
| tsaplin2000_a_sio2_x0382_1383 | imcc-published | 1383 | SiO | activity | True | outside_validated | -4.167 | 3.238e-09 | 4.76e-05 |
| tsaplin2000_a_sio2_x0382_1383 | imcc-ext | 1383 | SiO | activity | True | outside_validated | -4.126 | 3.559e-09 | 4.76e-05 |
| tsaplin2000_a_na2o_x0382_1383 | imcc-published | 1383 | Na | activity | True | outside_validated | 4.196 | 0.3819 | 2.43e-05 |
| tsaplin2000_a_na2o_x0382_1383 | imcc-ext | 1383 | Na | activity | True | outside_validated | 4.196 | 0.3819 | 2.43e-05 |
| tsaplin2000_a_sio2_x0349_1373 | imcc-published | 1373 | SiO | activity | True | outside_validated | -3.286 | 1.954e-09 | 3.77e-06 |
| tsaplin2000_a_sio2_x0349_1373 | imcc-ext | 1373 | SiO | activity | True | outside_validated | -3.241 | 2.164e-09 | 3.77e-06 |
| tsaplin2000_a_na2o_x0349_1373 | imcc-published | 1373 | Na | activity | True | outside_validated | 3.731 | 0.4639 | 8.62e-05 |
| tsaplin2000_a_na2o_x0349_1373 | imcc-ext | 1373 | Na | activity | True | outside_validated | 3.731 | 0.4639 | 8.62e-05 |
| yamaguchi1983_a_sio2_liquid_x0205_1373 | imcc-published | 1373 | SiO | activity | True | inside | -0.09324 | 0.6671 | 0.8268 |
| yamaguchi1983_a_sio2_liquid_x0205_1373 | imcc-ext | 1373 | SiO | activity | True | inside | -0.09104 | 0.6705 | 0.8268 |
| yamaguchi1983_a_sio2_liquid_x0205_1473 | imcc-published | 1473 | SiO | activity | True | inside | -0.07626 | 0.6692 | 0.7977 |
| yamaguchi1983_a_sio2_liquid_x0205_1473 | imcc-ext | 1473 | SiO | activity | True | inside | -0.07516 | 0.6709 | 0.7977 |
| yamaguchi1983_a_sio2_liquid_x0205_1573 | imcc-published | 1573 | SiO | activity | True | inside | -0.06282 | 0.6713 | 0.7757 |
| yamaguchi1983_a_sio2_liquid_x0205_1573 | imcc-ext | 1573 | SiO | activity | True | inside | -0.06279 | 0.6713 | 0.7757 |
| yamaguchi1983_a_sio2_liquid_x0205_1673 | imcc-published | 1673 | SiO | activity | True | inside | -0.05226 | 0.6732 | 0.7593 |
| yamaguchi1983_a_sio2_liquid_x0205_1673 | imcc-ext | 1673 | SiO | activity | True | inside | -0.05327 | 0.6716 | 0.7593 |
| yamaguchi1983_a_na2o_x0205_1173 | imcc-published | 1173 | Na | activity | True | inside | -0.09326 | 1.412e-12 | 1.751e-12 |
| yamaguchi1983_a_na2o_x0205_1173 | imcc-ext | 1173 | Na | activity | True | inside | 0.2464 | 3.087e-12 | 1.751e-12 |
| yamaguchi1983_a_na2o_x0205_1273 | imcc-published | 1273 | Na | activity | True | inside | -0.08629 | 1.455e-11 | 1.775e-11 |
| yamaguchi1983_a_na2o_x0205_1273 | imcc-ext | 1273 | Na | activity | True | inside | 0.1408 | 2.454e-11 | 1.775e-11 |
| yamaguchi1983_a_na2o_x0205_1373 | imcc-published | 1373 | Na | activity | True | inside | -0.08274 | 1.061e-10 | 1.284e-10 |
| yamaguchi1983_a_na2o_x0205_1373 | imcc-ext | 1373 | Na | activity | True | inside | 0.05052 | 1.442e-10 | 1.284e-10 |
| yamaguchi1983_a_na2o_x0205_1473 | imcc-published | 1473 | Na | activity | True | inside | -0.08168 | 5.881e-10 | 7.098e-10 |
| yamaguchi1983_a_na2o_x0205_1473 | imcc-ext | 1473 | Na | activity | True | inside | -0.02759 | 6.661e-10 | 7.098e-10 |
| yamaguchi1983_a_na2o_x0205_1573 | imcc-published | 1573 | Na | activity | True | inside | -0.08243 | 2.612e-09 | 3.158e-09 |
| yamaguchi1983_a_na2o_x0205_1573 | imcc-ext | 1573 | Na | activity | True | inside | -0.09583 | 2.532e-09 | 3.158e-09 |
| yamaguchi1983_a_na2o_x0205_1673 | imcc-published | 1673 | Na | activity | True | inside | -0.08448 | 9.675e-09 | 1.175e-08 |
| yamaguchi1983_a_na2o_x0205_1673 | imcc-ext | 1673 | Na | activity | True | inside | -0.156 | 8.207e-09 | 1.175e-08 |
| yamaguchi1983_a_sio2_liquid_x0298_1373 | imcc-published | 1373 | SiO | activity | True | inside | 0.05502 | 0.3612 | 0.3183 |
| yamaguchi1983_a_sio2_liquid_x0298_1373 | imcc-ext | 1373 | SiO | activity | True | inside | 0.07314 | 0.3766 | 0.3183 |
| yamaguchi1983_a_sio2_liquid_x0298_1473 | imcc-published | 1473 | SiO | activity | True | inside | 0.02269 | 0.3711 | 0.3522 |
| yamaguchi1983_a_sio2_liquid_x0298_1473 | imcc-ext | 1473 | SiO | activity | True | inside | 0.0313 | 0.3785 | 0.3522 |
| yamaguchi1983_a_sio2_liquid_x0298_1573 | imcc-published | 1573 | SiO | activity | True | inside | -0.00689 | 0.38 | 0.3861 |
| yamaguchi1983_a_sio2_liquid_x0298_1573 | imcc-ext | 1573 | SiO | activity | True | inside | -0.006716 | 0.3801 | 0.3861 |
| yamaguchi1983_a_sio2_liquid_x0298_1673 | imcc-published | 1673 | SiO | activity | True | inside | -0.03426 | 0.388 | 0.4199 |
| yamaguchi1983_a_sio2_liquid_x0298_1673 | imcc-ext | 1673 | SiO | activity | True | inside | -0.04155 | 0.3816 | 0.4199 |
| yamaguchi1983_a_na2o_x0298_1173 | imcc-published | 1173 | Na | activity | True | inside | -0.8192 | 9.789e-12 | 6.456e-11 |
| yamaguchi1983_a_na2o_x0298_1173 | imcc-ext | 1173 | Na | activity | True | inside | -0.5796 | 1.7e-11 | 6.456e-11 |
| yamaguchi1983_a_na2o_x0298_1273 | imcc-published | 1273 | Na | activity | True | inside | -0.6299 | 9.268e-11 | 3.952e-10 |
| yamaguchi1983_a_na2o_x0298_1273 | imcc-ext | 1273 | Na | activity | True | inside | -0.4732 | 1.329e-10 | 3.952e-10 |
| yamaguchi1983_a_na2o_x0298_1373 | imcc-published | 1373 | Na | activity | True | inside | -0.4716 | 6.274e-10 | 1.858e-09 |
| yamaguchi1983_a_na2o_x0298_1373 | imcc-ext | 1373 | Na | activity | True | inside | -0.3824 | 7.703e-10 | 1.858e-09 |
| yamaguchi1983_a_na2o_x0298_1473 | imcc-published | 1473 | Na | activity | True | inside | -0.3373 | 3.257e-09 | 7.081e-09 |
| yamaguchi1983_a_na2o_x0298_1473 | imcc-ext | 1473 | Na | activity | True | inside | -0.304 | 3.516e-09 | 7.081e-09 |
| yamaguchi1983_a_na2o_x0298_1573 | imcc-published | 1573 | Na | activity | True | inside | -0.2219 | 1.366e-08 | 2.276e-08 |
| yamaguchi1983_a_na2o_x0298_1573 | imcc-ext | 1573 | Na | activity | True | inside | -0.2357 | 1.323e-08 | 2.276e-08 |
| yamaguchi1983_a_na2o_x0298_1673 | imcc-published | 1673 | Na | activity | True | inside | -0.1216 | 4.809e-08 | 6.363e-08 |
| yamaguchi1983_a_na2o_x0298_1673 | imcc-ext | 1673 | Na | activity | True | inside | -0.1755 | 4.248e-08 | 6.363e-08 |
| yamaguchi1983_a_sio2_liquid_x0356_1373 | imcc-published | 1373 | SiO | activity | True | inside | 0.05964 | 0.1577 | 0.1375 |
| yamaguchi1983_a_sio2_liquid_x0356_1373 | imcc-ext | 1373 | SiO | activity | True | inside | 0.1157 | 0.1794 | 0.1375 |
| yamaguchi1983_a_sio2_liquid_x0356_1473 | imcc-published | 1473 | SiO | activity | True | inside | 0.03481 | 0.1716 | 0.1584 |
| yamaguchi1983_a_sio2_liquid_x0356_1473 | imcc-ext | 1473 | SiO | activity | True | inside | 0.06044 | 0.1821 | 0.1584 |
| yamaguchi1983_a_sio2_liquid_x0356_1573 | imcc-published | 1573 | SiO | activity | True | inside | 0.01017 | 0.1841 | 0.1799 |
| yamaguchi1983_a_sio2_liquid_x0356_1573 | imcc-ext | 1573 | SiO | activity | True | inside | 0.01067 | 0.1843 | 0.1799 |
| yamaguchi1983_a_sio2_liquid_x0356_1673 | imcc-published | 1673 | SiO | activity | True | inside | -0.01405 | 0.1954 | 0.2018 |
| yamaguchi1983_a_sio2_liquid_x0356_1673 | imcc-ext | 1673 | SiO | activity | True | inside | -0.03456 | 0.1864 | 0.2018 |
| yamaguchi1983_a_na2o_x0356_1173 | imcc-published | 1173 | Na | activity | True | inside | -0.6169 | 7.479e-11 | 3.096e-10 |
| yamaguchi1983_a_na2o_x0356_1173 | imcc-ext | 1173 | Na | activity | True | inside | -0.5801 | 8.14e-11 | 3.096e-10 |
| yamaguchi1983_a_na2o_x0356_1273 | imcc-published | 1273 | Na | activity | True | inside | -0.4889 | 5.852e-10 | 1.804e-09 |
| yamaguchi1983_a_na2o_x0356_1273 | imcc-ext | 1273 | Na | activity | True | inside | -0.4641 | 6.195e-10 | 1.804e-09 |
| yamaguchi1983_a_na2o_x0356_1373 | imcc-published | 1373 | Na | activity | True | inside | -0.3767 | 3.415e-09 | 8.131e-09 |
| yamaguchi1983_a_na2o_x0356_1373 | imcc-ext | 1373 | Na | activity | True | inside | -0.365 | 3.509e-09 | 8.131e-09 |
| yamaguchi1983_a_na2o_x0356_1473 | imcc-published | 1473 | Na | activity | True | inside | -0.2777 | 1.576e-08 | 2.988e-08 |
| yamaguchi1983_a_na2o_x0356_1473 | imcc-ext | 1473 | Na | activity | True | inside | -0.2792 | 1.571e-08 | 2.988e-08 |
| yamaguchi1983_a_na2o_x0356_1573 | imcc-published | 1573 | Na | activity | True | inside | -0.1898 | 6.009e-08 | 9.303e-08 |
| yamaguchi1983_a_na2o_x0356_1573 | imcc-ext | 1573 | Na | activity | True | inside | -0.2043 | 5.812e-08 | 9.303e-08 |
| yamaguchi1983_a_na2o_x0356_1673 | imcc-published | 1673 | Na | activity | True | inside | -0.1114 | 1.957e-07 | 2.529e-07 |
| yamaguchi1983_a_na2o_x0356_1673 | imcc-ext | 1673 | Na | activity | True | inside | -0.1383 | 1.839e-07 | 2.529e-07 |
| yamaguchi1983_a_sio2_liquid_x0400_1373 | imcc-published | 1373 | SiO | activity | True | inside | 0.1278 | 0.07063 | 0.05263 |
| yamaguchi1983_a_sio2_liquid_x0400_1373 | imcc-ext | 1373 | SiO | activity | True | inside | 0.21 | 0.08536 | 0.05263 |
| yamaguchi1983_a_sio2_liquid_x0400_1473 | imcc-published | 1473 | SiO | activity | True | inside | 0.1114 | 0.07993 | 0.06185 |
| yamaguchi1983_a_sio2_liquid_x0400_1473 | imcc-ext | 1473 | SiO | activity | True | inside | 0.1492 | 0.08721 | 0.06185 |
| yamaguchi1983_a_sio2_liquid_x0400_1573 | imcc-published | 1573 | SiO | activity | True | inside | 0.09386 | 0.08868 | 0.07145 |
| yamaguchi1983_a_sio2_liquid_x0400_1573 | imcc-ext | 1573 | SiO | activity | True | inside | 0.0946 | 0.08884 | 0.07145 |
| yamaguchi1983_a_sio2_liquid_x0400_1673 | imcc-published | 1673 | SiO | activity | True | inside | 0.07555 | 0.09685 | 0.08138 |
| yamaguchi1983_a_sio2_liquid_x0400_1673 | imcc-ext | 1673 | SiO | activity | True | inside | 0.04509 | 0.09029 | 0.08138 |
| yamaguchi1983_a_na2o_x0400_1173 | imcc-published | 1173 | Na | activity | True | inside | -0.8352 | 3.319e-10 | 2.271e-09 |
| yamaguchi1983_a_na2o_x0400_1173 | imcc-ext | 1173 | Na | activity | True | inside | -0.9023 | 2.844e-10 | 2.271e-09 |
| yamaguchi1983_a_na2o_x0400_1273 | imcc-published | 1273 | Na | activity | True | inside | -0.7079 | 2.371e-09 | 1.21e-08 |
| yamaguchi1983_a_na2o_x0400_1273 | imcc-ext | 1273 | Na | activity | True | inside | -0.7543 | 2.131e-09 | 1.21e-08 |
| yamaguchi1983_a_na2o_x0400_1373 | imcc-published | 1373 | Na | activity | True | inside | -0.5959 | 1.281e-08 | 5.054e-08 |
| yamaguchi1983_a_na2o_x0400_1373 | imcc-ext | 1373 | Na | activity | True | inside | -0.6278 | 1.191e-08 | 5.054e-08 |
| yamaguchi1983_a_na2o_x0400_1473 | imcc-published | 1473 | Na | activity | True | inside | -0.4966 | 5.54e-08 | 1.738e-07 |
| yamaguchi1983_a_na2o_x0400_1473 | imcc-ext | 1473 | Na | activity | True | inside | -0.5183 | 5.269e-08 | 1.738e-07 |
| yamaguchi1983_a_na2o_x0400_1573 | imcc-published | 1573 | Na | activity | True | inside | -0.4079 | 1.998e-07 | 5.109e-07 |
| yamaguchi1983_a_na2o_x0400_1573 | imcc-ext | 1573 | Na | activity | True | inside | -0.4227 | 1.93e-07 | 5.109e-07 |
| yamaguchi1983_a_na2o_x0400_1673 | imcc-published | 1673 | Na | activity | True | inside | -0.3281 | 6.202e-07 | 1.32e-06 |
| yamaguchi1983_a_na2o_x0400_1673 | imcc-ext | 1673 | Na | activity | True | inside | -0.3385 | 6.055e-07 | 1.32e-06 |
| yamaguchi1983_a_sio2_liquid_x0429_1373 | imcc-published | 1373 | SiO | activity | True | inside | 0.1104 | 0.03922 | 0.03042 |
| yamaguchi1983_a_sio2_liquid_x0429_1373 | imcc-ext | 1373 | SiO | activity | True | inside | 0.2022 | 0.04845 | 0.03042 |
| yamaguchi1983_a_sio2_liquid_x0429_1473 | imcc-published | 1473 | SiO | activity | True | inside | 0.09122 | 0.04501 | 0.03648 |
| yamaguchi1983_a_sio2_liquid_x0429_1473 | imcc-ext | 1473 | SiO | activity | True | inside | 0.1336 | 0.04963 | 0.03648 |
| yamaguchi1983_a_sio2_liquid_x0429_1573 | imcc-published | 1573 | SiO | activity | True | inside | 0.07144 | 0.05058 | 0.04291 |
| yamaguchi1983_a_sio2_liquid_x0429_1573 | imcc-ext | 1573 | SiO | activity | True | inside | 0.07228 | 0.05068 | 0.04291 |
| yamaguchi1983_a_sio2_liquid_x0429_1673 | imcc-published | 1673 | SiO | activity | True | inside | 0.05138 | 0.05589 | 0.04965 |
| yamaguchi1983_a_sio2_liquid_x0429_1673 | imcc-ext | 1673 | SiO | activity | True | inside | 0.01684 | 0.05162 | 0.04965 |
| yamaguchi1983_a_na2o_x0429_1173 | imcc-published | 1173 | Na | activity | True | inside | -0.7939 | 7.954e-10 | 4.948e-09 |
| yamaguchi1983_a_na2o_x0429_1173 | imcc-ext | 1173 | Na | activity | True | inside | -0.8893 | 6.384e-10 | 4.948e-09 |
| yamaguchi1983_a_na2o_x0429_1273 | imcc-published | 1273 | Na | activity | True | inside | -0.6696 | 5.556e-09 | 2.596e-08 |
| yamaguchi1983_a_na2o_x0429_1273 | imcc-ext | 1273 | Na | activity | True | inside | -0.7369 | 4.759e-09 | 2.596e-08 |
| yamaguchi1983_a_na2o_x0429_1373 | imcc-published | 1373 | Na | activity | True | inside | -0.5612 | 2.94e-08 | 1.07e-07 |
| yamaguchi1983_a_na2o_x0429_1373 | imcc-ext | 1373 | Na | activity | True | inside | -0.6065 | 2.648e-08 | 1.07e-07 |
| yamaguchi1983_a_na2o_x0429_1473 | imcc-published | 1473 | Na | activity | True | inside | -0.4655 | 1.246e-07 | 3.639e-07 |
| yamaguchi1983_a_na2o_x0429_1473 | imcc-ext | 1473 | Na | activity | True | inside | -0.4938 | 1.167e-07 | 3.639e-07 |
| yamaguchi1983_a_na2o_x0429_1573 | imcc-published | 1573 | Na | activity | True | inside | -0.3803 | 4.412e-07 | 1.059e-06 |
| yamaguchi1983_a_na2o_x0429_1573 | imcc-ext | 1573 | Na | activity | True | inside | -0.3953 | 4.262e-07 | 1.059e-06 |
| yamaguchi1983_a_na2o_x0429_1673 | imcc-published | 1673 | Na | activity | True | inside | -0.304 | 1.347e-06 | 2.713e-06 |
| yamaguchi1983_a_na2o_x0429_1673 | imcc-ext | 1673 | Na | activity | True | inside | -0.3086 | 1.333e-06 | 2.713e-06 |
| yamaguchi1983_a_sio2_liquid_x0500_1373 | imcc-published | 1373 | SiO | activity | True | outside_validated | -2.558 | 1.149e-05 | 0.004158 |
| yamaguchi1983_a_sio2_liquid_x0500_1373 | imcc-ext | 1373 | SiO | activity | True | outside_validated | -2.487 | 1.355e-05 | 0.004158 |
| yamaguchi1983_a_sio2_liquid_x0500_1473 | imcc-published | 1473 | SiO | activity | True | outside_validated | -2.3 | 2.812e-05 | 0.005609 |
| yamaguchi1983_a_sio2_liquid_x0500_1473 | imcc-ext | 1473 | SiO | activity | True | outside_validated | -2.27 | 3.013e-05 | 0.005609 |
| yamaguchi1983_a_sio2_liquid_x0500_1573 | imcc-published | 1573 | SiO | activity | True | outside_validated | -2.081 | 6.059e-05 | 0.007307 |
| yamaguchi1983_a_sio2_liquid_x0500_1573 | imcc-ext | 1573 | SiO | activity | True | outside_validated | -2.088 | 5.966e-05 | 0.007307 |
| yamaguchi1983_a_sio2_liquid_x0500_1673 | imcc-published | 1673 | SiO | activity | True | outside_validated | -1.893 | 0.0001184 | 0.009253 |
| yamaguchi1983_a_sio2_liquid_x0500_1673 | imcc-ext | 1673 | SiO | activity | True | outside_validated | -1.932 | 0.0001083 | 0.009253 |
| yamaguchi1983_a_na2o_x0500_1173 | imcc-published | 1173 | Na | activity | True | outside_validated | 2.676 | 3.247e-05 | 6.844e-08 |
| yamaguchi1983_a_na2o_x0500_1173 | imcc-ext | 1173 | Na | activity | True | outside_validated | 2.637 | 2.966e-05 | 6.844e-08 |
| yamaguchi1983_a_na2o_x0500_1273 | imcc-published | 1273 | Na | activity | True | outside_validated | 2.375 | 6.936e-05 | 2.925e-07 |
| yamaguchi1983_a_na2o_x0500_1273 | imcc-ext | 1273 | Na | activity | True | outside_validated | 2.338 | 6.369e-05 | 2.925e-07 |
| yamaguchi1983_a_na2o_x0500_1373 | imcc-published | 1373 | Na | activity | True | outside_validated | 2.162 | 0.000147 | 1.012e-06 |
| yamaguchi1983_a_na2o_x0500_1373 | imcc-ext | 1373 | Na | activity | True | outside_validated | 2.135 | 0.0001381 | 1.012e-06 |
| yamaguchi1983_a_na2o_x0500_1473 | imcc-published | 1473 | Na | activity | True | outside_validated | 1.994 | 0.0002914 | 2.957e-06 |
| yamaguchi1983_a_na2o_x0500_1473 | imcc-ext | 1473 | Na | activity | True | outside_validated | 1.977 | 0.0002804 | 2.957e-06 |
| yamaguchi1983_a_na2o_x0500_1573 | imcc-published | 1573 | Na | activity | True | outside_validated | 1.852 | 0.0005365 | 7.541e-06 |
| yamaguchi1983_a_na2o_x0500_1573 | imcc-ext | 1573 | Na | activity | True | outside_validated | 1.845 | 0.0005273 | 7.541e-06 |
| yamaguchi1983_a_na2o_x0500_1673 | imcc-published | 1673 | Na | activity | True | outside_validated | 1.73 | 0.0009234 | 1.719e-05 |
| yamaguchi1983_a_na2o_x0500_1673 | imcc-ext | 1673 | Na | activity | True | outside_validated | 1.731 | 0.0009245 | 1.719e-05 |
| yamaguchi1983_a_sio2_liquid_x0601_1373 | imcc-published | 1373 | SiO | activity | True | outside_validated | -4.2 | 3.339e-09 | 5.29e-05 |
| yamaguchi1983_a_sio2_liquid_x0601_1373 | imcc-ext | 1373 | SiO | activity | True | outside_validated | -4.155 | 3.699e-09 | 5.29e-05 |
| yamaguchi1983_a_sio2_liquid_x0601_1473 | imcc-published | 1473 | SiO | activity | True | outside_validated | -3.725 | 1.62e-08 | 8.6e-05 |
| yamaguchi1983_a_sio2_liquid_x0601_1473 | imcc-ext | 1473 | SiO | activity | True | outside_validated | -3.712 | 1.669e-08 | 8.6e-05 |
| yamaguchi1983_a_sio2_liquid_x0601_1573 | imcc-published | 1573 | SiO | activity | True | outside_validated | -3.312 | 6.428e-08 | 0.0001319 |
| yamaguchi1983_a_sio2_liquid_x0601_1573 | imcc-ext | 1573 | SiO | activity | True | outside_validated | -3.326 | 6.221e-08 | 0.0001319 |
| yamaguchi1983_a_sio2_liquid_x0601_1673 | imcc-published | 1673 | SiO | activity | True | outside_validated | -2.95 | 2.163e-07 | 0.0001928 |
| yamaguchi1983_a_sio2_liquid_x0601_1673 | imcc-ext | 1673 | SiO | activity | True | outside_validated | -2.988 | 1.981e-07 | 0.0001928 |
| yamaguchi1983_a_na2o_x0601_1173 | imcc-published | 1173 | Na | activity | True | outside_validated | 4.999 | 0.3361 | 3.367e-06 |
| yamaguchi1983_a_na2o_x0601_1173 | imcc-ext | 1173 | Na | activity | True | outside_validated | 4.999 | 0.3361 | 3.367e-06 |
| yamaguchi1983_a_na2o_x0601_1273 | imcc-published | 1273 | Na | activity | True | outside_validated | 4.46 | 0.3361 | 1.165e-05 |
| yamaguchi1983_a_na2o_x0601_1273 | imcc-ext | 1273 | Na | activity | True | outside_validated | 4.46 | 0.3361 | 1.165e-05 |
| yamaguchi1983_a_na2o_x0601_1373 | imcc-published | 1373 | Na | activity | True | outside_validated | 4 | 0.3361 | 3.365e-05 |
| yamaguchi1983_a_na2o_x0601_1373 | imcc-ext | 1373 | Na | activity | True | outside_validated | 4 | 0.3361 | 3.365e-05 |
| yamaguchi1983_a_na2o_x0601_1473 | imcc-published | 1473 | Na | activity | True | outside_validated | 3.602 | 0.3361 | 8.413e-05 |
| yamaguchi1983_a_na2o_x0601_1473 | imcc-ext | 1473 | Na | activity | True | outside_validated | 3.602 | 0.3361 | 8.413e-05 |
| yamaguchi1983_a_na2o_x0601_1573 | imcc-published | 1573 | Na | activity | True | outside_validated | 3.254 | 0.3361 | 0.0001872 |
| yamaguchi1983_a_na2o_x0601_1573 | imcc-ext | 1573 | Na | activity | True | outside_validated | 3.254 | 0.3361 | 0.0001872 |
| yamaguchi1983_a_na2o_x0601_1673 | imcc-published | 1673 | Na | activity | True | outside_validated | 2.948 | 0.3361 | 0.0003787 |
| yamaguchi1983_a_na2o_x0601_1673 | imcc-ext | 1673 | Na | activity | True | outside_validated | 2.948 | 0.3361 | 0.0003787 |

## Frozen SF04 MAGMA regression anchor

The tracked source snapshots rejoin **288** identical SF04 cells on `(sheet, species, T_K)`. Their MAGMA references agree to 0.0000 dex at four decimals.

MAGMA is a model-reproduction anchor, not correctness evidence. The empirical-KEMS column is the independent measured-pressure check available in the frozen VapoRock validation snapshot.

| Species | Shared MAGMA n | IMCC RMSE vs MAGMA | VapoRock RMSE vs MAGMA | Empirical KEMS n | VapoRock RMSE vs KEMS |
|---|---:|---:|---:|---:|---:|
| SiO2 | 36 | 0.099 | 0.351 | 0 | — |
| FeO | 36 | 0.185 | 0.446 | 0 | — |
| Fe | 36 | 0.186 | 0.531 | 0 | — |
| Mg | 36 | 0.248 | 0.346 | 0 | — |
| SiO | 36 | 0.556 | 0.891 | 3 | 0.248 |
| K | 36 | 2.15 | 0.462 | 3 | 0.0725 |
| Na | 36 | 1.18 | 0.143 | 0 | — |
| O2 | 36 | 8.86e-05 | 8.95e-05 | 1 | 0.331 |

Controller regression pool (all non-alkali rows, including the O2 fO2 pin): IMCC **0.274** vs VapoRock **0.503** dex; 0.274/0.503 anchor reproduced: **yes**.

Experimental KEMS snapshot: **7 scored / 9 retained** rows. These are independent KEMS compositions, not measurements on the four SF04 basalt sheets, and therefore do not turn the MAGMA table into empirical basalt evidence.

### Installed VapoRock snapshot check

Live comparison produced 288/288 cells; maximum live-minus-frozen magnitude: 4.64992e-05 dex.
The installed VapoRock run reproduces the frozen snapshot within 0.0005 dex.

## In-domain composition probes

These are engine robustness/coverage probes, not empirical score points.

| Composition | Class | Engine | Status | Reason |
|---|---|---|---|---|
| sf04_tholeiite | literal_basalt | imcc-published | ok | — |
| sf04_tholeiite | literal_basalt | imcc-ext | ok | — |
| sf04_tholeiite | literal_basalt | internal_analytic | ok | — |
| sf04_tholeiite | literal_basalt | alphamelts | observable_unavailable | alphaMELTS 2.3.1 subprocess transport exposes no activity observable (binary menu option returns 'Sorry, option not yet implemented'); this is an upstream capability gap at every composition, not a per-point domain verdict |
| sf04_tholeiite | literal_basalt | thermoengine | ok | — |
| sf04_tholeiite | literal_basalt | vaporock | ok | — |
| sf04_alkali_basalt | literal_basalt | imcc-published | ok | — |
| sf04_alkali_basalt | literal_basalt | imcc-ext | ok | — |
| sf04_alkali_basalt | literal_basalt | internal_analytic | ok | — |
| sf04_alkali_basalt | literal_basalt | alphamelts | observable_unavailable | alphaMELTS 2.3.1 subprocess transport exposes no activity observable (binary menu option returns 'Sorry, option not yet implemented'); this is an upstream capability gap at every composition, not a per-point domain verdict |
| sf04_alkali_basalt | literal_basalt | thermoengine | ok | — |
| sf04_alkali_basalt | literal_basalt | vaporock | ok | — |
| sf04_komatiite | literal_basalt | imcc-published | ok | — |
| sf04_komatiite | literal_basalt | imcc-ext | ok | — |
| sf04_komatiite | literal_basalt | internal_analytic | ok | — |
| sf04_komatiite | literal_basalt | alphamelts | observable_unavailable | alphaMELTS 2.3.1 subprocess transport exposes no activity observable (binary menu option returns 'Sorry, option not yet implemented'); this is an upstream capability gap at every composition, not a per-point domain verdict |
| sf04_komatiite | literal_basalt | thermoengine | ok | — |
| sf04_komatiite | literal_basalt | vaporock | ok | — |
| sf04_dunite | literal_basalt | imcc-published | ok | — |
| sf04_dunite | literal_basalt | imcc-ext | ok | — |
| sf04_dunite | literal_basalt | internal_analytic | ok | — |
| sf04_dunite | literal_basalt | alphamelts | observable_unavailable | alphaMELTS 2.3.1 subprocess transport exposes no activity observable (binary menu option returns 'Sorry, option not yet implemented'); this is an upstream capability gap at every composition, not a per-point domain verdict |
| sf04_dunite | literal_basalt | thermoengine | out_of_domain | ThermoEngineOutOfDomainError: fo2_outside_attainable_bracket: ThermoEngine absolute fO2 target is outside the attainable Fe-redox bracket: requested=-9 |
| sf04_dunite | literal_basalt | vaporock | ok | — |
| richter_type_b_cai | type_b_cai_like_cmas | imcc-published | ok | — |
| richter_type_b_cai | type_b_cai_like_cmas | imcc-ext | ok | — |
| richter_type_b_cai | type_b_cai_like_cmas | internal_analytic | ok | — |
| richter_type_b_cai | type_b_cai_like_cmas | alphamelts | observable_unavailable | alphaMELTS 2.3.1 subprocess transport exposes no activity observable (binary menu option returns 'Sorry, option not yet implemented'); this is an upstream capability gap at every composition, not a per-point domain verdict |
| richter_type_b_cai | type_b_cai_like_cmas | thermoengine | refused | ThermoEngineOutOfDomainError: fo2_requires_iron: ThermoEngine cannot impose absolute fO2 without FeO/Fe2O3 |
| richter_type_b_cai | type_b_cai_like_cmas | vaporock | out_of_domain | VapoRock refused T=2023 K outside admitted domain [1350, 1950] K (external domain gate; upstream fabricates finite garbage outside this envelope) |

## Cross-engine verdict

AlphaMELTS did not complete a usable melt-activity evaluation on all literal SF04 basalts.

IMCC-versus-AlphaMELTS empirical verdict: **none**. No point has both a convention-valid measurement and successful canonical activities from both engine families.

ThermoEngine produced 114/402 usable benchmark predictions; converged results without the requested canonical observable remain typed `observable_unavailable`.

## Stripping-trajectory coverage

- `alphamelts`: 0/168 accepted; 168 refused/unavailable; below 30 wt% SiO2, 0/40 accepted and 40/40 refused/unavailable.
- `imcc-ext`: 0/168 accepted; 168 refused/unavailable; below 30 wt% SiO2, 0/40 accepted and 40/40 refused/unavailable.
- `imcc-published`: 0/168 accepted; 168 refused/unavailable; below 30 wt% SiO2, 0/40 accepted and 40/40 refused/unavailable.
- `internal_analytic`: 0/168 accepted; 168 refused/unavailable; below 30 wt% SiO2, 0/40 accepted and 40/40 refused/unavailable.
- `thermoengine`: 0/168 accepted; 168 refused/unavailable; below 30 wt% SiO2, 0/40 accepted and 40/40 refused/unavailable.
- `vaporock`: 112/168 accepted; 56 refused/unavailable; below 30 wt% SiO2, 0/40 accepted and 40/40 refused/unavailable.
ThermoEngine coverage cells are explicitly `coverage not measured for this engine`. They do not call the ThermoEngine transport and they do not reuse the AlphaMELTS domain gate.

AlphaMELTS trajectory boundaries:

- `sf04_alkali_basalt` / `remove_silica`: first refusal at step 11, SiO2=28.534 wt%.
- `sf04_alkali_basalt` / `strip_modifiers`: first refusal at step 17, SiO2=81.286 wt%.
- `sf04_dunite` / `remove_silica`: first refusal at step 8, SiO2=29.695 wt%.
- `sf04_dunite` / `strip_modifiers`: first refusal at step 18, SiO2=82.451 wt%.
- `sf04_komatiite` / `remove_silica`: first refusal at step 12, SiO2=28.265 wt%.
- `sf04_komatiite` / `strip_modifiers`: first refusal at step 17, SiO2=82.640 wt%.
- `sf04_tholeiite` / `remove_silica`: first refusal at step 13, SiO2=29.174 wt%.
- `sf04_tholeiite` / `strip_modifiers`: first refusal at step 16, SiO2=81.775 wt%.

The CSV preserves each composition step, engine status, and typed reason.
It answers the rump question as a curve: AlphaMELTS rejects every normalized step below its 30 wt% SiO2 floor.

Paired below-30 wt% SiO2 coverage:

| IMCC engine | Both accept | internal_analytic only | IMCC only | Neither | Total |
|---|---:|---:|---:|---:|---:|
| imcc-ext | 37 | 3 | 0 | 0 | 40 |
| imcc-published | 38 | 2 | 0 | 0 | 40 |

## VapoRock vapour-pressure leg

VapoRock is scored on native log10 partial pressures converted to Pa, not on per-oxide melt activities. Activity and gamma points are omitted from the comparison table rather than reported as a dead activity engine.

Partial-pressure points: **5 scored / 6 planned**.

| Species | n | RMSE (dex) | Median residual | ok | OOD | refused |
|---|---:|---:|---:|---:|---:|---:|
| K | 2 | 0.07991 | -0.07488 | 2 | 1 | 0 |
| SiO | 3 | 0.2498 | -0.268 | 3 | 0 | 0 |

## Honest limits

- No direct experimental activity or partial-pressure points exist for the four literal SF04 basalt sheets in the tracked source inventory.
- Richter-2007 is an in-domain Type-B CAI-like CMAS melt, not a literal basalt; its six gamma targets are reported separately.
- Four OCR-digitized Richter Mg flux points are retained but refused for scoring because no independent experimental fO2 pin closes the gas/reference-state comparison.
- KEMS-008 Table 10 values are kinetic vaporization coefficients, not basalt melt activities.
- Melt-activity engines score gas observables through the fixture's pinned fO2 and the shared tracked analytical gas layer. Parent-formula activities are converted to the rail's single-cation component basis first. VapoRock is the exception: it is scored on its native offgas partial pressures, not on a derived activity surface.
- Activity coefficients are reported as `gamma = a/x` on the parent-oxide formula-unit basis. The internal analytical adapter converts its native single-cation activity and mole-fraction provenance before comparison.
- VapoRock is a vapour-pressure / offgas engine: native partial pressures are scored on their own leg. Activity/gamma points stay unasked (`observable_unavailable`). Frozen MAGMA/KEMS and the live-versus-frozen drift check remain.
- AlphaMELTS 2.3.1 subprocess transport exposes no activity observable. Every activity point is the same typed capability refusal (`transport_exposes_no_activity_observable`), not a per-composition domain verdict. It is never replaced by a fallback model.
