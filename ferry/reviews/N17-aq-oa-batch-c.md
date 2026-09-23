# N17 NEW EXTRACT — AQ-OA batch C (BACKLOG 4 acquisition follow-on)

**Branch:** `empirical/n17-aq-oa-batch-c-2026-09-23`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `4813ee1a29b640886ccf09fdd94c226b90ac8152`
**Worktree:** `/workspace/repos/wt/slot-n17`
**Date:** 2026-09-23 (America/Toronto / EDT)
**PDFs:** `/workspace/ferry-inbox/acquired/` (private AQ OA copies; not committed)
**Pushed:** yes → `origin/empirical/n17-aq-oa-batch-c-2026-09-23` (FF from green)

## Validate

```
OK: 4 extract file(s) valid
  ta-mendybaev-2002-lpsc.yaml   (new)
  ta-mendybaev-2020-lpsc.yaml   (new)
  ta-shirai-2000-lpsc.yaml      (new)
  ta-yamanaka-1997-metsoc.yaml  (existing on base; re-checked)
```

0 new errors. Validator via `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py <files>`.

## STEP 0 + no-duplicate

| Paper (corpus stem) | Author/`rg` on green | DOI | Prior extract on green? | PDF ↔ identity |
| --- | --- | --- | --- | --- |
| ta-yamanaka-1997-metsoc | Hit: `data/literature/extracts/ta-yamanaka-1997-metsoc.yaml` | None (MetSoc abstract 5122) | **YES — SKIP** | AQ PDF sha256 `b0ea5696…4fda8a` |
| ta-mendybaev-2002-lpsc | Related full papers `mendybaev-2017` / `mendybaev-2021` only (different works) | None (LPSC XXXIII 2040) | **No** same-paper extract | Match. Title/authors on p1. sha256 `7f9ef6f0…328fc` = B3 corpus copy |
| ta-mendybaev-2020-lpsc | Same; no 2020 LPSC extract on green | None (51st LPSC 2168) | **No** | Match. Title/authors on p1. sha256 `9aedf343…4c23c` = B3 |
| ta-shirai-2000-lpsc | No Shirai extract on green | None (LPSC XXXI 1610) | **No** | Match. **Na₂O–SiO₂ melt**, not forsterite. sha256 `c156af42…f84e8` = B3 |

**Note (no-duplicate across unmerged lanes):** Identical PDF bytes were previously extracted on `empirical/n3-forsterite-evaporation-2026-09-22` (Shirai), `empirical/n4-cai-liquid-evaporation-2026-09-22` (Mendybaev 2020 + initial 2002), and `empirical/n4b-y4-omissions-2026-09-22` (2002 multi-T/Ea fill). Those branches are **not** on `work-v064-green`. N17 lands the same audited YAML blobs onto green so AQ-OA batch C is independently integrable; content is byte-compatible with N3/N4/N4b for clean I2 merge.

## Per-paper status

### 1. ta-yamanaka-1997-metsoc — SKIPPED (STEP 0: extract exists on base)

| Check | Result |
| --- | --- |
| Identify | Yamanaka & Tsuchiyama, MetSoc 1997 abstract 5122 — Na from Na₂O–SiO₂ melt by TGA |
| STEP 0 | `data/literature/extracts/ta-yamanaka-1997-metsoc.yaml` already on `work-v064-green` |
| Action | **No new extract.** Do not duplicate under corpus stem. Validator re-check OK. |

### 2. ta-mendybaev-2002-lpsc — LANDED

| Check | Result |
| --- | --- |
| Extract | `data/literature/extracts/ta-mendybaev-2002-lpsc.yaml` |
| PDF | Mendybaev, Davis & Richter, LPSC XXXIII abstract **2040**, “The Effect of Sample Size on Experimental Evaporation of Type B CAIs” |
| AQ URL | https://www.lpi.usra.edu/meetings/lpsc2002/pdf/2040.pdf (LPI OA) |

