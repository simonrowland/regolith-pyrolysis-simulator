# R3 — printed fO2 onto waypoint inputs

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2`  
**Commits under review:**
- `e07ed3a92` battery: land printed fO2 on waypoint inputs
- `5d9753eaf` battery: build printed pO2 points inside the value boundary

**Intent:** Land printed oxygen facts (`log_fO2` / `log10_fO2` / `oxygen_partial_pressure`) onto `engine_point` `oxygen_condition` inputs; keep the author's log as stated; store companion atm pressure in Pa; record (do not apply) the 0.0057 dex atm↔bar offset; refuse bounds/inferred/disagreement; route pO2 point construction through `_point_selection`.

**Attack surface:** unit/sign errors (bar vs Pa, log10 vs ln); buffer offset as absolute fO2; derived stamped printed; range collapsed to a point; wrong experiment/context row.

**Method:** Static read of `simulator/battery/migrate.py`, `simulator/battery/waypoints.py`, extracts (Badro / Norris / Sossi / Gibson / ts1985), and `tests/battery/test_printed_fo2.py`. Narrow pytest: `tests/battery/test_printed_fo2.py` (5 passed). Adversarial probes for power-of-ten-as-log and interval overwrite.

---

## Findings

### P1 — `_printed_decimal` accepts `10^n` strings as log fO2 amounts

**Where:** `simulator/battery/migrate.py` `_printed_decimal` ~5377–5386; `_walk_printed_oxygen` log branch ~5549–5556.

**Evidence:** Shared parser used for both pressure amounts and log amounts. For `log_fO2: {value: "10^-9.1", units: log10}`, probe returns `7.94…e-10` as the landed log (the linear 10^n amount), not `-9.1`. That would stamp `observation_fO2_log ≈ 0` into `oxygen_condition` (routes[0], PRINTED).

Badro today is safe (`log_fO2.value: -9.1` numeric; `oxygen_partial_pressure.value: '10^-9.1'`). The bug is latent but catastrophic on the next mis-keyed extract. Comment at 5372–5373 even notes that `10^-9.1` logs back to `-9.1` — that algebra belongs only on the pressure path.

**Fix:** On log keys, refuse power-of-ten strings (or take the exponent explicitly). Never feed `_power_of_ten` into `State.of` for `fO2_log`. Optional patch: `ferry/reviews/R3-fix.patch`.

### P1 — `_land_experiment_oxygen_pressure` collapses a pre-existing interval/bound to a point

**Where:** `simulator/battery/migrate.py` `_land_experiment_oxygen_pressure` ~6190–6218.

**Evidence:** Existing control pressure is consulted only when `previous.kind is ValueKind.POINT`. An `INTERVAL` or `Bound` makes `existing` stay `None`, then:

```python
control = replace(control, oxygen_partial_pressure_Pa=located)  # point wins
```

Attack "range/interval collapsed to one point": Norris already lands `fO2_control.oxygen_partial_pressure_Pa` as an interval (`1.01325e-8` … `1.01325e-2`) from the printed log range `-7` to `-13` (extract lines ~90–104). Today Norris observations under review only print `log_fO2` points (no companion `oxygen_partial_pressure`), so the overwrite does not fire on green tip. Any later observation that also carries a printed pO2 point under the same experiment would silently replace the interval. Doctrine: do not invent/collapse points over a printed range.

**Fix:** If existing value is present and not an equal POINT, treat as conflict (refuse / clear), never overwrite interval/bound with a point. Optional patch: `ferry/reviews/R3-fix.patch`.

### P2 — atm-frame printed log is selected as bar-referenced `oxygen_condition`

**Where:** `migrate.py` `_annotate_atm_bar_offset` ~5461–5496 (records offset, does not apply); `waypoints.py` `oxygen_condition` ~877–884 (printed `fO2_log` is routes[0]); `_log_pressure` ~1006–1011 (bar = Pa/100000).

**Evidence:** Badro lands printed `-9.1` with companion `10^-9.1 atm`. Bar-frame from Pa is `-9.09428…` (offset `log10(101325/100000)`). Test asserts selected route is `observation_fO2_log == -9.1` while derived pressure routes carry the bar value. Waypoint docstring claims "Numerical log10(fO2 / 1 bar)". Intentional per commit message; residual 0.0057 dex frame error on the selected number. `_OXYGEN_LOG_UNITS` also accepts `log10(atm)` / `log10(bar)` identically; alone (no companion pressure) `log10(atm)` gets no note (probe: note `None`).

**Mitigation present:** note on locator when companion atm pressure exists; derived routes remain available. Not a land-blocker given explicit product intent, but consumers that trust `selected` as bar without reading notes will be off by ~0.0057 dex.

### P2 — deep walk can attribute run-nested oxygen to an observation via non-oxygen payloads

**Where:** `_walk_printed_oxygen` ~5510–5575; `_add_observation` oxygen_roots = `values` + `equipment` ~6958–6968.

**Evidence:** Badro VP isotope rows nest `run: *vp1` → `conditions: *conditions` → `log_fO2` / `oxygen_partial_pressure`. Walk depth finds them and lands on that observation. Correct for Badro (shared gas mix), but the walker has no notion of "oxygen context" vs arbitrary nested maps — any future embed of another row's conditions under `values` would bind the wrong experiment/context oxygen. Series path is tighter (`raw_item` then parent with `skip_tables=True`); non-series path is the risk.

**Not P1:** no wrong row demonstrated on Sossi (`point:0` → `-0.68`) or Badro VP1.

### P3 — `units: "log"` and empty units accepted as log10

**Where:** `_OXYGEN_LOG_UNITS` ~5337–5346.

**Evidence:** `"log"` and `""` are allowlisted. Natural-log mislabel would scale by `ln(10)`. Norris figure2 `log_fO2: -7` (bare scalar, empty units) correctly lands. Low immediate corpus risk.

### P3 — buffer / calculated keys correctly excluded (positive control)

**Where:** allowlist `_PRINTED_LOG_FO2_KEYS` / `_PRINTED_PO2_KEYS` ~5331–5335; ts1985 `log10_pO2_bar` + `pO2_inference`.

**Evidence:** Exact-name allowlist refuses `log10_pO2_bar` (probe: empty facts). Addresses attack "buffer offset / derived stamped as printed" for the cited b-526 pattern. `inferred: true`, bound semantics, list values (`[-11,-13]`), and string ranges refused (tests + probes).

---

## Commit `5d9753eaf` (value-boundary pO2)

`_pressure_located` now builds the Pa point via `_point_selection(si, printed_key, trail, {}, ())` instead of `Value.point_of(si)` (~5451–5455). Behavioral no-op for the numeric amount; doctrine compliance only. No new defect found. Does not mitigate P1s above.

---

## Tests

```
.venv/bin/pytest tests/battery/test_printed_fo2.py -o addopts=
# 5 passed
```

Probes (not committed): power-of-ten-as-log → `7.94e-10`; interval existing → overwrite path; `log10_pO2_bar` alone → empty; list/range refused.

Optional fix patch (not applied): `ferry/reviews/R3-fix.patch`.

---

## Verdict summary

| Sev | Count | Topic |
|-----|-------|-------|
| P0  | 0     | — |
| P1  | 2     | 10^n parsed as log; interval→point overwrite |
| P2  | 2     | atm log selected as bar; deep-walk nesting |
| P3  | 2     | ambiguous `log`/empty units; allowlist positive |

No confirmed wrong landing on green-tip Badro/Sossi/Gibson/Norris figure2. Fixes are narrow and local to migrate oxygen landing.

VERDICT: R3 | LAND-WITH-FIXES | P0=0 P1=2 P2=2 P3=2 | ran-tests
