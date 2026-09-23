# Y11 — CROSS-AUDIT: N11 NIST NBSIR + NTRS O2-compat (BACKLOG 4)

**Lane audited:** N11 (`empirical/n11-nist-ntrs-2026-09-22`)
**Tip:** `e81eab4d94f70fc6b314cd3e7547f614456a8d1c` (unchanged; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N11-nist-ntrs.md`
**Worktree:** `/workspace/repos/wt/slot-y11-n11` (detached @ tip; distinct seat from N11 extractor `slot-12`)
**PDFs:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/new-pdfs/{nist-nbsir-77-859,ntrs-20100041337,ntrs-20240012816}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N11 extractor

## Verdict

**READY.** Tip unchanged; nothing to push.

| Severity | Count |
| --- | ---: |
| **P0** (wrong number / wrong species lands) | **0** |
| P1 | 0 |
| P2 | 3 |
| P3 | 1 |

## Method

1. Detached worktree at tip `e81eab4d9`. Re-ran `.venv` `tools/validate_literature_extracts.py` (+ `--check-fidelity-match`) on all three → `OK: 3 extract file(s) valid` twice.
2. `sha256sum` of inbox PDFs vs sidecars / extract provenance strings.
3. `pdftotext -layout` (full + p1–3) and existing `_n11_png` page rasters for Forsyth Tables 1 / 2 / 5; NIST §3.2/§3.3/§6.1; Tylka slides 6–7 / 10–14.
4. Walked every numeric under `fidelity_samples`, `species.*.observations|context[].values` (including nested `rows_as_printed`), and N11 write-up scored/context tables.
5. Confirmed context-only parking of Forsyth/Tylka burn / non-flammable pressures (no closed flammability type; d-032 / N7 pattern).

P0 rule (X1–X9 / Y calibration): **P0 only if a wrong number or wrong species lands**. Locator coarseness / DERIVED micro-slip / soft paraphrase ≤ P2.

## Identity / STEP 0 / PDF ↔ sidecar

| Corpus | PDF p1 identity | Extract | sha256 | Match |
| --- | --- | --- | --- | --- |
| nist-nbsir-77-859 | H.M. Roder, *The Thermodynamic Properties of Slush Hydrogen and Oxygen*, NBSIR 77-859, Nov 1977, NBS Cryogenics / NASA-JSC (45 pp) | `nist-nbsir-77-859.yaml` LANDED | `e209245db0109d8a42327a58baa33007ff718da4a0e70f4b092d9c9389bdd065` | OK (= sidecar + extract provenance) |
| ntrs-20100041337 | Forsyth, Maes, Stoltzfus, Bachelier, *Promoted Ignition and Burning Tests of Stainless Steel…*, ASTM STP 1454 (2003); NASA JSC WSTF (18 pp). Sidecar citation OCR garbage `Source of Acqui siti on` | `ntrs-20100041337.yaml` LANDED (context-only scored absences) | `c84dad73bbe7eaffc8296ac4db772113b7aab599c26d899ff03e5137df227793` | OK PDF identity; extract citation rebuilt from p1 (sidecar OCR noted) |
| ntrs-20240012816 | Jonathan Tylka, *Promoted Combustion Behavior of 316 Stainless Steel and 6061 Aluminum Alloys in Elevated Pressure Nitrox*, 10/23/24, ASTM G04 Prague (15 slides) | `ntrs-20240012816.yaml` LANDED (context-only) | `f6ac9c1e7b1159089323f413399d4d1373bd8736b891a61d431b9f8a163e2ecf` | OK (= sidecar + extract provenance) |

STEP 0: no prior Roder / slush-H2-O2, Forsyth / Stoltzfus / Program 96-1, or Tylka / Nitrox-promoted-combustion extracts — skip-not-required confirmed.

## Cell-by-cell (printed numbers)

### 1. nist-nbsir-77-859 — PASS (scored secondary fixed points)

**Scored:** 3× `transition_point` (author-selected literature / EOS anchors — not a new lab campaign). Context: program I/O + 5000 psia / 34 MN/m² limits; O₂ “best estimate.”

| field | landed | printed (PDF) | locator | match |
| --- | --- | --- | --- | --- |
| H₂ T_triple | 13.8 / full 13.800 K | 13.800 K | pdf 11 / pub 5 §3.2 | OK |
| H₂ P_triple | 0.0695 atm | 0.0695 atm | same | OK |
| H₂ melting shift / Goodwin | +0.003 K; Goodwin (1962); 32-term MBWR Roder & McCarty 1975 | same prose | same | OK |
| H₂ ΔHfus | 117.277 J/mol; 28.03 cal/mol; factor 4.184; ~1% unc; Woolley 1948 | same | pdf 12 / pub 6 §3.3 | OK |
| O₂ T_triple / P_triple | 54.359 K; 0.0014451 atm | same (§6.1) | pdf 19 / pub 13 | OK |
| O₂ ΔHfus | 106.3 cal/mol; 444.8 J/mol; Giauque & Johnston 1929 | same | same | OK |
| O₂ ΔVfus / γ-solid range | ~0.94 cm³/mol; 43.801–54.359 K (Kemp & Pickup 1972); Weber 1977b | same | same | OK |
| Program scope | T+quality → P,ρ,H,S,U,quality; upper ~5000 psia (34 MN/m²); O₂ “best estimate” | SUMMARY | pub 22 (see P2 #1) | OK numbers |
| DERIVED Pa | formula `P_atm × 101325` stamped | — | — | formula OK; **values slip → P2 #2** |

**Not extracted (intentional):** melting-line / solid-vapor fit coefficients; solid-density polynomials; Appendix A/B FORTRAN; full property grids — OK.

### 2. ntrs-20100041337 — PASS (context-only; image-checked tables)

**Landed:** `observations: []`; Phase I/II/supplementary + apparatus in **context**. Figs 4–9 figure-only except prose/table duplicates.

#### Apparatus

| field | landed | printed | match |
| --- | --- | --- | --- |
| chamber max | 4500 psia / 31 MPa | same | OK |
| body / liner | 304 SS lined with copper | same | OK |
| flow / promoter / purity | counterflow upward; Pyrofuze; ≥99.5%; MIL-PRF-27210G | same | OK |
| geometries | ½″ 316L rods; 1¼″ Sch. 80 / XXS 316L pipe | same | OK |
| positive ignition / self-sustained | rods ≥0.5 in (1.3 cm); self-sustained ≥1.0 in (2.54 cm) | same | OK |
| facility / program | NASA JSC WSTF; Program 96-1 Phase II | same | OK |

#### Table 1 Phase I thresholds + max burn (raster `forsyth-t1-03.png`)

| material | pressures psia (MPa) | max burn in (cm) | threshold | match |
| --- | --- | --- | --- | --- |
| 1/8″ 316H | 2000/1000/700 (13.2/6.9/4.8) | 6.2/5.2/1.7 (15.75/13.20/4.32) | <700 psi (<4.8 MPa) | OK |
| 1/8″ 316L | 500/400/300 (3.5/2.8/2.1) | 6.3/1.3/0.4 (16.00/3.30/1.02) | 400 psi (2.8 MPa) | OK |
| 1/4″ CF8M | 1000/800/700 (6.9/5.5/4.8) | 4.9/1.5/0.8 (12.45/3.81/2.03) | 800 psi (5.5 MPa) | OK |
| 1/2″ 316L | 1500/1200/1000 (10.4/8.3/6.9) | 4.5/2.1/0.2 (11.43/5.33/0.51) | **1200 psi (8.3 MPa)** | OK |

WSTF ids 97-31619 / 97-31575 / 97-31753 / 97-31574 — OK.

#### Table 2 Phase II ½″ rods (raster `forsyth-t2-08.png`)

| P / V | n_tests / +ign | max burn | match |
| --- | --- | --- | --- |
| 500 psia / 50 ft/s | 11 / 7 | **4.81 in (12.22 cm)** complete | OK |
| 500 / 100 | 9 / 2 | 0.40 (1.02) | OK |
| 500 / 200 | 5 / 0 | No burn | OK |
| 1000 / 50 | 9 / 2 | 0.36 (0.91) | OK |
| 1000 / 100 | 5 / 0 | No burn | OK |
| 1500 / 50 | 7 / 0 | No burn | OK |

Phase I contrast 0.2 in @ 1000 psia nonflowing — OK. Prose “12.2 cm” vs table 12.22 — extract follows table — OK.

#### Table 5 supplementary (raster `forsyth-t5-14.png`)

| P / V | n / +ign | max burn | match |
| --- | --- | --- | --- |
| 500/50 | 3/3 | 2.50 (6.35) | OK |
| 450/55 | 4/2 | 1.20 (3.05) | OK |
| 450/29 | 3/2 | 3.13 (7.95) | OK |
| 400/29 | **3**/3 | 1.34 (3.40) | OK (OCR `-'` overridden by image) |
| 350/29 | 3/1 | 1.27 (3.23) | OK |
| 300/29 | **3**/1 | 0.57 (1.45) | OK (OCR `-'` overridden by image) |

Self-sustained down to **350 psi (2.4 MPa)** @ 29 ft/s; nonflowing comparator 1200 psi (8.27 MPa); **nearly 70%** reduction — OK ((1200−350)/1200 ≈ 70.8%).

#### Sch. 80 / XXS summary

Sch. 80 max burn range 0.04–0.40 in; examples 0.19 (0.48) @ 500/50 and 0.40 (1.02) @ 500/100; XXS no sustained burn past promoter — match Table 3/4 prose. Full cell dumps correctly omitted.

### 3. ntrs-20240012816 — PASS (context-only presentation)

**Landed:** `observations: []`; 25%/50% O₂ results + method design in context. Slides 10 & 12 3POD scatters figure-only — OK.

| field | landed | printed | match |
| --- | --- | --- | --- |
| 6061 Al @ 25% O₂ / 69 MPa | 10 tests; no sustained combustion; Pyrofuse/Ti; ~3 mm G124 | slide 6 | OK |
| 316 SS @ 25% O₂ / 69 MPa | same pattern | slide 7 | OK |
| Al @ 50% O₂ | ~10% reaction ~15 MPa; NASA non-flammable **~11 MPa** | slides 10–11 | OK |
| SS @ 50% O₂ | ~10% reaction ~43 MPa; NASA non-flammable **~33 MPa** | slides 12–13 | OK |
| Conclusions | both not flammable @ 69 MPa / 25%; Al @ 11 MPa / 50%; SS @ 33 MPa / 50%; bal. N₂; Ti caution; GSE/spacecraft | slide 14 | OK |
| Method margins | >23% → test 25%; NBL >46% → test 50%; storage → 69 MPa; 3POD then 10-replicate; NASA-STD-6016 / 6001B / ASTM G124 | slides 2–3 / 9 | OK |

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P2 | nist-nbsir-77-859 | H₂/O₂ program-scope context locator `pdf_page_index: 27` / `published_page: 22` — SUMMARY body with 5000 psia / “best estimate” prints on **PDF page 28** (TOC pub 22). Numbers themselves correct. |
| 2 | P2 | nist-nbsir-77-859 | DERIVED `P_triple_Pa` companions slightly off formula `P_atm × 101325`: H₂ landed 7042.08625 vs 7042.0875; O₂ landed 146.4237615 vs 146.4247575 (~0.001 Pa). Printed atm values and DERIVED stamp/formula correct. |
| 3 | P2 | ntrs-20100041337 | Apparatus context locator `pdf_page_index: 5` while chamber 4500 psia / 31 MPa prose opens on **PDF page 4** (note already says “pp. 4–6”). Numbers correct. |
| 4 | P3 | nist-nbsir-77-859 | `scope_assessment.method_observations: 1` but two `laboratory_conditions` context rows (H₂ + O₂ program scope). Soft count hygiene only. |

## Attack checklist

| Attack | Result |
| --- | --- |
| Swap H₂ 13.800 K ↔ O₂ 54.359 K or 0.0695 ↔ 0.0014451 | Fail — species/locators match |
| Swap ΔHfus 117.277 ↔ 444.8 or cal/J bases | Fail — both units + sources (Woolley / Giauque) match |
| Invent author-new calorimetry as measured_direct | Fail — secondary_compilation / measured_direct_observations: 0 |
| Table 1 threshold swap 400↔800↔1200 / 316H↔316L↔CF8M | Fail — rows + prose thresholds match image |
| Table 2 4.81 complete-burn at wrong P/V | Fail — only 500 psia / 50 ft/s |
| Higher V or P invent more burning | Fail — No burn / fewer ignitions match table |
| Table 5 n_tests OCR `-'` → invent 0 or omit | Fail — image-corrected n_tests=3 @ 400 & 300 |
| 70% vs (1200−350)/1200 | Fail — “nearly 70 percent” prose landed |
| Tylka swap Al ~11 ↔ SS ~33 or 15 ↔ 43 MPa | Fail — alloy slides match |
| Score Forsyth/Tylka burn lengths as closed observations | Fail — correctly context-only + amendment note |
| Digitize 3POD scatter run pressures | Fail — figure-only omission disclosed |
| PDF identity / sha256 mismatch | Fail — titles/authors/hashes match |
| Validator regressions | Fail — 3/3 OK (+ fidelity-match) |

## Summary

| Paper | Disposition | P0 |
| --- | --- | --- |
| nist-nbsir-77-859 | LANDED — scored TP/ΔHfus + program context OK | 0 |
| ntrs-20100041337 | LANDED — context-only tables/apparatus OK | 0 |
| ntrs-20240012816 | LANDED — context-only Nitrox thresholds OK | 0 |

## Validation

```
.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/nist-nbsir-77-859.yaml \
  data/literature/extracts/ntrs-20100041337.yaml \
  data/literature/extracts/ntrs-20240012816.yaml
→ OK: 3 extract file(s) valid

… --check-fidelity-match …
→ OK: 3 extract file(s) valid
```

## Push

None. Tip remains `empirical/n11-nist-ntrs-2026-09-22` @ `e81eab4d9`.

## Report line

**VERDICT: READY · P0=0 P1=0 P2=3 P3=1 · tip: `e81eab4d9` (unchanged)**
