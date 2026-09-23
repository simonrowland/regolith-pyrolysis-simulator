# Y10 — CROSS-AUDIT: N10 sf10-accretion-2010 + tenkate-2010-vapor-instrument (BACKLOG 3)

**Lane audited:** N10 (`empirical/n10-sf10-tenkate-2026-09-22`)
**Tip:** `fca8b044963eb0416f0416b7c310a5409efd8712` (unchanged; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N10-sf10-tenkate.md`
**Worktree:** `/workspace/repos/wt/slot-y10-n10` (detached @ tip; distinct seat from N10 extractor `slot-g2`)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{sf10-accretion-2010,tenkate-2010-vapor-instrument}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N10 extractor

## Verdict

**READY.** Tip unchanged; nothing to push.

| Severity | Count |
| --- | ---: |
| **P0** (wrong number / wrong species lands) | **0** |
| P1 | 0 |
| P2 | 0 |
| P3 | 2 |

## Method

1. Detached worktree at tip `fca8b0449`. Re-ran `.venv` `tools/validate_literature_extracts.py` on both files → `OK: 2 extract file(s) valid`.
2. `sha256sum` of inbox PDFs vs sidecars / extract provenance strings.
3. `pdftotext -layout` (full + page slices) and 200–300 dpi `pdftoppm` page/row rasters for Table 4 (ten Kate p. 1015) and SF10 Methods (p. 439).
4. Walked every landed numeric / species list under `fidelity_samples`, `experiments.*.sample|conditions`, and `species.*.observations|context[].values`; compared value, unit/basis, DERIVED stamp, and locator page to printed prose / table.
5. Confirmed model-paper typed absence (SF10 Table 1 / Figs 1–7) and instrument-paper non-digitisation (Figs 5–9) against BACKLOG 3 / N7 ruling.

P0 rule (X1–X9 / Y calibration): **P0 only if a wrong number or wrong species lands**. Omission / incompleteness ≤ P1.

## Identity / STEP 0 / PDF ↔ sidecar

| Corpus | PDF p1 identity | Extract | sha256 | Match |
| --- | --- | --- | --- | --- |
| sf10-accretion-2010 | Schaefer & Fegley — *Chemistry of atmospheres formed during accretion…*, Icarus 208 (2010) 438–448 | `sf10-accretion-2010.yaml` LANDED (context only) | `b1fe339f3780967dfe165c79b6d7a228ac06e374815978edb7520a6b8307a023` | OK (= sidecar + extract provenance) |
| tenkate-2010-vapor-instrument | ten Kate et al. — *VAPoR – Volatile Analysis by Pyrolysis of Regolith…*, Planet. Space Sci. 58 (2010) 1007–1017, DOI 10.1016/j.pss.2010.03.006 | `tenkate-2010-vapor-instrument.yaml` LANDED | `cad604c54f8982d5eb6f91f122ca1b257252d6994b220333226220b86154df5f` | OK (= sidecar + extract provenance) |

Locators: SF10 Methods `pdf_page_index: 2` = published 439 (PDF p1 = 438). ten Kate protocol `pdf_page_index: 5` = 1011; Table 4 `pdf_page_index: 9` = 1015. Consistent 1-indexed mapping.

## Per-source audit

### 1. sf10-accretion-2010 — PASS (context-only model paper)

**PDF:** 11-page Icarus modeling paper. **Landed:** 0 scored observations; 1 method context row + 2 fidelity stamps. Table 1 / Figs 1–7 correctly typed-absent (model-derived cohort).

| field | landed | printed (PDF) | locator | match |
| --- | --- | --- | --- | --- |
| nominal_temperature_K_as_printed | 1500 | 1500 K (Abe and Matsui 1987) | p2 / 439 Methods | OK |
| nominal_pressure_bars_as_printed | 100 | 100 bars | same | OK |
| temperature_grid_K | 300–2500 | 300–2500 K | same | OK |
| pressure_grid_log10_P_bars | from −4 to +4 | log₁₀ P of −4 to +4 bars | same | OK |
| ~930 compounds / 20 elements | as printed | ≈930 compounds of 20 elements | same | OK |
| elements_as_printed (20) | Al…Ti same order | Al, C, Ca, Cl, Co, Cr, F, Fe, H, K, Mg, Mn, N, Na, Ni, O, P, S, Si, Ti | same | OK |
| code_family | van Zeggern and Storey (1970) | same | same | OK |
| database | same as Schaefer and Fegley (2007) | same | same | OK |
| chondrite groups | CI, CM, CV, H, L, LL, EH, EL | same list | same | OK |
| CI Orgueil source | Lodders (2003) | (1) Lodders (2003) | same | OK |
| average H/L/LL source | Schaefer and Fegley (2007) | (2) Schaefer and Fegley (2007) | same | OK |
| CM/CV/EH/EL source | METBASE (Koblitz, 2005) | (3) METBASE (Koblitz, 2005) | same | OK |
| Lodders 2000 Earth mix | 70% EH / 21% H / 5% CV / 4% CI | same prose | same | OK |
| Table 1 caption (stamp only) | Major gas… at 1500 K and 100 bars | Table 1 caption identical | p3 / 440 | OK |
| Table 1 vol% / Figs 1–7 | TYPED ABSENCE | model output | — | OK (not ingested) |

- No reprinted numeric bulk compositions invented from Lodders / METBASE citations — correct.
- Prose linear combinations of Table 1 (e.g. 41% H₂ / 28% CO mixes) correctly **not** landed — OK.
- Fidelity samples echo nominal 1500 K / 100 bars only — OK.

### 2. tenkate-2010-vapor-instrument — PASS (Table 4 species + protocol)

**PDF:** 11-page PSS instrument / breadboard paper. **Landed:** 2 scored `gas_speciation` observations (qualitative Table 4 this-work rows) + protocol / sample characterization context; Figs 5–9 not digitised.

#### Protocol / masses / DERIVED K

| field | landed | printed | match |
| --- | --- | --- | --- |
| Apollo mass | ≈60 mg → 6.0×10⁻⁵ kg DERIVED | Approximately 60 mg Apollo regolith | OK |
| Murchison aliquots | 8 mg (shown) / 60 mg (RGA saturated) | 8 and 60 mg; only 8 mg profiles shown | OK |
| allocated / fragment | 68 mg from 6 g interior | 68 mg allocated; 6 g fragment | OK |
| USNM / sieve | USNM 6650,2; &lt;150 µm | same | OK |
| Apollo id / station / sieve | 64801,53; station 4 Descartes; &lt;1 mm | same | OK |
| CRE age | 310 Ma (cosmogenic ²¹Ne, Walton et al. 1973) | 310 million years | OK |
| pumpdown | ~1×10⁻⁸ mbar before heat; ~1×10⁻⁷ with filament | same (text layer `1 × 10⁻⁸` / `10⁻⁷`) | OK |
| filament warmup | 1 hour | left on for an hour | OK |
| ramp | 25 → 1200 °C at 5 °C/min | same | OK |
| T_K DERIVED | 298.15 / 1473.15 | T_C + 273.15 | OK |
| oven design / flight goal | 1200 °C breadboard; 1400 °C flight goal | same | OK |
| cleaning | ultrasonic 10 min DI water, 10 min 95% HPLC hexane, 10 min 99.5% ethanol; bake 2 h @ 500 °C | 95% HPLC grade n-hexane; 99.5% absolute 200-proof ethanol; bake 2 h @ 500 °C | OK (soft paraphrase) |
| bench hardware | WA Technology Knudsen; quartz in graphite; alumina/W wire; Watlow / SSR / Variac; Pt-10%Rh; turbo-diaphragm; RGA | same §3 prose | OK |
| mineralogy % | tochilinite/cronstedtite 58.5; serpentine 22.8; Fo100 7.4; Fo80 2.2; Fo50 2 | Bland et al. 2004 XRD-PSD same | OK |

#### Table 4 — this-work species lists (attack focus)

Raster + text-layer check of Table 4 (p. 1015). Qualitative presence bars only (no abundances — authors state not calibrated).

| sample | landed detected_species_as_printed | PDF Table 4 this-work row | match |
| --- | --- | --- | --- |
| Apollo 16, 64801,53 | H2O, CO/N2, CO2, CH4, SO2, H2S, S/O2, COS, CS2, He | same set (incl. late CH4 bar; COS/CS2 stack; He bar) | OK |
| Murchison | H2O, CO/N2, CO2, CH4, SO2, H2S, S/O2, CS2, Organics | same set; **no COS** (prose: “our analysis did not show the COS⁺ fragment”) | OK |

- Fidelity sample species lists == observation lists — OK.
- Prior-work Table 4 rows (Gibson & Moore; Simoneit) correctly **not** re-scored as this-work detections — OK.
- H₂⁺ mentioned in prose but unconfirmed as sample product / absent from Table 4 this-work — correctly omitted — OK.
- Ar appears in Fig. 6 but not as a Table 4 this-work bar — correctly omitted from scored list — OK.
- `quantification_as_printed` and COS/CS₂ minor-amount note match §5 prose — OK.
- Figs 5–8 supporting-but-not-digitised; Fig. 9 blank — intentional — OK.

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P3 | tenkate | `parent_sample_as_printed: 64801` is unquoted YAML → integer. Value is correct; soft typing hygiene only. |
| 2 | P3 | tenkate | Mineralogy summary soft-compresses “orthopyroxene (enstatite)” → “enstatite”; numeric wt% cells unchanged. |

## Attack checklist

| Attack | Result |
| --- | --- |
| SF10 Table 1 vol% sneak into scored observations | Fail — typed absence only |
| Lodders mix fractions swapped (70/21/5/4) | Fail — match printed |
| Element list permutation / drop | Fail — 20/20 exact order |
| Apollo↔Murchison Table 4 species swap | Fail — Apollo has COS+He, no Organics; Murchison has Organics, no COS/He |
| Invent CH4 on Apollo without Table 4 bar | Fail — late CH4 bar present on Apollo this-work row |
| Invent COS on Murchison | Fail — correctly absent |
| Wrong ramp / T / mass / pressure | Fail — 5 °C/min, 25–1200 °C, 60/8/60 mg, ~1e−8/~1e−7 match |
| DERIVED K arithmetic error | Fail — 25+273.15 / 1200+273.15 |
| PDF identity / sha256 mismatch | Fail — both match sidecars |
| Validator regressions | Fail — 2/2 OK |

## Validation

```
OK: 2 extract file(s) valid
  sf10-accretion-2010.yaml
  tenkate-2010-vapor-instrument.yaml
```

## Push / product fixes

**None.** No P0 with proof. Tip remains `origin/empirical/n10-sf10-tenkate-2026-09-22` @ `fca8b0449`.

Optional follow-ups (owner call; not blocking): quote `parent_sample_as_printed` as string; SOURCE_STATUS remap / migrate after I1 as N10 noted.

## Report line

**VERDICT: READY · P0: 0 · P1: 0 · P2: 0 · P3: 2 · tip: `fca8b0449` · no push**
