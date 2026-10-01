# REVIEW — delta2 re-review: review/stolyarova-1996-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z15` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `6bef95098ef28af2b43332082b48e11a445efc36` (parent `9e79f3fa1ea1e8de22fe25143a7a3ddd87041b97`)
- **Commit:** `Correct Stolyarova 1996 extract lineage and cell` — extract YAMLs plus regenerated works sidecar
  `data/literature/extracts/stolyarova-1996-cao-alumina-silica-kems.yaml`,
  `data/literature/extracts-v2/stolyarova-1996-cao-alumina-silica-kems.yaml`,
  `data/literature/works/46e00f6d80bff6f90992354a3203171b972f08e22cc679d8d1b207e5ea97d10f.yaml`
- **Date:** 2026-10-01 ~10:54 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Policy gate:** `POLICY-regolith-main-2026-10-01-use-values-first.md` — **wrong-number guards only**; incomplete `derived_from` / derivation lineage on model_derived rows is **not blocking**. OWNER RULING d-053 for inferred Mo cell.
- **Corpus:** `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1996/` (PDF + MinerU OCR + sidecar). Prior page-check of Table 1 (journal p. 19) and Eqs (7)–(9) (pp. 18–19) reused; no new digitize.
- **Prior:** `REVIEW-stolyarova-1996-delta-2026-10-01.md` (REVISE — false Table-1 parents on p_O)
- **REQ:** `REQ-delta2-review-stolyarova-1996-6bef9509-2026-10-01.md` — check only wrong-number guards; lineage completeness not a gate

## Attack (1) — cell material as inference (blocking under POLICY / d-053)

| claim | tip recording | result |
| --- | --- | --- |
| Cell typed Mo | v1 `benches[].cell_materials[0].state.value: Mo` with `inferred: true` | **PASS** |
| Not printed_direct | `cell_material_and_liner` remains `unknown` / `not_published`; Mo lives only on inferred `cell_materials` | **PASS** |
| Evidence + locators | inference text cites Mo+/MoO+/MoO2+/MoO3+ ions (p. 18) and MoO3/MoO2 oxygen derivation (p. 19); locator page 18 / published_page 18 / pdf_page_index 3 / Results | **PASS** |
| Works `Located.inference` | works sidecar `cell_materials[0].inference.relation: extract_inference` with `inferred=true` + same evidence string | **PASS** |

Not recorded as a printed cell. Matches OWNER RULING d-053.

## Attack (2) — O blocks model_derived, no false parents (blocking)

| block | tip evidence / method_class | `derived_from` | vs prior (9e79f3fa1) |
| --- | --- | --- | --- |
| p_O (30 pts) | `model_derived` / `model_derived` | **absent** (empty) | prior: all six Table-1 Mo ion IDs — **removed** |
| p′_O (10 pts) | `model_derived` / `model_derived` | **absent** | prior: p_Al + p_AlO — **removed** |
| p″_O (15 pts) | `model_derived` / `model_derived` | **absent** | prior: p_SiO + p_SiO2 — **removed** |

v2: all 55 O exploded points keep `evidence.class: model_derived`; prior 55 `derived_from` → tip **0**; no Mo-ion parent pointers remain. Empty parents OK under POLICY ("never write false parents"; lineage hard issue may stand). **PASS.**

Note (non-blocking): v1 `method_as_printed` for Eq. (7) now uses `σ(MoO3)` in the denominator (prior copied MinerU `σ(MoO2)` typo). Documentation string only; no measured value moved.

## Attack (3) — no measured value / unit / exponent / condition change (blocking)

- v1: all species `values.points` tip == prior `9e79f3fa1` == base `e311b68b2` for O blocks (30/10/15); full species point scan tip vs prior → **0 diffs**.
- Six Mo ion intensities tip == prior: MoO2+ 0.25 / 7.3 / 0.017; MoO3+ 0.05 / 1.4 / 0.003; still `measured_direct` / `measured_direct`.
- v2: 312/312 observation IDs stable; numeric value dig tip vs prior → **0 changes**.

**Values: PASS.**

## Attack (4) — six Mo ion intensities = one spectrum, three normalisations (supporting)

Locator / point_conditions notes on all six now state: Table 1 printed group header `CaO · Al2O3 · SiO2`: one mass spectrum of that melt at 1933 K shown in three normalisations; columns unlabeled; this is column N. Still stored as measured ion intensities (do not re-litigate). **PASS.**

## Attack (5) — scope (supporting)

- Diff vs `9e79f3fa1`: the two Stolyarova extract YAMLs + new works sidecar for this source only (cell inference / migrate). No other extract sources touched.
- Census unchanged: v1 25 obs bundles; v2 312 exploded observations.
- Full W3 / hard-issue global recount not run (VPS constraint). Lineage completeness deliberately not re-gated.

## Notes (non-blocking per POLICY)

- Incomplete `derived_from` on the three O blocks stands by design after false-parent removal; not a REVISE gate.
- REQ worker gate note accepted: cell-material 108/108 via inferred Mo; effusion-regime 0/108 (`effusion_regime_unverified`, background P unstated) — not a wrong-number verdict gate here.
- Works yaml is new on this tip (was absent at prior delta); carries the same inferred-Mo recording. Scope expansion beyond the two extract paths is cell-typing support, not invented extract numbers.

## P0 / P1 / P2

- **P0:** none
- **P1 (wrong-number):** none
- **P2 (false parents):** cleared vs delta — Table-1 Mo parents removed from p_O; no substitute false parents

VERDICT: LAND 6bef95098ef28af2b43332082b48e11a445efc36
