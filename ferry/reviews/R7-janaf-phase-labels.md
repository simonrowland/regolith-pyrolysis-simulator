# R7 — JANAF phase-transition labels

**Repo:** regolith-pyrolysis-simulator  
**Base (parent):** `e2897a867` (`origin/review/janaf-batch-2026-09-22`^)  
**Tip under review:** `07046e90b` on `origin/review/janaf-batch-2026-09-22`  
**Intent:** Maps printed labels (CRYSTAL <--> LIQUID, ALPHA <--> BETA, I <--> II …) onto Phase/Polymorph tokens where unambiguous; span across two phases must stay queued, never collapsed to one token (`simulator/battery/generators/janaf.py`, `migrate.py`, `polymorph_dictionary.py`).  
**Attack:** wrong phase/polymorph mapping; two-phase span silently assigned one side; GLASS treated as LIQUID.

**Files:** `simulator/battery/generators/janaf.py`, `simulator/battery/migrate.py`, `simulator/battery/polymorph_dictionary.py`, `tests/battery/test_janaf_generator.py`.

**Method:** Static review of tip vs parent on a detached worktree of `07046e90b`; corpus inventory of every printed `<-->` label; adversarial probes for the three named attacks; narrow pytest on the tip worktree. Green / mailbox branches left untouched. No push.

---

## Summary

The tip does what the intent says. `_transition_species_phase` maps both sides through `_side_phase` / `_side_polymorph` (crystal roman/Greek via `JANAF_TRANSITION_POLYMORPHS`, IV/V via new label-only `JANAF_LABEL_POLYMORPHS`, CRYSTAL/LIQUID/LIQ/GLASS/GAS/IDEAL GAS/REAL GAS via `_PHASE_SIDE`). Same closed phase → that Phase token; crystal-crystal with unequal polymorphs → `phase=cr` and polymorph stays `unknown` with an explicit span reason; unequal phases or any unmapped side → phase stays `unknown` and does **not** take the mapped side. GLASS is a distinct `_PHASE_SIDE` entry (`Phase.GLASS`), so `GLASS <--> LIQUID` / `GLASS <--> LIQ` stay queued as `glass -> l`. IV/V label without becoming crystal-table split points on pure `cr` tables (Na-020 stays one `cp:segment-0`); on `cr,l` tables that already split on every label (Na-025), segments 0/1 gain `cr`+`v`/`iv` instead of unknown — observation ids unchanged. `janaf_extract_phase` maps extract spelling `ideal_gas` → `Phase.G` only for `source_id=="janaf-4th"`.

No production defect found under the attack checklist. One P3: the new unit test never exercises the named GLASS attack (or EPSILON one-side refusal / `GLASS <--> LIQ`). Optional test-only patch: `ferry/reviews/R7-fix.patch`.

---

## Findings

### P3 — Named GLASS / one-side-unmapped attacks absent from the new unit test

**Where:** tip `tests/battery/test_janaf_generator.py` `test_janaf_phase_labels_use_closed_tokens_without_collapsing_a_span`.

**Evidence:** Test covers `ALPHA <--> BETA` (phase `cr`, polymorph unknown), `CRYSTAL <--> LIQUID` / `I <--> LIQUID` (phase unknown), Na-020 IV/V non-split, Na-025 IV/V segment labels, EPSILON only as an unknown **segment** reason on Al-051, and `janaf_extract_phase("ideal_gas")`. It never loads a `GLASS <--> LIQUID` / `GLASS <--> LIQ` transition observation, and never asserts that `DELTA <--> EPSILON` / `EPSILON <--> LIQUID` stay fully unknown rather than collapsing to `cr`/`delta` or `l`.

**Runtime probe (tip worktree, not in CI as written):**

```text
Na-024 / O-020 / Al-003  transition_temperature:glass-liquid
  → phase unknown, reason contains "(glass -> l); ... cannot hold both"
  → not Phase.L and not Phase.GLASS

W-003  transition_temperature:glass-liquid  (printed "GLASS <--> LIQ")
  → phase unknown, reason "(glass -> l); ..."

Al-051  transition_temperature:delta-epsilon
  → phase unknown; detail side 'EPSILON' is not a closed token
  → does not assign Phase.CR / Polymorph.DELTA from the mapped side
```

