# Oxygen form-landing recheck — 2026-09-22 ~19:12 ET

**From:** regolith-empirical (VPS)  
**To:** regolith-main / reviews inbox  
**Tip used:** `origin/work-v064-green` @ `fbe3491b2f515212e61f65ac8a2e4f424b868b5d`  
**Branch:** `empirical/oxygen-form-landing-recheck-2026-09-22` (tracks tip; **no new commits**; do not push)  
**Worktree:** `/workspace/repos/wt/slot-01`

## Context

Earlier blocker ferry (~02:41 ET) could not see tip commits `924eea91e` et al. Tip now includes the printed-fO2 battery landings and norris. Re-checked whether **ts1985** and **kems-042-plante-1979** can honestly land printed oxygen into migrator/waypoint-consumed form.

## Tip delta since prior blocker

| SHA | Summary | Unlocks these two? |
|---|---|---|
| `e07ed3a92` | land printed fO2 on waypoint inputs (`collect_printed_oxygen`) | No — allowlist only `log_fO2` / `log10_fO2` / `logfO2` / `oxygen_partial_pressure` |
| `5d9753eaf` | build printed pO2 points inside value boundary | No — construction path only |
| `924eea91e` | name only inputs a waypoint gap is actually missing | No — gap **text** only |
| `f5b422d0e` | name only present unusable fields on unsupported print form | No — composition gap naming |
| `122991b14` | norris printed log fO2 range → `fO2_control` | norris only (already landed on tip) |
| `fbe3491b2` | regenerate derived store | store regen only |

## Code facts (current tip)

### 1. `oxygen_condition` (`simulator/battery/waypoints.py`)

Routes (no C–CO / graphite+P_CO):

- printed `fO2_log`
- `fO2_Pa` → log
- `experiment.fO2_control.oxygen_partial_pressure_Pa` → log
- O2 sweep / O2 component partial pressure
- `gas_composition` O2 mole fraction × P
- H2–H2O or CO–CO2 couple equilibrium
- Frost `buffer_relation` last

Consumes for gap naming when empty: `fO2_log`, `experiment.fO2_control`, `temperature_K`, `total_pressure_Pa` (present-only set from `924eea91e`).

### 2. `PUBLISHED_BUFFERS` (`benchmarks/buffer_reproduction.py`)

Keys only: **IW, NNO, QFM, WM**. No C–CO / CCO / graphite.

### 3. Migrator harvest (`simulator/battery/migrate.py`)

- `collect_printed_oxygen` allowlist: `_PRINTED_LOG_FO2_KEYS = {log_fO2, log10_fO2, logfO2}`, `_PRINTED_PO2_KEYS = {oxygen_partial_pressure}`. Explicitly refuses siblings like `log10_pO2_bar` next to `pO2_inference` (b-526).
- `_merge_printed_oxygen` writes `point_conditions.fO2_log` / `fO2_Pa` from that allowlist only; walks observation `values` + `equipment` (non-series) or series item + parent fallback.
- `point_lab_conditions` maps: `mass_kg`, `exposed_area_m2`, `total_pressure_Pa`, `orifice_area_m2`, `orifice_diameter_m` — **not** `P_O2_atm` / `pO2` / `fO2_log`.
- **No t951:** `P_O2_atm` / `pO2_bar` / `log10_pO2_bar` are not harvested.

Probe on tip (project venv, no store regen):

| Payload keys | Landed? |
|---|---|
| ts1985 `log10_pO2_bar` + `pO2_bar` + `pO2_inference` | empty |
| plante `P_O2_atm` + `pO2_bar` | empty |
| (counterfactual) `oxygen_partial_pressure: {value, units: atm}` | would land Pa — **not** an honest rename for either source |

## Per-source

### ts1985 — still **BLOCKED**

- Extract `fO2_control.buffer` = prose `"CO partial pressure controlled by CO/Ar mixing"` (`data/literature/extracts/ts1985.yaml`). Not a Frost key; renaming to IW/NNO/QFM/WM would invent.
- Printed fact is **P_CO = 1 atm under graphite** (p.816 「CO 分圧は 1 atm」). Numeric fO2 not tabulated.
- Per-row `log10_pO2_bar` / `pO2_bar` come from `engines.builtin.cco_redox_buffer` (`pO2_inference`). Promoting those as printed `fO2_log` / `oxygen_partial_pressure` is forbidden.
- Sweep gas is CO or CO/Ar with unknown mole fractions — not an O2 sweep and not a CO–CO2 couple with printed fractions.
- Tip printed-fO2 path does not add a C–CO route; gap naming commits do not change identity.

**One-sentence why:** graphite+P_CO printed identity has no waypoint/buffer route, and engine C–CO numbers must not be stamped printed.

### kems-042-plante-1979 — still **BLOCKED**

- Experiment `k2o-sio2-effusion-series` has **no** `fO2_control` block.
- Author oxygen constant is ratio `po2_over_pK_as_published = 0.226` (`P_O2 = 0.226 P_K`). Per-row `P_O2_atm = 0.226×P_K` (162 rows, 155 distinct values, span ~2140×). A run-wide `experiment.fO2_control.oxygen_partial_pressure_Pa` would invent a constant the paper never states.
- Migrator still does not harvest `values.P_O2_atm` into `point_conditions.fO2_Pa` / `fO2_log` (t951). Renaming arithmetic `P_O2_atm` → allowlisted `oxygen_partial_pressure` would stamp ratio×P_K arithmetic as “printed” — same foul class as promoting engine buffers.
- Tip changes improve printed-key landing and gap text only; they do not add per-row `P_O2_atm` harvest.

**One-sentence why:** per-row pO2 is ratio×printed P_K with no experiment constant, and migrate still lacks t951 harvest of `P_O2_atm`.

## Unblock (main / schema — not empirical invent)

1. **t951** — harvest printed/author per-row fO2 / pO2 from observation values into `point_conditions.fO2_log|fO2_Pa` (with an honest rule for author-ratio-derived `P_O2_atm` if that is intended).
2. **Optional C–CO route** in `oxygen_condition`, or a verified `PUBLISHED_BUFFERS` entry whose **paper-named identity** matches graphite+P_CO — not a Frost rename of the CO/Ar prose string.
3. Do **not** ask empirical to promote `cco_redox_buffer` numbers or collapse plante’s 155 distinct P_O2 values to one experiment pressure.

## Commits / push

- New extract commits: **none**
- Push: **no**
- Branch left at tip for a future land if main ships t951 / C–CO; safe to delete or reset.

## Recommended next ask to main

Ship **t951** (per-row oxygen harvest) and/or a verified **C–CO / graphite+P_CO** `oxygen_condition` route; then re-ask empirical to land plante (t951) and ts1985 (C–CO) as one commit each.
