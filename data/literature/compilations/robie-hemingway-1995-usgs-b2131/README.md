# Robie and Hemingway (1995), USGS Bulletin 2131

Reference compilation, not experimental validation data. These records produce
no battery scoring rows. Runtime integration and unit conversion are outside
this ingest.

Source: *Thermodynamic properties of minerals and related substances at
298.15 K and 1 bar (10⁵ pascals) pressure and at higher temperatures*, USGS
Bulletin 2131, DOI 10.3133/b2131. Public domain in the United States as a
United States government work. Official PDF:
<https://pubs.usgs.gov/bul/2131/report.pdf>. Access date: 2026-09-06.

PDF SHA-256:
`ca89fc07fd110a0441f3bc01d5fe67d749a944da2e7f2537520dbaf367f49dd8`.
The 470-leaf PDF remains in the read-only `regolith-corpus-ctl` corpus;
`source/sidecar.yaml` is an unchanged copy of its acquisition sidecar.
Printed Arabic page numbers are PDF leaf numbers minus six.

## Native records and completeness

`census.json` enumerates source sections and per-page substance blocks.
`manifest.yaml` indexes every record, including explicitly untranscribed
records with page ranges and reasons. A counted placeholder does not mean a
table was successfully transcribed. The manifest summary separates those totals.

- `auxiliary/`: Table 1 atomic weights and the symbols/constants listing.
- `summary/`: per-substance reference-state and heat-capacity coefficient
  blocks. Source lines, uncertainties, references, units, and raw spans remain
  attached to their native rows.
- `records/`: 330 high-temperature substance-table slots, printed pp. 67–396.
  The OCR layer on printed p. 176 contains its heading but no table body;
  that record is explicitly untranscribed.

Only the atomic-weight table has a printed table number (1). Other tables
are located by section and page; null table numbers do not invent numbers.
Bibliographic references and name/formula indexes contain citation/page
locators, not thermodynamic data tables, and are outside the numeric-data census.

## OCR policy and limitations

Primary reading: `pdftotext -layout`, separately per page, with page text
kept under `/private/tmp`. MinerU text was absent at ingest start. Independent
reading: Tesseract on rendered pages. Atomic weights and constants also received
direct visual token checks. Automated raster OCR agreement is recorded as such;
it is not a claim that every token was manually inspected. Summary records
conservatively retain OCR suspicion where raster row/column alignment is not
certified.

Each numeric cell retains `raw`, `value`, and `ocr_suspect`. Invalid OCR tokens
have a null parsed value. A syntactically valid but suspect number retains its
float; consumers must inspect the flag. Tokens such as `o.oo`, malformed
exponents, and merged columns are never repaired. Parenthetical atomic-weight
markers remain in `raw`; `numeric_token` identifies the enclosed numeric text.
Missing names/formulae and unrecoverable layouts remain ambiguities or
explicit transcription gaps. No gaps are filled and no values are rounded.

The high-temperature enthalpy column is **(H−H298)/T**, not H−H298.
The high-temperature entropy identity checks S − (H−H298)/T against −(G−H298)/T with the
combined printed rounding allowance. The formation identity checks ΔfG against
−RT ln(10) log Kf using the bulletin's R = 8.31451 J mol⁻¹ K⁻¹ and the
printed Gibbs/log-K rounding. The summary formation detector uses
R = 8.31446261815324 J mol⁻¹ K⁻¹. All detectors only flag disagreements. Native stored units
and values remain unchanged. Metadata retains formula weights, volumes,
phase-transition data, regression coefficients, footnote markers, and units.

Feedstock coverage uses the existing ATcT helper on raw formula text.
It is an OCR-sensitive lexical inventory, not certified chemical identification
or a promise that every property exists for an element.

## Loader and verification

`simulator.reference_data.robie_hemingway_1995_usgs_b2131_loader.load_records()`
yields native records. `lookup_temperature(record_id, temperature)` returns
all exact-grid rows, including both rows at phase transitions, with OCR flags
intact. It raises `PrintedTemperatureUnavailable` for temperatures without a
recoverable printed row and `UntranscribedTableError` for untranscribed tables.
There is no interpolation, extrapolation, or zero default.

`harvest_high_temperature.py` reproduces the high-temperature extraction and
raster comparison from the external PDF. Use `--first`/`--last` PDF leaf
numbers for batches. `build_manifest.py` uses the census and native records;
run from repository root with `PYTHONPATH=.` and the project Python.
The summary directory contains its corresponding transcription script.

Tests in `tests/test_robie_hemingway_1995_usgs_b2131_compilation.py` cover
record/token round trips, census and feedstock coverage, source row/token
correspondence, a printed silver anchor, transition duplicates, and typed refusals.
