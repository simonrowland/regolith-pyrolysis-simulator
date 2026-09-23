# Y4 — CROSS-AUDIT: N4 CAI-liquid evaporation (BACKLOG 3)

**Lane:** N4 (`empirical/n4-cai-liquid-evaporation-2026-09-22`)
**Tip:** `700e1aeef61eaf4962e0238da00a665eb14a0313` (unchanged; no fix commit)
**Prior:** `/workspace/ferry-inbox/reviews/N4-cai-liquid-evap.md`
**Auditor worktree:** `/workspace/repos/wt/slot-y4-n4` (detached @ tip; different seat from extractor)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{ta-mendybaev-2002-lpsc,ta-mendybaev-2020-lpsc,richter-2008-cai-like-liquids-lpsc-abstract}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)

## Verdict

**VERDICT: READY**

| Severity | Count |
| --- | ---: |
| **P0** | **0** |
| **P1** | **2** |
| **P2** | **0** |
| **P3** | **1** |

No wrong number lands in any of the three extracts. Tip unchanged; nothing pushed.

## Method

Blind adversarial check vs PDF (not vs N4 write-up first):

1. Detached worktree at `700e1aeef`; `pdftotext -layout` + 200 dpi page rasters (`pdftoppm`) for all three 2-page LPSC abstracts.
2. Enumerated every landed numeric in `printed_composition` / `conditions` / `fidelity_samples` / `species.*.observations|context[].values` (rates, alphas/gammas, T, P, oxide wt%, loop diameters, apparatus intervals).
3. Compared value, unit/basis, experiment mapping, and locator page against printed prose (and figure *text* annotations where visible without curve digitisation).
4. `validate_literature_extracts.py` on the three files → `OK: 3 extract file(s) valid`.
5. PDF sha256 matched each corpus sidecar.

P0 rule (same as X1–X9 / Y calibration): **P0 only if a wrong number lands**. Omission / incompleteness ≤ P1.

## Per-source audit

### 1. ta-mendybaev-2002-lpsc — PASS (landed cells) / P1 incompleteness

**PDF:** LPSC XXXIII abstract 2040 — Mendybaev, Davis & Richter, “The Effect of Sample Size on Experimental Evaporation of Type B CAIs”.

#### Landed cells vs printed

| field | landed | printed | match |
| --- | --- | --- | --- |
| MgO / SiO2 / Al2O3 / CaO wt% | 12.0 / 46.1 / 19.4 / 25.5 | Methods: B133-glass same | yes |
| Ir loop diameters mm | 1.0, 2.5, 3.3, 6.0 | same | yes |
| initial surface area mm² | ~3.5–85 | “about 3.5 to 85 mm²” | yes |
| T setpoints °C | 1800, 1700, 1600 | same | yes |
| P | P≤10⁻⁶ torr | same | yes |
| rate 2.5–6.0 mm @ 1800 °C | (6.8±0.8)×10⁻⁶ g/mm²/min | Results p1 | yes |
| rate 1.0 mm @ 1800 °C | (1.1±0.2)×10⁻⁵ g/mm²/min | Results p1 | yes |
| Mg α (this campaign) | 0.987 | Results p2 Rayleigh fit | yes |
| theoretical comparator | √(24/25)=0.980 | Intro + Results | yes |
| T_K DERIVED | 2073.15 (=1800+273.15) | stamp OK | yes |

- Introduction’s prior-work α=0.989 (vacuum) / 0.987 (H₂) correctly **not** re-scored; Results α=0.987 is the scored arm — OK.
- Instruments JEOL JSM-5800LV / Oxford ISIS-300 / AEI IM-20 — OK.
- Figures 1–3 not digitised — intentional; no invented curve points — OK.

#### P1-1 — omitted printed multi-T rates / Ea

Page 2 prose prints weight-loss rates for **1.0 mm** samples at 1800 / 1700 / 1600 °C as **~1.1×10⁻⁵**, **~2×10⁻⁶**, **~3×10⁻⁷** g/mm²/min and **Ea = 580 kJ/mole** (plus forsterite comparators 628±16 / 584±28). None of these land in the extract. N4 write-up incorrectly claimed “1700/1600 °C numeric rates (not printed)”. Not a wrong-number landing (P0=0); completeness gap = **P1**.

### 2. ta-mendybaev-2020-lpsc — PASS

