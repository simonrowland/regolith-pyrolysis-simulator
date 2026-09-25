# Battery score report (schema v2.1)

Generated only. Pins are an independent baseline and are never
re-centred from these residuals. Refusals are diagnostics, never hidden.
Headline accuracy per rail is the product of score_eligible rows;
a rail with zero eligible references is reported as zero.

Hostname: `Simon-MacBookPro-M5.local`.

This report measured store `bfcd28637`: 46574 rows in, 103877 observations, 255 works, 2988 experiments, queue 70645, 1499 hard issues.

Engines: vaporock, alphamelts, thermoengine, magemin, imcc_sf04, imcc_sf04_ext, internal-analytical.

No engine residuals were regenerated for this store revision.
Match rate is blank. score_eligible is 0.
The live candidate census is the published candidate count.

## Live candidate census

Comparison candidates are measured rows admitted or pending.
Admitted is split from pending. Counts are observations, not
residuals, so they are not multiplied by the engine set.
A vapour candidate is only `p_sat`, `p_partial`, or `p_reference`.

| rail | candidates | admitted | pending | points |
|---|---:|---:|---:|---:|
| vapour | 246 | 0 | 246 | 230 |
| melt_activity | 43 | 0 | 43 | 37 |
| thermochemistry | 6 | 0 | 6 | 3 |
| SiO_evolution | 4 | 0 | 4 | 3 |
| pyrolysis_yield | 69 | 40 | 29 | 69 |
| wall_deposition | 0 | 0 | 0 | 0 |
| redox | 0 | 0 | 0 | 0 |
| alkali_shuttle | 9 | 0 | 9 | 9 |

### No headline rail

These quantities are not vapour candidates and not SiO_evolution
candidates. The reason is the refusal, not a borrowed rail.

| reason | candidates | admitted | pending | points |
|---|---:|---:|---:|---:|
| `non_alkali_kinetic` | 8 | 0 | 8 | 2 |
| `not_a_vapour_quantity` | 110 | 0 | 110 | 0 |
| `quantity_unknown` | 1411 | 0 | 1411 | 0 |

## Per rail × engine headline

Not regenerated. Match rate is blank. score_eligible is 0.

## Refusal census

Engine refusals were not regenerated with this census. Rows with no headline rail are counted above and are not hidden.

## Admission

score_eligible requires canonical admission admitted. Pending rows
stay in the comparison set as diagnostics (flagged and priced when
numeric, never the empirical headline). The admission rule is unchanged.

| count | n |
|---|---:|
| comparison candidates pending | 1866 |
| comparison candidates admitted | 40 |
| residuals with admission_admitted exclusion | 0 |
| residuals that die on admission alone | 0 |
| unique observations that die on admission alone | 0 |

## Notice backlog (certification projects)

| notice kind | n |
|---|---:|
| (none) | 0 |

## Internal-consistency / compilation diagnostics

species_rail_differential_ledger and gibbs_battery_residual_ledger
are internal-consistency instruments, not scoring ledgers. Compilation
observations are engine reference inputs. Neither population enters
the empirical headline. Do not validate an engine against a compilation it consumes.

Diagnostic residuals in this file: 0.

## Pin failures

Pins were not compared; no residuals ledger was regenerated for this store revision.

## status_diff vs old scorers

status_diff was not run; no residuals ledger was regenerated for this store revision.
