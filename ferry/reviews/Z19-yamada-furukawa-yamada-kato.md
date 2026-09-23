# Z19 — FIDELITY AUDIT: kems-067 / kems-069 / kems-087 — 2026-09-23

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/z19-yamada-furukawa-yamada-kato-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `2493b19befba6b45764848a21a005330fd6acd3d` (review-only mailbox commit; no extract/P0 fix)  
**Worktree:** `/workspace/repos/wt/slot-y12-n12`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/`  
(`kems-067-yamada-1980.pdf`, `kems-069-furukawa-1976.pdf`, `kems-087-yamada-kato-1980.pdf`). **Never committed.**  
**Date:** 2026-09-23 ~00:40 EDT (America/Toronto) — BACKLOG 5  
**Audit scratch:** `/workspace/ferry-inbox/reviews/_z19_audit/` (rasters/OCR; not in git)

**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection (steering)

Assigned seat was Z18 Kato/Kambayashi/Ohara (`kems-049`, `kems-057`, `kems-058`). Concurrent seats already owned those:

| Extract | Owner (in progress / no PASS file yet when claimed) |
| --- | --- |
| `kems-049-kato-1993-ms-review` | `slot-z4` `empirical/z18-kato-usgs-robinot-2026-09-23` (`_z18_audit/kato`) |
| `kems-057-kambayashi-1985` | `slot-b565` `empirical/z16-kems-057-058-066-2026-09-23` |
| `kems-058-ohara-1987` | `slot-b565` (same) |

**SKIPPED** assigned trio to avoid duplicate work. Next prefer-list unaudited B5 audit-pdfs (066/095/119/murchison/usgs already claimed by Z16/Z17/Z18):

| Extract | Obs | Disposition |
| --- | ---: | --- |
| `kems-067-yamada-1980` | 18 | **AUDITED** |
| `kems-069-furukawa-1976` | 18 | **AUDITED** |
| `kems-087-yamada-kato-1980` | 16 | **AUDITED** |

## Batch verdict

| Extract | Nested obs | Sampled | **P0** | Tip change |
| --- | ---: | ---: | ---: | --- |
| kems-067-yamada-1980 | 18 | **ALL 18** + **full Table 1 (10×4)** + **full Table 2 (18 lit + This work)** + Fig.5 lit pins | **0** | none |
| kems-069-furukawa-1976 | 18 | **ALL 18** + **Table 1 ppm** + **full Table 2 (10×4)** + **full Table 4 Ti+Fe (11×4)** + Table 3 γ° lit | **0** | none |
| kems-087-yamada-kato-1980 | 16 | **ALL 16** + ε_P^P / e_P^P / Fig.6 lit ε (4) + apparatus pins | **0** | none |

**READY for V-style verify as “clean”** — extract store tip = green base; branch HEAD `2493b19befba` is review-only under ferry/reviews/.

**Validator:** `tools/validate_literature_extracts.py` on all three → **OK: 3 extract file(s) valid**.

---

## 1. kems-067-yamada-1980

**Citation:** Yamada & Kato, *Tetsu-to-Hagané* 66 (1980) 488–495 — Mass spectrometric study of activity in liquid Fe–Si at 1600 °C.  
**Extract:** `data/literature/extracts/kems-067-yamada-1980.yaml`  
**PDF:** 8 pp Japanese body + English captions/tables.

### Inventory

| Layer | Count |
| --- | ---: |
| Nested observations | **18** (Si 16 + Fe 1 + Al/proxy psat 1) |
| Table 1 ion-current rows | 10 |
| Table 2 literature + This work | 18 + This work |
| `fidelity_samples` | 7 |

### Method

1. Rasterize all 8 pages @ 200 dpi; vision+OCR Table 1 (p.490) and Table 2 (p.493).  
2. Cell-compare all Table 1 N_Si / I+_Si / I+_Fe / log columns.  
3. Cell-compare all Table 2 γ°_Si / ε_Si^Si / methods; This work 7.9×10⁻⁴ / 11.5±1.5.  
4. Cross-check apparatus: ThO₂ cell, orifice 0.4–0.7 mm, chamber 24 V, Fe 99.99 %, ~1 g, pure-Fe effusion ~4.6 mg/1 h @ 1600 °C, log γ°_Si = −3.1 (±0.15 integration / ±0.2 with N_Si=0.5 anchor).  
5. Fig.5 literature log γ_Si @ N_Si=0.5 average −0.33 (Chipman/Woolley/Schwerdtfeger/Murakami/Turkdogan).

### Sampled checks — Table 1 (p.490) — **40/40 OK**

| N_Si | I+_Si | I+_Fe | log | ext |
| ---: | ---: | ---: | ---: | --- |
| 0.058 | 0.61 | 2.132 | −3.34 | OK |
| 0.104 | 2.81 | 3.914 | −3.21 | OK |
| 0.185 | 11.28 | 3.239 | −2.82 | OK |
| 0.197 | 13.68 | 2.115 | −2.58 | OK |
| 0.199 | 16.19 | 2.211 | −2.53 | OK |
| 0.201 | 26.23 | 2.816 | −2.43 | OK |
| 0.266 | 76.36 | 1.852 | −1.95 | OK |
| 0.352 | 333.7 | 0.653 | −1.03 | OK |
| 0.388 | 688.2 | 0.560 | −0.717 | OK |
| 0.511 | 928.6 | 0.109 | −0.096 | OK |

### Sampled checks — Table 2 This work + lit spots — **OK**

This work: γ°_Si = **7.9×10⁻⁴**, ε_Si^Si = **11.5±1.5**. Spot lit: Gokcen 7.7×10⁻³; Fruehan 4.57×10⁻³ / 9.8; Woolley 1.35×10⁻³ / 12.7; Smith & Taylar 8.5×10⁻⁴ / 13.2; Hultgren 1.32×10⁻³ / 8* — all match.

### P0

**None.**

### Coverage (non-P0)

- Figs 3–4, 6–9 digitization trajectories figure-only / `admission_status` — intentional.  
- Uncertainty note correctly splits ±0.15 (integration curves) vs ±0.2 (with N_Si=0.5 assignment).

---

## 2. kems-069-furukawa-1976

**Citation:** Furukawa & Kato, *Trans. ISIJ* 16 (1976) 382–387 — Thermodynamic study of liquid Fe–Ti.  
**Extract:** `data/literature/extracts/kems-069-furukawa-1976.yaml`  
**PDF:** 6 pp English.

### Inventory

| Layer | Count |
| --- | ---: |
| Nested observations | **18** (Ti 17 + Fe Table 4 twin) |
| Table 2 ion-ratio rows | 10 |
| Table 4 activity grid | 11×4 (a_Ti, γ_Ti, a_Fe, γ_Fe) |
| `fidelity_samples` | 4 |

### Method

1. Vision Table 1 (ppm), Table 2 (p.384), Tables 3–4 (p.385).  
2. Full Table 2 + Table 4 cell compare; Table 3 γ° lit; heats −16.1 / −15.7; H^M max −3.7 @ 65 at% Ti.  
3. Apparatus: thoria cell OD 9.5/11 mm, h 11, wall/lid 1 mm, orifices 0.4/0.6/0.7 mm, 19 V ionizing, 48Ti+/54Fe+.  
4. Recorded print conflict: synopsis log γ_Ti intercept **+0.58** vs body eq.(6)/Fig.7 **+0.53** — both stored; not a wrong-number P0.

### Sampled checks — Table 2 — **40/40 OK**

All 10 rows (N_Ti, d log(I_Ti/I_Fe)/d(1/T), log @ 1600 °C, log @ 1550 °C) match extract (incl. −5080 / −2.676 / −2.750; +270 / +1.738 / +1.742).

### Sampled checks — Table 4 — **44/44 OK**

Full a_Ti / γ_Ti / a_Fe / γ_Fe grid incl. γ°_Ti **0.017±0.002**, γ°_Fe **0.043±0.002** and printed ± on selected rows.

### Sampled checks — Table 1 / Table 3 / heats — **OK**

Iron ppm Al40/C50/S40/Si70/O26; Ti Fe100/Ag0.1/…/Sn50; γ° lit Chipman 0.011 @1627 … Wagner 0.068 @1545; H_Ti^∞ −16.1, H_Fe^∞ −15.7 kcal·mol⁻¹; H^M max −3.7 @ 65 at% Ti.

### P0

**None.**

---

## 3. kems-087-yamada-kato-1980

**Citation:** Yamada & Kato, *Trans. ISIJ* 20 (1980) 244–250 — Activity of P in Fe–P alloys.  
**Extract:** `data/literature/extracts/kems-087-yamada-kato-1980.yaml`  
**PDF:** 7 pp English (prior L4 lane had PDF MISSING; now present in B5).

### Inventory

| Layer | Count |
| --- | ---: |
| Nested observations | **16** |
| Primary result | ε_P^P = 7.3±0.1; e_P^P = 0.054; ln γ_P = 7.3 N_P ([at% P]<2.7) |
| Fig.6 literature ε | 4 (Urbain/Frohberg/Ban-ya/Schenck) |
| `fidelity_samples` | 4 |

### Method

1. Synopsis + §I + §III + conclusions vs extract ε / e / ranges.  
2. Fig.6 literature ε vs intro prose (not figure-line vision alone).  
3. Apparatus: alumina cell, orifice 0.4–0.6 mm, Hitachi RM-6K, 22 eV, ~1.5 g, 1600±1 °C, Fe 99.99 %, Fe₃P 99.9 %, mother alloy 10.2 wt% P.  
4. Fig.9 average counts **812.7** @ 1.52 wt% (2.71 at%) P.

### Sampled checks — primary + lit — **OK**

| quantity | printed | extract |
| --- | --- | --- |
| ε_P^P | 7.3±0.1 | 7.3 / unc 0.1 |
| e_P^P | 0.054 | 0.054 |
| P window | 0.7–3.2 wt% (1.3–5.7 at%) | same |
| Urbain ε | 5.16 | 5.16 |
| Frohberg ε | 8.36 | 8.36 |
| Ban-ya ε | 4.26 | 4.26 |
| Schenck ε | 16.0 (N_P 0.3–0.45; 1515–1540 °C) | 16.0 + ranges |
| ionizing eV | 22 | 22 |
| sample mass | ~1.5 g | 1.5 |
| Fig.9 avg counts | 812.7 | 812.7 |
| orifice | 0.4–0.6 mm | same |

### P0

**None.**

### Coverage (non-P0)

- Figs 2–5, 7–8, 10 figure-only / admission_status.  
- Composition still `unsupported_print_form` string range for engine_point (L4 near-ready gap) — coverage/consumer, not wrong stored number.

---

## P1 (latent — not fixed)

- **kems-069:** English synopsis Darken intercept +0.58 vs body eq.(6)/Fig.7 +0.53 — both recorded as printed (print_conflict); not a wrong stored number.
- **kems-067:** Fig.4 English caption repeats Fig.3 I+_SiO/I+_Si wording while axis/Japanese body are I+_Si/I+_Fe — already flagged in extract.
- Figure-only ion-ratio / activity curves (all three) remain admission_status figure_only — cannot feed numeric scores until digitised.

## Commits / push

- **Extract patches:** none (P0=0)  
- **Review commit:** `ferry/reviews/Z19-yamada-furukawa-yamada-kato.md` on this branch  
- **No PDFs in git. No force-push.**

**P0: 0 · sampled ALL obs on all three · tip: `2493b19befba6b45764848a21a005330fd6acd3d` · READY**
