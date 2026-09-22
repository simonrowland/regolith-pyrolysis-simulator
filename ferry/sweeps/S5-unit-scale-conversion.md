# S5 — unit / scale conversion slips

**Repo tip:** `c324151ee` (`empirical/reviews-2026-09-22`); live product paths also checked on `work-v064-green` (`fbe3491b2`) and base `origin/review/janaf-batch-2026-09-22` (`a49cef012`). Seed site is on `origin/review/janaf-p4a1-pure-phase` (`037f84bce`) — not landed on tip.  
**Scope:** `simulator/`, `engines/`, `scripts/`, `tools/` (prefer local arithmetic over shared converters).  
**Predicate:** conversion between Pa/bar/kbar/GPa/atm/torr/mbar, K/C, J/kJ/cal, mol/kg/g, wt%/mol%/fraction, log10/ln, or per-formula-unit vs per-oxide/per-atom basis where the factor is **wrong, duplicated, inverted**, or an **already-converted** value is converted again.  
**Seed:** P4a-1 / R5 — MAGEMin `pure_phase_properties` bar→kbar with `1e-4` instead of `1e-3` (`ferry/reviews/R5-pure-phase.md`).

Read-only sweep (no product edits).

| site (file:line) | predicate match | trigger input | live? | severity |
|---|---|---|---|---|
| `simulator/melt_backend/magemin.py:2247` on `origin/review/janaf-p4a1-pure-phase` (`037f84bce`) — `pure_phase_properties`: `pressure_kbar = float(pressure_bar) * 1.0e-4` (comment claims 1 kbar = 1000 bar). Tip `equilibrate` / `_call_magemin` uses `_pressure_bar_to_GPa` (`/1e4`) then `_GPa_to_kbar` (`*10`) → net `bar * 1e-3`. Companion test `tests/test_pure_phase_access.py` (~L277) passes `pressure_kbar=1.0e-4` as “1 bar”. **Absent from tip** (`c324151ee` / `fbe3491b2`). | bar→kbar factor **10× low** (local arithmetic, bypasses shared helpers). Seed P4a-1. Self-masks at 1 bar JANAF anchors (~J/mol VΔP). | `pure_phase_properties(..., pressure_bar=1.0)` → CLI `--Pres=0.000100` kbar (= 0.1 bar). At `pressure_bar=1000` → 0.1 kbar instead of 1 kbar. | latent (review branch only; not on tip) | P1 |
| `simulator/battery/migrate.py:5368-5374` (`_power_of_ten`) + `:5541-5561` (`_walk_printed_oxygen` for `_PRINTED_LOG_FO2_KEYS`) | Shared `10^n` parser returns the **linear** amount `10**n`. For `log_fO2` / `log10_fO2` with `units ∈ {log10, log, …}` that linear amount is stored as the log. `"10^-9.1"` → `≈7.94e-10` stamped as `fO2_log` (≈0 on any dex scale) instead of `-9.1`. Pressure keys using the same helper are fine (linear × unit → Pa). | Extract observation: `log_fO2: {value: "10^-9.1", units: log10}` (no companion numeric log). `collect_printed_oxygen` lands log ≈ `7.94e-10`. | latent (live Badro `ta-badro-2021.yaml` keeps numeric `log_fO2: -9.1` and puts `10^-9.1` on the **atm pressure** companion) | P1 |
| `simulator/diagnostic_helpers/binary_pot_scoring.py:313` (`_row_temperature_K`) | °C→K uses **`+ 273.0`**, not `273.15` / `CELSIUS_TO_KELVIN_OFFSET`. Offset **0.15 K low** on the `T_C` row path. | Scoring row with `T_C: 1400` and no `T_K` / obs `T_K`. Yields `1673.0` instead of `1673.15`. | latent (most rows prefer `T_K`) | P2 |
| `simulator/diagnostic_helpers/binary_pot_scoring.py:633` (`_gamma_field_temperature_K`) | Same wrong `+ 273.0` when parsing `gamma_<NNNN>C` field names. | Fields `gamma_1370C` / `gamma_1380C` / `gamma_1390C` (e.g. `data/literature/extracts/kems-057-kambayashi-1985.yaml`). | **live** | P2 |
| `simulator/diagnostic_helpers/extract_reproduction.py:1017-1018` (Antoine eval fallback) | When `source_form` lacks explicit Pa/bar/mmHg markers, scales by **A-magnitude heuristic**: `P = 10**log10_p` if `A >= 7` else `(10**log10_p)*1e5`. Misclassifies Pa-scale A&lt;7 (inflate 1e5×) or bar-scale A≥7 (skip ×1e5). Same class as the removed VapoRock magnitude heuristic. | Antoine coef with `A=6.5`, `source_form` silent on units, true fit in log10(Pa) → pressures reported 1e5× high into reproduction. | latent | P2 |
| `simulator/condensation.py:1585-1639` (`_chapman_enskog_d_ab_m2_s`) | Prefactor **0.00266** is the Reid/Marrero–Mason **bar** form, but code divides by `P[atm] = P_Pa/101325`. Documented ~**1.22–1.325%** overstate of `D_AB` vs BSL-equivalent / bar form. Live call at `:6764`. | Any condensation flux using Chapman–Enskog (default SiO/N2 path). Sanity in docstring: 0.049693 vs 0.049096 m²/s. | **live** | P3 |
| `tools/normalize_extract_store.py:854-855` (`normalize_equipment` mbar branch) | mbar→Pa factor **100** is correct, but conversion trail is stamped **`p_bar_to_p_Pa`** (algebra elsewhere: `p_Pa = p_bar × 1e5`). Wrong conversion **identity** on the Change record — invites a later reader to re-apply ×1e5 (double-convert). | Equipment `chamber_pressure: {value: 1.3e-8, units: mbar}` → value 0.0013 Pa with trail name claiming bar. | latent | P3 |
| `tools/normalize_extract_store.py:812-823` (`log10_P_Pa` / `log10_p_Pa` branch) | `p_Pa = 10**log_pa` is arithmetically correct for a Pa-log, but stamps conversion **`log10_pO2_bar_to_pO2_bar`** (wrong unit family / id). Bookkeeping slip that can drive a second bar↔Pa scale if the trail is trusted. | Observation point carrying `log10_P_Pa` without a prior Pa absolute. | latent | P3 |

