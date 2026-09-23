# Y6 — CROSS-AUDIT N6 Cl melts / Fe–Mo thermal / Mercury atmosphere — 2026-09-22

**Lane audited:** N6 (`empirical/n6-thomas-ueshima-jaggi-2026-09-22`)
**Author write-up:** `/workspace/ferry-inbox/reviews/N6-cl-femo-mercury.md`
**Tip (detached):** `7d06507ef5cbd0901e58b934758118f0dbefe217` — unchanged; no fix commit
**Auditor worktree:** `/workspace/repos/wt/slot-y6-n6` (detached @ tip; different seat from extractor `slot-07`)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{thomas-wood-2021-chlorine-silicate-melts,ueshima-1982-fe-mo-thermal,jaggi-2021-mercury-atmosphere}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)

## VERDICT

**READY.** Tip unchanged.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 1 |
| P2 | 1 |
| P3 | 2 |

## Method

Blind adversarial check vs PDF (not vs N6 write-up first):

1. Detached worktree at `7d06507ef`; `pdftotext -layout` + 150 dpi `pdftoppm` page rasters for critical tables/synopsis pages.
2. Enumerated every landed numeric in `printed_composition` / `conditions` / `fidelity_samples` / `species.*.observations|context[].values` (oxide wt%, Clglass ±unc, T/P/time/buffer/f(Cl₂)/log f(O₂), invariant T_C, cell geometry, Table 1–2 model inputs).
3. Compared value, unit/basis, experiment/series mapping, and locator against printed cells (Table 2 programmatically: all 29 extract Clglass rows matched a PDF AgI/Cl line including the pressure-block `(0.4)` reprint; f(Cl₂) sci-notation float-equal).
4. `validate_literature_extracts.py --check-fidelity-match` on the three files → `OK: 3 extract file(s) valid`.
5. PDF sha256 matched each corpus sidecar (all three YES).

P0 rule (same as X1–X9 / Y calibration): **P0 only if a wrong number lands**. Omission / incompleteness ≤ P1; locator hygiene ≤ P2.

## Per-source audit

### 1. thomas-wood-2021-chlorine-silicate-melts — PASS (landed cells) / P1+P2 hygiene

**PDF:** GCA article-in-press (encrypted; text layer usable). Title *The chemical behaviour of chlorine in silicate melts*; Thomas & Wood; DOI `10.1016/j.gca.2020.11.018` on title page. sha256 match.

#### Landed cells vs printed

| field | landed | printed | match |
| --- | --- | --- | --- |
| Table 1 CMAS SiO2/Al2O3/MgO/CaO | 46.53 / 18.33 / 17.82 / 17.33 | PDF p.4 Table 1 | yes |
| Table 1 Icelandic SiO2…Na2O | 50.76 / 1.02 / 15.32 / 9.61 / 9.19 / 12.26 / 2.01 | same (en-dash oxides omitted, not zeroed) | yes |
| Table 2 Clglass (29 rows) | all Cl ±unc, T_C, P_GPa, time, CCO/RRO, log f(O₂), f(Cl₂), Cl/(Cl+I), Ag/(Ag+Pt), mass ratio | PDF p.7 Table 2 | yes (0 mismatches) |
| AgI/Cl-005 pressure reprint | 2.55 (0.4) kept alongside 2.55 (0.04) elsewhere | printed both spellings | yes (corrections note) |
| ICB FeO_wt_pct_in_melt ±unc | 0.5(0.13)…8.52(0.13) etc. | Table 2 FeO column | yes |
| T_K DERIVED | T_C + 273.15 (e.g. 1400 → 1673.15) | stamp OK | yes |
| Henry logClmelt regressions | context only; `model_derived` | Abstract eqs | yes (not scored) |
| Bench | Boyd–England ½″ piston-cylinder; CAMECA SX-Five-FE + JEOL-8600 | methods | yes |

- Supplementary Table 1 glass majors correctly refused (not recoverable as numbers in body PDF).
- Table 2 series reprints of the same ID (except the divergent `(0.4)` pressure cell) intentionally deduped — numbers that land are still correct.

#### P1-1 — missed printed thermocouple type

Methods prose (PDF p.4) prints **alumina-sheathed C-type (W95Re5–W74Re26) thermocouple**. Extract `temperature_measurement.sensor` says type “not further specified in body text” / “Exact thermocouple type not printed…”. Missed printed bench fact = **P1** (not P0: no wrong observation number).

#### P2-1 — `pdf_page_index` systematically −1 vs physical PDF pages

Physical `pdftoppm`/`pdftotext -f N` pages: Table 1 = **4**, CAMECA/JEOL = **6**, Table 2 = **7** (PDF p.1 is the Elsevier “Dear author” cover). Extract locators use 3 / 5 / 6. Sibling extracts in this tip (Ueshima, Jäggi) use 1-indexed physical PDF pages. Off-by-one locators = **P2** (verification friction; values still match when the correct page is opened).

