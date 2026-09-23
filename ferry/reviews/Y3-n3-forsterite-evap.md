# Y3 — CROSS-AUDIT N3 forsterite evaporation — 2026-09-22

**Lane audited:** N3 (`empirical/n3-forsterite-evaporation-2026-09-22`)
**Author write-up:** `/workspace/ferry-inbox/reviews/N3-forsterite-evap.md`
**Tip (detached):** `0e3aef4b20420685ed751c6849a4cbec1ebb8c2c` — unchanged; no fix commit
**Worktree:** `/workspace/repos/wt/slot-y3-n3` (detached at tip for audit)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{kuroda-hashimoto-2002-forsterite-hydrogen,tsuchiyama-1998-forsterite-mg2sio4-h2,ta-shirai-2000-lpsc}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)

## VERDICT

**READY.** Tip unchanged.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 1 |
| P3 | 2 |

## Method

Detached at N3 tip. Re-ran `uv run` validator on all three extract files → `OK: 3 extract file(s) valid`. For every landed numeric cell: open the corpus PDF (sha256 matched each sidecar), compare value / unit / row mapping / locator to the extract. Kuroda + Shirai via `pdftotext -layout`; Tsuchiyama is encrypted (empty text layer) so Table 1 / apparatus / abstract were read from 200–300 dpi `pdftoppm` page rasters. P0 = a wrong number lands.

**PDF ↔ sidecar sha256:** all three YES  
(`f7af92cf…eee6`, `706c40ef…af7f`, `c156af42…84e8`).

## Per-source

### kuroda-hashimoto-2002-forsterite-hydrogen — PASS

- **Identity:** AMR 15, 152–164; DOI `10.15094/00006017`; title/authors match sidecar + extract.
- **Table 1 (published p. 157 / PDF p. 6):** All 36 `rate_series` rows checked against the printed table (ClearScan text layer + page raster). T, PH₂, PH, Δt, A₁, A₂, W₁, W₂, S₁, S₂, Jexp match cell-for-cell after OCR `l`→`1` normalisation in the text layer (`3.0lE-06` → extract `3.01e-06`, etc.). No wrong Jexp / weight / area landed.
- **Derived (Abstract + eq. 3 / §3.2):** Real Ea **32.6±4.8** kcal/mol; apparent Ea **86.5±4.8** kcal/mol; rate law `log10(JF)= -32.6(±4.8)×10³/(2.303 RT) + log10(PH/atm) + 4.00(±0.07)`; PH slope **1.05±0.09**, √PH₂ slope **0.52±0.05** at 1400 °C — all match printed; `derived: true` stamp correct.
- **Apparatus:** Deltech DT-31-VT-68-C; Mo cell; W catalyst; evacuation tube 0.8×5.4 cm; downstream alumina 55 mm / ~300 mm; 3000 L/s pump; porosity 7.7±2.6%; H₂ flows 2 / 6.7 / 20 / 60 cc min⁻¹; T 1200–1500 °C — match §2.
- **Not landed (correct):** Fig. 3–8 intercepts/curves not digitised.
- **A₂=0.107 row (1400 °C, PH₂=6.10×10⁻⁵ atm, 200 min):** PDF text layer and page raster both show **0.107** next to A₁=1.077. Extract retains printed value and notes likely typesetting error — correct policy; not a wrong landing (see P3).

### tsuchiyama-1998-forsterite-mg2sio4-h2 — PASS

- **Identity:** Min. J. 20, 113–126; DOI `10.2465/minerj.20.113`; encrypted publisher PDF; p1 title/authors match via render.
- **Table 1 (published p. 116):** All 18 runs (FoH-1…FoH-17 / A-series) — T, time, Wi, Wf, ΔM, a₀/b₀/c₀, ΔX, ΔM/S — match the page raster cell-for-cell (incl. FoH-15 0.21697/0.07545/0.1415/0.03002/0.09689; ρ_Fo=3.227 footnote).
- **Abstract Arrhenius / α:** `jFo = 2480 exp(-372 kJ mol⁻¹ / RT)` for {010}; α 0.04–0.12 this work / 0.03–0.2 with priors / recommended ~0.1 — match p. 113; stamped derived.
- **Apparatus (p. 115 Fig. 1):** Mo crucible 16×124 mm; Ta heater + shield; W near-heater wall; Fo hung on Mo wire; W5Re–W26Re; Fe/Ni/Au 1535/1453/1064 °C; better than ±10 °C; p < ~10⁻⁸ bar before H₂; pH₂=1.4×10⁻⁵ bar, drift 6×10⁻⁷; weigh ±0.01 mg; size ±0.05 mm — match.
- **Not landed (correct):** Figs. 2–3 slopes not digitised (rates come from author Arrhenius / Table 1).

### ta-shirai-2000-lpsc — PASS (landed cells) / P2 lane scope

- **Identity:** LPSC XXXI #1610 — Na₂O–SiO₂ melt at 1 atm (not forsterite). Extract + N3 write-up both flag this; numbers still audited.
- **α\* (prose, Estimation of α\*):** 1300 °C → 0.13 / 0.095 / 0.08 at pO₂=10⁻⁸/10⁻⁹/10⁻¹⁰; 1400 °C → 0.048 / 0.043 / 0.05 — match body text; `derived: true`.
- **Fig. 2 slopes / fits:** −0.153 and −0.26; `jNa = 6.70×10¹³ x^−0.153` (R=0.99653), `5.22×10¹³ x^−0.26` (R=0.99426); theoretical −0.25; Na₂O=20 wt% reference — match.
- **Experiments:** Na₂O start 22–23 wt%; Pt loop 2 mm; alumina muffle 4 cm ID; T 1300/1400 °C; pO₂ 10⁻⁸–10⁻¹⁰; flows 500/678/1000/1500 ml min⁻¹, selected 1000; EPMA 50 µm / 15 kV / 12 nA; quench in diffusion-pump oil; reaction NaO₀.₅(l)=Na(g)+¼O₂ — match.
- **Not landed (correct):** concentration–time curves not digitised; figure-box α\* annotations that differ slightly from prose (see P3).

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P2 | ta-shirai-2000-lpsc | Corpus lane is “forsterite evaporation” but the PDF is Na₂O–SiO₂ melt Na loss at 1 atm. Extract correctly labels this; no forsterite numbers invented. Scope/assignment gap only. |
| 2 | P3 | kuroda-2002 Table 1 | Row 1400 °C / PH₂=6.10×10⁻⁵ / 200 min prints A₂=**0.107** beside A₁=1.077 (physically implausible). Extract keeps printed cell + notes likely typesetting; does not “correct” to ~1.07. |
| 3 | P3 | ta-shirai Fig. 1 | Figure text boxes show α\*≈0.125 / 0.0475 / 0.0425 while prose prints 0.13 / 0.048 / 0.043. Extract follows prose (correct for derived author values). |

## Validation

```
.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/kuroda-hashimoto-2002-forsterite-hydrogen.yaml \
  data/literature/extracts/tsuchiyama-1998-forsterite-mg2sio4-h2.yaml \
  data/literature/extracts/ta-shirai-2000-lpsc.yaml
→ OK: 3 extract file(s) valid
```

## Push

None. Tip remains `origin/empirical/n3-forsterite-evaporation-2026-09-22` @ `0e3aef4b2`.

## Report line

**VERDICT: READY · P0=0 P1=0 P2=1 P3=2 · tip: `0e3aef4b2` (detached, unchanged)**
