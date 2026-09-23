# Z11 — FIDELITY AUDIT batch: kems-020 / robinot-2025 / sossi-2020 — 2026-09-22

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/z11-hastie-robinot-sossi-2026-09-22`  
**Base / tip:** `origin/work-v064-green` @ `2e9e17c3d` (**no fix commit**)  
**Worktree:** `/workspace/repos/wt/slot-y6-n6`  
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/`  
(`kems-020-hastie-1981-nbsir.pdf`, `robinot-2025-promes-review.pdf`, `sossi-2020-cu-zn-isotope-evap-formalism.pdf`). **Never committed.**  
**Date:** 2026-09-22 (America/Toronto) — BACKLOG 4  
**Audit scratch:** `/workspace/ferry-inbox/reviews/_z11_audit/` (OCR/rasters; not in git)

## Batch verdict

| Extract | Nested obs | Sampled numeric cells | **P0** | Tip change |
|---|---:|---:|---:|---|
| kems-020-hastie-1981-nbsir | 38 | **≥80** (Table 2 17×A/B; NaCl/Na₂SO₄ prose; Table 3 24-cell grid; residues/orifice/illite pins) | **0** | none |
| robinot-2025-promes-review | 29 | **≥90** (Table 1 33; Table 2 ≥47 T/P/power; Table 3 27; Table 4 12) | **0** | none |
| sossi-2020-cu-zn-isotope-evap-formalism | 7 (+ table rows) | **505** Table 1 Cu/Zn fields + **6** α + logK★ subset | **0** | none |

**READY for V-style verify as “clean”** — tip unchanged at `2e9e17c3d`; branch pushed FF (no new commit).

---

## 1. kems-020-hastie-1981-nbsir

**Citation:** Hastie, Plante & Bonnell (1981), *Alkali Vapor Transport in Coal Conversion and Combustion Systems*, NBSIR 81-2279. DOI `10.6028/NBS.IR.81-2279`.  
**Extract:** `data/literature/extracts/kems-020-hastie-1981-nbsir.yaml`  
**PDF:** 84 pp scan; tables/prose OCR + rotated Table 2 rasters (pdftoppm 200 dpi, −90° / r270).

### Inventory

| Layer | Count |
|---|---|
| Nested `species.*.observations` | **38** |
| Species keys | K, Na, Na2SO4, K2O, SiO, NaCl, KCl |
| Table 2 log P fit rows | 17 (parent + quoted sibling) |
| Table 3 SOLGASMIX grid cells | 24 numeric (+ dashes as `not_reported`) |
| Digitized figure points | Fig. 5 (6), Fig. 11 (K₂O γ series) |
| `fidelity_samples` pins | 6 |

### Method

1. Rasterize PDF pp. 17–18 (NaCl prose), 29–31 (Table 2 landscape), 49 (Table 3); OCR r270 for Table 2.  
2. Compare every Table 2 A/B coefficient (17/17) in both parent and quoted observations.  
3. Cross-check NaCl / Na₂SO₄ second-law prose, Table 3 full species grid, glass 17/12/71, gas quote H₂O 4 / O₂ 5 / CO₂ 12 / N₂ 76 / SO₂ 2 / HCl 1, orifice 0.34 mm, residue Na₂O 13.3 / 16.9 / 0.45 wt% Na₂CO₃, illite K₂O 7.4 / Al₂O₃ 26.0.  
4. Spot Fig. 5 digitization residuals (≤0.004 dex vs pass-2) — digitization uncertainty flagged 0.1 dex; not scored as P0.

### Sampled checks (≥20)

#### Table 2 K log P coefficients (report pp. 23–25) — **17/17 A+B OK**