**What landed (printed numbers + locators; N4b-complete):**
- Bench `chicago-high-vacuum-furnace-cai`; 1800/1700/1600 °C; P≤10⁻⁶ torr
- Starting B133-glass oxides: MgO 12.0, SiO₂ 46.1, Al₂O₃ 19.4, CaO 25.5 wt%; Ir loops 1.0–6.0 mm
- Scored `rate_series`: (6.8±0.8)×10⁻⁶ g/mm²/min (2.5–6.0 mm) and (1.1±0.2)×10⁻⁵ g/mm²/min (1.0 mm) at 1800 °C
- Scored multi-T 1.0 mm rates ~1.1×10⁻⁵ / ~2×10⁻⁶ / ~3×10⁻⁷ g/mm²/min at 1800/1700/1600 °C; Ea=580 kJ/mole (DERIVED / author)
- Scored `alpha`: Mg kinetic fractionation factor **0.987** (sample-size independent)
- Scope: measured_direct=5, method=2, model_derived=1; typed absence for Fig. 1–3 digitisation, per-run residues, fO₂ buffer

**Not extracted:** Fig. 1–3 curve digitisation; absolute sample masses.

### 3. ta-mendybaev-2020-lpsc — LANDED

| Check | Result |
| --- | --- |
| Extract | `data/literature/extracts/ta-mendybaev-2020-lpsc.yaml` |
| PDF | Mendybaev, Shornikov, Jacobson & Kowalski, 51st LPSC abstract **2168**, “Thermodynamics and Evaporation Kinetics of CAI-like Melts” |
| AQ URL | https://www.hou.usra.edu/meetings/lpsc2020/pdf/2168.pdf (LPI OA) |

**What landed:**
- Bench `nasa-glenn-vacuum-tga` (W5Re/W26Re; P ≤ 5×10⁻⁶ torr; 1800 °C)
- Scored SiO₂ `rate_series`: Langmuir loop **0.3** mg/mm²-hr; Knudsen Ir-cell **6.8** mg/mm²-hr
- Scored `alpha`: experimental **γ_Si ~0.04** (= J_free / J_eq)
- Context only: CMAS/IAS **profile-fit** γ_Mg/γ_Si (authors say these are not true evaporation coefficients) — not scored as measured
- Scope: measured_direct=3, method=2; typed absence for Fig. 1–3 digitisation, a_i measurements, γ_Mg TGA (future work)

**Not extracted:** digitized figures; melt activity measurements (absent from paper).

### 4. ta-shirai-2000-lpsc — LANDED (lane note: Na melt, not forsterite)

| Check | Result |
| --- | --- |
| Extract | `data/literature/extracts/ta-shirai-2000-lpsc.yaml` |
| PDF | Shirai, Tachibana & Tsuchiyama, LPSC XXXI abstract **1610**, “Evaporation rates of Na from Na₂O–SiO₂ melt at 1 atm” |
| AQ URL | https://www.lpi.usra.edu/meetings/LPSC2000/pdf/1610.pdf (LPI OA) |

**What landed:**
- Wire-loop / H₂–CO₂ 1 atm apparatus; start Na₂O 22–23 wt%; T=1300/1400 °C; pO₂=10⁻⁸/10⁻⁹/10⁻¹⁰ bar
- Six printed α\* (0.13 / 0.095 / 0.08 at 1300 °C; 0.048 / 0.043 / 0.05 at 1400 °C)
- Printed log jNa–log pO₂ slopes (−0.153 at 1300 °C; −0.26 at 1400 °C) — author-derived
- Typed absence OK for figure curves (not digitized); no numeric result table beyond prose/figure callouts

**Not extracted:** Fig. 1–2 concentration–time curve digitisation.

## Refusals / skips summary

| Paper | Reason |
| --- | --- |
| ta-yamanaka-1997-metsoc | STEP 0 — already extracted on `work-v064-green` |
| (none refused wholesale among the three assigned) | — |
| mendybaev-2020 profile-fit γ | Context only (model-dependent fits, not true γ) |
| All three: figure curves | No digitisation |

## Commit

```
4813ee1a2 extracts: N17 AQ-OA batch C (Mendybaev 2002/2020 LPSC, Shirai 2000 LPSC)
```

Files added (3). Private PDFs not committed (`*.pdf` gitignored; ferry-inbox outside repo). Audit scratch `_n17_audit/` untracked.

## Push

Pushed FF: `origin/empirical/n17-aq-oa-batch-c-2026-09-23` @ `4813ee1a29b640886ccf09fdd94c226b90ac8152`.
`origin/work-v064-green` is an ancestor of tip (clean FF).

## Optional follow-ups (not done this lane)

- I2: fold with N3/N4/N4b — YAML content matches those branches for the shared stems (identical PDF sha256), so merge should be content-identical.
- migrate + readiness for the three new source_ids once I1/I2 is green.
