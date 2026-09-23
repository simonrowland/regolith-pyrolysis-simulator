# Y12 — CROSS-AUDIT: N12 Mars SAM clay-sulfate EGA + SAM instrument suite (BACKLOG 4)

**Lane audited:** N12 (`empirical/n12-mars-sam-2026-09-22`)
**Prior tip:** `15cb5758c39685987646e9a486365c7683609c5d`
**Tip (post-fix):** `e31fddbe0f32e7df7ad675b8d0d4cf3746b10ca3`
**Prior (author):** `/workspace/ferry-inbox/reviews/N12-mars-sam.md`
**Worktree:** `/workspace/repos/wt/slot-y12-n12` (detached @ post-fix tip; distinct seat from N12 extractor `slot-03`)
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/{jgr-p-2024-mars-sam-clay-sulfate-ega,mahaffy-2012-sam-instrument-suite}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N12 extractor

## Verdict

**P0: 2 (fixed)** — Mahaffy TLS Herriott pass/path mix-up; oven Fig. 8 “1 cm” mis-attributed as cup height.
**READY** after fix commit `e31fddbe0` pushed to `origin/empirical/n12-mars-sam-2026-09-22`.

| Severity | Count |
| --- | ---: |
| **P0** (wrong number / wrong species lands) | **2 (fixed)** |
| P1 | 0 |
| P2 | 3 |
| P3 | 2 |

## Method

1. Detached worktree at prior tip `15cb5758c`, then at post-fix `e31fddbe0`. Re-ran `.venv` `tools/validate_literature_extracts.py --check-fidelity-match` on both files → `OK: 2 extract file(s) valid` (before and after).
2. `sha256sum` of inbox PDFs vs sidecars / extract provenance strings.
3. `pdftotext -layout` (full + page map via form-feeds) and 120–150 dpi `pdftoppm` rasters for Mahaffy p. 431–432 (oven Fig. 8), p. 439 (TLS Herriott), p. 452 (Table 11); Clark pp. 9–10 (H₂O).
4. Walked every landed numeric / species list under `fidelity_samples`, `experiments.*.sample|conditions`, and `species.*.observations|context[].values`; compared value, unit/basis, DERIVED stamp, and locator page to printed prose / table.
5. Confirmed Clark SI Tables S2–S9 typed absence and Mahaffy Mars-motivation / non-digitised schematics against N12 instrument/bench rule.

P0 rule (X1–X9 / Y calibration): **P0 only if a wrong number or wrong species lands**. Omission / incompleteness ≤ P1; locator coarseness ≤ P2.

## Identity / STEP 0 / PDF ↔ sidecar

| Corpus | PDF p1 identity | Extract | sha256 | Match |
| --- | --- | --- | --- | --- |
| jgr-p-2024-mars-sam-clay-sulfate-ega | Clark et al. — *Environmental Changes Recorded in Sedimentary Rocks in the Clay-Sulfate Transition Region in Gale Crater, Mars…*, JGR Planets 129 e2024JE008587, DOI 10.1029/2024JE008587 | `jgr-p-2024-mars-sam-clay-sulfate-ega.yaml` LANDED | `da22bf1c34fafdc58c39202129fcd09af85a5a7c55768f168a1b8cedc04850d0` | OK (= sidecar + extract provenance) |
| mahaffy-2012-sam-instrument-suite | Mahaffy et al. — *The Sample Analysis at Mars Investigation and Instrument Suite*, Space Sci. Rev. 170:401–478, DOI 10.1007/s11214-012-9879-z | `mahaffy-2012-sam-instrument-suite.yaml` LANDED | `c96a13886d05a0f2028c4867a94d4e74b3c204a20a6365aeb77a54a3ff0ddc09` | OK (= sidecar + extract provenance) |

STEP 0: no prior Clark 2024 / DOI 10.1029/2024JE008587 extract (`nasa-tm-2024-simulant-guide` is a different Clark). No prior Mahaffy 2012 suite extract (`lpsc-2014-2057` only cites Mahaffy as coauthor on a different abstract).

Clark PDF is 24 pp; Mahaffy 78 pp. Locators use 1-indexed `pdf_page_index` with Clark `published_page_of: 'N of 24'` / Mahaffy `published_page: 400+N`.

## P0 findings (fixed)

| # | sev | source | finding | fix |
| --- | --- | --- | --- | --- |
| 1 | P0 | mahaffy | `passes_for_CO2_H2O_as_printed: 81` + `path_length_m_as_printed: 8.93` mixed methane and CO₂/H₂O rows. Printed (p. 439 / raster): **81 passes (16.8 m) for methane**; **43 passes (8.93 m) for CO₂ and water**. Intro “81-pass Herriott cell” is a general phrase, not the CO₂/H₂O pass count. | Split: CH₄ 81 / 16.8 m; CO₂+H₂O 43 / 8.93 m. |
| 2 | P0 | mahaffy | `quartz_cup_height_cm_as_printed: 1` with `quartz_cup_ID_cm_as_printed: 0.7`. Fig. 8 caption (p. 432 / raster): “**inside diameter of the oven** at the sample location is approximately **1 cm** and the **inside diameter of the quartz cup 0.7 cm**”. 1 cm is oven ID, not cup height. | Renamed to `oven_ID_at_sample_location_cm_as_printed: 1` (+ approximate flag + caption note); cup ID 0.7 retained. |

