# Z16 FIDELITY AUDIT — ts1985 / kems-002-ohno-1967 / kems-019-miller-armatys-2013

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z16b — BACKLOG 5 audit-pdfs (assigned ts1985 / ohno / miller; Z16 number also used by concurrent `Z16-kambayashi-ohara-ichise` — distinct write-up)  
**Repo / branch:** `regolith-empirical` @ `empirical/z16-ts1985-ohno-miller-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z5` (recycled free slot; prior z5 clean)  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `ts1985` | 16 | **AUDITED** (deferred by Z14/Z15) |
| `kems-002-ohno-1967` | 12 | **AUDITED** (deferred by Z14/Z15) |
| `kems-019-miller-armatys-2013` | 28 | **SKIP-dup** — Z13-aq1 P0=0 + Z15 P0=0; brief reconfirm + key-table sample only |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| ts1985 | 16 | **ALL 16** (+ Table 2 all 4 A/B rows; Table 1 γ°_Na; prose 40–60 mol% span; SiO₂ GD start) | **0** | Fig. 4/6/7/8 curves figure-only (A,B already digitized by authors into Table 2); full Gibbs–Duhem a_SiO₂ curve not stored beyond start pin | none |
| kems-002-ohno-1967 | 12 | **ALL 12** (+ Table 1 **ALL** Fe–B K_B^S / K_V / C_s/C^m / K_D cells; Table 2 **ALL 3** Fe–Si–S rows; Table 3 α_exp grids for Mn/Cu/Sn/Cr/S; fit log K_S^S) | **0** | Figs 1–6 trajectories figure-only; Ward–Machlin α(cond) derivation detail beyond stored 0.2–0.3 | none |
| kems-019-miller-armatys-2013 | 28 | **SKIP-dup** sample ≥8 key quotes/fields (Al₂O, Over 30, Ti₁₀O₁₉, Ir/2200 K, 10⁻⁵–10 Pa / >2500 K, small-α, Chatillon, Zaitsev) | **0** (reconfirm) | Suppl. Tables 1–7 true-absence (already declared) | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. `pdftotext -layout` for ts1985 + miller; ohno scan has **no text layer** → `pdftoppm -png` 200 dpi + visual read of Tables 1–3 (pages 1165–1167).
2. ts1985 Tables 1–2 are image embeds → page renders `p3-3.png` (Table 1) and `p7-7.png` (Table 2) under `/workspace/ferry-inbox/reviews/_z16b_audit/png/`.
3. Dump all species observations → JSON under `_z16b_audit/json/`; cell-by-cell vs PDF.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
5. `validate_literature_extracts.py` on all three → **OK: 3 extract file(s) valid** (no edits).

---

## 1. ts1985 (Tsukihashi & Sano 1985, Tetsu-to-Hagané 71:815–822)

**PDF:** 8 pp. Japanese + English synopsis. Text layer sparse on tables (image); page renders used for Table 1 (p.817) and Table 2 (p.821).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 3 (`Na2O`, `Na`, `SiO2`) |
| Observations | **16** (all `activity_coefficient`) |
| fidelity_samples | 6 |
| experiments | 1 |

### Sample = ALL 16

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `ts1985_na2o_table2_log10_a_AT_B` | Table 2 p.821 | A,B rows: X=0.40 **(−1.46±0.02)×10⁴ / 3.03±0.06**; 0.45 **(−1.18±0.03)×10⁴ / 1.79±0.06**; 0.50 **(−0.98±0.03)×10⁴ / 1.03±0.03**; 0.55 **(−0.74±0.02)×10⁴ / 0.06±0.01** | yes |
| `ts1985_na2o_table2_X0p{40,45,50,55}_T{1100,1200,1300}C` (12) | Table 2 | Stored A/B match table; `log10 a = A/T+B` and `a=10**(…)` recalculated — **all 12 a_ok / log_ok** | yes |
| `ts1985_na2o_prose_1200C_range` | Synopsis + 結言 (2) | 1200 °C: a_Na2O **1×10⁻⁷ → 5×10⁻⁵** as Na2O 40→60 mol% | yes |
| `ts1985_gamma_Na_in_Pb_table1` | Table 1 p.817 | this-work γ°_Na: **0.582 @1300**, **0.315 @1200**, **0.156 @1100** (extrapolated); meta Si/Na/P_CO cells 0.0208/0.319/0.5 and 0.0136/0.420/0.1; Hultgren parentheticals 0.125/0.102/0.0813 | yes |
| `ts1985_sio2_gibbs_duhem_1200C_X0500` | p.821 prose | a_SiO2 start **6.01×10⁻³** @1200 °C, X_Na2O=0.500; method_class derived_gibbs_duhem | yes |

### Coverage

- Primary products (Table 2 A/B + evaluated a; Table 1 γ°_Na; prose span; SiO₂ GD start) fully represented.
- Not stored (acceptable): full Fig. 8 a_SiO2 curve; Fig. 4–7 trajectories (authors already collapsed Fig. 4 into Table 2).

### P0 / P1

