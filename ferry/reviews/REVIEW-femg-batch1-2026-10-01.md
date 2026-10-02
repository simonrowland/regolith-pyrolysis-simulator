# REVIEW — Fe/Mg extraction batch 1 (three one-paper commits)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z14` (`.slot-busy` cleared after STATUS ship)
- **Date:** 2026-10-01 ~22:50 ET
- **Mode:** read-only on tips; extracts not edited; tips not rebased onto green
- **Policy gate:** use-values-first / wrong-number + printed-source guards only
- **REQ:** `REQ-review-femg-batch1-2026-10-01.md`
- **Corpus:** PDFs under ferry-inbox/acquired + Mac corpus raw/; page images via pdftoppm; Markova re-digitised on 400 dpi page-2

Green tip on origin may have moved (`c9b6e545d`); REQ merge-clean parent is `696299350`. Reviewed each tip as given.

> **Store-count lean lifted (2026-10-02):** Main landing-train regen now confirms store counts (Plante 9 obs changed / hard +9; Markova 22→44). Values-side guards unchanged; leans that waited only on store regen are now final `VERDICT: LAND <sha>`.

---

## 1) Plante 1992 — `b843cd76be8762cf561267bd6fd5ef70be604422` (kems-021-plante-1992-feo)

### Wrong-number guards (Table 2 p.1278 + Table 1)

| x(FeO) | page f(FeO) | tip f | tip a=f·x | match |
|---|---:|---:|---:|:---:|
| 0.1 | 4.04 | 4.04 | 0.404 | yes |
| 0.2 | 3.02 | 3.02 | 0.604 | yes |
| 0.3 | 2.33 | 2.33 | 0.699 | yes |
| 0.4 | 1.85 | 1.85 | 0.74 | yes |
| 0.5 | 1.54 | 1.54 | 0.77 | yes |
| 0.6 | 1.32 | 1.32 | 0.792 | yes |
| 0.7 | 1.17 | 1.17 | 0.819 | yes |
| 0.8 | 1.07 | 1.07 | 0.856 | yes |
| 0.9 | 1.02 | 1.02 | 0.918 | yes |

Table 1 ion ratios (7 rows): I(Fe+)/I(Mg+) and I(Fe+)/I(SiO+) all match page (7.04/2.33 … 21.64/40.30). Sampled ≥5.

### Units / T windows
- Measurement windows on tip: **1850–1980 K** (charges x(FeO)≤0.80); **1790–1910 K** for x(FeO)=0.90. Matches page 1277 Experimental (OCR dropped leading “1”; tip corrected).
- Table 2 / Table 1 reported at **1973 K**; activity standard pure liquid FeO; a=f·x explicit. Fig. 1 / Fig. 4 figure-only blocked (no digitisation).

### Nothing invented
- W cell + Ir inner cup, 0.5 mm orifice: printed. Orifice **area** derived from diameter is flagged `inferred: true`.
- Calibrant / cell dims / chamber pressure: not printed (left unknown / not invented).

### NOT-FIXED lens
- Scorer: comparisons refuse `effusion_regime_unverified` (KEMS activities not yet on d-055 flag path) — **ticket t-1076**, scorer defect not extract wrong-number.
- Store regen counts (worker): 9 observations changed; hard issues +9 lineage under d-053 — confirm on box when migrate re-run; values side OK.

**VERDICT:** LAND b843cd76be8762cf561267bd6fd5ef70be604422

---

## 2) Yakovlev 1984 — `617741fad7f35ed1d9884de57e3d4fed3a5832ca` (kems-028-yakovlev-1984)

### Wrong-number guards (prose POINT pressures, p.945)
Sampled ≥5 against page crops / OCR:

| Point | tip p_torr | page |
|---|---:|---|
| Na @ 1225 °C | 2.7e-4 | 2.7×10⁻⁴ tor |
| K @ 1225 °C | 4.2e-5 | 4.2×10⁻⁵ tor |
| Fe troilite @ 1050 °C | 1.8e-5 | 1.8×10⁻⁵ tor |
| FeO top @ 1650 °C | 8.1e-5 | 8.1×10⁻⁵ tor |
| SiO CAI max @ 1875 °C | 6.2e-3 | 6.2×10⁻³ tor |
| Ca @ 1825 °C | 7.5e-5 | 7.5×10⁻⁵ tor |
| Al max @ 2100 °C | 1.7e-3 | 1.7×10⁻³ tor |

Torr→Pa factor 133.322 applied consistently. Figs 1–2 figure-only (not digitised). Po2 table `method_class: model_derived`.

