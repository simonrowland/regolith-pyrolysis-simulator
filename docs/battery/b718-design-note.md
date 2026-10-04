# b-718 design note — sample composition inheritance

## Problem
A composition map present on a **strict subset** of an experiment’s observation rows is lifted onto
`experiment.sample.printed_composition` by `_ensure_experiment` → `sample_from_equipment` →
`_merge_experiment_lab_params`. Rows without their own map then select
`normalized_printed_composition` via `waypoints.normalized_composition`’s experiment.sample fallback.
That is the root cause behind b-714 (foreign materials inheriting a neighbour’s melt map).

At tip `8089eadbf` (after b-714 extract scoping): Class B = **4 experiments / 51 inheriting rows /
4 sources / 25 comparison candidates**. Pre-b714 attached sweep had 221 / 7 sources.

## Smallest reader rule
Keep `experiment.sample.printed_composition` as a sample-level fallback **only** when:

1. an **explicit experiment/sample (or charge) declaration** supplies it, or
2. the **same fingerprint is present on every observation row** of that experiment.

If the map is on a strict subset, keep it **local to those rows**
(`observation.point_conditions.printed_composition` / `composition`).

## Consumers (rg) and what each receives afterwards

| Consumer | Today | After |
|---|---|---|
| `waypoints.normalized_composition` | prefers observation `point_conditions.printed_composition`, else `experiment.sample.printed_composition` → route `normalized_printed_composition` | unchanged API; subset-only maps no longer appear on experiment.sample, so foreign rows lose that route unless they carry a row-local map or a legitimate declaration/universal map |
| `waypoints` mass/inventory helpers using `experiment.sample.printed_composition` | read experiment sample | declarations and universal maps still present; subset promotions removed |
| `validity.py` bulk-species gate | uses identity / point composition / `experiment.sample.initial_composition` (not `normalized_composition`) | declaration-backed initial preserved; observation-derived initial no longer silently replaces declaration |
| `score.comparison_candidates` / residual path | composition via identity/point and validity gates | candidates for the 40 same-material rows that keep a declaration or row-local map stay; foreign subset inheritors drop the bogus sample route (expected) |
| `bench.py` melt-activity / engine-point | passes `normalized_composition` | same |

No stored field is deleted from extracts; the migrator stops **promoting** subset maps to the
experiment sample and attaches them on the owning observation instead.

## Implementation sketch
1. Track registry experiments whose `sample.printed_composition` was declared
   (`_registry_printed_composition_experiments`, parallel to `_registry_value_initial_experiments`).
2. In `_ensure_experiment`, never merge observation-row printed maps onto a declared sample
   (do not clear declaration on unequal fingerprints).
3. For non-declared experiments, do not promote observation printed maps onto
   `experiment.sample` during per-row ensure; attach the found map onto that observation’s
   `point_conditions`.
4. After an extract’s observations are migrated: if every observation of a non-declared
   experiment carries the same printed fingerprint, promote once onto `experiment.sample`
   (rule 2).
5. Same branch, extract edits: where inheritance is scientifically right, **declare** the sample
   at experiment level and bind the same-material rows (Bischof an-di-1-low already declares —
   protect it; Hashimoto FCMAS / LLNL CAI / Richter lab CAI-like series — add declarations and
   bind; leave Richter Table 2 natural CAIs and Hashimoto Table 6 JANAF rows unbound to that
   sample).

## Proof contract
- Tip Class B list (51) before/after: zero legitimate losses for the 40 same-material rows
  (declaration or row-local or universal promote); foreign 11 lose only the bogus inherited route.
- Comparison-candidate set and residuals for same-material rows unchanged.
- Re-run sweep: Class B count drops toward 0 for declared sources; remaining B only where
  subset promotion was the sole path and extract edits have not yet bound a declaration.
