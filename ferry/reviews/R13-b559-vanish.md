# R13 — zero-observation source must not silently vanish (b559)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `ef1c2a97b` on `origin/review/b559-vanish` (NOT landed on green; parent `e2897a867` alias single-count)  
**Files:** `scripts/bench_readiness.py`, `simulator/battery/waypoints.py`, `tests/battery/test_migrate_benches.py`

**Intent:** Readiness walked experiments only, so a work whose rows live in `context[]` (or that has no experiments) dropped out of the report. Keep its canonical `Work.source_ids[0]` and mark `GapReason.NO_SCOREABLE_OBSERVATIONS` instead of omitting it. Do not invent scoreable observations; do not re-open alias double-count.

**Attack surface:** zero-obs source still vanishes; invent observations / attribution to keep a source; readiness double-counts (aliases / summary).

**Method:** Static read of tip diff + live works/experiments scan (skip heavy observation shards) + constructed adversarial probes (shared canon, alias-as-second-work, orphan `work_id==source_id`, swapped-alias zeros, shared gap object, exp-without-obs). Pytest on tip worktree at `ef1c2a97b`. Mode: **ran-tests**.

---

## Findings

### P2 — Orphan experiment whose `work_id` equals a zero-exp work’s `source_ids[0]` invents attribution and suppresses the new gap (latent)

**Evidence (file:line on `ef1c2a97b`):**

- Experiment walk fallback when the work is missing (`bench_readiness.py:348–355`):
  - `work = works.get(experiment.work_id or "")`
  - `source_ids = work.source_ids if work is not None else (experiment.work_id or "unknown",)`
  - `row_source_ids = source_ids[:1]` then `by_source` / `source_readiness` append.
- New helper (`:265–281`, `:446–448`) only adds a no-scoreable row when `source_ids[0] not in set(by_source)`.
- Gap emission (`:460–475`) only runs when `source_readiness[source_id]` is empty.

**Constructed trigger:**

- Work `zero-f` with `source_ids=("SRC-F",)`, **no** experiments.
- Orphan experiment `work_id="SRC-F"` (source id string, not a real `work_id`), bench present; `works.get("SRC-F") is None`.
- `report(...)` → one source `SRC-F` with `experiments` length 1, consumer status **ready** (under test stub), `informational_gaps` **without** `no_scoreable_observations`.
- The zero-exp work never receives the new gap; the source is “kept” by invented attribution.

**Live check:** 0 orphan experiments; 5 zero-exp works’ canons do not appear as any `experiment.work_id`. **Latent.**

**Why P2:** Named invent attack. Pre-existing orphan fallback plus the new `listed` gate turns a true zero-obs work into a normal readiness row instead of marking the gap. Not P1: no live hit on this tip’s store.

**Fix direction:** Attribute into `by_source` only when `work is not None` (orphans → explicit `unknown` / separate bucket that does not suppress work canons); or compute “missing” from works with zero experiments and refuse to treat orphan-only rows as satisfying that work’s source. See optional notes in `R13-fix.patch` (shared-gap copy only applied there).

---

### P3 — Shared mutable gap dict across all consumer/engine slots

**Evidence:** `_no_scoreable_observations_row` (`:284–311`) builds one `gap = {...}` and reuses the same object in every `consumers[*].gaps` and `engines[*].gaps`; only `informational_gaps` gets `dict(gap)`.

**Probe:** `consumers[0].gaps[0] is engines[0].gaps[0]` → True; mutating `count` bleeds into every consumer/engine gap; informational copy stays 0.

**Why P3:** Defense-in-depth; JSON serialization does not share refs, but in-process callers can corrupt the row.

**Fix direction:** `R13-fix.patch` — fresh gap dict per slot.

---

### P3 — Reason name says “no scoreable observations”; detector is “canonical source never reached by experiment walk”

**Evidence:**

