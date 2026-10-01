# REVIEW — delta3 re-review: review/shornikov-1994-mullite-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `ed6ebc9144d0529f9ed6908929cb0d92b955b4cd` (parent `0b4368673add9c0ac1807c0af79b1c6690059482`)
- **Commit:** `Correct Shornikov oxygen lineage direction` — only
  `data/literature/extracts/shornikov-1994-mullite-kems.yaml` and
  `data/literature/extracts-v2/shornikov-1994-mullite-kems.yaml`
- **Date:** 2026-10-01 ~10:25 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Policy gate:** `POLICY-regolith-main-2026-10-01-use-values-first.md` — **wrong-number guards only**; incomplete `derived_from` / derivation lineage on model_derived rows is **not blocking**
- **Corpus:** PDF
  `/workspace/ferry-inbox/reviews/_req-2026-10-01/shornikov-1994-mullite-kems.pdf`
  (3 pages = journal 478–480). Table 2 Mo/O2 coefficients re-checked via `pdftotext` p. 478; p. 479 oxygen-calculation prose confirmed.
- **Prior:** `REVIEW-shornikov-1994-mullite-delta2-2026-10-01.md` (REVISE — O2-as-parent of p_O circularity)
- **REQ:** `REQ-delta3-review-shornikov-1994-ed6ebc91-2026-10-01.md` — check only wrong-number guards; lineage completeness not a gate

## Attack (1) — measured vs calculated tags (blocking under POLICY)

| row set | expected (paper) | tip tag | result |
| --- | --- | --- | --- |
| 4× MoO₂ | measured (Table 1 MoO₂⁺; ion-current comparison) | v1 `method_class: measured`; v2 `measured_direct` / `measured` | **PASS** |
| 4× MoO₃ | measured (Table 1 MoO₃⁺) | same | **PASS** |
| 4× O₂ | calculated (p. 479; no O₂⁺ in Table 1) | v1 `method_class: derived` / author-equilibrium-calculation; v2 `model_derived` / `derived` (was `measured_direct` on delta2) | **PASS** |
| 4× p_O | calculated via (1) | remains `model_derived` / `derived` | **PASS** |

Delta2 P2 “retag four O2 obs as model_derived” is applied. No calculated↔measured inversion remains on these rows.

## Attack (2) — printed Mo/O2 values vs Table 2 p. 478 (blocking)

Page text (PDF page 1 = journal 478) Table 2 coefficients and exponents match the extract notes and Pa conversions (× 101325) exactly; unchanged from delta2:

| species | scale | 1833 K | 1933 K | 1983 K | 2033 K |
| --- | --- | --- | --- | --- | --- |
| p_MoO₂ | × 10⁻⁸ atm | 0.526 ± 0.050 | 1.91 ± 0.15 | 3.40 ± 0.25 | 5.78 ± 0.30 |
| p_MoO₃ | × 10⁻⁹ atm | 1.24 ± 0.08 | 4.22 ± 0.25 | 7.64 ± 0.40 | 12.4 ± 0.6 |
| p_O₂ | × 10⁻¹¹ atm | 0.521 ± 0.050 | 4.59 ± 0.26 | 13.7 ± 0.6 | 34.3 ± 1.7 |

All twelve MoO₂/MoO₃/O₂ printed coefficients, exponents, and atm→Pa cells match. **Values: PASS.** Nothing else numeric moved in this tip.

## Attack (3) — false-parent / direction vs paper (blocking as wrong substance of derivation inputs)

**Paper (p. 479):** p_O from MoO₃ ⇌ MoO₂ + O **(1)**; p_O₂ from O₂ ⇌ 2O **(2)** once p_O is known. Al/Si (3)–(6) ≈15% check only.

**Tip:**
- All four `p_O` `derived_from` = same-T MoO2 + MoO3 only (**O2 dropped**).
- All four O2 `derived_from` = same-T p_O (child via (2)).
- v1 `derivation.relation` on p_O: “(1) … reaction (2) is used downstream to calculate p_O2, **not as an input to p_O**.”
- Provenance `oxygen_pressure_corroboration` rewritten: p_O from (1), then p_O2 from (2); (3)–(6) consistency check.
- Leftover v1 `method_as_printed` / `calculation_method` citing (1)–(6) cleaned to (1)/(2) correctly.
- Lineage graph over all 41 observations: **no cycle**.

Delta2 P2 ancestry direction is fixed. Under POLICY this clears the prior blocking wrong-parent / measured-tag failure; remaining “lineage completeness” questions (if any) are notes only.

## Attack (4) — scope / census / cell (supporting)

- Diff touches **only** this source’s two YAML files.
- Census stays **41** observations (unchanged vs delta2).
- Cell still `shornikov-1994-mullite-mo-cell` (Mo Knudsen, 400:1); no new apparatus claims.
- Targeted `python3 tools/validate_literature_extracts.py data/literature/extracts/shornikov-1994-mullite-kems.yaml --skip-priority` → **OK**.
- Hard-issue class set / global count 3180 not re-audited on VPS (constraint); REQ claim of unchanged 3180 accepted as non-gate for this wrong-number review.

## Notes (non-blocking per POLICY)

- v2 p_O / O2 rows still carry `derivation.relation: atm_to_Pa` for the unit conversion edge; author-calc direction lives in `derived_from`, provenance corroboration, and (on v1) the explicit relation / method_as_printed strings. That split is documentation shape, not a wrong number.
- Incomplete secondary lineage details on model_derived rows are **not** a REVISE gate under today’s owner ruling.

## P1 / P2

- **P1 (wrong-number):** none
- **P2 (blocking ancestry):** cleared vs delta2 — O2 no longer parent of p_O; O2 not tagged measured

VERDICT: LAND ed6ebc9144d0529f9ed6908929cb0d92b955b4cd
