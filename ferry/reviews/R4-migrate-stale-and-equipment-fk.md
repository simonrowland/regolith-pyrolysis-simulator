# R4 — migrate: stale sibling + d-036 equipment FK

**Scope:** `805b8db26` (zero-observation extracts-v2 empty rewrite) + `ea9cdfb63` (Experiment.equipment_context_id FK). Tip reference: green history through `fbe3491b2`. Read-only review; optional `ferry/reviews/R4-fix.patch` not applied.

**Intent (claimed):**
1. When an extract yields zero observations, its prior extracts-v2 sibling is rewritten empty so d-032 context rows cannot linger as scored observations.
2. Equipment evidence on a context row that names a declared experiment sets `Experiment.equipment_context_id` to `f"{source_id}::context::{observation_id}"` by reference (never copy). Undeclared targets and second equipment rows are typed `referential_integrity` issues.

**Attack surface (brief):** stale file that still survives (source removed / rename); FK that resolves to the wrong row or silently to none; context_id collision.

---

## Findings

### P0 — Cross-work equipment FK via `::` passthrough (silent wrong experiment)

**Evidence:**
- `simulator/battery/migrate.py:6316-6318` — `_registry_id` returns any token containing `::` unchanged (no work-scope check).
- `simulator/battery/migrate.py:6502-6534` — `_link_equipment_context` resolves `experiment_id` through `experiment_refs.get(..., _registry_id(...))` then looks up **`self.result.experiments` globally**. If a context row's `experiment:` is a fully-qualified id owned by another work already lifted in the same migrate run, the FK is written onto that foreign experiment with **no registry issue**.
- `simulator/battery/validate.py:730-741` — dangling check only; no `work_id` match between experiment and context row.
- SCHEMA (`data/literature/extracts/SCHEMA.md:250-254`) says consumers resolve against **the work record's** `context:` list — cross-work attachment violates that.

**Repro (tmp tree, alphabetical order forces victim then attacker):** extract `zzz-attacker` context row with `experiment: <victim_work>::experiment::vic-series` and `equipment: {cell_material: poison}` sets `victim.equipment_context_id == "zzz-attacker::context::evil_eq"`. `validate_corpus(..., context_rows=load_migrated_context(root))` reports **zero** equipment hard issues. `resolve_equipment_context` returns poison equipment. Victim `work_id` ≠ context row `work_id`.

**Why P0:** Silent wrong-experiment FK assignment; doctrine is REFERENCE with typed refusal for bad targets — foreign live targets skip the refusal.

**Fix direction:** After resolving `experiment`, refuse unless `experiment.work_id == work.work_id` (typed `referential_integrity`, no FK written). Validate the same work-id invariant when `context_rows` is supplied. See `ferry/reviews/R4-fix.patch`.

---

### P1 — extracts-v2 orphans survive delete / rename and re-enter the observation store

**Evidence:**
- `simulator/battery/migrate.py:8916-8926` — `observations-v2` is wipe-all then rewrite.
- `simulator/battery/migrate.py:8856-8860` — works files not in the live set are unlinked.
- `simulator/battery/migrate.py:8937-8958` — extracts-v2 “stale” handling only empty-rewrites when the **source extract still exists** and produced zero observations. Stems absent from `discover_extracts` are never touched.
- `simulator/battery/migrate.py:1483-1495` — `load_migrated_store` loads **every** `extracts-v2/*.yaml` via `iter_observation_store_paths`.
- `simulator/battery/score.py:1522-1531` — score context also walks all extracts-v2 files for origins.

**Repro:**
- Delete extract after a successful migrate → remigrate with another extract: `extracts-v2/<gone>.yaml` still on disk; `load_migrated_store` still returns `gone-source::…` observations.
- Rename extract stem / `source_id` → old sibling remains alongside the new one; both observation id families load.

**Why P1:** `805b8db26` fixed the zero-obs / d-032 leak for **still-present** extracts, but the stated attack (removed source, rename) still pollutes the derived observation store and scoring. Asymmetry with works / observations-v2 cleanup.

**Fix direction:** Before/after the empty-rewrite loop, `unlink` any `extracts-v2/*.yaml` whose stem ∉ live `extract_stems` (mirror works cleanup). Patch included.

