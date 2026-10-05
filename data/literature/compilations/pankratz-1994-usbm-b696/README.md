# Pankratz (1994), USBM Bulletin 696

**Partial species-scope ingest:** the requested gas tables found in the bulletin
are transcribed in full. This is not a full-bulletin ingest; the other substance
tables remain untranscribed. It contains 27 records, 602 temperature rows, and
4,816 numeric cells. All tabulated columns and units are retained as printed;
there is no interpolation, unit conversion, or numerical repair.

The source is a U.S. government work and is treated only as a thermodynamic
engine reference input. It is not measurement evidence and is not eligible for
battery scoring. This ingest adds no public simulator loader or engine wiring.

## Coverage

The 27 complete ideal-gas monoxide tables are AgO (printed p. 6), BiO (p. 115),
CdO (p. 191), CeO (p. 204), DyO (p. 273), ErO (p. 275), EuO (p. 278), GaO
(p. 319), GdO (p. 327), GeO (p. 330), HfO (p. 352), HoO (p. 356), InO
(p. 360), LaO (p. 410), LuO (p. 455), NdO (p. 629), PrO (p. 705), ScO
(p. 733), SmO (p. 769), SnO (p. 772), TbO (p. 803), TeO (p. 804), TmO
(p. 837), UO (p. 858), YO (p. 910), YbO (p. 920), and ZnO (p. 924). BiO
ends at 1200 K as printed. Formation and reaction tables preserve their printed
table kind and columns.

The requested species not found are PmO and Se2 (bulletin contents, PDF p. 3),
and As4 (contents p. 3 plus the arsenic-section pages, PDF pp. 46–50 / printed
pp. 42–46). Those arsenic pages contain AsH3, AsSe, As2Se3, AsTe, and As2Te3;
they do not contain an As4 table. GdO is present on printed p. 327.

The bulletin cites Pedley (reference 396) for 26 tables and Sidorov (reference
459) for BiO. These citations are retained in each record's existing raw-source
notes. No table cites Gurvich, Glushko, IVTAN, TSIV, or IVTANTHERMO.

## Image audit and source files

The contents/absence pages and each of the 27 table pages were rendered at
300 dpi. Formula, gas phase, and every numeric cell were checked against the
page images. The audit has 25 identity matches and two OCR identity corrections
(LaO and ScO); all 4,816 numeric cells are image-verified, with zero nonnumeric
cells. No numeric values were changed. The independent image-transcribed row
fixture and formula audit are retained under `source/` with the MinerU decodes
for the pages used and the source sidecar. The source PDF SHA-256 is recorded in
the manifest.

`manifest.yaml` and `census.json` describe this partial coverage, decoded PDF
pages, checked absences, image audit, and remaining pages. The shared B689
ingester owns the record and manifest assembly used to build this compilation.
