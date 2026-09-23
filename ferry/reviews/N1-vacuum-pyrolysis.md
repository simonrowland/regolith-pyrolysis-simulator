# N1 NEW EXTRACT — vacuum pyrolysis (BACKLOG 3)

**Branch:** `empirical/n1-vacuum-pyrolysis-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d`
**Tip SHA:** `d4e3d4710d1464563e7434385d863056b0ef486a`
**Worktree:** `/workspace/repos/wt/slot-n1` (created; avoided slot-02 / w3b-green / slot-l5)
**Date:** 2026-09-22 (America/Toronto)

## Validate

```
OK: 1 extract file(s) valid   # senior-1991-vacuum-pyrolysis.yaml
OK: 1 extract file(s) valid   # lpsc-2024-bennu-pyrolysis-vandam.yaml
```

0 new errors. Validator via `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py <file>`.

## Per-paper status

### 1. senior-1991-vacuum-pyrolysis — LANDED

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior extract for Constance L. Senior / this title |
| STEP 0 DOI | None on sidecar/PDF (NTRS abstract booklet) |
| PDF ↔ sidecar | Match. Booklet *Resources of Near-Earth Space: Abstracts* (1991); Senior abstract on PDF p21 / booklet p12; NASA stamp N91-26033; handwritten ABS. ONLY |
| Extract | `data/literature/extracts/senior-1991-vacuum-pyrolysis.yaml` |

**What landed:** Abstract-only context (d-032 Option A). Printed process claim `circa 2000 K`, feedstock/reagent/energy claims, example vapour species (SiO, AlO, Fe, Ca). No scored observations — solar-furnace results are deferred in prose and not printed as numbers. Modeled on filiberto-style abstract context + cardiff/steurer pyrolysis wording.

### 2. senior-1992-vacuum-pyrolysis — REFUSED (wrong PDF)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No prior Senior extract under another name |
| PDF ↔ sidecar | **FAIL.** Sidecar cites Senior 1991/1992 Space Manufacturing pyrolysis paper; PDF p1 is *Recovery and Utilization of Extraterrestrial Resources — A Special Bibliography* (NASA STI, January 2004), 208 pp. Sidecar already documents this (CORRECTION 2026-09-19). String "vacuum pyrolysis" absent; no measurement data. Bibliography *cites* Senior (accessions 19920035166 / 19920033574) but is not by Senior. |

**No extract written.** True paper still `access: held` / not obtained. Do not invent from bibliography abstracts.

### 3. lpsc-2024-bennu-pyrolysis-vandam — LANDED (corpus-name mismatch noted)

| Check | Result |
| --- | --- |
| STEP 0 author/`rg` | No Mojarro / abstract-1219 extract under another name |
| STEP 0 DOI | None (LPSC abstract) |
| PDF ↔ sidecar citation | Sidecar has **no** `citation:` field. PDF p1 is Mojarro et al. LPSC 2024 abstract **1219** (*Early OSIRIS-REx Mission Results from Standard and Wet Chemistry Pyrolysis…*). **No VanDam author.** Corpus dir stem `vandam` is a misnomer; HOU 1219.pdf title/authors confirm Mojarro. |
| Extract | `data/literature/extracts/lpsc-2024-bennu-pyrolysis-vandam.yaml` (source_id retains corpus dir name; citation records Mojarro et al.) |

**What landed:**
- Bench `gsfc-cds6200-py-gc-qqq` (CDS 6200 / TRACE 1600 / TSQ 9610 / 30 m Rtx-5ms)
- Experiments: standard pyrolysis 600 °C; wet-chemistry silylation pyrolysis 250 °C (~1 mg subsamples; OREX-500002-0 / OREX-800031-0)
- One scored `gas_speciation` observation: QL alanine+glycine quantitation signals **more than 100×** vs co-analyzed Murchison (bound retained, not coerced to a point)
- Context (d-032 Option A): apparatus/prep, qualitative PAH/amino-acid detection lists, TAGSAM relative prose
- DERIVED stamps on T_K = T_C + 273.15 companions only

**Not extracted:** absolute abundances, chromatograms, chamber pressure, ramp/hold times beyond printed setpoints.

## Refusals summary

| Paper | Reason |
| --- | --- |
| senior-1992-vacuum-pyrolysis | Wrong PDF (2004 NASA bibliography ≠ cited Senior paper) |
| (none other) | — |

## Commit

```
d4e3d4710 extracts: N1 Senior 1991 abstract + Mojarro LPSC 2024 Bennu pyrolysis
```

Files added (2). Private PDFs not committed.

## Optional follow-ups (not done this lane)

- Obtain true Senior Space Manufacturing / JBIS papers for a future extract under a corrected source_id.
- Rename corpus dir / SOURCE_STATUS `lpsc-2024-bennu-pyrolysis-vandam` → Mojarro-keyed id (owner call); extract already states the misnomer.
- migrate + readiness for the new source_ids once I1 is green.
