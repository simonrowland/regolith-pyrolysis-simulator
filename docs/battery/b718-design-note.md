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

## Review-of-record fixes (regolith-main ROR-b718, 2026-10-06)

The rule above was right; the implementation had a second path that broke it.

- **Nested row maps (P0).** `sample_from_equipment` walks into `values.rows`, `series`,
  `points` and `tests`, so a map printed on one nested row (quoted Hastie Table 2, body row 16
  illite) was attached to the parent's `point_conditions`. Every exploded sibling inherited it, and
  the universal promotion then saw "one fingerprint on every row". The parent now attaches a map
  only if the observation prints it outside every nested row list
  (`_printed_map_is_observation_level`). The owning row still carries its own map through
  `_oxide_map_from_mapping` in `_emit_exploded_point`.
- **Row-local maps never promote.** A child whose printed map is its own row's (not inherited from
  the parent) blocks rule 2 (`_row_local_printed_observations`). One row's measured composition,
  often a residue after mass loss, is not the experiment's sample.
- **Printed initial-charge row.** The one nested row the source prints as the series' starting
  charge (`T_C_is_initial_composition: true`, or T_C = 0 with 0 mass loss) stays on
  `experiment.sample` (`_is_printed_initial_charge`). This restores Markova 1984 Table 2.
- **Declaration owns the sample.** A row that only restates the declared map (often with an extra
  printed `Total`) does not attach and shadow it (`_repeats_declared_sample_map`). A prose
  declaration ("12 MgO, 46 SiO2, ...") is not an oxide map, so it does not get protection.
- **One owner of map identity.** `_printed_fingerprint` is used by the roots walk, the
  attachment gate, the declared-repeat check and the promotion.
- **Hashimoto depleted rows.** `hashimoto_1983_cao_al2o3_residue_enrichment` and
  `hashimoto_1983_stage_iv_cao_relative_volatility` are unbound from `fcmas-free-evap-series`.
  They describe a depleted state, and the readers cannot yet tell that state from the initial
  charge.

- **Hashimoto Fe class-B1 row.** `hashimoto_1983_fe_geometry_class_b1` is bound to
  `fcmas-free-evap-series` like the Mg and SiO class-B1 rows (same preform, same Table 1 map), so
  the three no longer split across a declared and an auto experiment.
- **Sweep script.** `b718-sweep.py` now uses `migrate._printed_fingerprint` for map identity
  instead of its own copy. The recorded "post-fix Class B = 0" in `b718-sweep-after-fix.md` was
  produced by the reviewed commit and is a false negative for quoted Hastie Table 2: once every
  child carried the copied parent map, the sweep's universal rule counted the copy as agreement.
  That file is kept as the record of the reviewed run, not as evidence for this fix. The quoted
  Table 2 shape is now pinned by unit and real-extract tests instead.

The composition role (printed analysis / calculated with derivation / initial charge only /
two-phase bulk) is still not a field. See the fix REPORT for the proposal.
