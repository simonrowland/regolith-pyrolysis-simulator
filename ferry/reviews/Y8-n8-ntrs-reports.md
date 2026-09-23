# Y8 — CROSS-AUDIT: N8 NTRS reports (BACKLOG 3)

**Lane audited:** N8 (`empirical/n8-ntrs-reports-2026-09-22`)
**Tip:** `4e6ccf2a97b1eadd9082e680a2d98144696ae1b0` (unchanged; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N8-ntrs-reports.md`
**Worktree:** `/workspace/repos/wt/slot-10` (at tip for audit)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{ntrs-20210022801,ntrs-20210012628,ntrs-20120000846}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)
**Auditor:** distinct subagent from N8 extractor

## Verdict

**P0: 0** — every landed printed number matches the PDF (value + unit sense + row mapping).
**READY.** No branch change; nothing to push.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 2 |
| P3 | 1 |

## Method

1. Detached/tip at `4e6ccf2a9`. `pdftotext -layout` on all three corpus PDFs; page-scoped extracts for Olson full-paper pp. 4 / 9–13; sha256 vs sidecars.
2. Walked every numeric under `species.*.observations|context[].values`, `fidelity_samples`, and N8 write-up scored tables (residual-He ppb/%; Mark-III design; Table 1 Apollo volatiles; abstract design claims; Jacobson ΔfH° ±unc).
3. Compared value, unit/basis, flow-rate / species mapping, and locator page to printed prose.
4. Validator: `tools/validate_literature_extracts.py` (+ `--check-fidelity-match`) → `OK: 3 extract file(s) valid` twice.
5. P0 rule: **P0 only if a wrong number lands**. Omission / locator coarseness ≤ P2.

## Identity / STEP 0

| Corpus | PDF p1 identity | Extract | Match |
| --- | --- | --- | --- |
| ntrs-20210022801 | Aaron D.S. Olson (NASA KSC), *Lunar Helium-3: Mining Concepts…*, AIAA ASCEND 2021, 15 pp | `ntrs-20210022801.yaml` LANDED | OK |
| ntrs-20210012628 | Same Olson title/author, ASCEND Abstract, 2 pp | `ntrs-20210012628.yaml` LANDED (context-only) | OK |
| ntrs-20120000846 | Jacobson (NASA GRC) & Myers (ECU), *Vaporization of B₂O₃(l) to B₂O₃(g) and B₂O₂(g)* poster, ECS 220th / Oct 2011; footer `PS–00625–1011` | `ntrs-20120000846.yaml` LANDED | OK |

**sha256 (PDF = sidecar = extract method claim):**
- `ntrs-20210022801.pdf` = `935bbe2269b04c30bcc35255c13ef14125f4d0e501842190e08b36a38fbb666a`
- `ntrs-20210012628.pdf` = `237048ffbdfeec79a4aa9f863021ae1c516cf7ff373e964fd3b035758c3b8f76`
- `ntrs-20120000846.pdf` = `467b6d0b29c5fae81b9927e1d839473cbae9fcf9a33ff3f0f3061c3ecaaf6a67`

STEP 0: no prior Olson / He-3 extracts; other Jacobson extracts (`costa-jacobson-2015`, `kems-032`, `kems-139`) are different papers — skip-not-required confirmed.

## Cell-by-cell (printed numbers)

### 1. ntrs-20210022801 (Olson ASCEND full paper) — PASS

**Scored:** `concentration_series` residual ⁴He after HEAT HPHX agitation (p13).

| field | landed | printed (PDF p13) | match |
| --- | --- | --- | --- |
| flow 1.5 / 9.0 g/s | 1.5 / 9.0 | same | OK |
| implanted ⁴He ppb (after implant) | 2.7 / 2.1 | 2.7 ppb / 2.1 ppb | OK |
| residual ⁴He ppb after HEAT (control-corrected) | 0.7 / 0.1 | 0.7 / 0.1 | OK |
| % remaining | 29 / 4 | 29% / 4% | OK |
| % removed | 70 / 96 | 70% / 96% | OK |
| not heated above RT; ~500 °C Apollo comparator | true / prose | same | OK |

**Mark-III design (p4 context):** excavate 1258 t/h; heat 556 t/h; move 23 m/h; ~350 kW; 33 kg ³He/yr @ 10 ppb; ~66 kg/yr @ 20 ppb; HPHX 700 °C from 30 °C inlet; 85% recuperation; size cut <100 µm — all match. DERIVED `T_K = 700+273.15 → 973.15` OK.

**Table 1 Apollo 10086.16 @ 700 °C (p4, g/tonne):** H₂ 43; ⁴He 22; ³He 0.007; H₂O 23; N₂ 4.0; CH₄ 11; CO 13.5; CO₂ 12; footnotes 450 kg heated / 20 ppb + 1258 t/h — match; correctly tagged design-basis compilation (not SWIM/HEAT).

**Apparatus (pp. 9–11):** SWIM 2 kg / <100 µm / 300–900 km/s (~450 avg) / example 0.6 mTorr ⁴He & −8 kV; HEAT 5-stage counter-flow HPHX / 14 type-K TCs + IR array; SCAN 100 cm³ crucible / 20 g / 300 W 8 mm cartridge / isochronal to 600 °C / RGA; 2018 FTI–KSC Swamp Works — match.

**Fidelity samples** (2.7 ppb; 96%; 2 kg) echo scored/context — OK.

**Not digitized (correct):** Figs 16–18 curves; absolute implanted inventories; control blank ppb (not printed).

### 2. ntrs-20210012628 (Olson ASCEND abstract) — PASS

**Landed:** context only; `observations: []`; `measured_direct_observations: 0`.

| field | landed | printed (p1) | match |
| --- | --- | --- | --- |
| excavate / process t/h | 1258 / 556 | same | OK |
| size cut / solar concentrator | <100 µm / 12 MW | same | OK |
| HPHX peak / recuperation | 700 °C / 85% | same | OK |
| ³He feed / captured | 20 ppb / 66 kg/yr | same | OK |
| near-term water demo | 15 t from 5%; ~400 t excavated; ~6 g ³He | same | OK |
| experiment | FTI–KSC; JSC-1A; ⁴He; 2 kg batches; HPHX flow; vacuum furnace+MS; agitation↑ with flow | same | OK |

Residual-He ppb / % correctly absent here (live only in companion full paper). Fig. 1 figure-only — OK.

### 3. ntrs-20120000846 (Jacobson/Myers B₂Oₓ poster) — PASS

**Scored:** three `gibbs_table` ΔfH°(298.15 K) rows — title-strip vs Conclusions retained separately (not averaged).

| species / panel | landed ΔfH° / unc | printed | match |
| --- | --- | --- | --- |
| B₂O₂(g) Conclusions (p2) | −479.9 ± 25.7 kJ/mol | `= –479.9 ± 25.7 kJ/mol` | OK |
| B₂O₂(g) title strip (p1) | −479.9 ± 41.5 | `-479.9 ± 41.5 kJ/mol` | OK |
| B₂O₃(g) title + Conclusions | −833.4 ± 13.1 | both panels same | OK |

**Method context:** FeB/Fe₂B activity fix; 1:1:1 FeB:Fe₂B:B₂O₃; routes `B₂O₃(l)=B₂O₃(g)` and `2/3 B + 2/3 B₂O₃(l)=B₂O₂(g)`; working rxn `4/3 FeB(s)+2/3 B₂O₃(l)=B₂O₂(g)+2/3 Fe₂B(s)`; KEMS; W1BD; journal “in press” not this PDF; poster id `PS–00625–1011` — match.

**Not extracted (intentional, disclosed):** per-run 2nd/3rd-law table cells (poster columns interleaved / OCR-hostile). Preferred Conclusions values landed — correct scope.

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P2 | ntrs-20210022801 | Apparatus observation locator is `pdf_page_index: 9` with note “pp. 9–12”; HEAT 5-stage / TC counts print on **p10** and SCAN crucible/20 g/600 °C on **p11**. Numbers themselves correct. |
| 2 | P2 | ntrs-20210022801 | Agitation `T_range_K: [298.15, 298.15]` — PDF prints only “not heated above room temperature” (no numeric T). Conventional RT→298.15 without a DERIVED stamp. |
| 3 | P3 | ntrs-20120000846 | Sidecar citation stamp `PS-00625-0911` vs poster footer / extract `PS–00625–1011` (month 09 vs 10). Extract follows the PDF footer; identity still matches. |

## Attack checklist

| Attack | Result |
| --- | --- |
| Swap 1.5↔9.0 g/s residual/implanted ppb | Fail — 2.7/0.7/29/70 vs 2.1/0.1/4/96 map correctly |
| Remaining % vs removed % swap | Fail — printed remaining 29/4 and removed 70/96 both landed |
| Table 1 ⁴He 22 vs ³He 0.007 swap | Fail — isotope labels match PDF |
| Abstract invent residual-He ppb | Fail — correctly absent; points to companion |
| Average B₂O₂ unc ±41.5 with ±25.7 | Fail — two separate observations retained |
| Wrong ΔfH central (−479.9 / −833.4) | Fail — both panels match |
| Re-extract prior Jacobson paper | Fail — STEP 0 different papers |
| PDF identity / sha256 mismatch | Fail — titles/authors/hashes match |
| Validator regressions | Fail — 3/3 OK (+ fidelity-match) |

## Summary

| Paper | Disposition | P0 |
| --- | --- | --- |
| ntrs-20210022801 | LANDED — scored residual-He + context OK | 0 |
| ntrs-20210012628 | LANDED — context-only OK | 0 |
| ntrs-20120000846 | LANDED — ΔfH° title+Conclusions OK | 0 |

## Validation

```
.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/ntrs-20210022801.yaml \
  data/literature/extracts/ntrs-20210012628.yaml \
  data/literature/extracts/ntrs-20120000846.yaml
→ OK: 3 extract file(s) valid

… --check-fidelity-match …
→ OK: 3 extract file(s) valid
```

## Push

None. Tip remains `origin/empirical/n8-ntrs-reports-2026-09-22` @ `4e6ccf2a9`.

## Report line

**VERDICT: READY · P0=0 P1=0 P2=2 P3=1 · tip: `4e6ccf2a9` (unchanged)**
