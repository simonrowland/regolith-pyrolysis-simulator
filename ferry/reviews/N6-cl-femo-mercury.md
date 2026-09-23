# N6 NEW EXTRACT — Cl melts / Fe–Mo thermal / Mercury atmosphere (BACKLOG 3)

**Branch:** `empirical/n6-thomas-ueshima-jaggi-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d`
**Tip SHA:** `7d06507ef5cbd0901e58b934758118f0dbefe217`
**Worktree:** `/workspace/repos/wt/slot-07`
**Date:** 2026-09-22 (America/Toronto)

## Validate

```
OK: 3 extract file(s) valid
```

0 new errors. Validator:

```
/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python \
  tools/validate_literature_extracts.py \
  data/literature/extracts/thomas-wood-2021-chlorine-silicate-melts.yaml \
  data/literature/extracts/ueshima-1982-fe-mo-thermal.yaml \
  data/literature/extracts/jaggi-2021-mercury-atmosphere.yaml \
  --check-fidelity-match
```

## Per-paper status

### 1. thomas-wood-2021-chlorine-silicate-melts — LANDED

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | Related extract `thomas-2022-chlorine-bonding-silicate-melts` is a **different** paper (Chem. Geol. DOI `10.1016/j.chemgeo.2022.121269`). No prior extract for DOI `10.1016/j.gca.2020.11.018`. |
| STEP 0 DOI | Clear |
| PDF ↔ sidecar | Match. Title *The chemical behaviour of chlorine in silicate melts*; Thomas & Wood; DOI on p.2. PDF is Elsevier article-in-press (encrypted; text layer + renders usable). |
| Extract | `data/literature/extracts/thomas-wood-2021-chlorine-silicate-melts.yaml` |

**What landed:**
- Bench: Oxford Boyd–England ½″ piston-cylinder; EPMA (CAMECA SX-Five-FE and JEOL-8600, University of Oxford)
- Experiment: Cl solubility series with Ag–AgCl–AgI chlorine buffer; CCO / RRO oxygen buffers
- `composition_series` Table 1 starting melts (CMAS An50Di28Fo22 + Icelandic basalt)
- `composition_series` Table 2 Measured Clglass wt% with per-row T/P/buffer/f(Cl₂) metadata (time, f(Cl₂), f(O₂), T, P series)
- Author Henry-region logClmelt regressions retained as **unscored context** (`model_derived` cohort)
- DERIVED stamps on T_K = T_C + 273.15 only

**Correction retained:** Table 2 reprints AgI/Cl-005 in the pressure block with parenthetical `(0.4)` while the same ID is `(0.04)` elsewhere; both spellings kept, not reconciled.

**Not extracted:** Supplementary Table 1 full glass majors (not in body PDF as numbers); figure-only trends.

### 2. ueshima-1982-fe-mo-thermal — LANDED

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | Prior Ueshima extracts are **different** papers: `kems-200-ueshima-1983` (phase diagram anneal/EPMA), `kems-120-ueshima-1984` (Fe–W), `kems-198-ichise-ueshima-1989` (Ag–Mn). Those *cite* the 1982 thermal-analysis paper; none extract DOI `10.2355/tetsutohagane1955.68.16_2569`. |
| STEP 0 DOI | Clear |
| PDF ↔ sidecar | Match. Japanese body + English Synopsis; title *Thermal Analysis of Fe-Mo Alloys by the Use of Knudsen Cell Mass Spectrometry*; Ueshima / Ichise / Mori; Tetsu-to-Hagané 68 (1982) 2569–2577. |
| Extract | `data/literature/extracts/ueshima-1982-fe-mo-thermal.yaml` |

**What landed:**
- Bench: Hitachi RM-6K KEMS; Al₂O₃ SSA-S cell (OD 11 / ID 9 / h 12 mm; orifice 0.5 mm) in Ta; W/W–Re 26% thermocouples; ⁵⁶Fe ion intensity
- Experiment: Fe–Mo 25–67 at% Mo; 1300–1650 °C; heating/cooling typically 3–5 °C/min
- Three scored `transition_point` observations from the **English Synopsis** invariants:
  - α = L + δ at **1454 ± 2 °C**
  - δ = L + σ at **1503–1521 °C**
  - σ = L + (Mo) at **1610 ± 1 °C** (~70 °C above prior reports)
- DERIVED stamps on T_K = T_C + 273.15 companions

**Not extracted:** Full Table 4 per-heat heating/cooling series (figure/table-dense Japanese pages); left for a deepening pass. Synopsis invariants are the author English summary of this work’s results.

### 3. jaggi-2021-mercury-atmosphere — LANDED (model paper; outputs typed absent)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Jäggi / this DOI extract. `boulliung-2025-mercury-volatile-metals-magmatic` is a different experimental Hg-volatility paper. |
| STEP 0 DOI | Clear (`10.3847/PSJ/ac2dfb`) |
| PDF ↔ sidecar | Match. *Evolution of Mercury’s Earliest Atmosphere*; Jäggi et al.; PSJ 2:230. Supplied PDF is the arXiv/accepted manuscript (2021-10-18). |
| Extract | `data/literature/extracts/jaggi-2021-mercury-atmosphere.yaml` |

**What landed:**
- Model-stack bench noted as **not a laboratory instrument** (SPIDER + VapoRock + VULCAN/FactSage)
- Scored `composition_series`: **Table 2** magma-ocean surface compositions (EH4 / CB / NSP source / NSP lava) as literature compilations used as model inputs (`secondary_compilation`)
- Context: Table 1 case parameters (SN5/SN3/SV/LN5/LN3/LV)
- Context: **typed absence** for Tables 3–5 and all escape/mass-loss/atmosphere-evolution model outputs (BACKLOG 3 N7 model-paper rule)
- Context: MESSENGER surface Na 3–5 wt% quote retained as quoted comparator, not this work’s measurement

**Not extracted / refused as observations:** all model-derived partial pressures, element ratios, escape parameters, and time-averaged mass-loss rates.

## Refusals summary

| Paper | Reason |
| --- | --- |
| (none refused wholesale) | — |
| jaggi model Tables 3–5 + figures | Typed absence (model-derived cohort) |
| ueshima Table 4 full series | Deferred (Synopsis invariants landed) |
| thomas Supplementary Table 1 | Not in body PDF as recoverable numbers |

## Commit

```
7d06507ef5cbd0901e58b934758118f0dbefe217 extracts: N6 Thomas Cl melts, Ueshima Fe-Mo thermal, Jäggi Mercury atmosphere
```

Files added (3). Private PDFs not committed (`.gitignore` `*.pdf`; `ferry/` left untracked).

## Optional follow-ups (not done this lane)

- Deepen Ueshima Table 4 per-run Th/Tc series from PNG renders
- migrate + readiness once I1 is green
- Y-lane cross-audit against PDFs (Y6)
