# R17 — JANAF liquid glass-region series (s9-glass)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `b5b9dacc8509217957258cc903bfce5d15274d2b` on `origin/review/s9-glass` (NOT landed on green; parent `07ad01dae`)  
**Worktree:** `/workspace/repos/wt/slot-01` @ detached `b5b9dacc8`  
**Files:** `simulator/battery/generators/janaf.py`, `tests/battery/test_janaf_generator.py`

**Intent:** JANAF liquid tables (`index state == "l"`) that print `GLASS <--> LIQUID` / `GLASS <--> LIQ` no longer stamp the whole one-segment series as phase `l`. Mixed glass+liquid stored points → `species.phase` unknown with the printed marker quoted in the reason / `phase_basis`; glass-only → `Phase.GLASS`. No segment split, no new observation ids, 13 681 rows conserved.

**Attack surface:** any glass-region value still labelled liquid; any liquid value lost; any id change; silent multi-marker / multi-segment skip; committed store still carrying old `l` stamps.

**Method:** Static read of tip vs parent on detached `b5b9dacc8`. Full-corpus tip↔parent `generate_table` compare (1655 tables). Inventory of every liquid-table glass/liquid printed label. Store freshness + per-table `cp:segment-0` phase check for all 128 glass-liquid tables in `observations-v2`. Focused pytest on tip. Mode: **ran-tests**.

---

## Findings

### P1 — Tip derived store still stamps all 128 glass-region liquid series as `l`

**Evidence (file:line / artifact on `b5b9dacc8`):**

- Commit touches only the generator + tests — no `battery_migrate` / store regen.
- `scripts/check_store_freshness.py --head HEAD` → `STALE`; lists `b5b9dacc8` among commits that touched migrate inputs without regen (store last touched at `2e9e17c3d`).
- Live store still wrong on the named attack:
  - `data/literature/observations-v2/compilations-janaf/janaf-Mg.yaml` — `nist-janaf-4th:Mg-013:cp:segment-0` → `phase.tag=value`, `phase.value=l` (glass marker at 900 K exists on the same table).
  - `janaf-W.yaml` — `nist-janaf-4th:W-003:cp:segment-0` → `l` (`GLASS <--> LIQ`).
  - Corpus scan: **128/128** store tables with `transition_temperature:glass-liquid` still have `cp:segment-0` phase `l` (0 unknown).

**Constructed trigger (store consumers today):** `load_migrated_store(root)` → observation `nist-janaf-4th:Mg-013:cp:segment-0` (and the other five quantities on `segment-0`) identity phase is `l` while the series still holds glass-side points at T ≤ 900 K (e.g. Cp `@298.15 = 81.927`) and liquid-side points above. Tip `generate_table` on the same YAML already returns `phase.unknown` with the printed marker quoted — generator and committed store disagree on this SHA.

**Why P1 (live-in-data, not P0):** Named attack hits the delivered store artifact. Batch Y: not P0 — JANAF compilation rows carry `scoring_eligible=false` / circularity notice in derivation (`janaf.py:1449–1451`), so a wrong phase stamp does not push a wrong number into score/ledger today. Same stale-store class as R16; this commit adds the glass-label delta without the follow-up regen.

**Fix direction:** Regenerate the derived store on top of this tip (`scripts/battery_migrate.py` + store summary) so `observations-v2` matches `generate_table`. No generator code change required for this finding. (Review rules: do not migrate from this review.)

---

### P3 — Pure-glass and multi-marker gates unexercised by corpus / silent skip

**Evidence:**

- `_glass_region_segment` (`janaf.py:665–681`) can stamp `State.of(Phase.GLASS)` when every stored number is glass-side; full corpus: **0/128** liquid+glass tables take that path (all mixed → unknown).
- Relabel runs only when `len(glass_markers) == 1 and len(segments) == 1` (`:1692–1693`). Corpus: 0 liquid tables with ≠1 glass marker; liquid `state` always yields one segment via `_SINGLE_PHASES`. A future double-marker table would keep the pre-fix `l` stamp with no warning.

