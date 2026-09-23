# R11 — R3+R4 hardening (printed fO2 + migrate equipment/orphans)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `07ad01dae` on `origin/review/r34-hardening` (NOT landed on green; parent `22cf80906`; branch also carries `22cf80906` over `d4f91337f`)  
**Files:** `simulator/battery/migrate.py`, `simulator/battery/validate.py`, `simulator/battery/waypoints.py`, `tests/battery/test_migrate.py`, `tests/battery/test_printed_fo2.py`

**Intent (claimed):** Close R3+R4 review defects: (1) no `10^n` string read as printed log fO2; (2) existing pO2 interval/bound kept and conflict-flagged when a printed point arrives; (3) cross-work equipment FK refused (typed) and validated; (4) extracts-v2 siblings of deleted/renamed extracts unlinked before write; (5) duplicate `context_id` under a work raises a typed error.

**Attack surface:** power-of-ten-as-log; interval collapsed to a point; silent cross-work equipment FK / id collision; stale extracts-v2 re-entering the store; last-wins wrong equipment row; `::` registry passthrough bypasses.

**Method:** Diff `22cf80906..07ad01dae` (tip) plus ancestry `d4f91337f..22cf80906` (R3 half of the claim). Static read of tip lines below. Adversarial tmp probes (unicode `10^n`, interval preservation, durable conflict visibility, normal cross-work refuse, `::` experiment-id steal, orphan+stale INDEX, duplicate context, validate without `work_id`). Pytest on tip worktree at `07ad01dae`. Mode: **ran-tests**.

---

## Findings

### P2 — `_registry_id` `::` passthrough still lets an extract *declare* another work's experiment id and own it

**Evidence (file:line on `07ad01dae`):**

- `migrate.py:6326–6333` — `_registry_id` still returns any token containing `::` unchanged (no work-scope check).
- `migrate.py:6388–6453` — `_lift_extract_registries` builds `experiment_id = _registry_id(work.work_id, "experiment", local_id)` then stores `payload["work_id"] = work.work_id`. A crafted `experiment_id: "<other_work>::experiment::…"` therefore **creates** that global id under the attacker's work when the attacker extract sorts first.
- `migrate.py:6530–6542` — the new cross-work gate only runs at equipment-link time: `experiment.work_id != work.work_id`. After a successful steal, work ids **match**, so the FK is written.
- **Constructed trigger:** extracts `aaa-attacker` (DOI `10.1234/ATTACK`) and `zzz-victim` (DOI `10.1234/VICTIM`). Attacker declares `experiments: [{experiment_id: "10.1234/victim::experiment::vic-series", …}]` and an equipment context naming that id. Tip migrate result: experiment owned by `10.1234/attack` with `equipment_context_id == "aaa-attacker::context::evil"`; victim gets `registry_issues` duplicate-payload and **no** experiment under the victim work. Direct foreign FK without steal still refuses (R4 P0 path closed — see checklist).

**Why P2 (latent):** Not the claim's direct “context names foreign live experiment” path — that is now typed-refused and validated. Residual cross-scope corruption via pre-existing `::` passthrough at **declaration** time; requires a malicious/malformed extract; none live in the corpus. The new work_id check cannot see the hijack because ownership was rewritten first. Duplicate-payload issue fires for the victim, but the foreign-shaped id remains attacker-owned with FK set.

**Fix direction:** Optional `ferry/reviews/R11-fix.patch` — when lifting experiments, refuse a `::`-bearing `experiment_id` that is not scoped to `work.work_id` (typed `referential_integrity`, do not insert). Keep `_registry_id` passthrough for lookup so the equipment-link path can still resolve + refuse foreign refs.

---

### P3 — interval/bound vs printed point: conflict is ephemeral only

**Evidence:**

- `migrate.py:6206–6213` — non-POINT previous → `_oxygen_pressure_conflict.add(experiment_id); return`. Interval stays (R3 P1 closed).
- `_oxygen_pressure_conflict` is only referenced at `:5993`, `:6190`, `:6212`, `:6215` — never copied onto `MigrationResult`, never appended as `ValidationIssue`.
- Probe: Norris-shaped interval + observation `oxygen_partial_pressure: "10^-9.1" atm` → `ValueKind.INTERVAL` preserved; `result.registry_issues` has **zero** oxygen/conflict entries.

