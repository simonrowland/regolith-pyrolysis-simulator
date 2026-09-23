# R23 — S2 provenance (converted values keep number + printed original)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`; local `origin/work-v064-green` has moved to `2e9e17c3d`)  
**Commit under review:** `e875a0d32270ceb2e8d1d4ed08d2531edede68d2` on `origin/review/s2-provenance` (detached)  
**Worktree:** `/workspace/repos/wt/slot-09` @ detached `e875a0d32`  
**Ferry tip check:** SHA matches; **1 commit** on branch tip vs parent `07ad01dae`:
- `e875a0d32` battery: record the printed original of a unit conversion

**Files (this commit only):** `simulator/battery/migrate.py`, `tests/battery/test_converted_provenance.py`

**Intent:** Celsius→kelvin landings and hand conversions recorded beside a printed number were stored as if printed. Keep the stored magnitude; attach a conversion `Derivation` (trail + printed original). Claim: 636→10 stamped printed; 0 numbers changed.

**Attack surface:** A landed magnitude moves; a true printed value is demoted to derived; twin Kelvin/Celsius preference rewrites +273 conventions; note parser false-matches `\bconverted\b`; `as_published` disagrees and crashes live load; `_generic_obs` still drops trails.

**Method:** Static read of `HEAD^..HEAD`; adversarial probes (twin prefer, +273, published-K, coincidental `converted`, identity notes, disagreeing arithmetic, clock/bound/word-atm); corpus walk of `data/literature/works` conversion notes; extract parent `T_C` census; focused pytest. Mode: **ran-tests**.

---

## Findings

### P2 — `_generic_obs` still wraps converted K via `located_value` (no trail)

**Evidence (file:line on `e875a0d32`):**

- `simulator/battery/migrate.py:8081-8083` — `_generic_obs` still does `point_conditions = {"temperature_K": located_value(temperature_K, locator)}` with no `conversion_derivation`
- Callers pass `t_sel.amount` after `select_declared_source(AXIS_TEMPERATURE_K, …)` (`migrate.py:8154-8178`, `8363-8373`, …) — so a Celsius coordinate would convert then drop the trail (same S2 pattern the main observation path just fixed at `7483-7488`)
- Live sidecars on this tree: kems / refractory coordinates are `temperature_K` with `identity:K` only (`would_drop_via_generic_obs == 0`); vacuum_pyrolysis `temperature_C` keys are not in `select_declared_source`'s temperature key list

**Why P2 (latent, not P0/P1):** Claim’s main extract observation path and note-recovery path are fixed. This emitter remains an open convert-then-`located_value` hole. No live sidecar today feeds a non-identity temperature trail into it, so no wrong PRINTED stamp from this path on the committed corpus. Batch Y: latent → not P0.

**Fix direction:** Mirror `_converted_temperature(t_sel.amount, t_sel.unit_trail, _temperature_field_raw(...), locator)` (or pass trail+original into `_generic_obs`).

---

## Residual notes (not severity / claim-adjacent)

- **Claim figure `636 → 10` not reproduced on this box.** Committed literature censuses: works conversion-note Located reload `152 → 5` still stamped printed (test pins `seen==152`, `recovered==147`, `left==5`); extract parent obs with `T_C`/`Tmax_C` `130 → 3` after `_converted_temperature` (the 3 keep `T_K` via +273 / non-equal twin). Exact 636/10 likely needs a full store remigrate tip not present here. Directional claim (converted demoted; residuals intentional) holds.
- **Works notes without the conversion keyword** (e.g. `Printed 1600 C` on `10.2355_tetsutohagane1955.66.5_488`) stay printed on YAML reload; remigrate from extract `T_C` would attach the trail. Incomplete recovery, not a magnitude rewrite.
- **Latent note false-positive:** `\bconverted\b` plus a coincidental convertible quantity that agrees with the stored SI can demote a non-unit-conversion note (probe: “Activity converted from oxide basis; chamber held near 0.3 bar” → `bar_to_Pa`). Live `\bconverted\b` recoveries are real “converted to Pa/kg/m/s” notes (7). Not filed separately; covered in attack table.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| A number moved (C↔K twin prefer, note recovery, as_published) | **Fail (safe).** Prefer only when Kelvin amounts equal; stored magnitude never replaced. Fixture migrate `T_C:1600` → `1873.15` with original `1600`. Corpus works walk: 0 magnitude moves (Decimal-equal). |
| Real printed value marked derived | **Mostly fail (safe).** `T_K_as_published` not demoted by Celsius twin; +273 mismatch keeps `T_K` / `inference is None`; identity / disagreeing notes stay printed; `no unit conversion` negation honored. **Intentional demotion:** equal T_K twin → prefer Celsius trail (extractor twin, not published-K). **Latent hit:** coincidental `\bconverted\b` + matching quantity (not in live corpus). |
| +273 convention rewritten as +273.15 | **Fail (safe).** `T_C:1370` + `T_K:1643.0` → amount `1643.0`, field `T_K`, no conversion inference (`test_author_plus_273_convention_is_not_rewritten`). |
| Hand-converted pressure stamped PRINTED | **Fail (safe / fixed).** Note recovery attaches `bar_to_Pa` / `atm_to_Pa` / …; `pressure_boundary` selects `printed_run_pressure` with `WaypointAuthority.DERIVED`; Pa unchanged. Live works: 29 conversion-note pressures flip printed→derived, values stable. |
| `as_published` that disagrees invents or loads wrong | **Fail (safe).** Raises `ValueError` (“does not reproduce”); 0 raises walking live works. |
| Ambiguous two matching unit operands | **Fail (safe).** `1 atm` + `760 Torr` both → 101325 Pa → `len(matching)!=1` → inference `None` (stays printed). |

---

## What looks sound

- `_located_from_plain` only synthesizes inference when none is stored; magnitude path unchanged (`migrate.py:690-696`, `2608-2662`).
- `conversion_derivation` refuses `identity*` trails; `_agrees_stored` gates note/as_published recovery so wrong arithmetic cannot attach.
- Main observation `point_conditions["temperature_K"]` uses `_converted_temperature` (`7483-7488`); series points already had / still use the same helper (`7871+`).
- `_prefer_printed_temperature` documents and tests the +273 non-rewrite; published Kelvin key excluded from twin set (`2220-2238`).
- New meta trails (`min_to_s`, `h_to_s`, `MPa_to_Pa`, …) match live note recoveries.

---

## Tests

Tip worktree at `e875a0d32`, repo `.venv` via `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/battery/test_converted_provenance.py`: **15 passed**
- `tests/battery/test_waypoints.py` + `test_migrate.py` (`-k 'conversion or converted or celsius or temperature or printed_run or inference or provenance'`): **20 passed** (190 deselected)
- Adversarial probes (twin prefer, +273, published-K, false `converted`, identity/disagree, clock, bound, word-atm, parent migrate C-only vs K-only): **passed**

No push. No fix patch (optional `_generic_obs` trail pass-through only).

---

## Verdict rationale

Named attacks do not move a landed number and do not demote a protected printed Kelvin (`T_K_as_published` / mismatch / identity). Converted Celsius parents and hand-conversion notes gain a checked trail + printed original; waypoint authority correctly becomes DERIVED for those Located values. One latent emitter (`_generic_obs`) still drops trails if Celsius ever reaches it; current sidecars do not. Claim’s exact `636→10` tally was not reproduced on the committed works/extract tree; residual printed cases match the tests’ intentional leftovers. Per Batch Y: not P0. **LAND.**

VERDICT: R23 | LAND | P0=0 P1=0 P2=1 P3=0 | ran-tests
