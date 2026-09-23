# R14 — migration-queue.yaml group by (work, source, why, axes) (t952)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `7861f71888059efcd121204646c5bdd64521e730` on `origin/review/t952-queue-group` (NOT landed on green; parent `2e9e17c3d`)  
**Files:** `simulator/battery/migrate.py`, `tests/battery/test_migrate.py`, `data/battery/migration-queue.yaml`

**Intent:** Replace flat `battery_migration_queue.v1` (one YAML entry per queued observation) with grouped `battery_migration_queue.v2`: one group per `(work_id, source, why, axes)`, listing every `observation_id` + `locator` under `observations`. Claim: **lossless** (every old flat entry recoverable as a multiset) and **deterministic** (same entries → same bytes regardless of input order); every on-disk reader updated for the new shape.

**Attack surface:** lossy regroup (dropped / merged / deduped rows; dropped fields); non-deterministic group or observation order; a reader still expecting flat top-level `observation_id` / `locator`.

**Method:** Detached checkout of tip in `/workspace/repos/wt/slot-04`. Static read of `group_queue_entries` / `expand_queue_entries` / `migration_queue_document` / `write_outputs`. Repo-wide reader sweep (`migration-queue.yaml`, `expand_queue_entries`, `battery_migration_queue`). Parent (`2e9e17c3d` v1 flat) vs tip multiset fingerprint compare. Adversarial probes (axes-order keying, dual-key expand, empty observations, alias reorder, extra fields, long-source YAML width). Pytest on tip. Mode: **ran-tests**.

---

## Findings

### P3 — `expand_queue_entries` treats dual-key rows as flat and drops `observations`

**Evidence (file:line on `7861f7188`):**

- `migrate.py:8967` — grouped path is taken only when `"observations" in entry and "observation_id" not in entry`.
- Probe: entry with both `observation_id: keep-me` and `observations: [{drop-me}, {drop-me-2}]` expands to a single flat row `keep-me`; the nested observations are discarded.
- Writer never emits dual-key rows (`migration_queue_document` / `group_queue_entries` put `observation_id` only under `observations`; tip YAML groups have no top-level `observation_id`). `test_committed_migration_queue_is_canonical_grouped_form` rebuilds from expand and asserts identity with committed groups.

**Why P3 (latent):** Fail-open only for hand-edited / buggy documents that mix shapes. Live committed file and the migrate writer stay lossless. Defense-in-depth: prefer `observations` when present, or refuse dual-key input.

---

### P3 — `dedupe_aliases` byte order is caller order (not sorted)

**Evidence:**

- `migrate.py:8998–9003` — docstring: “Alias order is the caller's order”; `dedupe_aliases` copied as given.
- Probe: `migration_queue_document(entries, aliases)` vs `..., list(reversed(aliases))` → **different bytes**.
- Entry/group determinism is covered: `test_queue_group_roundtrip_is_lossless` reverses **entries** and asserts identical dump bytes; tip vs parent aliases are equal (`4` aliases, identical lists).
- Aliases are appended in migrate encounter order (`migrate.py:6269–6270`).

**Why P3:** Named determinism claim holds for the grouped **entries** (sorted by `_group_sort_key` / `_observation_sort_key`). Alias list is a second channel; reordering aliases alone is enough to change YAML bytes. Latent unless a caller shuffles aliases. Fix direction: sort aliases by `(observation_id, source, row_indices)` inside `migration_queue_document`.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Lossy regroup | **Fail (safe).** Parent v1 flat `130257` entries ↔ tip expand `130257`; fingerprint multisets equal (unique `130241`, duplicates preserved); `dedupe_aliases` lists equal (`4`). Synthetic: duplicate identical flat rows stay duplicated (`group_queue_entries` docstring + test); roundtrip fingerprints and reason/axis counts match. Locator values preserved; keys reordered via `_canonicalize_locator` / `_QUEUE_LOCATOR_KEY_ORDER` (`:8825–8863`) matching `Locator` fields. Unknown top-level fields would be dropped by `_flat_queue_entry` (P3-class; not present on parent/tip). |
| Non-deterministic order | **Fail (safe) on entries.** Groups sorted by `(work_id, source, why, axes)` tokens; observations by `(observation_id, locator JSON)`; locator keys canonicalized; `dump_yaml(..., sort_keys=False)` relies on fixed insertion order of built dicts. Reverse-input dump bytes identical (test). Axes list order is identity (same multiset of axes in different order → two groups — not lossy). Alias order caveat = P3 above. |
| Reader still expecting flat schema | **Fail (safe).** On-disk consumers: (1) `tests/battery/test_migrate.py` `test_l05c5_store_unavailable_values_are_queued` — updated to `expand_queue_entries` (`:3468`); (2) `scripts/check_store_freshness.py:111–117` — raw-text `extracts/{name}` substring; `source` remains on each group (no schema parse); long path stays contiguous under `width=120` (test + probe, 142-char stem); (3) `simulator/battery/score.py:145` — path listed in `STORE_REVISION_PATHS` only (no entry parse). `QUEUE_PATH` constant unused as a loader. In-memory `result.queue` stays flat `QueueEntry` through migrate/report (`queue size: 130257` matches flat, not YAML group count `55274`). No `battery_migration_queue.v1` checker remains. |

---

## What looks sound

- Schema bump `battery_migration_queue.v1` → `.v2` with explicit `QUEUE_SCHEMA_VERSION` and writer path through `migration_queue_document` (`:8994–9004`, `:9157–9171`).
- Expand accepts legacy flat rows (`observation_id` on the entry) so older fixtures / mixed trees keep working (`:8988–8989`, legacy branch in test).
- Compression is real: `130257` flat → `55274` groups on tip without dropping rows.
- Report / per-source queued counts still iterate in-memory flat `result.queue` — consistent with “queue size” semantics (observation-reasons, not YAML group cardinality).
- Freshness tripwire compatibility consciously retained (test comment on contiguous `extracts/...` substring).

### Residual notes (not severity)

- Axes order is part of the group key; callers that pass the same axis set in different orders get two groups (still recoverable; slightly less compression).
- Empty `observations: []` expands to no flat rows (writer never emits empty groups).
- `migration-report.md` was not rewritten in this commit; its `queue size: 130257` still matches the flat multiset (parent store regen), not the new YAML group count.

---

## Tests

Tip worktree at `7861f7188`, shared repo `.venv`, `-o addopts=`:

- `tests/battery/test_migrate.py::test_queue_group_roundtrip_is_lossless`
- `tests/battery/test_migrate.py::test_committed_migration_queue_is_canonical_grouped_form`
- `tests/battery/test_migrate.py::test_l05c5_store_unavailable_values_are_queued`

**3 passed** in ~180s (dominated by loading the 63M queue YAML).

Adversarial parent↔tip fingerprint compare and dual-key / alias-order / long-source probes: as in checklist.

No fix patch. Do not push.

---

## Verdict rationale

Named attacks (lossy regroup, non-deterministic entry order, stale flat readers) do not land on live data or the migrate writer. Parent v1 → tip v2 is a verified lossless multiset rewrite; the structured YAML reader was updated; substring and path-only consumers remain valid. Two latent defense-in-depth nits on expand’s dual-key heuristic and unsorted aliases do not break the claim.

VERDICT: R14 | LAND | P0=0 P1=0 P2=0 P3=2 | ran-tests
