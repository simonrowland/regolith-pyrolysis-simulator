# REVIEW: stolyarova-1996-cao-alumina-silica-kems extract

- **Reviewer**: regolith-empirical (slot-z15)
- **Date**: 2026-09-30 ~20:05 ET
- **Tip**: `e311b68b2ef30e4c32cfaa416a4ce1078ec2832b` on `empirical/review-stolyarova-1996-extract` (tracks `review/stolyarova-1996-extract`; base `bba8ef156` as expected)
- **Extract**: `data/literature/extracts/stolyarova-1996-cao-alumina-silica-kems.yaml` (+ extracts-v2 twin)
- **Paper**: Stolyarova, Shornikov & Shultz 1996, CaO–Al₂O₃–SiO₂ KEMS at 1933 K (scan PDF)
- **Corpus kit**: `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1996/` (PDF + MinerU OCR + page images)

## Scope

Frontier review of record. Read-only; no extract edits; no origin push of the review tip.

## Tip / twin

- Worktree HEAD matches required tip `e311b68b2ef30e4c32cfaa416a4ce1078ec2832b`.
- extracts-v2 twin present (`battery_observations.v2.1`, 306 observations). Migrated census: 108 `measured_direct`, 55 `model_derived`, 143 `unknown` (figure-only / unsupported figure quantities).

## Check 1 — Table 2 / Table 3 numeric cells vs page images

**Counts in extract (match paper/OCR):**

| Table | Claimed | Extract numeric cells |
|-------|--------:|----------------------:|
| Table 2 partial pressures (Ca, Al, AlO, SiO, SiO₂, O, O′, O″) | 163 | **163** (30+23+10+30+15+30+10+15) |
| Table 3 activities (cols 1–5) | 137 | **137** (30+23+28+26+30) |

**Mechanical parity:** extract printed cells == MinerU table HTML for all 163 + 137 cells (0 mismatches).

**Independent page-image / PDF re-check (weighted to exponents):**

- Rendered PDF pp. 20 / 23 (`pdftoppm` 300 dpi) + MinerU table images; tesseract on Table 2 page; visual spot-check of ≥25 cells across both tables.
- Confirmed column scales on Table 2 header image: `p_Ca · 10^7`, `p_Al · 10^8`, `p_AlO · 10^8`, `p_SiO · 10^5`, `p_SiO2 · 10^8`, `p_O / p′_O / p″_O · 10^8`.
- `pressure_atm` conversions spot-checked (Ca ×10⁻⁷, SiO ×10⁻⁵, Al/O ×10⁻⁸): all OK.
- Sample cells verified against images/PDF OCR (all match extract):

| Row (xA/xC, xS) | Species | Printed cell | Notes |
|-----------------|---------|-------------:|-------|
| 0.11, 0.44 | Ca / SiO / SiO2 / O / O″ | 0.75 / 2.08 / 0.50 / 0.36 / 0.16 | header row |
| 0.15, 0.25 | Ca / SiO / O | 6.58 / 0.05 / 0.35 | |
| 0.16, 0.50 | Ca / Al / SiO / SiO2 / O / O″ | 0.50 / 0.27 / 3.38 / 0.80 / 0.36 / 0.16 | fidelity sample Al=0.27 |
| 0.17, 0.10 | Ca | **7.76** | tesseract+image; not 7.78 |
| 0.30, 0.50 | Ca / Al / SiO / SiO2 / O / O″ | 3.95 / 0.53 / 4.57 / 1.00 / 0.34 / 0.15 | |
| 0.35, 0.50 | full row incl AlO=0.14, O′=0.40 | OK | first AlO row |
| 0.61, 0.50 | Ca / Al / AlO / SiO / SiO2 / O / O′ / O″ | 2.24 / 1.21 / 0.25 / 6.99 / 1.50 / 0.32 / 0.38 / 0.15 | |
| 0.62, 0.40 | Ca | **8.29** | anomalous but confirmed on page |
| 0.67, 0.38 | full sparse AlO row | OK | |
| 1.00, 0.50 | Ca / Al / AlO / SiO / SiO2 / O / O′ / O″ | 0.18 / 1.97 / 0.36 / **10.9** / 1.80 / 0.28 / 0.33 / 0.12 | SiO 10.9 not 10^-misread |
| Table 3 0.11,0.44 | cols 1/3/5 | 0.098 / 0.009 / 0.130 | |
| Table 3 0.17,0.10 | col 1 | **1.000** | |
| Table 3 0.61,0.50 | all 5 | 0.026 / 0.251 / 0.230 / 0.310 / 0.390 | |
| Table 3 1.00,0.50 | all 5 | 0.019 / 0.434 / 0.470 / 0.470 / **0.519** | |

