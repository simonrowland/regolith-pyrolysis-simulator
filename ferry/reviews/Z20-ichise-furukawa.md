# Z20 FIDELITY AUDIT — kems-088-ichise-1975 / kems-111-ichise-1982 (+ kems-119 SKIP)

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z20 — BACKLOG 5, three extracts assigned  
**Repo / branch:** `regolith-empirical` @ `empirical/z20-ichise-furukawa-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z20`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate / disposition

| Extract | Obs (YAML) | Disposition |
| --- | ---: | --- |
| `kems-088-ichise-1975` | 16 | **AUDITED** (ALL) |
| `kems-111-ichise-1982` | 16 | **AUDITED** (ALL) |
| `kems-119-furukawa-1975` | 21 | **SKIPPED** — concurrent Z17 seat already audited + fixed P0 (`γ°_Fe` 0.389→0.385) tip `e52066ac7` on `empirical/z17-murchison-furukawa-ueda-2026-09-23`. Do not re-fix or fight that tip. |

## Verdict

| Extract | Obs | Sampled | P0 mismatches | Coverage | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-088-ichise-1975 | 16 | **ALL 16** (+4 fidelity pins); Tables 1–3 full numeric grids | **0** | Figs 1–10 figure-only as declared; γ_S vs %S Fig.7 not digitised (declared battery_limitation) | none |
| kems-111-ichise-1982 | 16 | **ALL 16** (+5 fidelity pins); Tables 1–5 full numeric grids | **0** | Figs 1–6 figure-only as declared; orifice area / Clausing / free surface unstated (declared) | none |
| kems-119-furukawa-1975 | 21 | — | — | **SKIPPED (Z17 owns)** | Z17 `e52066ac7` |

**Overall (this seat): PASS — P0=0 on audited pair. No extract patch on Z20 branch. Tip = green base.**

---

## Method

1. `pdftotext -layout` → `/workspace/ferry-inbox/reviews/_z20_audit/{ichise088,ichise111}/`
2. `pdftoppm -png` 200 dpi table/method pages; visual read of Table grids (088 pp.116–118; 111 pp.555–558).
3. Dump species observations + fidelity_samples → JSON under `_z20_audit/json/`.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
5. `validate_literature_extracts.py` **not re-run** (no extract edits on this branch).
6. CLAIM written first: `/workspace/ferry-inbox/reviews/_z20_audit/CLAIM.txt`.

---

## 1. kems-088-ichise-1975 (Ichise, Kitano & Mori 1975, Trans. ISIJ 15:115–120)

**PDF:** 6 pp. scan (encrypted; print/copy OK). Text layer noisy but tables recoverable; visual confirm on rasters.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 2 (`S`, `Fe`) |
| Observations | **16** |
| fidelity_samples | 4 |
| Main tables | T1 pure-Fe assay; T2 ion ratios (8×%S × 3 T + tangent); T3 ε_S^(S) / e_S^(S) |

### Sample = ALL 16 (numeric grids ≫ 20 cells)

| Observation / pin | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `ichise_1975_kems_geometry_and_calibration` | p.116 §4 / Fig.2 | Hitachi RM-6K; Al₂O₃ cell / Ta holder; orifice **0.5 φ**; chamber **30 V**; source **200°C**; slits **0.006 / 0.05 mm**; R≈**2000**; P_cell **(4–6)×10⁻⁶ Torr**; P_source **(5–7)×10⁻⁷ Torr**; ~**10 g**; max ~**1650°C**; ~2 h; mother Fe-23%S; Coulomatic S | yes |
| `ichise_1975_table1_pure_iron_composition` | Table 1 p.116 | C **0.002**; Si **0.005–0.010**; Mn **&lt;0.01**; Cu **0.005–0.01**; Ni/Cr **&lt;0.01**; Ti **&lt;0.005**; V **&lt;0.001**; Al **&lt;0.003**; O **&lt;0.005**; N **0.0014** | yes |
| `ichise_1975_ion_ratio_table2` (+ pin 4.485) | Table 2 p.117 | ALL 8 rows × {1550,1600,1650°C ln(I_Fe⁺/I_S₂⁺) + tangent×10³}: 0.31→**4.029/4.485/4.917/−31.12** … 4.32→**−0.336/−0.016/0.288/−21.87** | yes |
| `ichise_1975_ls_integrand_equations_7_to_9` | eqs 7–9 p.117 | slopes **−0.0189 / −0.0206 / −0.0216**; intercepts **0.377 / 0.375 / 0.376** | yes |
| `ichise_1975_epsilon_e_table3` (+ pin −0.049; eq12 pin) | Table 3 p.118 | ε **−6.6/−6.1/−5.8**; e **−0.053/−0.049/−0.047**; **e_S^(S)=−225/T+0.0704** | yes |
| `ichise_1975_fe_heat_of_vaporization_fig8` | Fig.8 / body p.118 | **82.4 kcal/g-atom**; T **1484–1584°C**; no mp inflection; authors caution absolute value | yes |
| Figs 1–10 figure_only admissions | pp.116–119 | captions + admission_status match; not digitised | yes |

