# R2 — readiness gap reporting

**Scope:** commits `924eea91e` (name only the inputs a waypoint gap is actually missing) and `f5b422d0e` (name only present unusable fields on an unsupported print form), both ancestors of `work-v064-green` tip `fbe3491b2`.

**Null hypothesis:** wrong or beyond intent. Intent: gap reports name only inputs that are actually missing (`MISSING_EVIDENCE`) or actually present-but-unusable (`UNSUPPORTED_PRINT_FORM`); reporting only — nothing becomes ready/partial that was not before; dropped-species branch untouched.

**Evidence mode:** static review of diffs + targeted probes (and narrow pytest of the two new test modules). Fix patch written, not applied: `ferry/reviews/R2-fix.patch`.

---

## Summary

`f5b422d0e` matches intent for `normalized_composition`: absent siblings leave `UNSUPPORTED_PRINT_FORM`; only present-unusable paths are named; the dropped-species branch is byte-stable in behavior. Land that half.

`924eea91e` correctly stops stamping the static OR-set for several waypoints (oxygen T/P helpers, pressure, G2/G3, charge) and does not change route selection or readiness status on the paths the new tests cover. But its presence predicates are weaker than “resolved / route-usable,” so a gap can **omit** inputs that never feed a route, and in one thermal case can emit **`MISSING_EVIDENCE` with `missing=()`** — a false report about why something is not ready.

Verdict: **LAND-WITH-FIXES**.

---

## Findings

### P1 — `thermal_path` can report `MISSING_EVIDENCE` with an empty `missing` list

**Where:** `simulator/battery/waypoints.py` `_schedule_temperature_present` (~177–187), `_located_present` (~173–174), `thermal_path` present map (~870–874), `_result` (~167–169).

**Attack input:**

- `thermal_schedule.setpoints_and_holds[0]`: `temperature_K` typed-absent, `hold_duration_s` POINT present → `_schedule_temperature_present` is True (any located value).
- `experiment.conditions.temperature_K`: `ValueKind.CATEGORICAL` → `_located_present` True; `temperature_points_only` requires POINT → no route.

**Observed:** `WaypointAbsence(thermal_path, MISSING_EVIDENCE, ())`. Via `collect_consumer_inputs`, engine_point surfaces the same lie as `temperature_K missing_evidence ()`.

**Why it fails doctrine:** a readiness gap must say true things about why something is not ready. Empty `missing` under `MISSING_EVIDENCE` asserts absence without naming any absent input. Status stays GAP (not elevated to READY) — so not P0 for readiness flip — but the report is false.

**Related P2 (same predicates):** incomplete schedule + truly absent conditions T → gap names only `experiment.conditions.temperature_K` and omits the unusable schedule (`G`/`Q` probes). Interval conditions T + no schedule → names only `experiment.thermal_schedule` and treats unusable interval T as “present” (`I`).

---

### P1 — `oxygen_condition` treats `experiment.conditions.fO2_log` as present `fO2_log` though no route reads it

**Where:** `simulator/battery/waypoints.py` ~992–993:

```python
"fO2_log": _located_present(point_fo2) or _condition_value(experiment, "fO2_log") is not None,
```

**Attack input:** `fO2_control=None`, `conditions.fO2_log=POINT(-10)`, conditions/run T and pressure resolve (e.g. kems factory).

**Observed:** gap `missing == ("experiment.fO2_control",)` — `fO2_log` omitted. Same experiment without conditions `fO2_log` lists `("fO2_log", "experiment.fO2_control")`. Selected stays `None` either way (resolution unchanged; reporting lies).

**Why:** `oxygen_condition` routes read observation `fO2_log` / control / sweep / gas / buffer — never `experiment.conditions["fO2_log"]`. Marking that field present invents evidence.

---

### P2 — `experiment.fO2_control` “present” if any located pO2/buffer exists, including unusable forms

**Where:** `simulator/battery/waypoints.py` ~988–990.

**Attacks:**

