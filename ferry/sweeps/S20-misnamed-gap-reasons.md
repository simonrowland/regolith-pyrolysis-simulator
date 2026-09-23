# S20 — refusal / notice / gap reason text that misnames the cause

**Repo tip (audited):** `1f8df6cbe4cfbca526192b92e8adb2c1e1780dfc` (`origin/review/r6-r8-fix`).  
**Worktree:** `/workspace/repos/wt/slot-07` (detached @ `1f8df6cbe`). READ-ONLY on product code; writes only to `/workspace/ferry-inbox/sweeps/`. No push.  
**Green tip (comparison):** `origin/work-v064-green` @ `fbe3491b2`.  
**Scope:** whole repo — typed `GapReason` / `RefusalReason` / notice-kind / gap `missing` text whose **code or text says X when the evaluated cause is Y**.  
**Predicate:** a readiness gap, validity refusal, generator refusal, or notice stamps a closed reason token (or cause-detail tuple) that names a different mechanism than the branch that fired.  
**Seed (class brief):** “a typed reason whose text or code says X when the evaluated cause is Y.” Closest in-tree relative: R22 Clausing-only escape gap fix on `review/s1-s4-fix` (not on this tip) — geometric escape still lists present orifice area as missing here.  
**Batch Y rank rule:** P0 only when a wrong number reaches a result/score/ledger **today**. Misnamed refusal/gap **labels** with correct GAP/REFUSED/NOT_APPLICABLE status → **≤ P1**. Latent ≤ P1.  
**Method:** static audit of every `GapReason.*` / `RefusalReason.*` assignment + adversarial probes (`consumer_readiness`, `_effective_escape_gap`, `effusion_regime_unverified`, `_refused`). Mode: **ran-tests** (probes) + static.

| site (file:line) | predicate match (says X / cause is Y) | constructed trigger | live? | severity | P0? | fix direction |
|---|---|---|---|---|---|---|
| `simulator/battery/waypoints.py:1297-1298` (`_consumer_constraints` kems branch) | **X** = `GapReason.OUTSIDE_PRESSURE_REGIME` on waypoint `method`. **Y** = method is a known `MethodToken` other than `knudsen_effusion` (wrong method for KEMS, pressure never evaluated). | `MethodToken.TRANSPIRATION` / `LANGMUIR_FREE_EVAPORATION` / `TGA` at P=`1e-6` Pa with orifice+clausing+thermal present → kems status `not_applicable`, gap `method` / `outside_pressure_regime` / `missing=(method,)`. Repro on tip. | live | P1 | no | New `GapReason` (e.g. `method_not_knudsen` / `consumer_method_mismatch`) or reuse a method-axis token; keep `OUTSIDE_PRESSURE_REGIME` only for the Kn-high-end `< FREE_MOLECULAR_KNUDSEN_MIN` branch (`:1302-1303`). |
| `simulator/battery/waypoints.py:1305-1306` (kems else / Kn inconclusive) | **X** = `GapReason.UNSUPPORTED_PRINT_FORM` with `missing=("Kn>=10 not established",)`. **Y** = Kn was derived (or partially derived) but does not establish `Kn≥10` (bound/interval straddles threshold) — not a print-form refusal of d/T/P. | Unknown method + `total_pressure_Pa` `BOUND <20` or `INTERVAL[1,100]` with orifice d=0.5 mm, Ar, T=1500 K → gap `knudsen_number_orifice` / `unsupported_print_form` / `("Kn>=10 not established",)`. Repro on tip. | live | P1 | no | Stamp `OUTSIDE_PRESSURE_REGIME` only when high end is strictly below threshold; for inconclusive Kn use a dedicated reason (e.g. `regime_not_established`) or `MISSING_EVIDENCE` naming the inconclusive inputs — never `UNSUPPORTED_PRINT_FORM`. |
| `simulator/battery/waypoints.py:1192-1197` (`_effective_escape_gap`) | **X** = `missing` tuple claims `bench.geometry.orifice_area_m2` (and often diameter path never used area). **Y** = geometric-only route already selected (`printed_area` / `diameter_geometric_area_single_opening`) so area/diameter evidence is present; only Clausing is absent. Reason code `MISSING_EVIDENCE` is fine; **cause text** lies. | Geometry with `orifice_area_m2` POINT only (live S4 shape, e.g. `data/literature/works/10.2355_isijinternational.32.1276.yaml`) → selected `printed_area`+`GEOMETRIC_ONLY`, gap `missing==("…clausing_factor","…orifice_area_m2")`. Same with diameter-only. Repro on tip. | live | P1 | no | List only `bench.geometry.clausing_factor` when `GEOMETRIC_ONLY` is set (R22 `f6a0e1712` on `review/s1-s4-fix`; not merged on this tip). |
| `simulator/battery/waypoints.py:1317-1328` (rps pressure floor) | **X** = `GapReason.BELOW_PRESSURE_FLOOR` whenever `not _pressure_meets_floor`. **Y** = for straddling intervals / `<` bounds above the floor / `>` lower bounds below the floor, pressure is **not** entirely below `RPS_PRESSURE_FLOOR_PA` (0.1 Pa) — floor simply not established. Only the `below` branch is truly “below”. | Interval `[floor/10, floor*10]`, or `BOUND < floor*10`, or `BOUND > floor/10` → status `gap` (not `not_applicable`) still stamps `below_pressure_floor`. Point `floor/10` correctly uses the same token with `not_applicable`. Repro on tip. | live | P1 | no | Use `BELOW_PRESSURE_FLOOR` only when `below` is True; otherwise a distinct reason (`pressure_floor_unestablished` / `UNSUPPORTED_PRINT_FORM` on the bound) or omit the regime gap and keep ordinary readiness gaps. |
| `simulator/battery/validity.py:391-393` (`effusion_regime_unverified`) | **X** = `RefusalReason.EFFUSION_REGIME_UNVERIFIED`. **Y** = orifice Kn **is** known and `kn < FREE_MOLECULAR_KNUDSEN_MIN` — regime was verified and failed. (Missing Kn / missing P → same token is closer to “unverified”.) | KEMS method + equilibrium quantity + `knudsen_number_orifice=2` + stated background P → `effusion_regime_unverified` / primary `orifice_knudsen`. Repro on tip. | live | P1 | no | Verified fail → `BACKGROUND_PRESSURE_HIGH` is wrong; add / use a fail-closed token (e.g. `effusion_regime_failed` / reuse readiness `OUTSIDE_PRESSURE_REGIME` mapping). Keep `EFFUSION_REGIME_UNVERIFIED` for unknown Kn / unknown P only (`:363-379`). |
| `simulator/battery/generators/bench.py:79-81` (`_refused`) + call sites `:117`, `:201`, `:281` | **X** = default `GapReason.UNSUPPORTED_PRINT_FORM` for every catch. **Y** = `UnsupportedValue("physical_inputs")` (T≤0 / P<0 / empty composition), `"single_oxide_formula"`, `"thermal_path.integer_duration_h_required_by_runner"`, `KEMSSchemaError` / `LabGeometryError` / `LabScheduleValidationError` — none are “present print form unusable”. | After READY, raise those paths (probe: `_refused(ready, {}, "physical_inputs")` → gap reason `unsupported_print_form`). Live whenever engine_point/kems/rps generation throws after requirements pass. | live | P1 | no | Pass an explicit `GapReason` per cause (`MISSING_EVIDENCE`, `CONSUMER_INPUT_NOT_SUPPLIED`, `SINGLE_SPECIES_CHARGE`, new `generator_constraint` / `schema_invalid`); stop defaulting unrelated failures to `UNSUPPORTED_PRINT_FORM`. |

