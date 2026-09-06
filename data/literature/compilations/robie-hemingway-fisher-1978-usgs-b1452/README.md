# Robie, Hemingway & Fisher — USGS Bulletin 1452

Complete numeric-table census of the 1978 bulletin (second printing 1979),
derived from the official USGS PDF retrieved 2026-09-06. The source PDF is a
United States government work and is public domain in the United States. Its
official URL, DOI, byte size, page count, and SHA-256 digest are preserved in
`manifest.yaml`; the PDF remains in the read-only corpus rather than being
copied here.

The bulletin contents identify numbered Tables 1–2 and the thermodynamic
sections. The resulting 400-record census comprises Table 1 on printed page 3,
Table 2 on page 4, the 298.15 K summary on pages 12–29, and one unnumbered
high-temperature substance table on every printed page 30–426. Printed page
427 was visually checked and is blank. Each census item has exactly one JSON
record, including explicit `untranscribed` records where the OCR text layer is
not losslessly recoverable.

## Native transcription

The PDF was read one page at a time with `pdftotext -layout`. Of 400 census
records, 96 high-temperature tables are transcribed into 1,528 rows. The other
304 records retain their page locators and exact failure reasons. Common hard
failures are omitted formula lines and temperature rows whose columns are
merged, shifted, or absent. The 298.15 K summary and numbered Tables 1–2 are
untranscribed because their text-layer columns or symbol/value pairs are
ragged or merged; values were not reconstructed from context.

Every recoverable numeric cell stores `as_published`, a parsed float or null,
`ocr_suspect`, and retained footnote markers. OCR-like numeric strings such as
`o.uoo`, `107.!168`, and `• 000` remain unchanged, are marked suspect, and have
no parsed value. No units are converted, no values are rounded, no gaps are
filled, and repeated transition temperatures remain separate rows. Formula,
title, state description, units, uncertainty line, formula weight, auxiliary
property/equation lines, PDF page, printed page, and source-text line locators
are retained when the text layer provides them.

The Gibbs/enthalpy/entropy identity and the relation between formation Gibbs
energy and log Kf are detector-only checks. Their disagreements are listed in
record and manifest ambiguities and never alter source tokens or parsed values.

## Loading and permitted use

`simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader`
validates every raw-token/float pair while loading. `lookup_temperature()`
returns all rows at an exact printed grid value, preserving duplicated phase
transition temperatures. It raises typed refusals for untranscribed tables and
for every off-grid request; it never interpolates, extrapolates, or supplies a
zero default.

This is an assessed reference compilation that an engine may consume. It is
not a measured validation source, produces no battery scoring rows, and refuses
as `gibbs_table_not_runtime_observable`.
