# X4 — EXTRACTION AUDIT: E4 kems-alloy-glass

**Lane:** E4 (`empirical/e4-kems-alloy-glass-2026-09-22`)  
**Worktree:** `/workspace/repos/wt/slot-01` (reset to e4 tip)  
**Prior:** `/workspace/ferry-inbox/reviews/E4-kems-alloy-glass.md`  
**PDFs:** `/workspace/batch-z/pdfs/kems-001-homma-1966.pdf`, `kems-027-plante-hastie-1983.pdf`, `kems-036-sesko-2024.pdf`  
**Tip:** `b764bca08566c8acbfce1ba58ffa659ccdd22e3b` (unchanged; no fix commit)  
**Date:** 2026-09-22 (America/Toronto)

## Scope

Cell-by-cell check of every landed starting composition (value, unit/basis, row/experiment mapping, locator) against the printed PDF table/caption. Mismatch → P0 + fix on the E branch.

## P0 summary

**P0 count: 0.** No composition mismatches. No fix/push.

## Per-source audit

### kems-001-homma-1966 — PASS

Printed mother-alloy elemental wt% from Tables 1–3 (journal pp. 517–518 / PDF pp. 3–4) and Fig. 1 caption carbon (journal p. 516 / PDF p. 2). Solute-only maps; Fe balance not printed and not invented.

| experiment_id | species | landed wt% | printed | source | Match |
| --- | --- | ---: | ---: | --- | --- |
| fe-mn-6502-1 | Mn | 1.05 | 1.05 | Table 1 Mother alloy %Mn | yes |
| fe-mn-6502-2 | Mn | 1.74 | 1.74 | Table 1 Mother alloy %Mn | yes |
| fe-mn-6412-14 | Mn | 2.89 | 2.89 | Table 1 Mother alloy %Mn | yes |
| fe-c-mn-6501-11 | C | 4.85 | 4.85 | Fig. 1 caption | yes |
| fe-c-mn-6501-12 | C | 4.69 | 4.69 | Fig. 1 caption | yes |
| fe-cu-6503-12 | Cu | 1.04 | 1.04 | Table 2 Mother alloy %Cu | yes |
| fe-cu-6503-11 | Cu | 1.74 | 1.74 | Table 2 Mother alloy %Cu | yes |
| fe-cu-6503-16 | Cu | 2.65 | 2.65 | Table 2 (cont.) Mother alloy %Cu | yes |
| fe-cu-6503-28 | Cu | 4.57 | 4.57 | Table 2 (cont.) Mother alloy %Cu | yes |
| fe-sn-6503-15 | Sn | 0.98 | 0.98 | Table 3 Mother alloy %Sn | yes |
| fe-sn-6503-14 | Sn | 1.84 | 1.84 | Table 3 Mother alloy %Sn | yes |
| fe-sn-6504-1 | Sn | 3.13 | 3.13 | Table 3 Mother alloy %Sn | yes |

- **Host:** twelve per-melt `sample.printed_composition` maps (not a lumped series)
- **Basis:** elemental wt% as printed (not oxides)
- **Locators:** tables `1`/`2`/`3` pages 517–518; Fe–C–Mn via `figure: "1"` page 517 note — correct for journal pagination
- **Not invented (correct):** Fe balance; Mn mother % for 6501-11/12 (caption prints C only); generic “carbon of order 10⁻² wt%” text for other mother alloys
- **Row/experiment mapping:** Melt No. → `fe-{system}-{melt}` — correct

### kems-027-plante-hastie-1983 — PASS

Printed Table 1 (report p. 3 / PDF p. 9), Weight % columns only; nested under `nominal` and `analytical` as printed. Identical maps on both series hosts.

| compound | nominal printed | nominal landed | analytical printed | analytical landed | Match |
| --- | ---: | ---: | ---: | ---: | --- |
| SiO2 | 52.00 | 52.0 | 53.76 | 53.76 | yes |
| Fe2O3 | 12.96 | 12.96 | 11.49 | 11.49 | yes |
| Al2O3 | 4.86 | 4.86 | 4.75 | 4.75 | yes |
| B2O3 | 7.29 | 7.29 | 6.49 | 6.49 | yes |
| Li2O | 5.10 | 5.1 | 3.96 | 3.96 | yes |
| Na2O | 10.13 | 10.13 | 8.59 | 8.59 | yes |
| MnO2 | 3.25 | 3.25 | 3.57 | 3.57 | yes |
| NiO | 1.05 | 1.05 | 1.80 | 1.8 | yes |
| CaO | 1.59 | 1.59 | 1.01 | 1.01 | yes |
| MgO | 0.68 | 0.68 | 0.54 | 0.54 | yes |
| ZrO2 | 0.70 | 0.7 | 3.79 | 3.79 | yes |
| SrO | 0.09 | 0.09 | 0.05 | 0.05 | yes |
| Cs2O | 0.10 | 0.1 | 0.07 | 0.07 | yes |
| RuO2 | 0.09 | 0.09 | 0.11 | 0.11 | yes |
| Re2O7 | 0.10 | 0.1 | 0.03 | 0.03 | yes |

- **Hosts:** `kms-vacuum-glass-series` and `tms-n2-glass-series` `sample.printed_composition` (same nested map)
- **Basis:** Weight %; Mole % columns not copied — correct
- **Locator:** `published_page: 3`, `pdf_page_index: 9`, `table: "1"` — correct (footer page 3 on PDF p. 9)
- **Footnotes:** a Soper 1982; b selection / nominal for thermo estimates — noted in locator
- **YAML float canonicalization** (`52.00`→`52.0`, `5.10`→`5.1`, etc.) is representation only; magnitudes match

### kems-036-sesko-2024 — PASS

Printed Table 4.1 (thesis p. 39 / PDF p. 54), **EAC-1** column (caption: EAC-1A numbers from [25]; header EAC-1).

| oxide | printed EAC-1 | landed | Match |
| --- | ---: | ---: | --- |
| SiO2 | 44.41 | 44.41 | yes |
| FeO | 0.00 | 0.0 | yes |
| MgO | 12.09 | 12.09 | yes |
| CaO | 10.98 | 10.98 | yes |
| Al2O3 | 12.80 | 12.8 | yes |
| TiO2 | 2.44 | 2.44 | yes |
| Cr2O3 | 0.00 | 0.0 | yes |
| MnO | 0.20 | 0.2 | yes |
| Na2O | 2.95 | 2.95 | yes |
| K2O | 1.32 | 1.32 | yes |
| Fe2O3 | 12.20 | 12.2 | yes |
| P2O5 | 0.61 | 0.61 | yes |

- **Host:** `solar-exposure-12.sample.printed_composition`
- **Basis:** oxide wt%; Total 100.00 omitted; 0.00 cells retained — correct
- **Locator:** `page: 39`, `pdf_page_index: 54`, `table: "4.1"` — correct
- **Not used (correct):** Apollo soil columns 24999 / 64501 / 70051 (not this charge)

## Method notes

- Homma: OCR of scan is noisy; Tables 1–3 and Fig. 1 caption read from rendered page PNGs at 200 dpi + layout `pdftotext` cross-check
- Plante + Sesko: layout `pdftotext` + rendered page PNG at 150 dpi

## Validation

`uv`/venv `python tools/validate_literature_extracts.py` on the three extract files → OK.

## Push

None. Tip remains `origin/empirical/e4-kems-alloy-glass-2026-09-22` @ `b764bca08566c8acbfce1ba58ffa659ccdd22e3b`.

## Report line

**P0: 0 · tip: `b764bca08` · READY**
