# V5 — verify S14 per-step flags dropped

**Verifier:** adversarial re-check of `/workspace/ferry-inbox/sweeps/S14-per-step-flags-dropped.md`  
**Code base:** `origin/review/r6-r8-fix` @ `1f8df6cbe` (worktree `/workspace/repos/wt/slot-07`, branch `verify-v5-s14`)  
**Sweep tip cited:** `origin/work-v064-green` @ `fbe3491b2` — ancestor of this base; cited line nums still match for all 8 P1 sites.  
**Compare branch:** `origin/review/n1-run-commissioning` @ `3a7824764` (“Report out-of-band engine commissioning beside run and optimizer numbers.”)  
**Also noted:** `origin/review/s7-battery-flags` @ `472febdd3` (battery score harvest — not on n1).  
**Calibration:** Batch Y / VERIFY-RULES — unflagged extrapolated / warned values = **P1** flag-contract break (not P0 unless a wrong number reaches result/score/ledger today). Null hypothesis = false positive.  
**Method:** static audit + minimal constructed inputs (no AlphaMELTS subprocess; diagnostics injected via `_record_equilibrium_status` / `assess_engine_commissioning`). READ-ONLY on product; no push.

## n1 fix pattern (seed reference)

`3a7824764` does **not** pack commissioning onto `HourSnapshot` / `build_per_hour_summary`. Instead:

1. `_record_equilibrium_status` → `_note_engine_commissioning_from_last_diagnostics` accumulates out-of-band steps on `sim._engine_commissioning_steps`.
2. `engine_commissioning_run_notice()` folds via `aggregate_engine_commissioning_notice` (authority, notices[], first/last hour, count); **None** when every step in-band.
3. Runner attaches `run_metadata["engine_commissioning_notice"]` (+ product-classification markdown sentence); objective / evaluate attach the same key beside scores. **Numbers unchanged.**

That is the pattern other S14 sites should mirror: step-stamp → sim accumulate → run/optimizer carrier (absence = in-band / no warning).

## Constructed seed proof (base `1f8df6cbe`, 2026-09-22 ~21:10 EDT)

```
assess_engine_commissioning('alphamelts', sio2=50, T=1773.15 K)
  → notice.authority='extrapolated', failed=('temperature_range',)
  band PUBLISHED_TEMPERATURE_CERTIFIED_K = [1073.15, 1700]
  TE twin same; in-band T=1500 K → notice=None

_record_equilibrium_status(status=ok + commissioning diagnostics + warning string)
  → _last_backend_status == 'ok'
  → commissioning_notice present in _last_backend_diagnostics
  → no engine_commissioning_run_notice / _engine_commissioning_steps on base
HourSnapshot: no commissioning / sulfsat fields
_vapor_pressure_source_report: only VP facet keys (commissioning absent)
build_per_hour_summary source: no commissioning / certified_band
```

## Verdict table (8 claimed P1)

| finding | exists? | live on base? | your sev | smallest fix (n1 pattern) | shared-root | n1 / sibling status |
|---|---|---|---|---|---|---|
| **1 seed** AM `_apply_engine_commissioning` (`alphamelts.py:2159-2198`) → `_record_equilibrium_status` (`core.py:8923-8937`) → no snapshot / run flag | **yes** — stamps `commissioning_notice`+`authority=extrapolated`+`certified_band` while `status` stays ok; warnings string appended but **never** copied by `_record_equilibrium_status` | **yes** — any successful AM eq outside T/SiO₂ band (e.g. 1500 °C = 1773.15 K) | **P1** | Accumulate step notices on sim; attach run/optimizer `engine_commissioning_notice` (do not equate `ok` with certified) | **R1** | **FIXED** on `3a7824764` |
| **2** TE `_commissioning_notice_fields` (`thermoengine.py:40-55` / `:326`) | **yes** — same three keys wired into TE equilibria | **yes** — TE hour outside band | **P1** | Same R1 rollup (MELTS-family) | **R1** | **FIXED** on n1 (shared accumulate from `_last_backend_diagnostics`) |
| **3** `build_per_hour_summary` (`runner/__init__.py:2429+`) omits commissioning keys | **yes** — schema has yields/O₂/Knudsen/vapour notices; no `commissioning_notice` / `certified_band` (the “authority” hits are wall-deposit sticking, unrelated) | **yes** — every commissioned hour’s per_hour row | **P1** on base | n1 chose **run-level** aggregate (with hour span) instead of per-hour columns — that satisfies the S14 run-facing predicate | **R1** | **Addressed** by n1 pattern (per_hour still omits keys by design; not a residual P1 against the chosen fix) |
| **4** optimizer `objective.py:3146-3155` + `run_executor.py:466-507` treat `backend_status=="ok"` as usable | **yes** — SC-109 comment admits ok ≠ certification; history is status tokens only; commissioning never flips status | **yes** — optimizer evaluates extrapolated campaigns with numeric scores and no notice on base | **P1** | Attach `engine_commissioning_notice` on objective summary / evaluate payload (n1); do not require refuse-ranking for P1 | **R1** | **FIXED** on n1 (`objective.py` + `evaluate.py` attach notice; scores unchanged) |
| **5** `_vapor_pressure_source_report` (`runner/__init__.py:1800-1846`) mines diagnostics only for VP keys | **yes** — constructed: commissioning present in `_last_backend_diagnostics`, absent from VP report | **yes** on base as a commissioning drop at that boundary | **P1** on base (commissioning facet) | Prefer dedicated accumulate (n1) over stuffing commissioning into the VP report | **R1** | **FIXED** for commissioning on n1 (captured at `_record_equilibrium_status` before last-diagnostics overwrite; VP report remains VP-only by design) |
| **6** `_attach_post_equilibrium_sulfsat` (`core.py:8560-8608`) + `_last_sulfur_saturation_result` | **yes** — `out_of_range`/`unavailable` → `result.warnings` + sim store; comment claims UI/telemetry; **zero** hits in `runner/` / `HourSnapshot` / optimizer | **yes** when Stage-0 S inventory > 0 and gate not `in_range` | **P1** | Mirror n1: accumulate sulfsat notices → `run_metadata` / per-hour / optimizer carrier; escalate only if S masses affect reported yields | **R2** | **OPEN** on n1 (no sulfsat rollup in that commit) |
| **7** `_capture_campaign_summary` rump (`core.py:12637-12658`) + `run_executor.py:315-358` | **yes** — `rump_expectation` embedded in campaign_summary / `_rump_expectation_warnings`; executor only promotes **C4/C6 refusal**; runner final doc has no rump key. (UI socket emits full `campaign_complete_summary` — live event only, not run/optimizer flag.) | **yes** when `_rump_expectation_diagnostic` returns a warning | **P1** | Forward rump warning into `run_metadata` (and optimizer if rump masses scored); keep C4/C6 refusal path distinct | **R3** | **OPEN** on n1 |
| **8** binary_pot `equilibrate_cell` harvest (`binary_pot_battery.py:2195-2226`) + scoring (`binary_pot_scoring.py:1208-1209` / `:1282-1283`) | **yes** — success path harvests **imcc_notices only**; `EquilibrateCell.authority`/`certified_band` exist but are filled for **qualification gate** only, not from `diagnostics['commissioning_notice']`. Scoring hardcodes `authority="bridge"` (ignores `cell.authority`). | **yes** — MELTS-family cell at 1773.15 K publishes numeric activities with `authority=bridge` on score rows | **P1** | Land `review/s7-battery-flags` harvest + `cell_score_authority` (do not recount as separate P0) | **R4** | **OPEN** on base/n1; **FIXED** on `review/s7-battery-flags` (`472febdd3`) |

