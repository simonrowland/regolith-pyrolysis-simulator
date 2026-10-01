# REVIEW — review/residue-rail-sq

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip:** `89d7af229d381fe3ec7527779bb34c9f2b265471` (squashed on `3bb8c5496`; tree identical to worker tip `52b4585d0` per main brief)
- **Commit:** `Add the residue-composition rail and split the Sossi 2019 and Hashimoto 1983 residue series`
- **Files:** extracts (`kems-012`, `kems-015`) + `enums` / `identity` / `migrate` / `score` + battery tests. **No docs-private.**
- **Date:** 2026-09-30 ~19:55–20:05 ET
- **Mode:** read-only; extract not edited. Targeted tests only (no full W3).

## Corpus

- Staged PDFs: `/workspace/ferry-inbox/reviews/_req-2026-09-30/residue/kems-012-sossi-2019.pdf`, `kems-015-hashimoto-1983.pdf`
- `_audit/` staging empty on this drop; Hashimoto Table 3 cross-checked via `/workspace/ferry-inbox/reviews/_z4_hashimoto/pdf.txt` plus fresh `pdftotext -layout` of both PDFs under `/tmp/residue-pdf/`.
- Prior Z2/Z4 fidelity audits (2026-09-22) used as expected printed grids only; this review re-migrated tip and re-spot-checked live cells.

## Scope / attack list (prior REVISE defects)

Per main brief: ninth rail `RESIDUE_COMPOSITION` / quantity `RESIDUE_COMPONENT_COMPOSITION`; split Sossi 2019 (kems-012) and Hashimoto 1983 (kems-015) residue-vs-time composites into typed cells. Attack claims that prior REVISE defects are gone:

1. `CO2_sccm` source_key rebinding
2. Duplicate live copies per printed cell
3. Missing / mislocated run conditions
4. Census by **output file**, not only `source_id`
5. Printed basis never converted; one live observation per printed cell
6. Spot-check ~10 values vs printed tables

## Rail / migrator

- `Rail.RESIDUE_COMPOSITION` is the ninth owner rail; `Quantity.RESIDUE_COMPONENT_COMPOSITION` with unit `subtype_defined`.
- Aliases: `open_furnace_residue_composition_vs_time`, `residue_composition_vs_time`.
- Series explosion emits one cell per component field (`residue_ppm` or oxide `*_wt_pct`); composite parents retained and superseded.
- Subtypes: Sossi `element_ppm_by_mass`; Hashimoto `oxide_wt_percent`.
- Identity requires `temperature_K` + `subtype`.

## Census (independent migrate+write + battery tests)

| Source | Live residue cells | By species | Subtype | Ready (comparison_candidates) | extracts-v2 file cells | `observations-v2/CO2_sccm.yaml` |
| --- | ---: | --- | --- | ---: | ---: | --- |
| kems-012-sossi-2019 | **344** | Mn/Ti/Sc/V/Zr/La/Gd/Yb × 43 | element_ppm_by_mass × 344 | 344 | 344 in `extracts-v2/kems-012-sossi-2019.yaml` | **absent** |
| kems-015-hashimoto-1983 | **120** | SiO2/Al2O3/FeO/MgO/CaO × 24 | oxide_wt_percent × 120 | 120 | 120 in `extracts-v2/kems-015-hashimoto-1983.yaml` | **absent** |

- Duplicate attack: 0 duplicate keys on `(species, T, time_min, value, experiment_id)`; 0 duplicate `observation_id`s among live residue cells.
- Wrong-bind attack: no live residue `observation_id` / `source_id` contains `CO2_sccm`; store write leaves `observations-v2/` empty for these single-source migrations (cells stay under each extract’s **own** `extracts-v2/<source_id>.yaml`).
- Experiments bound: 43 Sossi runs, 24 Hashimoto runs (one experiment per printed run / sample row).
- Conditions (all live cells): `time_min`, `temperature_K`, `total_pressure_Pa`, starting composition/ppm, and fO2 channel bound. Sossi `atmosphere` is intentionally `unknown` with locator note that printed CO–CO2 flow is a two-gas mixture (CO and CO2 both named in the note) — not a missing condition. Hashimoto atmosphere is valued (vacuum note path).
- Units / basis: Sossi stays ppm-by-mass as published (Table 2 glass; minor parent unit-string variants, both ppm). Hashimoto stays `wt % (100% normalized) as published` (VF text stripped from oxide cell units only; oxide values not converted).

