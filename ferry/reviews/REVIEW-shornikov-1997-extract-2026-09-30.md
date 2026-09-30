# REVIEW — review/shornikov-1997-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-r29`
- **Branch tip:** `4910534f4b3d489684a656397ca4c0248f7f6f6d` (on `bba8ef156`)
- **Commit:** `literature: extract Shornikov 1997 vapor pressures` (2 files: extracts + extracts-v2 only; no docs-private)
- **Date:** 2026-09-30 ~19:57 ET
- **Mode:** read-only; extract not edited

## Corpus

- Staged PDF: `/workspace/ferry-inbox/reviews/_req-2026-09-30/shornikov/shornikov-1997-cao-alumina-vapor.pdf`
- Audit text: `…/shornikov/_audit/shornikov-1997.txt`
- PDF SHA-256: `77e3fd13ba6ce30de43945c4d80be1f439de1fa93dd7685f6f7dff91afe69d90` (matches extract `corpus_sha256`)
- Cross-checked against rendered page image of published p. 20 (PDF page index 1) plus `pdftotext -layout`

## Scope checked

Per main brief: 14 printed Table 2 cells (pAl ×10^9 and pAlO ×10^10 atm, 7 interior compositions each), Mo cell, Table 1 ion signals as context only. Judged the extract itself; current `effusion_regime_unverified` refuse is out of scope (owner-approved gate change in progress).

## Coefficient / scaling findings (14/14 match)

Printed Table 2 header: `pAl × 10^9`, `pAlO × 10^10` (atm). Extract keeps printed coefficients and normalizes:

- Al: `pressure_atm = coefficient × 10^-9`
- AlO: `pressure_atm = coefficient × 10^-10`
- v2: `atm_to_Pa` with factor `101325` — all 14 Pa points match `coeff × 10^{-n} × 101325`

| x(CaO) | pAl coef | extract | pAlO coef | extract |
| --- | --- | --- | --- | --- |
| 0.143 | 41.7 | OK | 51.8 | OK |
| 0.333 | 31.7 | OK | 45.0 | OK |
| 0.500 | 13.8 | OK | 20.1 | OK |
| 0.625 | 3.82 | OK | 6.15 | OK |
| 0.632 | 3.29 | OK | 5.50 | OK |
| 0.750 | 1.97 | OK | 3.50 | OK |
| 0.860 | 1.68 | OK | 3.00 | OK |

- Pure endpoints `x=0.000` / `x=1.000` correctly excluded from the admitted Al/AlO points (brief: 7 compositions).
- Calculated `pO` / `pO2` and `pCa` columns not admitted as scoring pressures (correct for this brief).
- Binary Al2O3 complements documented as `1−x(CaO)` (not printed) — values correct for all 7 rows.
- Locators: Table 2 → published_page 20, pdf_page_index 1, table `2`.

## Mo / context handling

- **Mo cell:** benches record `cell_materials: Mo` and `Molybdenum Knudsen cells` from Experimental (published p. 19 / pdf_page_index 0). Matches printed “molybdenum cells” and Table 1 note (condition I in Mo cell). Not inferred solely from MoO2+/MoO3+ ions.
- **Orifice ratio:** “no less than 1:400 (effusion-hole cross-section to vaporization area)” — as printed.
- **Table 1:** Al 0.12, AlO 0.026, Al2O 0.010 under Condition I (`x(CaO)=0.86`, 1933 K, 70 eV) live in `species.*.context` / fidelity samples only; Al2O has `observations: []`. Explicitly not treated as partial pressures.
- **Figs 1–2:** figure_only / pending — not digitized.

## Other checks

- `extract_merge.load_extracts(..., require_valid=True)` loads this file alone.
- `validate_literature_extracts.check_all_fidelity_samples_match` → PASS (8 samples).
- Tip tree is extracts-only (no docs-private in history of this commit).

## Non-blocking notes (not P1/P2)

- Component mole-fraction strings sometimes normalize (`0.500`→`0.5`, `0.750`→`0.75`) while `CaO_mole_fraction_as_printed` keeps the printed form — values identical.
- Today’s battery score may still refuse `effusion_regime_unverified`; do not treat that as an extract defect.

## Verdict

VERDICT: LAND 4910534f4b3d489684a656397ca4c0248f7f6f6d

— regolith-empirical