- Docstring / enum (`waypoints.py:52–54`, `bench_readiness.py:265–270`) claim no scoreable **observations** (context ≠ observations).
- Detector (`:275–280`) only checks whether `work.source_ids[0]` already appears in `by_source` from the experiment loop.
- Probe: work with experiments but **empty** observations map → source listed as normal readiness (status from experiment/bench), **not** `no_scoreable_observations`. That matches “has experiments to walk,” but overclaims the reason string for the empty-experiment case only.

**Why P3:** Live empty-experiment works (5 on this tip) correctly match both readings. Naming/detection mismatch is latent confusion for future “experiments exist, observations do not” rows.

**Fix direction:** Rename helper / reason comment to “no experiments attributed to canonical source,” or additionally require zero point-condition observations when experiments exist (only if product intent expands).

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Zero-obs source still vanishes | **Fail (safe) on live + distinct constructed.** Live store: 238 works, 5 with `experiments: []` (`lebrun-2013-…`, `charnoz-2023-…`, `vanbuchem-2025-…`, `vanbuchem-2023-…`, `lpi-lunar-sourcebook-chapter08`); helper would list all five canons; old walk listed 233. Distinct zero + scored → `source_count` 2, gap row under canonical only (alias not fanned). Shared-canon scored+zero → source stays listed via scored experiments (source grain; gap reason for sibling work not emitted — by design at source_id key). |
| Invent observations / attribution to keep it | **Hit — P2 latent.** New row does not invent Observation records (`experiments: []`, `count: 0`). Orphan `work_id == source_ids[0]` invents experiment attribution and suppresses the gap (above). Context rows are not loaded as observations (`load_migrated_store` reads `observations` only). |
| Readiness double-counts | **Fail (safe) on R8 alias contract.** Single work `source_ids=("janaf-4th","nist-janaf-4th")` → one row (existing test). Zero-exp work with aliases → only `source_ids[0]` listed (test). Summary: ready/partial unchanged; gap +1 per consumer/engine for a new empty source (test). Two distinct works that swap alias tokens → two gap rows (correct for two `work_id`s; not a re-fan of one work). |

---

## What looks sound

- New `GapReason.NO_SCOREABLE_OBSERVATIONS` is explicit; absence is not a zero score.
- Empty sources do not inflate ready/partial; blockers record `experiment_count: 0`.
- Live motivating cases (context-only van Buchem 2025 / LPI ch.8; three empty model extracts) are exactly the five zero-exp works; extracts-v2 where present have `observations: []`.
- Alias single-count from `e2897a867` preserved for works that have experiments.

### Residual notes (not severity)

- `count: 0` on the synthetic gap matches `len(experiment_ids)` but overloads the usual “experiments affected” meaning with “zero observations.” Callers that skip `if gap["count"]` would ignore it; status/top_blockers still surface the source.
- Gap is duplicated into `informational_gaps` and every consumer/engine `gaps` (blocking + “informational” label).

---

## Tests

Tip worktree at `ef1c2a97b`, repo `.venv`, `-o addopts=`:

- `tests/battery/test_migrate_benches.py -k 'readiness or aliased or unscoreable or no_experiments or source'`: **6 passed**
- Adversarial probes (shared canon, alias second work, orphan invent, swapped zeros, shared gap identity, exp-without-obs): **P2 invent confirmed; double-count/vanish live paths safe**

Optional fix: `ferry/reviews/R13-fix.patch` (per-slot gap copy only). Not applied to `review/b559-vanish`; do not push.

---

## Verdict rationale

Live claim holds: the five zero-experiment sources no longer vanish; aliases are not re-double-counted; the new row does not fabricate observations. One latent invent path (orphan `work_id` colliding with a source id) can still keep a zero-obs source without the new gap. Harden that interaction before treating the contract as closed under hostile store shapes.

VERDICT: R13 | LAND-WITH-FIXES | P0=0 P1=0 P2=1 P3=2 | ran-tests
