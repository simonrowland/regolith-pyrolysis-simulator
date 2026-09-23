# Z14 FIDELITY AUDIT — fegley-2023 / plante-hastie-1983 / sossi-2018-pnas-cr

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z14 — BACKLOG 5, three high-obs remaining (ta-badro skipped; separate priority seat)  
**Repo / branch:** `regolith-empirical` @ `empirical/z14-fegley-plante-sossi-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z14`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

Prefer list (obs count on `data/literature/extracts/*.yaml`, not already in `reviews/Z*.md`):

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `fegley-2023-chemical-equilibrium-calculations-bu` | 28 | **AUDITED** |
| `kems-027-plante-hastie-1983` | 26 | **AUDITED** |
| `kems-045-sossi-2018-pnas-cr` | 25 | **AUDITED** |
| kems-025-markova-1983 | 22 | deferred (next seat) |
| kems-003-pound-1972 | 18 | deferred |
| kems-011-wetzel-gail-2013 | 17 | deferred |
| ts1985 | 16 | deferred |
| kems-002-ohno-1967 | 12 | deferred |
| ta-badro-2021 | — | **SKIP** (separate priority seat) |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| fegley-2023-chemical-equilibrium-calculations-bu | 28 (model tables) | **ALL 28** fidelity pins in-table + T2 **all 82** A/B cells + T32 **all 69** T50/K+Symbol+ratios + T4/T6/T19 multi-row | **0** | Tables 1, 3, 5, 26 (not stored as observations); wet-BSE companion curves figure-only | none |
| kems-027-plante-hastie-1983 | 26 | **ALL 26** | **0** | Study Knudsen orifice **0.5 cm** not a numeric field (only practical ~0.5 mm + note); O₂ Fig.5 / Figs 20–21 figure-only / model-proxy (already refused); timed mg·s⁻¹ true-absence | none |
| kems-045-sossi-2018-pnas-cr | 25 (+32 Table 1 points) | **ALL 25** obs; **ALL 32** Table 1 cells vs page render | **0** | SI Tables S9/S11/S12 and Figs S2–S3 unseen (already declared); Fig.1–3 figure-only | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. `pdftotext -layout` under `/workspace/ferry-inbox/reviews/_z14_audit/{fegley,plante,sossi}/`.
2. `pdftoppm -png` 150–200 dpi for Sossi Table 1 (PDF p.2), Plante Table 1 (p.9), Table 3 (p.21), activity prose (p.22), Cs §4.1 (p.42), Fig.19 caption region (p.51).
3. Dump all species observations → JSON; cell-by-cell / quote-by-quote vs PDF.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
5. Fegley: every fidelity pin required to appear inside the **matching `Table N.` body** (not merely somewhere in the PDF).
6. `validate_literature_extracts.py` **not re-run** (no extract edits).

---

## 1. fegley-2023-chemical-equilibrium-calculations-bu (Fegley, Lodders & Jacobson 2023 preprint)

**PDF:** 97 pp. ADS preprint. Text layer good for appendix tables (PDF pp. 62–78).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 1 (`BSE`) |
| Observations | **28** (1× `activity_coefficient` Table 2; 27× `transition_point` condensation tables) |
| fidelity_samples | 28 (one first-cell pin per stored table) |
| Missing table nos. in extract | 1, 3, 5, 26 |

### Sample = ALL 28 + deep T2/T32

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| T2 pin A_printed row0 | **0** (Ag₂O) | Table 2 p.62 first A column **0** | yes |
| T2 all 82 A/B cells | rows_as_printed | every A_printed/B_printed token in Table 2 body (unicode minus / commas normalized) | yes |
| T4 pin log_P_-4 | **2498** | Table 4 Dry row … **2498** … | yes |
| T6 pin BanYa | **2159** | Table 6 Ca @ log P −6 BanYa **2159** | yes |
| T7–T31 first-cell pins | 1882, 1807, 1803, 1477, 1528, 1793, 1439, 2101, 2054, 1850, 1729, 1803, 755, 1781, 1452, 1035, 1453, 1361, 1113, 1464, 1461, 1583, 1200, 1858 | each inside its own Table N body | yes (28/28) |
| T32 pin T50/K | **2122** (Li) | Table 32 first data row Li **2122** | yes |
| T32 all 69 Symbol + T50/K | full grid | all present; M/MO and M/M+ ratios match (`1.8×10^7` ↔ PDF `1.8×10⁷`) | yes |
| T6 / T19 multi-row | BanYa/MAGMA/FactSage grids | spot-checked clean | yes |

