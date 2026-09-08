# Kelley (1960), U.S. Bureau of Mines Bulletin 584

Complete native transcription of the bulletin's 893 numbered heat-content and
entropy tables. This is reference compilation input, not experimental
validation data; it is never typed measured and produces no battery score.

Source: K. K. Kelley, *Contributions to the Data on Theoretical Metallurgy:
XIII. High-Temperature Heat-Content, Heat-Capacity, and Entropy Data for the
Elements and Inorganic Compounds*, U.S. Bureau of Mines Bulletin 584 (1960).
U.S. government work, public domain in the United States. The official UNT
catalogue record is <https://digital.library.unt.edu/ark:/67531/metadc12739/>;
the acquired PDF is preserved in the read-only corpus with SHA-256
`504c6590c2e69af6529f6bf91c3a9e000bc2cb2f31e046fc82676c68b9abd698`.
Access date: 2026-09-07.

## Native shape and completeness

`census.json` transcribes the bulletin's own table list (PDF pages 4–14).
`records/table-NNN.json` stores one record per listed substance-table, in
printed order. Each record retains caption, formula/name/state strings, printed
page, column headings and units, footnotes, raw cell tokens, parsed floats, and
OCR flags. `source/mineru-tables.jsonl` is the independent round-trip source:
the six MinerU chunks' native HTML and a second raster OCR reading of every
table crop. The PDF itself remains outside git in the corpus.

MinerU found 908 table-shaped regions. Seven are front-matter/contents tables
and seven are equation layouts on PDF page 212; the remaining 893 numeric table
blocks exactly match the bulletin's numbered-table census. The source itself
prints “TABLE 42” twice (argon and arsenic) and omits 43 in the body; the table
list ordinal remains the record identity while both readings are retained.

## OCR and lookup policy

MinerU HTML is the primary reading. Tesseract reading of each printed table crop
checks every parsed number. Disagreement or a plausible OCR confusion marks the
cell `ocr_suspect: true`; raw tokens remain unchanged. No value is repaired,
interpolated, extrapolated, smoothed, or inferred. Finite-difference
thermodynamic identities are detectors only and append manifest ambiguities.

`lookup_temperature(record_id, temperature)` returns every row at an exact
printed temperature, including duplicate transition rows. It raises
`PrintedTemperatureUnavailable` for every off-grid request; there is no zero
default.
