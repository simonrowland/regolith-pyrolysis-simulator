# REVIEW — residue-r5 DELTA: bounce-fix + two green merges

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-y17` on `worktree/residue-r5-delta-review`
- **Tip:** `c5c49f53e985383b8a74e4764272d5de7bcabeca` (`review/residue-r5`)
- **Green base now:** `4355e163a66482d708ff03fd057ebe8f88d41872` (`origin/work-v064-green`)
- **Prior LAND tip (unchanged below):** `c35db99ffa4633a9e65786912da6ac8ede36b299`
- **New commits on top of prior LAND (in order):**
  1. `f550d1c6f2b531790d2e25380bf12e03485c5505` — Preserve default vapor dispatch signature (`simulator/core.py` only, +11/−5)
  2. `11e58e07a5d113bb5f039f2509d8b874e52ffb6f` — merge green `ebe83db46` (openimcc pin → bc3ac65; bridge binding digest)
  3. `c5c49f53e985383b8a74e4764272d5de7bcabeca` — merge green `4355e163a` (Gibson extract + derived store; no product code vs that green tip beyond R5 residue delta)
- **REQ:** `ferry/REQ-residue-r5-delta-from-regolith-physics-2026-10-03.md`
- **Packet:** `ferry/packet/` (`r5-kwarg-report.md`, `pre-offer-receipt-c5c49f53e.json`, `r5-digest-report.md`, `r5-form-report.md`)
- **Prior REVIEW:** `REVIEW-residue-r5-c35db99ff-2026-10-03.md` (LAND c35db99ff stands for everything below f550d1c6f)
- **Date:** 2026-10-04 ~00:15 ET
- **Mode:** read-only for product code; no extract invention; no Mac listen pools; no push of empirical product branches. Targeted VPS unit tests only (no full W3 / full-store score_store / full pytest).

## Scope (delta only)

Landing gate bounced prior LAND tip because
`tests/test_vaporock_backend.py::test_core_does_not_consume_non_authoritative_vaporock_pressures`
raised `TypeError: dispatch_only() got an unexpected keyword argument 'include_diagnostic_shadows'`.
Commit `3ca518746` (already in prior LAND range) passed that keyword to `self._dispatch_only(...)` **unconditionally**.
Fix `f550d1c6f` restores the old default signature and adds the keyword **only** when suppressing shadows.
Two clean green merges follow.

**Store effect:** `git diff 4355e163a c5c49f53e -- data/` → **0 bytes** (empty). Confirmed on seat.

**Merge-tree cleanliness (rechecked):**
- `git merge-tree --write-tree f550d1c6f ebe83db46` = `11e58e07a^{tree}` = `fa36619122ce08703c2a4a6990ae43c475d7d7c9` — **match**
- `git merge-tree --write-tree 11e58e07a 4355e163a` = `c5c49f53e^{tree}` = `213374119923bf3b93054e78abe3ca1ba8e2e89e` — **match**

**Pre-offer receipt (verified claims relied on; full 2999-test sweep not re-run on VPS):**
- `blocking: []`, `symbol_sweep.new_failures: []`, `symbol_sweep.passed: True`
- summary: 2999 passed; 12 xdist failures → 10 serial-still-fail **all also fail on green** → **NEW 0**
- `hard_issue_delta.conditional_field: 0` with both sides at **3655**
- fixed-tests list in receipt: migrate/j01 suite **passed** on offer machine

## Attack results

### (1) Is "pass the keyword only when False" complete? — **PASS (for current callers)**

Seat `rg` evidence:

**Keyword sites (`include_diagnostic_shadows`):**
| Site | Role |
| --- | --- |
| `simulator/core.py` `_dispatch_only` (~L2362) | Consumes option; selects `kernel.dispatch` vs `kernel._dispatch_authoritative_without_shadows`; **does not forward** the keyword to either callable |
| `simulator/core.py` `_refresh_vapor_pressures_from_kernel` (~L9315, L9485–9488) | **Fix:** builds `dispatch_kwargs` with old args; adds `include_diagnostic_shadows=False` **only when** `not include_diagnostic_shadows` |
| `simulator/diagnostic_helpers/binary_pot_battery.py` adapter (~L2166, L2361–2366) | Same convention one level up: default call omits keyword; suppress path passes `False` into refresh |
| `simulator/battery/residue.py` ~L1243, L1826 | Always passes `include_diagnostic_shadows=False` to the **adapter** (explicit adapter API), not to a substitutable `_dispatch_only` |
| Test pin | `tests/battery/test_internal_analytical_battery_engine.py:193` |

**No other production keyword added by this delta range** (`git diff c35db99ff..c5c49f53e -G include_diagnostic_shadows` → `simulator/core.py` only).

**Test stubs named `dispatch_only` / `_dispatch_only` / `dispatch`:** many fakes exist (`test_vaporock_backend.py:709` is the bounce stub — signature `(intent, *, control_inputs, fO2_log)`). After the fix, the vaporock test calls `_refresh_vapor_pressures_from_kernel(result)` on the **default** path → no keyword → **PASS on seat**.

**Caller that suppresses shadows with a substituted dispatcher?**
- Production suppress path: residue → adapter → real `core._refresh_…(…, include_diagnostic_shadows=False)` → real `_dispatch_only` (accepts the kwarg). **No substitution.**
- Seat found **no** test or production site that both (a) assigns `sim._dispatch_only = <old-signature stub>` and (b) calls refresh/adapter with `include_diagnostic_shadows=False`.

**Typed refusal?** A substituted old-signature dispatcher on the **suppress** path would still raise bare `TypeError`. That class is not exercised by any current caller. Hardening to a typed refusal (`inspect.signature` / catch-and-rewrap) would be a separate improvement; it is **not** required to close the bounce class (default path + stub). **Not a REVISE** for this delta.

### (2) Merge ebe83db46: residue byte digests vs bridge binding digest — **PASS (no new inconsistency)**

- Residue keeps `_gas_pack_byte_digests` (raw SHA-256 of `gas_path` / `oxide_path` bytes) → provenance fields `gas_table_sha256` / `condensate_table_sha256` (and Sossi `gas_pack_digest` / `liquid_pack_digest`). Docstring already names **b-690** as the future replacement by the bridge binding digest.
- Green `ebe83db46` changes `openimcc_bridge._pack_digest` to require `pack.binding_digest`, refusing with `openimcc_binding_digest_unavailable` when missing. Engine provenance uses `openimcc_pack_digest` / melt `pack_digest` from that path.
- Hashimoto nested provenance keeps **both** under distinct keys:
  - `pack_digest.melt_datapack` ← bridge binding digest
  - `pack_digest.gas_table_sha256` / `condensate_table_sha256` ← residue byte digests
- Scorer lineage (`score.py` ~L3333) reads engine identity `pack_digest`, not the residue gas-table byte hashes.
- **No consumer treats the two digests as the same value that must agree.** Merge did not wire them into one field or cross-check. Pre-existing dual-scheme remains; **nothing newly inconsistent NOW.** b-690 stays out of scope.
- Residue also records `openimcc_pin: OPENIMCC_RECORDED_PIN` (now bc3ac65) — provenance metadata only; green commit states numeric residue output and digest **fields used as numbers** unchanged.

### (3) NOT-FIXED lens — **PASS (class named; no live caller)**

Constructed dispatcher for which the vaporock **class** of failure still occurs:

```python
def dispatch_only(intent, *, control_inputs, fO2_log):  # no include_diagnostic_shadows
    ...
