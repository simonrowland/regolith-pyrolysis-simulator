# Y7 — CROSS-AUDIT: N7 model papers, measured/tabulated only (BACKLOG 3)

**Lane:** N7 (`empirical/n7-schaefer-spaargaren-pahlevan-2026-09-22`)
**Tip:** `b3ec515a29ef45d765361d7a55becc064ad5e1f7` (unchanged; no fix commit)
**Prior:** `/workspace/ferry-inbox/reviews/N7-model-measured-only.md`
**Auditor worktree:** `/workspace/repos/wt/slot-y7-n7` (detached @ tip; different seat from extractor)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{schaefer-fegley-2011-vaporization-earth,spaargaren-2025-disk-element-volatility,pahlevan-2026-protolunar-volatile-outflows}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)

## Verdict

**VERDICT: READY**

| Severity | Count |
| --- | ---: |
| **P0** | **0** |
| **P1** | **0** |
| **P2** | **0** |
| **P3** | **2** |

No wrong number lands. Author model outputs are typed absences (not scored observations). Tip unchanged; nothing pushed.

## Method

Blind adversarial check vs PDF (not vs N7 write-up first):

1. Detached worktree at `b3ec515a2`; `pdftotext -layout` of all three PDFs; key table pages re-extracted (`schaefer` pp. 31–34; `spaargaren` p. 3; `pahlevan` pp. 1–3 + full inventory).
2. Enumerated every landed numeric in `fidelity_samples` / `species.*.observations|context[].values` (Table 2 wt%; Table 1 planet M/R/ρ/a/Teq; Table 1 L03/W19 TC).
3. Compared value, unit/basis, column mapping, and locator page against printed cells. Confirmed GGchem / Gibbs / photochemical / hydrodynamic numerics are **absent** from observation payloads.
4. `validate_literature_extracts.py --check-fidelity-match` on the three files → `OK: 3 extract file(s) valid`.
5. PDF sha256 matched each corpus sidecar.

P0 rule (same as X1–X9 / Y calibration): **P0 only if a wrong number lands**. Omission / incompleteness ≤ P1. Model-output ingestion would also be P0 under this lane’s measured/tabulated-only rule.

## Per-source audit

### 1. schaefer-fegley-2011-vaporization-earth — PASS

**PDF:** Schaefer, Lodders, Fegley — *Vaporization of the Earth: Application to Exoplanet Atmospheres*; ApJ 755:41 (2012); DOI `10.1088/0004-637x/755/1/41`. sha256 `9b1e48b4275d28af171e82fa3f7a55c5dd17fadefc484831a5c80074026d3d2d` = sidecar.

#### Landed cells vs printed

| field | landed | printed | match |
| --- | --- | --- | --- |
| Table 2 Cont. Crust O…Cl (18 els) | 47.20 … 0.047 | PDF p. 32 Table 2 | yes (cell-for-cell) |
| Table 2 Cont. Crust TOTAL | 99.822 | same | yes |
| Table 2 BSE O…Cl (18 els) | 44.42 … 0.004; N `0.88×10-4` | same | yes |
| Table 2 BSE TOTAL | 99.776 | same | yes |
| Table 2 refs | Wedepohl 1995; Kargel & Lewis 1993 | footnotes | yes |
| fidelity O / Mg | 47.20 / 22.01 | Table 2 | yes |
| Table 1 11 planets M/R/ρ/a/Teq | Kepler-11 f … 55 Cnc e dual values | PDF p. 31 | yes (context) |
| method_class | `secondary_compilation` | compiled inputs | yes |

#### Typed absences (correct — not ingested)

| refused | PDF | extract |
| --- | --- | --- |
| Table 3 major atm. compositions @ extreme T/P | p. 33 (Gibbs speciation % H2O/CO2/SiO/…) | `typed_absences` kind `model_output` |
| Table 4 photochemical lifetimes | p. 34 (J / tchem) | `typed_absences` kind `model_output` |
| Figures 1–11 | captions after Table 4 | `typed_absences` kind `model_output` |

Leak check: no Table 3 % / Table 4 tchem numerics in observation or context payloads.

### 2. spaargaren-2025-disk-element-volatility — PASS