- **P0: 0**
- **P1 (latent):** `pO2_bar` / `log10_pO2_bar` on activity rows are engine C–CO buffer inferences at stated P_CO, not printed cells — already labeled `pO2_inference`; not a wrong printed number.

---

## 2. kems-002-ohno-1967 (Ohno & Ishida 1967, J. Japan Inst. Metals 31:1164–1169)

**PDF:** 6 pp. scan, **no text layer**. Tables 1–3 read from page renders (pp.1166–1167).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 5 (`Mn`, `Cu`, `Sn`, `Cr`, `S`) |
| Observations | **12** (6 `rate_series` + 6 `alpha`) |
| fidelity_samples | 6 |

### Sample = ALL 12 (+ full Table 1/2/3 grids)

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `ohno_1967_*_specific_evaporation_constant` (Mn/Cu/Sn/Cr/S) | Table 1 p.1166 | Fe–Mn K_B^S **8.3e-3 / 8.3e-3 / 8.6e-3**, K_V **8.80e-2**; Fe–Cu **4.9/4.4/5.2 e-3**, K_V **1.16e-2**; Fe–Sn **2.1/2.6/2.2 e-3**; Fe–Cr **2.1/2.0/2.3 e-4**, K_V **3.3e-4**; Fe–S **6.6/6.7/8.4/7.0 e-4** + C_s/C^m / K_D cells | yes |
| `ohno_1967_sis_desulfurization_rate_table2` | Table 2 p.1166 | melts 6601-4/3/2: K **3.1e-3 / 6.5e-3 / 1.1e-2** @ %Si 1.06/2.10/2.99, %S 0.25; fit `log K_S^S = 0.28×[%Si]−2.77` (p.1165) reproduces within ~8% | yes |
| `ohno_1967_mn_olette_alpha_table3` | Table 3 p.1167 | 6602-17 α_exp **121/94/99**; 6603-13 **94/84/71**; 6603-14 **155/146/100**; pin α=121 @5 min | yes |
| `ohno_1967_cu_olette_alpha_table3` | Table 3 | 6602-15 **51/48/50**; 6603-15 **71/62/65**; 6603-16 **131/75/82**; pin 51 | yes |
| `ohno_1967_sn_olette_alpha_table3` | Table 3 | 6602-16 **17/20/22**; 6603-17 **35/25/26**; 6603-18 **12/18/26**; pin 17 | yes |
| `ohno_1967_cr_olette_alpha_table3` | Table 3 | 6601-1 **2.6/2.5/3.0**; 6606-2 **3.0/3.5/3.8**; 6606-3 **3.0/3.6/3.3**; pin 2.6; extract flags Table1 **6606-1** vs Table3 **6601-1** as `SOURCE_INTERNAL_CONTRADICTION` | yes |
| `ohno_1967_s_condensation_alpha_table3` + class_b1 | Table 3 | α(cond) **0.2 / 0.2 / 0.3 / 0.2**; α_S(exp) series 6.0/5.7/6.0 …; Fe–Si–S α_S(exp) 24/29/26, 57/58/61, 103/83 | yes |

### Coverage

- Tables **1–3** (primary K and α products) fully represented.
- Documented source quirk (Cr melt id 6606-1 vs 6601-1) retained, not “fixed.”

### P0 / P1

- **P0: 0**
- **P1 (latent):** locators still point at private OCR artifact paths (`docs-private/research/ocr-artifacts/...`); content matches the held ferry PDF — locator hygiene only, not a wrong number.

---

## 3. kems-019-miller-armatys-2013 — SKIP-dup

**Prior:** `reviews/Z13-aq1-oa-pdf-fidelity.md` (P0=0, ≥22 quotes) and `reviews/Z15-miller-markova-halwax.md` (P0=0, ALL 28).  
**This seat:** brief reconfirm only; no re-audit of all 28.

### Reconfirm sample (key tables / quotes)

| Field | Extract | PDF (layout p.2/6/7) | OK? |
| --- | --- | --- | --- |
| Al₂O(g) major | `miller_2013_al2o_major_al_species` | “Al2O(g) is a major Al-containing species…” | yes |
| Over 30 glasses | `miller_2013_borate_silicate_glasses` | “Over 30 binary and multicomponent borate or silicate” | yes |
| Ti₁₀O₁₉ | `miller_2013_ti_oxides_and_titanates` | “ZnO, PuO2, Ti10O19 and some other compounds” | yes |
| Ir / 2200 K | `miller_2013_ir_cell_2200k` | “ceramics at temperatures up to 2200 K” (Hilpert / Ir cells) | yes |
| P / T window | `miller_2013_kems_p_and_t_window` | “generally between 10-5 and 10 Pa” / “above 2500 K” | yes |
| Small α | `miller_2013_refractory_oxide_small_alpha` | “small vaporization coefficient, particularly for the refractory oxides” | yes |

**P0: 0 (reconfirm).** No patch.

---

## Push

```bash
git push -u origin HEAD:empirical/z16-ts1985-ohno-miller-2026-09-23
```

Empty tip = `origin/work-v064-green` @ `2e9e17c3d`. No PDFs committed. No force-push.