### 2. ueshima-1982-fe-mo-thermal — PASS

**PDF:** J-STAGE OA; Japanese body + English Synopsis. Title *Thermal Analysis of Fe-Mo Alloys by the Use of Knudsen Cell Mass Spectrometry*; Ueshima / Ichise / Mori; Tetsu-to-Hagané 68 (1982) 2569–2577. DOI match. sha256 match.

| field | landed | printed | match |
| --- | --- | --- | --- |
| α = L + δ | 1454 ± 2 °C → T_K 1727.15 DERIVED | English Synopsis (PDF p.1) | yes |
| δ = L + σ | 1503–1521 °C → 1776.15–1794.15 | Synopsis | yes |
| σ = L + (Mo) | 1610 ± 1 °C → 1883.15; ~70 °C above prior | Synopsis + Japanese results (1610±1) | yes |
| Composition / T range | 25–67 at% Mo; 1300–1650 °C → 1573.15–1923.15 K | Synopsis | yes |
| Bench | Hitachi RM-6K; Al₂O₃ SSA-S OD 11 / ID 9 / h 12 mm; lid 1 mm; orifice 0.5 mm; Ta; W/W–Re 26%; ⁵⁶Fe | §2.1 + Fig.1 (PDF p.2) | yes |
| Rates / Δt | typically 3–5 °C/min; Δt 30 ± 0.5 °C | §2.3 | yes |
| Sample | ~0.25 cm³ (~2 g → 0.002 kg approximate) | §2.3 | yes |

- Table 4 per-heat series correctly deferred (Synopsis invariants scored) — intentional incompleteness, not wrong numbers.
- `reaction_as_printed` uses ASCII `alpha`/`delta`/`sigma` for Greek α/δ/σ — acceptable transliteration (**P3**).

### 3. jaggi-2021-mercury-atmosphere — PASS

**PDF:** arXiv/accepted MS (2021-10-18); *Evolution of Mercury’s Earliest Atmosphere*; Jäggi et al.; PSJ DOI `10.3847/PSJ/ac2dfb`. sha256 match.

| field | landed | printed | match |
| --- | --- | --- | --- |
| Table 2 EH4 row | 62.73 / 2.58 / 30.24 / 1.99 / 0.0 / 1.71 / 0.2 / 99.45 | PDF p.5 Table 2 | yes |
| Table 2 CB / NSP source / NSP lava | all oxide + Total cells | same | yes (50.70→50.7 float-equal) |
| method_class | `secondary_compilation` | literature inputs, not author EPMA | yes |
| Table 1 cases SN5/SN3/SV/LN5/LN3/LV | R_P 2440/3290; g 3.7/4.0; SiO 1.4E-4; volatile bars 0.7/3.2/0.05/1.1 and 1.2/5.8/0.2/4.9 | PDF p.4 Table 1 | yes (context, not scored) |
| Model stack | SPIDER + VapoRock + VULCAN/FactSage — not a lab instrument | §2 Methods | yes |
| fO2 | IW-1 (O’Neill & Eggins 2002) | §2.3 | yes |
| Tables 3–5 + escape/mass-loss | typed absence (`model_derived_cohort_blocked`) | BACKLOG N7 | yes |
| MESSENGER Na 3–5 wt% | quoted comparator context only | Intro | yes |

No model-derived partial pressures / mass-loss rates landed as observations — correct refusal.

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P1 | thomas-wood-2021 | C-type (W95Re5–W74Re26) thermocouple is printed; extract claims type not specified. |
| 2 | P2 | thomas-wood-2021 | `pdf_page_index` values are −1 vs physical 1-indexed PDF pages (Dear-author cover = p.1). |
| 3 | P3 | ueshima-1982 | Synopsis Greek α/δ/σ rendered as ASCII `alpha`/`delta`/`sigma` in `reaction_as_printed`. |
| 4 | P3 | thomas-wood-2021 | Identical Table 2 ID reprints across series blocks deduped (except divergent `(0.4)` unc) — intentional; documented in corrections. |

## Validation

```
.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/thomas-wood-2021-chlorine-silicate-melts.yaml \
  data/literature/extracts/ueshima-1982-fe-mo-thermal.yaml \
  data/literature/extracts/jaggi-2021-mercury-atmosphere.yaml \
  --check-fidelity-match
→ OK: 3 extract file(s) valid
```

PDF ↔ sidecar sha256: all three YES
(`f190a321…`, `68287a86…`, `6ab99216…`).

## Push / product fixes

**None.** No clear P0 with proof. Prefer report over patching P1/P2 bench/locator hygiene on the N6 branch.

Optional follow-ups (owner call): land C-type thermocouple on Thomas bench; bump Thomas `pdf_page_index` by +1 (or document Dear-author exclusion consistently); deepen Ueshima Table 4.

## Report line

**VERDICT: READY · P0=0 P1=1 P2=1 P3=2 · tip: `7d06507ef` (detached, unchanged)**
