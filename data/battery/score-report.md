# Battery score report (schema v2.1)

Generated only. Pins are an independent baseline and are never
re-centred from these residuals. Refusals are diagnostics, never hidden.
Headline accuracy per rail is the product of score_eligible rows;
a rail with zero eligible references is reported as zero.

Hostname: `Simon-MacBookPro-M5.local`.

This report measured store `0e78fd8a4`: 45736 rows in, 58693 observations, 165 works, 5881 experiments, queue 111652, 1215 hard issues.

Warning: residuals ledger recorded store `0e78fd8a4` but the live store is `7c1a4b458`

Engines: vaporock, imcc_sf04, imcc_sf04_ext, internal-analytical.

## Per rail × engine headline

| rail | engine | n candidates | n refused | n scored | match rate | median abs dex |
|---|---|---:|---:|---:|---:|---:|
| SiO_evolution | imcc_sf04 | 6 | 6 | 0 | — | — |
| SiO_evolution | imcc_sf04_ext | 6 | 6 | 0 | — | — |
| SiO_evolution | internal-analytical | 6 | 6 | 0 | — | — |
| SiO_evolution | vaporock | 6 | 6 | 0 | — | — |
| alkali_shuttle | imcc_sf04 | 0 | 0 | 0 | — | — |
| alkali_shuttle | imcc_sf04_ext | 0 | 0 | 0 | — | — |
| alkali_shuttle | internal-analytical | 0 | 0 | 0 | — | — |
| alkali_shuttle | vaporock | 0 | 0 | 0 | — | — |
| melt_activity | imcc_sf04 | 43 | 43 | 0 | — | — |
| melt_activity | imcc_sf04_ext | 43 | 43 | 0 | — | — |
| melt_activity | internal-analytical | 43 | 43 | 0 | — | — |
| melt_activity | vaporock | 43 | 43 | 0 | — | — |
| pyrolysis_yield | imcc_sf04 | 0 | 0 | 0 | — | — |
| pyrolysis_yield | imcc_sf04_ext | 0 | 0 | 0 | — | — |
| pyrolysis_yield | internal-analytical | 0 | 0 | 0 | — | — |
| pyrolysis_yield | vaporock | 0 | 0 | 0 | — | — |
| redox | imcc_sf04 | 0 | 0 | 0 | — | — |
| redox | imcc_sf04_ext | 0 | 0 | 0 | — | — |
| redox | internal-analytical | 0 | 0 | 0 | — | — |
| redox | vaporock | 0 | 0 | 0 | — | — |
| thermochemistry | imcc_sf04 | 39243 | 39243 | 0 | — | — |
| thermochemistry | imcc_sf04_ext | 39243 | 39243 | 0 | — | — |
| thermochemistry | internal-analytical | 39243 | 39243 | 0 | — | — |
| thermochemistry | vaporock | 39243 | 39243 | 0 | — | — |
| vapour | imcc_sf04 | 17295 | 17295 | 0 | — | — |
| vapour | imcc_sf04_ext | 17295 | 17295 | 0 | — | — |
| vapour | internal-analytical | 17295 | 17295 | 0 | — | — |
| vapour | vaporock | 17295 | 17295 | 0 | — | — |
| wall_deposition | imcc_sf04 | 0 | 0 | 0 | — | — |
| wall_deposition | imcc_sf04_ext | 0 | 0 | 0 | — | — |
| wall_deposition | internal-analytical | 0 | 0 | 0 | — | — |
| wall_deposition | vaporock | 0 | 0 | 0 | — | — |

## Refusal census

| reason | n |
|---|---:|
| `identity_unknown:quantity_unknown` | 68096 |
| `method_unknown` | 1120 |
| `metric_domain` | 14852 |
| `metric_domain:value_unknown` | 57220 |
| `underdetermined_apparatus` | 4 |
| `unsupported:diagnostic_population` | 85056 |

## Admission

score_eligible requires canonical admission admitted. Pending rows
stay in the comparison set as diagnostics (flagged and priced when
numeric, never the empirical headline). The admission rule is unchanged.

| count | n |
|---|---:|
| comparison candidates pending | 1277 |
| comparison candidates admitted | 0 |
| residuals with admission_admitted exclusion | 0 |
| residuals that die on admission alone | 0 |
| unique observations that die on admission alone | 0 |

