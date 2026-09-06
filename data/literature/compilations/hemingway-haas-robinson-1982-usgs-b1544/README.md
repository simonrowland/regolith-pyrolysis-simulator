# USGS Bulletin 1544 — Hemingway, Haas & Robinson 1982

Complete transcription of every numeric table in *Thermodynamic properties of
selected minerals in the system Al2O3-CaO-SiO2-H2O at 298.15 K and 1 bar
(10^5 pascals) pressure and at higher temperatures*, U.S. Geological Survey
Bulletin 1544 (supplement to Bulletin 1452).

These are assessed reference functions. They are not experimental measurements
and produce no battery scoring rows (`gibbs_table_not_runtime_observable`).

## Provenance

- Official PDF: https://pubs.usgs.gov/bul/1544/report.pdf
- DOI: 10.3133/b1544
- Licence: United States government work; public domain in the United States
- SHA-256 of the ingested PDF: `11a8781587745ea67de0cbb03947fcebf5cec2070e2d6dbe92129d536e94c17f`
- Retrieved 2026-09-06 from pubs.usgs.gov (corpus sidecar)

The PDF is an Adobe Paper Capture scan with an OCR text layer. Harvest uses
`pdftotext -bbox` for T-grid cells. TABLE 1, printed formulas, Cp equations,
H°298−H°0 metadata, and qualification marks are transcribed from page
images because positioned OCR loses signs, columns, or tokens in those fields.
Other tokens with plausible OCR confusions remain raw, are flagged
`ocr_suspect`, and are not corrected from thermodynamic identities.

## Native structure

`records/` has one JSON file per table:

- `usgs-b1544-table-1` — printed TABLE 1 (ΔfH at 298.15 K from four literature
  sources), 20 phases
- one record per substance T-grid (32 tables). Odd printed pages are formation
  from the elements; even pages, when present, are formation from the oxides.
  Shared columns: T, (H°_T−H°_298)/T, S°_T, −(G°_T−H°_298)/T, C°_p. Formation
  columns: ΔfH, ΔfG, log Kf.
- `marks` on each T-grid row locates every printed `*` by formation basis and
  column. Cp equations carry an equation-level `ocr_suspect` flag.

No unit conversion, no interpolation, no gap filling. Duplicate printed
temperatures at phase changes are kept as two rows.

## Loading

`simulator.reference_data.hemingway_haas_robinson_1982_usgs_b1544_loader`

- `load_records()` — manifest-indexed native files
- `lookup(record_id, T, column)` — exact printed-grid node only
- `TemperatureNotOnPrintedGrid` / `AmbiguousPrintedGridNode` /
  `AmbiguousPrintedTemperature` / `UnparsedPrintedToken`

The engine may consume these tables as assessed Cp/S/G functions on the printed
T nodes. It must not treat them as validation measurements.