sim._dispatch_only = dispatch_only
sim._refresh_vapor_pressures_from_kernel(eq, include_diagnostic_shadows=False)
# → TypeError: unexpected keyword argument 'include_diagnostic_shadows'
```

Also fails for any stub that rejects unexpected keywords on the suppress path (no `**kwargs`). The bounce class on the **default** path is fixed. Remaining class = suppress + old stub; **no such caller in tree** (attack 1).

### (4) Do the two merges change residue numbers? — **PASS (no numeric change from merges)**

- Pre-merge residue rail fingerprints (`r5-digest-report.md`, tip c35db99ff era): OpenIMCC residual SHA `4c5c2528…` / IA `cf15e097…` identical before/after digest consolidation; counts 464 / 163 / 86.
- Merge `11e58e07a` ← `ebe83db46`: bridge/provenance/pin only (`openimcc_bridge.py`, pin tests, `pyproject.toml` pin). Commit message: **numeric residue output unchanged**; digest is provenance not key material; v1.0.2 keeps `f2b479cd`. Code vs green after tip: residue/score/planner/core/adapter + tests only (R5 product), not melt-activity math from this merge.
- Merge `c5c49f53e` ← `4355e163a`: Gibson extract + migration-queue / derived sibling YAML — **no simulator code**. `data/` diff tip↔green empty ⇒ tip store matches green store.
- Seat does **not** re-fingerprint whole-store residue on 16GB VPS (policy). Worker/pre-offer cover numeric invariance for the pin move.

## REFACTOR-AS-YOU-GO (d-062) — new commits only

1. **Second copy of added/edited logic?** **No (two call sites of a convention, not one duplicated rule).** Conditional-keyword pattern appears in `core.py` (refresh→`_dispatch_only`) and the battery adapter (adapter→refresh). Each is the boundary that must preserve the default callable signature for stubs; neither copies a threshold/physics rule. No additional owner required; commit comment documents the convention. Residue sites pass an **explicit** adapter kwarg (different API).
2. **Rule/threshold/physics in presentation/wiring?** **No.** Wiring/signature only in `f550d1c6f`; merges are pin/provenance and extract data.
3. **Forbidden-layer import or cycle?** **No.** Fix touches `simulator/core.py` only. Import boundary on seat: **14 passed**.
4. **Behaviour-preserving move backed by pin before the move?** **Yes** for the fix: pre-existing vaporock test failed before `f550d1c6f` and passes after. Merges are not behaviour-preserving moves of logic.
5. **Relax guard / baseline / move into red-guard module?** **No.**

No FIX-FIRST items.

## Targeted tests (VPS seat)

| Suite | Result |
| --- | --- |
| `test_core_does_not_consume_non_authoritative_vaporock_pressures` | **PASS** (~3 s) |
| `test_internal_analytical_residue_adapter_skips_diagnostic_shadow` | **PASS** |
| `tests/test_import_boundary.py` | **14 PASS** |
| Sossi + Hashimoto residue suites (10 other collected) | **8 PASS**; **2 FAIL** — both `OpenImccBindingDigestUnavailableError` because VPS `openimcc==0.1.0.dev0` exposes **no** `binding_digest` (needs pin bc3ac65). Environment skew, same class as prior VPS openimcc notes — **not** a product REVISE of the tip (green intentionally refuses old hosts). |

Not run (policy): full W3, whole-store `score_store`, Mac Studio full pytest, offer's 2999-test sweep.

## Verdict

**LAND `c5c49f53e985383b8a74e4764272d5de7bcabeca`**

Bounce class fixed on the default path; suppress+stub remains theoretical with no live caller; merges clean and store-empty vs green; digests dual-scheme unchanged in meaning; d-062 clear.

**ASK (optional green gate / env):** Mac Studio (1) confirm vaporock + full residue suites under openimcc `@bc3ac65`; (2) if desired beyond this REVIEW OF RECORD, full-store `score_store` residue fingerprints. Do **not** run those on this 16GB VPS.

— regolith-empirical
