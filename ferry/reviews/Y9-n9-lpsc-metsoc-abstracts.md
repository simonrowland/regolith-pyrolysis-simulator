# Y9 — CROSS-AUDIT: N9 LPSC / MetSoc abstracts (BACKLOG 3)

**Lane audited:** N9 (`empirical/n9-lpsc-metsoc-abstracts-2026-09-22`)
**Tip:** `3a2c8f499392a9b277c42a6023e055bbd753cca5` (unchanged; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N9-lpsc-metsoc-abstracts.md`
**Worktree:** `/workspace/repos/wt/slot-l5`
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{lpsc-2020-1883,metsoc-2024-6397,w067-2012}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N9 extractor

## Verdict

**P0: 0** — every landed printed number matches the PDF (value + unit sense + locator page).
**READY.** No branch change; nothing to push.

## Method

`pdftotext -layout` on the three corpus PDFs; open each extract at tip SHA; walk every numeric / range field under `species.*.context[].values`, `fidelity_samples`, and author-write-up claims; confirm PDF p1 identity (title/authors) and sha256 vs N9 sidecar claims. Validator: `tools/validate_literature_extracts.py` → `OK: 2 extract file(s) valid`. P0 = a wrong number lands (per BACKLOG 3 / X-audit doctrine). Omissions and locator-section nits are non-P0.

## Identity / STEP 0

| Corpus | PDF p1 identity | Extract / disposition | Match |
| --- | --- | --- | --- |
| lpsc-2020-1883 | King, Zulmahilan, Schofield, Russell — *CM chondrites from multiple parent bodies…* LPSC 2020 #1883 | `lpsc-2020-1883.yaml` LANDED | OK |
| metsoc-2024-6397 | Russell et al. — *Mineralogy of Bennu: evidence from space and laboratory* MetSoc 2024 #6397 | `metsoc-2024-6397.yaml` LANDED | OK |
| w067-2012 | Valentina Stolyarova — *Thermodynamic properties and vaporization processes of the ionic silicate melts* (14-page review) | REFUSED — already `kems-018-stolyarova-2012` | OK |

**sha256:**
- `lpsc-2020-1883.pdf` = `b42ef98be872d66e10b262916d7cd253da137739e449a36884052dc1d09fd6f5` (matches N9)
- `metsoc-2024-6397.pdf` = `516e178763c7a9c59dabb7b8b0c0bdfb16635985fbfac055b0f81503bee73ec9` (matches N9)
- `w067-2012.pdf` = `7a9c05dd81d50994911b82a891f0bc82f80994e4a30535b2c593ffeb441b4d2f` = INDEX raw hash for `kems-018-stolyarova-2012` (STEP 0 skip correct; do not duplicate)

## Cell-by-cell (printed numbers)

### 1. lpsc-2020-1883 — PASS

**PDF:** 2-page LPSC abstract. **Landed:** 6 context rows + 4 fidelity samples; `scored_observations: 0`.

| landed field | landed | printed (PDF) | locator | match |
| --- | --- | --- | --- | --- |
| phyllosilicates_vol_pct_range_as_printed | `70‒80` | Intro: phyllosilicates (70‒80 vol%) | p1 Introduction | OK |
| unaltered_olivine_and_pyroxene_vol_pct_range_as_printed | `0‒20` | (0‒20 vol%) | p1 Introduction | OK |
| oxides_sulphides_carbonates_vol_pct_bound_as_printed | `<5` | (<5 vol%) | p1 Introduction | OK |
| sample_mass_mg_range_as_printed | `~50‒200` | Powdered… (~50‒200 mg) | p1 Experimental | OK |
| analysis_duration_hours | `16` | analysed for 16 hours | p1 Experimental | OK |
| mineral_standards_analysis_duration_minutes | `30` | analysed for 30 minutes | p1 Experimental | OK |
| phase_quantification_uncertainty_vol_pct_bound_as_printed | `<5` | uncertainties of <5 vol% | p1 Experimental | OK |
| sample_mass_mg_approx | `10` | Approximately 10 mg | p1 Experimental | OK |
| heating_rate_C_per_min | `10` | 10°C min-1 | p1 Experimental | OK |
| T_start_C / T_end_C | `25` / `1000` | 25°C to 1000°C | p1 Experimental | OK |
| H2O_mass_loss_interval_C_as_printed | `400‒770` | between 400‒770°C | p1 Experimental | OK |
| atmosphere | `N2 flow` | under an N2 flow | p1 Experimental | OK |
| CM_with_CRE_age_approx | `135` | ~135 CM chondrites | p1 Experimental | OK |
| heated_CM_fraction_of_CRE_set_pct_approx | `20` | (~20% are heated CMs) | p1 Experimental | OK |
| PSF_available_count | `53` | PSF … for 53 | p1 Experimental | OK |
| H2O_abundance_available_count | `64` | H2O … for 64 | p1 Experimental | OK |
| samples_with_both_PSF_and_H2O | `35` | 35 samples with both | p1 Experimental | OK |
| Takenouchi_et_al_thin_sections_count | `125` | studied 125 CM … thin sections | p1 (correlation §) | OK |
| CM1_PSF_bound_as_printed | `>0.8` | PSF >0.8 | p2 Fig.2 prose | OK |
| CM1_H2O_wt_pct_approx | `10` | H2O ~10 wt% | p2 | OK |
| CM1_CRE_age_Myr_approx | `0.2` | ~0.2 Myr | p2 | OK |
| CM2_PSF_range_as_printed | `~0.7‒0.75` | PSF ~0.7‒0.75 | p2 | OK |
| CM2_H2O_wt_pct_range_as_printed | `~7‒8` | H2O ~7‒8 wt% | p2 | OK |
| CM2_CRE_age_Myr_range_as_printed | `~0.6‒2` | ~0.6‒2 Myr | p2 | OK |
| poorly_constrained_CRE_gt_3_Myr_PSF_approx | `0.77` | PSF ~0.77 | p2 | OK |
| poorly_constrained_CRE_gt_3_Myr_H2O_wt_pct_approx | `8` | H2O ~8 wt% | p2 | OK |
| figure_error_bar_order_pct_as_printed | `5‒10` | order of 5‒10% (Fig. 2 caption) | p2 | OK |
| T_C_bound_as_printed | `>120` | (>120°C) tochilinite claim | p2 How many CM parent bodies? | OK |

- **Fidelity samples** echo method mass/rate and CM1 H2O / both-count — consistent with context.
- **Not digitized (correct):** Fig. 1/2 curve points; individual meteorite tables (none printed).
- **Unit / dash fidelity:** Unicode en-dash `‒` retained as in PDF — OK.

### 2. metsoc-2024-6397 — PASS

**PDF:** 1-page MetSoc abstract. **Landed:** 5 context rows + 4 fidelity samples; `scored_observations: 0`.

| landed field | landed | printed (PDF) | locator | match |
| --- | --- | --- | --- | --- |
| particle_size_mm_approx / as_printed | `1` / `~1 mm across` | particles ~1 mm across | p1 Techniques | OK |
| TIR_wavelength_um_range_as_printed | `2.5–25` | (2.5–25 µm) | p1 Techniques | OK |
| EDX_instrument | Zeiss Evo 15LS | Zeiss Evo 15LS | p1 Techniques | OK |
| TIR_instrument | Nicolet iN10 | Nicolet iN10 FTIR microscope | p1 Techniques | OK |
| phyllosilicates_pct_approx | `80` | ~80% phyllosilicates | p1 Results | OK |
| sulfides_pct_approx | `9` | ~9% sulfides | p1 Results | OK |
| magnetite_pct_approx | `5` | ~5% magnetite | p1 Results | OK |
| carbonates_pct_approx | `4` | ~4% carbonates | p1 Results | OK |
| average_matrix_Mg_number_pct_approx | `78` | average matrix Mg # ~78% | p1 Results | OK |
| Ca_phosphate_grain_size_um_bound_as_printed | `≤10` | Phosphates … ≤10 µm Ca-phosphate | p1 Results | OK |
| remote_sensing_anhydrous_silicate_vol_pct_bound_as_printed | `≤10` | ≤10 vol% anhydrous silicates [2] | p1 Discussion (claim) | OK |
| returned_sample_mass_g_approx | `120` | ~120 g sample returned | p1 Discussion | OK |

- **Fidelity samples** (1 mm, 80%, 9%, 78%) match context — OK.
- **Not digitized (correct):** Fig. 1–2 morphology; qualitative carbonate/magnetite prose without new numbers.
- **Instrument / author strings** match p1 (incl. P. J. Schofield on this abstract) — OK.

### 3. w067-2012 — REFUSAL CONFIRMED

Same title/author/year as existing `kems-018-stolyarova-2012`; identical PDF sha256 in INDEX. No new extract under `w067-2012` — correct STEP 0 stop.

## Non-P0 notes (no fix)

1. **Locator section split (metsoc):** `metsoc_2024_6397_phosphate_and_silicate_bounds` is tagged `section: Results`, but the ≤10 vol% anhydrous-silicate bound is printed under Discussion (Ca-phosphate ≤10 µm is Results). Number itself is correct; section tag is coarse.
2. **Boolean prose stamp:** `matches_bulk_XRD: true` restates “SEM-derived mineralogy matches bulk XRD” — not a numeric mismatch.
3. **Intentional omissions (not wrong numbers):** LPSC ~30% CRE >3 Myr; <1 µm grain-size aside; CI1 PSF >0.9 / ~3 Myr comparison; MetSoc OREX-80307* figure labels; qualitative morphology.
4. **Scope:** both abstracts correctly land as unscored d-032 context only (0 vaporization / p / melt-thermo observations).

## Attack checklist

| Attack | Result |
| --- | --- |
| Wrong oxide/compo cell vs table | N/A (no composition tables; abstract prose only) |
| Swapped bin averages (CM1↔CM2 PSF/H2O) | Fail — PDF prose matches landed bins |
| Wrong TGA interval / heating rate | Fail — 400‒770°C and 10°C min⁻¹ match |
| MetSoc mineralogy % permutation | Fail — 80/9/5/4 match printed order |
| Mg # 78 vs other nearby % | Fail — only ~78% is Mg # |
| w067 re-extract duplicate | Fail — correctly refused via kems-018 hash |
| PDF identity mismatch vs citation | Fail — titles/authors match |
| Validator regressions | Fail — 2/2 OK |

## Summary

| Paper | Disposition | P0 |
| --- | --- | --- |
| lpsc-2020-1883 | LANDED — all printed numbers OK | 0 |
| metsoc-2024-6397 | LANDED — all printed numbers OK | 0 |
| w067-2012 | STEP 0 skip (kems-018) confirmed | 0 |

**VERDICT: READY — P0=0.** Tip `3a2c8f499` unchanged.
