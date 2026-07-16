# t-333 fO2-floor diagnosis

## TL;DR

**Verdict: LEAK.** The grind's intended `log10(fO2/bar)` is a melt-redox axis. It correctly drives the Kress91 fixed-ferric preparation and the AlphaMELTS absolute redox constraint. The subprocess-only vapor projection then incorrectly reused the solved melt `fO2_log` as overhead-transport `pO2_bar`. At grid levels -12/-11/-10 this produced explicit transport requests below the generic 1e-9 bar transport-model floor and caused all 8,368 deterministic `exception_valueerror` failures.

The fix separates the two bases. Melt-redox `fO2_log` remains byte-for-byte unchanged through Kress91 and AlphaMELTS. Subprocess vapor transport now receives its own backend/environment setpoint, currently the unknown-body default `DEFAULT_VACUUM_FLOOR_BAR = 1e-9` bar. A three-point real AlphaMELTS smoke at melt-redox fO2=-11 succeeded at 1500/1600/1700 C and reported transport pO2=1e-9 bar for every point.

## Exact flow and failure

1. `scripts/grid_pregrind.py:433-525` constructs the composition x temperature x `intended_fO2_log` grid and stores each intended value on `GridPoint`.
2. `scripts/grid_pregrind.py:544-588` passes that intended melt-redox value to `kress91_split(fO2_log=...)` and repartitions FeO/Fe2O3 while conserving Fe atoms. This is the designed Kress91 consumer and is correct.
3. `scripts/grid_pregrind.py:697-725` preserves the same intended value as the point input `fO2_log`; `scripts/grid_pregrind.py:2228-2238` also persists the separately named `intended_fO2_log` provenance column.
4. `scripts/grid_pregrind.py:1190-1212` rejects stale epoch-2 jobs unless persisted `fO2_log == intended_fO2_log`. `scripts/grid_pregrind.py:1408-1423` then calls the subprocess backend with that melt-redox `fO2_log`.
5. `simulator/melt_backend/alphamelts.py:790-795` carries the value into the prepared solve. `simulator/melt_backend/alphamelts.py:2065-2080` selects the AlphaMELTS `Absolute` fO2 path and `simulator/melt_backend/alphamelts.py:2092-2095` writes it to the MELTS input. This second melt-redox consumer is intentional and unchanged.
6. After a successful engine solve, `simulator/melt_backend/alphamelts.py:2052-2062` invokes the builtin vapor projection. In base commit `55fd8bc`, the request at then-lines 3645-3658 set both `intrinsic_fO2_log=eq.fO2_log` and `pO2_bar=10**eq.fO2_log`. That second assignment crossed from the melt-redox basis into the overhead-transport basis. Git blame traces its introduction to `ea3b106` (subprocess full-capture vapor projection).
7. The builtin provider explicitly treats the channels independently: `engines/builtin/vapor_pressure.py:762-785` resolves overhead transport pO2 separately, while `intrinsic_fO2_log` drives melt dissociation/redox. `engines/builtin/vapor_pressure.py:1428-1453` documents that contract.
8. `engines/builtin/_common.py:320-347` resolves an explicit transport `pO2_bar` and raises if it is below the request's transport floor. Thus melt fO2=-11 leaked across the boundary as transport pO2=1e-11 bar and raised: `explicit pO2_bar=1e-11 below transport model floor 1e-09`.

## Why the 1e-9 floor is not a melt-redox validity limit

`simulator/environment.py:9-13` defines pressure floors for the environment/transport model. `simulator/environment.py:56-73` says 1e-9 bar is the historical default when body is missing or unknown, retained for golden neutrality. It is not a lower bound on physically meaningful melt fugacity. The same module already has different body values (Moon 1.3e-12 bar, asteroid/deep space 1e-14 bar, Mars ambient), while t-019/CF-1b remains the deferred task to make all residual consumers body-aware.

The static grind point contains no headspace ledger, campaign gas-cover history, or body/environment field from which to derive a live transport pressure. Its correct present transport basis is therefore the independent unknown-body backend default, not `10**melt_fO2_log`. This preserves the current model floor without clamping or deleting the lunar-IW melt-redox corner.

## Fix

- `scripts/grid_pregrind.py:673-694` now gives the subprocess backend an explicit `vapor_transport_pO2_bar=DEFAULT_VACUUM_FLOOR_BAR`. ThermoEngine config is unchanged.
- `simulator/melt_backend/alphamelts.py:339-381` stores and validates that independent backend/environment setpoint.
- `simulator/melt_backend/alphamelts.py:3661-3678` now passes the independent value as `control_inputs['pO2_bar']` while retaining `eq.fO2_log` in both melt-redox channels. The derivation comment names both bases and the CF-1b boundary.
- `tests/test_grid_pregrind.py:94-140` pins the subprocess-only config wiring.
- `tests/test_alphamelts_backend.py:550-593` sends a non-default 2e-9 bar transport setpoint through `initialize()`, then pins fO2=-11 as melt-redox while the emitted request retains that independent transport value.

Kress91 partitioning, the AlphaMELTS absolute fO2 input, cache keys, data, and runner goldens were not changed.

## Verification

- Required grid suite: `tests/test_grid_pregrind.py -n0` -> **58 passed**.
- Post-review config-boundary regression plus the required grid suite -> **59 passed**; the non-default 2e-9 bar sentinel survived initialization into the emitted vapor request.
- Basis/floor diagnostics plus focused AlphaMELTS regression -> **16 passed**.
- Real subprocess scratch smoke on `lunar_mare_low_ti`, melt-redox fO2=-11, T=1500/1600/1700 C -> **3/3 status=ok**; each reported overhead-transport pO2=1e-9 bar.
- Full `tests/test_alphamelts_backend.py -n0` -> **155 passed, 1 unrelated live-engine timeout**. The sole failure timed out inside the 75 C AlphaMELTS subprocess solve before the changed post-solve vapor projection ran.
- `git diff --exit-code -- tests/goldens tests/fixtures/runner` -> clean; runner golden bytes untouched.
- `git diff --check` -> clean.

## Corpus recovery recommendation (do not execute here)

The 8,368 failed cells are re-queueable after this fix. Do not mutate or re-run the completed corpus from this worktree.

Preferred operational sequence:

1. Land t-334's documented manifest-compat read/retry path, then use `--retry-failed exception_valueerror` against the completed v1 database; or
2. If t-334 is not available, create a fresh top-up batch/database for the affected cells and merge it through the existing cache merge tooling after its normal manifest checks.

Recommend option 1 when preserving the completed corpus in place matters; use a fresh top-up when schedule matters more than waiting for compatibility support. Do not clamp the melt-fO2 grid at -9: that would discard the mandate-primary lunar-IW redox corner and would conceal a wiring defect rather than fix it.

## Changed paths

- `scripts/grid_pregrind.py`
- `simulator/melt_backend/alphamelts.py`
- `tests/test_grid_pregrind.py`
- `tests/test_alphamelts_backend.py`
- `docs-private/research/2026-07-16-t333-fo2floor/findings.md`
