# Y2 — CROSS-AUDIT N2 hastie-1979 + ta-dacko-conradt — 2026-09-22

**Lane audited:** N2 (`empirical/n2-hastie-dacko-2026-09-22`)
**Author write-up:** `/workspace/ferry-inbox/reviews/N2-hastie-dacko.md`
**Tip (detached):** `2f92f46b95b914b72135d054f04f79cc4dceaccc` — unchanged; no fix commit
**Worktree:** `/workspace/repos/wt/slot-03` (detached at tip for audit)
**PDFs:** `/workspace/ferry-inbox/from-main-B3-20260923T024400Z/pdfs/{hastie-1979-characterization-of-high-temperature,ta-dacko-conradt-low-p-transpiration}.pdf`
**Date:** 2026-09-22 (America/Toronto, EDT)

## VERDICT

**READY.** Tip unchanged.

| severity | count |
| --- | ---: |
| **P0** (wrong number in extract) | **0** |
| P1 | 0 |
| P2 | 1 |
| P3 | 1 |

## Method

Detached at N2 tip. Re-ran `uv`/venv `tools/validate_literature_extracts.py` on both extract files → `OK: 2 extract file(s) valid`. For every landed numeric cell: open the PDF at the cited locator (`pdftotext -layout` + 150–200 dpi `pdftoppm` page raster), compare value / unit / row mapping / locator to the extract. P0 = a wrong number lands.

## Per-source

### hastie-1979-characterization-of-high-temperature (Bonnell & Hastie TMS chapter) — PASS

- **PDF↔locator:** Volume PDF p. 381 = printed p. 357 chapter title ("Transpiration Mass Spectrometry…"). Tables land at PDF 400/405/409/418 = printed 376/381/385/394 — matches extract locators.
- **Table 1 (p. 376):** All six gas rows (fraction, σ±unc at 30 eV, isotope masses/abundances) match printed cell-for-cell, including He mass 4 / abundance 1.00 and Xe σ 6.2 ± 2.
- **Table 2 (p. 381):** Five ion-intensity rows (Na+/NaCl+/Na2Cl+/R/cell/T/eV) and footnote numbers (920 µV / 10^7 Ω; I29 = 1.8×10^4 µV; k_N2 = 1.66×10^-10 and 3.7×10^-11 µV/atm·K; carrier 0.54 / 0.65 atm; Na+ 1.9×10^4 µV) match.
- **Table 3 (p. 385):** Mass-loss inputs (172 min, 7.9 sccm, 1360 K, 0.7 atm, 0.193 gm), auxiliary (avg mol wt 72; dimer fraction 0.23), derived P_NaCl total 2.96(±0.6)×10^-2 atm and monomer 2.28(±0.4)×10^-2 atm — match; class_tag `derived` correct.
- **Table 4 (p. 394):** All four dimerization fit rows (9300/10640/10312/10140; −ΔH/−ΔS; T ranges; Knudsen/TMS/JANAF/combined) match printed; JANAF row tagged compilation.
- **Bench:** shutter aperture 0.05 cm; max load ~50 sccm — match printed p. 361.
- **Not landed (correct):** Figs 8–12 figure_only; other volume chapters.

### ta-dacko-conradt-low-p-transpiration — PASS

- **Sample:** mol% 79 SiO2 / 7 Na2O / 10 B2O3 / 3 Al2O3 / 1 minor — matches Abstract + Experimental.
- **Bench / conditions:** Pt shuttle 100×10×10 mm³; min P 200 hPa; T 1400–1600 °C → K interval 1673.15–1873.15; P 0.2·10^5–1·10^5 Pa → 20000–100000 Pa; RH 60% room-T — match.
- **Table 1 (p. 207):** All 36 Δq (mg/cm²) cells (18 T/P/t × 2 runs) match printed exactly (float-equal; no wrong digits).
- **Eqs (2)–(4):** log-r expressions, EA 206.2 / 201.6 / 172.4 kJ/mol, σ(r) 5.79 / 6.18 / 9.65 % — match; stamped derived.
- **Table 2 (p. 209):** a(Na2O)/a(B2O3) at 1400/1500/1600 °C; all nine r_calc and nine r_exp — match; model vs derived stamps correct.
- **Table 4 (p. 211):** log Ki c0/c1/c2 for NaOH / NaBO2 / HBO2 / H3BO3 — match; model context.
- **Table 5 (p. 211):** Four condensate rows (A, ΔmB, ΔmNa, B/Na) — match printed column header `B/Na` (abstract prose "Na2O/B2O3 1.25–1.4" noted in extract, not substituted).
- **Not landed (correct):** Fig. 2 figure_only; Table 3 technological mass-transfer block (cited as r_calc generator only).

## Non-P0 findings (no tip change)

| # | sev | source | note |
| --- | --- | --- | --- |
| 1 | P2 | hastie-1979 | Bench `shutter_aperture_for_transpiration` flow-map locator uses an unquoted `note: … aperture, 4 cm …`, so YAML parses a spurious null key `4 cm from skimmer orifice` and truncates the note. Number `0.05` cm and page locator remain correct. |
| 2 | P3 | ta-dacko | Abstract names the condensate ratio "sodium oxide to boron oxide" while Table 5 prints `B/Na`; extract correctly keeps printed column values + notes the tension. |

## Validation

```
.venv/bin/python tools/validate_literature_extracts.py \
  data/literature/extracts/hastie-1979-characterization-of-high-temperature.yaml \
  data/literature/extracts/ta-dacko-conradt-low-p-transpiration.yaml
→ OK: 2 extract file(s) valid
```

## Push

None. Tip remains `origin/empirical/n2-hastie-dacko-2026-09-22` @ `2f92f46b9`.

## Report line

**VERDICT: READY · P0=0 P1=0 P2=1 P3=1 · tip: `2f92f46b9` (detached, unchanged)**
