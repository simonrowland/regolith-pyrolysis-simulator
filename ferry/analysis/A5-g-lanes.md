# A5 — G-lanes (ENGINEERING) for `regolith-empirical`

Generated 2026-09-22 21:45 EDT (America/Toronto). Inputs: `A1-hard-issues.md`, `A2-migration-queue.md`. A1 is a single DATA provenance class (`derived_from` / `derivation`); **no A1 item is a G-lane**. All five lanes below are A2 ENGINEERING reasons where existing payload/provenance is sufficient.

Base for implementation: `origin/work-v064-green` @ `2e9e17c3d`.

## Ranking rule

Order by exact-reason volume among **ENGINEERING** rows in A2 (paired quantity/value refusals merged when one code change closes both). Pure DATA (ranks 12, 14–15) and PERMANENT (ranks 1, 9–10) are excluded.

| G | A2 ranks | Approx. queue volume | Axis | One-line fix | Branch |
|---|---|---:|---|---|---|
| **G1** | 2 (formation-enthalpy subset), 3, 4 | ~5.5k direct + clears matching rank-2 ATcT/NASA-Glenn rows | `quantity` | Project printed formation-enthalpy fields onto existing `Quantity.DELTA_FH`, select the published numeric amount, and drop the obsolete “not a v2.1 Quantity token” queue. | `empirical/g1-delta-fh-quantity-2026-09-22` |
| **G2** | 8, 11 | 2,978 | `phase` | Add lossless `PHASE_MAP` aliases `(g)` → `Phase.G` and `(c)` → `Phase.CR` (published parenthetical spellings). | `empirical/g2-phase-paren-aliases-2026-09-22` |
| **G3** | 5, 6 | 3,760 | `quantity`/`value` | Lift ledger/rail vaporization-reaction identity into structured reactants/products and map the Gibbs table value to the reaction-aware quantity (dedupe the paired refusal). | `empirical/g3-vaporization-reaction-lift-2026-09-22` |
| **G4** | 7 | 1,855 | `read_from` | Register compilation record JSON paths as Work INDEX assets (or resolve record → compilation parent asset) in `choose_read_from` before falling back to `unknown`. | `empirical/g4-compilation-index-assets-2026-09-22` |
| **G5** | 16, 17, 19 (+ residual rank 2) | ~1.5k+ residual multi-column | `value`/`quantity` | Add deterministic label→Quantity maps for multi-column Cp/S/H/ΔHf/ΔGf tables and emit one observation per declared series; never pick the first numeric cell. | `empirical/g5-compilation-column-explode-2026-09-22` |

Deferred (still ENGINEERING, not in top-5 tonight): rank 13 `T_range_K` range-aware identity; rank 18 two-phase transition representation; rank 20 `K2O-SiO2_binary_silicate_melt` system/melt normalization; burcat `nasa7_polynomial` evaluator-as-quantity (coefficient intervals, not a single closed observable — belongs with G5/evaluator policy, not G1).

## G1 — `delta_fH` / ATcT quantity projection

**Fix:** Wire already-printed `formation_enthalpy_298_15_K_as_published` / `delta_f_H_298_15` (and aliases) through `_COMPILATION_CELL_QUANTITY`, top-level `compilation_quantity_from_record`, `QUANTITY_SOURCE_FIELDS[Quantity.DELTA_FH]`, and `_UNIQUE_QUANTITY_FIELDS`; remove the false queue in `_migrate_compilation_file` that claims delta_fH is not a v2.1 token (`Quantity.DELTA_FH` already exists). Do **not** invent `temperature_K=298.15` from the field name.

**Files:** `simulator/battery/migrate.py`, `tests/battery/test_migrate.py`.

**Test + mutation-proof plan:**
1. Unit: `compilation_quantity_from_record` on ATcT + NASA-Glenn fixtures → `Quantity.DELTA_FH`, reason `None`.
2. Integration: migrate copied ATcT + NASA-Glenn records → observation quantity is `delta_fH`, value is `POINT` with the published numeric amount; queue must not contain either obsolete token reason.
3. **Mutation:** delete the new cell/source-field entries (or restore the obsolete queue block) → assert the old reasons reappear / quantity stays unknown. Live path must stay green.
4. Guard: `atomic_weight` / `_COMPILATION_KIND_NOT_QUANTITY` rows still refuse with the compilation-columns reason (no accidental widen).

**Seat:** `/workspace/repos/wt/slot-06` ← soft checkout from `origin/work-v064-green`.

## G2 — phase paren aliases `(g)` / `(c)`

**Fix:** Extend `PHASE_MAP` with `"(g)"` → `Phase.G` and `"(c)"` → `Phase.CR` only. Do not alias multi-phase spans (`(c,l)`, `(c, l, g)`, …) or invent liquid from `(l)` in this lane unless a separate reviewed alias lands later.

**Files:** `simulator/battery/migrate.py`, `tests/battery/test_migrate.py`.

**Test + mutation-proof plan:**
1. Unit: `map_phase("(g)")` / `map_phase("(c)")` → `Phase.G` / `Phase.CR`; `map_phase("(c,l)")` and `map_phase("silicate_melt")` stay unknown.
2. Integration: migrate a Kelley/Pankratz record that prints `(g)` or `(c)` → species.phase is valued; reason does not contain `not in the closed automatic map`.
3. **Mutation:** remove the two aliases from `PHASE_MAP` → assert `map_phase` returns the closed-map unknown reason again.
4. Guard: existing `test_g01_map_phase_refuses_heuristics` / `test_h13_canonical_phase_tokens_are_reviewed_identity` remain green.

**Seat:** `/workspace/repos/wt/slot-g2` (new worktree) or clean `slot-01`.

## G3 — vaporization reaction lift (plan only tonight)

**Fix:** Promote existing ledger note/rail reaction metadata for `log10_Psat_over_P0` / Gibbs-of-vaporization rows into structured reaction identity; stop dual-queueing quantity+value once lifted.

**Test + mutation-proof:** fixture ledger point with printed reaction note → quantity/value admitted; mutant that strips reaction structure re-queues both axes with the current reason.

## G4 — compilation INDEX assets (plan only tonight)

**Fix:** When `locator.source_path` names `data/literature/compilations/.../records/*.json`, resolve or register a matching Work asset before `_unknown_asset_id`.

**Test + mutation-proof:** copied compilation record → `read_from` is a real asset id; mutant that skips record-path resolution restores `has no matching INDEX asset`.

## G5 — multi-column compilation explode (plan only tonight)

**Fix:** Map published column censuses (Cp/S/H/ΔHf/ΔGf and Kelley-style `cp_*_k` / entropy columns) to closed Quantities and emit one observation per declared series.

**Test + mutation-proof:** multi-column fixture emits N observations with distinct quantities; mutant that selects the first numeric column fails the identity assertion.

## Implementation status (this turn)

| Lane | Status |
|---|---|
| Plan file | this document |
| G1 | **DONE** tip `1c97733d0` on `origin/empirical/g1-delta-fh-quantity-2026-09-22`; review `reviews/G1-delta-fh-quantity.md` |
| G2 | **DONE** tip `143a9c094` on `origin/empirical/g2-phase-paren-aliases-2026-09-22`; review `reviews/G2-phase-paren-aliases.md` |
| G3–G5 | planned only |

