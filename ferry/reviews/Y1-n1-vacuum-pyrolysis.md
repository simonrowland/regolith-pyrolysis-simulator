# Y1 — CROSS-AUDIT N1 vacuum pyrolysis — 2026-09-22

**Lane audited:** N1 (`empirical/n1-vacuum-pyrolysis-2026-09-22`)
**Author write-up:** `/workspace/ferry-inbox/reviews/N1-vacuum-pyrolysis.md`
**Tip:** `d4e3d4710d1464563e7434385d863056b0ef486a` — unchanged; no fix commit
**Worktree:** `/workspace/repos/wt/slot-n1`
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{senior-1991-vacuum-pyrolysis,senior-1992-vacuum-pyrolysis,lpsc-2024-bennu-pyrolysis-vandam}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N1 extractor

## VERDICT

**READY.** Tip unchanged.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 0 |
| P3 | 2 |

## Method

Detached/audited at N1 tip. Re-ran `/workspace/repos/regolith-pyrolysis-simulator/.venv/bin/python tools/validate_literature_extracts.py` on both landed extracts → `OK: 2 extract file(s) valid`. For every landed numeric / bound / setpoint cell: `pdftotext -layout` on LPSC + Senior booklet p21; 150 dpi `pdftoppm` raster of Senior PDF p21 for stamps; sha256 vs sidecars; Senior-1992 identity + “vacuum pyrolysis” string count. P0 = a wrong number lands.

## Identity / STEP 0

| Corpus | PDF identity | Disposition | Match |
| --- | --- | --- | --- |
| senior-1991-vacuum-pyrolysis | 50-page scanned *Resources of Near-Earth Space: Abstracts* (Jan 1991). Senior abstract on PDF p21 = booklet “Page 12”; NASA stamp **N91-26033**; handwritten **ABS. ONLY** (with S14-91). Title/author/affiliation match extract. | `senior-1991-vacuum-pyrolysis.yaml` LANDED | OK |
| senior-1992-vacuum-pyrolysis | 208-pp *Recovery and Utilization of Extraterrestrial Resources — A Special Bibliography* (NASA STI, **January 2004**). Not by Senior. | REFUSED (wrong PDF) — no extract | OK |
| lpsc-2024-bennu-pyrolysis-vandam | Mojarro et al. LPSC 2024 abstract **1219** (*Early OSIRIS-REx… Standard and Wet Chemistry Pyrolysis…*). **No VanDam author.** Sidecar has no `citation:`. | `lpsc-2024-bennu-pyrolysis-vandam.yaml` LANDED (stem misnomer noted) | OK |

**sha256 (ferry PDFs = sidecars):**
- senior-1991: `44bd48d6c6b94667f73f75a10d77ffa6d5a53607870e4bdb3775014be445488a`
- senior-1992: `526fc4262ccb23a9fdd156b67c93fd9f2c04f6be9bb03c4f439e99de14811cd3`
- lpsc-2024-vandam: `9444343099d69f6e02b8671e679d20a118e59b03678bcd4d5fe727cbc008d69b`

**STEP 0 prior extract:** no prior Constance L. Senior / this-title extract; robinot-2025 only *cites* Senior in a review table (not a duplicate source extract). No prior Mojarro / 1219 extract under another name.

## Cell-by-cell (printed numbers)

### 1. senior-1991-vacuum-pyrolysis — PASS

**PDF:** scanned abstract booklet. **Landed:** 1 fidelity sample + 1 context row; `scored_observations: 0` (method_observations: 1).

| landed field | landed | printed (PDF p21 / booklet p12) | match |
| --- | --- | --- | --- |
| process_temperature_K_as_printed | `circa 2000` | “moderate temperatures, circa 2000 K” | OK |
| affiliation_as_printed | PSI… 20 New England Business Center, Andover, MA 01810 | same | OK |
| example_reduced_vapour_species_as_printed | SiO, AlO, Fe, Ca | “SiO, AlO” / “Fe, Ca” | OK |
| nasa_stamp_as_printed | N91-26033 | handwritten N91-26033 | OK |
| booklet_handwritten_note | ABS. ONLY | ABS. ONLY (adjacent S14-91) | OK |
| feedstock / reagents / energy claims | unbeneficiated regolith; no chemicals/reagents; direct solar or electrical | same prose | OK |
| demonstration_claim | solar furnace + deferred “These results will be discussed” | same; no numeric yields | OK |

- **Not digitized (correct):** solar-furnace run numbers (none printed); chemical-equilibrium model outputs (none printed); other abstracts on same page (Taylor/Carrier; Zhao/Shadman).