Commit: `e31fddbe0 extracts: N12 Y12 P0 fix Mahaffy TLS passes + oven ID attribution` (pushed).

## Per-source audit

### 1. jgr-p-2024-mars-sam-clay-sulfate-ega — PASS (main-text ranges + peaks)

**PDF:** 24-page JGR Planets flight SAM-EGA paper. **Landed:** 8 scored `gas_speciation` observations (campaign ranges + peak attributions + O₂/NO non-detections + CO) + protocol / inventory / SI typed-absence context. Figs 5–10 not digitised; SI Tables S2–S9 typed absent.

#### Abundance range endpoints (attack focus)

| field | landed | printed | PDF page | match |
| --- | --- | --- | --- | --- |
| H₂O wt.% | 0.87–7.74 | “between 0.87 wt.% and 7.74 wt.% H₂O” | 10 | OK |
| MG/ZE H₂O subrange | 5.35–7.74 | “ranging from 5.35 to 7.74 wt.%” | 10 | OK |
| H₂O window °C | 100–750 | “approximately 100°C and 750°C” | 10 | OK |
| SO₃ from SO₂ wt.% | 0.22±0.13 – 2.19±1.39 | same endpoints | 13 | OK |
| mid-T SO₂ °C | 320–750 | “approximately 320 and 750°C” | 10–11 | OK |
| AV SO₂ peak °C | 683 | “683°C” | 11 | OK |
| NT1 / NT2 / NT2 high-T SO₂ °C | 545 / 452 / 790 | same | 12–13 | OK |
| high-T Mg-sulfate SO₂ | >750 °C except BD | “all samples except for BD” | 11 | OK |
| Cl from HCl wt.% | 0.11–0.65 | same | 14 | OK |
| HCl peak °C | 550–600 | “approximately 550–600°C” | 14 | OK |
| C from CO₂ μg/g | 96–1668 | “between 96 and 1668 μg/g C” | 14 | OK |
| CO₂ main window °C | 150–550 | same | 14 | OK |
| post-ZE blank CO₂ µmol | 0.2 ± 0.1 | same | 16 | OK |
| CO µmol | 0.25–1.5 | “0.25 to 1.5 μmol” | 17 | OK |
| CO main window °C | 230–525 | same | 17 | OK |
| ClO₄/ClO₃ / NO limit | <0.001 wt.% | same | 13–14 | OK |
| BD NO peak °C | ~700 | “peak ∼700°C”; only BD detectable NO | 14 | OK |
| H₂O ~270 °C goethite | all except ZE, CA | same | 10 | OK |
| mid-T H₂O 360–430 °C | NT, BD, AV → nontronite | same | 10 | OK |
| AV ~700 °C montmorillonite | minor; CheMin no phyllosilicates | same | 10 | OK |

#### Protocol / inventory

| field | landed | printed | match |
| --- | --- | --- | --- |
| ramp / oven max | ~35 °C/min to ~900 °C | same | OK |
| He carrier | ~0.8 sccm / ~25 mbar | same | OK |
| cup | quartz | same | OK |
| FED/FEST after Sol 1536; pre-1536 portion 45±18 mg | same | same | OK |
| targets NT…CA + sols 3056/3094/3178/3229/3289/3528/3624 | same (Fig. 4) | same | OK |
| post-ZE blank Sol 3487 Windjana triple-portion | same | same | OK |
| dual portions EGA-GCMS | NT, MG, CA | same | OK |
| DERIVED K | T_C + 273.15 | arithmetic OK | OK |

- SI Tables S1–S9 correctly typed-absent — OK.
- Figs 5–10 supporting-but-not-digitised — OK.
- Fidelity samples echo H₂O / SO₃ / Cl / C ranges + ramp 35 — OK.

### 2. mahaffy-2012-sam-instrument-suite — PASS after P0 fix

**PDF:** 78-page Space Sci. Rev. instrument suite paper. **Landed:** 1 scored `composition_series` (Table 11) + SS-EGA protocol / oven / QMS / GC Table 9 / TLS Herriott context. Mars-motivation tables typed absent.

#### Table 11 calibration-gas cell (attack focus; raster p. 452)