| system (short) | A printed/ext | B printed/ext |
|---|---:|---:|
| K₂O–SiO₂ | 4.721 / 4.721 | 15624 / 15624 |
| K₂O–Al₂O₃ (KAlO₂) | 5.489 / 5.489 | 15036 / 15036 |
| K₂O–9Al₂O₃ | 7.487 / 7.487 | 21453 / 21453 |
| K₂O–ZrO₂ | 4.524 / 4.524 | 12873 / 12873 |
| K₂O–Fe₂O₃ (phase bdry) | 4.524 / 4.524 | 12873 / 12873 |
| K₂O–6Fe₂O₃–Fe₂O₃ | 7.028 / 7.028 | 16750 / 16750 |
| KAlSiO₄ | 8.722 / 8.722 | 20923 / 20923 |
| ~22[K₂O] | 7.068 / 7.068 | 20763 / 20763 |
| ~17[K₂O] | **6.350 / 6.35** | 20693 / 20693 |
| ~11.5[K₂O] | 4.667 / 4.667 | 19800 / 19800 |
| 10.8[K₂O]+mullite | 4.464 / 4.464 | 20424 / 20424 |
| Western slags | 5.228 / 5.228 | 16794 / 16794 |
| Real MHD K1 | 5.564 / 5.564 | 16650 / 16650 |
| Synth. Western | 6.818 / 6.818 | 18472 / 18472 |
| Synth. Eastern | 6.831 / 6.831 | 19056 / 19056 |
| Illite | **6.286 / 6.286** | **20642 / 20642** |
| Low-melt K2 slag | 6.231 / 6.231 | 17863 / 17863 |

Sibling note stores printed spelling `6.350` with numeric 6.35 — OK.

#### NaCl / Na₂SO₄ prose (pp. 11–12) — **8/8 OK**

| quantity | printed / ext |
|---|---|
| log P NaCl A, B | 4.85, 8820 / 4.85, 8820 |
| ΔHᵥ(1300), ΔSᵥ | 40.4, 22.2 / 40.4, 22.2 |
| dimer −ΔH, −ΔS | 46.4, 30.2 / 46.4, 30.2 |
| Na₂SO₄ ΔH(1550), ΔS | 69.8, 27.8 / 69.8, 27.8 |

#### Table 3 SOLGASMIX grid (p. 43) — **24/24 OK**

Glass 17/12/71 wt%; T=1400 K; P=1 atm. Grid matches OCR/vision including Total Na non-ideal gas **1.1(−2)=0.011**, Na₂O activity non-ideal **3.4(−8)**, ideal Na **2.2(−4)**. Gas quote in extract: CO₂ **(12)** (not OCR-garbled “(2)” from full-page pass).

#### Equipment / residue pins — **OK**

Orifice **0.34 mm**; KMS(3) final Na₂O **13.3**; TMS(2) **16.9**; impurity Na₂CO₃ **0.45 wt%**; illite K₂O **7.4**.

### P0

**None.**

---

## 2. robinot-2025-promes-review

**Citation:** Robinot et al. (2025), Acta Astronautica 234:242–259, DOI `10.1016/j.actaastro.2025.05.008` (HAL OA).  
**Extract:** `data/literature/extracts/robinot-2025-promes-review.yaml`  
**PDF:** 19 pp; Tables 1/3/4 via pdftotext; Table 2 image on published p. 247 (PDF p. 7) via 200 dpi raster + vision.

### Inventory

| Layer | Count |
|---|---|
| Nested observations | **29** (1×SiO₂ Table 1 + 28×O₂ Tables 2–4) |
| Table 2 experiment rows | 22 |
| `fidelity_samples` | 4 |

### Method

1. pdftotext Tables 1, 3, 4; raster+vision Table 2.  
2. Cell-compare all Table 1 oxide min/max/avg (11×3).  
3. Spot ≥47 Table 2 T_min/T_max/P_min/P_max/power fields vs printed (incl. as-printed P_min>P_max on Senior rows; CISRO typo retained).  
4. Full Table 3 (3×9) and Table 4 (3×4) vs text.

### Sampled checks

#### Table 1 lunar oxides (p. 243) — **33/33 OK**

| oxide | min / max / avg printed ≡ ext |
|---|---|
| SiO₂ | 40.60 / 48.10 / **44.59** |
| TiO₂ | 0.47 / 8.40 / 2.58 |
| Al₂O₃ | 9.70 / 28.00 / 17.82 |
| … through P₂O₃ | 0.05 / 0.51 / 0.16 |

