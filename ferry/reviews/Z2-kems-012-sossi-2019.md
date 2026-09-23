# Z2 — FIDELITY AUDIT kems-012-sossi-2019 — 2026-09-22

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/z2-kems-012-sossi-2019-2026-09-22`  
**Base / tip:** `origin/work-v064-green` @ `2e9e17c3d` (no fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z2`  
**PDF:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/kems-012-sossi-2019.pdf` (HAL A4, 59 pp; Tables 1–6 on PDF pp. 53–59). **Never committed.**  
**Extract:** `data/literature/extracts/kems-012-sossi-2019.yaml`  
**Date:** 2026-09-22 (America/Toronto)

## Verdict

**P0: 0** — no wrong numeric value found in sampled cells (value / unit / basis / sign / T / P / fO2 / run mapping).  
**Coverage gaps (non-P0):** documented below; not fixed (P0-only lane).  
**READY for V-style verify as “clean”** — tip unchanged.

## Inventory

| Layer | Count |
|---|---|
| Nested `species.*.observations` | 106 (53 live + 53 `*_quoted_20260906` superseders) |
| Expanded series points + scalars | **526** (= 430 `values.series` points + 96 non-series obs) |
| Species keys | 21 (Na K Mn Ti Sc V Zr La Gd Yb Li Rb Cu Ag Zn Ge Cd Ga Pb Mo Cr) |
| Digitized Table 2 residue series | Mn, Ti (parents) + Sc V Zr La Gd Yb (quoted superseders); **43 runs each** |
| Tables cited on locators | 1–6 (main paper tables) |

`deepening.policy`: quoted rows supersede parents; consumers must honor `supersedes`.

## Method

1. `pdftotext -layout` Tables 1–6 (PDF pp. 53–59) + raster check of Table 1.
2. Parse Table 2 run grid (43 experimental rows; blanks and empty OCR row `2M-15-07-16f` excluded as in extract notes).
3. Automated compare of all 43×8 residue series (Mn Ti Sc V Zr La Gd Yb) vs PDF: `residue_ppm`, `T_C`, `t_min`, `log10_fO2`, `loop`.
4. Ground-truth Table 3 logK* grid (18 printed element/reaction rows) vs all 17 non-quoted logK* observations (~112 cells: logK*, unc, N, t0, n).
5. Spot Table 4 γ (this work), Table 5 ΔH*/ΔS*/pure-system, Table 6 Mn Te¹, Table 1 starting ppm for non-volatiles.
6. Check experiment-level P (101325 Pa), T interval 1573.15–1823.15 K, fO2 gas-mix IW→air, units/basis/sign.

## Sampled checks (≥20)

### Table 2 residue series — **344 cells OK** (43 runs × 8 species)

Full automated match for Mn, Ti, Sc, V, Zr, La, Gd, Yb. Examples:

| run_id | T °C | logfO2 | loop | Mn pdf/ext | Ti pdf/ext | Sc pdf/ext |
|---|---|---|---|---|---|---|
| 2M-15-07-16c | 1300 | −0.68 | Pt | 759.6 / 759.6 | 1312.4 / 1312.4 | 1085.4 / 1085.4 |
| 1M-PS6 | 1300 | −8.00 | Pt | 782.9 / 782.9 | 1368.3 / 1368.3 | 1184.2 / 1184.2 |
| C17/12/15c | 1400 | −7.97 | Re | 843.3 / 843.3 | 1349.0 / 1349.0 | 1308.9 / 1308.9 |
| 2M-16-07-16d | 1550 | −9.56 | Re | 819.6 / 819.6 | 1220.5 / 1220.5 | 1079.6 / 1079.6 |
| 3M-1/3/16 | 1400 | −0.68 | Pt | 779.3 / 779.3 | 1323.2 / 1323.2 | 1102.7 / 1102.7 |

- **Unit / basis:** ppm by mass in quenched glass (LA-ICP-MS) — OK.  
- **Sign / T / fO2 / loop mapping:** OK on all 43 runs.  
- **Omitted (intentional, noted in extract):** empty row `2M-15-07-16f`; Blank Runs `P28-05-18`, `P23-05-18a`, `P24-05-18a/b`.

### Table 1 starting traces (non-volatiles) — OK

| sp | calc pdf/ext | meas pdf/ext | sd pdf/ext |
|---|---|---|---|
| Mn | 992.4 / 992.4 | 690.2 / 690.2 | 3.8 / 3.8 |
| Ti | 1008.0 / 1008.0 | 1045.0 / 1045.0 | 46.6 / 46.6 |
| Sc | 970.8 / 970.8 | 850.8 / 850.8 | 28.9 / 28.9 |
| V | 1019.7 / 1019.7 | 1024.6 / 1024.6 | 32.4 / 32.4 |
| Zr | 991.9 / 991.9 | 1118.1 / 1118.1 | 34.8 / 34.8 |
| La | 1038.9 / 1038.9 | 969.8 / 969.8 | 21.2 / 21.2 |
| Gd | 1063.3 / 1063.3 | 1060.6 / 1060.6 | 25.2 / 25.2 |
| Yb | 1036.2 / 1036.2 | 1111.0 / 1111.0 | 26.4 / 26.4 |

Basis note in extract (solution ICP-MS powder ≠ Table 2 LA-ICP-MS glass) — correct.

### Table 3 logK* — **17/18 rows present; all landed numbers OK**

Sample (1673.15 K unless noted):

| element / reaction | n | t0 | logK* pdf | ext | match |
|---|---|---|---|---|---|
| Na / Na(g)+¼O2 | 1 | 0 | −4.25±0.10 | −4.25±0.10 | OK |
| K / K(g)+¼O2 | 1 | 0 | −3.95±0.06 | −3.95±0.06 | OK |
| Li / Li(g)+¼O2 | 1 | 0 | −5.36±0.05 | −5.36±0.05 | OK |
| Li / LiO(g) n=−1 | −1 | 0 | −3.39±0.07 | −3.39±0.07 | OK |
| Rb multi-T | 1 | 14.4 | −4.15 / −3.12±0.10 / −2.63±0.02 / −2.26 | same | OK |
| Cu multi-T | 1 | 0 | −4.09±0.12 … −2.90 | same | OK |
| Zn / Zn(g)+½O2 | 2 | 9.0 | −5.06±0.09 … −2.57 | same | OK |
| Ge / GeO(g)+½O2 | 2 | 10.4 | −5.04±0.13 … −2.29 | same | OK |
| Pb PbO(g) / Pb(g) | 0 / 2 | 0 | −2.90±0.12, −2.00 / −4.47±0.19, −3.49 | same | OK |
| Mo MoO3 / MoO2→MoO3 | 0 / −2 | 0 | full multi-T grid | same | OK |
| Cr CrO→CrO2 n=−2 | −2 | 0 | −2.70 / −2.40±0.03 / −2.29 | same | OK |

**Missing row (coverage, not P0):** Table 3 `CrO1.5(l) + ¼O2 = CrO2(g)` (n=−1; −4.96 / −4.41±0.03 / −4.20) — not in extract.

### Table 4 γᵢ (this work) — sampled OK

Na 1.0×10⁻³; K 2.2×10⁻⁴; Li(−8) 15.4±3.1; Rb 1.3×10⁻⁴ / 7.4×10⁻⁵ / 9.0×10⁻⁵; Cu 24.0±6.3 … 4.2*; Zn 0.24±0.02 … 1.01; Ge 0.04±0.01 … 0.19; Ag 0.67; Cd 0.15; Pb^ 0.06±0.02 / 0.17; Pb# 0.21±0.09 / 0.38 — all match PDF.

### Table 5 ΔH*/ΔS* — sampled OK

Li n=1 (0.133, 413.0); Na* exp (0.071, 255.0) + pure (0.128, 255.0); K* (0.050, 209.5); Cu (0.089±0.009, 264.0±15.7); Zn (0.253±0.011, 548.6±19.6); Ge (0.294±0.014, 612.7±23.9); Ga n=3 (0.334±0.026, 885.9±45.7); Rb (0.123±0.024, 306.4±41.6); Pb n=0 (0.232±0.043, 452.1±70.6); Mo n=0 (0.118±0.016, 332.4±16.4); Cr n=−2 (0.046±0.016, 163.1±28.6); Mn pure (0.173, 606.3).

Cu reaction string stored as `+ 1/4 O2` (matches Table 3 n=1); PDF Table 5 body prints `+ 1/2O2` with n=1 — extract note records the print inconsistency; **numbers** match. Not P0.

### Table 6 — Mn Te¹ OK

Te¹(logfO2=−10/−5/−0.68) = 1974 / 2339 / 2783 K — match. Other Table 6 elements not landed in extract.

### Experiment conditions

- **P:** 101325 Pa (1 atm furnace) — OK.  
- **T:** series setpoints 1573.15–1823.15 K (1300–1550 °C) — OK.  
- **fO2:** per-run `log10_fO2` on series; experiment buffer text IW→air — OK.  
- **Sign:** all logK* negative as printed; n_electrons signs match Table 3 — OK.

## Coverage gaps (non-P0; no fix)

1. **Table 1 major oxides** (SiO2 40.76/40.60 … CaO 16.69/16.82 wt%) not landed as numeric `printed_composition` (prose label only).  
2. **Volatile / MVE Table 2 columns** (Li Na K Cu Zn Ga Ge Rb Mo Ag Cd Pb Cr …) not digitized as residue series — extract carries fitted **logK*** (Table 3) instead. Intentional for volatility fits; raw ppm grid absent for those elements.  
3. **Table 3 Cr n=−1** row absent.  
4. **Table 4 αe numeric rows** (Zn 2.0/2.2/1.7, Cu 2.4±0.8, …) not stored as separate observations (Na/K prose ranges + adopted αe=1 only); Li air γ=1.8±0.2 not stored (only −8 row 15.4).  
5. **Table 5 companion rows** partially absent (Li n=−1; Ga n=1; Pb n=2; Mo n=−2; Cr n=−1).  
6. **Table 6** only Mn Te¹; Pb Ge Zn Rb Cu K Ga Na Li Cr Mo Mg Ni Fe Co absent from extract.  
7. **`gas_species` field hygiene:** several oxide-gas reactions store elemental `X(g)` while `reaction` correctly names `XO(g)` / `MoO3(g)` / `CrO2(g)` (LiO, GeO, GaO, PbO, Mo×2, Cr). Parent species key is the melt element rail — mapping smell, **not a wrong number**.  
8. Many observation locators use OCR `source_path`/`line_range` without `page:`; `table:` is usually set. Experiment/bench locators use published section pages.

## P0 fixes

None. Branch tip remains base; PDF not added to git.

## Validation

No extract edit → no re-validate run required for a fix. YAML loads; inventory counts above.

## Push

```
git push -u origin empirical/z2-kems-012-sossi-2019-2026-09-22
```

Tip: `2e9e17c3d` (same as `origin/work-v064-green`).

## Report line

**Z2 kems-012-sossi-2019 · sampled n≥344 T2 cells + 17 logK* + T1/T4/T5/T6 spots · P0: 0 · coverage gaps noted · tip `2e9e17c3d` · no fix**