## Counts

- Sites: 8  
- Live: 2  
- P0: 0 · P1: 2 · P2: 3 · P3: 3  

## No-hit areas (in scope, audited)

- `simulator/physical_constants.py` + `simulator/battery/identity.py` — shared `PA_PER_BAR` / `STANDARD_ATMOSPHERE_PA` / `THERMOCHEMICAL_CALORIE_J` / `celsius_to_kelvin`; factors match IUPAC/CIPM.
- `simulator/battery/migrate.py` `convert_pressure_to_pa` — Pa/kPa/bar/atm/Torr/mbar factors correct; **GPa/kbar unmapped → refuse** (no silent 1:1). `convert_temperature_to_k` uses +273.15.
- Tip `simulator/melt_backend/magemin.py` `_pressure_bar_to_GPa` / `_GPa_to_kbar` on `equilibrate` — net bar×1e-3 (correct); **no** `pure_phase_properties` on tip.
- `simulator/melt_backend/sulfsat.py:515` — `P_kbar = P_bar/1000` (correct); round-trip `*1000` at `:626`.
- `engines/alphamelts/thermoengine.py:1224` — bar→MPa `/10` (correct); J/bar→m³ `*1e-5` documented.
- `simulator/runner/__init__.py`, `simulator/evaporation.py`, `simulator/ceramic_classifier.py`, `simulator/equilibrium.py` — mbar↔bar via `*1e-3` / `/1000` consistently.
- `simulator/melt_backend/vaporock.py` — declared `vapor_pressure_units` scale only; magnitude heuristic removed; log10(bar)→Pa uses `*1e5` once.
- `tools/normalize_extract_store.py` Torr/atm/bar and C→K conversion **numeric** tables (aside from trail-name sites above).
- `scripts/` temperature paths using `273.15` / `CELSIUS_TO_KELVIN_OFFSET` (binary_pot_scoring is the exception counted above).
- kcal↔kJ (`identity.kcal_th_to_kJ_per_mol`, Ellingham NiO `2×4.184`) and g↔kg molar (`MOLAR_MASS`/1000) — no inverted factors found under this predicate.
- Formula-basis / `formula_divisor` (en Mg2Si2O6→MgSiO3) — only on p4a1 pure-phase review branch; tip has no pure-phase divisor path.

SWEEP: S5 | sites=8 | live=2 | P0=0 P1=2 P2=3 P3=3 | no-hit areas: physical_constants/identity shared converters; migrate convert_pressure_to_pa (incl. GPa refuse); tip magemin equilibrate helpers; sulfsat bar/kbar; thermoengine bar/MPa + J/bar; runner/evaporation/ceramic mbar↔bar; vaporock declared-unit scale; normalize Torr/atm/bar/C→K numerics; scripts 273.15 paths; kcal/kJ + g/kg molar; formula_divisor (tip absent)