**PDF:** Spaargaren et al., A&A proofs; DOI `10.1051/0004-6361/202556011`; arXiv:2509.03724v2. sha256 `5a3317c5c7b0c1448672ee7bb9309384062d34646a6b25e36eb4bc4f1d393568` = sidecar.

#### Landed cells vs printed (Table 1, PDF p. 3)

| Element | L03 landed | L03 printed | W19 landed | W19 printed |
| --- | ---: | ---: | ---: | ---: |
| O | 180 | 180 | 183 | 183 |
| Na | 958 | 958 | 1035 | 1035 |
| Mg | 1336 | 1336 | 1343 | 1343 |
| Al | 1653 | 1653 | 1652 | 1652 |
| Si | 1310 | 1310 | 1314 | 1314 |
| S | 664 | 664 | 672 | 672 |
| Ca | 1517 | 1517 | 1535 | 1535 |
| Ti | 1582 | 1582 | 1565 | 1565 |
| Fe | 1334 | 1334 | 1338 | 1338 |
| Ni | 1353 | 1353 | 1363 | 1363 |

Fidelity Mg L03=1336 / Fe W19=1338 — match. Semantics `literature_TC_column_not_GGchem_model`; `column_refused_as_model: GGchem`.

#### Typed absences (correct — not ingested)

| refused | printed | extract |
| --- | --- | --- |
| Table 1 **GGchem** column | O `<400`, Na 941, Mg 1336, Al 1652, Si 1320, S 661, Ca 1512, Ti 1571, Fe 1332, Ni 1282 (±5 K) | `typed_absences` + `column_refused_as_model` |
| Figs 1–16 | proofs figures through Fig. 16 | `typed_absences` |
| Appendices A–D | TC parametrisations / high-C/O / effective TC / devolatilisation | `typed_absences` |

Leak check: GGchem-distinct values (941, 1320, 661, 1512, 1571, 1332, 1282, `<400`) do **not** appear as landed TC. (Al W19=1652 equals GGchem Al by coincidence; column stamp is W19 — OK.)

### 3. pahlevan-2026-protolunar-volatile-outflows — PASS

**PDF:** Pahlevan, Youdin, Sossi — *Hydrodynamic outflows of proto-lunar disk volatiles*; EPSL submission; arXiv `2603.05322`. sha256 `6ef790619b731fb1b983657309dbb584263e0f43be779132ed9c37f1139385ca` = sidecar.

| check | result |
| --- | --- |
| Numbered measurement tables | **0** (`rg` finds no `Table N` captions; header `Figures: 4`) |
| `observations:` | `[]` — 0 scored observations |
| Context scope row | title/authors/EPSL/arxiv/keywords/figures=4/tables=0; `method_class: method_only` |
| Figs 1–4 | captions match typed_absence items (speciation / disk structure / hydrodynamic stability / Na entrainment) |
| Cited Apollo/literature ppm | ~700–3000 ppm (Hirschmann 2018; Marty 2012); 900 ppm H2O / 100 ppm H; McDonough & Sun 1995; McCubbin et al. 2023 — noted in typed_absence only; **not** re-homed as observations |

## P3 (hygiene only)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P3 | pahlevan-2026 | Context row uses `type: laboratory_conditions` for a hydrodynamic **model** manuscript scope stamp. Correctly `method_only` / 0 observations; type name is cosmetic. |
| 2 | P3 | schaefer-fegley-2011 | Table 1 exoplanet compilation lives under `continental_crust.context` rather than its own species key. Numbers and context-only placement are correct. |

## Validation

```
OK: 3 extract file(s) valid
  schaefer-fegley-2011-vaporization-earth.yaml
  spaargaren-2025-disk-element-volatility.yaml
  pahlevan-2026-protolunar-volatile-outflows.yaml
```

(`--check-fidelity-match` included.) PDF ↔ sidecar sha256: all three YES.

## Push / product fixes

**None.** No P0. Tip remains `origin/empirical/n7-schaefer-spaargaren-pahlevan-2026-09-22` @ `b3ec515a29ef45d765361d7a55becc064ad5e1f7`.

## Report line

**VERDICT: READY · P0: 0 · P1: 0 · P2: 0 · P3: 2 · tip: `b3ec515a2` · no push**