---

### P1 — Duplicate `context_id` → FK points at id that resolves to the wrong row

**Evidence:**
- `simulator/battery/migrate.py:6476-6481` — `context_id = f"{source_id}::context::{raw_id}"` with **no uniqueness** check across species / rows; duplicates are appended.
- `simulator/battery/migrate.py:1520-1536` — `load_migrated_context` keys by `context_id` and **last-wins** silently.
- `simulator/battery/migrate.py:8789-8793` — finalize validation map also last-wins on duplicate keys.
- Link path first-wins on `equipment_context_id` (`6519-6534`), so the FK is set from the first equipment-bearing row, then resolve returns a later non-equipment row with the same id.

**Repro:** Two species share `observation_id: same_id`; Na row has `equipment` + `experiment`; K row is characterization without equipment. Migrator sets FK to `dup-ctx::context::same_id`. Work YAML carries **two** rows with that id. `resolve_equipment_context` returns `type=characterization` with **empty** equipment keys — silent wrong row.

**Why P1:** Exact “id collision / FK resolves to the wrong row” attack. No typed refusal; consumers see a resolvable id with the wrong payload.

**Fix direction:** Refuse duplicate `context_id` under a work at lift time (hard error or registry issue). Optionally assert uniqueness in validate. Patch raises `ValueError` on duplicate.

---

### P3 — Equipment FK validation is opt-in via `context_rows is not None`

**Evidence:** `simulator/battery/validate.py:730-733` — same shape as `bench_id` / `benches`. `Migrator.finalize` (`8789-8793`) does pass context rows. Callers that omit `context_rows` never see dangling equipment FKs.

**Why P3:** Consistent with bench pattern; migrate path is covered. Document or make the check unconditional when the field is set if corpus validation is expected to be complete without the optional arg.

---

### P3 — Empty `equipment: {}` counts as equipment evidence

**Evidence:** `simulator/battery/migrate.py:6497-6499` — `isinstance(record.get("equipment"), Mapping)` is true for `{}`; `values.apparatus is not None` is true for `""` / `{}`.

**Why P3:** Can attach an FK to a vacuous row. Narrow; prefer requiring a non-empty mapping or known apparatus keys.

---

## What works (positive)

- Zero-observation path for **still-present** extracts: empty rewrite + `tests/battery/test_context_observation_boundary.py` prevents context bases in extracts-v2 (`805b8db26`).
- Declared-experiment link, second-equipment conflict, undeclared **local** target refusal, dangling resolve/validate (`tests/battery/test_migrate.py` d-036 quartet) — all pass.
- Doctrine respected on the happy path: equipment values not copied onto Experiment; context_id shape matches `source_id::context::raw_id`.
- `resolve_equipment_context` raises `UnresolvedEquipmentContextError` on dangling refs (absence → `None`).

## Tests run

```
PYTHONPATH=. python -m pytest -o addopts= \
  tests/battery/test_context_observation_boundary.py \
  tests/battery/test_migrate.py::test_d036_equipment_fk_resolves_through_context_row \
  tests/battery/test_migrate.py::test_d036_migrator_links_equipment_context_rows \
  tests/battery/test_migrate.py::test_d036_equipment_context_row_naming_undeclared_experiment_refuses \
  tests/battery/test_migrate.py::test_d036_dangling_equipment_context_fk_refuses_typed
# 5 passed
```

Adversarial tmp repros (not landed as tests): delete orphan, rename orphan, duplicate context_id wrong resolve, cross-work FK — all demonstrated against tip code. Optional `R4-fix.patch` closes A/C/D under monkeypatch verification (not applied to the repo).

## Optional patch

`ferry/reviews/R4-fix.patch` — not applied:
1. Unlink extracts-v2 stems not in live `extract_stems`.
2. Refuse equipment link when `experiment.work_id != work.work_id`.
3. Refuse duplicate `context_id` under a work at lift.
4. Validate equipment FK work_id match when context row carries `work_id`.

---

VERDICT: R4 | DO-NOT-LAND | P0=1 P1=2 P2=0 P3=2 | ran-tests
