# N5 NEW EXTRACT — slag activity (BACKLOG 3)

**Branch:** `empirical/n5-slag-activity-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d`
**Tip SHA:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (no new extract commit — all three STEP 0 STOP)
**Worktree:** `/workspace/repos/wt/slot-06`
**Date:** 2026-09-22 (America/Toronto)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/` (private; not committed)

## Validate

Existing alternate-name extracts (already on base) re-checked:

```
OK: 1 extract file(s) valid   # slag-001-banya-1993.yaml
OK: 1 extract file(s) valid   # slag-002-banya-hino-nagasaka-1993.yaml
OK: 1 extract file(s) valid   # slag-003-hino-kitagawa-banya-1993.yaml
```

0 new errors. Validator via `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py <file>`.

No new extract files written under corpus dir names (STEP 0 STOP).

## Per-paper status

### 1. banya-1993-quadratic-formalism-slag-metal — REFUSED (STEP 0: extract exists)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | Hit: `data/literature/extracts/slag-001-banya-1993.yaml` (also extracts-v2 + INDEX) |
| STEP 0 DOI | `10.2355/isijinternational.33.2` — same DOI on existing extract |
| PDF ↔ sidecar | Match. PDF p1: Ban-Ya, *Mathematical Expression of Slag-Metal Reactions… Quadratic Formalism…*, ISIJ Int. 33 (1993) pp. 2–11. sha256 `a73b22cb…3198f4` = sidecar |
| Existing extract | `source_id: slag-001-banya-1993`; `corpus_source_id: banya-1993-quadratic-formalism-slag-metal`; landed 2026-09-07 (`ad13e2da1`) |

**No new extract.** Do not duplicate under corpus stem.

### 2. banya-hino-nagasaka-1993-hydroxyl — REFUSED (STEP 0: extract exists)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | Hit: `data/literature/extracts/slag-002-banya-hino-nagasaka-1993.yaml` |
| STEP 0 DOI | `10.2355/isijinternational.33.12` — same DOI on existing extract |
| PDF ↔ sidecar | Match. PDF p1: Ban-Ya, Hino, Nagasaka, *Estimation of Water Vapor Solubility in Molten Silicates…*, ISIJ Int. 33 (1993) pp. 12–19. sha256 `b70ddca6…1a20c0f` = sidecar |
| Existing extract | `source_id: slag-002-banya-hino-nagasaka-1993`; `corpus_source_id: banya-hino-nagasaka-1993-hydroxyl`; landed 2026-09-07 (`b31404f2f`) |

**No new extract.**

### 3. hino-kitagawa-banya-1993-sulphide — REFUSED (STEP 0: extract exists)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | Hit: `data/literature/extracts/slag-003-hino-kitagawa-banya-1993.yaml` |
| STEP 0 DOI | `10.2355/isijinternational.33.36` — same DOI on existing extract |
| PDF ↔ sidecar | Match. PDF p1: Hino, Kitagawa, Ban-Ya, *Sulphide Capacities of CaO-Al2O3-MgO and CaO-Al2O3-SiO2 Slags*, ISIJ Int. 33 (1993) pp. 36–42. sha256 `2d860f4f…fc0e573` = sidecar |
| Existing extract | `source_id: slag-003-hino-kitagawa-banya-1993`; `corpus_source_id: hino-kitagawa-banya-1993-sulphide`; landed 2026-09-07 (`5ed72c6b5`) |

**No new extract.**

## Refusals summary

| Paper (corpus dir) | Reason |
| --- | --- |
| banya-1993-quadratic-formalism-slag-metal | STEP 0 — already extracted as `slag-001-banya-1993` (same DOI / corpus_source_id) |
| banya-hino-nagasaka-1993-hydroxyl | STEP 0 — already extracted as `slag-002-banya-hino-nagasaka-1993` |
| hino-kitagawa-banya-1993-sulphide | STEP 0 — already extracted as `slag-003-hino-kitagawa-banya-1993` |

## Commit

None. Branch tip remains base `2e9e17c3d`. Private PDFs not committed. No duplicate corpus-stem YAML.

## Bookkeeping note (not fixed this lane)

`SOURCE_STATUS.yaml` still lists the three **corpus** `source_id`s as inbox / in_progress with reason “no extract”, while the live extracts and INDEX use the `slag-00*` ids with `corpus_source_id` aliases. Owner may want a STATUS remap / alias close-out later; N5 does not invent a second extract file.

## Optional follow-ups (not done)

- Remap / alias corpus SOURCE_STATUS rows → `slag-001` / `slag-002` / `slag-003`.
- migrate + readiness / engine_point for the existing slag extracts once I1 is green (optional backlog item).