## Notice backlog (certification projects)

| notice kind | n |
|---|---:|
| `derivation_uses_compilation` | 85060 |
| `out_of_certified_band` | 236 |

## Internal-consistency / compilation diagnostics

species_rail_differential_ledger and gibbs_battery_residual_ledger
are internal-consistency instruments, not scoring ledgers. Compilation
observations are engine reference inputs. Neither population enters
the empirical headline. Do not validate an engine against a compilation it consumes.

Diagnostic residuals in this file: 221212.

## Pin failures

23748 pin failures (coverage or outside pin_band). A live residual outside its pin_band is a failure, never a re-centre.

| key | reason | live | centre | pin_band |
|---|---|---:|---:|---:|
| `janaf-4th::JANAF1998_P2_formation_tabulation:T=1000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 0.18948855698057443 | 0.05 |
| `janaf-4th::JANAF1998_P2_formation_tabulation:T=1100::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 0.14372565013721683 | 0.05 |
| `janaf-4th::JANAF1998_P2_formation_tabulation:T=1200::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -2.063268928562815 | 0.05 |
| `janaf-4th::JANAF1998_P2_formation_tabulation:T=298.15::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::JANAF1998_P2_formation_tabulation:T=500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 0.34498564053745895 | 0.05 |
| `janaf-4th::JANAF1998_P2_formation_tabulation:T=800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 0.2686241107947893 | 0.05 |
| `janaf-4th::JANAF1998_P4O6_formation_tabulation_UNCERTAIN:T=1000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 595.8531702387063 | 0.05 |
| `janaf-4th::JANAF1998_P4O6_formation_tabulation_UNCERTAIN:T=1200::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 588.6477351845931 | 0.05 |
| `janaf-4th::JANAF1998_P4O6_formation_tabulation_UNCERTAIN:T=1500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 520.6573160164673 | 0.05 |
| `janaf-4th::JANAF1998_P4O6_formation_tabulation_UNCERTAIN:T=1800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 454.44487642807553 | 0.05 |
| `janaf-4th::JANAF1998_P4O6_formation_tabulation_UNCERTAIN:T=2000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 411.13288882744814 | 0.05 |
| `janaf-4th::JANAF1998_P4O6_formation_tabulation_UNCERTAIN:T=298.15::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::JANAF1998_P4O6_formation_tabulation_UNCERTAIN:T=500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 602.7214491113905 | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=1000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -0.20247953020196974 | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=1200::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -4.678866402513819 | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=1500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -68.55010777461884 | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=1800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -130.6239985826359 | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=2000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -171.17144039609832 | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=298.15::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 0.04185941297454665 | 0.05 |
| `janaf-4th::JANAF1998_P4_formation_tabulation:T=800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -0.07312075640304627 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=1000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 34.17285423130056 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=1200::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 33.64006607481434 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=1400::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 23.565787294276106 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=1500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 18.621404532036877 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=1600::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 13.731330446057711 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=1800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 4.098683381209071 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=2000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -5.3574815136906295 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=2200::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -14.658448532362343 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=2500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -28.351240677385732 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=298.15::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 33.13027888537073 | 0.05 |
| `janaf-4th::JANAF1998_PO2_formation_tabulation:T=800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | 33.67872575672442 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=1000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -4.357607706356021 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=1200::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -5.476937762089818 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=1400::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -16.17837767523571 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=1500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -21.446152756203844 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=1600::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -26.665260786370695 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=1800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -36.96722082233276 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=2000::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -47.10600785312542 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=2200::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -57.09878992534115 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=2500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -71.84514679430181 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=298.15::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=500::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -4.295388540535633 | 0.05 |
| `janaf-4th::JANAF1998_PO_formation_tabulation:T=800::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | -4.324562937737184 | 0.05 |
| `janaf-4th::anchor_P2_deltafG:payload::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::anchor_PO2_deltafG:payload::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::anchor_PO_deltafG:payload::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::extreme_reduction_from_phosphate:payload::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| `janaf-4th::extreme_reduction_from_phosphate_P4:payload::delta_fG::thermochemistry::nasa_cea_9` | coverage_failure | None | None | 0.05 |
| … | 23698 more | | | |

## status_diff vs old scorers

No mapped outcome changes.

Unmapped legacy keys: 23748. Old ledgers retained.
