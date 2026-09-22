# R8 — alias double count + ledger relabel

**Scope:** `e2897a867` on `origin/review/janaf-batch-2026-09-22`. Files: `scripts/bench_readiness.py`, `simulator/battery/migrate.py`, `data/literature/works/ALIASES.yaml`, tests. Derived works/observations store **not** regenerated in-commit. Read-only review; optional `ferry/reviews/R8-fix.patch` not applied. Do not push.

**Intent (claimed):**
1. Readiness attributed every experiment of an aliased work to **each** of its `Work.source_ids`, so synonym aliases (e.g. `janaf-4th` / `nist-janaf-4th`) inflated `source_count` (claimed 257→233 across 24 extra alias rows; on this tip: 16 multi-`source_ids` works, **24** extra `source_ids`). Fix: attribute each experiment to `source_ids[:1]` (first-registered) while still expanding **all** aliases for apparatus refs / acquisition leads.
2. Species-rail differential ledger points carried `source_id: janaf` (compilation compared against), which mislabelled the ledger work as NIST-JANAF. Migrate remaps that token to `species-rail-differential`, pinned in `ALIASES.yaml` to `citation_hash("janaf")` so experiment ids stay on `fc98d62f…`.

**Attack surface (brief):** Is first-registered deterministic and the right canonical id? Can a real distinct source be merged away? Do experiment ids stay stable?

---

## Findings

### P1 — Ledger remap only special-cases `source_id == "janaf"`; pankratz-token rows still merge into real USBM B689

**Evidence:**
- `migrate.py:8037-8043` (`e2897a867`) remaps only when `source_id == "janaf"` **and** `path.name == "species_rail_differential_ledger.yaml"`.
- Live ledger point tokens: `janaf` × 22801, `pankratz-1987-usbm-b689` × 876 (`rg` on `species_rail_differential_ledger.yaml`).
- Pankratz-token rows keep `source_id` unchanged → `_work_from_citation` joins work `4c12a2f5…` (`ALIASES` / works file `source_ids: [pankratz-1987-usbm-b689]`).
- That same work also loads `observations-v2/compilations-pankratz-1987-usbm-b689.yaml`. Work YAML experiments are ledger-shaped (`…::_channel`, `…::page-0003`, …) co-resident with the real compilation family.
- Real NIST-JANAF tables use `source_id: nist-janaf-4th` (separate work `ebbdedff…`); the janaf-token ledger bug was a **false label on a separate work**, not a merge into `janaf-4th`. The pankratz-token path is the actual “distinct source merged away” hit for this ledger.

**Why P1:** Commit doctrine says the ledger’s comparison token is not a battery work, then only patches the janaf spelling. USBM B689 readiness/scoring identity still absorbs species-rail residuals.

**Fix direction:** Remap **every** point from `species_rail_differential_ledger.yaml` to `species-rail-differential` (comparison compilation remains in observation keys / point fields). Accept experiment-id move for former pankratz-token ledger rows off `4c12…` onto the pinned `fc98…` ledger work. See `ferry/reviews/R8-fix.patch`.

---

### P2 — Aliases + migrate code relabel; committed works/observations still say `janaf`

**Evidence:**
- `ALIASES.yaml`: `janaf` removed; `species-rail-differential → fc98d62f…` added.
- Committed `works/fc98d62f….yaml` still has `citation: janaf`, `source_ids: [janaf]`.
- `observations-v2/species_rail_differential_ledger.yaml`: still `source_id: janaf` (22801 rows).
- Readiness loads the works store (`work_from_plain`), so until remigrate the readiness row label remains `janaf`, contradicting the alias registry and commit claim that migrate “now files it as species-rail-differential.”
- Unit tests cover dry-run migrate + alias pin only; nothing asserts the committed work label.

**Why P2:** Half-landed identity change. Consumers of aliases vs store disagree; validate still passes because observations and work both still say `janaf`.

