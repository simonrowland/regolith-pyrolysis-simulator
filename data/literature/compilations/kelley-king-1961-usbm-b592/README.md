# Kelley & King 1961 — U.S. Bureau of Mines Bulletin 592

Complete native transcription of every numeric table in *Entropies of the Elements and Inorganic Compounds*, USBM Bulletin 592 (1961). This is assessed reference data for engine consumption. It is not measured validation evidence and never creates battery scoring rows.

## Provenance

- Authors: K. K. Kelley and E. G. King
- Bulletin: U.S. Bureau of Mines Bulletin 592
- Official catalog: <https://digital.library.unt.edu/ark:/67531/metadc12746/>
- Access copy: the archived high-resolution UNT scan named in `source/sidecar.yaml`
- PDF SHA-256: `2ac2c85a27b6d3d123d680ecac3c68ac6046cd846ec6e4d59d63f1d7e3bfbfaa`
- Rights: U.S. government work; public domain in the United States

## Census

The bulletin's own list identifies seven numbered tables. MinerU recovered all 25 physical table blocks: Tables 1–5 occupy one block each, Table 6 spans printed pages 101–117, and Table 7 spans printed pages 118–120. The ingest contains 1,418 source records: 1,391 substances and 27 structural continuation records. The substances comprise 1,294 from Table 6, 71 from Table 7, and 26 from Tables 1–5. No listed table or source row is untranscribed.

Element-name grouping rows in Table 6 are stored as heading context, not mistaken for substances. The page-101 `Actinium: / Ac(c)` row shift is corrected only because the table crop proves the two entropy values belong to `Ac(c)`; both corrected cells remain OCR-suspect and the correction is explicit in the record and manifest.

## Native record format

Each JSON file under `records/` preserves the printed substance/formula, phase or state, page, numbered table, caption, column labels, units, temperature grid, every native row and cell string, parsed numeric values, footnote markers, OCR status, ambiguities, and corrections. `source/mineru-tables.jsonl` retains the 25 primary MinerU HTML blocks plus a Tesseract reading and image hash for each table crop. `source/image-verified-fixture.json` contains independent cells checked visually against the scan.

`source/formula-audit.jsonl` records the 300-dpi image audit of all 1,418 formula/name identities, including unchanged matches: 1,408 verified, with 210 corrected identities, and 10 explicitly unverified records. Each entry retains the printed quote, expected identity fields, and original tokens. Unresolved records concern a duplicate barium row, an iodine heading, a damaged platinum subscript, and seven sulfur charge glyphs; their identities have not been guessed. The 27 existing structural records retain their types. Image coverage does not certify numeric cells.

No unit conversion, interpolation, extrapolation, smoothing, inferred zero, or silent OCR repair is performed. Cp monotonicity, recommended-entropy agreement, and approximately factor-1000 magnitude checks are detectors only.

## Loader contract

`simulator/reference_data/kelley_king_1961_usbm_b592_loader.py` accepts only exact printed temperatures. It raises `PrintedTemperatureUnavailable` off-grid and `OCRSuspectRow` before any suspect numeric or metadata value can be returned bare. Explicit bulk access with `include_ocr_suspect=True` retains every flag for audit workflows.

Regenerate from the local read-only corpus with:

```bash
PYTHONPATH=. ../../../.venv/bin/python data/literature/compilations/kelley-king-1961-usbm-b592/harvest.py
```
