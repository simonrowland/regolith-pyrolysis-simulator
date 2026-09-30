# REVIEW — review/stolyarova-1995-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z2`
- **Branch tip:** `dacb200ffa064c23ac6ca185e6e2860facd2f24d` (matches requested SHA; parent `bba8ef156`)
- **Commit:** `Add Stolyarova 1995 KEMS extract` (2 files: extracts + extracts-v2 only; no docs-private)
- **Date:** 2026-09-30 ~19:58 ET
- **Mode:** read-only; extract not edited

## Corpus

- Staged PDF: `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1995/stolyarova-1995-cao-alumina-kems.pdf`
- OCR: `…/ocr-mineru/full/vlm/` (images/ ~35) + `_audit/stolyarova-1995-cao-alumina-kems.md`
- Spot-checked against rendered page image of published p. 687 (PDF page index 1 / `page-2.png`) and MinerU Table 2 crop `8f4e1321…jpg`, not OCR alone
- Sidecar SHA-256: `0e6d16b9df233803dbbd2fc795f5e377185f268ea4b1e8e43515bcfa9b81be22`

## Scope checked

Per main brief: 54 Table 2 pressure cells (9 compositions × 6 pressure columns), printed Mo cell, area ratio 100:1 recorded as 0.01, 50 numeric pressures admitted, hard issues unchanged. Spot-check ≥15 cells against page images; confirm 0.01 is effusion-to-evaporation **AREA** ratio (not orifice diameter); confirm Mo is printed, not inferred.

## Census (54 / 50 / 9)

| Claim | Result |
| --- | --- |
| 9 compositions | OK — x(CaO) = 0.000, 0.143, 0.333, 0.500, 0.625, 0.632, 0.750, 0.860, 1.000 |
| 54 Table 2 pressure cells | OK — 9×6 |
| 50 numeric admitted | OK — extract holds exactly those 50 printed coefficients |
| 4 em dashes | OK — remain **absent** (never zero): p_Ca @ 0.000; p_Al, p_AlO, p_O Eqn 2 @ 1.000 |
| Scale headings | OK on page image — p_Ca ×10^9, p_Al ×10^9, p_AlO ×10^10, p_O ×10^9 (Eqn 1 & 2), p_O2 ×10^11 (atm) |
| pressure_atm | OK — coeff × 10^{-n} for sampled points |

Full grid compare extract ↔ page-image/OCR ground truth: **50/50 numeric match, 0 mismatches**.

## Spot-check (≥15 cells vs page image)

Checked on rendered p. 687 / MinerU Table 2 image (coefficients as printed):

| x(CaO) | column | printed | extract |
| --- | --- | --- | --- |
| 0.000 | p_Al | 45.7 | OK |
| 0.000 | p_AlO | 55.1 | OK |
| 0.000 | p_O Eqn1 | 2.11 | OK |
| 0.000 | p_O Eqn2 | 2.23 | OK |
| 0.000 | p_O2 | 2.91 | OK |
| 0.143 | p_Ca | 5.26 | OK |
| 0.333 | p_AlO | 45.0 | OK |
| 0.500 | p_Ca | 103 | OK |
| 0.500 | p_O2 | 4.53 | OK |
| 0.625 | p_O Eqn1 | 2.89 | OK |
| 0.632 | p_Ca | 578 | OK |
| 0.750 | p_Ca | 880 | OK |
| 0.750 | p_Al | 1.97 | OK |
| 0.860 | p_O Eqn2 | 3.30 | OK (stored 3.3) |
| 1.000 | p_Ca | 782 | OK |
| 1.000 | p_O Eqn1 | 3.51 | OK |
| 1.000 | p_O2 | 8.07 | OK |
| 0.000 | p_Ca | — (dash) | absent OK |
| 1.000 | p_Al / p_AlO / p_O Eqn2 | — | absent OK |

Also: fidelity_samples 8/8 resolve to declared values.

## Area-ratio labelling

Printed Experimental (p. 687): *“molybdenum effusion cells with a ratio of the vaporization area to the area of the effusion orifice of 100:1.”*

Extract bench geometry:

- field: `orifice_to_sample_area_ratio` = `0.01`
- note explicitly: printed vaporization-area/orifice-area = 100:1; typed value is the reciprocal orifice/sample = 1:100 = 0.01

**PASS** — labelled as an **AREA** ratio (orifice-to-sample / effusion-to-evaporation), not an orifice diameter.

## Mo cell

- Table 1 present-study column, row “Material of effusion cell”: **Mo** (printed on page image)
- Experimental prose: “molybdenum effusion cells”
- Bench: `cell_materials: Mo`; `cell_material_and_liner: molybdenum effusion cell; no liner stated`
- Ca observation equipment: `Mo` with Table 1 locator

**PASS** — printed Mo, not inferred from Mo+/MoO2+/MoO3+ ion rows alone.

## Derived O / O2

p_O (Eqn 1 & 2) and p_O2 carry `method_class` / `evidence_class: model_derived` plus `derived_from` / `derivation` pointing at the measured Ca/Al/AlO series. Not typed as measured pressures. Matches table headers “Calculation using Eqn 1/2”.

## Hard issues

Extract-level: the four printed dashes stay absent (never filled as 0); no invented pressure cells; Mo and area ratio typed from printed text. Consistent with main’s “hard issues unchanged” claim for this tip. Store-wide hard-issue count not re-run on this VPS (targeted review only).

## Other checks

- Tip tree is extracts-only (no docs-private in this commit).
- Figures 1–4 retained as figure_only / typed_refusal placeholders; no figure digits invented.
- Tables 3–5 thermodynamic quantities correctly left outside this pressure extraction.
- Minor YAML locator quirk on one equipment note (`row “Material…”` split into an extra null key) — cosmetic, non-blocking, not a data error.

## Verdict

VERDICT: LAND dacb200ffa064c23ac6ca185e6e2860facd2f24d

— regolith-empirical
