# N14 NEW EXTRACT — misc corpus (BACKLOG 4)

**Branch:** `empirical/n14-misc-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `668ba43544f70836ade671872f1850332f3395d5`
**Worktree:** `/workspace/repos/wt/slot-n14`
**Date:** 2026-09-22 (America/Toronto, EDT)
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/` (private; not committed)
**Pushed:** yes → `origin/empirical/n14-misc-2026-09-22`

## Validate

```
OK: 4 extract file(s) valid
```

0 new errors. Validator run with `--check-fidelity-match` on each file and on all four together
(`tools/validate_literature_extracts.py` via repo `.venv`).

## Policy (N14 / BACKLOG 4)

Same rules as N1–N10: STEP 0 identity + no-duplicate; extract what is **printed**;
**marcq model → typed absence** (do not ingest model outputs as observations);
never commit private PDFs.

## Per-paper status

### 1. lpi-001332 — LANDED (SRR VIII volume; vacuum-pyrolysis abstracts)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for LPI Contribution 1332 / Space Resources Roundtable VIII volume under another name |
| STEP 0 DOI | None (LPI contribution) |
| PDF ↔ sidecar | Match. PDF p1: *PROGRAM AND ABSTRACTS / LPI Contribution No. 1332*; title page Space Resources Roundtable VIII, Oct 31–Nov 2 2006, Colorado School of Mines. sha256 `21e5777c58d81ca36b6fc89a1de9bb8bbb2d785cbe5cdc2070b76a594c04ba95` = sidecar |
| Extract | `data/literature/extracts/lpi-001332.yaml` |

**What landed (printed numbers + locators; d-032 context, 0 scored observations):**
- Volume identity: LPI Contribution 1332; SRR VIII 2006; ISSN 0161-5297
- Cardiff & Pomeroy p.14 *Development of Vacuum Pyrolysis Techniques*: MLS1A/JSC1A/ilmenite; solar + resistive; MS O₂ detection; SAM heritage ~20 W; 9.6 kW scale-up reflector; HSC Gibbs analysis cited
- Pomeroy & Cardiff p.47 *A Proof of Concept of Vacuum Pyrolysis*: outgas 2 h @ 200 °C; ≤12 °C/min to 1400 °C hold 20 min; MKS PPT RGA; MLS-1A completely vaporized; **non-condensed mass loss 1.17%**

**Typed absences (not ingested):** Cardiff Fig. 1 HSC model vaporization curve; Pomeroy 10–15% O₂-yield numerical analysis; Pomeroy Figs 1–2 digitization; other Roundtable VIII abstracts outside vacuum-pyrolysis scope.

### 2. gal-2018-pnas-calcium-storage — LANDED (identity + method context; misfile)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for Gal et al. / DOI `10.1073/pnas.1804139115` (sidecar already notes unrelated-pnas-oa-misfile vs Sossi Cr PNAS) |
| STEP 0 DOI | `10.1073/pnas.1804139115` — unique; distinct from Sossi Cr `10.1073/pnas.1809060115` |
| PDF ↔ sidecar | Match. PDF p1: Gal, Sorrentino, Kahil, Pereiro, Faivre, Scheffel — *Native-state imaging of calcifying and noncalcifying microalgae…*; PNAS 115(43):11000–11005. sha256 `47384f82da878b0590098d9eaf0d3e7b8f828f665ca62aeb4d6b0a8631a36514` = sidecar |
| Extract | `data/literature/extracts/gal-2018-pnas-calcium-storage.yaml` |

**What landed (context only; 0 scored observations):**
- Document identity confirming biology cryoimaging misfile (not KEMS / not Sossi Cr)
- Cultivation: P. carterae 18 °C artificial seawater; C. reinhardtii 22 °C acetate-phosphate
- CryoSXT energies 520 / 342 / 353.2 eV; Ca L-edge scan 344–360 eV @ 0.1 eV; cryoSEM −120 °C; EDS 6 nm C coat

**Typed absences:** Figs 1–5 imaging panels; entire topic out of vapour-rail scope (no vaporization/KEMS/pyrolysis measurements).

