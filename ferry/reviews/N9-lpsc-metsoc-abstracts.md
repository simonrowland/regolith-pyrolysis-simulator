# N9 NEW EXTRACT — LPSC / MetSoc abstracts (BACKLOG 3)

**Branch:** `empirical/n9-lpsc-metsoc-abstracts-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `3a2c8f499392a9b277c42a6023e055bbd753cca5`
**Worktree:** `/workspace/repos/wt/slot-l5`
**Date:** 2026-09-22 (America/Toronto)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/` (private; not committed)
**Pushed:** yes → `origin/empirical/n9-lpsc-metsoc-abstracts-2026-09-22`

## Validate

```
OK: 1 extract file(s) valid   # lpsc-2020-1883.yaml
OK: 1 extract file(s) valid   # metsoc-2024-6397.yaml
OK: 1 extract file(s) valid   # kems-018-stolyarova-2012.yaml (existing; w067 alias check)
```

0 new errors. Validator via `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py <file>`.

## Per-paper status

### 1. lpsc-2020-1883 — LANDED (identify: King et al. LPSC 2020 #1883)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for A. J. King / this abstract under another name |
| STEP 0 DOI | None (LPSC abstract) |
| PDF ↔ sidecar | Match. PDF p1: *CM chondrites from multiple parent bodies…* King, Zulmahilan, Schofield, Russell. sha256 `b42ef98b…09fd6f5` = sidecar |
| Extract | `data/literature/extracts/lpsc-2020-1883.yaml` |

**What landed (printed numbers + locators only; d-032 context, 0 scored observations):**
- Literature CM mineralogy ranges (70–80 / 0–20 / &lt;5 vol%) — Introduction
- PSD-XRD method: ~50–200 mg, 16 h, 30 min standards, &lt;5 vol% uncertainty — Experimental p1
- TGA method: ~10 mg, 10 °C min⁻¹, N₂, 25–1000 °C, H₂O interval 400–770 °C — Experimental p1
- Sample counts: ~135 CRE / 53 PSF / 64 H₂O / 35 both — Experimental p1
- Fig. 2 CRE-bin averages (CM1 PSF&gt;0.8, H₂O ~10 wt%; CM2 PSF ~0.7–0.75, H₂O ~7–8 wt%; &gt;3 Myr PSF ~0.77, H₂O ~8 wt%; error bars 5–10%) — p2
- CM1 alteration T bound &gt;120 °C (quoted prior claim) — p2

**Not extracted:** Fig. 1/2 curve digitization; individual meteorite tables (none printed).

### 2. metsoc-2024-6397 — LANDED (identify: Russell et al. MetSoc 2024 #6397)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for this Bennu mineralogy abstract / OREX-80307* under another name (other MetSoc 2024 abstracts 6131/6224 are different papers) |
| STEP 0 DOI | None (MetSoc abstract) |
| PDF ↔ sidecar | Match. PDF p1: *Mineralogy of Bennu: evidence from space and laboratory*, Russell et al. sha256 `516e1787…ee73ec9` = sidecar |
| Extract | `data/literature/extracts/metsoc-2024-6397.yaml` |

**What landed (printed numbers + locators only; d-032 context, 0 scored observations):**
- Techniques: ~1 mm particles; TIR 2.5–25 µm; Zeiss Evo 15LS / Nicolet iN10 — Techniques
- SEM mineralogy: ~80% phyllosilicates, ~9% sulfides, ~5% magnetite, ~4% carbonates — Results
- Average matrix Mg # ~78% — Results
- Ca-phosphate ≤10 µm; remote-sensing anhydrous silicate ≤10 vol% claim — Results/Discussion
- Returned sample mass ~120 g — Discussion

**Not extracted:** Fig. 1–2 digitization; qualitative carbonate/magnetite morphology prose without new numbers.

### 3. w067-2012 — REFUSED (STEP 0: extract exists)

| Check | Result |
| --- | --- |
| Identify | PDF p1: Valentina Stolyarova, *Thermodynamic properties and vaporization processes of the ionic silicate melts* (Molten Slags 2012 / pyrometallurgy.co.za W067.pdf). **14-page review paper, not a short abstract.** |
| STEP 0 author/`rg` | Hit: `data/literature/extracts/kems-018-stolyarova-2012.yaml` — same title/author/year |
| PDF ↔ existing | sha256 `7a9c05dd…441b4d2f` = sidecar `w067-2012` **and** INDEX raw hash prefix for `kems-018-stolyarova-2012` (`7a9c05dd`) — **same PDF** |
| Existing extract | `source_id: kems-018-stolyarova-2012` (Table 1 vapour-species map; no absolute p series) |

**No new extract** under corpus stem `w067-2012`. Do not duplicate.

## Refusals summary

| Paper (corpus dir) | Reason |
| --- | --- |
| w067-2012 | STEP 0 — already extracted as `kems-018-stolyarova-2012` (identical PDF sha256) |

## Commit

```
3a2c8f499 extracts: N9 King LPSC 2020 1883 + Russell MetSoc 2024 6397 abstracts
```

Files added (2). Private PDFs not committed. Branch pushed.

## Optional follow-ups (not done this lane)

- Remap / alias SOURCE_STATUS `w067-2012` → `kems-018-stolyarova-2012` (same pattern as N5 slag corpus ids).
- migrate + readiness for the two new abstract source_ids once I1 is green (likely low engine_point yield — characterization context only).
