# Y17 — CROSS-AUDIT: N17 AQ-OA batch C (regolith-empirical)

**Lane audited:** N17 (`empirical/n17-aq-oa-batch-c-2026-09-23`)
**Tip:** `4813ee1a29b640886ccf09fdd94c226b90ac8152` (unchanged; no fix commit)
**Prior (author):** `/workspace/ferry-inbox/reviews/N17-aq-oa-batch-c.md`
**Auditor worktree:** `/workspace/repos/wt/slot-y17` @ `empirical/y17-audit-n17-2026-09-23` from green `2e9e17c3d` (clean; N17 files compared via checkout-then-reset, not rewritten)
**N17 worktree:** `/workspace/repos/wt/slot-n17` @ tip
**PDFs:** `/workspace/ferry-inbox/acquired/{ta-mendybaev-2002-lpsc,ta-mendybaev-2020-lpsc,ta-shirai-2000-lpsc}.pdf` (yamanaka SKIP on green)
**PDF text:** `/workspace/ferry-inbox/reviews/_y17_pdf_text/`
**Date:** 2026-09-23 ~00:42 EDT (America/Toronto)
**Auditor:** distinct subagent from N17 extractor (blind adversarial)

## Verdict

**P0: 0** — every attacked printed number in the three landed extracts matches the PDF prose; profile-fit γ (Mendybaev 2020) and figure curves stay typed-absent / context-only; yamanaka correctly SKIPPED.
**READY.** Tip unchanged; already on `origin/empirical/n17-aq-oa-batch-c-2026-09-23` (= `4813ee1a2`). Nothing to push. No `empirical/n17b-y17-…` fix branch.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 1 |
| P3 | 3 |

## Method

1. Tip `4813ee1a2` confirmed via `ls-remote` (= N17 write-up SHA).
2. Targeted validate (+ `--check-fidelity-match`) on three new extracts + yamanaka on green → `OK: 4 extract file(s) valid` twice.
3. `sha256sum` ferry acquired PDFs: `7f9ef6f0…328fc` / `9aedf343…4c23c` / `c156af42…f84e8` / yamanaka `b0ea5696…4fda8a` — match N17 write-up claims.
4. `pdftotext -layout` full texts under `_y17_pdf_text/`. Walked every numeric under `fidelity_samples` and `species.*.observations|context[].values` (incl. nested `series` / rate triads / profile-fit γ blocks).
5. STEP 0: three new stems absent from green `2e9e17c3d`; `ta-yamanaka-1997-metsoc.yaml` present on green → SKIP confirmed.

P0 rule: **P0 only if a wrong number or wrong species lands**. Locator coarseness / citation hygiene / figure-vs-prose rounding ≤ P2–P3.

## Identity / STEP 0 / PDF ↔ provenance

| Corpus | PDF identity | Extract | sha256 (ferry) | Match |
| --- | --- | --- | --- | --- |
| `ta-yamanaka-1997-metsoc` | Yamanaka & Tsuchiyama, MetSoc 1997 #5122 | **SKIP** (on green) | `b0ea5696…4fda8a` | OK — no new extract |
| `ta-mendybaev-2002-lpsc` | Mendybaev, Davis & Richter, LPSC XXXIII **2040** (2 p.) | LANDED | `7f9ef6f0…328fc` | OK (= N17/B3 claim) |
| `ta-mendybaev-2020-lpsc` | Mendybaev, Shornikov, Jacobson & Kowalski, 51st LPSC **2168** (2 p.) | LANDED | `9aedf343…4c23c` | OK |
| `ta-shirai-2000-lpsc` | Shirai, Tachibana & Tsuchiyama, LPSC XXXI **1610** — **Na₂O–SiO₂ melt**, not forsterite | LANDED | `c156af42…f84e8` | OK |

**STEP 0 / no-duplicate on green:** confirmed. Parallel N3/N4/N4b unmerged-lane note in N17 write-up accepted (those tips not ancestors of green).

## Per-source audit

### 1. `ta-yamanaka-1997-metsoc` — SKIP PASS

Extract already on `work-v064-green` @ `2e9e17c3d`. Validator re-check OK. No duplicate landed.

### 2. `ta-mendybaev-2002-lpsc` — PASS (5 measured + 1 model_derived + method context)

**PDF:** 2-page LPSC abstract 2040. **Scored:** large-loop + 1.0 mm rates @ 1800 °C; 1.0 mm @ 1700/1600 °C; Ea; Mg α; starting composition context.

| field | landed | printed | match |
| --- | --- | --- | --- |
| starting oxides wt% | MgO 12.0 / SiO₂ 46.1 / Al₂O₃ 19.4 / CaO 25.5 | same (p1 Experimental methods) | OK |
| loop diameters mm | 1.0, 2.5, 3.3, 6.0 | same | OK |
| initial area | about 3.5 to 85 mm² | same | OK |
| T setpoints °C | 1800 / 1700 / 1600 | same | OK |
| P | P≤10⁻⁶ torr | same | OK |
| SEM / ion probe | JEOL JSM-5800LV + Oxford/Link ISIS-300; AEI IM-20 | same | OK |
| rate 2.5–6.0 mm @ 1800 | (6.8±0.8)×10⁻⁶ g/mm²/min | same | OK |
| rate 1.0 mm @ 1800 | (1.1±0.2)×10⁻⁵; ~50% higher | same | OK |
| multi-T triad 1.0 mm | ~1.1×10⁻⁵ / ~2×10⁻⁶ / ~3×10⁻⁷ | same (p2) | OK |
| Ea | 580 kJ/mole (model_derived) | “Ea =580 kJ/mole” | OK |
| forsterite comparators | 628±16 [4] Wang; 584±28 [3] Hashimoto | same sentence; `quoted_attributed` | OK |
| Mg α | 0.987; size-independent | “α=0.987 with no apparent distinction as to sample size” | OK |
| theoretical α | sqrt(24/25)=0.980 | same | OK |
| intro prior α 0.989 / H₂ 0.987 | **not re-scored** (quoted_attributed note) | intro only | OK |
| Figs 1–3 curves | TYPED ABSENCE | figure-only | OK |
| DERIVED K | 2073.15 / 1973.15 / 1873.15 | T_C+273.15 | OK |

