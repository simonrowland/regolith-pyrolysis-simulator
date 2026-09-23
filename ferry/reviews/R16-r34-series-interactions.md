# R16 — r34-hardening series interactions

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Series under review:** `origin/work-v064-green..origin/review/r34-hardening` (12 commits; tip `07ad01dae`)  
**Worktree:** `/workspace/repos/wt/slot-06` @ detached `07ad01dae`  
**Scope:** Cross-commit interactions only. Single-commit defects already owned by R11 (tip hardening), R15 (`d4f91337f` printed fO2 extracts), R6–R9 (JANAF / interval / alias pieces) are not re-litigated unless the series *composition* creates a new hole.

**Commits in range (oldest → newest):**

| SHA | Subject |
| --- | --- |
| `84c1d2939` | melt_backend: VapoRock MELTS commissioning-band notice (t-959) |
| `28546a466` | chore: stop tracking private review notes that bypassed the ignore rule |
| `a782e161c` | literature: register NIST-JANAF `.txt` cache as INDEX asset |
| `937d668be` | tests: guard that `docs-private/` is never git-tracked |
| `e2897a867` | battery: count each aliased work once; species-rail ledger own work id |
| `07046e90b` | battery: map JANAF printed phase-transition labels onto closed phase map |
| `a49cef012` | Type `engine_point` refusal for interval-valued printed conditions |
| `9e1271233` | extracts: resolve structured `fidelity_samples` pins against `context[]` |
| `2e9e17c3d` | battery: **regenerate** derived store (JANAF provenance / phase / counting) |
| `d4f91337f` | extracts: land printed per-run log fO2 for four sources |
| `22cf80906` | battery: do not read a `10^n` string as printed log fO2 (+ keep pO2 interval) |
| `07ad01dae` | battery: refuse cross-work equipment link; drop stale extract siblings |

**Intent of the series (as a unit):** Land JANAF provenance/phase/counting + readiness alias fix (with a mid-series regen), type interval→engine refusals, land printed per-run log fO2 for four sources, then harden migrate/waypoints against R3/R4 defects.

**Attack surface (series):** two commits on the same waypoint/migrate path that fight or leave a hole; mid-series regen that does not cover later extract/migrate commits; consumers that read the committed store vs tests that live-migrate; interval keep (22cf) vs typed `interval_needs_point` (a49) vs `oxygen_condition` route order.

**Method:** Static range log + overlapping-file diffs (`migrate.py` ×4, `waypoints.py` ×2). Tip `scripts/check_store_freshness.py --head HEAD`. Count `logfO2`/`log_fO2`/`log10_fO2` in source extracts vs `fO2_log` in `extracts-v2` for the four fO2 sources. Trace `load_migrated_store` consumers (`bench_readiness`, `score`). Read `oxygen_condition` route order against 22cf interval-keep. Pytest `tests/battery/test_printed_fo2.py` on tip. Mode: **ran-tests**.

---

## Findings

### P1 — Mid-series regen then three later commits: tip derived store is stale vs tip extracts/migrate (live for store consumers)

**Evidence (on tip `07ad01dae`):**

- Order: `2e9e17c3d` regenerates the store for JANAF/alias/phase landings, then **`d4f91337f` → `22cf80906` → `07ad01dae`** change extracts and/or `migrate.py` / `waypoints.py` / `validate.py` with **no** follow-up regen.
- In-tree tripwire agrees:

```text
$ python3 scripts/check_store_freshness.py --head HEAD
STALE: store last touched at 2e9e17c3d … regenerate the derived store …
3 later commit(s) touched migrate inputs without a store regen:
  07ad01dae … migrate.py, validate.py
  22cf80906 … migrate.py, waypoints.py
  d4f91337f … four extracts (holzheid / kems-140 / sossi-2020 / thomas-2022)
```

- Concrete fo2 hole (source cells present, derived landings absent):

| Source stem | Printed log keys in `extracts/` | `fO2_log` hits in `extracts-v2/` |
| --- | ---: | ---: |
| `holzheid-1997-feo-nio-coo-activity-metal-saturated` | 33 | **0** |
| `sossi-2020-cu-zn-isotope-evap-formalism` | 122 | **0** |
| `kems-140-heck-2025` | 81 | **0** |
| `thomas-2022-chlorine-bonding-silicate-melts` | 43 | **0** |