**Fix direction:** Remigrate (write) in the same change set, or gate with a committed-store assertion (included in `R8-fix.patch` as `test_committed_species_rail_work_label_matches_alias_pin`).

---

### P3 — “Canonical” is migrate discovery order; stable for a fixed corpus, not an owned field

**Evidence:**
- `migrate.py:6017-6018`: `_work_source_ids[work_id].append(source_id)` on first see; order = extract/named/compilation discovery order (`discover_extracts` / `sorted(rglob)` — deterministic for a given tree).
- Readiness `bench_readiness.py:305`: `row_source_ids = source_ids[:1]` with comment calling it “canonical.”
- No `canonical_source_id` field; REVIEWED_ALIASES comment (“canonical id is the kems-007 citation hash”) refers to **work_id**, while Costa’s persisted `source_ids[0]` is `costa-jacobson-2015`.
- Adding/renaming an extract that sorts earlier can flip `source_ids[0]` on remigrate → readiness **label** churn. Counts stay one-per-work; experiment ids (keyed by `work_id`) unchanged.

**Why P3:** First-registered is the right *count* key and is deterministic today; calling it canonical overclaims. Not a merge bug.

**Fix direction:** Soften comment; optionally document that readiness `source_id` is “first-registered,” not INDEX primary. Lexicographic pick is an alternative if label stability across remigrates matters more than discovery order.

---

### P3 — Pin keeps experiment ids for janaf-token rows; bare `janaf` citation still hashes to the ledger work

**Evidence:**
- `citation_hash("janaf") == fc98d62f… == aliases["species-rail-differential"]`; `citation_hash("species-rail-differential")` is a **different** digest (`7c966c2a…`).
- Experiment ids are `{work_id}::{locator}` (`migrate.py:6063-6082`). Pin preserves ids for historical janaf-token ledger experiments — attack “do experiment ids stay stable?” → **yes** for that set.
- After removing the `janaf` alias, any future non-ledger row with citation/source fallback `"janaf"` still resolves `work_id_for` → `citation_hash("janaf")` → same ledger work (path remap does not apply). Live compilations already use `nist-janaf-4th` / `janaf-4th`, so no current merge; residual landmine only.

**Why P3:** Intentional stability trade; document that bare `janaf` is permanently reserved by the pin.

---

## Attack answers

| Question | Answer |
| --- | --- |
| First-registered deterministic / right canonical? | Deterministic for a fixed migrate discovery order; correct as the **single readiness attribution key**; not an explicit canonical registry field (P3). |
| Real distinct source merged away? | Readiness fan-in does **not** merge works. Ledger janaf relabel does **not** pull into `janaf-4th`. **Yes** for pankratz-token ledger rows still co-owned with real USBM B689 (P1). |
| Experiment ids stable? | Yes for janaf-token ledger rows via alias pin to `citation_hash("janaf")`. Pankratz-token ledger ids stay on `4c12…` until a full-file remap (then they should move). |

## What is sound

- Readiness `source_ids[:1]` stops alias double-count; apparatus lookups still walk full `source_ids` (`bench_readiness.py:309-319` vs `325/354`).
- Tests: `test_aliased_work_counts_each_experiment_under_one_canonical_source`, `test_species_rail_janaf_token_migrates_under_ledger_label`, `test_committed_aliases_pin_species_rail_ledger_not_janaf` — **3 passed** (`pytest -o addopts=` against `e2897a867`).
- Counting claim matches tip works store: 16 multi-alias works, 24 extra `source_ids` (257→233).

## Optional patch

`ferry/reviews/R8-fix.patch` — not applied:
1. Path-based remap for all species-rail ledger points (not only `janaf`).
2. Regression test for pankratz-token ledger rows.
3. Committed-store assertion that `fc98…` carries `species-rail-differential` (forces remigrate).

---

VERDICT: R8 | LAND-WITH-FIXES | P0=0 P1=1 P2=1 P3=2 | ran-tests
