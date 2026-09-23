# Z17 FIDELITY AUDIT — murchison-degassing / furukawa-1975 / ueda-1986

**Date:** 2026-09-23 (America/Toronto, EDT)  
**Seat:** Z17 — BACKLOG 5, three remaining prefers  
**Repo / branch:** `regolith-pyrolysis-simulator` @ `empirical/z17-murchison-furukawa-ueda-2026-09-23`  
**Base:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `e52066ac75ba064cbacee248fe97c0c3f2c912d7` (1 fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z17`  
**PDFs:** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/` (never committed)  
**Rule:** P0 = wrong number stored that can reach a result/score/ledger today. Fix P0 on branch. Latent ≤ P1. No invented data.

## Candidate selection

Prefer list (not already owned by `reviews/Z1*.md`–`Z16*.md`; Z15 = miller/markova/halwax; Z16 = kems-057/058/066):

| Extract | Obs (YAML top-level) | Disposition |
| --- | ---: | --- |
| `murchison-degassing-2023-springer` | 27 | **AUDITED** |
| `kems-119-furukawa-1975` | 21 | **AUDITED** (P0×1 fixed) |
| `kems-095-ueda-1986` | 18 | **AUDITED** |
| kems-111-ichise-1982 | 17 | deferred |
| wilkerson-2021-jsc1a-tga-ms-poster | 17 | deferred |
| kems-190-wu-1993 | 16 | deferred |
| usgs-lunar-sourcebook-tab8-1 | compilation grid | deferred |

## Verdict

| Extract | Obs (YAML) | Sampled | P0 mismatches | Coverage gaps | Fix commit |
| --- | ---: | ---: | ---: | --- | --- |
| murchison-degassing-2023-springer | 27 | **ALL 27** obs; **ALL** Table 1 (24 cells) + Table 2 (80 cells) + Table 4 Chelyabinsk (8 cells) + Boudouard Kp fit | **0** | Table 3 IR band assignments qualitative (not numeric yields); Fig. 3 IR figure-only; aliquot mass true-absence | none |
| kems-119-furukawa-1975 | 21 | **ALL 21** obs; **ALL** Table 1 (3 rows) + Table 2 (9×3) + Table 3 (8×5) + γ/HM/quoted pins | **1 fixed** (γ°_Fe 0.389→0.385) | Figs 4–12 figure-only (typed); Motzfeldt geometry missing (typed); Table 3 N_V=0.3 block mole-fraction sum 1.10 as printed | `e52066ac7` |
| kems-095-ueda-1986 | 18 | **ALL 18** obs; **ALL** Table 2 (11×12 γ cells) + Table 1 (9 XRD rows) + mixing pins + geometry | **0** | Figs 1–13 figure-only (typed); Japanese body beyond captions/numerals unseen (declared); Motzfeldt missing (typed) | none |

**Overall: LAND-WITH-FIXES — P0=1 (Furukawa γ°_Fe). Pushed FF.**

---

## Method

1. `pdftotext -layout` (+ per-page) under `/workspace/ferry-inbox/reviews/_z17_audit/{murchison,furukawa,ueda}/`.
2. `pdftoppm -png` 200–350 dpi for Furukawa Tables 1–3 / Fig.6 (pp.3052–3058), Ueda Tables 1–2 (pp.1083, 1085), Murchison Tables 1–2/4 (PDF pp.6, 10).
3. Dump all species observations → JSON; mechanical cell compare (Murchison) + vision/OCR pin checks (JP tables).
4. Fields checked: **value / uncertainty / unit / basis / sign / T / locator / statement_as_published** vs PDF.
5. `uv run python tools/validate_literature_extracts.py` on all three → **OK** (post-fix).

---

## 1. murchison-degassing-2023-springer (Voropaev et al. 2023, Astron. Vestn. 57:571–582)

**PDF:** 12 pp. Russian OA. Text layer excellent for Tables 1–2, 4.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | 9 (`Murchison_CM2` + 8 gases) |
| Observations | **27** |
| Main numeric payload | Table 1 isothermal µg/g; Table 2 stepwise µg/g; Table 4 Chelyabinsk column; Boudouard `lg(Kp)=-9001/T+9.28` |

### Sample = ALL tables

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| Table 1 all 8×3 yield±σ | points `yield_ug_g` / `uncertainty_ug_g` | p.576 text layer | **24/24** |
| Table 2 all 8×(9 T + total) | incl. printed zeros without ± | p.576 | **80/80** |
| Table 4 Chelyabinsk 8 gases | COS printed `0` preserved | p.580 | **8/8** |
| Boudouard | `lg(Kp) = -9001/T + 9.28` | eq. (2) p.576 (slash restored vs OCR “–9001 T”) | yes |
| Method / IR Fig.3 | method_geometry + figure_only | apparatus pp.573–575; Fig.3 p.577 | yes |

### Coverage

- Stored: Tables **1, 2, 4** (Chelyabinsk quoted; Murchison 800 °C not duplicated — points back to Table 1).
- Not extracted as yields (acceptable): Table 3 IR band assignments; Fig. 1–3 curves figure-only; aliquot mass true-absence (µg/g specific yields).
- Prose cites CO₂ 4045 / CO 10218 µg/g; extract correctly keeps **table** cells 4040±40 / 10200±100.

**P0: 0.**

---

## 2. kems-119-furukawa-1975 (Furukawa & Kato 1975, Tetsu-to-Hagané 61:3050–3059)

**PDF:** 10 pp. Japanese body; English figure/table captions. Tables image-set (vision + OCR).

### Inventory

| Layer | Count |
| --- | ---: |
| Species | Fe, V, Cr |
| Observations | **21** (geometry, Table 1–3, γ fits, γ°, Hᴹ, liquidus/solidus, quoted priors, figure-only) |

### Sample = ALL main tables + pins

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| Table 1 Fe/V/Cr assays | Al 0.004, C 0.005, O 0.0026, Fe>99.97; V C/Fe/Si/H/O/N; Cr Pb&lt;5 ppm, Mg&lt;1 ppm, Cr>99.999% | p.3052 render | yes |
| Table 2 all 9 rows | N_V, dlog/d(1/T), log(I_V⁺/I_Fe⁺)@1600 °C | p.3054 (−10690/−2.641 … +0.270) | **9/9** |
| Table 3 all 8 rows | N_V/N_Fe/N_Cr + two log ratios; N_V=0.3 block sum **1.10 as printed** | p.3057 | **8/8** |
| eqs (9)(10) | log γ_V = −1.02 N_Fe² + 0.24; log γ_Fe = −0.11 N_V² − 0.31 | p.3057 | yes |
| γ°_V p.3056 | **0.16₃ → 0.163** | Fig.6 label + prose | yes |
| γ°_Fe p.3056 | was **0.389 / 0.38_9** | printed **0.38₅ → 0.385** (Fig.6 + prose) | **P0 fixed** |
| γ° conclusions | 0.16 (solid V), 0.39 (liquid Fe) | p.3059 | yes |
| Hᴹ_∞ | Hᴹ_Fe=−4.5, Hᴹ_V=−8.6 kcal·mol⁻¹; Hᴹ_max=−1.9±0.4 @ N_V=0.44 | p.3057/3059 | yes |
| ternary fits N_V=0.2/0.3 | −0.42+0.70 N_Cr etc. | p.3058–3059 | yes |
| quoted Chipman/Fruehan/Kay | γ°_V 0.12 / 0.10 / 0.21 | intro | yes |
| liquidus/solidus 1600 °C | 66 / 69 at% V; mp extrap. 1912 °C | Fig.3 discussion | yes |

### P0 fix

- **Pre:** `gamma0_Fe_as_printed_p3056: 0.389` with note `0.38_9`.
- **PDF:** infinite-dilution γ°_Fe printed **0.38₅** (subscript 5) on p.3056 and labeled on Fig. 6; conclusions round to **0.39**.
- **Post:** `0.385` / `0.38_5`. extracts-v2 locator note updated (v2 value remains `unavailable` for unsupported quantity — extract-v1 is authority).
- Commit: `e52066ac7` — `fix(kems-119): correct gamma0_Fe 0.38_9 → 0.38_5 (p.3056)`.

### Coverage / notes

- Figs 4–12: typed `figure_only` / `bound_not_point_ordering` — not digitised (correct refusal).
- Fig.6 observation fields `labeled_two_phase_N_Fe/N_V` hold the printed **γ° labels** (0.385 / 0.163), not two-phase compositions (liquidus/solidus are on a separate obs). Naming quirk ≤ P2; values match Fig.6 after the γ°_Fe fix.
- Motzfeldt orifice area / Clausing / sample area: typed missing.
- Synopsis vs body N_Fe range 0.02 vs 0.20: both recorded as printed (already noted in extract).

---

## 3. kems-095-ueda-1986 (Ueda, Nishi, Oishi & Ono 1986, J. Japan Inst. Metals 50:1081–1088)

**PDF:** 8 pp. Japanese body; English abstract + table/figure captions.

### Inventory

| Layer | Count |
| --- | ---: |
| Species | Ti, Co (+ figure-only bags) |
| Observations | **18** (geometry, Table 1 XRD, Table 2 γ_Ti + γ_Co, mixing functions, 13 figure-only) |

### Sample = ALL tables + abstract/conclusion pins

| Check | Extract | PDF | OK? |
| --- | --- | --- | --- |
| Table 2 γ_Ti/γ_Co B/T/Δ @ 1873 & 1973 K | all N_Co=0.0…1.0 (11 rows × 12 cells) | p.1085 render | **ALL match** (spot+full grid vs vision) |
| γ° pins | Ti 0.011/0.021; Co 0.014/0.028 (1973 ternary 0.028) | abstract + conclusion (1) | yes |
| Table 1 XRD phases | N_Co 0.1–0.9 experimental vs Fig.1 equilibrium | p.1083 | yes (9/9 rows stored) |
| Mixing ΔGᴹ_min | −32.1 (1873), −30.9 (1973) @ N_Co=0.49 | abstract + §5/conclusion (3) | yes |
| ΔHᴹ_min / ΔH̅_∞ | −50.5 @ 0.48; Ti −152.2; Co −195.7 kJ·mol⁻¹ | same | yes |
| ΔSᴹ_min | abstract/conclusion **−9.9**; §5 prints **9.9** without minus | both recorded (`print_conflict`) | yes |
| Geometry | ANELVA NAG-531; Y₂O₃ cell 11/9/7/6 mm; orifice 0.5–0.8 mm; 0.9–1.2 g; 1840–2020 K; 10.8–14.4 ks; 3×10⁻⁵ Pa; ⁴⁸Ti 73.45% / ⁵⁹Co 100% | pp.1082–1083 + abstract | yes |

### Coverage

- Stored: Tables **1–2**, mixing summary numbers, method geometry.
- Figure-only (typed): Figs 1–13 — not digitised.
- Japanese prose beyond English captions/numerals/abstract: declared unseen (not guessed).

**P0: 0.**

---

## Validation

```text
uv run python tools/validate_literature_extracts.py \
  data/literature/extracts/murchison-degassing-2023-springer.yaml \
  data/literature/extracts/kems-119-furukawa-1975.yaml \
  data/literature/extracts/kems-095-ueda-1986.yaml
→ OK: 3 extract file(s) valid
```

## Git

```text
git push -u origin empirical/z17-murchison-furukawa-ueda-2026-09-23
```

- Base `2e9e17c3d` → tip `e52066ac7` (1 commit ahead).
- Files: `data/literature/extracts/kems-119-furukawa-1975.yaml`, `data/literature/extracts-v2/kems-119-furukawa-1975.yaml`.
- **No PDFs in git.**

## Scratch

`/workspace/ferry-inbox/reviews/_z17_audit/` — layout/raw text, JSON dumps, PNG page renders (not for commit).

---

**Z17 murchison-degassing + kems-119-furukawa-1975 + kems-095-ueda-1986 · sampled ALL obs + full main-table cells · P0: 1 fixed (γ°_Fe 0.385) · tip `e52066ac7` · validator OK · READY**
