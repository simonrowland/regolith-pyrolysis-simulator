# R21 — W1 t-955 fix: declare `pure_substance_reference` on the row

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `9cfa5de8311db0bf23648d9ba54fb60eaf6126fe` on `origin/review/w1-fix` (detached; parent `db51af4e4`)  
**Files:** `simulator/battery/waypoints.py`, `scripts/bench_readiness.py`, `simulator/battery/generators/janaf.py`, `simulator/reference_data/janaf.py`, `simulator/battery/records.py`, `simulator/battery/migrate.py`, `tests/battery/test_t955_janaf_engine_point_na.py` (ferry `W1-t955.md` deleted)

**Intent (claimed):** JANAF generator rows **declare** `derivation.pure_substance_reference`; readiness selects on that field (no printed-cell text-clause fingerprint; no source-id branch); **1655** NIST-JANAF tabulation experiments become `engine_point` `not_applicable`; USGS peer Gibbs tables are **not** flipped.

**Attack surface:** any non-JANAF verdict change; alias-borrow via shared `locator.table`; store override untested / dead.

**Method:** Diff tip vs parent `db51af4e4` and vs green. Static read of predicate, report override, generator stamp, migrate round-trip. Live YAML scan of `observations-v2/compilations-janaf/` (1655 experiment ids) and USGS shards for the declaration field. Generator smoke on `Al-001`. Pytest `tests/battery/test_t955_janaf_engine_point_na.py`. Mode: **ran-tests**.

---

## Findings

### P0 — Live store never declares the field; claimed 1655 `not_applicable` is 0 today (parent regression)

**Evidence (file:line on `9cfa5de83`):**

- Predicate gate: `waypoints.py:1311` — `derivation.pure_substance_reference is not True` → False. Printed-cell conjunct removed (`_PRINTED_CELL_ACCOUNTING` gone).
- Generator only stamps on **new** generation: `reference_data/janaf.py:203` `COMPILATION_ROLE["pure_substance_reference"]=True`; `generators/janaf.py:1386–1388` copies it onto `Derivation`.
- Migrate reads the field if present (`migrate.py` `_derivation_from_plain`); tip commit does **not** regenerate `data/literature/observations-v2/compilations-janaf/`.
- Live shard probe: `rg` finds **zero** `pure_substance_reference` under `observations-v2/`. Full YAML pass: **1655** JANAF experiment ids; **parent-style** `printed-cell + role + composition not_applicable` matches **1655**; **tip-style** `declared + role + composition na` matches **0**.
- Generator vs live: `generate_table(Al-001)` → all obs `pure_substance_reference is True` and predicate True; same table’s live YAML → `_derivation_from_plain(...).pure_substance_reference is None` while relation still carries `printed cells blank or INFINITE`.
- Report override (`bench_readiness.py:357–371`) only fires when some linked obs passes the predicate → never on today’s store.

**Constructed trigger (live):** At this SHA, for any JANAF tabulation experiment (e.g. `nist-janaf-4th:Al-001:tabulation`), load store observations for that `experiment_id` and call `is_pure_substance_engine_reference` → all False. `scripts/bench_readiness.report(root)` therefore leaves those rows as ordinary `engine_point` **GAP**, not `NOT_APPLICABLE` / `PURE_SUBSTANCE_REFERENCE`. Parent `db51af4e4` would have typed all **1655** N/A via the printed-cell conjunct on the same YAML.

**Why P0 (live):** Claimed numeric outcome (“1655 NIST tabulations engine_point not_applicable”) does not hold on the reviewed SHA. Readiness ledger counts remain wrong **today**. Tip is a live no-op relative to green and a **regression** relative to parent for JANAF experiment verdicts.

**Fix direction:** Regenerate JANAF observation shards with the generator that stamps `pure_substance_reference: true` (and land that data with this code), **or** do not remove the printed-cell conjunct until the store carries the field. Add a CI assertion that at least one live `compilations-janaf/*.yaml` derivation serializes `pure_substance_reference: true` so an incomplete landing fails tests.

---

### P2 — Suite never loads a live JANAF shard; incomplete landing is invisible to pytest

**Evidence:**

- `tests/battery/test_t955_janaf_engine_point_na.py` builds synthetic observations with `declared=True` / `False`, and `test_generator_declares_marker_on_series_and_transition_rows` only checks **in-memory** `generate_table` output — not `observations-v2`.
- Tip suite: **6 passed**. No test fails when every on-disk JANAF derivation lacks the field.

**Why P2 (latent):** Contract tests are green while the claimed live disposition is false. Weaker than P0 (does not invent a wrong number by itself) but the missing guard is why the P0 shipped.

**Fix direction:** Pin one real shard observation (or a regenerate dry-run checksum) asserting `pure_substance_reference is True` after migrate load.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Any non-JANAF verdict change | **Fail (safe) for USGS.** Live USGS B1259/B1452/B1544 rows carry `engine_reference_input=true, scoring_eligible=false` and composition N/A but **no** `pure_substance_reference` and **no** printed-cell clause → predicate False; tip does not flip them. Synthetic USGS without declare stays GAP; with declare becomes N/A (intentional, no source-id branch). Tip does not change KEMS/melt consumers (override only rewrites `engine_point`). |
| Alias-borrow | **Fail (safe).** Parent `by_source_table[(source_id, table)]` secondary index removed (`bench_readiness.py:355–357` own `experiment_id` only). `test_report_override_types_only_rows_that_declare_the_marker`: legacy `work-janaf::Al-001` shares table `Al-001` with a marked sibling and stays GAP. |
| Override untested | **Fail (safe) on synthetic path.** Same test exercises `report()` override for declared / legacy / unmarked / USGS±declare, and asserts non-`engine_point` consumers are untouched. **Live override is dead** until store regen (covered by P0), not by missing unit coverage. |

---

## What looks sound

- Declaration model is the right selector: field on the row, not source-id substring, not prose fingerprint of “printed cells blank or infinite” (transition relations lack that clause; generator still stamps them — `test_generator_declares_marker…`).
- Alias-once attribution via `source_ids[:1]` unchanged; alias-borrow hole from parent closed.
- `Derivation.pure_substance_reference: bool | None = None` with “absent means undeclared”; migrate ignores non-bool payloads; `to_plain` omits None.
- Override scoped to `consumer == "engine_point"` only.
- Mutation test still proves the predicate is what drives N/A.

### Residual notes (not severity)

- `migrate` will accept YAML `pure_substance_reference: false` (`isinstance(..., bool)`); comment says never stamp false. Harmless (`is not True` fails the gate).
- Three `test_janaf_generator` failures here are missing corpus paths under `/Users/simonrowland/Repos/regolith-corpus/…` — env, not this tip.

---

## Tests

Tip worktree at `9cfa5de83`, repo `.venv`, `-o addopts=`:

- `tests/battery/test_t955_janaf_engine_point_na.py`: **6 passed**
- Live YAML accounting: JANAF experiments **1655**; tip declared hits **0**; parent printed-cell hits **1655**; `pure_substance_reference` occurrences in `observations-v2/`: **0**
- Generator smoke `Al-001`: stamp + predicate True in memory; live YAML for same table: field absent

---

## Verdict rationale

Selector design, USGS isolation, and alias-borrow fix are solid **once rows declare the field**. On this SHA the store does not declare it, so the claimed 1655 N/A disposition is false and readiness regresses vs parent. Do not land until JANAF observations are regenerated (or an interim dual-conjunct keeps live dispositions correct).

VERDICT: R21 | DO-NOT-LAND | P0=1 P1=0 P2=1 P3=0 | ran-tests
