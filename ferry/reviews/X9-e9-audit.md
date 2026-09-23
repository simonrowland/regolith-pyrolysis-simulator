# X9 — EXTRACTION AUDIT: E9 nakano / rusiecka / yam

**Lane:** E9 (`empirical/e9-nakano-rusiecka-yam-2026-09-22`)
**Worktree:** `/workspace/repos/wt/slot-04` (slot-g2 busy on g2-phase-paren-aliases; fell back per brief)
**Prior:** `/workspace/ferry-inbox/reviews/E9-nakano-rusiecka-yam.md`
**PDFs:** `/workspace/batch-z/pdfs/nakano-hashimoto-2020-bubbles-to-chondrites-i.pdf`, `rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts.pdf`, `yam1983.pdf`
**Tip:** `00c7ea66d1999b8607918aba3bd4774e027669b8` (unchanged; no fix commit)
**Date:** 2026-09-22 (America/Toronto)

## Scope

Cell-by-cell check of every landed starting composition (value, unit/basis, row/experiment mapping, locator) against the printed PDF table. Mismatch → P0 + fix on the E branch. Observation-level / species bags walked for composition-trap remnants (X5-class).

## P0 summary

**P0 count: 0.** No composition mismatches. No fix/push.

## Per-source audit

### nakano-hashimoto-2020-bubbles-to-chondrites-i — PASS

**Printed:** Table 1, PEPS 7:47 p. 2 (PDF p. 2), bulk chemical compositions of starting materials (mol% of MOx).
**Landed:** `allende-laser-boiling` and `lsil-laser-boiling` `sample.initial_composition` as mole_fraction (/100 only); labels on `printed_composition`. `laser-boiling-series` label-only.

#### Allende row → `allende-laser-boiling`

| oxide | printed mol% | landed mole_fraction | match |
| --- | ---: | ---: | --- |
| NaO0.5 | 0.84 | 0.0084 | yes |
| MgO | 35.19 | 0.3519 | yes |
| AlO1.5 | 3.40 | 0.0340 | yes |
| SiO2 | 32.82 | 0.3282 | yes |
| KO0.5 | 0.04 | 0.0004 | yes |
| CaO | 2.47 | 0.0247 | yes |
| TiO2 | 0.10 | 0.0010 | yes |
| CrO1.5 | 0.39 | 0.0039 | yes |
| MnO | 0.15 | 0.0015 | yes |
| FeO | 24.60 | 0.2460 | yes |

#### Lsil row → `lsil-laser-boiling`

| oxide | printed mol% | landed mole_fraction | match |
| --- | ---: | ---: | --- |
| NaO0.5 | 1.82 | 0.0182 | yes |
| MgO | 37.29 | 0.3729 | yes |
| AlO1.5 | 2.88 | 0.0288 | yes |
| SiO2 | 40.98 | 0.4098 | yes |
| KO0.5 | 0.68 | 0.0068 | yes |
| CaO | 2.22 | 0.0222 | yes |
| TiO2 | 0.78 | 0.0078 | yes |
| CrO1.5 | 0.41 | 0.0041 | yes |
| MnO | 0.46 | 0.0046 | yes |
| FeO | 12.48 | 0.1248 | yes |

- **Unit / basis:** mol% of MOx → mole_fraction via /100 only; not renormalised — OK.
- **Quoted spelling retained** as strings (`0.0340`, `0.0004`, …) — OK.
- **Total 100 omitted** — OK.
- **Row mapping:** Allende / Lsil start rows only; later evaporation-product tables unused as charge — OK.
- **Locator:** `page: 2`, `table: '1'` — OK (article page 2).
- **Hosts:** 3× `printed_composition` (2 numeric maps + 1 series label); no observation-level composition traps.
- **fidelity_samples** echo the same mol% maps — consistent.

### rusiecka-wood-2025-chlorine-nacl-hydrous-basaltic-melts — PASS

**Printed:** Table 1, GCA 393 p. 210 (PDF p. 3), starting materials on anhydrous basis (oxide wt% ± analytical uncertainty); H2O column separate.
**Landed:** `icb-2-start` / `icb-4-start` / `icb-8-start` `sample.printed_composition` anhydrous oxide maps; H2O only in locator notes. `icb-series` label-only.