**P0:** none. Trailing-zero YAML forms (e.g. 2.99 vs printed 2.990) are not wrong numbers.

**P1 latent (non-blocking):** possible S-loss bias note already in extract (`possible_S_loss_bias_e_1600C_range`); Fig.7 γ_S not tabulated (declared).

---

## 2. kems-111-ichise-1982 (Ichise et al. 1982, Trans. ISIJ 22:552–559)

**PDF:** 8 pp. OCR text layer usable; tables confirmed on rasters pp.555–558.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 2 (`Fe`, `Mo`) |
| Observations | **16** |
| fidelity_samples | 5 |
| Main tables | T1 λ (16 Ag-Fe); T2 a_Fe @ 1823 K (23 rows); T3 phase bounds; T4 partial molar; T5 ΔH^s/ΔH^v |

### Sample = ALL 16 (table cells ≫ 20)

| Observation / pin | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `ichise_1982_kems_geometry_and_calibration` | p.553 §II | HITACHI RM6K; alumina **9 mm ID / 11 mm H**; orifice **0.5 mm**; sample **2–3 g**; Ag foil ~**10 mg**; T_activity **1823 K**; T_Ag **1227 / 1234 K**; Mo purity **99.97%**; starting-Fe assay string matches | yes |
| `ichise_1982_table1_lambda` (+ pin 6.67) | Table 1 p.555 | ALL 16 λ + 4 group means (6.92 / 9.72 / 10.7 / 14.2) + used_in Ag-Fe-Mo mapping; T_Ag notes | yes |
| `ichise_1982_table2_a_Fe_1823K` (+ pin 0.897) | Table 2 p.555 | ALL 23 rows x_Mo / a_Fe / phases; Ag-Fe-Mo 13 **0.528\*** excluded with printed footnote | yes |
| `ichise_1982_table3_phase_boundaries` (+ pin 0.35) | Table 3 p.556 | liq+σ: **0.35 / 0.484**; σ+(Mo): **0.554 / 0.810** (YAML 0.81 = same number) | yes |
| `ichise_1982_table4_partial_molar_1823K` | Table 4 p.557 | Spot-checked x_Mo=0,0.1,0.2,0.35,0.484,0.5,0.554,0.81,0.9,1.0 a/γ/G^M/G^E Fe+Mo + printed err envelopes; footnote ΔH^m Fe **15.1**; Mo **7777 cal → 32.54 kJ** | yes |
| `ichise_1982_table5_heats` (+ pin 93.7) | Table 5 p.558 + body p.555 | This study **93.7 / 90.1** kcal; body kJ **392.0±0.8 / 377.0±1.0**; ΔH^m **15.1±1.7 kJ**; ΔH^s(α)298 **414 kJ = 99.0 kcal**; quoted Reese/Gilby/Kato/Selected rows match | yes |
| `ichise_1982_infinite_dilution_gamma` (+ pin 1.54) | §V + T4 | γ°_Mo(s) **1.54** (conclusion +0.32/−0.26 vs T4 +0.26/−0.21 — both retained as printed); γ°_Mo(l) **0.70**; γ°_Fe(l) **6.0(±1.1)** | yes |
| `ichise_1982_interaction_parameters_liquid` | eqs 16/21–23 | ε_Mo^Mo **4.1(±1.4)**; ε² **−11.0(±4.1)**; e **1.21×10⁻²**; e² **−1.1×10⁻⁴** | yes |
| `ichise_1982_deltaG_henrian_1wt_pct` | eqs 25–26 p.557–559 | **27100−54.1 T** J; **−71.5 kJ / −17.1 kcal** at 1823 K | yes |
| `ichise_1982_quoted_Mo_psat` | p.552 | **~3×10⁻¹⁰ atm** at 2000 K | yes |
| Figs 1–6 figure_only | pp.553–558 | labeled ΔH_m **15.1**, γ° pins, fit `−2.0₄ x²+6.0 x³` retained | yes |

**P0:** none.

**P1 latent:** conclusion vs Table 4 asymmetric uncertainty on γ°_Mo(s) already dual-recorded; Table 5 T_hi ink blot noted (body prints 1873 K); eq (11) subscript-4 on −2.04₄ preserved as `−2.0_4`.

---

## 3. kems-119-furukawa-1975 — SKIPPED

Concurrent seat **Z17** (`empirical/z17-murchison-furukawa-ueda-2026-09-23`) already completed fidelity on this extract and landed P0 fix tip **`e52066ac7`** (`γ°_Fe` **0.389→0.385**). Z20 does **not** re-audit, re-fix, rebase, or fight that tip. Assigned third PDF remains out of scope for this write-up’s verdict table.

---

## Product / push

| Item | Value |
| --- | --- |
| P0 count (Z20 audited) | **0** |
| Extract edits on Z20 branch | none |
| Tip | `2e9e17c3d` (= `origin/work-v064-green`) |
| Force-push | no |
| PDFs in git | no |
| Validator | not re-run (no edits) |
