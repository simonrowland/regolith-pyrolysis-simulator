# N10 NEW EXTRACT — sf10-accretion-2010 + tenkate-2010-vapor-instrument (BACKLOG 3)

**Branch:** `empirical/n10-sf10-tenkate-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `fca8b044963eb0416f0416b7c310a5409efd8712`
**Worktree:** `/workspace/repos/wt/slot-g2`
**Date:** 2026-09-22 (America/Toronto)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/` (private; not committed)

## Validate

```
OK: 1 extract file(s) valid   # sf10-accretion-2010.yaml
OK: 1 extract file(s) valid   # tenkate-2010-vapor-instrument.yaml
```

0 new errors. Validator via `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py <file>`.

## Per-paper status

### 1. sf10-accretion-2010 — LANDED (model paper; observations empty)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for Schaefer & Fegley 2010 accretion / this DOI. Related but distinct: `schaefer-and-fegley-2007-icarus-outgassing-of-oc`, `kems-008-schaefer-fegley-2004`, `2010zahnle-schaefer-fegley-cshperspect-ori-a004895-2019`
| STEP 0 DOI | `10.1016/j.icarus.2010.01.026` — no hit in extracts |
| PDF ↔ sidecar | Match. PDF p1: Schaefer & Fegley, *Chemistry of atmospheres formed during accretion of the Earth and other terrestrial planets*, Icarus 208 (2010) 438–448. sha256 `b1fe339f3780967dfe165c79b6d7a228ac06e374815978edb7520a6b8307a023` = sidecar |
| Extract | `data/literature/extracts/sf10-accretion-2010.yaml` |

**Identity:** Gibbs-energy chemical-equilibrium **model** of impact-outgassed chondritic atmospheres (nominal **1500 K, 100 bars**; P–T grid log10 P −4…+4, T 300–2500 K). Input bulk compositions **cited** (Lodders 2003 CI Orgueil; Schaefer & Fegley 2007 H/L/LL; METBASE / Koblitz 2005 CM Murchison, CV Allende, EH/EL averages) but **not reprinted** as numeric tables.

**What landed:** Context only (d-032 Option A) — nominal P–T, element/database list, composition-source citations, Lodders (2000) Earth mix fractions 70% EH / 21% H / 5% CV / 4% CI as printed prose.

**Typed absence (model-derived, BLOCKED by BACKLOG 3 / N7 ruling):** Table 1 major gas vol% at 1500 K / 100 bars; Figs 1–7 predicted chemistry; prose linear combinations of Table 1. **Not** ingested as scored observations.

### 2. tenkate-2010-vapor-instrument — LANDED (instrument / breadboard)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for ten Kate / VAPoR / DOI `10.1016/j.pss.2010.03.006` |
| STEP 0 DOI | No hit in extracts |
| PDF ↔ sidecar | Match. PDF p1: ten Kate et al., *VAPoR – Volatile Analysis by Pyrolysis of Regolith…*, Planet. Space Sci. 58 (2010) 1007–1017. sha256 `cad604c54f8982d5eb6f91f122ca1b257252d6994b220333226220b86154df5f` = sidecar |
| Extract | `data/literature/extracts/tenkate-2010-vapor-instrument.yaml` |

**What landed:**
- Bench `vapor-breadboard-knudsen-rga` (modified Knudsen cell + quartz holder + RGA; turbo-diaphragm chamber)
- Experiments: Apollo 16 **64801,53** (~60 mg); Murchison USNM **6650,2** (**8 mg** shown / **60 mg** saturated RGA; 68 mg allocated)
- Protocol: ~1×10⁻⁸ mbar before heat; ~1×10⁻⁷ mbar with filament; **25 → 1200 °C** at **5 °C/min** (K companions DERIVED)
- Two scored `gas_speciation` observations: qualitative Table 4 detection lists for this work (Apollo: H2O, CO/N2, CO2, CH4, SO2, H2S, S/O2, COS, CS2, He; Murchison: H2O, CO/N2, CO2, CH4, SO2, H2S, S/O2, CS2, Organics)
- Context: apparatus/protocol + sample characterization (d-032 Option A)

**Not extracted:** Figs 5–9 EGA traces (figure-only; authors state no quantification / not calibrated); absolute abundances; flight TOF-MS performance beyond design-goal prose.

## Refusals summary

| Paper | Reason |
| --- | --- |
| (none STOP) | — |
| sf10 Table 1 / Figs 1–7 | Typed absence — model-derived cohort (not a refusal of the paper; extract file written) |

## Commit

```
fca8b0449 extracts: N10 Schaefer & Fegley 2010 accretion + ten Kate 2010 VAPoR
```

Files added (2). Private PDFs not committed. Branch pushed to `origin/empirical/n10-sf10-tenkate-2026-09-22`.

## Optional follow-ups (not done this lane)

- migrate + readiness / engine_point for `tenkate-2010-vapor-instrument` once I1 is green
- SOURCE_STATUS remap inbox → extract present for both corpus ids
- Y-lane PDF cross-audit of Table 4 species lists (layout is two-column / sparse)
