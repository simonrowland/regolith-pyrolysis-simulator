# Z15 FIDELITY AUDIT — markova-1983 / pound-1972 / wetzel-gail-2013

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z15 — BACKLOG-5 (three extracts deferred from Z14 prefer-list)  
**Repo / branch:** `regolith-empirical` @ `empirical/z15-markova-pound-wetzel-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2e9e17c3d138fdfba9269c493f82974a71153fa5` (unchanged; no P0 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z6`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `kems-025-markova-1983` | 22 | **AUDITED** (ALL) |
| `kems-003-pound-1972` | 18 | **AUDITED** (ALL) |
| `kems-011-wetzel-gail-2013` | 17 | **AUDITED** (ALL) |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| kems-025-markova-1983 | 22 | **ALL 22** | **0** | Figs 1–4 P(T) curves figure-only (declared); Motzfeldt orifice/cell geometry absent in 2-page abstract | none |
| kems-003-pound-1972 | 18 | **ALL 18** | **0** | Full Tables 1–3 many non-rail rows not extracted (ledger declares); Pt vacuum metadata off by 10× (P1) | none |
| kems-011-wetzel-gail-2013 | 17 | **ALL 17** | **0** | Optical/dielectric figures; Table 5 Rc printed 3.2×10¹³ vs prose assumption 3×10¹⁴ (table stored correctly) | none |

**Overall: PASS — no P0. No extract patch committed. Pushed FF (empty tip = green base).**

---

## Method

1. Markova / Pound: image-only scans → `pdftoppm` 150–300 dpi + direct page/crop reads; Markova table OCR cross-check.
2. Wetzel: `pdftotext -layout` (good text layer) + page renders for Tables 1–6 / §3.3.
3. Dump all species observations → JSON; cell-by-cell / quote-by-quote vs PDF.
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
5. `uv run python tools/validate_literature_extracts.py` on all three → **OK**.

---

## 1. kems-025-markova-1983 (Markova, Yakovlev, Semenov & Belov 1983 LPSC XIV 460–461)

**PDF:** 2 pp. ADS `1983LPI....14..460M`. Image-only scan.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 20 (`Al2O3` + vapor forms) |
| Observations | **22** (2× `rate_series` method/composition; 20× `psat_series` figure-only / detected-not-tabulated / Al proportion bound) |
| fidelity_samples | 4 |

### Sample = ALL 22

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| `…_kems_method_and_inventory` | p.460 Technique/Results | MS-1301; Knudsen cell; heating **3–7 grad/min**; detected list Na…O₂; FeO/CaO/MgO(?)/Al₂O₂ insignificant; release `(Na,K)-Fe-SiO-Mg,Ca,TiO-Al`; De Maria + Yakovlev refs | yes |
| `…_initial_compositions_table` | p.461 unnumbered table | **ALL 4×9 oxide cells + totals** match raster/OCR (SiO₂ 42.86/42.73/45.40/44.16 … FeO 0.55/5.54/4.25/8.84 … totals 100.05/99.16/99.96/100.72); MnO sample1 **0.00** printed; footnote lithologies + 68415,40 caption | yes |
| `…_al_alo_al2o_proportion_bound` | p.460 | “proportions of AlO and Al₂O were not higher than **10%**”; Al prevailed | yes |
| 11× `…_psat_figs_figure_only` (Al,Na,K,Fe,SiO,Mg,Ca,AlO,Al2O,TiO,FeO,CaO) | Figs 1–4 | `admission_status: figure_only`; axes lg Pᵢ torr vs T°C 1000–2500 → T_K 1273.15–2773.15; sample↔fig mapping 1–4 | yes (declared figure-only; no invented digitizations) |
| 7× `…_detected_not_tabulated` (PO,PO2,TiO2,O,O2,Al2O2,MgO) | p.460 | Detected / insignificant / MgO(?) uncertain — values not printed | yes |

### Coverage

- Stored: method inventory, full initial-composition grid, Al-form bound, figure-only stubs for labeled P(T) species, detected-not-tabulated stubs.
- Not extracted (acceptable): digitized Fig 1–4 pressure points (figure-only policy); Motzfeldt geometry (absent in abstract).

### P0 / P1

- **P0: 0**
- **P1 (latent):** FeO row sometimes dropped by OCR; PDF raster confirms YAML. Release-sequence punctuation varies slightly vs prose arrows.

---

## 2. kems-003-pound-1972 (Pound 1972 JPCRD 1:135–146)

**PDF:** 12 pp. Image-only; Tables 1–3 landscape (PDF pp. 7–12 = printed 141–146).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 10 (Fe,Cr,Be,B,C,Ag,K,Pt,Rh,W) |
| Observations | **18** (13× `alpha` + 5× `rate_series` refusal/complements/method) |
| fidelity_samples | 8 |

### Sample = ALL 18

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| Fe Wessel α | T1 p.141 | **1 ± 0.2**; **1540–1740 K**; vacuum 10⁻⁵ torr; Langmuir-Knudsen | yes |
| Fe McCabe α | T1 p.141 | **0.9 ± 0.1**; **1358–1520 K**; McCabe et al. **[1956]** (corrections ledger) | yes |
| Fe class_b1 / 1600–1800 refusal / complements / completeness | T1 + methods | 1600–1800 K **not** a printed Fe band; Tables 1–3 page map 141–146; Clausing qualitative | yes |
| Cr McCabe α + class_b1 + complements | T1 p.141 | **0.9 ± 0.1**; **1318–1563 K** | yes |
| Be Holden α | T1 p.141 | **1.0 ± 0.02**; **1171–1552 K ±1**; torsion balance | yes |
| B Burns α | T1/T2 | **0.98 ± 0.02** at mp **2403 ± 40 K** | yes |
| C Marshall–Norton α | T1 | **1 ± 0.1**; **2357–2870 K**; 10⁻⁸ torr | yes |
| Ag Wessel α | T1 p.142 | **>0.92** just below mp **1234 ± 3 K**; 10⁻⁵ torr | yes |
| K Neumann α | T2 p.143 | **0.95 ± 0.05**; **66.7–119.3 °C** → 339.85–392.45 K | yes |
| Pt Chupka α_c | T3 p.145 | **>0.998**; substrate **1500 ± 20 K**; supersat ~10⁴; mass spec | yes (α, T) |
| Rh Chupka α_c | T3 p.145 | **>0.99**; **1500 ± 20 K**; vacuum **10⁻⁶** torr | yes |
| W Chupka α_c | T3 p.146 | **0.998 ± 0.0005**; **2200 ± 20 K**; vacuum 10⁻⁶ torr | yes |

### Coverage

- Rail-relevant Table 1–3 rows for Fe/Cr/Be/B/C/Ag/K/Pt/Rh/W represented.
- Many other Table 1–3 substances (Cd, Au, salts, organics, …) intentionally not extracted; completeness ledger states scope.

### P0 / P1

- **P0: 0**
- **P1 (latent):** `pound_1972_pt_condensation_alpha_chupka_1500k` stores `values.vacuum: 1.0e-6 torr`; PDF Table 3 Pt @1500 K vacuum column is **10⁻⁷ torr**. α and T correct — vacuum is ancillary metadata (same class as Z8 height-unit latents). Rh vacuum 10⁻⁶ matches. Second Pt row @600 K and second W @900 K not separately stored (high-T rows kept).

---

## 3. kems-011-wetzel-gail-2013 (Wetzel, Klevenz, Gail, Pucci & Trieloff 2013 A&A 553:A92)

**PDF:** 13 pp. Good text layer.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 1 (`SiO`) |
| Observations | **17** (4× `alpha` + 7× `rate_series` + 6× `gibbs_table` model admissions) |
| fidelity_samples | 9 |

### Sample = ALL 17

| Observation | Locator | Key checks vs PDF | OK? |
| --- | --- | --- | --- |
| α Arrhenius + fit_quoted + extrapolation | §3.3 Eq.(7)–(8) p.7 | Rocabois **α(T)=0.1687−2.909×10⁻⁴T+1.373×10⁻⁷T²** (1175–1410 K); paper **α(T)=0.52 exp(−3685/T)**; **α≈0.013** @1000 K | yes |
| growth class_b1 | §3.3 / Fig context | condensation/growth α_s not melt α_e; competing semantics | yes |
| Ta Knudsen geometry partial | §2.1 | tantalum Knudsen cell; P_bg ≤ **1×10⁻⁹ mbar**; orifice/Clausing missing (declared) | yes |
| Table 1 (rate + gibbs) | p.4 | osc 1–4: 1100/329/47, 983/709/57, 715/305/76, 384/469/113; **ε∞=3.61** | yes |
| Table 2 pins | p.4 | 40 K row 1: **1077/336/52**; 300 K matches T1 | yes (sampled pins + structure) |
| Table 3 (rate + gibbs) | p.5 | ALL 4 oscillator a±err / b±err grids match PDF | yes |
| Table 4 | p.7 | SiO **44.09 / 2.13 / Si / 3.55×10⁻⁵**; Iron/Olivine/Pyroxene rows match | yes |
| Table 5 | p.8 | Vd **3.46×10⁻²³**; α **0.013**; amax **0.0837**; Tc **700**; vth **1.54×10⁴**; Rc **3.2×10¹³** | yes |
| Table 6 | p.8 | Teff 2700 K; L* 1×10⁴ L☉; R* 3.18×10¹³ cm; vexp 10 km/s; Ṁ range; f_SiO 0.5; Ra 1×10⁵ R*; Tc 700 K | yes |

### Coverage

- Brendel oscillator Tables 1–3, dust Tables 4–6, growth α fit, Ta source note represented.
- Not extracted (acceptable): full Table 2 temperature grid beyond pins; transmittance figures; radiative-transfer figure curves.

### P0 / P1

- **P0: 0**
- **P1 (latent):** Prose later assumes Rc=3×10¹⁴ cm for a dilution estimate; Table 5 stores printed **3.2×10¹³** (correct). Bulk-film density 2.18 g cm⁻³ in §2 ≠ Table 4 model ρd 2.13 (different quantities). Companion extract `wetzel-gail-2013-sio-arrhenius` not in this seat.

---

## Validator

```text
uv run python tools/validate_literature_extracts.py \
  data/literature/extracts/kems-025-markova-1983.yaml \
  data/literature/extracts/kems-003-pound-1972.yaml \
  data/literature/extracts/kems-011-wetzel-gail-2013.yaml
→ OK: 3 extract file(s) valid
```

## Push

FF push of branch tip = green base. No force-push. No PDF in git. No extract edit (P0=0).
