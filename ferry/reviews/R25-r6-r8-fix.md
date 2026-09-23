# R25 — R6/R8 fix: NIST-JANAF txt hash guard + species-rail path remap

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** tip `1f8df6cbe4cfbca526192b92e8adb2c1e1780dfc` on `origin/review/r6-r8-fix` (detached); stack `0c2e59997944f070038fd3fafe206b4a8dd2f81a` + `1f8df6cbe` (ferry tips verified as ancestors of HEAD)  
**Files:** `data/literature/build_index.py`, `tests/test_literature_index.py`, `simulator/battery/migrate.py`, `tests/battery/test_migrate.py`  
(Prior context: R6 `a782e161c`, R8 `e2897a867`; optional `R6-fix.patch` / `R8-fix.patch`.)

**Intent (claimed):** NIST-JANAF txt hashes verified before advertising; JANAF index test skips without the corpus; species-rail ledger points remapped by **file path** off the real USBM B689 work (**186 → 1** experiments).

**Attack surface:** content-drift still advertising `raw/janaf-nist-txt`; corpus-absent CI red; pankratz-token ledger residuals still co-owned with USBM B689 on the committed store; claim 186→1 without remigrate.

**Method:** Diff tip vs `07ad01dae` (parent of stack). Static read of hash loop, skipif, path remap. Live store accounting on `works/4c12…`, `works/fc98…`, `observations-v2/species_rail_differential_ledger.yaml`, literature ledger tokens, pankratz compilation shard. Header-regex probe on all 1655 JANAF tables. Pytest focused modules. Mode: **ran-tests**.

---

## Findings

### P1 — Claimed B689 186→1 is not live; path remap is code-only (store still co-hosts ledger residuals)

**Evidence (file:line on `1f8df6cbe`):**

- Remap is path-based as claimed: `migrate.py:8080–8088` — any point from `species_rail_differential_ledger.yaml` gets `source_id = "species-rail-differential"` (janaf special-case removed). Discovery still feeds only `lit / "species_rail_differential_ledger.yaml"` (`:7474`).
- Tip commit does **not** regenerate `data/literature/works/` or `observations-v2/`.
- Live `works/4c12a2f5…yaml` (USBM B689): **186** experiments — 185 ledger `observation_id`s (`_channel` + `page-*`) plus the real compilation locator `pankratz-1987-usbm-b689`. Unchanged vs `e2897a867` / `2e9e17c3d` / parent `07ad01dae`.
- Literature ledger tokens still `janaf` × 22801 + `pankratz-1987-usbm-b689` × 876 (comparison tokens — expected on the **input** file).
- Committed `observations-v2/species_rail_differential_ledger.yaml`: `species-rail-differential` × 22801 (janaf-token rows remapped at `2e9e17c3d`) but still **`pankratz-1987-usbm-b689` × 872**.
- Real compilation shard `observations-v2/compilations-pankratz-1987-usbm-b689.yaml` contributes exactly **one** experiment id `4c12…::pankratz-1987-usbm-b689` (421 obs) — so remigrate under the tip remap **would** yield 186→1; the tip store does not.
- `R8-fix.patch` item 3 (`test_committed_species_rail_work_label_matches_alias_pin` / B689 pin) is **not** in the tip; only the pankratz dry-run unit test landed (`test_migrate.py:4797–4838`).

**Constructed trigger (live):** Load `work_from_plain` for `4c12a2f5…` at this SHA → `len(experiments) == 186` with `page-0003`… co-resident with the real bulletin. Readiness / scoring identity for USBM B689 still absorbs species-rail residuals today.

**Why P1 (live):** Ferry claim states the file-path remapping **outcome** (186→1). Code path is correct; committed data still carries the R8 P1 merge. Wrong work experiment set reaches readiness **today**.

**Fix direction:** Remigrate (write) under this remap and land works + `observations-v2` ledger; assert committed `4c12…` has exactly one experiment (`…::pankratz-1987-usbm-b689`) and zero `page-*` / `_channel` locs; assert obs-v2 ledger has no `pankratz-1987-usbm-b689` `source_id`.

---

### P2 — Suite stays green while B689 store remains polluted

**Evidence:**

- New test builds a tmp ledger with only a pankratz-token point and asserts dry-run migrate (`write=False`) — does not read committed `works/4c12…` or obs-v2.
- Tip focused suite: **4 passed, 1 skipped** — no failure mode for “186 still on B689”.

**Why P2 (latent):** Same class as R8’s half-land gap: unit coverage proves the code branch; incomplete landing is invisible to pytest. Weaker than P1 (does not invent the wrong count by itself).