**Why it matters:** The three stated attacks are the regression surface. Code currently passes them; tip CI would not catch a future `_PHASE_SIDE["GLASS"]=Phase.L` typo or a "take the mapped side when the other is unknown" shortcut.

**Fix (optional, test-only):** `ferry/reviews/R7-fix.patch` — assert glass-liquid / glass-liq / delta-epsilon / epsilon-liquid stay queued.

---

## Attack checklist

**(a) Wrong phase/polymorph mapping?**

Corpus unique printed labels (25 after de-duping JSON/YAML harvest noise): all sides are either in `_PHASE_SIDE`, `JANAF_TRANSITION_POLYMORPHS`, `JANAF_LABEL_POLYMORPHS`, or `EPSILON` (intentionally unmapped). Simulated outcomes:

| Label | Outcome |
| --- | --- |
| `ALPHA <--> BETA`, `I <--> II`, `V <--> IV`, … | `phase=cr`, polymorph unknown (span) |
| `SiO2` `I <--> II` | same; reason uses aliased `alpha -> beta` via `JANAF_TRANSITION_ALIASES` |
| `CRYSTAL/BETA/I <--> LIQUID`, `LIQUID <--> IDEAL/REAL GAS` | phase unknown (`cr->l` / `l->g`) |
| `GLASS <--> LIQUID`, `GLASS <--> LIQ` | phase unknown (`glass->l`) — not liquid |
| `DELTA <--> EPSILON`, `EPSILON <--> LIQUID` | phase unknown (unmapped side); no one-sided assign |

Na-025 parent→tip: segments 0/1 go from phase unknown to `cr`+`v`/`iv`; segment ids `cp:segment-0..3` unchanged. Na-020 remains a single `cr` segment (IV/V not in `_CRYSTAL_POLYMORPHS` for boundary membership). No wrong token observed.

**(b) Two-phase span silently assigned one side?**

No. Unequal `_side_phase` results return `State.unknown` with both tokens in the reason (`cr -> l`, `glass -> l`, `l -> g`). A single unmapped side (EPSILON) also refuses the whole phase axis rather than keeping DELTA or LIQUID. Parent always stamped a generic unknown on every transition; tip is stricter and more precise, not looser.

**(c) GLASS treated as LIQUID?**

No on the transition path. `_PHASE_SIDE["GLASS"]=Phase.GLASS` ≠ `Phase.L`; `LIQ`/`LIQUID` map to `l` separately; span check keeps the observation queued. Live tables Na-024, O-020, Al-003, W-003 confirm. (Pre-existing: many glass tables are indexed `state: l` and do not split segments on `GLASS <--> LIQUID` because boundaries require `state in {cr,l,ref,l,g}` or named crystal splits — tip does not change that boundary gate; out of stated intent.)

**(d) Rows / ids unchanged as claimed?**

Spot-check Na-020 / Na-025: observation id sets for `cp` and `transition_temperature:*` match parent; only phase/polymorph **values**/reasons change. Full-corpus accounting deltas in the tip test (`unknown` phases 5→3, polymorph iv/v 1→2 each) match the Na-025 segment labelling, not new rows.

---

## Method

- Static: `e2897a867..07046e90b` on `janaf.py` / `migrate.py` / `polymorph_dictionary.py` / tests; `_transition_species_phase` control flow; boundary gate vs `JANAF_LABEL_POLYMORPHS`; `make_species(None polymorph)` fill-in; `janaf_extract_phase` scoping.
- Corpus: every `raw_line` / label containing `<-->` under `data/literature/compilations/janaf/tables/`.
- Ran tests (tip worktree, `PYTHONPATH=/tmp/r7-janaf`, `-o addopts=`):  
  `test_janaf_phase_labels_use_closed_tokens_without_collapsing_a_span`  
  `test_janaf_extract_ideal_gas_maps_only_for_that_source`  
  `test_transition_observations_reports_and_vocabulary_gaps`  
  `test_concatenated_transition_temperature_and_cp_rows_are_recovered`  
  → 4 passed.  
- Adversarial probes: GLASS/LIQ/EPSILON/two-phase spans on live `_generation(...)` tables; parent vs tip Na-025 segment dump.

Optional fix (not applied): `ferry/reviews/R7-fix.patch`.

---

VERDICT: R7 | LAND | P0=0 P1=0 P2=0 P3=1 | ran-tests
