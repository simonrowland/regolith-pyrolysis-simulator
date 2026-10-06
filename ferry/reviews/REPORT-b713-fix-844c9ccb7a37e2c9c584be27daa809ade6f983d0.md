# REPORT: b713 absolute anchors, ROR fix

**From:** regolith-empirical (seat)   **To:** regolith-main   **At:** 2026-10-05 ~22:50 ET
**Review of record:** `ROR-b713.md` (FIX-FIRST, P1 + P2 + P3)
**Reviewed tip:** `803d1b2c9ee1b46f21a1ed44a17b466d56ac4f80` (verified on origin before work)
**Fixed tip:** `844c9ccb7a37e2c9c584be27daa809ade6f983d0`, rebased onto green `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`
**Branch:** `review/b713-absolute-anchors-r2` (new ref). Auto-review blocks force-push: the user's allow rules exclude it. So `review/b713-absolute-anchors` still points at `803d1b2c9`. Take the tip from `-r2`, or have Simon approve a force-with-lease.

Commits on green (author Simon Rowland, no AI trailers):
- `94d7246de` pin (was `7b1e2ef42`)
- `bf1ad49bf` 46-extract prefix rewrite (was `803d1b2c9`). The rebase was clean, and this commit's extract content is identical to the reviewed tip.
- `844c9ccb7` **fix(b713)**: refuse absolute path values per extract, using the parsed document

## Per-finding table (3/3 fixed, plus the required change)

| # | Finding | Change | File:line (fixed tip) | Test / mutation proof |
|---|---|---|---|---|
| Req / P1 | The ceiling counts regex *lines*. Quoted `source_path` and the Gibson `original_scan: &scan` / `source_path: *scan` form both pass. Green: 471 lines vs 1,602 parsed values. | Replaced the line gate with the parsed walk inside `validate_extract_document`, next to where the provenance check was. One absolute value on any `PATH_VALUE_KEYS` key fails *that* extract. The walk runs after YAML alias expansion, so an anchor reused N times is N values. Errors are deduplicated per (key, value), with a "+N more via YAML aliases/repeats" note. The old provenance-only check is subsumed: same message text, still pinned by the green test `test_refuses_absolute_provenance_path`. | `tools/validate_literature_extracts.py:158` (walk), `:186` (`_absolute_path_value_errors`), `:1582` (wiring) | `tests/test_absolute_path_ratchet.py:85` drives `validate_extract_document` with 4 spellings: unquoted, quoted, `gibson_anchor_alias`, provenance_path. `:100` drives `validate_all` with quoted + alias. `:110` checks that 1 anchor read by 3 locators gives 3 parsed values. **M1** (wiring line deleted): 8 FAIL (all 4 spellings, both validate_all cases, original_scan, `test_refuses_absolute_provenance_path`). **M2** (reviewed-tip validator restored): 9 FAIL, including quoted and alias. **M4** (walk does not recurse into lists): 4 FAIL. Each mutation was restored and the suite went back to 16/16 pass. |
| Req (no second signal) | Keeping the line regex would leave a second pass/fail rule that can report clean | Removed `_ABS_PATH_FIELD_LINE_RE`, `count_absolute_paths_in_extracts`, `check_absolute_path_count_ceiling`, `ABSOLUTE_PATH_COUNT_CEILING`, and the `validate_all` wiring. | `tools/validate_literature_extracts.py` (old 175-226 and 1769 deleted) | `test_line_regex_ceiling_is_gone` (`:160`). Under M2 it FAILS. |
| P1 (class) | `original_scan` is not in `PATH_VALUE_KEYS`, so an un-aliased absolute scan path has no owner | `PATH_VALUE_KEYS` now also holds `original_scan`, `chapter_pdf`, `corpus_asset`, `repaired_table_path`, `table_transcription` and `verified_data_path`. These are every path-valued key found in a survey of all 273 extracts. `url` stays out because its values are URLs. The derivation is in the comment above the set. | `tools/validate_literature_extracts.py:142` | `test_unaliased_original_scan_is_refused` (`:125`) and the key-set pin (`:143`). **M3** (`original_scan` dropped): 2 FAIL. Restored, pass. |
| P2 | The green validator test does not lock the gate (it is red with 614 unrelated errors, and the ratchet tests bypass `validate_all`) | Added `test_repo_extracts_carry_no_absolute_path_values` (`:132`). It runs the validator's own function on every repo extract and fails on any absolute path; unreadable files raise. The probes assert the probe doc has no other errors, so the refusal cannot be buried. Separately, `validate_all` now returns 614 errors and **0** absolute. That is the same count the ROR saw. | `tests/test_absolute_path_ratchet.py:132`, `:85` | **M5:** restoring `ta-badro-2021.yaml` to its green (absolute) text makes the test FAIL. The failure prints `source_locators.abstract.source_path … (+1001 more via YAML aliases/repeats)`, which is the ROR's 1,002 parsed values. Restored, pass. |
| P3 | The unreadable extract was counted as zero | The counter is deleted. `validate_extract_file` already turns a load failure into an error, and the repo-tree test reads every file without a swallow. | (removed code) | Covered by the removal and by `:132` |

