# Z6 — FIDELITY AUDIT batch: kems-022 / kems-023 / kems-024 — 2026-09-22

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/z6-demaria-gibson-2026-09-22`  
**Base / tip:** `origin/work-v064-green` @ `2e9e17c3d` (**no fix commit**)  
**Worktree:** `/workspace/repos/wt/slot-z6`  
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/`  
(`kems-022-demaria-1971.pdf`, `kems-023-demaria-1973.pdf`, `kems-024-gibson-hubbard-1972.pdf`). **Never committed.**  
**Date:** 2026-09-22 (America/Toronto) — BACKLOG 4 RETRY (prior seat failed mid-run)

## Batch verdict

| Extract | Nested obs | Sampled numeric cells | **P0** | Tip change |
|---|---:|---:|---:|---|
| kems-022-demaria-1971 | 31 | ≥53 table points + Table 6 (6 rxns) + masses/oxides (≥20 obs) | **0** | none |
| kems-023-demaria-1973 | 28 | **61/61** Table I printed pressures (+ 7 dash omissions) | **0** | none |
| kems-024-gibson-hubbard-1972 | 108 | **73** abundances + **24** K/Rb·K/Ba ratios (Table 2) | **0** | none |

**READY for V-style verify as “clean”** — tip unchanged; nothing to push.

---

## 1. kems-022-demaria-1971

**Citation:** De Maria, Balducci, Guido & Piacente (1971), Proc. Lunar Sci. Conf. 2, 1367–1380. ADS `1971LPSC....2.1367D`.  
**Extract:** `data/literature/extracts/kems-022-demaria-1971.yaml`  
**PDF:** 14 pp, JBIG2 scan (pdftotext empty); audited via 200–300 dpi page rasters.

### Inventory

| Layer | Count |
|---|---|
| Nested `species.*.observations` | **31** |
| Species keys | 17 (O2 Mn Cr Mg Ca Fe SiO SiO2 TiO TiO2 Al2O AlO Al O Na K Si) |
| Tabulated pressure points | 53 (T1:7, T2:5, T3:8, T4 Al2O:14 + AlO:6, T5:13) |
| Gibbs / third-law grids | Table 6 (6 reactions) + Table 7 (9 rows) + Table 8 (5 rows) |
| `fidelity_samples` pins | 19 |

### Method

1. Rasterize all 14 pages; vision-read Tables **1–8** and procedure prose (pp. 1368–1377).  
2. Compare every printed Table 1–5 pressure cell and Tables 6–8 ΔH°₀ / Kp cells to extract.  
3. Check sample masses, orifice Ø, Na₂O/K₂O wt%, Fe activity=1, figure-only / digitization-rejected Na policy.  
4. Spot `p_Pa = p_atm × 101325` on all 53 pressure points (2 float ULPs only — not P0).

### Sampled checks (≥20 observations; dense cell coverage)

#### Table 1 O₂ (p. 1370) — **7/7 OK**

| sample | T K | P atm printed / ext |
|---|---:|---|
| 12022 | 1396 | 5.54e-9 / 5.54e-9 |
| 12022 | 1475 | 4.96e-8 / 4.96e-8 |
| 12065 | 1433 | 3.78e-9 / 3.78e-9 |
| 12065 | 1482 | 1.22e-8 / 1.22e-8 |
| 12065 | 1499 | 2.11e-8 / 2.11e-8 |
| 12065 | 1471 | 1.27e-8 / 1.27e-8 |
| 12065 | 1459 | 8.78e-9 / 8.78e-9 |

Unit/basis: atm, NAA — OK. Footnote (a) NAA — OK.

#### Table 2 Mn/Cr over 12022 (p. 1371) — **5/5 OK**

| T K | P_Mn printed/ext | P_Cr printed/ext |
|---:|---|---|
| 1583 | 7.66e-9 / 7.66e-9 | — / omit |
| 1609 | 1.32e-8 / 1.32e-8 | — / omit |
| 1725 | **8.47e-8 / 8.47e-8** | 1.03e-8 / 1.03e-8 |
| 1794 | — / omit | 6.2e-8 / 6.2e-8 |

(Raster-confirmed 8.47, not 9.47.)

#### Table 3 Mg (p. 1372) — **8/8 OK**

| sample | T K | inst | P atm printed/ext |
|---|---:|---|---|
| 12022 | 1650 | BC | 1.6e-8 / 1.6e-8 |
| 12022 | 1694 | NAA | 5.7e-8 / 5.7e-8 |
| 12022 | 1815 | NAA | 3.96e-6 / 3.96e-6 |
| 12065ᵃ | 1747 | NAA | 1.65e-9 / 1.65e-9 |
| 12065 | 1854 | NAA | 9.4e-9 / 9.4e-9 |
| 12065 | 1776 | NAA | 2.4e-9 / 2.4e-9 |
| 12065 | 1857 | NAA | 8.85e-8 / 8.85e-8 |
| 12065 | 1927 | NAA | 3.2e-8 / 3.2e-8 |

Footnote ᵃ “probably not initial points” preserved in extract notes — OK.

#### Table 4 Al₂O / AlO (p. 1375) — **20/20 OK**

