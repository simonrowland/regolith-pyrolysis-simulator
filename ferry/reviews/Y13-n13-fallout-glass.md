# Y13 — CROSS-AUDIT: N13 fallout melt glass (BACKLOG 4)

**Lane audited:** N13 (`empirical/n13-fallout-glass-2026-09-22`)
**Tip:** `e3315d37b8f006bf0e237db337b70085aba0761a` (on `origin`; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N13-fallout-glass.md`
**Worktree:** `/workspace/repos/wt/slot-y10-n10` (detached @ tip; distinct seat from N13 extractor `slot-n13`)
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/{eppich-2014-fallout-melt-glass,bonamici-2017-glassy-fallout-debris,ross-1948-optical-properties-alamogordo-glass}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N13 extractor

## Verdict

**P0: 0** — no wrong number / wrong species that can reach result/score/ledger today.
**READY** at tip `e3315d37b`. No fix branch.

| Severity | Count |
| --- | ---: |
| **P0** (wrong number / wrong species lands) | **0** |
| P1 | 0 |
| P2 | 2 |
| P3 | 2 |

## Method

1. Confirmed tip on origin (`git ls-remote` → `e3315d37b…`); recycled free worktree `slot-y10-n10` detached at tip (N13 extractor remains on `slot-n13`).
2. Re-ran `.venv` `tools/validate_literature_extracts.py --check-fidelity-match` on all three files → `OK: 3 extract file(s) valid`.
3. `sha256sum` of inbox PDFs vs sidecars / extract provenance strings (all three match).
4. `pdftotext -layout` full text + form-feed page map; 150–300 dpi `pdftoppm` rasters for Eppich Table 1–2 (pdf pp. 35–36), Bonamici Table 1 (pdf p. 50), Ross pp. 2–3.
5. Programmatic row-diff of every Eppich Table 1 oxide/uncertainty/mass/shape field and every Table 2 ratio vs PDF parse → **0 mismatches** (22+22 rows). Bonamici Table 1 six mode rows + Average Si/K/Na/Al/Ca/Mg/Fe/O/C → **0 mismatches**. Walked Ross refractive / thickness / fusion-T context against raster + text.
6. Checked typed absences (SiO₂ ICP-MS; actinide Tables 3–5; Bonamici EPMA / Supplemental Table 1; Ross Fig. 1 grey levels; optical `n` not scored) against vapour-rail scope and PDF presence.

P0 rule (X1–X9 / Y calibration): **P0 only if a wrong number or wrong species lands**. Omission / incompleteness ≤ P1; locator coarseness ≤ P2; naming / trailing-zero soft ≤ P3.

## Identity / STEP 0 / PDF ↔ sidecar

| Corpus | PDF p1 identity | Extract | sha256 | Match |
| --- | --- | --- | --- | --- |
| eppich-2014-fallout-melt-glass | Eppich et al. — *Constraints on fallout melt glass formation…*, LLNL-JRNL-650394 / JRNC OSTI MS, DOI 10.1007/s10967-014-3293-9, 39 pp | `eppich-2014-fallout-melt-glass.yaml` LANDED (scored + context) | `ba4b22efdf476b09342076f55a2ed89eee77357103eeff34ce5740473246490f` | OK (= sidecar + extract provenance) |
| bonamici-2017-glassy-fallout-debris | Bonamici et al. — *A geochemical approach to constraining… glassy fallout debris…*, LA-UR-15-24262 / CMP, DOI 10.1007/s00410-016-1320-2, 50 pp | `bonamici-2017-glassy-fallout-debris.yaml` LANDED (context-only) | `477906e36aab1998b046007f91dc88c1e66c47b68cad70c20736b2fb7fd0a7f8` | OK |
| ross-1948-optical-properties-alamogordo-glass | Ross, C. S. — *Optical properties of glass from Alamogordo, New Mexico*, Am. Mineral. 33, 360–362, 3 pp | `ross-1948-optical-properties-alamogordo-glass.yaml` LANDED (context-only) | `2a456984d319d4d5efed3c9715656a451daf74f732fea9c76bb24861cdedf739` | OK |

STEP 0: no prior Eppich 2014 / DOI 10.1007/s10967-014-3293-9 extract. No prior Bonamici 2017 / DOI 10.1007/s00410-016-1320-2 extract. No prior Ross 1948 Alamogordo optical extract. `wimpenny-2019-zn-isotope-evaporation-extreme-t` cites Eppich as a coauthor and has fallout-glass Zn comparison rows — different paper (as N13 claimed).

## P0 findings

None.

## Soft findings (not blocking READY)

| # | sev | source | finding |
| --- | --- | --- | --- |
| 1 | P2 | bonamici | Condensation / Tg observation locator is `pdf_page_index: 16` (correct for ~2200 K / Tg >900 K). The printed range **1800–2200 K** and model-ending **900 K** also appear on pdf p. 18; single locator is coarse but values match print. |
| 2 | P2 | ross | Refractive-index observation locator cites pdf p. 2 / published 361 (correct for **n = 1.51–1.53** / 1.54). Silica **n close to 1.46** continues on pdf p. 3 / 362 under the same observation_id. |
| 3 | P3 | eppich | Context field `glass_mass_range_g_as_printed: ~7 to 36 mg (Table 1)` — value/unit string is correct as printed; field key suffix `_g_` mismatches mg. |
| 4 | P3 | eppich | Author Discussion prose “Na₂O … up to a factor of **0.86**” is retained as printed; Table 2 Na ratios go as low as **0.83** (F-8). Source prose↔table tension, not an extract transcription error. |

## Per-source audit

### 1. eppich-2014-fallout-melt-glass — PASS (scored Table 1 + DERIVED Table 2)

**PDF:** 39-page OSTI author manuscript. **Landed:** 2 scored `composition_series` (Table 1 measured oxides F-7…F-28; Table 2 glass/sediment ratios with `DERIVED: true`) + sediment-average prose + Na-volatilisation interpretation context. Actinide Tables 3–5 / figures not ingested (typed absence / out of vapour-rail scope).

#### Table 1 attack focus (raster pdf p. 35 / stamp 33)

| pin | landed | printed | match |
| --- | --- | --- | --- |
| F-7 Na₂O ± | 3.52 ± 0.11 wt% | same | OK |
| F-7 FeO ± | 3.05 ± 0.15 wt% | same | OK |
| F-28 FeO ± | 3.00 ± 0.10 wt% | same | OK |
| F-28 Na₂O ± | 3.13 ± 0.07 wt% | same | OK |
| n oxide rows | 22 (F-7…F-28) | 22; F-1…F-6 NM | OK |
| n mass+shape | 28 | F-1…F-28 | OK |
| k=2 expanded uncert. | stated | caption | OK |
| full 22×(mass, shape, 6 oxides × value+uncert) | — | programmatic diff | **0 mismatches** |

Spot-checked F-18…F-28 on 300 dpi crop (incl. F-20 Al₂O₃ **11.0 ± 0.3**) against extract — OK.

#### Table 2 (DERIVED; raster pdf p. 36 / stamp 34)

| pin | landed | printed | match |
| --- | --- | --- | --- |
| F-7 Na₂O ratio | 0.98 | 0.98 | OK |
| F-7 MgO…FeO | 1.87 / 1.08 / 0.96 / 1.19 / 1.39 | same | OK |
| F-28 ratios | 0.87 / 1.59 / 1.08 / 1.03 / 0.74 / 1.37 | same | OK |
| all 22×6 ratios | — | programmatic diff | **0 mismatches** |
| DERIVED stamp | true; author glass÷six-sediment average | caption + Methods | OK |

#### Context

| field | landed | printed | PDF page | match |
| --- | --- | --- | --- | --- |
| sediment Na₂O…FeO averages | ~3.6 / ~0.3 / ~10.4 / ~4.5 / ~1.5 / ~2.2 wt% | same | 12 | OK |
| SiO₂ by difference | ~72 to 75 wt%; semi-quant XRF | same | 12 | OK |
| Na₂O boiling / hold claim | 1950 °C; assumptions retained; not measured run T | same | 15 | OK |
| mass range prose | ~7 to 36 mg | same (Table 1 ≈7.1–35.6 mg) | 7 / Table 1 | OK (see P3 key name) |

### 2. bonamici-2017-glassy-fallout-debris — PASS (context-only)

**PDF:** 50-page LANL accepted manuscript. **Landed:** 0 scored observations. Context = Table 1 mode-based sediment estimates (`author_estimate`) + glass lineages + condensation/Tg/viscosity bounds. EPMA major-oxide tables correctly typed-absent (figures / Supplemental Materials Table 1 not in this ferry PDF).

#### Table 1 (raster pdf p. 50)

| field | landed | printed | match |
| --- | --- | --- | --- |
| High Quartz Si | 30.5 | 30.5 | OK |
| Average Si/K/Na/Al/Ca/Mg/Fe/O/C | 29.0 / 4.1 / 2.7 / 7.1 / 5.9 / 0.7 / 1.6 / 47.5 / 1.3 | same | OK |
| six mode rows (modes + 9 elements) | full | programmatic diff | **0 mismatches** |
| Min/Max element wt% | as printed | same | OK |
| method_class | author_estimate / not direct bulk assay | footnotes a–e | OK |

#### Temperature / viscosity context (pdf pp. 16–18)

| field | landed | printed | match |
| --- | --- | --- | --- |
| condensation upper | ~2200 K | “upper temperature limit … of ~2200 K” | OK |
| condensation range | 1800–2200 K | “constrained at 1800-2200 K” | OK (locator soft — P2) |
| Tg alkali/CaMgFe | >900 K | “greater than 900 K” | OK |
| model ending T | 900 K | “fixed at 900 K” | OK |
| Giordano et al. (2008) window | 600–1800 K; cal. limit 1800 K | same | OK |
| alkali vs CaMgFe viscosity | 2–5 orders (800–1800 K) | same | OK |
| silica vs CaMgFe viscosity | ~5–10 orders (Ryan & Blevins 1987) | same | OK |

Glass lineages (pdf p. 9): silica (lechatelierite) / alkali / CaMgFe — OK. EPMA tables absent from main PDF — OK typed absence.

### 3. ross-1948-optical-properties-alamogordo-glass — PASS (context-only)

**PDF:** 3-page Am. Mineral. note (pp. 360–362). **Landed:** 0 scored observations (optical `n` not a vapour-rail scored type). Context = layer morphology, refractive indices, fusion-T prose, copper pigment note.

| field | landed | printed | match |
| --- | --- | --- | --- |
| layer thickness | 1 to 2 cm | “1 to 2 centimeters” | OK |
| colour / texture | pale bottle green; extremely vesicular | same | OK |
| feldspar–clay n | 1.51 to 1.53; high 1.54; Fe-Mg to 1.55 | same (raster p. 361) | OK |
| silica n | close to 1.46 | same (p. 362) | OK (locator soft — P2) |
| cristobalite fusion | 1713 °C | same | OK |
| quartz direct fusion | slightly below 1470 °C | same | OK |
| explosion claim | much hotter / sudden → quartz → silica glass | same | OK |
| copper pigment | spectrographic Cu; likely wire | same | OK |

Fig. 1 photomicrograph grey levels not digitised — OK typed absence.

## Coverage / invented-data check

- No invented oxide rows, ratios, or temperatures beyond printed prose/tables.
- Scored path is only Eppich measured Table 1 + author-calculated Table 2 (DERIVED stamped).
- Bonamici / Ross correctly context-only; optical `n` and mode-based sediment estimates not coerced into scored vapour-rail types.
- Actinides, supplemental EPMA, and figure-only curves remain typed-absent with justification.

## Validate

```
OK: 3 extract file(s) valid   # --check-fidelity-match
```

## Tip / branch

- **Branch:** `empirical/n13-fallout-glass-2026-09-22`
- **Tip:** `e3315d37b8f006bf0e237db337b70085aba0761a`
- **Fix branch:** none (READY as-landed)