## Counts

- Sites: **6**  
- Live: **6** · Latent: **0**  
- P0: **0** · P1: **6** · P2: **0** · P3: **0**  
- P0 count: **0** (Batch Y: labels only; status stays gap/refused/not_applicable — no invented score/ledger number from these misnames on tip today)

## No-hit areas (audited; reason matches cause, fixed, or out of class)

- **`GapReason.OUTSIDE_PRESSURE_REGIME` at `:1302-1303`** when `high < FREE_MOLECULAR_KNUDSEN_MIN` — cause is free-molecular Kn fail; token is acceptable for that branch (not counted).  
- **`GapReason.INTERVAL_NEEDS_POINT`** (`generators/bench.py:49-51`) — text matches interval→point consumer need (R9).  
- **`UNSUPPORTED_PRINT_FORM` on normalized_composition dropped species / present-unusable siblings** (`waypoints.py:527-532`, B560 tests) — form really unusable.  
- **`UNSUPPORTED_PRINT_FORM` when Kn arithmetic returns `None`** (`waypoints.py:1241-1242`) — d/T/P print form failed to yield a Kn; distinct from `:1306`.  
- **`BELOW_PRESSURE_FLOOR` when point/high is strictly below floor** (`:1320-1326` `below=True` → `not_applicable`) — correctly named.  
- **`EFFUSION_REGIME_UNVERIFIED` for unknown Kn or unknown background P** (`validity.py:353-379`) — truly unverified.  
- **`BACKGROUND_PRESSURE_HIGH`** (`validity.py:398-439`) — threshold match.  
- **Session unknown-feedstock / Stage-0 input errors** (`session.py:_typed_input_error`, `web/events.py:3782+`) — `reason_code` stamped; no longer advertised as bare `backend_unavailable` (prior mislabel fixed).  
- **Melt-oxide `temperature_not_supplied` on no-gamma path** (`chemistry/melt_activity.py:594-604`) — already corrected to `declared_ideal_no_gamma_coefficient_non_authoritative`.  
- **`NoticeKind` map `melts_domain_gate` → `OUT_OF_CERTIFIED_BAND`** (`score.py:830-838`) — umbrella domain/band notice; gate_reason text preserved; not scored as X≠Y.  
- **Score `RefusalReason` collapses** (species unmatched → `UNSUPPORTED`, crash/timeout → `ATTEMPTED_UNAVAILABLE`) — detail payload carries typed cause; token is coarse but not a false X.  
- **Vapour-rail `TypedGapReason` dispositions** (`vapour_rail/channels.py:1542-1583`) — disposition derived from required channels; no X≠Y found.  
- **R2/R12 empty-`missing` / weak presence** — wrong *omission* of causes, not a typed code saying X for Y (covered in those reviews; out of this class).

## Method notes

- Enumerated all `GapReason` / production `RefusalReason` stamps under `simulator/battery/{waypoints,validity,generators/bench,score,validate}.py`.  
- Probes (tip `1f8df6cbe`, repo `.venv`): non-Knudsen methods → `outside_pressure_regime`; Kn bound/interval → `unsupported_print_form` + `Kn>=10 not established`; geometric escape with present area → `missing` still lists `orifice_area_m2`; RPS straddling/`>` lower-bound → `below_pressure_floor`; Kn=2 → `effusion_regime_unverified`; `_refused(..., "physical_inputs")` → `unsupported_print_form`.  
- Escape fix exists on `review/s1-s4-fix` (`f6a0e1712`) but **not** on this audited tip.

SWEEP: S20 | sites=6 | live=6 | P0=0 P1=6 P2=0 P3=0 | tip=1f8df6cbe4cfbca526192b92e8adb2c1e1780dfc | path=/workspace/ferry-inbox/sweeps/S20-misnamed-gap-reasons.md