| component | landed (post-fix) | printed 100 °C MR | match |
| --- | ---: | ---: | --- |
| CO₂ | 24.32 | 24.32 % | OK |
| N₂ | 24.1 | 24.10 % | OK (value; trailing-zero soft — P3) |
| Ar | 24.04 | 24.04 % | OK |
| XeT | 8.48 | 8.48 % | OK |
| ¹²⁹Xe | 15.51 | 15.51 % | OK |
| PFTBA | 3.0 | 3.00 % | OK (trailing-zero soft — P3) |
| 1FN | 0.54 | 0.54 % | OK |
| DFBP | 0.016 | 0.016 % | OK |
| PFBP | 0.0078 | 0.0078 % | OK |
| cell volume | 4.76 ml | 4.76 ml | OK |
| install P / T | 1.3 bar @ 125 °C | same | OK |
| 3× ¹²⁹Xe enrichment note | landed | same prose | OK |

#### Apparatus / protocol (post-fix)

| field | landed | printed | match |
| --- | --- | --- | --- |
| He flow | 0.03 atm-cc/s | same | OK |
| final T / ramp | 950–1100 °C; 35 °C/min; non-linear above ~800 °C | same | OK |
| aux oven / other oven | 1100 / 950 °C | same | OK |
| cup precondition | >900 °C | same | OK |
| heater wire | Pt–Zr 0.51 mm; Inconel 693 | same | OK |
| oven ID / cup ID | ~1 cm oven ID; 0.7 cm cup ID | Fig. 8 caption | OK (fixed) |
| QMS window | 1.5–535.5 Da @ 0.1 Da; unit 2–535 | same | OK |
| dynamic range / sensitivity | ~1e9; >1e-2 (cnts/sec)/(part/cc) | Table specs ∼10⁹ / >10⁻² | OK |
| GC columns (Table 9) | 6× 30 m × 0.25 mm; phases/targets; GC4–6 traps; TCD on five | same | OK |
| TLS Herriott | 20 cm; ~405 cm³; CH₄ 81/16.8 m; CO₂+H₂O 43/8.93 m; lasers 2.78 / 3.27 µm | p. 439 | OK (fixed) |
| manifold bake / transfer | 135 °C typical; transfer 135–200 °C | same numbers | OK |

Fidelity sample mole fractions == observation Table 11 — OK.

## Non-P0 findings (no further tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P2 | clark | Systematic `pdf_page_index` / `published_page_of` off-by-one on several observation locators (e.g. H₂O range claimed `9 of 24` but numbers sit on PDF p. 10; SO₃ claimed 12 → p. 13; HCl/NO claimed 13 → p. 14; CO claimed 16 → p. 17; protocol claimed 6 → p. 7; Fig. 2 inventory claimed 5 → p. 4). Values themselves match. |
| 2 | P2 | mahaffy | QMS mass-window locator `pdf_page_index: 35` / `published_page: 435` but “1.5 to 535.5 Da” prose is on PDF p. 36 (pub 436). |
| 3 | P2 | mahaffy | Oven-geometry context bag also carries `manifold_bake_typical_C` (printed ~p. 20/421) and `transfer_line_operating_C` (printed ~p. 33/433) under a p. 31/431 locator — bag mixing / locator coarseness only. |
| 4 | P3 | mahaffy | Table 11 N₂ / PFTBA stored as YAML floats `24.1` / `3.0` vs printed `24.10 %` / `3.00 %` — value-correct; quote-as-string hygiene. |
| 5 | P3 | mahaffy | GC2 id soft-normalises `GC2-MTX 5 (WCOT)` → `GC2-MTX5_WCOT`; “cyanopropyle” → “cyanopropyl”. |

## Attack checklist

| Attack | Result |
| --- | --- |
| Clark SI Table S2–S9 per-sample cells sneak into scored obs | Fail — typed absence only |
| Clark Fig 5–10 EGA traces digitised as numbers | Fail — figure-only stamps |
| H₂O / SO₃ / Cl / C / CO range endpoints swapped or truncated | Fail — match printed (post-audit) |
| AV 683 / NT1 545 / NT2 452 / 790 SO₂ peaks wrong | Fail — match |
| O₂ / ClOₓ non-detection invented as positive detection | Fail — non-detection + <0.001 limit |
| Mahaffy Table 11 mole % row swap | Fail — all nine MR match raster |
| TLS 81 passes attributed to CO₂/H₂O | **Hit pre-fix (P0-1); fixed** |
| Fig. 8 1 cm as cup height | **Hit pre-fix (P0-2); fixed** |
| Mars atmosphere / meteorite survey tables scored as lab numbers | Fail — typed absence / out of N12 scope |

## Push / product fixes

**Done.** Fix commit `e31fddbe0` on `empirical/n12-mars-sam-2026-09-22` (pushed). Clark extract unchanged (P0=0 on Clark). Locator off-by-ones left as P2 (report-only; matches Y6/Y8 doctrine).

Optional follow-ups (not done): ferry Clark 2024 SI PDF for per-sample Table S2–S9 cells; quote Table 11 MR as strings for trailing zeros.

## Final

**VERDICT: READY · P0: 2 fixed · P1: 0 · P2: 3 · P3: 2 · tip: `e31fddbe0` (was `15cb5758c`) · pushed**