**Why P3:** Claim wording “conflict-flagged” matches the in-memory mute set (blocks further experiment point landings). No durable typed audit trail for interval-vs-point (unlike point-vs-point, which clears `oxygen_partial_pressure_Pa` when a prior printed point was landed). Live safety property holds.

**Fix direction:** Append a `ValidationIssue(REFERENTIAL_INTEGRITY or CONFLICT)` when marking interval/bound vs point, or expose the set on `MigrationResult`.

---

### P3 — validate cross-work check skipped when context row lacks `work_id`

**Evidence:** `validate.py:740–743` — `row.get("work_id") is not None` guard. Probe: FK to `{context_id, equipment: …}` with no `work_id` → no equipment hard issue. Migrator always stamps `work_id` (`:6500`); hole is hand-edited / partial store only.

**Why P3:** Same shape as the R4-fix patch. Defense-in-depth; migrate+finalize path is covered.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| `10^n` string read as log fO2 | **Fail (safe).** `:5557–5558` skips when `_power_of_ten` matches; unicode minus / en-dash normalized (`:5368–5369`). Numeric / `"-9.1"` still land. Braces `10^{-9.1}` / `1e-9.1` do not match the regex and do not land a near-zero bogus log. |
| pO2 interval collapsed to a point | **Fail (safe).** Non-POINT previous kept; conflict set mute (`:6206–6213`). Fixture test + probe: interval digits unchanged; obs still get `fO2_Pa` points. Durable flag missing (P3). |
| Cross-work equipment FK (R4 P0 path) | **Fail (safe) on direct foreign ref.** Victim-then-attacker with qualified `experiment:` → typed `referential_integrity`, no FK; validate flags work mismatch when `work_id` present. Same-DOI shared work still allowed (test). Residual declaration steal (P2). |
| extracts-v2 orphan on delete/rename | **Fail (safe).** `:8910–8919` unlinks stems ∉ live `discover_extracts` before write. Probe: stale INDEX entry does not keep the orphan. Zero-obs live extracts still empty-rewritten below, not unlinked. |
| Duplicate `context_id` → wrong resolve | **Fail (safe).** `:6490–6497` raises `DuplicateContextIdError` before append/link. Distinct `source_id::context::raw` under a shared work still allowed. |
| Manufacture / hide residual oxygen | **Fail (safe) on the named bugs.** Docstring no longer claims printed log is bar-frame (`waypoints.py:884–889`). Power-of-ten skip prevents ~0 oxygen_condition stamp. |

---

## What looks sound

- Tip matches the R3-fix / R4-fix intent and adds a typed `DuplicateContextIdError` (better than bare `ValueError` in the review patch).
- Orphan unlink runs **before** extracts-v2 writes (`:8911–8919`) — safer ordering than the review patch’s post-dump placement.
- Validate + migrator share the work_id invariant for equipment FK; finalize still passes `context_rows`.
- Focused regression tests for all five claimed behaviors are in-tree and green.

### Residual notes (not severity)

- `_POWER_OF_TEN` still requires a bare `10^n` token; odd spellings are skipped via failed decimal parse rather than a typed refusal.
- `::` passthrough remains load-bearing for legitimate qualified cross-extract references within one work; only unscoped *declarations* need the lift-time refuse (P2).

---

## Tests

Tip worktree at `07ad01dae`, `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/battery/test_printed_fo2.py` (`power_of_ten` / `collapse` / `oxygen_condition_doc`): **3 passed**
- `tests/battery/test_migrate.py` (`equipment_fk` / `extracts_v2_orphan` / `duplicate_context` / `d036`): **12 passed** (15 with fo2 filter union)
- Adversarial probes: direct cross-work refuse confirmed; `::` declaration steal confirmed (P2); durable oxygen conflict absent (P3); orphan+stale INDEX confirmed safe

Optional fix: `ferry/reviews/R11-fix.patch` (lift-time refuse of foreign `::` experiment declarations). Not applied to `review/r34-hardening`; do not push.

---

## Verdict rationale

All five named claims hold against their direct attacks: `10^n` skip, interval keep, cross-work equipment FK refuse+validate, orphan unlink, typed duplicate context. One residual latent cross-scope path remains via pre-existing `::` registry passthrough at experiment **declaration** (bypasses the new work_id check after ownership steal). Two P3 defense/audit gaps. **LAND-WITH-FIXES** — apply the lift-time scoped-id refuse (or equivalent) before treating the cross-work story as fully closed.

VERDICT: R11 | LAND-WITH-FIXES | P0=0 P1=0 P2=1 P3=2 | ran-tests
