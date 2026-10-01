# REVIEW — delta2 re-review: review/stolyarova-1995-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z2` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `4ccee2108a0062abeebe4502c67e912ed567af25` (parent `b64efca6fffae933da35d5070f7fa09a4218571d`)
- **Commit:** `Fix comma-split Stolyarova flow mapping notes` — only
  `data/literature/extracts/stolyarova-1995-cao-alumina-kems.yaml` and
  `data/literature/extracts-v2/stolyarova-1995-cao-alumina-kems.yaml`
- **Date:** 2026-10-01 ~11:58 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Corpus:** prior kit `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1995/` (not re-digitized; quote-only delta)
- **Prior:** `REVIEW-stolyarova-1995-delta-2026-10-01.md` (LAND `b64efca6f…` TEST-ONLY pin)
- **REQ:** `REQ-delta2-review-stolyarova-1995-4ccee210-2026-10-01.md` — wrong-number / phantom-key check only; sibling regen judgment

## Diff scope

`git diff b64efca6f 4ccee2108` → **2 files**, note-only:

| file | numstat |
| --- | --- |
| `data/literature/extracts/stolyarova-1995-cao-alumina-kems.yaml` | 52 / 52 |
| `data/literature/extracts-v2/stolyarova-1995-cao-alumina-kems.yaml` | 50 / 50 |

- Extracts (v1): **50** flow-mapping `note: Printed x_CaO; x_Al2O3 is the binary complement, not separately printed.` → quoted; **2** `note: Present-study column, row “Material of effusion cell”.` → quoted. Zero non-note hunk lines.
- Extracts-v2: **50** block-style composition locator notes restored from truncated `…complement` → full `…complement, not separately printed.` (no quotes required in block style). Zero non-note hunk lines.
- REQ “104” matches the ± line count on extracts (52+52); unique unquoted flow offenders fixed = **52** (50+2).

## Attack (1) — no phantom keys remain (blocking)

Loaded both tips with repo `simulator.battery.migrate.load_yaml`.

| file | phantom null-prose keys (prior → tip) |
| --- | --- |
| extracts | **51 → 0** (50× `not separately printed.` + 1× `row “Material of effusion cell”.`) |
| extracts-v2 | 0 → 0 (block-style; never phantom-split) |

Targeted flow-mapping comma guard (same walker as `tests/test_yaml_flow_scalar_commas.py`) on tip extracts only → **0 offenders**. Full-store discovery of that test not run (261 extracts; VPS constraint).

**PASS.**

## Attack (2) — full note text loads (blocking)

| note | tip loads |
| --- | --- |
| `Printed x_CaO; x_Al2O3 is the binary complement, not separately printed.` | v1 **50**/50; v2 **50**/50 (prior v1 loaded truncated `…complement` only; prior v2 stored truncated) |
| `Present-study column, row “Material of effusion cell”.` | v1 **2**/2 quoted (benches + Ca equipment locator) |

**PASS.**

## Attack (3) — measured value / unit / exponent / condition / observation id identity (blocking)

Compare loaded structures tip vs `b64efca6f` after (a) stripping phantom null-prose keys and (b) masking `note` fields:

| file | phantom-stripped + note-masked deep-diff | fingerprints (ids/values/units/conditions sans notes) |
| --- | --- | --- |
| extracts | **0** | equal (62 fingerprints) |
| extracts-v2 | **0** | equal (56 obs; oid order identical) |

V1 observation_id list unchanged (12). V2 observation_id list unchanged (56). Raw diff non-note hunks: **0**.

**PASS — values identical; only notes (and removal of note-split phantoms) changed.**

## Attack (4) — extracts-v2 sibling vs regen (supporting)

Worker hand-edited v2 (local migration stalled) rather than regenerating.

- Full `scripts/battery_migrate.py` **not run** here (rewrites whole store across 261 extracts; VPS / seat constraint).
- Structural parity: tip v2 vs prior v2 with notes masked → **identical**; same 56 oids, same keys per observation (`admission`, `derivation`, `evidence`, `experiment_id`, `identity`, `locator`, `notices`, `observation_id`, `point_conditions`, `read_from`, `source_id`, `uncertainty`, `value`); value/unit/evidence/experiment_id fields unchanged.
- Note text: all 50 composition locator notes now match the fixed v1 string exactly. That is what a regen from the quoted v1 would carry for these rows (prior v2 truncation was the broken loaded-v1 artifact). Material/bench notes live on v1 benches/equipment (and works), not in this extracts-v2 observation file — correct that v2 only restored the 50 Printed notes.
- Dump formatting may differ under a real migrator YAML dump; **landing regen will overwrite either way.**

**Sibling judgment: faithful** (structural + note text match what regen from fixed extracts would produce). Inconclusive only on byte-identical dump formatting.

## Attack (5) — scope (supporting)

- Diff confined to this source’s two YAML files.
- No other sources, tests, or docs touched.
- Review branch not pushed to green/main.

## P0 / P1 / P2

- **P0:** none
- **P1 (wrong-number / phantom):** none — phantoms cleared; values identical
- **P2:** none blocking. Non-blocking: hand-edited v2 is regen-faithful on content; landing regen still authoritative for dump bytes.

VERDICT: LAND 4ccee2108a0062abeebe4502c67e912ed567af25

— regolith-empirical
