VERDICT: REVISE

Tip named: `c6a78cb365d8b8d379fc9e4e271143fceee1585b` on `review/shornikov-1997-extract`.

# REVIEW — Shornikov 1997 extract delta (+ measured p(Ca))

- **Reviewer:** regolith-empirical (frontier of record)
- **Date:** 2026-09-30 ~21:30 ET
- **Dispatch:** `regolith-main-to-empirical-0930e` / `4fbdd024-72e0-499b-a944-79a00cc3f0f2`
- **Tip:** `c6a78cb365d8b8d379fc9e4e271143fceee1585b` (`literature: extract Shornikov 1997 calcium pressure`, author/commit 2026-09-30 21:24 ET)
- **Parent:** `4910534f4b3d489684a656397ca4c0248f7f6f6d` (prior LAND, `REVIEW-shornikov-1997-extract-2026-09-30.md`)
- **Delta:** 1 file, `data/literature/extracts/shornikov-1997-cao-alumina-vapor.yaml` (+102 / −1). No docs-private. Read from the git object store; no worktree checkout.
- **Mode:** read-only. Extract not edited.

## Page check

**Image-checked.** Staged PDF is on this machine:

`/workspace/ferry-inbox/reviews/_req-2026-09-30/shornikov/shornikov-1997-cao-alumina-vapor.pdf`

SHA-256 `77e3fd13ba6ce30de43945c4d80be1f439de1fa93dd7685f6f7dff91afe69d90` matches extract `corpus_sha256`. Table 2 was checked on the rendered page image (`pdftoppm` of PDF page 2, published p. 20, `pdf_page_index` 1) and against `pdftotext -layout`. Printed cells below were read from that page, not invented.

## What the delta stores

New observation `species.Ca.observations[shornikov_1997_ca_pressure_table2_1933k]`, type `partial_pressure`, `method_class: measured_direct`, `admission_status: admitted`, `gas_species: Ca(g)`, T = 1933 K, scale `pCa × 10^9`, `pressure_atm = printed coefficient × 10^-9`. No `derived_from` (correct: nothing in the new block is `model_derived`).

Eight points. `x(CaO)=1.000` is included. Pure alumina `x(CaO)=0.000` stays out (printed pCa cell is an em-dash). Calculated `p(O)` / `p(O2)` columns were not added. The extraction-method sentence and the observation note both say those columns are author-calculated from equilibria (1)–(3) and are excluded. That exclusion is by design, not a defect.

| x(CaO) | printed pCa × 10^9 | extract coefficient | pressure_atm | Al2O3 = 1−x |
| --- | --- | --- | --- | --- |
| 0.143 | 5.26 | 5.26 | 5.26e-09 | 0.857 |
| 0.333 | 14.4 | 14.4 | 1.44e-08 | 0.667 |
| 0.500 | 103 | 103 | 1.03e-07 | 0.500 |
| 0.625 | 576 | 576 | 5.76e-07 | 0.375 |
| 0.632 | 578 | 578 | 5.78e-07 | 0.368 |
| 0.750 | 880 | 880 | 8.8e-07 | 0.250 |
| 0.860 | 850 | 850 | 8.5e-07 | 0.140 |
| 1.000 | 782 | 782 | 7.82e-07 | 0.000 |

All eight coefficients match the page image and `pdftotext`. All eight `pressure_atm` values are coefficient × 10^-9. Header on the page is `pCa × 10^9` (atm), same scale as the already-landed pAl column, not the pAlO × 10^10 scale.

One new fidelity sample: `points[0]` printed coefficient `5.26` at x(CaO)=0.143. `validate_literature_extracts.py --skip-priority --check-fidelity-match` on this blob only: `OK: 1 extract file(s) valid`.

## Apparatus (what this commit actually stores)

On the new Ca row, inline equipment objects (value + locator, same shape as the landed Al/AlO rows; not an equipment foreign key):

- `orifice_to_sample_area_ratio`: `no less than 1:400 (effusion-hole cross-section to vaporization area)` — published p. 19 / pdf_page_index 0. Matches Experimental: “ratio of the effusion hole cross section to the vaporization area of no less than 1 : 400.”
- `calibration`: `Silver vapor used as the reference for comparison of ion currents` — published p. 20 / pdf_page_index 1. Matches “The silver vapor was used as a reference.” `reference_as_printed: silver vapor`.

MI-201 and molybdenum cells are **not new in this commit**. They remain on the parent bench `shornikov-1997-mo-kems` (`apparatus_family`: `MI-201 mass spectrometer with Knudsen cells`; `cell_materials`: `Mo`; `cell_material_and_liner`: `Molybdenum Knudsen cells`), and the Ca row points at the existing experiment `shornikov-1997-mo-cell-kems` (`bench_id: shornikov-1997-mo-kems`). No equipment id was invented from the extract side. Printed Experimental text is “MI-201 mass spectrometer” and “molybdenum cells.”

## d-032

The new row is a measured partial pressure (comparison of ion currents, silver reference). It belongs in `observations`, not `context`. Table 1 ion signals stay in `context` (unchanged). No context row was moved. Ionization cross sections “calculated by the additivity rule” are a method note on the measured pressures, not a `model_derived` quantity and not `derived_from`.

Prior Al/AlO interior cells were not re-opened. They are outside this delta.

## P0 / P1 / P2

- **P0:** none. The eight new pressures match the page, and they are not in the store the battery scores (see P1), so no wrong number reaches a result, score, or ledger at this tip. The previously landed Al/AlO rows in extracts-v2 are byte-identical to `4910534f4`.
- **P1:** derived store not regenerated. `data/literature/extracts-v2/shornikov-1997-cao-alumina-vapor.yaml` is still blob `cef8daca895922ae6bd4cf065fb1a869ea769d4c`, same as the parent. That file has no `formula: Ca` partial-pressure rows (only `formula: CaO`). `scripts/check_store_freshness.py --head c6a78cb365d8b8d379fc9e4e271143fceee1585b` exits 1: `STALE`, last store touch `4910534f4`, later commit `c6a78cb36` touched `data/literature/extracts/shornikov-1997-cao-alumina-vapor.yaml` with no store regen. The eight admitted p(Ca) rows therefore do not reach `extracts-v2` / the battery at this tip. Parent LAND committed the v2 twin with the extract; this delta does not.
- **P2:** none.

Calculated p(O) / p(O2) staying out is not a finding.

## Required fix

Regenerate the derived store so this source’s extracts-v2 sibling contains the eight measured `Ca(g)` partial pressures (and still does not contain the calculated p(O) / p(O2) columns), then re-run `check_store_freshness.py` on the new tip. Do not retype the Ca row as context and do not add an equipment foreign key.

— regolith-empirical