### Bound_not_point for maxima (REQ focus)
- Fe max 10⁻² torr over **1450–1725 °C**: `admission_status: rejected`, `semantics: bound_not_point_ordering` — PASS (was wrong as POINT at unknown T).
- Mg max 8.3×10⁻³ torr over **1750–1825 °C**: same rejection — PASS.
- Conflicting FeO statements both retained: range sentence glyph P_FeO=10⁻² vs point P_FeO=8.1×10⁻⁵ @ 1650 °C; Fe(g) assignment for the range row follows Fig. 1 Fe label — quoted, not invented.

### Composition / apparatus
- Sample named Murchison / Efremovka CAI; **no numerical bulk composition** printed — tip notes match.
- Apparatus: Knudsen effusion + MS cited to prior technique paper; cell dims / calibrant / chamber pressure tagged not printed — PASS.

### Blocking extract defect (REVISE)
Same sentence as the rejected Mg max: “Maximum P_SiO and P_Mg in the temperature range 1750–1825 °C are accordingly 2×10⁻² tor and 8.3×10⁻³ tor.”
- **Mg** correctly rejected as range-max.
- **SiO** (`yakovlev_1984_murchison_SiO_Mg_max_1750_1825C`) still carries `p_torr: 0.02` with **no** `admission_status: rejected` / `bound_not_point_ordering` — same wrong-number class the commit claims to fix.
- Parallel: Ca max over **1950–2025 °C** (`…Ca_max_1950_2025C`) also kept as a point (`p_torr: 0.00044`).

### NOT-FIXED lens
- After SiO/Ca range-max fix, remaining scoring blockers are scorer/apparatus (calibration / effusion regime), not further wrong numbers in the admitted POINT set.
- Worker regen claim (15 obs changed; points 22→20; categoricals 4→6; hard issues unchanged) — re-confirm on box migrate after revise.

**VERDICT:** REVISE — reject SiO (and Ca) range-maxima as `bound_not_point_ordering` like Mg/Fe. (batch1 tip `617741fad7f35ed1d9884de57e3d4fed3a5832ca` — not LAND; revised tip is in batch2)

---

## 3) Markova 1983 — `4b04c2290552fca19b7d9c48b68066115a6787da` (kems-025-markova-1983)

### Re-digitisation (≥5 points, independent marker centers vs tip)
400 dpi page-2 (3167×4395). Axis least-squares from tip calibration ticks. Stated uncertainty: |ΔT|≤31.7 °C, |Δlog10 P|≤0.07.

| Point | fig | Δpx | ΔT (°C) | Δlog10 P | within unc. |
|---|---|---:|---:|---:|:---:|
| Fe[0] sample1 | 1 | (+0.8,−0.9) | +1.41 | +0.0078 | yes |
| Fe[3] sample3 | 3 | (−1.0,+1.3) | −1.60 | −0.0105 | yes |
| Mg[1] sample2 | 2 | (+5.6,−6.0) | +10.19 | +0.0525 | yes |
| Mg[3] sample3 | 3 | (−2.8,−3.7) | −4.61 | +0.0309 | yes |
| FeO[1] sample3 | 3 | (−0.5,+0.6) | −0.81 | −0.0047 | yes |
| FeO[4] sample4 | 4 | (−6.7,−4.8) | −11.43 | +0.0408 | yes |

**6/6 PASS.** Tip’s own independent_repeat pairs also within the same gates. Log-axis calibration residuals ≤0.008 log10; Fig. 2 x residual 31.7 °C drives the stated T bound.

### Counts / table
- 25 admitted digitised points (Fe 9 + Mg 9 + FeO 7) across 22 extract observation records (placeholders→points as REQ).
- Table comps checked via fidelity samples: Al2O3 anorthite 36.63; SiO2 Apollo-16 45.40; FeO troctolite 8.84 — match page table.

### Nothing invented
- `cell_material_and_liner: unknown / not_published` — abstract cites apparatus elsewhere; not invented. Scorer refuse `cell_material_unknown` = **t-1077** (scorer/paper-elsewhere), not extract inventing.
- Digitisation authorised for this paper only (controller); other species remain figure_only / measured_not_tabulated.

### Commit scope note
Tip also touches `simulator/battery/score.py` (+ in_cell_fallback→calibration taxonomy) and `tests/battery/test_score.py`. Values review does not depend on that; merge is still one commit. Not a wrong-number fail.

### NOT-FIXED lens
- All 25 points refuse `cell_material_unknown` until apparatus paper facts land or scorer path changes (t-1077).
- Store regen 22→44 observations / hard issues unchanged — confirm on box migrate.

**VERDICT:** LAND 4b04c2290552fca19b7d9c48b68066115a6787da
(landing as extract-only `2c92c4095`, blob-identical extract)

---

## Store regen

Lean condition (store-count confirmation via `battery_migrate`) is **lifted**: main's landing train regen confirms Plante 9 obs changed / hard +9 and Markova 22→44. Values-side guards above unchanged. Finalized 2026-10-02 (2026-10-02 ~00:31 ET).

— regolith-empirical
