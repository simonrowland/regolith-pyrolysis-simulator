# Z4 — FIDELITY AUDIT kems-015-hashimoto-1983 — 2026-09-22

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/z4-kems-015-hashimoto-1983-2026-09-22`  
**Base / tip:** `origin/work-v064-green` @ `2e9e17c3d` (no fix commit)  
**Worktree:** `/workspace/repos/wt/slot-z4`  
**PDF:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/kems-015-hashimoto-1983.pdf` (Geochem. J. 17, 111–145; 35 pp). **Never committed.**  
**Extract:** `data/literature/extracts/kems-015-hashimoto-1983.yaml`  
**Date:** 2026-09-22 (America/Toronto)

## Verdict

**P0: 0** — no wrong numeric value in sampled cells (value / unit / basis / sign / T / P / run↔Table-2 time mapping / locator).  
**Coverage:** Tables 1–6 main experimental/thermochem grids present; Table 7 (McSween petrographic classification) present as metadata only.  
**READY for V-style verify as “clean”** — tip unchanged; nothing to push.

## Inventory

| Layer | Count |
|---|---|
| Nested `species.*.observations` | **38** (Fe / Mg / SiO / Ca / Al2O3; includes `*_quoted` superseders) |
| Expanded migrate points | **285** (battery migration-report: 38 obs → 285 / 429) |
| `fidelity_samples` pins | **28** across **22** unique observation_ids |
| Experiments | 1 (`fcmas-evaporation-series`) — observations carry shared apparatus bags; no per-obs `experiment:` FK |
| Tables on locators | 1–6 (data) + 7 (petrographic classification, metadata) |
| Figures with numeric landings | Fig. 9 (ν endpoints), Fig. 10 (E_a); figure points themselves typed figure-only |

## Method

1. `pdftotext -layout` per page for Tables 1–6 + prose pins; 150–220 dpi page rasters for Table 4 (OCR garbled glass MgO as `LS 8`).
2. Automated compare of **all 31 Table 3 rows** (VF + five oxides + splash + T_C/t_min from Table 2 join) vs PDF transcription.
3. Automated compare of **all 6 Table 4 phase rows**, **all 4 Table 5 rows** (F, Pb, Po, P̄, Uv), **structured Table 6 reactions 1–4 / 13–17 / 18–20** + quote CSV for reactions 5–12.
4. Verify all **28 fidelity_samples** resolve to the same stored field values.
5. Spot-check geometry / vacuum / emissivity / reaction-order / activation-energy / CaO Stage-IV “1/3” prose.

## Sampled checks (≥20 observations; dense cell coverage)

Sampled **22 unique observation_ids** via fidelity pins, plus full-grid audits of Tables 1–6 (far above 20 numeric cells).

### Table 1 starting composition — OK (15 cells)

Locator: published p. 113 / PDF p. 3, table `1`. Row (1) Ave. = experiment starting composition (paper note).

| field | printed | landed | match |
|---|---:|---:|---|
| Ave SiO2 / Al2O3 / FeO / MgO / CaO wt% | 35.43 / 3.16 / 35.04 / 23.84 / 2.53 | same | OK |
| σ | 0.12 / 0.12 / 0.15 / 0.25 / 0.07 | same | OK |
| P.C. (prescribed) | 35.50 / 3.00 / 35.00 / 24.00 / 2.50 | 35.5 / 3.0 / 35.0 / 24.0 / 2.5 | OK |

Unit/basis: wt% 100% normalized — OK. Quote CSV also carries samples 1–7 + S.C. rows.

### Table 2 run grid — OK

Locator: p. 114 / PDF p. 4. `time_min_axis` = [1.29, 2.15, 3.59, 5.99, 10.0, 16.7, 27.8, 46.4, 77.4, 129]; pin `19B8` → 1900 °C, 5.99 min matches prose + grid. Empty cells typed absence (not 0). Quote lists all 19 run codes.

### Table 3 residue series — **31/31 rows OK** (full grid)

Locator: p. 116 / PDF p. 6. Every printed row present; VF (incl. parenthetical splash), oxides, and Table-2 time join checked.

| sample | T °C | t min | VF printed/ext | FeO / MgO printed/ext | splash |
|---|---:|---:|---|---|---|
| 17C5 (1) | 1700 | 27.8 | 18.6 / 18.6 | 22.74 / 22.74 ; 27.88 / 27.88 | no |
| 18C1 (1) | 1800 | 10.0 | (31.3) / 31.3 | 14.39 / 14.39 ; 32.49 / 32.49 | yes |
| 19B8 | 1900 | 5.99 | 38.7 / 38.7 | 11.49 / 11.49 ; 34.77 / 34.77 | no |
| 20B6 | 2000 | 3.59 | 52.5 / 52.5 | 3.15 / 3.15 ; **41.10 / 41.1** | no |
| 17C1 | 1700 | 10.0 | 8.2 / 8.2 | n.d. / null | no |
| 18C1 (3) | 1800 | 10.0 | (blank, valence-V) / null | — | no |

