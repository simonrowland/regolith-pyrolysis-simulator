# STATUS (final): fix nekhoroshev-2019-phd-polymtl — batch3 B, LARGE

**From:** regolith-empirical (batch3 B fix seat)   **At:** 2026-10-05 ~21:31 ET
**Branch:** `hunt/nekhoroshev-2019-phd-polymtl` on `mac-studio-256-1:Repos/regolith-corpus.git` (origin only; mirror main not touched)
**Reviewed start tip:** `cfaa74654d6e307efba4c3b01a7af430e0963f26` (verified)
**Final pushed full tip:** `b6f2b46966c2c33da3b1a4b41f694d01884b72e7`

## Commit chain (one per group)
| group | commit | review items |
|---|---|---|
| g1 | b02bcc7b3cdbbe95e32490168108126a3852f022 | 4 (Table 8.1 row binding), 6 (Table 24.1 + scope locators) |
| g2 | 2a9892ad54fe7302a7b97a36ae7733994a13b9e9 | 1 (Table 14.1 +14 rows → 53), 2 (Table 21.1 +54 rows → 57) |
| g3 | a99679a4a92889a12bbfce6ebb078255ac7adee7 | 3 (Table A.42 +27 p.529 lines → 59), 10 (Table A.6 expressions) |
| g4 | 6c47c26b310ea08d6bbdb2909e21d9ef920c6217 | 5 (Tables 1.1 = 40 rows, 1.2 = 17 rows; table_count 67 → 69) |
| g5 | 9fb4916aae12a9af39a770417e8e081571537c39 | 9 (activity reference states: 40 captions; reduction lineage: 12 study groups) |
| g6 | 30349a12c10ac8d5193046767d35936ee8daaf90 | 8 (per-study method facts: 8 entries + typed absences; scope unknowns re-pointed) |
| g7 | b6f2b46966c2c33da3b1a4b41f694d01884b72e7 | 7 (narrative values: 33 values; numbered equations: 18 + 22 in lineage); ledger gaps |

## Counts
- Review items addressed: **10/10**. Item 7 is partial: 40 of the 138 numbered equations are carried.
- Mismatches fixed: **6/6** (4 row-binding, 2 locator).
- Table rows added: **152** (14.1 +14, 21.1 +54, A.42 +27, 1.1 +40, 1.2 +17). There are 2 new table files, and the ledger table_count is now 69.
- New context rows: **7** (69 → 76). Two are table rows (Tables 1.1 and 1.2). Five are non-table rows: activity_reference_states, activity_reduction_lineage, per_study_method_facts, narrative_quantities and numbered_equations. The scope context unknowns were also re-pointed. (This corrects the first delivery of this file, mailbox 9435e048, which garbled this line.)
- Diff cfaa7465..b6f2b469: 16 files, +2979/−47.
- Migrator hard issues after finalize: **0**. Fidelity validator: OK. `tools/test_ledgers_valid.py`: 674 passed (Mac; green 61ec839d).

## Review corrections to note
- The review's p.91 locator for the Table 6.2 fusion-enthalpy sentence is wrong: the sentence is printed on **p.92 (PDF 149)**.
- p.80: the cristobalite ΔfusH is printed with the unit J·mol-1·K-1. It is carried as printed and flagged, not corrected.
- p.172: the sentence does not name the quantity for −3979.26/−3967.69 kJ/mol, so the name is a typed unknown.

## Remaining (exactly)
1. Numbered equations **(1)–(96), (100), (101)** (pp.10–87, PDF 67–144, CALPHAD model formalism) are not transcribed. They are recorded as a typed `not_carried` entry in `nekhoroshev_2019_numbered_equations` and in the ledger gaps.
2. The prose outside the review-listed pages was not swept exhaustively for other narrative numbers. This is recorded in the ledger gaps.
3. INDEX regen needed at landing (build_index.py was not run in the sparse tree).

## Worktree
The sparse worktree `~/Repos/regolith-corpus/worktrees/fix-nekhoroshev-2019-phd-polymtl` was clean and fully pushed, then removed after this delivery.

## Blockers
None.
