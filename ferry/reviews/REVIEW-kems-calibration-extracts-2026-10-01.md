# REVIEW — calibration/apparatus: review/kems-calibration-extracts

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `eeded0d0d09798dbf1c5f3b8033b391ef690b712` (parent / green `91567188d`)
- **Commit:** `literature: record printed calibration, orifice and cell facts for Stolyarova 1995, Shornikov 1997 and Shornikov 1994` — only
  `data/literature/extracts/stolyarova-1995-cao-alumina-kems.yaml`,
  `data/literature/extracts/shornikov-1997-cao-alumina-vapor.yaml`,
  `data/literature/extracts/shornikov-1994-mullite-kems.yaml`
  (extracts-v2 siblings deliberately untouched; landing regen writes them)
- **Date:** 2026-10-01 ~16:02 ET
- **Mode:** read-only; extracts not edited; review tip not pushed to green
- **Policy gate:** `POLICY-regolith-main-2026-10-01-use-values-first.md` — **wrong-number / printed-source guards only**; lineage completeness not a gate
- **Corpus:**
  - Stolyarova 1995 PDF + ocr-mineru MD under `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1995/`
  - Shornikov 1997 PDF under `/workspace/ferry-inbox/reviews/_req-2026-09-30/shornikov/`
  - Shornikov 1994 PDF (+ .txt) under `/workspace/ferry-inbox/reviews/_req-2026-10-01/`
  Page images rendered with `pdftoppm`; key locators re-checked against page text / OCR.
- **REQ:** `REQ-review-kems-calibration-extracts-2026-10-01.md` — after d-053 bad test (50 Stolyarova rows refused for missing calibration); worker added printed calibration / area-ratio / cell / instrument with locators; effusion-regime 50/50 and 22/22 via `in_cell_fallback`; Shornikov 1994 0/33 pending composition LAND already given.

## Attack (1) — quotes, locators, area-ratio DIRECTION, cell/instrument for THIS experiment (blocking)

### Stolyarova 1995 (pp. 686–688)

| Fact | Paper | Tip |
|---|---|---|
| Instrument | p. 686 Experimental: “magnetic mass spectrometer model MI 1201 … modified … by standard methods.³⁷” | `apparatus_family` = “magnetic mass spectrometer model MI 1201”; note says no apparatus numbers imported from citation 37. |
| Cell | Table 1 Present-study column: Material of effusion cell = **Mo**; body “molybdenum effusion cells” | Mo / “molybdenum effusion cell; no liner stated” |
| Area ratio | p. 687: “ratio of the vaporization area to the area of the effusion orifice of **100:1**” | `orifice_to_sample_area_ratio` point `0.01` with note that paper prints 100:1 vaporization/effusion-orifice and typed field is the reciprocal 1:100. |
| Calibration | p. 688: “ion current comparison method”; “The vapour pressure of silver was used as a standard for measurements made here.” | `method` = ion-current comparison method; `reference_substance` = silver vapor; locator p. 688. Extract note paraphrases with ellipsis (“silver vapor … a standard”); substance matches the printed sentence. |

**PASS.** Direction of 100:1 is vaporization : orifice (sample area larger). Cell/instrument are the present-study column / Experimental text for this work, not a cited earlier setup.

### Shornikov 1997 (pp. 19–20)

| Fact | Paper | Tip |
|---|---|---|
| Instrument | p. 19: “MI-201 mass spectrometer modified for thermodynamic studies [36]” | note on apparatus_family locator records MI-201 and cites [36] without importing numbers |
| Cell | p. 19: “molybdenum cells” | Mo / molybdenum; no liner |
| Area ratio | p. 19: “ratio of the effusion hole cross section to the vaporization area of no less than **1 : 400**” | `orifice_to_sample_area_ratio` `0.0025` (=1/400); note quotes “no less than 1 : 400” |
| Calibration | p. 20: “comparison of ion currents”; “The silver vapor was used as a reference.” | method + reference_substance with p. 20 locators |

**PASS.** Direction of 1:400 is effusion-hole : vaporization (orifice smaller). Typed 0.0025 matches that direction.

### Shornikov 1994 (pp. 478–479)

| Fact | Paper | Tip |
|---|---|---|
| Instrument | p. 478 Experimental: “mass spectrometer model MI 1201 … modified for high temperature mass spectrometric studies … according to traditional approaches” | “MI 1201 mass spectrometer modified for high-temperature mass spectrometric studies”; note says modification attributed to traditional approaches without citing an apparatus paper |
| Cell | p. 478: “molybdenum Knudsen effusion cell” | Mo / that string |
| Area ratio | p. 478: “ratio of vaporization to effusion orifices of **400: 1**” | typed `0.0025` with note “400:1” vaporization/effusion-orifice → reciprocal 1/400 |
| Calibration | p. 479: “Partial vapour pressures of silver and gold were used as standards” (comparison of ion currents) | `ion_current_pressure_standards` with quote note |

