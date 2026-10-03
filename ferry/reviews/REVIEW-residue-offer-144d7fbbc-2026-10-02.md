# REVIEW — residue-offer tip (SHORT DELTA)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z5`
- **Tip:** `144d7fbbc24cff74fa39506cabbe65836bdfe391` on `review/residue-offer` (`git rev-parse HEAD` confirmed)
- **Composition:** `ccbfc7db5` (merge LANDed regen `d9a06fcbb` + R3 `50da4a5e9`, on LANDed batch `d1179bbf9` + R2 `54bf0d7e4`) + `a106a338f` merge of green `e620b4dd5` (openimcc pin afcb5d8→be41a6d) + `144d7fbbc` test-only pin update
- **REQ:** `/workspace/ferry-inbox/REQ-residue-offer-delta-from-regolith-physics-2026-10-02.md`
- **Report:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/green-merge-report.md` (be41a6d Hashimoto primary; 23/24 converged)
- **Date:** 2026-10-02 ~21:01–21:05 ET
- **Mode:** read-only product; no extract invention; no Mac listen pools; VPS tightly-scoped unit tests only (no full W3 / 459-file suite on ~16GB box)

## Scope

DELTA of tip `144d7fbbc` = green merge + two OpenIMCC Hashimoto-start pO2 pins re-derived under be41a6d. Tip delta vs `a106a338f` is **one file**: `tests/battery/test_residue_oxygen_balance.py` (+7/−4). Tolerance stays `rel=2.0e-5`. IA pins unchanged.

**Store effect (whole offer, for the train):** regen `d9a06fcbb` rewrites `kems-012-sossi-2019.yaml` + `kems-015-hashimoto-1983.yaml` identities (522 + 648 obs, printed oxides only) + `migration-report.md`; per-source counts/ids unchanged (seat: 612 / 681 `observation_id` on tip). Prior combined REVIEW LANDed regen+R3; this tip adds green pin bump + oxygen-balance pin move only.

## Attack results

### (1) Merge `a106a338f` clean; tree = merge-tree — **PASS**

- Parents: `ccbfc7db5aea33891515e66299c167d253af743a` + `e620b4dd57cc0eb2a4f7fc1ebde877f103b064dd`
- `git merge-tree --write-tree P1 P2` → `b84c631c26038e54ed8dcbc6ad0840a5d99faba8`
- `a106a338f^{tree}` → **same** `b84c631c…`
- No conflict markers in merge tree; merge touches only `pyproject.toml` + `openimcc_bridge.py` (pin bump)

### (2) New pins reproduce on be41a6d; tolerance not widened; IA untouched — **PASS**

- OpenIMCC src checkout `be41a6db66a9597685e2bf685013bfdfff737009` on `PYTHONPATH` (scratch clone); simulator from seat tip.
- `test_hashimoto_vacuum_oxygen_balance_is_engine_specific_and_alpha_weighted` → **1 passed** (~18 s)
- Pins asserted: openimcc α=1 → `0.828632754`; α=0.25 → `0.300351240`; both `rel=2.0e-5` (unchanged)
- IA pins still `1.025329` / `0.374487` at `rel=2.0e-5` (byte-identical to pre-`144d7fbbc`)
- Comment records engine + old afcb5d8 values
- Contrast (afcb5d8 site-packages, new pins): **FAIL** obtained `0.7993030973935545` vs expected `0.828632754` — proves move is engine-driven, not loosened
- Narrow suite: `tests/battery/test_residue_oxygen_balance.py` → **10 passed** (~60 s) under be41a6d

### (3) No other residue value still pinned to afcb5d8 that should have moved — **PASS**

- `rg 'afcb5d8|0\.799303|0\.289332' tests/` → only the documenting comment in `test_residue_oxygen_balance.py` (old values not asserted)
- `rg 'be41a6d'` tests → same comment + product pin string in bridge/pyproject from green merge
- Other `pytest.approx` literals in residue/hashimoto battery are IA/geometry/mol arithmetic, not OpenIMCC Hashimoto-start pO2 pins
- No second afcb5d8-era openimcc pO2 assertion left behind

## Targeted tests (VPS)

| Suite | Engine | Result |
| --- | --- | --- |
| `test_…_hashimoto_vacuum_oxygen_balance_…` | be41a6d | **PASS** |
| same (contrast) | afcb5d8 | **FAIL** at old 0.799303 (expected) |
| `test_residue_oxygen_balance.py` (full file) | be41a6d | **10 passed** |

Not run (policy): worker’s six-file 459, full W3, Hashimoto cohort re-score, Mac Studio full pytest — green-merge-report 23/24 converged accepted; ASK Mac Studio if a broader green gate is required.

## Verdict

**LAND `144d7fbbc24cff74fa39506cabbe65836bdfe391`**

No P1 REVISE. Optional P2: full six-file / cohort re-host not done here — Mac Studio ASK only if physics wants that gate before merge.

— regolith-empirical