- **Unit/basis:** VF wt%; composition wt% 100% normalized — OK.  
- **Sign / T / run mapping:** T_C + t_min from Table 2 notation (17C5 = 1700 °C, C5 time) — OK on all 31.  
- Fidelity pins `pin_17C5_1_FeO_wt_pct=22.74`, `pin_20B6_VF_wt_pct=52.5`, `pin_20B6_MgO_wt_pct=41.1` — OK.

### Table 4 quench phases — **6/6 rows OK** (raster-confirmed)

Locator: p. 118 / PDF p. 8. Glass MgO **1.58** (pdftotext OCR fail `LS 8`; PNG confirms 1.58).

| sample / phase | Fe2O3 printed/ext | MgO printed/ext | Total / n |
|---|---|---|---|
| 18B6(1) Magnetite | 59.94 / 59.94 | 1.85 / 1.85 | 97.72 / 5 |
| 18B6(1) Olivine | — / null | 37.58 / 37.58 | 99.37 / 26 |
| 18B6(1) Glass | — / null | **1.58 / 1.58** | 98.42 / 5 |
| 17C1 Magnetite | 60.15 / 60.15 | 1.93 / 1.93 | 97.44 / 7 |
| 17C1 Olivine | — / null | 41.24 / 41.24 | 99.41 / 7 |
| 17C1 Glass | — / null | 1.31 / 1.31 | 98.39 / 7 |

Fe2O3 dashes → null (true absence, not 0) — OK.

### Table 5 F / pressures / Uv — **4/4 rows OK**

Locator: p. 122 / PDF p. 12. `F_mol_s_at_1800C = 1.6e-6` matches `1.6 × 10^{-6}`. Uv stored as 41 / 162 / 459 / 948 (= printed 0.041×10³ … 0.948×10³) — OK.

### Table 6 ΔH° / ΔH°/z — structured + quote OK

Locator: p. 124 / PDF p. 14. Species-scoped structured lists: Fe rxns 1–4, SiO2 13–17, MgO 18–20. Full 20-row CSV in Fe `*_quoted` quote (incl. Fe2O3/Fe3O4 5–12). Spot: rxn 2 ΔH/z **100.425**; rxn 13 **122.799**; rxn 18 **104.235** — match PDF.

### Fig. 9 / 10 + prose pins — OK

| pin | printed | landed |
|---|---|---|
| ν_FeO @ 1700 / 2000 °C | 1.204 / 0.798 | 1.204 / 0.798 |
| E_a FeO range | 93.7–107.9 kcal/mol | [93.7, 107.9] |
| E_a SiO2 / MgO | 131.6 / 123.6 | 131.6 / 123.6 |
| tube hole / chamber | 1.2 mm; 400×300 mm | 1.2; 400 / 300 |
| mass / vacuum / ε | 100.0 mg; ~10^{-4} Torr; 0.95 | 100.0; 1e-4; 0.95 |
| pump ultimate | 7×10^{-8} Torr | 7e-8 |
| volatility order | Fe, Mg, Si, Ca, Al | same list |
| Stage IV CaO vs SiO2 | “about 1/3” | 0.333… (`approximate_relative_ratio`) |
| α_i/α_j assumed (eqn 16) | 1 | 1.0 |

## Coverage notes (non-P0)

- **Table 6 Fe2O3/Fe3O4 reactions 5–12:** present in quote CSV on `hashimoto_1983_table6_feo_vaporization_enthalpies_quoted`; not duplicated into the structured `reactions:` array (species-scoped Fe/FeO only). Values are in the extract — not a missing-table gap.
- **Table 7:** McSween chondrule/CAI classification (p. 138) landed as descriptive metadata — not evaporation measurements; appropriate.
- **Figures 7–8, 11–21:** typed figure-only / no digitised plot points (`deepening.figure_status`) — intentional.
- **Observation→experiment FK:** single series experiment; obs reuse apparatus bags rather than `experiment:` FK. Mapping is via shared FCMAS series + Table 2 run codes — acceptable for this extract shape.
- **E3 oxide map on `experiments[].sample.printed_composition`:** still label-only on this base tip; numeric Ave. oxides live under Table 1 / geometry `composition_wt_pct` (and E3 branch `0601e1a3b` lands the structured sample map separately). Not a wrong stored number on this tip.

## Validation / hygiene

- All 28 `fidelity_samples` match stored observation fields (0 pin↔store mismatches).
- PDF used for audit only; **not** added to the worktree or commit pathspec.
- Branch tip remains `2e9e17c3d` — no extract edit.

## Push

None. Tip remains `origin/work-v064-green` @ `2e9e17c3d` on local branch `empirical/z4-kems-015-hashimoto-1983-2026-09-22` (no unique commits).

## Report line

**P0: 0 · sampled ≥22 obs / full Tables 1–6 grids · tip: `2e9e17c3d` · READY**
