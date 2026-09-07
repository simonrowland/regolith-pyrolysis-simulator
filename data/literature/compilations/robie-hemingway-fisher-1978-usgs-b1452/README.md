# Robie, Hemingway & Fisher — USGS Bulletin 1452

Complete numeric-table census of the 1978 bulletin (second printing 1979),
derived from the official USGS PDF retrieved 2026-09-06. The source PDF is a
United States government work and is public domain in the United States. Its
official URL, DOI, byte size, page count, and SHA-256 digest are preserved in
`manifest.yaml`; the PDF remains in the read-only corpus rather than being
copied here.

The bulletin contents identify numbered Tables 1–2 and the thermodynamic
sections. The resulting 400-table census comprises Table 1 on printed page 3,
Table 2 on page 4, the 298.15 K summary on pages 12–29, and one unnumbered
high-temperature substance table on every printed page 30–426. Printed page
427 was visually checked and is blank. Phase boundaries produce 574 JSON
records from those 400 tables; explicit `untranscribed` records remain where
the page image itself is unreadable.

## Native transcription

The PDF was read one page at a time with `pdftotext -layout`; per-page horizontal
column offsets recover tokens split across layout lines. Pass 2 added MinerU
`table_body` HTML plus page-image cross-check for the remaining stubs. Of 400
tables, 299 are transcribed into 5,123 rows and 473 phase-specific records.
The other 101 tables retain page locators and failure reasons in the `t-852`
MinerU follow-up list in `manifest.yaml`.

Every recoverable numeric cell stores its raw token, exact page-text line/span,
a parsed float or null, `ocr_suspect`, and retained footnote markers. Numeric
admission requires the column's printed digit/sign/decimal shape on the raw
token (bullets, spaces, and letters are never stripped); 8,451 failing tokens
remain unchanged, are marked suspect, and have no parsed value. Identity and
image-OCR checks are detectors: they set `ocr_suspect` and list ambiguities,
and they never null a numeric-shaped value. A value changes only with a
`corrections` entry that cites a page-image reading. No units are converted, no
values are rounded, and no gaps are filled. Printed rules and restarted
temperature grids split 174 additional phase records, each carrying
`phase_as_published` from its printed header. Formula, title, state description,
units, uncertainty line, formula weight, auxiliary property/equation lines, PDF
page, printed page, and source-text line locators are retained when the text
layer provides them.

The Gibbs/enthalpy/entropy identity and the relation between formation Gibbs
energy and log Kf are detector-only checks. Their disagreements are listed in
record and manifest ambiguities and never alter source tokens or parsed values.

## Loading and permitted use

`simulator.reference_data.robie_hemingway_fisher_1978_usgs_b1452_loader`
validates every raw-token/float pair while loading. `lookup_temperature()`
returns a row only at an exact, non-suspect printed grid value. It raises typed
refusals for untranscribed tables, suspect grid tokens, suspect row values, and
off-grid requests; it never interpolates, extrapolates, or supplies a zero
default.

This is an assessed reference compilation that an engine may consume. It is
not a measured validation source, produces no battery scoring rows, and refuses
as `gibbs_table_not_runtime_observable`.
