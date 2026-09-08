# Pankratz, Stuve & Gokcen 1984 — USBM Bulletin 677

Complete native-table transcription of *Thermodynamic Data for Mineral Technology*.
The ingest retains all 1,571 numeric tables found by MinerU across the 360-page scan:
six worked-example tables in Chapter 1 and 1,565 thermodynamic property tables in
Chapter 2. `census.json` assigns every table to the printed CONTENTS page ranges.

`source/mineru/` contains the nine source markdown decodes used for round-trip
verification. Each record retains its raw header matrix, raw cell strings, parsed
floats where syntax permits, footnote markers, printed temperature grid, PDF/printed
page locator, and source-table index. No values are interpolated, extrapolated,
zero-filled, or silently repaired. Raster OCR of 180-dpi page renders is a second
reading: disagreement marks the cell `ocr_suspect` while preserving the MinerU token.
Thermodynamic identities are detectors only and never alter a value.

Units remain as printed: T in K; Cp° and S° in cal/mol·K; H°−H°298, ΔHf°, and ΔGf°
in kcal/mol for Chapter 2. Chapter 1 examples retain their native headings without
invented units.

This assessed compilation is an engine reference input, not experimental measurement
evidence. It never produces validation or battery-scoring rows. The work is a U.S.
government publication and is public domain in the United States.
