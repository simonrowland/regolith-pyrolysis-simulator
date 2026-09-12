# Pankratz, Mah & Watson (1987), USBM Bulletin 689

**Partial ingest: printed pages 3–36 (PDF pages 7–40).** The local read-only
corpus contains only the first 40-page MinerU chunk. Printed pages 37–427
remain untranscribed; PDF page 432 is blank. All 432 source pages were rendered
at 300 dpi for this run, but rendering is not examination.

This US government compilation is a thermodynamic reference input, never a
measured validation source or battery-scoring entry. Sulfides fall under the
non-oxide warn-not-fail-closed policy and may inform Stage 0 cleanup chemistry.
This ingest adds no simulator execution path or oxide-rail default.

## Coverage and provenance

- 34 separate native table records: formation and reaction tables remain
  separate, even when they describe the same formula and phase.
- 506 rows / 4,048 numeric cells. Duplicate temperatures at transitions stay
  in printed order. Units remain cal/mol·K and kcal/mol; no conversion,
  interpolation, smoothing, scoring, or phase merging.
- All 34 identities examined against page images: 20 matched, 13 corrected,
  1 unverified. `source/formula-audit.jsonl` records every examination.
- Printed page 14 actually lacks “Al” in its substance heading. Its incomplete
  printed token is retained, formula is null, and the record is excluded from
  substance iteration and lookup. The reaction's complete formula is not used
  to silently reconstruct the heading.
- 97 numeric cells image-verified: all 96 on printed page 3, plus the repaired
  Log Kr cell at 1400 K on printed page 30 (`-968` → `-.968`). The other 3,951
  numeric cells require explicit OCR-suspect opt-in. No complete numeric
  examination is claimed.
- Post-table paragraphs, footnotes, source prose, and equation-table HTML are
  retained in `notes.blocks_raw` before parsing supplementary note fields.
  Notes and equations remain OCR-unverified; equations are not executable
  coefficient models. Full original page items are in `source/mineru-pages.jsonl`.

The source PDF checksum, acquisition URL, public-domain basis and native decode
checksum are recorded in the manifest and source sidecar. The independent
numeric fixture was transcribed from images, not generated from MinerU.
Corrections retain original OCR tokens and image quotes in each affected record
and in the manifest. Detectors flag wraps, merged headers and magnitude jumps;
they never apply repairs.

## Loader

`simulator.reference_data.pankratz_1987_usbm_b689_loader` exposes exactly
`load_manifest`, `load_records`, and `lookup_temperature`. Each requires
`include_ocr_suspect=True` to expose suspect material. No package re-exports
exist. Structural page headers are source metadata, never substances.
Exact-grid lookup returns all matching rows; verified rows on printed page 3
are available without opt-in. Record loading includes unverified notes and
therefore requires opt-in for the current corpus.

`tools/ingest_pankratz_1987_usbm_b689.py` reconstructs the artifact bundle from
the read-only corpus and committed image audits. `build(corpus_path)` returns
a relative-path-to-content mapping; the CLI writes that mapping as JSON to
stdout for application with native file tools. All YAML serialization uses
PyYAML's dumper. Tests include image anchors independent of the audit sidecar,
source-token round trips, guard mutations and a repair-revert assertion.
