# N7 NEW EXTRACT — model papers, measured/tabulated only (BACKLOG 3)

**Branch:** `empirical/n7-schaefer-spaargaren-pahlevan-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`
**Tip SHA:** `b3ec515a29ef45d765361d7a55becc064ad5e1f7`
**Worktree:** `/workspace/repos/wt/slot-08`
**Date:** 2026-09-22 (America/Toronto)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/` (private; not committed)

## Validate

```
OK: 3 extract file(s) valid
```

0 new errors. Validator run with `--check-fidelity-match` on each file and on all three together.

## Policy (N7 / BACKLOG 3)

Extract **only** printed MEASURED / TABULATED non-model data. Author **model
outputs** are the model-derived cohort the owner blocked: record as
`typed_absences`, **do not ingest as observations**.

## Per-paper status

### 1. schaefer-fegley-2011-vaporization-earth — LANDED (tabulated inputs only)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for this title/DOI under another name (other Schaefer/Fegley extracts are distinct DOIs) |
| STEP 0 DOI | `10.1088/0004-637x/755/1/41` — unique |
| PDF ↔ sidecar | Match. PDF p1: Schaefer, Lodders, Fegley — *Vaporization of the Earth: Application to Exoplanet Atmospheres*; ApJ manuscript (submitted 2011-08-22; published ApJ 755:41, 2012). sha256 `9b1e48b4275d28af171e82fa3f7a55c5dd17fadefc484831a5c80074026d3d2d` = sidecar |
| Extract | `data/literature/extracts/schaefer-fegley-2011-vaporization-earth.yaml` |

**What landed:**
- Table 2 continental-crust + BSE elemental wt% (Wedepohl 1995; Kargel & Lewis 1993) as `composition_series` / secondary-compilation observations
- Table 1 compiled measured exoplanet M/R/ρ/a/Teq kept in **context** (astronomy compilation, not vapour-rail lab data)

**Typed absences (not ingested):** Tables 3–4 Gibbs/photochemical model tables; Figures 1–11 model curves.

### 2. spaargaren-2025-disk-element-volatility — LANDED (literature TC columns only)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Spaargaren extract |
| STEP 0 DOI | `10.1051/0004-6361/202556011` — unique |
| PDF ↔ sidecar | Match. PDF p1: Spaargaren et al., A&A proofs / arXiv:2509.03724v2. sha256 `5a3317c5c7b0c1448672ee7bb9309384062d34646a6b25e36eb4bc4f1d393568` = sidecar |
| Extract | `data/literature/extracts/spaargaren-2025-disk-element-volatility.yaml` |

**What landed:**
- Table 1 **L03** (Lodders 2003) and **W19** (Wood et al. 2019) condensation-temperature columns as `volatility_series` secondary compilations

**Typed absences (not ingested):** Table 1 **GGchem** model column; Figures 1–16; Appendices A–D model parametrisations / simulation products.

### 3. pahlevan-2026-protolunar-volatile-outflows — LANDED (context + typed absence only)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Pahlevan extract (only incidental "protolunar" prose elsewhere) |
| STEP 0 DOI | None on sidecar (arXiv `2603.05322`) — no extract collision |
| PDF ↔ sidecar | Match. PDF p1: Pahlevan, Youdin, Sossi — *Hydrodynamic outflows of proto-lunar disk volatiles*; EPSL submission; Figures: 4. sha256 `6ef790619b731fb1b983657309dbb584263e0f43be779132ed9c37f1139385ca` = sidecar |
| Extract | `data/literature/extracts/pahlevan-2026-protolunar-volatile-outflows.yaml` |

**What landed:** Context-only scope row (d-032 Option A). **0 scored observations** — manuscript has no numbered measurement tables.

**Typed absences (not ingested):** Figures 1–4 hydrodynamic / speciation model products; cited Apollo/literature ppm values left attributed to prior works (not re-homed).

## Refusals summary

| Paper | Refused content | Reason |
| --- | --- | --- |
| schaefer-fegley-2011 | Tables 3–4, Figs 1–11 | model_output (Gibbs / photochemistry) |
| spaargaren-2025 | Table 1 GGchem column; Figs 1–16; Apps A–D | model_output (GGchem / simulations) |
| pahlevan-2026 | Figs 1–4; all author model numerics | model_output; no measurement tables |

## Commit

```
b3ec515a2 extracts: N7 Schaefer tabulated crust/BSE + Spaargaren L03/W19 TC; Pahlevan model typed-absence
```

Files added (3). Private PDFs not committed. Pushed to
`origin/empirical/n7-schaefer-spaargaren-pahlevan-2026-09-22`.

## Optional follow-ups (not done this lane)

- migrate + readiness / engine_point once I1 is green (optional).
- Remap SOURCE_STATUS inbox → extracted for the three corpus ids.
- If a future ruling admits model_tables for engine-reference, revisit Schaefer
  T3–T4 / Spaargaren GGchem under that container — **not** as scored observations.
