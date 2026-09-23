# N4 NEW EXTRACT — CAI-liquid evaporation (BACKLOG 3)

**Branch:** `empirical/n4-cai-liquid-evaporation-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d`
**Tip SHA:** `700e1aeef61eaf4962e0238da00a665eb14a0313`
**Worktree:** `/workspace/repos/wt/slot-05`
**Date:** 2026-09-22 (America/Toronto / EDT)

## Validate

```
OK: 3 extract file(s) valid
  ta-mendybaev-2002-lpsc.yaml
  ta-mendybaev-2020-lpsc.yaml
  richter-2008-cai-like-liquids-lpsc-abstract.yaml
```

0 new errors. Validator via `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py <files>`.

## STEP 0 duplicate check

| Paper | Author/`rg` | DOI | Prior extract? | PDF ↔ sidecar |
| --- | --- | --- | --- | --- |
| ta-mendybaev-2002-lpsc | Mendybaev extracts exist for **2017** and **2021** full papers only | None (LPSC abstract 2040) | **No** same-paper extract | Match. LPSC XXXIII 2040; title/authors on p1 |
| ta-mendybaev-2020-lpsc | Same; no 2020 LPSC extract | None (LPSC abstract 2168) | **No** | Match. 51st LPSC 2168; title/authors on p1 |
| richter-2008-cai-like-liquids-lpsc-abstract | Related full papers `kems-010-richter-2007`, `kems-037-richter-2002`, `richter-et-al-2007` exist — **different works** | None (LPSC abstract 1385) | **No** LPSC-2008 extract | **Citation mismatch** (see below); PDF is the cited abstract path |

SOURCE_STATUS: all three were `stage: inbox` / “document in corpus raw/; no extract”. PDF sha256 matched sidecars.

## Per-paper status

### 1. ta-mendybaev-2002-lpsc — LANDED

| Check | Result |
| --- | --- |
| Extract | `data/literature/extracts/ta-mendybaev-2002-lpsc.yaml` |
| PDF | Mendybaev, Davis & Richter, LPSC XXXIII abstract **2040**, “The Effect of Sample Size on Experimental Evaporation of Type B CAIs” |

**What landed:**
- Bench `chicago-high-vacuum-furnace-cai`; experiments at 1800 °C (rate arm) and multi-T campaign context (1800/1700/1600 °C)
- Starting B133-glass oxides as printed: MgO 12.0, SiO2 46.1, Al2O3 19.4, CaO 25.5 wt%; Ir loops 1.0–6.0 mm; P≤10⁻⁶ torr
- Scored `rate_series`: (6.8±0.8)×10⁻⁶ g/mm²/min (2.5–6.0 mm) and (1.1±0.2)×10⁻⁵ g/mm²/min (1.0 mm) at 1800 °C
- Scored `alpha`: Mg kinetic fractionation factor **0.987** (sample-size independent)
- DERIVED stamps on T_K = T_C + 273.15 companions only

**Not extracted:** Fig. 1–3 digitisation; per-run residue tables; 1700/1600 °C numeric rates (not printed).

### 2. ta-mendybaev-2020-lpsc — LANDED

| Check | Result |
| --- | --- |
| Extract | `data/literature/extracts/ta-mendybaev-2020-lpsc.yaml` |
| PDF | Mendybaev, Shornikov, Jacobson & Kowalski, 51st LPSC abstract **2168**, “Thermodynamics and Evaporation Kinetics of CAI-like Melts” |

**What landed:**
- Bench `nasa-glenn-vacuum-tga` (W5Re/W26Re; microbalance every 6 s; P ≤ 5×10⁻⁶ torr; 1800 °C)
- Scored SiO2 `rate_series`: Langmuir loop **0.3** mg/mm²-hr; Knudsen Ir-cell **6.8** mg/mm²-hr
- Scored `alpha`: experimental **γ_Si ~0.04** (= J_free / J_eq)
- Context only: CMAS/IAS **profile-fit** γ_Mg/γ_Si values (authors explicitly say these are not true evaporation coefficients)

**Not extracted:** Fig. 1–3 digitisation; γ_Mg TGA determination (future work); melt a_i measurements (absent).

### 3. richter-2008-cai-like-liquids-lpsc-abstract — LANDED (figure-only / context-heavy)

| Check | Result |
| --- | --- |
| Extract | `data/literature/extracts/richter-2008-cai-like-liquids-lpsc-abstract.yaml` |
| PDF | Richter, Teng, Mendybaev, Davis & Georg, LPSC XXXIX abstract **1385**, “…Evaporation in Low Pressure H2” |

**Sidecar mismatch (recorded in extract):** sidecar title appends “in hydrogen gas and in vacuum” and lists Wadhwa; PDF title is Low Pressure H2 and authors are Teng/Georg (no Wadhwa). Citation follows PDF.

**What landed:**
- H2 pressure regime thresholds (~10⁻⁷ bar vacuum-like; √P_H2 scaling above; comparison at 2×10⁻⁴ bar)
- Remeasurement arm at **1.87×10⁻⁴** bar H2 / 1500 °C (conditions + ICPMS method)
- One `rate_series` row: Fig. 1 caption claim “about two orders of magnitude larger” H2 vs vacuum-extrapolated rate at 1500 °C — `method_class: figure_only`; points not digitized
- Fig. 2/3 Mg isotope alpha: **no prose numeric α** for the new H2 arm → not scored

**Not extracted:** digitized Fig. 1–3; starting oxide assay (not printed); absolute rates.

## Refusals summary

| Paper | Reason |
| --- | --- |
| (none refused wholesale) | — |
| richter-2008 figures / fitted α | No digitisation; no prose α for new H2 arm |
| mendybaev-2020 profile-fit γ | Context only (model-dependent fits, not true γ) |

Related prior extracts (`mendybaev-2017`, `mendybaev-2021`, `kems-010-richter-2007`, `kems-037-richter-2002`) left untouched — different papers.

## Commit

```
700e1aeef extracts: N4 CAI-liquid evaporation LPSC abstracts (Mendybaev 2002/2020, Richter 2008)
```

Files added (3). Private PDFs not committed (`*.pdf` gitignored; ferry/ untracked).

## Push

Pushed: yes (`origin/empirical/n4-cai-liquid-evaporation-2026-09-22` @ `700e1aeef`).

## Optional follow-ups (not done this lane)

- migrate + readiness for the three new source_ids once I1 is green.
- Digitize Fig. 1–3 on Richter 2008 / Mendybaev 2002 only if owner authorizes figure digitisation.
- Consider correcting corpus sidecar citation for richter-2008 to match PDF authors/title.