(`P2O3_as_printed` note retained — paper spelling.)

#### Table 2 vacuum-pyrolysis summary (p. 247) — **≥40/47 key fields OK**

Steurer SiO₂ T=**2375**; Senior FeTiO₃ 1926–2126, P 5.30E−04→2.00E−04 (min>max **as printed**); Sauerborn power **165**\*; Matchett **365**\*; Tanaka Al₂O₃ 2826–3426, 51–260 W/cm²; Shaw **132**; Li 2023 powers **5969 / 3979 / 9948**; Šeško EAC-1 1476–1626, P 1E−10–1E−05; Li 2024 FeO 1726–2176. Institution **CISRO** retained as printed (paper typo).

#### Tables 3–4 Li 2023 (pp. 249–250) — **all OK**

| sample | T3 power | T4 Fe wt% | O₂ purity | Fe powder |
|---|---:|---|---|---|
| Fe₂O₃ | **5969** | **27.1 %** | 99.5 % | 90 % |
| FeTiO₃/Fe₂O₃/SiO₂ | 3979 | 30 % | 99.0 % | 85 % |
| Regolith simulant | 9948 | Unreported | 98.5 % | 45 % |

Distances 50/30/30 mm; angles Unreported/45/60; P 10⁻⁸ / 10⁻⁸ / 10⁻⁹ — match.

### P0

**None.**

---

## 3. sossi-2020-cu-zn-isotope-evap-formalism

**Citation:** Sossi et al. (2020), GCA 288:316–340, DOI `10.1016/j.gca.2020.08.011`.  
**Extract:** `data/literature/extracts/sossi-2020-cu-zn-isotope-evap-formalism.yaml`  
**PDF:** 26 pp clean text (pdftotext −layout).

### Inventory

| Layer | Count |
|---|---|
| Nested observations | 7 |
| Table 1 Cu measured rows | **34** |
| Table 1 Zn measured rows | **36** |
| logK★ Cu / Zn | 20 / 20 selected |
| Fractionation α series | Cu 2 + Zn 4 |
| `fidelity_samples` | 2 (row‑0 Cu & Zn) |

### Method

1. Parse Table 1 run lines from PDF pp. 6–7.  
2. Automated field match: T, log fO₂, time, ppm, ln f, δ, 2SD for every Cu/Zn row (**505/505 OK**; earlier false misses were prose collisions on run IDs).  
3. Verify reduced α against abstract + results: Cu air **0.9972±0.0001 (N=17)**; Cu log fO₂=−8 **0.9979**; Zn **0.9959 / 0.9967 / 0.9974 / 0.9978** at −0.68 / −3 / −5.5 / −8.  
4. Spot logK★ values embedded in Table 1 (e.g. run P 5/04/17b Cu −2.72).

### Sampled checks (highlights)

| run | field | printed / ext |
|---|---|---|
| P 5/04/17b | Cu ppm, Zn ppm, T | 6580, 4983, 1500 / same |
| P 10/04/17a | Cu ppm | 11,113 / 11113 |
| P 20/06/18b | Cu ppm, log fO₂ | 91, −8.03 / 91, −8.03 |
| P 01/08/18d | Cu ppm, δ⁶⁵Cu | 62, 11.96 / 62, 11.96 |
| α Cu air | 0.9972 ± 0.0001 | 0.9972 / unc 0.0001 / N 17 |
| α Zn air | 0.9959 ± 0.0002 | 0.9959 / unc 0.0002 / N 8 |

### P0

**None.**

---

## Validation

```
python tools/validate_literature_extracts.py \
  data/literature/extracts/kems-020-hastie-1981-nbsir.yaml \
  data/literature/extracts/robinot-2025-promes-review.yaml \
  data/literature/extracts/sossi-2020-cu-zn-isotope-evap-formalism.yaml
# → OK: 3 extract file(s) valid
```

No YAML edits; extracts-v2 left untouched (derived / battery schema — out of scope for this seat).

## Push

```
git push -u origin empirical/z11-hastie-robinot-sossi-2026-09-22
# FF: tip == origin/work-v064-green @ 2e9e17c3d (no unique commits)
```