**Fix direction:** Committed-store assertion as in `R8-fix.patch` (extend to B689 experiment count / locs).

---

### P3 — Hash-refusal tmp test omits missing `source_sha256`

**Evidence:**

- Builder refuses missing recorded sha (`build_index.py:484–486`) and content mismatch (`:487–489`).
- `test_nist_janaf_txt_tables_refuses_stem_mismatch_and_content_drift` covers foreign stem + byte drift only — not a table YAML lacking `source_sha256`.

**Why P3:** Defense-in-depth; live tables all carry sha in the first 4k (1655/1655 probe). Not a land blocker.

**Fix direction:** One tmp_path row with stems matched and sha field absent → `exists is False`.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Stem match + content drift still advertises txt cache | **Fail (safe).** Drifted `A-001.txt` → `path == ""`, `exists is False` (unit test PASSED). Hash loop compares every table’s recorded `extraction.source_sha256` to `sha256_file(stem.txt)` (`build_index.py:483–489`). |
| Corpus absent → INDEX integration red | **Fail (safe).** `@pytest.mark.skipif(_nist_janaf_txt_corpus_absent())` → SKIPPED on this box (`test_literature_index.py:737–740`). |
| Regex miss on JSON-in-YAML headers | **Fail (safe) on live corpus.** All 1655 `compilations/janaf/tables/*.yaml` match `_janaf_table_source_sha256` within first 4096 bytes. |
| Pankratz-token ledger still merges into B689 (code path) | **Fail (safe) on dry-run.** Path remap + `test_species_rail_pankratz_token_also_files_under_ledger_label` PASSED. |
| Pankratz-token ledger still merges into B689 (**live store**) | **Hit — P1.** Works 186; obs-v2 872 pankratz `source_id`s. |
| Claim 186→1 arithmetic wrong | **Fail (safe) as prediction.** 185 unique pankratz ledger obs + 1 real compilation experiment = 186; remigrate would leave 1. Outcome not landed. |
| INDEX still advertises path without re-build under hash guard | **Fail (safe) for builder.** Committed INDEX from `a782e161c` still lists `raw/janaf-nist-txt`; future `build_index` enforces hashes. No tip regression of INDEX bytes. |

---

## What looks sound

- R6 P1 closed in **builder**: stem equality alone no longer advertises; docstring names the private-mirror / hash-bridge rationale (`build_index.py:463–474`).
- R6 P2 closed in tests: tmp refusal + skipif on external corpus (matches / exceeds `R6-fix.patch`; skipif is tip-extra).
- R8 P1 closed in **migrate code**: file-name gate, not `source_id == "janaf"`.
- Alias pin `species-rail-differential → citation_hash("janaf")` unchanged; janaf-token experiment ids stay on `fc98…`.
- `fc98…` work already cites `species-rail-differential` (regen `2e9e17c3d`); residual hole is pankratz-only.

### Residual notes (not severity)

- Literature ledger YAML keeping comparison tokens (`janaf` / `pankratz-…`) is correct; remap belongs at migrate, not in the differential source file.
- Tip does not regenerate INDEX under the new guard; honesty of the committed `exists: true` row still depends on an author-machine corpus that matched hashes at `a782e161c`.

---

## Tests

Tip worktree at `1f8df6cbe`, repo `.venv`, `-o addopts=`:

- `test_nist_janaf_txt_tables_refuses_stem_mismatch_and_content_drift` — **PASSED**
- `test_nist_janaf_index_asset_is_the_corpus_txt_download` — **SKIPPED** (no `REGOLITH_CORPUS_ROOT` / default corpus dir)
- `test_species_rail_pankratz_token_also_files_under_ledger_label` — **PASSED**
- `test_species_rail_janaf_token_migrates_under_ledger_label` — **PASSED**
- `test_committed_aliases_pin_species_rail_ledger_not_janaf` — **PASSED**

Live accounting: B689 works experiments **186**; obs-v2 ledger pankratz `source_id` **872**; JANAF tables with `source_sha256` in 4k header **1655/1655**.

---

## Verdict rationale

Hash-before-advertise and corpus skip are solid and landable. Path-based ledger remap is the right code fix and unit-tested, and the 186→1 arithmetic is correct **after remigrate**. On this SHA the committed store still shows 186 on USBM B689 with pankratz-token ledger residuals in obs-v2, so the ferry outcome claim is false until a store regen lands. Land the code with a remigrate (or do not advertise 186→1 until then).

VERDICT: R25 | LAND-WITH-FIXES | P0=0 P1=1 P2=1 P3=1 | ran-tests
