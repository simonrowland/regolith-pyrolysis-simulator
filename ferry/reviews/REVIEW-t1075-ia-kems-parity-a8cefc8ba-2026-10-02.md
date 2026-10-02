# REVIEW — t-1075: IA/openimcc KEMS calibration flag parity

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `a8cefc8ba67d82c3aa12a27f5f4cb513f8ccb891` on `review/t1075-ia-kems-parity`
- **Green parent:** `191960ce8a657a25681aad624dd64670bf85df6a`
- **Range:** `191960ce8a657a25681aad624dd64670bf85df6a..a8cefc8ba67d82c3aa12a27f5f4cb513f8ccb891` (two commits)
  - `e1b6f70bffd7c1a6ca27e54dbe581063d4720281` Test KEMS calibration flag parity
  - `a8cefc8ba67d82c3aa12a27f5f4cb513f8ccb891` Assert KEMS parity band exclusion
- **REQ:** `/workspace/ferry-inbox/REQ-t1075-from-regolith-physics-2026-10-02.md`
- **Mutation:** `/workspace/ferry-inbox/regolith-physics-ia-battery-2026-10-01/t1075_ia_flag_mutation.py`
- **Date:** 2026-10-02 ~11:42–11:45 ET
- **Mode:** read-only for product code; extracts not edited; no force-push. Targeted VPS unit tests only (no full W3 / full-store score).

## Scope

Ticket t-1075 (requested by regolith-main): on a `calibration_not_grounded` (uncalibrated Knudsen) row, internal-analytical's residual carries the same flag and is excluded from KEMS band derivation exactly as openimcc's — field-by-field residual comparison + explicit no-band assertions for both engines. Test-only; no production change expected.

Files: `tests/battery/test_score.py` **+42** only (`git diff --stat` vs green). No simulator/production paths touched.

New test: `test_uncalibrated_kems_residual_matches_between_oxygen_balance_engines`.

## Attack results

### (1) Field-by-field residual comparison IA ↔ openimcc — **PASS**

Tip builds residuals for `Engine.OPENIMCC` and `Engine.INTERNAL_ANALYTICAL` on the same uncalibrated KEMS partial-pressure fixture (`F.kems_experiment(kn=None, calibrated=False)` + `_uncalibrated_kems_partial_row`). Assertions:

- `replace(openimcc, key=…, candidate=…) == internal_analytical` (full Residual equality after key/candidate normalize)
- `openimcc.notices == internal_analytical.notices`
- `openimcc.numeric == internal_analytical.numeric`
- `flagged_strata(…)` for both equals `(FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED,)` and match each other

### (2) Explicit no-band assertions gate band inclusion for BOTH engines — **PASS**

Not notice-only: tip asserts for both engines

- `status is ResidualStatus.NO_BAND`
- `score_eligible is False`
- `numeric.decision_band is None`

If IA entered KEMS band derivation despite the flag, `NO_BAND` / `decision_band is None` / `score_eligible is False` would fail. Nearby existing `test_uncalibrated_kems_partial_pressure_is_excluded_from_band_population` still asserts `derive_kems_partial_pressure_band(…) is None` for uncalibrated replicates (unchanged by tip).

### (3) Diff vs green is tests-only — **PASS**

`git diff --name-only 191960ce8..a8cefc8ba` → only `tests/battery/test_score.py`. Zero production/simulator file changes.

### (4) Mutation non-vacuous — **PASS**

Ran from slot worktree:

```
.venv/bin/python /workspace/ferry-inbox/regolith-physics-ia-battery-2026-10-01/t1075_ia_flag_mutation.py
```

Plugin drops IA's `calibration_not_grounded` / `UNVERIFIED_APPARATUS` notice from `compile_residual`. Result: **1 failed** on the field-equality assert (`notices` diverge: openimcc keeps the flag, IA becomes `()`). Script printed `mutation detected: IA calibration flag removal fails parity test` and exited 0. Proves the parity test would also fail if IA lost the flag.

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/battery/test_score.py` (full file, 159 collected) | **159 passed** |
| `tests/battery/test_score.py::test_uncalibrated_kems_residual_matches_between_oxygen_balance_engines` | **1 passed** |
| `tests/battery/test_score.py::test_uncalibrated_kems_partial_pressure_is_excluded_from_band_population` | **1 passed** |
| `tests/battery/test_internal_analytical_battery_engine.py` (4 collected) | **4 passed** |
| `tests/chemistry/test_flattering_default_guard.py` (307 collected) | **307 passed** |
| Combined (`-q -n 0`) | **470 passed**, 1 warning (VapoRock unavailable — pre-existing env) in ~48 s |
| Mutation `t1075_ia_flag_mutation.py` | **detected** (parity FAIL under IA flag drop; script exit 0) |

Not run (policy/cost): full W3, full-store score, Mac Studio green gate.

**Env note:** this seat run had **no** openimcc `ModuleNotFoundError` / `OpenImccUnavailableError` in the targeted suites (prior bounce seats sometimes saw openimcc-absent fails on unrelated paths). Tip does not touch openimcc adapter paths.

## Verdict

**LAND `a8cefc8ba67d82c3aa12a27f5f4cb513f8ccb891`**

Test-only tip; IA and openimcc share `calibration_not_grounded` + `NO_BAND` / ineligible / `decision_band is None` on uncalibrated Knudsen KEMS rows; mutation proves non-vacuous; 470/470 targeted tests green.

— regolith-empirical