### Coverage

- Stored: Tables **2, 4, 6–25, 27–32** as model_derived condensation / activity-coefficient tables.
- Not extracted (acceptable): Tables **1, 3, 5, 26**; narrative wet-BSE / figure curves.

### P0 / P1

- **P0: 0**
- **P1 (latent):** Ideal_Lock cells store en-dash `–` for “no value”; PDF same. Table 2 oxide label `K2 O` retains a space as printed in some rows — cosmetic.

---

## 2. kems-027-plante-hastie-1983 (Plante, Bonnell & Hastie, NBSIR 83-2731)

**PDF:** 76 pp. NBSIR scan. Text layer noisy on superscripts; page renders used for Tables 1 & 3 and activity prose.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 4 (`Na`, `Li`, `Cs`, `O2`) |
| Observations | **26** (rate_series 4 / psat_series 13 / activity_coefficient 7 / alpha 2) |
| fidelity_samples | 10 |

### Sample = ALL 26

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_kms_pt_cell_geometry` | §2 / Fig.2 | cell **0.80×1.90 cm**, wall **0.025 cm**; practical orifice **~0.5 mm** (not the study **0.5 cm** orifice — `missing_note` states this) | yes |
| `…_table1_snw_glass_composition` | Table 1 p.3 / PDF p.9 | Nominal/analytical wt% grid (SiO₂ 52.00/53.76 … Na₂O **10.13/8.59** … Re₂O₇ 0.10/0.03) matches render | yes |
| `…_table3_mass_loss` | Table 3 p.15 / PDF p.21 | Series I **263.1 mg**, Series II **418.4 mg**; Na₂O total loss **5.3** / present **26.1**; B₂O₃ dashes true-absence; Series II Cs₂O loss **0.50** > inventory **0.42** noted | yes |
| `…_fig19_nabo2_logP_ls` (+ quoted) | Fig.19 caption p.45 | **A=4.09±0.2**, **B=1.190×10⁴**; derived log P(1250 K)=**−5.43** | yes |
| `…_fig19_libo2_logP_ls` (+ quoted) | same | **A=4.73±0.2**, **B=1.292×10⁴** | yes |
| `…_fig19_csbo2_logP_ls` (+ quoted) | same | **A=3.45±0.3**, **B=1.288×10⁴** | yes |
| `…_nabo2_activity_*` | p.16 / PDF p.22 | activity **≈0.01** vs JANAF NaBO₂(l) | yes |
| `…_libo2_activity_*` | p.16 | activity **≈0.1** | yes |
| `…_csbo2_activity_1200K` / quoted | p.16 + §4.1.1 | **a=4×10⁻⁶** @1200 K; ideal-mixing **4×10⁻⁴**; P_obs order **10⁻⁸ atm** | yes |
| `…_detection_limits_quoted` | §4.1 | **10⁻⁷ atm** @ ≤1500 °C | yes |
| `…_alpha_*` | §8 captions | true_absence of kinetic α | yes |
| `…_o2_*` / `…_fig20_21_*` / off-gas Table 4 | figures / Table 4 | figure_only / model_output_not_measurement admissions stand | yes |

### Coverage

- Primary metaborate LS (Na/Li/Cs), activities, SNW composition, mass-loss inventory, geometry, detection limits, α-absence represented.
- Gaps (documented): numeric Clausing C for the **0.5 cm** study orifice; O₂ LS coefficients unpublished; Figs 20–21 model proxies; timed gravimetry absent.

### P0 / P1

- **P0: 0**
- **P1 (latent):** Locator paragraph on geometry mentions “effusion orifice 0.5 cm” while the only numeric orifice field is `practical_orifice_size_mm_typical: 0.5` — intentional per `missing_note`, but easy to misread. Study orifice diameter itself is not stored as a number.

---

## 3. kems-045-sossi-2018-pnas-cr (Sossi et al. 2018, PNAS 115:10920–10925)

**PDF:** 6 pp. PNAS. Text layer good; Table 1 PCC-1 δ⁵³Cr column blank in `pdftotext` — confirmed on page render.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 5 (`Cr`, `CrO`, `CrO2`, `CrO3`, `O2`) |
| Observations | **25** (gibbs_table 21 / activity_coefficient 2 / psat_series 2) |
| Table 1 points | **32** (20 Earth + 12 Moon) |
| fidelity_samples | 8 |

### Sample = ALL 25 (+ full Table 1)

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| Table 1 n | **32** | 20 Earth + 12 Moon | yes |
| points[0] 49J | **−0.065** / 0.041 / n=3 / Cr 3190 / MgO 32.16 | Table 1 same | yes |
| points[18] PCC-1 | **−0.083** / 0.004 | render (text layer dropped δ column) | yes |
| points[29] 10003 | **−0.307** / 0.007 | Table 1 same | yes |
| All 32 rows | sample / rock / δ / 2SD / n / Cr / MgO | page-2 render + text | yes |
| Terrestrial average | **−0.11 ± 0.05** (n=20) | prose p.10921 | yes |
| BHVO-2 | **−0.104 ± 0.03** (n=4) | Methods p.10924 | yes |
| BSE Schoenberg | **−0.13 ± 0.10** | prose | yes |
| Bonnand mare avg | **−0.22 ± 0.10** | prose | yes |
| Grubbs BSE n36 | **−0.11 ± 0.06** (2SE 0.02) | p.10922 | yes |
| Moon filtered | **−0.21 ± 0.06** (2SE 0.03; 17 meas / 15 samples) | p.10922 | yes |
| Δ⁵³Cr Moon−Earth | **−0.10 ± 0.04**; t=**13.36** | p.10922 | yes |
| Cr abundance | Moon **2125±110** ppm; BSE **2520±250**; depletion **16±10%**; f_Cr=**0.84** | p.10922–23 | yes |
| Cr²⁺/ΣCr | **0.32** / **0.91**; logK~**1.9** @1400 °C | p.10921 | yes |
| γ_CrO | **~3** (Berry et al.) | p.10922 | yes |
| Eq.3 prefactor | **−0.31 ± 0.16 × 10⁶/T²** | Eq.3 p.10923 | yes |
| lnβ CrO/CrO₂/CrO₃ | **0.28 / 0.57 / 0.89**; ν=**864 cm⁻¹** | p.10923 | yes |
| Costa / De Maria T windows | **1827–1970 K** olivine; **1396–1499 K** 12002 | p.10922 | yes |
| Visscher–Fegley fO₂ | **IW+2.5** @1800 K | p.10922 | yes |
| Fig.1–3 | figure_only admissions | captions match stored numbers | yes |

Prose rounding (422/95 “−0.17±0.06” vs table **−0.168±0.059**; 49J “−0.07±0.04” vs **−0.065±0.041**) is documented under `prose_rounding_note`; stored points follow the table.

### Coverage

- Main-text isotopic means, Table 1, fractionation Eq.3, quoted KEMS T windows, γ_CrO, vapor T–fO₂ window represented.
- Gaps: SI Appendix tables/figures (true-absence from held PDF).

### P0 / P1

- **P0: 0**
- **P1 (latent):** Sample id `RL-12-1` stores ASCII hyphen; PDF uses en-dash `RL-12–1`. Not a numeric error.

---

## Push

```
branch: empirical/z14-fegley-plante-sossi-2026-09-23
base = tip = 2e9e17c3d138fdfba9269c493f82974a71153fa5  (origin/work-v064-green)
```

Fast-forward push of empty tip (no extract changes). Review artifact only under `ferry-inbox/reviews/` (not in git). PDFs never staged.