**Result:** no numeric corrections found in the sampled cells; worker’s zero-correction claim holds on this sample. Dash cells are omitted (not zero-filled).

## Check 2 — Calculated O = model_derived (not measured)

Paper footnote (Table 2): values of `p_O`, `p′_O`, `p″_O` calculated per Eqs. (7)–(9).

Extract:

| Observation | n points | method_class / evidence_class | Notes |
|-------------|--------:|-------------------------------|-------|
| `…_pO_1933k` | 30 | **model_derived** | Eq. (7) MoO₃ ⇌ MoO₂ + O |
| `…_pO_prime_1933k` | 10 | **model_derived** | Eq. (8) AlO ⇌ Al + O |
| `…_pO_double_prime_1933k` | 15 | **model_derived** | Eq. (9) SiO₂ ⇌ SiO + O |

Measured Table 2 species (Ca, Al, AlO, SiO, SiO₂) correctly remain `measured_direct`. **Pass** on tagging.

## Check 3 — Store hard issues 3180 → 3235 (+55 `conditional_field`)

**What the 55 are:** exactly the **55 author-calculated O partial-pressure points** after migrate/explode.

From extracts-v2 twin on this tip:

- 55 observations with `evidence.class = model_derived` (all O / O′ / O″ cells).
- All 55 have `derivation` present as unit conversion (`atm_to_Pa`) only.
- All 55 have **`derived_from` absent**.
- Validator emits one hard `conditional_field` per such obs on `.derived_from` → **+55**, no new refusal class.

**Legitimate?** Yes — J02 / validate rule: `MODEL_DERIVED` requires resolvable `derived_from` ancestry. The extract correctly *labels* these as author-calculated and cites Eqs. (7)–(9) in `method_as_printed`, but does **not** supply structured `derived_from` (and a thermodynamic `derivation` beyond atm→Pa) pointing at measured parents (MoO₂⁺/MoO₃⁺ ion currents and/or Al/AlO/SiO/SiO₂ pressure rows). That is an extract completeness gap, not a false-positive census.

→ **P2** to add structured lineage for the three O observation blocks (and their exploded points).

## Check 4 — Cell material / no Mo inference

- Paper prints **no** Knudsen-cell material.
- Extract `benches[0].cell_material_and_liner.state = {tag: unknown, reason: not_published}` with explicit note: *“MoO ions in Table 1 do not establish a Mo cell.”*
- No molybdenum cell value anywhere in the extract; MoO appears only in that disclaimer and in O Eq. (7) method prose.
- Table 1 MoO₂⁺/MoO₃⁺ ions were **not** used to invent cell material. **Pass.**

## Other notes (non-blocking)

- Figure-only activity observations left as unsupported/unknown in v2 (expected; not numeric admits).
- Table 3 activities are `measured_reduced` with prose `derivation`; they do not contribute to the +55 delta.
- Fidelity samples (8) align with checked Table 2 cells.

## Findings summary

| Item | Result |
|------|--------|
| Tip SHA | Match |
| ≥25 cell image spot-check | Pass (0 corrections in sample) |
| Exponent / scale conversion | Pass |
| O tagged model_derived | Pass |
| +55 hard issues | Identified: 55 O points missing `derived_from` |
| Mo cell inference | Pass (typed unknown) |

## P1 / P2

- **P1:** none
- **P2:** Add structured `derived_from` (+ thermodynamic `derivation` beyond atm→Pa) for the three author-calculated O observation blocks (`stolyarova_1996_table2_pO_1933k`, `_pO_prime_1933k`, `_pO_double_prime_1933k`) so migrated points clear the J02 `conditional_field` on `.derived_from`. Do not invent cell material.

VERDICT: REVISE