Examples: Al₂O NAA 12022 @2060 = **5.07e-9**; Al₂O BC 12065 @2360 = **6.90e-7**; AlO BC 12065 @2360 = **1.30e-6**; @2500 Al₂O 4.73e-6 / AlO 8.96e-6 — all match.

#### Table 5 atomic O (p. 1375) — **13/13 OK**

Examples: NAA 12022 @2060 = **2.2e-7**; BC 12065 @2360 = **2.91e-6**; @2500 = **1.42e-5** — match.

#### Table 6 ΔH°₀ summary (p. 1376) — **6/6 reactions OK**

| # | reaction | BC printed/ext | NAA printed/ext |
|---|---|---|---|
| 1 | SiO₂=SiO+½O₂ | 47±2 / 47±2 | 46±2 / 46±2 |
| 2 | AlO=Al+O | 112±3 / 112±3 | — |
| 3 | Al₂O+O=2AlO | 15.6±2 / 15.6±2 | — |
| 4 | FeO=Fe+½O₂ | 39±2; 38±3ᵃ / same | 38±2 / 38±2 |
| 5 | TiO₂=TiO+½O₂ | 83±2 / 83±2 | — |
| 6 | TiO₂=TiO+O | — | 142±3; 143±8ᵃ / same |

ᵃ = second-law — mapped correctly.

#### Table 7 SiO₂ third-law (p. 1377) — **9/9 OK**

All −R ln Kp / −Δ(fef) / ΔH°₀ cells match (incl. 1850 K footnote-b row 5.995 / 19.105 / 46.4).

#### Table 8 AlO=Al+O (p. 1377) — **5/5 OK**

Kp @2360 = **3.19e-5**; ΔH°₀ grid 112.8…111.9 — match. Mean 112.2±0.9 in paper; extract carries per-T rows + Table 6 summary 112±3.

#### Procedure / masses / oxides (p. 1368–1369) — OK

| field | printed | landed |
|---|---|---|
| 12022,57 total | 680.2 mg | 680.2 |
| 12022 BC splits | 167.6, 162.6 mg | same |
| 12022 NAA | 350 mg | 350 |
| 12065,36 total | 928.6 mg | 928.6 |
| 12065 BC / NAA | 305, 150 / 304 mg | same |
| multi-rotating 12065 | 106 mg | 106 |
| orifice Ø main Re | 2.3 mm | 2.3 |
| multi-cell orifice | 1.0 mm | 1.0 |
| Na₂O / K₂O (12022) | 0.36 / 0.068 wt% | same |
| Fe activity | ~unit | 1.0 |
| ion energy | 30 or 70 eV | [30, 70] |

### Coverage (non-P0)

- Figs 1–7: absolute P figure-only; Na Fig.1 digitization **rejected** (typed) — intentional.  
- Ca: figure-only + measurement-difficulty note — OK.  
- Si monatomic: typed not-reported — OK.  
- 12065 Na₂O=0.27 on digitized-Na observation attributed to Maxwell & Wiik / Wolf 2022 (not printed in this paper) — documented secondary; not a stored wrong *paper* number.  
- `p_Pa` float ULP on 2 of 53 points (e.g. 1.65e-9×101325) — not P0.

### Verdict (022)

**P0: 0.** Tip unchanged.

---

## 2. kems-023-demaria-1973

**Citation:** De Maria & Piacente (1973), Lunar Science IV, 175–177. ADS `1973LPI.....4..175D`.  
**Extract:** `data/literature/extracts/kems-023-demaria-1973.yaml`  
**PDF:** 3 pp abstract scan.

### Inventory

| Layer | Count |
|---|---|
| Nested observations | **28** |
| Table I pressure points | **61** (+ 7 printed dashes as `omitted_cells`) |
| This-work samples | 10017, 12073 |
| Quoted comparison (ref 4 = kems-022) | 12022, 12065 |
| `fidelity_samples` | 5 |

### Method

Vision-read full Table I (p. 176) vs every extract point; confirm dash omissions; confirm ~ qualifiers on Na@1300/12065 and TiO@2100/12065; coverage of detected-but-not-tabulated species.

### Sampled checks — **full Table I (61 cells) OK**

Fidelity pins + full grid (examples):

| species | sample | T K | printed | ext | match |
|---|---|---:|---|---|---|
| Na | 10017 | 1400 | 1.3e-7 | 1.3e-7 | OK |
| Na | 12022 | 1300 | 8.5e-9 | 8.5e-9 | OK |
| Na | 12065 | 1300 | ~4e-9 | 4e-9 + `approximate_as_printed` | OK |
| Fe | 10017 | 1600 | 7.8e-7 | 7.8e-7 | OK |
| Mg | 10017 | 1700 | 5.6e-8 | 5.6e-8 | OK |
| SiO | 12073 | 1800 | 7.6e-6 | 7.6e-6 | OK |
| SiO | 10017 | 1900 | 1.2e-6 | 1.2e-6 | OK (printed; depletion) |
| Ca | 10017 | 1900 | 6.2e-8 | 6.2e-8 | OK |
| TiO | 10017 | 2100 | 1.1e-7 | 1.1e-7 | OK |
| TiO | 12065 | 2100 | ~9e-8 | 9e-8 + approx qualifier | OK |
| Al | 10017 | 2100 | 2.3e-7 | 2.3e-7 | OK |

