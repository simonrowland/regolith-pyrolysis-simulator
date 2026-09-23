# R26 — t951 author-ratio oxygen (Plante P_O2=0.226×P_K → DERIVED fO2_Pa)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `aa6bd6223` on `origin/empirical/l1-near-ready-oxygen-2026-09-22`  
**Range for claim:** `80155a9e7` (migrate + tests) + `aa6bd6223` (Plante `fO2_control` channel doc); parent for bit-compare `25e99857a`  
**Files:**  
- `simulator/battery/migrate.py` (`collect_author_ratio_oxygen`, `_merge_printed_oxygen`)  
- `tests/battery/test_printed_fo2.py`  
- `data/literature/extracts/kems-042-plante-1979.yaml`  
- (branch neighbor, out of oxygen claim) `mendybaev-2017-fun-cai-lab-evaporation.yaml` composition structure only  

**Intent:** Plante 1979 prints stoichiometric `P_O2 = 0.226 P_K` and tabulates `P_K`; extract stores ratio, `P_K`, and arithmetic product `P_O2_atm`. Land product on observation `point_conditions.fO2_Pa` with a **Derivation** stamp (never printed allowlist; never collapse distinct per-row products onto `experiment.fO2_control.oxygen_partial_pressure_Pa`). Mendybaev: no invented fO2. Chamber pressure stays the printed Torr **interval** (no midpoint).

**Attack surface:** stamped printed; invented midpoint pressure; wrong algebra/units; numbers changed on non-ratio paths.

**Method:** Detached worktree at `aa6bd6223`. Static read of tip diffs vs `25e99857a`. Validated all 162 complete ratio triples (`po2_over_pK_as_published` × `P_K_atm_as_published` == `P_O2_atm`, rel tol 1e-4; 0 disagreements). Confirmed no other extract carries those keys. Live migrate Plante + Mendybaev; bit-compared tip↔parent with `fO2_Pa`/`fO2_log` stripped. Spot-checked PDF text for `0.226 P` ratio. Pytest `tests/battery/test_printed_fo2.py`. Mode: **ran-tests**.

---

## Findings

No P0 / P1 / P2 on the named claim.

### P3 — `_AUTHOR_RATIO_PO2_KEYS` defined but unused

**Evidence (file:line on `aa6bd6223`):**

- `migrate.py:5339–5343` — `_AUTHOR_RATIO_PO2_KEYS = ("po2_over_pK_as_published", "P_K_atm_as_published", "P_O2_atm")` never referenced.
- `migrate.py:5628–5630` — `collect_author_ratio_oxygen` hardcodes the same three key strings.

**Why P3:** Dead constant / defense-in-depth hygiene. Live path is correct; no wrong number reaches a result.