- `extracts-v2` for those stems was last rewritten on green-era `8c7fea238` / `fbe3491b2` paths — **not** by `2e9e17c3d` (JANAF-scoped regen) and not by any post-`d4f` commit.
- Live consumers read the committed store, not a fresh migrate: `migrate.py:1466` `load_migrated_store` → `extracts-v2`; `scripts/bench_readiness.py:278`, `simulator/battery/score.py:1518`.
- Live migrate + tip tests *do* land the logs (`tests/battery/test_printed_fo2.py`: **12 passed** on tip) — so R15’s single-commit claim holds under re-migrate, while the **series tip as a landed unit** still ships a store that never saw `d4f`/`22cf`/`07ad`.

**Why P1 (live-in-data, series interaction):** Named series attack — two (here: three) changes after a regen leave a hole. Tip advertises printed per-run log fO2 and migrate hardenings, but readiness/score/`load_migrated_store` still see the pre-landing world. Missing facts (not invented wrong fo2): `oxygen_condition` stays fail-closed without a printed log, so this is a contract / completeness break for the series tip, not a silent wrong number. `07ad01dae`’s stale-*sibling* unlink cannot heal same-stem *stale content*.

**Fix direction:** One tip regen after `07ad01dae` (as `check_store_freshness.py` prints: `scripts/battery_migrate.py` + store summary). Do not land the series claiming store consumers see the four fo2 landings until that commit exists. Out of scope for this review to run the regen.

---

### P2 — `a49cef012` typed `interval_needs_point` and `22cf80906` interval-keep do not compose into a consumer-visible conflict on `oxygen_condition`

**Evidence:**

- `a49cef012` adds `GapReason.INTERVAL_NEEDS_POINT` and benches stamp it when the **selected** waypoint value is an interval (`simulator/battery/generators/bench.py` engine_point readiness).
- `22cf80906` keeps an existing experiment `oxygen_partial_pressure_Pa` INTERVAL when a later printed point arrives (`migrate.py` ~6206–6213) and mute-flags `_oxygen_pressure_conflict` (R11 already notes the flag is ephemeral — not re-scored here).
- Same commit’s regression (`test_printed_fo2.py` interval-keep) asserts the observation **still** receives its own `fO2_Pa` POINT.
- Tip `oxygen_condition` (`waypoints.py` ~881–910) appends routes in order: printed `fO2_log` → observation `fO2_Pa`→log → experiment control pO2→log → …. First selected wins (`_result` keeps caller order).

**Constructed interaction:** Norris-shaped experiment pO2 INTERVAL + observation printed pO2 POINT (exactly the 22cf fixture shape). Experiment interval is preserved (22cf). Observation point is also present. `oxygen_condition` selects the observation-derived **point** route. Engine readiness never sees an interval → **`interval_needs_point` never fires**. No durable migrate issue records the disagreement (R11 P3). Series composition: interval-keep + typed interval refusal + existing route preference = interval preserved in the experiment record but invisible to the engine_point gap reason this series just introduced.

**Why P2 (latent):** Not live on the four new fo2 extracts (they land bare numeric logs, not this interval+point pair). Live only when an experiment-level pO2 interval coexists with an observation pO2 point — the interaction the 22cf test itself constructs. Weaker than P1 because fail-closed alternatives still avoid inventing a midpoint; the hole is “typed refusal / conflict does not surface where consumers look.”

**Fix direction:** When `_oxygen_pressure_conflict` is set for interval-vs-point, either (a) durable `ValidationIssue`, and/or (b) demote/omit the observation `fO2_Pa`→log route so `oxygen_condition` selects the preserved interval and a49’s `interval_needs_point` applies — or attach an explicit conflict notice on the waypoint. Owned as series composition; durable-flag detail overlaps R11 P3.

---

## Attack checklist (series)

