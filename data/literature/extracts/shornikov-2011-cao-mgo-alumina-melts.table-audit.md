# Table audit — shornikov-2011-cao-mgo-alumina-melts

The emitted Table 1 contains 32 numeric Gibbs-energy cells (11 solid, 7 liquid, 14 gas); the emitted Table 2 contains 72 numeric scalar cells, grouped by printed row into 12 composition context records. `shornikov-2011-cao-mgo-alumina-melts.table-audit.tsv` records all 104 emitted cells. MinerU and `pdftotext-layout.txt` agree on the digits, signs, and decimal places. Each table's row and column assignments were checked against its 600 dpi page render; no numeric cell required correction.

`shornikov-2011-cao-mgo-alumina-melts.compare.csv` is the raw output of the required compare command. It reports zero points because the current comparator only walks nested series points; this extract stores the emitted Table 1 cells as scalar observations and Table 2 as context rows. The output therefore cannot reconcile those 104 cells. The cell-by-cell audit above uses the emitter output, the authoritative text layer, and the rendered row/column layout.

The emitter does not assemble scalar cells into composition rows. Table 2 values were grouped structurally from the 72 emitted cells without changing their text. The extraction did not hand-correct any numeric value.
