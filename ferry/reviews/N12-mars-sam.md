# N12 NEW EXTRACT — Mars SAM clay-sulfate EGA + SAM instrument suite (BACKLOG 4)

**Branch:** `empirical/n12-mars-sam-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `15cb5758c39685987646e9a486365c7683609c5d`
**Worktree:** `/workspace/repos/wt/slot-03`
**Date:** 2026-09-22 (America/Toronto)
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/` (private; not committed)

## Validate

```
OK: 1 extract file(s) valid   # jgr-p-2024-mars-sam-clay-sulfate-ega.yaml
OK: 1 extract file(s) valid   # mahaffy-2012-sam-instrument-suite.yaml
OK: 2 extract file(s) valid   # both together
```

0 new errors. Validator via `/workspace/repos/wt/slot-03/.venv/bin/python tools/validate_literature_extracts.py <file>`.

## Per-paper status

### 1. jgr-p-2024-mars-sam-clay-sulfate-ega — LANDED

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Clark 2024 / clay-sulfate SAM-EGA extract. `nasa-tm-2024-simulant-guide` cites a different Clark 2021 simulant guide. |
| STEP 0 DOI | `10.1029/2024JE008587` — no hit in extracts |
| PDF ↔ sidecar | Match. PDF p1: Clark et al., *Environmental Changes Recorded in Sedimentary Rocks in the Clay-Sulfate Transition Region in Gale Crater, Mars…*, JGR Planets 129 e2024JE008587. sha256 `da22bf1c34fafdc58c39202129fcd09af85a5a7c55768f168a1b8cedc04850d0` = sidecar |
| Extract | `data/literature/extracts/jgr-p-2024-mars-sam-clay-sulfate-ega.yaml` |

**Identity:** Flight SAM-EGA (QMS) of seven Gale clay–sulfate transition drill fines (Nontron NT, Bardou BD, Pontours PT, Maria Gordon MG, Zechstein ZE, Avanavero AV, Canaima CA) plus post-Zechstein Windjana residue blank (Sol 3487). Protocol as printed: quartz cups, ~35 °C/min to ~900 °C, He ~0.8 sccm (~25 mbar).

**What landed (printed main text only):**
- Bench `msl-curiosity-sam-ega-flight`
- Scored `gas_speciation` ranges: H₂O 0.87–7.74 wt.%; SO₃ from SO₂ 0.22±0.13–2.19±1.39 wt.%; Cl from HCl 0.11–0.65 wt.%; C from CO₂ 96–1668 μg/g; CO 0.25–1.5 μmol; named peak temperatures (e.g. AV SO₂ 683 °C; NT1/NT2 545/452 °C; HCl ~550–600 °C; H₂O ~270 °C goethite; mid-T Fe-sulfate SO₂ ~320–750 °C; Mg-sulfate SO₂ >750 °C except BD)
- O₂ non-detection / ClO₄–ClO₃ limit <0.001 wt.%; NO only in BD (~700 °C)
- Context: SAM-EGA protocol, drill inventory + sols, SI Tables S2–S9 typed absence

**Not extracted:** Per-sample SI table cells (not in this PDF); digitised Figs 5–10 EGA traces; FED/FEST delivery masses as points.

### 2. mahaffy-2012-sam-instrument-suite — LANDED (instrument / bench only)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Mahaffy 2012 SAM suite extract. `lpsc-2014-2057` cites Mahaffy as coauthor on a different LPSC abstract. |
| STEP 0 DOI | `10.1007/s11214-012-9879-z` — no hit in extracts |
| PDF ↔ sidecar | Match. PDF p1: Mahaffy et al., *The Sample Analysis at Mars Investigation and Instrument Suite*, Space Sci. Rev. 170:401–478. sha256 `c96a13886d05a0f2028c4867a94d4e74b3c204a20a6365aeb77a54a3ff0ddc09` = sidecar |
| Extract | `data/literature/extracts/mahaffy-2012-sam-instrument-suite.yaml` |

**What landed (N12 instrument/bench rule):**
- Bench `sam-fm-instrument-suite` (QMS + 6× GC + TLS; dual pyrolysis ovens; WRPs)
- One scored `composition_series`: Table 11 calibration-gas cell mole % at 100 °C (CO₂ 24.32, N₂ 24.10, Ar 24.04, XeT 8.48, ¹²⁹Xe 15.51, PFTBA 3.00, 1FN 0.54, DFBP 0.016, PFBP 0.0078); cell 4.76 ml; install 1.3 bar @ 125 °C
- Context: SS-EGA protocol (He ~0.03 atm-cc/s; ramp 35 °C/min; final 950–1100 °C); oven geometry (cup ~1 cm × 0.7 cm ID; 0.51 mm Pt–Zr heater); QMS 1.5–535.5 Da @ 0.1 Da; Table 9 GC columns; TLS Herriott cell (20 cm, 405 cm³, 81 passes / 8.93 m)

**Not extracted:** Mars science-motivation / meteorite / atmosphere survey tables without laboratory numbers; digitised schematics.

## Refusals summary

| Paper | Reason |
| --- | --- |
| (none STOP) | — |
| Clark SI Tables S2–S9 per-sample cells | Typed absence — supporting info not in this PDF |
| Mahaffy Mars-motivation tables | Out of N12 instrument/bench scope (not a paper refusal; extract written) |

## Commit

```
15cb5758c extracts: N12 Clark 2024 Gale SAM-EGA + Mahaffy 2012 SAM instrument suite
```

Files added (2). Private PDFs not committed. Branch pushed to `origin/empirical/n12-mars-sam-2026-09-22`.

## Optional follow-ups (not done this lane)

- migrate + readiness / engine_point once I1 is green
- If SI PDF for Clark 2024 is later ferry’d, fill per-sample abundance tables under the same source_id (or a `…-si` companion — owner call)
- Y-lane PDF cross-audit of Table 11 mole fractions and main-text abundance range endpoints