| experiment | oxide | landed | printed (anhydrous) | match |
| --- | --- | ---: | ---: | --- |
| icb-2-start | SiO2 | 50.66 | 50.66 ± 0.26 | yes |
| | TiO2 | 1.01 | 1.01 ± 0.03 | yes |
| | Al2O3 | 14.42 | 14.42 ± 0.18 | yes |
| | FeO | 9.74 | 9.74 ± 0.13 | yes |
| | MgO | 9.23 | 9.23 ± 0.09 | yes |
| | CaO | 12.73 | 12.73 ± 0.14 | yes |
| | Na2O | 2.15 | 2.15 ± 0.11 | yes |
| | K2O | 0.05 | 0.05 ± 0.02 | yes |
| icb-4-start | SiO2 | 50.68 | 50.68 ± 0.22 | yes |
| | TiO2 | 1.02 | 1.02 ± 0.03 | yes |
| | Al2O3 | 14.06 | 14.06 ± 0.14 | yes |
| | FeO | 9.75 | 9.75 ± 0.14 | yes |
| | MgO | 9.36 | 9.36 ± 0.08 | yes |
| | CaO | 12.87 | 12.87 ± 0.10 | yes |
| | Na2O | 2.17 | 2.17 ± 0.12 | yes |
| | K2O | 0.08 | 0.08 ± 0.03 | yes |
| icb-8-start | SiO2 | 51.41 | 51.41 ± 0.23 | yes |
| | TiO2 | 1.01 | 1.01 ± 0.04 | yes |
| | Al2O3 | 13.20 | 13.20 ± 0.13 | yes |
| | FeO | 9.61 | 9.61 ± 0.13 | yes |
| | MgO | 9.46 | 9.46 ± 0.10 | yes |
| | CaO | 12.93 | 12.93 ± 0.19 | yes |
| | Na2O | 2.31 | 2.31 ± 0.10 | yes |
| | K2O | 0.06 | 0.06 ± 0.02 | yes |

- **Unit / basis:** anhydrous oxide wt%; ± uncertainties not folded into map; H2O (2.67 / 4.38 / 6.29) retained in locator notes only — OK.
- **Quoted spelling:** YAML source retains `Al2O3: 13.20` (PyYAML float load is representation only) — OK.
- **Not used as charge (correct):** Table 2 product-glass observation rows; Table 3 basalt/andesite/rhyolite calculation mixes.
- **Locator:** `published_page: 210`, `pdf_page_index: 3`, `table: '1'` — OK (footer page 210).
- **Hosts:** 4× experiment-level `printed_composition` only; no observation equipment.sample composition traps.
- **Validator:** still FAIL with the same 4 pre-existing errors noted in E9 (unchanged; not composition mismatches).

### yam1983 — PASS

**Printed:** Table 1, J. Japan Inst. Metals 47 p. 738, `XNa20` batch column (seven charges + Ref.).
**Landed:** seven `na2o-sio2-xna2o-{0p205,…,0p601}` experiments with `initial_composition` mole_fraction Na2O = printed X + SiO2 = 1−X complement. `na2o-sio2-emf-series` label-only.

| experiment | printed X_Na2O | landed Na2O | landed SiO2 (=1−X) | match |
| --- | ---: | ---: | ---: | --- |
| na2o-sio2-xna2o-0p205 | 0.205 | 0.205 | 0.795 | yes |
| na2o-sio2-xna2o-0p298 | 0.298 | 0.298 | 0.702 | yes |
| na2o-sio2-xna2o-0p356 | 0.356 | 0.356 | 0.644 | yes |
| na2o-sio2-xna2o-0p400 | 0.400 | 0.400 | 0.600 | yes |
| na2o-sio2-xna2o-0p429 | 0.429 | 0.429 | 0.571 | yes |
| na2o-sio2-xna2o-0p500 | 0.500 | 0.500 | 0.500 | yes |
| na2o-sio2-xna2o-0p601 | 0.601 | 0.601 | 0.399 | yes |

- **Unit / basis:** mole fraction binary; SiO2 complement only — OK.
- **Quoted spelling retained** (`0.400`, `0.500` as strings) — OK.
- **Not used as these charges (correct):** Table 1 Ref. row; prose reference melt 0.395Na2O–0.526SiO2–0.079Fe2O3.
- **Locator:** `page: 738`, `table: '1'` — OK (journal page 738).
- **Hosts:** 8× experiment-level only; no observation-level composition traps.

## Method notes

- Nakano + Rusiecka: layout `pdftotext` of the table page (text-native PDFs; values unambiguous).
- Yamaguchi 1983: layout `pdftotext` of journal p. 738; OCR-noisy body text but Table 1 `XNa20` column digits (`0.205` … `0.601`) clear and cross-checked against E9 landing notes.
- Full-file walk of `printed_composition` / `initial_composition` hosts: only the experiment `sample` bags listed above (no X5-class observation remaps).
- `extracts-v2/` for these three sources has observation bags only (0 composition hosts) — out of scope for start-composition audit.

## Validation

Not re-run; E9 already reported nakano + yam OK and rusiecka FAIL with pre-existing non-composition schema errors unchanged by this audit.

## Push

None. Tip remains `origin/empirical/e9-nakano-rusiecka-yam-2026-09-22` @ `00c7ea66d1999b8607918aba3bd4774e027669b8`.

## Report line

**P0: 0 · tip: `00c7ea66d` · READY**
