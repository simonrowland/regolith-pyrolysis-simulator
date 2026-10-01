# REVIEW — delta: review/residue-rail-sq (qualitative-condition + nine-rail fix)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `ebbb6352e1923fc09e602909ed1bad212c8d4fc9` (`empirical/review-residue-rail-delta`)
- **Parent / merge base:** `07ebb48f88e726e43ce0b7dd703b040414165053` (merge of prior LAND `89d7af229…` onto green; keep-both in `QUANTITY_ALIASES`)
- **Prior LAND tip compared:** `89d7af229d381fe3ec7527779bb34c9f2b265471`
- **Commit under review:** `fix battery residue composition migration` — `simulator/battery/migrate.py` (+16 qualitative-condition guards), `enums.py` docstring (nine rails), `tests/battery/test_migrate.py` / `test_conversions.py`
- **Date:** 2026-10-01 ~10:08 ET
- **Mode:** read-only; extracts not edited. Targeted tests only (no full W3).
- **POLICY:** owner 2026-10-01 use-values-first — blocking = wrong-number guards only.
- **REQ:** `REQ-delta-review-residue-rail-ebbb6352-2026-10-01.md`
- **Prior REVIEW:** `REVIEW-residue-rail-sq-2026-09-30.md` (LAND `89d7af229…`)

## Diff scope

`git diff 07ebb48f8..ebbb6352e` touches only migrator condition parsing + rail docstring/tests. Extract YAML for kems-012 / kems-015 is **byte-identical** to prior LAND tip. No invented extract data.

## Attack (1) — STORED residue value / unit / basis vs `89d7af229`

Independent single-source migrate of `kems-012-sossi-2019` + `kems-015-hashimoto-1983` on both tips (same extract bytes; tip-local `simulator/battery`):

| | prior `89d7af229` | current `ebbb6352e` |
| --- | ---: | ---: |
| Live residue cells | 464 | 464 |
| Sossi live | 344 | 344 |
| Hashimoto live | 120 | 120 |
| Common `observation_id`s | 464 | 464 |
| Mismatches on value / unit / subtype / `derivation.output_unit` / species | **0** | **0** |

Subtypes unchanged: Sossi `element_ppm_by_mass` × 344; Hashimoto `oxide_wt_percent` × 120. Output units stay as-published ppm / `wt % (100% normalized)`. **PASS — no stored residue number, unit, or basis drift.**

## Attack (2) — qualitative conditions preserved (not dropped / not coerced)

Hashimoto live cells carry `point_conditions` (all 120):

| key | stored type | sample / pattern |
| --- | --- | --- |
| `fO2_control` | **str** | `"none"` |
| `atmosphere` | **str** | vacuum / no-buffer prose |
| `pressure_on_throw_Torr` | **dict** | `{value: "0.00015", units: "Torr", kind: about_nominal_by_temperature, as_printed: …}` |

Prior tip vs current tip: **0** differences on these three condition payloads. No decimal coercion of `"none"` / atmosphere prose. Unit test `test_residue_point_condition_values_keep_their_printed_types` pins the `_point_condition_from_plain` guards. **PASS.**

## Attack (3) — live-cell counts + source totals

| Source | Live residue | Total observations (this two-source migrate) |
| --- | ---: | ---: |
| kems-012-sossi-2019 | **344** | **612** (unchanged vs prior) |
| kems-015-hashimoto-1983 | **120** | **681** (unchanged vs prior; residue split already on LAND tip) |

No other sources in this migrate tree. Full-store “no other source moves” / hard-issue 3180 / 110,249 obs claimed by main after regen — not re-run on VPS (targeted-only constraint); two-source totals stable tip-to-tip. **PASS** for the attack’s live-cell and two-source scope.

## Attack (4) — nine-rail guard exact set equality

`Rail` enum and `test_nine_rails_are_the_owner_bound_set` pin exactly:

`vapour`, `melt_activity`, `thermochemistry`, `SiO_evolution`, `pyrolysis_yield`, `wall_deposition`, `redox`, `alkali_shuttle`, `residue_composition`

Owner ratification of `residue_composition` as ninth rail reflected in enums docstring. **PASS** (exact set equality).

## Targeted tests (VPS, `-o addopts=`)

`tests/battery/test_migrate.py` + `test_conversions.py` `-k 'residue or nine_rails or residue_point_condition'` → **9 passed**, 259 deselected. No full suite.

## Verdict logic

Fix is migrator-only: keep qualitative condition strings/dicts out of `as_decimal`, census aliases/fields for residue components, and nine-rail guard. Residue **values** are unchanged vs the reviewed LAND tip. No wrong-number defect under POLICY.

VERDICT: LAND ebbb6352e1923fc09e602909ed1bad212c8d4fc9

— regolith-empirical