**PASS.** Direction of 400:1 is vaporization : effusion (same sense as Stolyarova 100:1).

## Attack (2) — anything recorded as printed that is inferred / imported? (blocking)

- Orifice diameter, orifice channel length, chamber/background pressure, sweep gas, regime_class: tagged `unknown` / `not_published` with “not printed in this paper” notes on all three extracts.
- Citation notes explicitly refuse importing apparatus numbers from Drowart/Exsteen/Verhaegen (Stolyarova), Shul’ts & Shornikov 1995 (Shornikov 1997), or an apparatus paper (Shornikov 1994).
- Stolyarova extraction note: paper does **not** describe x(CaO)=0.75 or 0.86 as two-phase / lime-saturated (pp. 686–687). Table 3 / Fig. 3 show ΔH̄/ΔS̄/Δμ(CaO)=0 at those compositions, but the article never labels them two-phase; leaving composition/phase as previously typed is correct (no invented two-phase tag).

**PASS.** No imported apparatus facts recorded as printed.

## Attack (3) — area ratio ≠ orifice diameter; does in_cell_fallback pass on a printed fact or a typing convenience? (blocking)

- All three extracts put the printed ratio under `geometry.orifice_to_sample_area_ratio` and leave `orifice_diameter_m` / `orifice_channel_length_m` as `not_published`.
- Gate path (`simulator/battery/validity.py` `effusion_regime_unverified`): after no Kn and no printed diameter+cell-pressure pair, `_calibration_grounded(calibration)` must pass, then `in_cell_fallback` scores the printed in-cell partial-pressure sum against the Drowart et al. 2005 p. 689 usual ~10 Pa limit (`limit_basis`: `Drowart_standalone_usual_10_Pa_limit; no defensible d printed` when diameter is absent).
- `_calibration_grounded` requires located printed calibration entries — which this commit adds (silver / silver+gold + method). The area-ratio field is **not** what the gate consumes as a diameter.

**Plain statement:** the worker-reported 50/50 (Stolyarova 1995) and 22/22 (Shornikov 1997) `in_cell_fallback` passes rest on **printed** facts: (i) calibration standard + method with page locators grounding `_calibration_grounded`, and (ii) the already-landed printed partial pressures summing under the Drowart standalone limit. They do **not** rest on mistyping the area ratio as an orifice diameter. Shornikov 1994 remains 0/33 on this tip for the separate composition issue already given LAND `8b1100836` (in landing gate) — not a calibration typing failure.

**PASS.**

## Attack (4) — no measured value / unit / exponent / condition changed (blocking)

Structural diff vs green `91567188d`:
- **Files:** only the three extract YAMLs (no extracts-v2, no store).
- Deep compare of observation value cores (numeric points / series payloads, stripping quantity-name / reason / standard_state text): **0 changes** on Stolyarova (12 obs), Shornikov 1997 (6 obs), Shornikov 1994 (41 obs).
- Census unchanged. Equipment moved from per-observation free-text (Shornikov 1997) up to bench/experiment apparatus blocks; pressure series payloads identical.
- Targeted `python3 tools/validate_literature_extracts.py <yaml> --skip-priority` → **OK** on all three.

**PASS.**

## Attack (5) — four re-typed Stolyarova 1995 rows (blocking)

Only Stolyarova figure-only rows changed quantity / reason / standard_state text:

1. `stolyarova_1995_cao_activity_fig1_figure_only`: `CaO_activity` → `quantity: activity` + `composition_component: CaO`; `standard_state` cites Eq. (7) p. 688 and vapor pressures over the “individual oxides” (paper: Eqs. 7–8 and “partial pressures of vapour species over … the individual oxide”).
2. `stolyarova_1995_alumina_activity_fig1_figure_only`: same for Al₂O₃ / Eq. (8).
3–4. Chemical-potential Fig. 3 rows: quantity names **unchanged** (`CaO_chemical_potential` / `Al2O3_chemical_potential`); reasons clarified that Fig. 3 reports Δμ curves, **not** activities — correctly **not** re-typed as activities.

Still figure-only / typed_refusal; no values digitized. **PASS.**

## Notes (non-blocking per POLICY)

- Stolyarova calibration note’s ellipsis paraphrase (“silver vapor … a standard”) is slightly shorter than the printed “vapour pressure of silver was used as a standard”; locator and substance are correct.
- Shornikov 1997 geometry is new at bench level (previously only on observation `equipment`); values unchanged.
- Shornikov 1994 effusion-regime 0/33 is expected until composition LAND is in the same store as this calibration; out of scope for this tip’s wrong-number gate.

## P1 / P2

- **P1 (wrong-number / printed-source):** none
- **P2:** none

VERDICT: LAND eeded0d0d09798dbf1c5f3b8033b391ef690b712