Dashes (Na 1300/10017 & 12073; Na 1500/12022; Mg 1700/12073 & 12065; TiO 2200/12065; Al 2100/12073) typed as omitted, **not zero** — OK.

Unit/basis: atm as printed; `p_Pa = p_atm × 101325` — OK.  
Evidence split this-work vs quoted-1971 — OK.

### Coverage (non-P0)

- Figs 1–3: figure-only observations (no digitization) — OK.  
- K, O, Cr, Mn, SiO₂, O₂, FeO, AlO, Al₂O, TiO₂: detected inventory / typed absence of tabulated P — OK for a 3-page abstract.  
- Motzfeldt geometry not restated — typed missing — OK.

### Verdict (023)

**P0: 0.** Tip unchanged.

---

## 3. kems-024-gibson-hubbard-1972

**Citation:** Gibson & Hubbard (1972), Proc. Lunar Sci. Conf. 3, 2003–2014. ADS `1972LPSC....3.2003G`.  
**Extract:** `data/literature/extracts/kems-024-gibson-hubbard-1972.yaml`  
**PDF:** 12 pp scan; main numeric table = **Table 2** (p. 2005).

### Inventory

| Layer | Count |
|---|---|
| Nested observations | **108** |
| Table 2 elemental abundances | **73** cells landed |
| Table 2 K/Rb + K/Ba ratios | **24** |
| Qual volatilization statements | 11 (K Rb Na Ba Li Ce Nd Sm Eu Gd Sr) |
| Experiments | 1 (`lunar-volatilization-series`) |
| `fidelity_samples` | 0 |

### Method

Vision-read Table 2 (p. 2005) full grid (10017 / 10073 / 12022 / 14163 columns); compare every landed abundance and `ratio_as_printed`; confirm dash omissions; check vacuum 2×10⁻⁶ Torr → Pa and Table 1 exclusion note.

### Sampled checks (≥20; effectively full Table 2)

#### Abundances (ppm) — examples + full-row audits OK

| component | sample / condition | printed | ext |
|---|---|---:|---:|
| K | 10017 Initial | 2610 | 2610 |
| K | 10017 1400°C 2 hr | 641 | 641 |
| K | 12022 950→1400 cascade | 449 / 360 / 300 / 179 | same |
| K | 14163 Initial† → 72 hr | 4840 / 4220 / 3027 / 1437 / 88 | same |
| Rb | 10017 Init / 1400 | 5.63 / 1.05 | same |
| Rb | 12022 Init | 0.738 | 0.738 |
| Na | 10017 Init / 1400 | 3800 / 500 | same |
| Na | 10073 1400 | — (omit) | omit |
| Ba | 12022 Init / 1400 | 59.5 / 59.3 | same |
| Ba | 14163 Init† / 1–72 hr | 873 / 822 / 826 / 829 | same |
| Li | 10017 Init* / 1400 | 18.1* / 19.3 | 18.1 / 19.3 |
| Ce…Gd | 10017 / 10073 Init + 12022 Init/1400 | full REE block | match |
| Eu | 12022 Init & 1400 | 1.28 / 1.28 | same |

\* Tera et al. (1970) footnote preserved in extract context.

#### Ratios — **24/24 OK**

| ratio | sample / condition | printed | `ratio_as_printed` |
|---|---|---:|---|
| K/Rb | 10017 Init / 1400 | 464 / 610 | same |
| K/Rb | 12022 1050 | 1259 | 1259 |
| K/Rb | 14163 72 hr | 880 | 880 |
| K/Ba | 12022 Init→1400 | 9.0 / 7.5 / 6.1 / 5.0 / 3.0 | same |
| K/Ba | 14163 instantaneous | ~5.1 | `~5.1` + approx semantics |

#### Bench / conditions — OK

- Chamber 2×10⁻⁶ Torr → **0.00026664… Pa** (Torr×133.322…) — OK.  
- fO₂ upper bound `<10^-10` atm — typed bound — OK.  
- Hold times / T_C on heated columns — OK.

### Coverage (non-P0)

- **Table 1** (Brewer 1953 simple-oxide vaporization temps): explicitly excluded as non-lunar measured — OK.  
- Sr: qualitative / Fig.1 only (no Table 2 ppm) — typed statement — OK.  
- `abundance_ppm` / `ratio_as_printed` stored as strings (preserve printed precision) — schema style, not wrong number.  
- `admission_status: pending_validation` on cells — process flag, not P0.  
- Figures not digitized — OK for this extract’s Table-2 scope.

### Verdict (024)

**P0: 0.** Tip unchanged.

---

## Fixes

**None.** No YAML edits; no commit; PDFs never staged.

## Return path

Write-up only: `/workspace/ferry-inbox/reviews/Z6-demaria-gibson.md`  
Branch tip remains `2e9e17c3d` on `empirical/z6-demaria-gibson-2026-09-22`.
