# Robie & Waldbaum 1968 — USGS Bulletin 1259

This directory is a **compilation home**: assessed thermodynamic functions the
engine may consume as reference input. It is **not** a validation source and
produces **no** battery scoring rows
(`battery_refusal: gibbs_table_not_runtime_observable`).

## Provenance

- **Citation:** Robie, R. A. and Waldbaum, D. R., 1968, Thermodynamic properties of minerals and related substances at 298.15 K (25.0 C) and one atmosphere (1.013 bars) pressure and at higher temperatures: U.S. Geological Survey Bulletin 1259, 256 p.
- **DOI:** 10.3133/b1259
- **Official URL:** https://pubs.usgs.gov/bul/1259/report.pdf
- **Licence:** United States government work. USGS numbered series; public domain in the United States.
- **Local PDF sha256:** `013abbde1aef291c7492f2eaf7ab552619699e558d741ae5f39a87830e080e83` (9 819 644 bytes, 262 PDF pages). The PDF is held in the literature corpus and is **not** copied into this repository.
- **Access date:** 2026-09-06
- **Text source:** `source/layout.txt`, a deterministic form-feed-delimited `pdftotext -layout` derivative of all 262 PDF pages. MinerU markdown was not present at ingest.

## Native structure (no conversion)

`records/` holds one JSON file per table or substance-table:

- Table 1 symbols and constants (bulletin p. 3)
- Table 2 atomic weights for 1963 (bulletin p. 4)
- Table 3 chronological list of critical summaries (bulletin p. 6; bibliographic)
- One record per substance in the 298.15 K properties tables (bulletin pp. 11–25)
- One record per high-temperature substance table (bulletin pp. 26–237), with the printed T grid and columns H−H298, S, −(G−H298)/T, ΔfH, ΔfG, log Kf as printed strings **and** parsed floats

No unit conversion, no rounding, no gap filling, no interpolation. Footnote markers stay on the token. Identity relations (gef ≈ S − 1000(H−H298)/T and ΔfG vs log Kf) are **detectors** listed under `identity_disagreements` / `ambiguities`; they are never used to rewrite a number.

OCR tokens with letter/digit confusions (O/0, C/0, l/1, dropped or shifted decimal, merged columns) are stored with `ocr_suspect: true` and the raw token. Image-proven corrections retain `layout_as_extracted`, carry a `correction_id`, and close to the manifest's page-and-quote correction ledger.

## Known gaps

- PDF page 125 (bulletin p. 119) is rotated 180°; the OCR text layer is inverted glyphs. The high-T table between Li2O and brucite is **untranscribed**.
- The 298.15 K fixed-column census is 334 substance rows across PDF pp. 17–31 (`24, 24, 24, 24, 25, 24, 24, 24, 21, 23, 21, 22, 23, 23, 8`). Header fragments are rejected by column alignment, not token value.
- High-T computer printout OCR regularly confuses O/0 and C/0 (`10.2CO`, `18.0CO`).
- A high-T temperature enters the lookup grid only from a clean T-column token or a manifest-listed page-image correction. Ambiguous rows remain present as `grid_refusal` rows.

## Loading and permitted use

`simulator.reference_data.robie_waldbaum_1968_usgs_b1259_loader` loads manifest-indexed records. `lookup(record, T)` returns the printed row(s) at that T and raises `TemperatureNotOnPrintedGrid` for any temperature that is not on that table's printed grid (no interpolation, no extrapolation, no zero default). Duplicate printed T values at a phase change return both rows.

These are assessed reference inputs, never measured validation observations.