Targeted tests (VPS, `-o addopts=`):

- `tests/battery/test_migrate.py -k residue` → **7 passed**
- `tests/battery/test_score.py -k residue` → **1 passed**

## Spot-checks vs printed tables

### Sossi 2019 Table 2 (element ppm in quenched glass)

PDF `pdftotext` rows vs live cells (all OK):

| Run | Species checked | Result |
| --- | --- | --- |
| 2M-15-07-16c (1300 °C, 15 min, logfO2 −0.68, Pt) | Sc 1085.4, Ti 1312.4, V 1098.9, Mn 759.6, Zr 1088.1, La 1113.8, Gd 1276.0, Yb 1273.7 | 8/8 OK |
| 1M-PS6 (1300 °C, 60 min, logfO2 −8.00, Pt) | Sc 1184.2, Ti 1368.3, V 1343.1, Mn 782.9, Zr 1252.8, La 1087.5, Gd 1171.2, Yb 1149.3 | 8/8 OK |
| C17/12/15c | Mn 843.3, Ti 1349.0, Sc 1308.9 | 3/3 OK |
| 2M-16-07-16d | Mn 819.6, Ti 1220.5, Sc 1079.6 | 3/3 OK |
| 3M-1/3/16 | Mn 779.3, Ti 1323.2, Sc 1102.7 | 3/3 OK |

### Hashimoto 1983 Table 3 (oxide wt %, 100% normalized)

| Sample | T_K / t_min (live) | SiO2 / Al2O3 / FeO / MgO / CaO | Result |
| --- | --- | --- | --- |
| 17C3 (2) | 1973.15 / 16.7 | 39.09 / 3.57 / 27.92 / 26.32 / 3.09 | 5/5 OK |
| 17C5 (1) | — | 41.74 / 4.11 / 22.74 / 27.88 / 3.52 | 5/5 OK |
| 17C7 | — | 45.24 / 4.49 / 15.53 / 30.97 / 3.78 | 5/5 OK |
| 18B8 (1) | — | 40.41 / 3.80 / 23.82 / 28.56 / 3.40 | 5/5 OK |
| 18C3 (2) | 2073.15 / 16.7 | 45.30 / 4.33 / 12.87 / 33.57 / 3.93 | 5/5 OK |
| 19B2 | — | 37.94 / 3.55 / 29.14 / 26.09 / 3.29 | 5/5 OK |
| 20B6 | — | 43.74 / 6.24 / 3.15 / 41.10 / 5.77 | 5/5 OK |

Spot-check total: **25/25 Hashimoto oxide cells + 25/25 Sossi Table-2 cells** matched printed values (no invented numbers).

## Prior REVISE defects — disposition

| Defect | Status on tip |
| --- | --- |
| Cells filed as `CO2_sccm` | **Gone** — no `CO2_sccm.yaml`; cells only under each source’s `extracts-v2/<source_id>.yaml` |
| 2–4 duplicate live copies | **Gone** — 344 / 120 unique live cells; 0 dup keys |
| Missing / mislocated run conditions | **Gone** — per-run experiment ids; time/T/P/fO2/starting bound on every live cell |

## Non-blocking notes (not P1/P2)

- Sossi `atmosphere` remains `unknown` for the printed CO–CO2 mixture (single-species field cannot represent it); locator note names both gases. fO2_log and flow columns remain available as conditions where printed.
- Sossi live cells carry two as-published ppm unit strings depending on parent metadata (`…Table 2` vs `…Table 1 starting; Table 2 glasses`); both are ppm by mass — not a basis conversion.
- Composite / superseded parents retained by design; census above is live (non-superseded) residue cells only.

## Verdict

VERDICT: LAND 89d7af229d381fe3ec7527779bb34c9f2b265471

— regolith-empirical