**Why P3:** Latent / defense-in-depth. Today’s 128 tables all hit the mixed-unknown path. Tip test covers Mg-013 + W-003 mixed unknown and id stability, not the pure-glass branch or a multi-marker refusal.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Glass-region value still labelled liquid | **Fail (safe) on generator.** 128/128 liquid tables with a glass/liquid short-row marker → series phase `unknown` (never `l`); reasons quote `printed "GLASS <--> LIQUID"` (127) or `printed "GLASS <--> LIQ"` (1=W-003); orientation always glass→l. Liquid tables without marker (e.g. Al-011) stay `l`. **Hit — P1 on committed store** (128/128 `cp:segment-0` still `l`). |
| Liquid value lost | **Fail (safe).** Tip↔parent series values identical on all 13 681 observation ids (0 value mismatches, 0 dropped T points). Mg-013 keeps both 900 K Cp values `119.675` (glass) and `146.440` (liquid) plus `3000 K` liquid point in one series. |
| Id change / split / new rows | **Fail (safe).** Tip n = parent n = **13 681**; observation id sets equal (0 mismatches); all glass-liquid quantity series remain `…:segment-0` only (no new segment index). Phase-change summary tip↔parent: exactly **768** `l → unknown` (128 tables × 6 quantities); no other phase transitions. Full-corpus test phase bag `{"cr":796,"l":316,"g":874,"unknown":131}` matches 444−128=316 liquid. |
| Wrong collapse / unquoted marker | **Fail (safe).** Mixed → unknown with marker + temperature token in `phase.reason` and `phase_basis` (surfaced on `derivation.relation`). Transition row `…:glass-liquid` stays unknown via pre-existing span logic (`glass -> l`), unchanged by this commit. |

---

## What looks sound

- Post-hoc relabel on liquid single-segment tables is the right layer: `_segments` still treats `l` as `_SINGLE_PHASES` (no split on `GLASS <--> LIQUID`), matching “no split, no new ids”.
- Same-T left/right rule mirrors `segment_index` (`_on_printed_left` / `_on_glass_side`) so the labelled short row stays glass-side; set equality `{GLASS, L}` accepts either printed order (corpus is all glass→l).
- `_row_stores_number` aligns with the point-admission filters (errata / merged formation tail / scale-error), so “has glass-side numbers” tracks what actually enters the series.
- Tests pin Mg-013 ids, unknown+quote, dual 900 K points, glass-liquid transition unknown, W-003 `GLASS <--> LIQ`, and the corpus phase-count shift.

### Residual notes (not severity)

- `III <--> LIQUID` (etc.) printed on liquid tables remain non-boundaries (pre-existing); out of stated glass intent.
- Tip does not change transition observation phases; only the tabulated quantity series phase/basis.

---

## Tests

Tip worktree at `b5b9dacc8`, repo `.venv`, `-o addopts=`:

- `test_liquid_glass_region_is_not_stamped_liquid`
- `test_janaf_phase_labels_use_closed_tokens_without_collapsing_a_span`
- `test_full_corpus_control_cell_accounting_and_transcription_report`

**3 passed** (~102 s). Adversarial tip↔parent corpus compare and store phase scan: as above.

No `R17-fix.patch` — the live defect is missing store regen (operational), not a generator logic hole on today’s corpus.

---

## Verdict rationale

Generator claim holds under every named value/id attack: mixed glass/liquid liquid-tables become phase-unknown with the printed marker quoted, 13 681 rows and ids conserved, no split. The tip as a landable battery artifact still serves the old `l` stamps from the stale derived store — **LAND-WITH-FIXES** pending regen so store consumers match `generate_table`.

VERDICT: R17 | LAND-WITH-FIXES | P0=0 P1=1 P2=0 P3=1 | ran-tests