**PDF:** 51st LPSC abstract 2168 — Mendybaev, Shornikov, Jacobson & Kowalski.

| field | landed | printed | match |
| --- | --- | --- | --- |
| Langmuir SiO₂ rate | 0.3 mg/mm²-hr | p2 loop TGA | yes |
| Knudsen SiO₂ rate | 6.8 mg/mm²-hr | p2 Ir-cell | yes |
| γ_Si | ~0.04 (0.04) | p2 Ji,free/Ji,eq | yes |
| T / P | 1800 °C; 5×10⁻⁶ torr or less | p2 | yes |
| loop / orifice | 2.5 mm Ir; 1 mm orifice | p2 | yes |
| SiO₂ + 1 wt% Ir; 99.995% | as printed | p2 | yes |
| microbalance | every 6 sec | p2 | yes |
| TC | W5Re/W26Re | p2 | yes |
| profile-fit γ (context only) | 0.18/0.16 @1900; ~0.13/@1800; 0.06/0.07 @1600; γMg/γSi=0.74; forsteritic ~2.1; IAS =1 | Results p1 | yes |

- 0.3/6.8 ≈ 0.044 → authors’ ~0.04 — magnitude consistent; approximate qualifier retained — OK.
- Profile-fit γ kept as context (`not_a_direct_gamma_measurement`) — OK (not falsely scored as measured α).
- Fig. 3 slope annotation (~−0.2766) not digitised; prose 0.3 used — correct.

### 3. richter-2008-cai-like-liquids-lpsc-abstract — PASS (landed cells) / P1 figure-label miss

**PDF:** LPSC XXXIX abstract 1385 — Richter, Teng, Mendybaev, Davis & Georg (title “…Low Pressure H₂”). Sidecar cites Wadhwa + “hydrogen gas and in vacuum”; extract correctly follows PDF — OK. sha256 match.

| field | landed | printed | match |
| --- | --- | --- | --- |
| vacuum-like H₂ bound | ~10⁻⁷ bar | Elemental Fractionations | yes |
| Fig.1 comparison P | 2×10⁻⁴ bar H₂ | Fig.1 caption | yes |
| relative rate claim | “about two orders of magnitude larger” | Fig.1 caption | yes |
| isotope-arm P | 1.87×10⁻⁴ bar H₂ | body + Fig.2/3 | yes |
| T | 1500 °C → 1773.15 K DERIVED | Fig.3 caption | yes |
| ICPMS | GV Instruments Isoprobe (Chicago) | Isotopic Fractionations | yes |
| starting oxides | unknown / not printed | absent | yes (correct refusal) |
| absolute rates | not scored | none in prose | yes |

- `method_class: figure_only` on the relative-rate row; no invented absolute rates — OK.
- Prose states H₂ α “approximately the same” / “slightly above” vacuum with **no body-text numeric α** — extract correctly does not invent one from prose.

#### P1-2 — Fig. 2 printed annotation α not landed

Fig. 2 carries readable text boxes **α = 0.98797±0.00022** (fit to this-work + [2] residues) and **α = 0.97980** (ideal comparator). These are printed figure annotations (no curve digitisation required). Extract refused under “no prose numeric α”. Schema allows printed figure values with `method_class: figure_only`. Missed printed number = **P1** (not P0: nothing wrong was landed).

## P3 (hygiene only)

- Fig.1 caption PDF text layer has “larger **that** the vacuum”; extract soft-normalises to “than” in `relative_rate_definition_as_printed`. Magnitude claim unchanged.
- 2020 paper alternates γ_Si / γ_SiO; extract uses γ_Si consistently with Results prose — acceptable.

## Validation

```
OK: 3 extract file(s) valid
  ta-mendybaev-2002-lpsc.yaml
  ta-mendybaev-2020-lpsc.yaml
  richter-2008-cai-like-liquids-lpsc-abstract.yaml
```

PDF ↔ sidecar sha256: all three YES.

## Push / product fixes

**None.** No clear P0 with proof. Prefer report over landing incompleteness patches on the N4 branch.

Optional follow-ups (owner call): land 2002 multi-T rates + Ea; land Richter Fig.2 annotation α as `figure_only`; correct corpus sidecar citation for richter-2008.

## Report line

**VERDICT: READY · P0: 0 · P1: 2 · P2: 0 · P3: 1 · tip: `700e1aeef` · no push**