### 3. marcq-2017-magma-ocean-radiation — LANDED (model → typed absence)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Marcq 2017 extract (lebrun-2013 cites Marcq 2012 atmosphere module — different DOI/paper) |
| STEP 0 DOI | `10.1002/2016JE005224` — unique |
| PDF ↔ sidecar | Match. PDF: HAL OA copy; journal article Marcq, Salvador, Massol, Davaille — *Thermal radiation of magma ocean planets using a 1-D radiative-convective model of H₂O-CO₂ atmospheres*; JGR Planets 122, 1539–1553. sha256 `035268efebf0a0560fa702556796ced68c04dcc95eea3733c724b703089296ff` = sidecar |
| Extract | `data/literature/extracts/marcq-2017-magma-ocean-radiation.yaml` |

**What landed:** Context-only scope row (d-032 Option A). **0 scored observations** — radiative-convective model paper; no laboratory measurement tables.

**Typed absences (not ingested):** Figures 1–13 model OLR/ε/Tε/spectra/contrast products; Table 1 model NL comparison (incl. This study 280 W/m²); abstract Key Points Nakajima-limit / cloud / Tε model headlines.

### 4. busemann-2000-phase-q-noble-gases — LANDED (CSSE bench + Table 2 + Ne-Q prose)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for this DOI/title (only `busemann-2024-noble-gases-ryugu-bennu`, which *cites* this 2000 paper for ionization_energy / pressure_calibration_method) |
| STEP 0 DOI | `10.1111/j.1945-5100.2000.tb01485.x` — unique |
| PDF ↔ sidecar | Match. ADS scan; PDF p1 title/authors/MAPS 35 949–973. sha256 `a39d24cb1febb73ef264532439d0e4d21ef15480be734b6a705a6214d122a854` = sidecar. Native text layer empty — pages read via pdftoppm + visual OCR |
| Extract | `data/literature/extracts/busemann-2000-phase-q-noble-gases.yaml` |

**What landed (printed numbers + locators; d-032 context, 0 scored observations):**
- Document identity + six meteorites (Cold Bokkeveld, Grosnaja, Lancé, Isna, Chainpur, Dimmitt)
- HF/HCl residue prep: ≥10 cycles; 10.4 M HF / 1.0 M HCl + 6.0 M HCl; AlCl₃; CS₂/acetone
- **Table 2** sample masses / residue yields / etch mass-loss (Cold Bokkeveld 3.0% yield … Dimmitt 1.9%; null mass-loss retained as absence not zero)
- CSSE bench: Au/Pt line; 65% / 14.4 M HNO₃; charcoal −196/−115 °C; MS 60° 21 cm; **M/ΔM ~550**; **ionization 45 eV**; typical procedure blanks
- Furnace blanks + standard-gas uncertainty estimates (~0.5 / 1 / 4%)
- Prose Ne-Q (²⁰Ne/²²Ne)_Q: Grosnaja 10.66±0.04; Lancé 10.17±0.03; Isna 10.16±0.12; Dimmitt ≤10.75±0.05; Chainpur 10.60±0.06

**Typed absences:** Figure digitization; full etch-step tables beyond Table 2 / prose Ne-Q summary.

## Refusals summary

| Paper | Refused content | Reason |
| --- | --- | --- |
| lpi-001332 | Cardiff Fig. 1 HSC curve; Pomeroy 10–15% O₂ model; Figs 1–2; other SRR abstracts | model_output / figure_only / scope |
| gal-2018-pnas-calcium-storage | Figs 1–5; all vapour-rail observables | out_of_scope_topic (biology misfile) |
| marcq-2017-magma-ocean-radiation | Figs 1–13; Table 1; abstract model headlines | model_output (N14 explicit) |
| busemann-2000-phase-q-noble-gases | Full etch-step tables; figure panels | table_not_fully_extracted / figure_only |

## Commit

```
668ba4354 extracts: N14 LPI-1332 vacuum-pyrolysis abstracts + Busemann Phase Q bench; Gal misfile context; Marcq model typed-absence
```

Files added (4). Private PDFs not committed. Branch pushed.

## Optional follow-ups (not done this lane)

- migrate + readiness / engine_point once I1 is green (likely low yield: 0 scored observations on all four; LPI/Busemann are method/characterization context).
- Remap SOURCE_STATUS inbox → extracted for the four corpus ids.
- Y-audit seat for N14 (cross-check Table 2 / 1.17% / 45 eV / Marcq typed-absence leak).
- If a future ruling admits model_tables for engine-reference, revisit Marcq Table 1 NL column / Cardiff HSC curve under that container — **not** as scored observations.