**Fix direction (optional):** Use the tuple in the collector, or delete it. Out of scope for landing.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Stamped printed | **Fail (safe).** Live migrate: 162/162 `fO2_Pa` carry `inference` with `author_ratio_P_O2_atm` in `relation`; 0 with `inference is None`; no `fO2_log` on those rows. Waypoint `oxygen_condition` selects `observation_fO2_Pa_to_log_fO2` / `WaypointAuthority.DERIVED` only. Printed allowlist unchanged: `_PRINTED_LOG_FO2_KEYS` / `_PRINTED_PO2_KEYS` do not include `P_O2_atm`. Ratio harvest runs only after printed facts are empty (`migrate.py:6323–6345`) and does **not** call `_land_experiment_oxygen_pressure`. Experiment `oxygen_partial_pressure_Pa` stays unset (`exp_ctrl_pa=0`). Channel `intrinsic` lands as documentation only (`FO2Channel.INTRINSIC`); waypoints never invent a numeric log from channel alone. |
| Invented midpoint pressure | **Fail (safe).** Plante `pressure_environment.total_pressure_Pa` remains `ValueKind.INTERVAL` (Torr `10^-8`–`10^-7` → Pa lows/highs bit-identical tip↔parent). `pressure_boundary` selects `printed_run_pressure` / PRINTED / interval — not a POINT. Tip YAML adds only `fO2_control` channel/buffer notes; pressure block untouched. Mendybaev pressure still BOUND (`< 0.0001` Pa); no fO2 invent. |
| Wrong algebra / units | **Fail (safe).** All 162 complete triples agree: `0.226 * P_K_atm == P_O2_atm` (rel err 0 within 1e-4). Collector refuses `pO2_inference`, `inferred: true`, and arithmetic disagreement (`migrate.py:5624–5637`). Conversion `convert_pressure_to_pa(..., "atm")` → Pa. Probe row `plante1979_table2_k2o_s1104_000_1302K`: `P_O2=1.561660e-07 atm` → `0.015823519950 Pa`; waypoint log `log10(Pa/1e5) = -6.800696901…` exact. PDF text retains `= 0.226 P` stoichiometric substitution (OCR-garbled `P_K`). 221 quoted-K rows have only `P_K_atm_as_published` → correctly no `fO2_Pa`. 155 distinct Pa values across 162 rows (shared P_K repeats) — not a single collapsed control. |
| Numbers changed on non-ratio paths | **Fail (safe).** Tip↔parent (`25e99857a`) migrate: 383 observations; **0** mismatches with `fO2_Pa`/`fO2_log` stripped; **0** full mismatches on the 221 non-ratio rows; experiment equal once `fO2_control` excluded; pressure_env identical. Tip **adds** `fO2_Pa` on exactly the 162 ratio rows (parent had 0). Mendybaev: 0 oxygen `point_conditions` diffs; `fO2_control` still None. No other extract defines the author-ratio key trio. |

---

## What looks sound

- Gate order is printed-first, derived-second; ratio products never enter the printed allowlist and never land on experiment control (`migrate.py:6320–6345`).
- Derivation relation records ratio algebra + `atm_to_Pa` trail; parameters include ratio, P_K, and product (`:5646–5675`).
- Plante extract `fO2_control.channel: intrinsic` documents stoichiometric control without asserting `oxygen_partial_pressure_Pa` (`kems-042-plante-1979.yaml:166–178`).
- Tests pin unit refusal cases, Plante 162 DERIVED waypoints, and Mendybaev no invented fo2 (`test_printed_fo2.py:159–248`).
- Chamber / vacuum non-point pressures preserved — honest residual for `engine_point` pressure_boundary.

### Residual notes (not severity)

- **engine_point READY:** neither Plante nor Mendybaev is fully READY after this lane. Plante oxygen closed on 162 ratio rows only; pressure still interval (`interval_needs_point`); 221 non-ratio rows still missing oxygen; composition residual from prior e5 tip out of scope. Mendybaev: no printed fO2; pressure still bound.
- Mendybaev FUNC oxide map (`25e99857a`) is on the branch but outside the oxygen claim; oxygen/pressure behavior unchanged tip↔parent.
- Unused `_AUTHOR_RATIO_PO2_KEYS` (P3 above).

---

## Tests

Worktree `/workspace/repos/wt/slot-05` @ `aa6bd6223`, venv `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/battery/test_printed_fo2.py`: **8 passed**
- Live migrate probes (printed stamp, experiment collapse, midpoint, algebra, non-ratio bit-identity tip↔`25e99857a`, Mendybaev no invent): **passed / attacks failed**

No fix patch. Do not push.

---

## Verdict rationale

Tip does what it claims: author-ratio product lands as DERIVED `fO2_Pa` on 162 Plante rows only; never printed; never collapsed to experiment control; pressure interval untouched; non-ratio observation numbers bit-identical to parent; Mendybaev gets no invented oxygen. Named attacks miss. One optional P3 dead constant.

READY: **NOT READY** for `engine_point` (Plante pressure interval + partial oxygen coverage; Mendybaev no printed fO2). Lane is the honest READY *path* for printed-ratio oxygen only.

VERDICT: R26 | LAND | P0=0 P1=0 P2=0 P3=1 | ran-tests  
READY: NOT READY (engine_point) — oxygen DERIVED on 162/383 Plante rows; pressure still non-point
