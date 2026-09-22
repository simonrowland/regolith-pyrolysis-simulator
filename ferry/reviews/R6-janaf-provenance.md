# R6 — JANAF provenance (INDEX asset for NIST .txt cache)

**Scope:** `a782e161c` on `origin/review/janaf-batch-2026-09-22` (not landed on green). Derived store **not** regenerated — source changes only (`data/literature/build_index.py`, committed `INDEX.yaml`/`INDEX.md`, `tests/test_literature_index.py`). Read-only review; optional `ferry/reviews/R6-fix.patch` not applied. Do not push.

**Intent (claimed):** Register the NIST-JANAF `.txt` download cache (`raw/janaf-nist-txt`) as the compilation's INDEX `tables` asset so 13,681 `nist-janaf-4th` observations' `read_from` resolves via `choose_read_from` → `tables:nist-janaf-4th`, instead of `unknown:nist-janaf-4th` / “no matching INDEX asset”. Guard: advertise the path only when corpus `.txt` stems **exactly equal** compilation table ids; otherwise leave asset empty so rows honestly re-queue as unknown.

**Attack surface (brief):** Could the asset attach rows to a document they were **not** read from under a stem-only guard? Is the synthesized INDEX row honest that the asset is a **download cache** of the printed monograph (not the absent PDF, not `tables/nist-janaf-4th`, not the separate `janaf-4th` extract)?

---

## Findings

### P1 — Stem-set equality is not document identity; content drift still attaches all table locators

**Evidence:**
- `nist_janaf_txt_tables` (`build_index.py` at `a782e161c`) returns `raw/janaf-nist-txt` when `stems == txts` only — **no** check of `extraction.source_sha256` against corpus file bytes.
- Every compilation table records `extraction.source_sha256` (1655/1655). Harvest `source_cache_path` is **never** `raw/janaf-nist-txt`: tallied 1421 `janaf-mirror`, 194 other Dropbox paths, 40 `/private/tmp/janaf-review-evidence/...`. The INDEX path is a **relocated** cache; hash is the only honest bridge, and the builder does not use it.
- `choose_read_from` (`migrate.py:5745-5748`): for a `tables/` locator, after path-substring miss, returns **any** `AssetRole.TABLE_CSV`. Registering one directory asset therefore stamps `tables:nist-janaf-4th` on every JANAF table locator for that work.
- Adversarial tmp probe (commit code): keep stem `A-001`, overwrite `A-001.txt` with different bytes while YAML still carries the original `source_sha256` → guard still returns `exists: True`, `source_files_for` emits `tables:nist-janaf-4th`, `choose_read_from` → `tables:nist-janaf-4th`, `unmatched_read_from_reason` → `None`, while `sha256(txt) != recorded`. Exact “attach rows to a document they were NOT read from” under the stated stem guard.

**Why P1:** Commit message asserts all 1,655 hashes match the corpus; that claim is not enforced at INDEX build time. Same-named re-download, stale refresh, or partial overwrite keeps the asset advertised and silently rewrites provenance for the full JANAF observation set on remigrate. Stem inequality correctly refuses foreign/extra/missing names — necessary, not sufficient.

**Fix direction:** Before advertising the path, require every table's recorded `source_sha256` to equal `sha256` of `raw/janaf-nist-txt/<stem>.txt` (refuse on missing sha or mismatch). See `ferry/reviews/R6-fix.patch` (plus tmp_path refusal test).

---

### P2 — No negative unit test for the stem/refusal path; happy path needs live corpus

**Evidence:**
- Only `test_nist_janaf_index_asset_is_the_corpus_txt_download` — live `build_index(REPO_ROOT)` + one `K-001` hash spot-check. On this box without `REGOLITH_CORPUS_ROOT`, that test **fails** (`tables.path == ''`).
- No tmp_path test that extra/missing stems or content drift leave `path == ""` / `exists is False`, or that empty tables asset yields `unknown:nist-janaf-4th` for a tables locator.

**Why P2:** The attack guard is the load-bearing claim of the commit; it is unpinned except by an environment-dependent integration test. Fix patch adds `test_nist_janaf_txt_tables_refuses_stem_mismatch_and_content_drift`.

---

### P3 — Citation is Monograph 9; asset is NIST web `.txt` cache (path honest, media not named)

**Evidence:**
- INDEX row citation: Chase … Monograph 9 (1998); `doi: 10.18434/T42S31`; `pdf_status: ABSENT`; `corpus.tables.path: raw/janaf-nist-txt` (1655 files); conventional `raw/nist-janaf-4th/nist-janaf-4th.pdf` left absent.
- Table extraction method text: “machine parse of NIST tab-delimited table download; source bytes cached unchanged”; `download_url: https://janaf.nist.gov/tables/<id>.txt`.
- INDEX schema has no “asset nature” note; honesty is carried by the atypical `raw/janaf-nist-txt` path in the tables column (INDEX.md renders it under corpus raw/text/tables).

**Why P3:** Not a lie — PDF is not claimed present; path names the txt cache; DOI is the digital SRD. Mild clarity gap vs “download cache of the printed monograph” in prose; not a land blocker.

---

## What works (positive)

- Stem **inequality** (extra/missing `.txt`) leaves `path: ""`, `exists: False`; `source_files_for` treats empty path as falsy → **no** `TABLE_CSV` asset → table locators get `unknown:nist-janaf-4th` and the unmatched queue reason (honest re-queue).
- Distinct from manual `janaf-4th`: no alias; legacy tables path stays `tables/janaf-4th`; comment + test assert txt cache is not attached to the extract work.
- Overrides `corpus_pointers`' conventional `tables/<source_id>` so an empty `tables/nist-janaf-4th` cannot be the advertised source (null hypothesis in the test docstring).
- For table locators, `choose_read_from` does **not** fall through to the absent monograph PDF path when no TABLE_CSV exists (layer `table` → `_unknown_asset_id`).
- Committed INDEX: `extracts: []`, `aliases: []`, sources 167→168; PDF ABSENT.

---

## Tests run

```
# worktree at a782e161c; venv python
# Integration (needs live corpus — absent here):
REGOLITH_CORPUS_ROOT=/nonexistent pytest tests/test_literature_index.py::test_nist_janaf_index_asset_is_the_corpus_txt_download
# → FAILED assert '' == 'raw/janaf-nist-txt'

pytest tests/test_literature_index.py::test_compilation_manifest_identity_header_fixture  # PASSED

# Adversarial probes against commit code (tmp trees):
#   stem match attaches; extra/missing txt refuses; content-swap still attaches + read_from tables:nist-janaf-4th
# Optional R6-fix.patch: content-swap refuses; new tmp_path test PASSED
```

Mode: **ran-tests** (probes + unit tests; live corpus integration not green on this box).

---

## Optional patch

`ferry/reviews/R6-fix.patch` — hash-verify every table `source_sha256` against corpus `.txt` before advertising `raw/janaf-nist-txt`; add tmp_path refusal test for foreign stem + content drift. Not applied.

---

## Verdict rationale

Intent is right and the stem guard blocks wrong **filenames**. The attack question is answered: stem equality alone **can** still attach `read_from` to bytes that were not what the tables were parsed from, and the INDEX relocates provenance from private harvest paths to the corpus cache without enforcing the hash bridge the commit message relies on. Row is otherwise honest (not the PDF / not `janaf-4th`). **LAND-WITH-FIXES** — land after hash guard (+ negative test); re-run INDEX regen on a corpus that passes the tighter check.

VERDICT: R6 | LAND-WITH-FIXES | P0=0 P1=1 P2=1 P3=1 | ran-tests