### 3. `ta-mendybaev-2020-lpsc` — PASS (3 measured TGA + profile-fit context-only)

**PDF:** 2-page LPSC abstract 2168. **Scored:** Langmuir 0.3 + Knudsen 6.8 mg/mm²-hr SiO₂ @ 1800 °C; γ_Si ~0.04. Profile-fit γ block is **context only**.

| field | landed | printed | match |
| --- | --- | --- | --- |
| bench | NASA Glenn vacuum TGA; W5Re/W26Re; ≤5×10⁻⁶ torr; every 6 s | same (p2) | OK |
| Langmuir start | SiO₂ 99.995% + 1 wt% Ir; 2.5 mm Ir loop | same | OK |
| Knudsen | Ir cell 1 mm orifice; pure SiO₂ or SiO₂+1 wt% Ir | same | OK |
| J_free | 0.3 mg/mm²-hr | “0.3 mg/mm²-hr” | OK |
| J_eq | 6.8 mg/mm²-hr | “6.8 mg/mm²-hr” | OK |
| γ_Si | ~0.04 (= J_free/J_eq) | “γSi ~0.04” | OK |
| CMAS fit γ @ 1900/1800/1600 | context: 0.18/0.16; ~0.13/~0.13; 0.06/0.07; ratio 0.74 | same (p1 Results) | OK — **not in `observations`** |
| forsteritic γ_Si/γ_Mg ~2.1 @ 1900 | context | same | OK |
| IAS γ_Si/γ_Mg=1; within 5 wt% SiO₂ | context | same | OK |
| Figs 1–3 digitisation | TYPED ABSENCE | curves only | OK |
| a_i / γ_Mg TGA | TYPED ABSENCE (future work) | authors defer | OK |

### 4. `ta-shirai-2000-lpsc` — PASS (6 α* + 2 slopes; Na melt)

**PDF:** 2-page LPSC abstract 1610. Lane note correct: Na₂O–SiO₂ melt at 1 atm, **not** forsterite.

| field | landed | printed | match |
| --- | --- | --- | --- |
| start Na₂O | 22–23 wt% | “Na₂O=22-23wt%” | OK |
| apparatus | Pt wire loop 2 mm; alumina muffle 4 cm ID; H₂–CO₂; quench diffusion-pump oil | same | OK |
| T / pO₂ | 1300/1400 °C; 10⁻⁸/10⁻⁹/10⁻¹⁰ bar | same | OK |
| flow | 500/678/1000/1500; selected 1000 @ 1400 °C, pO₂=10⁻⁹, 30 min | same | OK |
| EPMA | 50 µm, 15 kV, 12 nA; ZAF; albite+quartz | same | OK |
| reaction | NaO₀.₅(l)=Na(g)+¼O₂(g) | same | OK |
| α* @ 1300 | 0.13 / 0.095 / 0.08 | prose p2 | OK |
| α* @ 1400 | 0.048 / 0.043 / 0.05 | prose p2 | OK |
| summary | ~0.1 @ 1300; ~0.05 @ 1400; >0.01 assumption | same | OK |
| slopes @ Na₂O=20 wt% | −0.153 / −0.26; theory −0.25 | same | OK |
| Fig. 2 fits | jNa=6.70e13·x^−0.153 R=0.99653; 5.22e13·x^−0.26 R=0.99426 | same | OK |
| Figs 1–2 curves | not digitized | OK | OK |

## Soft findings (not blocking READY)

| # | sev | item |
| --- | --- | --- |
| 1 | **P2** | `ta-shirai-2000-lpsc` `activity_model_source: Rego et al. 1985` — intro cites Rego [7] for Na₂O activity known; body says a(NaO₀.₅) “obtained experimentally by [6]” (= Tsuchiyama et al. 1981 GCA). Conflated citation; **no wrong numeric**. |
| 2 | P3 | Shirai Fig. 1 curve labels show finer α* (0.125 / 0.0475 / 0.0425) vs prose (0.13 / 0.048 / 0.043). Extract correctly prefers **prose** printed values. |
| 3 | P3 | Shirai extract is thinner schema (no `scope_assessment` / `benches`; `page:` vs `pdf_page_index:`; `provenance_path` points at ferry path). Validator OK. |
| 4 | P3 | Extraction `method` strings do not stamp PDF sha256 (N17 write-up + this audit verified ferry hashes separately). |

## P0 findings

**None.**

## Commit / push

- Tip `4813ee1a29b640886ccf09fdd94c226b90ac8152` unchanged (no fix commit; no `n17b-y17` branch).
- `git ls-remote origin refs/heads/empirical/n17-aq-oa-batch-c-2026-09-23` = same SHA — **already pushed; no FF push needed.**
- Auditor branch `empirical/y17-audit-n17-2026-09-23` @ green `2e9e17c3d` (clean stamp; not required to push).

**VERDICT: READY · P0=0 P1=0 P2=1 P3=3 · tip: `4813ee1a2` (unchanged)**
