# REVIEW — composition2: review/kems-calibration-extracts @ 8a75858b

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` @ tip (`.seat-busy` cleared after this review)
- **Tip:** `8a75858b3249fea45315273559f7b5277560c886` (parent / calibration LAND `eeded0d0d09798dbf1c5f3b8033b391ef690b712`)
- **Commit:** `Add derived mullite composition to Table 2 points` — only
  `data/literature/extracts/shornikov-1994-mullite-kems.yaml`
  (extracts-v2 sibling deliberately untouched; ids are content hashes; landing regen writes it)
- **Date:** 2026-10-01 ~16:52 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Policy gate:** `POLICY-regolith-main-2026-10-01-use-values-first.md` — **wrong-number / composition-source guards**; lineage completeness not a gate
- **Corpus:** PDF + `.txt`
  `/workspace/ferry-inbox/reviews/_req-2026-10-01/shornikov-1994-mullite-kems.pdf`
  (3 pages = journal 478–480). Formula / sample-prep / Table 2 re-checked via text layer.
- **Prior:** `REVIEW-shornikov-1994-mullite-composition-2026-10-01.md` (LAND `8b1100836` **WITHDRAWN at landing** — composition was at obs/bench level so migrator wrote zero known point-condition compositions). This tip places the same derived 0.6/0.4 on **measured point** `values.points[].point_conditions.composition`.
- **REQ:** `REQ-delta-review-shornikov-1994-composition2-8a75858b-2026-10-01.md` — regenerate; confirm/correct 33/24/9 and 23.5549 Pa; wrong-number guards.

## Attack (1) — composition derived from printed 3Al2O3·2SiO2 on measured/Table-2 points (blocking)

**Paper (p. 478):** title/opening “Mullite (3Al2O3·2SiO2)”; Experimental precursor-method prep; Table 2 “Partial pressures of vapour species (pi) **over mullite**” at 1833–2033 K (all below printed incongruent melting point 2163 ± 4 K).

**Tip:**
- **33** Table 2 partial-pressure rows gain `values.points[0].point_conditions.composition` with `state.tag: value`, `basis: derived_from_formula`, `amount_basis: mole_fraction`, components Al2O3 `'0.6'` / SiO2 `'0.4'`, `inference.relation: formula_stoichiometry_to_mole_fraction`, inputs stating derived-not-printed analysis.
- Observation-level `point_conditions.composition` remains `state.tag: unknown` (same as parent) — migrator reads the **point** block (this was the landing failure mode of withdrawn `8b1100836`).
- 8 Table 6 activity rows: no point-level composition added (still unknown).
- Benches/experiments: **no** composition written there.

**Arithmetic:** 3/5=0.6, 2/5=0.4; wt% sanity 71.8. **PASS.**

## Attack (2) — phase still solid mullite; formula locator = Table 2 condensed sample (blocking)

Phase string unchanged on all rows: `mullite (3Al2O3·2SiO2); phase not specified by the article` — **not** retagged melt. Composition locator page 478 / table `"2"` with note that Table 2 has no compositional analysis; derived from printed formula. **PASS.**

## Attack (3) — no measured value/unit/exponent/condition changed vs parent `eeded0d0d` (blocking)

Structural diff vs parent:
- **Files:** only `data/literature/extracts/shornikov-1994-mullite-kems.yaml` (no extracts-v2).
- **Census:** 41 observations unchanged (33 Table 2 PP + 8 Table 6 activities).
- Stripping `point_conditions.composition` (point-level only addition): JSON-identical to parent on all 41 (pressure_atm / sigma / locators / method_class / derived_from / phase / experiment id).
- `benches`, `experiments`, calibration / orifice / cell / instrument blocks: **byte-equal** to parent LAND tip (calibration facts untouched).
- Targeted `python3 tools/validate_literature_extracts.py … --skip-priority` → **OK**.

**PASS.**

## Attack (4) — REGENERATE derived store; confirm 33 / 24 / 9 and 23.5549 Pa (blocking)

Single-source in-memory lift on this tip (`Migrator._migrate_extract` + `finalize`; no full-corpus write; VPS constraint):

| Claim | Regenerated result |
|---|---|
| Known point-condition composition | **33** / 33 p_partial (8 activities still unknown) |
| Observation/point id changes vs committed store | **33** changed; 8 activity ids unchanged. Al @ 1833 K: `…:h=b88c911ec335` → `…:h=743c0b2be08a` |
| Effusion gate (no engines) | **24** pass via `in_cell_fallback`; **9** refuse at `in_cell_partial_pressure_sum` |
| 2033 K printed in-cell pressure sum | **23.5549046… Pa** ≈ **23.5549 Pa** (limit 10 Pa; `limit_basis`: Drowart standalone usual 10 Pa; no defensible d printed) |

Gate sum at 2033 K uses admitted **measured** partials only (excludes model_derived O / O2): SiO dominates (23.5074 Pa) + Al/AlO/Al2O/AlSiO/MoO2/MoO3. Matches Table 2 printed column after atm→Pa. Temperatures 1833/1933/1983 K sum to ~4.00 / 4.14 / 8.71 Pa (<10) → 24 rows (6+9+9); 2033 K 9 rows refuse.

**PASS — worker numbers confirmed (no correction).**

## Attack (5) — analysed printed composition that should take precedence? (blocking)

Full-text search: no chemical analysis, wt%, or mole-fraction assay of the condensed mullite sample. Only identity as mullite / 3Al2O3·2SiO2 and precursor-method prep. (Prior composition review same finding.) **PASS.**

## Notes (non-blocking per POLICY)

- Obs-level composition left `unknown` is correct for migrator visibility; point-level is what landing reads.
- REQ “33 extracts-v2 observations … known point-condition composition” matches p_partial only; activities intentionally unchanged.
- Lineage on O/O2 author-calc rows untouched (not a gate).

## P1 / P2

- **P1 (wrong-number / composition-source):** none
- **P2:** none

VERDICT: LAND 8a75858b3249fea45315273559f7b5277560c886