| Input | Effect |
|---|---|
| `oxygen_partial_pressure_Pa` POINT `0` | `_value` non-None; `_log_pressure` rejects → control omitted from missing |
| categorical pO2 | same |
| `buffer="NOT_A_REAL_BUFFER"` | `buffer.state.is_value` → control omitted; only `fO2_log` named while T/P resolve |

Known buffer (`IW`) with T/P absent correctly keeps control “present” and names `temperature_K` / `total_pressure_Pa` — that case is fine. The bug is counting **non-route-usable** control payload as present.

---

### P2 — (covered above) weak thermal presence omits present-but-unusable OR members

Same root as P1 thermal: presence ≠ route acceptance. Incomplete reports when the other OR member is genuinely absent.

---

### P3 — cosmetic / dead weight in `924eea91e`

- Call sites pass `tuple(volume_inputs)` / `tuple(pressure_inputs)` etc. while also passing `present=...`; `_result` overwrites from `present` — the positional tuple is dead.
- After a correct thermal presence fix, `_schedule_temperature_present` is unused (introduced only for this commit’s weak check).

---

## What holds (attacks that failed to break intent)

### Resolution / readiness status unchanged on covered paths

- Route construction for oxygen, pressure, G2/G3, charge, volume, thermal, normalized_composition is not rewritten by these commits beyond absence text / `_dimensions_present` factoring (behaviorally equivalent to prior `_value` all-three check).
- Probes: unsupported print, conditions-only `fO2_log`, empty-missing thermal — `selected` stays `None`; consumer status stays GAP / NOT_APPLICABLE, never newly READY.
- New tests assert GAP with shorter missing lists (`test_reporting_a_shorter_gap_does_not_make_the_consumer_ready`).

### `f5b422d0e` — unsupported print form naming

- Absent field → `absent`; present-unusable → `missing` + `unsupported`; `UNSUPPORTED_PRINT_FORM` uses `missing` only; both-absent stays `MISSING_EVIDENCE` with both paths.
- Dropped-species branch (`printed_path.<species>`) unchanged; probe `printed={SiO2,MgO,FeOT}` + reduced initial still yields `UNSUPPORTED_PRINT_FORM` / `printed_composition.FeOT` only.

### Evaluated OR-set wins that match intent

- Oxygen gap omits resolved `temperature_K` / `total_pressure_Pa` when helpers select (b556).
- Pressure omits present pump speed; G3 names only the absent factor; G2 omits resolved escape area.

---

## Fix (not applied)

`ferry/reviews/R2-fix.patch` (dry-run clean against tip):

1. **`_result`:** if `present` filters to empty while `selected is None`, fall back to `tuple(present)` (refuse empty `MISSING_EVIDENCE`).
2. **`thermal_path`:** mark OR members present only if a fired route’s inputs reference that family (on the no-route path both false → full OR-set named).
3. **`oxygen_condition` gap path:** stop treating `conditions.fO2_log` as evidence (`fO2_log: False` once no route fired); require log-convertible pO2 or a **published** buffer name for `control_present`.

Validated against attack probes A/B/C/H/J and re-ran `test_b556_*` + `test_b560_*` on the patched tree (12 passed).

---

## Tests

| What | Result |
|---|---|
| Static diff review of `924eea91e`, `f5b422d0e` | done |
| Targeted attack probes (empty missing, false fO2_log, weak control, unsupported/dropped) | done |
| `tests/battery/test_b556_missing_evaluated.py` + `test_b560_unsupported_print_form.py` (`-o addopts=`) | 12 passed on tip; 12 passed with fix |
| Full suite | not run (box lacks full engine/numpy stack) |

---

## Severity tally

| Sev | Count | Titles |
|---|---|---|
| P0 | 0 | — |
| P1 | 2 | empty `MISSING_EVIDENCE` missing; false `fO2_log` from conditions |
| P2 | 2 | weak `fO2_control` present; weak thermal presence / incomplete gap text |
| P3 | 1 | dead positional `missing` args / unused schedule helper |

---

VERDICT: R2 | LAND-WITH-FIXES | P0=0 P1=2 P2=2 P3=1 | ran-tests