### 2. senior-1992-vacuum-pyrolysis — REFUSAL CONFIRMED

PDF p1 title is the 2004 NASA special bibliography (208 pp). String **“vacuum pyrolysis” count = 0** across full text (~977k chars). Bibliography *cites* Senior accessions **19920035166** / **19920033574** but is not the cited Space Manufacturing / JBIS paper. Sidecar CORRECTION 2026-09-19 matches. **No extract** — correct.

### 3. lpsc-2024-bennu-pyrolysis-vandam (Mojarro 1219) — PASS

**PDF:** 1-page LPSC abstract. **Landed:** 1 scored `gas_speciation` + benches/experiments/context; Kelvin companions stamped DERIVED.

| landed field | landed | printed (PDF p1) | match |
| --- | --- | --- | --- |
| pyrolysis_T_C_as_printed | `600` | standard pyrolysis at 600°C | OK |
| pyrolysis_T_K_derived | `873.15` | DERIVED 600+273.15 | OK |
| wet_chemistry_pyrolysis_T_C_as_printed | `250` | wet chemistry pyrolysis at 250°C | OK |
| wet_chemistry_pyrolysis_T_K_derived / obs T_range | `523.15` | DERIVED 250+273.15 | OK |
| subsample_mass_mg_as_printed | `~1` | Two ~1 mg subsamples | OK |
| mass_kg (experiments) | `0.000001` approx | unit conversion 1 mg → 1e-6 kg | OK |
| subsample_count_per_parent_as_printed | `2` | Two … from each parent | OK |
| quantitation_signal_ratio_vs_murchison_bound | `more than 100x` | “more than 100x … for alanine and glycine” | OK |
| incubation_T / duration | 85°C → `358.15` K DERIVED; 1 hr | “85 °C for 1 hr” | OK |
| silylation volume / ratio | 5 µL; 4:1 v/v | “5 µL … (4:1, v/v)” | OK |
| vial volume | 2 mL | “capped 2 mL vial” | OK |
| GC column | 30 m Rtx-5ms | “30 m Rtx-5ms column” | OK |
| m/z window | 50-500 | “full scan (50-500 m/z)” | OK |
| apparatus | CDS 6200 / TRACE 1600 / TSQ 9610 | same | OK |
| sample IDs | OREX-500002-0 / OREX-800031-0 | same | OK |
| abstract no. | 1219 | header `1219.pdf` | OK |

- **Bound retained:** >100× not coerced to a point — OK.
- **Not digitized (correct):** absolute abundances, chromatograms, chamber pressure, ramp/hold beyond setpoints.

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P3 | senior-1991 | `booklet_handwritten_note: ABS. ONLY` omits adjacent handwritten `S14-91`; ABS. ONLY + N91-26033 remain correct. |
| 2 | P3 | lpsc-2024-vandam | Corpus dir / `source_id` stem `vandam` is a misnomer (no VanDam author); extract + N1 write-up already document Mojarro citation. Owner rename follow-up only. |

## Validation

```
.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/senior-1991-vacuum-pyrolysis.yaml \
  data/literature/extracts/lpsc-2024-bennu-pyrolysis-vandam.yaml
→ OK: 2 extract file(s) valid
```

Commit `d4e3d4710` adds exactly those 2 files (+401 lines); private PDFs not committed.

## Attack checklist

| Attack | Result |
| --- | --- |
| Wrong process T (2000 vs nearby booklet numbers) | Fail — only “circa 2000 K” on Senior abstract |
| Invent Senior-1992 numbers from bibliography abstracts | Fail — correctly refused; 0 “vacuum pyrolysis” hits |
| Force >100× to a point / wrong analyte pair | Fail — bound + alanine/glycine retained |
| Swap 600°C ↔ 250°C or wrong Kelvin companions | Fail — 873.15 / 523.15 / 358.15 match T_C+273.15 |
| Wrong sample IDs or apparatus strings | Fail — OREX IDs + CDS/TRACE/TSQ/Rtx match p1 |
| PDF identity vs citation (VanDam stem) | Fail — citation records Mojarro 1219; misnomer noted |
| Validator regressions | Fail — 2/2 OK |

## Push

None. Tip remains `origin/empirical/n1-vacuum-pyrolysis-2026-09-22` @ `d4e3d4710`.

## Report line

**VERDICT: READY · P0=0 P1=0 P2=0 P3=2 · tip: `d4e3d4710` (unchanged)**