| Attack | Result |
| --- | --- |
| Two changes to same waypoint path fight | **Partial hit — P2.** `a49` + `22cf` both touch `waypoints.py` / oxygen path; they do not overwrite each other, but interval-keep + route order bypasses `interval_needs_point` in the conflict fixture shape. Docstring-only 22cf edit on `oxygen_condition` composes cleanly with a49’s enum. |
| Regen then later extract/migrate commits leave a hole | **Hit — P1.** `check_store_freshness.py` STALE; four fo2 sources 0× `fO2_log` in tip `extracts-v2`; store consumers read stale derived data. |
| Alias-count fix × ledger relabel × regen disagree | **Fail (safe).** `e2897a867` before `2e9e17c3d`; regen commit message covers counting landings; ALIASES pin `species-rail-differential`. |
| JANAF INDEX × phase-label map × regen | **Fail (safe).** `a782e161c` + `07046e90b` precede `2e9e17c3d`; phase hook is `source_id == "janaf-4th"` only; ledger remap uses a different work id. |
| Fidelity `context[]` resolve × Thomas hoist to observations | **Fail (safe).** `9e1271233` then `d4f91337f` updates Thomas fidelity path `context[…].rows[0]` → `observations[…].series[0]`; path resolver accepts `series[0]`. |
| `rows`→`series` + `temperature_C`→`T_C` without migrate support | **Fail (safe) under live migrate.** Explosion iterates `series`; T keys include `T_C`; tip fo2 tests pass. Hole is store lag (P1), not key support. |
| Stale-sibling unlink × same-stem content refresh | **Hit folded into P1.** `07ad` unlinks missing stems only; cannot refresh same-stem pre-`d4f` `extracts-v2` bodies without regen. |
| VapoRock notice × battery series | **Fail (safe).** `84c1d2939` isolated to melt_backend / commissioning. |
| docs-private untrack × hygiene test | **Fail (safe).** Complementary (`28546a466` + `937d668be`). |

---

## What looks sound (series composition)

- Pre-regen JANAF/alias/phase commits are paired with `2e9e17c3d` — that half of the series is internally consistent.
- `d4f91337f` + `22cf80906` are complementary under live migrate: extracts expose `series` logs; migrate refuses `10^n`-as-log and keeps author numeric logs (R15 + R11 cover the singles).
- `a49cef012` does not invent midpoints; when an interval *is* selected, engine_point refusal is correctly typed (R9).
- Tip fo2 pytest green confirms extract↔migrate coherence when the store is rebuilt in-process.

### Residual notes (not severity / owned elsewhere)

- R11 P2 (`::` experiment declaration steal) and R11 P3 (ephemeral oxygen conflict; validate `work_id` guard) remain tip-hardening items — not re-counted.
- R15 P3 (`runs` still absent from oxygen table-key skip set) remains extract-landing latent.
- Four extracts still UNTRACED in freshness output (`bencze-…`, `charnoz-…`, `lebrun-…`, `vanbuchem-…`) look pre-existing vs this series’ fo2 stems (those four *have* v2 siblings — just stale ones).

---

## Tests

Tip worktree `07ad01dae`, `/workspace/repos/regolith-pyrolysis-simulator/.venv`:

- `tests/battery/test_printed_fo2.py`: **12 passed**
- `scripts/check_store_freshness.py --head HEAD`: **STALE** (3 post-regen input commits) — confirms P1
- Static probes: fo2 key counts extracts vs extracts-v2; `load_migrated_store` consumer paths; `oxygen_condition` route order vs 22cf fixture shape

No series fix patch (fix is a regen commit, not a code diff). Do not push.

---

## Verdict rationale

Single-commit claims mostly hold in isolation (R15/R11/R6–R9). As a **series tip**, the mid-regen then three follow-on commits leave store consumers without the printed fo2 landings and without 22cf/07ad migrate effects materialized — a live-in-data completeness break the in-repo freshness tripwire already names. A second, latent composition gap: interval-keep + `interval_needs_point` + `oxygen_condition` preference order never surfaces the 22cf conflict to engine_point. **LAND-WITH-FIXES** — regenerate the derived store at tip (and preferably close the R11 P2 `::` steal before treating cross-work equipment as fully closed).

VERDICT: R16 | LAND-WITH-FIXES | P0=0 P1=1 P2=1 P3=0 | ran-tests
