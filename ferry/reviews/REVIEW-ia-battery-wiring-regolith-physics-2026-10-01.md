# REVIEW — ia-battery-wiring (t-1068, owner priority)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `76b55001909ecdebc1a335ac6d45337870448075` on `review/ia-battery-wiring` (detached seat; re-pinned after concurrent seat drift)
- **Range:** `72cb7d960a71e7bb81430ed733a15f99edfa330b..76b55001909ecdebc1a335ac6d45337870448075` (one commit)
  - `76b55001909ecdebc1a335ac6d45337870448075` Wire internal analytical battery scoring
- **Parent confirmed:** `72cb7d960a71e7bb81430ed733a15f99edfa330b`
- **REQ:** `/workspace/ferry-inbox/REQ-ia-battery-wiring-from-regolith-physics-2026-10-01.md`
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-ia-battery-2026-10-01/report.md`
- **Date:** 2026-10-01 ~20:58–21:03 ET
- **Mode:** read-only for product code; extracts not edited; no force-push of feature branches. Targeted VPS unit tests only (no full W3 / full-store Plante re-score).

## Scope

Wire the simulator's own internal-analytical physics into the battery producer: observation seeds a `PyrolysisSimulator` melt state; core `VAPOR_PRESSURE` route produces `P_PARTIAL` (no second model). Effusion oxygen follows the existing openimcc oxygen-balance convention for the pO2 *condition* only. Typed refusals for missing/invalid input; out-of-band values with extrapolated authority. Minimal `score.py` hook (`OXYGEN_BALANCE_EFFUSION_ENGINES` + IA provenance on the engine trace) and `oxygen_balance.py` ownership set.

Files: `simulator/diagnostic_helpers/binary_pot_battery.py`, `simulator/battery/score.py`, `simulator/battery/oxygen_balance.py`, `tests/battery/test_internal_analytical_battery_engine.py`.

## Attack results

### (1) Battery state vs hero melt state for composition/T — **PASS**

Shared scorer path (all vapour engines): `Identity.composition` → `composition_wt_pct` (mole→oxide wt% renormalisation) → `identity_battery_pot` → `composition_kg_and_mol` (1 kg batch) → `equilibrate_cell`.

IA adapter then:
- refuses missing composition / oxygen (cat 1) and invalid T/P/composition/oxygen (cat 2); **no silent defaults**
- seeds `process.cleaned_melt` with that oxide kg inventory; sets melt T, total P, commanded/solved pO2 + `fO2_log` on melt and oxygen reservoir
- rebuilds chemistry kernel; calls `_refresh_vapor_pressures_from_kernel` → `_dispatch_only(ChemistryIntent.VAPOR_PRESSURE)` → `BuiltinVaporPressureProvider`
- does **not** call `InternalAnalyticalBackend.equilibrate()`

Hand-built observation test asserts Fe `P_PARTIAL` equals core dispatch for that seeded state to **1e-12** relative. Oxide-basis / 1 kg normalisation is shared with openimcc on the same scorer path — not an IA-only handicap. Battery state is a static product vapor-intent state for composition/T/pO2, not a full hero timestep; that is the correct battery construction.

### (2) Effusion oxygen convention (openimcc pO2 → IA) — **PASS (convention fair; note coupling)**

For Knudsen effusion without printed fO2, score gates on `OXYGEN_BALANCE_EFFUSION_ENGINES` and builds `Po2Request(mode=PO2_OXYGEN_BALANCE_EFFUSION)`. IA adapter solves pO2 via `_OpenImccBatteryBackend.equilibrate` **condition-only**, then predicts pressures with the IA core. Provenance splits `oxygen_condition_source=openimcc_oxygen_balance_condition_only` vs `pressure_prediction_source=internal-analytical core`.

Like-for-like battery convention: both engines share the same oxygen-condition solve; pressure models differ on purpose. Residual risk: openimcc-consistent fO2 may not be IA-self-consistent for strongly redox-sensitive species. Plante headline is all **K**; report T-trend (+0.88 dex @1200 K → −0.59 @1800 K) is the visible model gap, not hidden by the oxygen hook. Non-blocking follow-up: IA-native oxygen balance if redox-sensitive species enter the numeric band.

### (3) Minimal score.py hook vs in-flight workers — **PASS**

Tip `score.py` delta (~20 lines): import `OXYGEN_BALANCE_EFFUSION_ENGINES`; swap effusion gate from `IMCC_ENGINES`; IA `engine_version` from vapor provenance JSON; map `internal_analytical_missing_*` / `oxygen_balance_unavailable` → `IDENTITY_INCOMPLETE`, `internal_analytical_invalid_*` → `INVALID_IDENTITY`. Ownership set in `oxygen_balance.py` (`OPENIMCC` ∪ `INTERNAL_ANALYTICAL`); `IMCC_ENGINES` remains openimcc-only (SF04 workbook gate unchanged).

Disjoint from concurrent `origin/review/refconv` (fusion comparison / `compile_residual`) and `origin/review/effusion-stratum` (unverified-apparatus / flagged strata). `git merge-tree` vs both: **0 conflict markers**. Train still merges adapter + hook after those land; conflict surface is small.

### (4) Refusal accounting per rail/reason — **PASS**

Report vapour-rail table present. Shared reasons agree (bulk_not_liquid 59, effusion_regime_unverified 219, method_unknown 49). Engine-specific split differs (IA identity_unknown 75 / unsupported 91 vs openimcc 117 / 48 + 1 attempted_unavailable) — expected for domain/species support. Plante 162 admitted: **0 refused** both engines. Adapter unit tests cover cat-1 missing composition/oxygen and cat-2 nonfinite T; score maps those codes to typed scorer refusals. Seat probe: `has_own_engine_solved_oxygen_balance(INTERNAL_ANALYTICAL, …)` True when notice origin is `engine:internal-analytical`.

### (5) 1 ms process-start race — **NOT tip regression**

Worker: `test_isolated_cell_worker_timeout_kills_sleeping_grandchild` failed on Mac at tip and at base `72cb7d960` (1 ms timeout before PID file). VPS seat: **5/5 tip + 5/5 base PASS** (~6 s each). Mechanism is the isolated-cell worker timeout race, outside the IA adapter path. Accept worker Mac base failure as pre-existing flake; **not a tip regression / not a REVISE blocker**.

## Worker claims

| Claim | Seat finding |
| --- | --- |
| IA wires simulator internal-analytical into battery producer | **Confirmed** — `_InternalAnalyticalBatteryBackend` + core seed |
| `VAPOR_PRESSURE` route (no second model) | **Confirmed** — path string + parity test + mutation narrative in report |
| Typed refusals | **Confirmed** — `_InternalAnalyticalInputRefusal` + score mapping + 3 refusal tests |
| Out-of-band → extrapolated authority | **Confirmed** — core_flags / `AUTHORITY_EXTRAPOLATED`; not refused |
| Minimal score.py hook | **Confirmed** — see (3) |
| Like-for-like 162 Plante K numbers | **Report trusted** — arithmetic 33/162=20.37%, 112/162=69.14%; RMS/median match report. Full paired re-score **not** re-run on 16GB VPS (Mac ~178 s Plante / ~650 s full vapour). |

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/battery/test_internal_analytical_battery_engine.py` | **4 passed** (~3 s) |
| `tests/battery/test_score.py` `-k oxygen_balance\|internal_analytical\|…` | **1 passed**, 122 deselected |
| Seat oxygen-balance ownership probe (IA + openimcc) | **PASS** |
| `test_isolated_cell_worker_timeout_kills_sleeping_grandchild` ×5 tip / ×5 base | **10 passed** (VPS); Mac flake per worker |

Not run (policy/cost): full W3, full-store Plante/`score_store`, openimcc live suite, migrate remainder, Mac Studio green gate.

**ASK (optional green gate):** Mac Studio full `tests/battery/test_score.py` + Plante paired `battery_score` replay if train wants numeric re-confirmation beyond report arithmetic.

## Non-blocking notes

- No commissioning-table / `engine_commissioning.py` allowlist entry — intentional; scoring uses extrapolated authority under current rules.
- Add a `test_score` assertion that IA notices satisfy `has_own_engine_solved_oxygen_balance` (today only openimcc is covered; ownership set change is production-correct).
- Concurrent score.py workers: merge tip hook after/with refconv-scorer-1001 and effusion-stratum-1001 (auto-merge expected).
- High IA Plante residuals are reported, not masked.

## Verdict

**LAND `76b55001909ecdebc1a335ac6d45337870448075`**

Producer uses the product `VAPOR_PRESSURE` path on a fairly seeded melt state; effusion oxygen coupling is the stated shared battery convention; score hook is minimal and merge-clean vs in-flight score.py workers; refusals are typed and accounted; the 1 ms race is pre-existing / not tip-introduced.

— regolith-empirical