The ROR's measurement reproduces on the 46 rewritten files (green text): line regex 471, parsed walk 1,602 on the original 4 keys, and 1,602 on the extended key set. After the rewrite, the parsed walk finds 0 across the whole tree.

## Expected store delta (not committed; main regenerates at landing)

The validator/test commit changes nothing in the store. The extract rewrite (`bf1ad49bf`, unchanged from the reviewed tip) carries the delta the ROR measured. On landing regen, locator/context `source_path` strings change from `/Users/simonrowland/Repos/regolith-corpus/raw/...` to `corpus/raw/...` in `extracts-v2` (~1,235 occurrences), `works` (~288 locator/context strings, not asset `path:`), and `data/battery/migration-queue.yaml` (~1,319). There are 0 in `migration-report.md` and 0 in `observations-v2`. Work ids, asset paths and sha256, observation ids, and INDEX `corpus.raw.path`/sha256 are unchanged, per the ROR's identity checks; the rebase did not touch those files.

## Targeted tests (VPS)

- `tests/test_absolute_path_ratchet.py` (15) plus `tests/test_literature_extracts.py::test_refuses_absolute_provenance_path`: **16 passed**.
- Full `tests/test_literature_extracts.py` (the validator's direct tests), `-n 3`: 430 passed, 12 failed. The same 12 fail on the rebased reviewed tip without my commit (diff of failure sets: identical). They are pre-existing: `test_repo_extracts_validate_green` (614 schema/fidelity/vocabulary errors, none absolute), 7 `test_fidelity_sample_matches_extract[...]`, `test_every_extract_has_fidelity_sample`, `test_coverage_pending_stubs_not_found`, `test_pilot_extract_count_exact_and_merge_smoke`, and one more fidelity case.
- I did not run the full suite (VPS limit). **ASK:** run the full gate on a Mac Studio.

## D-062 checklist

1. Second copy? **No.** `rg` of `_ABS_PATH_RE|PATH_VALUE_KEYS|absolute/machine-local` in tools/simulator/scripts/tests finds one walk and one error builder, both in `tools/validate_literature_extracts.py`. The provenance-only branch was folded into it, and the line regex is deleted.
2. Rule in presentation/wiring? **No.** It is a validator rule in `tools/`, the module that already owns it.
3. New import or cycle? **No new imports.**
4. Behaviour-preserving move without a prior pin? The provenance refusal moved into the general walk. It is pinned by `tests/test_literature_extracts.py::test_refuses_absolute_provenance_path`, which predates this branch on green and asserts the same "repository-relative" text. It passed before and after, and failed under M1.
5. Relaxed guard / baseline entry? **No.** The guard is tightened: every parsed path value is checked per file, and the key set is wider. No baseline/allowlist entry was added.

Hygiene: the diff adds no `docs-private` and no absolute path (the absolute strings appear only as `/Users/someone/...` probe literals in the test). No derived store is in the diff. `git diff --stat 61ec839da..844c9ccb7`: 48 files (46 extracts + validator + ratchet test).
