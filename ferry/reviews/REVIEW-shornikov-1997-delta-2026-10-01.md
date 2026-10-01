VERDICT: LAND bc96eafe222ed671fd7617480d720a4030d9131c

# REVIEW — Shornikov 1997 delta (derived-store regen for measured p(Ca))

- **Reviewer:** regolith-empirical (frontier of record)
- **Date:** 2026-10-01 ~08:20 ET
- **REQ:** `REQ-delta-review-shornikov-1997-bc96eafe-2026-10-01.md`
- **Tip:** `bc96eafe222ed671fd7617480d720a4030d9131c` (`literature: regenerate Shornikov 1997 calcium store`, author/commit 2026-10-01 08:09 ET)
- **Parent:** `c6a78cb365d8b8d379fc9e4e271143fceee1585b` (prior REVISE for P1 stale store; `REVIEW-shornikov-1997-extract-c6a78-2026-09-30.md`)
- **Seat:** `/workspace/repos/wt/slot-n1` (checked out at tip; read-only on extracts)
- **Delta:** 5 files — `data/literature/extracts-v2/shornikov-1997-cao-alumina-vapor.yaml` (+1200), `data/battery/migration-queue.yaml`, `data/battery/migration-report.md`, `data/literature/works/14a28aff…yaml`, `data/literature/works/ALIASES.yaml`. **v1 extract not touched.** No docs-private.

## Prior P1 (closed)

At parent tip the extracts-v2 sibling was still blob of LAND `4910534f4` (no `formula: Ca` p_partial rows); `check_store_freshness.py --head c6a78cb36` → STALE. This commit regenerates this source’s derived output only.

## Attack results

### 1. Eight admitted Ca `p_partial` rows (PASS)

extracts-v2 at tip: 25 observations total; **8** with `identity.species.formula: Ca`, `identity.quantity: p_partial`, `admission.status: admitted`, `evidence.class: measured_direct`, T=1933 K. `derivation.relation: atm_to_Pa`. **0** O / O2 observations; **0** `model_derived`.

| x(CaO) | printed coef ×10^9 | Pa (= coef × 10^-9 × 101325) | match |
| --- | --- | --- | --- |
| 0.143 | 5.26 | 0.0005329695 | OK |
| 0.333 | 14.4 | 0.00145908 | OK |
| 0.500 | 103 | 0.010436475 | OK |
| 0.625 | 576 | 0.0583632 | OK |
| 0.632 | 578 | 0.05856585 | OK |
| 0.750 | 880 | 0.089166 | OK |
| 0.860 | 850 | 0.08612625 | OK |
| 1.000 | 782 | 0.07923615 | OK |

Composition components agree with printed x(CaO) / (1−x) Al2O3. Locator notes carry the printed coefficients.

**PDF sanity:** staged PDF SHA-256 `77e3fd13ba6ce30de43945c4d80be1f439de1fa93dd7685f6f7dff91afe69d90`; Table 2 (`pdftotext -layout` page 2 / published p. 20) prints the same eight pCa ×10^9 cells. Calculated pO / pO2 columns still not admitted.

### 2. Al/AlO blocks byte-identical (PASS)

Parent extracts-v2 blob == LAND `4910534f4` blob. Tip vs parent: **14/14** Al+AlO observation objects deep-equal (same `observation_id` set; 7 Al + 7 AlO). Formula census tip: Al2O3×2, Al×7, AlO×7, Ca×8, CaO×1 (parent identical except no Ca).

### 3. Store freshness (PASS)

```
python3 scripts/check_store_freshness.py --head bc96eafe222ed671fd7617480d720a4030d9131c
→ OK: store last touched at bc96eafe2 …; no later migrate-input commits, every extract has a store trace
EXIT:0
```

### 4. Other sources / hard-issue census (PASS)

- Only extracts-v2 path changed under `data/literature/extracts-v2/`: this shornikov file.
- `migration-report.md`: **hard issues: 3180** unchanged (header + Hard issue census). Records-out 109982 → 110007 (+25 = this source’s observation count newly present in the report table). No other source’s extracts-v2 observation payload moved.

## P0 / P1 / P2

- **P0:** none. Eight Ca Pa values match printed coefficients × 10^-9 × 101325; Al/AlO unchanged; no calculated O/O2 admitted.
- **P1:** none. Prior stale-store P1 closed.
- **P2:** none.

## d-032

Measured p(Ca) stays in observations (admitted, measured_direct). No equipment FK invented from the extract side; experiment still points at existing `shornikov-1997-mo-cell-kems`. Context / Table 1 ion signals not reopened.

## Verdict

**VERDICT: LAND bc96eafe222ed671fd7617480d720a4030d9131c**

— regolith-empirical
