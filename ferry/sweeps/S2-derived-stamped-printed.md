# S2 — derived value stamped as printed/measured

**Repo tip:** `a49cef012` (`origin/review/janaf-batch-2026-09-22`).  
**Scope:** `simulator/battery/`, `scripts/` (bench/battery), `tools/validate_literature_extracts.py`, `data/literature/build_index.py`.  
**Predicate:** a value computed, defaulted, converted or inferred by code ends up with a provenance/state/method flag claiming printed or measured (or loses a flag saying derived). Includes unit conversions, buffer→absolute fO2, interval→point, and copies between containers.  
**Seed:** b-552 — 59 inferred `total_pressure` stamped PRINTED. Lab-vocab path (`_located_from_hit`) now always attaches a `Derivation` (including `extract_inference` when `inferred: true`); residual LIVE hits are SI fields that never get an `inference` block, plus convert-then-`located_value` drops.

**Live corpus check (2026-09-22, tip `a49cef012`):**
- `data/literature/works/**`: **30** `total_pressure_Pa` blocks with note matching `unit conversion` and **no** `inference:` (19 files) — waypoints stamp `WaypointAuthority.PRINTED`.
- `data/literature/extracts/**`: **≥1600** `T_C:` rows; migrate main observation path wraps converted K via `located_value` with **no** conversion `inference`.
- Lab `chamber_pressure` + `inferred: true`: 3 extract sites; works store shows `inferred=true` on inference inputs when the lab path ran (seed path mitigated).

| site (file:line) | predicate match | trigger input | live? | severity |
|---|---|---|---|---|
| `simulator/battery/migrate.py:6949` (`point_conditions = {"temperature_K": located_value(t_known, locator)}` after `select_declared_source(AXIS_TEMPERATURE_K)` at `:6740`) | `select_declared_source` returns Kelvin after `celsius_to_kelvin` (`unit_trail=celsius_to_kelvin`), but `located_value` drops the trail — no `Located.inference`. `_point_condition` (`waypoints.py:227`) then stamps **PRINTED**. Series-point path at `:7334` correctly keeps `conversion_derivation(t_trail, …)`. | Observation `values: {T_C: 1600}` (or `T_C` series cells as in `kems-015-hashimoto-1983`, `kems-010-richter-2007`). | **LIVE** (≥1600 `T_C` extract rows) | P0 |
| `simulator/battery/waypoints.py:603-604` (`printed_run_pressure` / `WaypointAuthority.DERIVED if run_pressure.inference else …PRINTED`) | Unit-converted (or inferred) Pa with `inference is None` is selected as **PRINTED**. Seed residual: extract-native `pressure_environment.total_pressure_Pa` already in Pa with locator note `unit conversion only` never carries a Derivation through `_located_from_plain` (`migrate.py:681-692`). | Extract/experiment block e.g. `kems-010-richter-2007` / works `10.1038_nature23645`: `total_pressure_Pa` note `Printed one atmosphere; unit conversion only`, no `inference:`. | **LIVE** (30 works blocks / 19 files) | P0 |
| `simulator/battery/waypoints.py:873` (`temperature_points_only`) | Invents series `((0, T))` from a lone `conditions.temperature_K` and stamps **PRINTED** (computed time axis + packaging, not a printed schedule). | Experiment with POINT `conditions.temperature_K` and empty/absent thermal schedule. | **LIVE** (common when only a setpoint T is extracted) | P1 |
| `simulator/battery/migrate.py:7545` (`_generic_obs` → `located_value(temperature_K, locator)`) | Same drop-inference pattern as `:6949` on the generic observation emitter (compilation / ledger / named sidecars). | Celsius (or other non-K) temperature fed into `_generic_obs` after conversion to K. | latent (same defect; fewer extract callers than `:6949`) | P1 |
| `simulator/battery/migrate.py:1730-1731` (`def located_value`) | Helper always builds `Located(State.of(value), locator=locator)` with **no** inference slot — any convert-then-wrap loses the derived flag (copies between containers). Callers include `:6949`, `:7545`, form/container fingerprints `:4946+`. | Any converted Decimal passed through `located_value` instead of `Located(..., inference=conversion_derivation(...))`. | latent (enabler) | P2 |
| `simulator/battery/migrate.py:6867` (`ident_kwargs["temperature_K"] = State.of(Decimal("298.15"))`) | Defaulted 298.15 K when `Delta_f_G_298_*` present without T — stored as ordinary `State.of` known value (no assumed/derived marker). Downstream identity consumers treat it as a stated temperature. | Observation with `Delta_f_G_298_kJ_mol` (or alias) and no T axis. | **LIVE** (path still armed; few current extracts hit it) | P2 |

## Seed path status (b-552)

| site | status |
|---|---|
| `migrate.py:4699-4740` `_located_from_hit` | **Mitigated on tip:** always attaches `Derivation`; `inferred: true` → `relation=extract_inference` + `inferred=true` in inputs. Tests: `tests/battery/test_migrate_lab_params.py`, `tests/battery/test_waypoints.py::test_inferred_run_pressure_never_carries_printed_authority`. |
| `migrate.py:5425-5457` `_pressure_located` (printed O₂) | Conversion → Derivation; identity units stay `inference=None` (true print). Inferred / bound / range nodes refused (`_oxygen_node_rejected`). Not an S2 hit. |

## Counts

- Sites: 6  
- Live: 4  
- P0: 2 · P1: 2 · P2: 2 · P3: 0  

## No-hit areas (in scope, audited)

- **Buffer → absolute fO2 stamped printed:** `oxygen_condition` buffer / pO₂ / gas-couple routes use `WaypointAuthority.DERIVED` (`waypoints.py:893-982`); calculated `log10_pO2_bar` refused as printed (`migrate.py:5332-5335`, R3/b-526).  
- **Interval → point:** migrate refuses midpoints (`migrate.py:6749/6857/7800/7881/7906`); tip adds engine_point interval refusal (`a49cef012`).  
- **Lab-vocab inferred pressures (seed core):** `_located_from_hit` + waypoint inference gate — fixed; not counted as an open site.  
- **`tools/validate_literature_extracts.py`:** requires derivation prose when `inferred: true` on geometry; does not stamp printed/measured on converted values.  
- **`data/literature/build_index.py`:** measured-evidence class filter / store tallies; no waypoint authority or provenance stamp of this class.  
- **`scripts/bench_*.py`, `scripts/battery_*.py`, `scripts/calibration_battery.py`:** scoring `authority` (bridge/extrapolated/refused) is a different vocabulary; no PRINTED stamp on converted literature pressures/T.  
- **`simulator/battery/generators/` (JANAF / USGS):** `UncertaintyKind.PRINTED` attaches to table-sibling printed uncertainties / as-published cells — matches printed source, not a derived-as-printed hit.  
- **wt% → mole fraction:** initial composition carries `wt_pct_to_mole_fraction_derivation` (`migrate.py:2131-2135`); waypoints mark DERIVED when `composition.inference` set.

SWEEP: S2 | sites=6 | live=4 | P0=2 P1=2 P2=2 P3=0 | no-hit areas: buffer→absolute fO2 printed; interval→point midpoints; lab-vocab inferred pressure (b-552 mitigated); tools/validate_literature_extracts.py; data/literature/build_index.py; scripts/bench|battery|calibration_battery authority vocab; generators/ JANAF·USGS printed uncertainty; wt%→mole-fraction derivation