## P2 / P3 (not under-rated)

| finding | exists? | live? | your sev | note |
|---|---|---|---|---|
| `_last_phase_context_diagnostic` (`core.py:2414-2418` / `:13002`) | yes | yes (cleared next hour) | **P2** | Debug bag; not a trust-gate flag today |
| `_last_char_{lance,feo_reduction,contamination}_diagnostic` (~10560-10693); `_c0_char_diagnostic` only reads stage0 foulant partition_carbon | yes | yes on C0 char paths | **P2** | Same class as phase-context |
| `_last_extraction_completeness` / `_last_overlap_evaporation` / `_last_freeze_gate` (~12159-12440, `:3256`) | yes | yes when those paths fire | **P2** | Step-local; freeze-gate itself is separate gating |
| `silent_zero_diagnostic` / `partial_melt_offgassing_diagnostic` on `HourSnapshot` but not `build_per_hour_summary` | yes | yes when stamped | **P3** | Comments already mark diagnostic-only / non-gating |

## Shared roots

1. **R1 — engine commissioning run rollup** (sites 1–5): one accumulate + `engine_commissioning_notice` on run_metadata / product report / optimizer. **Landed on n1.**  
2. **R2 — sulfsat run rollup** (site 6): same shape for sulfur-saturation gate notices.  
3. **R3 — rump warning run rollup** (site 7): promote `rump_expectation.warning` beyond campaign_summary / UI socket.  
4. **R4 — battery cell authority harvest** (site 8): already implemented on `review/s7-battery-flags`.

## Counts

| class | S14 claimed | verifier on base `r6-r8-fix` | after n1 + s7 (compare) |
|---|---|---|---|
| **P1 confirmed** | 8 | **8** | — |
| **P1 false positive** | — | **0** | — |
| **P1 fixed on n1 (`3a7824764`)** | — | — | **5** (sites 1–5 / R1) |
| **P1 still open (need R2/R3)** | — | — | **2** (sulfsat, rump) |
| **P1 fixed on s7-battery-flags (not n1)** | — | — | **1** (battery) |
| **P2 held** | 3 | **3** | 3 |
| **P3 held** | 1 | **1** | 1 |
| **P0** | 0 | **0** | 0 |

No severity inflation: all eight P1 descriptions match live flag-contract gaps on this base. Commissioning seed is the n1 exemplar; apply the same accumulate→run-carrier pattern to sulfsat and rump; take battery from s7.

## TL;DR

- **CONFIRMED P1: 8 / 8** on `origin/review/r6-r8-fix` @ `1f8df6cbe`. **False positives: 0.** P0: 0. P2/P3: held (3+1), not under-rated.  
- **n1 `3a7824764` FIXED R1 (sites 1–5)** via run/optimizer `engine_commissioning_notice` (not HourSnapshot packing). **Still open:** sulfsat (R2), rump (R3). **Battery (R4)** fixed on `review/s7-battery-flags`, not n1.  
- Path: `/workspace/ferry-inbox/verify/V5-s14-per-step-flags.md`

READY: /workspace/ferry-inbox/verify/V5-s14-per-step-flags.md
