# X2 — EXTRACTION AUDIT e2-holzheid-kems — 2026-09-22

Branch: `empirical/e2-holzheid-kems-2026-09-22`
Tip: `1a57a0fd4` (unchanged; no fix commit)
Prior: `ferry-inbox/reviews/E2-holzheid-kems.md`
Worktree: `/workspace/repos/wt/slot-10`
PDFs: `/workspace/batch-z/pdfs/{holzheid-1997-feo-nio-coo-activity-metal-saturated,kems-006-zhang-2021,kems-010-richter-2007}.pdf`
Scout: `/workspace/batch-z/scout/slice-1.md` rows 6, 8, 9

## Verdict

**P0: 0** — every landed oxide cell matches the printed table cell (value, unit wt%, oxide basis, row/column mapping, locator).
**READY.** No branch change; nothing to push.

## Method

For each of the three E2 landings: open the PDF at the cited locator, read the printed cell (layout `pdftotext` + raster of the table region), compare to every oxide in `experiments[].sample.printed_composition.state.value`. Also re-checked the Holzheid `species.SiO2` Table 1 observation composition block (same cells, dual landing).

## Cell-by-cell

### holzheid-1997-feo-nio-coo-activity-metal-saturated

- **Printed:** Table 1, Chem. Geol. 139 p. 22 (PDF p. 2), header `wt.%` / electron microprobe analysis. Columns AD (anorth.–diopside) and BK (komatiitic basalt).
- **Landed:** `ad-variable-mgo-equilibration-series` and `bk-variable-mgo-equilibration-series` `sample.printed_composition`.

| oxide | printed AD | landed AD | printed BK | landed BK | match |
|-------|------------|-----------|------------|-----------|-------|
| SiO2  | 50.9       | 50.9      | 49.1       | 49.1      | OK |
| CaO   | 24.9       | 24.9      | 19.2       | 19.2      | OK |
| MgO   | 10.4       | 10.4      | 10.6       | 10.6      | OK |
| Al2O3 | 13.8       | 13.8      | 14.1       | 14.1      | OK |
| FeO   | 0.0        | 0.0       | 7.0        | 7.0       | OK |

- **Unit / basis:** oxide wt% as printed — OK.
- **Row/experiment mapping:** AD column → `ad-…`; BK column → `bk-…`. Table 3a/3b obs FKs → `ad-…`; Table 3c/3d → `bk-…`. Table 1 observation has no experiment FK (characterization of both columns) — OK, matches E2 intent.
- **Locator:** `page: 22`, `table: '1'` — OK (published page).
- **Not landed (correct):** Table 3 variable-MgO / dopant run rows (not a fresh five-oxide starting vector).
- **Also OK:** `species.SiO2` observation `holzheid_1997_table1_starting_compositions` repeats the same 10 cells under `*_wt_pct` keys — identical to printed.

### kems-006-zhang-2021

- **Printed:** Table 1, corpus PDF p. 71, **Initial glass** row, EPMA (wt%).
- **Landed:** `basalt-vacuum-evaporation-series.sample.printed_composition`.

| oxide | printed | landed | match |
|-------|---------|--------|-------|
| Na2O  | 2.27    | 2.27   | OK |
| MgO   | 7.09    | 7.09   | OK |
| CaO   | 11.21   | 11.21  | OK |
| TiO2  | 1.79    | 1.79   | OK |
| SiO2  | 45.94   | 45.94  | OK |
| Al2O3 | 16.00   | 16.00  | OK |
| FeO   | 10.67   | 10.67  | OK |
| K2O   | 2.49    | 2.49   | OK |
| Rb2O  | 1.86    | 1.86   | OK |

- **Unit / basis:** EPMA oxide wt% — OK. Rb2O retained as printed (outside migrate key set) — intentional, not a mismatch.
- **Row mapping:** Initial glass only; Mixed powder (Na/Al, Rb/Al only) and residue rows unused — OK.
- **Locator:** `page: 71`, `table: '1'` — OK.

### kems-010-richter-2007

- **Printed:** Table 1, GCA 71 p. 5549 (PDF p. 6), **Starting material** row, oxide columns labelled `(wt%)`.
- **Landed:** `cai-vacuum-evaporation-series.sample.printed_composition`.

| oxide | printed | landed | match |
|-------|---------|--------|-------|
| MgO   | 11.48   | 11.48  | OK |
| SiO2  | 46.00   | 46.00  | OK |
| Al2O3 | 19.39   | 19.39  | OK |
| CaO   | 23.12   | 23.12  | OK |

- **Unit / basis:** oxide wt% — OK.
- **Row mapping:** Starting material only; residue / run rows unused — OK.
- **Locator:** `published_page: 5549`, `pdf_page_index: 6`, `table: '1'` — OK (footer on PDF p. 6 is 5549).

## Non-P0 notes (no fix)

- Holzheid BK locator note text has `komatiiticbasalt` (missing space). Label hygiene only; composition values correct. Left unfixed to avoid a no-op composition commit.
- YAML float canonicalization (`16.00`→`16.0`, `46.00`→`46.0`) is representation only; printed magnitudes match.

## Validation

`uv run python tools/validate_literature_extracts.py` on the three extract files → `OK: 3 extract file(s) valid`.

## Push

None. Tip remains `origin/empirical/e2-holzheid-kems-2026-09-22` @ `1a57a0fd4`.

## Report line

**P0: 0 · tip: `1a57a0fd4` · READY**
