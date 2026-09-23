# L1 — near-ready oxygen/pressure closure (A3 sources 1–2)

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/l1-near-ready-oxygen-2026-09-22`  
**Base:** `empirical/e5-kems-037-046-2026-09-22` @ `906147d3e` (Plante tip) + cherry `c7f05c672` (Mendybaev 2017 FUNC oxides from e8)  
**Tip:** `aa6bd6223`
**Date:** 2026-09-22 ~22:10 ET (America/Toronto)  
**PDFs:** both present under `/workspace/batch-z/pdfs/` — no `ASK-pdfs-l1.md`

## Scope (BACKLOG night-2 §L1)

A3 near-ready sources needing oxygen/pressure:

| # | source | A3 missing |
| --- | --- | --- |
| 2 | `kems-042-plante-1979` | `experiment.fO2_control`, `fO2_log`; residual `pressure_boundary` |
| 5 | `mendybaev-2017-fun-cai-lab-evaporation` | same |

Only land what is **printed**. Derived values carry a **derived** stamp. Never invent point pressures from printed ranges/bounds.

## What the papers print

### kems-042 Plante 1979
- **Oxygen:** stoichiometric `P_O2 = 0.226 P_K` (p.279). Per-row `P_K` tabulated (Table 2). Product `P_O2` is **not** a printed cell — it is ratio × printed `P_K`.
- **Pressure:** ionization-gauge chamber range **10⁻⁸–10⁻⁷ Torr** (p.267) — an **interval**, not a point.

### mendybaev-2017 FUN CAI lab evaporation
- **Oxygen:** vacuum free evaporation. “Oxygen” in the paper is **isotopic** (δ¹⁸O / α¹⁸,¹⁶), not fO2. **No** printed fO2 / buffer / pO2.
- **Pressure:** vacuum **&lt;10⁻⁹ bars** during evaporation; pump-down “about 10⁻⁷ bars”; ramp after furnace “~10⁻⁹ bars” — **bound / approximate**, already recorded as such (`not_a_point`).

## What landed

### Code (t951 remainder)
- `simulator/battery/migrate.py` — `collect_author_ratio_oxygen`: when values carry `po2_over_pK_as_published` + `P_K_atm_as_published` + `P_O2_atm` and the product agrees with ratio×P_K, land `point_conditions.fO2_Pa` with a **Derivation** (authority DERIVED). Refuses `pO2_inference` / `inferred: true` / arithmetic disagreement. **Does not** collapse distinct per-row products onto `experiment.fO2_control.oxygen_partial_pressure_Pa`.
- `_merge_printed_oxygen` calls this only after the printed allowlist (`log_fO2` / `oxygen_partial_pressure`) is empty.

### Extract
- `kems-042-plante-1979.yaml` — `fO2_control.channel: intrinsic` documenting the printed stoichiometric ratio; no experiment-level pO2 asserted.
- `mendybaev-2017-…yaml` — **no oxygen invent**. Composition cherry from e8 already on branch. Pressure bound left as printed.

### Tests
- `tests/battery/test_printed_fo2.py` — author-ratio unit cases; Plante 162 derived waypoint fires; Mendybaev has no invented fO2_Pa/fO2_log. **8 passed** (`-o addopts=`).

## Ladder before → after (engine_point / vaporock, live migrate write=False)

### Before (e5 tip @ 906147d3e + e8 cherry; no L1 land)

| Source | obs | oxygen selected | pressure selected | engine_point gaps (per obs) |
| --- | --- | --- | --- | --- |
| **mendybaev-2017** | 11 | 0/11 | 11/11 **bound** (`printed_run_pressure`) | `pressure_boundary` **unsupported_print_form**; `oxygen_condition` **missing_evidence** (`fO2_log`, `experiment.fO2_control`) |
| **kems-042** | 383 | 0/383 | 383/383 **interval** | `pressure_boundary` **interval_needs_point**; `oxygen_condition` **missing_evidence** (`fO2_log`, `experiment.fO2_control`) |

### After (this branch)

| Source | obs | oxygen selected | pressure selected | engine_point gaps (per obs) |
| --- | --- | --- | --- | --- |
| **mendybaev-2017** | 11 | 0/11 (unchanged — not printed) | 11/11 **bound** (unchanged) | same pressure form + oxygen missing |
| **kems-042** | 383 | **162/383** `observation_fO2_Pa_to_log_fO2` / **DERIVED** (Table 2 K2O activity rows with ratio keys) | 383/383 **interval** (unchanged) | `pressure_boundary` **interval_needs_point** (383); `oxygen_condition` missing on **221** non-ratio rows; `normalized_composition` missing (383) — see residual |

## Residuals (honest; not invented)

1. **pressure_boundary form** — both papers print non-point chamber/vacuum pressures. Interval/bound already selected as `printed_run_pressure`; engine_point refuses non-POINT (`interval_needs_point` / `unsupported_print_form`). Collapsing to a midpoint would invent.
2. **mendybaev oxygen** — not printed as fO2. Leave GAP.
3. **kems-042 oxygen on 221 quoted-K rows** — no ratio/`P_O2_atm` keys; correctly unselected.
4. **kems-042 normalized_composition** — e5 X5 tip (`906147d3e`) replaced running Table 2 oxides with crystalline `K2Si2O5` label; migrator does not treat that string as an oxide map (composition gap appears on this tip). **Out of L1 oxygen/pressure scope**; do not re-land running ion-current wt% as start.

## READY status

Neither source is fully **READY** for `engine_point` after this lane:
- Plante: oxygen closed on the 162 ratio rows; pressure still interval; composition residual from e5 tip.
- Mendybaev: oxygen not printed; pressure still bound.

Lane is the honest READY **path**: printed ratio oxygen landed as DERIVED; non-point pressures preserved; no invented fO2/pressure points.

## Push

Branch pushed: `aa6bd6223` on `origin/empirical/l1-near-ready-oxygen-2026-09-22`.
